import gzip
import os
import tarfile
from pathlib import Path

import pytest

from bugit_v2.mcp_server.logstore import LogStore, expand_archives
from bugit_v2.mcp_server.redact import Redactor


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    d = tmp_path / "job"
    (d / "sub").mkdir(parents=True)
    (d / "dmesg.log").write_text(
        "\n".join(
            [
                "boot ok",
                "ACPI Error: AE_NOT_FOUND",
                "nic aa:bb:cc:dd:ee:ff up 10.1.2.3",
                "Kernel panic - not syncing",
                "kernel panic again",
                "last line",
            ]
        )
        + "\n"
    )
    (d / "sub" / "x.log").write_text("hello alice\n")
    return d


@pytest.fixture
def store() -> LogStore:
    return LogStore(Redactor("alice"))


def test_list_files_is_relative_and_sorted(store: LogStore, job_dir: Path):
    files = store.list_files(job_dir)
    assert [f["name"] for f in files] == ["dmesg.log", "sub/x.log"]
    assert files[0]["size"] == (job_dir / "dmesg.log").stat().st_size


def test_grep_is_case_insensitive_literal_and_redacted(store: LogStore, job_dir: Path):
    out = store.grep(job_dir, "dmesg.log", "kernel panic")
    assert out.splitlines() == [
        "4: Kernel panic - not syncing",
        "5: kernel panic again",
    ]
    out = store.grep(job_dir, "dmesg.log", "nic")
    assert "aa:bb" not in out and "10.1.2.3" not in out
    assert "<MAC>" in out and "<IPv4>" in out


def test_grep_keyword_is_not_a_regex(store: LogStore, job_dir: Path):
    assert store.grep(job_dir, "dmesg.log", ".*") == "No matches."


def test_grep_no_match_and_cap(store: LogStore, tmp_path: Path):
    d = tmp_path / "big"
    d.mkdir()
    (d / "a.log").write_text("err\n" * 1000)
    assert store.grep(d, "a.log", "zzz") == "No matches."
    out = store.grep(d, "a.log", "err", max_matches=10)
    assert len(out.splitlines()) == 11
    assert "truncated" in out.splitlines()[-1]


def test_grep_empty_keyword_rejected(store: LogStore, job_dir: Path):
    with pytest.raises(ValueError):
        store.grep(job_dir, "dmesg.log", "")


def test_tail_returns_last_lines_redacted(store: LogStore, job_dir: Path):
    assert store.tail(job_dir, "dmesg.log", 2) == "kernel panic again\nlast line"
    assert "<USER>" in store.tail(job_dir, "sub/x.log", 5)


def test_tail_clamps_lines(store: LogStore, tmp_path: Path):
    d = tmp_path / "t"
    d.mkdir()
    (d / "a.log").write_text("".join(f"l{i}\n" for i in range(5000)))
    out = store.tail(d, "a.log", 10**6)
    assert len(out.splitlines()) == 1000
    assert out.splitlines()[-1] == "l4999"
    with pytest.raises(ValueError):
        store.tail(d, "a.log", 0)


def test_tail_whole_small_file(store: LogStore, job_dir: Path):
    assert len(store.tail(job_dir, "dmesg.log", 100).splitlines()) == 6


@pytest.mark.parametrize(
    "bad",
    ["../secret", "/etc/passwd", "sub/../../x", "nope.log", "sub", ""],
)
def test_path_escape_and_missing_rejected(store: LogStore, job_dir: Path, bad: str):
    with pytest.raises(ValueError):
        store.tail(job_dir, bad, 5)
    with pytest.raises(ValueError):
        store.grep(job_dir, bad, "x")


def test_symlink_escape_rejected(store: LogStore, job_dir: Path, tmp_path: Path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    os.symlink(secret, job_dir / "link.log")
    with pytest.raises(ValueError):
        store.tail(job_dir, "link.log", 5)
    assert "link.log" not in [f["name"] for f in store.list_files(job_dir)]


def test_output_is_size_capped(store: LogStore, tmp_path: Path):
    d = tmp_path / "c"
    d.mkdir()
    (d / "a.log").write_text(("x" * 500 + "\n") * 500)
    out = store.tail(d, "a.log", 500)
    assert len(out) <= 20_500
    assert "truncated" in out


def test_binary_garbage_does_not_crash(store: LogStore, tmp_path: Path):
    d = tmp_path / "b"
    d.mkdir()
    (d / "a.bin").write_bytes(b"\xff\xfe\x00error\n")
    assert "error" in store.grep(d, "a.bin", "error")


def test_expand_archives_extracts_tar_and_gz(tmp_path: Path):
    d = tmp_path / "j"
    d.mkdir()
    inner = tmp_path / "inner.txt"
    inner.write_text("inside tar")
    with tarfile.open(d / "checkbox_session.tar.gz", "w:gz") as t:
        t.add(inner, arcname="data/inner.txt")
    with gzip.open(d / "nvidia-bug-report.log.gz", "wb") as g:
        g.write(b"nvidia text")
    expand_archives(d)
    assert (d / "checkbox_session" / "data" / "inner.txt").read_text() == "inside tar"
    assert (d / "nvidia-bug-report.log").read_bytes() == b"nvidia text"


def test_expand_archives_blocks_traversal(tmp_path: Path):
    d = tmp_path / "j"
    d.mkdir()
    evil = tmp_path / "evil.txt"
    evil.write_text("x")
    with tarfile.open(d / "evil.tar.gz", "w:gz") as t:
        t.add(evil, arcname="../../escaped.txt")
    expand_archives(d)
    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()
