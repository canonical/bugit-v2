"""
Implements the concrete Jira submitter that submits a bug report to Jira.
"""

import json
import os
import random
import time
from collections.abc import Generator, Mapping
from dataclasses import asdict, dataclass
from typing import final, override

from jira import JIRA
from textual import on
from textual.app import ComposeResult
from textual.containers import Center, VerticalGroup
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.models.bug_report import BugReport, Severity


@dataclass(slots=True)
class JiraBasicAuth:
    email: str
    token: str


@final
class JiraAuthModal(ModalScreen[tuple[JiraBasicAuth, bool]]):
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
            yield Label("[b][$primary]Jira Authentication")
            yield Input(placeholder="your.email@jira.com", id="email")
            yield Input(
                placeholder="A token can be created at the link below if you don't already have one",
                id="token",
            )
            yield Label(
                "https://id.atlassian.com/manage-profile/security/api-tokens"
            )
            yield Checkbox(
                "Cache valid credentials until next boot",
                tooltip=(
                    "Save the credentials to /tmp so you don't need to "
                    "type the credentials over and over again. They are erased "
                    "at the next boot, or you can manually delete them"
                ),
                value=True,
            )
            yield Center(
                Button("Continue", id="continue_button", disabled=True)
            )

    def on_mount(self):
        self.query_exactly_one("#top_level_container").border_title = (
            "Jira Authentication"
        )
        self.query_exactly_one("#email").border_title = "Email"
        self.query_exactly_one("#token").border_title = "Jira Access Token"

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
        # should only be clickable when auth has been filled
        assert self.auth
        self.dismiss((self.auth, self.query_exactly_one(Checkbox).value))


@final
class JiraBugReportSubmitter(BugReportSubmitter[JiraBasicAuth, str]):
    name = "jira_submitter"
    display_name = "Jira Client"
    steps = 4
    jira: JIRA | None = None
    auth_modal = JiraAuthModal
    auth: JiraBasicAuth | None = None

    # map the severity value inside the app to the ones on Jira
    severity_name_map: Mapping[Severity, str] = {
        "highest": "Highest",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "lowest": "Lowest",
    }

    def project_exists(self, project_name: str) -> bool:
        """Does the project exist?

        :param project_name:
            name of the project, usually the name at the end
            of the URL:
            https://hostname.com/jira/software/c/projects/NAME
        :return: true if the project exists
        """
        assert self.jira, "Jira object is not initialized"
        try:
            self.jira.project(id=project_name)
            return True
        except Exception:
            return False

    def assignee_exists_and_unique(self, assignee: str) -> bool:
        """Does @param assignee exist and is it unique?

        :param assignee: the email of the assignee or some form of ID
        :return: exists and unique
        """
        assert self.jira, "Jira object is not initialized"
        try:
            query_result = self.jira.search_users(query=assignee)
            return len(query_result) == 1
        except Exception:
            return False

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage | Exception, None, str]:
        # final submit
        bug_dict = {
            "assignee": bug_report.assignee,
            "project": bug_report.project,
            "summary": bug_report.title,
            "description": bug_report.description,
            "components": bug_report.platform_tags,
            "labels": bug_report.additional_tags,
            "priority": {"name": self.severity_name_map[bug_report.severity]},
            "issuetype": {"name": "Bug"},
        }

        try:
            jira_server_addr = os.getenv("JIRA_SERVER")
            assert self.auth, "Missing auth credentials"
            assert jira_server_addr, "JIRA_SERVER is not specified!"

            yield "Starting Jira authentication..."
            self.jira = JIRA(
                server=jira_server_addr,
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

            assert self.project_exists(
                bug_report.project
            ), f"Project '{bug_report.project}' doesn't exist!"
            yield AdvanceMessage(
                f"Project {bug_report.project} exists on {jira_server_addr}"
            )

            if bug_report.assignee:
                assert self.assignee_exists_and_unique(
                    bug_report.assignee
                ), f"Assignee {bug_report.assignee} doesn't exist or isn't unique!"
                # yield "OK! Assignee exist"
                yield AdvanceMessage(
                    f"Assignee [u]{bug_report.assignee}[/u] exists and is unique!"
                )
            else:
                yield AdvanceMessage(
                    "Assignee unspecified, marking the bug as unassigned"
                )

            issue = self.jira.create_issue(  # pyright: ignore[reportUnknownMemberType]
                bug_dict
            )
            print(issue)
            yield AdvanceMessage(f"Created {issue.id}")
            return issue.id
        except Exception as e:
            yield e

        # the submission screen should stop as soon as an Exception is yielded
        raise RuntimeError("Intermediate exceptions were not caught")

    @override
    def get_cached_credentials(self) -> JiraBasicAuth | None:
        try:
            with open(f"/tmp/{self.name}-credentials.json") as f:
                auth_json = json.load(f)
                return JiraBasicAuth(auth_json["email"], auth_json["token"])
        except Exception:
            return None


@final
class MockJiraSubmitter(BugReportSubmitter[JiraBasicAuth, str]):
    name = "mock_jira_submitter"
    display_name = "Mock Jira"
    steps = 4
    jira: JIRA | None = None
    auth_modal = JiraAuthModal
    auth: JiraBasicAuth | None = None
    allow_cache_credentials = False

    # map the severity value inside the app to the ones on Jira
    severity_name_map: Mapping[Severity, str] = {
        "highest": "Highest",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "lowest": "Lowest",
    }

    def project_exists(self, project_name: str) -> bool:
        """Does the project exist?

        :param project_name:
            name of the project, usually the name at the end
            of the URL:
            https://hostname.com/jira/software/c/projects/NAME
        :return: true if the project exists
        """
        assert self.jira, "Jira object is not initialized"
        try:
            self.jira.project(id=project_name)
            return True
        except Exception:
            return False

    def assignee_exists_and_unique(self, assignee: str) -> bool:
        """Does @param assignee exist and is it unique?

        :param assignee: the email of the assignee or some form of ID
        :return: exists and unique
        """
        assert self.jira, "Jira object is not initialized"
        try:
            query_result = self.jira.search_users(query=assignee)
            return len(query_result) == 1
        except Exception:
            return False

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage | Exception, None, str]:
        # final submit
        bug_dict = {
            "assignee": bug_report.assignee,
            "project": bug_report.project,
            "summary": bug_report.title,
            "description": bug_report.description,
            "components": bug_report.platform_tags,
            "labels": bug_report.additional_tags,
            "priority": {"name": self.severity_name_map[bug_report.severity]},
            "issuetype": {"name": "Bug"},
        }

        try:
            jira_server_addr = os.getenv("JIRA_SERVER")
            assert self.auth, "Missing auth credentials"
            assert jira_server_addr, "JIRA_SERVER is not specified!"

            yield "Starting Jira authentication..."
            self.jira = JIRA(
                server=jira_server_addr,
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

            assert self.project_exists(
                bug_report.project
            ), f"Project '{bug_report.project}' doesn't exist!"
            yield AdvanceMessage(
                f"Project {bug_report.project} exists on {jira_server_addr}"
            )

            if bug_report.assignee:
                assert self.assignee_exists_and_unique(
                    bug_report.assignee
                ), f"Assignee {bug_report.assignee} doesn't exist or isn't unique!"
                yield AdvanceMessage(
                    f"Assignee [u]{bug_report.assignee}[/u] exists and is unique!"
                )
            else:
                yield AdvanceMessage(
                    "Assignee unspecified, marking the bug as unassigned"
                )
            print(bug_dict)
            if os.getenv("MOCK_SUBMIT") == "random":
                if random.random() > 0.5:
                    raise RuntimeError("err during issue()")

            time.sleep(2)
            yield AdvanceMessage("OK! Created `issue id`")

            return "issue id"
        except Exception as e:
            yield e

        raise RuntimeError("Intermediate exceptions were not caught")

    @override
    def get_cached_credentials(self) -> JiraBasicAuth | None:
        try:
            with open(f"/tmp/{self.name}-credentials.json") as f:
                auth_json = json.load(f)
                return JiraBasicAuth(auth_json["email"], auth_json["token"])
        except Exception:
            return None
