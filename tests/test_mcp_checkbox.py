import base64
import gzip
import json
import os
import tarfile
import time
from pathlib import Path

import pytest

from bugit_v2.mcp_server.checkbox import CheckboxService


def make_session(root: Path, name: str, results: dict[str, str], mtime: float | None = None):
    d = root / name
    (d / "io-logs").mkdir(parents=True)
    app_blob = base64.b64encode(json.dumps({"testplan_id": "tp::full"}).encode()).decode()
    body = {
        "session": {
            "metadata": {"app_blob": app_blob},
            "results": {
                job: [
                    {
                        "outcome": outcome,
                        "io_log_filename": f"io-logs/{i}.record.gz",
                        "comments": "cmt" if outcome == "fail" else None,
                    }
                ]
                for i, (job, outcome) in enumerate(results.items())
            },
        }
    }
    with gzip.open(d / "session", "wt") as f:
        json.dump(body, f)
    for i, job in enumerate(results):
        (d / "io-logs" / f"{i}.stdout").write_text(f"out of {job}")
        (d / "io-logs" / f"{i}.stderr").write_text(f"err of {job}")
    if mtime:
        os.utime(d, (mtime, mtime))
        os.utime(d / "session", (mtime, mtime))
    return d


def make_submission(directory: Path, name: str, results: dict[str, str]):
    directory.mkdir(parents=True, exist_ok=True)
    sub = {
        "testplan_id": "tp::sub",
        "results": [
            {
                "category": "c",
                "category_id": "cid",
                "certification_status": "blocker",
                "comments": None,
                "full_id": job,
                "name": job,
                "outcome": outcome,
                "project": "p",
                "status": "done",
                "io_log": f"log of {job}",
            }
            for job, outcome in results.items()
        ],
    }
    j = directory / "submission.json"
    j.write_text(json.dumps(sub))
    p = directory / name
    with tarfile.open(p, "w:xz") as t:
        t.add(j, arcname="submission.json")
    j.unlink()
    return p


@pytest.fixture
def svc(tmp_path: Path) -> CheckboxService:
    return CheckboxService(tmp_path / "sessions", [tmp_path / "subs"])


def test_list_empty_when_nothing_exists(svc: CheckboxService):
    assert svc.list_submissions() == []


def test_list_sessions_and_submissions_newest_first(svc: CheckboxService, tmp_path: Path):
    now = time.time()
    make_session(tmp_path / "sessions", "old", {"a": "pass"}, now - 1000)
    make_session(tmp_path / "sessions", "new", {"a": "fail"}, now - 10)
    sub = make_submission(tmp_path / "subs", "sub.tar.xz", {"b": "fail"})
    os.utime(sub, (now - 500, now - 500))

    items = svc.list_submissions()
    assert [i["submission_id"] for i in items] == [
        "session:new",
        f"submission:{sub}",
        "session:old",
    ]
    assert items[0]["kind"] == "session"
    assert items[0]["testplan_id"] == "tp::full"
    assert items[1]["kind"] == "submission"
    assert items[1]["testplan_id"] == "tp::sub"
    assert "modified" in items[0]


def test_list_respects_limit_and_skips_broken(svc: CheckboxService, tmp_path: Path):
    for i in range(5):
        make_session(tmp_path / "sessions", f"s{i}", {"a": "fail"})
    (tmp_path / "sessions" / "broken" / "io-logs").mkdir(parents=True)
    (tmp_path / "subs").mkdir()
    (tmp_path / "subs" / "junk.tar.xz").write_text("not a tar")
    items = svc.list_submissions(limit=3)
    assert len(items) == 3
    assert all("broken" not in i["submission_id"] for i in svc.list_submissions())
    assert all("junk" not in i["submission_id"] for i in svc.list_submissions())


def test_failed_cases_from_session(svc: CheckboxService, tmp_path: Path):
    make_session(
        tmp_path / "sessions",
        "s",
        {"audio/play": "pass", "wifi/connect": "fail", "gpu/crash": "crash", "x": "skip"},
    )
    cases = svc.get_failed_test_cases("session:s")
    assert {c["test_id"] for c in cases} == {"wifi/connect", "gpu/crash"}
    wifi = next(c for c in cases if c["test_id"] == "wifi/connect")
    assert wifi["outcome"] == "fail"
    assert wifi["comments"] == "cmt"
    assert "failed_at" in wifi


def test_list_failed_tests_carries_testplan_and_time(svc: CheckboxService, tmp_path: Path):
    make_session(tmp_path / "sessions", "a", {"wifi/connect": "fail", "ok": "pass"})
    p = make_submission(tmp_path / "subs", "s.tar.xz", {"bad": "fail"})
    rows = svc.list_failed_tests()
    assert {r["test_id"] for r in rows} == {"wifi/connect", "bad"}
    by_id = {r["test_id"]: r for r in rows}
    assert by_id["bad"]["submission_id"] == f"submission:{p}"
    for r in rows:
        assert r["testplan_id"] and r["modified"] and r["failed_at"]


def test_failed_cases_from_submission(svc: CheckboxService, tmp_path: Path):
    p = make_submission(tmp_path / "subs", "s.tar.xz", {"ok": "pass", "bad": "fail"})
    cases = svc.get_failed_test_cases(f"submission:{p}")
    assert [c["test_id"] for c in cases] == ["bad"]
    assert cases[0]["certification_status"] == "blocker"


def test_job_output_for_session_and_submission(svc: CheckboxService, tmp_path: Path):
    make_session(tmp_path / "sessions", "s", {"wifi/connect": "fail"})
    out = svc.get_job_output("session:s", "wifi/connect")
    assert out == {"stdout": "out of wifi/connect", "stderr": "err of wifi/connect", "comments": "cmt"}
    p = make_submission(tmp_path / "subs", "s.tar.xz", {"bad": "fail"})
    out = svc.get_job_output(f"submission:{p}", "bad")
    assert out is not None and out["stdout"] == "log of bad"
    assert svc.get_job_output("session:s", "missing") is None


@pytest.mark.parametrize(
    "bad",
    [
        "session:../etc",
        "session:",
        "session:nope",
        "submission:/etc/passwd",
        "submission:/tmp/x.tar.xz",
        "garbage",
        "",
    ],
)
def test_bad_submission_ids_rejected(svc: CheckboxService, bad: str):
    with pytest.raises(ValueError):
        svc.get_failed_test_cases(bad)


def test_describe_and_collector_input(svc: CheckboxService, tmp_path: Path):
    make_session(tmp_path / "sessions", "s", {"a": "fail"})
    d = svc.describe("session:s")
    assert d["kind"] == "session" and "failed_at" in d
    inp = svc.collector_input("session:s")
    assert inp.checkbox_session is not None and inp.checkbox_submission is None

    p = make_submission(tmp_path / "subs", "s.tar.xz", {"b": "fail"})
    assert svc.describe(f"submission:{p}")["kind"] == "submission"
    inp = svc.collector_input(f"submission:{p}")
    assert inp.checkbox_submission is not None and inp.checkbox_session is None
    with pytest.raises(ValueError):
        svc.describe("session:../x")
