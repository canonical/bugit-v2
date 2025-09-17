import abc
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from pathlib import Path

from textual.screen import ModalScreen

from bugit_v2.models.bug_report import BugReport, Severity


@dataclass(slots=True, frozen=True)
class AdvanceMessage:
    """
    Indicates to the submission screen that the progress bar
    should be advanced when this message appears
    """

    message: str


class BugReportSubmitter[TAuth, TReturn](abc.ABC):
    """The bug report submitter interface"""

    # name of the submitter, used in the credential cache file name
    # should not contain spaces and slashes
    name: str
    # a pretty name for display. If None, self.name will be used
    display_name: str | None = None
    # maps the internal severity type to the ones specific to this submitter
    # see the jira submitter for an example
    severity_name_map: Mapping[Severity, str]
    # number of steps, used to show submission progress
    # NOTE: you need to hard-code this for now
    steps: int

    # If the submitter requires the user to authenticate, provide a modal here
    # that will wait until the auth is ready
    # this modal should return a pair of (authType, bool)
    # where authType is actual auth object, bool is whether to cache this
    auth_modal: type[ModalScreen[tuple[TAuth, bool] | None]] | None = None
    # the actual auth object. Useful if the auth object needs to be reused
    # for every step in the submission process instead of just during init
    auth: TAuth | None = None
    # Should the credentials collected by auth_modal be cached?
    # if auth_modal is None, this does nothing
    allow_cache_credentials: bool = False
    # Whether this concrete submitter can safely upload all attachments in
    # parallel. If false, attachments will be uploaded sequentially
    allow_parallel_upload: bool = False

    @abc.abstractmethod
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage, None, TReturn]:
        """The main bug creation sequence

        :param bug_report: bug report to submit
        :yield: Intermediate results of each step. Concrete submitters decide
                what message to yield. The number of steps here should match
                the number in self.steps
        """
        pass

    @abc.abstractmethod
    def bug_exists(self, bug_id: str) -> bool:
        pass

    @abc.abstractmethod
    def reopen(
        self, bug_id: str
    ) -> Generator[str | AdvanceMessage, None, TReturn]:
        pass

    @abc.abstractmethod
    def get_cached_credentials(self) -> TAuth | None:
        """
        Returns the cached credentials saved in the auth_modal

        Returning None will cause the submission progress screen to show
        self.auth_modal
        """
        pass

    @abc.abstractmethod
    def upload_attachment(self, attachment_file: Path) -> str | None:
        """Uploads a single attachment file

        :param attachment_dir: directory with ALL the files to upload.
                               The caller is responsible for collecting and
                               putting the desired files in this directory
        :yield: Intermediate messages or errors. Caller can decide whether to
                stop on error
        """
        pass

    @property
    @abc.abstractmethod
    def bug_url(self) -> str:
        """
        Returns the url of the newly created bug

        Concrete submitters should raise if the bug hasn't been created or if
        submit() failed
        """
        pass
