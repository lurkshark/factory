import contextlib
from datetime import date
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from specflow.cli import main
from specflow.git import Git
from specflow.night import AgentResult, docker_command, run_agent
from tests.test_phase4 import TOOLS, WorkerFixture

FAKE = Path(__file__).with_name("fake_regen_agent.py")
EVAL = '''import sys
from pathlib import Path
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path.cwd()))
from pilot import answer
suite = ET.Element("testsuite")
case = ET.SubElement(suite, "testcase", name="PILOT-001: answer is an integer")
if not isinstance(answer(), int):
    ET.SubElement(case, "failure", message="expected integer")
path = Path("evals/.results/junit.xml")
path.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(suite).write(path)
'''


class RegenerationTests(WorkerFixture):
    def setUp(self):
        super().setUp()
        self.write(".gitignore", (self.root / ".gitignore").read_text() + ".worktrees/\n")
        self.write("packages/pilot/evals/run.py", EVAL)
        self.write("docs/playbooks/regen.md", (TOOLS.parent / "docs/playbooks/regen.md").read_text())
        self.write("packages/pilot/src/migrations/deep/002.sql", "SELECT 2;\n")
        self.write("packages/pilot/src/migrationsx/delete.sql", "SELECT 0;\n")
        self.write("packages/pilot/src/nested/api.pyi", "disposable nested stub\n")
        self.write("packages/pilot/src/disposable/deep/.gitkeep", "")
        self.write("packages/pilot/src/.hidden", "disposable hidden file\n")
        self.write("packages/pilot/README.md", "metadata survives\n")
        self.cfg["agent_commands_no_sandbox"] = {
            agent: [sys.executable, str(FAKE), "{prompt}"] for agent in ("claude", "codex")
        }
        self.save_config()
        self.base = self.commit("Regeneration fixture")
        self.worktree = self.root / ".worktrees/regen-pilot"
        self.branch = f"regen/pilot-{date.today():%Y%m%d}"
        self.original_branch = self.git.run("branch", "--show-current").strip()
        self.original = self.snapshot(self.root)

    def snapshot(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
                if p.is_file() and not p.is_symlink() and p.relative_to(root).parts[0] not in {".git", ".worktrees"}}

    def regen(self, *args, expected=0):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = main(["regen", *(args or ("pilot",))], self.root)
        self.assertEqual(expected, result, output.getvalue())
        return output.getvalue()

    def assert_original_untouched(self):
        self.assertEqual(self.original, self.snapshot(self.root))
        self.assertEqual(self.original_branch, self.git.run("branch", "--show-current").strip())
        self.assertEqual(self.base, self.git.run("rev-parse", "HEAD").strip())
        self.assertEqual("", self.git.run("status", "--porcelain"))

    def test_preserved_files_survive_disposable_files_and_empty_directories_removed(self):
        with patch("specflow.regen.run_agent", side_effect=AssertionError("manual mode ran agent")), \
                patch("specflow.regen.load_config", side_effect=AssertionError("manual mode loaded config")):
            output = self.regen()
        self.assertIn("Deleted 5 file(s); preserved 4 file(s).", output)
        self.assertIn(f"Run your agent in {self.worktree} with docs/playbooks/regen.md.", output)
        surviving = self.snapshot(self.worktree)
        expected_deleted = {
            "packages/pilot/src/answer.py", "packages/pilot/src/migrationsx/delete.sql",
            "packages/pilot/src/nested/api.pyi", "packages/pilot/src/disposable/deep/.gitkeep",
            "packages/pilot/src/.hidden",
        }
        self.assertEqual({p: data for p, data in self.original.items() if p not in expected_deleted}, surviving)
        for directory in ("migrationsx", "nested", "disposable"):
            self.assertFalse((self.worktree / "packages/pilot/src" / directory).exists())
        self.assertEqual(self.base, self.git.run("rev-parse", self.branch).strip())
        self.assertEqual(self.branch, Git(self.worktree).run("branch", "--show-current").strip())
        self.assertIn(str(self.worktree), self.git.run("worktree", "list", "--porcelain"))
        self.assert_original_untouched()

    def test_agent_runs_in_worktree_then_coverage_prints_result(self):
        output = self.regen("pilot", "--agent")
        self.assertIn("Scripted regeneration complete.", output)
        self.assertIn("PILOT-001 blackbox pass (1 tests)", output)
        self.assertIn("WARN sandbox: none", output)
        call = json.loads((self.worktree / ".night/regen-call.json").read_text())
        self.assertEqual({"cwd": str(self.worktree), "prompt": "Follow docs/playbooks/regen.md exactly. Module: pilot."}, call)
        self.assertTrue((self.worktree / "packages/pilot/REGEN-NOTES.md").exists())
        log = self.worktree / ".night" / date.today().isoformat() / "regen-pilot-agent.log"
        self.assertIn("Scripted regeneration complete.", log.read_text())
        self.assertEqual(self.base, self.git.run("rev-parse", self.branch).strip())
        self.assert_original_untouched()

    def test_docker_runner_mounts_worktree_and_read_only_git_metadata(self):
        self.regen()
        self.cfg["sandbox"] = "docker"
        self.cfg["agent_commands"] = {agent: ["scripted-agent", "{prompt}"] for agent in ("claude", "codex")}
        command = docker_command(self.cfg, self.worktree)
        git_dir = Path(Git(self.worktree).run("rev-parse", "--absolute-git-dir").strip())
        self.assertEqual(["docker", "run", "--rm", "-v", f"{self.worktree}:/workspace", "-w", "/workspace"], command[:7])
        self.assertIn(f"{self.root / '.git'}:/specflow-git:ro", command)
        self.assertIn("GIT_DIR=/specflow-git/" + git_dir.relative_to(self.root / ".git").as_posix(), command)
        self.assertIn("GIT_WORK_TREE=/workspace", command)
        self.assertIn("GIT_OPTIONAL_LOCKS=0", command)
        self.assertNotIn(f"{self.root}:/workspace", command)
        real_run = subprocess.run
        calls = []
        def docker_or_git(args, **kwargs):
            if args[0] == "git":
                return real_run(args, **kwargs)
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, "Docker agent output\n", "")
        log = self.worktree / ".night/docker-agent.log"
        with patch("specflow.night.subprocess.run", side_effect=docker_or_git):
            result = run_agent("regenerate", log, self.cfg, self.worktree)
        self.assertEqual(0, result.exit_code)
        self.assertEqual(command + ["scripted-agent", "regenerate"], calls[0][0])
        self.assertEqual(self.worktree, calls[0][1]["cwd"])
        self.assert_original_untouched()

    def test_agent_zero_exit_does_not_hide_coverage_failure(self):
        with patch.dict(os.environ, {"SPECFLOW_TEST_REGEN_ACTION": "failing-eval"}):
            output = self.regen("pilot", "--agent", expected=1)
        self.assertIn("ERROR E04", output)
        self.assertTrue(self.worktree.is_dir())
        self.assertEqual(self.base, self.git.run("rev-parse", self.branch).strip())
        self.assert_original_untouched()

    def test_agent_failure_still_runs_coverage_and_keeps_worktree(self):
        with patch.dict(os.environ, {"SPECFLOW_TEST_REGEN_ACTION": "agent-error"}):
            output = self.regen("pilot", "--agent", expected=1)
        self.assertIn("PILOT-001 blackbox pass", output)
        self.assertTrue(self.worktree.is_dir())
        self.assert_original_untouched()

    def test_missing_agent_and_report_produce_failure_without_removing_worktree(self):
        for agent in ("claude", "codex"):
            self.cfg["agent_commands_no_sandbox"][agent] = ["/nonexistent/specflow-agent", "{prompt}"]
        self.save_config()
        self.commit("Missing agent command")
        output = self.regen("pilot", "--agent", expected=1)
        self.assertIn("Agent could not start", output)
        self.assertIn("ERROR E01", output)
        self.assertTrue(self.worktree.is_dir())

    def test_timeout_is_failure_even_if_coverage_passes(self):
        def timed_out(prompt, log_path, cfg, root):
            (root / "packages/pilot/src/answer.py").write_text("def answer(): return 42\n")
            return AgentResult(1, "Agent attempt timed out.\n", True, False)
        with patch("specflow.regen.run_agent", side_effect=timed_out):
            output = self.regen("pilot", "--agent", expected=1)
        self.assertIn("Agent attempt timed out", output)
        self.assertIn("PILOT-001 blackbox pass", output)

    def test_agent_commits_are_undone_and_source_work_retained(self):
        def committing_agent(prompt, log_path, cfg, root):
            (root / "packages/pilot/src/answer.py").write_text("def answer(): return 42\n")
            git = Git(root)
            git.run("add", "-A")
            git.run("commit", "-q", "-m", "Unauthorized agent commit")
            return AgentResult(0, "", False, False)
        with patch("specflow.regen.run_agent", side_effect=committing_agent):
            output = self.regen("pilot", "--agent")
        self.assertIn("WARN agent committed; commits were undone", output)
        self.assertEqual(self.base, self.git.run("rev-parse", self.branch).strip())
        self.assertIn("return 42", (self.worktree / "packages/pilot/src/answer.py").read_text())
        self.assert_original_untouched()

    def test_dirty_tracked_staged_and_untracked_changes_refuse_before_worktree_creation(self):
        for kind in ("tracked", "staged", "untracked"):
            with self.subTest(kind=kind):
                path = "dirty.txt" if kind == "untracked" else "packages/pilot/src/answer.py"
                self.write(path, "dirty\n")
                if kind == "staged":
                    self.git.run("add", "--", path)
                output = self.regen(expected=2)
                self.assertIn("working tree must be clean", output)
                self.assertFalse(self.worktree.exists())
                self.assertEqual("dirty\n", (self.root / path).read_text())
                self.git.run("reset", "--hard", self.base)
                if kind == "untracked":
                    (self.root / path).unlink()

    def test_invalid_unknown_and_system_modules_do_not_create_a_worktree(self):
        for module in ("system", "unknown", "../pilot", "Pilot"):
            with self.subTest(module=module):
                self.regen(module, expected=2)
        self.assertFalse((self.root / ".worktrees").exists())
        self.assert_original_untouched()

    def test_bad_interface_protection_refuses_before_deleting_source(self):
        path = self.root / "packages/pilot/SPEC.md"
        path.write_text(path.read_text().replace("src/api.pyi, ", ""))
        self.commit("Unprotected interface")
        self.assertIn("ERROR C11", self.regen(expected=1))
        self.assertFalse(self.worktree.exists())

    def test_existing_worktree_and_branch_collisions_leave_existing_work_intact(self):
        self.regen()
        (self.worktree / "packages/pilot/REGEN-NOTES.md").write_text("existing work\n")
        self.regen(expected=2)
        self.assertEqual("existing work\n", (self.worktree / "packages/pilot/REGEN-NOTES.md").read_text())
        self.assert_original_untouched()

    def test_existing_branch_refuses_without_creating_worktree(self):
        self.git.run("branch", self.branch)
        self.regen(expected=2)
        self.assertFalse(self.worktree.exists())
        self.assert_original_untouched()

    def test_no_preserve_globs_removes_source_directory(self):
        path = self.root / "packages/pilot/SPEC.md"
        path.write_text(path.read_text().replace("interface: src/api.pyi\n", "")
                        .replace("preserve: [src/api.pyi, src/migrations/**, src/settings.json]\n", "")
                        .replace("append_only: [src/migrations/**]\n", ""))
        self.commit("Fully disposable source")
        self.assertIn("Deleted 9 file(s); preserved 0 file(s).", self.regen())
        self.assertFalse((self.worktree / "packages/pilot/src").exists())
        self.assertTrue((self.root / "packages/pilot/src/answer.py").exists())

    def test_no_source_directory_is_a_valid_noop(self):
        self.write("packages/empty/SPEC.md", "---\nname: empty\nkind: library\nid_prefix: EMPTY\ndepends_on: []\n---\n\n## Specs\n")
        self.commit("Empty package")
        output = self.regen("empty")
        self.assertIn("Deleted 0 file(s); preserved 0 file(s).", output)
        self.assertTrue((self.root / ".worktrees/regen-empty").is_dir())

    def test_symlinks_are_not_followed_and_preserved_symlinks_survive(self):
        target = self.root / "packages/pilot/README.md"
        (self.root / "packages/pilot/src/settings-link.json").symlink_to("../README.md")
        (self.root / "packages/pilot/src/disposable-link").symlink_to("..", target_is_directory=True)
        (self.root / "packages/pilot/src/broken-link").symlink_to("missing")
        path = self.root / "packages/pilot/SPEC.md"
        path.write_text(path.read_text().replace("src/settings.json]", "src/settings.json, src/settings-link.json]"))
        self.base = self.commit("Source symlinks")
        self.original = self.snapshot(self.root)
        output = self.regen()
        self.assertIn("Deleted 7 file(s); preserved 5 file(s).", output)
        src = self.worktree / "packages/pilot/src"
        self.assertTrue((src / "settings-link.json").is_symlink())
        self.assertFalse((src / "disposable-link").is_symlink())
        self.assertFalse((src / "broken-link").is_symlink())
        self.assertEqual(target.read_bytes(), (self.worktree / "packages/pilot/README.md").read_bytes())
        self.assert_original_untouched()

    def test_symlink_source_root_is_rejected_before_creating_worktree(self):
        self.git.run("rm", "-r", "--", "packages/pilot/src")
        (self.root / "packages/pilot/src").symlink_to("evals", target_is_directory=True)
        path = self.root / "packages/pilot/SPEC.md"
        path.write_text(path.read_text().replace("interface: src/api.pyi\n", ""))
        self.commit("Linked source directory")
        self.assertIn("ordinary package and src directories", self.regen(expected=2))
        self.assertFalse(self.worktree.exists())

    def test_bad_agent_configuration_fails_before_worktree_creation(self):
        self.write("tools/night.yaml", "unknown: setting\n")
        self.commit("Invalid config")
        self.assertIn("unknown night.yaml keys", self.regen("pilot", "--agent", expected=2))
        self.assertFalse(self.worktree.exists())

    def test_cli_subprocess_runs_regeneration_and_agent(self):
        result = subprocess.run([sys.executable, "-m", "specflow", "regen", "pilot", "--agent"],
                                cwd=self.root, env=os.environ | {"PYTHONPATH": str(TOOLS)}, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        self.assertIn("PILOT-001 blackbox pass", result.stdout)
        self.assert_original_untouched()
