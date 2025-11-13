import os
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
from bugit_v2.checkbox_utils.models import SimpleCheckboxSubmission
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.bug_report import (
    BugReport,
    PartialBugReport,
    recover_from_autosave,
)
from bugit_v2.screens.bug_report_screen import BugReportScreen
from bugit_v2.screens.job_selection_screen import JobSelectionScreen
from bugit_v2.screens.recover_from_autosave_screen import (
    RecoverFromAutoSaveScreen,
)
from bugit_v2.screens.reopen_precheck_screen import ReopenPreCheckScreen
from bugit_v2.screens.session_selection_screen import SessionSelectionScreen
from bugit_v2.screens.submission_progress_screen import (
    ReturnScreenChoice,
    SubmissionProgressScreen,
)
from bugit_v2.utils import is_prod, is_snap
from bugit_v2.utils.constants import AUTOSAVE_DIR, NullSelection
from bugit_v2.utils.validations import (
    checkbox_submission_check,
    is_cid,
    sudo_devmode_check,
)

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
class NavigationState:
    """
    The global state for navigation. Check the watch_state function to see all possible
    state combinations
    """

    # For session and job_id, None means the user hasn't selected anything
    # but still POSSIBLE to go to a screen that can select them
    # NullSelection means an explicit selection of no session/no job
    session: Session | Literal[NullSelection.NO_SESSION] | None = None
    job_id: str | Literal[NullSelection.NO_JOB] | None = None
    # Initial state of the bug report, only used for backup right now
    bug_report_init_state: (
        BugReport | Literal[NullSelection.NO_BACKUP] | None
    ) = None
    # The finished bug report ready to be fed to the submitter
    bug_report_to_submit: BugReport | None = None
    # Checkbox submission passed in from the CLI
    # Must check if this is valid at the beginning of the app
    checkbox_submission: (
        SimpleCheckboxSubmission
        | Literal[NullSelection.NO_CHECKBOX_SUBMISSION]
    ) = NullSelection.NO_CHECKBOX_SUBMISSION


@dataclass(slots=True, frozen=True)
class ReopenNavigationState:
    session: Session | Literal[NullSelection.NO_SESSION] | None = None
    job_id: str | Literal[NullSelection.NO_JOB] | None = None
    bug_report_to_submit: PartialBugReport | None = None
    bug_report_init_state: PartialBugReport | None = None


@final
class BugitApp(App[None]):
    nav_state = var[NavigationState | ReopenNavigationState](NavigationState())
    args: AppArgs
    # Any doesn't matter here
    submitter_class: type[
        BugReportSubmitter[Any, Any]  # pyright: ignore[reportExplicitAny]
    ]
    partial_bug_report_to_submit_backup: PartialBugReport | None = None
    BINDINGS = [Binding("alt+left", "go_back", "Go Back")]

    def __init__(
        self,
        args: AppArgs,
        driver_class: type[Driver] | None = None,
        css_path: CSSPathType | None = None,
        watch_css: bool = False,
        ansi_color: bool = False,
    ):
        super().__init__(driver_class, css_path, watch_css, ansi_color)

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

    @work(thread=True)
    def on_mount(self) -> None:
        self.theme = "solarized-light"
        if is_prod():
            self.title = "Bugit V2"
        else:
            self.title = "Bugit V2 🐛🐛 DEBUG MODE 🐛🐛"

        if self.args.checkbox_submission:
            self.nav_state = NavigationState(
                session=NullSelection.NO_SESSION,
                checkbox_submission=self.args.checkbox_submission,
            )

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
    async def watch_nav_state(self) -> None:
        """Push different screens based on the state"""

        match self.nav_state:
            case ReopenNavigationState():
                # not used right now
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

            case NavigationState(
                session=None,
                job_id=None,
                bug_report_to_submit=None,
                bug_report_init_state=None,
                checkbox_submission=checkbox_submission,
            ):
                # show the recovery screen if there's no backup
                recovery_file = await self.push_screen_wait(
                    RecoverFromAutoSaveScreen(AUTOSAVE_DIR)
                )
                if recovery_file is None:
                    self.nav_state = NavigationState(
                        None, None, NullSelection.NO_BACKUP, None
                    )
                    return

                recovered_report = recover_from_autosave(recovery_file)
                # trigger this watcher again to actually go to the editor page
                self.nav_state = NavigationState(
                    recovered_report.checkbox_session
                    or NullSelection.NO_SESSION,
                    recovery_file.job_id or NullSelection.NO_JOB,
                    recovered_report,
                    None,
                    checkbox_submission,
                )

            case NavigationState(
                session=None,
                job_id=None,
                bug_report_to_submit=None,
                bug_report_init_state=NullSelection.NO_BACKUP,
            ):
                # explicitly chose no backup
                def after_session_select(
                    rv: Path | Literal[NullSelection.NO_SESSION] | None,
                ):
                    match rv:
                        case Path():
                            self.nav_state = NavigationState(Session(rv))
                        case NullSelection.NO_SESSION:
                            self.nav_state = NavigationState(
                                rv,
                                NullSelection.NO_JOB,
                                NullSelection.NO_BACKUP,
                            )
                        case None:
                            raise RuntimeError(
                                "Session selection should not return None"
                            )

                self.push_screen(
                    SessionSelectionScreen(), after_session_select
                )

            case NavigationState(
                session=Session() as session,
                job_id=None,
                checkbox_submission=NullSelection.NO_CHECKBOX_SUBMISSION as checkbox_submission,
                bug_report_to_submit=None,
                # job selection is only possible without init value
                bug_report_init_state=None | NullSelection.NO_BACKUP,
            ):
                # selected a normal session
                # should go to job selection
                self.push_screen(
                    JobSelectionScreen(
                        session.get_run_jobs(),
                        str(os.path.basename(session.session_path)),
                    ),
                    lambda job_id: self._write_state(
                        NavigationState(
                            session,
                            job_id,
                            NullSelection.NO_BACKUP,
                            None,
                            checkbox_submission,
                        )
                    ),
                )

            case NavigationState(
                session=NullSelection.NO_SESSION as session,
                job_id=None,
                checkbox_submission=SimpleCheckboxSubmission() as checkbox_submission,
                bug_report_to_submit=None,
                bug_report_init_state=None | NullSelection.NO_BACKUP,
            ):
                # a submission was passed from the CLI
                self.push_screen(
                    JobSelectionScreen(
                        [r.full_id for r in checkbox_submission.base.results],
                        checkbox_submission.base.testplan_id,
                    ),
                    lambda job_id: self._write_state(
                        NavigationState(
                            session,
                            job_id,
                            NullSelection.NO_BACKUP,
                            None,
                            checkbox_submission,
                        )
                    ),
                )

            case NavigationState(
                session=NullSelection.NO_SESSION | Session() as session,
                job_id=NullSelection.NO_JOB | str() as job_id,
                checkbox_submission=checkbox_submission,
                bug_report_to_submit=None,
                bug_report_init_state=BugReport()
                | NullSelection.NO_BACKUP as init_state,
            ):
                if (
                    session == NullSelection.NO_SESSION
                    and checkbox_submission
                    is NullSelection.NO_CHECKBOX_SUBMISSION
                ):
                    assert (
                        job_id == NullSelection.NO_JOB
                    ), f"Got job id '{job_id}' but no session/submission"

                if job_id != NullSelection.NO_JOB:
                    # if a job is selected
                    # it must come from only 1 source
                    assert (session == NullSelection.NO_SESSION) ^ (
                        checkbox_submission
                        == NullSelection.NO_CHECKBOX_SUBMISSION
                    ), f"Ambiguous source of job '{job_id}'"

                # normal case, session and job_id were selected
                # go to editor with info
                self.push_screen(
                    BugReportScreen(
                        session,
                        checkbox_submission,
                        job_id,
                        self.args,
                        (
                            None
                            if init_state == NullSelection.NO_BACKUP
                            else init_state
                        ),
                    ),
                    lambda bug_report_to_submit: self._write_state(
                        NavigationState(
                            session, job_id, None, bug_report_to_submit
                        )
                    ),
                )
            case NavigationState(
                session=Session() | NullSelection.NO_SESSION as session,
                job_id=str() | NullSelection.NO_JOB as job_id,
                bug_report_to_submit=BugReport() as report,
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
                            self.nav_state = NavigationState()
                        case "job" if session != NullSelection.NO_SESSION:
                            self.nav_state = NavigationState(
                                session, None, NullSelection.NO_BACKUP
                            )
                        case "report_editor":
                            # restore the report
                            self.nav_state = NavigationState(
                                session, job_id, report
                            )
                        case _:
                            raise RuntimeError()

                self.push_screen(
                    SubmissionProgressScreen(
                        report, self.submitter_class(), self.args
                    ),
                    after_submission_finished,
                )

            case _:
                raise RuntimeError(f"Impossible state: {self.nav_state}")

    def action_go_back(self) -> None:
        """Handles the `Go Back` button

        This function should only reassign (not modify) the state object and
        let textual automatically re-render
        """
        match self.nav_state:
            case NavigationState(
                session=None,
                job_id=None,
                bug_report_init_state=None,
                bug_report_to_submit=None,
            ) | NavigationState(
                session=NullSelection.NO_SESSION,
                checkbox_submission=SimpleCheckboxSubmission(),
                job_id=None,
                bug_report_init_state=None,
                bug_report_to_submit=None,
            ):
                # backup selection screen
                self.notify("Already at the beginning")
            case NavigationState(
                session=None as s,
                checkbox_submission=NullSelection.NO_CHECKBOX_SUBMISSION as cbs,
            ):
                self.nav_state = NavigationState(
                    session=s, checkbox_submission=cbs
                )  # back to backup selection
            case NavigationState(
                session=NullSelection.NO_SESSION as s,
                checkbox_submission=SimpleCheckboxSubmission() as cbs,
            ):
                self.nav_state = NavigationState(
                    session=s, checkbox_submission=cbs
                )  # back to backup selection
            case NavigationState(
                bug_report_init_state=BugReport()
            ) | NavigationState(
                session=NullSelection.NO_SESSION,
                job_id=NullSelection.NO_JOB,
                bug_report_to_submit=None,
            ):
                # 1. started with a backup
                # 2. returning from editor with nothing selected
                # => go to backup selection
                self.nav_state = NavigationState()
            case NavigationState(session=Session(), job_id=None):
                # returning from job selection
                self.nav_state = NavigationState(
                    bug_report_init_state=NullSelection.NO_BACKUP
                )
            case NavigationState(
                session=Session() as session,
                job_id=str() | NullSelection.NO_JOB,
                bug_report_init_state=NullSelection.NO_BACKUP,
            ):
                # returning from editor with explicit job selection and session
                # go back to job selection
                self.nav_state = NavigationState(session, None)
            case NavigationState(
                bug_report_to_submit=BugReport() | PartialBugReport(),
            ):
                self.notify(
                    title="Cannot go back while a submission is happening",
                    message="But you can force quit with Ctrl+Q",
                )
            case _:
                raise RuntimeError(
                    f"Impossible state when going back: {self.nav_state}"
                )

    def _write_state(self, new_state: NavigationState):
        """a small wrapper for places that only take functions

        :param new_state: the state to write
        """
        self.nav_state = new_state


@cli_app.command("lp", help="Submit a bug to Launchpad")
def launchpad_mode(
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
    sudo_devmode_check()
    BugitApp(
        AppArgs(
            submitter="lp",
            checkbox_submission=None,
            bug_to_reopen=None,
            cid=cid,
            sku=sku,
            project=project,
            assignee=assignee,
            platform_tags=platform_tags,
            tags=tags,
        )
    ).run()


@cli_app.command("jira", help="Submit a bug to Jira")
def jira_mode(
    checkbox_submission: Annotated[
        Path | None,
        typer.Option(
            "-s",
            "--checkbox-submission",
            help=(
                "The .tar.xz file submitted by checkbox after a test session has finished. "
                + "If this option is specified, "
                + "Bugit will read from this file instead of checkbox sessions "
                + "and enter the editor directly"
            ),
            exists=True,
            dir_okay=False,
            file_okay=True,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
    cid: Annotated[
        str | None,
        typer.Option(
            "-c",
            "--cid",
            help=(
                "Canonical ID (CID) of the device under test. "
                + 'This is used to pre-fill the "CID" field in the editor'
            ),
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
            help="Stock Keeping Unit (SKU) string of the device under test. "
            + 'This is used to pre-fill the "SKU" field in the editor',
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
            help="Project name (case sensitive) like STELLA, SOMERVILLE. "
            + 'This is used to pre-fill the "Project" field in the editor',
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
            help="Assignee ID. For Jira it's the assignee's email. "
            + 'This is used to pre-fill the "Assignee" field in the editor',
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
            help='Platform Tags. They will appear under "Components" on Jira. '
            + 'This is used to pre-fill the "Platform Tags" field in the editor',
            file_okay=False,
            dir_okay=False,
        ),
    ] = [],  # pyright: ignore[reportCallInDefaultInitializer]
    tags: Annotated[
        list[str],
        typer.Option(
            "-t",
            "--tags",
            help="Additional tags on Jira. "
            + 'This is used to pre-fill the "Tags" field in the editor',
            file_okay=False,
            dir_okay=False,
        ),
    ] = [],  # pyright: ignore[reportCallInDefaultInitializer]
):
    sudo_devmode_check()
    cbs = checkbox_submission_check(checkbox_submission)

    BugitApp(
        # reopen is disabled for now
        AppArgs(
            submitter="jira",
            checkbox_submission=cbs,
            bug_to_reopen=None,
            cid=cid,
            sku=sku,
            project=project,
            assignee=assignee,
            platform_tags=platform_tags,
            tags=tags,
        )
    ).run()


if __name__ == "__main__":
    cli_app(prog_name="bugit.bugit-v2" if is_snap() else "bugit-v2")
