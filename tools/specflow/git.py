"""Read-only Git access shared by history checks and PR descriptions."""

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

CHANGE_TRAILER = re.compile(r"^Change: (\d{4}-[a-z0-9-]+)$", re.MULTILINE)


class GitError(Exception):
    pass


@dataclass(frozen=True)
class FileChange:
    status: str
    path: str
    old_path: str | None = None

    @property
    def paths(self) -> tuple[str, ...]:
        return (self.old_path, self.path) if self.old_path is not None else (self.path,)


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str
    files: list[FileChange]

    @property
    def change_name(self) -> str | None:
        match = CHANGE_TRAILER.search(self.body)
        return match[1] if match else None


class Git:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def run(self, *args: str) -> str:
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True)
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip().replace("\n", " ")
            raise GitError(detail or f"git {args[0]} failed")
        return result.stdout.decode("utf-8")

    def read(self, revision: str, path: str) -> str | None:
        # Missing files are expected for deleted/new modules and pending changes.
        result = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=self.root, capture_output=True)
        return result.stdout.decode("utf-8") if result.returncode == 0 else None

    def parents(self, sha: str) -> list[str]:
        return self.run("rev-list", "--parents", "-n", "1", sha).strip().split()[1:]

    def changed_files(self, sha: str) -> list[FileChange]:
        raw = self.run("diff-tree", "--root", "--no-commit-id", "--name-status", "-r", "-M", "-z", sha)
        fields = raw.split("\0")
        changes = []
        i = 0
        while i < len(fields) - 1:
            status, path = fields[i:i + 2]
            i += 2
            old_path = None
            if status.startswith(("R", "C")):
                old_path, path = path, fields[i]
                i += 1
            changes.append(FileChange(status, path, old_path))
        return changes

    def commits(self, rev_range: str) -> list[Commit]:
        if not rev_range or rev_range.startswith("-"):
            raise GitError("invalid revision range")
        raw = self.run("log", "--reverse", "-z", "--format=%H%x00%s%x00%b", rev_range, "--")
        fields = raw.split("\0")
        return [Commit(sha, subject, body, self.changed_files(sha))
                for sha, subject, body in zip(fields[0:-1:3], fields[1:-1:3], fields[2:-1:3])]
