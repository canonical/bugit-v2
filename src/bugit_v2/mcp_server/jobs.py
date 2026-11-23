"""In-memory manager for asynchronous log-collection jobs"""

import asyncio
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bugit_v2.mcp_server.logstore import expand_archives

MAX_MESSAGES = 30


@dataclass(slots=True, frozen=True)
class CollectContext:
    context_test_case: str | None = None
    submission_id: str | None = None
    # extra collectors on top of the defaults; ("all",) selects every one
    log_names: tuple[str, ...] = ()


type Progress = Callable[[str], None]
type Collector = Callable[[Path, CollectContext, Progress], Awaitable[None]]


@dataclass(slots=True)
class _Job:
    job_id: str
    directory: Path
    context: CollectContext
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: str = "running"
    error: str | None = None
    messages: list[str] = field(default_factory=list[str])
    task: "asyncio.Task[None] | None" = None


class JobManager:
    def __init__(
        self,
        base_dir: Path,
        collector: Collector,
        *,
        redact: Callable[[str], str] = lambda s: s,
        max_running: int = 1,
        max_jobs: int = 10,
    ) -> None:
        self._base = base_dir
        self._collector = collector
        self._redact = redact
        self._max_running = max_running
        self._max_jobs = max_jobs
        self._jobs: dict[str, _Job] = {}

    def _get(self, job_id: str) -> _Job:
        job = self._jobs.get(job_id) if isinstance(job_id, str) else None
        if job is None:
            raise ValueError(f"Unknown job_id: {job_id!r}")
        return job

    def start(
        self,
        context_test_case: str | None,
        submission_id: str | None = None,
        log_names: Sequence[str] = (),
    ) -> str:
        if sum(j.status == "running" for j in self._jobs.values()) >= self._max_running:
            raise RuntimeError(
                "A log collection job is already running. "
                "Call check_job_status() on it and wait for 'completed'."
            )
        self._evict()
        job_id = uuid.uuid4().hex
        directory = self._base / job_id
        directory.mkdir(parents=True)
        job = _Job(
            job_id,
            directory,
            CollectContext(context_test_case, submission_id, tuple(log_names)),
        )
        self._jobs[job_id] = job
        job.task = asyncio.create_task(self._run(job))
        return job_id

    async def _run(self, job: _Job) -> None:
        def progress(msg: str) -> None:
            job.messages.append(self._redact(msg))
            del job.messages[:-MAX_MESSAGES]

        try:
            await self._collector(job.directory, job.context, progress)
            await asyncio.to_thread(expand_archives, job.directory)
            job.status = "completed"
        except Exception as e:
            job.status = "failed"
            job.error = self._redact(f"{type(e).__name__}: {e}")
        finally:
            job.finished_at = time.time()

    def _evict(self) -> None:
        finished = [j for j in self._jobs.values() if j.status != "running"]
        finished.sort(key=lambda j: j.started_at)
        # make room for the job about to be created
        while len(self._jobs) >= self._max_jobs and finished:
            old = finished.pop(0)
            del self._jobs[old.job_id]
            shutil.rmtree(old.directory, ignore_errors=True)

    def status(self, job_id: str) -> dict[str, Any]:
        job = self._get(job_id)
        return {
            "job_id": job.job_id,
            "status": job.status,
            "error": job.error,
            "messages": list(job.messages),
            "started_at": job.started_at,
            "finished_at": job.finished_at,
        }

    def context(self, job_id: str) -> CollectContext:
        return self._get(job_id).context

    def has_running(self) -> bool:
        return any(j.status == "running" for j in self._jobs.values())

    def job_dir(self, job_id: str) -> Path:
        return self._get(job_id).directory
