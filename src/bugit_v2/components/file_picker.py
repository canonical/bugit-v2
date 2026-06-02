from pathlib import Path
from typing import final, override
from rich.console import RenderableType
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import HorizontalGroup, VerticalScroll
from textual.content import ContentText
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, DirectoryTree, Label
from textual.widgets.button import ButtonVariant
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
class CompactButton(Button):
    DEFAULT_CSS = """
    CompactButton {
        width: auto;
        height: auto;
        min-width: 1;
        min-height: 1;
        padding: 0;
        border: none;
    }
    """

    def __init__(
        self,
        label: ContentText | None = None,
        variant: ButtonVariant = "default",
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        tooltip: RenderableType | None = None,
        action: str | None = None,
        flat: bool = False,
    ):
        super().__init__(
            label,
            variant,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            tooltip=tooltip,
            action=action,
            compact=True,
            flat=flat,
        )


@final
class FilePickerWidget(Widget):
    """This widget has a list of chosen files and a button for adding new files
    Clicking the add button will show a full screen file FilePickerModal
    """

    chosen_files = set[Path]()

    DEFAULT_CSS = """
    FilePickerWidget {
        height: auto
    }
    """

    @override
    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="file_list_scroll")
        yield Button("Pick Files", id="pick_files", flat=True, classes="debug")

    @work
    @on(Button.Pressed, "#pick_files")
    async def open_file_picker(self, _):
        selection = await self.app.push_screen_wait(FilePickerModal())
        if selection is None:
            return
        self.chosen_files.add(selection)
        self._redraw_file_list()

    @on(Button.Pressed, ".delete_selection")
    def delete_file_from_list(self, event: Button.Pressed):
        assert event.button.name is not None
        try:
            self.chosen_files.remove(Path(event.button.name))
        except KeyError:
            pass  # if the user is clicking very fast
        self._redraw_file_list()

    def _redraw_file_list(self):
        scroll = self.query_exactly_one(VerticalScroll)
        scroll.query_children().remove()
        scroll.mount_all(
            HorizontalGroup(
                Label(str(file)),
                Label(" "),
                CompactButton(
                    "Del", variant="error", classes="delete_selection", name=str(file)
                ),
            )
            for file in self.chosen_files
        )


class DirectoryTreeApp(App[Path]):
    @override
    def compose(self) -> ComposeResult:
        yield FilePickerWidget()


if __name__ == "__main__":
    app = DirectoryTreeApp()
    o = app.run()
    print(o)
