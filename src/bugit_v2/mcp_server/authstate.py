"""Authentication state of the issue trackers, plus a headless Launchpad login"""

import json
import logging
import os
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

JIRA_TOKEN_URL = "https://id.atlassian.com/manage-profile/security/api-tokens"
SERVICES = ("launchpad", "jira", "local")


class LaunchpadLoginFlow(Protocol):
    def begin(self) -> str:
        """Start a login, return the URL the human must open"""

    def complete(self, auth_file: Path) -> bool:
        """Save the credentials to auth_file if the human has approved. Never blocks"""


class RealLaunchpadLoginFlow:
    """Browser-based Launchpad OAuth without needing a TTY or a TUI"""

    def __init__(self, app_name: str, service_root: str) -> None:
        self._app_name = app_name
        self._service_root = service_root
        self._credentials: Any | None = None
        self._lock = threading.Lock()

    def begin(self) -> str:
        from launchpadlib.credentials import Credentials

        with self._lock:
            self._credentials = Credentials(consumer_name=self._app_name)
            url = self._credentials.get_request_token(web_root=self._service_root)
            return url + "&allow_permission=WRITE_PRIVATE"

    def complete(self, auth_file: Path) -> bool:
        from lazr.restfulclient.errors import HTTPError

        with self._lock:
            if self._credentials is None:
                return False
            try:
                self._credentials.exchange_request_token_for_access_token(
                    self._service_root
                )
            except HTTPError:
                return False  # the human hasn't approved it yet
            self._credentials.save_to_path(str(auth_file))
            self._credentials = None
            return True


def default_validate_lp(app_name: str, service_root: str) -> Callable[[Path], None]:
    def validate(auth_file: Path) -> None:
        from launchpadlib.launchpad import Launchpad

        lp = Launchpad.login_with(
            app_name, service_root, credentials_file=str(auth_file)
        )
        _ = lp.me  # raises if the token is revoked/expired

    return validate


def default_validate_jira(server: str) -> Callable[[str, str], None]:
    def validate(email: str, token: str) -> None:
        from jira import JIRA

        JIRA(server=server.rstrip("/"), basic_auth=(email, token), validate=True)

    return validate


class AuthService:
    def __init__(
        self,
        *,
        lp_auth_file: Path,
        jira_creds_file: Path,
        env: Mapping[str, str] = os.environ,
        lp_flow: LaunchpadLoginFlow,
        validate_lp: Callable[[Path], None],
        validate_jira: Callable[[str, str], None],
    ) -> None:
        self._lp_file = lp_auth_file
        self._jira_file = jira_creds_file
        self._env = env
        self._flow = lp_flow
        self._validate_lp = validate_lp
        self._validate_jira = validate_jira
        self._lp_pending = False

    def jira_credentials(self) -> tuple[str, str] | None:
        email, token = self._env.get("BUGIT_JIRA_EMAIL"), self._env.get("BUGIT_JIRA_TOKEN")
        if email and token:
            return email, token
        try:
            data = json.loads(self._jira_file.read_text())
            return str(data["email"]), str(data["token"])
        except (OSError, ValueError, KeyError):
            return None

    def check_auth_status(self, service: str) -> dict[str, Any]:
        if service == "launchpad":
            return self._check_lp()
        if service == "jira":
            return self._check_jira()
        if service == "local":
            return self._result("local", True, "Local file tracker, no login needed")
        raise ValueError(f"Unknown service {service!r}; expected one of {SERVICES}")

    def _result(self, service: str, ok: bool, detail: str) -> dict[str, Any]:
        return {"service": service, "authenticated": ok, "detail": detail}

    def _check_lp(self) -> dict[str, Any]:
        if self._lp_pending:
            if self._flow.complete(self._lp_file):
                self._lp_pending = False
            else:
                return self._result(
                    "launchpad",
                    False,
                    "Login pending: the user has not approved the URL from "
                    "get_login_url('launchpad') yet. Ask them, then check again.",
                )
        if not self._lp_file.exists():
            return self._result(
                "launchpad",
                False,
                "No Launchpad credentials. Call get_login_url('launchpad') "
                "and ask the user to open it.",
            )
        try:
            self._validate_lp(self._lp_file)
        except Exception as e:
            return self._result("launchpad", False, f"Credentials are invalid: {e}")
        return self._result("launchpad", True, "Launchpad credentials are valid")

    def _check_jira(self) -> dict[str, Any]:
        creds = self.jira_credentials()
        if creds is None:
            return self._result(
                "jira",
                False,
                "No Jira credentials. Call get_login_url('jira') for how to set them up.",
            )
        email, token = creds
        try:
            self._validate_jira(email, token)
        except Exception as e:
            msg = str(e).replace(token, "***")
            return self._result("jira", False, f"Credentials are invalid: {msg}")
        return self._result("jira", True, f"Jira credentials for {email} are valid")

    def get_login_url(self, service: str) -> str:
        if service == "launchpad":
            self._lp_pending = True
            return self._flow.begin()
        if service == "jira":
            return JIRA_TOKEN_URL
        if service == "local":
            return "The local tracker needs no login."
        raise ValueError(f"Unknown service {service!r}; expected one of {SERVICES}")
