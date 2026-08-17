import json
import tarfile
from pathlib import Path

from bugit_v2.checkbox_utils.models import (
    BaseSimpleCheckboxSubmission,
    SimpleCheckboxSubmission,
)


def read_simple_submission(submission_path: Path) -> SimpleCheckboxSubmission:
    with tarfile.open(submission_path, "r:xz") as f:
        # .extractfile raises KeyError if the file is not in the tar
        json_io_reader = f.extractfile("submission.json")
        if not json_io_reader:
            raise FileNotFoundError(
                f"submission.json exists, but it's not a regular file in {submission_path}"
            )
        return SimpleCheckboxSubmission(
            submission_path.absolute(),
            BaseSimpleCheckboxSubmission.model_validate(
                json.load(json_io_reader), extra="allow"
            ),
        )
