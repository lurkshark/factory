"""Classify paths using the module metadata at a particular revision."""

from pathlib import Path, PurePosixPath

from .git import Git
from .globs import matches
from .markdown import Doc, parse


def package_path(path: str) -> tuple[str, str] | None:
    parts = path.split("/", 2)
    return (parts[1], parts[2]) if len(parts) == 3 and parts[0] == "packages" else None


class Zones:
    def __init__(self, root: Path, revision: str | None = None):
        self.root = root.resolve()
        self.revision = revision
        self.git = Git(self.root)
        self._specs: dict[str, Doc | None] = {}

    def spec(self, module: str) -> Doc | None:
        if module not in self._specs:
            relative = f"packages/{module}/SPEC.md"
            path = self.root / relative
            text = self.git.read(self.revision, relative) if self.revision is not None else (
                path.read_bytes().decode("utf-8") if path.is_file() else None)
            self._specs[module] = parse(path, text) if text is not None else None
        return self._specs[module]

    def append_only(self, path: str) -> bool:
        package = package_path(path)
        if package is None:
            return False
        module, relative = package
        doc = self.spec(module)
        return doc is not None and any(matches(relative, glob) for glob in doc.fm.get("append_only", []))

    def classify(self, path: str) -> str:
        if path.startswith(("tools/", ".github/", ".devcontainer/")):
            return "tools"
        if path.startswith("changes/"):
            return "changes"
        if path.startswith("docs/playbooks/") or PurePosixPath(path).name in {"AGENTS.md", "CLAUDE.md"}:
            return "playbooks"
        if path == "SYSTEM.md":
            return "system-spec"
        if path.startswith("system-evals/"):
            return "system-evals"
        package = package_path(path)
        if package is None:
            return "other"
        module, relative = package
        if relative == "SPEC.md":
            return "spec"
        if relative.startswith("evals/"):
            return "evals"
        doc = self.spec(module)
        if doc is not None:
            if relative == doc.fm.get("interface"):
                return "interface"
            if any(matches(relative, glob) for glob in doc.fm.get("preserve", [])):
                return "preserved"
        if relative.startswith("src/"):
            return "implementation"
        return "package-meta"
