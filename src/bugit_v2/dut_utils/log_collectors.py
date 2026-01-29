"""
Implement concrete log collectors here
--
Each log collector should be an instance of LogCollector. The big assumption
here is that each log collectors is a (slow-running) function and is *independent*
from all other collectors.
"""

import asyncio
import re
import importlib.resources
import os
import shutil
import tarfile
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from bugit_v2.models.bug_report import BugReport, LogName, PartialBugReport
from bugit_v2.utils import host_is_ubuntu_core, is_snap
from bugit_v2.utils.async_subprocess import asp_check_call, asp_check_output
from bugit_v2.utils.constants import MAX_JOB_OUTPUT_LEN

COMMAND_TIMEOUT = 10 * 60  # 10 minutes
NVIDIA_BUG_REPORT_PATH = Path(
    "/var/lib/snapd/hostfs/usr/bin/nvidia-bug-report.sh"
    if is_snap()
    else "nvidia-bug-report.sh"
)


@dataclass(slots=True, frozen=True)
class LogCollector:
    # internal name, alphanumeric or dashes only
    name: LogName
    # the function that actually collects the logs
    collect: Callable[
        [Path, BugReport],
        Awaitable[str | None],  # (target_dir: Path) -> optional result string
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
    # whether to make this collector visible on screen
    # if true, then collect_by_default will dictate if this log collector is run
    hidden: bool = False


async def pack_checkbox_session(
    target_dir: Path, bug_report: BugReport | PartialBugReport
) -> str:
    assert (
        bug_report.checkbox_session is not None
    ), "Can't use this collector if there's no checkbox session"

    with tarfile.open(target_dir / "checkbox_session.tar.gz", "w:gz") as f:
        f.add(bug_report.checkbox_session.session_path)

    return f"Added checkbox session to {target_dir}"


async def nvidia_bug_report(target_dir: Path, _: BugReport | PartialBugReport) -> str:
    if is_snap():
        env = os.environ | {
            "PATH": ":".join(
                [
                    "/var/lib/snapd/hostfs/usr/local/sbin",
                    "/var/lib/snapd/hostfs/usr/local/bin",
                    "/var/lib/snapd/hostfs/usr/sbin",
                    "/var/lib/snapd/hostfs/usr/bin",
                    "/var/lib/snapd/hostfs/sbin",
                    "/var/lib/snapd/hostfs/bin",
                ]
            ),
            "LD_LIBRARY_PATH": ":".join(
                [
                    # $ARCH is exported from bugit-v2/snap/local/scripts/env_wrapper.sh
                    # the shell script is always called before bugit starts
                    f"/var/lib/snapd/hostfs/lib/{os.environ['ARCH']}",
                    f"/var/lib/snapd/hostfs/usr/lib/{os.environ['ARCH']}",
                ]
            ),
        }
    else:
        env = os.environ

    return await asp_check_output(
        [
            str(NVIDIA_BUG_REPORT_PATH),
            "--extra-system-data",
            "--output-file",
            str(target_dir / "nvidia-bug-report.log.gz"),
        ],
        env=env,
    )


async def journal_logs(
    target_dir: Path, _: BugReport | PartialBugReport, num_days: int = 7
) -> None:
    filepath = target_dir / f"journalctl_{num_days}_days.log"
    with open(filepath, "w") as f:
        try:
            await asp_check_call(
                ["journalctl", "--since", f"{num_days} days ago"],
                stdout=f,
                timeout=COMMAND_TIMEOUT,
            )
        except asyncio.TimeoutError:
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
        filepath.unlink(True)
        raise ValueError(
            "Not going to attach an empty journalctl file. "
            + "Is the DUT using a much newer version of journalctl?"
        )


async def acpidump(target_dir: Path, _: BugReport | PartialBugReport) -> None:
    await asp_check_call(
        [
            "acpidump",
            "-o",
            str(target_dir.absolute() / "acpidump.log"),
        ],
    )


async def dmesg_of_current_boot(
    target_dir: Path, _: BugReport | PartialBugReport
) -> str:
    with open("/proc/sys/kernel/random/boot_id") as boot_id_file:
        boot_id = boot_id_file.read().strip().replace("-", "")
        with open(target_dir / f"dmesg-of-boot-{boot_id}.log", "w") as f:
            await asp_check_call(
                ["journalctl", "--dmesg"], timeout=COMMAND_TIMEOUT, stdout=f
            )
            return f"Saved dmesg logs of boot {boot_id} to {f.name}"


async def snap_list(target_dir: Path, _: BugReport | PartialBugReport):
    with open(target_dir / "snap_list.log", "w") as f:
        await asp_check_call(
            ["snap", "list", "--all"],
            stdout=f,
            timeout=COMMAND_TIMEOUT,
        )


async def snap_debug(target_dir: Path, _: BugReport | PartialBugReport):
    script_path = importlib.resources.files("bugit_v2.dut_utils") / "snap_debug.sh"
    with open(target_dir / "snap_debug.log", "w") as f:
        await asp_check_call(
            [str(script_path)],
            stdout=f,
            timeout=COMMAND_TIMEOUT,
        )


async def pack_checkbox_submission(
    target_dir: Path, bug_report: BugReport | PartialBugReport
):
    assert (
        bug_report.checkbox_submission is not None
    ), "Can't use this collector if there's no checkbox submission"
    submission_path = bug_report.checkbox_submission.submission_path
    assert (
        submission_path.exists()
    ), f"{submission_path} was deleted after the bug report was created!"

    shutil.copyfile(submission_path, target_dir / os.path.basename(submission_path))

    return f"Added checkbox submission to {target_dir}"


async def long_job_outputs(target_dir: Path, bug_report: BugReport):
    """
    Only used when the job's stdout is way too long for the description
    """

    assert (
        bug_report.checkbox_session is not None
    ), "Can't use this collector if there's no checkbox session"
    assert bug_report.job_id is not None, "Can't use this collector if there's no job id"
    assert (
        bug_report.checkbox_session.session_path.exists()
    ), f"{bug_report.checkbox_session.session_path} was deleted after the bug report was created!"

    job_output = bug_report.checkbox_session.get_job_output(bug_report.job_id)

    assert (
        job_output
    ), "This collector should not be called if there's no job output. Please report this bug to bugit's repo."

    added_keys: list[str] = []
    for k, v in job_output.items():
        sv = str(v)
        if len(sv) >= MAX_JOB_OUTPUT_LEN:
            with open(target_dir / f"job_{k}.txt", "w") as f:
                f.write(sv)
                added_keys.append(k)

    if added_keys:
        return f"Added job {','.join(added_keys)} to {target_dir}"


async def oem_getlogs(target_dir: Path, _: BugReport):
    out = await asp_check_output(["oem-getlogs"])
    log_file_match = re.search(r"oemlogs.*\.apport\.gz", out)
    if log_file_match is not None:
        file_path = Path(log_file_match.group(0))
        assert file_path.exists(), f"{file_path} doesn't exist!"
        shutil.move(file_path, target_dir)
    else:
        raise FileNotFoundError(
            "oem-getlogs finished, but didn't find a filename matching"
            + "'oemlogs.*\\.apport\\.gz' in its output"
        )


async def slow(target_dir: Path, bug_report: BugReport, secs: int):
    await asp_check_call(["sleep", "10"])


mock_collectors: Sequence[LogCollector] = (
    LogCollector("slow2", lambda p, b: slow(p, b, 30), "Slow2"),
    LogCollector("slow1", lambda p, b: slow(p, b, 10), "Slow1"),
    LogCollector("fast1", lambda p, b: slow(p, b, 2), "Fast1"),
)


real_collectors: Sequence[LogCollector] = (
    LogCollector(
        "acpidump",
        acpidump,
        "ACPI Dump",
        # acpi dump is not applicable to ubuntu core
        os.uname().machine in ("x86_64", "x86", "amd64"),
        "sudo acpidump -o acpidump.log",
    ),
    LogCollector(
        "dmesg",
        dmesg_of_current_boot,
        "dmesg Logs of This Boot",
        True,
        "sudo dmesg",
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "checkbox-session",
        pack_checkbox_session,
        "Checkbox Session",
    ),
    LogCollector(
        "checkbox-submission",
        pack_checkbox_submission,
        "Checkbox Submission",
        True,
    ),
    LogCollector(
        "nvidia-bug-report",
        nvidia_bug_report,
        "NVIDIA Bug Report",
        False,
        "nvidia-bug-report.sh --extra-system-data",
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "journalctl-7-days",
        lambda target_dir, bug_report: journal_logs(target_dir, bug_report, 7),
        "Journal Logs of This Week",
        False,
        'journalctl --since="1 week ago"',
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "journalctl-3-days",
        lambda target_dir, bug_report: journal_logs(target_dir, bug_report, 3),
        "Journal Logs of the Last 3 Days",
        False,
        'journalctl --since="3 days ago"',
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "snap-list",
        snap_list,
        "List of Snaps in This System",
        True,
        "snap list --all",
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "snap-debug",
        snap_debug,
        "snapd team's snap-debug.sh (has apparmor logs and gadget snap info)",
        # can be very big because it tries to get journal logs
        # only run this by default on ubuntu core
        host_is_ubuntu_core(),
        "curl -fsSL https://raw.githubusercontent.com/canonical/snapd/refs/heads/master/debug-tools/snap-debug-info.sh | bash",
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        "oem-getlogs",
        oem_getlogs,
        "oem-getlogs (experimental)",
        False,
        "sudo -E oem-getlogs",
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
        # only invoked when job outputs are too long
        "long-job-outputs",
        long_job_outputs,
        "Long Job Outputs",
        collect_by_default=True,
        hidden=True,
    ),
)

LOG_NAME_TO_COLLECTOR: Mapping[LogName, LogCollector] = {
    collector.name: collector
    for collector in real_collectors  # replace mock_collectors with others
}
