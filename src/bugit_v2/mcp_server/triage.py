"""Pure helpers for triage_failures: find each test's error and propose the bug text"""

import re
from collections.abc import Mapping, Sequence

from bugit_v2.mcp_server.bugs import MAX_TITLE_LEN
from bugit_v2.utils import slugify

IO_LOG_DIR = "checkbox_session/io-logs"
MAX_ERROR_LINES = 5
FALLBACK_TAIL_LINES = 30
_GREP_PREFIX = re.compile(r"^\d+: ")


def io_log_name(test_id: str, stream: str) -> str:
    """The collected checkbox io-log file of a job, e.g. ...__graphics_2_vkcube.stderr"""
    return f"{IO_LOG_DIR}/{slugify(test_id)}.{stream}"


def short_id(test_id: str) -> str:
    return test_id.split("::", 1)[-1]


def error_lines(text: str, keyword: str, limit: int = MAX_ERROR_LINES) -> list[str]:
    """The last `limit` lines containing keyword (case-insensitive); tracebacks end with the cause"""
    needle = keyword.lower()
    hits = [
        _GREP_PREFIX.sub("", line).strip()
        for line in text.splitlines()
        if needle in line.lower() and line.strip()
    ]
    return list(dict.fromkeys(hits))[-limit:]


def last_lines(text: str, limit: int = FALLBACK_TAIL_LINES) -> list[str]:
    return [line.rstrip() for line in text.splitlines() if line.strip()][-limit:]


def _category(test_ids: Sequence[str]) -> str:
    groups = {short_id(t).split("/", 1)[0] for t in test_ids}
    return groups.pop() + " " if len(groups) == 1 else ""


def default_fields(
    test_ids: Sequence[str], errors: Mapping[str, Sequence[str]]
) -> dict[str, str]:
    """Bug sections proposed from the errors found; the agent may override any of them"""
    n = len(test_ids)
    causes = [lines[-1] for lines in errors.values() if lines]
    shared = list(dict.fromkeys(causes))
    cause = (
        shared[0]
        if len(shared) == 1
        else (f"{len(shared)} different errors" if shared else "")
    )
    who = short_id(test_ids[0]) if n == 1 else f"{n} {_category(test_ids)}tests"
    title = f"{who} fail{'s' if n == 1 else ''}" + (f": {cause}" if cause else "")
    if len(title) > MAX_TITLE_LEN:
        title = title[: MAX_TITLE_LEN - 3] + "..."
    names = "\n".join(f"- {short_id(t)}" for t in test_ids)
    actual = "\n\n".join(
        f"{short_id(t)}:\n"
        + ("\n".join(errors.get(t) or []) or "(no error output found)")
        for t in test_ids
    )
    return {
        "title": title,
        "summary": f"The following Checkbox test{'s' if n > 1 else ''} failed"
        + (f' with "{cause}"' if len(shared) == 1 else "")
        + f":\n{names}",
        "steps_to_reproduce": "1. Run the Checkbox test plan on the DUT.\n2. Run:\n"
        + names,
        "expected_result": f"The test{'s' if n > 1 else ''} pass{'' if n > 1 else 'es'}.",
        "actual_result": actual,
        "failure_rate": f"{n}/{n} tests in 1/1 runs",
    }
