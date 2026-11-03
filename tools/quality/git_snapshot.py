"""Read immutable Git objects without touching the index or working tree."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType


@dataclass(frozen=True)
class Entry:
    path: str
    oid: str
    kind: str
    size: int


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    ).stdout


def revision(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()


def entries(repo: Path, ref: str) -> list[Entry]:
    result = []
    for row in git(repo, "ls-tree", "-r", "-l", "-z", ref).split(b"\0"):
        if not row:
            continue
        header, name = row.split(b"\t", 1)
        _, kind, oid, size = header.split()
        result.append(
            Entry(
                name.decode("utf-8"),
                oid.decode(),
                kind.decode(),
                int(size) if size != b"-" else 0,
            )
        )
    return result


class BlobReader:
    """Stream objects to keep model/media bytes out of the Python heap."""

    def __init__(self, repo: Path):
        self.process = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def __enter__(self) -> "BlobReader":
        return self

    def read(self, oid: str) -> bytes:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("Git object stream is unavailable")
        self.process.stdin.write(oid.encode("ascii") + b"\n")
        self.process.stdin.flush()
        header = self.process.stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError(f"Expected blob for {oid}: {header!r}")
        size = int(header[2])
        data = self.process.stdout.read(size)
        if len(data) != size or self.process.stdout.read(1) != b"\n":
            raise ValueError(f"Truncated Git object: {oid}")
        return data

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            code = self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise
        finally:
            if self.process.stdout is not None:
                self.process.stdout.close()
        if code and exc_type is None:
            raise RuntimeError(f"git cat-file failed with exit code {code}")
