import dataclasses
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bugit_v2.mcp_server.bugs import Draft
from bugit_v2.mcp_server.trackers import JiraTracker, LaunchpadTracker


def make_draft(
    project: str | None = "PROJ", logs: str | None = "j", attach_logs: list[str] | None = None
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
    (d / "dmesg.log").write_text("hello")
    return d


def test_launchpad_search_maps_tasks():
    seen: dict[str, Any] = {}

    def searchTasks(**kw):
        seen.update(kw)
        return [
            SimpleNamespace(
                bug=SimpleNamespace(id=42, title="Wifi dead"),
                status="New",
                web_link="https://lp/42",
            )
        ] * 3

    client = SimpleNamespace(
        projects={"oem": SimpleNamespace(searchTasks=searchTasks)},
        distributions={"ubuntu": SimpleNamespace(searchTasks=searchTasks)},
    )
    t = LaunchpadTracker(lambda: client, FakeSubmitter, default_project="oem")
    res = t.search("wifi", limit=2)
    assert seen["search_text"] == "wifi"
    assert res == [
        {"id": "42", "title": "Wifi dead", "status": "New", "url": "https://lp/42"}
    ] * 2


def test_launchpad_search_falls_back_to_ubuntu_distro():
    called = []
    client = SimpleNamespace(
        projects={},
        distributions={
            "ubuntu": SimpleNamespace(searchTasks=lambda **kw: called.append(kw) or [])
        },
    )
    assert LaunchpadTracker(lambda: client, FakeSubmitter).search("x") == []
    assert called


def test_launchpad_submit_builds_report_and_uploads_logs(logs_dir: Path):
    sub = FakeSubmitter()
    t = LaunchpadTracker(lambda: None, lambda: sub)
    out = t.submit(make_draft(), logs_dir)
    assert out == {"url": "https://bugs.example/123", "id": "123", "tracker": "launchpad"}
    r = sub.report
    assert (r.title, r.description, r.project) == ("Title", "Body", "PROJ")
    assert list(r.additional_tags) == ["wifi", "regression"]
    assert r.checkbox_session is None and r.job_id is None
    assert sub.uploads == [("logs.tar.gz", ["dmesg.log"])]
    assert sub.finalized


def test_launchpad_submit_without_logs_or_project(logs_dir: Path):
    sub = FakeSubmitter()
    t = LaunchpadTracker(lambda: None, lambda: sub)
    with pytest.raises(ValueError, match="project"):
        t.submit(make_draft(project=None), None)
    t.submit(make_draft(), None)
    assert sub.uploads == []


def test_jira_search_builds_escaped_jql():
    captured = {}

    class Client:
        def search_issues(self, jql, maxResults):
            captured["jql"] = jql
            captured["max"] = maxResults
            return [SimpleNamespace(key="STELLA-1", fields=SimpleNamespace(
                summary="Boom", status=SimpleNamespace(name="Open")))]

    t = JiraTracker(lambda: Client(), FakeSubmitter, "https://jira.example", default_project="STELLA")
    res = t.search('say "hi" \\ there', limit=5)
    assert res == [
        {
            "id": "STELLA-1",
            "title": "Boom",
            "status": "Open",
            "url": "https://jira.example/browse/STELLA-1",
        }
    ]
    assert captured["jql"].startswith('project = "STELLA" AND text ~ "say \\"hi\\" \\\\ there"')
    assert captured["max"] == 5


def test_jira_submit_sets_credentials(logs_dir: Path):
    sub = FakeSubmitter()
    t = JiraTracker(lambda: None, lambda: sub, "https://j", credentials=lambda: ("e@x.io", "tok"))
    out = t.submit(make_draft(), logs_dir)
    assert sub.auth.email == "e@x.io" and sub.auth.token == "tok"
    assert out["url"] == "https://bugs.example/123"


def test_jira_submit_without_credentials_fails():
    t = JiraTracker(lambda: None, FakeSubmitter, "https://j", credentials=lambda: None)
    with pytest.raises(RuntimeError, match="credentials"):
        t.submit(make_draft(), None)


def _source(submission_id: str):
    assert submission_id == "session:s1"
    return SimpleNamespace(checkbox_session="SESSION", checkbox_submission=None)


def test_submit_maps_every_bugit_field_and_job_context():
    sub = FakeSubmitter()
    t = LaunchpadTracker(lambda: None, lambda: sub, report_source=_source)
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
        "low", "after_reboot", "Confirmed", "noble",
    )
    assert list(r.platform_tags) == ["numbat"] and r.assignee == "alice"
    assert list(r.impacted_features) == ["wifi"] and list(r.impacted_vendors) == ["intel"]
    # lets the submitter append [Job Output] exactly like the TUI
    assert r.checkbox_session == "SESSION" and r.job_id == "a::b"


def test_jira_gets_the_same_mapping():
    sub = FakeSubmitter()
    t = JiraTracker(lambda: None, lambda: sub, "https://j", credentials=lambda: ("e", "t"), report_source=_source)
    d = make_draft(logs=None)
    d = dataclasses.replace(d, submission_id="session:s1", test_case="a::b")
    t.submit(d, None)
    assert sub.report.job_id == "a::b" and sub.report.severity == "highest"


def test_local_tracker_writes_report_and_logs(tmp_path: Path) -> None:
    from bugit_v2.mcp_server.trackers import LocalTracker

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.txt").write_text("hello")
    t = LocalTracker(tmp_path / "reports")
    res = t.submit(make_draft(), logs)
    assert res["tracker"] == "local"
    d = Path(res["path"])
    assert (d / "report.md").read_text().startswith("# Title")
    assert "Body" in (d / "report.md").read_text()
    with tarfile.open(d / "logs.tar.gz") as tar:
        assert tar.getnames() == ["a.txt"]
    assert res["id"] == d.name


def test_local_tracker_search_matches_saved_reports(tmp_path: Path) -> None:
    from bugit_v2.mcp_server.trackers import LocalTracker

    t = LocalTracker(tmp_path / "reports")
    assert t.search("Title") == []
    t.submit(make_draft(), None)
    hits = t.search("title")
    assert len(hits) == 1 and hits[0]["title"] == "Title"
    assert t.search("nomatch") == []


def test_only_selected_logs_are_packed(tmp_path: Path) -> None:
    from bugit_v2.mcp_server.trackers import LocalTracker

    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.txt").write_text("a")
    (logs / "b.txt").write_text("b")
    res = LocalTracker(tmp_path / "reports").submit(make_draft(attach_logs=["b.txt"]), logs)
    with tarfile.open(Path(res["path"]) / "logs.tar.gz") as tar:
        assert tar.getnames() == ["b.txt"]
