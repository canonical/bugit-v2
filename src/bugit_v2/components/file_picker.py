from pathlib import Path
from typing import final, override
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.reactive import var
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, DirectoryTree, Label

from bugit_v2.utils import is_snap


class FilePickerModal(ModalScreen[Path | None]):
    @override
    def compose(self) -> ComposeResult:
        yield DirectoryTree(Path("/var/lib/snapd/hostfs/" if is_snap() else "/"))
        yield Button("Close", flat=True, id="close")

    @on(DirectoryTree.FileSelected)
    def finish_selection(self, e: DirectoryTree.FileSelected):
        self.dismiss(e.path)

    @on(Button.Pressed, "#close")
    def exit_without_selection(self, _):
        self.dismiss(None)


@final
class FilePickerWidget(Widget):
    """This widget has a list of chosen files and a button for adding new files
    Clicking the add button will show a full screen file FilePickerModal
    """

    chosen_files = var[list[Path]](lambda: [])

    @override
    def compose(self) -> ComposeResult:
        yield Button("Pick Files", id="pick_files")
        # for file in self.chosen_files:
        yield VerticalScroll(id="file_list_scroll")

    @work
    @on(Button.Pressed, "#pick_files")
    async def open_file_picker(self, _):
        o = await self.app.push_screen_wait(FilePickerModal())
        if o is not None:
            self.chosen_files = [*self.chosen_files, o]

    def watch_chosen_files(self):
        scroll = self.query_exactly_one(VerticalScroll)
        scroll.query_children().remove()
        scroll.mount_all(Label(str(file)) for file in self.chosen_files)


class DirectoryTreeApp(App[Path]):
    @override
    def compose(self) -> ComposeResult:
        yield FilePickerWidget()


if __name__ == "__main__":
    app = DirectoryTreeApp()
    o = app.run()
    print(o)
