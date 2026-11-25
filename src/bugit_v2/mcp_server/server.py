"""The MCP server: tool/prompt definitions and the ASGI app"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field, create_model
from starlette.types import ASGIApp

from bugit_v2.mcp_server.auth import BearerAuthMiddleware
from bugit_v2.mcp_server.authstate import AuthService
from bugit_v2.mcp_server.bugs import BugService
from bugit_v2.mcp_server.checkbox import CheckboxService
from bugit_v2.mcp_server.collector import collector_for_file
from bugit_v2.mcp_server.config import McpConfig
from bugit_v2.mcp_server.jobs import JobManager
from bugit_v2.mcp_server.logstore import LogStore
from bugit_v2.mcp_server.playbook import BUG_TRIAGE_PLAYBOOK
from bugit_v2.mcp_server.report import (
    MAX_PREVIEW_OUTPUT,
    SECTION_NAMES,
    format_preview,
    render_description,
)
from bugit_v2.mcp_server.triage import (
    default_fields,
    error_lines,
    io_log_name,
    last_lines,
)
from bugit_v2.models.dut_info import DutInfo

logger = logging.getLogger(__name__)

# the end of a test's output holds the cause; never read more than this per io-log
_MAX_IO_LOG_BYTES = 2 * 1024 * 1024


class _Approval(BaseModel):
    approve: bool = Field(description="Submit this bug report now?")


async def ask_user_approval(ctx: Context, message: str) -> bool:  # type: ignore[type-arg]
    """Ask the human directly (MCP elicitation) when the client supports it.

    Clients without elicitation fall back to the playbook's instruction to get
    approval in the chat.
    """
    from mcp import types

    try:
        session = ctx.request_context.session
        supported = session.check_client_capability(
            types.ClientCapabilities(elicitation=types.ElicitationCapability())
        )
    except Exception:
        return True
    if not supported:
        return True
    result = await ctx.elicit(message=message, schema=_Approval)
    return result.action == "accept" and result.data.approve


async def ask_log_selection(
    ctx: Context, names: Sequence[str]  # type: ignore[type-arg]
) -> list[str] | None:
    """Let the human pick the files to upload (MCP elicitation); None if unsupported or cancelled"""
    from mcp import types

    try:
        supported = ctx.request_context.session.check_client_capability(
            types.ClientCapabilities(elicitation=types.ElicitationCapability())
        )
    except Exception:
        return None
    if not supported:
        return None
    schema = create_model(
        "_LogSelection",
        files=(
            list[Literal[tuple(names)]],  # type: ignore[valid-type]
            Field(
                default=[], description="Logs to upload with the bug (none by default)"
            ),
        ),
    )
    result = await ctx.elicit(message="Which logs should be attached?", schema=schema)
    if result.action != "accept":
        return None
    return list(result.data.files)


@dataclass(slots=True)
class _Session:
    last_submission: str | None = None
    extras: dict[str, Any] = field(default_factory=dict[str, Any])


def build_server(
    cfg: McpConfig,
    *,
    checkbox: CheckboxService,
    jobs: JobManager,
    bugs: BugService,
    auth: AuthService,
    logstore: LogStore,
    machine_info: Callable[[], Awaitable[Mapping[str, str]]] | None = None,
    dut_defaults: Callable[[], DutInfo | None] = lambda: None,
    log_catalog: Sequence[Mapping[str, Any]] = (),
) -> FastMCP:
    mcp = FastMCP(
        "bugit-v2",
        instructions=(
            "Hardware bug triage for Canonical's bugit-v2. "
            "Start by invoking the `bug_triage_playbook` prompt."
        ),
        host=cfg.bind_address,
        port=cfg.port,
        # Every request must carry the bearer key, which browsers never attach
        # on their own, so DNS-rebinding protection adds nothing here and would
        # break access via a LAN address.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    state = _Session()

    def blocking[**P, R](fn: Callable[P, R]) -> Callable[P, Any]:
        async def run(*a: P.args, **kw: P.kwargs) -> R:
            return await asyncio.to_thread(fn, *a, **kw)

        return run

    @mcp.prompt(
        name="bug_triage_playbook",
        description="The required step-by-step procedure for investigating a hardware failure and filing a bug.",
    )
    def bug_triage_playbook() -> str:
        return BUG_TRIAGE_PLAYBOOK

    @mcp.tool()
    async def check_auth_status(service: str) -> dict[str, Any]:
        """Use this to check whether the user is logged in to an issue tracker ('launchpad', 'jira' or 'local'; 'local' files to a directory on the device and needs no login). ALWAYS call this BEFORE search_bugs() or confirm_submission(); if authenticated is false, call get_login_url() and ask the user to complete the login."""
        return await blocking(auth.check_auth_status)(service)

    @mcp.tool()
    async def get_login_url(service: str) -> str:
        """Use this when check_auth_status() reports authenticated=false. Returns a URL (or instructions link) that you MUST show to the user so they can log in interactively in their browser. Afterwards call check_auth_status() again to confirm."""
        return await blocking(auth.get_login_url)(service)

    @mcp.tool()
    async def list_checkbox_submissions() -> list[dict[str, Any]]:
        """Use this FIRST to discover recent local Checkbox hardware certification test runs (newest first). Each item has a submission_id to pass to get_failed_test_cases()."""
        return await blocking(checkbox.list_submissions)()

    @mcp.tool()
    async def list_failed_tests() -> list[dict[str, Any]]:
        """Use this INSTEAD of calling list_checkbox_submissions() and get_failed_test_cases() one by one. Returns, in one call, every failed test of the recent Checkbox runs (newest first), GROUPED BY SUBMISSION: each item has submission_id, testplan_id, modified and a nested tests list of {test_id, outcome, failed_at, cert_status, suggested_severity} (blocker -> highest/Critical, non-blocker -> high, null -> unknown, no suggestion; ask the user for the severity AFTER they pick the tests and BEFORE drafting, preselecting the highest suggested_severity of the chosen tests). ALWAYS present the failures grouped by submission (keep each submission's tests together, showing its testplan_id and modified time once) when asking the user which failure(s) to investigate. If the user picks tests from more than one submission for a single bug, warn them but it is allowed."""
        rows = await blocking(checkbox.list_failed_tests)()
        if rows:
            state.last_submission = rows[0]["submission_id"]
        groups: list[dict[str, Any]] = []
        for row in rows:
            if groups and groups[-1]["submission_id"] == row["submission_id"]:
                group = groups[-1]
            else:
                group = {
                    "submission_id": row["submission_id"],
                    "testplan_id": row["testplan_id"],
                    "modified": row["modified"],
                    "tests": [],
                }
                groups.append(group)
            group["tests"].append(
                {
                    "test_id": row["test_id"],
                    "outcome": row["outcome"],
                    "failed_at": row["failed_at"],
                    "cert_status": row.get("cert_status"),
                    "suggested_severity": row.get("suggested_severity"),
                }
            )
        return groups

    @mcp.tool()
    async def get_failed_test_cases(submission_id: str) -> list[dict[str, Any]]:
        """Use this to list the failed (fail/crash) test cases of one Checkbox submission_id from list_checkbox_submissions(). ALWAYS present the failures to the user and ask which one to investigate; do NOT choose for them."""
        cases = await blocking(checkbox.get_failed_test_cases)(submission_id)
        state.last_submission = submission_id
        return cases

    @mcp.tool()
    async def start_log_collection(
        context_test_case: str | None = None,
        submission_id: str | None = None,
        log_names: list[str] | None = None,
    ) -> str:
        """Use this AFTER the user chose a failed test case: pass its test_id as context_test_case. Starts a log collection in the background and returns a job_id immediately. Then call check_job_status(job_id) repeatedly until the status is 'completed'. submission_id defaults to the one last passed to get_failed_test_cases(). By default only the quick logs are collected (dmesg, journal, snap list, checkbox session/submission). Pass log_names to ADD more collectors, e.g. ['sosreport', 'oem-getlogs'], or ['all'] for every available one (see list_log_collectors()). sosreport is slow (up to ~10 minutes), so ask the user before requesting it or 'all'."""
        names = list(log_names or [])
        known = {c["name"] for c in log_catalog}
        unknown = [n for n in names if n != "all" and known and n not in known]
        if unknown:
            raise ValueError(
                f"Unknown log collector(s): {unknown}. Valid names: {sorted(known)} or 'all'"
            )
        return jobs.start(
            context_test_case, submission_id or state.last_submission, names
        )

    @mcp.tool()
    async def list_log_collectors() -> list[dict[str, Any]]:
        """Use this to see which log collectors start_log_collection() can run (name, description, default). Collectors with default=false (e.g. sosreport, oem-getlogs, nvidia-bug-report) only run when named in log_names, or with log_names=['all']."""
        return [dict(c) for c in log_catalog]

    @mcp.tool()
    async def check_job_status(job_id: str) -> dict[str, Any]:
        """Use this to poll a log collection job started by start_log_collection(). status is 'running', 'completed' or 'failed'. Do NOT call grep_log()/tail_log() until it is 'completed'; wait a few seconds between polls."""
        return jobs.status(job_id)

    @mcp.tool()
    async def list_collected_logs(job_id: str) -> list[dict[str, Any]]:
        """Use this to list ALL the files (name, size, collector, collector_description) collected by a completed job_id. Pass these names as filename to grep_log() and tail_log(). When the user chooses attachments, show the COMPLETE list grouped by collector (never a shortened or suggested subset)."""
        files = await blocking(logstore.list_files)(jobs.job_dir(job_id))
        titles = {c["name"]: c["description"] for c in log_catalog}
        titles["context"] = "Test context, job output and collector errors"
        titles["other"] = "Other files"
        out: list[dict[str, Any]] = []
        for f in files:
            key = collector_for_file(f["name"])
            out.append(
                {**f, "collector": key, "collector_description": titles.get(key, key)}
            )
        return out

    @mcp.tool()
    async def grep_log(job_id: str, filename: str, keyword: str) -> str:
        """Use this to search a collected log for an error string (case-insensitive literal match, not a regex). Output is PII-redacted and limited to the first 200 matches; use the line numbers with tail_log() context. Start with broad keywords such as 'error', 'panic', 'ACPI', 'firmware', 'failed' near the failure time from context.txt."""
        return await blocking(logstore.grep)(jobs.job_dir(job_id), filename, keyword)

    @mcp.tool()
    async def tail_log(job_id: str, filename: str, lines: int = 50) -> str:
        """Use this to read the last N lines (max 1000) of a collected log file. Output is PII-redacted."""
        return await blocking(logstore.tail)(jobs.job_dir(job_id), filename, lines)

    async def _machine_info() -> Mapping[str, str]:
        if machine_info is None:
            return {}
        try:
            return await machine_info()
        except Exception:
            logger.exception("Could not collect basic machine info")
            return {}

    async def _render(parts: Mapping[str, str], test_case: str | None) -> str:
        return render_description(
            {k: v for k, v in parts.items() if k in SECTION_NAMES},
            test_case=test_case,
            cid=parts.get("cid") or None,
            sku=parts.get("sku") or None,
            machine_info=await _machine_info(),
            extra_info=parts.get("extra_info", ""),
        )

    def _preview(draft: Any) -> str:
        output = None
        if draft.submission_id and draft.test_case:
            try:
                raw = checkbox.get_job_output(draft.submission_id, draft.test_case)
            except Exception:
                raw = None
            output = (
                {k: logstore.redact(str(v)) for k, v in raw.items()} if raw else None
            )
        return format_preview(draft, output)

    async def _create_draft(
        *,
        title: str,
        summary: str = "",
        steps_to_reproduce: str = "",
        expected_result: str = "",
        actual_result: str = "",
        failure_rate: str = "",
        additional_information: str = "",
        tags: list[str] | None = None,
        logs_job_id: str | None = None,
        tracker: str | None = None,
        project: str | None = None,
        severity: str = "highest",
        issue_file_time: str = "immediate",
        platform_tags: list[str] | None = None,
        impacted_features: list[str] | None = None,
        impacted_vendors: list[str] | None = None,
        assignee: str | None = None,
        status: str = "New",
        series: str | None = None,
        description: str = "",
        cid: str | None = None,
        sku: str | None = None,
        attach_logs: list[str] | None = None,
        affected_test_cases: Sequence[str] = (),
    ) -> str:
        if not (description.strip() or summary.strip()):
            raise ValueError("Provide a summary (preferred) or a full description")
        saved = dut_defaults()
        if saved is None:
            logger.warning(
                "No saved dut-info found: CID, SKU, project, assignee and tags are not defaulted"
            )
        if saved:
            project = project or saved.project
            platform_tags = platform_tags or list(saved.platform_tags)
            tags = tags or list(saved.tags)
            tracker_name = tracker or bugs.default_tracker
            assignee = assignee or (
                saved.jira_assignee if tracker_name == "jira" else saved.lp_assignee
            )
        submission_id, test_case = state.last_submission, None
        if logs_job_id is not None:
            ctx = jobs.context(logs_job_id)
            submission_id = ctx.submission_id or submission_id
            test_case = ctx.context_test_case
        parts: dict[str, str] | None = None
        if not description.strip():
            parts = {
                "Summary": summary,
                "Steps to Reproduce": steps_to_reproduce,
                "Expected Result": expected_result,
                "Actual Result": actual_result,
                "Failure Rate": failure_rate,
                "cid": cid or (saved.cid if saved else None) or "",
                "sku": sku or (saved.sku if saved else None) or "",
                "extra_info": additional_information,
                "Affected Test Cases": "\n".join(affected_test_cases),
            }
            description = await _render(parts, test_case)
        return bugs.draft_bug(
            title,
            description,
            tags or [],
            logs_job_id,
            tracker,
            project,
            severity=severity,
            issue_file_time=issue_file_time,
            platform_tags=platform_tags or [],
            impacted_features=impacted_features or [],
            impacted_vendors=impacted_vendors or [],
            assignee=assignee,
            status=status,
            series=series,
            submission_id=submission_id,
            test_case=test_case,
            parts=parts,
            attach_logs=attach_logs or [],
        )

    @mcp.tool()
    async def draft_bug(
        title: str,
        summary: str = "",
        steps_to_reproduce: str = "",
        expected_result: str = "",
        actual_result: str = "",
        failure_rate: str = "",
        additional_information: str = "",
        tags: list[str] | None = None,
        logs_job_id: str | None = None,
        tracker: str | None = None,
        project: str | None = None,
        severity: str = "highest",
        issue_file_time: str = "immediate",
        platform_tags: list[str] | None = None,
        impacted_features: list[str] | None = None,
        impacted_vendors: list[str] | None = None,
        assignee: str | None = None,
        status: str = "New",
        series: str | None = None,
        description: str = "",
        cid: str | None = None,
        sku: str | None = None,
        attach_logs: list[str] | None = None,
    ) -> str:
        """Use this to prepare a bug report in the same format as bugit-v2. It does NOT submit anything: it validates the payload and returns a draft_id. Fill summary, steps_to_reproduce, expected_result, actual_result and failure_rate; the server adds Affected Test Cases and Additional Information (CID, SKU, machine info) and, on submit, the Stage and Job Output. cid, sku, project, assignee, platform_tags and tags default to the values saved by `bugit.dut-info` when omitted (explicit arguments win); severity is highest (Critical on Launchpad), high, medium, low or lowest (propose one from the impact; the user can change it at approval); issue_file_time is immediate, after_reboot or later; platform_tags are Jira components; tags are extra tracker labels. Pass logs_job_id to link the collected logs, and attach_logs (file names from list_collected_logs()) for the files to upload with the bug; NOTHING is uploaded unless attach_logs is set, so leave it empty: the user picks the files right AFTER approving the draft. Then call get_draft(draft_id), show the whole preview to the user and ask for explicit approval (approve/reject) before confirm_submission(). Only pass description to bypass the template."""
        return await _create_draft(
            title=title, summary=summary, steps_to_reproduce=steps_to_reproduce,
            expected_result=expected_result, actual_result=actual_result,
            failure_rate=failure_rate, additional_information=additional_information,
            tags=tags, logs_job_id=logs_job_id, tracker=tracker, project=project,
            severity=severity, issue_file_time=issue_file_time, platform_tags=platform_tags,
            impacted_features=impacted_features, impacted_vendors=impacted_vendors,
            assignee=assignee, status=status, series=series, description=description,
            cid=cid, sku=sku, attach_logs=attach_logs,
        )  # fmt: skip

    def _job_texts(
        job_dir: Path | None, submission_id: str | None, test_id: str
    ) -> dict[str, str]:
        """stderr/stdout of one test: collected io-logs, else the session's job output"""
        texts: dict[str, str] = {}
        if job_dir is not None:
            for stream in ("stderr", "stdout"):
                try:
                    path = logstore.resolve(job_dir, io_log_name(test_id, stream))
                except ValueError:
                    continue
                with open(path, "rb") as f:
                    f.seek(max(0, path.stat().st_size - _MAX_IO_LOG_BYTES))
                    texts[stream] = f.read().decode(errors="replace")
        if not any(t.strip() for t in texts.values()) and submission_id:
            try:
                out = checkbox.get_job_output(submission_id, test_id) or {}
            except Exception:
                logger.exception("Could not read the job output of %s", test_id)
                out = {}
            texts = {
                "stderr": str(out.get("stderr") or ""),
                "stdout": str(out.get("stdout") or ""),
            }
        return texts

    def _find_errors(
        job_dir: Path | None, submission_id: str | None, test_id: str, keyword: str
    ) -> list[str]:
        """Error lines of one test: collected stderr, then stdout, then the session itself"""
        texts = list(_job_texts(job_dir, submission_id, test_id).values())
        for text in texts:
            if lines := error_lines(text, keyword):
                return [logstore.redact(line) for line in lines]
        for text in texts:
            if lines := last_lines(text):
                return [logstore.redact(line) for line in lines]
        return []

    def _job_logs(
        job_dir: Path | None, submission_id: str | None, test_id: str
    ) -> dict[str, str]:
        """Redacted, size-capped stdout/stderr of one test, for the agent's root-cause analysis"""
        texts = _job_texts(job_dir, submission_id, test_id)
        out: dict[str, str] = {}
        for stream in ("stdout", "stderr"):
            text = logstore.redact(texts.get(stream, ""))
            if len(text) > MAX_PREVIEW_OUTPUT:
                text = text[:MAX_PREVIEW_OUTPUT] + "\n... (truncated)"
            out[stream] = text
        return out

    @mcp.tool()
    async def triage_failures(
        job_id: str,
        test_ids: list[str],
        tracker: str | None = None,
        keyword: str = "error",
        title: str | None = None,
        summary: str | None = None,
        steps_to_reproduce: str | None = None,
        expected_result: str | None = None,
        actual_result: str | None = None,
        failure_rate: str | None = None,
        severity: str = "highest",
        issue_file_time: str = "immediate",
        submissions: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Use this INSTEAD of grep_log() + draft_bug() once start_log_collection() is 'completed'. In one call it finds the error of every test in test_ids (all the failures the user chose; keyword is a case-insensitive literal, default 'error') and drafts ONE bug listing every test under Affected Test Cases. Title, summary, steps, expected/actual result and failure rate are proposed from the errors; pass any of them (or severity, or issue_file_time: immediate, after_reboot or later) to override. submissions maps a test_id to its submission_id when the tests come from different runs (default: the job's). Returns draft_id, preview (identical to get_draft()), errors (matched lines) and logs (the redacted, truncated stdout/stderr of each test) per test, and warnings. errors/logs are raw material for YOU, the calling agent, to analyze: read them and call update_draft() with a real root-cause title/summary/actual_result that explains WHY the test failed, not a restatement of the exception text, before showing the whole preview to the user and asking for explicit approval (approve/reject) before confirm_submission()."""
        test_ids = list(dict.fromkeys(t.strip() for t in test_ids if t.strip()))
        if not test_ids:
            raise ValueError("test_ids must list at least one failed test")
        if not keyword.strip():
            raise ValueError("keyword must not be empty")
        status = jobs.status(job_id)["status"]
        if status != "completed":
            raise ValueError(
                f"Log collection {job_id} is {status!r}; poll check_job_status() until 'completed'"
            )
        job_dir = jobs.job_dir(job_id)
        submission_id = jobs.context(job_id).submission_id or state.last_submission
        subs = submissions or {}
        errors = {
            # the job only collected its own submission's io-logs
            t: await blocking(_find_errors)(
                job_dir if subs.get(t, submission_id) == submission_id else None,
                subs.get(t) or submission_id,
                t,
                keyword,
            )
            for t in test_ids
        }
        warnings = [
            f"No error output found for {t}" for t, lines in errors.items() if not lines
        ]
        logs = {
            t: await blocking(_job_logs)(
                job_dir if subs.get(t, submission_id) == submission_id else None,
                subs.get(t) or submission_id,
                t,
            )
            for t in test_ids
        }

        fields = default_fields(test_ids, errors)
        overrides = dict(
            title=title, summary=summary, steps_to_reproduce=steps_to_reproduce,
            expected_result=expected_result, actual_result=actual_result,
            failure_rate=failure_rate,
        )  # fmt: skip
        fields.update(
            {k: v for k, v in overrides.items() if v is not None and v.strip()}
        )

        tracker_name = tracker or bugs.default_tracker

        draft_id = await _create_draft(
            title=fields["title"],
            summary=fields["summary"],
            steps_to_reproduce=fields["steps_to_reproduce"],
            expected_result=fields["expected_result"],
            actual_result=fields["actual_result"],
            failure_rate=fields["failure_rate"],
            logs_job_id=job_id,
            tracker=tracker_name,
            severity=severity,
            issue_file_time=issue_file_time,
            affected_test_cases=test_ids,
        )
        preview = await blocking(lambda: _preview(bugs.get_draft(draft_id)))()
        return {
            "draft_id": draft_id,
            "preview": preview,
            "errors": errors,
            "logs": logs,
            "warnings": warnings,
        }

    @mcp.tool()
    async def update_draft(
        draft_id: str,
        title: str | None = None,
        summary: str | None = None,
        steps_to_reproduce: str | None = None,
        expected_result: str | None = None,
        actual_result: str | None = None,
        failure_rate: str | None = None,
        additional_information: str | None = None,
        cid: str | None = None,
        sku: str | None = None,
        tags: list[str] | None = None,
        project: str | None = None,
        severity: str | None = None,
        issue_file_time: str | None = None,
        platform_tags: list[str] | None = None,
        impacted_features: list[str] | None = None,
        impacted_vendors: list[str] | None = None,
        assignee: str | None = None,
        tracker: str | None = None,
        description: str | None = None,
        attach_logs: list[str] | None = None,
    ) -> str:
        """Use this when the user wants to change or correct a draft after get_draft(): pass the draft_id and ONLY the fields to change (omitted fields keep their value; a section you pass replaces that whole section). The draft_id stays the same. It does NOT submit anything. ALWAYS call get_draft(draft_id) again, show the full preview and ask for approval again. Only pass description to replace the whole text. Use severity to change the severity and attach_logs (full list of file names from list_collected_logs(); [] attaches nothing) to choose which logs are uploaded."""
        old = bugs.get_draft(draft_id)
        changes: dict[str, Any] = dict(
            title=title, tags=tags, project=project, severity=severity,
            issue_file_time=issue_file_time, platform_tags=platform_tags,
            impacted_features=impacted_features, impacted_vendors=impacted_vendors,
            assignee=assignee, tracker=tracker, attach_logs=attach_logs,
        )  # fmt: skip
        edits = {
            "Summary": summary,
            "Steps to Reproduce": steps_to_reproduce,
            "Expected Result": expected_result,
            "Actual Result": actual_result,
            "Failure Rate": failure_rate,
            "cid": cid,
            "sku": sku,
            "extra_info": additional_information,
        }
        edits = {k: v for k, v in edits.items() if v is not None}
        if description is not None:
            changes.update(description=description, parts={})
        elif edits:
            if old.parts is None:
                raise ValueError(
                    "This draft was created from a full description; pass description instead"
                )
            parts = {**old.parts, **edits}
            changes.update(parts=parts, description=await _render(parts, old.test_case))
        draft = await blocking(lambda: bugs.update_draft(draft_id, **changes))()
        return draft.draft_id

    @mcp.tool()
    async def get_draft(draft_id: str) -> str:
        """Use this after draft_bug() to get the exact report that will be filed, laid out as in bugit-v2 (metadata, title, description sections, Stage and Job Output). ALWAYS show it to the user in full and wait for explicit approval before confirm_submission(). Read-only."""
        return await blocking(lambda: _preview(bugs.get_draft(draft_id)))()

    @mcp.tool()
    async def confirm_submission(draft_id: str, ctx: Context) -> dict[str, Any]:  # type: ignore[type-arg]
        """Executes the FINAL, IRREVERSIBLE submission of a draft to the issue tracker. NEVER call this without the user's explicit approval of the draft in this conversation. Needs a draft_id from draft_bug() and a logged-in tracker (see check_auth_status())."""
        draft = bugs.get_draft(draft_id)
        if draft.logs_job_id and not draft.attach_logs:
            files = await blocking(logstore.list_files)(jobs.job_dir(draft.logs_job_id))
            names = [f["name"] for f in files]
            picked = await ask_log_selection(ctx, names) if names else None
            if picked:
                bugs.update_draft(draft_id, attach_logs=picked)
                draft = bugs.get_draft(draft_id)
        approved = await ask_user_approval(
            ctx, f"Submit this bug?\n\n{await blocking(_preview)(draft)}"
        )
        if not approved:
            raise ValueError(
                "The user declined to submit this draft; do not retry without asking."
            )
        return await blocking(bugs.confirm_submission)(draft_id)

    return mcp


def wrap_asgi(app: ASGIApp, cfg: McpConfig) -> ASGIApp:
    return BearerAuthMiddleware(app, cfg.api_key)


def build_default_services(cfg: McpConfig) -> dict[str, Any]:
    """Wire the real implementations (Launchpad, Jira, bugit log collectors)"""
    from bugit_v2.bug_report_submitters.jira_submitter import (
        JIRA_SERVER_ADDRESS,
        JiraSubmitter,
    )
    from bugit_v2.bug_report_submitters.launchpad_submitter import (
        LP_APP_NAME,
        LP_AUTH_FILE_PATH,
        SERVICE_ROOT,
        LaunchpadSubmitter,
    )
    from bugit_v2.bug_report_submitters.mock_jira import MockJiraSubmitter
    from bugit_v2.bug_report_submitters.mock_lp import MockLaunchpadSubmitter
    from bugit_v2.dut_utils.info_getters import get_standard_info
    from bugit_v2.dut_utils.log_collectors import LOG_NAME_TO_COLLECTOR
    from bugit_v2.mcp_server.authstate import (
        RealLaunchpadLoginFlow,
        default_validate_jira,
        default_validate_lp,
    )
    from bugit_v2.mcp_server.collector import describe_collectors, make_collector
    from bugit_v2.mcp_server.redact import Redactor
    from bugit_v2.mcp_server.trackers import JiraTracker, LaunchpadTracker, LocalTracker
    from bugit_v2.models.dut_info import get_saved_dut_info
    from bugit_v2.utils import is_prod

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    redactor = Redactor.for_current_user()
    home = Path(os.environ.get("SNAP_REAL_HOME") or Path.home())
    submission_dirs = cfg.submission_dirs or (
        home / ".local/share/checkbox-ng",
        home,
        Path("/var/tmp/checkbox-ng"),
    )
    checkbox = CheckboxService(submission_dirs=submission_dirs)
    jobs = JobManager(
        cfg.work_dir / "jobs",
        make_collector(checkbox, LOG_NAME_TO_COLLECTOR),
        redact=redactor.redact,
    )
    auth = AuthService(
        lp_auth_file=LP_AUTH_FILE_PATH,
        jira_creds_file=Path("/tmp/jira_submitter-credentials.json"),
        lp_flow=RealLaunchpadLoginFlow(LP_APP_NAME, SERVICE_ROOT),
        validate_lp=default_validate_lp(LP_APP_NAME, SERVICE_ROOT),
        validate_jira=default_validate_jira(JIRA_SERVER_ADDRESS),
    )

    trackers = {
        "local": LocalTracker(cfg.work_dir / "reports"),
        "launchpad": LaunchpadTracker(
            LaunchpadSubmitter if is_prod() else MockLaunchpadSubmitter,
            cfg.default_project,
            report_source=checkbox.collector_input,
        ),
        "jira": JiraTracker(
            JiraSubmitter if is_prod() else MockJiraSubmitter,
            cfg.default_project,
            auth.jira_credentials,
            report_source=checkbox.collector_input,
        ),
    }
    bugs = BugService(
        trackers,
        job_dir=jobs.job_dir,
        redact=redactor.redact,
        default_tracker=cfg.default_tracker,
        default_project=cfg.default_project,
    )

    def dut_defaults() -> DutInfo | None:
        try:
            return get_saved_dut_info(search_other_users=True)
        except Exception:
            logger.exception("Could not read the saved dut-info")
            return None

    return dict(
        checkbox=checkbox,
        jobs=jobs,
        bugs=bugs,
        auth=auth,
        logstore=LogStore(redactor),
        log_catalog=describe_collectors(LOG_NAME_TO_COLLECTOR),
        machine_info=get_standard_info,
        dut_defaults=dut_defaults,
    )


def build_app(cfg: McpConfig, **services: Any) -> ASGIApp:
    services = services or build_default_services(cfg)
    return wrap_asgi(build_server(cfg, **services).sse_app(), cfg)
