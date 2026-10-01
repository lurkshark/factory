"""Apply changes in memory, validating before any caller writes files."""

from .markdown import Doc, InvalidDocument, Message, line_text, parse
from .validation import change_items, flow_line, spec_items, specs_section, validate_change, validate_spec


def normalized_section(change: Doc, item) -> list[str]:
    lines = [line_text(line) + "\n" for line in change.lines[item.section.start:item.section.end]]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines + ["\n"]


def apply_change(spec: Doc, change: Doc) -> Doc:
    kind = "system" if change.fm.get("module") == "system" else "module"
    messages = validate_change(change, spec.fm.get("id_prefix"))
    # A04 must be reported for a non-flow depends_on in the input, even though
    # ordinary per-file validation would also reject it as C03.
    if messages:
        raise InvalidDocument(messages)
    current = spec
    errors = []
    for title, code in (("Removed", "A01"), ("Modified", "A02"), ("Added", "A03")):
        for item in change_items(change, title):
            existing = next((x for x in spec_items(current) if x.id == item.id), None)
            if title != "Added" and existing is None:
                verb = "remove" if title == "Removed" else "modify"
                errors.append(Message(code, change.path, f"cannot {verb} {item.id}: not found", item.section.start + 1))
                continue
            if title == "Added" and existing is not None:
                errors.append(Message(code, change.path, f"cannot add {item.id}: already exists", item.section.start + 1))
                continue
            lines = current.lines.copy()
            if title == "Removed":
                del lines[existing.section.start:existing.section.end]
            elif title == "Modified":
                lines[existing.section.start:existing.section.end] = normalized_section(change, item)
            else:
                parent = specs_section(current)
                if parent is None:
                    raise InvalidDocument(validate_spec(current, kind))
                inserted = normalized_section(change, item)
                if parent.end and lines[parent.end - 1].strip():
                    # At EOF the previous line might have no newline at all.
                    separator = "\n" if lines[parent.end - 1].endswith(("\n", "\r")) else "\n\n"
                    inserted.insert(0, separator)
                lines[parent.end:parent.end] = inserted
            current = parse(spec.path, "".join(lines))
    if "set_depends_on" in change.fm:
        if not flow_line(current, "depends_on"):
            errors.append(Message("A04", change.path, "depends_on must be a single flow-style line"))
        else:
            lines = current.lines.copy()
            for i in range(1, current.fm_end - 1):
                if lines[i].startswith("depends_on:"):
                    ending = lines[i][len(line_text(lines[i])):]
                    lines[i] = "depends_on: [" + ", ".join(change.fm["set_depends_on"]) + "]" + ending
            current = parse(spec.path, "".join(lines))
    if errors:
        raise InvalidDocument(errors)
    errors = validate_spec(current, kind)
    if errors:
        raise InvalidDocument(errors)
    return current
