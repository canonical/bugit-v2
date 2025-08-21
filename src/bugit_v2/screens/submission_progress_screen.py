import shutil
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
from bugit_v2.dut_utils.log_collectors import LOG_NAME_TO_COLLECTOR
from bugit_v2.models.bug_report import BugReport, LogName
from bugit_v2.utils import is_prod

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
    submitter_sequence_status = var[
        Exception | Literal["in_progress", "done"]
    ]("in_progress")

    attachment_workers: dict[str, Worker[str | None]]
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
        self.log_dir = Path(mkdtemp()).expanduser().absolute()
        self.attachment_workers = {}

        super().__init__(name, id, classes)

    @work
    async def on_mount(self) -> None:
        self.log_widget = self.query_exactly_one("#submission_logs", RichLog)
        self.query_exactly_one("#menu_after_finish").display = False
        if self.submitter.auth_modal:
            # submission screen controls how the credentials are assigned
            try:
                cached_credentials = self.submitter.get_cached_credentials()
                if cached_credentials is None:
                    auth_rv = await self.app.push_screen_wait(
                        self.submitter.auth_modal()
                    )
                    assert auth_rv
                    (
                        self.submitter.auth,
                        self.submitter.allow_cache_credentials,
                    ) = auth_rv
                else:
                    (
                        self.submitter.auth,
                        self.submitter.allow_cache_credentials,
                    ) = (
                        cached_credentials,
                        True,  # if it was saved before,
                        # then allow_cache_credentials is definitely true
                    )
            except AssertionError:
                self.dismiss("report_editor")

        # auth ready, do the jira/lp steps
        self.call_after_refresh(self.main_submission_sequence)

    @work(thread=True)
    def main_submission_sequence(self) -> None:
        assert self.log_widget
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        # get the log collectors running first
        # all log collectors are allowed to fail. If they do, write a message
        # to the screen to tell the user how to get the logs manually
        for log_name in self.bug_report.logs_to_include:

            def run_collect(log: LogName) -> str | None:
                collector = LOG_NAME_TO_COLLECTOR[log]
                try:
                    rv = collector.collect(self.log_dir, self.bug_report)
                    if not self.log_widget:
                        return rv

                    if rv and rv.strip():
                        # only show non-empty, non-null messages
                        self.log_widget.write(
                            f"[green]OK![/green] [b]{collector.display_name}[/b]: {rv.strip()}"
                        )
                    else:
                        self.log_widget.write(
                            f"[green]OK![/green] {collector.display_name} finished!"
                        )
                except Exception as e:
                    if not self.log_widget:
                        return
                    self.log_widget.write(
                        f"[red]FAILED![/red] {collector.display_name} failed!"
                    )
                    self.log_widget.write(Pretty(e))
                    if collector.manual_collection_command:
                        self.log_widget.write(
                            f"You can rerun [blue]{collector.display_name}[/] "
                            + f"with {collector.manual_collection_command}"
                        )
                finally:
                    progress_bar.advance()

            self.attachment_workers[log_name] = self.run_worker(
                # closure workaround
                # https://stackoverflow.com/a/1107260
                # bind the value early
                lambda n=log_name: run_collect(n),
                thread=True,  # not async
                name=log_name,
                exit_on_error=False,  # hold onto the err, don't crash
            )

            display_name = LOG_NAME_TO_COLLECTOR[log_name].display_name
            self.log_widget.write(
                f"Launched collector: {display_name}!"
            )  # late write

        # then do the jira/lp stuff
        display_name = self.submitter.display_name or self.submitter.name
        try:
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
                            f"[green]OK![/green] [b]{display_name}[/b]: "
                            + step_result.message
                        )
                        progress_bar.advance()
        except Exception as e:
            self.submitter_sequence_status = e
            return  # exit early, don't mark self.finished = True

        self.submitter_sequence_status = "done"
        # update state, there's another updater in on_worker_state_changed
        self.finished = self.is_finished()

    def is_finished(self) -> bool:
        """
        Determines self.finished. It should always be assigned the value
        returned by this function.

        - Did all the steps from the submitter finish successfully?
            - Errors from the submitter should be caught
            - self.finished is False if submitter failed
        - Did all log collectors *finish*?
            - errors are ok, just report them in the log window since the user
              can likely just run the collector again
        """
        return self.submitter_sequence_status == "done" and all(
            worker.is_finished for worker in self.attachment_workers.values()
        )

    @work
    async def watch_finished(self):
        if not self.finished:
            return

        if is_prod():
            shutil.rmtree(self.log_dir)

        self.query_exactly_one("#finish_message", Label).update(
            "\n".join(
                [
                    "Submission finished!",
                    f"URL: {self.submitter.bug_url}",
                    "You can go back to job/session selection or quit BugIt.",
                ]
            )
        )
        self.query_exactly_one("#menu_after_finish").display = True

    @work
    async def watch_submitter_sequence_status(self):
        if self.submitter_sequence_status in ("in_progress", "done"):
            return

        # stop all log workers asap
        for worker in self.attachment_workers.values():
            worker.cancel()

        if is_prod():
            shutil.rmtree(self.log_dir)

        await self.app.push_screen_wait(
            ConfirmScreen[ReturnScreenChoice](
                "Got the following error during submission",
                sub_prompt=f"[red]{self.submitter_sequence_status}",
                choices=(("Return to Report Editor", "report_editor"),),
                focus_id_on_mount="report_editor",
            ),
        )
        self.dismiss("report_editor")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if (
            not self.log_widget
            or event.worker.name not in self.attachment_workers
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
                yield Center(Label(classes="wa", id="finish_message"))
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
