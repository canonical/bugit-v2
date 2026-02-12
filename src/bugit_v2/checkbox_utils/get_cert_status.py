import asyncio
import gzip
import json
import logging
import os
import shutil
import csv
from base64 import b64decode
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple
import re

from bugit_v2.checkbox_utils.checkbox_exec import checkbox_exec, get_checkbox_info
from bugit_v2.checkbox_utils.checkbox_session import SESSION_ROOT_DIR
from bugit_v2.checkbox_utils.models import CERT_STATUSES, CertificationStatus
from bugit_v2.utils import slugify
from bugit_v2.utils.constants import DISK_CACHE_DIR

logger = logging.getLogger(__name__)

CERT_STATUS_FILE_PREFIX = "cert_status_cache"
MISSING_TEMPLATE_ID = "<missing template-id>"


class TestCaseWithCertStatus(NamedTuple):
    full_id: str
    cert_status: CertificationStatus


def _template_to_regex(template_str: str) -> str:
    escaped_template = re.escape(template_str)
    regex_pattern = re.sub(r"\\\{.*?\\\}", ".*", escaped_template)
    return f"^{regex_pattern}$"


async def _cache_cert_status_to_file(
    test_plan: str, filepath: Path, checkbox_env: dict[str, str] | None = None
) -> None:
    """Writes a fresh cert status csv file

    :param test_plan: the test plan with namespace
    :param checkbox_env: optional env to use when bootstrapping
    :raises RuntimeError: if checkbox-cli list-bootstrapped failed
    """
    lb_out = await checkbox_exec(
        [
            "list-bootstrapped",
            test_plan,
            "-f",
            r"{full_id}\n{template-id}\n{certification_status}\n\n",
        ],
        checkbox_env,
        30,
    )

    if lb_out.returncode != 0:
        logger.error(lb_out.stderr)
        raise RuntimeError(
            f"Failed to run checkbox-cli list-bootstrapped {repr(lb_out)}"
        )

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f, delimiter=" ", quotechar="|", quoting=csv.QUOTE_MINIMAL)
        # split by empty line
        raw_cases = lb_out.stdout.strip().split("\n\n")
        for raw_case in raw_cases:
            lines = raw_case.splitlines()

            if len(lines) != 3 or lines[2] not in CERT_STATUSES:
                logger.error("Bad cert status group")
                logger.error(lines)
                continue

            writer.writerow(lines)


async def _get_cert_status_from_file(
    filepath: Path, job_id: str
) -> TestCaseWithCertStatus | None:
    """Use the cached csv file as a guide to find the cert status

    :param filepath: where is the csv
    :param job_id: full job id with namespace
    :return: TestCaseWithCertStatus
    """
    with open(filepath, "r", newline="") as f:
        reader = csv.reader(f, delimiter=" ", quotechar="|", quoting=csv.QUOTE_MINIMAL)

        for line in reader:
            if len(line) < 3:
                logger.error("Bad cert status group len")
                logger.error(line)
                continue

            full_id, template_id, cert_status = line
            if cert_status not in CERT_STATUSES:
                logger.error("Bad cert status group")
                logger.error(line)
                continue

            if full_id == job_id:
                return TestCaseWithCertStatus(job_id, cert_status)

        f.seek(0)  # rewind to the top
        reader = csv.reader(f, delimiter=" ", quotechar="|", quoting=csv.QUOTE_MINIMAL)
        for line in reader:
            if len(line) < 3:
                logger.error("Bad cert status group len")
                logger.error(line)
                continue

            full_id, template_id, cert_status = line
            if cert_status not in CERT_STATUSES:
                logger.error("Bad cert status group")
                logger.error(line)
                continue

            if template_id == MISSING_TEMPLATE_ID:
                continue

            # failed to match the id exactly
            # see if the template id can match
            try:
                out = await checkbox_exec(["show", template_id, "--exact"], timeout=5)
            except asyncio.TimeoutError:
                continue

            if out.returncode != 0:
                continue

            # output from 'show' is usually really small, .splitlines should be ok
            for line in out.stdout.splitlines():
                # this gets us the 'real' template id before slugify()
                if line.startswith("id:"):
                    real_template_id = line.strip().removeprefix("id:").strip()
                    template_id_regex = _template_to_regex(real_template_id)
                    if re.match(
                        template_id_regex, job_id.split("::", maxsplit=1)[-1].strip()
                    ):
                        return TestCaseWithCertStatus(job_id, cert_status)


@lru_cache()
def get_session_envs(session_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    env_gz = (
        session_path / "io-logs" / "com.canonical.certification__environment.record.gz"
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


async def get_certification_status(
    test_plan: str, job_id: str, session_path: Path | None = None
) -> TestCaseWithCertStatus | None:
    logger.info(f"Getting all cert status values for {test_plan}")
    cb_info = await get_checkbox_info()

    if cb_info is None:
        logger.warning(
            "get_certification_status was called while no checkbox is present on the system"
        )
        return None

    cache_file = (
        DISK_CACHE_DIR
        / slugify(cb_info.version)
        / f"{CERT_STATUS_FILE_PREFIX}_{slugify(test_plan)}.csv"
    )
    try:
        out = await _get_cert_status_from_file(cache_file, job_id)
        remove_listing_ephemeral_dirs()
        return out
    except Exception:
        remove_listing_ephemeral_dirs()
        cb_env = None
        if session_path:
            logger.debug(f"Using envs from {session_path}")
            cb_env = get_session_envs(session_path)

        await _cache_cert_status_to_file(test_plan, cache_file, cb_env)
        return await _get_cert_status_from_file(cache_file, job_id)


def remove_listing_ephemeral_dirs() -> None:
    try:
        # list-bootstrapped always generates a new 'session'
        for extra_dir in os.listdir(SESSION_ROOT_DIR):
            if extra_dir.startswith("checkbox-listing-ephemeral"):
                logger.info(f"Removing checkbox-listing temp dir {extra_dir}")
                shutil.rmtree(SESSION_ROOT_DIR / extra_dir, ignore_errors=True)
    except Exception:
        pass
