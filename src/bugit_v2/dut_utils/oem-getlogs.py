#!/usr/bin/python3

"""
This is a slightly modified version of the original oem-getlogs from apport
Vendorized from the file in the `python3-apport` package
"""

import platform

try:
    # this modification prevents the script from
    # reading the base snap's /etc/os-release (core22)
    # or straight up crash (core 24)
    platform._os_release_candidates = (  # pyright: ignore[reportAttributeAccessIssue]
        "/var/lib/snapd/hostfs/etc/os-release",
        *platform._os_release_candidates,  # pyright: ignore[reportAttributeAccessIssue]
    )
except Exception:
    pass

import gzip
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from argparse import ArgumentParser
from glob import glob
from io import BytesIO
from typing import Callable, Literal

# if your IDE doesn't see these libraries
# add /usr/lib/python3/dist-packages/ to extraPaths
# and apt install python3-apport
import apport
from apport import hookutils
from problem_report import CompressedValue

opt_debug = False


# Apport helper routines
def debug(*text: str):
    if opt_debug:
        print(*text, "\n")


def attach_command_output(
    report: apport.Report, command_list: list[str], key: str
):
    debug(" ".join(command_list))
    log = hookutils.command_output(command_list)
    if not log or log[:5] == "Error":
        return
    report[key] = log


def attach_pathglob_as_zip(
    report: apport.Report,
    pathglob: list[str],
    key: str,
    data_filter: Callable[[str], str] | None = None,
    mode: Literal["a", "b"] = "b",
):
    """Use zip file here because tarfile module in linux can't
    properly handle file size 0 with content in /sys directory like
    edid file. zipfile module works fine here. So we use it.

    mode:
         a: for ascii  mode of data
         b: for binary mode of data
    """
    filelist: list[str] = []
    for pg in pathglob:
        for file in glob(pg):
            filelist.append(file)

    zipf = BytesIO()
    with zipfile.ZipFile(
        zipf, mode="w", compression=zipfile.ZIP_DEFLATED
    ) as zipobj:
        for f in filelist:
            debug(key, f)
            if not os.path.isfile(f):
                debug(f, "is not a file")
                continue
            if mode == "a":
                with open(f, encoding="ascii") as f_fd:
                    data = f_fd.read()
                    if data_filter is None:
                        zipobj.writestr(f, data)
                    else:
                        zipobj.writestr(f, data_filter(data))
            else:
                zipobj.write(f)
    cvalue = CompressedValue()
    cvalue.set_value(zipf.getbuffer().tobytes())
    report[key + ".zip"] = cvalue


def attach_nvidia_debug_logs(report: apport.Report, keep_locale: bool = False):
    # check if nvidia-bug-report.sh exists
    nv_debug_command = "nvidia-bug-report.sh"

    if shutil.which(nv_debug_command) is None:
        debug(nv_debug_command, "does not exist.")
        return

    env = os.environ.copy()
    if not keep_locale:
        env["LC_MESSAGES"] = "C"

    # output result to temp directory
    nv_tempdir = tempfile.mkdtemp()
    nv_debug_file = "nvidia-bug-report"
    nv_debug_fullfile = os.path.join(nv_tempdir, nv_debug_file)
    nv_debug_cmd = [nv_debug_command, "--output-file", nv_debug_fullfile]
    try:
        subprocess.run(
            nv_debug_cmd,
            env=env,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        nv_debug_fullfile_gz = nv_debug_fullfile + ".gz"
        hookutils.attach_file_if_exists(
            report, nv_debug_fullfile_gz, "nvidia-bug-report.gz"
        )
        os.unlink(nv_debug_fullfile_gz)
        os.rmdir(nv_tempdir)
    except OSError as e:
        print("Error:", str(e))
        print("Fail on cleanup", nv_tempdir, ". Please file a bug for it.")


def dot():
    print(".", end="", flush=True)


def build_packages():
    # related packages
    packages = ["apt", "grub2"]

    # display
    packages.append("xorg")
    packages.append("gnome-shell")

    # audio
    packages.append("alsa-base")

    # hotkey and hotplugs
    packages.append("udev")

    # networking issues
    packages.append("network-manager")

    return packages


def helper_url_credential_filter(string_with_urls: str) -> str:
    return re.sub(r"://\w+?:\w+?@", "://USER:SECRET@", string_with_urls)


def add_info(report: apport.Report):
    # Check if the DCD file is exist in the installer.
    attach_command_output(report, ["ubuntu-report", "show"], "UbuntuReport")
    dot()
    hookutils.attach_file_if_exists(report, "/etc/buildstamp", "BuildStamp")
    dot()
    attach_pathglob_as_zip(
        report,
        ["/sys/firmware/acpi/tables/*", "/sys/firmware/acpi/tables/*/*"],
        "acpitables",
    )
    dot()

    # Basic hardare information
    hookutils.attach_hardware(report)
    dot()
    hookutils.attach_wifi(report)
    dot()

    hwe_system_commands = {
        "lspci--xxxx": ["lspci", "-xxxx"],
        "lshw.json": ["lshw", "-json", "-numeric"],
        "dmidecode": ["dmidecode"],
        "fwupdmgr_get-devices": [
            "fwupdmgr",
            "get-devices",
            "--show-all-devices",
            "--no-unreported-check",
        ],
        "boltctl-list": ["boltctl", "list"],
        "mokutil---sb-state": ["mokutil", "--sb-state"],
        "tlp-stat": ["tlp-stat"],
    }
    for name, command_list in hwe_system_commands.items():
        attach_command_output(report, command_list, name)
        dot()

    # More audio related
    hookutils.attach_alsa(report)
    dot()
    audio_system_commands = {
        "pactl-list": ["pactl", "list"],
        "aplay-l": ["aplay", "-l"],
        "aplay-L": ["aplay", "-L"],
        "arecord-l": ["arecord", "-l"],
        "arecord-L": ["arecord", "-L"],
    }
    for name, command_list in audio_system_commands.items():
        attach_command_output(report, command_list, name)
        dot()
    attach_pathglob_as_zip(
        report,
        [
            "/usr/share/alsa/ucm/*/*",
            "/usr/share/alsa/ucm2/*",
            "/usr/share/alsa/ucm2/*/*",
            "/usr/share/alsa/ucm2/*/*/*",
        ],
        "ALSA-UCM",
    )
    dot()

    # FIXME: should be included in xorg in the future
    gfx_system_commands = {
        "glxinfo": ["glxinfo"],
        "xrandr": ["xrandr"],
        "xinput": ["xinput"],
    }
    for name, command_list in gfx_system_commands.items():
        attach_command_output(report, command_list, name)
        dot()
    attach_pathglob_as_zip(
        report, ["/sys/devices/*/*/drm/card?/*/edid"], "EDID"
    )
    dot()

    # nvidia-bug-reports.sh
    attach_nvidia_debug_logs(report)
    dot()

    # FIXME: should be included in thermald in the future
    attach_pathglob_as_zip(
        report,
        [
            "/etc/thermald/*",
            "/sys/devices/virtual/thermal/*",
            "/sys/class/thermal/*",
        ],
        "THERMALD",
    )
    dot()

    # all kernel and system messages
    attach_pathglob_as_zip(report, ["/var/log/*", "/var/log/*/*"], "VAR_LOG")
    dot()

    # apt configs
    attach_pathglob_as_zip(
        report,
        [
            "/etc/apt/apt.conf.d/*",
            "/etc/apt/sources.list",
            "/etc/apt/sources.list.d/*.list",
            "/etc/apt/preferences.d/*",
        ],
        "APT_CONFIGS",
        mode="a",
        data_filter=helper_url_credential_filter,
    )
    dot()

    # TODO: debug information for suspend or hibernate

    # packages installed.
    attach_command_output(report, ["dpkg", "-l"], "dpkg-l")
    dot()

    # FIXME: should be included in bluez in the future
    attach_command_output(report, ["hciconfig", "-a"], "hciconfig-a")
    dot()

    # FIXME: should be included in dkms in the future
    attach_command_output(report, ["dkms", "status"], "dkms_status")
    dot()

    # enable when the feature to include data from package hooks exists.
    # packages = build_packages()
    # attach_related_packages(report, packages)


def main():
    parser = ArgumentParser(
        prog="oem-getlogs",
        usage="Useage: sudo -E oem-getlogs [-c CASE_ID]",
        description=__doc__,
    )
    parser.add_argument(
        "-c", "--case-id", help="optional CASE_ID", dest="cid", default=""
    )
    args = parser.parse_args()

    # check if we got root permission
    if os.geteuid() != 0:
        print("Error: you need to run this program as root")
        parser.print_help()
        sys.exit(1)

    print("Start to collect logs: ", end="", flush=True)
    # create report
    report = apport.Report()
    add_info(report)

    # generate filename
    hostname = os.uname()[1]
    date_time = time.strftime("%Y%m%d%H%M%S%z", time.localtime())
    filename_lst = ["oemlogs", hostname]
    if len(args.cid) > 0:
        filename_lst.append(args.cid)
    filename_lst.append(date_time + ".apport.gz")
    filename = "-".join(filename_lst)

    with gzip.open(filename, "wb") as f:
        report.write(f)  # pyright: ignore[reportArgumentType]
    print("\nSaved log to", filename)
    print("The owner of the file is root. You might want to")
    print("    chown [user]:[group]", filename)


if __name__ == "__main__":
    main()
