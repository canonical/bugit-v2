"""Bug report layout, identical to what the bugit-v2 TUI editor produces"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final

from bugit_v2.mcp_server.collector import collector_for_file
from bugit_v2.models.bug_report import pretty_issue_file_times, pretty_severities
from bugit_v2.utils.constants import MAX_JOB_OUTPUT_LEN

if TYPE_CHECKING:
    from bugit_v2.mcp_server.bugs import Draft

# same keys and order as BugReportScreen.initial_report
SECTION_NAMES: Final = (
    "Summary",
    "Steps to Reproduce",
    "Expected Result",
    "Actual Result",
    "Failure Rate",
    "Affected Test Cases",
    "Additional Information",
)

MAX_PREVIEW_OUTPUT = 2000

# what "highest" is called on each tracker, same as the TUI's report editor
HIGHEST_BY_TRACKER: Final = {
    "jira": "Highest",
    "launchpad": "Critical",
    "local": "Highest / Critical",
}


def severity_label(tracker: str, severity: str) -> str:
    if severity == "highest":
        return HIGHEST_BY_TRACKER.get(tracker, pretty_severities["highest"])
    return pretty_severities[severity]  # pyright: ignore[reportArgumentType]


def attachment_archives(files: Sequence[str]) -> str:
    """The <collector>.tar.xz archives submit uploads, with the files in each"""
    groups: dict[str, list[str]] = {}
    for f in files:
        groups.setdefault(collector_for_file(f), []).append(f)
    return "; ".join(f"{g}.tar.xz ({', '.join(fs)})" for g, fs in groups.items())


def render_description(
    sections: Mapping[str, str],
    *,
    test_case: str | None,
    cid: str | None,
    sku: str | None,
    machine_info: Mapping[str, str],
    extra_info: str = "",
) -> str:
    unknown = set(sections) - set(SECTION_NAMES)
    if unknown:
        raise ValueError(f"Unknown report sections: {sorted(unknown)}")
    filled = {name: sections.get(name, "").strip() for name in SECTION_NAMES}
    filled["Affected Test Cases"] = filled["Affected Test Cases"] or test_case or ""
    filled["Additional Information"] = "\n".join(
        [
            f"CID: {cid or ''}",
            f"SKU: {sku or ''}",
            *(f"{k}: {v}" for k, v in machine_info.items()),
            *([extra_info.strip()] if extra_info.strip() else []),
        ]
    )
    return "\n".join(f"[{k}]\n" + v + ("\n" if v else "") for k, v in filled.items())


def _job_output_block(tracker: str, job_output: Mapping[str, str]) -> str:
    parts: list[str] = []
    for k, v in job_output.items():
        text = str(v)
        if len(text) >= MAX_JOB_OUTPUT_LEN:
            text = (
                f"Job {k} is too long to fit inside the description. "
                f"It's in the job_{k}.txt file in the attachments."
            )
        if tracker == "jira":
            parts.append(f"*Job {k}*\n{{noformat}}{text}{{noformat}}")
        else:
            parts.append(f"Job {k}\n----\n{text or f'No {k} was found for this job'}")
    return "\n\n".join(parts)


def format_preview(
    draft: "Draft", job_output: Mapping[str, str] | None
) -> str:  # noqa: F821
    """What the user is about to file, including what the submitter appends"""

    def csv(items: object) -> str:
        return (
            ", ".join(items) if items else ""
        )  # pyright: ignore[reportGeneralTypeIssues]

    lines = [
        f"Tracker: {draft.tracker}",
        f"Project: {draft.project or ''}",
        f"Severity: {severity_label(draft.tracker, draft.severity)}",
        f"Stage: {pretty_issue_file_times[draft.issue_file_time]}",
        f"Platform tags: {csv(draft.platform_tags)}",
        f"Additional tags: {csv(draft.tags)}",
        f"Assignee: {draft.assignee or ''}",
        f"Impacted features: {csv(draft.impacted_features)}",
        f"Impacted vendors: {csv(draft.impacted_vendors)}",
    ]
    lines.append(
        f"Attached logs (tar.xz): {attachment_archives(draft.attach_logs) or 'none'}"
    )
    if draft.tracker == "launchpad":
        lines += [f"Status: {draft.status}", f"Series: {draft.series or ''}"]
    out = "\n".join(lines) + f"\n\nTitle: {draft.title}\n\n{draft.description}"
    out += f"\n\n[Stage]\n{pretty_issue_file_times[draft.issue_file_time]}"
    if job_output:
        out += "\n\n[Job Output]\n\n" + _job_output_block(draft.tracker, job_output)
    return out
