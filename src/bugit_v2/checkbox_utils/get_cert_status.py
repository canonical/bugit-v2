import configparser as cp
import json
import shutil
import subprocess as sp
from functools import lru_cache
from math import inf
from pathlib import Path
from sys import stderr
from tempfile import TemporaryDirectory
from typing import Any, Literal

from bugit_v2.checkbox_utils import CheckboxInfo, get_checkbox_info
from bugit_v2.checkbox_utils.models import CERT_STATUSES, CertificationStatus
from bugit_v2.utils import is_snap
from bugit_v2.utils.constants import HOST_FS


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
def expand_test_plan(
    test_plan: str, checkbox_info: CheckboxInfo
) -> list[dict[str, Any]]:
    if checkbox_info.type == "snap" or not is_snap():
        # snap checkbox is easy, just call checkbox-cli expand
        # for both snap bugit and pipx bugit
        expand_main_test_plan_out = sp.run(
            [str(checkbox_info.bin_path), "expand", test_plan, "-f", "json"],
            text=True,
            capture_output=True,
        )

        if expand_main_test_plan_out.returncode != 0:
            return []

        return json.loads(expand_main_test_plan_out.stdout)
    else:
        # snap bugit + deb checkbox case
        # try to find .provider files under /usr/share/plainbox-providers-1/
        # and prepend /var/lib/snapd/hostfs to these keys
        """
        [PlainBox Provider]
        bin_dir = /usr/lib/checkbox-provider-base/bin
        data_dir = /usr/share/checkbox-provider-base/data
        units_dir = /usr/share/checkbox-provider-base/units
        """
        with TemporaryDirectory() as temp_dir:
            for src_file in Path(
                "/var/lib/snapd/hostfs/usr/share/plainbox-providers-1/"
            ).iterdir():
                dst_file = shutil.copy(src_file, temp_dir)
                provider_config = cp.ConfigParser()
                provider_config.read(dst_file)

                for key in ("bin_dir", "data_dir", "units_dir"):
                    if key not in provider_config["PlainBox Provider"]:
                        print("No such key", key, "in", src_file)
                        continue

                    new_path = HOST_FS / (
                        provider_config["PlainBox Provider"][key]
                        # vvvvvv prevent pathlib from treating it as abs path
                    ).lstrip("/")

                    if not new_path.exists():
                        print("No such path", new_path, file=stderr)
                        continue

                    provider_config["PlainBox Provider"][key] = str(new_path)
                    with open(dst_file, "w") as f:
                        provider_config.write(f)

            expand_main_test_plan_out = sp.run(
                [
                    str(checkbox_info.bin_path),
                    "expand",
                    test_plan,
                    "-f",
                    "json",
                ],
                text=True,
                capture_output=True,
                env={
                    "PYTHONPATH": "/var/lib/snapd/hostfs/usr/lib/python3/dist-packages",
                    "PROVIDERPATH": str(
                        Path(temp_dir).absolute(),
                    ),
                },
            )

            if expand_main_test_plan_out.returncode != 0:
                return []

            return json.loads(expand_main_test_plan_out.stdout)


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
    cb_info = get_checkbox_info()

    if cb_info is None:
        return None

    test_job_list = expand_test_plan(test_plan, cb_info)

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
