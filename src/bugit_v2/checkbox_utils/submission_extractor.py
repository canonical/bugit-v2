import json
import tarfile
from pathlib import Path

from bugit_v2.checkbox_utils.models import SimpleCheckboxSubmission


def read_simple_submission(submission_path: Path) -> SimpleCheckboxSubmission:
    with tarfile.open(submission_path, "r:xz") as f:
        json_io_reader = f.extractfile("submission.json")
        assert (
            json_io_reader
        ), f"submission.json does not exist in {submission_path}"
        return SimpleCheckboxSubmission.model_validate(
            json.load(json_io_reader), extra="allow"
        )
