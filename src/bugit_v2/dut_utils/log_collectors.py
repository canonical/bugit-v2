"""
Implement concrete log collectors here
--
Each log collector should be an instance of LogCollector. The big assumption
here is that each log collectors is a (slow-running) command and is *independent*
from all other collectors.
"""

import subprocess as sp
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

LogName = Literal[
    "sosreport",
    "oem-getlogs",
    "immediate",
    "fast1",
    "fast2",
    "slow1",
    "slow2",
    "always-fail",
]
LOG_NAMES: tuple[LogName, ...] = LogName.__args__
CollectorMap = Mapping[LogName, "LogCollector"]


@dataclass
class LogCollector:
    name: LogName  # internal name
    collect: Callable[
        [Path],
        sp.CompletedProcess[str],  # (target_dir: Path) -> CompletedProcess
    ]  # actually collect the logs
    # right now the assumption is that all collectors are shell commands
    display_name: str  # the string to show in report collector
    tooltip: str | None = None
    # should this log be collected by default?
    # (set to false for ones that are uncommon or very slow)
    collect_by_default: bool = True


def sos_report(target_dir: Path):
    assert target_dir.is_dir()
    return sp.run(
        ["sudo", "sos", "report", "--batch", f"--tmp-dir={target_dir}"],
        text=True,
        stdout=sp.PIPE,
        stderr=sp.PIPE,
    )


def oem_getlogs(target_dir: Path):
    assert target_dir.is_dir()
    return sp.run(
        ["sudo", "-E", "oem-getlogs"],
        text=True,
        stdout=sp.PIPE,
        stderr=sp.PIPE,
    )


mock_collectors: CollectorMap = {
    "sosreport": LogCollector(
        "sosreport", lambda _: sp.run(["sleep", "4"], text=True), "SOS Report"
    ),
    "oem-getlogs": LogCollector(
        "oem-getlogs",
        lambda _: sp.run(["sleep", "2"], text=True),
        "OEM GetLogs",
    ),
    "immediate": LogCollector(
        "immediate", lambda _: sp.run(["echo"], text=True), "Immediate return"
    ),
    "fast1": LogCollector(
        "fast1", lambda _: sp.run(["sleep", "3"], text=True), "Fast collect 1"
    ),
    "fast2": LogCollector(
        "fast2", lambda _: sp.run(["sleep", "5"], text=True), "Fast collect 2"
    ),
    "slow1": LogCollector(
        "slow1", lambda _: sp.run(["sleep", "6"], text=True), "Slow collect 1"
    ),
    "slow2": LogCollector(
        "slow2", lambda _: sp.run(["sleep", "7"], text=True), "Slow collect 2"
    ),
    "always-fail": LogCollector(
        "always-fail", lambda _: sp.run(["false"], text=True), "Always fail"
    ),
}


real_collectors: CollectorMap = {
    "sosreport": LogCollector(
        "sosreport",
        sos_report,
        "SOS Report",
        "Runs the 'sos report --batch' command",
    ),
    "oem-getlogs": LogCollector(
        "oem-getlogs",
        oem_getlogs,
        "OEM GetLogs",
        "Runs the oem-getlogs command",
    ),
}

LOG_NAME_TO_COLLECTOR = mock_collectors
