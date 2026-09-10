"""
AI Log Collector — an LLM-driven log collector adapted from the
hackathon-oemqa-log-collector-05-13 ("Sherlog") agent.

Unlike Sherlog, which analyses a Jira bug description with an LLM and then
SSHes into a *remote* device to run the commands it decides on, this
collector runs entirely *locally* on the DUT bugit itself is running on,
using bugit's existing async subprocess helpers instead of SSH.

The collector is only shown/selectable when an OpenAI-compatible API is
configured (see utils/ai_config.py). It is intentionally stateless: unlike
Sherlog it does not persist "learnings" across runs.

Safety guardrails (since this now runs directly on real hardware, not a
disposable remote test box):
  - a hard cap on the number of agentic tool-call iterations
  - a per-command timeout
  - commands run as the current user; the LLM is never allowed to invoke
    `sudo` itself
  - a blocklist of obviously destructive command patterns, checked before
    any command is executed
  - a collection-manifest.txt is always written, mapping output files to the
    commands that produced them (same format as Sherlog's manifest)
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypedDict, cast

from bugit_v2.models.bug_report import BugReport
from bugit_v2.utils.ai_config import get_ai_config
from bugit_v2.utils.constants import AI_SYSTEM_PROMPT_FILE

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 60  # safety cap on agentic tool-call turns
COMMAND_TIMEOUT = 60  # seconds, matches Sherlog's SSH command timeout

# Default system prompt template, written out to AI_SYSTEM_PROMPT_FILE the
# first time it's needed so users can subsequently edit it without touching
# source code. `{target_dir}` and `{max_iterations}` are substituted at
# prompt-build time via str.format(); any other literal `{`/`}` a user adds
# should be avoided or doubled (`{{`/`}}`) to not confuse str.format().
DEFAULT_SYSTEM_PROMPT_TEMPLATE = (
    'You are a Linux log collection agent running non-interactively on a device to diagnose a described bug.\n'
    '## STRICT EXECUTION RULES\n'
    '1. **Tool Usage:** You MUST use the `run_command` tool to execute commands. Do not output raw commands in text.\n'
    '2. **Output Location:** All command output MUST be saved directly to `{target_dir}` using file redirects (e.g., `journalctl -k --no-pager > {target_dir}/journal-kernel.txt`).\n'
    '3. **No Sudo / No Destruction:** Do NOT use `sudo`, do NOT install packages, and do NOT run reboot, shutdown, or destructive commands.\n'
    '4. **Sysfs Paths:** When checking hardware states via `/sys/`, suppress errors by appending `2>/dev/null` (e.g., `cat /sys/class/net/*/operstate > {target_dir}/sysfs-net.txt 2>/dev/null`).\n'
    '5. **Log Findings:** Before finishing, write your key findings to `{target_dir}/finding.log`.\n'
    '6. **Finish:** When all logs are collected, call the `finish` tool with a summary of the collected files and what the user should inspect first.\n'
    '## WHAT TO COLLECT\n'
    'Always collect these baseline logs:\n'
    '- `journalctl -k --no-pager > {target_dir}/journal-kernel.txt`\n'
    '- `journalctl --no-pager -n 200 > {target_dir}/journal-tail.txt`\n'
    'Then, collect specific logs based on the bug keywords:\n'
    '- **Network / Wi-Fi:** `journalctl -u NetworkManager --no-pager -n 300` and `ip link show`'
    '- **Suspend / Wake:** `journalctl --no-pager | grep -i -A5 -B5 "suspend entry|suspend exit|PM: suspend|PM: resume"`\n'
    '- **Display / GPU / Wayland:** `journalctl -k --no-pager | grep -i "drm|gpu|i915|amdgpu"` and compositor logs like `journalctl _COMM=gnome-shell --no-pager -n 300` or `sway` or `kwin_wayland`.\n'
    '- **Audio:** `journalctl -u pulseaudio --no-pager -n 100` or `journalctl -u pipewire --no-pager -n 100`\n'
    '- **Bluetooth:** `journalctl -u bluetooth --no-pager -n 200`\n'
    '- **Disk / Storage:** `journalctl -k --no-pager | grep -i "ata|nvme|scsi|disk|error"`\n'
    '- **Crash / Panic:** `journalctl -k --no-pager | grep -i "BUG|panic|oops|warning|error" -A 10 -B 2`\n'
    '## YOUR WORKFLOW\n'
    '1. Identify the bug type from the users description.\n'
    '2. Use the `run_command` tool to collect baselines and bug-specific logs, redirecting all output to `{target_dir}`.\n'
    '3. Use the `run_command` tool to echo a brief summary of clues to `{target_dir}/finding.log`.\n'
    '4. Call the `finish` tool with a summary of the files you collected. You have a maximum of {max_iterations} turns.\n'
)


def _load_system_prompt_template(
    prompt_file: Path = AI_SYSTEM_PROMPT_FILE,
) -> str:
    """Return the (possibly user-edited) system prompt template.

    If *prompt_file* doesn't exist yet, it is created with
    `DEFAULT_SYSTEM_PROMPT_TEMPLATE` so users have a starting point they can
    edit at runtime — no rebuild/reinstall required. The file is re-read on
    every call (i.e. once per `ai_collect()` run), so edits take effect the
    next time a collection is started.
    """
    if not prompt_file.exists():
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(DEFAULT_SYSTEM_PROMPT_TEMPLATE)
    return prompt_file.read_text()


# Patterns that must never be executed, regardless of what the LLM asks for.
# Matched case-insensitively against the full command string.
_DESTRUCTIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\b(?=[^|;&\n]*(?:\s-(?:[^\s]*r[^\s]*)|\s--recursive\b))(?=[^|;&\n]*(?:\s-(?:[^\s]*f[^\s]*)|\s--force\b)).*", re.IGNORECASE),  # rm -rf / -r -f / --recursive --force
    re.compile(r"\bmkfs(\.\w+)?\b", re.IGNORECASE),
    re.compile(r"\bdd\b.*\bof=\s*/dev/", re.IGNORECASE),
    re.compile(r"\bwipefs\b", re.IGNORECASE),
    re.compile(r"\b(fdisk|parted|sgdisk|gdisk)\b", re.IGNORECASE),
    re.compile(r"\b(shutdown|reboot|poweroff|halt)\b", re.IGNORECASE),
    re.compile(r"\binit\s+[06]\b", re.IGNORECASE),
    re.compile(r"\bsystemctl\s+(poweroff|reboot|halt)\b", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),  # no LLM-initiated privilege escalation
    re.compile(r":\(\)\s*{\s*:\s*\|\s*:\s*&\s*}\s*;\s*:"),  # fork bomb
    re.compile(r">\s*/dev/sd[a-z]"),
)


class CommandLogEntry(TypedDict):
    command: str
    file: str | None


def _is_destructive(command: str) -> str | None:
    """Return a rejection reason if *command* matches a blocked pattern, else None."""
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return f"Command blocked by safety guardrails (matched pattern: {pattern.pattern})"
    return None


# Matches "> $TARGET_DIR/foo.txt" / "> "$TARGET_DIR/foo.txt"" style redirections
# used to figure out which file a command produced, for the manifest.
def _detect_output_file(command: str, target_dir: Path) -> str | None:
    pattern = re.compile(
        r">\s*[\"']?"
        + re.escape(str(target_dir))
        + r"/([^\s\"';&|>]+)"
    )
    m = pattern.search(command)
    return m.group(1).strip() if m else None


async def _run_local_command(
    command: str,
    on_output: Callable[[str], None] | None,
    timeout: int = COMMAND_TIMEOUT,
) -> str:
    """Run *command* through the shell locally and return its combined output.

    Mirrors Sherlog's `run_ssh_command`, but executes on the local machine
    instead of over SSH. Never raises on a non-zero exit code — the output
    (including a captured stderr tail) is just returned so the LLM can see
    what happened and adjust its next step.
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return f"[ERROR] Failed to start command: {exc}"

    if proc.stdout is None or proc.stderr is None:
        return "[ERROR] Subprocess stdout/stderr pipe was not created"
    stdout_stream = proc.stdout
    stderr_stream = proc.stderr

    async def _drain(stream: asyncio.StreamReader, is_stderr: bool) -> bytes:
        chunks: list[bytes] = []
        while True:
            line = await stream.readline()
            if not line:
                break
            chunks.append(line)
            if on_output is not None and not is_stderr:
                on_output(line.decode(errors="replace").rstrip("\n"))
        return b"".join(chunks)

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            asyncio.gather(_drain(stdout_stream, False), _drain(stderr_stream, True)),
            timeout=timeout,
        )
        await proc.wait()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"[ERROR] Command timed out after {timeout} seconds"

    output = stdout_bytes.decode(errors="replace")
    if proc.returncode != 0 and stderr_bytes:
        output += f"\n[stderr]: {stderr_bytes.decode(errors='replace').strip()}"
    return output or "(no output)"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command locally on this device to collect logs. "
                "Redirect any file output into the provided target directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute locally",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Call this when all log collection is complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["success", "failure"],
                    },
                    "summary": {
                        "type": "string",
                        "description": "Summary of files collected and what to look at first",
                    },
                },
                "required": ["status", "summary"],
            },
        },
    },
]


def _build_manifest_content(
    target_dir: Path, bug_description: str, command_log: list[CommandLogEntry]
) -> str:
    lines = [
        "# Log Collection Manifest",
        f"# Log dir   : {target_dir}",
        f"# Bug       : {bug_description[:200].strip()}{'…' if len(bug_description) > 200 else ''}",
        "",
        "# Files and the commands that produced them",
        "# ------------------------------------------",
    ]
    mapped = [e for e in command_log if e["file"]]
    unmapped = [e for e in command_log if not e["file"]]
    if mapped:
        max_file = max(len(e["file"] or "") for e in mapped)
        for entry in mapped:
            lines.append(f"{entry['file']:<{max_file}}  |  {entry['command']}")
    if unmapped:
        lines += ["", "# Commands with no file output", "# ----------------------------"]
        for entry in unmapped:
            lines.append(f"  {entry['command']}")
    return "\n".join(lines) + "\n"


def _bug_description_text(bug_report: BugReport) -> str:
    parts = [f"Title: {bug_report.title}", "", bug_report.description]
    if bug_report.platform_tags:
        parts.append(f"\nPlatform tags: {', '.join(bug_report.platform_tags)}")
    if bug_report.impacted_features:
        parts.append(f"Impacted features: {', '.join(bug_report.impacted_features)}")
    return "\n".join(parts)


async def ai_collect(
    target_dir: Path,
    bug_report: BugReport,
    on_output: Callable[[str], None] | None,
) -> str:
    ai_config = get_ai_config()
    if ai_config is None:
        raise RuntimeError(
            "AI Log Collector is not configured. Set it up with "
            "'sudo snap set bugit ai-api-key=<key> ai-base-url=<url> ai-model=<model>'"
        )

    # imported lazily so bugit doesn't need `openai` installed unless this
    # collector is actually configured and used
    from openai import OpenAI

    client = OpenAI(api_key=ai_config.api_key, base_url=ai_config.base_url)
    bug_description = _bug_description_text(bug_report)

    system_prompt_template = _load_system_prompt_template()
    try:
        system_prompt = system_prompt_template.format(
            target_dir=target_dir, max_iterations=MAX_ITERATIONS
        )
    except (KeyError, IndexError) as exc:
        raise RuntimeError(
            f"Malformed system prompt template at {AI_SYSTEM_PROMPT_FILE}: {exc}. "
            "Literal '{' or '}' characters must be doubled ('{{' / '}}'), and only "
            "'{target_dir}' / '{max_iterations}' are valid placeholders."
        ) from exc

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": bug_description},
    ]

    command_log: list[CommandLogEntry] = []
    summary = ""

    for iteration in range(MAX_ITERATIONS):
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model=ai_config.model,
            messages=cast(Any, messages),
            tools=cast(Any, TOOLS),
            tool_choice="required",
            max_tokens=4096,
        )
        msg = response.choices[0].message

        assistant_msg: dict[str, Any] = {"role": "assistant"}
        if msg.content:
            assistant_msg["content"] = msg.content
        if msg.tool_calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        # only "function"-type tool calls are ever produced,
                        # since TOOLS only declares function tools
                        "name": cast(Any, tc).function.name,
                        "arguments": cast(Any, tc).function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_msg)

        if not msg.tool_calls:
            break

        finished = False
        for tool_call in msg.tool_calls:
            tool_call_fn = cast(Any, tool_call).function
            fn_name = tool_call_fn.name
            try:
                fn_args = json.loads(tool_call_fn.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            if fn_name == "finish":
                summary = fn_args.get("summary", "")
                messages.append(
                    {"role": "tool", "tool_call_id": tool_call.id, "content": "done"}
                )
                finished = True
                continue

            if fn_name != "run_command":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": f"[REJECTED] Unknown tool '{fn_name}'",
                    }
                )
                continue

            command = fn_args.get("command", "")
            if on_output is not None:
                on_output(f"$ {command}")

            rejection = _is_destructive(command) if command else "No command provided"
            if rejection:
                if on_output is not None:
                    on_output(f"[BLOCKED] {rejection}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": f"[REJECTED] {rejection}",
                    }
                )
                continue

            output = await _run_local_command(command, on_output, COMMAND_TIMEOUT)
            command_log.append(
                {"command": command, "file": _detect_output_file(command, target_dir)}
            )
            messages.append(
                {"role": "tool", "tool_call_id": tool_call.id, "content": output[:8000]}
            )

        if finished:
            break
    else:
        logger.warning("AI Log Collector hit the %d-iteration cap", MAX_ITERATIONS)
        summary = summary or (
            f"Stopped after reaching the {MAX_ITERATIONS}-iteration safety cap"
        )

    manifest_content = _build_manifest_content(target_dir, bug_description, command_log)
    (target_dir / "collection-manifest.txt").write_text(manifest_content)

    return summary or f"AI Log Collector finished; see {target_dir}/collection-manifest.txt"
