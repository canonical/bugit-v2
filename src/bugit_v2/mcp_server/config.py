"""Runtime configuration, from the environment (set by the snap) or CLI flags"""

import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bugit_v2.utils.constants import DISK_CACHE_DIR


def _port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError:
        raise ValueError(f"Invalid port: {raw!r}") from None
    if not 1 <= port <= 65535:
        raise ValueError(f"Invalid port: {raw!r}")
    return port


@dataclass(frozen=True, slots=True)
class McpConfig:
    api_key: str | None = None
    bind_address: str = "127.0.0.1"
    port: int = 8000
    work_dir: Path = DISK_CACHE_DIR / "mcp"
    default_tracker: str = "launchpad"
    default_project: str | None = None
    submission_dirs: tuple[Path, ...] = ()
    # seconds without any connection before exiting (0 = never). Used under
    # socket activation so the daemon uses no resources when idle
    idle_timeout: int = 0

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "McpConfig":
        defaults = cls()
        api_key = env.get("BUGIT_MCP_API_KEY") or None
        if api_key is None and (key_file := env.get("BUGIT_MCP_API_KEY_FILE")):
            try:
                api_key = Path(key_file).read_text().strip() or None
            except OSError:
                api_key = None
        dirs = env.get("BUGIT_MCP_SUBMISSION_DIRS")
        return cls(
            api_key=api_key,
            bind_address=env.get("BUGIT_MCP_BIND_ADDRESS") or defaults.bind_address,
            port=(
                _port(env["BUGIT_MCP_PORT"])
                if "BUGIT_MCP_PORT" in env
                else defaults.port
            ),
            work_dir=(
                Path(env["BUGIT_MCP_WORK_DIR"])
                if "BUGIT_MCP_WORK_DIR" in env
                else defaults.work_dir
            ),
            default_tracker=env.get("BUGIT_MCP_DEFAULT_TRACKER")
            or defaults.default_tracker,
            default_project=env.get("BUGIT_MCP_DEFAULT_PROJECT") or None,
            submission_dirs=(
                tuple(Path(p) for p in dirs.split(os.pathsep) if p) if dirs else ()
            ),
            idle_timeout=int(env.get("BUGIT_MCP_IDLE_TIMEOUT") or 0),
        )

    def with_overrides(self, **overrides: Any) -> "McpConfig":
        return dataclasses.replace(
            self, **{k: v for k, v in overrides.items() if v is not None}
        )
