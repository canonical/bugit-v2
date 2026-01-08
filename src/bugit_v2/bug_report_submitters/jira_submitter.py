"""
Implements the concrete Jira submitter that submits a bug report to Jira.
"""

import json
import logging
import os
from collections.abc import Generator, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast, final, override

from jira import JIRA, Issue, JIRAError
from jira.resources import Component
from textual import on
from textual.app import ComposeResult
from textual.containers import Center, VerticalGroup
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.models.bug_report import (
    BugReport,
    PartialBugReport,
    Severity,
    pretty_issue_file_times,
)

logger = logging.getLogger(__name__)
JIRA_SERVER_ADDRESS = os.getenv("JIRA_SERVER", "https://warthogs.atlassian.net")


@dataclass(slots=True, frozen=True)
class JiraBasicAuth:
    email: str
    token: str


@final
class JiraAuthModal(ModalScreen[tuple[JiraBasicAuth, bool] | None]):
    auth: JiraBasicAuth | None = None

    CSS = """
    JiraAuthModal {
        align: center middle;
        background: $background 100%;
    }

    #top_level_container {
        padding: 0 5;
    }

    JiraAuthModal Input {
        border: round $boost 700%;
        background: $background 100%;
    }

    JiraAuthModal Checkbox {
        border: round $boost 700%;
        background: $background 100%;
    }

    JiraAuthModal Input:focus-within {
        border: round $primary;
    }

    JiraAuthModal Checkbox:focus-within {
        border: round $primary;
    }
    """

    @override
    def compose(self) -> ComposeResult:
        with VerticalGroup(id="top_level_container"):
            yield Label(f"[b][$primary]Jira Authentication for {JIRA_SERVER_ADDRESS}")
            yield Input(placeholder="your.email@jira.com", id="email")
            yield Input(
                placeholder="A token can be created at the link below if you don't already have one",
                id="token",
            )
            yield Label("https://id.atlassian.com/manage-profile/security/api-tokens")
            yield Checkbox(
                "Cache valid credentials until next boot",
                tooltip=(
                    "Save the credentials to /tmp so you don't need to "
                    "type the credentials over and over again. They are erased "
                    "at the next boot, or you can manually delete them"
                ),
                value=False,
            )
            yield Center(Button("Continue", id="continue_button", disabled=True))

    def on_mount(self):
        self.query_exactly_one("#top_level_container").border_title = (
            "Jira Authentication"
        )

        email_input = self.query_exactly_one("#email")
        email_input.border_title = "Email"
        email_input.border_subtitle = "Use Ctrl+Shift+V to paste into the textbox"

        token_input = self.query_exactly_one("#token")
        token_input.border_title = "Jira Access Token"
        token_input.border_subtitle = "Use Ctrl+Shift+V to paste into the textbox"

    @on(Input.Blurred)
    @on(Input.Changed)
    def update_auth(self, _) -> None:
        email = self.query_exactly_one("#email", Input).value
        token = self.query_exactly_one("#token", Input).value

        if email and token:
            self.auth = JiraBasicAuth(email.strip(), token.strip())
            self.query_exactly_one(Button).disabled = False
        else:
            self.auth = None
            self.query_exactly_one(Button).disabled = True

    @on(Button.Pressed, "#continue_button")
    def exit_widget(self) -> None:
        if not self.auth:
            self.dismiss(None)
        else:
            self.dismiss((self.auth, self.query_exactly_one(Checkbox).value))


class JiraSubmitterError(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)


@final
class JiraSubmitter(BugReportSubmitter[JiraBasicAuth, None]):
    name = "jira_submitter"
    display_name = "Jira"
    steps = 5

    jira: JIRA | None = None
    auth_modal = JiraAuthModal
    auth: JiraBasicAuth | None = None
    issue: Issue | None = None

    # map the severity value inside the app to the ones on Jira
    severity_name_map: Mapping[Severity, str] = {
        "highest": "Highest",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "lowest": "Lowest",
    }

    allow_parallel_upload = True

    def project_exists(self, project_name: str) -> None:
        """Does the project exist?

        :param project_name:
            name of the project, usually the name at the end
            of the URL:
            https://hostname.com/jira/software/c/projects/NAME
        :return: true if the project exists
        """
        assert self.jira, "Jira client is not initialized"
        try:
            self.jira.project(id=project_name)
        except Exception:
            raise JiraSubmitterError(f"Project '{project_name}' doesn't exist!")

    def assignee_exists_and_unique(self, assignee: str) -> str:
        """Does @param assignee exist and is it unique?

        :param assignee: the email of the assignee or some form of ID
        :return: exists and unique
        """
        assert self.jira, "Jira client is not initialized"

        query_result = self.jira.search_users(query=assignee)
        if len(query_result) == 0:
            raise JiraSubmitterError(f"Assignee '{assignee}' doesn't exist!")
        elif len(query_result) > 1:
            raise JiraSubmitterError(f"Assignee '{assignee}' isn't unique!")

        # this field exists, but not listed in the jira library
        return query_result[0].accountId  # pyright: ignore[reportAny]

    def all_components_exist(self, project: str, components: Sequence[str]) -> None:
        assert self.jira, "Jira client is not initialized"
        # the @translate_args decorator confuses the type checker
        query_result = cast(list[Component], self.jira.project_components(project))
        for wanted_component in components:
            if not any(
                actual_component.name  # str  # pyright: ignore[reportAny]
                # apparently .name exists, but the library didn't declare it
                == wanted_component
                for actual_component in query_result
            ):
                raise JiraSubmitterError(
                    f"Component '{wanted_component}' doesn't exist in {project}!"
                )

    @override
    def bug_exists(self, bug_id: str) -> bool:
        assert self.auth

        try:
            if not self.jira:
                self.jira = JIRA(
                    server=JIRA_SERVER_ADDRESS,
                    basic_auth=(self.auth.email, self.auth.token),
                    validate=True,
                )
                if self.allow_cache_credentials:
                    with open(f"/tmp/{self.name}-credentials.json", "w") as f:
                        json.dump(asdict(self.auth), f)
            self.jira.issue(bug_id)
            return True
        except JIRAError as e:
            logger.error(e)
            return False

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage, None, None]:
        issue_file_time_block = (
            f"[Stage]\n{pretty_issue_file_times[bug_report.issue_file_time]}"
        )

        # final submit
        bug_dict = {
            "assignee": bug_report.assignee,
            "project": bug_report.project,
            "summary": bug_report.title,
            "description": bug_report.description + "\n\n" + issue_file_time_block,
            "components": [{"name": tag} for tag in bug_report.platform_tags],
            "labels": [
                *bug_report.additional_tags,
                *bug_report.impacted_vendors,
                *bug_report.impacted_features,
            ],
            "priority": {"name": self.severity_name_map[bug_report.severity]},
            "issuetype": {"name": "Bug"},
        }

        assert self.auth, "Missing auth credentials"
        assert JIRA_SERVER_ADDRESS, "JIRA_SERVER is not specified!"

        yield "Starting Jira authentication..."
        self.jira = JIRA(
            server=JIRA_SERVER_ADDRESS.rstrip("/"),
            basic_auth=(self.auth.email, self.auth.token),
            validate=True,
        )

        # immediately cache
        if self.allow_cache_credentials:
            with open(f"/tmp/{self.name}-credentials.json", "w") as f:
                json.dump(asdict(self.auth), f)
        yield AdvanceMessage(
            "Jira auth is valid"
            + (
                " and credentials have been cached!"
                if self.allow_cache_credentials
                else ""
            )
        )

        self.project_exists(bug_report.project)
        yield AdvanceMessage(f"Project {bug_report.project} exists!")

        if bug_report.assignee:
            user_id = self.assignee_exists_and_unique(bug_report.assignee)
            bug_dict["assignee"] = {"id": user_id}
            yield AdvanceMessage(
                f"Assignee [u]{bug_report.assignee}[/u] exists and is unique!"
            )
        else:
            yield AdvanceMessage("Assignee unspecified, marking the bug as unassigned")

        if len(bug_report.platform_tags) > 0:
            self.all_components_exist(bug_report.project, bug_report.platform_tags)
            yield AdvanceMessage("All platform tags exist")
        else:
            yield AdvanceMessage("No platform tags were given, not assigning any tags")

        self.issue = self.jira.create_issue(bug_dict)
        yield AdvanceMessage(f"Created {self.issue.key}")

    @override
    def reopen(
        self, bug_report: PartialBugReport, bug_id: str
    ) -> Generator[str | AdvanceMessage, None, None]:
        """Reopens a bug on Jira

        :param bug_report: the partial bug report that will appear in a comment
            - This comment will look like the user posted it
            - A "This comment is generated by bugit-v2" header will be added
        :param bug_id: it's the string at the end of a bug url, like STELLA-123
        """
        issue_file_time_block = (
            f"[Stage]\n{pretty_issue_file_times[bug_report.issue_file_time]}"
        )

        assert self.auth, "Missing auth credentials"
        assert JIRA_SERVER_ADDRESS, "JIRA_SERVER is not specified!"

        yield "Starting Jira authentication..."
        self.jira = JIRA(
            server=JIRA_SERVER_ADDRESS.rstrip("/"),
            basic_auth=(self.auth.email, self.auth.token),
            validate=True,
        )

        # immediately cache
        if self.allow_cache_credentials:
            with open(f"/tmp/{self.name}-credentials.json", "w") as f:
                json.dump(asdict(self.auth), f)
        yield AdvanceMessage(
            "Jira auth is valid"
            + (
                " and credentials have been cached!"
                if self.allow_cache_credentials
                else ""
            )
        )

        self.issue = self.jira.issue(bug_id)

        if bug_report.assignee:
            self.assignee_exists_and_unique(bug_report.assignee)
            self.jira.assign_issue(self.issue, bug_report.assignee)
            yield AdvanceMessage(
                f"Assignee has been updated to [u]{bug_report.assignee}[/u]"
            )
        else:
            yield AdvanceMessage("Assignee unspecified, not changing the bug's assignee")

        if len(bug_report.platform_tags) > 0:
            self.all_components_exist(
                self.issue.fields.project.key, bug_report.platform_tags
            )
            self.issue.update(
                fields={"components": [{"name": pt} for pt in bug_report.platform_tags]}
            )
            yield AdvanceMessage(
                f"Replaced the {bug_id}'s platform tags with {bug_report.platform_tags}"
            )
        else:
            yield AdvanceMessage(
                "No platform tags were given, not modifying the bug's tags"
            )

        if len(bug_report.additional_tags) > 0:
            self.issue.update(fields={"labels": bug_report.additional_tags})
            yield AdvanceMessage(
                f"Replaced the {bug_id}'s tags with {bug_report.additional_tags}"
            )
        else:
            yield AdvanceMessage(
                "No additional tags were given, not modifying the bug's tags"
            )

        self.jira.add_comment(
            self.issue,
            "\n".join(
                [
                    "(This comment is posted by bugit-v2)",
                    bug_report.description,
                    issue_file_time_block,
                ]
            ),
        )
        yield AdvanceMessage("Finished commenting on the bug!")

    @override
    def get_cached_credentials(self) -> JiraBasicAuth | None:
        try:
            with open(f"/tmp/{self.name}-credentials.json") as f:
                auth_json = json.load(f)
                return JiraBasicAuth(auth_json["email"], auth_json["token"])
        except Exception:
            return None

    @override
    def upload_attachment(self, attachment_file: Path) -> None:
        assert self.jira
        assert self.issue
        # .add_attachment has a decorator that confuses the typechecker
        # go to its definition to see the expected arguments
        self.jira.add_attachment(self.issue.id, str(attachment_file))

    @property
    @override
    def bug_url(self) -> str:
        assert self.jira
        assert self.issue, "Nothing has been submitted to Jira yet"
        return f"{self.jira.server_url}/browse/{self.issue.key}"
