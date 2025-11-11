from collections.abc import Sequence
from typing import Literal

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
    full_id: str
    name: str
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


class SimpleCheckboxSubmission(BaseModel):
    results: Sequence[SimpleJobResult]
