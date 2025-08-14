import os
import subprocess as sp
import tarfile
from pathlib import Path
from tempfile import mkdtemp
from typing import Generic, Literal, TypeVar, final

from rich.pretty import Pretty
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Center, HorizontalGroup, VerticalGroup
from textual.reactive import var
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ProgressBar, RichLog
from textual.worker import Worker, WorkerState
from typing_extensions import override

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.components.confirm_dialog import ConfirmScreen
from bugit_v2.dut_utils.log_collectors import LOG_NAME_TO_COLLECTOR, LogName
from bugit_v2.models.bug_report import BugReport

ReturnScreenChoice = Literal["job", "session", "quit", "report_editor"]
RETURN_SCREEN_CHOICES: tuple[ReturnScreenChoice, ...] = (
    ReturnScreenChoice.__args__
)

TAuth = TypeVar("TAuth")
TReturn = TypeVar("TReturn")


@final
class SubmissionProgressScreen(
    Generic[TAuth, TReturn], Screen[ReturnScreenChoice]
):
    """
    The progress screen shown while submission/log collection is happening
    """

    bug_report: BugReport
    finished = var(False)
    last_submitter_error = var[Exception | None](None)

    subprocess_log_workers: dict[str, Worker[sp.CompletedProcess[str]]]
    general_log_workers: dict[str, Worker[None]]
    log_dir: Path
    log_widget: RichLog | None = None  # late init in on_mount

    submitter: BugReportSubmitter[TAuth, TReturn]

    CSS = """
    SubmissionProgressScreen {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    #menu_after_finish {
        display: none;
    }

    RichLog {
        padding: 0 1;
    }
    """

    CSS_PATH = "styles.tcss"

    def __init__(
        self,
        bug_report: BugReport,
        submitter: BugReportSubmitter[TAuth, TReturn],
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.bug_report = bug_report
        self.submitter = submitter
        self.log_dir = Path(mkdtemp())
        print(self.log_dir)
        self.subprocess_log_workers = {}
        self.general_log_workers = {}
        super().__init__(name, id, classes)

    @work
    async def on_mount(self) -> None:
        self.log_widget = self.query_exactly_one("#submission_logs", RichLog)
        self.query_exactly_one("#menu_after_finish").display = False
        if self.submitter.auth_modal:
            # this is a bit weird, right now it's basically letting the
            # submission screen control how the credentials are assigned
            cached_credentials = self.submitter.get_cached_credentials()
            if cached_credentials is None:
                self.submitter.auth, self.submitter.allow_cache_credentials = (
                    await self.app.push_screen_wait(
                        self.submitter.auth_modal()
                    )
                )
            else:
                self.submitter.auth, self.submitter.allow_cache_credentials = (
                    cached_credentials,
                    True,  # if it was saved before,
                    # then allow_cache_credentials is definitely true
                )

        # auth ready, do the jira/lp steps
        self.call_after_refresh(self.main_submission_sequence)

    @work(thread=True)
    def main_submission_sequence(self) -> None:
        assert self.log_widget
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        # get the log collectors running first
        for log_name in self.bug_report.logs_to_include:

            def run_collect(log: LogName):
                collector = LOG_NAME_TO_COLLECTOR[log]
                rv = collector.collect(Path(self.log_dir.name))
                progress_bar.advance()
                if not self.log_widget:
                    return rv

                if rv.returncode == 0:
                    self.log_widget.write(
                        f"[green]OK![/green] {collector.name} finished!"
                    )
                else:
                    self.log_widget.write(
                        f"[red]FAILED![/red] Collector {collector.name} failed"
                    )
                    self.log_widget.write(Pretty(rv))

                return rv

            self.subprocess_log_workers[log_name] = self.run_worker(
                # closure workaround
                # https://stackoverflow.com/a/1107260
                # bind the value early
                lambda n=log_name: run_collect(n),
                thread=True,  # not async
                name=log_name,
                exit_on_error=False,  # hold onto the err, don't crash
            )

            self.log_widget.write(
                f"[green]OK![/green] Launched {log_name} log collector in the background!"
            )  # late write

        # also make the tars
        self.general_log_workers["checkbox_session_tar"] = self.run_worker(
            lambda: self.pack_checkbox_session(),
            thread=True,
            exit_on_error=False,
        )

        # then do the jira/lp stuff
        display_name = self.submitter.display_name or self.submitter.name
        for step_result in self.submitter.submit(self.bug_report):
            match step_result:
                case str():
                    # general logs
                    self.log_widget.write(
                        f"[b]{display_name}[/b]: {step_result}"
                    )
                case AdvanceMessage():
                    # messages that will advance the progress bar
                    self.log_widget.write(
                        f"[green]OK![/green] [b]{display_name}[/b]: {step_result.message}"
                    )
                    progress_bar.advance()
                case Exception():
                    # errors
                    self.last_submitter_error = step_result
                    return  # exit early, don't mark self.finished = True

        # update state, there's another updater in on_worker_state_changed
        self.finished = self.is_finished()

    def is_finished(self) -> bool:
        """
        Determines the "finished" criteria. The self.finished reactive should
        always be assigned the value determined by this function.

        - Did all the steps from the submitter finish successfully?
        - last_submitter_error is None
        - Did all log collectors *finish*?
        - errors are ok, just report them in the log window since the user can
            likely just run the collector again
        - all(w.is_finished for w in self.log_workers.values())
        - Was the final tar ball created with the log files and checkbox session?
        """
        return (
            self.last_submitter_error is None
            and all(
                worker.is_finished
                for worker in self.subprocess_log_workers.values()
            )
            and all(
                worker.is_finished
                for worker in self.general_log_workers.values()
            )
        )

    @work
    async def watch_finished(self):
        if not self.finished:
            return

        if not os.getenv("DEBUG"):
            os.rmdir(self.log_dir)
        self.query_exactly_one("#menu_after_finish").display = True

    @work
    async def watch_last_submitter_error(self):
        if self.last_submitter_error is None:
            return

        # stop all log workers asap
        for worker in self.subprocess_log_workers.values():
            worker.cancel()
        if not os.getenv("DEBUG"):
            os.rmdir(self.log_dir)

        await self.app.push_screen_wait(
            ConfirmScreen[ReturnScreenChoice](
                f"Got the following error during submission: {str(self.last_submitter_error)}",
                choices=(("Return to Report Editor", "report_editor"),),
                focus_id_on_mount="report_editor",
            ),
        )
        self.dismiss("report_editor")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if (
            not self.log_widget
            or event.worker.name not in self.subprocess_log_workers
        ):
            return

        if event.worker.state == WorkerState.CANCELLED:
            self.log_widget.write(f"{event.worker.name} was cancelled")

        self.finished = self.is_finished()

    @on(Button.Pressed, "#job")
    @on(Button.Pressed, "#session")
    @on(Button.Pressed, "#quit")
    def handle_button_in_menu_after_finish(self, event: Button.Pressed):
        if event.button.id in RETURN_SCREEN_CHOICES:
            self.dismiss(event.button.id)

    def pack_checkbox_session(self):
        assert self.log_widget
        try:
            with tarfile.open(
                self.log_dir / "checkbox_session.tar.gz", "w:gz"
            ) as f:
                f.add(self.bug_report.checkbox_session.session_path)
                self.log_widget.write(
                    f"[green]OK![/green] Packed checkbox submission to {self.log_dir}"
                )
        except Exception as e:
            self.log_widget.write(
                f"[red]Failed![/red] Couldn't pack checkbox session. Error: {e}"
            )

    @override
    def compose(self) -> ComposeResult:
        yield Header(classes="dt")

        with Center(classes="lrm1"):
            with HorizontalGroup():
                yield Label("Submission Progress", classes="mr1")
                yield ProgressBar(
                    total=self.submitter.steps
                    + len(self.bug_report.logs_to_include),
                    id="progress",
                    show_eta=False,
                )
            yield RichLog(id="submission_logs", markup=True)

        with VerticalGroup(classes="db"):
            with VerticalGroup(
                classes="w100 ha center tbm1", id="menu_after_finish"
            ):
                yield Center(
                    Label(
                        "Submission finished! You can go back to job/session selection or quit BugIt.",
                        classes="wa",
                    )
                )
                with Center():
                    with HorizontalGroup(classes="wa center"):
                        yield Button(
                            "Select another job", classes="mr1", id="job"
                        )
                        yield Button(
                            "Select another session",
                            classes="mr1",
                            id="session",
                        )
                        yield Button("Quit", id="quit")

            yield Footer()
