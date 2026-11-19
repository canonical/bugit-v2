"""Entry point for `bugit-v2 mcp`"""

import asyncio
import os
from collections.abc import Mapping

from starlette.types import ASGIApp, Receive, Scope, Send

from bugit_v2.mcp_server.config import McpConfig

SD_LISTEN_FDS_START = 3


def resolve_config(
    env: Mapping[str, str],
    *,
    api_key: str | None,
    bind_address: str | None,
    port: int | None,
) -> McpConfig:
    cfg = McpConfig.from_env(env).with_overrides(
        api_key=api_key, bind_address=bind_address, port=port
    )
    if not cfg.api_key:
        raise ValueError(
            "An API key is required: use --api-key, BUGIT_MCP_API_KEY, "
            + "or `snap set bugit-v2 mcp.api-key=...`"
        )
    return cfg


def socket_activation_fd(env: Mapping[str, str], pid: int | None = None) -> int | None:
    if env.get("LISTEN_PID") != str(pid if pid is not None else os.getpid()):
        return None
    if int(env.get("LISTEN_FDS") or 0) < 1:
        return None
    return SD_LISTEN_FDS_START


def restrict_local_address(app: ASGIApp, bind_address: str) -> ASGIApp:
    """The snapd socket listens on all interfaces; enforce `mcp.bind-address`
    by refusing connections that arrived on any other local address."""
    wildcard = bind_address in ("0.0.0.0", "::", "")

    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not wildcard:
            server = scope.get("server")
            local = server[0] if server else None
            if local not in (bind_address, f"::ffff:{bind_address}"):
                await send({"type": "http.response.start", "status": 403, "headers": []})
                await send({"type": "http.response.body", "body": b"Forbidden"})
                return
        await app(scope, receive, send)

    return middleware


async def _serve(cfg: McpConfig) -> None:
    import uvicorn

    from bugit_v2.mcp_server.idle import IdleTracker, watch_idle
    from bugit_v2.mcp_server.server import build_app, build_default_services

    services = build_default_services(cfg)
    tracker = IdleTracker()
    app = tracker.wrap(build_app(cfg, **services))
    fd = socket_activation_fd(os.environ)
    if fd is not None:
        app = restrict_local_address(app, cfg.bind_address)
    uv_cfg = (
        uvicorn.Config(app, fd=fd, log_level="info")
        if fd is not None
        else uvicorn.Config(app, host=cfg.bind_address, port=cfg.port, log_level="info")
    )
    server = uvicorn.Server(uv_cfg)
    jobs, bugs = services["jobs"], services["bugs"]
    watcher = asyncio.create_task(
        watch_idle(
            server,
            tracker,
            cfg.idle_timeout,
            is_busy=lambda: jobs.has_running() or bugs.has_drafts(),
        )
    )
    try:
        await server.serve()
    finally:
        watcher.cancel()


def run(cfg: McpConfig) -> None:
    asyncio.run(_serve(cfg))
