import contextlib
from datetime import date
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from specflow.git import Git
from specflow.history import check_commits
from specflow.markdown import parse
from specflow.night import AgentResult, Worker, main, remote_command
from specflow.repository import Repository
from specflow.validation import spec_items
from tests.test_phase4 import CHANGE, TOOLS, WorkerFixture


class NightTests(WorkerFixture):
    def setUp(self):
        super().setUp()
        self.write("changes/0002-block.md", CHANGE.replace("PILOT-002", "PILOT-003"))
        self.write("changes/0003-last.md", CHANGE.replace("PILOT-002", "PILOT-004"))
        self.base = self.commit("Three queued requests")
        self.git.run("branch", "-M", "main")
        self.remote_root = self.root.parent / "remote.git"
        self.remote_root.mkdir()
        Git(self.remote_root).run("init", "--bare", "-q")
        self.git.run("remote", "add", "origin", str(self.remote_root))
        self.git.run("push", "-u", "origin", "main")
        self.commands = []
        self.bodies = []

    def remote(self, command, root):
        self.commands.append(command)
        if command[:3] == ["gh", "pr", "create"]:
            self.bodies.append(Path(command[command.index("--body-file") + 1]).read_text())
        return remote_command(command, root)

    def run_loop(self, *args, expected=0):
        with patch.dict(os.environ, {"SPECFLOW_FAKE_REMOTE": "1", "SPECFLOW_TEST_ACTION": "queue-block-middle"}), \
                patch("specflow.night.remote_command", side_effect=self.remote), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            result = main(["run", *args], self.root)
        self.assertEqual(expected, result, output.getvalue())
        return output.getvalue()

    def done(self):
        return [p.stem for p in Repository(self.root).change_paths(archived=True)]

    def test_three_changes_halt_on_second_block_and_open_one_PR(self):
        output = self.run_loop()
        self.assertEqual(["0001-answer"], self.done())
        self.assertIn("blocked", parse(self.root / "changes/0002-block.md").fm)
        self.assertTrue((self.root / "changes/0003-last.md").exists())
        self.assertEqual(["evals(pilot): 0001-answer", "impl(pilot): 0001-answer", "chore: block 0002-block"],
                         [c.subject for c in self.commits()])
        self.assertEqual(3, len(self.calls()))
        self.assertIn("FAKE git push -u origin night/", output)
        self.assertEqual(1, len(self.bodies))
        self.assertIn("0001-answer (pilot) — DONE", self.bodies[0])
        self.assertIn("0002-block (pilot) — BLOCKED", self.bodies[0])
        self.assertIn("## Night log", self.bodies[0])
        self.assertIn("WARN sandbox: none", self.bodies[0])
        self.assertIn(f"Night {date.today()}: 1 change(s)", self.commands[-1])
        self.assertFalse(Path(self.commands[-1][-1]).exists())
        self.assertEqual([], check_commits(self.root, f"{self.base}..HEAD"))
        self.assertEqual("", self.git.run("status", "--porcelain"))
        self.assertEqual(self.base, Git(self.remote_root).run("rev-parse", "main").strip())

    def test_three_changes_skip_second_block_and_complete_third(self):
        self.run_loop("--skip-blocked")
        self.assertEqual(["0001-answer", "0003-last"], self.done())
        self.assertIn("blocked", parse(self.root / "changes/0002-block.md").fm)
        self.assertEqual(["PILOT-001", "PILOT-002", "PILOT-004"],
                         [item.id for item in spec_items(Repository(self.root).specs["pilot"])])
        self.assertEqual(5, len(self.calls()))
        self.assertIn("0003-last (pilot) — DONE", self.bodies[0])
        self.assertIn(f"Night {date.today()}: 2 change(s)", self.commands[-1])
        self.assertEqual([], Repository(self.root).check())
        self.assertEqual([], check_commits(self.root, f"{self.base}..HEAD"))
        self.assertEqual("", self.git.run("status", "--porcelain"))

    def test_cli_max_changes_overrides_config_and_counts_only_completed_changes(self):
        self.cfg["max_changes"] = 1
        self.save_config()
        self.commit("Set configured maximum")
        self.git.run("push", "origin", "main")
        self.run_loop("--skip-blocked", "--max-changes", "2")
        self.assertEqual(["0001-answer", "0003-last"], self.done())

    def test_config_max_changes_stops_after_first(self):
        self.cfg["max_changes"] = 1
        self.save_config()
        self.commit("Set configured maximum")
        self.git.run("push", "origin", "main")
        self.run_loop("--skip-blocked")
        self.assertEqual(["0001-answer"], self.done())
        self.assertNotIn("blocked", parse(self.root / "changes/0002-block.md").fm)

    def test_branch_collision_checks_local_and_remote_branches(self):
        name = f"night/{date.today()}"
        self.git.run("branch", name)
        self.git.run("push", "origin", f"HEAD:refs/heads/{name}-2")
        self.run_loop("--max-changes", "1")
        self.assertEqual(name + "-3", self.git.run("branch", "--show-current").strip())
        self.assertEqual(["git", "push", "-u", "origin", name + "-3"], self.commands[-2])

    def test_dry_run_prints_phases_and_never_calls_git_agent_or_remote(self):
        self.write("untracked.txt", "dry-run may inspect a dirty tree")
        with patch("specflow.night.Git.run", side_effect=AssertionError("dry-run ran git")), \
                patch("specflow.night.run_agent", side_effect=AssertionError("dry-run ran agent")):
            output = self.run_loop("--dry-run", "--max-changes", "1")
        self.assertIn("PLAN 0001-answer (pilot): evals, code", output)
        self.assertNotIn("PLAN 0002", output)
        self.assertEqual([], self.commands)
        self.assertFalse((self.root / ".night").exists())
        self.assertEqual([], self.done())

    def test_dry_run_maintenance_and_existing_blocked_queue(self):
        self.change.write_text(CHANGE.split("## Added")[0])
        path = self.root / "changes/0002-block.md"
        path.write_text(path.read_text().replace("module: pilot\n", "module: pilot\nblocked: waiting\n"))
        output = self.run_loop("--dry-run")
        self.assertIn("PLAN 0001-answer (pilot): code", output)
        self.assertIn("queue halted at 0002-block: waiting", output)
        self.assertNotIn("PLAN 0003", output)
        output = self.run_loop("--dry-run", "--skip-blocked")
        self.assertIn("SKIP 0002-block: waiting", output)
        self.assertIn("PLAN 0003-last", output)

    def test_dirty_tree_and_bad_maximum_refuse_before_remote_calls(self):
        self.write("untracked.txt", "dirty")
        self.assertIn("working tree must be clean", self.run_loop(expected=2))
        self.assertEqual([], self.commands)
        for value in ("0", "-1", "no"):
            self.assertIn("ERROR usage", self.run_loop("--max-changes", value, expected=2))
        self.assertEqual("main", self.git.run("branch", "--show-current").strip())

    def test_authentication_failure_precedes_fetch_and_branch(self):
        from specflow.cli import UsageError
        with patch("specflow.night.remote_command", side_effect=UsageError("login required")) as remote, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(2, main(["run"], self.root))
        self.assertIn("login required", output.getvalue())
        remote.assert_called_once_with(["gh", "auth", "status"], self.root)
        self.assertEqual("main", self.git.run("branch", "--show-current").strip())
        self.assertNotIn("night/", self.git.run("branch", "--list"))

    def test_run_starts_from_fetched_remote_base_and_rejects_broken_main(self):
        self.write("SYSTEM.md", "broken\n")
        self.commit("Broken remote main")
        self.git.run("push", "origin", "main")
        self.git.run("switch", "-c", "local-good", self.base)
        output = self.run_loop(expected=1)
        self.assertIn("C01", output)
        self.assertFalse((self.root / ".night/calls.json").exists())
        self.assertEqual([["gh", "auth", "status"]], self.commands)

    def test_empty_queue_removes_unused_branch_and_returns_to_base(self):
        for path in Repository(self.root).change_paths():
            path.unlink()
        self.commit("Empty queue")
        self.git.run("push", "origin", "main")
        self.run_loop()
        self.assertEqual("main", self.git.run("branch", "--show-current").strip())
        self.assertNotIn("night/", self.git.run("branch", "--list"))
        self.assertEqual([["gh", "auth", "status"]], self.commands)

    def test_preblocked_head_halts_and_removes_unused_branch(self):
        self.change.write_text(CHANGE.replace("module: pilot\n", "module: pilot\nblocked: waiting\n"))
        self.commit("Block queue head")
        self.git.run("push", "origin", "main")
        output = self.run_loop()
        self.assertIn("queue halted at 0001-answer: waiting", output)
        self.assertEqual("main", self.git.run("branch", "--show-current").strip())
        self.assertEqual([], self.done())
        self.assertEqual([], self.bodies)

    def test_stopped_worker_finishes_without_retrying_the_change(self):
        with patch("specflow.night.Worker.process_change", return_value="STOPPED") as process:
            self.run_loop()
        self.assertEqual(1, process.call_count)
        self.assertEqual("main", self.git.run("branch", "--show-current").strip())

    def test_limit_stop_publishes_completed_work(self):
        def agent(prompt, log_path, cfg, root):
            if "0002-block" in prompt:
                return AgentResult(1, "usage limit", False, True)
            return self.original_agent(prompt, log_path, cfg, root)
        from specflow.night import run_agent
        self.original_agent = run_agent
        with patch("specflow.night.run_agent", side_effect=agent), patch("specflow.night.time.sleep") as sleep:
            output = self.run_loop("--skip-blocked")
        self.assertEqual(16, sleep.call_count)
        self.assertEqual(["0001-answer"], self.done())
        self.assertNotIn("blocked", parse(self.root / "changes/0002-block.md").fm)
        self.assertIn("STOPPED agent limit", output)
        self.assertEqual(1, len(self.bodies))

    def test_timeout_is_bounded_by_deadline_and_stops_cleanly(self):
        worker = Worker(self.root, self.cfg, deadline=time.monotonic() + 30)
        def timed_out(prompt, log_path, cfg, root):
            self.assertLessEqual(cfg["attempt_timeout_minutes"], 0.5)
            self.write("packages/pilot/src/attempt.py", "unfinished\n")
            worker.deadline = 0
            return AgentResult(1, "timeout", True, False)
        with patch("specflow.night.run_agent", side_effect=timed_out), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual("STOPPED", worker.process_change(parse(self.change)))
        self.assertFalse((self.root / "packages/pilot/src/attempt.py").exists())
        self.assertEqual([], self.commits())

    def test_push_failure_keeps_completed_branch_for_recovery(self):
        def failing_remote(command, root):
            from specflow.cli import UsageError
            if command[:2] == ["git", "push"]:
                raise UsageError("push rejected")
            return self.remote(command, root)
        with patch.dict(os.environ, {"SPECFLOW_FAKE_REMOTE": "1", "SPECFLOW_TEST_ACTION": "queue-block-middle"}), \
                patch("specflow.night.remote_command", side_effect=failing_remote), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(2, main(["run", "--max-changes", "1"], self.root))
        self.assertIn("push rejected", output.getvalue())
        self.assertEqual(["0001-answer"], self.done())
        self.assertEqual("", self.git.run("status", "--porcelain"))
        self.assertTrue(self.git.run("branch", "--show-current").startswith("night/"))

    def test_PR_failure_keeps_pushed_branch_and_cleans_temporary_body(self):
        from specflow.cli import UsageError
        body_path = None
        def failing_remote(command, root):
            nonlocal body_path
            if command[:3] == ["gh", "pr", "create"]:
                body_path = Path(command[-1])
                self.assertIn("0001-answer (pilot) — DONE", body_path.read_text())
                raise UsageError("PR rejected")
            return self.remote(command, root)
        with patch.dict(os.environ, {"SPECFLOW_FAKE_REMOTE": "1", "SPECFLOW_TEST_ACTION": "queue-block-middle"}), \
                patch("specflow.night.remote_command", side_effect=failing_remote), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(2, main(["run", "--max-changes", "1"], self.root))
        self.assertIn("PR rejected", output.getvalue())
        self.assertEqual(["git", "push"], self.commands[-1][:2])
        self.assertFalse(body_path.exists())
        self.assertEqual(["0001-answer"], self.done())

    def test_fake_remote_prints_while_real_remote_executes_argument_list(self):
        with patch.dict(os.environ, {"SPECFLOW_FAKE_REMOTE": "0"}), \
                patch("specflow.night.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "https://example.invalid/pr/1\n", "")) as run:
            self.assertEqual("https://example.invalid/pr/1", remote_command(["gh", "pr", "create"], self.root))
        self.assertEqual(["gh", "pr", "create"], run.call_args.args[0])
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(self.root, run.call_args.kwargs["cwd"])

    def test_all_preblocked_changes_are_skipped_without_a_PR(self):
        for path in Repository(self.root).change_paths():
            path.write_text(path.read_text().replace("module: pilot\n", "module: pilot\nblocked: waiting\n"))
        self.commit("All blocked")
        self.git.run("push", "origin", "main")
        self.run_loop("--skip-blocked")
        self.assertEqual("main", self.git.run("branch", "--show-current").strip())
        self.assertEqual([], self.done())
        self.assertEqual([], self.bodies)

    def test_deadline_stops_between_changes_and_publishes_completed_work(self):
        original = Worker.process_change
        def process(worker, change):
            result = original(worker, change)
            worker.deadline = 0
            return result
        with patch("specflow.night.Worker.process_change", new=process):
            self.run_loop("--skip-blocked")
        self.assertEqual(["0001-answer"], self.done())
        self.assertEqual(2, len(self.calls()))
        self.assertEqual(1, len(self.bodies))

    def test_run_cli_subprocess_fake_remote(self):
        env = os.environ | {"PYTHONPATH": str(TOOLS), "SPECFLOW_FAKE_REMOTE": "1", "SPECFLOW_TEST_ACTION": "queue-block-middle"}
        result = subprocess.run([sys.executable, "-m", "specflow.night", "run", "--skip-blocked"],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        self.assertIn("FAKE gh pr create", result.stdout)
        self.assertEqual(["0001-answer", "0003-last"], self.done())


class SandboxTests(WorkerFixture):
    def test_login_inherits_terminal_and_mounts_persistent_agent_home(self):
        self.cfg["sandbox"] = "docker"
        self.cfg["agent"] = "codex"
        self.cfg["docker_mounts"].append("extra-cache:/cache")
        self.save_config()
        with patch("specflow.night.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertEqual(0, main(["login"], self.root))
        self.assertEqual(["docker", "run", "-it", "--rm", "-v", f"{self.root}:/workspace", "-w", "/workspace",
                          "-v", "specflow-agent-home:/home/worker", "-v", "extra-cache:/cache", "specflow-worker", "codex"],
                         run.call_args.args[0])
        self.assertEqual({"cwd": self.root}, run.call_args.kwargs)

    def test_login_requires_docker_and_propagates_container_exit(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(2, main(["login"], self.root))
        self.assertIn("login requires sandbox: docker", output.getvalue())
        self.cfg["sandbox"] = "docker"
        self.save_config()
        with patch("specflow.night.subprocess.run", return_value=subprocess.CompletedProcess([], 7)):
            self.assertEqual(7, main(["login"], self.root))

    def test_devcontainer_reuses_worker_home_workspace_and_nonroot_user(self):
        config = json.loads((TOOLS.parent / ".devcontainer/devcontainer.json").read_text())
        self.assertEqual("Dockerfile", config["build"]["dockerfile"])
        self.assertEqual("worker", config["remoteUser"])
        self.assertEqual("/workspace", config["workspaceFolder"])
        self.assertIn("source=specflow-agent-home,target=/home/worker,type=volume", config["mounts"])
        dockerfile = (TOOLS.parent / ".devcontainer/Dockerfile").read_text()
        self.assertIn("USER worker", dockerfile)
        self.assertIn("HOME=/home/worker", dockerfile)
        self.assertNotIn("gh auth", dockerfile)
