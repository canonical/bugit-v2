from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

CertificationStatus = Literal["non-blocker", "blocker"]

CERT_STATUSES: tuple[CertificationStatus, ...] = CertificationStatus.__args__

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
    io_log: str | None  # the job output


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
    base: BaseSimpleCheckboxSubmission = field(repr=False)

    def get_job_output(self, full_job_id: str) -> str | None:
        for result in self.base.results:
            if result.full_id == full_job_id:
                return result.io_log
