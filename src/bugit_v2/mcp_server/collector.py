"""Glue between the async job manager and bugit's existing LogCollectors"""

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from bugit_v2.mcp_server.jobs import CollectContext, Collector, Progress

logger = logging.getLogger(__name__)

DEFAULT_LOGS: tuple[str, ...] = (
    "dmesg",
    "journalctl-3-days",
    "snap-list",
    "checkbox-session",
    "checkbox-submission",
)
ALL = "all"
# not meaningful in the MCP flow (need a new bug's text or an attach editor)
_NOT_FOR_MCP = frozenset(("ai-log-collector", "job-outputs", "long-job-outputs"))
_SESSION_ONLY = "checkbox-session"
_SUBMISSION_ONLY = "checkbox-submission"


class CheckboxLike(Protocol):
    def get_job_output(
        self, submission_id: str, test_id: str
    ) -> dict[str, str] | None: ...
    def describe(self, submission_id: str) -> dict[str, Any]: ...
    def collector_input(self, submission_id: str) -> Any: ...


def describe_collectors(collectors: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The collectors an agent may request through `log_names`"""
    return [
        {
            "name": name,
            "description": getattr(c, "display_name", name),
            "default": name in DEFAULT_LOGS,
        }
        for name, c in collectors.items()
        if name not in _NOT_FOR_MCP and not getattr(c, "hidden", False)
    ]


_CONTEXT_FILES = ("context.txt", "collector-errors.txt")


def collector_for_file(name: str) -> str:
    """Which collector produced a file of a log job ('context' for the
    bookkeeping files, 'other' when unknown)"""
    top = name.split("/", 1)[0]
    if top == "checkbox_session" or top.startswith("checkbox_session."):
        return "checkbox-session"
    if top.startswith("dmesg-of-boot-"):
        return "dmesg"
    if top.startswith("journalctl_") and top.endswith("_days.log"):
        return "journalctl-" + top.split("_")[1] + "-days"
    if top.startswith("nvidia-bug-report"):
        return "nvidia-bug-report"
    if top.startswith("sosreport"):
        return "sosreport"
    if top.endswith(".tar.xz"):
        return "checkbox-submission"
    simple = {
        "acpidump.log": "acpidump",
        "snap_list.log": "snap-list",
        "snap_debug.log": "snap-debug",
    }
    if top in simple:
        return simple[top]
    if top in _CONTEXT_FILES or (top.startswith("job_") and top.endswith(".txt")):
        return "context"
    return "other"


def resolve_names(
    requested: Sequence[str], collectors: Mapping[str, Any], base: Sequence[str]
) -> list[str]:
    if ALL in requested:
        extra = [d["name"] for d in describe_collectors(collectors)]
    else:
        extra = list(requested)
    return list(dict.fromkeys([*base, *extra]))


def make_collector(
    checkbox: CheckboxLike,
    collectors: Mapping[str, Any],
    names: Sequence[str] = DEFAULT_LOGS,
) -> Collector:
    async def run(target: Path, ctx: CollectContext, progress: Progress) -> None:
        info: dict[str, Any] = {}
        collector_input: Any = None
        selected = resolve_names(ctx.log_names, collectors, names)
        if ctx.submission_id:
            info = checkbox.describe(ctx.submission_id)
            collector_input = checkbox.collector_input(ctx.submission_id)
        kind = info.get("kind")
        if kind != "session":
            selected = [n for n in selected if n != _SESSION_ONLY]
        if kind != "submission":
            selected = [n for n in selected if n != _SUBMISSION_ONLY]

        (target / "context.txt").write_text(
            "\n".join(
                [
                    f"test_case: {ctx.context_test_case}",
                    f"submission_id: {ctx.submission_id}",
                    f"failed_at: {info.get('failed_at')}",
                ]
            )
            + "\n"
        )
        if ctx.submission_id and ctx.context_test_case:
            out = checkbox.get_job_output(ctx.submission_id, ctx.context_test_case)
            for key, value in (out or {}).items():
                if value and value.strip():
                    (target / f"job_{key}.txt").write_text(value)

        errors: list[str] = []
        succeeded = 0
        for name in selected:
            collector = collectors.get(name)
            if collector is None:
                continue
            progress(f"starting {name}")
            try:
                await collector.collect(
                    target,
                    collector_input,
                    lambda line, n=name: progress(f"{n}: {line.strip()}"),
                )
                succeeded += 1
                progress(f"finished {name}")
            except Exception as e:
                logger.exception("collector %s failed", name)
                errors.append(f"{name}: {type(e).__name__}: {e}")
                progress(f"{name} failed: {e}")

        if errors:
            (target / "collector-errors.txt").write_text("\n".join(errors) + "\n")
        if succeeded == 0:
            raise RuntimeError("No logs could be collected: " + "; ".join(errors))

    return run
