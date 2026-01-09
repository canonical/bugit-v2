import asyncio
import asyncio.subprocess as asp
import logging
import subprocess as sp
from collections.abc import MutableMapping, Sequence
from subprocess import CalledProcessError
from typing import IO, AnyStr, Literal, cast

import psutil

logger = logging.getLogger(__name__)


async def asp_check_output(
    cmd: Sequence[str],
    timeout: int | None = None,
    env: MutableMapping[str, str] | None = None,
) -> str:
    """Async version of subprocess.check_output

    :param cmd: command array like the sync version
    :param timeout: timeout in seconds. Wait forever if None
    :param env: env override
    :raises CalledProcessError: when the process doesn't return 0
    :return: stdout as a string if successful
    """
    if env:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=asp.PIPE, stderr=asp.PIPE, env=env
        )
    else:
        proc = await asp.create_subprocess_exec(*cmd, stdout=asp.PIPE, stderr=asp.PIPE)

    if timeout:
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError as e:
            if proc.returncode is None:
                parent = psutil.Process(proc.pid)
                for child in cast(list[psutil.Process], parent.children(recursive=True)):
                    child.kill()
                parent.kill()
                logger.error(
                    f"Force killing process {proc.pid}, cmd='{cmd}' (timed out)"
                )
            raise e
    else:
        stdout, stderr = await proc.communicate()

    assert proc.returncode is not None
    if proc.returncode != 0:
        raise CalledProcessError(proc.returncode, cmd, stdout, stderr)

    return stdout.decode()


async def asp_check_call(
    cmd: list[str],
    timeout: int | None = None,
    env: dict[str, str] | None = None,
    stdout: IO[AnyStr] | int = asp.DEVNULL,
    stderr: IO[AnyStr] | int = asp.DEVNULL,
) -> Literal[0]:
    """Async version of sp.check_call

    :param cmd: command array like the sync version
    :param timeout: timeout in seconds. Wait forever if None
    :param env: env override
    :param stdout: where to put stdout, defaults to asp.DEVNULL
    :param stderr: where to put stderr, defaults to asp.DEVNULL
    :raises CalledProcessError: when return code is not 0
    :return: 0
    """
    if env:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=stdout, stderr=stderr, env=env
        )
    else:
        proc = await asp.create_subprocess_exec(*cmd, stdout=stdout, stderr=stderr)

    if timeout:
        try:
            rc = await asyncio.wait_for(proc.wait(), timeout)
        except asyncio.TimeoutError as e:
            if proc.returncode is None:
                parent = psutil.Process(proc.pid)
                for child in cast(list[psutil.Process], parent.children(recursive=True)):
                    child.kill()
                parent.kill()
                logger.error(
                    f"Force killing process {proc.pid}, cmd='{cmd}' (timed out)"
                )
            raise e
    else:
        rc = await proc.wait()

    if rc != 0:
        raise CalledProcessError(rc, cmd)

    return rc


async def asp_run(
    cmd: Sequence[str],
    timeout: int | None = None,
    env: MutableMapping[str, str] | None = None,
) -> sp.CompletedProcess[str]:
    """Async version of subprocess.check_output

    :param cmd: command array like the sync version
    :param timeout: timeout in seconds. Wait forever if None
    :param env: env override
    :return: stdout as a string if successful
    """
    if env:
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=asp.PIPE, stderr=asp.PIPE, env=env
        )
    else:
        proc = await asp.create_subprocess_exec(*cmd, stdout=asp.PIPE, stderr=asp.PIPE)

    if timeout:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
    else:
        stdout, stderr = await proc.communicate()

    assert proc.returncode is not None

    return sp.CompletedProcess[str](
        cmd, proc.returncode, stdout.decode(), stderr.decode()
    )
