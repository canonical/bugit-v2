import enum
import logging
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import mkdtemp
from typing import Final, Literal, final, override

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Center, HorizontalGroup, VerticalGroup
from textual.markup import escape as escape_markup
from textual.reactive import var
from textual.screen import Screen
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Button, Footer, Label, ProgressBar, RichLog, Static
from textual.worker import Worker, WorkerState

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.components.confirm_dialog import ConfirmScreen
from bugit_v2.components.header import SimpleHeader
from bugit_v2.dut_utils.log_collectors import LOG_NAME_TO_COLLECTOR
from bugit_v2.models.bug_report import BugReport, LogName
from bugit_v2.utils import is_prod, is_snap, slugify
from bugit_v2.utils.constants import HOST_FS

logger = logging.getLogger(__name__)

ReturnScreenChoice = Literal["job", "session", "quit", "report_editor"]
RETURN_SCREEN_CHOICES: tuple[ReturnScreenChoice, ...] = ReturnScreenChoice.__args__


class WorkerName(enum.StrEnum):
    BUG_CREATION = enum.auto()
    SEQUENTIAL_UPLOAD = enum.auto()


@final
class SubmissionProgressScreen[TAuth](Screen[ReturnScreenChoice]):
    """
    The progress screen shown while submission/log collection is happening
    """

    bug_report: BugReport

    finished = var(False)

    attachment_workers: dict[LogName, Worker[str | None]]
    attachment_worker_checker_timers: dict[str, Timer]
    upload_workers: dict[str, Worker[str | None]]
    bug_creation_worker: Worker[None] | None = None
    finalize_worker: Worker[None] | None = None

    progress_start_time: float

    attachment_dir: Path
    log_widget: RichLog | None = None  # late init in on_mount, collector output only
    activity_log_widget: RichLog | None = None  # late init in on_mount, everything else
    upload_attempted = False

    # snapshot the current thread id in the constructor
    # to allow _call_on_app_thread to dynamically pick between
    # direct call and app.call_from_thread
    _app_thread_id: int | None = None

    # tracks when each log collector was launched so we can show elapsed time
    collector_start_times: dict[LogName, float]
    # tracks the most recent stdout line streamed from each running collector
    collector_last_line: dict[LogName, str]
    collector_status_timer: Timer | None = None

    submitter: Final[BugReportSubmitter[TAuth]]
    # handles the special case for bugit.submit
    # app mode is for bugit.submit
    # screen mode is for the main app
    mode: Final[Literal["app", "screen"]]

    CSS = """
    SubmissionProgressScreen {
        width: 100%;
        height: 100%;
        align: center middle;
    }

    #menu_after_finish {
        display: none;
    }

    #collector_status {
        display: none;
        height: auto;
        padding: 0 1;
    }

    #activity_log {
        height: 40%;
        border: round $primary;
    }

    #submission_logs {
        height: 1fr;
        border: round $primary;
    }

    #progress_bar_status_container {
        border: round $accent;
        padding: 0;
    }

    RichLog {
        background: $background 0%;
    }
    """

    CSS_PATH = "styles.tcss"

    def __init__(
        self,
        bug_report: BugReport,
        submitter: BugReportSubmitter[TAuth],
        mode: Literal["app", "screen"] = "screen",
        attachment_dir: Path | None = None,
        # ---
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        self.bug_report = bug_report
        self.submitter = submitter
        self.mode = mode

        if attachment_dir and attachment_dir.exists() and attachment_dir.is_dir():
            self.attachment_dir = attachment_dir
        else:
            try:
                prefix = Path(os.environ["SNAP_USER_COMMON"]) / "tmp"
                prefix.mkdir(exist_ok=True)
            except KeyError:
                prefix = Path("/var/tmp")
            self.attachment_dir = Path(mkdtemp(dir=prefix)).expanduser().absolute()

        self.attachment_workers = {}
        self.attachment_worker_checker_timers = {}
        self.upload_workers = {}
        self.collector_start_times = {}
        self.collector_last_line = {}
        self.progress_start_time = time.time()

        super().__init__(name, id, classes)

    @work
    async def on_mount(self) -> None:
        # capture this once, on the app/UI thread, so _call_on_app_thread has
        # a stable reference that never changes for the lifetime of the screen
        self._app_thread_id = threading.get_ident()

        self.log_widget = self.query_exactly_one("#submission_logs", RichLog)
        self.activity_log_widget = self.query_exactly_one("#activity_log", RichLog)
        self.query_exactly_one("#menu_after_finish").display = False

        if self.submitter.auth_modal:
            # submission screen controls how the credentials are assigned
            try:
                cached_credentials = self.submitter.get_cached_credentials()
                if cached_credentials is None:
                    auth_rv = await self.app.push_screen_wait(
                        self.submitter.auth_modal()
                    )
                    if not auth_rv:
                        raise ValueError("Auth modal was dismissed without a result")
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
            except ValueError:
                if self.mode == "screen":
                    prompt = ConfirmScreen[ReturnScreenChoice](
                        "[red]Authentication form returned nothing[/]",
                        sub_prompt="Click this button to go back to the editor and try again",
                        choices=(("Return to Report Editor", "report_editor"),),
                        focus_id_on_mount="report_editor",
                    )
                else:
                    prompt = ConfirmScreen[Literal["quit"]](
                        "[red]Authentication form returned nothing[/]",
                        sub_prompt="Relaunch bugit to authenticate again",
                        choices=(("Quit", "quit"),),
                        focus_id_on_mount="quit",
                    )
                self.dismiss(await self.app.push_screen_wait(prompt))
                return  # need explicit return here

        # only collect logs when it's in the main app
        if self.mode == "screen":
            self.start_parallel_log_collection()
            self.collector_status_timer = self.set_interval(
                1, self._update_collector_status
            )
            await self._update_collector_status()

        # auth ready, do the jira/lp steps
        self.bug_creation_worker = self.run_worker(
            self.create_bug,
            thread=True,
            name=WorkerName.BUG_CREATION,
            exit_on_error=False,
        )

    def on_unmount(self) -> None:
        if self.collector_status_timer is not None:
            self.collector_status_timer.stop()
        for key, worker in self.attachment_workers.items():
            if worker.is_running:
                self._log_collector(
                    f"Unmount, cancelling collector {escape_markup(str(key))}"
                )
                worker.cancel()
        for key, worker in self.upload_workers.items():
            if worker.is_running:
                self._log_with_time(
                    f"Unmount, cancelling uploader {escape_markup(str(key))}"
                )
                worker.cancel()

    def start_parallel_log_collection(self) -> None:
        """Launches all log collectors and keep the worker objects

        This does NOT wait for them to finish, just launches them
        """
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        # the additional files are also technically "logs"
        # run the workaround in this function, not the uploaders
        for file in self.bug_report.additional_files:
            # workaround, if sysfs nodes are selected and we don't copy
            # uploads will hang forever
            # so we must copy them and "finish" writing the file
            try:
                if (is_snap() and file.is_relative_to(HOST_FS / "home")) or (
                    not is_snap() and file.is_relative_to("/home")
                ):
                    # from DUT's home, just use the actual name
                    target_file_name = file.name
                else:
                    # something under root, include the entire path and slugify
                    target_file_name = slugify(str(file.parent)) + "_" + file.name
                shutil.copy(file, self.attachment_dir / target_file_name)
            except Exception as e:
                self._log_collector(
                    f"FAIL! Failed to copy {escape_markup(str(file))}: {escape_markup(repr(e))}"
                )

        # get the log collectors running first
        # all log collectors are allowed to fail. If they do, write a message
        # to the screen to tell the user how to get the logs manually
        for log_name in self.bug_report.logs_to_include:

            async def run_collect(log: LogName):
                collector = LOG_NAME_TO_COLLECTOR[log]
                last_logged_at = 0.0
                # chatty collectors (e.g. a week of journalctl, snap-debug)
                # can emit tens of thousands of lines; throttle how often we
                # write to the RichLog to avoid flooding the UI/memory, while
                # still updating the status panel's "latest line" every time
                min_log_interval = 0.2

                def stream_line(line: str) -> None:
                    nonlocal last_logged_at
                    stripped = line.strip()
                    if not stripped:
                        return
                    self.collector_last_line[log] = stripped
                    now = time.time()
                    if now - last_logged_at < min_log_interval:
                        return
                    last_logged_at = now
                    self._log_collector(
                        f"{collector.display_name} | {escape_markup(stripped)}"
                    )

                try:
                    rv = await collector.collect(
                        self.attachment_dir, self.bug_report, stream_line
                    )
                    if rv and rv.strip():
                        # only show non-empty, non-null messages
                        self._log_collector(
                            " ".join(
                                [
                                    "OK!",
                                    f"{escape_markup(collector.display_name)}:",
                                    escape_markup(rv.strip()),
                                ]
                            )
                        )
                    else:
                        self._log_collector(
                            " ".join(
                                [
                                    "OK!",
                                    f"{escape_markup(collector.display_name)}:",
                                    "Finished collection!",
                                ]
                            )
                        )
                except Exception as e:
                    self._log_collector(
                        " ".join(
                            [
                                f"FAIL! {escape_markup(collector.display_name)} failed:",
                                escape_markup(repr(e)),
                            ]
                        )
                    )
                    logger.error(f"{collector.display_name}:{e!r}")
                    if collector.manual_collection_command:
                        self._log_collector(
                            f"You can rerun {collector.display_name} "
                            + f"with {collector.manual_collection_command}"
                        )
                finally:
                    self.collector_last_line.pop(log, None)
                    progress_bar.advance()

            def check_if_worker_is_pending(name: LogName):
                if self.attachment_workers[name].is_running:
                    msg = f"{LOG_NAME_TO_COLLECTOR[name].display_name} is still running"
                    if (t := LOG_NAME_TO_COLLECTOR[name].advertised_timeout) is not None:
                        msg += f" (timeout: {t}s)"
                    msg += "..."
                    self._log_collector(msg)
                else:
                    self.attachment_worker_checker_timers[name].stop()

            self.attachment_workers[log_name] = self.run_worker(
                run_collect(log_name),
                name=log_name,
                exit_on_error=False,  # hold onto the err, don't crash
            )
            self.collector_start_times[log_name] = time.time()
            self.attachment_worker_checker_timers[log_name] = self.set_interval(
                30, lambda n=log_name: check_if_worker_is_pending(n)
            )

            display_name = LOG_NAME_TO_COLLECTOR[log_name].display_name
            msg = f"Launched collector: {display_name}"
            if (t := LOG_NAME_TO_COLLECTOR[log_name].advertised_timeout) is not None:
                msg += f" (timeout: {t}s)"
            self._log_collector(msg)

        self._log_collector(
            "[blue]Slow collectors will print a status report every 30 seconds"
        )

    def start_parallel_attachment_upload(self) -> None:
        if not self.activity_log_widget:
            raise RuntimeError("Activity log widget is not mounted")
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        def upload_one(f: Path):
            try:
                if f.stat().st_size == 0:
                    self._log_with_time(
                        f"WARN! {escape_markup(str(f))} is an empty file. Skipping"
                    )
                    return

                if not f.is_file():
                    raise RuntimeError(f"{f} is not a regular file during submission")

                rv = self.submitter.upload_attachment(f, slugify(str(f.stem)) + f.suffix)

                if rv and rv.strip():
                    # only show non-empty, non-null messages
                    self._log_with_time(
                        f"OK! Uploaded {escape_markup(f.name)}: {escape_markup(rv.strip())}"
                    )
                else:
                    self._log_with_time(f"OK! Uploaded {escape_markup(f.name)}")
            except Exception as e:
                self._log_with_time(
                    f"FAIL! failed to upload {escape_markup(str(f))}: {escape_markup(repr(e))}"
                )
                raise e  # mark the worker as failed
            finally:
                self._call_on_app_thread(progress_bar.advance)

        for file in self.attachment_dir.iterdir():
            self.upload_workers[str(file)] = self.run_worker(
                # closure workaround
                # https://stackoverflow.com/a/1107260
                # bind the value early
                lambda f=file: upload_one(f),
                thread=True,  # not async
                exit_on_error=False,  # hold onto the err, don't crash
            )
            self._log_with_time(f"Uploading: {escape_markup(file.name)}")

    def start_sequential_attachment_upload(self) -> None:
        if not self.activity_log_widget:
            raise RuntimeError("Activity log widget is not mounted")
        progress_bar = self.query_exactly_one("#progress", ProgressBar)

        def upload_all():
            failed_attachments: list[str] = []
            for f in self.attachment_dir.iterdir():
                try:
                    if f.stat().st_size == 0:
                        self._log_with_time(
                            f"WARN! {escape_markup(str(f))} is an empty file. Skipping"
                        )
                        continue

                    self._log_with_time(f"Uploading: {escape_markup(f.name)}")
                    rv = self.submitter.upload_attachment(f)

                    if rv and rv.strip():
                        # only show non-empty, non-null messages
                        self._log_with_time(
                            f"OK! Uploaded {escape_markup(f.name)}: {escape_markup(rv.strip())}"
                        )
                    else:
                        self._log_with_time(f"OK! Uploaded {escape_markup(f.name)}")
                except Exception as e:
                    failed_attachments.append(f.name)
                    self._log_with_time(
                        f"FAIL! failed to upload {escape_markup(str(f))}: {escape_markup(repr(e))}"
                    )
                finally:
                    self._call_on_app_thread(progress_bar.advance)

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
        if not self.activity_log_widget:
            raise RuntimeError("Activity log widget is not mounted")

        progress_bar = self.query_exactly_one("#progress", ProgressBar)
        display_name = self.submitter.display_name or self.submitter.name

        for step_result in self.submitter.submit(self.bug_report):
            match step_result:
                case str():
                    # general logs
                    self._log_with_time(
                        f"{escape_markup(display_name)}: {escape_markup(step_result)}"
                    )
                case AdvanceMessage():
                    # messages that will advance the progress bar
                    self._log_with_time(
                        f"OK! {escape_markup(display_name)}: "
                        + escape_markup(step_result.message)
                    )
                    self._call_on_app_thread(progress_bar.advance)

        running_collectors = [
            w for w in self.attachment_workers.values() if w.is_running
        ]
        num_attachments = sum(1 for _ in self.attachment_dir.iterdir())
        if len(running_collectors) > 0:
            self._log_with_time(
                f"Finished bug creation. Waiting for {len(running_collectors)} log collector(s) to finish"
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
            if num_attachments > 0:
                self._log_with_time(
                    f"[blue]Finished bug creation, uploading {num_attachments} attachment(s)..."
                )
            else:
                self._log_with_time(
                    "[blue]Finished bug creation, no attachments to upload"
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
        if not self.upload_attempted:
            logger.debug("Attachment upload hasn't been attempted")
            return False
        if self.bug_creation_worker is None:
            logger.debug("No bug creation worker")
            return False
        if self.bug_creation_worker.state != WorkerState.SUCCESS:
            logger.debug("Bug creation worker not done")
            return False
        if not all(w.is_finished for w in self.attachment_workers.values()):
            logger.debug("Some attachment collectors are still running")
            return False
        if not all(w.is_finished for w in self.upload_workers.values()):
            logger.debug("Some attachment upload-ers are still running")
            return False

        return True

    def _ready_to_upload_attachments(self) -> bool:
        if self.bug_creation_worker is None:
            logger.error("No bug creation worker, logic error")
            return False
        if self.bug_creation_worker.state != WorkerState.SUCCESS:
            # explicitly check for success here
            # because any failure in the bug creation worker
            # will prompt the user to go back to the editor
            logger.debug(
                f"Bug creation worker hasn't finished: {self.bug_creation_worker.state}"
            )
            return False

        if any(w.is_running for w in self.upload_workers.values()):
            logger.debug("An upload worker is already running")
            return False

        if not all(w.is_finished for w in self.attachment_workers.values()):
            logger.debug("Some attachment workers are not done")
            return False

        return True

    def watch_finished(self):
        if not self.finished:
            return

        # immediately hide the give up button
        self.query_exactly_one("#give_up", Button).display = False
        self.run_worker(self._actually_finish, thread=True)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.state == WorkerState.CANCELLED:
            log = (
                self._log_collector
                if event.worker.name in self.attachment_workers
                else self._log_with_time
            )
            log(f"{event.worker.name} was cancelled")

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
                self._log_collector(f"Cancelling collector {escape_markup(str(key))}")
                worker.cancel()
                self.attachment_worker_checker_timers[key].stop()
                self.query_exactly_one("#progress", ProgressBar).advance()

        # nothing to give up, disable the button
        event.button.disabled = True
        event.button.label = "All collectors finished"
        event.button.styles.width = "auto"
        self._update_collector_status()

    @override
    def compose(self) -> ComposeResult:
        yield SimpleHeader()

        with Center(classes="lrm1"):
            with VerticalGroup(id="progress_bar_status_container"):
                with HorizontalGroup(classes="w100"):
                    yield Label("Submission Progress", classes="mr1")
                    yield ProgressBar(
                        total=self.submitter.steps
                        + len(self.bug_report.logs_to_include) * 2,  # collect + upload
                        id="progress",
                        show_eta=False,
                    )
                yield VerticalGroup(
                    id="collector_status_container",
                    markup=False,
                )
            al = RichLog(
                id="activity_log",
                markup=False,
                wrap=True,
                max_lines=100,
            )
            al.border_title = "Submitter Output"
            yield al

            sl = RichLog(
                id="submission_logs",
                markup=False,
                wrap=True,
                max_lines=5000,
            )
            sl.border_title = "Log Collector Output"
            yield sl
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
            with VerticalGroup(classes="w100 ha center tbm1", id="menu_after_finish"):
                yield Center(Label(classes="wa", id="finish_message"))
                with Center(), HorizontalGroup(classes="wa center"):
                    if self.mode == "screen":
                        if self.bug_report.checkbox_session:
                            yield Button(
                                "Select another session",
                                classes="mr1",
                                id="session",
                            )
                            yield Button("Select another job", classes="mr1", id="job")
                        if self.bug_report.checkbox_submission:
                            yield Button("Select another job", classes="mr1", id="job")
                    yield Button("Quit", id="quit")

            yield Footer()

    async def _update_collector_status(self) -> None:
        """Refreshes the live "collectors remaining" panel.

        Runs on a 1s interval so users can see how many log collectors are
        still running and how long each of them has been running for, even
        during long stretches where no collector has produced any log output.
        """
        status_widget = self.query_exactly_one(
            "#collector_status_container", VerticalGroup
        )
        await status_widget.remove_children()

        running_names: list[LogName] = [
            name
            for name, worker in self.attachment_workers.items()
            if not worker.is_finished
        ]

        if not running_names:
            await status_widget.remove_children()
            status_widget.display = False
            if self.collector_status_timer is not None:
                self.collector_status_timer.stop()
            return

        status_widget.display = True
        widgets_to_mount: list[Widget] = [
            Static(f"{len(running_names)} log collector(s) still running:")
        ]

        now = time.time()
        for name in running_names:
            collector = LOG_NAME_TO_COLLECTOR[name]
            elapsed = now - self.collector_start_times.get(name, now)
            timeout_suffix = (
                f" / {collector.advertised_timeout}s timeout"
                if collector.advertised_timeout is not None
                else ""
            )

            last_line = self.collector_last_line.get(name)
            if last_line and len(last_line) > 80:
                last_line = last_line[:77] + "..."

            widgets_to_mount.append(
                HorizontalGroup(
                    Static(
                        f" - [$secondary]{collector.display_name}[/]: [$accent]{elapsed:.0f}s[/] elapsed{timeout_suffix}",
                        classes="wa",
                    ),
                    Static(f" - {last_line}", markup=False, classes="wa"),
                    classes="wa",
                )
            )

        await status_widget.mount_all(widgets_to_mount)

    def _log_with_time(self, msg: str):
        """Logs a general "activity" message: bug creation steps, auth,
        uploads, finalize, etc. Anything that isn't raw collector output or
        collector status belongs here. See `_log_collector` for that.
        """
        self._write_log(self.activity_log_widget, msg)

    def _log_collector(self, msg: str):
        """Logs a collector-specific message: launch/status/stdout/result.

        Kept in its own widget so verbose collectors (journalctl, dmesg,
        snap-debug, etc.) don't drown out the higher-signal activity log.
        """
        self._write_log(self.log_widget, msg)

    def _call_on_app_thread[**P, R](
        self, callback: Callable[P, R], *args: P.args, **kwargs: P.kwargs
    ) -> None:
        """
        Thin wrapper over call_from_thread
        """
        if threading.get_ident() == self._app_thread_id:
            callback(*args, **kwargs)
        else:
            self.app.call_from_thread(callback, *args, **kwargs)

    def _write_log(self, widget: RichLog | None, msg: str):
        if widget is None:
            logger.warning("Uninitialized log widget")
            return
        # 999 seconds is about 2 hours
        # should be enough digits
        s = f"{round(time.time() - self.progress_start_time, 1)}".rjust(6)
        line = f"[ {s} ] {msg}"
        self._call_on_app_thread(widget.write, line)

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
                        shutil.rmtree(self.attachment_dir, ignore_errors=True)

                match self.mode:
                    case "screen":

                        def dismiss_wrapper(_: ReturnScreenChoice | None):
                            # force a null return to avoid awaiting inside a msg handler
                            self.dismiss("report_editor")

                        self.app.push_screen(
                            ConfirmScreen[ReturnScreenChoice](
                                "Got the following error during submission",
                                sub_prompt=f"[red]{escape_markup(str(event.worker.error))}",
                                choices=(
                                    (
                                        "Return to Report Editor",
                                        "report_editor",
                                    ),
                                ),
                                focus_id_on_mount="report_editor",
                            ),
                            dismiss_wrapper,
                        )
                    case "app":

                        def dismiss_wrapper(_: ReturnScreenChoice | None):
                            self.dismiss("quit")

                        self.app.push_screen(
                            ConfirmScreen[Literal["quit"]](
                                "Got the following error during submission",
                                sub_prompt=f"[red]{escape_markup(str(event.worker.error))}",
                                choices=(("Quit", "quit"),),
                                focus_id_on_mount="quit",
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
            case WorkerState.SUCCESS | WorkerState.CANCELLED:
                if self._ready_to_upload_attachments():
                    self._launch_upload_workers()
            case WorkerState.ERROR:
                self._log_collector(
                    f"[red]Collector {event.worker.name} failed! {escape_markup(repr(event.worker.error))}"
                )
            case _:
                pass

    def _launch_upload_workers(self):
        give_up_btn = self.query_exactly_one("#give_up", Button)
        give_up_btn.disabled = True
        give_up_btn.label = "All collectors finished"
        give_up_btn.styles.width = "auto"

        # make sure we own everything before uploading
        if self.mode == "screen":
            subprocess.check_call(
                [
                    "sudo",
                    "--non-interactive",
                    "chown",
                    "-R",
                    str(os.getuid()),
                    str(self.attachment_dir.absolute()),
                ]
            )

        if self.submitter.allow_parallel_upload:
            self.start_parallel_attachment_upload()
        else:
            self.start_sequential_attachment_upload()
        self.upload_attempted = True

        progress_bar = self.query_exactly_one("#progress", ProgressBar)
        progress_bar.total = (
            self.submitter.steps
            + len(self.attachment_workers)
            + len(self.upload_workers)
        )

    def _actually_finish(self):
        try:
            rv = self.submitter.finalize()
            finalize_ok = True
            if rv:
                self._log_with_time(f"FINALIZE OK {escape_markup(rv)}")
            else:
                self._log_with_time(
                    f"FINALIZE OK {escape_markup(self.submitter.display_name or self.submitter.name)}"
                )
        except Exception as e:
            finalize_ok = False
            self._log_with_time(f"FINALIZE FAIL!: {escape_markup(repr(e))}")
            logger.error(e)

        finish_message_lines = ["[green]Submission finished![/]"]

        all_upload_ok = all(
            w.state == WorkerState.SUCCESS for w in self.upload_workers.values()
        )
        if all_upload_ok and finalize_ok:
            # only cleanup if everything was uploaded
            finish_message_lines.insert(
                1,
                f"URL: [$primary]{self.submitter.bug_url}[/]",
            )
            if is_prod():
                shutil.rmtree(self.attachment_dir, ignore_errors=True)

        if not (all_upload_ok and finalize_ok) and self.attachment_dir.exists():
            if is_snap():
                attachment_dir = (
                    "/tmp/snap-private-tmp/snap.bugit-v2/tmp" / self.attachment_dir
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

        if self.mode == "screen":
            finish_message_lines.append(
                "You can go back to job/session selection or quit BugIt."
            )

        def _show_finish_message() -> None:
            self.query_exactly_one("#finish_message", Label).update(
                "\n".join(finish_message_lines)
            )
            self.query_exactly_one("#menu_after_finish").display = True

        self._call_on_app_thread(_show_finish_message)
