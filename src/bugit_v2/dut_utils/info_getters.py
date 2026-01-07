"""
A collection of simple functions that gets various information from the DUT

These are carried over from the original bugit
https://git.launchpad.net/bugit/tree/bugit/bug_assistant.py
"""

import asyncio
import os
import platform
import re
from collections import Counter

from bugit_v2.checkbox_utils.checkbox_exec import get_checkbox_info
from bugit_v2.utils import is_snap
from bugit_v2.utils.async_subprocess import asp_check_output, asp_run


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
            while i < L and not dmi_out_lines[i].strip().startswith(
                "Version ID:"
            ):
                i += 1
            if i < L:
                return (
                    dmi_out_lines[i]
                    .strip()
                    .removeprefix("Version ID:")
                    .strip()
                )
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
        "{} ({}x)".format(cpu_name, count)
        for cpu_name, count in cpu_names.items()
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
                    ["lspci", "-Dnm", "-d", "1002{}".format(klass)],
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
                        vbios += (
                            f"{f.read().strip()}[{pci.split()[3].upper()}] "
                        )
    return vbios


async def get_standard_info(
    command_timeout: int | None = 30,
) -> dict[str, str]:
    """
    Gather standard information that should be present in all bugs.
    This can be very slow so run it asynchronously
    """
    standard_info: dict[str, str] = {}

    async def build_stamp():
        build_stamp_paths = [
            "/var/lib/snapd/hostfs/var/lib/ubuntu_dist_channel",  # PC project
            "/var/lib/snapd/hostfs/.disk/info",  # ubuntu classic
            "/run/mnt/ubuntu-seed/.disk/info",  # ubuntu core
        ]
        for path in build_stamp_paths:
            if os.path.isfile(path):
                log = (
                    await asp_check_output(
                        ["tail", "-n", "1", path], timeout=command_timeout
                    )
                ).strip()
                standard_info["Image"] = log
                break

        if "Image" not in standard_info:
            standard_info["Image"] = "Failed to get build stamp"

    async def dmi():
        for dmi_key in (
            "system-manufacturer",
            "system-product-name",
            "bios-version",
        ):
            standard_info[
                " ".join(word.capitalize() for word in dmi_key.split("-"))
            ] = (
                await asp_check_output(
                    ["dmidecode", "-s", dmi_key],
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

        if (
            ec_version := await get_thinkpad_ec_version(command_timeout)
        ) is not None:
            standard_info["Embedded Controller Version"] = ec_version

    await asyncio.gather(
        build_stamp(), dmi(), lspci(), ec(), return_exceptions=True
    )

    if "NVIDIA" in standard_info["GPU"]:
        nvidia_err = "Cannot capture driver or VBIOS version"
        try:
            nvidia_log = await asp_run(
                [
                    ("/var/lib/snapd/hostfs/usr/bin/" if is_snap() else "")
                    + "nvidia-smi",
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
                    standard_info["NVIDIA Driver"] = nvidia_driver_match.group(
                        1
                    )
                else:
                    standard_info["NVIDIA Driver"] = (
                        nvidia_err
                        + ", no driver version was listed in nvidia-smi -q"
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
                        nvidia_err
                        + ", no VBIOS version was listed in nvidia-smi -q"
                    )
            else:
                standard_info["NVIDIA Driver"] = (
                    nvidia_err
                    + f", nvidia-smi -q returned {nvidia_log.returncode}"
                )
        except FileNotFoundError:
            standard_info["NVIDIA Driver"] = (
                f"{nvidia_err}, nvidia-smi is not installed on this system"
            )

    if "AMD" in standard_info["GPU"]:
        vbios = await get_amd_gpu_info(command_timeout)
        standard_info["AMD VBIOS"] = (
            vbios or "Cannot capture AMD VBIOS version"
        )

    standard_info["Kernel Version"] = platform.uname().release

    if (cb := get_checkbox_info()) is not None:
        standard_info["Checkbox Version"] = cb.version
        standard_info["Checkbox Type"] = cb.type.capitalize()

    return standard_info
