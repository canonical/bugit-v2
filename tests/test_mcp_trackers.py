import dataclasses
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from bugit_v2.mcp_server.bugs import Draft
from bugit_v2.mcp_server.trackers import JiraTracker, LaunchpadTracker


def make_draft(
    project: str | None = "PROJ",
    logs: str | None = "j",
    attach_logs: list[str] | None = None,
) -> Draft:
    return Draft(
        "d1", "Title", "Body", ["wifi", "regression"], logs, "x", project, 0.0,
        attach_logs=attach_logs or [],
    )  # fmt: skip


class FakeSubmitter:
    def __init__(self) -> None:
        self.report = None
        self.uploads: list[tuple[str, list[str]]] = []
        self.finalized = False
        self.auth = None

    def submit(self, report):
        self.report = report
        yield "step"

    def upload_attachment(self, path: Path, filename: str | None = None):
        with tarfile.open(path) as t:
            self.uploads.append((filename or path.name, t.getnames()))

    def finalize(self):
        self.finalized = True

    @property
    def bug_url(self) -> str:
        return "https://bugs.example/123"


@pytest.fixture
def logs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs"
    d.mkdir()
    (d / "dmesg-of-boot-0.log").write_text("hello")
    return d


def test_launchpad_submit_builds_report_and_uploads_logs(logs_dir: Path):
    sub = FakeSubmitter()
    t = LaunchpadTracker(lambda: sub)
    out = t.submit(make_draft(), logs_dir)
    assert out == {
        "url": "https://bugs.example/123",
        "id": "123",
        "tracker": "launchpad",
    }
    r = sub.report
    assert (r.title, r.description, r.project) == ("Title", "Body", "PROJ")
    assert list(r.additional_tags) == ["wifi", "regression"]
    assert r.checkbox_session is None and r.job_id is None
    assert sub.uploads == [("dmesg.tar.xz", ["dmesg-of-boot-0.log"])]
    assert sub.finalized


def test_launchpad_submit_without_logs_or_project(logs_dir: Path):
    sub = FakeSubmitter()
    t = LaunchpadTracker(lambda: sub)
    with pytest.raises(ValueError, match="project"):
        t.submit(make_draft(project=None), None)
    t.submit(make_draft(), None)
    assert sub.uploads == []


def test_jira_submit_sets_credentials(logs_dir: Path):
    sub = FakeSubmitter()
    t = JiraTracker(lambda: sub, credentials=lambda: ("e@x.io", "tok"))
    out = t.submit(make_draft(), logs_dir)
    assert sub.auth.email == "e@x.io" and sub.auth.token == "tok"
    assert out["url"] == "https://bugs.example/123"


def test_jira_submit_without_credentials_fails():
    t = JiraTracker(FakeSubmitter, credentials=lambda: None)
    with pytest.raises(RuntimeError, match="credentials"):
        t.submit(make_draft(), None)


class _ShortSession:
    def get_job_output(self, job_id: str):
        return {"stdout": "out"}


def _source(submission_id: str):
    assert submission_id == "session:s1"
    return SimpleNamespace(checkbox_session=_ShortSession(), checkbox_submission=None)


def test_submit_maps_every_bugit_field_and_job_context():
    sub = FakeSubmitter()
    t = LaunchpadTracker(lambda: sub, report_source=_source)
    d = make_draft(logs=None)
    d = dataclasses.replace(
        d,
        severity="low",
        issue_file_time="after_reboot",
        platform_tags=["numbat"],
        impacted_features=["wifi"],
        impacted_vendors=["intel"],
        assignee="alice",
        status="Confirmed",
        series="noble",
        submission_id="session:s1",
        test_case="a::b",
    )
    t.submit(d, None)
    r = sub.report
    assert (r.severity, r.issue_file_time, r.status, r.series) == (
        "low",
        "after_reboot",
        "Confirmed",
        "noble",
    )
    assert list(r.platform_tags) == ["numbat"] and r.assignee == "alice"
    assert list(r.impacted_features) == ["wifi"] and list(r.impacted_vendors) == [
        "intel"
    ]
    # lets the submitter append [Job Output] exactly like the TUI
    assert isinstance(r.checkbox_session, _ShortSession) and r.job_id == "a::b"


def test_jira_gets_the_same_mapping():
    sub = FakeSubmitter()
    t = JiraTracker(lambda: sub, credentials=lambda: ("e", "t"), report_source=_source)
    d = make_draft(logs=None)
    d = dataclasses.replace(d, submission_id="session:s1", test_case="a::b")
    t.submit(d, None)
    assert sub.report.job_id == "a::b" and sub.report.severity == "highest"


def test_local_tracker_writes_report_and_logs(tmp_path: Path) -> None:
    from bugit_v2.mcp_server.trackers import LocalTracker

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "acpidump.log").write_text("hello")
    (logs / "snap_list.log").write_text("snaps")
    t = LocalTracker(tmp_path / "reports")
    res = t.submit(make_draft(), logs)
    assert res["tracker"] == "local"
    d = Path(res["path"])
    assert (d / "report.md").read_text().startswith("# Title")
    assert "Body" in (d / "report.md").read_text()
    assert sorted(f.name for f in d.glob("*.tar.xz")) == [
        "acpidump.tar.xz",
        "snap-list.tar.xz",
    ]
    with tarfile.open(d / "acpidump.tar.xz") as tar:
        assert tar.getnames() == ["acpidump.log"]
    assert res["id"] == d.name


def test_only_selected_logs_are_packed(tmp_path: Path) -> None:
    from bugit_v2.mcp_server.trackers import LocalTracker

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.txt").write_text("a")
    (logs / "b.txt").write_text("b")
    res = LocalTracker(tmp_path / "reports").submit(
        make_draft(attach_logs=["b.txt"]), logs
    )
    with tarfile.open(Path(res["path"]) / "other.tar.xz") as tar:
        assert tar.getnames() == ["b.txt"]


def test_long_job_output_is_attached_as_a_file():
    class Session:
        def get_job_output(self, job_id: str):
            return {"stdout": "x" * 5000, "stderr": "short", "comments": ""}

    sub = FakeSubmitter()
    sub.raw_uploads = []
    sub.upload_attachment = lambda path, filename=None: sub.raw_uploads.append(
        (filename, Path(path).read_text())
    )
    t = JiraTracker(
        lambda: sub,
        credentials=lambda: ("e", "t"),
        report_source=lambda _: SimpleNamespace(
            checkbox_session=Session(), checkbox_submission=None
        ),
    )
    t.submit(dataclasses.replace(make_draft(logs=None), submission_id="session:s1", test_case="a::b"), None)  # fmt: skip
    assert sub.raw_uploads == [("job_stdout.txt", "x" * 5000)]
