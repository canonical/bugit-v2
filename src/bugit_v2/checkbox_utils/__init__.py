import base64
import gzip
import json
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal, TypedDict, cast, final

from typing_extensions import override

from bugit_v2.utils import is_snap

SESSION_ROOT_DIR: Final = Path("/var/tmp/checkbox-ng/sessions")


def get_checkbox_version() -> str | None:
    if is_snap():
        if Path(
            deb_checkbox := "/var/lib/snapd/hostfs/usr/bin/checkbox-cli"
        ).exists():
            # host is using debian checkbox
            return subprocess.check_output(
                [deb_checkbox, "--version"],
                text=True,
                env={
                    "PYTHONPATH": "/var/lib/snapd/hostfs/usr/lib/python3/dist-packages"
                },
            ).strip()
        elif (
            Path(
                snap_checkbox := "/var/lib/snapd/hostfs/snap/bin/checkbox.checkbox-cli"
            )
        ).exists():
            return subprocess.check_output(
                [snap_checkbox, "--version"], text=True
            ).strip()
    else:
        checkbox_bin = shutil.which("checkbox-cli") or shutil.which(
            "checkbox.checkbox-cli"
        )
        if checkbox_bin:
            return subprocess.check_output(
                [checkbox_bin, "--version"], text=True
            ).strip()


def get_valid_sessions() -> list[Path]:
    """Get a list of valid sessions in /var/tmp/checkbox-ng

    This is achieved by looking at which session directory has non-empty
    io-logs. If it's empty, it's either tossed by checkbox or didn't even
    reach the test case where it dumps the udev database, thus invalid
    """
    if not SESSION_ROOT_DIR.exists():
        return []
    valid_session_dirs: list[Path] = []
    for d in os.listdir(SESSION_ROOT_DIR):
        try:
            if len(os.listdir(SESSION_ROOT_DIR / d / "io-logs")) != 0:
                valid_session_dirs.append(SESSION_ROOT_DIR / d)
        except FileNotFoundError:
            continue
    return valid_session_dirs


JobOutcome = Literal["pass", "fail", "skip"]


class JobOutput(TypedDict):
    stdout: str
    stderr: str
    comments: str


class JobResult(TypedDict):
    comments: Sequence[str] | None
    execution_duration: float
    io_log_filename: str
    outcome: JobOutcome
    return_code: int


SessionResults = Mapping[str, Sequence[JobResult]]


@final
class Session:
    """
    A Checkbox session
    """

    description: str
    testplan_id: str
    session_path: Path
    failed_jobs: Sequence[str]

    def __init__(self, session_path: Path):
        self.session_path = session_path

        if not self.session_path.is_dir():
            raise FileNotFoundError(
                f"Directory '{session_path}' does not exist"
            )
        if not Path(self.session_path / "session").is_file():
            raise FileNotFoundError(
                f"Session file not found in '{session_path}'."
            )
        self.session_json = self.get_session_json()

        if self.session_json["session"]["metadata"]["app_blob"]:
            app_blob = json.loads(
                base64.b64decode(
                    self.session_json["session"]["metadata"]["app_blob"]
                )
            )
            try:
                self.description = app_blob["description"]
                self.testplan_id = app_blob["testplan_id"]
            except KeyError as e:
                print(f"{self} is missing field(s): {', '.join(e.args)}.")
        else:
            print(f"{self} does not contain valid information.")
        self.failed_jobs = self.get_run_jobs()

    @override
    def __repr__(self):
        return f"<Session {self.session_path}>"

    def get_session_json(self):
        with gzip.open(self.session_path / "session") as arc:
            session_json = json.load(arc)

        return session_json

    def get_job_output(self, job_id: str) -> JobOutput | None:
        """
        Get the output (stdout, stderr, comments) of a given job

        :param job_id: job ID user wish to retrieve the record logs from
        :type job_id: str

        :returns: a dictionary with the standard output (stdout) and standard
        error (stderr) logs as well as user-input comments
        :rtype: dict
        """
        try:
            # A job can be retried but the io-logs filename is always the same,
            # so arbitrarily get data from last retry.
            io_log_filename: str | None = self.session_json["session"][
                "results"
            ][job_id][-1].get("io_log_filename")
            comments: str = self.session_json["session"]["results"][job_id][
                -1
            ].get("comments", "")
            print(comments, type(comments))

            if io_log_filename:
                stdout_filename = io_log_filename.replace(
                    "record.gz", "stdout"
                )
                stderr_filename = io_log_filename.replace(
                    "record.gz", "stderr"
                )
                with open(self.session_path / stdout_filename) as f:
                    stdout = f.read().strip()
                with open(self.session_path / stderr_filename) as f:
                    stderr = f.read().strip()
                return JobOutput(
                    stdout=stdout, stderr=stderr, comments=comments
                )
            else:
                print(f"Job `{job_id}` does not have associated log records.")
                return JobOutput(stdout="", stderr="", comments=comments)

        except KeyError:
            print(f"Current session does not have job `{job_id}`.")
            return None
        except FileNotFoundError as e:
            print(f"Corrupted session with missing file {e}")
            return None

    def has_failed_jobs(self) -> bool:
        results: dict[str, Any] = self.session_json["session"]["results"]
        for job in results:
            # a job can be retried; picking the last retry.
            if results[job][-1]["outcome"] == "fail":
                return True
        return False

    def get_run_jobs(
        self, status_filter: Sequence[JobOutcome] = ("fail",)
    ) -> list[str]:
        """
        Get list of jobs that have been run (failed ones by default)

        :param status: only select jobs with these statuses

        :returns: list of corresponding run jobs
        :rtype: list
        """
        results = cast(SessionResults, self.session_json["session"]["results"])
        return [
            job
            for job, outcome in results.items()
            if outcome[-1]["outcome"] in status_filter
        ]
