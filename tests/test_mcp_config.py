from pathlib import Path

import pytest

from bugit_v2.mcp_server.config import McpConfig


def test_defaults_are_loopback_only():
    c = McpConfig.from_env({})
    assert c.bind_address == "127.0.0.1" and c.port == 8000
    assert c.api_key is None and c.idle_timeout == 0


def test_from_env():
    c = McpConfig.from_env(
        {
            "BUGIT_MCP_API_KEY": "abc",
            "BUGIT_MCP_BIND_ADDRESS": "0.0.0.0",
            "BUGIT_MCP_PORT": "9001",
            "BUGIT_MCP_DEFAULT_PROJECT": "oem",
            "BUGIT_MCP_SUBMISSION_DIRS": "/a:/b",
            "BUGIT_MCP_IDLE_TIMEOUT": "60",
            "BUGIT_MCP_WORK_DIR": "/w",
        }
    )
    assert (c.api_key, c.bind_address, c.port) == ("abc", "0.0.0.0", 9001)
    assert c.default_project == "oem" and c.idle_timeout == 60
    assert c.submission_dirs == (Path("/a"), Path("/b"))
    assert c.work_dir == Path("/w")


def test_api_key_file_wins_when_env_missing(tmp_path: Path):
    f = tmp_path / "key"
    f.write_text("  filekey\n")
    assert McpConfig.from_env({"BUGIT_MCP_API_KEY_FILE": str(f)}).api_key == "filekey"
    assert (
        McpConfig.from_env({"BUGIT_MCP_API_KEY": "envkey", "BUGIT_MCP_API_KEY_FILE": str(f)}).api_key
        == "envkey"
    )


def test_overrides_replace_values_but_skip_none():
    c = McpConfig.from_env({"BUGIT_MCP_PORT": "9001"}).with_overrides(
        port=None, bind_address="0.0.0.0", api_key="x"
    )
    assert (c.port, c.bind_address, c.api_key) == (9001, "0.0.0.0", "x")


@pytest.mark.parametrize("port", ["0", "70000", "abc", "-1"])
def test_bad_port_rejected(port: str):
    with pytest.raises(ValueError, match="port"):
        McpConfig.from_env({"BUGIT_MCP_PORT": port})
