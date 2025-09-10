import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, final

import typer
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.driver import Driver
from textual.reactive import var
from textual.types import CSSPathType
from textual.widgets import Footer, Header, LoadingIndicator
from typing_extensions import override

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
from bugit_v2.models.bug_report import BugReport
from bugit_v2.screens.bug_report_screen import BugReportScreen
from bugit_v2.screens.job_selection_screen import JobSelectionScreen
from bugit_v2.screens.session_selection_screen import SessionSelectionScreen
from bugit_v2.screens.submission_progress_screen import (
    ReturnScreenChoice,
    SubmissionProgressScreen,
)
from bugit_v2.utils import is_prod
from bugit_v2.utils.constants import NullSelection
from bugit_v2.utils.validations import before_entry_check

cli_app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=not is_prod(),
    pretty_exceptions_show_locals=not is_prod(),
)


@dataclass(slots=True)
class AppState:
    """The global app state.

    Combination of null and non-nulls determine which state we are in
    - All null: session selection
    - Only session is NOT null: job selection
    - Only bug_report is null: editor
    - All non-null: submission in progress
    """

    session: Session | Literal[NullSelection.NO_SESSION] | None = None
    job_id: str | Literal[NullSelection.NO_JOB] | None = None
    bug_report: BugReport | None = None


@dataclass(slots=True)
class AppArgs:
    submitter: Literal["lp", "jira"]


@final
class BugitApp(App[None]):
    state = var(AppState())
    args: AppArgs
    # Any doesn't matter here
    submitter_class: type[
        BugReportSubmitter[Any, Any]  # pyright: ignore[reportExplicitAny]
    ]
    bug_report_backup: BugReport | None = None
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

    @work
    async def on_mount(self) -> None:
        self.theme = "solarized-light"
        if is_prod():
            self.title = "BugIt V2"
        else:
            self.title = "BugIt V2 🐛🐛 DEBUG MODE 🐛🐛"

        if (version := get_checkbox_version()) is not None:
            self.sub_title = f"Checkbox {version}"

    @override
    def _handle_exception(self, error: Exception) -> None:
        if is_prod() or "SNAP" in os.environ:
            raise SystemExit(error)
        else:
            # don't use pretty exception in prod, it shows local vars
            # if not in a snap the code is already in the system anyways
            super()._handle_exception(error)

    def watch_state(self) -> None:
        """Push different screens based on the state"""

        def _write_state(new_state: AppState):
            self.state = new_state

        match self.state:
            case AppState(session=None, job_id=None, bug_report=None):
                # init, nothing has been selected yet
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
                    BugReportScreen(
                        session,
                        job_id,
                        self.args.submitter,
                        self.bug_report_backup,
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
                    BugReportScreen(
                        session,
                        job_id,
                        self.args.submitter,
                        self.bug_report_backup,
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
                    BugReportScreen(
                        session,
                        job_id,
                        self.args.submitter,
                        self.bug_report_backup,
                    ),
                    lambda bug_report: _write_state(
                        AppState(session, job_id, bug_report)
                    ),
                )
            case AppState(
                session=Session() | NullSelection.NO_SESSION as session,
                job_id=str() | NullSelection.NO_JOB as job_id,
                bug_report=BugReport() as br,
            ):
                # returning from the end of submission screen
                # handle the button selections
                def after_submission_finished(
                    return_screen: ReturnScreenChoice | None,
                ):
                    # already submitted, flush the stale backup
                    self.bug_report_backup = None
                    match return_screen:
                        case None:
                            raise RuntimeError(
                                "Submission screen should not return None"
                            )
                        case "quit":
                            self.exit()
                        case "session":
                            self.state = AppState()
                        case "job" if session != NullSelection.NO_SESSION:
                            # this is only available when there's a session
                            self.state = AppState(session)
                        case "report_editor":
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
                bug_report=BugReport(),
            ):
                self.notify(
                    title="Cannot go back while a submission is happening",
                    message="But you can force quit with Ctrl+Q",
                )
            case _:
                raise RuntimeError(
                    f"Impossible state when going back: {self.state}"
                )

    @override
    def compose(self) -> ComposeResult:
        yield Header(icon="〇")
        yield LoadingIndicator()
        yield Footer()


@cli_app.command("lp", help="Submit a bug to Launchpad")
def launchpad_mode():
    before_entry_check()
    app = BugitApp(AppArgs("lp"))
    app.run()


@cli_app.command("jira", help="Submit a bug to Jira")
def jira_mode():
    before_entry_check()
    app = BugitApp(AppArgs("jira"))
    app.run()


if __name__ == "__main__":
    cli_app(prog_name="bugit-v2")
