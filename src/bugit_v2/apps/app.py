import json
import logging
import os
from pathlib import Path
from typing import Annotated, Literal, final, override

import typer
from cysystemd import journal
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center
from textual.content import Content
from textual.driver import Driver
from textual.logging import TextualHandler
from textual.reactive import var
from textual.types import CSSPathType
from textual.widgets import Label

from bugit_v2.bug_report_submitters.jira_submitter import JiraSubmitter
from bugit_v2.bug_report_submitters.launchpad_submitter import LaunchpadSubmitter
from bugit_v2.bug_report_submitters.local_file_submitter import LocalFileSubmitter
from bugit_v2.bug_report_submitters.mock_jira import MockJiraSubmitter
from bugit_v2.bug_report_submitters.mock_lp import MockLaunchpadSubmitter
from bugit_v2.checkbox_utils.checkbox_exec import (
    get_checkbox_info,
    set_checkbox_bin_path_override,
)
from bugit_v2.components.header import SimpleHeader
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.app_state import (
    AppContext,
    AppState,
    QuitState,
    RecoverFromAutosaveState,
    SubmissionProgressState,
)
from bugit_v2.models.dut_info import DutInfo, get_saved_dut_info
from bugit_v2.models.visual_customization import VisualConfig
from bugit_v2.utils import get_bugit_version, is_prod, is_snap
from bugit_v2.utils.constants import LOGO_ASCII_ART, VISUAL_CONFIG_DIR, NullSelection
from bugit_v2.utils.validations import (
    checkbox_submission_check,
    ensure_all_directories_exist,
    is_cid,
    is_jira_issue_key,
    is_lp_bug_id,
    sudo_devmode_check,
)

logging.basicConfig(
    level=logging.INFO if is_prod() else logging.DEBUG,
    handlers=[
        (
            journal.JournaldLogHandler(identifier="bugit.bugit-v2")
            if is_snap()
            else TextualHandler()
        )
    ],
)
logger = logging.getLogger(__name__)


cli_app = typer.Typer(
    help="Bugit is a tool for creating bug reports on Launchpad and Jira",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    pretty_exceptions_enable=not is_prod(),
    pretty_exceptions_show_locals=not is_prod(),
    add_completion=not is_snap(),  # the built-in ones doesn't work in snap
)


def executable_check(value: Path | None) -> Path | None:
    if value:
        if not os.access(value, os.X_OK):
            raise typer.BadParameter(f"{value} is not executable")
        else:
            return value
    return value


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
                submitter_class = JiraSubmitter if is_prod() else MockJiraSubmitter
            case "lp":
                submitter_class = (
                    LaunchpadSubmitter if is_prod() else MockLaunchpadSubmitter
                )
            case "local":
                submitter_class = LocalFileSubmitter

        self.state.context = AppContext(
            args,
            submitter_class,
            session=(
                None
                if args.checkbox_submission is NullSelection.NO_CHECKBOX_SUBMISSION
                else NullSelection.NO_SESSION
            ),
            checkbox_submission=args.checkbox_submission,
        )

    @work
    async def on_mount(self) -> None:
        try:
            with open(VISUAL_CONFIG_DIR / "visual-config.json") as f:
                self.theme = VisualConfig.model_validate(json.load(f)).theme
        except FileNotFoundError:
            self.theme = "solarized-light"

        if is_prod():
            self.title = "Bugit V2"
        else:
            self.title = "Bugit V2 🐛🐛 DEBUG MODE 🐛🐛"

        # snap checkbox takes a while to respond especially if it's the
        # 1st use after reboot
        if (cb := get_checkbox_info()) is not None:
            self.sub_title = f"Checkbox {cb.version}"

        self.call_after_refresh(self.watch_state)

    def watch_theme(self, theme: str) -> None:
        with open(VISUAL_CONFIG_DIR / "visual-config.json", "w") as f:
            f.write(VisualConfig(theme=theme).model_dump_json())

    @override
    def format_title(self, title: str, sub_title: str) -> Content:
        match (title, sub_title, self.args.bug_to_attach):
            case (str(t), str(s), str(b)):
                return Content.assemble(
                    Content(t),
                    (" - ", "dim"),
                    Content(s).stylize("$secondary"),
                    (" - ", "dim"),
                    Content(f"Attach to {b}").stylize("dim"),
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
            logger.fatal(error)
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
                screen_result = (
                    await self.push_screen_wait(  # pyright: ignore[reportAny]
                        self.state.get_screen_constructor()()
                    )
                )
                self.state = self.state.go_forward(
                    screen_result  # pyright: ignore[reportAny]
                )

    def action_go_back(self):
        if (next_state := self.state.go_back()) is not None:
            self.pop_screen()
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


CheckboxSubmissionOption = Annotated[
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
]
CheckboxBinPathOption = Annotated[
    Path | None,
    typer.Option(
        "-cb",
        "--checkbox-bin-path",
        help="Override the path to the checkbox executable. "
        + "Use this option if bugit can't find your checkbox installation",
        dir_okay=False,
        file_okay=True,
        resolve_path=True,
        readable=True,
        callback=executable_check,
    ),
]


@cli_app.command("local", help="Pack the bug report to a local file")
@cli_app.command("lp", help="Submit a bug to Launchpad")
@cli_app.command("jira", help="Submit a bug to Jira")
def main(
    ctx: typer.Context,
    checkbox_submission: CheckboxSubmissionOption = None,
    checkbox_bin_path: CheckboxBinPathOption = None,
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
            help="Assignee ID. For Jira it's the assignee's email. For Launchpad it's the launchpad ID. "
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
            help='Platform Tags. They will appear under "Components" on Jira, or as regular tags on Launchpad. '
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
            help="Additional tags on Jira/Launchpad. "
            + 'This is used to pre-fill the "Tags" field in the editor',
            file_okay=False,
            dir_okay=False,
        ),
    ] = [],  # pyright: ignore[reportCallInDefaultInitializer]
):
    sudo_devmode_check()
    ensure_all_directories_exist()
    if ctx.command.name not in ("lp", "jira", "local"):
        raise RuntimeError(f"Unexpected command name: {ctx.command.name!r}")

    if checkbox_submission:
        print(f"Decompressing checkbox submission at {checkbox_submission}")

    cbs = checkbox_submission_check(checkbox_submission)
    saved_dut_info = get_saved_dut_info() or DutInfo()  # all none

    print("Waiting for checkbox...")
    set_checkbox_bin_path_override(checkbox_bin_path)
    get_checkbox_info()  # populate cache

    BugitApp(
        AppArgs(
            submitter=ctx.command.name,
            checkbox_submission=cbs,
            cid=cid or saved_dut_info.cid,
            sku=sku or saved_dut_info.sku,
            project=project or saved_dut_info.project,
            assignee=assignee
            or (
                # NOTE: user need to manually set this param when using `local`
                saved_dut_info.lp_assignee
                if ctx.command.name == "lp"
                else saved_dut_info.jira_assignee
            ),
            platform_tags=platform_tags or saved_dut_info.platform_tags,
            tags=tags or saved_dut_info.tags,
        )
    ).run()


def jira_issue_key_check(value: str) -> str:
    value = value.strip().upper()
    if not is_jira_issue_key(value):
        raise typer.BadParameter(
            f"Invalid Jira issue key: '{value}'. "
            + "It should look like STELLA-123 (uppercase project key, dash, number)"
        )
    return value


def lp_bug_id_check(value: str) -> str:
    value = value.strip()
    if not is_lp_bug_id(value):
        raise typer.BadParameter(
            f"Invalid Launchpad bug ID: '{value}'. It should be a number like 2091234"
        )
    return value


def run_attach(
    submitter: Literal["jira", "lp"],
    bug_id: str,
    checkbox_submission: Path | None,
    checkbox_bin_path: Path | None,
) -> None:
    sudo_devmode_check()
    ensure_all_directories_exist()

    if checkbox_submission:
        print(f"Decompressing checkbox submission at {checkbox_submission}")
    cbs = checkbox_submission_check(checkbox_submission)

    print("Waiting for checkbox...")
    set_checkbox_bin_path_override(checkbox_bin_path)
    get_checkbox_info()  # populate cache

    BugitApp(
        AppArgs(
            submitter=submitter,
            checkbox_submission=cbs,
            bug_to_attach=bug_id,
        )
    ).run()


@cli_app.command(
    "attach-jira",
    help="Attach logs and a comment to an existing Jira issue",
)
def attach_jira(
    bug_id: Annotated[
        str,
        typer.Argument(
            metavar="ISSUE_KEY",
            help="The Jira issue to attach to, like STELLA-123",
            callback=jira_issue_key_check,
        ),
    ],
    checkbox_submission: CheckboxSubmissionOption = None,
    checkbox_bin_path: CheckboxBinPathOption = None,
):
    run_attach("jira", bug_id, checkbox_submission, checkbox_bin_path)


@cli_app.command(
    "attach-lp",
    help="Attach logs and a comment to an existing Launchpad bug",
)
def attach_lp(
    bug_id: Annotated[
        str,
        typer.Argument(
            metavar="BUG_ID",
            help="The Launchpad bug number to attach to, like 2091234",
            callback=lp_bug_id_check,
        ),
    ],
    checkbox_submission: CheckboxSubmissionOption = None,
    checkbox_bin_path: CheckboxBinPathOption = None,
):
    run_attach("lp", bug_id, checkbox_submission, checkbox_bin_path)


if __name__ == "__main__":
    cli_app(prog_name="bugit.bugit-v2" if is_snap() else "bugit-v2")
