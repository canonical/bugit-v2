from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from attr import field
from pydantic import BaseModel

type CertificationStatus = Literal["non-blocker", "blocker"]
type JobOutcome = Literal[
    "pass",
    "fail",
    "skip",
    "not-supported",
    "not-implemented",
    "undecided",
    "crash",
]


class SimpleJobResult(BaseModel):
    category: str
    category_id: str
    certification_status: CertificationStatus
    comments: str | None
    full_id: str  # the job id with namespace
    name: str  # display name
    outcome: JobOutcome
    project: str  # provider name basically
    status: str  # not sure what this is


class AttachmentResult(BaseModel):
    category: str
    category_id: str
    certification_status: CertificationStatus
    comments: Sequence[str] | None
    duration: float
    full_id: str
    id: str
    io_log: str
    name: str
    outcome: JobOutcome


class BaseSimpleCheckboxSubmission(BaseModel):
    results: Sequence[SimpleJobResult]
    testplan_id: str


@dataclass(slots=True, frozen=True)
class SimpleCheckboxSubmission:
    submission_path: Path
    base: BaseSimpleCheckboxSubmission = field(  # pyright: ignore[reportAny]
        repr=lambda f: str(  # pyright: ignore[reportAny]
            type(f)  # pyright: ignore[reportUnknownArgumentType, reportAny]
        )
    )
