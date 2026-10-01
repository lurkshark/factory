"""Phase 1 command-line interface."""

import argparse
import os
from pathlib import Path
import subprocess
import traceback

from .apply import apply_change
from .markdown import InvalidDocument, Message, parse
from .repository import Repository
from .validation import CHANGE_NAME, KINDS, NAME, PREFIX, SLUG, flow_line, spec_items, specs_section

CHANGE_TEMPLATE = """---
module: {module}
---

## Motivation
TODO: why this change is needed.

## Interface

## Added

## Modified

## Removed

## Decisions

## Rejected alternatives
"""
MODULE_TEMPLATE = """---
name: {name}
kind: {kind}
id_prefix: {prefix}
depends_on: []
---

# {name}

TODO: one paragraph describing this module's purpose.

## Specs
"""
AGENT_TEMPLATE = """# Module: {name}

- Spec: `SPEC.md` in this directory. Read it before changing anything here.
- Evals: `evals/`. Run with `tools/spec coverage {name}` from the repo root.
- Interface file and preserved paths are listed in the SPEC.md frontmatter.
- Follow the rules in the root `AGENTS.md`.
"""


class UsageError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def parser() -> Parser:
    result = Parser(prog="spec")
    commands = result.add_subparsers(dest="command", required=True, parser_class=Parser)
    for command in ("check", "list", "next"):
        commands.add_parser(command)
    show = commands.add_parser("show")
    show.add_argument("module")
    show.add_argument("id", nargs="?")
    show.add_argument("--projected", action="store_true")
    change = commands.add_parser("new-change")
    change.add_argument("module")
    change.add_argument("slug")
    module = commands.add_parser("new-module")
    module.add_argument("name")
    module.add_argument("--kind", choices=KINDS, required=True)
    module.add_argument("--prefix", required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("change_file")
    apply.add_argument("--now", action="store_true")
    return result


def report(messages: list[Message], root: Path) -> int:
    for message in sorted(messages, key=lambda m: (m.path.as_posix(), m.line, m.code, m.message)):
        print(message.render(root))
    errors = sum(m.level == "ERROR" for m in messages)
    warnings = sum(m.level == "WARN" for m in messages)
    if messages:
        print(f"{errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def require_module(repo: Repository, name: str):
    if name not in repo.specs:
        errors = [m for m in repo.messages if m.path == repo.root / "SYSTEM.md" or m.path == repo.root / "packages" / name / "SPEC.md"]
        if errors:
            raise InvalidDocument(errors)
        raise UsageError(f"module does not exist: {name}")
    return repo.specs[name]


def new_change(repo: Repository, module: str, slug: str) -> Path:
    require_module(repo, module)
    if not SLUG.fullmatch(slug):
        raise UsageError("slug must contain lowercase letters, digits, and single hyphens")
    number = max((int(CHANGE_NAME.fullmatch(p.name)["num"]) for p in repo.change_paths() + repo.change_paths(True)), default=0) + 1
    if number > 9999:
        raise UsageError("change numbering exhausted (maximum 9999)")
    path = repo.root / "changes" / f"{number:04d}-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as stream:
        stream.write(CHANGE_TEMPLATE.format(module=module))
    return path


def new_module(repo: Repository, name: str, kind: str, prefix: str) -> Path:
    if not NAME.fullmatch(name) or name == "system":
        raise UsageError("invalid or reserved module name")
    if kind not in KINDS:
        raise UsageError("invalid module kind")
    if not PREFIX.fullmatch(prefix) or prefix == "SYS":
        raise UsageError("invalid or reserved id_prefix")
    if prefix in {doc.fm.get("id_prefix") for doc in repo.specs.values()}:
        raise UsageError(f"id_prefix is already taken: {prefix}")
    # Invalid existing specs might still own a prefix; do not create a module
    # until those specs can be read reliably.
    if any(m.code in {"C03", "C04"} for m in repo.messages):
        raise InvalidDocument(repo.messages)
    directory = repo.root / "packages" / name
    if directory.exists():
        raise UsageError(f"module directory already exists: packages/{name}")
    directory.mkdir(parents=True)
    (directory / "SPEC.md").write_text(MODULE_TEMPLATE.format(name=name, kind=kind, prefix=prefix), encoding="utf-8")
    (directory / "AGENTS.md").write_text(AGENT_TEMPLATE.format(name=name), encoding="utf-8")
    for child in ("src", "evals"):
        (directory / child).mkdir()
        (directory / child / ".gitkeep").touch()
    return directory / "SPEC.md"


def apply_file(repo: Repository, path: Path, now: bool = False) -> list[Message]:
    path = path.resolve()
    queue = repo.change_paths()
    if path not in queue:
        raise InvalidDocument([Message("C07", path, "apply requires a pending change with a valid filename")])
    # Preserve the specified A04 diagnostic even when C03 prevents the module
    # from being loaded into the validated repository lookup.
    try:
        raw_change = parse(path)
    except InvalidDocument as exc:
        raise InvalidDocument([Message("C09", m.path, m.message, m.line) for m in exc.messages]) from exc
    module = raw_change.fm.get("module")
    if "set_depends_on" in raw_change.fm and isinstance(module, str) and NAME.fullmatch(module) and module != "system":
        spec_path = repo.root / "packages" / module / "SPEC.md"
        if spec_path.is_file() and not flow_line(parse(spec_path), "depends_on"):
            raise InvalidDocument([Message("A04", path, "depends_on must be a single flow-style line")])
    change = repo.load_change(path)
    if "blocked" in change.fm:
        raise InvalidDocument([Message("C09", path, f"change is blocked: {change.fm['blocked']}")])
    if not now and path != queue[0]:
        raise InvalidDocument([Message("C09", path, "change is not the queue head; use --now to apply it out of order")])
    spec = require_module(repo, change.fm["module"])
    existing = {item.id for item in spec_items(spec)}
    missing = [Message("C09", path, f"target {target} does not exist") for target in change.fm.get("targets", []) if target not in existing]
    if missing:
        raise InvalidDocument(missing)
    updated = apply_change(spec, change)
    archive = path.parent / "archive" / path.name
    if archive.exists():
        raise InvalidDocument([Message("C08", archive, "archive destination already exists")])
    in_git = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo.root, capture_output=True, text=True)
    tracked = False
    if in_git.returncode == 0 and Path(in_git.stdout.strip()).resolve() == repo.root:
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", path.relative_to(repo.root).as_posix()], cwd=repo.root, capture_output=True).returncode == 0
    archive_existed = archive.parent.exists()
    archive.parent.mkdir(exist_ok=True)
    moved = False
    try:
        spec.path.write_bytes(updated.text.encode("utf-8"))
        if tracked:
            result = subprocess.run(["git", "mv", "--", path.relative_to(repo.root).as_posix(), archive.relative_to(repo.root).as_posix()], cwd=repo.root, capture_output=True, text=True)
            if result.returncode:
                raise UsageError("git mv failed: " + result.stderr.strip().replace("\n", " "))
        else:
            path.rename(archive)
        moved = True
    except Exception:
        spec.path.write_bytes(spec.text.encode("utf-8"))
        if moved:
            if tracked:
                subprocess.run(["git", "mv", "--", archive.relative_to(repo.root).as_posix(), path.relative_to(repo.root).as_posix()], cwd=repo.root, check=True, capture_output=True)
            else:
                archive.rename(path)
        if not archive_existed and not any(archive.parent.iterdir()):
            archive.parent.rmdir()
        raise
    if now:
        _, messages = Repository(repo.root).project()
        return [Message(m.code, m.path, m.message, m.line, "WARN") for m in messages]
    return []


def run(args, root: Path) -> int:
    repo = Repository(root)
    if args.command == "check":
        return report(repo.check(), root)
    if args.command == "list":
        if repo.messages:
            return report(repo.messages, root)
        for name, doc in sorted(repo.specs.items()):
            print(f"{name} {doc.fm.get('kind', 'system')} {doc.fm['id_prefix']} {len(spec_items(doc))}")
        for path in repo.change_paths():
            change = repo.load_change(path)
            blocked = f" [BLOCKED: {change.fm['blocked']}]" if "blocked" in change.fm else ""
            print(f"{path.stem} {change.fm['module']}{blocked}")
        return 0
    if args.command == "show":
        doc = require_module(repo, args.module)
        if args.projected:
            projected, messages = repo.project(args.module)
            if messages:
                return report(messages, root)
            doc = projected[args.module]
        if args.id:
            item = next((item for item in spec_items(doc) if item.id == args.id), None)
            if item is None:
                raise UsageError(f"spec ID does not exist: {args.id}")
            text = doc.section_text(item.section)
        else:
            text = doc.section_text(specs_section(doc))
        print(text, end="")
        return 0
    if args.command == "next":
        queue = repo.change_paths()
        if not queue:
            return 4
        change = repo.load_change(queue[0])
        path = queue[0].relative_to(root).as_posix()
        if "blocked" in change.fm:
            print(f"{path} [BLOCKED: {change.fm['blocked']}]")
            return 3
        print(path)
        return 0
    if args.command == "new-change":
        print(new_change(repo, args.module, args.slug).relative_to(root).as_posix())
        return 0
    if args.command == "new-module":
        print(new_module(repo, args.name, args.kind, args.prefix).relative_to(root).as_posix())
        return 0
    if args.command == "apply":
        path = root / args.change_file
        messages = apply_file(repo, path, args.now)
        return report(messages, root)
    raise UsageError("unknown command")


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    root = (root or Path.cwd()).resolve()
    try:
        return run(parser().parse_args(argv), root)
    except InvalidDocument as exc:
        return report(exc.messages, root)
    except UsageError as exc:
        print(f"ERROR usage spec: {str(exc).replace(chr(10), ' ')}")
        return 2
    except Exception as exc:
        if os.environ.get("SPECFLOW_DEBUG") == "1":
            traceback.print_exc()
        print(f"ERROR tooling spec: {type(exc).__name__}: {str(exc).replace(chr(10), ' ')}")
        return 2
