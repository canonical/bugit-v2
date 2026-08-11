import asyncio
import asyncio.subprocess as asp
import logging
import subprocess as sp
from collections.abc import Callable, MutableMapping, Sequence
from pathlib import Path
from subprocess import CalledProcessError
from typing import IO, Literal

import psutil

logger = logging.getLogger(__name__)


async def _stream_lines(
    stream: asyncio.StreamReader,
    on_line: Callable[[str], None] | None,
    dest_file: IO[str] | None,
    capture: bool = True,
) -> bytes:
    """Reads a subprocess stream line by line as it's produced.

    Each decoded line is forwarded to ``on_line`` (if given) so callers can
    show live output (e.g. in a UI) instead of waiting for the whole process
    to finish. Raw bytes are also written to ``dest_file`` (if given) to
    preserve the existing "write stdout straight to a file" behavior.

    :param capture: if True (default), every line read is kept in memory and
        returned at the end, mirroring the buffering `communicate()` does.
        Callers that don't need the full captured output (e.g. `asp_check_call`
        when it's just going to discard the return value) should pass False
        to avoid holding potentially huge command output (e.g. a week of
        `journalctl` output) in memory for no reason, since it's often
        already being written line-by-line to `dest_file` anyway.
    :return: all the bytes read from the stream, concatenated. Empty bytes if
        `capture` is False.
    """
    chunks: list[bytes] = []
    while True:
        line = await stream.readline()
        if not line:
            break
        if capture:
            chunks.append(line)
        if dest_file is not None:
            dest_file.write(line.decode(errors="replace"))
            dest_file.flush()
        if on_line is not None:
            on_line(line.decode(errors="replace").rstrip("\n"))

    return b"".join(chunks) if capture else b""


async def asp_check_output(
    cmd: Sequence[str],
    timeout: int | None = None,
    env: MutableMapping[str, str] | None = None,
    cwd: str | Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> str:
    """Async version of subprocess.check_output

    :param cmd: command array like the sync version
    :param timeout: timeout in seconds. Wait forever if None
    :param env: env override
    :param cwd: override current working directory
    :param on_line: if given, stdout is streamed and this callback is invoked
        with each decoded line as soon as it's produced, instead of buffering
        everything until the process exits. Useful for showing live progress
        of long running commands in a UI.
    :raises CalledProcessError: when the process doesn't return 0
    :return: stdout as a string if successful
    """
    if env:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=asp.PIPE, stderr=asp.PIPE, env=env, cwd=cwd
        )
    else:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=asp.PIPE, stderr=asp.PIPE, cwd=cwd
        )

    assert proc.stdout is not None
    assert proc.stderr is not None
    # bind to locals so type checkers can narrow away the `| None` from
    # `proc.stdout`/`proc.stderr` inside the nested closure below
    stdout_stream = proc.stdout
    stderr_stream = proc.stderr

    async def _run() -> tuple[bytes, bytes]:
        if on_line is not None:
            # stream stdout line-by-line while draining stderr concurrently
            # (stderr must still be drained or the process can deadlock once
            # its pipe buffer fills up)
            stdout_bytes, stderr_bytes = await asyncio.gather(
                _stream_lines(stdout_stream, on_line, None),
                stderr_stream.read(),
            )
            return stdout_bytes, stderr_bytes
        else:
            return await proc.communicate()

    try:
        if timeout:
            stdout, stderr = await asyncio.wait_for(_run(), timeout)
        else:
            stdout, stderr = await _run()
    except TimeoutError as e:
        if proc.returncode is None:
            logger.error(f"Force killing process {proc.pid}, cmd='{cmd}' (timed out)")
            recursive_kill(proc.pid)
        raise e
    except asyncio.CancelledError as e:
        if proc.returncode is None:
            logger.warning(f"Force killing process {proc.pid}, cmd='{cmd}' (cancelled)")
            recursive_kill(proc.pid)
        raise e

    assert proc.returncode is not None
    if proc.returncode != 0:
        raise CalledProcessError(proc.returncode, cmd, stdout, stderr)

    return stdout.decode()


async def asp_check_call(
    cmd: list[str],
    timeout: int | None = None,
    env: dict[str, str] | None = None,
    stdout: IO[str] | int = asp.DEVNULL,
    stderr: IO[str] | int = asp.DEVNULL,
    cwd: str | Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> Literal[0]:
    """Async version of sp.check_call

    :param cmd: command array like the sync version
    :param timeout: timeout in seconds. Wait forever if None
    :param env: env override
    :param stdout: where to put stdout, defaults to asp.DEVNULL. Either an
        open text-mode file object or a file descriptor/one of the
        subprocess.PIPE/DEVNULL/STDOUT ints. If it's a file object and
        `on_line` is given, lines are written to it as they're streamed
        instead of all at once at the end
    :param stderr: where to put stderr, defaults to asp.DEVNULL
    :param cwd: override current working directory
    :param on_line: if given, stdout is streamed line-by-line and this
        callback is invoked with each decoded line as soon as it's produced.
        Useful for showing live progress of long running commands in a UI.
    :raises CalledProcessError: when return code is not 0
    :return: 0
    """
    # when streaming, we need our own pipe to read from as the lines are
    # produced; `dest_file` (if any) is written to manually inside the
    # streaming loop instead of being handed directly to the subprocess
    streaming = on_line is not None
    dest_file: IO[str] | None = (
        stdout if streaming and not isinstance(stdout, int) else None
    )
    stdout_arg = asp.PIPE if streaming else stdout

    if env:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=stdout_arg, stderr=stderr, env=env, cwd=cwd
        )
    else:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=stdout_arg, stderr=stderr, cwd=cwd
        )

    async def _run() -> int:
        if streaming:
            assert proc.stdout is not None
            # capture=False: asp_check_call doesn't return captured stdout,
            # so don't hold potentially huge output in memory for nothing
            await _stream_lines(proc.stdout, on_line, dest_file, capture=False)
        return await proc.wait()

    try:
        if timeout:
            rc = await asyncio.wait_for(_run(), timeout)
        else:
            rc = await _run()
    except TimeoutError as e:
        if proc.returncode is None:
            logger.error(f"Force killing process {proc.pid}, cmd='{cmd}' (timed out)")
            recursive_kill(proc.pid)
        raise e
    except asyncio.CancelledError as e:
        if proc.returncode is None:
            logger.warning(f"Force killing process {proc.pid}, cmd='{cmd}' (cancelled)")
            recursive_kill(proc.pid)
        raise e

    if rc != 0:
        raise CalledProcessError(rc, cmd)

    return rc


async def asp_run(
    cmd: Sequence[str],
    timeout: int | None = None,
    env: MutableMapping[str, str] | None = None,
    cwd: str | Path | None = None,
) -> sp.CompletedProcess[str]:
    """Async version of subprocess.run

    :param cmd: command array like the sync version
    :param timeout: timeout in seconds. Wait forever if None
    :param env: env override
    :param cwd: override current working directory
    :return: stdout as a string if successful
    """
    if env:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=asp.PIPE, stderr=asp.PIPE, env=env, cwd=cwd
        )
    else:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=asp.PIPE, stderr=asp.PIPE, cwd=cwd
        )

    try:
        if timeout:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        else:
            stdout, stderr = await proc.communicate()
    except TimeoutError as e:
        if proc.returncode is None:
            logger.error(f"Force killing process {proc.pid}, cmd='{cmd}' (timed out)")
            recursive_kill(proc.pid)
        raise e
    except asyncio.CancelledError as e:
        if proc.returncode is None:
            logger.warning(f"Force killing process {proc.pid}, cmd='{cmd}' (cancelled)")
            recursive_kill(proc.pid)
        raise e

    assert proc.returncode is not None

    return sp.CompletedProcess[str](
        cmd, proc.returncode, stdout.decode(), stderr.decode()
    )


def recursive_kill(pid: int):
    try:
        parent = psutil.Process(pid)

        for child in parent.children(recursive=True):
            child.kill()

        parent.kill()
    except psutil.NoSuchProcess:
        logger.warning(f"No such process: {pid}")
    except PermissionError as e:
        logger.warning(f"Permission error when killing {pid}: {e}")
