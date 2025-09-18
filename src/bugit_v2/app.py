from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, final

import typer
from textual import work
from textual.app import App
from textual.binding import Binding
from textual.content import Content
from textual.driver import Driver
from textual.reactive import var
from textual.types import CSSPathType
from typing_extensions import Annotated, override

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    BugReportSubmitter,
)
from bugit_v2.bug_report_submitters.jira_submitter import JiraSubmitter
from bugit_v2.bug_report_submitters.launchpad_submitter import (
    LaunchpadSubmitter,
)
from bugit_v2.bug_report_submitters.mock_jira import MockJiraSubmitter
from bugit_v2.bug_report_submitters.mock_lp import MockLaunchpadSubmitter
from bugit_v2.checkbox_utils import Session, get_checkbox_version
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.bug_report import BugReport, PartialBugReport
from bugit_v2.screens.bug_report_screen import BugReportScreen
from bugit_v2.screens.job_selection_screen import JobSelectionScreen
from bugit_v2.screens.reopen_bug_editor_screen import ReopenBugEditorScreen
from bugit_v2.screens.reopen_precheck_screen import ReopenPreCheckScreen
from bugit_v2.screens.session_selection_screen import SessionSelectionScreen
from bugit_v2.screens.submission_progress_screen import (
    ReturnScreenChoice,
    SubmissionProgressScreen,
)
from bugit_v2.utils import is_prod, is_snap
from bugit_v2.utils.constants import NullSelection
from bugit_v2.utils.validations import before_entry_check, is_cid

cli_app = typer.Typer(
    help="Bugit is a tool for creating bug reports on Launchpad and Jira",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=not is_prod(),
    pretty_exceptions_show_locals=not is_prod(),
)


def strip(value: str | None) -> str | None:
    return value and value.strip()


def cid_check(value: str | None) -> str | None:
    if value is None:
        return None
    if not is_cid(value):
        raise typer.BadParameter(
            f"Invalid CID: '{value}'. "
            + "CID should look like 202408-12345 "
            + "(6 digits, dash, then 5 digits)",
        )
    return value.strip()


def alnum_check(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.isalnum():
        raise typer.BadParameter(
            f"Invalid project: '{value}'. "
            + "Project name should be an alphanumeric string."
        )
    return value.strip()


def assignee_str_check(value: str | None) -> str | None:
    if value is None:
        return None
    # not going to check for email, way too complicated
    # we'll just send it to jira and let jira figure it out
    if value.startswith("lp:"):
        raise typer.BadParameter('Assignee should not start with "lp:"')
    return value.strip()


@dataclass(slots=True, frozen=True)
class AppState:
    """
    The global app state. Check the watch_state function to see all possible
    state combinations
    """

    session: Session | Literal[NullSelection.NO_SESSION] | None = None
    job_id: str | Literal[NullSelection.NO_JOB] | None = None
    bug_report: BugReport | PartialBugReport | None = None


@final
class BugitApp(App[None]):
    state = var(AppState())
    args: AppArgs
    # Any doesn't matter here
    submitter_class: type[
        BugReportSubmitter[Any, Any]  # pyright: ignore[reportExplicitAny]
    ]
    bug_report_backup: BugReport | None = None
    partial_bug_report_backup: PartialBugReport | None = None
    BINDINGS = [Binding("alt+left", "go_back", "Go Back")]

    def __init__(
        self,
        args: AppArgs,
        driver_class: type[Driver] | None = None,
        css_path: CSSPathType | None = None,
        watch_css: bool = False,
        ansi_color: bool = False,
    ):
        self.args = args
        match args.submitter:
            case "jira":
                self.submitter_class = (
                    JiraSubmitter if is_prod() else MockJiraSubmitter
                )
            case "lp":
                self.submitter_class = (
                    LaunchpadSubmitter if is_prod() else MockLaunchpadSubmitter
                )

        super().__init__(driver_class, css_path, watch_css, ansi_color)

    @work(thread=True)
    def on_mount(self) -> None:
        self.theme = "solarized-light"
        if is_prod():
            self.title = "Bugit V2"
        else:
            self.title = "Bugit V2 🐛🐛 DEBUG MODE 🐛🐛"

        # snap checkbox takes a while to respond especially if it's the
        # 1st use after reboot
        if (version := get_checkbox_version()) is not None:
            self.sub_title = f"Checkbox {version}"

    @override
    def format_title(self, title: str, sub_title: str) -> Content:
        match (title, sub_title, self.args.bug_to_reopen):
            case (str(t), str(s), str(b)):
                return Content.assemble(
                    Content(t),
                    (" - ", "dim"),
                    Content(s).stylize("$secondary"),
                    (" - ", "dim"),
                    Content(f"Reopen {b}").stylize("dim"),
                )
            case (str(t), str(s), None) if s:
                return Content.assemble(
                    Content(t),
                    (" - ", "dim"),
                    Content(s).stylize("$secondary"),
                )
            case (str(t), str(s), None) if not s:
                return Content(t)
            case _:
                return self.app.format_title(title, sub_title)

    @override
    def _handle_exception(self, error: Exception) -> None:
        if is_prod() or is_snap():
            raise SystemExit(error)
        else:
            # don't use pretty exception in prod, it shows local vars
            # if not in a snap the code is already in the system anyways
            super()._handle_exception(error)

    @work
    async def watch_state(self) -> None:
        """Push different screens based on the state"""

        def _write_state(new_state: AppState):
            self.state = new_state

        match self.state:
            case AppState(session=None, job_id=None, bug_report=None):
                # init, nothing has been selected yet
                if (
                    b := self.args.bug_to_reopen
                ) is not None and not ReopenPreCheckScreen.already_checked:
                    check_result = await self.push_screen_wait(
                        ReopenPreCheckScreen(self.submitter_class(), self.args)
                    )
                    if check_result is True:
                        self.notify(f"Bug '{b}' exists!")
                    else:
                        msg = f"Bug '{b}' doesn't exist or you don't have permission. "
                        if isinstance(check_result, Exception):
                            msg += f"Error is: {repr(check_result)}"
                        self.exit(return_code=1, message=msg)

                def after_session_select(
                    rv: Path | Literal[NullSelection.NO_SESSION] | None,
                ):
                    match rv:
                        case Path():
                            self.state = AppState(Session(rv))
                        case NullSelection.NO_SESSION:
                            self.state = AppState(rv, NullSelection.NO_JOB)
                        case None:
                            raise RuntimeError(
                                "Session selection should not return None"
                            )

                self.push_screen(
                    SessionSelectionScreen(), after_session_select
                )
            case AppState(
                session=Session() as session, job_id=None, bug_report=None
            ):
                # selected a normal session, should go to job selection
                self.push_screen(
                    JobSelectionScreen(session),
                    lambda job_id: _write_state(AppState(session, job_id)),
                )
            case AppState(
                session=NullSelection.NO_SESSION as session,
                job_id=NullSelection.NO_JOB as job_id,
                bug_report=None,
            ):
                # selected no session, skip to editor with absolutely nothing
                self.push_screen(
                    (
                        ReopenBugEditorScreen(
                            session,
                            job_id,
                            self.args,
                            self.partial_bug_report_backup,
                        )
                        if self.args.bug_to_reopen
                        else BugReportScreen(
                            session,
                            job_id,
                            self.args,
                            self.bug_report_backup,
                        )
                    ),
                    lambda bug_report: _write_state(
                        AppState(
                            session,
                            job_id,
                            bug_report,
                        )
                    ),
                )
            case AppState(
                session=Session() as session,
                job_id=NullSelection.NO_JOB as job_id,
                bug_report=None,
            ):
                # has session, but chose the no job object
                # skip to editor with session
                self.push_screen(
                    (
                        ReopenBugEditorScreen(
                            session,
                            job_id,
                            self.args,
                            self.partial_bug_report_backup,
                        )
                        if self.args.bug_to_reopen
                        else BugReportScreen(
                            session,
                            job_id,
                            self.args,
                            self.bug_report_backup,
                        )
                    ),
                    lambda bug_report: _write_state(
                        AppState(session, job_id, bug_report)
                    ),
                )
            case AppState(
                session=Session() as session,
                job_id=str() as job_id,
                bug_report=None,
            ):
                # normal case, session and job_id were selected
                # go to editor with info
                self.push_screen(
                    (
                        ReopenBugEditorScreen(
                            session,
                            job_id,
                            self.args,
                            self.partial_bug_report_backup,
                        )
                        if self.args.bug_to_reopen
                        else BugReportScreen(
                            session,
                            job_id,
                            self.args,
                            self.bug_report_backup,
                        )
                    ),
                    lambda bug_report: _write_state(
                        AppState(session, job_id, bug_report)
                    ),
                )
            case AppState(
                session=Session() | NullSelection.NO_SESSION as session,
                job_id=str() | NullSelection.NO_JOB as job_id,
                bug_report=BugReport() | PartialBugReport() as br,
            ):
                # returning from the end of submission screen
                # handle the button selections
                def after_submission_finished(
                    return_screen: ReturnScreenChoice | None,
                ):
                    # already submitted, flush the stale backup
                    match return_screen:
                        case None:
                            raise RuntimeError(
                                "Submission screen should not return None"
                            )
                        case "quit":
                            self.exit()
                        case "session":
                            self.bug_report_backup = None
                            self.partial_bug_report_backup = None
                            self.state = AppState()
                        case "job" if session != NullSelection.NO_SESSION:
                            # this is only available when there's a session
                            self.bug_report_backup = None
                            self.partial_bug_report_backup = None
                            self.state = AppState(session)
                        case "report_editor":
                            if isinstance(br, PartialBugReport):
                                self.partial_bug_report_backup = br
                            else:
                                self.bug_report_backup = br

                            self.state = AppState(session, job_id)
                        case _:
                            raise RuntimeError()

                self.push_screen(
                    SubmissionProgressScreen(
                        br,
                        self.submitter_class(),
                    ),
                    after_submission_finished,
                )

            case _:
                raise RuntimeError(f"Impossible state: {self.state}")

    def action_go_back(self) -> None:
        """Handles the `Go Back` button

        This function should only reassign (not modify) the state object and
        let textual automatically re-render
        """
        match self.state:
            case AppState(session=None):
                self.notify("Already at the beginning")
                return
            case AppState(session=Session(), job_id=None):
                # returning from job selection
                self.state = AppState(None, None)
            case AppState(
                session=NullSelection.NO_SESSION,
                job_id=NullSelection.NO_JOB,
                bug_report=None,
            ):
                # returning from editor with nothing selected
                # just go back to the beginning
                self.state = AppState(None, None)
            case AppState(
                session=Session() as session,
                job_id=str() | NullSelection.NO_JOB,
                bug_report=None,
            ):
                # returning from editor with explicit job selection
                # go back to job selection
                self.state = AppState(session, None)
            case AppState(
                session=Session() | NullSelection.NO_SESSION,
                job_id=str() | NullSelection.NO_JOB,
                bug_report=BugReport() | PartialBugReport(),
            ):
                self.notify(
                    title="Cannot go back while a submission is happening",
                    message="But you can force quit with Ctrl+Q",
                )
            case _:
                raise RuntimeError(
                    f"Impossible state when going back: {self.state}"
                )


@cli_app.command("lp", help="Submit a bug to Launchpad")
def launchpad_mode(
    reopen: Annotated[
        int | None,
        typer.Option(
            "-r",
            "--reopen",
            help=(
                "Reopen a bug on Launchpad. "
                "The value for this option should look like 2025684 i.e. just a number"
            ),
        ),
    ] = None,
    cid: Annotated[
        str | None,
        typer.Option(
            "-c",
            "--cid",
            help="Canonical ID (CID) of the device under test",
            file_okay=False,
            dir_okay=False,
            callback=cid_check,
        ),
    ] = None,
    sku: Annotated[
        str | None,
        typer.Option(
            "-k",
            "--sku",
            help="Stock Keeping Unit (SKU) string of the device under test",
            file_okay=False,
            dir_okay=False,
            callback=strip,
        ),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option(
            "-p",
            "--project",
            help="Project name like STELLA, SOMERVILLE. Case sensitive.",
            file_okay=False,
            dir_okay=False,
            callback=alnum_check,
        ),
    ] = None,
    assignee: Annotated[
        str | None,
        typer.Option(
            "-a",
            "--assignee",
            help='Assignee ID. For Launchpad it\'s LP ID, without the "lp:" part',
            file_okay=False,
            dir_okay=False,
            callback=assignee_str_check,
        ),
    ] = None,
    platform_tags: Annotated[
        list[str],
        typer.Option(
            "-pt",
            "--platform-tags",
            help='Platform Tags. They appear under "Components" on Jira',
            file_okay=False,
            dir_okay=False,
        ),
    ] = [],  # pyright: ignore[reportCallInDefaultInitializer]
    tags: Annotated[
        list[str],
        typer.Option(
            "-t",
            "--tags",
            help="Additional tags on Jira",
            file_okay=False,
            dir_okay=False,
        ),
    ] = [],  # pyright: ignore[reportCallInDefaultInitializer]
):
    before_entry_check()
    BugitApp(
        AppArgs(
            "lp", str(reopen), cid, sku, project, assignee, platform_tags, tags
        )
    ).run()


@cli_app.command("jira", help="Submit a bug to Jira")
def jira_mode(
    reopen: Annotated[
        str | None,
        typer.Option(
            "-r",
            "--reopen",
            help=(
                "Reopen a bug on Jira. "
                "The value for this option should look like STELLA-1234"
            ),
        ),
    ] = None,
    cid: Annotated[
        str | None,
        typer.Option(
            "-c",
            "--cid",
            help="Canonical ID (CID) of the device under test",
            file_okay=False,
            dir_okay=False,
            callback=cid_check,
        ),
    ] = None,
    sku: Annotated[
        str | None,
        typer.Option(
            "-k",
            "--sku",
            help="Stock Keeping Unit (SKU) string of the device under test",
            file_okay=False,
            dir_okay=False,
            callback=strip,
        ),
    ] = None,
    project: Annotated[
        str | None,
        typer.Option(
            "-p",
            "--project",
            help="Project name like STELLA, SOMERVILLE. Case sensitive.",
            file_okay=False,
            dir_okay=False,
            callback=alnum_check,
        ),
    ] = None,
    assignee: Annotated[
        str | None,
        typer.Option(
            "-a",
            "--assignee",
            help="Assignee ID. For Jira it's the assignee's email",
            file_okay=False,
            dir_okay=False,
            callback=assignee_str_check,
        ),
    ] = None,
    platform_tags: Annotated[
        list[str],
        typer.Option(
            "-pt",
            "--platform-tags",
            help='Platform Tags. They appear under "Components" on Jira',
            file_okay=False,
            dir_okay=False,
        ),
    ] = [],  # pyright: ignore[reportCallInDefaultInitializer]
    tags: Annotated[
        list[str],
        typer.Option(
            "-t",
            "--tags",
            help="Additional tags on Jira",
            file_okay=False,
            dir_okay=False,
        ),
    ] = [],  # pyright: ignore[reportCallInDefaultInitializer]
):
    before_entry_check()
    BugitApp(
        AppArgs(
            "jira", reopen, cid, sku, project, assignee, platform_tags, tags
        )
    ).run()


if __name__ == "__main__":
    cli_app(prog_name="bugit-v2")
