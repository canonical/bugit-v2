import os
from collections.abc import Generator
from pathlib import Path
from typing import Any, final
from unittest.mock import MagicMock

from launchpadlib.launchpad import Launchpad
from launchpadlib.uris import LPNET_WEB_ROOT, QASTAGING_WEB_ROOT
from typing_extensions import override

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    AdvanceMessage,
    BugReportSubmitter,
)
from bugit_v2.bug_report_submitters.launchpad_submitter import (
    LaunchpadAuthModal,
)
from bugit_v2.models.bug_report import BugReport

LAUNCHPAD_AUTH_FILE_PATH = Path("/tmp/bugit-v2-launchpad.txt")
# 'staging' doesn't seem to work
# only 'qastaging' and 'production' works
VALID_SERVICE_ROOTS = ("production", "qastaging")


@final
class MockLaunchpadSubmitter(BugReportSubmitter[Path, None]):
    name = "mock_launchpad_submitter"
    display_name = "Mock Launchpad"
    severity_name_map = {
        "highest": "Critical",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "lowest": "Wishlist",
    }
    steps = 7
    lp_client: Launchpad | None = None
    auth_modal = LaunchpadAuthModal

    def check_project_existence(self, project_name: str) -> Any:
        assert self.lp_client
        try:
            # type checker freaks out here
            # since launchpad lib wants unknown member access + index access
            return self.lp_client.projects[  # pyright: ignore[reportIndexIssue, reportOptionalSubscript, reportUnknownVariableType]
                project_name
            ]
        except Exception as e:
            error_message = (
                f"Project '{project_name}' doesn't exist or you don't have access. "
                + f"Original error: {repr(e)}"
            )
            raise ValueError(error_message)

    def check_assignee_existence(self, assignee: str) -> Any:
        assert self.lp_client
        try:
            return self.lp_client.people[  # pyright: ignore[reportIndexIssue, reportOptionalSubscript, reportUnknownVariableType]
                assignee
            ]
        except Exception as e:
            error_message = (
                f"Assignee '{assignee}' doesn't exist. Original error: {e}"
            )
            raise ValueError(error_message)

    def check_series_existence(self, series: str) -> Any:
        assert self.lp_client
        try:
            return self.lp_client.project.getSeries(  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess, reportUnknownVariableType]
                name=series
            )
        except Exception as e:
            error_message = (
                f"Series '{series}' doesn't exist. Original error: {e}"
            )
            raise ValueError(error_message)

    @override
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage, None, None]:

        service_root = os.getenv("APPORT_LAUNCHPAD_INSTANCE", "qastaging")
        app_name = os.getenv("BUGIT_APP_NAME")

        assert service_root in VALID_SERVICE_ROOTS, (
            "Invalid APPORT_LAUNCHPAD_INSTANCE, "
            f"expected one of {VALID_SERVICE_ROOTS}, but got {service_root}"
        )
        assert app_name, "BUGIT_APP_NAME was not specified"
        assert (
            LAUNCHPAD_AUTH_FILE_PATH.exists()
        ), "At this point auth should already be valid"

        yield f"Logging into Launchpad: {service_root}"
        self.lp_client = Launchpad.login_with(
            app_name,
            service_root,
            credentials_file=LAUNCHPAD_AUTH_FILE_PATH,
        )  # this blocks until ready
        yield AdvanceMessage("Launchpad auth succeeded")

        assignee = None
        series = None
        project = self.check_project_existence(bug_report.project)
        yield AdvanceMessage(
            f"Project '{bug_report.project}' exists at {project}"
        )

        if bug_report.assignee:
            assignee = self.check_assignee_existence(bug_report.assignee)
            yield AdvanceMessage(
                f"Assignee [u]{bug_report.assignee}[/u] exists"
            )
        else:
            yield AdvanceMessage(
                "Assignee unspecified, marking the bug as unassigned"
            )

        if bug_report.series:
            series = self.check_series_existence(bug_report.series)
            yield AdvanceMessage(f"Series [u]{bug_report.series} exists![/]")
        else:
            yield AdvanceMessage("Series unspecified, skipping")

        # # actually create the bug
        bug = MagicMock(
            title=bug_report.title,
            description=bug_report.description,  # is there a length limit?
            tags=[
                *bug_report.platform_tags,
                *bug_report.additional_tags,
            ],  # length limit?
            target=self.lp_client.projects[  # pyright: ignore[reportIndexIssue, reportOptionalSubscript]
                bug_report.project  # index access also has a side effect
            ],
        )
        # https://documentation.ubuntu.com/launchpad/user/explanation/launchpad-api/launchpadlib/#persistent-references-to-launchpad-objects
        yield AdvanceMessage(f"Created bug: {str(bug)}")

        task = bug.bug_tasks[0]
        if assignee:
            yield f"Setting assignee to {assignee}..."
            task.assignee = assignee
        if series:
            yield f"Setting series to {series}"
            bug.addNomination(target=series).approve()

        yield f"Setting status to {bug_report.status}..."
        task.status = bug_report.status

        lp_importance = self.severity_name_map[bug_report.severity]
        yield f"Setting importance to {lp_importance}..."
        task.importance = lp_importance  # the update request is a side effect

        task.lp_save()
        yield "Saved bug settings"

        match service_root:
            case "production":
                bug_url = f"{LPNET_WEB_ROOT}bugs/{bug.id}"
            case "qastaging":
                bug_url = f"{QASTAGING_WEB_ROOT}bugs/{bug.id}"

        yield AdvanceMessage(f"Bug URL is: {bug_url}")

    @property
    @override
    def bug_url(self) -> str:
        return "https://www.example.com"

    @override
    def get_cached_credentials(self) -> Path | None:
        if LAUNCHPAD_AUTH_FILE_PATH.exists():
            return LAUNCHPAD_AUTH_FILE_PATH
        return None

    @override
    def upload_attachment(self, attachment_file: Path) -> str | None:
        return super().upload_attachment(attachment_file)
