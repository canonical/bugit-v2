"""Real Launchpad / Jira adapters built on top of the existing bugit submitters"""

import logging
import tarfile
import tempfile
import uuid
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

from bugit_v2.mcp_server.bugs import Draft
from bugit_v2.models.bug_report import BugReport

logger = logging.getLogger(__name__)

MAX_ATTACHED_FILE_BYTES = 50 * 10**6


def _pack_logs(logs_dir: Path, out: Path, only: Collection[str] | None = None) -> None:
    skipped: list[str] = []
    with tarfile.open(out, "w:gz") as tar:
        for p in sorted(logs_dir.rglob("*")):
            if not p.is_file() or p.is_symlink():
                continue
            rel = p.relative_to(logs_dir).as_posix()
            if only is not None and rel not in only:
                continue
            if p.stat().st_size > MAX_ATTACHED_FILE_BYTES:
                skipped.append(rel)
                continue
            tar.add(p, arcname=rel, recursive=False)
    if skipped:
        logger.warning("Not attaching oversized files: %s", skipped)


type ReportSource = Callable[[str], Any]


def _report(
    draft: Draft, project: str | None, report_source: ReportSource | None = None
) -> BugReport:
    if not project:
        raise ValueError(
            "No project to file the bug in; pass project= to draft_bug() "
            "or configure a default project"
        )
    # gives the submitter the checkbox data it needs to append [Job Output]
    source = report_source(draft.submission_id) if report_source and draft.submission_id else None
    return BugReport(
        report_id=uuid.uuid4(),
        title=draft.title,
        description=draft.description,
        project=project,
        severity=draft.severity,
        issue_file_time=draft.issue_file_time,
        checkbox_session=source.checkbox_session if source else None,
        checkbox_submission=source.checkbox_submission if source else None,
        job_id=draft.test_case if source else None,
        assignee=draft.assignee,
        platform_tags=list(draft.platform_tags),
        additional_tags=list(draft.tags),
        status=draft.status,
        series=draft.series,
        impacted_features=list(draft.impacted_features),
        impacted_vendors=list(draft.impacted_vendors),
    )


def _run_submitter(
    submitter: Any, report: BugReport, logs_dir: Path | None, only: Collection[str] | None = None
) -> str:
    for _ in submitter.submit(report):
        pass
    if logs_dir is not None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "logs.tar.gz"
            _pack_logs(logs_dir, archive, only)
            submitter.upload_attachment(archive, "logs.tar.gz")
    submitter.finalize()
    return submitter.bug_url


class LaunchpadTracker:
    def __init__(
        self,
        submitter_factory: Callable[[], Any],
        default_project: str | None = None,
        report_source: ReportSource | None = None,
    ) -> None:
        self._submitter_factory = submitter_factory
        self._default_project = default_project
        self._report_source = report_source

    def submit(self, draft: Draft, logs_dir: Path | None) -> dict[str, Any]:
        report = _report(draft, draft.project or self._default_project, self._report_source)
        url = _run_submitter(self._submitter_factory(), report, logs_dir, draft.attach_logs or None)
        return {"url": url, "id": url.rstrip("/").rsplit("/", 1)[-1], "tracker": "launchpad"}


class JiraTracker:
    def __init__(
        self,
        submitter_factory: Callable[[], Any],
        default_project: str | None = None,
        credentials: Callable[[], tuple[str, str] | None] = lambda: None,
        report_source: ReportSource | None = None,
    ) -> None:
        self._report_source = report_source
        self._submitter_factory = submitter_factory
        self._default_project = default_project
        self._credentials = credentials

    def submit(self, draft: Draft, logs_dir: Path | None) -> dict[str, Any]:
        creds = self._credentials()
        if creds is None:
            raise RuntimeError(
                "No Jira credentials; call check_auth_status('jira') for how to set them"
            )
        from bugit_v2.bug_report_submitters.jira_submitter import JiraBasicAuth

        report = _report(draft, draft.project or self._default_project, self._report_source)
        submitter = self._submitter_factory()
        submitter.auth = JiraBasicAuth(*creds)
        url = _run_submitter(submitter, report, logs_dir, draft.attach_logs or None)
        return {"url": url, "id": url.rsplit("/", 1)[-1], "tracker": "jira"}


class LocalTracker:
    """Files reports as directories on disk (report.md + logs.tar.gz); needs no account"""

    def __init__(self, reports_dir: Path) -> None:
        self._dir = reports_dir

    def submit(self, draft: Draft, logs_dir: Path | None) -> dict[str, Any]:
        self._dir.mkdir(parents=True, exist_ok=True)
        bug_id = uuid.uuid4().hex[:8]
        target = self._dir / bug_id
        target.mkdir()
        meta = [
            f"Project: {draft.project or '-'}",
            f"Severity: {draft.severity}",
            f"Tags: {', '.join([*draft.platform_tags, *draft.tags])}",
        ]
        (target / "report.md").write_text(
            f"# {draft.title}\n\n" + "\n".join(meta) + f"\n\n{draft.description}\n"
        )
        if logs_dir is not None:
            _pack_logs(logs_dir, target / "logs.tar.gz", draft.attach_logs or None)
        return {"url": target.as_uri(), "id": bug_id, "path": str(target), "tracker": "local"}
