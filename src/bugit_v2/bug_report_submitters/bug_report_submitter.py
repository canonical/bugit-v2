import abc
from collections.abc import Generator, Mapping
from typing import Generic, TypeVar

from textual.screen import ModalScreen

from bugit_v2.models.bug_report import BugReport, Severity

A = TypeVar("A")
R = TypeVar("R")


class BugReportSubmitter(Generic[A, R], abc.ABC):
    """The bug report submitter interface"""

    # name of the submitter, just for labeling
    name: str
    # maps the internal severity type to the ones specific to this submitter
    # see the jira submitter for an example
    severity_name_map: Mapping[Severity, str]
    # number of steps, used to show submission progress
    steps: int

    # If the submitter requires the user to authenticate, provide a modal here
    # that will wait until the auth is ready
    # this modal should return a pair of (authType, bool)
    # where authType is actual auth object, bool is whether to cache this
    auth_modal: type[ModalScreen[tuple[A, bool]]] | None = None
    # the actual auth object. Useful if the auth object needs to be reused
    # for every step in the submission process instead of just during init
    auth: A | None = None
    allow_cache_credentials: bool = False

    @abc.abstractmethod
    def submit(
        self, bug_report: BugReport
    ) -> Generator[str | Exception, None, R]: ...

    @abc.abstractmethod
    def get_cached_credentials(self) -> A | None: ...
