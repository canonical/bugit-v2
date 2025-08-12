import shutil
import subprocess
from collections.abc import Mapping
from typing import Final, cast, final

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import HorizontalGroup, VerticalGroup, VerticalScroll
from textual.reactive import var
from textual.screen import Screen
from textual.validation import Length, ValidationResult, Validator
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    RadioButton,
    RadioSet,
    SelectionList,
    TextArea,
)
from typing_extensions import override

from bugit_v2.checkbox_utils import Session
from bugit_v2.components.confirm_dialog import ConfirmScreen
from bugit_v2.components.selection_with_preview import SelectionWithPreview
from bugit_v2.dut_utils.info_getters import get_standard_info
from bugit_v2.dut_utils.log_collectors import LOG_NAME_TO_COLLECTOR, LogName
from bugit_v2.models.bug_report import (
    ISSUE_FILE_TIMES,
    SEVERITIES,
    BugReport,
    pretty_issue_file_times,
    pretty_severities,
)
from bugit_v2.utils.constants import FEATURE_MAP, VENDOR_MAP


class ValidSpaceSeparatedTags(Validator):
    @override
    def validate(self, value: str) -> ValidationResult:
        """Check a string is equal to its reverse."""
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
        """Check a string is equal to its reverse."""
        if " " in value.strip():
            return self.failure("Can't have spaces here")
        else:
            return self.success()


@final
class BugReportScreen(Screen[BugReport]):
    session: Final[Session]
    job_id: Final[str]
    initial_report: dict[str, str]
    # ELEM_ID_TO_BORDER_TITLE[id] = (title, subtitle)
    # id should match the property name in the BugReport object
    # TODO: rename this, it does more than just holding titles now
    ELEM_ID_TO_BORDER_TITLE: Final[Mapping[str, tuple[str, str]]] = {
        "title": ("[b]Bug Title", "This is the title in Jira/Launchpad"),
        "description": (
            "[b]Bug Description",
            "Include all the details :)",
        ),
        "issue_file_time": ("[b]When was this issue filed?", ""),
        "platform_tags": ("[b]Platform Tags", ""),
        "assignee": ("[b]Assignee", ""),
        "severity": ("[b]How bad is it?", ""),
        "project": ("[b]Project Name", ""),
        "additional_tags": ("[b]Additional Tags", ""),
        "logs_to_include": ("[b]Select some logs to include", ""),
        "impacted_features": ("[b]Impacted Features", ""),
        "impacted_vendors": ("[b]Impacted Vendors", ""),
    }

    CSS = """
    BugReportScreen {
        width: 100%;
        height: 100%;
    }
    """
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("m", "toggle_metadata_display", "Toggle Metadata Display")
    ]

    # inputs that have validators
    validation_status = var(
        {"title": False, "platform_tags": True, "project": False}
    )

    def __init__(
        self,
        session: Session,
        job_id: str,
        existing_report: BugReport | None = None,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.session = session
        self.job_id = job_id
        self.existing_report = existing_report
        self.machine_info = get_standard_info()  # slow
        self.initial_report = {
            "Summary": "",
            "Steps to reproduce": "",
            "Expected result": "",
            "Actual result": "",
            "Failure rate": "",
            "Affected test cases": "",
            "Additional Information": "\n".join(
                f"{k}: {v}" for k, v in self.machine_info.items()
            ),
        }

    @override
    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalGroup(classes="lrp2 boxed lrm1", id="bug_metadata"):
            # stick to the top
            yield Label("[bold]Bug Report for...[/bold]")
            yield Label(f"- Job ID: {self.job_id}")
            yield Label(f"- Test Plan: {self.session.testplan_id}")

        with VerticalScroll(classes="center lrm1"):
            yield Input(
                placeholder="Short title for this bug",
                id="title",
                classes="default_box",
                validators=[Length(1)],
            )

            with HorizontalGroup():
                with VerticalGroup(id="bug_report_description"):
                    yield TextArea(
                        "\n".join(
                            f"[{k}]\n" + v
                            for k, v in self.initial_report.items()
                        ),
                        classes="default_box",
                        show_line_numbers=True,
                        soft_wrap=False,
                        id="description",
                    )
                    yield HorizontalGroup(
                        Button(
                            "Hide Line Numbers",
                            id="show_line_numbers_toggle",
                            compact=True,
                            classes="editor_button",
                        ),
                        Button(
                            "Enable Wrap",
                            id="wrap_text_toggle",
                            compact=True,
                            classes="editor_button",
                            tooltip="Wrap long lines around so you don't need to scroll to see them",
                        ),
                        Button(
                            "Copy All",
                            id="copy_to_clipboard",
                            compact=True,
                            classes="editor_button wa",
                            tooltip="Copy the entire description to the system clipboard",
                        ),
                        classes="right",
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
                        id="project",
                        placeholder="SOMERVILLE, STELLA, ...",
                        classes="default_box",
                        validators=[NoSpaces(), Length(1)],
                    )
                    yield Input(
                        id="platform_tags",
                        placeholder='Tags like "numbat-hello", space separated',
                        classes="default_box",
                        validators=[ValidSpaceSeparatedTags()],
                    )
                    yield Input(
                        id="additional_tags",
                        placeholder="Optional, extra Jira/LP tags specific to the project",
                        classes="default_box",
                        validators=[ValidSpaceSeparatedTags()],
                    )
                    yield Input(
                        id="assignee",
                        placeholder="Email for Jira, Launchpad ID for Launchpad",
                        classes="default_box",
                    )
                    yield RadioSet(
                        *(
                            RadioButton(
                                display_name,
                                name=severity,
                                value=severity
                                == "highest",  # default to critical
                            )
                            for severity, display_name in pretty_severities.items()
                        ),
                        id="severity",
                        classes="default_box",
                    )
                    yield SelectionList[str](
                        *(
                            (
                                collector.display_name,
                                collector.name,
                                collector.collect_by_default,
                            )
                            for collector in LOG_NAME_TO_COLLECTOR.values()
                        ),
                        classes="default_box",
                        id="logs_to_include",
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
                "Submit Bug Report",
                id="submit_button",
                variant="success",
                disabled=True,
            )
        yield Footer()

    def on_mount(self):
        for elem_id, border_titles in self.ELEM_ID_TO_BORDER_TITLE.items():
            elem = self.query_exactly_one(f"#{elem_id}")
            elem.border_title, elem.border_subtitle = border_titles
            # restore existing report
            if not self.existing_report:
                continue

            if not hasattr(self.existing_report, elem_id):
                self.log.warning(f"No such attribute in BugReport: {elem_id}")
                continue

            match elem:
                case Input():
                    report_value = getattr(self.existing_report, elem_id)
                    if isinstance(report_value, list):
                        elem.value = " ".join(map(str, report_value))
                    else:
                        elem.value = str(report_value)
                case TextArea():
                    elem.text = self.existing_report.get_with_type(
                        elem_id, str
                    )
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

        self.query_exactly_one("#title", Input).focus()

    @on(Button.Pressed, "#copy_to_clipboard")
    def copy_to_clipboard(self):
        if not shutil.which("xclip"):
            self.notify(
                (
                    "Run [bold]sudo apt install xclip[/bold] "
                    "to enable copying to system clipboard "
                    "(click this message to dismiss)"
                ),
                title="xclip is not installed.",
                timeout=5,
                severity="warning",
            )
            return

        content = self.query_exactly_one(TextArea).text
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            check=False,
            input=content.encode(),
        )
        btn = self.query_exactly_one("#copy_to_clipboard", Button)
        old_label = btn.label
        btn.label = "Copied!"

        def f():
            btn.label = old_label

        self.set_timer(3, f)

    @on(Button.Pressed, "#wrap_text_toggle")
    def toggle_wrap(self):
        text_area = self.query_exactly_one(TextArea)
        btn = self.query_exactly_one("#wrap_text_toggle", Button)
        text_area.soft_wrap = not text_area.soft_wrap
        btn.label = "Disable Wrap" if text_area.soft_wrap else "Enable Wrap"

    @on(Button.Pressed, "#show_line_numbers_toggle")
    def toggle_line_number(self):
        text_area = self.query_exactly_one(TextArea)
        btn = self.query_exactly_one("#show_line_numbers_toggle", Button)
        text_area.show_line_numbers = not text_area.show_line_numbers
        btn.label = (
            "Hide Line Numbers"
            if text_area.show_line_numbers
            else "Show Line Numbers"
        )

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
            event.input.border_subtitle = self.ELEM_ID_TO_BORDER_TITLE.get(
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

    def watch_validation_status(self):
        self.query_exactly_one("#submit_button", Button).disabled = not all(
            self.validation_status.values()
        )

    def action_toggle_metadata_display(self) -> None:
        old_state = self.query_exactly_one(
            "#bug_metadata", VerticalGroup
        ).display
        self.query_exactly_one("#bug_metadata", VerticalGroup).display = (
            not old_state
        )

    def _build_bug_report(self) -> BugReport:
        selected_severity_button = self.query_exactly_one(
            "#severity", RadioSet
        ).pressed_button
        selected_issue_file_time_button = self.query_exactly_one(
            "#issue_file_time", RadioSet
        ).pressed_button

        # shouldn't fail at runtime
        assert selected_severity_button
        assert selected_severity_button.name in SEVERITIES
        assert selected_issue_file_time_button
        assert selected_issue_file_time_button.name in ISSUE_FILE_TIMES

        return BugReport(
            title=self.query_exactly_one("#title", Input).value,
            description=self.query_exactly_one("#description", TextArea).text,
            assignee=self.query_exactly_one("#assignee", Input).value,
            project=self.query_exactly_one("#project", Input).value,
            severity=selected_severity_button.name,
            issue_file_time=selected_issue_file_time_button.name,
            additional_tags=self.query_exactly_one("#additional_tags", Input)
            .value.strip()
            .split(),
            platform_tags=self.query_exactly_one("#platform_tags", Input)
            .value.strip()
            .split(),
            impacted_features=self.query_exactly_one(
                "#impacted_features", SelectionWithPreview
            ).selected_values,
            impacted_vendors=self.query_exactly_one(
                "#impacted_vendors", SelectionWithPreview
            ).selected_values,
            logs_to_include=cast(
                SelectionList[LogName],
                self.query_exactly_one("#logs_to_include", SelectionList),
            ).selected,
        )
