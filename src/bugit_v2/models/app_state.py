import abc
import os
from typing import Literal, override

from attr import dataclass

from bugit_v2.checkbox_utils import Session
from bugit_v2.checkbox_utils.models import SimpleCheckboxSubmission
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.bug_report import BugReport
from bugit_v2.screens.submission_progress_screen import RETURN_SCREEN_CHOICES
from bugit_v2.utils.constants import AUTOSAVE_DIR, NullSelection


@dataclass(slots=True, frozen=True)
class AppContext(abc.ABC):
    # For session and job_id, None means the user hasn't selected anything
    # but still POSSIBLE to go to a screen that can select them
    # NullSelection means an explicit selection of no session/no job
    args: AppArgs
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

    @property
    def context(self) -> AppContext:
        assert self._context, "Use before context is assigned"
        return self._context

    @context.setter
    def context(self, context: AppContext) -> None:
        self._context = context

    @property
    def name(self) -> str:
        """Just a name for debugging"""
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
    def go_forward[T](
        self, screen_result: T, expected_result_type: type[T]
    ) -> "AppState | None":
        """When the current screen is dismissed

        :return: None if there's nowhere to go to, new AppState otherwise
        """
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
    def go_forward[T](
        self, screen_result: T, expected_result_type: type[T]
    ) -> "AppState | None":
        return SessionSelectionState()


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
            return RecoverFromAutosaveState()

    @override
    def go_forward[T](
        self, screen_result: T, expected_result_type: type[T]
    ) -> "AppState | None":
        # can either go to job selection or editor
        pass


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
            return RecoverFromAutosaveState()
        else:
            return SessionSelectionState()

    @override
    def go_forward[T](
        self, screen_result: T, expected_result_type: type[T]
    ) -> "AppState | None":
        # can either go to job selection or editor
        return ReportEditorState()


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
                NullSelection.NO_SESSION,
                NullSelection.NO_JOB,
                NullSelection.NO_CHECKBOX_SUBMISSION,
            ):
                # came from nothing => go back to recovery
                return RecoverFromAutosaveState()
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
                return JobSelectionState()
            case _:
                raise RuntimeError(
                    f"Impossible context when returning from editor {self.context}"
                )

    @override
    def go_forward[T](
        self, screen_result: T, expected_result_type: type[T]
    ) -> "AppState | None":
        return SubmissionProgressState()


class SubmissionProgressState(AppState):
    @override
    def assertions(self) -> None:
        assert self.context.bug_report_to_submit, "No bug report to submit"

    @override
    def go_back(self) -> "AppState | None":
        return None  # don't allow back button during submission

    @override
    def go_forward[T](
        self, screen_result: T, expected_result_type: type[T]
    ) -> "AppState | None":
        assert screen_result in RETURN_SCREEN_CHOICES
        # this is where we get the select a new session/job buttons
        match screen_result:
            case "job":
                return JobSelectionState()
            case "quit":
                return QuitState()
            case "report_editor":
                return ReportEditorState()
            case "session":
                return SessionSelectionState()


class QuitState(AppState):
    @override
    def go_back(self) -> "AppState | None":
        return None

    @override
    def go_forward[T](
        self, screen_result: T, expected_result_type: type[T]
    ) -> "AppState | None":
        return None

    @override
    def assertions(self) -> None:
        return None
