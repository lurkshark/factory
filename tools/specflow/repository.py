"""Repository discovery, deterministic static checks, and queue projection."""

import json
from pathlib import Path
import re
import tomllib

from .apply import apply_change
from .globs import matches
from .markdown import Doc, InvalidDocument, Message, parse
from .validation import CHANGE_NAME, change_items, fm_line, spec_items, validate_change, validate_spec


class Repository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.specs: dict[str, Doc] = {}
        self.messages: list[Message] = []
        self._load_specs()

    def _load_specs(self) -> None:
        paths = [("system", self.root / "SYSTEM.md", "system")]
        packages = self.root / "packages"
        if packages.is_dir():
            paths.extend((p.name, p / "SPEC.md", "module") for p in sorted(packages.iterdir()) if p.is_dir())
        for name, path, kind in paths:
            missing_code = "C01" if kind == "system" else "C02"
            if not path.is_file():
                self.messages.append(Message(missing_code, path, "spec file is missing"))
                continue
            try:
                doc = parse(path)
            except InvalidDocument as exc:
                self.messages.extend(exc.messages)
                if kind == "system":
                    self.messages.append(Message("C01", path, "system spec is invalid"))
                continue
            errors = validate_spec(doc, kind)
            self.messages.extend(errors)
            if kind == "system" and errors:
                self.messages.append(Message("C01", path, "system spec is invalid"))
            if kind == "module" and doc.fm.get("name") != name:
                self.messages.append(Message("C02", path, f"name must equal directory name {name}", fm_line(doc, "name")))
            if not any(m.code in {"C03", "C04"} for m in errors):
                # A package directory named system is invalid and cannot replace
                # the pseudo-module SYSTEM.md in the lookup table.
                if kind == "system" or name != "system":
                    self.specs[name] = doc

    def change_paths(self, archived: bool = False) -> list[Path]:
        directory = self.root / "changes" / "archive" if archived else self.root / "changes"
        return sorted(p for p in directory.iterdir() if p.is_file() and CHANGE_NAME.fullmatch(p.name)) if directory.is_dir() else []

    def load_change(self, path: Path) -> Doc:
        try:
            doc = parse(path)
        except InvalidDocument as exc:
            raise InvalidDocument([Message("C09", m.path, m.message, m.line) for m in exc.messages]) from exc
        module = doc.fm.get("module")
        spec = self.specs.get(module) if isinstance(module, str) else None
        errors = validate_change(doc, spec.fm["id_prefix"] if spec else None)
        if spec is None:
            errors.append(Message("C09", path, "target module does not exist", fm_line(doc, "module")))
        if not errors and "set_depends_on" in doc.fm:
            for dependency in doc.fm["set_depends_on"]:
                if dependency == "system" or dependency not in self.specs:
                    errors.append(Message("C09", path, f"unknown dependency {dependency}", fm_line(doc, "set_depends_on")))
        if errors:
            raise InvalidDocument(errors)
        return doc

    def project(self, module_filter: str | None = None) -> tuple[dict[str, Doc], list[Message]]:
        projected = self.specs.copy()
        messages = []
        for path in self.change_paths():
            try:
                if module_filter is not None and parse(path).fm.get("module") != module_filter:
                    continue
                change = self.load_change(path)
            except InvalidDocument as exc:
                messages.extend(exc.messages)
                continue
            module = change.fm["module"]
            current = projected[module]
            existing = {item.id for item in spec_items(current)}
            for target in change.fm.get("targets", []):
                if target not in existing:
                    messages.append(Message("C09", path, f"target {target} does not exist at this point in the queue", fm_line(change, "targets")))
            try:
                projected[module] = apply_change(current, change)
            except InvalidDocument as exc:
                messages.extend(Message("C10", path, f"{m.code} {m.message}", m.line) for m in exc.messages)
        return projected, messages

    def check(self) -> list[Message]:
        messages = self.messages.copy()
        seen_prefixes = {}
        seen_ids = {}
        for name, doc in sorted(self.specs.items()):
            prefix = doc.fm["id_prefix"]
            if prefix in seen_prefixes:
                messages.append(Message("C05", doc.path, f"id_prefix {prefix} also used by {seen_prefixes[prefix]}", fm_line(doc, "id_prefix")))
            seen_prefixes[prefix] = name
            for item in spec_items(doc):
                if item.id in seen_ids and seen_ids[item.id] != doc.path:
                    messages.append(Message("C05", doc.path, f"spec ID {item.id} is already used in {seen_ids[item.id].relative_to(self.root)}", item.section.start + 1))
                seen_ids[item.id] = doc.path
            if name != "system":
                interface = doc.fm.get("interface")
                preserve = doc.fm.get("preserve", [])
                if interface and (not (doc.path.parent / interface).is_file() or not any(matches(interface, pattern) for pattern in preserve)):
                    messages.append(Message("C11", doc.path, "interface must exist and match a preserve glob", fm_line(doc, "interface")))
                for pattern in doc.fm.get("append_only", []):
                    if pattern not in preserve:
                        messages.append(Message("C11", doc.path, f"append_only glob {pattern} must appear in preserve", fm_line(doc, "append_only")))
        for archived in (False, True):
            directory = self.root / "changes" / "archive" if archived else self.root / "changes"
            if directory.is_dir():
                for path in sorted(directory.iterdir()):
                    if path.is_file() and path.suffix == ".md" and path.name != "AGENTS.md" and not CHANGE_NAME.fullmatch(path.name):
                        messages.append(Message("C07", path, "unexpected Markdown filename; ignored", level="WARN"))
        numbers = {}
        for path in self.change_paths() + self.change_paths(True):
            number = CHANGE_NAME.fullmatch(path.name)["num"]
            if number in numbers:
                messages.append(Message("C08", path, f"change number {number} also used by {numbers[number].relative_to(self.root)}"))
            numbers[number] = path
        projected, queue_messages = self.project()
        messages.extend(queue_messages)
        messages.extend(self.dependencies(self.specs))
        # Dependency changes can introduce invalid graphs even when the base
        # specs are valid. Check the final projected graph as well.
        if any(projected[name].text != doc.text for name, doc in self.specs.items()):
            messages.extend(self.dependencies(projected))
        messages.extend(self.manifests())
        removed = set()
        for path in self.change_paths(True):
            try:
                removed.update(item.id for item in change_items(parse(path), "Removed"))
            except InvalidDocument as exc:
                messages.extend(Message("C09", m.path, m.message, m.line) for m in exc.messages)
        for doc in self.specs.values():
            for item in spec_items(doc):
                if item.id in removed:
                    messages.append(Message("C13", doc.path, f"removed ID {item.id} must not be reused", item.section.start + 1))
        for path in self.change_paths():
            try:
                doc = self.load_change(path)
            except InvalidDocument:
                continue
            for item in change_items(doc, "Added"):
                if item.id in removed:
                    messages.append(Message("C13", path, f"removed ID {item.id} must not be reused", item.section.start + 1))
        # Avoid duplicate graph diagnostics when both base and projection fail.
        return list(dict.fromkeys(messages))

    def dependencies(self, specs: dict[str, Doc]) -> list[Message]:
        graph = {name: doc.fm.get("depends_on", []) for name, doc in specs.items() if name != "system"}
        messages = []
        for name, dependencies in sorted(graph.items()):
            for dependency in dependencies:
                if dependency not in graph or dependency == name:
                    messages.append(Message("C06", specs[name].path, f"invalid dependency {name} -> {dependency}", fm_line(specs[name], "depends_on")))
        visited, active = set(), []

        def cycle(name):
            if name in active:
                return active[active.index(name):] + [name]
            if name in visited:
                return None
            active.append(name)
            for dependency in sorted(graph[name]):
                if dependency in graph and dependency != name:
                    found = cycle(dependency)
                    if found:
                        return found
            active.pop()
            visited.add(name)
            return None

        for name in sorted(graph):
            found = cycle(name)
            if found:
                messages.append(Message("C06", specs[name].path, "dependency cycle: " + " -> ".join(found), fm_line(specs[name], "depends_on")))
                break
        return messages

    def manifests(self) -> list[Message]:
        messages = []
        native = {}
        package_names, distribution_names = {}, {}
        for name, doc in sorted(self.specs.items()):
            if name == "system":
                continue
            package = doc.path.parent / "package.json"
            python = doc.path.parent / "pyproject.toml"
            try:
                if package.is_file():
                    data = json.loads(package.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("manifest must be an object")
                    native[name] = ("js", data, package)
                    if isinstance(data.get("name"), str):
                        package_names[data["name"]] = name
                elif python.is_file():
                    data = tomllib.loads(python.read_text(encoding="utf-8")).get("project", {})
                    if not isinstance(data, dict):
                        raise ValueError("project must be a table")
                    native[name] = ("py", data, python)
                    if isinstance(data.get("name"), str):
                        distribution_names[normalize(data["name"])] = name
                else:
                    messages.append(Message("C12", doc.path, "no supported manifest", level="WARN"))
            except (ValueError, TypeError) as exc:
                messages.append(Message("C12", package if package.is_file() else python, f"cannot read manifest: {exc}", level="WARN"))
        # A module with both manifests still supplies its Python distribution
        # name to other Python modules, even though its own adapter is JS.
        for name, doc in self.specs.items():
            python = doc.path.parent / "pyproject.toml"
            if name == "system" or not python.is_file() or native.get(name, (None,))[0] == "py":
                continue
            try:
                project = tomllib.loads(python.read_text(encoding="utf-8")).get("project", {})
                if isinstance(project, dict) and isinstance(project.get("name"), str):
                    distribution_names[normalize(project["name"])] = name
            except ValueError:
                pass  # An unselected adapter does not emit manifest warnings.
        for name, (kind, data, path) in native.items():
            dependencies = set()
            if kind == "js":
                for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                    entries = data.get(key, {})
                    if not isinstance(entries, dict):
                        messages.append(Message("C12", path, f"{key} must be an object", level="WARN"))
                        continue
                    for dep, version in entries.items():
                        if isinstance(version, str) and version.startswith("workspace:"):
                            target = package_names.get(dep)
                            if target is None or target == name:
                                messages.append(Message("C12", path, f"workspace dependency {dep} maps to no other module", level="WARN"))
                            else:
                                dependencies.add(target)
            else:
                entries = data.get("dependencies", [])
                if not isinstance(entries, list) or any(not isinstance(x, str) for x in entries):
                    messages.append(Message("C12", path, "project dependencies must be strings", level="WARN"))
                    entries = []
                for entry in entries:
                    dep = normalize(re.split(r"[<>=!~;\[ (]", entry, maxsplit=1)[0])
                    target = distribution_names.get(dep)
                    if target is not None and target != name:
                        dependencies.add(target)
            declared = set(self.specs[name].fm["depends_on"])
            if declared != dependencies:
                messages.append(Message("C12", path, f"missing from manifest: {', '.join(sorted(declared - dependencies)) or 'none'}; missing from depends_on: {', '.join(sorted(dependencies - declared)) or 'none'}", level="WARN"))
        return messages


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.lower())
