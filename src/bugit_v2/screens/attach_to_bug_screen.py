"""
Editor for attaching logs, files and a comment to an existing bug
"""

import logging
import uuid
from collections.abc import Callable, Sequence
from functools import wraps
from typing import Final, Literal, cast, final, override

from textual import on, work
from textual.app import ComposeResult
from textual.containers import HorizontalGroup, Right, VerticalGroup, VerticalScroll
from textual.markup import escape as escape_markup
from textual.screen import Screen
from textual.timer import Timer
from textual.types import OptionDoesNotExist
from textual.widgets import Button, Collapsible, Footer, Label, SelectionList, TextArea
from textual.widgets.selection_list import Selection

from bugit_v2.checkbox_utils.checkbox_session import CheckboxSession
from bugit_v2.checkbox_utils.models import SimpleCheckboxSubmission
from bugit_v2.components.confirm_dialog import ConfirmScreen
from bugit_v2.components.file_picker import FilePickerWidget
from bugit_v2.components.header import SimpleHeader
from bugit_v2.dut_utils.log_collectors import (
    LOG_NAME_TO_COLLECTOR,
    NVIDIA_BUG_REPORT_PATH,
    LogCollector,
)
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.attachment_request import (
    ATTACH_PLACEHOLDER_COMMENT,
    AttachmentRequest,
    AttachSubmitterName,
    SerializableAttachmentRequest,
)
from bugit_v2.models.bug_report import LogName
from bugit_v2.utils.constants import ATTACH_AUTOSAVE_DIR, NullSelection

logger = logging.getLogger(__name__)

COMMENT_ID: Final = "comment"
LOGS_ID: Final = "logs_to_include"
FILES_ID: Final = "additional_files"
SUBMIT_ID: Final = "submit_button"


@final
class AttachToBugScreen(Screen[AttachmentRequest]):
    request_id: Final[uuid.UUID]
    session: Final[CheckboxSession | Literal[NullSelection.NO_SESSION]]
    checkbox_submission: Final[
        SimpleCheckboxSubmission | Literal[NullSelection.NO_CHECKBOX_SUBMISSION]
    ]
    job_id: Final[str | Literal[NullSelection.NO_JOB]]
    existing_request: Final[AttachmentRequest | None]
    submitter: Final[AttachSubmitterName]
    bug_id: Final[str]

    autosave_timer: Timer | None = None

    CSS = """
    AttachToBugScreen {
        width: 100%;
        height: 100%;
    }

    #comment {
        width: 60%;
        height: 24;
    }

    #submit_button {
        width: 100%;
        padding: 0;
    }

    #attach_metadata_header {
        background: $primary 10%;
        padding: 0;
    }

    #attach_metadata_header Label:last-child {
        margin-bottom: 1;
    }
    """
    CSS_PATH = "styles.tcss"

    def __init__(
        self,
        session: CheckboxSession | Literal[NullSelection.NO_SESSION],
        checkbox_submission: (
            SimpleCheckboxSubmission | Literal[NullSelection.NO_CHECKBOX_SUBMISSION]
        ),
        job_id: str | Literal[NullSelection.NO_JOB],
        app_args: AppArgs,
        existing_request: AttachmentRequest | None = None,
        # ---
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        if app_args.bug_to_attach is None:
            raise ValueError("AttachToBugScreen needs app_args.bug_to_attach")
        if app_args.submitter == "local":
            raise ValueError("The local submitter can't attach to existing bugs")

        self.session = session
        self.checkbox_submission = checkbox_submission
        self.job_id = job_id
        self.submitter = app_args.submitter
        self.bug_id = app_args.bug_to_attach
        self.existing_request = existing_request
        self.request_id = (
            existing_request.request_id if existing_request else uuid.uuid4()
        )

    @property
    def submitter_display_name(self) -> str:
        return "Jira" if self.submitter == "jira" else "Launchpad"

    @override
    def compose(self) -> ComposeResult:
        yield SimpleHeader(Label(id="dirty_label"))
        with Collapsible(
            title=(
                f"[bold]Attach to {self.submitter_display_name} "
                + f"bug {escape_markup(self.bug_id)}...[/bold]"
            ),
            collapsed=False,
            classes="nb",
            id="attach_metadata_header",
        ):
            if self.session is not NullSelection.NO_SESSION:
                yield Label(f"- Test Plan: {escape_markup(self.session.testplan_id)}")
            elif self.checkbox_submission is not NullSelection.NO_CHECKBOX_SUBMISSION:
                yield Label(
                    "- Test Plan: "
                    + escape_markup(self.checkbox_submission.base.testplan_id)
                )
            else:
                yield Label("- [$warning-darken-2]No session/submission selected")

            if self.job_id is NullSelection.NO_JOB:
                yield Label("- [$warning-darken-2]No job selected")
            else:
                yield Label(f"- Job ID: {escape_markup(self.job_id)}")

        with VerticalScroll():
            with HorizontalGroup():
                comment = TextArea(
                    id=COMMENT_ID,
                    classes="default_box",
                    soft_wrap=True,
                    tab_behavior="focus",
                    placeholder=f"Optional. Defaults to '{ATTACH_PLACEHOLDER_COMMENT}'",
                )
                comment.border_title = "[b]Comment[/]"
                comment.border_subtitle = (
                    "Posted as a new comment that links to the attachments"
                    if self.submitter == "jira"
                    else "Posted together with the 1st attachment"
                )
                yield comment

                with VerticalGroup():
                    logs = SelectionList[LogName](
                        *(
                            Selection[LogName](
                                collector.display_name,
                                collector.name,
                                self._should_select_collector_by_default(collector),
                                id=collector.name,
                            )
                            for collector in sorted(
                                self._available_collectors(),
                                # prioritize the ones selected by default
                                key=lambda c: (
                                    0
                                    if self._should_select_collector_by_default(c)
                                    else 1
                                ),
                            )
                        ),
                        classes="default_box",
                        id=LOGS_ID,
                    )
                    logs.border_title = "[b]Select some logs to attach[/]"
                    logs.border_subtitle = "Green = Selected"
                    yield logs
                    yield Right(
                        Button(
                            "Clear",
                            compact=True,
                            tooltip="Clear log selection",
                            classes="editor_button mr1",
                            id="clear_log_selection",
                        )
                    )
                    picker = FilePickerWidget(classes="default_box", id=FILES_ID)
                    picker.border_title = "[b]Additional files to attach[/]"
                    picker.border_subtitle = "Any file from the system"
                    yield picker

            yield Button(
                "Attach",
                id=SUBMIT_ID,
                variant="success",
                disabled=True,
            )
        yield Footer()

    def on_mount(self) -> None:
        if self.existing_request is not None:
            self._restore_existing_request(self.existing_request)
        self._update_submit_button()
        self.query_exactly_one(f"#{COMMENT_ID}", TextArea).focus()

    def _available_collectors(self) -> list[LogCollector]:
        def is_available(c: LogCollector) -> bool:
            if c.hidden or "attach" not in c.available_in:
                return False
            match c.name:
                case "checkbox-session":
                    return self.session is not NullSelection.NO_SESSION
                case "checkbox-submission":
                    return (
                        self.checkbox_submission
                        is not NullSelection.NO_CHECKBOX_SUBMISSION
                    )
                case "job-outputs":
                    return self.job_id is not NullSelection.NO_JOB
                case "nvidia-bug-report":
                    return NVIDIA_BUG_REPORT_PATH.exists()
                case _:
                    return True

        return [c for c in LOG_NAME_TO_COLLECTOR.values() if is_available(c)]

    def _should_select_collector_by_default(self, collector: LogCollector) -> bool:
        if collector.name == "job-outputs":
            return self.job_id is not NullSelection.NO_JOB
        if self.checkbox_submission is not NullSelection.NO_CHECKBOX_SUBMISSION:
            # the logs on this machine are likely unrelated to the submission
            return collector.name == "checkbox-submission"
        return collector.collect_by_default

    def _restore_existing_request(self, request: AttachmentRequest) -> None:
        self.query_exactly_one(f"#{COMMENT_ID}", TextArea).text = request.comment

        logs = cast(
            SelectionList[LogName], self.query_exactly_one(f"#{LOGS_ID}", SelectionList)
        )
        logs.deselect_all()  # clear first, then recover
        for log_name in request.logs_to_include:
            try:
                logs.select(logs.get_option(log_name))
            except OptionDoesNotExist:
                logger.warning(f"Ignoring unavailable log collector: {log_name}")

        self.query_exactly_one(f"#{FILES_ID}", FilePickerWidget).restore_selection(
            request.additional_files
        )

    def _build_request(self) -> AttachmentRequest:
        logs = cast(
            SelectionList[LogName], self.query_exactly_one(f"#{LOGS_ID}", SelectionList)
        )
        return AttachmentRequest(
            request_id=self.request_id,
            submitter=self.submitter,
            bug_id=self.bug_id,
            comment=self.query_exactly_one(f"#{COMMENT_ID}", TextArea).text,
            checkbox_session=(
                None if self.session is NullSelection.NO_SESSION else self.session
            ),
            checkbox_submission=(
                None
                if self.checkbox_submission is NullSelection.NO_CHECKBOX_SUBMISSION
                else self.checkbox_submission
            ),
            job_id=None if self.job_id is NullSelection.NO_JOB else self.job_id,
            logs_to_include=list(logs.selected),
            additional_files=self.query_exactly_one(
                f"#{FILES_ID}", FilePickerWidget
            ).chosen_files,
        )

    def _attachment_summary(self, request: AttachmentRequest) -> Sequence[str]:
        parts: list[str] = []
        if n := len(request.logs_to_include):
            parts.append(f"{n} log{'s' if n > 1 else ''}")
        if n := len(request.additional_files):
            parts.append(f"{n} file{'s' if n > 1 else ''}")
        return parts

    @on(TextArea.Changed)
    @on(SelectionList.SelectedChanged)
    @on(FilePickerWidget.FilesUpdated)
    def _update_submit_button(self) -> None:
        request = self._build_request()
        btn = self.query_exactly_one(f"#{SUBMIT_ID}", Button)
        attachments = self._attachment_summary(request)
        btn.disabled = not attachments and not request.comment.strip()
        if btn.disabled:
            btn.label = "Nothing to attach (select some logs/files or write a comment)"
        elif attachments:
            btn.label = f"Attach {' and '.join(attachments)} to {self.bug_id}"
        else:
            btn.label = f"Post comment on {self.bug_id}"

    @work
    @on(Button.Pressed, f"#{SUBMIT_ID}")
    async def confirm_submit(self) -> None:
        request = self._build_request()
        attachments = self._attachment_summary(request)
        if self.submitter == "jira":
            sub_prompt = "A new comment will be posted on the issue" + (
                " with links to the attachments" if attachments else ""
            )
        else:
            sub_prompt = (
                "Each file will appear as a separate comment on the bug"
                if attachments
                else "A new comment will be posted on the bug"
            )
        ok = (
            await self.app.push_screen_wait(
                ConfirmScreen(
                    f"Are you sure you want to attach to {escape_markup(self.bug_id)}?",
                    sub_prompt=sub_prompt,
                    choices=(("Yes", "yes"), ("No", "no")),
                    focus_id_on_mount="no",
                )
            )
            == "yes"
        )
        if ok:
            self.dismiss(self._build_request())

    @on(Button.Pressed, "#clear_log_selection")
    def clear_log_selection(self, _: Button.Pressed) -> None:
        self.query_exactly_one(f"#{LOGS_ID}", SelectionList).deselect_all()

    def _debounce[**P, R](self, f: Callable[P, R], delay: float) -> Callable[P, None]:
        @wraps(f)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
            if self.autosave_timer is not None:
                self.autosave_timer.stop()

            self.query_exactly_one("#dirty_label", Label).update(
                "[grey]Autosave scheduled..."
            )
            self.autosave_timer = self.set_timer(delay, lambda: f(*args, **kwargs))

        return wrapper

    @on(TextArea.Changed)
    @on(SelectionList.SelectedChanged)
    @on(FilePickerWidget.FilesUpdated)
    def trigger_autosave(self) -> None:
        def save():
            label = self.query_exactly_one("#dirty_label", Label)
            try:
                with open(ATTACH_AUTOSAVE_DIR / f"{self.request_id}.json", "w") as f:
                    f.write(
                        SerializableAttachmentRequest.from_attachment_request(
                            self._build_request()
                        ).model_dump_json()
                    )
                label.update("[green]Progress Saved")
            except Exception as e:  # noqa: BLE001
                logger.error(repr(e))
                label.update(f"[red]Autosave failed! {escape_markup(repr(e)[:20])}")

        # run auto save 1 second after the user stops typing
        self._debounce(lambda: self.run_worker(save, thread=True), 1)()
