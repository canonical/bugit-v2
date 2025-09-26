import datetime
import os
from pathlib import Path
from typing import final, override

import pydantic
from textual import on
from textual.app import ComposeResult
from textual.containers import (
    HorizontalGroup,
    Right,
    Vertical,
    VerticalGroup,
    VerticalScroll,
)
from textual.content import Content
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Label, Rule

from bugit_v2.components.header import SimpleHeader
from bugit_v2.models.bug_report import BugReportAutoSaveData
from bugit_v2.utils import pretty_date
from bugit_v2.utils.constants import AUTOSAVE_DIR


@final
class RecoverFromAutoSaveScreen(Screen[BugReportAutoSaveData | None]):

    CSS_PATH = "styles.tcss"

    is_relative = reactive[bool](False, recompose=True)
    valid_autosave_data: dict[str, BugReportAutoSaveData]

    def __init__(
        self,
        autosave_dir: Path = AUTOSAVE_DIR,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.autosave_dir = autosave_dir
        self.valid_autosave_data = {}
        # file names are already timestamps, can just use string sort
        for file in sorted(os.listdir(autosave_dir), reverse=True):
            with open(autosave_dir / file) as f:
                try:
                    self.valid_autosave_data[file] = (
                        BugReportAutoSaveData.model_validate_json(f.read())
                    )
                except pydantic.ValidationError as e:
                    self.log.error(e)

        super().__init__(name, id, classes)

    @override
    def compose(self) -> ComposeResult:
        yield SimpleHeader()
        with Vertical(classes="w100 h100 center"):
            with VerticalGroup(classes="round_box lrp2"):
                yield Label("[b][$primary]Resume from a Recovery File")
                yield Label(
                    "These were automatically saved by the bug report editor"
                )
                yield Rule(classes="m0 boost", line_style="ascii")
                with HorizontalGroup():
                    yield Checkbox(
                        id="mode_toggle",
                        classes="mr1 nb",
                        value=self.is_relative,
                        compact=True,
                    )
                    yield Label(
                        "Relative Timestamp"
                        if self.is_relative
                        else "Absolute Timestamp"
                    )
                    with Right():
                        yield Button(
                            "Start a new bug report (Don't recover)",
                            id="no_recovery",
                            tooltip="This will not delete any of the existing recovery files",
                            compact=True,
                            classes="editor_button",
                        )

            yield VerticalScroll(
                *(
                    Button(
                        self._button_text(filename),
                        name=filename,  # can't have slashes in id
                        classes="mb1 session_button",
                    )
                    for filename in self.valid_autosave_data.keys()
                ),
                classes="w100 center",
            )

        yield Footer()

    @on(Checkbox.Changed, "#mode_toggle")
    def change_mode(self, event: Checkbox.Changed):
        self.is_relative = event.checkbox.value

    @on(Button.Pressed)
    def finish_selection(self, event: Button.Pressed):
        if event.button.id == "no_recovery":
            self.dismiss(None)
        else:
            assert event.button.name
            self.dismiss(self.valid_autosave_data[event.button.name])

    def _button_text(self, filename: str) -> Content:
        assert filename in self.valid_autosave_data
        lines: list[str] = []
        if self.is_relative:
            lines.append(
                "Saved [$primary]"
                + pretty_date(
                    datetime.datetime.fromtimestamp(
                        os.stat(self.autosave_dir / filename).st_ctime
                    )
                ),
            )
        else:
            lines.append(
                "Saved at [$primary]"
                + datetime.datetime.fromtimestamp(
                    os.stat(self.autosave_dir / filename).st_ctime
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

        if session_path := self.valid_autosave_data[filename].checkbox_session:
            lines.append(f"[grey]{os.path.basename(session_path)}")
        else:
            lines.append("[i][grey]No session selected")

        if job_id := self.valid_autosave_data[filename].job_id:
            lines.append(f"[grey]{job_id}")
        else:
            lines.append("[i][grey]No job selected")

        return Content("\n").join(Content.from_markup(line) for line in lines)
