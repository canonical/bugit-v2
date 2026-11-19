import asyncio
from pathlib import Path

import pytest

from bugit_v2.mcp_server.jobs import CollectContext, JobManager


async def wait_done(m: JobManager, job_id: str, timeout: float = 2.0) -> dict:
    async with asyncio.timeout(timeout):
        while (st := m.status(job_id))["status"] == "running":
            await asyncio.sleep(0.01)
    return st


async def ok_collector(target: Path, ctx: CollectContext, progress) -> None:
    progress("collecting dmesg")
    (target / "dmesg.log").write_text(f"ctx={ctx.context_test_case}\n")


async def test_job_lifecycle_completed(tmp_path: Path):
    m = JobManager(tmp_path, ok_collector)
    job_id = m.start("com.canonical.certification::wifi/connect")
    st = m.status(job_id)
    assert st["job_id"] == job_id
    assert st["status"] in ("running", "completed")

    st = await wait_done(m, job_id)
    assert st["status"] == "completed"
    assert st["error"] is None
    assert "collecting dmesg" in st["messages"]
    d = m.job_dir(job_id)
    assert (d / "dmesg.log").read_text() == "ctx=com.canonical.certification::wifi/connect\n"
    assert d.is_relative_to(tmp_path)


async def test_job_failure_is_reported_and_redacted(tmp_path: Path):
    async def boom(target, ctx, progress):
        raise RuntimeError("cannot read /home/alice/x from 10.0.0.9")

    m = JobManager(tmp_path, boom, redact=lambda s: s.replace("alice", "<USER>"))
    job_id = m.start(None)
    st = await wait_done(m, job_id)
    assert st["status"] == "failed"
    assert "alice" not in st["error"] and "<USER>" in st["error"]


async def test_archives_are_expanded_after_collection(tmp_path: Path):
    import gzip

    async def gz(target, ctx, progress):
        with gzip.open(target / "x.log.gz", "wb") as g:
            g.write(b"hi")

    m = JobManager(tmp_path, gz)
    job_id = m.start(None)
    await wait_done(m, job_id)
    assert (m.job_dir(job_id) / "x.log").read_bytes() == b"hi"


async def test_unknown_or_malicious_job_id(tmp_path: Path):
    m = JobManager(tmp_path, ok_collector)
    for bad in ("nope", "../etc", "", "/tmp"):
        with pytest.raises(ValueError):
            m.status(bad)
        with pytest.raises(ValueError):
            m.job_dir(bad)


async def test_concurrency_cap(tmp_path: Path):
    gate = asyncio.Event()

    async def slow(target, ctx, progress):
        await gate.wait()

    m = JobManager(tmp_path, slow, max_running=1)
    first = m.start(None)
    with pytest.raises(RuntimeError, match="already running"):
        m.start(None)
    gate.set()
    await wait_done(m, first)
    second = m.start(None)
    await wait_done(m, second)


async def test_old_finished_jobs_are_evicted(tmp_path: Path):
    m = JobManager(tmp_path, ok_collector, max_jobs=2)
    ids = []
    for _ in range(3):
        j = m.start(None)
        await wait_done(m, j)
        ids.append(j)
    with pytest.raises(ValueError):
        m.status(ids[0])
    assert not (tmp_path / ids[0]).exists()
    assert m.status(ids[2])["status"] == "completed"


async def test_submission_ref_reaches_collector(tmp_path: Path):
    seen = {}

    async def spy(target, ctx, progress):
        seen["sub"] = ctx.submission_id

    m = JobManager(tmp_path, spy)
    await wait_done(m, m.start("t", submission_id="sess-1"))
    assert seen["sub"] == "sess-1"
