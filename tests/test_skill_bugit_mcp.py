import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).parents[1] / ".github/skills/bugit/scripts/bugit_mcp.py"
_spec = importlib.util.spec_from_file_location("bugit_mcp", _PATH)
assert _spec and _spec.loader
bugit_mcp = importlib.util.module_from_spec(_spec)
sys.modules["bugit_mcp"] = bugit_mcp
_spec.loader.exec_module(bugit_mcp)

ROWS = [
    {
        "submission_id": "session:new",
        "testplan_id": "ns::plan",
        "failed_at": "2026-01-02T03:04:05.1+00:00",
        "test_id": "ns::gfx/a",
    },
    {
        "submission_id": "session:old",
        "testplan_id": "ns::plan",
        "failed_at": "2026-01-01T00:00:00+00:00",
        "test_id": "ns::gfx/a",
    },
]


GROUPS = [
    {
        "submission_id": "session:new",
        "testplan_id": "ns::plan",
        "modified": "2026-01-02T03:04:05",
        "tests": [
            {
                "test_id": "ns::gfx/a",
                "outcome": "fail",
                "failed_at": "2026-01-02T03:04:05.1+00:00",
            }
        ],
    },
    {
        "submission_id": "session:old",
        "testplan_id": "ns::plan",
        "modified": "2026-01-01T00:00:00",
        "tests": [
            {
                "test_id": "ns::gfx/a",
                "outcome": "fail",
                "failed_at": "2026-01-01T00:00:00+00:00",
            }
        ],
    },
]


def test_labels_round_trip_to_test_and_submission():
    labels = [bugit_mcp.label(r) for r in ROWS]
    assert labels[1] == "[plan @ 2026-01-01T00:00:00] gfx/a (session:old)"
    assert bugit_mcp.parse_tests(labels[1:], ROWS) == [("ns::gfx/a", "session:old")]
    # a bare id picks the newest run; duplicates collapse
    assert bugit_mcp.parse_tests(["gfx/a", "ns::gfx/a"], ROWS) == [
        ("ns::gfx/a", "session:new")
    ]
    with pytest.raises(bugit_mcp.Usage, match="not a failed test"):
        bugit_mcp.parse_tests(["gfx/zzz"], ROWS)


def test_flatten_groups_reconstructs_flat_rows_for_parse_tests():
    flat = bugit_mcp.flatten_groups(GROUPS)
    assert [
        (r["submission_id"], r["testplan_id"], r["test_id"], r["failed_at"])
        for r in flat
    ] == [
        (r["submission_id"], r["testplan_id"], r["test_id"], r["failed_at"])
        for r in ROWS
    ]
    # the flattened rows keep working with parse_tests, unchanged
    assert bugit_mcp.parse_tests(["gfx/a"], flat) == [("ns::gfx/a", "session:new")]


def test_cmd_failures_prints_tests_grouped_by_submission(capsys):
    class FakeClient:
        def call(self, tool, **args):
            assert tool == "list_failed_tests"
            return GROUPS

    assert bugit_mcp.cmd_failures(FakeClient(), argparse.Namespace()) == 0
    out = json.loads(capsys.readouterr().out)
    assert [g["submission_id"] for g in out] == ["session:new", "session:old"]
    assert out[0]["tests"] == ["[plan @ 2026-01-02T03:04:05] gfx/a (session:new)"]


def test_cmd_triage_warns_on_cross_submission_selection(capsys):
    calls: list[str] = []

    class FakeClient:
        def call(self, tool, **args):
            calls.append(tool)
            if tool == "check_auth_status":
                return {"authenticated": True}
            if tool == "list_failed_tests":
                return GROUPS
            if tool == "start_log_collection":
                return "job1"
            if tool == "check_job_status":
                return {"status": "completed"}
            if tool == "triage_failures":
                return {
                    "draft_id": "d1",
                    "preview": "P",
                    "errors": {},
                    "logs": {},
                    "warnings": [],
                }
            raise AssertionError(f"unexpected tool {tool}")

    a = argparse.Namespace(
        tracker="local",
        logs="",
        keyword=None,
        override=None,
        tests=[bugit_mcp.label(r) for r in bugit_mcp.flatten_groups(GROUPS)],
    )
    assert bugit_mcp.cmd_triage(FakeClient(), a) == 0
    err = capsys.readouterr().err
    assert "WARNING: selected tests span 2 different submissions" in err


def test_submit_needs_approval_and_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("COPILOT_MCP_CONFIG", str(tmp_path / "mcp.json"))
    assert bugit_mcp.main(["submit", "1.2.3.4", "d1"]) == 1
    assert "--approved" in capsys.readouterr().err
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {}}))
    assert bugit_mcp.main(["failures", "1.2.3.4"]) == 1
    assert "run configure.sh 1.2.3.4" in capsys.readouterr().err


def test_iter_sse_parses_events_and_skips_pings():
    stream = [b": ping\r\n", b"event: endpoint\n", b"data: /messages/?s=1\n", b"\n"]
    stream += [b'data: {"a":\n', b"data: 1}\n", b"\n", b"event: message\n", b"data:x"]
    assert list(bugit_mcp.iter_sse(stream)) == [
        ("endpoint", "/messages/?s=1"),
        ("message", '{"a":\n1}'),
        ("message", "x"),
    ]


@pytest.fixture(params=["sse", "http"])
def mcp_url(request, tmp_path, monkeypatch):
    import socket
    import threading
    import time

    import uvicorn
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("t")

    @mcp.tool()
    def echo(text: str) -> dict:
        return {"echo": text}

    @mcp.tool()
    def boom() -> str:
        raise RuntimeError("it broke")

    app = mcp.sse_app() if request.param == "sse" else mcp.streamable_http_app()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    path = "/sse" if request.param == "sse" else "/mcp"
    config = {
        "type": request.param,
        "url": f"http://127.0.0.1:{port}{path}",
        "headers": {},
    }
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"bugit-dut": config}}))
    monkeypatch.setenv("COPILOT_MCP_CONFIG", str(tmp_path / "mcp.json"))
    yield
    server.should_exit = True
    thread.join(5)


def test_client_speaks_mcp_with_the_standard_library(mcp_url, capsys):
    assert bugit_mcp.with_client("dut", lambda c: c.call("echo", text="hi")) == {
        "echo": "hi"
    }
    with pytest.raises(bugit_mcp.ToolError, match="it broke"):
        bugit_mcp.with_client("dut", lambda c: c.call("boom"))
    # a server without the new tools is reported as too old
    assert bugit_mcp.main(["call", "dut", "triage_failures", "{}"]) == 3
    assert "too old" in capsys.readouterr().err
