from collections.abc import Generator
from dataclasses import asdict
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import final, override
from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.models.bug_report import SEVERITIES, BugReport


"""
A local .gz file should have

bug-report.gz
    bug-report.txt
    attachment1.tar
    attachment2.tar
    checkbox-session.tar


"""


@final
class LocalFileSubmitter(BugReportSubmitter[None]):
    name = "local_file_submitter"
    display_name = "Local File Submitter"
    severity_name_map = {sev: sev for sev in SEVERITIES}
    steps = 1

    def __init__(self) -> None:
        super().__init__()
        self.working_dir = TemporaryDirectory()

    def __del__(self):
        self.working_dir.cleanup()

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage, None, None]:
        report_json_path = Path(self.working_dir.name) / "bug-report.json"
        with open(report_json_path, "w") as f:
            d = asdict(bug_report, dict_factory=BugReport.dict_factory)
            json.dump(d, f)

        yield f"Dumped bug report to {report_json_path}"

    @override
    def upload_attachment(self, attachment_file: Path) -> str | None:
        shutil.copy(attachment_file, self.working_dir.name)

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
