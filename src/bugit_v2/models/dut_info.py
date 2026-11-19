import os
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, EmailStr, StringConstraints

from bugit_v2.utils.constants import DUT_INFO_DIR, HOST_FS


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


def _fallback_info_files() -> list[Path]:
    """dut-info saved by the DUT user, seen from the root MCP daemon.

    `bugit.dut-info` writes under the invoking user's SNAP_USER_DATA, but the
    daemon runs as root and so has a different one.
    """
    homes: list[Path] = []
    if real_home := os.environ.get("SNAP_REAL_HOME"):
        homes.append(Path(real_home))
    for root in (Path("/"), HOST_FS):
        homes.extend(sorted((root / "home").glob("*")))
        homes.append(root / "root")
    pattern = f"snap/bugit/*/{DUT_INFO_DIR.name}/dut_info.json"
    found = {f for home in homes for f in home.glob(pattern) if f.is_file()}
    return sorted(found, key=lambda f: f.stat().st_mtime, reverse=True)


def get_saved_dut_info(search_other_users: bool = False) -> DutInfo | None:
    info_file = DUT_INFO_DIR / "dut_info.json"
    if not info_file.exists():
        fallback = _fallback_info_files() if search_other_users else []
        if not fallback:
            return None
        info_file = fallback[0]

    with open(info_file) as f:
        return DutInfo.model_validate_json(f.read())
