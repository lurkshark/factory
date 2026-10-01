"""Lossless, line-based Markdown parsing shared by specs and changes."""

from dataclasses import dataclass
from pathlib import Path
import re

import yaml


@dataclass(frozen=True)
class Message:
    code: str
    path: Path
    message: str
    line: int = 0
    level: str = "ERROR"

    def render(self, root: Path) -> str:
        try:
            path = self.path.relative_to(root).as_posix()
        except ValueError:
            path = self.path.as_posix()
        location = f"{path}:{self.line}" if self.line else path
        return f"{self.level} {self.code} {location}: {self.message}"


class InvalidDocument(Exception):
    def __init__(self, messages: list[Message]):
        self.messages = messages
        super().__init__(messages[0].message)


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    start: int
    end: int


@dataclass
class Doc:
    path: Path
    lines: list[str]
    fm_text: str
    fm: dict
    fm_end: int
    sections: list[Section]

    @property
    def body_start(self) -> int:
        return self.fm_end

    @property
    def text(self) -> str:
        return "".join(self.lines)

    def section_text(self, section: Section) -> str:
        return "".join(self.lines[section.start:section.end])


def line_text(line: str) -> str:
    return line.rstrip("\r\n")


def parse(path: Path, text: str | None = None) -> Doc:
    if text is None:
        text = path.read_bytes().decode("utf-8")
    lines = text.splitlines(keepends=True)
    if not lines or line_text(lines[0]) != "---":
        raise InvalidDocument([Message("C03", path, "file must start with ---", 1)])
    closing = next((i for i in range(1, len(lines)) if line_text(lines[i]) == "---"), None)
    if closing is None:
        raise InvalidDocument([Message("C03", path, "missing closing frontmatter fence", 1)])
    fm_text = "".join(lines[1:closing])
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 2 if mark else 2
        raise InvalidDocument([Message("C03", path, "invalid YAML frontmatter", line)]) from exc
    if not isinstance(fm, dict):
        raise InvalidDocument([Message("C03", path, "frontmatter must be a mapping", 2)])
    headings = []
    fenced = False
    for i in range(closing + 1, len(lines)):
        raw = line_text(lines[i])
        if raw.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        match = re.fullmatch(r"(#{1,6}) (.*?)\s*", raw) if not fenced else None
        if match:
            headings.append((len(match[1]), match[2], i))
    sections = []
    for j, (level, title, start) in enumerate(headings):
        end = next((other[2] for other in headings[j + 1:] if other[0] <= level), len(lines))
        sections.append(Section(level, title, start, end))
    return Doc(path, lines, fm_text, fm, closing + 1, sections)
