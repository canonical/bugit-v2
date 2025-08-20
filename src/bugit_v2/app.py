import argparse
import os
from dataclasses import dataclass
from typing import Any, Literal, final

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
from bugit_v2.checkbox_utils import Session, get_checkbox_version
from bugit_v2.models.bug_report import BugReport
from bugit_v2.screens.bug_report_screen import BugReportScreen
from bugit_v2.screens.job_selection_screen import JobSelectionScreen
from bugit_v2.screens.session_selection_screen import SessionSelectionScreen
from bugit_v2.screens.submission_progress_screen import (
    ReturnScreenChoice,
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
                if os.getenv("DEBUG") == "1":
                    self.submitter_class = MockJiraSubmitter
                else:
                    self.submitter_class = JiraSubmitter
            case "lp":
                self.submitter_class = LaunchpadSubmitter

        super().__init__(driver_class, css_path, watch_css, ansi_color)

    @work
    async def on_mount(self) -> None:
        self.theme = "solarized-light"
        self.title = (
            f"BugIt V2 👾 {'DEBUG MODE' if os.getenv('DEBUG') else ''}"
        )

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

    def watch_state(self) -> None:
        """Push different screens based on the state"""

        def _write_state(new_state: AppState):
            self.state = new_state

        if self.state.session is None:
            self.push_screen(
                SessionSelectionScreen(),
                lambda session_path: session_path
                and _write_state(AppState(Session(session_path))),
            )
        elif self.state.job_id is None:
            self.push_screen(
                JobSelectionScreen(self.state.session),
                lambda job_id: _write_state(
                    AppState(self.state.session, job_id)
                ),
            )

        elif self.state.bug_report is None:
            self.push_screen(
                BugReportScreen(
                    self.state.session,
                    self.state.job_id,
                    self.bug_report_backup,
                ),
                lambda bug_report: _write_state(
                    AppState(self.state.session, self.state.job_id, bug_report)
                ),
            )
        else:

            def handle_return(return_screen: ReturnScreenChoice):
                match return_screen:
                    case "quit":
                        self.exit()
                    case "session":
                        self.bug_report_backup = None
                        self.state = AppState(None, None)
                    case "job":
                        self.bug_report_backup = None
                        self.state = AppState(self.state.session, None)
                    case "report_editor":
                        self.bug_report_backup = self.state.bug_report
                        self.state = AppState(
                            self.state.session, self.state.job_id, None
                        )

            self.push_screen(
                SubmissionProgressScreen(
                    self.state.bug_report, self.submitter_class()
                ),
                lambda return_screen: return_screen
                and handle_return(return_screen),
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
    # TODO: use a typed parser
    app = BugitApp(AppArgs(**vars(args)))  # pyright: ignore[reportAny]
    app.run()


if __name__ == "__main__":
    main()
