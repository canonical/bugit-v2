import os


def is_prod() -> bool:
    """Is bugit in a prod environment?"""
    return os.getenv("DEBUG") != "1"


def is_snap() -> bool:
    return "SNAP" in os.environ
