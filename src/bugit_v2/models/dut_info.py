from pydantic import BaseModel, StringConstraints
from typing_extensions import Annotated


class DutInfo(BaseModel):
    """DUT information like CID, SKU, project name, and platform tags"""

    cid: Annotated[
        str,
        StringConstraints(
            pattern=r"\d{6}-\d{5}\b",
            strip_whitespace=True,
        ),
    ]
    sku: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
        ),
    ]
    project: Annotated[
        str,
        StringConstraints(
            to_upper=True,
            strip_whitespace=True,
        ),
    ]
    platform_tags: list[
        Annotated[
            str,
            StringConstraints(
                to_lower=True,
                strip_whitespace=True,
            ),
        ]
    ]
