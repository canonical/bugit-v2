from collections.abc import Sequence
from typing import Final, Literal, final

from textual import on
from textual.app import ComposeResult
from textual.containers import VerticalGroup
from textual.screen import Screen
from textual.widgets import Button, Footer, Label, RadioButton, RadioSet
from typing_extensions import override

from bugit_v2.components.header import SimpleHeader
from bugit_v2.utils.constants import NullSelection


@final  # should return a full job id upon dismissal
class JobSelectionScreen(Screen[str | Literal[NullSelection.NO_JOB]]):
    CSS_PATH = "styles.tcss"

    job_id_options: Final[Sequence[str]]
    selected_job: str | None

    CSS = """
    JobSelectionScreen {
        align: center middle;
    }

    #job_list_container {
        border: none;
        height: 100%;
    }
    """

    def __init__(
        self,
        job_id_options: Sequence[str],
        job_id_source_name: str,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        """Job ID selection screen

        :param job_id_options: all possible job ids to choose from
        :param job_id_source_name:
            Arbitrary string, name of where the IDs came from
            Only used in the screen's title
        """
        self.job_id_options = job_id_options
        self.selected_job = None
        self.job_id_source_name = job_id_source_name
        super().__init__(name, id, classes)

    @override
    def compose(self) -> ComposeResult:
        with VerticalGroup(classes="dt"):
            yield SimpleHeader()
            yield Label(
                (
                    f"[bold][$primary]Select a job in [$secondary] {self.job_id_source_name}"
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
            *(RadioButton(job_id, name=job_id) for job_id in self.job_id_options),
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
            btn.label = f"File a bug for [u]{self.selected_job.split('::')[-1]}"
        btn.disabled = False
        btn.variant = "success"
