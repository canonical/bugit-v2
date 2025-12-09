from pathlib import Path
from typing import final, override

import typer
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center
from textual.content import Content
from textual.driver import Driver
from textual.reactive import var
from textual.types import CSSPathType
from textual.widgets import Label
from typing_extensions import Annotated

from bugit_v2.bug_report_submitters.jira_submitter import JiraSubmitter
from bugit_v2.bug_report_submitters.launchpad_submitter import (
    LaunchpadSubmitter,
)
from bugit_v2.bug_report_submitters.mock_jira import MockJiraSubmitter
from bugit_v2.bug_report_submitters.mock_lp import MockLaunchpadSubmitter
from bugit_v2.checkbox_utils import get_checkbox_version
from bugit_v2.components.header import SimpleHeader
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.app_state import (
    AppContext,
    AppState,
    QuitState,
    RecoverFromAutosaveState,
    SubmissionProgressState,
)
from bugit_v2.utils import get_bugit_version, is_prod, is_snap
from bugit_v2.utils.constants import LOGO_ASCII_ART, NullSelection
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


@final
class BugitApp(App[None]):

    state = var[AppState](RecoverFromAutosaveState(), init=False)

    BINDINGS = [Binding("alt+left", "go_back", "Go Back")]
    CSS = """
    #spinner_wrapper {
        height: 100%;
        align: center middle;
        content-align: center middle;
    }
    """

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
                submitter_class = (
                    JiraSubmitter if is_prod() else MockJiraSubmitter
                )
            case "lp":
                submitter_class = (
                    LaunchpadSubmitter if is_prod() else MockLaunchpadSubmitter
                )

        self.state.context = AppContext(
            args,
            submitter_class,
            session=(
                None
                if args.checkbox_submission
                is NullSelection.NO_CHECKBOX_SUBMISSION
                else NullSelection.NO_SESSION
            ),
            checkbox_submission=args.checkbox_submission,
        )

    @work(thread=True)
    def on_mount(self) -> None:
        self.theme = "solarized-light"
        if is_prod():
            self.title = "Bugit V2"
        else:
            self.title = "Bugit V2 🐛🐛 DEBUG MODE 🐛🐛"

        # snap checkbox takes a while to respond especially if it's the
        # 1st use after reboot
        if (tv := get_checkbox_version()) is not None:
            _, cb_version = tv
            self.sub_title = f"Checkbox {cb_version}"

        self.call_after_refresh(self.watch_state)

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
    async def watch_state(self):
        self.state.assertions()
        match self.state:
            case QuitState():
                self.exit()
            case _:
                self.state = self.state.go_forward(
                    # never used anywhere else
                    # Any is ok
                    await self.push_screen_wait(  # pyright: ignore[reportAny]
                        self.state.get_screen_constructor()()
                    )
                )

    def action_go_back(self):
        if (next_state := self.state.go_back()) is not None:
            self.state = next_state
        elif isinstance(self.state, SubmissionProgressState):
            self.notify(
                title="Cannot go back while a submission is happening",
                message="But you can force quit with Ctrl+Q",
            )
        else:
            self.notify("Already at the beginning")

    @override
    def compose(self) -> ComposeResult:
        yield SimpleHeader()
        with Center(id="spinner_wrapper"):
            yield Label(LOGO_ASCII_ART, id="logo")


def version_callback(value: bool):
    if value:
        typer.echo(f"v{get_bugit_version()}")
        raise typer.Exit()


@cli_app.callback()
def common(
    _ctx: typer.Context,
    _version: Annotated[
        bool | None,
        typer.Option("--version", callback=version_callback, is_eager=True),
    ] = None,
):
    pass


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
            checkbox_submission=NullSelection.NO_CHECKBOX_SUBMISSION,
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

    if checkbox_submission:
        print(f"Decompressing checkbox submission at {checkbox_submission}")

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
