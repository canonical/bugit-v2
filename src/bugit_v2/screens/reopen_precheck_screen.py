from typing import Final, final, override

from textual import work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Label
from textual.worker import Worker, WorkerState

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    BugReportSubmitter,
)
from bugit_v2.components.header import SimpleHeader
from bugit_v2.models.app_args import AppArgs


# must finish auth here instead of waiting until the submitter
@final
class ReopenPreCheckScreen[TAuth, TReturn](Screen[bool | Exception]):
    app_args: Final[AppArgs]
    submitter: Final[BugReportSubmitter[TAuth, TReturn]]

    # static var, did a check already happen?
    # don't check over and over again
    already_checked = False

    CSS_PATH = "styles.tcss"
    CSS = """
    ReopenPreCheckScreen {
        align: center middle;
    }
    """

    def __init__(
        self,
        submitter: BugReportSubmitter[TAuth, TReturn],
        app_args: AppArgs,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.submitter = submitter
        self.app_args = app_args
        super().__init__(name, id, classes)

    @work
    async def on_mount(self):
        assert self.submitter.auth_modal
        assert self.app_args.bug_to_reopen

        try:
            cached_credentials = self.submitter.get_cached_credentials()

            if cached_credentials is None:
                auth_rv = await self.app.push_screen_wait(
                    self.submitter.auth_modal()
                )
                assert auth_rv is not None
                (
                    self.submitter.auth,
                    self.submitter.allow_cache_credentials,
                ) = auth_rv
            else:
                (
                    self.submitter.auth,
                    self.submitter.allow_cache_credentials,
                ) = (
                    cached_credentials,
                    True,  # if it was saved before,
                    # then allow_cache_credentials is definitely true
                )
            self.run_worker(
                # early bind
                lambda b=self.app_args.bug_to_reopen: self.submitter.bug_exists(
                    b
                ),
                name="bug_existence_check",
                exit_on_error=False,
                thread=True,
            )
        except Exception as e:
            self.dismiss(e)

    @override
    def compose(self) -> ComposeResult:
        yield SimpleHeader()
        if self.app_args.submitter == "jira":
            yield Label(
                f"Checking Jira auth and making sure [u]{self.app_args.bug_to_reopen}[/] exists before reopening...",
                classes="mw75",
            )
        else:
            yield Label(
                f"Checking Launchpad auth and making sure [u]{self.app_args.bug_to_reopen}[/] exists before reopening...",
                classes="mw75",
            )

    def on_worker_state_changed(self, event: Worker.StateChanged):
        if event.worker.name != "bug_existence_check":
            return

        ReopenPreCheckScreen.already_checked = True
        match event.state:
            case WorkerState.SUCCESS:
                assert (
                    type(
                        event.worker.result  # pyright: ignore[reportUnknownArgumentType]
                    )
                    is bool
                )
                self.dismiss(event.worker.result)
            case WorkerState.ERROR:
                if isinstance(event.worker.error, Exception):
                    self.dismiss(event.worker.error)
                else:
                    self.dismiss(False)
            case _:
                pass
