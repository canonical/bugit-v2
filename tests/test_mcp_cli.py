import pytest

from bugit_v2.mcp_server.cli import resolve_config, socket_activation_fd


def test_flags_override_env():
    cfg = resolve_config(
        {"BUGIT_MCP_API_KEY": "envkey", "BUGIT_MCP_PORT": "9000"},
        api_key="flagkey",
        bind_address="0.0.0.0",
        port=None,
    )
    assert (cfg.api_key, cfg.bind_address, cfg.port) == ("flagkey", "0.0.0.0", 9000)


def test_refuses_without_key():
    with pytest.raises(ValueError, match="API key"):
        resolve_config({}, api_key=None, bind_address=None, port=None)


def test_socket_activation_fd():
    assert socket_activation_fd({"LISTEN_FDS": "1", "LISTEN_PID": "42"}, pid=42) == 3
    assert socket_activation_fd({"LISTEN_FDS": "1", "LISTEN_PID": "41"}, pid=42) is None
    assert socket_activation_fd({}, pid=42) is None


def test_busy_predicates(tmp_path):
    from bugit_v2.mcp_server.bugs import BugService
    from bugit_v2.mcp_server.jobs import JobManager

    jm = JobManager(tmp_path, lambda *a, **k: None)
    assert jm.has_running() is False
    bs = BugService({}, job_dir=lambda j: tmp_path)
    assert bs.has_drafts() is False


async def test_restrict_local_address():
    from bugit_v2.mcp_server.cli import restrict_local_address

    sent = []

    async def app(scope, receive, send):
        sent.append("app")

    async def send(m):
        sent.append(m.get("status"))

    mw = restrict_local_address(app, "127.0.0.1")
    await mw({"type": "http", "server": ("127.0.0.1", 8000)}, None, send)
    await mw({"type": "http", "server": ("10.0.0.5", 8000)}, None, send)
    assert sent[0] == "app" and 403 in sent and sent.count("app") == 1
    open_mw = restrict_local_address(app, "0.0.0.0")
    await open_mw({"type": "http", "server": ("10.0.0.5", 8000)}, None, send)
    assert sent.count("app") == 2
