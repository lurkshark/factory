"""Prepare an isolated branch for manual regeneration of disposable source."""

from datetime import date
from pathlib import Path

from .cli import UsageError, main, require_module
from .git import Git
from .globs import matches
from .markdown import InvalidDocument
from .night import load_config, run_agent
from .repository import Repository
from .validation import NAME


def prune_source(package: Path, preserve: list[str]) -> tuple[int, int]:
    """Unlink disposable entries without following source-tree symlinks."""
    deleted = preserved = 0

    def visit(directory: Path):
        nonlocal deleted, preserved
        for path in sorted(directory.iterdir()):
            if path.is_dir() and not path.is_symlink():
                visit(path)
            elif any(matches(path.relative_to(package).as_posix(), glob) for glob in preserve):
                preserved += 1
            else:
                path.unlink()
                deleted += 1
        if not any(directory.iterdir()):
            directory.rmdir()

    source = package / "src"
    if source.exists():
        visit(source)
    return deleted, preserved


def regenerate(root: Path, module: str, agent: bool = False) -> int:
    if not NAME.fullmatch(module) or module == "system":
        raise UsageError("regen requires a package module name")
    git = Git(root)
    if git.run("status", "--porcelain=v1", "--untracked-files=all"):
        raise UsageError("working tree must be clean")
    repo = Repository(root)
    spec = require_module(repo, module)
    errors = [m for m in repo.check() if m.path == spec.path and m.level == "ERROR"
              and m.code in {"C02", "C03", "C04", "C11"}]
    if errors:
        raise InvalidDocument(errors)
    package = spec.path.parent
    source = package / "src"
    if (package.parent.is_symlink() or package.is_symlink() or source.is_symlink()
            or (source.exists() and not source.is_dir())):
        raise UsageError("regen requires ordinary package and src directories")
    cfg = load_config(root) if agent else None
    worktree = root / ".worktrees" / f"regen-{module}"
    branch = f"regen/{module}-{date.today():%Y%m%d}"
    git.run("worktree", "add", worktree.relative_to(root).as_posix(), "-b", branch, "HEAD")
    deleted, preserved = prune_source(worktree / "packages" / module, spec.fm.get("preserve", []))
    print(f"Deleted {deleted} file(s); preserved {preserved} file(s).")
    print(f"Worktree: {worktree}\nBranch: {branch}")
    if not agent:
        print(f"Run your agent in {worktree} with docs/playbooks/regen.md.")
        return 0

    if cfg["sandbox"] == "none":
        print("WARN sandbox: none; agent runs directly on the host")
    prompt = f"Follow docs/playbooks/regen.md exactly. Module: {module}."
    log_path = worktree / ".night" / date.today().isoformat() / f"regen-{module}-agent.log"
    worktree_git = Git(worktree)
    baseline = worktree_git.run("rev-parse", "HEAD").strip()
    result = run_agent(prompt, log_path, cfg, worktree)
    if result.output:
        print(result.output, end="" if result.output.endswith("\n") else "\n")
    if worktree_git.run("rev-parse", "HEAD").strip() != baseline:
        worktree_git.run("reset", "--soft", baseline)
        print("WARN agent committed; commits were undone")
    coverage_status = main(["coverage", module], worktree)
    return coverage_status or (1 if result.exit_code or result.timed_out or result.hit_limit else 0)
