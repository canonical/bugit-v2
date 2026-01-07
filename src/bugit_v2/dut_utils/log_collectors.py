"""
Implement concrete log collectors here
--
Each log collector should be an instance of LogCollector. The big assumption
here is that each log collectors is a (slow-running) function and is *independent*
from all other collectors.
"""

import asyncio
import asyncio.subprocess as asp
import importlib.resources
import os
import shutil
import tarfile
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError
from typing import IO, Any, Callable, Literal

from bugit_v2.models.bug_report import BugReport, LogName, PartialBugReport
from bugit_v2.utils import host_is_ubuntu_core, is_snap
from bugit_v2.utils.constants import MAX_JOB_OUTPUT_LEN

COMMAND_TIMEOUT = 10 * 60  # 10 minutes
NVIDIA_BUG_REPORT_PATH = Path(
    "/var/lib/snapd/hostfs/usr/bin/nvidia-bug-report.sh"
    if is_snap()
    else "nvidia-bug-report.sh"
)


async def asp_check_output(
    cmd: list[str],
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Async version of subprocess.check_output

    :param cmd: command array like the sync version
    :param timeout: timeout in seconds. Wait forever if None
    :param env: env override
    :raises CalledProcessError: when the process doesn't return 0
    :return: stdout as a string if successful
    """
    if env:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=asp.PIPE, stderr=asp.PIPE, env=env
        )
    else:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=asp.PIPE, stderr=asp.PIPE
        )

    if timeout:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    else:
        stdout, stderr = await proc.communicate()

    assert proc.returncode is not None
    if proc.returncode != 0:
        raise CalledProcessError(proc.returncode, cmd, stdout, stderr)

    return stdout.decode()


async def asp_check_call(
    cmd: list[str],
    timeout: int | None = None,
    env: dict[str, str] | None = None,
    stdout: IO[Any] | int = asp.DEVNULL,
    stderr: IO[Any] | int = asp.DEVNULL,
) -> Literal[0]:
    """Async version of sp.check_call

    :param cmd: command array like the sync version
    :param timeout: timeout in seconds. Wait forever if None
    :param env: env override
    :param stdout: where to put stdout, defaults to asp.DEVNULL
    :param stderr: where to put stderr, defaults to asp.DEVNULL
    :raises CalledProcessError: when return code is not 0
    :return: 0
    """
    if env:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=stdout, stderr=stderr, env=env
        )
    else:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=stdout, stderr=stderr
        )

    if timeout:
        rc = await asyncio.wait_for(proc.wait(), timeout)
    else:
        rc = await proc.wait()

    if rc != 0:
        raise CalledProcessError(rc, cmd)

    return rc


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


async def nvidia_bug_report(
    target_dir: Path, _: BugReport | PartialBugReport
) -> str:
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

    proc = await asp.create_subprocess_exec(
        str(NVIDIA_BUG_REPORT_PATH),
        "--extra-system-data",
        "--output-file",
        str(target_dir / "nvidia-bug-report.log.gz"),
        stdout=asp.PIPE,
        stderr=asp.PIPE,
        env=env,
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(stderr.decode())

    return stdout.decode()


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
    script_path = (
        importlib.resources.files("bugit_v2.dut_utils") / "snap_debug.sh"
    )
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

    shutil.copyfile(
        submission_path, target_dir / os.path.basename(submission_path)
    )

    return f"Added checkbox submission to {target_dir}"


async def long_job_outputs(target_dir: Path, bug_report: BugReport):
    """
    Only used when the job's stdout is way too long for the description
    """

    assert (
        bug_report.checkbox_session is not None
    ), "Can't use this collector if there's no checkbox session"
    assert (
        bug_report.job_id is not None
    ), "Can't use this collector if there's no job id"
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


async def slow(target_dir: Path, bug_report: BugReport, secs: int):
    proc = await asp.create_subprocess_exec("sleep", str(secs))
    await proc.communicate()


mock_collectors: Sequence[LogCollector] = ()


real_collectors: Sequence[LogCollector] = (
    LogCollector(
        "acpidump",
        acpidump,
        "ACPI Dump",
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
        host_is_ubuntu_core(),  # can be very big
        "curl -fsSL https://raw.githubusercontent.com/canonical/snapd/refs/heads/master/debug-tools/snap-debug-info.sh | bash",
        advertised_timeout=COMMAND_TIMEOUT,
    ),
    LogCollector(
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
