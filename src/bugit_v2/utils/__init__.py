import os


def is_prod() -> bool:
    """Is bugit in a prod environment?"""
    return os.getenv("PROD") == "1"
