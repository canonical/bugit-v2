import os
from pathlib import Path

from bugit_v2.checkbox_utils import Session

SESSION_ROOT_DIR = Path("/var/tmp/checkbox-ng/sessions")


def get_valid_sessions() -> list[Path]:
    """Get a list of valid sessions in /var/tmp/checkbox-ng

    This is achieved by looking at which session directory has non-empty
    io-logs. If it's empty, it's either tossed by checkbox or didn't even
    reach the test case where it dumps the udev database, thus invalid
    """
    if not SESSION_ROOT_DIR.exists():
        return []
    valid_session_dirs: list[Path] = []
    for d in os.listdir(SESSION_ROOT_DIR):
        try:
            if len(os.listdir(SESSION_ROOT_DIR / d / "io-logs")) != 0:
                valid_session_dirs.append(SESSION_ROOT_DIR / d)
        except FileNotFoundError:
            continue
    return valid_session_dirs


if __name__ == "__main__":
    valid_sessions = get_valid_sessions()

    if len(valid_sessions) == 0:
        print("No sessions were found on this device")
        exit()

    for session_path in valid_sessions:
        print("Session directory:", session_path)
        session = Session(session_path)
        print("Test Plan:", session.testplan_id)
        print()
