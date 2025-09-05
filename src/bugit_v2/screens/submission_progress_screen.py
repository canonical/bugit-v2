import os
import shutil
import time
from pathlib import Path
from tempfile import mkdtemp
from typing import Final, Generic, Literal, TypeVar, final

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

    attachment_workers: dict[str, Worker[str | None]]
    upload_workers: dict[str, Worker[str | None]]
    bug_creation_worker: Worker[None] | None = None

    attachment_dir: Path
    log_widget: RichLog | None = None  # late init in on_mount

    submitter: Final[BugReportSubmitter[TAuth, TReturn]]
    BUG_CREATION_WORKER_NAME = "bug_creation"

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
        self.attachment_dir = Path(mkdtemp()).expanduser().absolute()
        self.attachment_workers = {}
        self.upload_workers = {}
        self.progress_start_time = time.time()  # doesn't have to precise

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
        self.start_parallel_log_collection()
        self.bug_creation_worker = self.run_worker(
            self.create_bug,
            thread=True,
            name=self.BUG_CREATION_WORKER_NAME,
            exit_on_error=False,
        )

    def start_parallel_log_collection(self) -> None:
        """Launches all log collectors and keep the worker objects

        This does NOT wait for them to finish, just launches them
        """
        assert self.log_widget
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        # get the log collectors running first
        # all log collectors are allowed to fail. If they do, write a message
        # to the screen to tell the user how to get the logs manually
        for log_name in self.bug_report.logs_to_include:

            def run_collect(log: LogName) -> str | None:
                collector = LOG_NAME_TO_COLLECTOR[log]
                try:
                    rv = collector.collect(
                        self.attachment_dir, self.bug_report
                    )
                    if not self.log_widget:
                        return rv

                    if rv and rv.strip():
                        # only show non-empty, non-null messages
                        self.log_widget.write(
                            " ".join(
                                [
                                    self._time_str(),
                                    "[green]OK[/]",
                                    f"[b]{collector.display_name}[/b]:",
                                    rv.strip(),
                                ]
                            )
                        )
                    else:
                        self.log_widget.write(
                            " ".join(
                                [
                                    self._time_str(),
                                    "[green]OK[/]",
                                    f"[b]{collector.display_name}[/b]:",
                                    "Finished collection!",
                                ]
                            )
                        )
                except Exception as e:
                    if not self.log_widget:
                        return
                    self.log_widget.write(
                        " ".join(
                            [
                                self._time_str(),
                                f"[red]FAIL[/red] {collector.display_name} failed:",
                                repr(e),
                            ]
                        )
                    )
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
                f"{self._time_str()} Launched collector: {display_name}!"
            )

    def start_parallel_attachment_upload(self) -> None:
        assert self.log_widget
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        for file_name in self.attachment_dir.iterdir():

            def upload_one(f: Path) -> str | None:
                try:
                    rv = self.submitter.upload_attachment(f)
                    if not self.log_widget:
                        return rv

                    if rv and rv.strip():
                        # only show non-empty, non-null messages
                        self.log_widget.write(
                            f"{self._time_str()} [green]OK[/] [b]Uploaded {f}[/]: {rv.strip()}"
                        )
                    else:
                        self.log_widget.write(
                            f"{self._time_str()} [green]OK[/] [b]Uploaded {f}[/b]"
                        )
                except Exception as e:
                    if not self.log_widget:
                        return
                    self.log_widget.write(
                        f"{self._time_str()} [red]FAIL[/red] failed to upload {f}: {repr(e)}"
                    )
                    raise e  # mark the worker as failed
                finally:
                    progress_bar.advance()

            self.attachment_workers[str(file_name)] = self.run_worker(
                # closure workaround
                # https://stackoverflow.com/a/1107260
                # bind the value early
                lambda f=file_name: upload_one(f),
                thread=True,  # not async
                exit_on_error=False,  # hold onto the err, don't crash
            )

            self.log_widget.write(f"{self._time_str()} Uploading: {file_name}")

    def start_sequential_attachment_upload(self) -> None:
        assert self.log_widget
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        def upload_all() -> str | None:
            for f in self.attachment_dir.iterdir():
                try:
                    if self.log_widget:
                        self.log_widget.write(
                            f"{self._time_str()} Uploading: {f}"
                        )

                    rv = self.submitter.upload_attachment(f)

                    if not self.log_widget:
                        return rv

                    if rv and rv.strip():
                        # only show non-empty, non-null messages
                        self.log_widget.write(
                            f"{self._time_str()} [green]OK[/] [b]Uploaded {f}[/]: {rv.strip()}"
                        )
                    else:
                        self.log_widget.write(
                            f"{self._time_str()} [green]OK[/] [b]Uploaded {f}[/b]"
                        )
                except Exception as e:
                    if not self.log_widget:
                        return
                    self.log_widget.write(
                        f"{self._time_str()} [red]FAIL[/red] failed to upload {f}: {repr(e)}"
                    )
                    raise e  # mark the worker as failed
                finally:
                    progress_bar.advance()

        self.attachment_workers["sequential_all"] = self.run_worker(
            # closure workaround
            # https://stackoverflow.com/a/1107260
            # bind the value early
            upload_all,
            thread=True,  # not async
            exit_on_error=False,  # hold onto the err, don't crash
        )

    def create_bug(self) -> None:
        """Do the entire bug creation sequence. This should be run in a worker"""
        assert self.log_widget
        progress_bar = self.query_exactly_one("#progress", ProgressBar)
        display_name = self.submitter.display_name or self.submitter.name

        for step_result in self.submitter.submit(self.bug_report):
            match step_result:
                case str():
                    # general logs
                    self.log_widget.write(
                        f"{self._time_str()} [b]{display_name}[/b]: {step_result}"
                    )
                case AdvanceMessage():
                    # messages that will advance the progress bar
                    self.log_widget.write(
                        f"{self._time_str()} [green]OK[/] [b]{display_name}[/b]: "
                        + step_result.message
                    )
                    progress_bar.advance()

        running_collectors = [
            w for w in self.attachment_workers.values() if w.is_running
        ]
        if len(running_collectors) > 0:
            self.log_widget.write(
                f"{self._time_str()} Finished bug creation. Waiting for {len(running_collectors)} log collector(s) to finish"
            )
            for c in running_collectors:
                if c.name in LOG_NAME_TO_COLLECTOR:
                    display_name = LOG_NAME_TO_COLLECTOR[
                        c.name  # pyright can't infer this yet
                    ].display_name  # pyright: ignore[reportArgumentType]
                    self.log_widget.write(
                        f"- {display_name}",
                    )
        else:
            self.log_widget.write(
                f"{self._time_str()} Finished bug creation, starting to upload attachments..."
            )

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
        if self.bug_creation_worker is None:
            self.log.info("No bug creation worker")
            return False
        if self.bug_creation_worker.state != WorkerState.SUCCESS:
            self.log.info("Bug creation worker not done")
            return False
        if not all(w.is_finished for w in self.attachment_workers.values()):
            self.log.info("Some attachment collectors are still running")
            return False
        if not all(w.is_finished for w in self.upload_workers.values()):
            self.log.info("Some attachment upload-ers are still running")
            return False

        return True

    def ready_to_upload_attachments(self) -> bool:
        if self.bug_creation_worker is None:
            self.log.error("No bug creation worker, logic error")
            return False
        if self.bug_creation_worker.state != WorkerState.SUCCESS:
            self.log.warning(
                f"Bug creation worker hasn't finished: {self.bug_creation_worker.state}"
            )
            return False

        if any(w.is_running for w in self.upload_workers.values()):
            self.log.warning("An upload worker is already running")
            return False

        if not all(w.is_finished for w in self.attachment_workers.values()):
            self.log.warning("Some attachment workers are not done")
            return False

        return True

    @work
    async def watch_finished(self):
        if not self.finished:
            return

        all_upload_ok = all(
            w.state == WorkerState.SUCCESS
            for w in self.upload_workers.values()
        )
        if is_prod() and all_upload_ok:
            # only cleanup if everything was uploaded
            shutil.rmtree(self.attachment_dir)

        finish_message_lines = [
            "[green]Submission finished![/]",
            f"URL: {self.submitter.bug_url}",
            "You can go back to job/session selection or quit BugIt.",
        ]

        if not all_upload_ok:
            if "SNAP" in os.environ:
                attachment_dir = (
                    "/tmp/snap-private-tmp/snap.bugit-v2/tmp"
                    / self.attachment_dir
                )
            else:
                attachment_dir = self.attachment_dir
            finish_message_lines.insert(
                1,
                "\n".join(
                    [
                        "[red]But some files failed to upload.[/]",
                        f"[red]You can manually reupload the files at: {attachment_dir}[/]",
                    ]
                ),
            )

        self.query_exactly_one("#finish_message", Label).update(
            "\n".join(finish_message_lines)
        )
        self.query_exactly_one("#menu_after_finish").display = True

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if not self.log_widget or self.finished:
            return

        if event.worker.state == WorkerState.CANCELLED:
            self.log_widget.write(f"{event.worker.name} was cancelled")

        match event.worker:
            case Worker(
                name=self.BUG_CREATION_WORKER_NAME, state=WorkerState.ERROR
            ):
                for worker in self.attachment_workers.values():
                    worker.cancel()

                if is_prod():
                    shutil.rmtree(self.attachment_dir)

                def dismiss_wrapper(_: ReturnScreenChoice | None):
                    # force a null return to avoid awaiting inside a msg handler
                    self.dismiss("report_editor")
                    return None

                self.app.push_screen(
                    ConfirmScreen[ReturnScreenChoice](
                        "Got the following error during submission",
                        sub_prompt=f"[red]{event.worker.error}",
                        choices=(
                            ("Return to Report Editor", "report_editor"),
                        ),
                        focus_id_on_mount="report_editor",
                    ),
                    dismiss_wrapper,
                )

            case Worker(state=WorkerState.SUCCESS):
                if (
                    event.worker.name == self.BUG_CREATION_WORKER_NAME
                    or event.worker.name in self.attachment_workers
                ) and self.ready_to_upload_attachments():
                    if self.submitter.allow_parallel_upload:
                        self.start_parallel_attachment_upload()
                    else:
                        self.start_sequential_attachment_upload()

            case _:  # pyright: ignore[reportUnknownVariableType]
                pass

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
                    + len(self.bug_report.logs_to_include)
                    * 2,  # collect + upload
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

    def _time_str(self) -> str:
        # 999 seconds is about 2 hours
        # should be enough digits
        s = f"{round(time.time() - self.progress_start_time, 1)}".rjust(6)
        return f"[grey70][ {s} ][/]"
