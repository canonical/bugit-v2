import abc
import base64
import gzip
import json
import logging
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final, TypedDict, cast, final, override

import ijson

from bugit_v2.checkbox_utils.models import JobOutcome

logger = logging.getLogger(__name__)
SESSION_ROOT_DIR: Final = Path("/var/tmp/checkbox-ng/sessions")


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
        except (FileNotFoundError, NotADirectoryError):
            continue
    return valid_session_dirs


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


class AbstractCheckboxSession(abc.ABC):
    """
    Abstract Checkbox session reader
    - Concrete classes can implement different ways to read the session
    """

    @property
    @abc.abstractmethod
    def testplan_id(self) -> str:
        pass

    def get_session_json(self) -> dict[str, Any]:
        """Reads the entire 'session' json into memory
        This may explode on low memory systems, avoid if possible

        :return: The entire session json
        """
        with gzip.open(self.session_path / "session") as arc:
            session_json = json.load(arc)

        return session_json

    @property
    @abc.abstractmethod
    def session_path(self) -> Path:
        pass

    @abc.abstractmethod
    def get_job_output(self, job_id: str) -> JobOutput | None:
        pass

    @abc.abstractmethod
    def list_jobs(
        self, status_filter: Sequence[JobOutcome] | None = None
    ) -> Sequence[str]:
        pass


@final
class InMemoryCheckboxSession(AbstractCheckboxSession):
    def __init__(self, session_path: Path):
        self._session_path = session_path

        if not self.session_path.is_dir():
            raise FileNotFoundError(f"Directory '{session_path}' does not exist")
        if not Path(self.session_path / "session").is_file():
            raise FileNotFoundError(f"Session file not found in '{session_path}'.")
        self._session_json = self.get_session_json()

        app_blob = json.loads(
            base64.b64decode(self._session_json["session"]["metadata"]["app_blob"])
        )
        try:
            self._testplan_id = str(app_blob["testplan_id"])
        except KeyError as e:
            raise KeyError(
                f"{self} is missing field(s): {', '.join(e.args)}.",
            )

    @override
    def __repr__(self):
        return f"<Session {self.session_path}>"

    @override
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
            io_log_filename: str | None = self._session_json["session"]["results"][
                job_id
            ][-1].get("io_log_filename")
            comments: str = (
                self._session_json["session"]["results"][job_id][-1].get("comments", "")
                or ""  # the key can exist while the value is None
            )

            if io_log_filename:
                stdout_filename = io_log_filename.replace("record.gz", "stdout")
                stderr_filename = io_log_filename.replace("record.gz", "stderr")
                with open(self.session_path / stdout_filename) as f:
                    stdout = f.read().strip()
                with open(self.session_path / stderr_filename) as f:
                    stderr = f.read().strip()
                return JobOutput(stdout=stdout, stderr=stderr, comments=comments)
            else:
                logger.warning(f"Job `{job_id}` does not have associated log records.")
                return JobOutput(stdout="", stderr="", comments=comments)

        except KeyError:
            logger.warning(f"Current session does not have job `{job_id}`.")
            return None
        except FileNotFoundError as e:
            logger.error(f"Corrupted session with missing file {e}")
            return None

    @override
    def list_jobs(
        self, status_filter: Sequence[JobOutcome] | None = None
    ) -> Sequence[str]:
        """
        Get list of jobs that have been run (failed ones by default)

        :param status: only select jobs with these statuses

        :returns: list of corresponding run jobs
        :rtype: list
        """
        results = cast(SessionResults, self._session_json["session"]["results"])
        return [
            job
            for job, outcome in results.items()
            if not status_filter or outcome[-1]["outcome"] in status_filter
        ]

    @property
    @override
    def session_path(self) -> Path:
        return self._session_path

    @property
    @override
    def testplan_id(self) -> str:
        return self._testplan_id


@final
class IterativeCheckboxSession(AbstractCheckboxSession):
    """
    A Checkbox session
    """

    def __init__(self, session_path: Path):
        if not session_path.is_dir():
            raise FileNotFoundError(f"Directory '{session_path}' does not exist")
        if not Path(session_path / "session").is_file():
            raise FileNotFoundError(f"Session file not found in '{session_path}'.")

        self._session_path = session_path
        self._testplan_id = self.get_testplan_id()

    def get_testplan_id(self) -> str:
        with gzip.open(self.session_path / "session", "rb") as arc:
            for app_blob_b64 in ijson.items(arc, "session.metadata.app_blob"):
                app_blob = json.loads(base64.b64decode(app_blob_b64))
                try:
                    return str(app_blob["testplan_id"])
                except KeyError as e:
                    raise KeyError(f"{self} is missing field(s): {', '.join(e.args)}.")
        raise KeyError(f"{self} does not contain 'session.metadata.app_blob'.")

    @override
    def list_jobs(self, status_filter: Sequence[JobOutcome] | None = None) -> list[str]:
        """
        Get list of jobs that have been run (failed ones by default)

        :param status: only select jobs with these statuses

        :returns: list of corresponding run jobs
        :rtype: list
        """
        matched_jobs: list[str] = []
        with gzip.open(self.session_path / "session", "rb") as arc:
            for job_id, results in ijson.kvitems(arc, "session.results"):
                if not status_filter:
                    matched_jobs.append(job_id)
                    continue
                if results and results[-1]["outcome"] in status_filter:
                    matched_jobs.append(job_id)
        return matched_jobs

    @override
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
            last: dict[str, Any] | None = None
            with gzip.open(self.session_path / "session", "rb") as arc:
                for jid, results in ijson.kvitems(arc, "session.results"):
                    if jid == job_id:
                        # A job can be retried but the io-logs filename is always
                        # the same, so arbitrarily get data from last retry.
                        last = results[-1]
                        break

            if last is None:
                logger.warning(f"Current session does not have job `{job_id}`.")
                return None

            io_log_filename: str | None = last.get("io_log_filename")
            comments = str(last.get("comments", "") or "")

            if io_log_filename:
                stdout_filename = io_log_filename.replace("record.gz", "stdout")
                stderr_filename = io_log_filename.replace("record.gz", "stderr")
                with open(self.session_path / stdout_filename) as f:
                    stdout = f.read().strip()
                with open(self.session_path / stderr_filename) as f:
                    stderr = f.read().strip()
                return JobOutput(stdout=stdout, stderr=stderr, comments=comments)
            else:
                logger.warning(f"Job `{job_id}` does not have associated log records.")
                return JobOutput(stdout="", stderr="", comments=comments)

        except FileNotFoundError as e:
            logger.error(f"Corrupted session with missing file {e}")
            return None

    @property
    @override
    def session_path(self) -> Path:
        return self._session_path

    @property
    @override
    def testplan_id(self) -> str:
        return self._testplan_id


CheckboxSession = IterativeCheckboxSession
