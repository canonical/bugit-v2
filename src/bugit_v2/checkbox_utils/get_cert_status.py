import gzip
import json
from base64 import b64decode
from functools import lru_cache
from math import inf
from pathlib import Path
from sys import stderr
from typing import Any, Literal, NamedTuple

from bugit_v2.checkbox_utils.checkbox_exec import (
    checkbox_exec,  # pyright: ignore[reportUnknownVariableType]
)
from bugit_v2.checkbox_utils.models import CERT_STATUSES, CertificationStatus


class TestCaseWithCertStatus(NamedTuple):
    full_id: str
    cert_status: CertificationStatus


@lru_cache
def edit_distance(word1: str, word2: str) -> int:
    m = len(word1)
    n = len(word2)

    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        dp[i][0] = i

    for j in range(1, n + 1):
        dp[0][j] = j

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if word1[i - 1] == word2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = (
                    min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1]) + 1
                )

    return dp[m][n]


@lru_cache
def expand_test_plan(test_plan: str) -> list[dict[str, Any]]:
    """Runs checkbox-cli expand for a given test plan

    The result of this function is lru cached.
    Directly call checkbox_exec if fresh results is needed

    :param test_plan: the test plan to expand. Include the namespace
    :raises RuntimeError: when `checkbox-cli expand` fails
    :raises json.decoder.JSONDecodeError: from json.loads
    :return: list of dicts, each dict is a checkbox unit
    """
    out = checkbox_exec(["expand", test_plan, "-f", "json"])
    if out.returncode != 0:
        raise RuntimeError(f"Failed to run checkbox-cli expand {repr(out)}")

    return json.loads(out.stdout)


def list_bootstrapped_cert_status(
    test_plan: str, checkbox_env: dict[str, str] | None = None
) -> dict[str, TestCaseWithCertStatus]:
    lb_out = checkbox_exec(
        [
            "list-bootstrapped",
            test_plan,
            "-f",
            "{full_id}\n{certification_status}\n\n",
        ],
        checkbox_env,
    )

    if lb_out.returncode != 0:
        raise RuntimeError(
            f"Failed to run checkbox-cli list-bootstrapped {repr(lb_out)}"
        )

    out: dict[str, TestCaseWithCertStatus] = {}

    # split by empty line
    raw_cases = lb_out.stdout.strip().split("\n\n")
    for raw_case in raw_cases:
        lines = raw_case.splitlines()

        if len(lines) != 2 or lines[1] not in CERT_STATUSES:
            print("Bad group", lines, file=stderr)
            continue

        out[lines[0]] = TestCaseWithCertStatus(
            full_id=lines[0], cert_status=lines[1]
        )

    return out


@lru_cache()
def get_session_envs(session_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    env_gz = (
        session_path
        / "io-logs"
        / "com.canonical.certification__environment.record.gz"
    )

    if not env_gz.exists():
        # possible to not exist in a valid session
        return {}

    with gzip.open(env_gz) as f:
        for line in f:
            elems = json.loads(line.decode().strip())
            if len(elems) != 3:
                continue
            env_elems = b64decode(elems[2]).decode().split(":")

            if len(env_elems) != 2:
                continue

            k, v = env_elems[0].strip(), env_elems[1].strip()
            if not k.isupper():
                # handle the SUDO_COMMAND line
                continue
            if k == "PATH":
                continue
            out[k] = v

    return out


@lru_cache
def get_certification_status(
    test_plan: str, session_path: Path | None = None
) -> dict[str, TestCaseWithCertStatus] | None:
    cb_env: dict[str, str] | None = None

    if session_path is not None:
        print(f"Using envs from {session_path}")
        cb_env = get_session_envs(session_path)

    return list_bootstrapped_cert_status(test_plan, cb_env)


def guess_certification_status(
    test_plan: str, job_id: str
) -> tuple[CertificationStatus, str, Literal["exact", "guess"]] | None:
    """Guess the certification status of a job in the given test plan

    Uses Levenshtein edit distance to determine similarity
    TODO: find a way to directly query this

    :return:
        None if:
            1. not applicable (like attachment jobs)
            2. the job is not in the test plan
        Tuple if:
            1. Found an exact match
            2. Found an approximate match
        Use the 2nd tuple element to see if it's an exact match or a guess
    """

    test_job_list = expand_test_plan(test_plan)

    if type(test_job_list) is not list:
        return None

    # each item should have the "id" and "certification-status" keys
    min_edit_dist = inf
    best_match_job_name: str | None = None
    min_edit_dist_job_cert_status: CertificationStatus | None = None

    for test_job in test_job_list:
        if type(test_job) is not dict:
            continue
        if "id" not in test_job or "certification-status" not in test_job:
            continue
        if test_job["certification-status"] not in CERT_STATUSES:
            continue
        if type(test_job["id"]) is not str:  # pyright: ignore[reportAny]
            continue

        if (
            test_job["id"] == job_id
            or test_job["id"] == job_id.split("::")[-1]
        ):
            if test_job.get("plugin") == "attachment":
                return None  # ignore attachment jobs
            return test_job["certification-status"], test_job["id"], "exact"

        if (ed := edit_distance(job_id, test_job["id"])) < min_edit_dist:
            min_edit_dist = ed
            best_match_job_name = test_job["id"]
            min_edit_dist_job_cert_status = test_job["certification-status"]

    if min_edit_dist_job_cert_status is None:
        return None
    else:
        assert best_match_job_name is not None
        return min_edit_dist_job_cert_status, best_match_job_name, "guess"
