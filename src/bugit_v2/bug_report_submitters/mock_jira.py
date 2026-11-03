import json
import os
import random
import threading
import time
from collections.abc import Generator, Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import cast, final, override

from jira import JIRA, JIRAError
from jira.resources import Component

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.bug_report_submitters.jira_submitter import (
    JiraAuthModal,
    JiraBasicAuth,
    JiraSubmitterError,
    build_attachment_comment,
)
from bugit_v2.models.attachment_request import AttachmentRequest
from bugit_v2.models.bug_report import BugReport, Severity

JIRA_SERVER_ADDRESS = os.getenv("JIRA_SERVER", "https://warthogs.atlassian.net")


@final
class MockJiraSubmitter(BugReportSubmitter[JiraBasicAuth]):
    name = "mock_jira_submitter"
    display_name = "Mock Jira"
    steps = 5
    jira: JIRA | None = None
    auth_modal = JiraAuthModal
    auth: JiraBasicAuth | None = None
    allow_cache_credentials = False
    mock_issue: str | None = None
    allow_parallel_upload = True
    supports_attach = True
    attach_steps = 2
    attach_request: AttachmentRequest | None = None

    def __init__(self) -> None:
        self.uploaded_filenames: list[str] = []
        self._uploaded_filenames_lock = threading.Lock()

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
        if not self.jira:
            raise JiraSubmitterError("Jira object is not initialized")
        try:
            self.jira.project(id=project_name)
        except Exception:
            raise JiraSubmitterError(f"Project '{project_name}' doesn't exist!")

    def assignee_exists_and_unique(self, assignee: str) -> None:
        """Does @param assignee exist and is it unique?

        :param assignee: the email of the assignee or some form of ID
        :return: exists and unique
        """
        if not self.jira:
            raise JiraSubmitterError("Jira object is not initialized")

        query_result = self.jira.search_users(query=assignee)
        if len(query_result) == 0:
            raise JiraSubmitterError(f"Assignee '{assignee}' doesn't exist!")
        elif len(query_result) > 1:
            raise JiraSubmitterError(f"Assignee '{assignee}' isn't unique!")

    def all_components_exist(self, project: str, components: Sequence[str]) -> None:
        if not self.jira:
            raise JiraSubmitterError("Jira object is not initialized")
        # the @translate_args decorator confuses the type checker
        query_result = cast(list[Component], self.jira.project_components(project))
        for wanted_component in components:
            if not any(
                actual_component.name == wanted_component  # apparently .name exists
                for actual_component in query_result
            ):
                raise JiraSubmitterError(
                    f"Component '{wanted_component}' doesn't exist in {project}!"
                )

    @override
    def bug_exists(self, bug_id: str) -> bool:
        if not self.auth:
            raise JiraSubmitterError("Missing auth credentials")

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
            print(e)
            return False

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

        if not self.auth:
            raise JiraSubmitterError("Missing auth credentials")
        if not JIRA_SERVER_ADDRESS:
            raise JiraSubmitterError("JIRA_SERVER is not specified!")

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
            yield AdvanceMessage("Assignee unspecified, marking the bug as unassigned")

        if len(bug_report.platform_tags) > 0:
            self.all_components_exist(bug_report.project, bug_report.platform_tags)
            yield AdvanceMessage("All platform tags exist")
        else:
            yield AdvanceMessage("No platform tags were given, not assigning any tags")
        print(bug_dict)
        if os.getenv("MOCK_SUBMIT") == "random":
            if random.random() > 0.5:
                raise RuntimeError("err during issue()")

        time.sleep(2)
        yield AdvanceMessage("OK! Created `issue id`")
        self.mock_issue = "mock_issue"

    @override
    def attach_to_existing(
        self, request: AttachmentRequest
    ) -> Generator[str | AdvanceMessage, None, None]:
        """Real auth and issue lookup, nothing is written to jira"""
        self.attach_request = None
        self.uploaded_filenames = []
        if not self.auth:
            raise JiraSubmitterError("Missing auth credentials")

        yield "Starting Jira authentication..."
        self.jira = JIRA(
            server=JIRA_SERVER_ADDRESS,
            basic_auth=(self.auth.email, self.auth.token),
            validate=True,
        )
        if self.allow_cache_credentials:
            with open(f"/tmp/{self.name}-credentials.json", "w") as f:
                json.dump(asdict(self.auth), f)
        yield AdvanceMessage("Jira auth is valid")

        try:
            issue = self.jira.issue(request.bug_id)
        except JIRAError as e:
            raise JiraSubmitterError(
                f"Can't access {request.bug_id}. Original error: {e.status_code} {e.text}"
            )
        if os.getenv("MOCK_SUBMIT") == "random" and random.random() > 0.5:
            raise RuntimeError("err during attach_to_existing()")
        yield AdvanceMessage(f"Found {issue.key}")
        self.mock_issue = issue.key
        self.attach_request = request

    @override
    def get_cached_credentials(self) -> JiraBasicAuth | None:
        try:
            with open(f"/tmp/{self.name}-credentials.json") as f:
                auth_json = json.load(f)
                return JiraBasicAuth(auth_json["email"], auth_json["token"])
        except Exception:
            return None

    @override
    def upload_attachment(
        self, attachment_file: Path, filename: str | None = None
    ) -> str | None:
        time.sleep(random.randint(1, 5))
        print("uploaded", attachment_file)
        if self.attach_request is not None:
            with self._uploaded_filenames_lock:
                self.uploaded_filenames.append(filename or attachment_file.name)

    @property
    @override
    def bug_url(self) -> str:
        if not self.mock_issue:
            raise JiraSubmitterError("Nothing has been submitted to Jira yet")
        return "http://example.com/"

    @override
    def finalize(self) -> str | None:
        if self.attach_request is None:
            return None
        comment = build_attachment_comment(
            self.attach_request.comment, sorted(self.uploaded_filenames)
        )
        print("mock comment on", self.mock_issue, comment, sep="\n")
        return f"Mock comment on {self.mock_issue}:\n{comment}"
