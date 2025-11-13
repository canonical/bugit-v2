from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from bugit_v2.checkbox_utils.models import SimpleCheckboxSubmission
from bugit_v2.utils.constants import NullSelection


@dataclass(slots=True, frozen=True, kw_only=True)
class AppArgs:
    """The global constant holding the values from the CLI"""

    submitter: Literal["lp", "jira"]
    # read from this submission tarball
    # instead of asking the user to select session/job
    checkbox_submission: (
        SimpleCheckboxSubmission
        | Literal[NullSelection.NO_CHECKBOX_SUBMISSION]
    ) = NullSelection.NO_CHECKBOX_SUBMISSION
    # comment on an existing bug instead of making a new one
    bug_to_reopen: str | None = None
    # hold onto these values to pre-fill them in the bug report
    cid: str | None = None  # has a validator
    sku: str | None = None
    project: str | None = None  # has a validator
    assignee: str | None = None  # has a validator
    platform_tags: Sequence[str] = ()
    tags: Sequence[str] = ()
