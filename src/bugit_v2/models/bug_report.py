from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel

from bugit_v2.checkbox_utils import Session
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
    "nvidia-bug-report",
    "acpidump",
    "dmesg",
    "snap-list",
]
LOG_NAMES: tuple[LogName, ...] = LogName.__args__
# pretty log names should be specified in the LogCollector class


@dataclass(slots=True, frozen=True)
class BugReport:
    """
    The data model for a bug report.
    Avoid attaching methods to this class unless it's a simple getter
    """

    # required
    title: str
    description: str
    project: str
    severity: Severity
    issue_file_time: IssueFileTime
    # optionals
    checkbox_session: Session | None
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

    @staticmethod
    def dict_factory(x: list[tuple[str, Any]]) -> dict[str, Any]:
        o = {}
        for k, v in x:
            if k == "checkbox_submission":
                if v is None:
                    o[k] = None
                else:
                    # dataclass already converted into dict
                    assert type(v) is dict
                    o[k] = str(v["submission_path"].absolute())
            elif k == "checkbox_session":
                if isinstance(v, Session):
                    o[k] = str(v.session_path.absolute())
                else:
                    o[k] = None
            else:
                o[k] = v
        return o


@dataclass(slots=True, frozen=True)
class PartialBugReport:
    """
    The data model for a partial bug report used when reopening a bug.
    Avoid attaching methods to this class unless it's a simple getter
    """

    # required
    description: str
    issue_file_time: IssueFileTime
    # optionals
    checkbox_session: Session | None
    checkbox_submission: SimpleCheckboxSubmission | None
    assignee: str | None = None  # appear as unassigned if None
    platform_tags: Sequence[str] = field(default_factory=list[str])
    additional_tags: Sequence[str] = field(default_factory=list[str])
    # selections
    logs_to_include: Sequence[LogName] = field(default_factory=list[LogName])

    def get_with_type[T](self, attr: str, expected_type: type[T]) -> T:
        value = getattr(self, attr)  # pyright: ignore[reportAny]
        if type(value) is expected_type:  # pyright: ignore[reportAny]
            return value
        raise TypeError(
            f"Expected {expected_type}, but got {type(value)}"  # pyright: ignore[reportAny]
        )


class BugReportAutoSaveData(BaseModel):
    title: str
    description: str
    project: str
    severity: Severity
    issue_file_time: IssueFileTime
    checkbox_session: Path | None
    checkbox_submission: Path | None
    job_id: str | None
    assignee: str | None
    platform_tags: list[str]
    additional_tags: list[str]
    status: BugStatus
    series: str | None
    # selections
    logs_to_include: list[LogName]
    impacted_features: list[str]
    impacted_vendors: list[str]


def recover_from_autosave(
    autosave_data: BugReportAutoSaveData,
) -> BugReport:
    # job_id is handled separately
    return BugReport(
        autosave_data.title,
        autosave_data.description,
        autosave_data.project,
        autosave_data.severity,
        autosave_data.issue_file_time,
        autosave_data.checkbox_session
        and Session(autosave_data.checkbox_session),
        autosave_data.checkbox_submission
        and read_simple_submission(autosave_data.checkbox_submission),
        autosave_data.job_id,
        autosave_data.assignee,
        autosave_data.platform_tags,
        autosave_data.additional_tags,
        autosave_data.status,
        autosave_data.series,
        autosave_data.logs_to_include,
        autosave_data.impacted_features,
        autosave_data.impacted_vendors,
        "recovery",
    )
