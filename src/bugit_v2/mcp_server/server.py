"""The MCP server: tool/prompt definitions and the ASGI app"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.types import ASGIApp

from bugit_v2.mcp_server.auth import BearerAuthMiddleware
from bugit_v2.mcp_server.authstate import AuthService
from bugit_v2.mcp_server.bugs import BugService
from bugit_v2.mcp_server.checkbox import CheckboxService
from bugit_v2.mcp_server.config import McpConfig
from bugit_v2.mcp_server.jobs import JobManager
from bugit_v2.mcp_server.logstore import LogStore
from bugit_v2.mcp_server.playbook import BUG_TRIAGE_PLAYBOOK
from bugit_v2.models.dut_info import DutInfo
from bugit_v2.mcp_server.report import SECTION_NAMES, format_preview, render_description

logger = logging.getLogger(__name__)


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
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
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
        """Use this INSTEAD of calling list_checkbox_submissions() and get_failed_test_cases() one by one. Returns, in one call, every failed test of the recent Checkbox runs (newest first), each with submission_id, testplan_id, modified, test_id, outcome and failed_at. ALWAYS show testplan_id and failed_at in front of each test_id when asking the user which failure to investigate."""
        rows = await blocking(checkbox.list_failed_tests)()
        if rows:
            state.last_submission = rows[0]["submission_id"]
        return rows

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
        return jobs.start(context_test_case, submission_id or state.last_submission, names)

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
        """Use this to list the files (name, size) collected by a completed job_id. Pass these names as filename to grep_log() and tail_log()."""
        return await blocking(logstore.list_files)(jobs.job_dir(job_id))

    @mcp.tool()
    async def grep_log(job_id: str, filename: str, keyword: str) -> str:
        """Use this to search a collected log for an error string (case-insensitive literal match, not a regex). Output is PII-redacted and limited to the first 200 matches; use the line numbers with tail_log() context. Start with broad keywords such as 'error', 'panic', 'ACPI', 'firmware', 'failed' near the failure time from context.txt."""
        return await blocking(logstore.grep)(jobs.job_dir(job_id), filename, keyword)

    @mcp.tool()
    async def tail_log(job_id: str, filename: str, lines: int = 50) -> str:
        """Use this to read the last N lines (max 1000) of a collected log file. Output is PII-redacted."""
        return await blocking(logstore.tail)(jobs.job_dir(job_id), filename, lines)

    @mcp.tool()
    async def search_bugs(query: str, tracker: str = "launchpad") -> list[dict[str, Any]]:
        """Use this BEFORE drafting a bug to check for duplicates (read-only). tracker is 'launchpad', 'jira' or 'local'. Search with the distinctive error string you found. If a matching bug exists, warn the user about it but STILL call draft_bug() and show the draft; the user decides whether to file."""
        return await blocking(bugs.search_bugs)(query, tracker)

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
            output = {k: logstore.redact(str(v)) for k, v in raw.items()} if raw else None
        return format_preview(draft, output)

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
        """Use this to prepare a bug report in the same format as bugit-v2. It does NOT submit anything: it validates the payload and returns a draft_id. Fill summary, steps_to_reproduce, expected_result, actual_result and failure_rate; the server adds Affected Test Cases and Additional Information (CID, SKU, machine info) and, on submit, the Stage and Job Output. cid, sku, project, assignee, platform_tags and tags default to the values saved by `bugit.dut-info` when omitted (explicit arguments win); severity is highest (Critical on Launchpad), high, medium, low or lowest (propose one from the impact; the user can change it at approval); issue_file_time is immediate, after_reboot or later; platform_tags are Jira components; tags are extra tracker labels. Pass logs_job_id to link the collected logs, and attach_logs (file names from list_collected_logs()) for the files to upload with the bug; NOTHING is uploaded unless attach_logs is set, so leave it empty until the user picks files. Then call get_draft(draft_id), show the whole preview to the user and ask for explicit approval (approve/reject) before confirm_submission(). Only pass description to bypass the template."""
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
        approved = await ask_user_approval(
            ctx, f"Submit this bug?\n\n{await blocking(_preview)(draft)}"
        )
        if not approved:
            raise ValueError("The user declined to submit this draft; do not retry without asking.")
        return await blocking(bugs.confirm_submission)(draft_id)

    return mcp


def wrap_asgi(app: ASGIApp, cfg: McpConfig) -> ASGIApp:
    return BearerAuthMiddleware(app, cfg.api_key)


def build_default_services(cfg: McpConfig) -> dict[str, Any]:
    """Wire the real implementations (Launchpad, Jira, bugit log collectors)"""
    from launchpadlib.launchpad import Launchpad

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

    def jira_client() -> Any:
        from jira import JIRA

        creds = auth.jira_credentials()
        if creds is None:
            raise RuntimeError("No Jira credentials; call check_auth_status('jira')")
        return JIRA(server=JIRA_SERVER_ADDRESS.rstrip("/"), basic_auth=creds, validate=True)

    trackers = {
        "local": LocalTracker(cfg.work_dir / "reports"),
        "launchpad": LaunchpadTracker(
            lambda: Launchpad.login_anonymously(LP_APP_NAME, SERVICE_ROOT),
            LaunchpadSubmitter if is_prod() else MockLaunchpadSubmitter,
            cfg.default_project,
            report_source=checkbox.collector_input,
        ),
        "jira": JiraTracker(
            jira_client,
            JiraSubmitter if is_prod() else MockJiraSubmitter,
            JIRA_SERVER_ADDRESS,
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
