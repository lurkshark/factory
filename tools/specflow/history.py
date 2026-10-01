"""Enforce worker zones and append-only files over a commit range."""

from pathlib import Path

from .git import Git
from .markdown import Message
from .zones import Zones


def check_commits(root: Path, rev_range: str) -> list[Message]:
    git = Git(root)
    messages = []
    for commit in git.commits(rev_range):
        zones = Zones(root, commit.sha)
        parent_zones = None
        for change in commit.files:
            for path in change.paths:
                zone = zones.classify(path)
                if commit.change_name is not None:
                    if zone in {"tools", "playbooks"}:
                        messages.append(Message("H01", root / path,
                                                f"worker commit {commit.sha} touched {zone} path"))
                    if commit.subject.startswith("impl(") and zone in {"evals", "system-evals", "interface"}:
                        messages.append(Message("H02", root / path,
                                                f"implementation commit {commit.sha} touched {zone} path"))
                if change.status[0] in {"M", "D", "R"}:
                    if parent_zones is None:
                        parent_zones = [Zones(root, sha) for sha in git.parents(commit.sha)]
                    if zones.append_only(path) or any(parent.append_only(path) for parent in parent_zones):
                        messages.append(Message("H03", root / path,
                                                f"commit {commit.sha} {change.status} touched append-only file"))
    return messages
