from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, TypeVar

from bugit_v2.checkbox_utils import Session

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
    "sos-report",
    "oem-get-logs",
    "immediate",
    "fast1",
    "fast2",
    "slow1",
    "slow2",
    "always-fail",
    "checkbox-session",
    "nvidia-bug-report",
]
LOG_NAMES: tuple[LogName, ...] = LogName.__args__
# pretty log names should be specified in the LogCollector class

T = TypeVar("T")


@dataclass(slots=True)
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
    checkbox_session: Session
    # optionals
    assignee: str | None = None  # appear as unassigned if None
    platform_tags: Sequence[str] = field(default_factory=list[str])
    additional_tags: Sequence[str] = field(default_factory=list[str])
    status: BugStatus = "Confirmed"  # only used in launchpad
    series: str | None = None  # only used in launchpad
    # selections
    logs_to_include: Sequence[LogName] = field(default_factory=list[LogName])
    impacted_features: Sequence[str] = field(default_factory=list[str])
    impacted_vendors: Sequence[str] = field(default_factory=list[str])

    def get_with_type(self, attr: str, expected_type: type[T]) -> T:
        value = getattr(self, attr)  # pyright: ignore[reportAny]
        if type(value) is expected_type:  # pyright: ignore[reportAny]
            return value
        raise TypeError(
            f"Expected {expected_type}, but got {type(value)}"  # pyright: ignore[reportAny]
        )
