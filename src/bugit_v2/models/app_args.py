from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True)
class AppArgs:
    """The global constant holding the values from the CLI"""

    submitter: Literal["lp", "jira"]
    # hold onto these values to pre-fill them in the bug report
    cid: str | None = None
    sku: str | None = None
