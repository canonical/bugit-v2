from collections.abc import Generator
from pathlib import Path
from typing import override
from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.models.bug_report import BugReport


class LocalFileSubmitter(BugReportSubmitter[None, Path]):
    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage, None, Path]:
        return super().submit(bug_report)

    @override
    def upload_attachment(self, attachment_file: Path) -> str | None:
        return super().upload_attachment(attachment_file)

    @override
    def bug_exists(self, bug_id: str) -> bool:
        return False

    @override
    def get_cached_credentials(self) -> None:
        return None

    @property
    @override
    def bug_url(self) -> str:
        return ""
