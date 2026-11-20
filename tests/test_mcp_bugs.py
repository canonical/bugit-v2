from pathlib import Path
from typing import Any

import pytest

from bugit_v2.mcp_server.bugs import BugService, Draft


class FakeTracker:
    def __init__(self) -> None:
        self.submitted: list[tuple[Draft, Path | None]] = []

    def submit(self, draft: Draft, logs_dir: Path | None) -> dict[str, Any]:
        self.submitted.append((draft, logs_dir))
        return {"url": "http://x/2", "id": "2"}


@pytest.fixture
def parts(tmp_path: Path):
    lp, jira = FakeTracker(), FakeTracker()
    logs = tmp_path / "job1"
    logs.mkdir()

    def job_dir(job_id: str) -> Path:
        if job_id != "job1":
            raise ValueError("Unknown job_id")
        return logs

    clock = {"t": 1000.0}
    svc = BugService(
        {"launchpad": lp, "jira": jira},
        job_dir=job_dir,
        redact=lambda s: s.replace("alice", "<USER>"),
        default_tracker="launchpad",
        default_project="oem-priority",
        draft_ttl=60,
        now=lambda: clock["t"],
    )
    return svc, lp, jira, logs, clock


def test_draft_with_unknown_tracker_is_rejected(parts):
    svc, *_ = parts
    with pytest.raises(ValueError, match="tracker"):
        svc.draft_bug("Title", "Body", [], None, tracker="bugzilla")


def test_draft_does_not_submit(parts):
    svc, lp, _, logs, _ = parts
    draft_id = svc.draft_bug("Title", "Body by alice", ["wifi"], "job1")
    assert isinstance(draft_id, str) and draft_id
    assert lp.submitted == []
    d = svc.get_draft(draft_id)
    assert d.description == "Body by <USER>"
    assert d.tags == ["wifi"] and d.project == "oem-priority" and d.tracker == "launchpad"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": "", "description": "d", "tags": [], "logs_job_id": "job1"},
        {"title": "t" * 201, "description": "d", "tags": [], "logs_job_id": "job1"},
        {"title": "t", "description": " ", "tags": [], "logs_job_id": "job1"},
        {"title": "t", "description": "d", "tags": [""], "logs_job_id": "job1"},
        {"title": "t", "description": "d", "tags": ["has space"], "logs_job_id": "job1"},
        {"title": "t", "description": "d", "tags": [], "logs_job_id": "nope"},
    ],
)
def test_draft_validation(parts, kwargs):
    svc, *_ = parts
    with pytest.raises(ValueError):
        svc.draft_bug(**kwargs)


def test_draft_without_logs_job_allowed(parts):
    svc, *_ = parts
    assert svc.draft_bug("t", "d", [], None)


def test_confirm_submits_once_with_logs(parts):
    svc, lp, _, logs, _ = parts
    (logs / "dmesg.log").write_text("x")
    draft_id = svc.draft_bug("T", "D", ["a"], "job1", attach_logs=["dmesg.log"])
    result = svc.confirm_submission(draft_id)
    assert result["url"] == "http://x/2"
    assert lp.submitted[0][0].title == "T" and lp.submitted[0][1] == logs
    with pytest.raises(ValueError, match="draft"):
        svc.confirm_submission(draft_id)


def test_confirm_unknown_draft(parts):
    svc, lp, *_ = parts
    with pytest.raises(ValueError, match="draft"):
        svc.confirm_submission("made-up")
    assert lp.submitted == []


def test_expired_draft(parts):
    svc, lp, _, _, clock = parts
    draft_id = svc.draft_bug("T", "D", [], None)
    clock["t"] += 61
    with pytest.raises(ValueError, match="expired|draft"):
        svc.confirm_submission(draft_id)
    assert lp.submitted == []


def test_failed_submission_keeps_draft_for_retry(parts):
    svc, lp, *_ = parts
    calls = {"n": 0}
    orig = lp.submit

    def flaky(draft, logs_dir):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network down")
        return orig(draft, logs_dir)

    lp.submit = flaky
    draft_id = svc.draft_bug("T", "D", [], None)
    with pytest.raises(RuntimeError):
        svc.confirm_submission(draft_id)
    assert svc.confirm_submission(draft_id)["id"] == "2"


def _draft(svc, **kw):
    return svc.get_draft(svc.draft_bug("T", "D", [], None, **kw))


def test_draft_defaults_match_bugit_editor(parts):
    d = _draft(parts[0])
    assert (d.severity, d.issue_file_time, d.status) == ("highest", "immediate", "New")
    assert d.platform_tags == [] and d.assignee is None and d.series is None


def test_draft_carries_bugit_fields_and_redacts_free_text(parts):
    d = _draft(
        parts[0],
        severity="low",
        issue_file_time="after_reboot",
        platform_tags=["numbat-hello"],
        impacted_features=["wifi"],
        impacted_vendors=["intel"],
        assignee="alice",
        status="Confirmed",
        series="noble",
        submission_id="session:s",
        test_case="a::b",
    )
    assert (d.severity, d.issue_file_time, d.status, d.series) == (
        "low", "after_reboot", "Confirmed", "noble",
    )
    assert d.platform_tags == ["numbat-hello"] and d.impacted_vendors == ["intel"]
    assert d.assignee == "alice"  # identity, not free text: must stay exact
    assert (d.submission_id, d.test_case) == ("session:s", "a::b")


@pytest.mark.parametrize(
    "kw",
    [
        {"severity": "critical"},
        {"issue_file_time": "yesterday"},
        {"status": "Fixed"},
        {"platform_tags": ["has space"]},
        {"impacted_features": ["bad;tag"]},
        {"assignee": "no spaces allowed"},
    ],
)
def test_draft_rejects_invalid_bugit_fields(parts, kw):
    with pytest.raises(ValueError):
        _draft(parts[0], **kw)


def test_saved_dut_info_is_found_across_users(tmp_path, monkeypatch):
    import bugit_v2.models.dut_info as di

    f = tmp_path / "home/ubuntu/snap/bugit/42" / di.DUT_INFO_DIR.name / "dut_info.json"
    f.parent.mkdir(parents=True)
    f.write_text(di.DutInfo(cid="202401-12345", project="p").model_dump_json())
    monkeypatch.setattr(di, "DUT_INFO_DIR", tmp_path / "nothing" / di.DUT_INFO_DIR.name)
    monkeypatch.setattr(di, "HOST_FS", tmp_path)
    assert di.get_saved_dut_info() is None
    info = di.get_saved_dut_info(search_other_users=True)
    assert info and info.cid == "202401-12345" and info.project == "p"


def test_logs_not_attached_unless_selected(parts):
    svc, lp, _, logs, _ = parts
    (logs / "dmesg.log").write_text("x")
    (logs / "sub").mkdir()
    (logs / "sub" / "a.log").write_text("y")
    d = svc.draft_bug("T", "D", [], "job1")
    assert svc.get_draft(d).attach_logs == []
    svc.confirm_submission(d)
    assert lp.submitted[-1][1] is None

    d = svc.draft_bug("T", "D", [], "job1")
    svc.update_draft(d, attach_logs=["sub/a.log", "sub/a.log"], severity="low")
    got = svc.get_draft(d)
    assert (got.attach_logs, got.severity) == (["sub/a.log"], "low")
    svc.update_draft(d, attach_logs=[])
    assert svc.get_draft(d).attach_logs == []


@pytest.mark.parametrize("name", ["missing.log", "../job1", "/etc/passwd", "sub"])
def test_attach_logs_rejects_bad_names(parts, name):
    svc, _, _, logs, _ = parts
    (logs / "sub").mkdir()
    with pytest.raises(ValueError, match="Cannot attach"):
        svc.draft_bug("T", "D", [], "job1", attach_logs=[name])


def test_attach_logs_needs_job(parts):
    with pytest.raises(ValueError, match="logs_job_id"):
        parts[0].draft_bug("T", "D", [], None, attach_logs=["a"])
