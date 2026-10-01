import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import yaml

from specflow.cli import UsageError
from specflow.git import Git
from specflow.history import check_commits
from specflow.markdown import parse
from specflow.night import AgentResult, Worker, changed_files, is_spec_change, load_config, main, phase_prompt, run_agent
from specflow.repository import Repository
from specflow.validation import spec_items

TOOLS = Path(__file__).resolve().parents[1]
FAKE = Path(__file__).with_name("fake_agent.py")
EVAL = '''import sys
from pathlib import Path
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path.cwd()))
from pilot import answer
suite = ET.Element("testsuite")
for spec_id in ("PILOT-001", "PILOT-002"):
    case = ET.SubElement(suite, "testcase", name=spec_id + ": answer is observable")
    if spec_id == "PILOT-002" and answer() != 42:
        ET.SubElement(case, "failure", message="expected 42")
path = Path("evals/.results/junit.xml")
path.parent.mkdir(parents=True, exist_ok=True)
ET.ElementTree(suite).write(path)
'''
SPEC = '''---
name: pilot
kind: library
id_prefix: PILOT
depends_on: []
interface: src/api.pyi
preserve: [src/api.pyi, src/migrations/**, src/settings.json]
append_only: [src/migrations/**]
evals:
  run: python3 evals/run.py
  report: evals/.results/junit.xml
---

# pilot

## Specs

### PILOT-001
The public answer MUST be an integer.
'''
CHANGE = '''---
module: pilot
---

## Motivation
Provide the requested answer.

## Added

### PILOT-002
The public answer MUST equal 42.

## Decisions
'''


class WorkerFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = (Path(temporary.name) / "repo").resolve()
        self.root.mkdir()
        self.git = Git(self.root)
        self.write("SYSTEM.md", "---\nname: system\nid_prefix: SYS\n---\n\n## Specs\n")
        self.write(".gitignore", ".night/\n**/evals/.results/\n__pycache__/\n")
        self.write("packages/pilot/SPEC.md", SPEC)
        self.write("packages/pilot/src/api.pyi", "def answer() -> int: ...\n")
        self.write("packages/pilot/src/answer.py", "def answer():\n    return 0\n")
        self.write("packages/pilot/pilot.py", "from src.answer import answer\n")
        self.write("packages/pilot/src/migrations/001.sql", "SELECT 1;\n")
        self.write("packages/pilot/src/settings.json", "{}\n")
        self.write("packages/pilot/pyproject.toml", '[project]\nname = "pilot"\ndependencies = []\n')
        self.change = self.write("changes/0001-answer.md", CHANGE)
        self.write("tools/eval-template.py", EVAL)
        self.cfg = yaml.safe_load((TOOLS / "night.yaml").read_text())
        self.cfg["sandbox"] = "none"
        self.cfg["limit_sleep_minutes"] = 0.001
        self.cfg["agent_commands_no_sandbox"] = {
            "claude": [sys.executable, str(FAKE), "{prompt}"],
            "codex": [sys.executable, str(FAKE), "{prompt}"],
        }
        self.save_config()
        self.git.run("init", "-q")
        self.git.run("config", "user.name", "Worker Test")
        self.git.run("config", "user.email", "worker@example.invalid")
        self.git.run("config", "commit.gpgsign", "false")
        self.git.run("config", "core.autocrlf", "false")
        self.base = self.commit("Initial pilot")

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
        return target

    def save_config(self):
        self.write("tools/night.yaml", yaml.safe_dump(self.cfg))

    def commit(self, subject):
        self.git.run("add", "-A")
        self.git.run("commit", "-q", "-m", subject)
        return self.git.run("rev-parse", "HEAD").strip()

    def process(self, action="happy", deadline=None):
        worker = Worker(self.root, self.cfg, deadline)
        prior = worker.log_path.read_text() if worker.log_path.exists() else ""
        with patch.dict(os.environ, {"SPECFLOW_TEST_ACTION": action}), contextlib.redirect_stdout(io.StringIO()):
            result = worker.process_change(Repository(self.root).load_change(self.change))
        return result, worker.log_path.read_text()[len(prior):]

    def calls(self):
        return json.loads((self.root / ".night/calls.json").read_text())

    def commits(self):
        return self.git.commits(f"{self.base}..HEAD")

    def night_cli(self, expected=0, *args):
        env = os.environ | {"PYTHONPATH": str(TOOLS), "SPECFLOW_TEST_ACTION": "happy"}
        result = subprocess.run([sys.executable, "-m", "specflow.night", *(args or ("once", str(self.change)))],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        return result.stdout


class ProcessTests(WorkerFixture):
    def test_happy_path_two_commits_archive_spec_and_clean_history(self):
        result, log = self.process()
        self.assertEqual("DONE", result)
        commits = self.commits()
        self.assertEqual(["evals(pilot): 0001-answer", "impl(pilot): 0001-answer"], [c.subject for c in commits])
        self.assertTrue(all(c.body == "Change: 0001-answer\n" for c in commits))
        self.assertFalse(self.change.exists())
        self.assertTrue((self.root / "changes/archive/0001-answer.md").exists())
        self.assertEqual(["PILOT-001", "PILOT-002"], [i.id for i in spec_items(Repository(self.root).specs["pilot"])])
        self.assertEqual([], Repository(self.root).check())
        self.assertEqual([], check_commits(self.root, f"{self.base}..HEAD"))
        self.assertEqual([], changed_files(self.git))
        self.assertEqual(2, len(self.calls()))
        self.assertIn("DONE", log)

    def test_P1_01_retries_then_blocks_and_discards_forbidden_work(self):
        result, log = self.process("p1-src")
        self.assertEqual("BLOCKED", result)
        self.assertIn("P1-01 phase 1 touched packages/pilot/src/forbidden.py", log)
        self.assertEqual(2, len(self.calls()))
        self.assertIn("This is attempt 2 of 2", self.calls()[1])
        self.assertIn("P1-01", self.calls()[1])
        self.assertEqual(["chore: block 0001-answer"], [c.subject for c in self.commits()])
        self.assertIn("phase 1 failed verification after 2 attempts", parse(self.change).fm["blocked"])
        self.assertFalse((self.root / "packages/pilot/src/forbidden.py").exists())
        self.assertFalse((self.root / "packages/pilot/evals/run.py").exists())
        self.assertEqual([], changed_files(self.git))

    def test_P1_01_forbids_other_module_evals(self):
        _, log = self.process("other-module")
        self.assertIn("P1-01 phase 1 touched packages/other/evals/forbidden.py", log)
        self.assertFalse((self.root / "packages/other/evals/forbidden.py").exists())

    def test_P2_01_blocks_and_preserves_phase_one_commit(self):
        result, log = self.process("p2-evals")
        self.assertEqual("BLOCKED", result)
        self.assertIn("P2-01", log)
        self.assertEqual(["evals(pilot): 0001-answer", "chore: block 0001-answer"], [c.subject for c in self.commits()])
        self.assertTrue((self.root / "packages/pilot/evals/run.py").exists())
        self.assertFalse((self.root / "packages/pilot/evals/extra.py").exists())
        self.assertIn("return 0", (self.root / "packages/pilot/src/answer.py").read_text())
        self.assertEqual([], changed_files(self.git))

    def test_P2_01_interface_tools_and_other_changes(self):
        for action in ("p2-interface", "p2-tools", "p2-change"):
            with self.subTest(action=action):
                self.git.run("reset", "--hard", self.base)
                result, log = self.process(action)
                self.assertEqual("BLOCKED", result)
                self.assertIn("P2-01", log)
                self.assertEqual([], changed_files(self.git))

    def test_agent_blocks_each_phase_discards_other_edits(self):
        for phase in (1, 2):
            with self.subTest(phase=phase):
                self.git.run("reset", "--hard", self.base)
                result, _ = self.process(f"block-{phase}")
                self.assertEqual("BLOCKED", result)
                subjects = [c.subject for c in self.commits()]
                self.assertEqual(phase, len(subjects))
                self.assertEqual("chore: block 0001-answer", subjects[-1])
                self.assertFalse(any(s.startswith("impl(") for s in subjects))
                self.assertEqual("Need a clearer interface.", parse(self.change).fm["blocked"])
                self.assertIn("return 0", (self.root / "packages/pilot/src/answer.py").read_text())
                self.assertEqual([], changed_files(self.git))

    def test_limit_sleep_repeats_same_attempt_without_counting(self):
        self.cfg["max_attempts_per_phase"] = 1
        with patch("specflow.night.time.sleep") as sleep:
            result, log = self.process("limit-once")
        self.assertEqual("DONE", result)
        sleep.assert_called_once_with(0.06)
        self.assertEqual(self.calls()[0], self.calls()[1])
        self.assertEqual(3, len(self.calls()))
        self.assertIn("Agent usage limit", log)

    def test_limit_wait_budget_stops_cleanly_without_block_commit(self):
        self.cfg["max_limit_waits"] = 1
        with patch("specflow.night.time.sleep") as sleep:
            result, log = self.process("limit-always")
        self.assertEqual("STOPPED", result)
        self.assertEqual(1, sleep.call_count)
        self.assertEqual([], self.commits())
        self.assertNotIn("blocked", parse(self.change).fm)
        self.assertEqual([], changed_files(self.git))
        self.assertIn("wait budget exhausted", log)

    def test_limit_next_wait_crosses_deadline(self):
        self.cfg["limit_sleep_minutes"] = 30
        with patch("specflow.night.time.sleep") as sleep:
            result, _ = self.process("limit-always", time.monotonic() + 60)
        self.assertEqual("STOPPED", result)
        sleep.assert_not_called()

    def test_expired_deadline_does_not_run_agent(self):
        result, _ = self.process(deadline=time.monotonic() - 1)
        self.assertEqual("STOPPED", result)
        self.assertFalse((self.root / ".night/calls.json").exists())

    def test_agent_commit_soft_reset_retains_edits_and_logs_warning(self):
        result, log = self.process("commit")
        self.assertEqual("DONE", result)
        self.assertIn("WARN agent committed; commits were undone", log)
        self.assertEqual(2, len(self.commits()))
        self.assertTrue(all("Agent's" not in c.subject for c in self.commits()))

    def test_maintenance_skips_phase_one(self):
        self.change.write_text(CHANGE.split("## Added")[0] + "## Decisions\n")
        self.write("packages/pilot/evals/run.py", EVAL.replace('("PILOT-001", "PILOT-002")', '("PILOT-001",)'))
        self.base = self.commit("Maintenance request")
        result, _ = self.process()
        self.assertEqual("DONE", result)
        self.assertEqual(["impl(pilot): 0001-answer"], [c.subject for c in self.commits()])
        self.assertEqual(1, len(self.calls()))
        self.assertIn("implement-code.md", self.calls()[0])

    def test_P1_02_and_E01_E02_block_phase_one(self):
        for action, code in (("missing", "P1-02"), ("no-report", "E01"), ("orphan", "E02")):
            with self.subTest(action=action):
                self.git.run("reset", "--hard", self.base)
                result, log = self.process(action)
                self.assertEqual("BLOCKED", result)
                self.assertIn(code, log)

    def test_P1_03_fail_first_warning_does_not_fail(self):
        self.write("packages/pilot/src/answer.py", "def answer():\n    return 42\n")
        self.base = self.commit("Already implemented")
        result, log = self.process()
        self.assertEqual("DONE", result)
        self.assertIn("WARN P1-03 evals for PILOT-002 already pass", log)

    def test_P1_04_interface_warning_and_interface_edit_allowed(self):
        self.change.write_text(CHANGE + "\n## Interface\nUpdate the public annotation.\n")
        self.base = self.commit("Interface request")
        result, log = self.process()
        self.assertEqual("DONE", result)
        self.assertIn("WARN P1-04", log)
        self.git.run("reset", "--hard", self.base)
        result, log = self.process("interface")
        self.assertEqual("DONE", result)
        self.assertNotIn("WARN P1-04", log)

    def test_P2_02_append_only_modify_delete_rename(self):
        for action in ("append-modify", "append-delete", "append-rename", "append-type"):
            with self.subTest(action=action):
                self.git.run("reset", "--hard", self.base)
                result, log = self.process(action)
                self.assertEqual("BLOCKED", result)
                self.assertIn("P2-02", log)
                self.assertEqual("SELECT 1;\n", (self.root / "packages/pilot/src/migrations/001.sql").read_text())
                self.assertEqual([], changed_files(self.git))

    def test_append_only_add_and_preserved_edits_allowed_and_logged(self):
        result, log = self.process("append-add")
        self.assertEqual("DONE", result)
        self.assertIn("Preserved file changed: packages/pilot/src/migrations/002.sql", log)
        self.assertEqual([], check_commits(self.root, f"{self.base}..HEAD"))
        self.git.run("reset", "--hard", self.base)
        result, log = self.process("preserved")
        self.assertEqual("DONE", result)
        self.assertIn("Preserved file changed: packages/pilot/src/settings.json", log)

    def test_new_append_only_file_can_be_edited_before_its_first_commit(self):
        result, _ = self.process("append-staged")
        self.assertEqual("DONE", result)
        self.assertEqual("SELECT 3;\n", (self.root / "packages/pilot/src/migrations/002.sql").read_text())
        self.assertEqual([], check_commits(self.root, f"{self.base}..HEAD"))

    def test_retry_repairs_prior_work_and_can_complete(self):
        result, log = self.process("repair")
        self.assertEqual("DONE", result)
        self.assertIn("P2-01", log)
        self.assertEqual(3, len(self.calls()))
        self.assertIn("inspect it with `git status` and `git diff`", self.calls()[-1])

    def test_post_apply_check_failure_rolls_back_and_retries(self):
        result, log = self.process("bad-apply")
        self.assertEqual("DONE", result)
        self.assertIn("C06", log)
        self.assertEqual(3, len(self.calls()))
        self.assertEqual([], Repository(self.root).check())
        self.assertEqual([], changed_files(self.git))

    def test_wrong_implementation_E04_blocks(self):
        result, log = self.process("wrong-code")
        self.assertEqual("BLOCKED", result)
        self.assertIn("E04", log)

    def test_nonzero_agent_exit_is_failed_attempt(self):
        result, log = self.process("exit")
        self.assertEqual("BLOCKED", result)
        self.assertEqual(2, len(self.calls()))
        self.assertIn("Agent exited 7", log)

    def test_timeouts_count_as_failed_attempts_and_block(self):
        with patch("specflow.night.run_agent", return_value=AgentResult(1, "partial output", True, False)) as agent:
            result, log = self.process()
        self.assertEqual("BLOCKED", result)
        self.assertEqual(2, agent.call_count)
        self.assertIn("Agent timed out", log)

    def test_malformed_agent_change_is_restored_before_blocking(self):
        result, log = self.process("malformed")
        self.assertEqual("BLOCKED", result)
        self.assertIn("C09", log)
        self.assertEqual("pilot", parse(self.change).fm["module"])
        self.assertIn("blocked", parse(self.change).fm)
        self.assertEqual([], Repository(self.root).check())
        self.assertEqual([], changed_files(self.git))

    def test_agent_cannot_retarget_the_change(self):
        result, log = self.process("retarget")
        self.assertEqual("BLOCKED", result)
        self.assertIn("change module must remain pilot", log)
        self.assertEqual("pilot", parse(self.change).fm["module"])
        self.assertEqual([], Repository(self.root).check())

    def test_system_coverage_failure_blocks_module_implementation(self):
        self.write("SYSTEM.md", "---\nname: system\nid_prefix: SYS\nevals:\n  run: python3 run.py\n  report: junit.xml\n---\n\n## Specs\n\n### SYS-001\nThe integration MUST work.\n")
        self.write("system-evals/.gitignore", "junit.xml\n")
        self.write("system-evals/run.py", 'from pathlib import Path\nPath("junit.xml").write_text(\'<testsuite><testcase name="SYS-001"><failure/></testcase></testsuite>\')\n')
        self.base = self.commit("Integration evals")
        result, log = self.process()
        self.assertEqual("BLOCKED", result)
        self.assertIn("E04", log)
        self.assertIn("SYS-001", log)
        self.assertEqual(2, len(self.commits()))

    def test_system_change_allows_phase_one_system_evals_only(self):
        self.write("SYSTEM.md", "---\nname: system\nid_prefix: SYS\nevals:\n  run: python3 run.py\n  report: junit.xml\n---\n\n## Specs\n")
        self.write("system-evals/.gitignore", "junit.xml\n")
        self.change.write_text("---\nmodule: system\n---\n\n## Motivation\nAdd a system behavior.\n\n## Added\n\n### SYS-001\nThe integration MUST work.\n")
        self.base = self.commit("System request")
        result, log = self.process("system")
        self.assertEqual("BLOCKED", result)
        self.assertIn("P2-01 phase 2 touched system-evals/run.py", log)
        self.assertEqual(["evals(system): 0001-answer", "chore: block 0001-answer"], [c.subject for c in self.commits()])
        self.assertIn("SYS-001", (self.root / "system-evals/run.py").read_text())

    def test_eval_command_file_edits_are_verified_after_coverage(self):
        self.write("tools/eval-template.py", EVAL + '\nPath("src/forbidden.py").write_text("forbidden")\n')
        self.base = self.commit("Eval command mutates source")
        result, log = self.process()
        self.assertEqual("BLOCKED", result)
        self.assertIn("P1-01 phase 1 touched packages/pilot/src/forbidden.py", log)

    def test_once_cli_stays_on_current_branch_and_logs_host_warning(self):
        before = self.git.run("branch", "--show-current")
        output = self.night_cli()
        self.assertIn("WARN sandbox: none", output)
        self.assertIn("DONE 0001-answer", output)
        self.assertEqual(before, self.git.run("branch", "--show-current"))
        self.assertEqual(2, len(self.commits()))

    def test_cli_rejects_dirty_tree_invalid_file_blocked_and_nonhead(self):
        self.write("untracked.txt", "dirty\n")
        self.assertIn("working tree must be clean", self.night_cli(2))
        (self.root / "untracked.txt").unlink()
        self.assertIn("pending change", self.night_cli(2, "once", "missing.md"))
        self.change.write_text(CHANGE.replace("module: pilot\n", "module: pilot\nblocked: waiting\n"))
        self.commit("Blocked change")
        self.assertIn("change is blocked", self.night_cli(2))
        self.write("changes/0002-later.md", CHANGE.split("## Added")[0])
        self.commit("Later change")
        self.assertIn("queue head", self.night_cli(2, "once", "changes/0002-later.md"))

    def test_cli_static_check_fails_before_agent(self):
        self.write("SYSTEM.md", "invalid system\n")
        self.commit("Broken system")
        self.assertIn("C01", self.night_cli(1))
        self.assertFalse((self.root / ".night/calls.json").exists())

    def test_cli_blocked_execution_returns_one_and_leaves_clean_tree(self):
        with patch.dict(os.environ, {"SPECFLOW_TEST_ACTION": "p1-src"}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(1, main(["once", str(self.change)], self.root))
        self.assertIn("BLOCKED", output.getvalue())
        self.assertEqual([], changed_files(self.git))

    def test_changed_paths_nul_delimited_spaces_and_rename(self):
        self.write('packages/pilot/src/a "quoted" file.py', "hello\n")
        self.git.run("mv", "packages/pilot/src/migrations/001.sql", "packages/pilot/src/migrations/renamed file.sql")
        files = changed_files(self.git)
        self.assertIn('packages/pilot/src/a "quoted" file.py', [f.path for f in files])
        rename = next(f for f in files if f.old_path is not None)
        self.assertEqual("packages/pilot/src/migrations/001.sql", rename.old_path)
        self.assertEqual("packages/pilot/src/migrations/renamed file.sql", rename.path)

    def test_blocked_yaml_is_quoted_and_preserves_CRLF_body(self):
        self.change.write_bytes(CHANGE.replace("\n", "\r\n").encode())
        self.base = self.commit("CRLF request")
        worker = Worker(self.root, self.cfg)
        original = self.change.read_bytes()
        reason = "agent's answer: needs 'clarity'"
        with contextlib.redirect_stdout(io.StringIO()):
            worker.mark_blocked(parse(self.change), reason)
        self.assertEqual(reason, parse(self.change).fm["blocked"])
        inserted = ("blocked: 'agent''s answer: needs ''clarity'''\r\n").encode()
        self.assertEqual(original, self.change.read_bytes().replace(inserted, b""))


class ConfigAndAgentTests(WorkerFixture):
    def test_defaults_unknown_keys_invalid_values_and_regexes(self):
        self.write("tools/night.yaml", "agent: codex\n")
        cfg = load_config(self.root)
        self.assertEqual("codex", cfg["agent"])
        self.assertEqual(90, cfg["attempt_timeout_minutes"])
        self.assertEqual(2, cfg["max_attempts_per_phase"])
        self.assertEqual("docker", cfg["sandbox"])
        for text in ("unknown: true\n", "max_attempts_per_phase: 0\n", "sandbox: invalid\n",
                     "limit_patterns: ['[']\n", "agent_commands: {claude: [echo]}\n", "[]\n"):
            with self.subTest(text=text):
                self.write("tools/night.yaml", text)
                with self.assertRaises(UsageError):
                    load_config(self.root)

    def test_missing_config_uses_defaults(self):
        (self.root / "tools/night.yaml").unlink()
        self.assertEqual("claude", load_config(self.root)["agent"])

    def test_run_agent_argument_list_timeout_output_and_limit_detection(self):
        log = self.root / ".night/attempt.log"
        completed = subprocess.CompletedProcess([], 1, stdout="USAGE LIMIT", stderr=" stderr")
        with patch("specflow.night.subprocess.run", return_value=completed) as run:
            result = run_agent("literal $(never-run)", log, self.cfg, self.root)
        self.assertTrue(result.hit_limit)
        self.assertFalse(result.timed_out)
        self.assertEqual("USAGE LIMIT stderr", log.read_text())
        args, kwargs = run.call_args
        self.assertEqual("literal $(never-run)", args[0][-1])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(self.root, kwargs["cwd"])
        self.assertEqual(5400, kwargs["timeout"])
        with patch("specflow.night.subprocess.run", side_effect=subprocess.TimeoutExpired([], 1, output=b"partial", stderr=b"timeout")):
            result = run_agent("prompt", log, self.cfg, self.root)
        self.assertTrue(result.timed_out)
        self.assertIn("partialtimeout", log.read_text())
        with patch("specflow.night.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="quota", stderr="")):
            self.assertFalse(run_agent("prompt", log, self.cfg, self.root).hit_limit)

    def test_docker_prefix_and_command_defaults(self):
        self.cfg["sandbox"] = "docker"
        self.cfg["agent"] = "codex"
        self.cfg["agent_commands"] = {}
        with patch("specflow.night.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout="", stderr="")) as run:
            run_agent("prompt", self.root / ".night/attempt.log", self.cfg, self.root)
        command = run.call_args.args[0]
        self.assertEqual(["docker", "run", "--rm", "-v", f"{self.root}:/workspace", "-w", "/workspace",
                          "-v", "specflow-agent-home:/home/worker", "specflow-worker"], command[:10])
        self.assertEqual(["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "prompt"], command[10:])

    def test_prompt_templates_retry_80_lines_and_change_kind(self):
        change = parse(self.change)
        expected = ("You are running unattended. Follow docs/playbooks/implement-evals.md exactly.\n"
                    "Change file: changes/0001-answer.md\nModule: pilot\n"
                    "Do not run git commit, git push, or any git command that changes history.\n")
        self.assertEqual(expected, phase_prompt(change, self.root, 1, 1, 2, ""))
        retry = phase_prompt(change, self.root, 2, 2, 2, "\n".join(f"line {i}" for i in range(100)))
        self.assertNotIn("\nline 19\n", retry)
        self.assertIn("\nline 20\n", retry)
        self.assertIn("\nline 99\n", retry)
        self.assertTrue(is_spec_change(change))
        self.change.write_text(CHANGE.split("## Added")[0].replace("module: pilot\n", "module: pilot\nset_depends_on: []\n"))
        self.assertFalse(is_spec_change(parse(self.change)))


if __name__ == "__main__":
    unittest.main()
