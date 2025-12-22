from pydantic import BaseModel, EmailStr, StringConstraints
from typing_extensions import Annotated


class DutInfo(BaseModel):
    """DUT information like CID, SKU, project name, and platform tags"""

    cid: (
        Annotated[
            str,
            StringConstraints(
                pattern=r"\d{6}-\d{5}\b",  # 123456-12345
                strip_whitespace=True,
            ),
        ]
        | None
    ) = None
    sku: (
        Annotated[
            str,
            StringConstraints(
                pattern=r"^[a-zA-Z0-9\-_]+$",
                strip_whitespace=True,
            ),
        ]
        | None
    ) = None
    project: (
        Annotated[
            str,
            StringConstraints(
                pattern=r"^[a-zA-Z0-9]+$",  # no spaces in between
                to_upper=True,
                strip_whitespace=True,
            ),
        ]
        | None
    ) = None
    platform_tags: list[
        Annotated[
            str,
            StringConstraints(
                pattern=r"^[a-zA-Z0-9\-]+$",  # no spaces in between
                to_lower=True,
                strip_whitespace=True,
            ),
        ]
    ] = []
    jira_assignee: EmailStr | None = None
    lp_assignee: (
        Annotated[
            str,
            StringConstraints(
                # alphanumeric, no "lp:"
                pattern=r"^[a-zA-Z0-9]+$",
                strip_whitespace=True,
            ),
        ]
        | None
    ) = None
