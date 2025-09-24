import datetime
import os
from pathlib import Path
from typing import final, override

import pydantic
from textual import on
from textual.app import ComposeResult
from textual.containers import (
    HorizontalGroup,
    Vertical,
    VerticalGroup,
    VerticalScroll,
)
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Label, Switch

from bugit_v2.components.header import SimpleHeader
from bugit_v2.models.bug_report import BugReportAutoSaveData
from bugit_v2.utils import pretty_date
from bugit_v2.utils.constants import AUTOSAVE_DIR


@final
class RecoverFromAutoSaveScreen(Screen[BugReportAutoSaveData | None]):

    CSS_PATH = "styles.tcss"

    is_relative = reactive[bool](False, recompose=True)

    def __init__(
        self,
        autosave_dir: Path = AUTOSAVE_DIR,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.autosave_dir = autosave_dir
        self.valid_autosave_data: dict[str, BugReportAutoSaveData] = {}
        for file in os.listdir(autosave_dir):
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
            with VerticalGroup(classes="m1"):
                yield Label("[b][$primary]Select a Recovery File")
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

            yield VerticalScroll(
                Button(
                    "Start a new bug report (Don't recover)",
                    tooltip="This will not delete any of the existing recovery files",
                    classes="mb1 session_button",
                ),
                *(
                    Button(
                        self._button_text(filename),
                        name=filename,
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
        return "\n".join(lines)
