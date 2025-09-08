"""
Implement concrete log collectors here
--
Each log collector should be an instance of LogCollector. The big assumption
here is that each log collectors is a (slow-running) function and is *independent*
from all other collectors.
"""

import os
import re
import shutil
import subprocess as sp
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bugit_v2.models.bug_report import BugReport, LogName


@dataclass(slots=True)
class LogCollector:
    # internal name, alphanumeric or dashes only
    name: LogName
    # the function that actually collects the logs
    collect: Callable[
        [Path, BugReport],
        str | None,  # (target_dir: Path) -> optional result string
        # if returns None, a generic success message is logged to the screen
        # errors should be raised as regular exceptions
    ]
    display_name: str  # the string to show in report collector
    # should this log be collected by default?
    # (set to false for ones that are uncommon or very slow)
    collect_by_default: bool = True
    # provide a way for the user to manually collect the logs if this collector
    # failed at runtime
    manual_collection_command: str | None = None


def sos_report(target_dir: Path, _: BugReport):
    assert target_dir.is_dir()
    out = sp.check_output(
        ["sos", "report", "--batch", f"--tmp-dir={target_dir}"],
        text=True,
        timeout=600,  # just in case
    )
    # remove the sha file
    for file in target_dir.iterdir():
        if file.name.startswith("sosreport") and file.name.endswith(".sha256"):
            os.remove(file)
    return out


def oem_getlogs(target_dir: Path, _: BugReport):
    assert target_dir.is_dir()
    out = sp.check_output(["oem-getlogs"], text=True)
    if (
        log_file_match := re.search(r"oemlogs.*\.apport\.gz", out)
    ) is not None:
        file_path = Path(log_file_match.group(0))
        assert file_path.exists(), f"{file_path} doesn't exist!"
        shutil.move(file_path, target_dir)
    else:
        raise FileNotFoundError(
            "oem-getlogs finished, but didn't find a filename matching"
            + "oemlogs.*\\.apport\\.gz in its output"
        )


def pack_checkbox_session(target_dir: Path, bug_report: BugReport) -> str:
    with tarfile.open(target_dir / "checkbox_session.tar.gz", "w:gz") as f:
        f.add(bug_report.checkbox_session.session_path)
    return f"Added checkbox session to {target_dir}"


def nvidia_bug_report(target_dir: Path, _: BugReport) -> str:
    return sp.check_output(
        [
            "nvidia-bug-report.sh",
            "--extra-system-data",
            "--output-file",
            str(target_dir / "nvidia-bug-report.log"),
        ],
        text=True,
    )


def acpidump(target_dir: Path, _: BugReport) -> str:
    return sp.check_output(
        ["acpidump", "-o", str(target_dir.absolute() / "acpidump.log")],
        text=True,
    )


def dmesg_of_current_boot(target_dir: Path, _: BugReport) -> str:
    with open("/proc/sys/kernel/random/boot_id") as boot_id_file:
        boot_id = boot_id_file.read().strip().replace("-", "")
        with open(target_dir / f"dmesg-{boot_id}.log", "w") as f:
            sp.check_call(["dmesg"], stdout=f, stderr=sp.DEVNULL, text=True)
            return f"Saved dmesg logs of boot {boot_id} to {f.name}"


def inxi(target_dir: Path, _: BugReport) -> str:
    with open(target_dir / "inxi-dump.txt", "w") as f:
        sp.check_call(
            ["inxi", "--tty", "-ACDEGJLMNSxm"],
            stdout=f,
            stderr=sp.DEVNULL,
            text=True,
        )
        return f"Saved inxi dump  to {f.name}"


mock_collectors: Sequence[LogCollector] = (
    LogCollector(
        "sos-report",
        lambda p, b: sp.check_output(["sleep", "4"], text=True),
        "SOS Report",
        manual_collection_command="sudo sos report --batch",
    ),
    LogCollector(
        "oem-get-logs",
        lambda p, b: sp.check_output(["sleep", "2"], text=True),
        "OEM Get Logs",
        manual_collection_command="sudo oem-getlogs",
    ),
    LogCollector(
        "immediate",
        lambda p, b: sp.check_output(["echo"], text=True),
        "Immediate return",
    ),
    LogCollector(
        "fast1",
        lambda p, b: sp.check_output(["sleep", "3"], text=True),
        "Fast collect 1",
    ),
    LogCollector(
        "fast2",
        lambda p, b: sp.check_output(["sleep", "5"], text=True),
        "Fast collect 2",
    ),
    LogCollector(
        "slow1",
        lambda p, b: sp.check_output(["sleep", "6"], text=True),
        "Slow collect 1",
    ),
    LogCollector(
        "slow2",
        lambda p, b: sp.check_output(["sleep", "7"], text=True),
        "Slow collect 2",
    ),
    LogCollector(
        "always-fail",
        lambda p, b: sp.check_output(["false"], text=True),
        "Always fail",
    ),
    LogCollector(
        "checkbox-session",
        pack_checkbox_session,
        "Checkbox Session",
    ),
    LogCollector(
        "nvidia-bug-report",
        nvidia_bug_report,
        "NVIDIA Bug Report",
    ),
)


real_collectors: Sequence[LogCollector] = (
    LogCollector(
        "sos-report",
        sos_report,
        "SOS Report",
        False,
        manual_collection_command="sudo sos report --batch",
    ),
    LogCollector(
        "oem-get-logs",
        oem_getlogs,
        "OEM Get Logs",
        manual_collection_command="sudo oem-getlogs",
    ),
    LogCollector(
        "acpidump",
        acpidump,
        "ACPI Dump",
        manual_collection_command="sudo acpidump -o acpidump.log",
    ),
    LogCollector(
        "dmesg",
        dmesg_of_current_boot,
        "dmesg Logs of This Boot",
        False,
        manual_collection_command="sudo dmesg",
    ),
    LogCollector(
        "checkbox-session",
        pack_checkbox_session,
        "Checkbox Session",
    ),
    LogCollector(
        "nvidia-bug-report",
        nvidia_bug_report,
        "NVIDIA Bug Report",
        False,
        manual_collection_command="nvidia-bug-report.sh --extra-system-data",
    ),
    LogCollector(
        "inxi",
        inxi,
        "inxi Report (verbose)",
        False,
        manual_collection_command="sudo inxi -ACDEGJLMNSxm",
    ),
)

LOG_NAME_TO_COLLECTOR: Mapping[LogName, LogCollector] = {
    collector.name: collector
    for collector in real_collectors  # replace mock_collectors with others
}
