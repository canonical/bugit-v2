from pathlib import Path
from types import SimpleNamespace

import pytest

from bugit_v2.mcp_server.collector import make_collector
from bugit_v2.mcp_server.jobs import CollectContext


class FakeCheckbox:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_job_output(self, submission_id: str, test_id: str):
        self.calls.append((submission_id, test_id))
        if test_id == "bad/test":
            return {"stdout": "S", "stderr": "E", "comments": ""}
        return None

    def describe(self, submission_id: str):
        return {"kind": "session", "failed_at": "2026-01-01T00:00:00+00:00"}

    def collector_input(self, submission_id: str):
        return SimpleNamespace(checkbox_session=None, checkbox_submission=None, job_id=None)


def make_fake(name: str, fail: bool = False, calls: list | None = None):
    async def collect(target: Path, inp, on_output):
        if calls is not None:
            calls.append(name)
        if fail:
            raise RuntimeError(f"{name} exploded")
        (target / f"{name}.log").write_text(name)
        if on_output:
            on_output(f"{name} line")
        return None

    return SimpleNamespace(name=name, collect=collect)


async def test_runs_all_collectors_and_writes_context(tmp_path: Path):
    calls: list[str] = []
    cols = {n: make_fake(n, calls=calls) for n in ("a", "b")}
    msgs: list[str] = []
    run = make_collector(FakeCheckbox(), cols, ("a", "b"))
    await run(tmp_path, CollectContext("bad/test", "session:s"), msgs.append)
    assert sorted(calls) == ["a", "b"]
    assert (tmp_path / "a.log").exists() and (tmp_path / "b.log").exists()
    ctx = (tmp_path / "context.txt").read_text()
    assert "bad/test" in ctx and "session:s" in ctx and "2026-01-01" in ctx
    assert (tmp_path / "job_stdout.txt").read_text() == "S"
    assert (tmp_path / "job_stderr.txt").read_text() == "E"
    assert any("a line" in m for m in msgs)


async def test_one_failure_does_not_stop_others_and_is_recorded(tmp_path: Path):
    cols = {"a": make_fake("a", fail=True), "b": make_fake("b")}
    msgs: list[str] = []
    await make_collector(FakeCheckbox(), cols, ("a", "b"))(tmp_path, CollectContext(), msgs.append)
    assert (tmp_path / "b.log").exists()
    assert "a exploded" in (tmp_path / "collector-errors.txt").read_text()
    assert any("a" in m and "failed" in m.lower() for m in msgs)


async def test_all_failing_fails_the_job(tmp_path: Path):
    cols = {"a": make_fake("a", fail=True)}
    with pytest.raises(RuntimeError, match="No logs"):
        await make_collector(FakeCheckbox(), cols, ("a",))(tmp_path, CollectContext(), lambda m: None)


async def test_unknown_names_are_ignored(tmp_path: Path):
    cols = {"a": make_fake("a")}
    await make_collector(FakeCheckbox(), cols, ("a", "missing"))(tmp_path, CollectContext(), lambda m: None)
    assert (tmp_path / "a.log").exists()


async def test_no_submission_means_no_job_output_lookup(tmp_path: Path):
    cb = FakeCheckbox()
    await make_collector(cb, {"a": make_fake("a")}, ("a",))(tmp_path, CollectContext("bad/test", None), lambda m: None)
    assert cb.calls == []


async def test_log_names_add_to_defaults_and_all(tmp_path: Path):
    calls: list[str] = []
    cols = {n: make_fake(n, calls=calls) for n in ("a", "b", "c")}
    run = make_collector(FakeCheckbox(), cols, ("a",))
    await run(tmp_path, CollectContext(log_names=("b",)), lambda m: None)
    assert calls == ["a", "b"]
    calls.clear()
    await run(tmp_path, CollectContext(log_names=("all",)), lambda m: None)
    assert calls == ["a", "b", "c"]


def test_describe_collectors_hides_non_mcp_ones():
    from bugit_v2.mcp_server.collector import describe_collectors

    cols = {
        "sosreport": SimpleNamespace(display_name="SOS", hidden=False),
        "dmesg": SimpleNamespace(display_name="dmesg", hidden=False),
        "job-outputs": SimpleNamespace(display_name="x", hidden=False),
        "long-job-outputs": SimpleNamespace(display_name="x", hidden=True),
    }
    got = {d["name"]: d["default"] for d in describe_collectors(cols)}
    assert got == {"sosreport": False, "dmesg": True}
