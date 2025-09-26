import datetime as dt
import os


def is_prod() -> bool:
    """Is bugit in a prod environment?"""
    return os.getenv("DEBUG") != "1"


def is_snap() -> bool:
    return "SNAP" in os.environ


def pretty_date(d: dt.datetime) -> str:
    diff = dt.datetime.now() - d
    s = diff.seconds
    if diff.days > 7 or diff.days < 0:
        return d.strftime("%d %b %y")
    elif diff.days == 1:
        return "1 day ago"
    elif diff.days > 1:
        return f"{diff.days} days {diff.seconds} ago"
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
