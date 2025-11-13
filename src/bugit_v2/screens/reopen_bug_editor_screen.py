from collections.abc import Mapping
from typing import Final, Literal, cast, final

from textual import on, work
from textual.app import ComposeResult
from textual.containers import HorizontalGroup, VerticalGroup, VerticalScroll
from textual.reactive import var
from textual.screen import Screen
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
)
from textual.widgets.selection_list import Selection
from textual.worker import Worker, WorkerState
from typing_extensions import override

from bugit_v2.checkbox_utils import Session
from bugit_v2.components.confirm_dialog import ConfirmScreen
from bugit_v2.components.description_editor import DescriptionEditor
from bugit_v2.components.header import SimpleHeader
from bugit_v2.components.selection_with_preview import SelectionWithPreview
from bugit_v2.dut_utils.info_getters import get_standard_info
from bugit_v2.dut_utils.log_collectors import LOG_NAME_TO_COLLECTOR
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.bug_report import (
    ISSUE_FILE_TIMES,
    LogName,
    PartialBugReport,
    pretty_issue_file_times,
)
from bugit_v2.utils.constants import NullSelection


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
class ReopenBugEditorScreen(Screen[PartialBugReport]):
    session: Final[Session | Literal[NullSelection.NO_SESSION]]
    job_id: Final[str | Literal[NullSelection.NO_JOB]]
    existing_report: Final[PartialBugReport | None]
    app_args: Final[AppArgs]
    elem_id_to_border_title: Final[Mapping[str, tuple[str, str]]]

    initial_report: dict[str, str]

    # ELEM_ID_TO_BORDER_TITLE[id] = (title, subtitle)
    # id should match the property name in the BugReport object
    # TODO: rename this, it does more than just holding titles now

    CSS = """
    ReopenBugEditorScreen {
        width: 100%;
        height: 100%;
    }

    #submit_button {
        width: 100%;
        padding: 0;
        margin-top: 1;
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
    """
    CSS_PATH = "styles.tcss"

    # inputs that have validators
    # the keys should appear in elem_id_to_border_title
    validation_status = var({"platform_tags": True, "tags": True})

    def __init__(
        self,
        session: Session | Literal[NullSelection.NO_SESSION],
        job_id: str | Literal[NullSelection.NO_JOB],
        app_args: AppArgs,
        existing_report: PartialBugReport | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.session = session
        self.job_id = job_id
        self.existing_report = existing_report
        self.app_args = app_args

        self.elem_id_to_border_title = {
            "description": (
                "[b]Bug Description",
                "Include all the details :)",
            ),
            "issue_file_time": ("[b]When was this issue filed?", ""),
            "platform_tags": ("[b]Platform Tags", ""),
            "assignee": ("[b]Assignee", ""),
            "additional_tags": ("[b]Additional Tags", ""),
            "logs_to_include": (
                "[b]Select some logs to include",
                "Green = Selected",
            ),
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

        if session is NullSelection.NO_SESSION:
            return

        if job_id is NullSelection.NO_JOB:
            return

        self.initial_report["Affected Test Cases"] = job_id
        job_output = session.get_job_output(job_id)
        if job_output is None:
            self.initial_report["Job Output"] = (
                "No output was found for this job"
            )
            return

        # add an empty string at the end for a new line
        lines: list[str] = []
        for k in ("stdout", "stderr", "comments"):
            lines.extend(
                [
                    k,
                    "------",
                    job_output[k] or f"No {k} were found for this job",
                    "",
                ]
            )

        self.initial_report["Job Output"] = "\n".join(lines)

    @override
    def compose(self) -> ComposeResult:
        yield SimpleHeader()
        with Collapsible(
            title=f"[bold]Reopen {'Jira' if self.app_args.submitter == 'jira' else 'Launchpad'} Bug Report for...[/bold]",
            collapsed=False,
            classes="nb",
            id="bug_report_metadata_header",
        ):
            if self.session is NullSelection.NO_SESSION:
                yield Label("- [$warning-darken-2]No session selected")
            else:
                yield Label(f"- Test Plan: {self.session.testplan_id}")

            if self.job_id is NullSelection.NO_JOB:
                yield Label("- [$warning-darken-2]No job selected")
            else:
                yield Label(f"- Job ID: {self.job_id}")

        with VerticalScroll(classes="center"):
            with HorizontalGroup():
                yield DescriptionEditor(
                    classes="ha", id="description", disabled=True
                )

                with VerticalGroup():
                    yield RadioSet(
                        *(
                            RadioButton(
                                display_name,
                                name=issue_file_time,
                                value=issue_file_time
                                == "immediate",  # default val
                            )
                            for issue_file_time, display_name in pretty_issue_file_times.items()
                        ),
                        id="issue_file_time",
                        classes="default_box",
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

                    if self.session is NullSelection.NO_SESSION:
                        # don't even include the session collector if there's no session
                        collectors = [
                            c
                            for c in LOG_NAME_TO_COLLECTOR.values()
                            if c.name != "checkbox-session"
                        ]
                    else:
                        collectors = [
                            c for c in LOG_NAME_TO_COLLECTOR.values()
                        ]

                    yield SelectionList[LogName](
                        *(
                            Selection[LogName](
                                collector.display_name,
                                collector.name,
                                collector.collect_by_default,
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
            name=get_standard_info.__name__,
            thread=True,
            exit_on_error=False,  # still allow editing
        )

        # fill the app_args values first, they have lower precedence
        self._prefill_with_app_args()

        if not self.existing_report:
            return

        # restore existing report, take over the CLI values
        self._restore_existing_report()

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

    @on(Input.Blurred)
    @on(Input.Changed)
    def show_invalid_reasons(self, event: Input.Changed) -> None:
        # dev time check, shouldn't happen in prod
        # this will immediately panic when typing inside a textbox if it's None
        assert event.input.id

        if event.validation_result is None:
            return

        if event.validation_result.is_valid:
            event.input.border_subtitle = self.elem_id_to_border_title.get(
                event.input.id, ("", "")
            )[1]

        else:
            event.input.border_subtitle = " ".join(
                event.validation_result.failure_descriptions
            )

        self.validation_status = {
            **self.validation_status,
            event.input.id: event.validation_result.is_valid,
        }

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if (
            not event.worker.is_finished
            or event.worker.name != get_standard_info.__name__
        ):
            return

        textarea = self.query_exactly_one("#description", DescriptionEditor)
        textarea.disabled = False

        if event.worker.state != WorkerState.SUCCESS:
            self.notify(
                title="Failed to collect basic machine info",
                message=repr(event.worker.error),
            )
            return

        # only write if basic info collection succeeded
        # this also implicitly achieves what on_mount does with app_args
        # since the values in self.initial_report is only used when there's no
        # existing report
        machine_info = cast(dict[str, str], event.worker.result)
        self.initial_report["Additional Information"] = "\n".join(
            [
                f"CID: {self.app_args.cid or ''}",
                f"SKU: {self.app_args.sku or ''}",
                *(f"{k}: {v}" for k, v in machine_info.items()),
            ]
        )

        if self.existing_report is None:
            # only overwrite the textarea if there's no existing report
            textarea.text = "\n".join(
                f"[{k}]\n" + v + ("\n" if v else "")
                for k, v in self.initial_report.items()
            )

        log_selection_list = cast(
            SelectionList[LogName],
            self.query_exactly_one("#logs_to_include", SelectionList),
        )
        # do not directly query the option by id, they don't exist in the DOM
        if "NVIDIA" in machine_info["GPU"]:
            # include nvidia logs by default IF we actually have it
            log_selection_list.enable_option("nvidia-bug-report")
            log_selection_list.select("nvidia-bug-report")
        else:
            # disable the nvidia log collector if there's no nvidia card
            log_selection_list.remove_option("nvidia-bug-report")

    def watch_validation_status(self):
        btn = self.query_exactly_one("#submit_button", Button)
        btn.disabled = not all(self.validation_status.values())
        if btn.disabled:
            btn.label = "Bug Report Incomplete (check if bug title or project name is empty)"
        else:
            btn.label = "Submit Bug Report"

    def _build_bug_report(self) -> PartialBugReport:
        selected_issue_file_time_button = self.query_exactly_one(
            "#issue_file_time", RadioSet
        ).pressed_button

        # shouldn't fail at runtime, major logic error if they do
        assert selected_issue_file_time_button
        assert selected_issue_file_time_button.name in ISSUE_FILE_TIMES

        return PartialBugReport(
            checkbox_session=(
                None
                if self.session is NullSelection.NO_SESSION
                else self.session
            ),
            description=self.query_exactly_one(
                "#description", DescriptionEditor
            ).text.strip(),
            assignee=self.query_exactly_one("#assignee", Input).value.strip(),
            issue_file_time=selected_issue_file_time_button.name,
            additional_tags=self.query_exactly_one("#additional_tags", Input)
            .value.strip()
            .split(),
            platform_tags=self.query_exactly_one("#platform_tags", Input)
            .value.strip()
            .split(),
            logs_to_include=cast(
                SelectionList[LogName],
                self.query_exactly_one("#logs_to_include", SelectionList),
            ).selected,
        )

    def _prefill_with_app_args(self):
        if self.app_args.assignee:
            self.query_exactly_one("#assignee", Input).value = (
                self.app_args.assignee
            )
        if len(self.app_args.platform_tags) > 0:
            self.query_exactly_one("#platform_tags", Input).value = " ".join(
                self.app_args.platform_tags
            )
        if len(self.app_args.tags) > 0:
            self.query_exactly_one("#additional_tags", Input).value = " ".join(
                self.app_args.tags
            )

    def _restore_existing_report(self):
        if not self.existing_report:
            return

        # restore existing report, take over the CLI values
        for elem_id in self.elem_id_to_border_title:
            elem = self.query_exactly_one(f"#{elem_id}")

            if not hasattr(self.existing_report, elem_id):
                self.log.warning(f"No such attribute in BugReport: {elem_id}")
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
                    elem.text = self.existing_report.get_with_type(
                        elem_id, str
                    )
                    # don't wait for the info collector, immediately enable
                    # and allow editing
                    elem.disabled = False
                case RadioSet():
                    selected_name = self.existing_report.get_with_type(
                        elem_id, str
                    )
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
                    for v in elem_value:
                        try:
                            cast(SelectionList[str], elem).select(str(v))
                        except Exception:
                            pass
                case _:
                    pass
