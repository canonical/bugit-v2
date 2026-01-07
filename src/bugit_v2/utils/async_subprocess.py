import asyncio
import asyncio.subprocess as asp
from subprocess import CalledProcessError
from typing import IO, Any, Literal


async def asp_check_output(
    cmd: list[str],
    timeout: int | None = None,
    env: dict[str, str] | None = None,
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
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=asp.PIPE, stderr=asp.PIPE
        )

    if timeout:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
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
    stdout: IO[Any] | int = asp.DEVNULL,
    stderr: IO[Any] | int = asp.DEVNULL,
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
        proc = await asp.create_subprocess_exec(
            *cmd, stdout=stdout, stderr=stderr
        )

    if timeout:
        rc = await asyncio.wait_for(proc.wait(), timeout)
    else:
        rc = await proc.wait()

    if rc != 0:
        raise CalledProcessError(rc, cmd)

    return rc
