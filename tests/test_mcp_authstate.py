import json
from pathlib import Path

import pytest

from bugit_v2.mcp_server.authstate import AuthService


class FakeLpFlow:
    def __init__(self, authorized: bool = False) -> None:
        self.authorized = authorized
        self.begun = 0

    def begin(self) -> str:
        self.begun += 1
        return "https://launchpad.net/+authorize-token?oauth_token=abc"

    def complete(self, auth_file: Path) -> bool:
        if self.authorized:
            auth_file.write_text("creds")
        return self.authorized


@pytest.fixture
def paths(tmp_path: Path):
    return tmp_path / "lp.txt", tmp_path / "jira.json"


def make(paths, *, env=None, flow=None, validate_lp=lambda p: None, validate_jira=lambda e, t: None):
    lp, jira = paths
    return AuthService(
        lp_auth_file=lp,
        jira_creds_file=jira,
        env=env or {},
        lp_flow=flow or FakeLpFlow(),
        validate_lp=validate_lp,
        validate_jira=validate_jira,
    )


def test_unknown_service(paths):
    with pytest.raises(ValueError, match="service"):
        make(paths).check_auth_status("github")
    with pytest.raises(ValueError, match="service"):
        make(paths).get_login_url("github")


def test_launchpad_missing(paths):
    st = make(paths).check_auth_status("launchpad")
    assert st["authenticated"] is False and st["service"] == "launchpad"
    assert "get_login_url" in st["detail"]


def test_launchpad_valid_and_invalid(paths):
    paths[0].write_text("creds")
    assert make(paths).check_auth_status("launchpad")["authenticated"] is True

    def bad(_p):
        raise RuntimeError("401 expired")

    st = make(paths, validate_lp=bad).check_auth_status("launchpad")
    assert st["authenticated"] is False and "expired" in st["detail"]


def test_launchpad_login_flow_completes_on_status_check(paths):
    flow = FakeLpFlow(authorized=False)
    svc = make(paths, flow=flow)
    url = svc.get_login_url("launchpad")
    assert url.startswith("https://launchpad.net/")
    st = svc.check_auth_status("launchpad")
    assert st["authenticated"] is False and "pending" in st["detail"].lower()
    flow.authorized = True
    assert svc.check_auth_status("launchpad")["authenticated"] is True
    assert paths[0].exists()


def test_jira_from_env_and_file(paths):
    assert make(paths).check_auth_status("jira")["authenticated"] is False
    env = {"BUGIT_JIRA_EMAIL": "a@b.c", "BUGIT_JIRA_TOKEN": "tok"}
    assert make(paths, env=env).check_auth_status("jira")["authenticated"] is True
    paths[1].write_text(json.dumps({"email": "a@b.c", "token": "tok"}))
    assert make(paths).check_auth_status("jira")["authenticated"] is True


def test_jira_invalid_creds_reported_without_leaking_token(paths):
    paths[1].write_text(json.dumps({"email": "a@b.c", "token": "SECRETTOKEN"}))

    def bad(email, token):
        raise RuntimeError(f"bad {token}")

    st = make(paths, validate_jira=bad).check_auth_status("jira")
    assert st["authenticated"] is False
    assert "SECRETTOKEN" not in json.dumps(st)


def test_jira_login_url_explains_how(paths):
    url = make(paths).get_login_url("jira")
    assert "atlassian" in url


def test_local_service_needs_no_login(paths):
    svc = make(paths)
    st = svc.check_auth_status("local")
    assert st["authenticated"] is True and st["service"] == "local"
    assert "no login" in svc.get_login_url("local").lower()
