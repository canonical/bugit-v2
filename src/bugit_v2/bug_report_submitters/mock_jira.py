import json
import os
import random
import time
from collections.abc import Generator, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import cast, final

from jira import JIRA
from jira.resources import Component
from typing_extensions import override

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.bug_report_submitters.jira_submitter import (
    JiraAuthModal,
    JiraBasicAuth,
    JiraSubmitterError,
)
from bugit_v2.models.bug_report import BugReport, Severity

JIRA_SERVER_ADDRESS = os.getenv("JIRA_SERVER")


@final
class MockJiraSubmitter(BugReportSubmitter[JiraBasicAuth, None]):
    name = "mock_jira_submitter"
    display_name = "Mock Jira"
    steps = 5
    jira: JIRA | None = None
    auth_modal = JiraAuthModal
    auth: JiraBasicAuth | None = None
    allow_cache_credentials = False
    mock_issue: str | None = None

    # map the severity value inside the app to the ones on Jira
    severity_name_map: Mapping[Severity, str] = {
        "highest": "Highest",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "lowest": "Lowest",
    }

    def project_exists(self, project_name: str) -> None:
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
        except Exception:
            raise JiraSubmitterError(f"{project_name} doesn't exist!")

    def assignee_exists_and_unique(self, assignee: str) -> None:
        """Does @param assignee exist and is it unique?

        :param assignee: the email of the assignee or some form of ID
        :return: exists and unique
        """
        assert self.jira, "Jira object is not initialized"

        query_result = self.jira.search_users(query=assignee)
        if len(query_result) == 0:
            raise JiraSubmitterError(f"{assignee} doesn't exist!")
        elif len(query_result) > 1:
            raise JiraSubmitterError(f"{assignee} isn't unique!")

    def all_components_exist(
        self, project: str, components: Sequence[str]
    ) -> None:
        assert self.jira, "Jira object is not initialized"
        # the @translate_args decorator confuses the type checker
        query_result = cast(
            list[Component], self.jira.project_components(project)
        )
        for wanted_component in components:
            if not any(
                actual_component.name
                == wanted_component  # apparently .name exists
                for actual_component in query_result
            ):
                raise JiraSubmitterError(
                    f"{wanted_component} doesn't exist in {project}!"
                )

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage, None, None]:
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

        assert self.auth, "Missing auth credentials"
        assert JIRA_SERVER_ADDRESS, "JIRA_SERVER is not specified!"

        yield "Starting Jira authentication..."
        self.jira = JIRA(
            server=JIRA_SERVER_ADDRESS,
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
        yield AdvanceMessage(f"Project {bug_report.project} exists")

        if bug_report.assignee:
            self.assignee_exists_and_unique(bug_report.assignee)
            yield AdvanceMessage(
                f"Assignee [u]{bug_report.assignee}[/u] exists and is unique!"
            )
        else:
            yield AdvanceMessage(
                "Assignee unspecified, marking the bug as unassigned"
            )

        if len(bug_report.platform_tags) > 0:
            self.all_components_exist(
                bug_report.project, bug_report.platform_tags
            )
            yield AdvanceMessage("All platform tags exist")
        else:
            yield AdvanceMessage(
                "No platform tags were given, not assigning any tags"
            )
        print(bug_dict)
        if os.getenv("MOCK_SUBMIT") == "random":
            if random.random() > 0.5:
                raise RuntimeError("err during issue()")

        time.sleep(2)
        yield AdvanceMessage("OK! Created `issue id`")
        self.mock_issue = "mock_issue"

    @override
    def get_cached_credentials(self) -> JiraBasicAuth | None:
        try:
            with open(f"/tmp/{self.name}-credentials.json") as f:
                auth_json = json.load(f)
                return JiraBasicAuth(auth_json["email"], auth_json["token"])
        except Exception:
            return None

    @override
    def upload_attachment(self, attachment_file: Path) -> str | None:
        time.sleep(random.randint(1, 5))
        print("uploaded", attachment_file)

    @property
    @override
    def bug_url(self) -> str:
        assert self.mock_issue
        return "http://example.com/"
