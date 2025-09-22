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

from bugit_v2.components.header import SimpleHeader
from bugit_v2.utils.constants import NullSelection


@final
class SessionSelectionScreen(Screen[Path | Literal[NullSelection.NO_SESSION]]):
    session_dirs = reactive[list[Path]]([], recompose=True)
    SESSION_ROOT_DIR = Path("/var/tmp/checkbox-ng/sessions")

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
        with Vertical(classes="w100 h100 center", id="after_load_container"):
            yield Label("[b][$primary]Select a Session")
            yield VerticalScroll(
                Button(
                    "No Session (Skip to Editor)",
                    name="bugit_no_session",
                    # tooltip != None is used to check if this special
                    # button is clicked, do not remove
                    tooltip="Choose this to skip to report editor",
                    classes="mb1 session_button",
                ),
                *(
                    Button(
                        os.path.basename(session),
                        name=str(session),
                        classes="mb1 session_button",
                    )
                    for session in self.session_dirs
                ),
                classes="w100 h100 center",
            )
        yield Footer()

    def on_mount(self) -> None:
        if not self.SESSION_ROOT_DIR.exists():
            self.notify(
                f"{self.SESSION_ROOT_DIR} doesn't exist!",
                severity="error",
                timeout=float("inf"),
            )
        self.session_dirs = self._get_valid_sessions()

    def action_refresh_sessions(self):
        self.session_dirs = self._get_valid_sessions()
        self.notify(
            "Click this message to dismiss",
            title=f"Finished reading {self.SESSION_ROOT_DIR}!",
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

    def _get_valid_sessions(self) -> list[Path]:
        """Get a list of valid sessions in /var/tmp/checkbox-ng

        This is achieved by looking at which session directory has non-empty
        io-logs. If it's empty, it's either tossed by checkbox or didn't even
        reach the test case where it dumps the udev database, thus invalid
        """
        if not self.SESSION_ROOT_DIR.exists():
            return []
        valid_session_dirs: list[Path] = []
        for d in os.listdir(self.SESSION_ROOT_DIR):
            try:
                if len(os.listdir(self.SESSION_ROOT_DIR / d / "io-logs")) != 0:
                    valid_session_dirs.append(self.SESSION_ROOT_DIR / d)
            except FileNotFoundError:
                continue
        return valid_session_dirs
