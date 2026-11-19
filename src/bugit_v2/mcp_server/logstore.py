"""Safe, redacted, read-only access to the files in a log collection directory"""

import gzip
import logging
import tarfile
from pathlib import Path
from typing import TypedDict

from bugit_v2.mcp_server.redact import Redactor

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 20_000
MAX_TAIL_LINES = 1000
DEFAULT_MAX_MATCHES = 200
_MAX_TAIL_BYTES = 8 * 1024 * 1024
_MAX_GUNZIP_BYTES = 1024 * 1024 * 1024
_TAR_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")


class FileInfo(TypedDict):
    name: str
    size: int


def _inside(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def expand_archives(job_dir: Path) -> None:
    """Unpack tarballs and single .gz files next to themselves.

    Best effort: a broken or malicious archive is skipped, never fatal.
    """
    for path in sorted(job_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        name = path.name
        try:
            suffix = next((s for s in _TAR_SUFFIXES if name.endswith(s)), None)
            if suffix:
                dest = path.with_name(name[: -len(suffix)])
                dest.mkdir(exist_ok=True)
                with tarfile.open(path) as tar:
                    tar.extractall(dest, filter="data")
            elif name.endswith(".gz"):
                with gzip.open(path, "rb") as src, open(path.with_suffix(""), "wb") as dst:
                    copied = 0
                    while chunk := src.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > _MAX_GUNZIP_BYTES:
                            break
                        dst.write(chunk)
        except (tarfile.TarError, OSError, EOFError) as e:
            logger.warning("Could not expand %s: %r", path, e)


class LogStore:
    def __init__(self, redactor: Redactor) -> None:
        self._redactor = redactor

    def redact(self, text: str) -> str:
        return self._redactor.redact(text)

    def resolve(self, job_dir: Path, filename: str) -> Path:
        rel = Path(filename) if filename else None
        if rel is None or rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Invalid filename: {filename!r}")
        path = job_dir / rel
        if not _inside(job_dir, path) or not path.is_file():
            raise ValueError(f"No such log file: {filename!r}")
        return path

    def list_files(self, job_dir: Path) -> list[FileInfo]:
        return [
            FileInfo(name=p.relative_to(job_dir).as_posix(), size=p.stat().st_size)
            for p in sorted(job_dir.rglob("*"))
            if p.is_file() and _inside(job_dir, p)
        ]

    def grep(
        self,
        job_dir: Path,
        filename: str,
        keyword: str,
        max_matches: int = DEFAULT_MAX_MATCHES,
    ) -> str:
        if not keyword:
            raise ValueError("keyword must not be empty")
        path = self.resolve(job_dir, filename)
        needle = keyword.lower()
        out: list[str] = []
        truncated = False
        with open(path, errors="replace") as f:
            for n, line in enumerate(f, 1):
                if needle in line.lower():
                    if len(out) >= max_matches:
                        truncated = True
                        break
                    out.append(f"{n}: {line.rstrip()}")
        if not out:
            return "No matches."
        text = self._redactor.redact("\n".join(out))
        if truncated:
            text += f"\n[truncated after {max_matches} matches; refine the keyword]"
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + "\n[truncated: output too large]"
        return text

    def tail(self, job_dir: Path, filename: str, lines: int) -> str:
        if lines < 1:
            raise ValueError("lines must be >= 1")
        lines = min(lines, MAX_TAIL_LINES)
        path = self.resolve(job_dir, filename)
        with open(path, "rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            data = b""
            while pos > 0 and data.count(b"\n") <= lines and len(data) < _MAX_TAIL_BYTES:
                step = min(64 * 1024, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
        text = data.decode(errors="replace")
        selected = "\n".join(text.splitlines()[-lines:])
        selected = self._redactor.redact(selected)
        if len(selected) > MAX_OUTPUT_CHARS:
            selected = "[truncated: output too large]\n" + selected[-MAX_OUTPUT_CHARS:]
        return selected
