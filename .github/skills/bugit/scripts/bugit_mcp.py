#!/usr/bin/env python3
"""Run the bugit triage steps against a DUT's MCP server from the shell.

The server URL and API key come from the Copilot CLI MCP config written by configure.sh,
so the key never appears on a command line or in the chat.

Usage:
  bugit_mcp.py failures <ip>
  bugit_mcp.py auth <ip> <tracker>
  bugit_mcp.py triage <ip> --tracker <t> [--logs a,b] [--keyword k] [--severity s] [--stage s] [--override JSON] <test>...
  bugit_mcp.py edit <ip> <draft_id> <JSON of fields to change>
  bugit_mcp.py attach <ip> <draft_id> [--files | --groups g1,g2 | --select a,b | --all | --none]
  bugit_mcp.py submit <ip> <draft_id> --approved
  bugit_mcp.py call <ip> <tool> [JSON arguments]

<test> is a label printed by `failures` or a bare test_id.
Needs only python3 >= 3.10 (standard library); speaks MCP over SSE or streamable HTTP.
Exit codes: 0 ok, 1 usage or config error, 2 tool error, 3 server too old, 4 not logged in.
"""

import argparse
import itertools
import json
import os
import re
import socket
import sys
import threading
import time
from collections.abc import Iterable, Iterator, Sequence
from http.client import HTTPConnection, HTTPSConnection
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ask_form import form_attach  # noqa: E402
from render_draft import render  # noqa: E402

LABEL_RE = re.compile(
    r"^\[(?P<plan>[^\]]*) @ (?P<at>[^\]]*)\] (?P<test>\S+) \((?P<sub>.+)\)$"
)
OPTION_RE = re.compile(r"^(?P<sub>\S+) \| (?P<test>\S+) \| (?P<sev>\S+)$")
POLL_SECONDS = 3
JOB_TIMEOUT = 20 * 60
CALL_TIMEOUT = 10 * 60
PROTOCOL_VERSION = "2025-06-18"


class ToolError(Exception):
    code = 2


class Usage(Exception):
    code = 1


def server_entry(ip: str) -> dict[str, Any]:
    path = server_config_path()
    try:
        servers = json.loads(path.read_text())["mcpServers"]
    except (OSError, ValueError, KeyError) as e:
        raise Usage(f"cannot read {path}: {e}; run configure.sh {ip} first") from None
    entry = servers.get(f"bugit-{ip}")
    if not entry or not entry.get("url"):
        raise Usage(f"bugit-{ip} is not in {path}; run configure.sh {ip} first")
    return entry


def state_path() -> Path:
    return server_config_path().with_name("bugit-drafts.json")


def server_config_path() -> Path:
    return Path(
        os.environ.get("COPILOT_MCP_CONFIG", Path.home() / ".copilot/mcp-config.json")
    )


def load_state() -> dict[str, Any]:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def save_state(draft_id: str, **fields: Any) -> None:
    """Remember per draft its logs job and whether the user chose the attachments"""
    state = load_state()
    state[draft_id] = {**state.get(draft_id, {}), **fields}
    state_path().write_text(json.dumps(state))


def label(row: dict[str, Any]) -> str:
    plan = str(row.get("testplan_id") or "").split("::")[-1]
    at = str(row.get("failed_at") or "")[:19]
    return (
        f"[{plan} @ {at}] {str(row['test_id']).split('::')[-1]} ({row['submission_id']})"
    )


def flatten_groups(groups: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flat per-test rows from list_failed_tests()'s submission-grouped response."""
    return [
        {
            "submission_id": g["submission_id"],
            "testplan_id": g["testplan_id"],
            "modified": g["modified"],
            "test_id": t["test_id"],
            "outcome": t["outcome"],
            "failed_at": t["failed_at"],
        }
        for g in groups
        for t in g["tests"]
    ]


def parse_tests(
    tests: Sequence[str], rows: Sequence[dict[str, Any]]
) -> list[tuple[str, str]]:
    """(full test_id, submission_id) for each label or bare test_id, newest run first"""
    out: list[tuple[str, str]] = []
    for t in tests:
        t = t.strip()
        m = LABEL_RE.match(t) or OPTION_RE.match(t)
        want_test, want_sub = (m["test"], m["sub"]) if m else (t, None)
        match = next(
            (
                r
                for r in rows
                if want_test in (r["test_id"], str(r["test_id"]).split("::")[-1])
                and want_sub in (None, r["submission_id"])
            ),
            None,
        )
        if match is None:
            raise Usage(f"{t!r} is not a failed test; run `failures` for the list")
        out.append((match["test_id"], match["submission_id"]))
    return list(dict.fromkeys(out))


def iter_sse(lines: Iterable[bytes]) -> Iterator[tuple[str, str]]:
    """(event, data) pairs of a text/event-stream; comments and pings are skipped"""
    event, data = "message", []
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = "message", []
        elif line.startswith(":"):
            continue
        else:
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)
    if data:
        yield event, "\n".join(data)


class Rpc:
    """Minimal JSON-RPC client for MCP; subclasses only move messages"""

    def __init__(self, url: str, headers: dict[str, str]) -> None:
        self.url = url
        self.headers = headers
        self._ids = itertools.count(1)

    def _post(self, url: str, msg: dict[str, Any], accept: str) -> Any:
        req = Request(
            url,
            data=json.dumps(msg).encode(),
            method="POST",
            headers={
                **self.headers,
                "Content-Type": "application/json",
                "Accept": accept,
            },
        )
        try:
            return urlopen(req, timeout=CALL_TIMEOUT)
        except HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise ToolError(f"HTTP {e.code} from {url}: {body or e.reason}") from None

    def exchange(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError

    def request(self, method: str, params: dict[str, Any]) -> Any:
        msg_id = next(self._ids)
        reply = self.exchange(
            {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
        )
        if reply is None:
            raise ToolError(f"{method}: no reply from the server")
        if "error" in reply:
            raise ToolError(f"{method}: {reply['error'].get('message', reply['error'])}")
        return reply.get("result")

    def notify(self, method: str) -> None:
        self.exchange({"jsonrpc": "2.0", "method": method})

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                # no capabilities: the server must not ask for approval via elicitation
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "bugit-skill", "version": "1"},
            },
        )
        self.notify("notifications/initialized")

    def close(self) -> None:
        pass


class SseRpc(Rpc):
    """GET the event stream, POST requests to its endpoint, replies come on the stream"""

    def __init__(self, url: str, headers: dict[str, str]) -> None:
        super().__init__(url, headers)
        u = urlsplit(url)
        conn_cls = HTTPSConnection if u.scheme == "https" else HTTPConnection
        # a raw connection, so close() can shut the socket down instead of waiting for a ping
        self._conn = conn_cls(u.netloc, timeout=JOB_TIMEOUT)
        self._conn.request(
            "GET",
            u.path + (f"?{u.query}" if u.query else ""),
            headers={**headers, "Accept": "text/event-stream"},
        )
        self._stream = self._conn.getresponse()
        if self._stream.status != 200:
            raise ToolError(
                f"HTTP {self._stream.status} from {url}: {self._stream.reason}"
            )
        self._cond = threading.Condition()
        self._replies: dict[Any, dict[str, Any]] = {}
        self._endpoint: str | None = None
        self._error: BaseException | None = None
        threading.Thread(target=self._read, daemon=True).start()
        with self._cond:
            if not self._cond.wait_for(
                lambda: self._endpoint or self._error, timeout=30
            ):
                raise ToolError(f"{url} sent no endpoint event")
        if self._endpoint is None:
            raise ToolError(f"{url}: {self._error}")

    def _read(self) -> None:
        try:
            for event, data in iter_sse(self._stream):
                with self._cond:
                    if event == "endpoint":
                        self._endpoint = urljoin(self.url, data)
                    elif event == "message":
                        self._handle(json.loads(data))
                    self._cond.notify_all()
            err: BaseException = ConnectionError("the server closed the event stream")
        except Exception as e:
            err = e
        with self._cond:
            self._error = err
            self._cond.notify_all()

    def _handle(self, msg: dict[str, Any]) -> None:
        if "method" not in msg:
            self._replies[msg.get("id")] = msg
        elif "id" in msg and self._endpoint:
            # a request from the server (ping, ...): this client supports none of them
            reply = {"jsonrpc": "2.0", "id": msg["id"]}
            if msg["method"] == "ping":
                reply["result"] = {}
            else:
                reply["error"] = {"code": -32601, "message": "not supported"}
            threading.Thread(
                target=lambda: self._post(self._endpoint or "", reply, "*/*").close(),
                daemon=True,
            ).start()

    def exchange(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        assert self._endpoint
        self._post(self._endpoint, msg, "*/*").close()
        if "id" not in msg:
            return None
        with self._cond:
            if not self._cond.wait_for(
                lambda: msg["id"] in self._replies or self._error, timeout=CALL_TIMEOUT
            ):
                raise ToolError(f"{msg['method']}: no reply after {CALL_TIMEOUT}s")
            if msg["id"] not in self._replies:
                raise ToolError(f"{msg['method']}: {self._error}")
            return self._replies.pop(msg["id"])

    def close(self) -> None:
        try:
            if self._conn.sock:
                self._conn.sock.shutdown(socket.SHUT_RDWR)
            self._conn.close()
        except OSError:
            pass


class HttpRpc(Rpc):
    """Streamable HTTP: every POST answers with JSON or a short event stream"""

    session_id: str | None = None

    def exchange(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        if self.session_id:
            self.headers["Mcp-Session-Id"] = self.session_id
        with self._post(self.url, msg, "application/json, text/event-stream") as resp:
            self.session_id = resp.headers.get("Mcp-Session-Id") or self.session_id
            if "id" not in msg or resp.status == 202:
                return None
            if "text/event-stream" in resp.headers.get("Content-Type", ""):
                for _, data in iter_sse(resp):
                    reply = json.loads(data)
                    if reply.get("id") == msg["id"] and "method" not in reply:
                        return reply
                return None
            return json.loads(resp.read() or b"null")

    def initialize(self) -> None:
        super().initialize()
        self.headers["Mcp-Protocol-Version"] = PROTOCOL_VERSION


class Client:
    def __init__(self, rpc: Rpc) -> None:
        self._rpc = rpc

    def call(self, tool: str, **args: Any) -> Any:
        result = self._rpc.request(
            "tools/call",
            {
                "name": tool,
                "arguments": {k: v for k, v in args.items() if v is not None},
            },
        )
        text = "\n".join(c.get("text", "") for c in result.get("content") or [])
        if result.get("isError"):
            if "Unknown tool" in text:
                err = ToolError(
                    f"{tool}: unknown tool; the bugit snap on the DUT may be too old"
                )
                err.code = 3
                raise err
            raise ToolError(f"{tool}: {text}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        if structured is not None:
            return structured
        try:
            return json.loads(text)
        except ValueError:
            return text


def with_client(ip: str, fn: Any) -> Any:
    entry = server_entry(ip)
    headers = {str(k): str(v) for k, v in (entry.get("headers") or {}).items()}
    rpc_cls = SseRpc if entry.get("type") == "sse" else HttpRpc
    rpc = rpc_cls(entry["url"], headers)
    try:
        rpc.initialize()
        return fn(Client(rpc))
    finally:
        rpc.close()


def wait_job(c: Client, job_id: str) -> None:
    deadline = time.monotonic() + JOB_TIMEOUT
    while True:
        st = c.call("check_job_status", job_id=job_id)
        if st["status"] == "completed":
            return
        if st["status"] == "failed":
            raise ToolError(f"log collection failed: {st.get('error')}")
        if time.monotonic() > deadline:
            raise ToolError(
                f"log collection {job_id} still running after {JOB_TIMEOUT}s"
            )
        time.sleep(POLL_SECONDS)


def start_job(c: Client, test: str, sub: str, logs: list[str] | None) -> str:
    deadline = time.monotonic() + JOB_TIMEOUT
    while True:
        try:
            return c.call(
                "start_log_collection",
                context_test_case=test,
                submission_id=sub,
                log_names=logs,
            )
        except ToolError as e:
            if "already running" not in str(e) or time.monotonic() > deadline:
                raise
            print("another log collection is running; waiting...", file=sys.stderr)
            time.sleep(POLL_SECONDS)


def show_draft(draft_id: str, preview: str) -> None:
    print(render(draft_id, preview))


def option(row: dict[str, Any]) -> str:
    """The `submission | test case name | suggested severity` line users pick from"""
    return (
        f"{row['submission_id']} | {str(row['test_id']).split('::')[-1]} | "
        f"{row.get('suggested_severity') or 'n/a'}"
    )


def cmd_failures(c: Client, a: argparse.Namespace) -> int:
    groups = c.call("list_failed_tests")
    out = [
        {
            "submission_id": g["submission_id"],
            "testplan_id": g["testplan_id"],
            "modified": g["modified"],
            "tests": [label({**g, **t}) for t in g["tests"]],
            "choices": [option({**g, **t}) for t in g["tests"]],
            "severity_hints": {
                label({**g, **t}): {
                    "cert_status": t.get("cert_status"),
                    "suggested_severity": t.get("suggested_severity"),
                }
                for t in g["tests"]
            },
        }
        for g in groups
    ]
    print(json.dumps(out, indent=1))
    return 0


def cmd_auth(c: Client, a: argparse.Namespace) -> int:
    st = c.call("check_auth_status", service=a.tracker)
    if st.get("authenticated"):
        print(json.dumps(st))
        return 0
    detail = {"authenticated": False, "detail": st.get("detail")}
    if a.tracker == "launchpad":
        detail["login_url"] = c.call("get_login_url", service=a.tracker)
    print(json.dumps(detail, indent=1))
    return 4


def cmd_triage(c: Client, a: argparse.Namespace) -> int:
    overrides = json.loads(a.override) if a.override else {}
    if not isinstance(overrides, dict):
        raise Usage("--override must be a JSON object")
    if getattr(a, "severity", None):
        overrides["severity"] = a.severity
    if getattr(a, "stage", None):
        overrides["issue_file_time"] = a.stage
    auth = c.call("check_auth_status", service=a.tracker)
    if not auth.get("authenticated"):
        print(json.dumps({"authenticated": False, "detail": auth.get("detail")}))
        return 4
    tests = parse_tests(a.tests, flatten_groups(c.call("list_failed_tests")))
    subs = {s for _, s in tests}
    if len(subs) > 1:
        print(
            f"WARNING: selected tests span {len(subs)} different submissions; "
            "filing one bug anyway",
            file=sys.stderr,
        )
    first_test, first_sub = tests[0]
    logs = [n for n in (a.logs or "").split(",") if n.strip()] or None
    print(f"collecting logs for {first_test} ({first_sub})...", file=sys.stderr)
    job_id = start_job(c, first_test, first_sub, logs)
    wait_job(c, job_id)
    out = c.call(
        "triage_failures",
        job_id=job_id,
        test_ids=[t for t, _ in tests],
        tracker=a.tracker,
        keyword=a.keyword,
        submissions={t: s for t, s in tests},
        **overrides,
    )
    print("=== errors (raw, for root-cause analysis) ===")
    print(json.dumps(out["errors"], indent=1))
    print("=== logs (raw stdout/stderr per test, for root-cause analysis) ===")
    print(json.dumps(out["logs"], indent=1))
    save_state(out["draft_id"], job_id=job_id, attach_chosen=False)
    show_draft(out["draft_id"], out["preview"])
    for w in out["warnings"]:
        print(f"WARNING: {w}")
    print(f"\njob_id: {job_id}\ndraft_id: {out['draft_id']}")
    return 0


def cmd_edit(c: Client, a: argparse.Namespace) -> int:
    changes = json.loads(a.changes)
    if not isinstance(changes, dict) or not changes:
        raise Usage("changes must be a non-empty JSON object of draft fields")
    c.call("update_draft", draft_id=a.draft_id, **changes)
    if "attach_logs" in changes:
        save_state(a.draft_id, attach_chosen=True)
    show_draft(a.draft_id, c.call("get_draft", draft_id=a.draft_id))
    print(f"\ndraft_id: {a.draft_id}")
    return 0


def group_logs(files: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        groups.setdefault(f.get("collector") or "other", []).append(f)
    return groups


def print_log_summary(files: Sequence[dict[str, Any]]) -> None:
    """One line per collector group, then the ready ask_user form (groups only)"""
    groups = group_logs(files)
    print(f"=== {len(files)} files in {len(groups)} log groups ===")
    for key, items in groups.items():
        title = items[0].get("collector_description") or key
        size = sum(int(f.get("size") or 0) for f in items)
        print(f"- {key}: {title} ({len(items)} files, {size} bytes)")
    print("\n=== requestedSchema for ask_user (pass as-is) ===")
    print(json.dumps(form_attach(list(groups))["requestedSchema"], indent=2))


def print_log_list(files: Sequence[dict[str, Any]]) -> None:
    """Every collected file, grouped by collector"""
    groups = group_logs(files)
    print(f"=== {len(files)} files in {len(groups)} collector groups ===")
    for key, items in groups.items():
        title = items[0].get("collector_description") or key
        print(f"\n## {key}: {title} ({len(items)} files)")
        for f in items:
            print(f"  {f['name']}  ({f['size']} bytes)")


def split_csv(value: str) -> list[str]:
    return [n.strip() for n in value.split(",") if n.strip()]


def cmd_attach(c: Client, a: argparse.Namespace) -> int:
    job_id = load_state().get(a.draft_id, {}).get("job_id")
    if not job_id:
        raise Usage(f"no logs job is known for draft {a.draft_id}; run `triage` first")
    files = c.call("list_collected_logs", job_id=job_id)
    names = [f["name"] for f in files]
    if a.all:
        chosen = names
    elif a.none:
        chosen = []
    elif a.groups:
        groups = group_logs(files)
        wanted = split_csv(a.groups)
        unknown = [g for g in wanted if g not in groups]
        if unknown:
            raise Usage(f"unknown log group(s) {unknown}; choose from {list(groups)}")
        chosen = [f["name"] for g in wanted for f in groups[g]]
    elif a.select:
        chosen = split_csv(a.select)
        unknown = [n for n in chosen if n not in names]
        if unknown:
            shown = ", ".join(unknown[:5]) + (" ..." if len(unknown) > 5 else "")
            raise Usage(
                f"{len(unknown)} unknown log file(s): {shown}. To attach whole groups "
                f"use --groups {','.join(group_logs(files))}; "
                "run with --files to see every file name"
            )
    elif a.files:
        print_log_list(files)
        return 0
    else:
        print_log_summary(files)
        return 0
    c.call("update_draft", draft_id=a.draft_id, attach_logs=chosen)
    save_state(a.draft_id, attach_chosen=True)
    show_draft(a.draft_id, c.call("get_draft", draft_id=a.draft_id))
    print(f"\nattached: {len(chosen)} file(s)\ndraft_id: {a.draft_id}")
    return 0


def cmd_submit(c: Client, a: argparse.Namespace) -> int:
    if not a.approved:
        raise Usage(
            "refusing to submit without --approved (the user must approve the draft)"
        )
    print(json.dumps(c.call("confirm_submission", draft_id=a.draft_id), indent=1))
    return 0


def cmd_call(c: Client, a: argparse.Namespace) -> int:
    args = json.loads(a.args) if a.args else {}
    if a.tool == "confirm_submission":
        raise Usage("use `submit <ip> <draft_id> --approved` to file a bug")
    out = c.call(a.tool, **args)
    print(out if isinstance(out, str) else json.dumps(out, indent=1))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("failures")
    s.add_argument("ip")
    s.set_defaults(fn=cmd_failures)
    s = sub.add_parser("auth")
    s.add_argument("ip")
    s.add_argument("tracker", choices=["local", "jira", "launchpad"])
    s.set_defaults(fn=cmd_auth)
    s = sub.add_parser("triage")
    s.add_argument("ip")
    s.add_argument("--tracker", required=True, choices=["local", "jira", "launchpad"])
    s.add_argument(
        "--logs", help="extra log collectors, comma separated (e.g. sosreport)"
    )
    s.add_argument("--keyword", default="error")
    s.add_argument(
        "--override", help="JSON: title, summary, severity, ... to replace defaults"
    )
    s.add_argument(
        "--severity",
        choices=("highest", "high", "medium", "low", "lowest"),
        help="the severity the user chose",
    )
    s.add_argument(
        "--stage",
        choices=("immediate", "after_reboot", "later"),
        help="when the issue was filed (issue_file_time) the user chose",
    )
    s.add_argument("tests", nargs="+")
    s.set_defaults(fn=cmd_triage)
    s = sub.add_parser("edit")
    s.add_argument("ip")
    s.add_argument("draft_id")
    s.add_argument("changes")
    s.set_defaults(fn=cmd_edit)
    s = sub.add_parser("attach")
    s.add_argument("ip")
    s.add_argument("draft_id")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--files", action="store_true", help="list every file name")
    g.add_argument("--groups", help="comma separated log groups to upload whole")
    g.add_argument("--select", help="comma separated file names to upload")
    g.add_argument("--all", action="store_true")
    g.add_argument("--none", action="store_true")
    s.set_defaults(fn=cmd_attach)
    s = sub.add_parser("submit")
    s.add_argument("ip")
    s.add_argument("draft_id")
    s.add_argument("--approved", action="store_true")
    s.set_defaults(fn=cmd_submit)
    s = sub.add_parser("call")
    s.add_argument("ip")
    s.add_argument("tool")
    s.add_argument("args", nargs="?")
    s.set_defaults(fn=cmd_call)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    a = parser().parse_args(argv)
    try:
        if a.cmd == "submit" and not a.approved:
            raise Usage(
                "refusing to submit without --approved (the user must approve the draft)"
            )
        if a.cmd == "submit" and not load_state().get(a.draft_id, {}).get(
            "attach_chosen", True
        ):
            raise Usage(
                f"choose the logs first: `attach {a.ip} {a.draft_id}`, then "
                "`--groups a,b`, `--select a,b`, `--all` or `--none`"
            )
        return with_client(a.ip, lambda c: a.fn(c, a))
    except (Usage, ToolError) as e:
        print(f"error: {e}", file=sys.stderr)
        return e.code
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error: cannot talk to bugit-{a.ip}: {e!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
