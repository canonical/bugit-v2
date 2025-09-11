"""
Implement concrete log collectors here
--
Each log collector should be an instance of LogCollector. The big assumption
here is that each log collectors is a (slow-running) function and is *independent*
from all other collectors.
"""

import os
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


def pack_checkbox_session(target_dir: Path, bug_report: BugReport) -> str:
    assert (
        bug_report.checkbox_session is not None
    ), "Can't use this collector if there's no checkbox session"

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


def journal_of_past_week(target_dir: Path, _: BugReport) -> None:
    with open(target_dir / "journalctl_1_week.log", "w") as f:
        sp.check_call(
            ["journalctl", "--since", "1 week ago"],
            stdout=f,
            text=True,
        )

    bad = False
    with open(target_dir / "journalctl_1_week.log") as f:
        first_line = f.readline()
        if "No entries" in first_line:
            # this happens when the the journalctl binary can't read the
            # journal file on the host
            # in `snap run --shell bugit.bugit-v2``, calling journalctl will
            # show more errors
            bad = True

    if bad:
        os.remove(target_dir / "journalctl_1_week.log")
        raise ValueError(
            "Not going to attach an empty journalctl file. "
            + "Is the DUT using a much newer version of journalctl?"
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


mock_collectors: Sequence[LogCollector] = (
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
    LogCollector(
        "journalctl",
        journal_of_past_week,
        "Journalctl Logs of This Week",
        True,
        'journalctl --since="1 week ago"',
    ),
)


real_collectors: Sequence[LogCollector] = (
    LogCollector(
        "acpidump",
        acpidump,
        "ACPI Dump",
        True,
        "sudo acpidump -o acpidump.log",
    ),
    LogCollector(
        "dmesg",
        dmesg_of_current_boot,
        "dmesg Logs of This Boot",
        False,
        "sudo dmesg",
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
        "nvidia-bug-report.sh --extra-system-data",
    ),
    LogCollector(
        "journalctl",
        journal_of_past_week,
        "Journalctl Logs of This Week",
        True,
        'journalctl --since="1 week ago"',
    ),
)

LOG_NAME_TO_COLLECTOR: Mapping[LogName, LogCollector] = {
    collector.name: collector
    for collector in real_collectors  # replace mock_collectors with others
}
