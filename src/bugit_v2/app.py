import argparse
import os
from dataclasses import dataclass
from typing import Literal, final

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.driver import Driver
from textual.reactive import var
from textual.types import CSSPathType
from textual.widgets import Footer, Header, LoadingIndicator
from typing_extensions import override

from bugit_v2.bug_report_submitters.mock_jira import MockJiraSubmitter
from bugit_v2.checkbox_utils import Session, get_checkbox_version
from bugit_v2.models.bug_report import BugReport
from bugit_v2.screens.bug_report_screen import BugReportScreen
from bugit_v2.screens.job_selection_screen import JobSelectionScreen
from bugit_v2.screens.session_selection_screen import SessionSelectionScreen
from bugit_v2.screens.submission_progress_screen import (
    SubmissionProgressScreen,
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

    session: Session | None = None
    job_id: str | None = None
    bug_report: BugReport | None = None


@dataclass(slots=True)
class AppArgs:
    submitter: Literal["lp", "jira"]


@final
class BugitApp(App[None]):
    state = var(AppState())
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
        super().__init__(driver_class, css_path, watch_css, ansi_color)

    @work
    async def on_mount(self) -> None:
        self.theme = "solarized-light"
        self.title = "BugIt V2 👾"

        if (version := get_checkbox_version()) is not None:
            self.sub_title = f"Checkbox {version}"

    @override
    def _handle_exception(self, error: Exception) -> None:
        if os.getenv("DEBUG") == "1" and "SNAP" not in os.environ:
            # don't use pretty exception in prod, it shows local vars
            # if not in a snap the code is already in the system anyways
            super()._handle_exception(error)
        else:
            raise SystemExit(error)

    @work
    async def watch_state(self) -> None:
        # just update the state, let the library do the rendering

        if self.state.session is None:
            session_path = await self.push_screen_wait(
                SessionSelectionScreen()
            )
            self.state = AppState(Session(session_path))
        elif self.state.job_id is None:
            job_id = await self.push_screen_wait(
                JobSelectionScreen(self.state.session)
            )
            self.state = AppState(self.state.session, job_id)
        elif self.state.bug_report is None:
            bug_report = await self.push_screen_wait(
                BugReportScreen(
                    self.state.session,
                    self.state.job_id,
                    self.bug_report_backup,
                )
            )
            self.state = AppState(
                self.state.session, self.state.job_id, bug_report
            )
        else:
            return_to = await self.push_screen_wait(
                SubmissionProgressScreen(
                    self.state.bug_report, MockJiraSubmitter()
                )
            )
            match return_to:
                case "quit":
                    self.exit()
                case "session":
                    self.state = AppState(None, None)
                case "job":
                    self.state = AppState(self.state.session, None)
                case "report_editor":
                    self.bug_report_backup = self.state.bug_report
                    self.state = AppState(
                        self.state.session, self.state.job_id, None
                    )

    def action_go_back(self) -> None:
        """Handles the `Go Back` button

        This function should only reassign (not modify) the state object and
        let textual automatically re-render
        """
        if self.state.session is None:
            self.notify("Already at the beginning")
            return  # nothing to do here if no session is selected
        elif self.state.job_id is None:
            # go back to session selection
            self.state = AppState(None, None)
        elif self.state.bug_report is None:
            # go back to job selection
            self.state = AppState(self.state.session, None)
        else:
            self.notify(
                "(but you can use Ctrl+Q to quit)",
                title="Can't go back while a submission is happening",
                severity="error",
            )

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        yield LoadingIndicator()
        yield Footer()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("submitter", choices=["lp", "jira"])
    return parser.parse_args()
    # parser.add_argument("--submitter", choices=["lp", "jira"])


def main():
    args = parse_args()
    # vars() is very ugly, but it allows the AppArgs constructor to fail fast
    # before the app takes over the screen
    args = AppArgs(*vars(args).values())
    print(args)
    app = BugitApp(args)
    app.run()


if __name__ == "__main__":
    main()
