"""Read-only discovery of local Checkbox sessions and submission tarballs"""

import asyncio
import datetime as dt
import gzip
import logging
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import ijson

from bugit_v2.checkbox_utils.checkbox_session import SESSION_ROOT_DIR, CheckboxSession
from bugit_v2.checkbox_utils.get_cert_status import get_certification_status
from bugit_v2.checkbox_utils.submission_extractor import read_simple_submission
from bugit_v2.models.bug_report import BugReport

logger = logging.getLogger(__name__)

FAILED_OUTCOMES = ("fail", "crash")

# same suggestion as the bugit TUI (bug_report_screen._color_cert_status_box)
SUGGESTED_SEVERITY = {"blocker": "highest", "non-blocker": "high"}


def _session_cert_status(
    testplan_id: str, test_id: str, session_dir: Path
) -> str | None:
    """Look the status up in the test plan (a session does not record it)"""
    try:
        found = asyncio.run(get_certification_status(testplan_id, test_id, session_dir))
    except Exception as e:
        logger.debug("no cert status for %s: %r", test_id, e)
        return None
    return found.cert_status if found else None


def _iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()


def _session_results(session_dir: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    with gzip.open(session_dir / "session", "rb") as f:
        for job_id, results in ijson.kvitems(f, "session.results"):
            if results:
                yield job_id, results[-1]


class CheckboxService:
    def __init__(
        self,
        session_root: Path = SESSION_ROOT_DIR,
        submission_dirs: Sequence[Path] = (),
    ) -> None:
        self._session_root = session_root
        self._submission_dirs = [d.resolve() for d in submission_dirs]

    def list_submissions(self, limit: int = 20) -> list[dict[str, Any]]:
        items: list[tuple[float, dict[str, Any]]] = []

        if self._session_root.is_dir():
            for d in self._session_root.iterdir():
                try:
                    mtime = (d / "session").stat().st_mtime
                    testplan = CheckboxSession(d).testplan_id
                except Exception as e:
                    logger.debug("skipping session %s: %r", d, e)
                    continue
                items.append(
                    (
                        mtime,
                        {
                            "submission_id": f"session:{d.name}",
                            "kind": "session",
                            "testplan_id": testplan,
                            "modified": _iso(mtime),
                        },
                    )
                )

        for sdir in self._submission_dirs:
            if not sdir.is_dir():
                continue
            for p in sdir.glob("*.tar.xz"):
                try:
                    testplan = read_simple_submission(p).base.testplan_id
                    mtime = p.stat().st_mtime
                except Exception as e:
                    logger.debug("skipping submission %s: %r", p, e)
                    continue
                items.append(
                    (
                        mtime,
                        {
                            "submission_id": f"submission:{p}",
                            "kind": "submission",
                            "testplan_id": testplan,
                            "modified": _iso(mtime),
                        },
                    )
                )

        items.sort(key=lambda t: t[0], reverse=True)
        return [i for _, i in items[:limit]]

    def _resolve(self, submission_id: str) -> tuple[str, Path]:
        kind, _, ref = (submission_id or "").partition(":")
        if kind == "session" and ref and "/" not in ref and ref not in (".", ".."):
            d = self._session_root / ref
            if (d / "session").is_file():
                return kind, d
        elif kind == "submission" and ref.endswith(".tar.xz"):
            p = Path(ref).resolve()
            if p.is_file() and any(p.is_relative_to(d) for d in self._submission_dirs):
                return kind, p
        raise ValueError(f"Unknown submission_id: {submission_id!r}")

    def get_failed_test_cases(self, submission_id: str) -> list[dict[str, Any]]:
        kind, path = self._resolve(submission_id)
        failed_at = _iso(
            (path / "session").stat().st_mtime
            if kind == "session"
            else path.stat().st_mtime
        )
        cases: list[dict[str, Any]] = []
        if kind == "session":
            for job_id, last in _session_results(path):
                if last.get("outcome") in FAILED_OUTCOMES:
                    cases.append(
                        {
                            "test_id": job_id,
                            "outcome": last["outcome"],
                            "comments": last.get("comments") or None,
                            "failed_at": failed_at,
                        }
                    )
        else:
            for r in read_simple_submission(path).base.results:
                if r.outcome in FAILED_OUTCOMES:
                    cases.append(
                        {
                            "test_id": r.full_id,
                            "name": r.name,
                            "outcome": r.outcome,
                            "certification_status": r.certification_status,
                            "comments": r.comments,
                            "failed_at": failed_at,
                        }
                    )
        return cases

    def list_failed_tests(self, limit: int = 20) -> list[dict[str, Any]]:
        """Every failed test of the recent submissions, each tagged with its
        submission, test plan and time so the user can pick one in a single step"""
        rows: list[dict[str, Any]] = []
        for sub in self.list_submissions(limit):
            try:
                cases = self.get_failed_test_cases(sub["submission_id"])
            except Exception as e:
                logger.debug("skipping %s: %r", sub["submission_id"], e)
                continue
            for case in cases:
                status = case.get("certification_status")
                if status is None and sub["kind"] == "session":
                    status = _session_cert_status(
                        sub["testplan_id"],
                        case["test_id"],
                        self._resolve(sub["submission_id"])[1],
                    )
                rows.append(
                    {
                        "submission_id": sub["submission_id"],
                        "kind": sub["kind"],
                        "testplan_id": sub["testplan_id"],
                        "modified": sub["modified"],
                        **case,
                        "cert_status": status,
                        "suggested_severity": SUGGESTED_SEVERITY.get(status or ""),
                    }
                )
        return rows

    def describe(self, submission_id: str) -> dict[str, Any]:
        kind, path = self._resolve(submission_id)
        mtime = (
            (path / "session").stat().st_mtime
            if kind == "session"
            else path.stat().st_mtime
        )
        return {"kind": kind, "path": str(path), "failed_at": _iso(mtime)}

    def collector_input(self, submission_id: str) -> BugReport:
        """A throwaway BugReport so bugit's log collectors can read the session"""
        kind, path = self._resolve(submission_id)
        return BugReport(
            report_id=uuid.uuid4(),
            title="",
            description="",
            project="",
            severity="medium",
            issue_file_time="later",
            checkbox_session=CheckboxSession(path) if kind == "session" else None,
            checkbox_submission=(
                read_simple_submission(path) if kind == "submission" else None
            ),
            job_id=None,
        )

    def get_job_output(self, submission_id: str, test_id: str) -> dict[str, str] | None:
        kind, path = self._resolve(submission_id)
        if kind == "session":
            out = CheckboxSession(path).get_job_output(test_id)
            return dict(out) if out else None
        for r in read_simple_submission(path).base.results:
            if r.full_id == test_id:
                return {
                    "stdout": r.io_log or "",
                    "stderr": "",
                    "comments": r.comments or "",
                }
        return None
