import enum
import shutil
import time
from pathlib import Path
from tempfile import mkdtemp
from typing import Final, Literal, final

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Center, HorizontalGroup, VerticalGroup
from textual.markup import escape as escape_markup
from textual.reactive import var
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Button, Footer, Label, ProgressBar, RichLog
from textual.worker import Worker, WorkerState
from typing_extensions import override

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.components.confirm_dialog import ConfirmScreen
from bugit_v2.components.header import SimpleHeader
from bugit_v2.dut_utils.log_collectors import LOG_NAME_TO_COLLECTOR
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.bug_report import BugReport, LogName, PartialBugReport
from bugit_v2.utils import is_prod, is_snap

ReturnScreenChoice = Literal["job", "session", "quit", "report_editor"]
RETURN_SCREEN_CHOICES: tuple[ReturnScreenChoice, ...] = (
    ReturnScreenChoice.__args__
)


class WorkerName(enum.StrEnum):
    BUG_CREATION = enum.auto()
    SEQUENTIAL_UPLOAD = enum.auto()


@final
class SubmissionProgressScreen[TAuth, TReturn](Screen[ReturnScreenChoice]):
    """
    The progress screen shown while submission/log collection is happening
    """

    bug_report: BugReport
    app_args: AppArgs

    finished = var(False)

    attachment_workers: dict[LogName, Worker[str | None]]
    attachment_worker_checker_timers: dict[str, Timer]
    upload_workers: dict[str, Worker[str | None]]
    bug_creation_worker: Worker[None] | None = None
    progress_start_time: float

    attachment_dir: Path
    log_widget: RichLog | None = None  # late init in on_mount

    submitter: Final[BugReportSubmitter[TAuth, TReturn]]

    CSS = """
    SubmissionProgressScreen {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    #menu_after_finish {
        display: none;
    }
    """

    CSS_PATH = "styles.tcss"

    def __init__(
        self,
        bug_report: BugReport,
        submitter: BugReportSubmitter[TAuth, TReturn],
        app_args: AppArgs,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.bug_report = bug_report
        self.submitter = submitter
        self.attachment_dir = Path(mkdtemp()).expanduser().absolute()
        self.attachment_workers = {}
        self.attachment_worker_checker_timers = {}
        self.upload_workers = {}
        self.progress_start_time = time.time()  # doesn't have to precise
        self.app_args = app_args

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
                # overwrite the old one to avoid counting th_log_with_time time waiting
                # for the auth modal
                self.progress_start_time = time.time()
            except AssertionError:
                self.dismiss("report_editor")

        # auth ready, do the jira/lp steps
        self.start_parallel_log_collection()
        self.bug_creation_worker = self.run_worker(
            self.create_bug,
            thread=True,
            name=WorkerName.BUG_CREATION,
            exit_on_error=False,
        )

    def start_parallel_log_collection(self) -> None:
        """Launches all log collectors and keep the worker objects

        This does NOT wait for them to finish, just launches them
        """
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        # get the log collectors running first
        # all log collectors are allowed to fail. If they do, write a message
        # to the screen to tell the user how to get the logs manually

        final_logs_to_include = set(self.bug_report.logs_to_include)
        for log_name, collector in LOG_NAME_TO_COLLECTOR.items():
            if collector.hidden and collector.collect_by_default:
                final_logs_to_include.add(log_name)

        for log_name in final_logs_to_include:

            async def run_collect(log: LogName):
                collector = LOG_NAME_TO_COLLECTOR[log]
                try:
                    rv = await collector.collect(
                        self.attachment_dir, self.bug_report
                    )
                    if rv and rv.strip():
                        # only show non-empty, non-null messages
                        self._log_with_time(
                            " ".join(
                                [
                                    "[green]OK[/]",
                                    f"[b]{collector.display_name}[/b]:",
                                    rv.strip(),
                                ]
                            )
                        )
                    else:
                        self._log_with_time(
                            " ".join(
                                [
                                    "[green]OK[/]",
                                    f"[b]{collector.display_name}[/b]:",
                                    "Finished collection!",
                                ]
                            )
                        )
                except Exception as e:
                    self._log_with_time(
                        " ".join(
                            [
                                f"[red]FAIL[/red] {collector.display_name} failed:",
                                repr(e),
                            ]
                        )
                    )
                    if collector.manual_collection_command:
                        self._log_with_time(
                            f"You can rerun [blue]{collector.display_name}[/] "
                            + f"with [blue]{collector.manual_collection_command}[/]"
                        )
                finally:
                    progress_bar.advance()

            def check_if_worker_is_pending(name: LogName):
                if self.attachment_workers[name].is_running:
                    msg = (
                        LOG_NAME_TO_COLLECTOR[name].display_name
                        + " is still running"
                    )
                    if (
                        t := LOG_NAME_TO_COLLECTOR[name].advertised_timeout
                    ) is not None:
                        msg += f" (timeout: {t}s)"
                    msg += "..."
                    self._log_with_time(msg)
                else:
                    self.attachment_worker_checker_timers[name].stop()

            self.attachment_workers[log_name] = self.run_worker(
                run_collect(log_name),
                name=log_name,
                exit_on_error=False,  # hold onto the err, don't crash
            )
            self.attachment_worker_checker_timers[log_name] = (
                self.set_interval(
                    30, lambda n=log_name: check_if_worker_is_pending(n)
                )
            )

            display_name = LOG_NAME_TO_COLLECTOR[log_name].display_name
            msg = f"Launched collector: {display_name}"
            if (
                t := LOG_NAME_TO_COLLECTOR[log_name].advertised_timeout
            ) is not None:
                msg += f" (timeout: {t}s)"
            self._log_with_time(msg)

        self._log_with_time(
            "[blue]Slow collectors will print a status report every 30 seconds"
        )

    def start_parallel_attachment_upload(self) -> None:
        assert self.log_widget
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        for file_name in self.attachment_dir.iterdir():

            def upload_one(f: Path):
                try:
                    rv = self.submitter.upload_attachment(f)

                    if rv and rv.strip():
                        # only show non-empty, non-null messages
                        self._log_with_time(
                            f"[green]OK[/] [b]Uploaded {f}[/]: {rv.strip()}"
                        )
                    else:
                        self._log_with_time(
                            f"[green]OK[/] [b]Uploaded {f}[/b]"
                        )
                except Exception as e:

                    self._log_with_time(
                        f"[red]FAIL[/red] failed to upload {f}: {repr(e)}"
                    )
                    raise e  # mark the worker as failed
                finally:
                    progress_bar.advance()

            self.upload_workers[str(file_name)] = self.run_worker(
                # closure workaround
                # https://stackoverflow.com/a/1107260
                # bind the value early
                lambda f=file_name: upload_one(f),
                thread=True,  # not async
                exit_on_error=False,  # hold onto the err, don't crash
            )

            self._log_with_time(f"Uploading: {file_name}")

    def start_sequential_attachment_upload(self) -> None:
        assert self.log_widget
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        def upload_all():
            failed_attachments: list[str] = []
            for f in self.attachment_dir.iterdir():
                try:
                    self._log_with_time(f"Uploading: {f}")

                    rv = self.submitter.upload_attachment(f)
                    if rv and rv.strip():
                        # only show non-empty, non-null messages
                        self._log_with_time(
                            f"[green]OK[/] [b]Uploaded {f}[/]: {rv.strip()}"
                        )
                    else:
                        self._log_with_time(
                            f"[green]OK[/] [b]Uploaded {f}[/b]"
                        )
                except Exception as e:
                    failed_attachments.append(f.name)
                    self._log_with_time(
                        f"[red]FAIL[/red] failed to upload {f}: {repr(e)}"
                    )
                finally:
                    progress_bar.advance()

            if len(failed_attachments) != 0:
                # force an error here to mark the worker as failed
                raise RuntimeError(
                    f"These attachments failed to upload: {', '.join(failed_attachments)}"
                )

        self.upload_workers[WorkerName.SEQUENTIAL_UPLOAD] = self.run_worker(
            upload_all,
            name=WorkerName.SEQUENTIAL_UPLOAD,  # just for completeness
            thread=True,  # not async
            exit_on_error=False,  # hold onto the err, don't crash
        )

    def create_bug(self) -> None:
        """Do the entire bug creation sequence. This should be run in a worker"""
        assert self.log_widget

        progress_bar = self.query_exactly_one("#progress", ProgressBar)
        display_name = self.submitter.display_name or self.submitter.name

        match self.bug_report:
            case BugReport() as b:
                submission_step_iterator = self.submitter.submit(b)
            case PartialBugReport() as p:
                assert self.app_args.bug_to_reopen
                submission_step_iterator = self.submitter.reopen(
                    p, self.app_args.bug_to_reopen
                )

        for step_result in submission_step_iterator:
            match step_result:
                case str():
                    # general logs
                    self._log_with_time(
                        f"[b]{display_name}[/b]: {step_result}"
                    )
                case AdvanceMessage():
                    # messages that will advance the progress bar
                    self._log_with_time(
                        f"[green]OK[/] [b]{display_name}[/b]: "
                        + step_result.message
                    )
                    progress_bar.advance()

        running_collectors = [
            w for w in self.attachment_workers.values() if w.is_running
        ]
        num_attachments = sum(1 for _ in self.attachment_dir.iterdir())
        if len(running_collectors) > 0:
            self._log_with_time(
                f"[blue]Finished bug creation. Waiting for {len(running_collectors)} log collector(s) to finish"
            )
            self._log_with_time(
                f"[blue]{num_attachments} attachment(s) will start to upload after they are done"
            )
            for c in running_collectors:
                if c.name in LOG_NAME_TO_COLLECTOR:
                    display_name = LOG_NAME_TO_COLLECTOR[
                        c.name  # pyright can't infer this yet
                    ].display_name  # pyright: ignore[reportArgumentType]
                    self._log_with_time(
                        f" - {display_name}",
                    )
        else:
            self._log_with_time(
                f"[blue]Finished bug creation, uploading {num_attachments} attachment(s)..."
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

    def _ready_to_upload_attachments(self) -> bool:
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

    def watch_finished(self):
        if not self.finished:
            return

        self.query_exactly_one("#give_up", Button).display = False

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
            if is_snap():
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
        if event.worker.state == WorkerState.CANCELLED:
            self._log_with_time(
                f"[yellow]{event.worker.name} was cancelled[/]"
            )

        if self.finished:
            # don't do the following callbacks if finished
            return

        worker_name = event.worker.name

        if worker_name == WorkerName.BUG_CREATION:
            self._bug_creation_worker_callback(event)
        elif worker_name in self.attachment_workers:
            self._attachment_worker_callback(event)

        self.finished = self.is_finished()

    @on(Button.Pressed, "#job")
    @on(Button.Pressed, "#session")
    @on(Button.Pressed, "#quit")
    def handle_button_in_menu_after_finish(self, event: Button.Pressed):
        if event.button.id in RETURN_SCREEN_CHOICES:
            self.dismiss(event.button.id)

    @on(Button.Pressed, "#give_up")
    def cancel_all_unfinished_collectors(self, event: Button.Pressed):
        for key, worker in self.attachment_workers.items():
            if worker.is_running:
                self._log_with_time(f"Cancelling collector [b]{key}[/]")
                worker.cancel()
                self.attachment_worker_checker_timers[key].stop()
                self.query_exactly_one("#progress", ProgressBar).advance()

        # nothing to give up, disable the button
        event.button.disabled = True
        event.button.label = "All collectors finished"
        event.button.styles.width = "auto"

    @override
    def compose(self) -> ComposeResult:
        yield SimpleHeader()

        with Center(classes="lrm1"):
            with HorizontalGroup(classes="w100 center"):
                yield Label("Submission Progress", classes="mr1")
                yield ProgressBar(
                    total=self.submitter.steps
                    + len(self.bug_report.logs_to_include)
                    * 2,  # collect + upload
                    id="progress",
                    show_eta=False,
                )
            yield RichLog(
                id="submission_logs",
                markup=True,
                wrap=True,
            )
            with HorizontalGroup(classes="w100 right"):
                yield Button(
                    "Give up",
                    id="give_up",
                    classes="wa",
                    variant="error",
                    compact=True,
                    tooltip="Cancel all unfinished log collectors",
                )

        with VerticalGroup(classes="db"):
            with VerticalGroup(
                classes="w100 ha center tbm1", id="menu_after_finish"
            ):
                yield Center(Label(classes="wa", id="finish_message"))
                with Center():
                    with HorizontalGroup(classes="wa center"):
                        if self.bug_report.checkbox_session:
                            yield Button(
                                "Select another session",
                                classes="mr1",
                                id="session",
                            )
                            yield Button(
                                "Select another job", classes="mr1", id="job"
                            )
                        if self.bug_report.checkbox_submission:
                            yield Button(
                                "Select another job", classes="mr1", id="job"
                            )
                        yield Button("Quit", id="quit")

            yield Footer()

    def _log_with_time(self, msg: str):
        if self.log_widget is None:
            self.log.warning("Uninitialized log widget")
            return
        # 999 seconds is about 2 hours
        # should be enough digits
        s = f"{round(time.time() - self.progress_start_time, 1)}".rjust(6)
        self.log_widget.write(f"[grey70][ {s} ][/] {msg}")

    def _bug_creation_worker_callback(self, event: Worker.StateChanged):
        if event.worker.name != WorkerName.BUG_CREATION:
            raise ValueError(
                f"This callback was used on {event.worker.name}, but expected {WorkerName.BUG_CREATION}"
            )

        match event.worker.state:
            case WorkerState.ERROR:
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
            case WorkerState.SUCCESS:
                if self._ready_to_upload_attachments():
                    self._launch_upload_workers()
            case _:
                pass

    def _attachment_worker_callback(self, event: Worker.StateChanged):
        if event.worker.name not in self.attachment_workers:
            raise ValueError(
                f"This callback was used on {event.worker.name}, but it's not a log collector"
            )

        match event.worker.state:
            case WorkerState.SUCCESS:
                if self._ready_to_upload_attachments():
                    self._launch_upload_workers()
            case WorkerState.ERROR:
                self._log_with_time(
                    f"[red]Collector {event.worker.name} failed! {escape_markup(repr(event.worker.error))}"
                )
            case _:
                pass

    def _launch_upload_workers(self):
        give_up_btn = self.query_exactly_one("#give_up", Button)
        give_up_btn.disabled = True
        give_up_btn.label = "All collectors finished"
        give_up_btn.styles.width = "auto"

        if self.submitter.allow_parallel_upload:
            self.start_parallel_attachment_upload()
        else:
            self.start_sequential_attachment_upload()

        progress_bar = self.query_exactly_one("#progress", ProgressBar)
        progress_bar.total = (
            self.submitter.steps
            + len(self.attachment_workers)
            + len(self.upload_workers)
        )
