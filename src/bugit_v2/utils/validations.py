import os
import subprocess as sp


def bugit_is_in_devmode() -> bool:
    # technically bugit won't even install if --devmode is not specified
    # because of the sudoer hook
    # but it's possible to go from devmode (with sudoer hook succeeded)
    # into strict mode and this check will kick in
    try:
        snap_list = sp.check_output(
            ["snap", "list"], text=True
        )  # do not use snap info, it needs the internet
    except PermissionError:
        return False  # can't call the snap command in strict confinement

    for line in snap_list.splitlines():
        if "bugit" in line and "devmode" in line:
            return True

    return False


def before_entry_check():
    """Runs the checks necessary for all the commands provided by the snap

    :raises SystemExit: Not using sudo
    :raises SystemExit: Not installed with --devmode
    """
    if os.getuid() != 0:
        raise SystemExit(
            "Please run this app with \033[4msudo bugit-v2\033[0m"
        )

    if "SNAP" in os.environ and not bugit_is_in_devmode():
        raise SystemExit(
            "Bugit is not installed in devmode. Please reinstall with --devmode specified."
        )
