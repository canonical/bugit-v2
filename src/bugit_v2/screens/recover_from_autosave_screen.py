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
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Label, Rule, Switch

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
                    print(e)

        super().__init__(name, id, classes)

    @override
    def compose(self) -> ComposeResult:
        yield SimpleHeader()
        with Vertical(classes="w100 h100 center"):
            with VerticalGroup(classes="round_box lrp2"):
                yield Label("[b][$primary]Select a Recovery File")
                yield Rule(classes="m0 boost", line_style="ascii")
                with HorizontalGroup():
                    yield Switch(
                        id="mode_toggle",
                        animate=False,
                        classes="nb mr1",
                        value=self.is_relative,
                    )
                    yield Label(
                        "Relative Timestamp"
                        if self.is_relative
                        else "Absolute Timestamp"
                    )
                    with Right():
                        yield Button(
                            "Start a new bug report (Don't recover)",
                            name="no_recovery",
                            tooltip="This will not delete any of the existing recovery files",
                            compact=True,
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

    @on(Switch.Changed, "#mode_toggle")
    def change_mode(self, event: Switch.Changed):
        print("called!")
        self.is_relative = event.switch.value

    @on(Button.Pressed)
    def finish_selection(self, event: Button.Pressed):
        assert event.button.name
        if event.button.name == "no_recovery":
            self.dismiss(None)
        else:
            self.dismiss(self.valid_autosave_data[event.button.name])

    def _button_text(self, filename: str):
        assert filename in self.valid_autosave_data
        lines: list[str] = []
        if self.is_relative:
            lines.append(
                "[$primary]"
                + pretty_date(
                    datetime.datetime.fromtimestamp(
                        os.stat(self.autosave_dir / filename).st_ctime
                    )
                ),
            )
        else:
            lines.append(
                "[$primary]"
                + datetime.datetime.fromtimestamp(
                    os.stat(self.autosave_dir / filename).st_ctime
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

        if job_id := self.valid_autosave_data[filename].job_id:
            lines.append(f"[grey]{job_id}")
        return "\n".join(lines)  #
