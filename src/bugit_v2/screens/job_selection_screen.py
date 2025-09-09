import os
from typing import Final, final

from textual import on
from textual.app import ComposeResult
from textual.containers import VerticalGroup
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    RadioButton,
    RadioSet,
)
from typing_extensions import override

from bugit_v2.checkbox_utils import Session


@final
class JobSelectionScreen(Screen[str]):
    CSS_PATH = "styles.tcss"

    session: Final[Session]
    selected_job: str | None

    CSS = """
    JobSelectionScreen {
        align: center middle;
    }

    #job_list_container {
        align: center middle;
        overflow: scroll;
        height: 100%;
    }
    """

    def __init__(
        self,
        session: Session,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.session = session
        self.selected_job = None
        super().__init__(name, id, classes)

    @override
    def compose(self) -> ComposeResult:
        with VerticalGroup(classes="dt"):
            yield Header(icon="〇")
            yield Label(
                (
                    "[bold][$primary]Select a job in [$secondary]"
                    f"{os.path.basename(self.session.session_path)}"
                )
            )
        jobs = self.session.get_run_jobs()
        if len(jobs) == 0:
            with VerticalGroup(classes="w100 h100 center"):
                yield Label(
                    (
                        "There are no failed jobs. "
                        "Use Alt+LeftArrow to go back to session selection"
                    ),
                    classes="wa ha",
                )
        else:
            yield RadioSet(
                *(RadioButton(job) for job in jobs),
                classes="nb",
                id="job_list_container",
            )
        with VerticalGroup(classes="db"):
            yield Button(
                "Select a job to continue",
                id="continue_button",
                disabled=True,
                classes="w100 ha",
            )
            yield Footer()

    def on_mount(self) -> None:
        try:
            self.query_one(RadioSet).focus()
        except Exception:
            # when there are no jobs, don't focus
            pass

    @on(Button.Pressed, "#continue_button")
    def finish_selection(self) -> None:
        assert self.session is not None and self.selected_job is not None
        self.dismiss(self.selected_job)

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self.selected_job = str(event.pressed.label)
        btn = self.query_exactly_one("#continue_button", Button)
        btn.label = f"File a bug for [u]{self.selected_job.split('::')[-1]}"
        btn.disabled = False
        btn.variant = "success"
