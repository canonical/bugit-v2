import logging
from typing import final

from cysystemd import journal
from textual import work
from textual.app import App
from textual.logging import TextualHandler

from bugit_v2.bug_report_submitters.launchpad_submitter import (
    LaunchpadAuthModal,
)
from bugit_v2.utils import is_prod, is_snap

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


@final
class yApp(App[None]):
    @work
    async def on_mount(self):
        await self.push_screen_wait(LaunchpadAuthModal())


if __name__ == "__main__":
    yApp().run()
