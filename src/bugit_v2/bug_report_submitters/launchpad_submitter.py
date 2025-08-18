import os
import time
from collections.abc import Generator
from pathlib import Path
from typing import Callable, final, override

from launchpadlib.credentials import (
    Credentials,
    EndUserDeclinedAuthorization,
    EndUserNoAuthorization,
    HTTPError,
    RequestTokenAuthorizationEngine,
)
from launchpadlib.launchpad import Launchpad
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Center, HorizontalGroup, VerticalGroup
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, RichLog

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.models.bug_report import BugReport

LAUNCHPAD_AUTH_FILE_PATH = Path("/tmp/bugit-v2-launchpad.txt")


@final
class GraphicalAuthorizeRequestTokenWithURL(RequestTokenAuthorizationEngine):
    """
    Override some of the handlers in AuthorizeRequestTokenWithURL
    to work with a graphical application
    """

    def __init__(
        self,
        log_widget: RichLog,
        check_finish_button_status: Callable[[], bool],
        service_root: str,
        application_name: str | None = None,
        consumer_name: str | None = None,
        allow_access_levels: list[str] | None = None,
    ):
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            service_root, application_name, consumer_name, allow_access_levels
        )
        self.log_widget = log_widget
        self.check_finish_button_status = check_finish_button_status

    def check_end_user_authorization(self, credentials: Credentials) -> None:
        """This is the same as AuthorizeRequestTokenWithURL"""
        try:
            credentials.exchange_request_token_for_access_token(self.web_root)
        except HTTPError as e:
            if e.response.status == 403:
                # The user decided not to authorize this
                # application.
                raise EndUserDeclinedAuthorization(str(e.content))
            else:
                if e.response.status != 401:
                    # There was an error accessing the server.
                    self.log_widget.write(
                        "Unexpected response from Launchpad:"
                    )
                    self.log_widget.write(e)
                # The user has not made a decision yet.
                raise EndUserNoAuthorization(str(e.content))

    @override
    def make_end_user_authorize_token(
        self, credentials: Credentials, request_token: str
    ):
        """The 'entrypoint' of this auth engine, see the superclass for details

        basically we implement this method to specify how to get auth from the
        user
        """
        authorization_url = self.authorization_url(request_token)
        # self.notify_end_user_authorization_url(authorization_url)
        self.log_widget.write(authorization_url)
        self.log_widget.write(
            "[b]Press the [blue]'Finish Browser Authentication'[/] button after you have authenticated in the browser"
        )
        while not self.check_finish_button_status():
            time.sleep(0.5)  # avoid busy-poll
        self.log_widget.write("Checking auth...")
        self.check_end_user_authorization(credentials)


@final
class LaunchpadAuthModal(ModalScreen[tuple[Path, bool]]):
    auth: Path | None = None  # path to the launchpad auth file
    finished_browser_auth = False

    CSS = """
    LaunchpadAuthModal {
        align: center middle;
        background: $background 100%;
    }

    #top_level_container {
        padding: 0 5;
    }

    LaunchpadAuthModal Checkbox {
        border: round $boost 700%;
        background: $background 100%;
    }

    LaunchpadAuthModal Checkbox:focus-within {
        border: round $primary;
    }

    #finish_button {
        margin-right: 1;
    }
    """

    @override
    def compose(self) -> ComposeResult:
        with VerticalGroup(id="top_level_container"):
            yield Label("[b][$primary]Launchpad Authentication")
            yield RichLog(id="lp_login_stdout", markup=True)
            yield Checkbox(
                "Cache valid credentials until next boot",
                tooltip=(
                    "Save the credentials to /tmp so you don't need to "
                    "authenticate over and over again. They are erased "
                    "at the next boot, or you can manually delete them"
                ),
                value=True,
            )
            with Center():
                with HorizontalGroup(classes="wa"):
                    yield Button(
                        "Finish Browser Authentication",
                        id="finish_button",
                    )
                    b = Button(
                        "Continue",
                        id="continue_button",
                        classes="wa",
                    )
                    b.display = False
                    yield b

    def on_mount(self):
        self.query_exactly_one("#top_level_container").border_title = (
            "Launchpad Authentication"
        )
        self.call_after_refresh(self.main_auth_sequence)

    @work(thread=True)
    def main_auth_sequence(self):

        service_root = os.getenv("APPORT_LAUNCHPAD_INSTANCE", "production")
        app_name = os.getenv("BUGIT_APP_NAME")
        assert service_root in ("production", "staging", "qastaging")
        assert app_name

        log_widget = self.query_exactly_one("#lp_login_stdout", RichLog)
        auth_engine = GraphicalAuthorizeRequestTokenWithURL(
            log_widget,
            lambda: self.finished_browser_auth,
            "production",
            app_name,
            allow_access_levels=["WRITE_PRIVATE"],
        )

        try:
            Launchpad.login_with(  # pyright: ignore[reportUnknownMemberType]
                application_name=app_name,
                service_root=service_root,
                authorization_engine=auth_engine,
                credentials_file=str(LAUNCHPAD_AUTH_FILE_PATH),
            )
            self.auth = LAUNCHPAD_AUTH_FILE_PATH
            log_widget.write("[green]Auth seems ok!")
            btn = self.query_exactly_one("#continue_button", Button)
            btn.display = True
            btn.variant = "success"
        except Exception as e:
            log_widget.write("[red]Authentication failed![/]")
            log_widget.write(f"[red]Reason[/]: {e}")
            btn = self.query_exactly_one("#continue_button", Button)
            btn.display = True
            btn.label = "Return to Editor"

    @on(Button.Pressed, "#finish_button")
    def finish_browser_auth(self, event: Button.Pressed):
        self.finished_browser_auth = True
        event.button.disabled = True

    @on(Button.Pressed, "#continue_button")
    def exit_widget(self) -> None:
        # should only be clickable when auth has been filled
        assert self.auth
        self.dismiss((self.auth, self.query_exactly_one(Checkbox).value))


@final
class LaunchpadSubmitter(BugReportSubmitter[Path, None]):
    name = "launchpad_submitter"
    severity_name_map = {
        "highest": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "lowest": "Wishlist",
    }
    steps = 5
    lp_client: Launchpad | None = None
    auth_modal = LaunchpadAuthModal

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage | Exception, None, None]:
        bug_dict = {
            "assignee": bug_report.assignee,
            "project": bug_report.project,
            "title": bug_report.title,
            "description": bug_report.description,
            "priority": self.severity_name_map[bug_report.severity],
            "status": bug_report.status,
            "tags": " ".join(
                [*bug_report.platform_tags, *bug_report.additional_tags]
            ),
            "series": bug_report.series or "",
        }

        try:
            service_root = os.getenv("APPORT_LAUNCHPAD_INSTANCE", "production")
            app_name = os.getenv("BUGIT_APP_NAME")

            assert service_root in ("production", "staging", "qastaging")
            assert app_name
            assert (
                LAUNCHPAD_AUTH_FILE_PATH.exists()
            ), "At this point auth should already be valid"
            print(bug_dict)

            yield f"Logging into Launchpad: {service_root}"
            Launchpad.login_with(
                app_name,
                service_root,
                credentials_file=LAUNCHPAD_AUTH_FILE_PATH,
            )
        except Exception as e:
            yield e

    @override
    def upload_attachments(
        self, attachment_dir: Path
    ) -> Generator[str | AdvanceMessage | Exception, None, None]:
        # return super().upload_attachments(attachment_dir)
        yield "step 1"
        yield "step 2"

    @property
    @override
    def bug_url(self) -> str:
        return "https://www.example.com"

    @override
    def get_cached_credentials(self) -> Path | None:
        if LAUNCHPAD_AUTH_FILE_PATH.exists():
            return LAUNCHPAD_AUTH_FILE_PATH
        return None
