from collections.abc import Generator
from pathlib import Path
from typing import final, override

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.models.bug_report import BugReport


@final
class LaunchpadSubmitter(BugReportSubmitter[None, None]):
    name = "launchpad_submitter"
    severity_name_map = {
        "highest": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "lowest": "Wishlist",
    }
    steps = 5

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage | Exception, None, None]:
        return super().submit(bug_report)

    @override
    def upload_attachments(
        self, attachment_dir: Path
    ) -> Generator[str | AdvanceMessage | Exception, None, None]:
        return super().upload_attachments(attachment_dir)

    @property
    @override
    def bug_url(self) -> str:
        return super().bug_url

    @override
    def get_cached_credentials(self) -> None:
        return None
