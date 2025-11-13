import abc
import os
from pathlib import Path
from typing import Any, Callable, Literal, override

from attr import dataclass
from textual.screen import Screen

from bugit_v2.bug_report_submitters.bug_report_submitter import (
    BugReportSubmitter,
)
from bugit_v2.checkbox_utils import Session
from bugit_v2.checkbox_utils.models import SimpleCheckboxSubmission
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.bug_report import (
    BugReport,
    BugReportAutoSaveData,
    recover_from_autosave,
)
from bugit_v2.screens.bug_report_screen import BugReportScreen
from bugit_v2.screens.job_selection_screen import JobSelectionScreen
from bugit_v2.screens.recover_from_autosave_screen import (
    RecoverFromAutoSaveScreen,
)
from bugit_v2.screens.session_selection_screen import SessionSelectionScreen
from bugit_v2.screens.submission_progress_screen import (
    RETURN_SCREEN_CHOICES,
    SubmissionProgressScreen,
)
from bugit_v2.utils.constants import AUTOSAVE_DIR, NullSelection


@dataclass(slots=True)
class AppContext(abc.ABC):
    # For session and job_id, None means the user hasn't selected anything
    # but still POSSIBLE to go to a screen that can select them
    # NullSelection means an explicit selection of no session/no job
    args: AppArgs
    submitter: type[BugReportSubmitter[Any, Any]]
    session: Session | Literal[NullSelection.NO_SESSION] | None = None
    job_id: str | Literal[NullSelection.NO_JOB] | None = None
    # Initial state of the bug report, only used for backup right now
    bug_report_init_state: (
        BugReport | Literal[NullSelection.NO_BACKUP] | None
    ) = None
    # The finished bug report ready to be fed to the submitter
    bug_report_to_submit: BugReport | None = None
    # Checkbox submission passed in from the CLI
    # Must check if this is valid at the beginning of the app
    checkbox_submission: (
        SimpleCheckboxSubmission
        | Literal[NullSelection.NO_CHECKBOX_SUBMISSION]
    ) = NullSelection.NO_CHECKBOX_SUBMISSION


class AppState(abc.ABC):
    _context: AppContext | None = None

    def __init__(self, context: AppContext | None = None) -> None:
        self._context = context

    @property
    def context(self) -> AppContext:
        assert self._context, "Use before context is assigned"
        return self._context

    @context.setter
    def context(self, context: AppContext) -> None:
        self._context = context

    @property
    def name(self) -> str:
        """name used to match this state to a screen"""
        return type(self).__name__

    @abc.abstractmethod
    def assertions(self) -> None:
        """Do assertion in here"""
        pass

    @abc.abstractmethod
    def go_back(self) -> "AppState | None":
        """When the user presses the go-back button/shortcut

        :return: None if there's nowhere to go back to, new AppState otherwise
        """
        pass

    @abc.abstractmethod
    def go_forward(self, screen_result: object) -> "AppState":
        """When the current screen is dismissed

        :return: None if there's nowhere to go to, new AppState otherwise
        """
        pass

    @abc.abstractmethod
    def get_screen_constructor(self) -> Callable[[], Screen[Any]]:
        pass


class RecoverFromAutosaveState(AppState):
    @override
    def assertions(self) -> None:
        return None

    @override
    def go_back(self) -> "AppState | None":
        # nowhere to go
        return None

    @override
    def go_forward(self, screen_result: object) -> "AppState":
        if screen_result is None:
            return SessionSelectionState(self.context)

        assert isinstance(screen_result, BugReportAutoSaveData)
        self.context.bug_report_init_state = recover_from_autosave(
            screen_result
        )
        return ReportEditorState(self.context)

    @override
    def get_screen_constructor[T](self) -> Callable[[], Screen[Any]]:
        def c():
            return RecoverFromAutoSaveScreen()

        return c


class SessionSelectionState(AppState):

    @override
    def assertions(self) -> None:
        assert self.context.session in (
            None,
            NullSelection.NO_SESSION,
        ), "Entered session selection with one already selected"
        assert (
            self.context.job_id is None
        ), "Impossible to have a job ID during session selection"
        assert (
            self.context.bug_report_to_submit is None
        ), "Impossible to have a complete bug report during session selection"

    @override
    def go_back(self) -> "AppState | None":
        # only possible screen is recovery screen
        if len(os.listdir(AUTOSAVE_DIR)) != 0:
            return RecoverFromAutosaveState(self.context)

    @override
    def go_forward(self, screen_result: object) -> AppState:
        # can either go to job selection or editor
        match screen_result:
            case NullSelection.NO_SESSION:
                self.context.session = NullSelection.NO_SESSION
                return ReportEditorState(self.context)
            case Path():
                self.context.session = Session(screen_result)
                return JobSelectionState(self.context)
            case _:
                raise RuntimeError(
                    f"Unexpected return value from session selection: {screen_result}"
                )

    @override
    def get_screen_constructor(self) -> Callable[[], Screen[Any]]:
        return lambda: SessionSelectionScreen()


class JobSelectionState(AppState):

    @override
    def assertions(self) -> None:
        assert isinstance(self.context.session, Session) or isinstance(
            self.context.checkbox_submission, SimpleCheckboxSubmission
        ), "No source to choose jobs from"
        assert (
            self.context.bug_report_to_submit is None
        ), "Impossible to have a complete bug report during job selection"

    @override
    def go_back(self) -> "AppState | None":
        # if we have a submission
        # then we definitely came from the recovery screen
        if (
            self.context.checkbox_submission
            != NullSelection.NO_CHECKBOX_SUBMISSION
        ) and len(os.listdir(AUTOSAVE_DIR)) != 0:
            return RecoverFromAutosaveState(self.context)
        else:
            return SessionSelectionState(self.context)

    @override
    def go_forward(self, screen_result: object) -> AppState:
        # can either go to job selection or editor
        assert type(screen_result) is str
        self.context.job_id = screen_result
        return ReportEditorState(self.context)

    @override
    def get_screen_constructor(self) -> Callable[[], Screen[Any]]:
        match (
            self.context.session,
            self.context.checkbox_submission,
        ):
            case (
                Session() as session,
                NullSelection.NO_CHECKBOX_SUBMISSION,
            ):
                return lambda: JobSelectionScreen(
                    session.get_run_jobs(),
                    str(session.session_path),
                )
            case (
                NullSelection.NO_SESSION,
                SimpleCheckboxSubmission() as submission,
            ):
                return lambda: JobSelectionScreen(
                    [r.full_id for r in submission.base.results],
                    str(submission.submission_path),
                )
            case _:
                raise RuntimeError("Impossible combination")


class ReportEditorState(AppState):
    @override
    def assertions(self) -> None:
        assert (
            self.context.bug_report_to_submit is None
        ), "Impossible to have a complete bug report during job selection"

    @override
    def go_back(self) -> "AppState | None":
        match (
            self.context.session,
            self.context.job_id,
            self.context.checkbox_submission,
        ):
            case (
                NullSelection.NO_SESSION | None,
                NullSelection.NO_JOB | None,
                NullSelection.NO_CHECKBOX_SUBMISSION,
            ):
                # came from nothing => go back to recovery
                return RecoverFromAutosaveState(self.context)
            case (
                Session(),
                str(),
                NullSelection.NO_CHECKBOX_SUBMISSION,
            ) | (
                NullSelection.NO_SESSION,
                str(),
                SimpleCheckboxSubmission(),
            ):
                # submission and job => back to job selection
                # selected session and job => go back to job selection
                return JobSelectionState(self.context)
            case _:
                raise RuntimeError(
                    f"Impossible context when returning from editor {self.context}"
                )

    @override
    def go_forward(self, screen_result: object) -> AppState:
        assert isinstance(screen_result, BugReport)
        self.context.bug_report_to_submit = screen_result
        return SubmissionProgressState(self.context)

    @override
    def get_screen_constructor(self) -> Callable[[], Screen[Any]]:
        return lambda: BugReportScreen(
            self.context.session or NullSelection.NO_SESSION,
            self.context.checkbox_submission,
            self.context.job_id or NullSelection.NO_JOB,
            self.context.args,
            (
                None
                if self.context.bug_report_init_state
                == NullSelection.NO_BACKUP
                else self.context.bug_report_init_state
            ),
        )


class SubmissionProgressState(AppState):
    @override
    def assertions(self) -> None:
        assert self.context.bug_report_to_submit, "No bug report to submit"

    @override
    def go_back(self) -> "AppState | None":
        return None  # don't allow back button during submission

    @override
    def go_forward(self, screen_result: object) -> AppState:
        assert screen_result in RETURN_SCREEN_CHOICES
        backup = self.context.bug_report_to_submit
        self.context.bug_report_to_submit = None
        # this is where we get the select a new session/job buttons
        match screen_result:
            case "job":
                self.context.job_id = None
                return JobSelectionState(self.context)
            case "quit":
                return QuitState(self.context)
            case "report_editor":
                self.context.bug_report_init_state = backup
                return ReportEditorState(self.context)
            case "session":
                self.context.session = None
                self.context.job_id = None
                return SessionSelectionState(self.context)

    @override
    def get_screen_constructor(self) -> Callable[[], Screen[Any]]:
        def c():
            assert self.context.bug_report_to_submit
            return SubmissionProgressScreen(
                self.context.bug_report_to_submit,
                self.context.submitter(),
                self.context.args,
            )

        return c


class QuitState(AppState):
    @override
    def go_back(self) -> "AppState | None":
        return None

    @override
    def go_forward(self, screen_result: object) -> AppState:
        return self

    @override
    def assertions(self) -> None:
        return None

    @override
    def get_screen_constructor(self) -> Callable[[], Screen[Any]]:
        raise RuntimeError("Impossible to construct screen in quit state")
