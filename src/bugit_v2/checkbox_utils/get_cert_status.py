import json
import subprocess as sp
from functools import lru_cache
from math import inf
from pathlib import Path
from typing import Any, Literal

from bugit_v2.checkbox_utils import get_checkbox_info
from bugit_v2.checkbox_utils.models import CERT_STATUSES, CertificationStatus


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
    test_plan: str, checkbox_bin_path: Path
) -> list[dict[str, Any]]:
    expand_main_test_plan_out = sp.run(
        [str(checkbox_bin_path), "expand", test_plan, "-f", "json"],
        text=True,
        capture_output=True,
    )

    if expand_main_test_plan_out.returncode != 0:
        return []

    return json.loads(expand_main_test_plan_out.stdout)


def guess_certification_status(
    test_plan: str, job_id: str
) -> tuple[CertificationStatus, Literal["exact", "guess"]] | None:
    cb_info = get_checkbox_info()

    if cb_info is None:
        return None

    checkbox_bin = str(cb_info[2].absolute())
    test_job_list = expand_test_plan(test_plan, checkbox_bin)

    if type(test_job_list) is not list:
        return None

    # each item should have the "id" and "certification-status" keys
    min_edit_dist, min_edit_dist_job_cert_status = inf, None
    for test_job in test_job_list:
        if type(test_job) is not dict:
            continue
        if "id" not in test_job or "certification-status" not in test_job:
            continue
        if test_job["certification-status"] not in CERT_STATUSES:
            continue
        if (
            test_job["id"] == job_id
            or test_job["id"] == job_id.split("::")[-1]
        ):
            if test_job.get("plugin") == "attachment":
                return None  # ignore attachment jobs
            return test_job["certification-status"], "exact"

        if (ed := edit_distance(job_id, test_job["id"])) < min_edit_dist:
            min_edit_dist = ed
            min_edit_dist_job_cert_status = test_job["certification-status"]

    if min_edit_dist_job_cert_status is None:
        return None
    else:
        return min_edit_dist_job_cert_status, "guess"
