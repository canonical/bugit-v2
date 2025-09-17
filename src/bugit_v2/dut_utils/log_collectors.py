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

COMMAND_TIMEOUT = 10 * 60  # 10 minutes


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
    # how long until this collector is expected to timeout
    # this is purely visual and doesn't change any behaviors
    # each collector, if specifies this, should actually implement a timeout
    advertised_timeout: int | None = None


def pack_checkbox_session(target_dir: Path, bug_report: BugReport) -> str:
    assert (
        bug_report.checkbox_session is not None
    ), "Can't use this collector if there's no checkbox session"

    with tarfile.open(target_dir / "checkbox_session.tar.gz", "w:gz") as f:
        f.add(bug_report.checkbox_session.session_path)

    return f"Added checkbox session to {target_dir}"


def nvidia_bug_report(target_dir: Path, _: BugReport) -> str:
    if "SNAP" in os.environ:
        executable = "/var/lib/snapd/hostfs/usr/bin/nvidia-bug-report.sh"
    else:
        executable = "nvidia-bug-report.sh"
    return sp.check_output(
        [
            executable,
            "--extra-system-data",
            "--output-file",
            str(target_dir / "nvidia-bug-report.log.gz"),
        ],
        text=True,
        timeout=COMMAND_TIMEOUT,
    )


def journal_logs(target_dir: Path, _: BugReport, num_days: int = 7) -> None:
    filepath = target_dir / f"journalctl_{num_days}_days.log"
    with open(filepath, "w") as f:
        try:
            sp.check_call(
                ["journalctl", "--since", f"{num_days} days ago"],
                stdout=f,
                text=True,
                timeout=COMMAND_TIMEOUT,
            )
        except sp.TimeoutExpired:
            raise RuntimeError(
                "Journalctl didn't finish dumping the logs in 600 seconds"
            )

    bad = False
    with open(filepath) as f:
        first_line = f.readline()
        if "No entries" in first_line:
            # this happens when the the journalctl binary can't read the
            # journal file on the host
            # in `snap run --shell bugit.bugit-v2``, calling journalctl will
            # show more errors
            bad = True

    if bad:
        os.remove(filepath)
        raise ValueError(
            "Not going to attach an empty journalctl file. "
            + "Is the DUT using a much newer version of journalctl?"
        )


def acpidump(target_dir: Path, _: BugReport) -> str:
    return sp.check_output(
        ["acpidump", "-o", str(target_dir.absolute() / "acpidump.log")],
        text=True,
        timeout=COMMAND_TIMEOUT,
    )


def dmesg_of_current_boot(target_dir: Path, _: BugReport) -> str:
    with open("/proc/sys/kernel/random/boot_id") as boot_id_file:
        boot_id = boot_id_file.read().strip().replace("-", "")
        with open(target_dir / f"dmesg-of-boot-{boot_id}.log", "w") as f:
            sp.check_call(
                ["dmesg"],
                stdout=f,
                stderr=sp.DEVNULL,
                text=True,
                timeout=COMMAND_TIMEOUT,
            )
            return f"Saved dmesg logs of boot {boot_id} to {f.name}"


def snap_list(target_dir: Path, _: BugReport):
    with open(target_dir / "snap_list.log", "w") as f:
        sp.check_call(
            ["snap", "list", "--all"],
            stdout=f,
            text=True,
            timeout=COMMAND_TIMEOUT,
        )


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
        lambda p, b: sp.check_output(
            ["sleep", "60"], text=True, timeout=COMMAND_TIMEOUT
        ),
        "Slow collect 1",
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "slow2",
        lambda p, b: sp.check_output(
            ["sleep", "700"], text=True, timeout=COMMAND_TIMEOUT
        ),
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
        "journalctl-7-days",
        lambda target_dir, bug_report: journal_logs(target_dir, bug_report, 7),
        "Journalctl Logs of This Week (Slow)",
        False,
        'journalctl --since="1 week ago"',
    ),
    LogCollector(
        "journalctl-3-days",
        lambda target_dir, bug_report: journal_logs(target_dir, bug_report, 3),
        "Journalctl Logs of the Last 3 Days",
        True,
        'journalctl --since="3 days ago"',
    ),
)


real_collectors: Sequence[LogCollector] = (
    LogCollector(
        "acpidump",
        acpidump,
        "ACPI Dump".title(),
        True,
        "sudo acpidump -o acpidump.log",
    ),
    LogCollector(
        "dmesg",
        dmesg_of_current_boot,
        "dmesg Logs of This Boot".title(),
        True,
        "sudo dmesg",
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "checkbox-session",
        pack_checkbox_session,
        "Checkbox Session".title(),
    ),
    LogCollector(
        "nvidia-bug-report",
        nvidia_bug_report,
        "NVIDIA Bug Report".title(),
        False,
        "nvidia-bug-report.sh --extra-system-data",
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "journalctl-7-days",
        lambda target_dir, bug_report: journal_logs(target_dir, bug_report, 7),
        "Journal Logs of This Week (Slow)".title(),
        False,
        'journalctl --since="1 week ago"',
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "journalctl-3-days",
        lambda target_dir, bug_report: journal_logs(target_dir, bug_report, 3),
        "Journal Logs of the Last 3 Days".title(),
        True,
        'journalctl --since="3 days ago"',
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "snap-list",
        snap_list,
        "List of Snaps in This System".title(),
        True,
        "snap list --all",
        advertised_timeout=COMMAND_TIMEOUT,
    ),
)

LOG_NAME_TO_COLLECTOR: Mapping[LogName, LogCollector] = {
    collector.name: collector
    for collector in real_collectors  # replace mock_collectors with others
}
