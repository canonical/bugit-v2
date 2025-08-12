import subprocess as sp
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Generic, Literal, TypeVar, final

from rich.pretty import Pretty
from textual import on, work
from textual.app import ComposeResult
from textual.containers import (
    Center,
    HorizontalGroup,
    VerticalGroup,
)
from textual.reactive import var
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ProgressBar,
    RichLog,
)
from textual.worker import Worker, WorkerState
from typing_extensions import override

from bugit_v2.bug_report_submitters.bug_report_submitter import (
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
class SubmissionProgressScreen(Generic[TAuth], Screen[ReturnScreenChoice]):
    bug_report: BugReport
    finished = var(False)
    last_submission_err = var[Exception | None](None)

    log_workers: dict[LogName, Worker[sp.CompletedProcess[str]]]
    log_dir: TemporaryDirectory[str]
    log_widget: RichLog | None = None  # late init in on_mount

    CSS = """
    SubmissionProgressScreen {
        width: 100%;
        height: 100%;
        align: center middle;
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
        self.log_dir = TemporaryDirectory()
        self.log_workers = {}
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
                    # then allow_cache_credentials is def true
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
                        f"[green][ OK! ][/green] {collector.name} finished! mainseq"
                    )
                    print(collector.name, "finished")
                else:
                    self.log_widget.write(
                        f"[red][ FAILED ][/red] Collector {collector.name} failed"
                    )
                    self.log_widget.write(Pretty(rv))
                    print(collector.name, "failed")

                return rv

            n = "".join(list(log_name))
            self.log_workers[n] = self.run_worker(
                lambda: run_collect(
                    n  # pyright: ignore[reportArgumentType] # pyright being stupid
                ),
                thread=True,  # not async
                name=n,
                exit_on_error=False,  # hold onto the err, don't crash
            )

            self.log_widget.write(
                f"[green][ OK! ][/green] Launched {log_name} log collector in the background!"
            )  # late write
        print(self.log_workers, len(self.log_workers))
        # then do the jira/lp stuff
        for step_result in self.submitter.submit(self.bug_report):
            match step_result:
                case str():
                    self.log_widget.write(step_result)
                    progress_bar.advance()
                case Exception():
                    self.last_submission_err = step_result
                    return  # exit early

        # update state, there's another updater in on_worker_state_changed
        if len(self.log_workers) == 0 and self.last_submission_err is None:
            self.finished = True

    @work
    async def watch_finished(self):
        if not self.finished:
            return

        self.log_dir.cleanup()
        self.query_exactly_one("#menu_after_finish").display = True

    @work
    async def watch_last_submission_err(self):
        if self.last_submission_err is None:
            return

        # stop all log workers asap
        for worker in self.log_workers.values():
            worker.cancel()
        self.log_dir.cleanup()

        await self.app.push_screen_wait(
            ConfirmScreen[ReturnScreenChoice](
                f"Got the following error during submission: {str(self.last_submission_err)}",
                choices=(("Return to Report Editor", "report_editor"),),
                focus_id_on_mount="report_editor",
            ),
        )
        self.dismiss("report_editor")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if not self.log_widget or event.worker.name not in self.log_workers:
            return

        if event.worker.state == WorkerState.CANCELLED:
            self.log_widget.write(f"{event.worker.name} was cancelled")

        if self.last_submission_err is None and all(
            log_worker.is_finished for log_worker in self.log_workers.values()
        ):
            self.finished = True

    @on(Button.Pressed, "#job")
    @on(Button.Pressed, "#session")
    @on(Button.Pressed, "#quit")
    def handle_button_in_menu_after_finish(self, event: Button.Pressed):
        if event.button.id in RETURN_SCREEN_CHOICES:
            self.dismiss(event.button.id)

    @override
    def compose(self) -> ComposeResult:
        yield Header(classes="dt")

        with Center(classes="lrm1"):
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
                        "Submission finished! You can go back to job/session selection or quit BugIt",
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
