"""Render a review-oriented PR description from committed change files."""

from pathlib import Path

from .git import Commit, Git, GitError
from .markdown import Doc, parse
from .validation import change_items
from .zones import Zones


def section_body(doc: Doc, title: str) -> str:
    section = next((s for s in doc.sections if s.level == 2 and s.title == title), None)
    return "".join(doc.lines[section.start + 1:section.end]) if section else ""


def pr_body(root: Path, rev_range: str, notes: Path | None = None) -> str:
    git = Git(root)
    groups: dict[str, list[Commit]] = {}
    others = []
    for commit in git.commits(rev_range):
        if commit.change_name is None:
            others.append(commit)
        else:
            groups.setdefault(commit.change_name, []).append(commit)
    blocks = []
    for name, commits in groups.items():
        relative = f"changes/archive/{name}.md"
        text = git.read("HEAD", relative)
        state = "DONE"
        if text is None:
            relative = f"changes/{name}.md"
            text = git.read("HEAD", relative)
            state = "BLOCKED"
        if text is None:
            raise GitError(f"change file for {name} is missing at HEAD")
        change = parse(root / relative, text)
        block = f"## {name} ({change.fm['module']}) — {state}\n\n"
        if state == "BLOCKED":
            block += f"**Blocked reason:** {change.fm.get('blocked', 'none')}\n\n"
        paths: dict[str, set[str]] = {}
        for commit in commits:
            zones = Zones(root, commit.sha)
            for edit in commit.files:
                for path in edit.paths:
                    paths.setdefault(zones.classify(path), set()).add(path)
        block += "**Read carefully**\n"
        for title, names in (
            ("Evals changed", paths.get("evals", set()) | paths.get("system-evals", set())),
            ("Interface changed", paths.get("interface", set())),
            ("Preserved files changed", paths.get("preserved", set())),
            ("Review-tagged specs added/modified", {item.id for section in ("Added", "Modified")
                                                   for item in change_items(change, section) if item.tag == "review"}),
        ):
            if names:
                block += f"- {title}: " + ", ".join(sorted(names)) + "\n"
        decisions = section_body(change, "Decisions")
        block += "- Decisions recorded by the agent:\n"
        if decisions.strip():
            block += decisions
            if not decisions.endswith("\n"):
                block += "\n"
        else:
            block += "  none\n"
        if not block.endswith("\n\n"):
            block += "\n"
        block += "**Skim**\n"
        implementation = sorted(paths.get("implementation", set()))
        if implementation:
            block += f"- Implementation files changed: {len(implementation)} (" + ", ".join(implementation[:10]) + ")\n"
        other_paths = set().union(*(names for zone, names in paths.items()
                                   if zone not in {"evals", "system-evals", "interface", "preserved", "implementation"}))
        if other_paths:
            block += "- Other files: " + ", ".join(sorted(other_paths)) + "\n"
        blocks.append(block)
    if others:
        blocks.append("## Other commits\n\n" + "".join(f"- {c.sha[:7]} {c.subject}\n" for c in others))
    if notes is not None:
        blocks.append("## Night log\n\n" + notes.read_bytes().decode("utf-8"))
    return "\n".join(blocks)
