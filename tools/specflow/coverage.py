"""Run black-box evals and map JUnit test labels to spec IDs."""

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

from .apply import apply_change
from .markdown import InvalidDocument, Message
from .repository import Repository
from .validation import spec_items

CANDIDATE_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*-\d{3,}(?![0-9])")


@dataclass(frozen=True)
class TestResult:
    label: str
    status: str


@dataclass(frozen=True)
class SpecResult:
    id: str
    tag: str
    status: str
    tests: list[TestResult]

    def render(self) -> str:
        return f"{self.id} {self.tag} {self.status} ({len(self.tests)} tests)"


@dataclass
class CoverageResult:
    specs: list[SpecResult]
    tests: list[TestResult]
    messages: list[Message]


def references(label: str, spec_id: str) -> bool:
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(spec_id) + r"(?![0-9])", label) is not None


def parse_junit(path: Path) -> list[TestResult]:
    try:
        root = ET.parse(path).getroot()
        if root.tag not in {"testsuites", "testsuite"}:
            raise ValueError("root must be testsuites or testsuite")
    except (ET.ParseError, ValueError) as exc:
        raise InvalidDocument([Message("E01", path, f"invalid eval report: {exc}")]) from exc
    results = []
    for case in root.iter("testcase"):
        label = case.get("classname", "") + " " + case.get("name", "")
        status = "pass"
        if case.find("failure") is not None or case.find("error") is not None:
            status = "fail"
        elif case.find("skipped") is not None:
            status = "skip"
        results.append(TestResult(label, status))
    return results


def coverage(repo: Repository, module: str, with_change: Path | None = None) -> CoverageResult:
    spec = repo.specs[module]
    if with_change is not None:
        change = repo.load_change(with_change)
        if change.fm["module"] != module:
            raise InvalidDocument([Message("C09", with_change, f"change must target {module}")])
        current_ids = {item.id for item in spec_items(spec)}
        missing = [Message("C09", with_change, f"target {target} does not exist")
                   for target in change.fm.get("targets", []) if target not in current_ids]
        if missing:
            raise InvalidDocument(missing)
        spec = apply_change(spec, change)

    tests = []
    messages = []
    if "evals" in spec.fm:
        directory = repo.root / "system-evals" if module == "system" else spec.path.parent
        report = directory / spec.fm["evals"]["report"]
        report.unlink(missing_ok=True)
        run = subprocess.run(spec.fm["evals"]["run"], shell=True, cwd=directory,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if not report.is_file():
            output = " | ".join(run.stdout.splitlines()[-40:])
            detail = f"eval run produced no report: {report.relative_to(repo.root).as_posix()}"
            if output:
                detail += f"; last 40 lines of output: {output}"
            messages.append(Message("E01", report, detail))
        else:
            try:
                tests = parse_junit(report)
            except InvalidDocument as exc:
                messages.extend(exc.messages)

    items = spec_items(spec)
    known_ids = {item.id for item in items}
    prefixes = {doc.fm["id_prefix"] for doc in repo.specs.values()} | {"SYS"}
    for test in tests:
        for candidate in sorted(set(CANDIDATE_ID.findall(test.label))):
            if candidate.split("-", 1)[0] in prefixes and candidate not in known_ids:
                messages.append(Message("E02", spec.path,
                                        f"test references unknown spec {candidate}: {test.label}"))

    results = []
    for item in items:
        linked = [test for test in tests if references(test.label, item.id)]
        failing = [test.label for test in linked if test.status == "fail"]
        status = "fail" if failing else "pass" if any(t.status == "pass" for t in linked) else "uncovered"
        if item.tag == "review" and not linked:
            status = "n/a"
        if item.tag != "review" and status in {"fail", "uncovered"}:
            code = "E04" if failing else "E03"
            detail = f"{item.id} failing: " + ", ".join(failing) if failing else f"{item.id} uncovered"
            messages.append(Message(code, spec.path, detail, item.section.start + 1,
                                    "WARN" if item.tag == "quarantine" else "ERROR"))
        results.append(SpecResult(item.id, item.tag, status, linked))
    return CoverageResult(results, tests, messages)
