import configparser as cp
import os
import shutil
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess
from sys import stderr
from tempfile import TemporaryDirectory
from typing import Literal, NamedTuple

from async_lru import alru_cache

from bugit_v2.utils import is_snap
from bugit_v2.utils.async_subprocess import asp_check_output, asp_run
from bugit_v2.utils.constants import HOST_FS


class CheckboxInfo(NamedTuple):
    type: Literal["deb", "snap"]
    version: str
    bin_path: Path  # absolute path


async def checkbox_exec(
    checkbox_args: list[str],
    additional_env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> CompletedProcess[str]:
    """Run checkbox commands with already prepped environment

    :param args: the arguments to subprocess.run().
        **This should not include the checkbox command itself**
    :param additional_env: Additional environment variables
    :return: whatever subprocess.run returns
    """
    checkbox_info = await get_checkbox_info()
    assert checkbox_info, "Unable to find checkbox on this DUT"
    if checkbox_info.type == "snap" or not is_snap():
        # pipx bugit or snap checkbox
        # no need to setup anything, just run the command
        print("normal path")
        return await asp_run(
            [str(checkbox_info.bin_path), *checkbox_args],
            env=(additional_env or {}) | os.environ,
            timeout=timeout,
        )
    else:
        print("special path")
        with TemporaryDirectory() as temp_dir:
            for src_file in Path(
                "/var/lib/snapd/hostfs/usr/share/plainbox-providers-1/"
            ).iterdir():
                dst_file = shutil.copy(src_file, temp_dir)
                provider_config = cp.ConfigParser()
                provider_config.read(dst_file)

                for key in ("bin_dir", "data_dir", "units_dir", "jobs_dir"):
                    if key not in provider_config["PlainBox Provider"]:
                        print("No such key", key, "in", src_file)
                        continue

                    new_path = HOST_FS / (
                        provider_config["PlainBox Provider"][key]
                        # vvvvvv prevent pathlib from treating it as abs path
                    ).lstrip("/")

                    if not new_path.exists():
                        print("No such path", new_path, file=stderr)
                        continue

                    provider_config["PlainBox Provider"][key] = str(new_path)
                    with open(dst_file, "w") as f:
                        provider_config.write(f)

            PATH = ":".join(
                map(
                    lambda s: str(HOST_FS) + s,
                    [
                        "/usr/local/sbin",
                        "/usr/local/bin",
                        "/usr/sbin",
                        "/usr/bin",
                        "/sbin",
                        "/bin",
                        "/usr/games",
                        "/usr/local/games",
                        "/snap/bin",
                    ],
                )
            )
            return await asp_run(
                [str(checkbox_info.bin_path), *checkbox_args],
                env=(additional_env or {})
                | {
                    "PATH": PATH,
                    "PYTHONPATH": "/var/lib/snapd/hostfs/usr/lib/python3/dist-packages",
                    "PROVIDERPATH": str(
                        Path(temp_dir).absolute(),
                    ),
                },
                timeout=timeout,
            )


@alru_cache()
async def get_checkbox_info() -> CheckboxInfo | None:
    try:
        if is_snap():
            if (deb_checkbox := HOST_FS / "usr" / "bin" / "checkbox-cli").exists():
                # host is using debian checkbox
                return CheckboxInfo(
                    "deb",
                    (
                        await asp_check_output(
                            [str(deb_checkbox), "--version"],
                            env={
                                "PYTHONPATH": "/var/lib/snapd/hostfs/usr/lib/python3/dist-packages"
                            },
                        )
                    ).strip(),
                    deb_checkbox,
                )
            else:
                # search through /snap/bin and see if a project checkbox is there
                for executable in os.listdir(HOST_FS / "snap" / "bin"):
                    if (
                        executable.endswith("checkbox-cli")
                        and "ce-oem" not in executable
                    ):
                        return CheckboxInfo(
                            "snap",
                            (
                                await asp_check_output(
                                    [
                                        str(HOST_FS / "snap" / "bin" / executable),
                                        "--version",
                                    ],
                                )
                            ).strip(),
                            (HOST_FS / "snap" / "bin" / executable),
                        )
        else:
            if (checkbox_bin := shutil.which("checkbox-cli")) is not None:
                return CheckboxInfo(
                    "deb",
                    (
                        await asp_check_output(
                            [checkbox_bin, "--version"],
                        )
                    ).strip(),
                    Path(checkbox_bin),
                )
            else:
                # search through /snap/bin and see if a project checkbox is there
                for executable in os.listdir("/snap/bin"):
                    if (
                        executable.endswith("checkbox-cli")
                        and "ce-oem" not in executable
                    ):
                        return CheckboxInfo(
                            "snap",
                            (
                                await asp_check_output(
                                    [
                                        (f"/snap/bin/{executable}"),
                                        "--version",
                                    ],
                                )
                            ).strip(),
                            Path("/snap/bin") / executable,
                        )
    except CalledProcessError:
        return None
