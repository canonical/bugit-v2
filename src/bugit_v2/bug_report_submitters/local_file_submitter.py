from collections.abc import Generator
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import final, override
from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.models.bug_report import SEVERITIES, BugReport, SerializableBugReport


SERIALIZED_REPORT_NAME = "bug-report.json"


@final
class LocalFileSubmitter(BugReportSubmitter[None]):
    name = "local_file_submitter"
    display_name = "Local File Submitter"
    severity_name_map = {sev: sev for sev in SEVERITIES}
    steps = 1

    # this is determined when submit() finishes
    archive_name: str | None = None
    # this is determined in finalize()
    archive_path: Path | None = None
    finalize_ok = False

    WRAPPER_DIR = "tar-contents"

    def __init__(self) -> None:
        super().__init__()
        self.working_dir = TemporaryDirectory(delete=False)
        os.makedirs(Path(self.working_dir.name) / self.WRAPPER_DIR, exist_ok=True)

    def __del__(self):
        if self.finalize_ok:
            shutil.rmtree(Path(self.working_dir.name), True)

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage, None, None]:
        """
        A local .tar.gz file should have these files

        bug-report.gz
            bug-report.json
            attachment1.tar
            attachment2.tar
            checkbox-session.tar

        `submit()` in this case only serializes the report and force includes
        checkbox-session if not selected.

        `finalize()` creates the actual archive because `submit()` is run in
        parallel to the `upload_attachment()` methods
        """

        working_dir = Path(self.working_dir.name)
        report_json_path = working_dir / self.WRAPPER_DIR / SERIALIZED_REPORT_NAME
        report_json = SerializableBugReport.from_bug_report(bug_report)

        # must bring the checkbox session if one was referenced
        # even if the user didn't select it
        # do check for selection because we don't want parallel writes
        if bug_report.checkbox_session:
            # the bugit.submit command should read this relative to the
            # file produced by this submitter
            report_json.checkbox_session = Path("checkbox_session.tar.gz")
            if "checkbox-session" not in bug_report.logs_to_include:
                shutil.make_archive(
                    str(working_dir / self.WRAPPER_DIR / "checkbox_session"),
                    root_dir=bug_report.checkbox_session.session_path,
                    format="gztar",
                )

        with open(report_json_path, "w") as f:
            f.write(report_json.model_dump_json())

        yield AdvanceMessage(f"Dumped bug report to {report_json_path}")
        self.archive_name = f"bugit-bug-report-{bug_report.report_id}"

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
        assert self.archive_path, "Report archive not created"
        return str(self.archive_path)

    @override
    def finalize(self) -> str:
        assert self.archive_name, "Unexpected call before archive name was determined"

        working_dir = Path(self.working_dir.name)

        try:
            self.archive_path = Path(
                shutil.make_archive(
                    self.archive_name,
                    root_dir=working_dir / self.WRAPPER_DIR,
                    format="gztar",
                )
            )
            assert self.archive_path.exists()
            self.finalize_ok = True
            return f"The bug report archive is at {self.archive_path}"
        except Exception as e:
            raise RuntimeError(
                f"Failed to create archive, you can manually recover the files at {self.working_dir.name}. Original err: {repr(e)}"
            ) from e
