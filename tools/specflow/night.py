"""Night queue and single-change worker; Git and verification belong to the host."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import os
import re
import shlex
import subprocess
import tempfile
import time
import traceback

import yaml

from .cli import Parser, UsageError, apply_file
from .coverage import coverage, references
from .git import FileChange, Git, GitError
from .markdown import Doc, InvalidDocument, parse
from .pr import pr_body
from .repository import Repository
from .validation import change_items
from .zones import Zones, package_path


def load_config(root: Path) -> dict:
    # Empty command maps select the built-in command arrays in run_agent.
    defaults = {
        "agent": "claude", "base_branch": "main", "remote": "origin", "sandbox": "docker",
        "docker_image": "specflow-worker", "docker_mounts": ["specflow-agent-home:/home/worker"],
        "attempt_timeout_minutes": 90, "max_attempts_per_phase": 2, "max_changes": 10,
        "max_hours": 8, "limit_patterns": ["usage limit", "rate limit", "limit reached", "limit will reset", "quota"],
        "limit_sleep_minutes": 30, "max_limit_waits": 16,
        "agent_commands": {}, "agent_commands_no_sandbox": {},
    }
    path = root / "tools/night.yaml"
    supplied = yaml.safe_load(path.read_text()) if path.is_file() else {}
    if supplied is None:
        supplied = {}
    if not isinstance(supplied, dict):
        raise UsageError("night.yaml must contain a mapping")
    unknown = supplied.keys() - defaults.keys()
    if unknown:
        raise UsageError("unknown night.yaml keys: " + ", ".join(sorted(map(str, unknown))))
    cfg = defaults | supplied
    if cfg["agent"] not in {"claude", "codex"} or cfg["sandbox"] not in {"docker", "none"}:
        raise UsageError("agent must be claude or codex; sandbox must be docker or none")
    for key in ("base_branch", "remote", "docker_image"):
        if not isinstance(cfg[key], str) or not cfg[key]:
            raise UsageError(f"{key} must be a non-empty string")
    for key in ("max_attempts_per_phase", "max_changes", "max_limit_waits"):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise UsageError(f"{key} must be a positive integer")
    for key in ("attempt_timeout_minutes", "max_hours", "limit_sleep_minutes"):
        if type(cfg[key]) not in {int, float} or not 0 < cfg[key] < float("inf"):
            raise UsageError(f"{key} must be a positive number")
    for key in ("docker_mounts", "limit_patterns"):
        if not isinstance(cfg[key], list) or any(not isinstance(x, str) or not x for x in cfg[key]):
            raise UsageError(f"{key} must be a list of non-empty strings")
    for pattern in cfg["limit_patterns"]:
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise UsageError(f"invalid limit_patterns regex: {exc}") from exc
    for key in ("agent_commands", "agent_commands_no_sandbox"):
        commands = cfg[key]
        if not isinstance(commands, dict) or (commands.keys() != {"claude", "codex"} and key in supplied):
            raise UsageError(f"{key} must define claude and codex commands")
        for command in commands.values():
            if (not isinstance(command, list) or not command or
                    any(not isinstance(arg, str) or not arg for arg in command) or
                    command.count("{prompt}") != 1):
                raise UsageError(f"{key} commands must be argument lists containing one {{prompt}}")
    return cfg


@dataclass(frozen=True)
class AgentResult:
    exit_code: int
    output: str
    timed_out: bool
    hit_limit: bool


def run_agent(prompt: str, log_path: Path, cfg: dict, repo_root: Path) -> AgentResult:
    defaults = {
        "agent_commands": {
            "claude": ["claude", "-p", "{prompt}", "--dangerously-skip-permissions", "--max-turns", "200"],
            "codex": ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "{prompt}"],
        },
        "agent_commands_no_sandbox": {
            "claude": ["claude", "-p", "{prompt}", "--permission-mode", "acceptEdits", "--allowedTools",
                       "Read,Edit,Write,Glob,Grep,Bash", "--max-turns", "200"],
            "codex": ["codex", "exec", "--approve-for-me", "{prompt}"],
        },
    }
    key = "agent_commands" if cfg["sandbox"] == "docker" else "agent_commands_no_sandbox"
    template = (cfg[key] or defaults[key])[cfg["agent"]]
    command = [prompt if arg == "{prompt}" else arg for arg in template]
    if cfg["sandbox"] == "docker":
        command = [*docker_command(cfg, repo_root), *command]
    timed_out = False
    try:
        result = subprocess.run(command, cwd=repo_root, input="", capture_output=True, text=True,
                                timeout=cfg["attempt_timeout_minutes"] * 60)
        exit_code, output = result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired as exc:
        def decoded(value):
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
        exit_code, output = 1, decoded(exc.stdout) + decoded(exc.stderr)
        output += "\nAgent attempt timed out.\n"
        timed_out = True
    except OSError as exc:
        exit_code, output = 1, f"Agent could not start: {exc}\n"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output, encoding="utf-8")
    hit_limit = (exit_code != 0 and not timed_out and
                 any(re.search(pattern, output, re.IGNORECASE) for pattern in cfg["limit_patterns"]))
    return AgentResult(exit_code, output, timed_out, hit_limit)


def docker_command(cfg: dict, root: Path, interactive: bool = False) -> list[str]:
    return ["docker", "run", *(["-it"] if interactive else []), "--rm",
            "-v", f"{root}:/workspace", "-w", "/workspace",
            *[arg for mount in cfg["docker_mounts"] for arg in ("-v", mount)], cfg["docker_image"]]


def remote_command(command: list[str], root: Path) -> str:
    """Keep remote writes and GitHub calls replaceable without config keys."""
    if os.environ.get("SPECFLOW_FAKE_REMOTE") == "1":
        output = "FAKE " + shlex.join(command)
        print(output, flush=True)
        return output
    result = subprocess.run(command, cwd=root, capture_output=True, text=True)
    if result.returncode:
        raise UsageError((result.stderr or result.stdout).strip() or f"{shlex.join(command)} failed")
    return result.stdout.strip()


def changed_files(git: Git) -> list[FileChange]:
    fields = git.run("status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    changes = []
    i = 0
    while i < len(fields) - 1:
        status, path = fields[i][:2], fields[i][3:]
        i += 1
        old_path = None
        if "R" in status or "C" in status:
            old_path = fields[i]
            i += 1
        changes.append(FileChange(status, path, old_path))
    return changes


def section_body(change: Doc, title: str) -> str:
    section = next((s for s in change.sections if s.level == 2 and s.title == title), None)
    return "".join(change.lines[section.start + 1:section.end]).strip() if section else ""


def is_spec_change(change: Doc) -> bool:
    return bool(any(change_items(change, title) for title in ("Added", "Modified", "Removed")) or
                change.fm.get("targets") or section_body(change, "Interface"))


def phase_prompt(change: Doc, root: Path, phase: int, attempt: int, maximum: int, output: str) -> str:
    playbook = "implement-evals" if phase == 1 else "implement-code"
    prompt = (f"You are running unattended. Follow docs/playbooks/{playbook}.md exactly.\n"
              f"Change file: {change.path.relative_to(root).as_posix()}\n"
              f"Module: {change.fm['module']}\n"
              "Do not run git commit, git push, or any git command that changes history.\n")
    if attempt > 1:
        prompt += (f"This is attempt {attempt} of {maximum}. A previous attempt did not pass verification.\n"
                   "Your earlier work is still in the working tree; inspect it with `git status` and `git diff` before continuing.\n"
                   "Verification output:\n" + "\n".join(output.splitlines()[-80:]) + "\n")
    return prompt


class Worker:
    def __init__(self, root: Path, cfg: dict, deadline: float | None = None, skip_blocked: bool = False):
        self.root = root.resolve()
        self.cfg = cfg
        self.git = Git(self.root)
        self.deadline = deadline if deadline is not None else time.monotonic() + cfg["max_hours"] * 3600
        self.skip_blocked = skip_blocked
        self.log_dir = self.root / ".night" / date.today().isoformat()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.log_dir / "log.md"

    def log(self, text: str):
        print(text, flush=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(text + "\n")

    def discard(self, keep: Path | None = None):
        saved = keep.read_bytes() if keep is not None and keep.is_file() else None
        untracked = [f.path for f in changed_files(self.git) if f.status == "??"]
        self.git.run("restore", "--staged", "--worktree", ".")
        if untracked:
            self.git.run("clean", "-fd", "--", *untracked)
        if saved is not None:
            keep.write_bytes(saved)

    def commit(self, subject: str, change: Doc, all_files: bool = False):
        if all_files:
            self.git.run("add", "-A")
        else:
            self.git.run("add", "--", change.path.relative_to(self.root).as_posix())
        # A removal-only change or existing evals may make phase 1 a no-op.
        self.git.run("commit", "--allow-empty", "-m", subject, "-m", f"Change: {change.path.stem}")

    def commit_blocked(self, change: Doc) -> str:
        self.discard(keep=change.path)
        self.commit(f"chore: block {change.path.stem}", change)
        self.log(f"BLOCKED {change.path.stem}: {parse(change.path).fm['blocked']}")
        return "BLOCKED"

    def mark_blocked(self, change: Doc, reason: str) -> str:
        # Restore phase edits before inserting a last-line YAML string; keep the
        # agent's Decisions and all other untouched bytes in the change file.
        self.discard(keep=change.path)
        try:
            doc = Repository(self.root).load_change(change.path)
            if doc.fm["module"] != change.fm["module"]:
                raise UsageError("agent retargeted the change")
        except (InvalidDocument, OSError, UsageError):
            # The agent may have damaged the Markdown/frontmatter itself.
            # Block the valid committed change rather than a malformed file.
            original = self.git.read("HEAD", change.path.relative_to(self.root).as_posix())
            if original is None:
                raise UsageError("cannot restore committed change file")
            change.path.write_bytes(original.encode("utf-8"))
            doc = parse(change.path)
        lines = doc.lines.copy()
        ending = "\r\n" if lines[0].endswith("\r\n") else "\n"
        lines[doc.fm_end - 1:doc.fm_end - 1] = ["blocked: '" + reason.replace("'", "''").replace("\n", " ").replace("\r", " ") + "'" + ending]
        change.path.write_bytes("".join(lines).encode("utf-8"))
        return self.commit_blocked(change)

    def verify_paths(self, change: Doc, phase: int, baseline: str) -> list[str]:
        zones = Zones(self.root, baseline)
        existing = set(self.git.run("ls-tree", "-r", "--name-only", "-z", baseline).split("\0"))
        module = change.fm["module"]
        current = change.path.relative_to(self.root).as_posix()
        errors = []
        for file in changed_files(self.git):
            for path in file.paths:
                zone = zones.classify(path)
                if phase == 1:
                    package = package_path(path)
                    allowed = (path == current or (module == "system" and zone == "system-evals") or
                               (package is not None and package[0] == module and zone in {"evals", "interface"}))
                    if not allowed:
                        errors.append(f"P1-01 phase 1 touched {path}")
                else:
                    if (zone in {"evals", "system-evals", "interface", "tools", "playbooks", "spec", "system-spec"} or
                            (zone == "changes" and path != current)):
                        errors.append(f"P2-01 phase 2 touched {path}")
                if zones.append_only(path) and (file.old_path is not None or path in existing):
                    errors.append(f"P2-02 append-only file modified or deleted: {path}")
        return errors

    def verify(self, change: Doc, phase: int, baseline: str) -> list[str]:
        errors = self.verify_paths(change, phase, baseline)
        if errors:
            return errors
        repo = Repository(self.root)
        result = coverage(repo, change.fm["module"], change.path)
        for message in result.messages:
            rendered = message.render(self.root)
            if message.level == "WARN":
                self.log(rendered)
            if message.level == "ERROR" and (phase == 2 or message.code in {"E01", "E02"}):
                errors.append(rendered)
        if phase == 1:
            ids = [item.id for title in ("Added", "Modified") for item in change_items(change, title)]
            ids += change.fm.get("targets", [])
            for spec_id in ids:
                tests = [test for test in result.tests if references(test.label, spec_id)]
                if not tests:
                    errors.append(f"P1-02 no eval for {spec_id}")
                elif all(test.status == "pass" for test in tests):
                    self.log(f"WARN P1-03 evals for {spec_id} already pass before implementation")
            interface = repo.specs[change.fm["module"]].fm.get("interface")
            paths = {path for file in changed_files(self.git) for path in file.paths}
            if section_body(change, "Interface") and (not interface or
                    f"packages/{change.fm['module']}/{interface}" not in paths):
                self.log("WARN P1-04 Interface section present but interface file unchanged")
        else:
            if change.fm["module"] != "system" and "evals" in repo.specs["system"].fm:
                system = coverage(repo, "system")
                errors.extend(m.render(self.root) for m in system.messages if m.level == "ERROR")
            zones = Zones(self.root, baseline)
            for file in changed_files(self.git):
                for path in file.paths:
                    if zones.classify(path) == "preserved":
                        self.log(f"Preserved file changed: {path}")
        # Evals are arbitrary shell commands; include their file edits too.
        return errors + self.verify_paths(change, phase, baseline)

    def apply_and_check(self, change: Doc) -> list[str]:
        repo = Repository(self.root)
        spec_path = repo.specs[change.fm["module"]].path
        spec_bytes, change_bytes = spec_path.read_bytes(), change.path.read_bytes()
        archive = change.path.parent / "archive" / change.path.name
        applied = False
        try:
            for message in apply_file(repo, change.path, now=self.skip_blocked):
                self.log(message.render(self.root))
            applied = True
            errors = [m.render(self.root) for m in Repository(self.root).check() if m.level == "ERROR"]
            if not errors:
                return []
        except InvalidDocument as exc:
            errors = [m.render(self.root) for m in exc.messages]
        except (OSError, UsageError) as exc:
            errors = [str(exc)]
        if applied:
            # Undo only the host's apply, leaving the agent's implementation in
            # the tree for the next attempt to inspect and fix.
            self.git.run("restore", "--staged", "--worktree", "--", change.path.relative_to(self.root).as_posix())
            self.git.run("reset", "--", archive.relative_to(self.root).as_posix())
            archive.unlink(missing_ok=True)
            spec_path.write_bytes(spec_bytes)
            change.path.write_bytes(change_bytes)
        return errors

    def run_phase(self, change: Doc, phase: int) -> tuple[str, str]:
        baseline = self.git.run("rev-parse", "HEAD").strip()
        maximum = self.cfg["max_attempts_per_phase"]
        attempt, limit_waits, output = 1, 0, ""
        while attempt <= maximum:
            if time.monotonic() >= self.deadline:
                self.discard()
                self.log("STOPPED night deadline reached")
                return "STOPPED", ""
            prompt = phase_prompt(change, self.root, phase, attempt, maximum, output)
            self.log(f"{change.path.stem}: phase {phase}, attempt {attempt} of {maximum}")
            previous_head = self.git.run("rev-parse", "HEAD").strip()
            attempt_cfg = self.cfg | {"attempt_timeout_minutes": min(
                self.cfg["attempt_timeout_minutes"], max(0.001, (self.deadline - time.monotonic()) / 60))}
            result = run_agent(prompt, self.log_dir / f"{change.path.stem}-{phase}-{attempt}.log", attempt_cfg, self.root)
            if self.git.run("rev-parse", "HEAD").strip() != previous_head:
                self.git.run("reset", "--soft", previous_head)
                self.log("WARN agent committed; commits were undone")
            if result.timed_out and time.monotonic() >= self.deadline:
                self.discard()
                self.log("STOPPED night deadline reached")
                return "STOPPED", ""
            if result.hit_limit:
                wait = self.cfg["limit_sleep_minutes"] * 60
                if limit_waits >= self.cfg["max_limit_waits"] or time.monotonic() + wait > self.deadline:
                    self.discard()
                    self.log("STOPPED agent limit wait budget exhausted or next wait exceeds deadline")
                    return "STOPPED", ""
                limit_waits += 1
                self.log(f"Agent usage limit: waiting {self.cfg['limit_sleep_minutes']} minutes ({limit_waits} of {self.cfg['max_limit_waits']})")
                time.sleep(wait)
                continue
            limit_waits = 0
            try:
                updated = Repository(self.root).load_change(change.path)
                if "blocked" in updated.fm:
                    return "BLOCKED", updated.fm["blocked"]
                if updated.fm["module"] != change.fm["module"]:
                    errors = [f"change module must remain {change.fm['module']}"]
                elif result.exit_code:
                    errors = [f"Agent {'timed out' if result.timed_out else 'exited ' + str(result.exit_code)}", *result.output.splitlines()]
                else:
                    errors = self.verify(updated, phase, baseline)
                    if not errors and phase == 2:
                        errors = self.apply_and_check(updated)
            except (InvalidDocument, KeyError, OSError) as exc:
                errors = [m.render(self.root) for m in exc.messages] if isinstance(exc, InvalidDocument) else [str(exc)]
            if not errors:
                return "DONE", ""
            output = "\n".join(errors)
            self.log(output)
            attempt += 1
        return "FAILED", f"phase {phase} failed verification after {maximum} attempts: {errors[0]}"

    def process_change(self, change: Doc) -> str:
        for phase in ([1, 2] if is_spec_change(change) else [2]):
            result, reason = self.run_phase(change, phase)
            if result == "STOPPED":
                return result
            if result == "BLOCKED":
                return self.commit_blocked(change)
            if result == "FAILED":
                return self.mark_blocked(change, reason)
            prefix = "evals" if phase == 1 else "impl"
            self.commit(f"{prefix}({change.fm['module']}): {change.path.stem}", change, all_files=True)
        self.log(f"DONE {change.path.stem}")
        return "DONE"


def check_repository(root: Path, log=print) -> bool:
    messages = Repository(root).check()
    for message in messages:
        log(message.render(root))
    return not any(message.level == "ERROR" for message in messages)


def dry_run(root: Path, cfg: dict, skip_blocked: bool, maximum: int) -> int:
    if cfg["sandbox"] == "none":
        print("WARN sandbox: none; agent runs directly on the host")
    if not check_repository(root):
        return 1
    repo = Repository(root)
    count = 0
    for path in repo.change_paths():
        change = repo.load_change(path)
        if "blocked" in change.fm:
            if not skip_blocked:
                print(f"queue halted at {path.stem}: {change.fm['blocked']}")
                break
            print(f"SKIP {path.stem}: {change.fm['blocked']}")
            continue
        phases = "evals, code" if is_spec_change(change) else "code"
        print(f"PLAN {path.stem} ({change.fm['module']}): {phases}")
        count += 1
        if count >= maximum:
            break
    return 0


def run_night(root: Path, cfg: dict, skip_blocked: bool, maximum: int) -> int:
    git = Git(root)
    if git.run("status", "--porcelain"):
        raise UsageError("working tree must be clean")
    remote_command(["gh", "auth", "status"], root)
    git.run("fetch", cfg["remote"])
    today = date.today().isoformat()
    branch = f"night/{today}"
    local = set(git.run("for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines())
    remote = {line.split("\t", 1)[1].removeprefix("refs/heads/")
              for line in git.run("ls-remote", "--heads", cfg["remote"]).splitlines()}
    suffix = 2
    while branch in local | remote:
        branch = f"night/{today}-{suffix}"
        suffix += 1
    base = f"{cfg['remote']}/{cfg['base_branch']}"
    git.run("switch", "-c", branch, base)
    worker = Worker(root, cfg, skip_blocked=skip_blocked)
    if cfg["sandbox"] == "none":
        worker.log("WARN sandbox: none; agent runs directly on the host")
    if not check_repository(root, worker.log):
        return 1
    worker.deadline = time.monotonic() + cfg["max_hours"] * 3600
    done_count = 0
    while done_count < maximum and time.monotonic() < worker.deadline:
        repo = Repository(root)
        change = None
        for path in repo.change_paths():
            candidate = repo.load_change(path)
            if "blocked" not in candidate.fm:
                change = candidate
                break
            if not skip_blocked:
                worker.log(f"queue halted at {path.stem}: {candidate.fm['blocked']}")
                break
        if change is None:
            break
        result = worker.process_change(change)
        if result == "STOPPED" or (result == "BLOCKED" and not skip_blocked):
            break
        if result == "DONE":
            done_count += 1
    if not int(git.run("rev-list", "--count", f"{base}..HEAD").strip()):
        git.run("switch", cfg["base_branch"])
        git.run("branch", "-D", branch)
        return 0
    remote_command(["git", "push", "-u", cfg["remote"], branch], root)
    body = pr_body(root, f"{base}..HEAD", worker.log_path)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".md") as body_file:
        body_file.write(body)
        body_file.flush()
        output = remote_command(["gh", "pr", "create", "--base", cfg["base_branch"], "--head", branch,
                                 "--title", f"Night {today}: {done_count} change(s)",
                                 "--body-file", body_file.name], root)
    if output:
        worker.log(output)
    return 0


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    root = (root or Path.cwd()).resolve()
    try:
        parser = Parser(prog="night")
        commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
        once = commands.add_parser("once")
        once.add_argument("change_file")
        run = commands.add_parser("run")
        run.add_argument("--dry-run", action="store_true")
        run.add_argument("--skip-blocked", action="store_true")
        run.add_argument("--max-changes", type=int)
        commands.add_parser("login")
        args = parser.parse_args(argv)
        cfg = load_config(root)
        if args.command == "login":
            if cfg["sandbox"] != "docker":
                raise UsageError("login requires sandbox: docker")
            return subprocess.run([*docker_command(cfg, root, interactive=True), cfg["agent"]], cwd=root).returncode
        if args.command == "run":
            maximum = args.max_changes if args.max_changes is not None else cfg["max_changes"]
            if maximum < 1:
                raise UsageError("--max-changes must be a positive integer")
            if args.dry_run:
                return dry_run(root, cfg, args.skip_blocked, maximum)
            return run_night(root, cfg, args.skip_blocked, maximum)
        git = Git(root)
        if git.run("status", "--porcelain"):
            raise UsageError("working tree must be clean")
        repo = Repository(root)
        messages = repo.check()
        for message in messages:
            print(message.render(root))
        if any(m.level == "ERROR" for m in messages):
            return 1
        path = (root / args.change_file).resolve()
        if path not in repo.change_paths():
            raise UsageError("once requires a pending change file")
        change = repo.load_change(path)
        if "blocked" in change.fm:
            raise UsageError(f"change is blocked: {change.fm['blocked']}")
        if path != repo.change_paths()[0]:
            raise UsageError("change is not the queue head")
        worker = Worker(root, cfg)
        if cfg["sandbox"] == "none":
            worker.log("WARN sandbox: none; agent runs directly on the host")
        return 1 if worker.process_change(change) == "BLOCKED" else 0
    except InvalidDocument as exc:
        for message in exc.messages:
            print(message.render(root))
        return 1
    except (UsageError, GitError, yaml.YAMLError) as exc:
        print(f"ERROR usage night: {str(exc).replace(chr(10), ' ')}")
        return 2
    except Exception as exc:
        if os.environ.get("SPECFLOW_DEBUG") == "1":
            traceback.print_exc()
        print(f"ERROR tooling night: {type(exc).__name__}: {str(exc).replace(chr(10), ' ')}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
