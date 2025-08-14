import os
from pathlib import Path
from typing import final

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label
from typing_extensions import override


@final
class SessionSelectionScreen(Screen[Path]):
    session_dirs = reactive[list[Path]]([], recompose=True)
    session_root = Path("/var/tmp/checkbox-ng/sessions")

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
        yield Header()
        with Vertical(classes="w100 h100 center", id="after_load_container"):
            if len(self.session_dirs) > 0:
                yield Label("[b][$primary]Select a Session")
                yield VerticalScroll(
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
            else:
                yield Center(
                    Label(
                        "There are no sessions with failed jobs",
                    )
                )
        yield Footer()

    def on_mount(self) -> None:
        if not self.session_root.exists():
            self.notify(
                f"{self.session_root} doesn't exist!",
                severity="error",
                timeout=float("inf"),
            )
        self.session_dirs = self._get_valid_sessions()

    def action_refresh_sessions(self):
        self.session_dirs = self._get_valid_sessions()
        self.notify(
            "Click this message to dismiss",
            title=f"Finished reading {self.session_root}!",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        session_path = event.button.name
        try:
            assert session_path and Path(session_path).exists()
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
        if not self.session_root.exists():
            return []
        valid_session_dirs: list[Path] = []
        for d in os.listdir(self.session_root):
            try:
                if len(os.listdir(self.session_root / d / "io-logs")) != 0:
                    valid_session_dirs.append(self.session_root / d)
            except FileNotFoundError:
                continue
        return valid_session_dirs
