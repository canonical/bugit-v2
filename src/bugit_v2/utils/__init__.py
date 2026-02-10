import datetime as dt
import importlib.metadata
import os
import shutil
import string

from bugit_v2.utils.constants import HOST_FS


_VALID_CHARS = frozenset(f"-_.{string.ascii_letters}{string.digits}")


def is_prod() -> bool:
    """Is bugit in a prod environment?"""
    return os.getenv("DEBUG") != "1"


def is_snap() -> bool:
    return "SNAP" in os.environ


def host_is_ubuntu_core() -> bool:
    if shutil.which("apt") is not None:
        return False
    # TODO: this is prob not the best way to do this
    apt_path = HOST_FS / "usr" / "bin" / "apt"
    return not apt_path.exists() or not apt_path.is_file()


def get_bugit_version() -> str:
    return importlib.metadata.version("bugit-v2")


def pretty_date(d: dt.datetime) -> str:
    diff = dt.datetime.now() - d
    s = diff.seconds
    if diff.days > 7 or diff.days < 0:
        return d.strftime("%d %b %y")
    elif diff.days == 1:
        return "1 day ago"
    elif diff.days > 1:
        return f"{diff.days} days {diff.seconds//3600} hours ago"
    elif s <= 1:
        return "just now"
    elif s < 60:
        return f"{s} seconds ago"
    elif s < 3600:
        return "{:.2f} minutes ago".format(s / 60)
    elif s < 7200:
        return "1 hour ago"
    else:
        return "{:.2f} hours ago".format(s / 3600)


def slugify(s: str) -> str:
    """the slugify from checkbox

    :param s: string to slugify
    :return: a clean string for filenames
    """

    return "".join(char if char in _VALID_CHARS else "_" for char in s)
