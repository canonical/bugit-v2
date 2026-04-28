import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal, Self
from uuid import UUID

from pydantic import BaseModel

from bugit_v2.checkbox_utils.checkbox_session import CheckboxSession
from bugit_v2.checkbox_utils.models import SimpleCheckboxSubmission
from bugit_v2.checkbox_utils.submission_extractor import read_simple_submission

# Internal representation of bug severity
Severity = Literal["highest", "high", "medium", "low", "lowest"]
SEVERITIES: Final[tuple[Severity, ...]] = Severity.__args__
# values are what appears on screen in report editor
pretty_severities: Mapping[Severity, str] = {
    "highest": "Critical (LP) / Highest (Jira)",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "lowest": "Lowest",
}

# Internal representation of when the issue was filed
IssueFileTime = Literal["immediate", "after_reboot", "later"]
ISSUE_FILE_TIMES: Final[tuple[IssueFileTime, ...]] = IssueFileTime.__args__
pretty_issue_file_times: Mapping[IssueFileTime, str] = {
    "immediate": "Right after it happened",
    "after_reboot": "Device froze, reported after a reboot",
    "later": "At a later stage",
}

BugStatus = Literal["New", "Confirmed"]  # has to be capitalized
BUG_STATUSES: Final[tuple[BugStatus, ...]] = BugStatus.__args__


# log choices
LogName = Literal[
    # mock
    "immediate",
    "fast1",
    "fast2",
    "slow1",
    "slow2",
    "always-fail",
    # real
    "journalctl-7-days",
    "journalctl-3-days",
    "checkbox-session",
    "checkbox-submission",
    "nvidia-bug-report",
    "acpidump",
    "dmesg",
    "snap-list",
    "snap-debug",
    "long-job-outputs",
    "oem-getlogs",
]
LOG_NAMES: tuple[LogName, ...] = LogName.__args__
# pretty log names should be specified in the LogCollector class


@dataclass(slots=True, frozen=True)
class BugReport:
    """
    The data model for a bug report.
    Avoid attaching methods to this class unless it's a simple getter
    """

    report_id: UUID  # internal uuid, used for keeping track of auto saves
    # required
    title: str
    description: str
    project: str
    severity: Severity
    issue_file_time: IssueFileTime
    # optionals
    checkbox_session: CheckboxSession | None
    checkbox_submission: SimpleCheckboxSubmission | None
    job_id: str | None
    assignee: str | None = None  # appear as unassigned if None
    platform_tags: Sequence[str] = field(default_factory=list[str])
    additional_tags: Sequence[str] = field(default_factory=list[str])
    status: BugStatus = "New"  # only used in launchpad
    series: str | None = None  # only used in launchpad
    # selections
    logs_to_include: Sequence[LogName] = field(default_factory=list[LogName])
    impacted_features: Sequence[str] = field(default_factory=list[str])
    impacted_vendors: Sequence[str] = field(default_factory=list[str])
    # for recovery only
    source: Literal["editor", "recovery"] = "editor"

    def get_with_type[T](self, attr: str, expected_type: type[T]) -> T:
        value = getattr(self, attr)  # pyright: ignore[reportAny]
        if isinstance(value, expected_type):
            return value
        raise TypeError(
            f"Expected {expected_type}, but got {type(value)}"  # pyright: ignore[reportAny]
        )


class SerializableBugReport(BaseModel):
    report_id: UUID  # internal uuid, used for keeping track of auto saves
    last_updated_timestamp: int
    title: str
    description: str
    project: str
    severity: Severity
    issue_file_time: IssueFileTime
    checkbox_session: Path | None
    checkbox_submission: Path | None
    job_id: str | None
    assignee: str | None
    platform_tags: Sequence[str]
    additional_tags: Sequence[str]
    status: BugStatus
    series: str | None
    # selections
    logs_to_include: Sequence[LogName]
    impacted_features: Sequence[str]
    impacted_vendors: Sequence[str]

    @classmethod
    def from_bug_report(cls, r: BugReport) -> Self:
        return cls(
            report_id=r.report_id,
            last_updated_timestamp=int(time.time()),
            title=r.title,
            description=r.description,
            project=r.project,
            severity=r.severity,
            issue_file_time=r.issue_file_time,
            checkbox_session=r.checkbox_session
            and r.checkbox_session.session_path.absolute(),
            checkbox_submission=r.checkbox_submission
            and r.checkbox_submission.submission_path.absolute(),
            job_id=r.job_id,
            assignee=r.assignee,
            platform_tags=r.platform_tags,
            additional_tags=r.additional_tags,
            status=r.status,
            series=r.series,
            logs_to_include=r.logs_to_include,
            impacted_features=r.impacted_features,
            impacted_vendors=r.impacted_vendors,
        )

    def to_bug_report(self) -> BugReport:
        # job_id conversion to NULL_JOB is handled separately
        return BugReport(
            self.report_id,
            self.title,
            self.description,
            self.project,
            self.severity,
            self.issue_file_time,
            self.checkbox_session and CheckboxSession(self.checkbox_session),
            self.checkbox_submission
            and read_simple_submission(self.checkbox_submission),
            self.job_id,
            self.assignee,
            self.platform_tags,
            self.additional_tags,
            self.status,
            self.series,
            self.logs_to_include,
            self.impacted_features,
            self.impacted_vendors,
            "recovery",
        )
