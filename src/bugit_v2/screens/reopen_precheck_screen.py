from typing import Final, final, override

from textual import work
from textual.app import ComposeResult
from textual.screen import Screen

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    BugReportSubmitter,
)
from bugit_v2.models.app_args import AppArgs


# must finish auth here instead of waiting until the submitter
@final
class ReopenPreCheckScreen[A, R](Screen[bool | Exception]):
    app_args: Final[AppArgs]
    submitter: Final[BugReportSubmitter[A, R]]

    def __init__(
        self,
        submitter: BugReportSubmitter[A, R],
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

            if self.submitter.bug_exists(self.app_args.bug_to_reopen):
                self.dismiss(True)
            else:
                self.dismiss(False)
        except Exception as e:
            self.dismiss(e)

    @override
    def compose(self) -> ComposeResult:
        return super().compose()
