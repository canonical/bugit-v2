import os
import pty
import subprocess
import time
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / ".github/skills/bugit/scripts"
CREDS = SCRIPTS / "credentials.sh"


def run(script: Path, *args: str, cred_file: Path, **kw) -> subprocess.CompletedProcess:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("SSHPASS", "BUGIT_JIRA_EMAIL", "BUGIT_JIRA_TOKEN")
    }
    env["BUGIT_CRED_FILE"] = str(cred_file)
    return subprocess.run(
        ["bash", str(script), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        **kw,
    )


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_check_without_a_file_prints_the_command_to_run(tmp_path):
    result = run(CREDS, "--check", "--tracker", "jira", cred_file=tmp_path / "c")
    assert result.returncode == 6
    assert "SSH password, Jira email, Jira API token" in result.stderr
    assert f"Run {SCRIPTS}/credentials.sh --tracker jira in your terminal" in (
        result.stderr
    )


def test_check_needs_the_jira_token_only_for_jira(tmp_path):
    cred = write(tmp_path / "c", "SSHPASS=pw\n")
    assert run(CREDS, "--tracker", "local", "--check", cred_file=cred).returncode == 0
    result = run(CREDS, "--check", "--tracker", "jira", cred_file=cred)
    assert result.returncode == 6
    assert "pw" not in result.stdout + result.stderr


def test_check_rejects_a_readable_file(tmp_path):
    cred = write(tmp_path / "c", "SSHPASS=pw\n")
    cred.chmod(0o644)
    assert run(CREDS, "--check", cred_file=cred).returncode == 6


def test_clear_after_tracker_removes_the_file(tmp_path):
    cred = write(tmp_path / "c", "SSHPASS=pw\n")
    assert run(CREDS, "--tracker", "jira", "--clear", cred_file=cred).returncode == 0
    assert not cred.exists()


def test_configure_without_ssh_password_exits_6(tmp_path):
    result = run(
        SCRIPTS / "configure.sh",
        "--status",
        "--tracker",
        "jira",
        "1.2.3.4",
        cred_file=tmp_path / "c",
    )
    assert result.returncode == 6
    assert "credentials.sh --tracker jira" in result.stderr


def test_saving_for_local_keeps_the_jira_credentials(tmp_path):
    cred = write(
        tmp_path / "c",
        "SSHPASS=old\nBUGIT_JIRA_EMAIL=a@b.com\nBUGIT_JIRA_TOKEN=tok=1\n",
    )
    master, slave = pty.openpty()
    env = {**os.environ, "BUGIT_CRED_FILE": str(cred)}
    proc = subprocess.Popen(
        ["bash", str(CREDS), "--tracker", "local"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
    )
    os.close(slave)
    time.sleep(0.5)
    os.write(master, b"new\n")
    assert proc.wait(timeout=10) == 0
    os.close(master)
    assert cred.read_text() == (
        "SSHPASS=new\nBUGIT_JIRA_EMAIL=a@b.com\nBUGIT_JIRA_TOKEN=tok=1\n"
    )
    assert oct(cred.stat().st_mode & 0o777) == "0o600"
