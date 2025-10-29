import os
from pathlib import Path
from typing import Literal, final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Label
from typing_extensions import override

from bugit_v2.checkbox_utils import SESSION_ROOT_DIR, get_valid_sessions
from bugit_v2.components.header import SimpleHeader
from bugit_v2.utils.constants import NullSelection


@final
class SessionSelectionScreen(Screen[Path | Literal[NullSelection.NO_SESSION]]):
    session_dirs = reactive[list[Path]]([], recompose=True)

    BINDINGS = [
        Binding(
            "r",
            "refresh_sessions",
            "Refresh Sessions",
            tooltip="Read the checkbox-ng directory again to load new sessions",
        )
    ]
    CSS_PATH = "styles.tcss"

    @override
    def compose(self) -> ComposeResult:
        # textual's header crashes for some reason
        yield SimpleHeader()
        with Vertical(classes="w100 h100 center"):
            yield Label("[b][$primary]Select a Session")
            yield VerticalScroll(
                Button(
                    "No Session (Skip to Editor)",
                    name="bugit_no_session",
                    # tooltip != None is used to check if this special
                    # button is clicked, do not remove
                    tooltip="Choose this to skip to report editor",
                    classes="session_button",
                    flat=True,
                ),
                *(
                    Button(
                        os.path.basename(session),
                        name=str(session),
                        classes="session_button",
                        flat=True,
                    )
                    for session in self.session_dirs
                ),
                classes="w100 h100 center",
            )
        yield Footer()

    def on_mount(self) -> None:
        if not SESSION_ROOT_DIR.exists():
            self.notify(
                f"{SESSION_ROOT_DIR} doesn't exist!",
                severity="error",
                timeout=float("inf"),
            )
        self.session_dirs = get_valid_sessions()

    def action_refresh_sessions(self):
        self.session_dirs = get_valid_sessions()
        self.notify(
            "Click this message to dismiss",
            title=f"Finished reading {SESSION_ROOT_DIR}!",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        session_path = event.button.name
        try:
            assert session_path
            if (
                session_path == "bugit_no_session"
                and event.button.tooltip is not None
            ):
                self.dismiss(NullSelection.NO_SESSION)
            else:
                assert Path(session_path).exists()
                self.dismiss(Path(session_path).absolute())
        except AssertionError:
            self.app.notify(
                "Was it deleted while BugIt is running?",
                title=f"{session_path} doesn't exist.",
                severity="error",
            )
