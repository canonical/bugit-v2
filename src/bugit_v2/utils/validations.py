import os
import re
import subprocess as sp
from pathlib import Path

import pydantic

from bugit_v2.checkbox_utils.submission_extractor import read_simple_submission
from bugit_v2.utils import is_snap
from bugit_v2.utils.constants import (
    AUTOSAVE_DIR,
    DISK_CACHE_DIR,
    DUT_INFO_DIR,
    VISUAL_CONFIG_DIR,
    NullSelection,
)


def bugit_is_in_devmode() -> bool:
    # technically bugit won't even install if --devmode is not specified
    # because of the sudoer hook
    # but it's possible to go from devmode (with sudoer hook succeeded)
    # into strict mode and this check will kick in
    try:
        snap_list = sp.check_output(
            ["snap", "list"], text=True
        )  # do not use snap info, it needs the internet
    except PermissionError:
        return False  # can't call the snap command in strict confinement

    for line in snap_list.splitlines():
        if "bugit" in line and "devmode" in line:
            return True

    return False


def sudo_devmode_check():
    """Check for sudo and --devmode

    :raises SystemExit: Not using sudo
    :raises SystemExit: Not installed with --devmode
    """
    if os.getuid() != 0:
        raise SystemExit("Please run this app with \033[4msudo\033[0m")

    if is_snap() and not bugit_is_in_devmode():
        raise SystemExit(
            "Bugit is not installed in devmode. Please reinstall with --devmode specified."
        )


def checkbox_submission_check(checkbox_submission: Path | None):
    """Small wrapper over read_simple_submission to raise system exist instead
    of a huge call trace

    This is only intended to be used at the beginning of the app

    :param checkbox_submission: path to the submission file
    :raises SystemExit: when the file is invalid.
    :return: None if no path, SimpleCheckboxSubmission if validation passed
    """
    if not checkbox_submission:
        return NullSelection.NO_CHECKBOX_SUBMISSION
    try:
        return read_simple_submission(checkbox_submission)
    except pydantic.ValidationError as e:
        raise SystemExit(f"Broken checkbox submission. Reason: {e}")


def is_cid(cid: str) -> bool:
    return re.compile(r"\d{6}-\d{5}\b").fullmatch(cid) is not None


def ensure_all_directories_exist() -> None:
    if not AUTOSAVE_DIR.exists():
        os.makedirs(AUTOSAVE_DIR)
    if not VISUAL_CONFIG_DIR.exists():
        os.makedirs(VISUAL_CONFIG_DIR)
    if not DUT_INFO_DIR.exists():
        os.makedirs(DUT_INFO_DIR)
    if not DISK_CACHE_DIR.exists():
        os.makedirs(DISK_CACHE_DIR)
