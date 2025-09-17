import os
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, Callable, Literal, cast, final, override

from launchpadlib.credentials import (
    Credentials,
    EndUserDeclinedAuthorization,
    EndUserNoAuthorization,
    RequestTokenAuthorizationEngine,
)
from launchpadlib.launchpad import Launchpad
from launchpadlib.uris import LPNET_WEB_ROOT, QASTAGING_WEB_ROOT
from lazr.restfulclient.errors import HTTPError
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Center, HorizontalGroup, VerticalGroup
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Label, RichLog

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.models.bug_report import BugReport, pretty_issue_file_times
from bugit_v2.utils import is_prod

LP_AUTH_FILE_PATH = Path("/tmp/bugit-v2-launchpad.txt")
# 'staging' doesn't seem to work
# only 'qastaging' and 'production' works
VALID_SERVICE_ROOTS = ("production", "qastaging")

SERVICE_ROOT = cast(
    Literal["production", "qastaging"],
    os.getenv(
        "APPORT_LAUNCHPAD_INSTANCE", "production" if is_prod() else "qastaging"
    ),
)
LP_APP_NAME = os.getenv("BUGIT_APP_NAME", "bugit-v2")

assert SERVICE_ROOT in VALID_SERVICE_ROOTS


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
        super().__init__(
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
                # content is apparently a byte-string
                raise EndUserDeclinedAuthorization(bytes(e.content).decode())
            else:
                if e.response.status == 401:
                    raise EndUserNoAuthorization(bytes(e.content).decode())
                else:
                    # There was an error accessing the server.
                    self.log_widget.write(
                        "Unexpected response from Launchpad:"
                    )
                    self.log_widget.write(repr(e))

    @override
    def make_end_user_authorize_token(
        self, credentials: Credentials, request_token: str
    ):
        """The 'entrypoint' of this auth engine, see the superclass for details

        basically we implement this method to specify how to get auth from the
        user
        """
        self.log_widget.write("Initializing Launchpad authorization...")
        authorization_url = self.authorization_url(request_token)
        # self.notify_end_user_authorization_url(authorization_url)
        self.log_widget.write(authorization_url)
        self.log_widget.write(
            "[b]Press the [blue]'Finish Browser Authentication'[/] button after you have authenticated in the browser"
        )
        # this loop is an ugly workaround for the login method
        # because it expects the auth to be ready by the end of this function
        # so we have to block until auth is here
        # NOTE: this causes the app to not exit cleanly when ^Q is pressed
        # during the auth sequence
        while not self.check_finish_button_status():
            time.sleep(0.5)  # avoid busy-poll
        self.log_widget.write("Checking auth...")
        self.check_end_user_authorization(credentials)


@final
class LaunchpadAuthModal(ModalScreen[tuple[Path, bool] | None]):
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

    .mb1 {
        margin-bottom: 1;
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
            with Center(classes="mb1"):
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
        assert SERVICE_ROOT in VALID_SERVICE_ROOTS, (
            "Invalid APPORT_LAUNCHPAD_INSTANCE, "
            f"expected one of {VALID_SERVICE_ROOTS}, but got {SERVICE_ROOT}"
        )
        assert LP_APP_NAME, "BUGIT_APP_NAME was not specified"

        log_widget = self.query_exactly_one("#lp_login_stdout", RichLog)
        auth_engine = GraphicalAuthorizeRequestTokenWithURL(
            log_widget,
            lambda: self.finished_browser_auth,
            SERVICE_ROOT,
            LP_APP_NAME,
            allow_access_levels=["WRITE_PRIVATE"],
        )

        try:
            # immediately write something so it doesn't look dead
            log_widget.write("Waiting for launchpad to respond...")
            Launchpad.login_with(
                application_name=LP_APP_NAME,
                service_root=SERVICE_ROOT,
                authorization_engine=auth_engine,
                credentials_file=str(LP_AUTH_FILE_PATH),
            )
            self.auth = LP_AUTH_FILE_PATH
            log_widget.write(
                "[green]Auth is ready! Click the continue button to start submitting the bug report."
            )
            continue_btn = self.query_exactly_one("#continue_button", Button)
            continue_btn.display = True
            continue_btn.variant = "success"
        except Exception as e:
            log_widget.write("[red]Authentication failed![/]")
            log_widget.write(f"[red]Reason[/]: {e}")
            continue_btn = self.query_exactly_one("#continue_button", Button)
            continue_btn.display = True
            continue_btn.label = "Return to Editor"

            finish_auth_btn = self.query_exactly_one("#finish_button", Button)
            finish_auth_btn.disabled = True

    @on(Button.Pressed, "#finish_button")
    def finish_browser_auth(self, event: Button.Pressed):
        self.finished_browser_auth = True
        event.button.disabled = True

    @on(Button.Pressed, "#continue_button")
    def exit_widget(self) -> None:
        if not self.auth:
            # this should go back to the editor
            # see the except clause of the auth sequence
            self.dismiss(None)
        else:
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
    display_name = "Launchpad"
    steps = 6
    auth_modal = LaunchpadAuthModal
    # parallel upload will cause an irrecoverable segfault
    # and completely kill the shell
    allow_parallel_upload = False

    lp_client: Launchpad | None = None
    lp_bug_object: Any | None = None  # TODO: make a wrapper for this

    def check_project_existence(self, project_name: str) -> Any:
        assert self.lp_client
        try:
            # type checker freaks out here
            # since launchpad lib wants unknown member access + index access
            return self.lp_client.projects[  # pyright: ignore[reportIndexIssue, reportOptionalSubscript, reportUnknownVariableType]
                project_name
            ]
        except Exception as e:
            error_message = (
                f"Project '{project_name}' doesn't exist or you don't have access. "
                + f"Original error: {e}"
            )
            raise ValueError(error_message)

    def check_assignee_existence(self, assignee: str) -> Any:
        assert self.lp_client
        try:
            return self.lp_client.people[  # pyright: ignore[reportIndexIssue, reportOptionalSubscript, reportUnknownVariableType]
                assignee
            ]
        except Exception as e:
            error_message = f"Assignee '{assignee}' doesn't exist. Original error: {repr(e)}"
            raise ValueError(error_message)

    def check_series_existence(self, series: str) -> Any:
        assert self.lp_client
        try:
            return self.lp_client.project.getSeries(  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess, reportUnknownVariableType]
                name=series
            )
        except Exception as e:
            error_message = (
                f"Series '{series}' doesn't exist. Original error: {e}"
            )
            raise ValueError(error_message)

    @override
    def bug_exists(self, bug_id: str) -> bool:
        return False

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage, None, None]:
        assert SERVICE_ROOT in VALID_SERVICE_ROOTS, (
            "Invalid APPORT_LAUNCHPAD_INSTANCE, "
            f"expected one of {VALID_SERVICE_ROOTS}, but got {SERVICE_ROOT}"
        )
        assert LP_APP_NAME, "BUGIT_APP_NAME was not specified"
        assert (
            LP_AUTH_FILE_PATH.exists()
        ), "At this point auth should already be valid"

        yield f"Logging into Launchpad: {SERVICE_ROOT}"
        self.lp_client = Launchpad.login_with(
            LP_APP_NAME,
            SERVICE_ROOT,
            credentials_file=LP_AUTH_FILE_PATH,
        )  # this blocks until ready
        yield AdvanceMessage("Launchpad auth succeeded")

        assignee = None
        series = None
        project = self.check_project_existence(bug_report.project)
        yield AdvanceMessage(
            f"Project '{bug_report.project}' exists at {project}"
        )

        if bug_report.assignee:
            assignee = self.check_assignee_existence(bug_report.assignee)
            yield AdvanceMessage(
                f"Assignee [u]{bug_report.assignee}[/u] exists"
            )
        else:
            yield AdvanceMessage(
                "Assignee unspecified, marking the bug as unassigned"
            )

        if bug_report.series:
            series = self.check_series_existence(bug_report.series)
            yield AdvanceMessage(f"Series [u]{bug_report.series} exists![/]")
        else:
            yield AdvanceMessage("Series unspecified, skipping")

        issue_file_time_block = (
            f"[Stage]\n{pretty_issue_file_times[bug_report.issue_file_time]}"
        )
        # actually create the bug
        self.lp_bug_object = self.lp_client.bugs.createBug(  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess]
            title=bug_report.title,
            description=bug_report.description
            + "\n\n"
            + issue_file_time_block,  # TODO: is there a length limit?
            tags=[
                *bug_report.platform_tags,
                *bug_report.additional_tags,
            ],  # length limit?
            target=self.lp_client.projects[  # pyright: ignore[reportIndexIssue, reportOptionalSubscript]
                bug_report.project  # index access also has a side effect
            ],
        )
        assert self.lp_bug_object, "Unexpected null bug"
        # https://documentation.ubuntu.com/launchpad/user/explanation/launchpad-api/launchpadlib/#persistent-references-to-launchpad-objects
        yield AdvanceMessage(
            f"Created bug: {str(self.lp_bug_object)}"  # pyright: ignore[reportUnknownArgumentType]
        )

        task = self.lp_bug_object.bug_tasks[0]  # TODO: is it always non-empty?
        if assignee:
            yield f"Setting assignee to {assignee}..."
            task.assignee = assignee
        if series:
            yield f"Setting series to {series}"
            self.lp_bug_object.addNomination(target=series).approve()

        yield f"Setting status to {bug_report.status}..."
        task.status = bug_report.status

        # TODO: skip this if severity doesn't exist?
        lp_importance = self.severity_name_map[bug_report.severity]
        yield f"Setting importance to {lp_importance}..."
        task.importance = lp_importance  # the update request is a side effect

        task.lp_save()
        yield "Saved bug settings"

        yield AdvanceMessage(f"Bug URL is: {self.bug_url}")

    @override
    def reopen(
        self, bug_id: str
    ) -> Generator[str | AdvanceMessage, None, None]:
        return super().reopen(bug_id)

    @override
    def upload_attachment(self, attachment_file: Path) -> str | None:
        assert (
            self.lp_bug_object
        ), "No launchpad bug has been created or fetched"
        with open(attachment_file, "rb") as f:
            # this might explode on low memory systems
            # but idk how to work around it
            self.lp_bug_object.addAttachment(
                comment="Automatically attached by bugit-v2",
                filename=attachment_file.name,
                data=f.read(),
            )

    @property
    @override
    def bug_url(self) -> str:
        assert (
            self.lp_bug_object
        ), "No launchpad bug has been created or fetched"
        match SERVICE_ROOT:
            case "production":
                return f"{LPNET_WEB_ROOT}bugs/{self.lp_bug_object.id}"
            case "qastaging":
                return f"{QASTAGING_WEB_ROOT}bugs/{self.lp_bug_object.id}"

    @override
    def get_cached_credentials(self) -> Path | None:
        if LP_AUTH_FILE_PATH.exists():
            return LP_AUTH_FILE_PATH
        return None
