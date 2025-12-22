from pydantic import BaseModel, EmailStr, StringConstraints
from typing_extensions import Annotated

from bugit_v2.utils.constants import DUT_INFO_DIR


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
    tags: list[
        Annotated[
            str,
            StringConstraints(
                pattern=r"^[a-zA-Z0-9\-]+$",  # no spaces in between
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


def get_saved_dut_info() -> DutInfo | None:
    info_file = DUT_INFO_DIR / "dut_info.json"
    if not info_file.exists():
        return None

    with open(info_file) as f:
        return DutInfo.model_validate_json(f.read())
