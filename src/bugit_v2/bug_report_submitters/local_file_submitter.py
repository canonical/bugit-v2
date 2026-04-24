from collections.abc import Generator
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import tarfile
from tempfile import TemporaryDirectory
from typing import final, override
from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.models.bug_report import SEVERITIES, BugReport


@final
class LocalFileSubmitter(BugReportSubmitter[None]):
    name = "local_file_submitter"
    display_name = "Local File Submitter"
    severity_name_map = {sev: sev for sev in SEVERITIES}
    steps = 1

    final_archive_name: str | None = None

    WRAPPER_DIR = "tar-contents"

    def __init__(self) -> None:
        super().__init__()
        self.working_dir = TemporaryDirectory(delete=False)
        os.makedirs(Path(self.working_dir.name) / self.WRAPPER_DIR, exist_ok=True)

    def __del__(self):
        shutil.rmtree(Path(self.working_dir.name) / self.WRAPPER_DIR, True)

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage, None, None]:
        """
        A local .gz file should have

        bug-report.gz
            bug-report.txt
            attachment1.tar
            attachment2.tar
            checkbox-session.tar
        """

        working_dir = Path(self.working_dir.name)
        report_json_path = working_dir / self.WRAPPER_DIR / "bug-report.json"
        report_json = asdict(bug_report, dict_factory=bug_report.dict_factory)

        # must bring the checkbox session if one was referenced
        # even if the user didn't select it
        # do check for selection because we don't want parallel writes
        if bug_report.checkbox_session:
            # the bugit.submit command should read this relative to the
            # file produced by this submitter
            report_json["checkbox_session"] = "checkbox_session.tar.gz"
            if "checkbox-session" not in bug_report.logs_to_include:
                with tarfile.open(
                    working_dir / self.WRAPPER_DIR / "checkbox_session.tar.gz", "w:gz"
                ) as f:
                    f.add(bug_report.checkbox_session.session_path)

        with open(report_json_path, "w") as f:
            json.dump(report_json, f)

        yield AdvanceMessage(f"Dumped bug report to {report_json_path}")
        self.final_archive_name = f"bugit-bug-report-{bug_report.report_id}"

    @override
    def upload_attachment(self, attachment_file: Path) -> str | None:
        shutil.copy(attachment_file, Path(self.working_dir.name) / self.WRAPPER_DIR)

    @override
    def bug_exists(self, bug_id: str) -> bool:
        return False

    @override
    def get_cached_credentials(self) -> None:
        return None

    @property
    @override
    def bug_url(self) -> str:
        assert self.final_archive_name, "Report archive not created"
        return str(Path().absolute() / self.final_archive_name)

    @override
    def finalize(self) -> str:
        assert (
            self.final_archive_name
        ), "Unexpected call before final archive name can be determined"

        working_dir = Path(self.working_dir.name)
        shutil.make_archive(
            self.final_archive_name,
            root_dir=working_dir / self.WRAPPER_DIR,
            format="gztar",
        )
        if (Path().absolute() / self.final_archive_name).exists():
            return f"The bug report archive is at {Path().absolute() / self.final_archive_name}.tar.gz"
        else:
            raise RuntimeError("Failed to create archive")
