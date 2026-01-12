import enum
import json
import logging
import uuid
from collections.abc import Mapping
from dataclasses import asdict
from functools import wraps
from typing import Callable, Final, Literal, cast, final

from textual import on, work
from textual.app import ComposeResult
from textual.containers import (
    HorizontalGroup,
    Right,
    VerticalGroup,
    VerticalScroll,
)
from textual.markup import escape as escape_markup
from textual.reactive import var
from textual.screen import Screen
from textual.timer import Timer
from textual.types import OptionDoesNotExist
from textual.validation import ValidationResult, Validator
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Input,
    Label,
    RadioButton,
    RadioSet,
    SelectionList,
    TextArea,
)
from textual.widgets.selection_list import Selection
from textual.worker import Worker, WorkerState
from typing_extensions import override

from bugit_v2.checkbox_utils.checkbox_session import Session
from bugit_v2.checkbox_utils.get_cert_status import (
    TestCaseWithCertStatus,
    get_certification_status,
)
from bugit_v2.checkbox_utils.models import (
    CERT_STATUSES,
    CertificationStatus,
    SimpleCheckboxSubmission,
)
from bugit_v2.components.confirm_dialog import ConfirmScreen
from bugit_v2.components.description_editor import DescriptionEditor
from bugit_v2.components.header import SimpleHeader
from bugit_v2.components.selection_with_preview import SelectionWithPreview
from bugit_v2.dut_utils.info_getters import get_standard_info
from bugit_v2.dut_utils.log_collectors import (
    LOG_NAME_TO_COLLECTOR,
    NVIDIA_BUG_REPORT_PATH,
    LogCollector,
)
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.bug_report import (
    BUG_STATUSES,
    ISSUE_FILE_TIMES,
    SEVERITIES,
    BugReport,
    LogName,
    pretty_issue_file_times,
    pretty_severities,
)
from bugit_v2.utils.constants import (
    AUTOSAVE_DIR,
    FEATURE_MAP,
    MAX_JOB_OUTPUT_LEN,
    VENDOR_MAP,
    NullSelection,
)

logger = logging.getLogger(__name__)


class WorkerName(enum.StrEnum):
    GET_CERT_STATUS = enum.auto()
    GET_STANDARD_INFO = enum.auto()
    SUBMISSION_CERT_STATUS = enum.auto()


class BugReportElemId(enum.StrEnum):
    TITLE = "title"
    DESCRIPTION = "description"
    ISSUE_FILE_TIME = "issue_file_time"
    PLATFORM_TAGS = "platform_tags"
    ASSIGNEE = "assignee"
    SEVERITY = "severity"
    PROJECT = "project"
    ADDITIONAL_TAGS = "additional_tags"
    LOGS_TO_INCLUDE = "logs_to_include"
    LP_STATUS = "status"
    IMPACTED_FEATURES = "impacted_features"
    IMPACTED_VENDORS = "impacted_vendors"


class ValidSpaceSeparatedTags(Validator):
    @override
    def validate(self, value: str) -> ValidationResult:
        if self.is_valid_space_separated_string_tags(value):
            return self.success()
        else:
            return self.failure("Can't have trailing spaces")

    @staticmethod
    def is_valid_space_separated_string_tags(value: str) -> bool:
        for tag in value.strip().split():
            if tag.strip() == "":
                return False
            if " " in tag:
                return False

        return True


class NoSpaces(Validator):
    @override
    def validate(self, value: str) -> ValidationResult:
        if " " in value.strip():
            return self.failure("Can't have spaces here")
        else:
            return self.success()


class NonEmpty(Validator):
    @override
    def validate(self, value: str) -> ValidationResult:
        if not value.strip():
            return self.failure("Must be non-empty after trimming")
        else:
            return self.success()


@final
class BugReportScreen(Screen[BugReport]):
    report_id: Final[uuid.UUID]
    session: Final[Session | Literal[NullSelection.NO_SESSION]]
    checkbox_submission: Final[
        SimpleCheckboxSubmission | Literal[NullSelection.NO_CHECKBOX_SUBMISSION]
    ]
    job_id: Final[str | Literal[NullSelection.NO_JOB]]
    existing_report: Final[BugReport | None]
    app_args: Final[AppArgs]
    # ELEM_ID_TO_BORDER_TITLE[id] = (title, subtitle)
    # id should match the property name in the BugReport object
    # TODO: rename this, it does more than just holding titles now
    elem_id_to_border_title: Final[Mapping[BugReportElemId, tuple[str, str]]]
    # Is the device where bugit is running on the one we want to open bugs for?
    dut_is_report_target: Final[bool]

    initial_report: dict[str, str]

    autosave_timer: Timer | None = None

    CSS = """
    BugReportScreen {
        width: 100%;
        height: 100%;
    }

    #impacted_features {
        height: 25;
    }

    #impacted_vendors {
        height: 17;
    }

    #submit_button {
        width: 100%;
        padding: 0;
    }

    #description {
        height: auto;
        width: 60%;
    }

    #bug_report_metadata_header {
        background: $primary 10%;
        padding: 0;
    }

    #bug_report_metadata_header Label:last-child {
        margin-bottom: 1;
    }

    #cert_status_box {
        width: 50%;
        height: 100%;
        content-align: center middle;
        padding: 1;
    }
    """
    CSS_PATH = "styles.tcss"

    # inputs that have validators
    # the keys should appear in elem_id_to_border_title
    validation_status = var(
        {
            BugReportElemId.TITLE: False,
            BugReportElemId.PLATFORM_TAGS: True,
            BugReportElemId.PROJECT: False,
        }
    )

    def __init__(
        self,
        session: Session | Literal[NullSelection.NO_SESSION],
        checkbox_submission: (
            SimpleCheckboxSubmission | Literal[NullSelection.NO_CHECKBOX_SUBMISSION]
        ),
        job_id: str | Literal[NullSelection.NO_JOB],
        app_args: AppArgs,
        existing_report: BugReport | None = None,
        dut_is_report_target: bool = True,
        # ---
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.session = session
        self.checkbox_submission = checkbox_submission
        self.job_id = job_id
        self.app_args = app_args
        self.dut_is_report_target = dut_is_report_target

        if existing_report:
            self.existing_report = existing_report
            self.report_id = existing_report.report_id
        else:
            self.existing_report = None
            self.report_id = uuid.uuid4()

        self.elem_id_to_border_title = {
            BugReportElemId.TITLE: (
                "[b]Bug Title",
                f"This is the title in {'Jira' if app_args.submitter == 'jira' else 'Launchpad'}",
            ),
            BugReportElemId.DESCRIPTION: (
                "[b]Bug Description",
                "Include all the details :)",
            ),
            BugReportElemId.ISSUE_FILE_TIME: (
                "[b]When was this issue filed?",
                "",
            ),
            BugReportElemId.PLATFORM_TAGS: ("[b]Platform Tags", ""),
            BugReportElemId.ASSIGNEE: ("[b]Assignee", ""),
            BugReportElemId.SEVERITY: ("[b]How bad is it?", ""),
            BugReportElemId.PROJECT: ("[b]Project Name", ""),
            BugReportElemId.ADDITIONAL_TAGS: ("[b]Additional Tags", ""),
            BugReportElemId.LOGS_TO_INCLUDE: (
                "[b]Select some logs to include",
                "Green = Selected",
            ),
            BugReportElemId.LP_STATUS: ("[b]Bug status on Launchpad", ""),
            BugReportElemId.IMPACTED_FEATURES: ("[b]Impacted Features", ""),
            BugReportElemId.IMPACTED_VENDORS: ("[b]Impacted Vendors", ""),
        }

        self.initial_report = {
            "Summary": "",
            "Steps to Reproduce": "",
            "Expected Result": "",
            "Actual Result": "",
            "Failure Rate": "",
            "Affected Test Cases": "",
            "Additional Information": "",
        }

        if job_id is NullSelection.NO_JOB:
            return

        self.initial_report["Affected Test Cases"] = job_id

        if session is not NullSelection.NO_SESSION:
            job_output = session.get_job_output(job_id)
            if job_output is None:
                self.initial_report["Job Output"] = "No output was found for this job"
                return

            # add an empty string at the end for a new line
            lines: list[str] = []
            for k in ("stdout", "stderr", "comments"):
                if len(job_output[k]) < MAX_JOB_OUTPUT_LEN:
                    lines.extend(
                        [
                            k,
                            "------",
                            job_output[k] or f"No {k} were found for this job",
                            "",
                        ]
                    )
                else:
                    lines.extend(
                        [
                            k,
                            "------",
                            f"{k} of this job is too long. It will be attached as a file.",
                            "",
                        ]
                    )

            self.initial_report["Job Output"] = "\n".join(lines)
        elif checkbox_submission is not NullSelection.NO_CHECKBOX_SUBMISSION:
            job_output = checkbox_submission.get_job_output(job_id)
            if not job_output:  # can be empty string
                self.initial_report["Job Output"] = "No output was found for this job"
                return
            self.initial_report["Job Output"] = job_output

    @override
    def compose(self) -> ComposeResult:
        yield SimpleHeader(Label(id="dirty_label"))
        with Collapsible(
            title=f"[bold]{'Jira' if self.app_args.submitter == 'jira' else 'Launchpad'} Bug Report for...[/bold]",
            collapsed=False,
            classes="nb",
            id="bug_report_metadata_header",
        ):
            if self.session != NullSelection.NO_SESSION:
                yield Label(f"- Test Plan: {self.session.testplan_id}")
            elif self.checkbox_submission is not NullSelection.NO_CHECKBOX_SUBMISSION:
                yield Label(f"- Test Plan: {self.checkbox_submission.base.testplan_id}")
            else:
                yield Label("- [$warning-darken-2]No session/submission selected")

            if self.job_id is NullSelection.NO_JOB:
                yield Label("- [$warning-darken-2]No job selected")
            else:
                yield Label(f"- Job ID: {self.job_id}")

        with VerticalScroll(classes="center"):
            yield Input(
                placeholder="Short title for this bug",
                id="title",
                classes="default_box",
                validators=[NonEmpty()],
            )

            with HorizontalGroup():
                yield DescriptionEditor(classes="ha", id="description", disabled=True)

                with VerticalGroup():
                    yield RadioSet(
                        *(
                            RadioButton(
                                display_name,
                                name=issue_file_time,
                                value=issue_file_time == "immediate",  # default val
                            )
                            for issue_file_time, display_name in pretty_issue_file_times.items()
                        ),
                        id="issue_file_time",
                        classes="default_box",
                    )
                    yield Input(
                        id="project",
                        placeholder="SOMERVILLE, STELLA, ...",
                        classes="default_box",
                        validators=[NoSpaces(), NonEmpty()],
                    )
                    yield Input(
                        id="platform_tags",
                        placeholder='Tags like "numbat-hello", space separated',
                        classes="default_box",
                        validators=[ValidSpaceSeparatedTags()],
                    )
                    yield Input(
                        id="additional_tags",
                        placeholder=f"Optional, extra {'Jira' if self.app_args.submitter == 'jira' else 'LP'} tags specific to the project",
                        classes="default_box",
                        validators=[ValidSpaceSeparatedTags()],
                    )
                    yield Input(
                        id="assignee",
                        placeholder=(
                            "Assignee's Jira Email"
                            if self.app_args.submitter == "jira"
                            else "Assignee's Launchpad ID"
                        ),
                        classes="default_box",
                    )

                    with HorizontalGroup():
                        highest_display_name = (
                            "Highest (Jira)"
                            if self.app_args.submitter == "jira"
                            else "Critical (LP)"
                        )
                        yield RadioSet(
                            *(
                                RadioButton(
                                    (
                                        highest_display_name
                                        if severity == "highest"
                                        else display_name
                                    ),
                                    name=severity,
                                    value=severity == "highest",  # default to critical
                                )
                                for severity, display_name in pretty_severities.items()
                            ),
                            id="severity",
                            classes="default_box",
                        )

                        cert_status_label = Label(
                            "Querying checkbox for the certification status (10s timeout)...",
                            id="cert_status_box",
                            classes="default_box",
                            disabled=True,
                        )
                        cert_status_label.border_title = "Certification Status"
                        yield cert_status_label

                    if self.session is NullSelection.NO_SESSION:
                        # don't even include the session collector if there's no session
                        collectors = [
                            c
                            for c in LOG_NAME_TO_COLLECTOR.values()
                            if c.name != "checkbox-session" and not c.hidden
                        ]
                    else:
                        collectors = [
                            c for c in LOG_NAME_TO_COLLECTOR.values() if not c.hidden
                        ]

                    with VerticalGroup():
                        yield SelectionList[LogName](
                            *(
                                Selection[LogName](
                                    collector.display_name,
                                    collector.name,
                                    self._should_select_collector_by_default(collector),
                                    id=collector.name,
                                    # disable nvidia collector
                                    # unless get_standard_info finds an nvidia card
                                    disabled=collector.name == "nvidia-bug-report",
                                )
                                for collector in sorted(
                                    collectors,
                                    key=lambda a: (
                                        # prioritize collect_by_default ones
                                        0
                                        if a.collect_by_default
                                        else 1
                                    ),
                                )
                            ),
                            classes="default_box",
                            id="logs_to_include",
                        )
                        yield Right(
                            Button(
                                "Clear",
                                compact=True,
                                tooltip="Clear log selection",
                                classes="editor_button mr1",
                                id="clear_log_selection",
                            )
                        )

            # always make it query-able, but visually hide it when not using lp
            yield RadioSet(
                *(
                    RadioButton(
                        status,
                        name=status,
                        value=status == "New",  # default to New
                    )
                    for status in BUG_STATUSES
                ),
                id="status",
                classes=("default_box" if self.app_args.submitter == "lp" else "hidden"),
            )

            yield SelectionWithPreview(
                FEATURE_MAP,
                Label("[i][$primary]These features will be tagged"),
                id="impacted_features",
                classes="default_box",
            )
            yield SelectionWithPreview(
                VENDOR_MAP,
                Label("[i][$primary]These vendors will be tagged"),
                id="impacted_vendors",
                classes="default_box",
            )

            yield Button(
                "Waiting for basic machine info to be collected...",
                id="submit_button",
                variant="success",
                disabled=True,
            )
        yield Footer()

    def on_mount(self):
        # this loop must happen
        for elem_id, border_titles in self.elem_id_to_border_title.items():
            elem = self.query_exactly_one(f"#{elem_id}")
            elem.border_title, elem.border_subtitle = border_titles
        # must launch the worker
        self.run_worker(
            get_standard_info,
            name=WorkerName.GET_STANDARD_INFO,
            exit_on_error=False,  # still allow editing
        )

        if self.job_id is NullSelection.NO_JOB:
            self.query_exactly_one("#cert_status_box", Label).display = False

        else:
            if self.checkbox_submission is not NullSelection.NO_CHECKBOX_SUBMISSION:
                self.run_worker(
                    lambda cbs=self.checkbox_submission, jid=self.job_id: cbs.get_job_cert_status(
                        jid
                    ),
                    name=WorkerName.SUBMISSION_CERT_STATUS,
                    thread=True,
                    exit_on_error=False,
                )

            elif self.session is not NullSelection.NO_SESSION:
                self.run_worker(
                    get_certification_status(
                        self.session.testplan_id, self.session.session_path
                    ),
                    name=WorkerName.GET_CERT_STATUS,
                    exit_on_error=False,
                )

        self.query_exactly_one(f"#{BugReportElemId.TITLE}", Input).focus()

        if self.existing_report is not None:
            self._restore_existing_report()
        else:
            # app_args values have lower precedence
            # use them only when there's no existing report
            self._prefill_with_app_args()

        if self.checkbox_submission is NullSelection.NO_CHECKBOX_SUBMISSION:
            selection_list = cast(
                SelectionList[LogName],
                self.query_exactly_one(
                    f"#{BugReportElemId.LOGS_TO_INCLUDE}", SelectionList
                ),
            )
            try:
                selection_list.remove_option("checkbox-submission")
            except OptionDoesNotExist:
                logger.warning("checkbox-submission collector doesn't exist")
        # TODO: select the severity button automatically when using a submission

    @work
    @on(Button.Pressed, "#submit_button")
    async def confirm_submit(self):
        ok = (
            await self.app.push_screen_wait(
                ConfirmScreen(
                    "Are you sure you want to submit this bug report?",
                    choices=(("Yes", "yes"), ("No", "no")),
                    focus_id_on_mount="no",
                )
            )
            == "yes"
        )
        if ok:
            self.dismiss(self._build_bug_report())

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

    @on(Input.Blurred)
    @on(Input.Changed)
    def show_invalid_reasons(self, event: Input.Changed) -> None:
        # dev time check, shouldn't happen in prod
        # this will immediately panic when typing inside a textbox if it's None
        # also makes the type checker happy
        elem_id = BugReportElemId(event.input.id)

        if event.validation_result is None:
            return

        if event.validation_result.is_valid:
            event.input.border_subtitle = self.elem_id_to_border_title.get(
                elem_id, ("", "")
            )[1]

        else:
            event.input.border_subtitle = " ".join(
                event.validation_result.failure_descriptions
            )

        self.validation_status = {
            **self.validation_status,
            elem_id: event.validation_result.is_valid,
        }

    @on(Input.Changed)
    @on(TextArea.Changed)
    @on(SelectionList.SelectedChanged)
    @on(RadioSet.Changed)
    def trigger_autosave(self):
        def f():
            # these steps are only executed when the real autosave happens
            # otherwise it's cancelled
            label = self.query_exactly_one("#dirty_label", Label)
            try:
                # filename is just a unix timestamp in seconds
                with open(AUTOSAVE_DIR / f"{self.report_id}.json", "w") as f:
                    report = self._build_bug_report()
                    d = asdict(report, dict_factory=BugReport.dict_factory)
                    if self.job_id is NullSelection.NO_JOB:
                        d["job_id"] = None
                    else:
                        d["job_id"] = self.job_id
                    json.dump(d, f)
                label.update("[green]Progress Saved")
            except Exception as e:
                logger.error(repr(e))
                label.update(f"[red]Autosave failed! {escape_markup(repr(e)[:20])}")

        # run auto save 0.5 seconds after the user stops typing
        self._debounce(lambda: self.run_worker(f, thread=True), 0.5)()

    @on(Button.Pressed, "#clear_log_selection")
    def clear_log_selection(self, _: Button.Pressed):
        self.query_exactly_one(
            f"#{BugReportElemId.LOGS_TO_INCLUDE}", SelectionList
        ).deselect_all()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if not event.worker.is_finished:
            return

        match event.worker.name:
            case WorkerName.GET_CERT_STATUS:
                self._get_cert_status_worker_callback(event)
            case WorkerName.GET_STANDARD_INFO:
                self._standard_info_worker_callback(event)
            case WorkerName.SUBMISSION_CERT_STATUS:
                self._get_submission_cert_status_worker_callback(event)
            case _:
                pass

    def watch_validation_status(self):
        btn = self.query_exactly_one("#submit_button", Button)
        btn.disabled = not all(self.validation_status.values())
        if btn.disabled:
            btn.label = (
                "Bug Report Incomplete (check if bug title or project name is empty)"
            )
        else:
            btn.label = "Submit Bug Report"

    def _standard_info_worker_callback(self, event: Worker.StateChanged):
        assert (
            event.worker.is_finished
        ), "Standard info callback invoked but the worker has not finished"

        textarea = self.query_exactly_one(
            f"#{BugReportElemId.DESCRIPTION}", DescriptionEditor
        )
        textarea.disabled = False  # unlock asap

        if event.worker.state == WorkerState.SUCCESS:
            # only write if basic info collection succeeded
            # this also implicitly achieves what on_mount does with app_args
            # since the values in self.initial_report is only used when there's no
            # existing report
            machine_info = cast(dict[str, str], event.worker.result)
            self.initial_report["Additional Information"] = "\n".join(
                [
                    f"CID: {self.app_args.cid or ''}",
                    f"SKU: {self.app_args.sku or ''}",
                    *(
                        (f"{k}: {v}" for k, v in machine_info.items())
                        # don't put the current machine's info when using a submission
                        if self.checkbox_submission
                        is NullSelection.NO_CHECKBOX_SUBMISSION
                        else []
                    ),
                ]
            )
            log_selection_list = cast(
                SelectionList[LogName],
                self.query_exactly_one(
                    f"#{BugReportElemId.LOGS_TO_INCLUDE}", SelectionList
                ),
            )
            # do not directly query the option by id, they don't exist in the DOM
            try:
                if "NVIDIA" in machine_info["GPU"] and NVIDIA_BUG_REPORT_PATH.exists():
                    # include nvidia logs by default IF we actually have it
                    log_selection_list.enable_option("nvidia-bug-report")
                    log_selection_list.select("nvidia-bug-report")
                else:
                    # disable the nvidia log collector if there's no nvidia card
                    log_selection_list.remove_option("nvidia-bug-report")
            except OptionDoesNotExist:
                logger.warning("nvidia-bug-report collector doesn't exist")

        else:
            log_selection_list = cast(
                SelectionList[LogName],
                self.query_exactly_one(
                    f"#{BugReportElemId.LOGS_TO_INCLUDE}", SelectionList
                ),
            )

            try:
                if NVIDIA_BUG_REPORT_PATH.exists():
                    log_selection_list.enable_option("nvidia-bug-report")
                else:
                    log_selection_list.remove_option("nvidia-bug-report")
            except OptionDoesNotExist:
                logger.warning("nvidia-bug-report collector doesn't exist")

            # still put these in
            self.initial_report["Additional Information"] = "\n".join(
                [
                    f"CID: {self.app_args.cid or ''}",
                    f"SKU: {self.app_args.sku or ''}",
                ]
            )

            self.notify(
                title="Failed to collect basic machine info",
                message=str(event.worker.error),
                timeout=180,
            )

        if self.existing_report is None:
            # only overwrite the textarea if there's no existing report
            textarea.text = "\n".join(
                f"[{k}]\n" + v + ("\n" if v else "")
                for k, v in self.initial_report.items()
            )

    def _get_cert_status_worker_callback(self, event: Worker.StateChanged):
        assert self.job_id is not NullSelection.NO_JOB
        assert (
            event.worker.is_finished
        ), "Cert status callback invoked but the worker has not finished"

        cert_status_box = self.query_exactly_one("#cert_status_box", Label)

        if event.worker.result is None:
            cert_status_box.update("Unable to determine cert status")
            return

        if event.worker.state == WorkerState.ERROR:
            cert_status_box.update(f"Failed to get cert status. {event.worker.error}")
            return

        all_cert_statuses = cast(
            dict[str, TestCaseWithCertStatus],
            event.worker.result,
        )
        self._color_cert_status_box(all_cert_statuses[self.job_id].cert_status)

    def _get_submission_cert_status_worker_callback(self, event: Worker.StateChanged):
        assert self.job_id is not NullSelection.NO_JOB
        assert (
            event.worker.is_finished
        ), "Submission cert status callback invoked but the worker has not finished"

        if event.worker.state == WorkerState.SUCCESS:
            assert event.worker.result in CERT_STATUSES
            self._color_cert_status_box(event.worker.result)
        else:
            logger.error(f"Cert status worker error {event.worker.error}")
            logger.error(f"Cert status worker state {event.worker.state}")
            self.query_exactly_one("#cert_status_box", Label).update(
                "Unable to determine cert status"
            )

    def _build_bug_report(self) -> BugReport:
        selected_severity_button = self.query_exactly_one(
            f"#{BugReportElemId.SEVERITY}", RadioSet
        ).pressed_button
        selected_issue_file_time_button = self.query_exactly_one(
            f"#{BugReportElemId.ISSUE_FILE_TIME}", RadioSet
        ).pressed_button
        selected_status_button = self.query_exactly_one(
            f"#{BugReportElemId.LP_STATUS}", RadioSet
        ).pressed_button

        # shouldn't fail at runtime, major logic error if they do
        assert selected_severity_button
        assert selected_severity_button.name in SEVERITIES
        assert selected_issue_file_time_button
        assert selected_issue_file_time_button.name in ISSUE_FILE_TIMES
        assert selected_status_button
        assert selected_status_button.name in BUG_STATUSES

        return BugReport(
            report_id=self.report_id,
            title=self.query_exactly_one(
                f"#{BugReportElemId.TITLE}", Input
            ).value.strip(),
            checkbox_session=(
                None if self.session is NullSelection.NO_SESSION else self.session
            ),
            checkbox_submission=(
                None
                if self.checkbox_submission is NullSelection.NO_CHECKBOX_SUBMISSION
                else self.checkbox_submission
            ),
            job_id=self.job_id if type(self.job_id) is str else None,
            description=self.query_exactly_one(
                f"#{BugReportElemId.DESCRIPTION}", DescriptionEditor
            ).text.strip(),
            assignee=self.query_exactly_one(
                f"#{BugReportElemId.ASSIGNEE}", Input
            ).value.strip(),
            project=self.query_exactly_one(
                f"#{BugReportElemId.PROJECT}", Input
            ).value.strip(),
            severity=selected_severity_button.name,
            status=selected_status_button.name,
            issue_file_time=selected_issue_file_time_button.name,
            additional_tags=self.query_exactly_one(
                f"#{BugReportElemId.ADDITIONAL_TAGS}", Input
            )
            .value.strip()
            .split(),
            platform_tags=self.query_exactly_one(
                f"#{BugReportElemId.PLATFORM_TAGS}", Input
            )
            .value.strip()
            .split(),
            impacted_features=self.query_exactly_one(
                f"#{BugReportElemId.IMPACTED_FEATURES}", SelectionWithPreview
            ).selected_values,
            impacted_vendors=self.query_exactly_one(
                f"#{BugReportElemId.IMPACTED_VENDORS}", SelectionWithPreview
            ).selected_values,
            logs_to_include=cast(
                SelectionList[LogName],
                self.query_exactly_one(
                    f"#{BugReportElemId.LOGS_TO_INCLUDE}", SelectionList
                ),
            ).selected,
            source=self.existing_report and self.existing_report.source or "editor",
        )

    def _prefill_with_app_args(self):
        if self.app_args.assignee:
            self.query_exactly_one(f"#{BugReportElemId.ASSIGNEE}", Input).value = (
                self.app_args.assignee
            )
        if self.app_args.project:
            self.query_exactly_one(f"#{BugReportElemId.PROJECT}", Input).value = (
                self.app_args.project
            )
        if len(self.app_args.platform_tags) > 0:
            self.query_exactly_one(f"#{BugReportElemId.PLATFORM_TAGS}", Input).value = (
                " ".join(self.app_args.platform_tags)
            )
        if len(self.app_args.tags) > 0:
            self.query_exactly_one(
                f"#{BugReportElemId.ADDITIONAL_TAGS}", Input
            ).value = " ".join(self.app_args.tags)

    def _restore_existing_report(self):
        if not self.existing_report:
            return

        # restore existing report, take over the CLI values
        for elem_id in self.elem_id_to_border_title:
            elem = self.query_exactly_one(f"#{elem_id}")

            if not hasattr(self.existing_report, elem_id):
                logger.warning(f"No such attribute in BugReport: {elem_id}")
                continue

            match elem:
                case Input():
                    report_value = cast(
                        list[str] | str, getattr(self.existing_report, elem_id)
                    )
                    if isinstance(report_value, list):
                        elem.value = " ".join(map(str, report_value))
                    else:
                        elem.value = str(report_value)
                case DescriptionEditor():
                    elem.text = self.existing_report.get_with_type(elem_id, str)
                    # don't wait for the info collector, immediately enable
                    # and allow editing
                    elem.disabled = False
                case RadioSet():
                    selected_name = self.existing_report.get_with_type(elem_id, str)
                    for child in elem.children:
                        if (
                            isinstance(child, RadioButton)
                            and child.name == selected_name
                        ):
                            child.action_toggle_button()
                case SelectionWithPreview():
                    value = cast(
                        list[str],
                        self.existing_report.get_with_type(elem_id, list),
                    )
                    elem.restore_selection(value)
                case SelectionList():
                    elem_value = cast(
                        list[str],
                        self.existing_report.get_with_type(elem_id, list),
                    )
                    elem.deselect_all()  # clear first, then recover

                    for v in elem_value:
                        # if v in elem.options:
                        try:
                            elem.select(elem.get_option(v))
                            # cast(SelectionList[str], elem).select(str(v))
                        except OptionDoesNotExist:
                            logger.warning("Ignoring option", v)
                case _:
                    pass

    def _should_select_collector_by_default(self, collector: LogCollector) -> bool:
        return (
            self.checkbox_submission is NullSelection.NO_CHECKBOX_SUBMISSION
            and collector.collect_by_default
        ) or (
            self.checkbox_submission is not NullSelection.NO_CHECKBOX_SUBMISSION
            and collector.collect_by_default
            and collector.name == "checkbox-submission"
        )

    def _color_cert_status_box(self, cert_status: CertificationStatus):
        cert_status_box = self.query_exactly_one("#cert_status_box", Label)
        if cert_status == "blocker":
            cert_status_box.update(
                "\n".join(
                    [
                        "[$error]Blocker[/]",
                        "Suggested severity: Highest/Critical",
                    ]
                )
            )
            cert_status_box.styles.border = (
                "round",
                self.app.theme_variables["error"],
            )
        elif cert_status == "non-blocker":
            cert_status_box.update(
                "\n".join(
                    [
                        "[$warning]Non-Blocker[/]",
                        "Suggested severity: High",
                    ]
                )
            )
            cert_status_box.styles.border = (
                "round",
                self.app.theme_variables["warning"],
            )
