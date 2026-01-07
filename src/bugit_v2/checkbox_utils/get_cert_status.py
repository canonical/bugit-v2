import gzip
import json
import os
import shutil
from base64 import b64decode
from functools import lru_cache
from pathlib import Path
from sys import stderr
from typing import NamedTuple

from async_lru import alru_cache

from bugit_v2.checkbox_utils.checkbox_exec import checkbox_exec
from bugit_v2.checkbox_utils.checkbox_session import SESSION_ROOT_DIR
from bugit_v2.checkbox_utils.models import CERT_STATUSES, CertificationStatus


class TestCaseWithCertStatus(NamedTuple):
    full_id: str
    cert_status: CertificationStatus


async def list_bootstrapped_cert_status(
    test_plan: str, checkbox_env: dict[str, str] | None = None
) -> dict[str, TestCaseWithCertStatus]:
    lb_out = await checkbox_exec(
        [
            "list-bootstrapped",
            test_plan,
            "-f",
            "{full_id}\n{certification_status}\n\n",
        ],
        checkbox_env,
        10,
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


@alru_cache
async def get_certification_status(
    test_plan: str, session_path: Path | None = None
) -> dict[str, TestCaseWithCertStatus]:
    cb_env: dict[str, str] | None = None

    if session_path is not None:
        print(f"Using envs from {session_path}")
        cb_env = get_session_envs(session_path)

    sessions_before = set(os.listdir(SESSION_ROOT_DIR))
    out = await list_bootstrapped_cert_status(test_plan, cb_env)
    sessions_after = set(os.listdir(SESSION_ROOT_DIR))

    try:
        # list-bootstrapped always generates a new 'session'
        for extra_dir in sessions_after.difference(sessions_before):
            if extra_dir.startswith("checkbox-listing-ephemeral"):
                print("removing", extra_dir)
                shutil.rmtree(SESSION_ROOT_DIR / extra_dir, ignore_errors=True)
                break
    except Exception:
        pass

    return out
