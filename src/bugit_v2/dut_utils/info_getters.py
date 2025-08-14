"""
A collection of simple functions that gets various information from the DUT
"""


import os
import re
import shutil
import subprocess as sp
from collections import Counter


def get_cpu_info() -> str:
    """Parse /proc/cpuinfo and return cpu model information."""
    cpus: list[dict[str, str]] = []
    cpuinfo: dict[str, str] = {}
    parse_line = re.compile(r"(.*?)\s+:\s+(.*)").match
    processor_line = re.compile(r"^(p|P)rocessor")

    with open("/proc/cpuinfo") as file:
        for line in file:
            if processor_line.match(line) and cpuinfo:
                cpus.append(cpuinfo)
                cpuinfo = {}
            match = parse_line(line)
            if match:
                key, value = match.groups()
                cpuinfo[key] = value
    cpus.append(cpuinfo)

    cpu_names = Counter[str]()
    for cpu in cpus:
        if "model name" in cpu:
            cpu_name = " ".join(cpu["model name"].split())
            cpu_names[cpu_name] += 1
        elif "Processor" in cpu:
            cpu_name = " ".join(cpu["Processor"].split())
            cpu_names[cpu_name] += 1

    cpu_names_str = [
        "{} ({}x)".format(cpu_name, count)
        for cpu_name, count in cpu_names.items()
    ]

    return "\n".join(cpu_names_str)


def get_amdgpu_info() -> str | None:
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
            .split("\n")
        )
        if len(pcis) == 0 or pcis[0] == "":
            continue
        for path in paths:
            for pci in pcis:
                p = pci.split()
                if len(p) > 3 and p[0] in path:
                    v = sp.check_output(["cat", path]).strip()
                    vbios += f"{v.strip()}[{pci.split()[3].upper()}] "
    return vbios


def get_standard_info() -> dict[str, str]:
    """Gather standard information that should be present in all bugs."""
    standard_info: dict[str, str] = {}

    build_stamp_paths = [
        "/var/lib/snapd/hostfs/var/lib/ubuntu_dist_channel",  # PC project
        "/var/lib/snapd/hostfs/.disk/info",  # ubuntu classic
        "/run/mnt/ubuntu-seed/.disk/info",  # ubuntu core
    ]
    for path in build_stamp_paths:
        if os.path.isfile(path):
            log = sp.check_output(["tail", "-n", "1", path], text=True).strip()
            standard_info["Image"] = log
            break

    if "Image" not in standard_info:
        print("WARNING: Failed to get build stamp")

    for dmi_value in (
        "system-manufacturer",
        "system-product-name",
        "bios-version",
    ):
        standard_info[
            " ".join(word.capitalize() for word in dmi_value.split("-"))
        ] = sp.check_output(
            ["sudo", "dmidecode", "-s", dmi_value], text=True
        ).strip()

    standard_info["CPU"] = get_cpu_info()

    lspci_log = sp.check_output(["lspci", "-nn"], text=True).strip()
    lspci_output = lspci_log.splitlines()
    # '03' is the PCI class for display controllers
    standard_info["GPU"] = "\n".join(
        [line for line in lspci_output if "[03" in line]
    )

    if "NVIDIA" in standard_info["GPU"] and shutil.which("nvidia-smi"):
        nvidia_err = "Cannot capture driver or VBIOS version"
        nvidia_log = sp.run(["nvidia-smi", "-q"], check=False, text=True)

        if nvidia_log.returncode == 0:
            if (
                nvidia_driver_match := re.search(
                    r"Driver Version\s*:\s(\d*\.\d*[\.\d*]*)\s*",
                    nvidia_log.stdout,
                )
            ) is not None:
                standard_info["nvidia-driver"] = nvidia_driver_match.group(1)
            else:
                standard_info["nvidia-driver"] = nvidia_err

            if (
                nvidia_vbios_match := re.search(
                    r"VBIOS Version\s*:\s([\w\d]*[\.\w\d]*)\s*",
                    nvidia_log.stdout,
                )
            ) is not None:
                standard_info["nvidia-driver"] = nvidia_vbios_match.group(1)
            else:
                standard_info["nvidia-driver"] = nvidia_err
        else:
            standard_info["nvidia-info"] = nvidia_err

    if "AMD" in standard_info["GPU"]:
        vbios = get_amdgpu_info()
        if vbios:
            standard_info["amd-vbios"] = vbios
        else:
            standard_info["amd-vbios"] = "Cannot capture VBIOS version"

    standard_info["Kernel Version"] = sp.check_output(
        ["uname", "-r"], text=True
    ).strip()

    return standard_info
