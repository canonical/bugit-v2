import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import final, override

from rich.console import RenderableType
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import HorizontalGroup, VerticalScroll
from textual.content import ContentText
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, DirectoryTree, Label
from textual.widgets.button import ButtonVariant

from bugit_v2.utils import is_snap
from bugit_v2.utils.constants import HOST_FS, MAX_ADDITIONAL_FILE_SIZE


class NoSpecialFileDirectoryTree(DirectoryTree):
    @override
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if path.is_dir() or path.is_file()]


class FilePickerModal(ModalScreen[Path | None]):
    discovery_root: Path

    def __init__(
        self,
        discovery_root: Path,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        if not discovery_root.exists():
            raise FileNotFoundError(f"{discovery_root} doesn't exist")
        self.discovery_root = discovery_root
        super().__init__(name, id, classes)

    @override
    def compose(self) -> ComposeResult:
        yield NoSpecialFileDirectoryTree(self.discovery_root)
        yield Button("Close", flat=True, id="close")

    @on(DirectoryTree.FileSelected)
    def finish_selection(self, e: DirectoryTree.FileSelected):
        try:
            st_size = os.stat(e.path).st_size
        except OSError as exc:
            self.app.notify(
                f"Unable to read file metadata for {e.path}: {exc}",
                title="File selection failed",
                severity="error",
            )
            return

        if st_size > MAX_ADDITIONAL_FILE_SIZE:
            self.app.notify(
                f"File size = {st_size // 10**6}Mb. Please choose a smaller file.",
                title=f"This file is over {MAX_ADDITIONAL_FILE_SIZE // 10**6}Mb",
                severity="error",
            )
            return

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

    @final
    class FilesUpdated(Message):
        def __init__(self, files: list[Path]) -> None:
            super().__init__()
            self.files = files

    _chosen_files: set[Path]

    DEFAULT_CSS = """
    FilePickerWidget {
        height: auto;
        width: 100%;
    }
    FilePickerWidget Label {
        width: 2fr;
        overflow-x: hidden;
        text-overflow: ellipsis;
    }
    FilePickerWidget .delete_selection {
        dock: right;
    }
    FilePickerWidget .filename_label {
        margin-right: 1;
    }
    """

    def __init__(
        self,
        initial_files: Sequence[Path] | None = None,
        # ---
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            markup=markup,
        )
        self._chosen_files = set()
        if not initial_files:
            return
        for file in initial_files:
            if file.exists():
                self._chosen_files.add(file)

    @override
    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="file_list_scroll", classes="ha")
        yield Button("Pick Files", id="pick_files", compact=True)

    @work
    @on(Button.Pressed, "#pick_files")
    async def open_file_picker(self, _):
        selection = await self.app.push_screen_wait(
            FilePickerModal(HOST_FS if is_snap() else Path("/"))
        )
        if selection is None:
            return
        self._chosen_files.add(selection)
        self._redraw_file_list()
        self.post_message(self.FilesUpdated(list(self._chosen_files)))

    def on_mount(self):
        self._redraw_file_list()

    @on(Button.Pressed, ".delete_selection")
    def delete_file_from_list(self, event: Button.Pressed):
        assert event.button.name is not None
        try:
            self._chosen_files.remove(Path(event.button.name))
            self.post_message(self.FilesUpdated(list(self._chosen_files)))
        except KeyError:
            pass  # if the user is clicking very fast
        self._redraw_file_list()

    @property
    def chosen_files(self) -> Sequence[Path]:
        return list(self._chosen_files)

    def restore_selection(self, files: Sequence[Path]):
        self._chosen_files = set(files)

        for file in files:
            if not file.exists():
                self.app.notify(
                    "It existed during the last autosave, but not anymore",
                    title=f"{file} was deselected",
                    severity="warning",
                )
                self._chosen_files.remove(file)

        self._redraw_file_list()

    def _redraw_file_list(self):
        scroll = self.query_exactly_one(VerticalScroll)
        scroll.query_children().remove()
        groups: list[Widget] = []
        for file in self._chosen_files:
            label = Label(str(file), classes="filename_label")
            label.tooltip = str(file)
            groups.append(
                HorizontalGroup(
                    label,
                    CompactButton(
                        "Deselect",
                        variant="error",
                        classes="delete_selection",
                        name=str(file),
                    ),
                )
            )
        scroll.mount_all(groups)


class DirectoryTreeApp(App[Path]):
    @override
    def compose(self) -> ComposeResult:
        yield FilePickerWidget()


if __name__ == "__main__":
    app = DirectoryTreeApp()
    o = app.run()
    print(o)
