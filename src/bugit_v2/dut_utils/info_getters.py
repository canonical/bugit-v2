"""
A collection of simple functions that gets various information from the DUT

These are carried over from the original bugit
https://git.launchpad.net/bugit/tree/bugit/bug_assistant.py
"""

import os
import platform
import re
import shutil
import subprocess as sp
from collections import Counter


def get_cpu_info() -> str:
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


def get_amd_gpu_info() -> str | None:
    paths = sp.check_output(
        ["find", "/sys/devices/", "-name", "vbios_version"], text=True
    ).split()
    if len(paths) == 0:
        return None

    gpu_related_classes = ("::0300", "::0301", "::0302", "::0380")
    vbios = ""

    for klass in gpu_related_classes:
        pcis = (
            sp.check_output(
                ["lspci", "-Dnm", "-d", "1002{}".format(klass)], text=True
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
                    v = sp.check_output(["cat", path], text=True).strip()
                    vbios += f"{v.strip()}[{pci.split()[3].upper()}] "
    return vbios


def get_standard_info(command_timeout: int = 30) -> dict[str, str]:
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
            log = sp.check_output(
                ["tail", "-n", "1", path], text=True, timeout=command_timeout
            ).strip()
            standard_info["Image"] = log
            break

    if "Image" not in standard_info:
        standard_info["Image"] = "Failed to get build stamp"

    for dmi_key in (
        "system-manufacturer",
        "system-product-name",
        "bios-version",
    ):
        standard_info[
            " ".join(word.capitalize() for word in dmi_key.split("-"))
        ] = sp.check_output(
            ["dmidecode", "-s", dmi_key], text=True, timeout=command_timeout
        ).strip()

    standard_info["CPU"] = get_cpu_info()

    lspci_log = sp.check_output(
        ["lspci", "-nn"], text=True, timeout=command_timeout
    ).strip()
    lspci_output = lspci_log.splitlines()
    # '03' is the PCI class for display controllers
    standard_info["GPU"] = "\n".join(
        [line for line in lspci_output if "[03" in line]
    )

    if "NVIDIA" in standard_info["GPU"] and shutil.which("nvidia-smi"):
        nvidia_err = "Cannot capture driver or VBIOS version"
        nvidia_log = sp.run(
            ["nvidia-smi", "-q"],
            text=True,
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
                standard_info["NVIDIA Driver"] = nvidia_err

            if (
                nvidia_vbios_match := re.search(
                    r"VBIOS Version\s*:\s([\w\d]*[\.\w\d]*)\s*",
                    nvidia_log.stdout,
                )
            ) is not None:
                standard_info["NVIDIA Driver"] = nvidia_vbios_match.group(1)
            else:
                standard_info["NVIDIA Driver"] = nvidia_err
        else:
            standard_info["NVIDIA Driver"] = nvidia_err

    if "AMD" in standard_info["GPU"]:
        vbios = get_amd_gpu_info()
        standard_info["AMD VBIOS"] = vbios or "Cannot capture VBIOS version"

    standard_info["Kernel Version"] = platform.uname().release

    return standard_info
