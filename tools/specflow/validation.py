"""Per-file schemas and Markdown grammar; no filesystem mutations."""

from dataclasses import dataclass
from pathlib import PurePosixPath
import re

from .markdown import Doc, Message, Section, line_text

NAME = re.compile(r"[a-z][a-z0-9-]*")
PREFIX = re.compile(r"[A-Z][A-Z0-9]*")
SPEC_HEADING = re.compile(r"### (?P<id>[A-Z][A-Z0-9]*-\d{3,})(?: \[(?P<tag>blackbox|review|quarantine)\])?")
REMOVED_HEADING = re.compile(r"### (?P<id>[A-Z][A-Z0-9]*-\d{3,})")
ID = re.compile(r"[A-Z][A-Z0-9]*-\d{3,}")
CHANGE_NAME = re.compile(r"(?P<num>\d{4})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.md")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
KINDS = ("library", "service", "cli", "infra")
CHANGE_TITLES = {"Motivation", "Interface", "Added", "Modified", "Removed", "Decisions", "Rejected alternatives", "Notes"}


@dataclass(frozen=True)
class Item:
    id: str
    tag: str
    section: Section


def relative_path(value: str) -> bool:
    return bool(value) and not PurePosixPath(value).is_absolute() and "\\" not in value and all(
        part not in ("", ".", "..") for part in value.split("/")
    )


def fm_line(doc: Doc, key: str) -> int:
    return next((i + 1 for i in range(1, doc.fm_end - 1) if doc.lines[i].startswith(key + ":")), 2)


def flow_line(doc: Doc, key: str) -> bool:
    pattern = re.compile(r"^" + re.escape(key) + r":\s*\[.*\]\s*$")
    return sum(bool(pattern.fullmatch(line_text(line))) for line in doc.lines[1:doc.fm_end - 1]) == 1


def frontmatter(doc: Doc, kind: str) -> list[Message]:
    if kind == "change":
        required, allowed = {"module"}, {"module", "targets", "set_depends_on", "blocked"}
    elif kind == "system":
        required, allowed = {"name", "id_prefix"}, {"name", "id_prefix", "evals"}
    else:
        required = {"name", "kind", "id_prefix", "depends_on"}
        allowed = required | {"interface", "preserve", "append_only", "evals"}
    messages = []

    def error(key: str, message: str) -> None:
        messages.append(Message("C03", doc.path, message, fm_line(doc, key)))

    for key in sorted(required - doc.fm.keys()):
        error(key, f"missing required key {key}")
    for key in sorted(doc.fm.keys() - allowed, key=str):
        error(str(key), f"unknown frontmatter key {key}")
    string_keys = {"name", "kind", "id_prefix", "interface", "module", "blocked"}
    list_keys = {"depends_on", "preserve", "append_only", "targets", "set_depends_on"}
    for key, value in doc.fm.items():
        if key not in allowed:
            continue
        if key in string_keys and not isinstance(value, str):
            error(key, f"{key} must be a string")
            continue
        if key in list_keys:
            if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
                error(key, f"{key} must be a list of strings")
                continue
            if key in {"depends_on", "targets", "set_depends_on"} and not flow_line(doc, key):
                error(key, f"{key} must be a single flow-style line")
            if key in {"depends_on", "set_depends_on"} and len(value) != len(set(value)):
                error(key, f"{key} contains duplicates")
            if key == "targets" and any(not ID.fullmatch(x) for x in value):
                error(key, "targets must contain valid spec IDs")
            if key in {"preserve", "append_only"} and any(not relative_path(x) for x in value):
                error(key, f"{key} must contain relative POSIX globs")
        if key == "name":
            if kind == "system" and value != "system":
                error(key, "system name must be system")
            elif kind == "module" and (not NAME.fullmatch(value) or value == "system"):
                error(key, "invalid or reserved module name")
        elif key == "module" and not NAME.fullmatch(value):
            error(key, "invalid module name")
        elif key == "kind" and value not in KINDS:
            error(key, "kind must be library, service, cli, or infra")
        elif key == "id_prefix":
            if kind == "system" and value != "SYS":
                error(key, "system id_prefix must be SYS")
            elif kind == "module" and (not PREFIX.fullmatch(value) or value == "SYS"):
                error(key, "invalid or reserved id_prefix")
        elif key == "interface" and not relative_path(value):
            error(key, "interface must be a relative POSIX path")
        elif key == "evals":
            if not isinstance(value, dict) or set(value) != {"run", "report"}:
                error(key, "evals must have exactly run and report keys")
            elif any(not isinstance(value[x], str) or not value[x].strip() for x in value):
                error(key, "evals run and report must be non-empty strings")
            elif not relative_path(value["report"]):
                error(key, "evals report must be a relative POSIX path")
    if kind == "change" and doc.fm.get("module") == "system" and "set_depends_on" in doc.fm:
        error("set_depends_on", "set_depends_on is not allowed for system")
    return messages


def specs_section(doc: Doc) -> Section | None:
    return next((s for s in doc.sections if s.level == 2 and line_text(doc.lines[s.start]) == "## Specs"), None)


def items(doc: Doc, parent: Section) -> list[Item]:
    found = []
    for section in doc.sections:
        if parent.start < section.start < parent.end:
            match = SPEC_HEADING.fullmatch(line_text(doc.lines[section.start]))
            if match:
                found.append(Item(match["id"], match["tag"] or "blackbox", section))
    return found


def spec_items(doc: Doc) -> list[Item]:
    parent = specs_section(doc)
    return items(doc, parent) if parent else []


def item_grammar(doc: Doc, parent: Section, prefix: str | None, removed: bool = False) -> list[Message]:
    messages = []
    children = [s for s in doc.sections if parent.start < s.start < parent.end]
    first = children[0].start if children else parent.end
    if any(line.strip() for line in doc.lines[parent.start + 1:first]):
        messages.append(Message("C04", doc.path, "only blank lines are allowed before the first spec", parent.start + 2))
    for i, section in enumerate(children):
        match = (REMOVED_HEADING if removed else SPEC_HEADING).fullmatch(line_text(doc.lines[section.start]))
        if not match:
            messages.append(Message("C04", doc.path, "invalid spec heading", section.start + 1))
            continue
        if prefix and match["id"].split("-")[0] != prefix:
            messages.append(Message("C04", doc.path, f"{match['id']} must use prefix {prefix}", section.start + 1))
        # A spec body stops at ANY heading, even though generic Sections nest.
        end = children[i + 1].start if i + 1 < len(children) else parent.end
        if not removed and not any(line.strip() for line in doc.lines[section.start + 1:end]):
            messages.append(Message("C04", doc.path, "spec body must not be empty", section.start + 1))
    return messages


def validate_spec(doc: Doc, kind: str = "module") -> list[Message]:
    messages = frontmatter(doc, kind)
    parents = [s for s in doc.sections if s.level == 2 and line_text(doc.lines[s.start]) == "## Specs"]
    if len(parents) != 1:
        messages.append(Message("C04", doc.path, "exactly one ## Specs section is required"))
    for parent in parents:
        messages.extend(item_grammar(doc, parent, doc.fm.get("id_prefix")))
    seen = set()
    for item in spec_items(doc):
        if item.id in seen:
            messages.append(Message("C05", doc.path, f"duplicate spec ID {item.id}", item.section.start + 1))
        seen.add(item.id)
    return messages


def change_sections(doc: Doc) -> dict[str, Section]:
    return {s.title: s for s in doc.sections if s.level == 2}


def change_items(doc: Doc, title: str) -> list[Item]:
    parent = change_sections(doc).get(title)
    return items(doc, parent) if parent else []


def validate_change(doc: Doc, prefix: str | None) -> list[Message]:
    messages = frontmatter(doc, "change")
    parents = [s for s in doc.sections if s.level == 2]
    first = parents[0].start if parents else len(doc.lines)
    if any(line.strip() for line in doc.lines[doc.body_start:first]):
        messages.append(Message("C04", doc.path, "only blank lines allowed before change sections", doc.body_start + 1))
    seen_titles = set()
    for parent in parents:
        if parent.title not in CHANGE_TITLES or parent.title in seen_titles:
            messages.append(Message("C04", doc.path, "unknown or duplicate change section", parent.start + 1))
        seen_titles.add(parent.title)
        if parent.title in {"Added", "Modified", "Removed"}:
            messages.extend(item_grammar(doc, parent, prefix, parent.title == "Removed"))
    # Level 1 headings are never part of a change, including in free-prose sections.
    for section in doc.sections:
        if section.level == 1:
            messages.append(Message("C04", doc.path, "level 1 headings are not allowed", section.start + 1))
    motivation = change_sections(doc).get("Motivation")
    body = "".join(doc.lines[motivation.start + 1:motivation.end]).strip() if motivation else ""
    if not body or body == "TODO: why this change is needed.":
        messages.append(Message("C04", doc.path, "Motivation must contain finished, non-empty text", motivation.start + 1 if motivation else 0))
    seen_ids = set()
    referenced = [(item.id, item.section.start + 1) for title in ("Added", "Modified", "Removed") for item in change_items(doc, title)]
    targets = doc.fm.get("targets", [])
    if isinstance(targets, list):
        referenced.extend((target, fm_line(doc, "targets")) for target in targets if isinstance(target, str))
    for spec_id, line in referenced:
        if spec_id in seen_ids:
            messages.append(Message("C05", doc.path, f"duplicate change ID {spec_id}", line))
        seen_ids.add(spec_id)
        if prefix and spec_id.split("-")[0] != prefix:
            messages.append(Message("C04", doc.path, f"{spec_id} must use prefix {prefix}", line))
    return [Message("C09", m.path, m.message, m.line) for m in messages]
