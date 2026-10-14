"""
A collection of simple functions that gets various information from the DUT

These are carried over from the original bugit
https://git.launchpad.net/bugit/tree/bugit/bug_assistant.py
"""

import asyncio
import logging
import os
import platform
import re
import shlex
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from bugit_v2.checkbox_utils.checkbox_exec import get_checkbox_info
from bugit_v2.utils import is_snap
from bugit_v2.utils.async_subprocess import asp_check_output, asp_run
from bugit_v2.utils.constants import HOST_FS, NSENTER_PREFIX

logger = logging.getLogger(__name__)


async def get_thinkpad_ec_version(timeout: int | None = 30) -> str | None:
    """Thinkpad specific, get the embedded controller info

    :return: controller version if found, None otherwise
    """
    marker = "ThinkPad Embedded Controller Program"
    dmi_out_lines = (
        await asp_check_output(["dmidecode", "-t", "140"], timeout=timeout)
    ).splitlines()

    L = len(dmi_out_lines)
    i = 0

    while i < L:
        line = dmi_out_lines[i]
        if line.strip() == marker:
            i += 1
            while i < L and not dmi_out_lines[i].strip().startswith("Version ID:"):
                i += 1
            if i < L:
                return dmi_out_lines[i].strip().removeprefix("Version ID:").strip()
        else:
            i += 1


async def get_cpu_info() -> str:
    cpu_names = Counter[str]()
    with open("/proc/cpuinfo") as file:
        for line in file:
            clean_line = line.strip()
            if clean_line.startswith(("model name", "Processor")):
                cpu_name = clean_line.split(":", maxsplit=1)[-1].strip()
                cpu_names[cpu_name] += 1

    cpu_name_strings = [
        f"{cpu_name} ({count}x)" for cpu_name, count in cpu_names.items()
    ]

    return "\n".join(cpu_name_strings)


async def get_amd_gpu_info(timeout: int | None = 30) -> str | None:
    paths = (
        await asp_check_output(
            ["find", "/sys/devices/", "-name", "vbios_version"],
            timeout=timeout,
        )
    ).split()
    if len(paths) == 0:
        return None

    gpu_related_classes = ("::0300", "::0301", "::0302", "::0380")
    vbios = ""

    for klass in gpu_related_classes:
        pcis = (
            (
                await asp_check_output(
                    ["lspci", "-Dnm", "-d", f"1002{klass}"],
                    timeout=timeout,
                )
            )
            .strip()
            .splitlines()
        )
        if len(pcis) == 0 or pcis[0] == "":
            continue
        for path in paths:
            for pci in pcis:
                p = pci.split()
                if len(p) > 3 and p[0] in path:
                    with open(path) as f:
                        vbios += f"{f.read().strip()}[{pci.split()[3].upper()}] "
    return vbios


def _host_path(path: str) -> Path:
    """Map an absolute host path to one readable from inside the snap"""
    if is_snap():
        return HOST_FS / path.lstrip("/")
    return Path(path)


def _parse_os_release(content: str) -> dict[str, str]:
    os_release: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        try:
            os_release[key] = shlex.split(value)[0] if value else ""
        except (ValueError, IndexError):
            os_release[key] = value.strip("\"'")
    return os_release


async def _read_host_os_release(timeout: int | None = 30) -> dict[str, str]:
    try:
        result = await asp_run(
            [*NSENTER_PREFIX, "cat", "/etc/os-release"], timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            return _parse_os_release(result.stdout)
    except (OSError, TimeoutError, RuntimeError) as e:
        logger.warning("Failed to read host os-release with nsenter: %s", e)

    for path in ("/etc/os-release", "/usr/lib/os-release"):
        host_file = _host_path(path)
        if host_file.is_file():
            return _parse_os_release(host_file.read_text())
    return {}


async def _get_installed_host_packages(
    packages: Sequence[str], timeout: int | None = 30
) -> set[str]:
    """Returns the subset of `packages` that are installed on the host"""
    try:
        # dpkg-query returns 1 if any package is unknown,
        # but still prints the known ones, so don't check the return code
        result = await asp_run(
            [
                *NSENTER_PREFIX,
                "dpkg-query",
                "--show",
                "--showformat=${Package} ${db:Status-Abbrev}\n",
                *packages,
            ],
            timeout=timeout,
        )
    except (OSError, TimeoutError, RuntimeError) as e:
        logger.warning("Failed to query host packages: %s", e)
        return set()

    installed: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        # "ii" => desired=install, status=installed
        if len(parts) >= 2 and parts[1].startswith("ii"):
            installed.add(parts[0].split(":")[0])
    return installed


def _host_has_graphical_sessions() -> bool:
    for path in ("/usr/share/wayland-sessions", "/usr/share/xsessions"):
        session_dir = _host_path(path)
        if session_dir.is_dir() and any(session_dir.glob("*.desktop")):
            return True
    return False


async def get_release_info(timeout: int | None = 30) -> str:
    """Get the ubuntu flavor (Desktop/Server/Core) and version of the host

    :return: something like "Ubuntu Desktop 24.04 (Ubuntu 24.04.3 LTS)"
    """
    os_release = await _read_host_os_release(timeout)
    if not os_release:
        return "Failed to read /etc/os-release on the host"

    os_id = os_release.get("ID", "")
    version = os_release.get("VERSION_ID", "unknown version")
    pretty_name = os_release.get("PRETTY_NAME", "")

    if os_id == "ubuntu-core" or "Ubuntu Core" in os_release.get("NAME", ""):
        flavor = "Core"
    elif os_id == "ubuntu":
        desktop_pkgs = ("ubuntu-desktop", "ubuntu-desktop-minimal")
        installed = await _get_installed_host_packages(
            (*desktop_pkgs, "ubuntu-server"), timeout
        )
        if installed.intersection(desktop_pkgs) or _host_has_graphical_sessions():
            flavor = "Desktop"
        elif "ubuntu-server" in installed:
            flavor = "Server"
        else:
            flavor = "Server (no desktop environment detected)"
    else:
        return pretty_name or f"Non-Ubuntu OS: {os_id or 'unknown'} {version}"

    release = f"Ubuntu {flavor} {version}"
    if pretty_name:
        release += f" ({pretty_name})"
    return release


async def get_standard_info(
    command_timeout: int | None = 30,
) -> dict[str, str]:
    """
    Gather standard information that should be present in all bugs.
    This can be very slow so run it asynchronously
    """
    standard_info: dict[str, str] = {}

    build_stamp_paths = [
        "/var/lib/snapd/hostfs/var/lib/ubuntu_dist_channel",  # PC project
        "/var/lib/snapd/hostfs/.disk/info",  # ubuntu classic
        "/run/mnt/ubuntu-seed/.disk/info",  # ubuntu core
    ]
    for path in build_stamp_paths:
        if os.path.isfile(path):
            with open(path, "rb") as f:
                try:  # catch OSError in case of a one line file
                    f.seek(-2, os.SEEK_END)
                    while f.read(1) != b"\n":
                        f.seek(-2, os.SEEK_CUR)
                except OSError:
                    f.seek(0)
                last_line = f.readline().decode().strip()
                standard_info["Image"] = last_line
            break

    if "Image" not in standard_info:
        standard_info["Image"] = "Failed to get build stamp"

    async def dmi():
        for dmi_key in (
            "system-manufacturer",
            "system-product-name",
            "bios-version",
        ):
            standard_info[" ".join(word.capitalize() for word in dmi_key.split("-"))] = (
                await asp_check_output(
                    [*NSENTER_PREFIX, "dmidecode", "-s", dmi_key],
                    timeout=command_timeout,
                )
            ).strip()

    async def cpu():
        standard_info["CPU"] = await get_cpu_info()

    async def lspci():
        lspci_log = (
            await asp_check_output(["lspci", "-nn"], timeout=command_timeout)
        ).strip()
        lspci_output = lspci_log.splitlines()
        # '03' is the PCI class for display controllers
        standard_info["GPU"] = "\n".join(
            [line for line in lspci_output if "[03" in line]
        )

    async def ec():
        if (ec_version := await get_thinkpad_ec_version(command_timeout)) is not None:
            standard_info["Embedded Controller Version"] = ec_version

    async def release_info():
        try:
            standard_info["Release Info"] = await get_release_info(command_timeout)
        except (OSError, TimeoutError, RuntimeError) as e:
            standard_info["Release Info"] = f"Failed to get release info: {e}"

    await asyncio.gather(
        cpu(),
        dmi(),
        lspci(),
        ec(),
        release_info(),
        return_exceptions=True,
    )

    if "NVIDIA" in standard_info["GPU"]:
        nvidia_err = "Cannot capture driver or VBIOS version"
        try:
            nvidia_log = await asp_run(
                [
                    *NSENTER_PREFIX,
                    "nvidia-smi",
                    "-q",
                ],
                timeout=command_timeout,
            )

            if nvidia_log.returncode == 0:
                if (
                    nvidia_driver_match := re.search(
                        r"Driver Version\s*:\s(\d*\.\d*[\.\d*]*)\s*",
                        nvidia_log.stdout,
                    )
                ) is not None:
                    standard_info["NVIDIA Driver"] = nvidia_driver_match.group(1)
                else:
                    standard_info["NVIDIA Driver"] = (
                        nvidia_err + ", no driver version was listed in nvidia-smi -q"
                    )

                if (
                    nvidia_vbios_match := re.search(
                        r"VBIOS Version\s*:\s([\w\d]*[\.\w\d]*)\s*",
                        nvidia_log.stdout,
                    )
                ) is not None:
                    standard_info["NVIDIA VBIOS"] = nvidia_vbios_match.group(1)
                else:
                    standard_info["NVIDIA VBIOS"] = (
                        nvidia_err + ", no VBIOS version was listed in nvidia-smi -q"
                    )
            else:
                standard_info["NVIDIA Driver"] = (
                    nvidia_err + f", nvidia-smi -q returned {nvidia_log.returncode}"
                )
        except FileNotFoundError:
            standard_info["NVIDIA Driver"] = (
                f"{nvidia_err}, nvidia-smi is not installed on this system"
            )

    if "AMD" in standard_info["GPU"]:
        vbios = await get_amd_gpu_info(command_timeout)
        standard_info["AMD VBIOS"] = vbios or "Cannot capture AMD VBIOS version"

    standard_info["Kernel Version"] = platform.uname().release

    if (cb_info := get_checkbox_info()) is not None:
        standard_info["Checkbox Version"] = cb_info.version
        standard_info["Checkbox Type"] = cb_info.type.capitalize()

    return standard_info
