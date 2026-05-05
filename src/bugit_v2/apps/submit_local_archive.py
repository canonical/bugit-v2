import json
import shutil
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, final

import typer
from textual import work
from textual.app import App
from textual.driver import Driver
from textual.types import CSSPathType
from typing_extensions import Annotated

from bugit_v2.bug_report_submitters.bug_report_submitter import BugReportSubmitter
from bugit_v2.bug_report_submitters.jira_submitter import JiraSubmitter
from bugit_v2.bug_report_submitters.launchpad_submitter import LaunchpadSubmitter
from bugit_v2.bug_report_submitters.local_file_submitter import SERIALIZED_REPORT_NAME
from bugit_v2.bug_report_submitters.mock_jira import MockJiraSubmitter
from bugit_v2.bug_report_submitters.mock_lp import MockLaunchpadSubmitter
from bugit_v2.models.bug_report import BugReport, SerializableBugReport
from bugit_v2.screens.submission_progress_screen import SubmissionProgressScreen
from bugit_v2.utils import is_prod, is_snap

# these files or directories in the working_dir should not be uploaded to jira/lp
ATTACHMENT_BLACKLIST = [
    SERIALIZED_REPORT_NAME,
]

app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=not is_prod(),
    pretty_exceptions_show_locals=not is_prod(),
    no_args_is_help=True,
    help="Submit the archive made by [yellow]bugit-v2 local[/]",
    add_completion=not is_snap(),  # the built-in ones doesn't work in snap
)


@final
class SubmitOnlyApp(App[None]):
    def __init__(
        self,
        report: BugReport,
        submitter: BugReportSubmitter[Any],
        attachment_dir: Path,
        # ---
        driver_class: type[Driver] | None = None,
        css_path: CSSPathType | None = None,
        watch_css: bool = False,
        ansi_color: bool = False,
    ):
        super().__init__(driver_class, css_path, watch_css, ansi_color)
        self.report = report
        self.submitter = submitter
        self.attachment_dir = attachment_dir

    @work
    async def on_mount(self):
        await self.push_screen_wait(
            SubmissionProgressScreen(
                bug_report=self.report,
                submitter=self.submitter,
                attachment_dir=self.attachment_dir,
                mode="app",
            ),
        )
        self.exit()


def build_bug_report_from_archive(file: Path, working_dir: Path) -> BugReport:
    with tarfile.open(file, "r:gz") as report_tar:
        report_tar.extractall(working_dir)

        if not (working_dir / SERIALIZED_REPORT_NAME).exists():
            raise typer.Abort(
                f"{SERIALIZED_REPORT_NAME} doesn't exist in {file}, cannot continue"
            )

        with open(working_dir / SERIALIZED_REPORT_NAME) as f:
            serialized_report = SerializableBugReport.model_validate(
                json.load(f), extra="allow"
            )
            if (
                serialized_report.checkbox_session
                and not (working_dir / "checkbox_session.tar.gz").exists()
            ):
                typer.echo(
                    f"The bug report requires a checkbox session, but it wasn't in {file}",
                    err=True,
                )
                raise typer.Exit(1)

            with tarfile.open(working_dir / "checkbox_session.tar.gz", "r:gz") as cbs:
                cbs.extractall(working_dir / "checkbox_session")

            serialized_report.checkbox_session = working_dir / "checkbox_session"
            return serialized_report.to_bug_report()


@app.command("lp", help="Submit to Launchpad")
@app.command("jira", help="Submit to Jira")
def main(
    ctx: typer.Context,
    file: Annotated[
        Path,
        typer.Argument(
            help=(
                "The .tar.gz file created by [u]bugit-v2 local[/]. "
                + "A Jira/Launchpad bug report will be created based on the content of this archive."
            ),
            exists=True,
            dir_okay=False,
            file_okay=True,
            readable=True,
            resolve_path=True,
        ),
    ],
):
    match ctx.command.name:
        case "lp":
            submitter = LaunchpadSubmitter() if is_prod() else MockLaunchpadSubmitter()
        case "jira":
            submitter = JiraSubmitter() if is_prod() else MockJiraSubmitter()
        case _:
            typer.echo(f"Unexpected command '{ctx.command.name}'")
            raise typer.Exit(1)

    with (
        TemporaryDirectory() as temp_extract_dir_str,
        TemporaryDirectory() as temp_attachment_dir_str,
    ):
        temp_extract_dir = Path(temp_extract_dir_str)
        temp_attachment_dir = Path(temp_attachment_dir_str)

        report = build_bug_report_from_archive(file, temp_extract_dir)

        for file in temp_extract_dir.iterdir():
            if not file.is_file():
                continue
            if file.name in ATTACHMENT_BLACKLIST:
                continue
            if (
                file.name == "checkbox_session.tar.gz"
                and "checkbox-session" not in report.logs_to_include
            ):
                continue

            (temp_attachment_dir / file.name).symlink_to(file)

        SubmitOnlyApp(report, submitter, temp_attachment_dir).run()


if __name__ == "__main__":
    app(prog_name="bugit.submit" if is_snap() else "bugit-submit")
