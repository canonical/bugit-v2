import datetime
import os
from pathlib import Path
from typing import Literal, final, override

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

from bugit_v2.checkbox_utils.models import SimpleCheckboxSubmission
from bugit_v2.components.header import SimpleHeader
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.bug_report import BugReportAutoSaveData
from bugit_v2.utils import pretty_date
from bugit_v2.utils.constants import AUTOSAVE_DIR

type SaveType = Literal["session", "submission"]


@final
class RecoverFromAutoSaveScreen(Screen[BugReportAutoSaveData | None]):

    CSS_PATH = "styles.tcss"

    is_relative = reactive[bool](True, recompose=True)
    lock_delete = reactive[bool](True, recompose=True)
    valid_autosave_data: dict[str, BugReportAutoSaveData]
    save_type: SaveType

    def __init__(
        self,
        save_type: SaveType,
        app_args: AppArgs,
        autosave_dir: Path = AUTOSAVE_DIR,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.autosave_dir = autosave_dir
        self.valid_autosave_data = {}
        self.save_type = save_type
        # file names are already timestamps, can just use string sort
        for file in sorted(os.listdir(autosave_dir), reverse=True):
            with open(autosave_dir / file) as f:
                try:
                    autosave = BugReportAutoSaveData.model_validate_json(
                        f.read()
                    )
                    match (
                        save_type,
                        autosave.checkbox_submission,
                        app_args.checkbox_submission,
                    ):
                        case ("session", None, _):
                            self.valid_autosave_data[file] = autosave
                        case (
                            "submission",
                            Path() as p,
                            SimpleCheckboxSubmission() as cbs,
                        ):
                            if cbs.submission_path == p:
                                self.valid_autosave_data[file] = autosave
                        case _:
                            pass
                except pydantic.ValidationError as e:
                    self.log.error(e)

        super().__init__(name, id, classes)

    def on_mount(self):
        if len(self.valid_autosave_data) == 0:
            self.dismiss(None)

    @override
    def compose(self) -> ComposeResult:
        yield SimpleHeader()
        with Vertical(classes="w100 h100 center"):
            with VerticalGroup(classes="round_box lrp2"):
                yield Label("[b][$primary]Resume from a Recovery File")
                yield Label(
                    "These were automatically saved by the bug report editor"
                )
                if self.save_type == "submission":
                    yield Label(
                        "[$warning]Only the recovery files originated from this checkbox submission are shown"
                    )
                yield Rule(classes="m0 boost", line_style="ascii")
                with HorizontalGroup():
                    yield Checkbox(
                        id="mode_toggle",
                        classes="mr1 nb",
                        value=self.is_relative,
                        compact=True,
                    )
                    yield Label("Relative Timestamp")
                with HorizontalGroup():
                    yield Checkbox(
                        id="show_delete_toggle",
                        classes="mr1 nb",
                        value=self.lock_delete,
                        compact=True,
                    )
                    yield Label("Lock Delete Button")
                    with Right():
                        yield Button(
                            "Start a New Bug Report (Don't recover)",
                            id="no_recovery",
                            tooltip="This will not delete any of the existing recovery files",
                            compact=True,
                            classes="editor_button",
                        )

            with VerticalScroll(classes="w100 center"):
                for filename in self.valid_autosave_data.keys():
                    with HorizontalGroup(classes="center row"):
                        yield Button(
                            self._button_text(filename),
                            name=filename,  # can't have slashes in id
                            flat=True,
                            classes="session_button mr1 ha",
                        )

                        yield Button(
                            "⌫",
                            name=f"delete:{filename}",
                            variant="error",
                            flat=True,
                            tooltip=(
                                "Delete this backup"
                                + (" (locked)" if self.lock_delete else "")
                            ),
                            classes="h100 center",
                            disabled=self.lock_delete,
                        )

        yield Footer()

    @on(Checkbox.Changed, "#mode_toggle")
    def change_mode(self, event: Checkbox.Changed):
        self.is_relative = event.checkbox.value

    @on(Checkbox.Changed, "#show_delete_toggle")
    def toggle_delete(self, event: Checkbox.Changed):
        self.lock_delete = event.checkbox.value

    @on(Button.Pressed)
    async def handle_buttons(self, event: Button.Pressed):
        if event.button.id == "no_recovery":
            self.dismiss(None)
            return

        assert event.button.name

        if event.button.name.startswith("delete:"):
            savefile_name = event.button.name.removeprefix("delete:")
            (self.autosave_dir / savefile_name).unlink()
            del self.valid_autosave_data[savefile_name]
            if len(self.valid_autosave_data) == 0:
                self.dismiss(None)
            else:
                await self.recompose()
        else:
            self.dismiss(self.valid_autosave_data[event.button.name])

    def _button_text(self, filename: str) -> Content:
        assert filename in self.valid_autosave_data
        autosave = self.valid_autosave_data[filename]
        lines: list[str] = []
        if self.is_relative:
            lines.append(
                "Saved "
                + pretty_date(
                    datetime.datetime.fromtimestamp(
                        os.stat(self.autosave_dir / filename).st_ctime
                    )
                ),
            )
        else:
            lines.append(
                "Saved at "
                + datetime.datetime.fromtimestamp(
                    os.stat(self.autosave_dir / filename).st_ctime
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

        if session_path := autosave.checkbox_session:
            lines.append(f"[grey]{os.path.basename(session_path)}")
        elif checkbox_submission_path := autosave.checkbox_submission:
            lines.append(f"[grey]{os.path.basename(checkbox_submission_path)}")
        else:
            lines.append("[i][grey]No session selected")

        if job_id := autosave.job_id:
            lines.append(f"[grey]{job_id}")
        else:
            lines.append("[i][grey]No job selected")

        return Content("\n").join(Content.from_markup(line) for line in lines)
