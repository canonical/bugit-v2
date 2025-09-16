import os
from typing import Final, Literal, final

from textual import on
from textual.app import ComposeResult
from textual.containers import VerticalGroup
from textual.screen import Screen
from textual.widgets import Button, Footer, Label, RadioButton, RadioSet
from typing_extensions import override

from bugit_v2.checkbox_utils import Session
from bugit_v2.components.header import SimpleHeader
from bugit_v2.utils.constants import NullSelection


@final
class JobSelectionScreen(Screen[str | Literal[NullSelection.NO_JOB]]):
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
            yield SimpleHeader()
            yield Label(
                (
                    "[bold][$primary]Select a job in [$secondary]"
                    f"{os.path.basename(self.session.session_path)}"
                )
            )

        yield RadioSet(
            RadioButton(
                "No Job (skip to editor)",
                name="bugit_no_job",
                # tooltip != None is used to check if this special
                # button is clicked, do not remove
                tooltip="Choose this to skip to editor with the session data",
            ),
            *(
                RadioButton(job_id, name=job_id)
                for job_id in self.session.get_run_jobs()
            ),
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
        assert self.selected_job is not None
        if self.selected_job == "bugit_no_job":
            self.dismiss(NullSelection.NO_JOB)
        else:
            self.dismiss(self.selected_job)

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        self.selected_job = str(event.pressed.name)
        btn = self.query_exactly_one("#continue_button", Button)
        if self.selected_job == "bugit_no_job":
            btn.label = "Skip to editor with session data"
        else:
            btn.label = (
                f"File a bug for [u]{self.selected_job.split('::')[-1]}"
            )
        btn.disabled = False
        btn.variant = "success"
