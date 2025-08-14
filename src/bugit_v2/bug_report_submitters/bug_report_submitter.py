import abc
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

from textual.screen import ModalScreen

from bugit_v2.models.bug_report import BugReport, Severity


@dataclass(slots=True)
class AdvanceMessage:
    """
    Indicates to the submission screen that the progress bar
    should be advanced when this message appears
    """

    message: str


TAuth = TypeVar("TAuth")
TReturn = TypeVar("TReturn")


class BugReportSubmitter(Generic[TAuth, TReturn], abc.ABC):
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
    auth_modal: type[ModalScreen[tuple[TAuth, bool]]] | None = None
    # the actual auth object. Useful if the auth object needs to be reused
    # for every step in the submission process instead of just during init
    auth: TAuth | None = None
    allow_cache_credentials: bool = False

    @abc.abstractmethod
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | AdvanceMessage | Exception, None, TReturn]: ...

    @abc.abstractmethod
    def get_cached_credentials(self) -> TAuth | None: ...
