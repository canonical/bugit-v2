import datetime
import os
from pathlib import Path
from typing import final, override

from textual import on
from textual.app import ComposeResult
from textual.containers import HorizontalGroup, VerticalGroup
from textual.widget import Widget
from textual.widgets import Button, TextArea


@final
class DescriptionEditor(Widget):

    DEFAULT_CSS = """
    .editor_button {
        background-tint: $primary 10%;
    }

    .right {
        align: right middle;
    }

    .mr1 {
        margin-right: 1;
    }
    """

    def __init__(
        self,
        *children: Widget,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
        markup: bool = True,
    ) -> None:
        super().__init__(
            *children,
            name=name,
            id=id,
            classes=classes,
            disabled=disabled,
            markup=markup,
        )

    @override
    def compose(self) -> ComposeResult:
        with VerticalGroup():
            yield TextArea(
                "Waiting for basic machine info to be collected (30 second timeout)...",
                classes="default_box",
                show_line_numbers=True,
                soft_wrap=True,
            )
            yield HorizontalGroup(
                Button(
                    "Hide Line Numbers",
                    id="show_line_numbers_toggle",
                    compact=True,
                    classes="editor_button",
                ),
                Button(
                    "Toggle Line Wrap",
                    id="wrap_text_toggle",
                    compact=True,
                    classes="editor_button",
                    tooltip="Wrap long lines around so you don't need to scroll to see them",
                ),
                Button(
                    "Save as Text File",
                    id="save_as_text_file",
                    compact=True,
                    classes="editor_button mr1",
                    tooltip="Save the description to the current directory as a .txt file",
                ),
                classes="right",
            )

    @property
    def text(self) -> str:
        return self.query_exactly_one(TextArea).text

    @text.setter
    def text(self, value: str) -> None:
        self.query_exactly_one(TextArea).text = value

    @property
    @override
    def border_title(self) -> str | None:
        return self.query_exactly_one(TextArea).border_title

    @border_title.setter
    def border_title(self, value: str) -> str | None:
        self.query_exactly_one(TextArea).border_title = value

    @property
    @override
    def border_subtitle(self) -> str | None:
        return self.query_exactly_one(TextArea).border_subtitle

    @border_subtitle.setter
    def border_subtitle(self, value: str) -> str | None:
        self.query_exactly_one(TextArea).border_subtitle = value

    @on(Button.Pressed, "#wrap_text_toggle")
    def toggle_wrap(self):
        text_area = self.query_exactly_one(TextArea)
        btn = self.query_exactly_one("#wrap_text_toggle", Button)
        text_area.soft_wrap = not text_area.soft_wrap
        btn.label = "Disable Wrap" if text_area.soft_wrap else "Enable Wrap"

    @on(Button.Pressed, "#show_line_numbers_toggle")
    def toggle_line_number(self):
        text_area = self.query_exactly_one(TextArea)
        btn = self.query_exactly_one("#show_line_numbers_toggle", Button)
        text_area.show_line_numbers = not text_area.show_line_numbers
        btn.label = (
            "Hide Line Numbers"
            if text_area.show_line_numbers
            else "Show Line Numbers"
        )

    @on(Button.Pressed, "#save_as_text_file")
    def save_as_text_file(self):
        content = self.query_exactly_one(TextArea).text
        btn = self.query_exactly_one("#save_as_text_file", Button)
        old_label = btn.label

        try:
            timestamp = (
                datetime.datetime.now()
                .isoformat(timespec="seconds")
                .replace(":", ".")
            )  # this produces a checkbox-style timestamp
            file_path = Path(os.curdir) / f"bug-description-{timestamp}.txt"
            with open(file_path, "w") as file:
                file.write(content)
                btn.label = "Saved!"
                self.notify(
                    title="Saved current bug description!",
                    timeout=10,  # make it longer so users can see the path
                    message=f"It's at {file_path.absolute().expanduser()}",
                )

                def f():
                    btn.label = old_label

                self.set_timer(3, f)
        except Exception as e:
            self.notify(f"Failed to save. Reason {repr(e)}")
