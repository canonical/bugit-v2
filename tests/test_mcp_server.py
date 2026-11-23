import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from bugit_v2.mcp_server.authstate import AuthService
from bugit_v2.mcp_server.bugs import BugService, Draft
from bugit_v2.mcp_server.checkbox import CheckboxService
from bugit_v2.mcp_server.config import McpConfig
from bugit_v2.mcp_server.jobs import JobManager
from bugit_v2.mcp_server.logstore import LogStore
from bugit_v2.mcp_server.redact import Redactor
from bugit_v2.mcp_server.server import build_server
from bugit_v2.models.dut_info import DutInfo
from tests.test_mcp_checkbox import make_session

SPEC_TOOLS = {
    "update_draft",
    "check_auth_status",
    "get_login_url",
    "list_checkbox_submissions",
    "list_failed_tests",
    "get_failed_test_cases",
    "start_log_collection",
    "list_log_collectors",
    "check_job_status",
    "list_collected_logs",
    "grep_log",
    "tail_log",
    "draft_bug",
    "confirm_submission",
    "get_draft",  # read-only preview of the bugit-v2 formatted report
    "triage_failures",  # grep_log + draft_bug in one call
}


class FakeTracker:
    def __init__(self) -> None:
        self.submitted: list[Draft] = []

    def submit(self, draft: Draft, logs_dir: Path | None) -> dict[str, Any]:
        self.submitted.append(draft)
        return {"url": "http://bug/9", "id": "9"}


SAVED_DUT_INFO: dict[str, DutInfo | None] = {
    "v": DutInfo(
        cid="202401-12345",
        sku="SKU9",
        project="saved",
        platform_tags=["pt1"],
        jira_assignee="a@b.com",
        lp_assignee="lpuser",
    )
}


class FakeFlow:
    def begin(self) -> str:
        return "https://login"

    def complete(self, p: Path) -> bool:
        return False


async def fake_machine_info():
    return {"Image": "img", "GPU": "alice-gpu"}


@pytest.fixture
def env(tmp_path: Path):
    sessions = tmp_path / "sessions"
    make_session(sessions, "s1", {"wifi/connect": "fail", "ok/test": "pass"})
    redactor = Redactor("alice")
    checkbox = CheckboxService(sessions, [])

    async def collector(target: Path, ctx, progress):
        progress("working")
        (target / "dmesg.log").write_text(
            "boot\nACPI Error: AE_NOT_FOUND from alice at 10.0.0.5\nend\n"
        )
        (target / "ctx.txt").write_text(f"{ctx.context_test_case}|{ctx.submission_id}")

    jobs = JobManager(tmp_path / "jobs", collector, redact=redactor.redact)
    tracker = FakeTracker()
    bugs = BugService(
        {"launchpad": tracker, "jira": FakeTracker()},
        job_dir=jobs.job_dir,
        redact=redactor.redact,
        default_project="proj",
    )
    auth = AuthService(
        lp_auth_file=tmp_path / "lp",
        jira_creds_file=tmp_path / "jira",
        env={},
        lp_flow=FakeFlow(),
        validate_lp=lambda p: None,
        validate_jira=lambda e, t: None,
    )
    cfg = McpConfig(api_key="k", work_dir=tmp_path)
    server = build_server(
        cfg,
        checkbox=checkbox,
        jobs=jobs,
        bugs=bugs,
        auth=auth,
        logstore=LogStore(redactor),
        log_catalog=[{"name": "dmesg", "description": "d", "default": True}],
        machine_info=fake_machine_info,
        dut_defaults=lambda: SAVED_DUT_INFO["v"],
    )
    return server, tracker, jobs


async def call(server, name: str, **args):
    result = await server.call_tool(name, args)
    # FastMCP returns (content, structured) or content depending on version
    content = result[0] if isinstance(result, tuple) else result
    if (
        isinstance(result, tuple)
        and isinstance(result[1], dict)
        and "result" in result[1]
    ):
        return result[1]["result"]
    text = content[0].text
    try:
        return json.loads(text)
    except ValueError:
        return text


async def test_exposes_exactly_the_spec_tools(env):
    server, *_ = env
    assert {t.name for t in await server.list_tools()} == SPEC_TOOLS


async def test_tool_descriptions_are_llm_instructions(env):
    server, *_ = env
    tools = {t.name: t for t in await server.list_tools()}
    assert "confirm_submission" in tools
    assert (
        "ALWAYS" in tools["confirm_submission"].description
        or "explicit" in tools["confirm_submission"].description.lower()
    )
    for t in tools.values():
        assert len(t.description or "") > 40, t.name


async def test_playbook_prompt(env):
    server, *_ = env
    prompts = await server.list_prompts()
    assert [p.name for p in prompts] == ["bug_triage_playbook"]
    got = await server.get_prompt("bug_triage_playbook")
    text = got.messages[0].content.text
    assert "Canonical bugit-v2 SRE Assistant" in text
    for tool in (
        "list_failed_tests",
        "start_log_collection",
        "check_job_status",
        "grep_log",
        "tail_log",
        "draft_bug",
        "triage_failures",
        "confirm_submission",
    ):
        assert f"`{tool}()`" in text
    assert "Call `confirm_submission()` only after an explicit approval" in text
    assert "FOUR times" in text and "<testplan_id> @ <failed_at>" in text
    assert "ALWAYS call `draft_bug()`" in text
    assert "`get_draft()`" in text


async def test_unknown_log_name_is_rejected(env):
    server, _, _ = env
    assert (await call(server, "list_log_collectors"))[0]["name"] == "dmesg"
    with pytest.raises(Exception, match="Unknown log collector"):
        await call(server, "start_log_collection", log_names=["nope"])


async def test_full_workflow(env):
    server, tracker, jobs = env

    subs = await call(server, "list_checkbox_submissions")
    assert subs[0]["submission_id"] == "session:s1"

    failed = await call(server, "get_failed_test_cases", submission_id="session:s1")
    assert [c["test_id"] for c in failed] == ["wifi/connect"]

    job_id = await call(server, "start_log_collection", context_test_case="wifi/connect")
    async with asyncio.timeout(3):
        while (st := await call(server, "check_job_status", job_id=job_id))[
            "status"
        ] == "running":
            await asyncio.sleep(0.01)
    assert st["status"] == "completed"

    files = await call(server, "list_collected_logs", job_id=job_id)
    assert "dmesg.log" in [f["name"] for f in files]
    # the submission picked in get_failed_test_cases is remembered
    ctx_file = (jobs.job_dir(job_id) / "ctx.txt").read_text()
    assert ctx_file == "wifi/connect|session:s1"

    out = await call(
        server, "grep_log", job_id=job_id, filename="dmesg.log", keyword="acpi"
    )
    assert "AE_NOT_FOUND" in out and "alice" not in out and "10.0.0.5" not in out
    out = await call(server, "tail_log", job_id=job_id, filename="dmesg.log", lines=1)
    assert out == "end"

    draft_id = await call(
        server,
        "draft_bug",
        title="ACPI error",
        description="alice saw AE_NOT_FOUND",
        tags=["acpi"],
        logs_job_id=job_id,
    )
    assert tracker.submitted == []
    result = await call(server, "confirm_submission", draft_id=draft_id)
    assert result["url"] == "http://bug/9"
    assert tracker.submitted[0].description == "<USER> saw AE_NOT_FOUND"


async def test_path_traversal_is_an_error_not_a_leak(env):
    server, *_ = env
    job_id = await call(server, "start_log_collection")
    await asyncio.sleep(0.2)
    with pytest.raises(Exception):
        await server.call_tool(
            "tail_log", {"job_id": job_id, "filename": "../../etc/passwd", "lines": 5}
        )


async def test_confirm_without_draft_is_an_error(env):
    server, tracker, _ = env
    with pytest.raises(Exception):
        await server.call_tool("confirm_submission", {"draft_id": "nope"})
    assert tracker.submitted == []


async def test_user_decline_via_elicitation_blocks_submission(env, monkeypatch):
    server, tracker, _ = env
    import bugit_v2.mcp_server.server as srv

    async def decline(ctx, message):
        return False

    monkeypatch.setattr(srv, "ask_user_approval", decline)
    draft_id = await call(
        server, "draft_bug", title="t", description="d", tags=[], logs_job_id=None
    )
    with pytest.raises(Exception, match="declined|approv"):
        await server.call_tool("confirm_submission", {"draft_id": draft_id})
    assert tracker.submitted == []
    # the draft survives a decline so the user can change their mind
    monkeypatch.setattr(
        srv, "ask_user_approval", lambda ctx, message: asyncio.sleep(0, True)
    )
    await call(server, "confirm_submission", draft_id=draft_id)
    assert len(tracker.submitted) == 1


async def test_auth_tools(env):
    server, *_ = env
    st = await call(server, "check_auth_status", service="launchpad")
    assert st["authenticated"] is False
    assert await call(server, "get_login_url", service="launchpad") == "https://login"


async def _collect(server, test_case="wifi/connect"):
    await call(server, "get_failed_test_cases", submission_id="session:s1")
    job_id = await call(server, "start_log_collection", context_test_case=test_case)
    async with asyncio.timeout(3):
        while (await call(server, "check_job_status", job_id=job_id))[
            "status"
        ] == "running":
            await asyncio.sleep(0.01)
    return job_id


async def test_draft_from_sections_follows_bugit_layout(env):
    server, tracker, _ = env
    job_id = await _collect(server)
    draft_id = await call(
        server,
        "draft_bug",
        title="Wifi fails",
        summary="alice cannot connect",
        steps_to_reproduce="run the test",
        actual_result="timeout",
        logs_job_id=job_id,
        platform_tags=["numbat"],
        project="somerville",
    )
    await call(server, "confirm_submission", draft_id=draft_id)
    d = tracker.submitted[0]
    assert d.description == (
        "[Summary]\n<USER> cannot connect\n\n"
        "[Steps to Reproduce]\nrun the test\n\n"
        "[Expected Result]\n\n"
        "[Actual Result]\ntimeout\n\n"
        "[Failure Rate]\n\n"
        "[Affected Test Cases]\nwifi/connect\n\n"
        "[Additional Information]\nCID: 202401-12345\nSKU: SKU9\nImage: img\nGPU: <USER>-gpu"
    )
    assert (d.severity, d.issue_file_time) == ("highest", "immediate")
    assert (d.submission_id, d.test_case) == ("session:s1", "wifi/connect")
    assert d.platform_tags == ["numbat"] and d.project == "somerville"


async def test_failed_tests_and_cid_sku_override(env):
    server, tracker, _ = env
    groups = await call(server, "list_failed_tests")
    assert [
        (g["submission_id"], [t["test_id"] for t in g["tests"]]) for g in groups
    ] == [("session:s1", ["wifi/connect"])]
    assert "testplan_id" in groups[0] and "modified" in groups[0]
    assert "failed_at" in groups[0]["tests"][0]
    draft_id = await call(
        server, "draft_bug", title="T", summary="s", cid="202402-22222", sku="X-1"
    )
    await call(server, "confirm_submission", draft_id=draft_id)
    assert "CID: 202402-22222\nSKU: X-1" in tracker.submitted[0].description


async def test_failed_tests_grouped_by_submission_newest_first(env, tmp_path):
    server, *_ = env
    make_session(
        tmp_path / "sessions",
        "s2",
        {"gfx/glxgears": "fail"},
        mtime=time.time() + 100,
    )
    groups = await call(server, "list_failed_tests")
    assert [g["submission_id"] for g in groups] == ["session:s2", "session:s1"]
    assert [t["test_id"] for t in groups[0]["tests"]] == ["gfx/glxgears"]
    assert [t["test_id"] for t in groups[1]["tests"]] == ["wifi/connect"]
    for g in groups:
        assert g["testplan_id"] and g["modified"]
        for t in g["tests"]:
            assert t["outcome"] and t["failed_at"]


async def test_cid_sku_blank_and_warned_without_saved_dut_info(env, monkeypatch, caplog):
    server, tracker, _ = env
    monkeypatch.setitem(SAVED_DUT_INFO, "v", None)
    draft_id = await call(server, "draft_bug", title="T", summary="s")
    await call(server, "confirm_submission", draft_id=draft_id)
    assert "CID: \nSKU: \n" in tracker.submitted[0].description
    assert "No saved dut-info found" in caplog.text


async def test_get_draft_previews_exactly_what_will_be_filed(env):
    server, *_ = env
    job_id = await _collect(server)
    draft_id = await call(
        server, "draft_bug", title="T", summary="s", logs_job_id=job_id, severity="low"
    )
    preview = await call(server, "get_draft", draft_id=draft_id)
    assert "Severity: Low" in preview and "Title: T" in preview
    assert "[Summary]\ns\n" in preview and "[Stage]\nRight after it happened" in preview
    assert "[Job Output]" in preview and "out of wifi/connect" in preview


async def test_confirmation_prompt_shows_the_same_preview(env, monkeypatch):
    server, *_ = env
    import bugit_v2.mcp_server.server as srv

    seen = []

    async def approve(ctx, message):
        seen.append(message)
        return True

    monkeypatch.setattr(srv, "ask_user_approval", approve)
    draft_id = await call(server, "draft_bug", title="T", summary="s")
    await call(server, "confirm_submission", draft_id=draft_id)
    assert "[Stage]" in seen[0] and "Title: T" in seen[0]


async def test_invalid_severity_is_rejected_for_the_agent(env):
    server, *_ = env
    with pytest.raises(Exception, match="severity"):
        await server.call_tool(
            "draft_bug", {"title": "T", "summary": "s", "severity": "urgent"}
        )


async def test_draft_needs_a_summary_or_description(env):
    server, *_ = env
    with pytest.raises(Exception, match="summary|description"):
        await server.call_tool("draft_bug", {"title": "T"})


async def test_draft_defaults_come_from_saved_dut_info(env):
    server, *_ = env
    draft_id = await call(server, "draft_bug", title="T", summary="s")
    preview = await call(server, "get_draft", draft_id=draft_id)
    assert "Project: saved" in preview and "Platform tags: pt1" in preview
    assert "Assignee: lpuser" in preview and "CID: 202401-12345" in preview
    draft_id = await call(
        server, "draft_bug", title="T", summary="s", tracker="jira", project="mine"
    )
    preview = await call(server, "get_draft", draft_id=draft_id)
    assert "Project: mine" in preview and "Assignee: a@b.com" in preview


async def test_update_draft_changes_only_what_was_given(env):
    server, tracker, _ = env
    draft_id = await call(
        server, "draft_bug", title="T", summary="s", actual_result="bad", severity="low"
    )
    same_id = await call(
        server,
        "update_draft",
        draft_id=draft_id,
        actual_result="worse",
        title="T2",
        cid="202402-22222",
    )
    assert same_id == draft_id
    preview = await call(server, "get_draft", draft_id=draft_id)
    assert "Title: T2" in preview and "Severity: Low" in preview
    assert "[Summary]\ns\n" in preview and "[Actual Result]\nworse\n" in preview
    assert "CID: 202402-22222" in preview
    with pytest.raises(Exception, match="severity"):
        await server.call_tool(
            "update_draft", {"draft_id": draft_id, "severity": "urgent"}
        )
    await call(server, "confirm_submission", draft_id=draft_id)
    assert tracker.submitted[0].title == "T2"


async def test_severity_and_attached_logs_can_be_chosen_after_drafting(env):
    server, tracker, _ = env
    job_id = await _collect(server)
    draft_id = await call(
        server, "draft_bug", title="T", summary="s", logs_job_id=job_id
    )
    assert "Attached logs: none" in await call(server, "get_draft", draft_id=draft_id)
    name = (await call(server, "list_collected_logs", job_id=job_id))[0]["name"]
    await call(
        server, "update_draft", draft_id=draft_id, severity="medium", attach_logs=[name]
    )
    preview = await call(server, "get_draft", draft_id=draft_id)
    assert "Severity: Medium" in preview and f"Attached logs: {name}" in preview
    await call(server, "confirm_submission", draft_id=draft_id)
    assert tracker.submitted[0].attach_logs == [name]


async def test_confirm_elicits_the_logs_to_attach(env, monkeypatch):
    server, tracker, _ = env
    import bugit_v2.mcp_server.server as srv

    job_id = await _collect(server)
    name = (await call(server, "list_collected_logs", job_id=job_id))[0]["name"]
    offered = []

    async def pick(ctx, names):
        offered.extend(names)
        return [name]

    monkeypatch.setattr(srv, "ask_log_selection", pick)
    draft_id = await call(
        server, "draft_bug", title="T", summary="s", logs_job_id=job_id
    )
    await call(server, "confirm_submission", draft_id=draft_id)
    assert name in offered
    assert tracker.submitted[0].attach_logs == [name]


def _io_log(server_jobs, job_id: str, test_id: str, stderr: str) -> None:
    d = server_jobs.job_dir(job_id) / "checkbox_session" / "io-logs"
    d.mkdir(parents=True, exist_ok=True)
    name = test_id.replace("::", "__").replace("/", "_")
    (d / f"{name}.stderr").write_text(stderr)


async def test_triage_failures_drafts_one_bug_for_every_test(env):
    server, tracker, jobs = env
    job_id = await _collect(server)
    tests = ["ns::graphics/2_glxgears", "ns::graphics/2_vkcube"]
    for t in tests:
        _io_log(
            jobs, job_id, t, "Traceback\n  File x\nAssertionError: no card for alice\n"
        )

    out = await call(server, "triage_failures", job_id=job_id, test_ids=tests)

    assert out["errors"] == {t: ["AssertionError: no card for <USER>"] for t in tests}
    assert out["warnings"] == []
    assert out["logs"] == {
        t: {"stderr": "Traceback\n  File x\nAssertionError: no card for <USER>\n", "stdout": ""}
        for t in tests
    }  # fmt: skip
    assert out["preview"] == await call(server, "get_draft", draft_id=out["draft_id"])
    assert (
        "Title: 2 graphics tests fail: AssertionError: no card for <USER>"
        in out["preview"]
    )
    assert (
        "[Affected Test Cases]\nns::graphics/2_glxgears\nns::graphics/2_vkcube\n"
        in out["preview"]
    )
    assert "[Failure Rate]\n2/2 tests in 1/1 runs" in out["preview"]
    assert tracker.submitted == []
    # the test list survives edits of other sections
    await call(server, "update_draft", draft_id=out["draft_id"], summary="new")
    assert "ns::graphics/2_vkcube" in await call(
        server, "get_draft", draft_id=out["draft_id"]
    )


async def test_triage_failures_overrides_fallbacks_and_warnings(env):
    server, *_ = env
    job_id = await _collect(server)
    out = await call(
        server, "triage_failures", job_id=job_id,
        test_ids=["wifi/connect", "gone/test"], title="My title", severity="low",
    )  # fmt: skip
    # no collected io-log: falls back to the session output (no keyword match -> last lines)
    assert out["errors"] == {"wifi/connect": ["err of wifi/connect"], "gone/test": []}
    assert out["logs"]["wifi/connect"]["stderr"] == "err of wifi/connect"
    assert out["logs"]["gone/test"] == {"stdout": "", "stderr": ""}
    assert "No error output found for gone/test" in out["warnings"]
    assert "Title: My title" in out["preview"] and "Severity: Low" in out["preview"]


async def test_triage_failures_needs_a_known_job_and_tests(env):
    server, *_ = env
    with pytest.raises(Exception, match="Unknown job_id"):
        await call(server, "triage_failures", job_id="nope", test_ids=["a"])
    job_id = await _collect(server)
    with pytest.raises(Exception, match="at least one"):
        await call(server, "triage_failures", job_id=job_id, test_ids=[" "])


def test_collector_for_file_groups_collector_output():
    from bugit_v2.mcp_server.collector import collector_for_file

    assert collector_for_file("checkbox_session/io-logs/x.stderr") == "checkbox-session"
    assert collector_for_file("dmesg-of-boot-abc.log") == "dmesg"
    assert collector_for_file("journalctl_3_days.log") == "journalctl-3-days"
    assert collector_for_file("snap_list.log") == "snap-list"
    assert collector_for_file("context.txt") == "context"
    assert collector_for_file("job_stderr.txt") == "context"
    assert collector_for_file("mystery.bin") == "other"


async def test_failed_tests_carry_cert_status_and_suggested_severity(env):
    server, *_ = env
    groups = await call(server, "list_failed_tests")
    test = groups[0]["tests"][0]
    assert "cert_status" in test and "suggested_severity" in test
