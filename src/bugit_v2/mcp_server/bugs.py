"""The draft -> confirm submission flow (human in the loop)"""

import re
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from bugit_v2.models.bug_report import (
    BUG_STATUSES,
    ISSUE_FILE_TIMES,
    SEVERITIES,
    BugStatus,
    IssueFileTime,
    Severity,
)

MAX_TITLE_LEN = 200
MAX_TAGS = 20
_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_ASSIGNEE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+@-]*")

_UPDATABLE = (
    "title", "description", "tags", "logs_job_id", "tracker", "project", "severity",
    "issue_file_time", "platform_tags", "impacted_features", "impacted_vendors",
    "assignee", "status", "series", "submission_id", "test_case", "parts", "attach_logs",
)  # fmt: skip


@dataclass(slots=True, frozen=True)
class Draft:
    draft_id: str
    title: str
    description: str
    tags: list[str]
    logs_job_id: str | None
    tracker: str
    project: str | None
    created_at: float
    # the fields below mirror bugit-v2's BugReport
    severity: Severity = "highest"
    issue_file_time: IssueFileTime = "immediate"
    platform_tags: list[str] = field(default_factory=list[str])
    impacted_features: list[str] = field(default_factory=list[str])
    impacted_vendors: list[str] = field(default_factory=list[str])
    assignee: str | None = None
    status: BugStatus = "New"
    series: str | None = None
    submission_id: str | None = None
    test_case: str | None = None
    # inputs the description was rendered from, so update_draft can change one section
    parts: dict[str, str] | None = None
    # files of the logs job to upload with the bug (relative names); none by default
    attach_logs: list[str] = field(default_factory=list[str])


def _validate_attach_logs(logs_dir: Path, names: Sequence[str]) -> list[str]:
    root = logs_dir.resolve()
    out: list[str] = []
    for name in dict.fromkeys(names):
        path = logs_dir / name
        if (
            Path(name).is_absolute()
            or path.is_symlink()
            or not path.resolve().is_relative_to(root)
            or not path.is_file()
        ):
            raise ValueError(
                f"Cannot attach {name!r}: not a collected log file (see list_collected_logs())"
            )
        out.append(Path(name).as_posix())
    return out


class BugTracker(Protocol):
    def submit(self, draft: Draft, logs_dir: Path | None) -> dict[str, Any]: ...


class BugService:
    def __init__(
        self,
        trackers: Mapping[str, BugTracker],
        *,
        job_dir: Callable[[str], Path],
        redact: Callable[[str], str] = lambda s: s,
        default_tracker: str = "launchpad",
        default_project: str | None = None,
        draft_ttl: float = 3600,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._trackers = trackers
        self._job_dir = job_dir
        self._redact = redact
        self._default_tracker = default_tracker
        self._default_project = default_project
        self._ttl = draft_ttl
        self._now = now
        self._drafts: dict[str, Draft] = {}

    @property
    def default_tracker(self) -> str:
        return self._default_tracker

    def _tracker(self, name: str) -> BugTracker:
        try:
            return self._trackers[name]
        except KeyError:
            raise ValueError(
                f"Unknown tracker {name!r}; expected one of {sorted(self._trackers)}"
            ) from None

    def draft_bug(
        self,
        title: str,
        description: str,
        tags: Sequence[str],
        logs_job_id: str | None,
        tracker: str | None = None,
        project: str | None = None,
        *,
        severity: str = "highest",
        issue_file_time: str = "immediate",
        platform_tags: Sequence[str] = (),
        impacted_features: Sequence[str] = (),
        impacted_vendors: Sequence[str] = (),
        assignee: str | None = None,
        status: str = "New",
        series: str | None = None,
        submission_id: str | None = None,
        test_case: str | None = None,
        parts: Mapping[str, str] | None = None,
        attach_logs: Sequence[str] = (),
    ) -> str:
        draft = self._build(
            secrets.token_urlsafe(12),
            self._now(),
            title=title,
            description=description,
            tags=tags,
            logs_job_id=logs_job_id,
            tracker=tracker,
            project=project,
            severity=severity,
            issue_file_time=issue_file_time,
            platform_tags=platform_tags,
            impacted_features=impacted_features,
            impacted_vendors=impacted_vendors,
            assignee=assignee,
            status=status,
            series=series,
            submission_id=submission_id,
            test_case=test_case,
            parts=parts,
            attach_logs=attach_logs,
        )
        self._drafts[draft.draft_id] = draft
        return draft.draft_id

    def update_draft(self, draft_id: str, **changes: Any) -> Draft:
        """Replace fields of an existing draft (same draft_id), with the same validation"""
        old = self.get_draft(draft_id)
        unknown = set(changes) - set(_UPDATABLE)
        if unknown:
            raise ValueError(f"Cannot update {sorted(unknown)}")
        values = {f: getattr(old, f) for f in _UPDATABLE}
        values.update({k: v for k, v in changes.items() if v is not None})
        draft = self._build(old.draft_id, old.created_at, **values)
        self._drafts[draft_id] = draft
        return draft

    def _build(
        self,
        draft_id: str,
        created_at: float,
        *,
        title: str,
        description: str,
        tags: Sequence[str],
        logs_job_id: str | None,
        tracker: str | None,
        project: str | None,
        severity: str,
        issue_file_time: str,
        platform_tags: Sequence[str],
        impacted_features: Sequence[str],
        impacted_vendors: Sequence[str],
        assignee: str | None,
        status: str,
        series: str | None,
        submission_id: str | None,
        test_case: str | None,
        parts: Mapping[str, str] | None,
        attach_logs: Sequence[str],
    ) -> Draft:
        title, description = title.strip(), description.strip()
        if not title or len(title) > MAX_TITLE_LEN:
            raise ValueError(f"title must be 1-{MAX_TITLE_LEN} characters")
        if not description:
            raise ValueError("description must not be empty")
        if len(tags) > MAX_TAGS or not all(_TAG_RE.fullmatch(t) for t in tags):
            raise ValueError(
                f"tags must be at most {MAX_TAGS} items of letters, digits, '.', '_', '+', '-'"
            )
        if severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {list(SEVERITIES)}")
        if issue_file_time not in ISSUE_FILE_TIMES:
            raise ValueError(f"issue_file_time must be one of {list(ISSUE_FILE_TIMES)}")
        if status not in BUG_STATUSES:
            raise ValueError(f"status must be one of {list(BUG_STATUSES)}")
        for name, values in (
            ("platform_tags", platform_tags),
            ("impacted_features", impacted_features),
            ("impacted_vendors", impacted_vendors),
        ):
            if len(values) > MAX_TAGS or not all(_TAG_RE.fullmatch(v) for v in values):
                raise ValueError(
                    f"{name} must be words without spaces or special characters"
                )
        if assignee is not None and not _ASSIGNEE_RE.fullmatch(assignee):
            raise ValueError(
                "assignee must be a Launchpad ID or a Jira email, without spaces"
            )
        tracker = tracker or self._default_tracker
        self._tracker(tracker)
        if logs_job_id is not None:
            logs_dir = self._job_dir(logs_job_id)  # raises ValueError if unknown
        if attach_logs:
            if logs_job_id is None:
                raise ValueError("attach_logs needs a logs_job_id")
            attach_logs = _validate_attach_logs(logs_dir, attach_logs)

        return Draft(
            draft_id=draft_id,
            title=self._redact(title),
            description=self._redact(description),
            tags=list(tags),
            logs_job_id=logs_job_id,
            tracker=tracker,
            project=project or self._default_project,
            created_at=created_at,
            severity=severity,  # pyright: ignore[reportArgumentType]
            issue_file_time=issue_file_time,  # pyright: ignore[reportArgumentType]
            platform_tags=list(platform_tags),
            impacted_features=list(impacted_features),
            impacted_vendors=list(impacted_vendors),
            assignee=assignee or None,
            status=status,  # pyright: ignore[reportArgumentType]
            series=series or None,
            submission_id=submission_id,
            test_case=test_case,
            parts=dict(parts) if parts else None,
            attach_logs=list(attach_logs),
        )

    def has_drafts(self) -> bool:
        return bool(self._drafts)

    def get_draft(self, draft_id: str) -> Draft:
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise ValueError(f"Unknown draft_id {draft_id!r}; call draft_bug() first")
        if self._now() - draft.created_at > self._ttl:
            del self._drafts[draft_id]
            raise ValueError(f"The draft {draft_id!r} expired; call draft_bug() again")
        return draft

    def confirm_submission(self, draft_id: str) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        logs_dir = (
            self._job_dir(draft.logs_job_id)
            if draft.logs_job_id and draft.attach_logs
            else None
        )
        result = self._tracker(draft.tracker).submit(draft, logs_dir)
        # only consume the draft after success so a transient failure can be retried
        del self._drafts[draft_id]
        return result
