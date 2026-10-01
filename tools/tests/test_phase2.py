import shutil
import subprocess
import unittest

from specflow.coverage import coverage, parse_junit, references
from specflow.git import Git
from specflow.history import check_commits
from specflow.pr import pr_body
from specflow.repository import Repository
from specflow.zones import Zones
from tests.test_phase1 import FixtureTest


class CoverageTests(FixtureTest):
    def set_report(self, xml, exit_code=0, directory="packages/auth", report="evals/.results/junit.xml"):
        self.write(f"{directory}/evals/run.py", "from pathlib import Path\n"
                   f"p = Path({report!r})\np.parent.mkdir(parents=True, exist_ok=True)\n"
                   f"p.write_text({xml!r}, encoding='utf-8')\nraise SystemExit({exit_code})\n")

    def result(self, change=None):
        return coverage(Repository(self.root), "auth", change)

    def test_fake_fixture_coverage_and_cli(self):
        result = self.result()
        self.assertEqual([("AUTH-001", "pass"), ("AUTH-002", "fail")], [(s.id, s.status) for s in result.specs])
        self.assertEqual([("E04", "WARN")], [(m.code, m.level) for m in result.messages])
        output = self.cli("coverage", "auth")
        self.assertIn("AUTH-001 blackbox pass (1 tests)", output)
        self.assertIn("AUTH-002 quarantine fail (1 tests)", output)
        self.assertIn("WARN E04", output)
        self.assertTrue(output.endswith("0 error(s), 1 warning(s)\n"))

    def test_junit_roots_depth_statuses_and_missing_attributes(self):
        cases = ('<testcase classname="AUTH-001" name="passes"/>'
                 '<testcase name="fails"><failure/></testcase>'
                 '<testcase name="errors"><error/></testcase>'
                 '<testcase name="skips"><skipped/></testcase>'
                 '<testcase name="both"><skipped/><failure/></testcase>'
                 '<testcase/>')
        for xml in (f"<testsuite>{cases}</testsuite>", f"<testsuites><testsuite><testsuite>{cases}</testsuite></testsuite></testsuites>"):
            with self.subTest(xml=xml):
                tests = parse_junit(self.write("results.xml", xml))
                self.assertEqual(["pass", "fail", "fail", "skip", "fail", "pass"], [t.status for t in tests])
                self.assertEqual("AUTH-001 passes", tests[0].label)
                self.assertEqual(" ", tests[-1].label)

    def test_id_matching_boundaries(self):
        for label in ("AUTH-003:", "(AUTH-003)", "AUTH-003", "[AUTH-003]", "AUTH-003a"):
            with self.subTest(label=label):
                self.assertTrue(references(label, "AUTH-003"))
        for label in ("AUTH-0031", "XAUTH-003", "1AUTH-003", "aAUTH-003"):
            with self.subTest(label=label):
                self.assertFalse(references(label, "AUTH-003"))

    def test_orphans_known_prefixes_unknown_prefixes_ignored(self):
        self.set_report('<testsuite><testcase name="AUTH-001"/>'
                        '<testcase name="AUTH-999 AUTH-999 CLOCK-999 SYS-999 UTF-008 XAUTH-001"/></testsuite>')
        messages = self.result().messages
        orphans = [m for m in messages if m.code == "E02"]
        self.assertEqual(3, len(orphans))
        for candidate in ("AUTH-999", "CLOCK-999", "SYS-999"):
            self.assertTrue(any(f"unknown spec {candidate}:" in m.message for m in orphans))
        output = self.cli("coverage", "auth", expected=1)
        self.assertNotIn("unknown spec UTF-008", output)
        self.assertNotIn("unknown spec XAUTH-001", output)

    def test_boundary_does_not_cover_shorter_id(self):
        self.set_report('<testsuite><testcase name="AUTH-0011 XAUTH-001"/></testsuite>')
        result = self.result()
        self.assertEqual("uncovered", result.specs[0].status)
        self.assertEqual(0, len(result.specs[0].tests))
        self.assertEqual(1, sum(m.code == "E02" for m in result.messages))

    def test_testcase_can_reference_multiple_specs_in_classname_and_name(self):
        self.set_report('<testsuite><testcase classname="AUTH-001" name="(AUTH-002)"/></testsuite>')
        result = self.result()
        self.assertEqual([], result.messages)
        self.assertEqual(["pass", "pass"], [s.status for s in result.specs])
        self.assertEqual([1, 1], [len(s.tests) for s in result.specs])

    def test_multiline_test_label_keeps_diagnostic_on_one_line(self):
        self.set_report('<testsuite><testcase name="AUTH-001&#10;fails"><failure/></testcase></testsuite>')
        output = self.cli("coverage", "auth", expected=1)
        line = next(line for line in output.splitlines() if "ERROR E04" in line)
        self.assertIn("AUTH-001 fails", line)
        self.assertEqual(5, len(output.splitlines()))

    def test_blackbox_failures_and_errors_override_passes_skips_ignored(self):
        for failure in ("failure", "error"):
            with self.subTest(failure=failure):
                self.set_report('<testsuite><testcase name="AUTH-001 passes"/>'
                                f'<testcase name="AUTH-001 fails"><{failure}/></testcase>'
                                '<testcase name="AUTH-001 skipped"><skipped/></testcase></testsuite>', exit_code=1)
                result = self.result()
                self.assertEqual("fail", result.specs[0].status)
                errors = [m for m in result.messages if m.level == "ERROR"]
                self.assertEqual(["E04"], [m.code for m in errors])
                self.assertIn("AUTH-001 fails", errors[0].message)
                self.assertNotIn("skipped", errors[0].message)

    def test_pass_plus_skip_covers_but_only_skips_are_uncovered(self):
        self.set_report('<testsuite><testcase name="AUTH-001 skipped"><skipped/></testcase>'
                        '<testcase name="AUTH-001 passes"/></testsuite>')
        self.assertEqual("pass", self.result().specs[0].status)
        self.set_report('<testsuite><testcase name="AUTH-001 skipped"><skipped/></testcase>'
                        '<testcase name="AUTH-002 skipped"><skipped/></testcase></testsuite>')
        result = self.result()
        self.assertEqual(["uncovered", "uncovered"], [s.status for s in result.specs])
        self.assertEqual({("E03", "ERROR"), ("E03", "WARN")}, {(m.code, m.level) for m in result.messages})
        self.cli("coverage", "auth", expected=1)

    def test_review_has_no_requirement_and_reports_referenced_failures(self):
        self.mutate("packages/auth/SPEC.md", "### AUTH-001", "### AUTH-001 [review]")
        self.set_report("<testsuite/>")
        self.assertEqual("n/a", self.result().specs[0].status)
        self.assertFalse(any(m.level == "ERROR" for m in self.result().messages))
        self.set_report('<testsuite><testcase name="AUTH-001"><error/></testcase></testsuite>')
        self.assertEqual("fail", self.result().specs[0].status)
        output = self.cli("coverage", "auth")
        self.assertIn("AUTH-001 review fail (1 tests)", output)
        self.assertNotIn("E04", output)

    def test_nonzero_eval_exit_is_not_a_tooling_error_with_valid_report(self):
        self.set_report('<testsuite><testcase name="AUTH-001"/></testsuite>', exit_code=17)
        self.assertFalse(any(m.level == "ERROR" for m in self.result().messages))
        self.cli("coverage", "auth")

    def test_with_change_new_id_is_known_required_and_specs_are_untouched(self):
        original = self.spec.read_bytes()
        self.set_report('<testsuite><testcase name="AUTH-001"/><testcase name="AUTH-003:"/></testsuite>')
        self.assertTrue(any(m.code == "E02" for m in self.result().messages))
        self.assertEqual([], self.result(self.change).messages)
        output = self.cli("coverage", "auth", "--with-change", "changes/0002-login.md")
        self.assertIn("AUTH-003 blackbox pass (1 tests)", output)
        self.assertNotIn("AUTH-002", output)
        self.set_report('<testsuite><testcase name="AUTH-001"/></testsuite>')
        result = self.result(self.change)
        self.assertEqual(["E03"], [m.code for m in result.messages])
        self.assertIn("AUTH-003 uncovered", self.cli("coverage", "auth", "--with-change", "changes/0002-login.md", expected=1))
        self.assertEqual(original, self.spec.read_bytes())
        self.assertTrue(self.change.exists())

    def test_with_change_does_not_replay_other_pending_changes(self):
        self.write("changes/0000-remove-login.md", self.change_text("## Removed\n### AUTH-001\n"))
        self.set_report('<testsuite><testcase name="AUTH-003"/></testsuite>')
        self.assertEqual([], self.result(self.change).messages)

    def test_removed_id_becomes_orphan(self):
        result = self.result(self.change)
        self.assertTrue(any(m.code == "E02" and "AUTH-002" in m.message for m in result.messages))

    def test_with_change_wrong_module_missing_targets_and_bad_apply(self):
        self.write("changes/0003-wrong.md", self.change_text("").replace("module: auth", "module: clock"))
        self.assertIn("C09", self.cli("coverage", "auth", "--with-change", "changes/0003-wrong.md", expected=1))
        self.change.write_text(self.change_text("", "targets: [AUTH-003]\n"), encoding="utf-8")
        self.assertIn("C09", self.cli("coverage", "auth", "--with-change", "changes/0002-login.md", expected=1))
        self.change.write_text(self.change_text("## Modified\n### AUTH-999\nMissing spec.\n"), encoding="utf-8")
        self.assertIn("A02", self.cli("coverage", "auth", "--with-change", "changes/0002-login.md", expected=1))

    def test_no_evals_is_uncovered_not_missing_report(self):
        output = self.cli("coverage", "clock", expected=1)
        self.assertIn("CLOCK-001 blackbox uncovered (0 tests)", output)
        self.assertIn("E03", output)
        self.assertNotIn("E01", output)
        self.assertIn("SYS-001 review n/a (0 tests)", self.cli("coverage", "system"))

    def test_stale_report_deleted_missing_report_has_last_40_combined_lines(self):
        stale = self.write("packages/auth/evals/.results/junit.xml", '<testsuite><testcase name="AUTH-001"/></testsuite>')
        self.write("packages/auth/evals/run.py", "import sys\nfor n in range(50):\n"
                   "    print(f'line-{n}', file=sys.stderr if n % 2 else sys.stdout, flush=True)\n")
        result = self.result()
        self.assertFalse(stale.exists())
        message = next(m for m in result.messages if m.code == "E01")
        self.assertIn("eval run produced no report: packages/auth/evals/.results/junit.xml", message.message)
        self.assertNotIn("line-9", message.message)
        self.assertIn("line-10", message.message)
        self.assertIn("line-49", message.message)
        self.assertEqual(40, message.message.count("line-"))
        self.assertIn("E01", self.cli("coverage", "auth", expected=1))

    def test_invalid_junit_reports_are_E01(self):
        for xml in ("<invalid", "<html/>"):
            with self.subTest(xml=xml):
                self.set_report(xml)
                self.assertTrue(any(m.code == "E01" for m in self.result().messages))
                self.assertIn("ERROR E01", self.cli("coverage", "auth", expected=1))

    def test_system_eval_cwd_and_report_are_relative_to_system_evals(self):
        self.mutate("SYSTEM.md", "id_prefix: SYS", "id_prefix: SYS\nevals:\n  run: python3 evals/run.py\n  report: .results/junit.xml")
        self.mutate("SYSTEM.md", "### SYS-001 [review]", "### SYS-001")
        self.set_report('<testsuite><testcase classname="SYS-001" name="system"/></testsuite>',
                        directory="system-evals", report=".results/junit.xml")
        result = coverage(Repository(self.root), "system")
        self.assertEqual([], result.messages)
        self.assertEqual("pass", result.specs[0].status)
        self.assertIn("SYS-001 blackbox pass (1 tests)", self.cli("coverage", "system"))
        self.assertTrue((self.root / "system-evals/.results/junit.xml").exists())

    def test_missing_module_is_usage_error(self):
        self.cli("coverage", "missing", expected=2)


class GitFixture(FixtureTest):
    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture Author")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.write("packages/auth/src/migrations/001.sql", "CREATE TABLE users (id INT);\n")
        self.base = self.commit("Initial fixture")

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return result.stdout.strip()

    def commit(self, subject, trailer=None):
        self.git("add", "-A")
        args = ["-c", "commit.gpgsign=false", "commit", "-q", "-m", subject]
        if trailer is not None:
            args.extend(["-m", trailer])
        self.git(*args)
        return self.git("rev-parse", "HEAD")

    def history(self):
        return check_commits(self.root, f"{self.base}..HEAD")


class ZoneTests(GitFixture):
    def test_every_zone_and_priority(self):
        zones = Zones(self.root)
        paths = {
            "tools/AGENTS.md": "tools", ".github/workflows/check.yml": "tools",
            ".devcontainer/Dockerfile": "tools", "changes/AGENTS.md": "changes",
            "changes/archive/0001-old-expiry.md": "changes", "docs/playbooks/regen.md": "playbooks",
            "AGENTS.md": "playbooks", "CLAUDE.md": "playbooks", "packages/auth/AGENTS.md": "playbooks",
            "packages/auth/CLAUDE.md": "playbooks", "SYSTEM.md": "system-spec",
            "system-evals/auth.test.ts": "system-evals", "packages/auth/SPEC.md": "spec",
            "packages/auth/evals/test.ts": "evals", "packages/auth/src/api.ts": "interface",
            "packages/auth/src/migrations/a/001.sql": "preserved", "packages/auth/src/login.ts": "implementation",
            "packages/auth/package.json": "package-meta", "README.md": "other", "docs/notes.md": "other",
            "packages/auth/SPEC.md.bak": "package-meta", "tools-extra/file.py": "other",
        }
        for path, expected in paths.items():
            with self.subTest(path=path):
                self.assertEqual(expected, zones.classify(path))

    def test_historical_specs_ignore_working_tree_metadata(self):
        self.mutate("packages/auth/SPEC.md", "interface: src/api.ts", "interface: src/new.ts")
        self.mutate("packages/auth/SPEC.md", "preserve: [src/api.ts, src/migrations/**]", "preserve: [src/new.ts]")
        historical = Zones(self.root, self.base)
        current = Zones(self.root)
        self.assertEqual("interface", historical.classify("packages/auth/src/api.ts"))
        self.assertEqual("preserved", historical.classify("packages/auth/src/migrations/001.sql"))
        self.assertEqual("implementation", current.classify("packages/auth/src/api.ts"))
        self.assertEqual("interface", current.classify("packages/auth/src/new.ts"))
        self.assertEqual("implementation", Zones(self.root, self.base).classify("packages/new/src/api.py"))


class HistoryTests(GitFixture):
    def test_H01_worker_tools_and_playbooks_human_edits_allowed(self):
        self.write("tools/human.py", "human edit\n")
        self.write("AGENTS.md", "Human instructions.\n")
        self.commit("Human tooling edit")
        self.assertEqual([], self.history())
        paths = ("tools/worker.py", ".github/check.yml", ".devcontainer/Dockerfile",
                 "docs/playbooks/author-change.md", "packages/auth/AGENTS.md", "CLAUDE.md")
        for path in paths:
            self.write(path, "worker edit\n")
        worker = self.commit("evals(auth): forbidden edits", "Change: 0002-login")
        messages = self.history()
        self.assertEqual(len(paths), len(messages))
        self.assertEqual({"H01"}, {m.code for m in messages})
        self.assertTrue(all(worker in m.message for m in messages))
        self.assertIn("ERROR H01", self.cli("check-commits", f"{self.base}..HEAD", expected=1))

    def test_H02_implementation_forbids_evals_system_evals_and_interface(self):
        paths = ("packages/auth/evals/login.py", "system-evals/integration.py", "packages/auth/src/api.ts")
        for path in paths:
            self.write(path, "eval phase\n")
        self.commit("evals(auth): allowed", "Change: 0002-login")
        self.assertEqual([], self.history())
        for path in paths:
            self.write(path, "implementation phase\n")
        self.commit("impl(auth): forbidden", "Change: 0002-login")
        self.assertEqual({"H02"}, {m.code for m in self.history()})
        self.assertEqual(set(paths), {m.path.relative_to(self.root).as_posix() for m in self.history()})

    def test_human_impl_subject_without_trailer_and_invalid_trailers_ignored(self):
        for index, trailer in enumerate((None, "Change: 0002-login extra", "prefix Change: 0002-login", "Change: 02-login")):
            with self.subTest(trailer=trailer):
                self.write("packages/auth/src/api.ts", f"human version {index}\n")
                self.write("tools/human.py", f"human version {index}\n")
                self.commit("impl(auth): human", trailer)
                self.assertEqual([], self.history())
        self.write("packages/auth/src/api.ts", "worker edit\n")
        self.commit("impl(auth): worker", "Explanation.\n\nChange: 0002-login\nOther: ignored")
        self.assertEqual(["H02"], [m.code for m in self.history()])

    def test_H03_append_only_add_allowed_modify_delete_rename_for_all_commits(self):
        self.write("packages/auth/src/migrations/002.sql", "CREATE TABLE sessions (id INT);\n")
        self.commit("Human adds migration")
        self.assertEqual([], self.history())
        self.write("packages/auth/src/migrations/001.sql", "ALTER TABLE users ADD email TEXT;\n")
        modified = self.commit("Human modifies migration")
        (self.root / "packages/auth/src/migrations/002.sql").unlink()
        deleted = self.commit("Worker deletes migration", "Change: 0002-login")
        self.git("mv", "packages/auth/src/migrations/001.sql", "packages/auth/src/login.sql")
        renamed = self.commit("Human renames migration")
        messages = self.history()
        self.assertEqual(["H03", "H03", "H03"], [m.code for m in messages])
        self.assertTrue(any(modified in m.message and " M " in m.message for m in messages))
        self.assertTrue(any(deleted in m.message and " D " in m.message for m in messages))
        self.assertTrue(any(renamed in m.message and " R100 " in m.message for m in messages))

    def test_H03_rename_into_append_only_zone(self):
        self.write("packages/auth/src/disposable.sql", "SELECT 1;\n")
        self.commit("Create ordinary source")
        self.git("mv", "packages/auth/src/disposable.sql", "packages/auth/src/migrations/003.sql")
        self.commit("Move into migrations")
        messages = self.history()
        self.assertEqual(["H03"], [m.code for m in messages])
        self.assertEqual("003.sql", messages[0].path.name)

    def test_H03_deleted_module_uses_parent_spec(self):
        shutil.rmtree(self.root / "packages/auth")
        self.commit("Remove module")
        self.assertEqual(["H03"], [m.code for m in self.history()])

    def test_H03_removing_protection_cannot_hide_same_commit_modification(self):
        self.mutate("packages/auth/SPEC.md", "append_only: [src/migrations/**]", "append_only: []")
        self.write("packages/auth/src/migrations/001.sql", "SELECT 1;\n")
        self.commit("Remove protection and edit migration")
        self.assertEqual(["H03"], [m.code for m in self.history()])

    def test_history_classification_uses_each_commit_not_HEAD(self):
        self.write("packages/auth/src/api.ts", "worker interface edit\n")
        worker = self.commit("impl(auth): interface edit", "Change: 0002-login")
        self.mutate("packages/auth/SPEC.md", "interface: src/api.ts", "interface: src/new.ts")
        self.commit("Human changes interface path")
        self.write("packages/auth/SPEC.md", "invalid uncommitted spec")
        messages = self.history()
        self.assertEqual(["H02"], [m.code for m in messages])
        self.assertIn(worker, messages[0].message)

    def test_renames_check_both_paths_and_spaces_are_preserved(self):
        self.write("packages/auth/evals/old test.py", "test content\n")
        self.commit("Add human eval")
        self.git("mv", "packages/auth/evals/old test.py", "packages/auth/src/new test.py")
        self.commit("impl(auth): move eval", "Change: 0002-login")
        messages = self.history()
        self.assertEqual(["H02"], [m.code for m in messages])
        self.assertEqual("old test.py", messages[0].path.name)

    def test_root_commit_and_empty_ranges(self):
        self.assertEqual([], check_commits(self.root, self.base))
        self.assertEqual([], check_commits(self.root, f"{self.base}..{self.base}"))
        self.assertEqual("", self.cli("check-commits", f"{self.base}..{self.base}"))

    def test_invalid_range_is_one_line_usage_error(self):
        output = self.cli("check-commits", "missing-ref..HEAD", expected=2)
        self.assertEqual(1, len(output.splitlines()))
        self.cli("pr-body", "missing-ref..HEAD", expected=2)

    def test_history_cli_does_not_load_uncommitted_specs(self):
        self.write("README.md", "human edit\n")
        self.commit("Human documentation update")
        self.spec.write_bytes(b"\xff")
        self.assertEqual("", self.cli("check-commits", f"{self.base}..HEAD"))
        self.assertIn("Other commits", self.cli("pr-body", f"{self.base}..HEAD"))


class PrBodyTests(GitFixture):
    def build_range(self):
        self.write("packages/auth/evals/login.py", "new eval\n")
        self.write("packages/auth/src/api.ts", "changed interface\n")
        self.commit("evals(auth): 0002-login", "Change: 0002-login")
        self.write("packages/auth/src/login.ts", "implementation\n")
        self.write("packages/auth/src/migrations/002.sql", "new preserved migration\n")
        self.change.write_text(self.change_text("## Added\n\n### AUTH-003 [review]\nReview logout.\n\n"
                                              "## Decisions\n\n- Use deterministic tokens in evals.\n"
                                              "### Detail\nKeep this prose verbatim.\n"), encoding="utf-8")
        self.git("mv", "changes/0002-login.md", "changes/archive/0002-login.md")
        self.commit("impl(auth): 0002-login", "Change: 0002-login")
        self.write("changes/0003-expiry.md", self.change_text("## Decisions\n", "blocked: 'The interface needs clarification.'\n"))
        self.commit("chore: block 0003-expiry", "Change: 0003-expiry")
        return f"{self.base}..HEAD"

    def test_done_and_blocked_snapshot_cli_notes_and_HEAD_content(self):
        rev_range = self.build_range()
        self.write("changes/0003-expiry.md", "broken uncommitted change should be ignored")
        notes = self.write("night-log.md", "WARN P1-03 already passes\nLimit wait finished.\n")
        expected = (
            "## 0002-login (auth) — DONE\n\n"
            "**Read carefully**\n"
            "- Evals changed: packages/auth/evals/login.py\n"
            "- Interface changed: packages/auth/src/api.ts\n"
            "- Preserved files changed: packages/auth/src/migrations/002.sql\n"
            "- Review-tagged specs added/modified: AUTH-003\n"
            "- Decisions recorded by the agent:\n\n"
            "- Use deterministic tokens in evals.\n"
            "### Detail\nKeep this prose verbatim.\n\n"
            "**Skim**\n"
            "- Implementation files changed: 1 (packages/auth/src/login.ts)\n"
            "- Other files: changes/0002-login.md, changes/archive/0002-login.md\n\n"
            "## 0003-expiry (auth) — BLOCKED\n\n"
            "**Blocked reason:** The interface needs clarification.\n\n"
            "**Read carefully**\n"
            "- Decisions recorded by the agent:\n  none\n\n"
            "**Skim**\n- Other files: changes/0003-expiry.md\n\n"
            "## Night log\n\nWARN P1-03 already passes\nLimit wait finished.\n"
        )
        self.assertEqual(expected, pr_body(self.root, rev_range, notes))
        self.assertEqual(expected, self.cli("pr-body", rev_range, "--notes", "night-log.md"))

    def test_first_commit_order_groups_interleaved_commits_and_deduplicates(self):
        for number in (3, 2, 3):
            name = f"000{number}-change"
            self.write(f"changes/{name}.md", self.change_text(f"## Notes\nAttempt {number}.\n", "blocked: waiting\n"))
            self.write("packages/auth/src/login.ts", f"v{self.git('rev-parse', 'HEAD')}\n")
            self.commit(f"impl(auth): {name}", f"Change: {name}")
        body = pr_body(self.root, f"{self.base}..HEAD")
        self.assertLess(body.index("## 0003-change"), body.index("## 0002-change"))
        self.assertEqual(1, body.count("## 0003-change"))
        self.assertEqual(2, body.count("Implementation files changed: 1"))

    def test_other_commits_and_empty_range(self):
        self.write("README.md", "Human work\n")
        sha = self.commit("Human documentation update")
        expected = f"## Other commits\n\n- {sha[:7]} Human documentation update\n"
        self.assertEqual(expected, self.cli("pr-body", f"{self.base}..HEAD"))
        self.assertEqual("", self.cli("pr-body", "HEAD..HEAD"))

    def test_implementation_count_and_first_ten_paths(self):
        self.write("changes/0003-many.md", self.change_text("", "blocked: waiting\n"))
        for index in range(12):
            self.write(f"packages/auth/src/file-{index:02d}.ts", "source\n")
        self.commit("impl(auth): lots of files", "Change: 0003-many")
        body = pr_body(self.root, f"{self.base}..HEAD")
        self.assertIn("Implementation files changed: 12 (", body)
        self.assertIn("file-09.ts)", body)
        self.assertNotIn("file-10.ts", body)

    def test_modified_review_specs_and_system_evals(self):
        self.change.write_text(self.change_text("## Modified\n### AUTH-001 [review]\nReview behavior.\n"), encoding="utf-8")
        self.git("mv", "changes/0002-login.md", "changes/archive/0002-login.md")
        self.write("system-evals/integration.py", "new integration eval\n")
        self.commit("evals(system): integration", "Change: 0002-login")
        body = pr_body(self.root, f"{self.base}..HEAD")
        self.assertIn("Evals changed: system-evals/integration.py", body)
        self.assertIn("Review-tagged specs added/modified: AUTH-001", body)
        self.assertNotIn("Interface changed:", body)

    def test_decisions_preserve_whitespace_and_nested_headings_verbatim(self):
        decisions = "\n- Keep trailing spaces.  \n\n### Rationale\n  Indented prose.\n\n\n"
        self.change.write_text(self.change_text("## Decisions\n" + decisions + "## Notes\nOther prose.\n"), encoding="utf-8")
        self.git("mv", "changes/0002-login.md", "changes/archive/0002-login.md")
        self.commit("impl(auth): decisions", "Change: 0002-login")
        body = pr_body(self.root, f"{self.base}..HEAD")
        self.assertIn("- Decisions recorded by the agent:\n" + decisions + "**Skim**", body)

    def test_missing_change_file_reports_usage_error(self):
        self.write("packages/auth/src/login.ts", "implementation\n")
        self.commit("impl(auth): missing", "Change: 0003-missing")
        self.assertIn("missing at HEAD", self.cli("pr-body", f"{self.base}..HEAD", expected=2))

    def test_commit_reader_preserves_subject_and_multiline_body(self):
        self.write("README.md", "human\n")
        sha = self.commit("Subject with café", "Explanation.\n\nChange: 0002-login\nOther: detail")
        commit = Git(self.root).commits(f"{self.base}..HEAD")[0]
        self.assertEqual(sha, commit.sha)
        self.assertEqual("Subject with café", commit.subject)
        self.assertIn("Explanation.\n\nChange: 0002-login\nOther: detail", commit.body)
        self.assertEqual("0002-login", commit.change_name)


if __name__ == "__main__":
    unittest.main()
