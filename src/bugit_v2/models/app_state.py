import abc
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, override

from textual.screen import Screen

from bugit_v2.bug_report_submitters.bug_report_submitter import BugReportSubmitter
from bugit_v2.checkbox_utils.checkbox_session import (
    AbstractCheckboxSession,
    CheckboxSession,
)
from bugit_v2.checkbox_utils.models import SimpleCheckboxSubmission
from bugit_v2.models.app_args import AppArgs
from bugit_v2.models.attachment_request import (
    AttachmentRequest,
    SerializableAttachmentRequest,
)
from bugit_v2.models.bug_report import BugReport, SerializableBugReport
from bugit_v2.screens.attach_to_bug_screen import AttachToBugScreen
from bugit_v2.screens.bug_report_screen import BugReportScreen
from bugit_v2.screens.job_selection_screen import JobSelectionScreen
from bugit_v2.screens.recover_from_autosave_screen import RecoverFromAutoSaveScreen
from bugit_v2.screens.session_selection_screen import SessionSelectionScreen
from bugit_v2.screens.submission_progress_screen import (
    RETURN_SCREEN_CHOICES,
    SubmissionProgressScreen,
)
from bugit_v2.utils.constants import ATTACH_AUTOSAVE_DIR, AUTOSAVE_DIR, NullSelection


@dataclass(slots=True)
class AppContext:
    # For session and job_id, None means the user hasn't selected anything
    # but still POSSIBLE to go to a screen that can select them
    # NullSelection means an explicit selection of no session/no job
    args: AppArgs
    submitter: type[BugReportSubmitter[Any]]
    session: CheckboxSession | Literal[NullSelection.NO_SESSION] | None = field(
        default=None, repr=False
    )
    job_id: str | Literal[NullSelection.NO_JOB] | None = None
    # Initial state of the bug report, only used for backup right now
    bug_report_init_state: BugReport | Literal[NullSelection.NO_BACKUP] | None = None
    # The finished bug report ready to be fed to the submitter
    bug_report_to_submit: BugReport | None = None
    # Checkbox submission passed in from the CLI
    # Must check if this is valid at the beginning of the app
    checkbox_submission: (
        SimpleCheckboxSubmission | Literal[NullSelection.NO_CHECKBOX_SUBMISSION]
    ) = field(default=NullSelection.NO_CHECKBOX_SUBMISSION, repr=False)
    # Attach mode only (args.bug_to_attach is set)
    # Initial state of the attachment editor, only used for backup right now
    attachment_request_init_state: AttachmentRequest | None = None
    # The finished attachment request ready to be fed to the submitter
    attachment_request_to_submit: AttachmentRequest | None = None

    @property
    def attach_mode(self) -> bool:
        return self.args.bug_to_attach is not None

    def has_autosaves(self) -> bool:
        return (
            len(os.listdir(ATTACH_AUTOSAVE_DIR if self.attach_mode else AUTOSAVE_DIR))
            != 0
        )

    def get_editor_state(self) -> "AppState":
        """The editor to go to after session/job selection"""
        if self.attach_mode:
            return AttachEditorState(self)
        return ReportEditorState(self)


class AppState(abc.ABC):
    _context: AppContext | None = None

    def __init__(self, context: AppContext | None = None) -> None:
        self._context = context

    @property
    def context(self) -> AppContext:
        if not self._context:
            raise RuntimeError("Use before context is assigned")
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

    @abc.abstractmethod
    def go_back(self) -> "AppState | None":
        """When the user presses the go-back button/shortcut

        :return: None if there's nowhere to go back to, new AppState otherwise
        """

    @abc.abstractmethod
    def go_forward(self, screen_result: object) -> "AppState":
        """When the current screen is dismissed

        :return: None if there's nowhere to go to, new AppState otherwise
        """

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
    def go_forward(self, screen_result: object) -> AppState:
        if screen_result is None:
            # chose to start a new report
            if self.context.checkbox_submission is NullSelection.NO_CHECKBOX_SUBMISSION:
                return SessionSelectionState(self.context)
            else:
                return JobSelectionState(self.context)

        if isinstance(screen_result, SerializableAttachmentRequest):
            request = screen_result.to_attachment_request()
            self.context.attachment_request_init_state = request
            self.context.session = request.checkbox_session or NullSelection.NO_SESSION
            self.context.job_id = request.job_id or NullSelection.NO_JOB
            self.context.checkbox_submission = (
                request.checkbox_submission or NullSelection.NO_CHECKBOX_SUBMISSION
            )
            return AttachEditorState(self.context)

        # recover from existing report path
        if not isinstance(screen_result, SerializableBugReport):
            raise TypeError(
                f"Unexpected screen result during autosave recovery: {screen_result!r}"
            )
        backup = screen_result.to_bug_report()
        self.context.bug_report_init_state = backup
        self.context.session = backup.checkbox_session or NullSelection.NO_SESSION
        self.context.job_id = backup.job_id or NullSelection.NO_JOB
        self.context.checkbox_submission = (
            backup.checkbox_submission or NullSelection.NO_CHECKBOX_SUBMISSION
        )

        return ReportEditorState(self.context)

    @override
    def get_screen_constructor(self):
        def c():
            if (
                self.context.args.checkbox_submission
                is not NullSelection.NO_CHECKBOX_SUBMISSION
            ):
                return RecoverFromAutoSaveScreen("submission", self.context.args)
            else:
                return RecoverFromAutoSaveScreen("session", self.context.args)

        return c


class SessionSelectionState(AppState):
    @override
    def assertions(self) -> None:
        if self.context.session not in (None, NullSelection.NO_SESSION):
            raise RuntimeError("Entered session selection with one already selected")
        if self.context.job_id not in (None, NullSelection.NO_JOB):
            raise RuntimeError(
                f"Impossible to have a job ID during session selection: {self.context.job_id}"
            )
        if self.context.bug_report_to_submit is not None:
            raise RuntimeError(
                "Impossible to have a complete bug report during session selection"
            )

    @override
    def go_back(self) -> "AppState | None":
        # only possible screen is recovery screen
        if self.context.has_autosaves():
            return RecoverFromAutosaveState(self.context)

    @override
    def go_forward(self, screen_result: object) -> AppState:
        # can either go to job selection or editor
        match screen_result:
            case NullSelection.NO_SESSION:
                self.context.session = NullSelection.NO_SESSION
                return self.context.get_editor_state()
            case Path():
                # switch CheckboxSession implementations here
                # by constructing a different concrete class
                self.context.session = CheckboxSession(screen_result)
                return JobSelectionState(self.context)
            case _:
                raise RuntimeError(
                    f"Unexpected return value from session selection: {screen_result}"
                )

    @override
    def get_screen_constructor(self):
        return lambda: SessionSelectionScreen()


class JobSelectionState(AppState):
    @override
    def assertions(self) -> None:
        if not (
            isinstance(self.context.session, AbstractCheckboxSession)
            or isinstance(self.context.checkbox_submission, SimpleCheckboxSubmission)
        ):
            raise TypeError("No source to choose jobs from")
        if self.context.bug_report_to_submit is not None:
            raise RuntimeError(
                "Impossible to have a complete bug report during job selection"
            )

    @override
    def go_back(self) -> "AppState | None":
        # if we have a submission
        # then we definitely came from the recovery screen
        if (
            self.context.checkbox_submission is not NullSelection.NO_CHECKBOX_SUBMISSION
        ) and self.context.has_autosaves():
            return RecoverFromAutosaveState(self.context)
        else:
            self.context.job_id = None
            self.context.session = None
            return SessionSelectionState(self.context)

    @override
    def go_forward(self, screen_result: object) -> AppState:
        # can either go to job selection or editor
        if not ((type(screen_result) is str) or (screen_result is NullSelection.NO_JOB)):
            raise RuntimeError(
                f"Unexpected return value from job selection: {screen_result!r}"
            )
        self.context.job_id = screen_result
        return self.context.get_editor_state()

    @override
    def get_screen_constructor(self):
        JOB_STATUS_FILTER = ("fail", "crash", "undecided")
        match (
            self.context.session,
            self.context.checkbox_submission,
        ):
            case (
                CheckboxSession() as session,
                NullSelection.NO_CHECKBOX_SUBMISSION,
            ):
                return lambda: JobSelectionScreen(
                    session.list_jobs(JOB_STATUS_FILTER),
                    str(session.session_path),
                    session.testplan_id,
                )
            case (
                NullSelection.NO_SESSION,
                SimpleCheckboxSubmission() as submission,
            ):
                return lambda: JobSelectionScreen(
                    [
                        r.full_id
                        for r in submission.base.results
                        if r.outcome in JOB_STATUS_FILTER
                    ],
                    str(submission.submission_path),
                    submission.base.testplan_id,
                )
            case _:
                raise RuntimeError(
                    f"Impossible combination: ({type(self.context.session)}, {type(self.context.checkbox_submission)})"
                )


class ReportEditorState(AppState):
    @override
    def assertions(self) -> None:
        if self.context.bug_report_to_submit is not None:
            raise RuntimeError(
                "Impossible to have a complete bug report during report editing"
            )

    @override
    def go_back(self) -> "AppState | None":
        bug_report_source = "editor"

        if (
            self.context.bug_report_init_state is not NullSelection.NO_BACKUP
            and self.context.bug_report_init_state is not None
        ):
            bug_report_source = self.context.bug_report_init_state.source

        self.context.bug_report_init_state = None
        return _go_back_from_editor(self.context, bug_report_source)

    @override
    def go_forward(self, screen_result: object) -> AppState:
        if not isinstance(screen_result, BugReport):
            raise TypeError(
                f"Unexpected screen result from report editor: {screen_result!r}"
            )
        self.context.bug_report_to_submit = screen_result
        return SubmissionProgressState(self.context)

    @override
    def get_screen_constructor(self):
        return lambda: BugReportScreen(
            self.context.session or NullSelection.NO_SESSION,
            self.context.checkbox_submission,
            self.context.job_id or NullSelection.NO_JOB,
            self.context.args,
            (
                None
                if self.context.bug_report_init_state is NullSelection.NO_BACKUP
                else self.context.bug_report_init_state
            ),
            self.context.checkbox_submission is NullSelection.NO_CHECKBOX_SUBMISSION,
        )


class AttachEditorState(AppState):
    @override
    def assertions(self) -> None:
        if not self.context.attach_mode:
            raise RuntimeError(
                "Entered the attachment editor without a bug to attach to"
            )
        if self.context.attachment_request_to_submit is not None:
            raise RuntimeError(
                "Impossible to have a complete attachment request during editing"
            )

    @override
    def go_back(self) -> "AppState | None":
        source = (
            self.context.attachment_request_init_state.source
            if self.context.attachment_request_init_state is not None
            else "editor"
        )
        self.context.attachment_request_init_state = None
        return _go_back_from_editor(self.context, source)

    @override
    def go_forward(self, screen_result: object) -> AppState:
        if not isinstance(screen_result, AttachmentRequest):
            raise TypeError(
                f"Unexpected screen result from attachment editor: {screen_result!r}"
            )
        self.context.attachment_request_to_submit = screen_result
        return SubmissionProgressState(self.context)

    @override
    def get_screen_constructor(self):
        return lambda: AttachToBugScreen(
            self.context.session or NullSelection.NO_SESSION,
            self.context.checkbox_submission,
            self.context.job_id or NullSelection.NO_JOB,
            self.context.args,
            self.context.attachment_request_init_state,
        )


def _go_back_from_editor(
    context: AppContext, source: Literal["editor", "recovery"]
) -> AppState:
    """Where to go when leaving either editor with the back button

    :param source: whether the editor's content was restored from an autosave
    """
    match (
        context.session,
        context.job_id,
        context.checkbox_submission,
    ):
        case (
            NullSelection.NO_SESSION | None,
            NullSelection.NO_JOB | None,
            NullSelection.NO_CHECKBOX_SUBMISSION,
        ):
            # came from nothing => go back to recovery
            return RecoverFromAutosaveState(context)
        case (
            CheckboxSession(),
            str() | NullSelection.NO_JOB | None,
            NullSelection.NO_CHECKBOX_SUBMISSION as cbs,
        ) | (
            NullSelection.NO_SESSION,
            str() | NullSelection.NO_JOB | None,
            SimpleCheckboxSubmission() as cbs,
        ):
            # submission and job => back to job selection
            # selected session and job => go back to job selection
            if source == "editor":
                return JobSelectionState(context)
            else:
                context.job_id = None
                if cbs is NullSelection.NO_CHECKBOX_SUBMISSION:
                    context.session = None
                else:
                    context.session = NullSelection.NO_SESSION
                return RecoverFromAutosaveState(context)
        case _:
            raise RuntimeError(
                "Impossible context when returning from editor {}".format(
                    (
                        context.session,
                        context.job_id,
                        type(context.checkbox_submission),
                    )
                )
            )


class SubmissionProgressState(AppState):
    @override
    def assertions(self) -> None:
        if not (
            self.context.bug_report_to_submit
            or self.context.attachment_request_to_submit
        ):
            raise RuntimeError("Nothing to submit")

    @override
    def go_back(self) -> "AppState | None":
        return None  # don't allow back button during submission

    @override
    def go_forward(self, screen_result: object) -> AppState:
        if screen_result not in RETURN_SCREEN_CHOICES:
            raise RuntimeError(
                f"Unexpected screen result from submission progress: {screen_result!r}"
            )
        backup = self.context.bug_report_to_submit
        attach_backup = self.context.attachment_request_to_submit
        self.context.bug_report_to_submit = None
        self.context.attachment_request_to_submit = None
        # this is where we get the select a new session/job buttons
        match screen_result:
            case "job":
                self.context.job_id = None
                self.context.bug_report_init_state = None
                self.context.attachment_request_init_state = None
                return JobSelectionState(self.context)
            case "quit":
                return QuitState(self.context)
            case "report_editor":
                # this is only used when a submission step fails
                if attach_backup is not None:
                    self.context.attachment_request_init_state = attach_backup
                    return AttachEditorState(self.context)
                self.context.bug_report_init_state = backup
                return ReportEditorState(self.context)
            case "session":
                self.context.session = None
                self.context.job_id = None
                self.context.bug_report_init_state = None
                self.context.attachment_request_init_state = None
                return SessionSelectionState(self.context)

    @override
    def get_screen_constructor(self):
        def c():
            payload = (
                self.context.attachment_request_to_submit
                or self.context.bug_report_to_submit
            )
            if not payload:
                raise RuntimeError("Nothing to submit")
            return SubmissionProgressScreen(
                payload,
                self.context.submitter(),
                # self.context.args, # optional
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
    def get_screen_constructor(self):
        raise RuntimeError("Impossible to construct screen in quit state")
