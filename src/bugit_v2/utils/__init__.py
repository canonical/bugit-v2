import os


def is_prod() -> bool:
    """Is bugit in a prod environment?"""
    return os.getenv("PROD") == "1"


def on_ubuntucore() -> bool:
    """
    From checkbox: https://github.com/canonical/checkbox/blob/main/checkbox-ng/plainbox/impl/unit/unit.py#L61
    """
    snap = os.getenv("SNAP")
    if snap:
        with open(os.path.join(snap, "meta/snap.yaml")) as f:
            for line in f.readlines():
                if line == "confinement: classic\n":
                    return False
        return True
    return False
