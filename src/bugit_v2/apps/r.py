from pathlib import Path
from typing import override
from textual import on, work
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import DirectoryTree


class TreeScreen(Screen[Path]):
    @override
    def compose(self) -> ComposeResult:
        yield DirectoryTree(Path('/var/lib/snapd/hostfs/'))

    @on(DirectoryTree.FileSelected)
    def finish_selection(self, e: DirectoryTree.FileSelected):
        self.dismiss(e.path)


class DirectoryTreeApp(App[Path]):
    @work
    async def on_mount(self):
        res = await self.push_screen_wait(TreeScreen())
        self.exit(res)


if __name__ == "__main__":
    app = DirectoryTreeApp()
    o = app.run()
    print(o)
