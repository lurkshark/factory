import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from specflow.apply import apply_change
from specflow.cli import AGENT_TEMPLATE, CHANGE_TEMPLATE, MODULE_TEMPLATE, apply_file, main, new_change, new_module
from specflow.globs import matches
from specflow.markdown import InvalidDocument, Message, parse
from specflow.repository import Repository
from specflow.validation import change_items, spec_items, specs_section, validate_change, validate_spec

FIXTURE = Path(__file__).parent / "fixtures" / "valid"
TOOLS = Path(__file__).resolve().parents[1]


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = (Path(self.temporary.name) / "repo").resolve()
        shutil.copytree(FIXTURE, self.root)
        self.spec = self.root / "packages/auth/SPEC.md"
        self.change = self.root / "changes/0002-login.md"

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path

    def mutate(self, relative, old, new):
        path = self.root / relative
        text = path.read_bytes().decode("utf-8")
        self.assertIn(old, text)
        path.write_bytes(text.replace(old, new).encode("utf-8"))

    def change_text(self, body, fm=""):
        return "---\nmodule: auth\n" + fm + "---\n\n## Motivation\nA finished motivation.\n\n" + body

    def cli(self, *args, expected=0):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(TOOLS)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run([sys.executable, "-m", "specflow", *args], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        self.assertEqual("", result.stderr)
        return result.stdout

    def codes(self):
        return {m.code for m in Repository(self.root).check()}

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}


class StaticChecks(FixtureTest):
    def test_valid_fixture_and_check_cli(self):
        self.assertEqual([], Repository(self.root).check())
        self.assertEqual("", self.cli("check"))

    def test_C01_missing_and_invalid_system(self):
        system = self.root / "SYSTEM.md"
        original = system.read_bytes()
        system.unlink()
        self.assertIn("C01", self.cli("check", expected=1))
        system.write_bytes(original.replace(b"id_prefix: SYS", b"id_prefix: BAD"))
        self.assertIn("C01", self.cli("check", expected=1))

    def test_C02_missing_spec_and_directory_name_mismatch(self):
        self.mutate("packages/auth/SPEC.md", "name: auth", "name: other")
        self.assertIn("C02", self.cli("check", expected=1))
        (self.root / "packages/clock/SPEC.md").unlink()
        self.assertIn("C02", self.codes())

    def test_C03_frontmatter(self):
        self.mutate("packages/auth/SPEC.md", "kind: service", "kind: unknown")
        self.assertIn("C03", self.cli("check", expected=1))

    def test_C04_body(self):
        self.mutate("packages/auth/SPEC.md", "### AUTH-001", "### AUTH-001 [unknown]")
        self.assertIn("C04", self.cli("check", expected=1))

    def test_C05_duplicate_ids_and_prefixes(self):
        self.mutate("packages/auth/SPEC.md", "### AUTH-002 [quarantine]", "### AUTH-001 [quarantine]")
        self.assertIn("C05", self.cli("check", expected=1))
        self.mutate("packages/clock/SPEC.md", "CLOCK", "AUTH")
        self.assertIn("C05", self.codes())

    def test_C06_unknown_self_and_cycles(self):
        self.mutate("packages/clock/SPEC.md", "depends_on: []", "depends_on: [auth]")
        output = self.cli("check", expected=1)
        self.assertIn("C06", output)
        self.assertIn("auth -> clock -> auth", output)
        self.mutate("packages/clock/SPEC.md", "depends_on: [auth]", "depends_on: [clock, absent]")
        messages = Repository(self.root).check()
        self.assertEqual(2, sum(m.code == "C06" for m in messages))

    def test_C07_unexpected_filename_warning_and_apply_error(self):
        bad = self.write("changes/not-numbered.md", self.change_text(""))
        self.write("changes/AGENTS.md", "Ignored instructions.\n")
        self.assertIn("WARN C07", self.cli("check"))
        before = self.snapshot()
        self.assertIn("ERROR C07", self.cli("apply", str(bad), expected=1))
        self.assertEqual(before, self.snapshot())

    def test_C08_duplicate_number_across_pending_and_archive(self):
        self.write("changes/0001-duplicate.md", self.change_text(""))
        self.assertIn("C08", self.cli("check", expected=1))

    def test_C09_unfinished_template(self):
        self.change.write_text(CHANGE_TEMPLATE.format(module="auth"), encoding="utf-8")
        self.assertIn("C09", self.cli("check", expected=1))

    def test_C09_unknown_module_targets_dependencies(self):
        for fm in ("targets: [AUTH-999]\n", "set_depends_on: [missing]\n"):
            with self.subTest(fm=fm):
                self.change.write_text(self.change_text("", fm), encoding="utf-8")
                self.assertIn("C09", self.cli("check", expected=1))
        self.change.write_text(self.change_text("").replace("module: auth", "module: missing"), encoding="utf-8")
        self.assertIn("C09", self.cli("check", expected=1))

    def test_C10_queue_removes_then_modifies(self):
        self.change.write_text(self.change_text("## Removed\n\n### AUTH-002\n"), encoding="utf-8")
        self.write("changes/0003-too-late.md", self.change_text("## Modified\n\n### AUTH-002\nChanged expiry.\n"))
        output = self.cli("check", expected=1)
        self.assertIn("C10", output)
        self.assertIn("A02 cannot modify AUTH-002: not found", output)

    def test_C11_interface_exists_and_preserve_append_only(self):
        (self.root / "packages/auth/src/api.ts").unlink()
        self.assertIn("C11", self.cli("check", expected=1))
        self.mutate("packages/auth/SPEC.md", "preserve: [src/api.ts, src/migrations/**]", "preserve: []")
        self.assertEqual(2, sum(m.code == "C11" for m in Repository(self.root).check()))

    def test_C12_manifest_warning_never_error(self):
        self.write("packages/auth/package.json", '{"name":"@fixture/auth"}')
        self.assertIn("WARN C12", self.cli("check"))
        (self.root / "packages/auth/package.json").unlink()
        self.assertIn("no supported manifest", self.cli("check"))

    def test_C13_no_current_or_pending_reuse(self):
        self.mutate("packages/auth/SPEC.md", "AUTH-002", "AUTH-009")
        self.assertIn("C13", self.cli("check", expected=1))
        self.mutate("packages/auth/SPEC.md", "AUTH-009", "AUTH-002")
        self.mutate("changes/0002-login.md", "AUTH-003", "AUTH-009")
        self.assertIn("C13", self.cli("check", expected=1))

    def test_C13_ignores_removed_heading_inside_fence(self):
        self.write("changes/archive/0001-old-expiry.md", self.change_text("## Removed\n\n```\n### AUTH-001\n```\n"))
        self.assertNotIn("C13", self.codes())

    def test_blocked_changes_are_projected(self):
        self.mutate("changes/0002-login.md", "module: auth", "module: auth\nblocked: waiting")
        projected, errors = Repository(self.root).project()
        self.assertEqual([], errors)
        self.assertEqual({"AUTH-001", "AUTH-003"}, {i.id for i in spec_items(projected["auth"])})
        self.assertEqual([], Repository(self.root).check())

    def test_targets_exist_at_position_not_only_final_projection(self):
        self.write("changes/0003-target.md", self.change_text("", "targets: [AUTH-003]\n"))
        self.assertEqual([], Repository(self.root).check())
        self.write("changes/0000-too-early.md", self.change_text("", "targets: [AUTH-003]\n"))
        self.assertIn("C09", self.codes())

    def test_projected_dependency_cycle(self):
        self.change.write_text(self.change_text("", "set_depends_on: [auth]\n").replace("module: auth", "module: clock"), encoding="utf-8")
        self.assertIn("C06", self.cli("check", expected=1))

    def test_diagnostics_deterministic_and_summary_last(self):
        (self.root / "SYSTEM.md").unlink()
        self.write("changes/z.md", "Ignored")
        first = self.cli("check", expected=1)
        self.assertEqual(first, self.cli("check", expected=1))
        self.assertEqual("1 error(s), 1 warning(s)", first.splitlines()[-1])


class ParserTests(FixtureTest):
    def test_every_markdown_fixture_round_trips_byte_identically(self):
        for path in self.root.rglob("*.md"):
            with self.subTest(path=path):
                self.assertEqual(path.read_bytes(), parse(path).text.encode("utf-8"))
        self.assertIn(b"\r\n", (self.root / "SYSTEM.md").read_bytes())
        self.assertFalse((self.root / "packages/clock/SPEC.md").read_bytes().endswith(b"\n"))

    def test_fenced_headings_are_not_sections(self):
        doc = parse(self.spec)
        self.assertEqual(["AUTH-001", "AUTH-002"], [i.id for i in spec_items(doc)])
        self.assertEqual([], validate_spec(doc))
        self.assertIn("### AUTH-888", doc.section_text(spec_items(doc)[0].section))
        self.mutate("packages/auth/SPEC.md", "```", "  ~~~")
        self.assertEqual([], validate_spec(parse(self.spec)))

    def test_generic_sections_end_at_equal_or_lower_heading(self):
        doc = parse(self.spec)
        parent = specs_section(doc)
        self.assertEqual("## Notes\n", doc.lines[parent.end])
        first = spec_items(doc)[0].section
        self.assertEqual("### AUTH-002 [quarantine]\n", doc.lines[first.end])

    def test_frontmatter_schema_errors(self):
        original = self.spec.read_bytes().decode()
        mutations = [
            ("---\n", "\n---\n"),
            ("kind: service\n", ""),
            ("kind: service", "kind: 3"),
            ("name: auth", "name: system"),
            ("id_prefix: AUTH", "id_prefix: SYS"),
            ("id_prefix: AUTH", "id_prefix: auth"),
            ("depends_on: [clock]", "depends_on:\n  - clock"),
            ("depends_on: [clock]", "depends_on: [clock, clock]"),
            ("depends_on: [clock]", "depends_on: wrong"),
            ("preserve: [src/api.ts, src/migrations/**]", "preserve: [1]"),
            ("kind: service", "kind: service\nunknown: true"),
            ("interface: src/api.ts", "interface: ../secret"),
            ("evals:\n  run: python3 evals/run.py\n  report: evals/.results/junit.xml", "evals: {run: 2, report: x}"),
            ("evals:\n  run: python3 evals/run.py\n  report: evals/.results/junit.xml", "evals: {run: test}"),
        ]
        for old, new in mutations:
            with self.subTest(new=new):
                try:
                    messages = validate_spec(parse(self.spec, original.replace(old, new, 1)))
                except InvalidDocument as exc:
                    messages = exc.messages
                self.assertIn("C03", {m.code for m in messages})
        for text in ("---\nname: auth\n", "---\n[]\n---\n", "---\nname: [\n---\n"):
            with self.subTest(text=text), self.assertRaises(InvalidDocument) as caught:
                parse(self.spec, text)
            self.assertEqual("C03", caught.exception.messages[0].code)

    def test_spec_body_grammar_errors(self):
        base = MODULE_TEMPLATE.format(name="auth", kind="service", prefix="AUTH")
        for body in ("prose\n", "### BAD-001\nBody.\n", "### AUTH-01\nBody.\n", "### AUTH-001\n\n", "#### AUTH-001\nBody.\n", "### AUTH-001\nBody.\n#### nested\nText.\n", "## Specs\n"):
            with self.subTest(body=body):
                self.assertIn("C04", {m.code for m in validate_spec(parse(self.spec, base + body))})
        self.assertIn("C04", {m.code for m in validate_spec(parse(self.spec, base.replace("## Specs", "## Specs ")))})

    def test_change_body_and_frontmatter_errors(self):
        cases = [
            ("", ""),
            ("## Motivation\nDuplicate.\n", ""),
            ("## Unknown\nSomething\n", ""),
            ("## Added\n### AUTH-003 [wrong]\nBody\n", ""),
            ("## Removed\n### AUTH-002 [review]\n", ""),
            ("## Added\n### AUTH-003\n", ""),
            ("## Added\n### CLOCK-003\nBody\n", ""),
            ("## Added\n### AUTH-003\nBody\n\n## Modified\n### AUTH-003\nOther\n", ""),
            ("## Modified\n### AUTH-001\nBody\n", "targets: [AUTH-001]\n"),
            ("## Notes\n# Disallowed\n", ""),
            ("## Notes\nText\n", "targets:\n  - AUTH-001\n"),
            ("## Notes\nText\n", "targets: [bad]\n"),
            ("## Notes\nText\n", "unknown: 1\n"),
            ("## Notes\nText\n", "blocked: false\n"),
        ]
        for body, fm in cases:
            with self.subTest(body=body, fm=fm):
                text = self.change_text(body, fm)
                if not body and not fm:
                    text = text.replace("A finished motivation.", "")
                self.assertTrue(validate_change(parse(self.change, text), "AUTH"))
        text = self.change_text("", "set_depends_on: []\n").replace("module: auth", "module: system")
        self.assertTrue(validate_change(parse(self.change, text), "SYS"))

    def test_valid_tags_and_maintenance_change(self):
        text = MODULE_TEMPLATE.format(name="auth", kind="service", prefix="AUTH")
        for i, tag in enumerate(("", " [blackbox]", " [review]", " [quarantine]"), 1):
            text += f"\n### AUTH-{i:03d}{tag}\nBehavior MUST hold.\n"
        self.assertEqual([], validate_spec(parse(self.spec, text)))
        self.assertEqual([], validate_change(parse(self.change, self.change_text("## Notes\n### Details\nProse.\n")), "AUTH"))


class ApplyTests(FixtureTest):
    def test_apply_preserves_untouched_bytes_and_replaces_in_place(self):
        original = parse(self.spec)
        updated = apply_change(original, parse(self.change))
        before = specs_section(original)
        after = specs_section(updated)
        self.assertEqual(original.lines[:before.start + 1][5:], updated.lines[:after.start + 1][5:])
        self.assertEqual(original.lines[before.end:], updated.lines[after.end:])
        self.assertEqual(["AUTH-001", "AUTH-003"], [i.id for i in spec_items(updated)])
        self.assertIn("depends_on: []\n", updated.text)
        self.assertIn("### AUTH-001 [review]\nA login MUST return an opaque token.\n\n", updated.text)
        self.assertEqual(original.text.encode(), self.spec.read_bytes())

    def test_only_dependency_line_changes_in_CRLF_file(self):
        text = self.spec.read_bytes().decode().replace("\n", "\r\n")
        self.spec.write_bytes(text.encode())
        change = parse(self.change, self.change_text("", "set_depends_on: []\n"))
        updated = apply_change(parse(self.spec), change)
        self.assertEqual(text.replace("depends_on: [clock]\r\n", "depends_on: []\r\n"), updated.text)

    def test_append_normalizes_only_inserted_text(self):
        text = self.spec.read_bytes().decode().replace("\n", "\r\n")
        original = parse(self.spec, text)
        change = parse(self.change, self.change_text("## Added\n\n### AUTH-004\r\nNew behavior.\r\n\r\n\r\n### AUTH-005\nSecond behavior.\n\n"))
        updated = apply_change(original, change)
        parent = specs_section(original)
        insertion = "### AUTH-004\nNew behavior.\n\n### AUTH-005\nSecond behavior.\n\n"
        self.assertEqual("".join(original.lines[:parent.end]) + insertion + "".join(original.lines[parent.end:]), updated.text)

    def test_append_at_eof_without_newline(self):
        path = self.root / "packages/clock/SPEC.md"
        original = parse(path)
        change = parse(self.change, self.change_text("## Added\n### CLOCK-002\nAnother behavior.\n").replace("module: auth", "module: clock"))
        updated = apply_change(original, change)
        self.assertEqual(original.text + "\n\n### CLOCK-002\nAnother behavior.\n\n", updated.text)

    def test_A01_A02_A03_A04_do_not_write_any_file(self):
        cases = [
            ("A01", "## Removed\n### AUTH-999\n", ""),
            ("A02", "## Modified\n### AUTH-999\nNew text\n", ""),
            ("A03", "## Added\n### AUTH-001\nDuplicate\n", ""),
            ("A04", "", "set_depends_on: []\n"),
        ]
        original = self.spec.read_bytes()
        for code, body, fm in cases:
            with self.subTest(code=code):
                self.spec.write_bytes(original)
                if code == "A04":
                    self.mutate("packages/auth/SPEC.md", "depends_on: [clock]", "depends_on:\n  - clock")
                self.change.write_text(self.change_text(body, fm), encoding="utf-8")
                before = self.snapshot()
                output = self.cli("apply", "changes/0002-login.md", expected=1)
                self.assertIn(code, output)
                self.assertEqual(before, self.snapshot())

    def test_error_after_valid_edit_still_writes_nothing(self):
        self.change.write_text(self.change_text("## Removed\n### AUTH-002\n\n## Modified\n### AUTH-999\nMissing\n"), encoding="utf-8")
        before = self.snapshot()
        self.cli("apply", "changes/0002-login.md", expected=1)
        self.assertEqual(before, self.snapshot())

    def test_apply_cli_archives_without_git(self):
        before = self.change.read_bytes()
        self.cli("apply", "changes/0002-login.md")
        self.assertFalse(self.change.exists())
        self.assertEqual(before, (self.root / "changes/archive/0002-login.md").read_bytes())
        self.assertEqual(["AUTH-001", "AUTH-003"], [i.id for i in spec_items(parse(self.spec))])

    def test_non_head_requires_now_and_reports_remaining_queue_warnings(self):
        earlier = self.write("changes/0000-modify.md", self.change_text("## Modified\n### AUTH-002\nDifferent expiry\n"))
        before = self.snapshot()
        self.assertIn("not the queue head", self.cli("apply", "changes/0002-login.md", expected=1))
        self.assertEqual(before, self.snapshot())
        output = self.cli("apply", "changes/0002-login.md", "--now")
        self.assertIn("WARN C10", output)
        self.assertTrue(earlier.exists())
        self.assertIn("0 error(s), 1 warning(s)", output)

    def test_blocked_changes_refused_with_and_without_now(self):
        self.mutate("changes/0002-login.md", "module: auth", "module: auth\nblocked: waiting")
        before = self.snapshot()
        for extra in ([], ["--now"]):
            self.assertIn("blocked: waiting", self.cli("apply", "changes/0002-login.md", *extra, expected=1))
            self.assertEqual(before, self.snapshot())

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def test_git_mv_for_tracked_change(self):
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        self.cli("apply", "changes/0002-login.md")
        status = self.git("status", "--porcelain")
        self.assertIn("R  changes/0002-login.md -> changes/archive/0002-login.md", status)
        self.assertIn(" M packages/auth/SPEC.md", status)

    def test_untracked_change_in_git_uses_plain_move(self):
        self.git("init", "-q")
        self.cli("apply", "changes/0002-login.md")
        self.assertTrue((self.root / "changes/archive/0002-login.md").exists())

    def test_archiving_failure_rolls_back_spec(self):
        self.git("init", "-q")
        self.git("add", ".")
        before = self.snapshot()
        original_run = subprocess.run

        def fake_run(command, **kwargs):
            if command[:2] == ["git", "mv"]:
                return subprocess.CompletedProcess(command, 1, "", "simulated move failure")
            return original_run(command, **kwargs)

        with patch("specflow.cli.subprocess.run", side_effect=fake_run):
            with self.assertRaises(Exception):
                apply_file(Repository(self.root), self.change)
        self.assertEqual(before, self.snapshot())

    def test_system_change_and_apply_revalidation(self):
        system = self.root / "SYSTEM.md"
        before = system.read_bytes()
        self.change.write_text(self.change_text("## Added\n### SYS-002\nModules MUST cooperate.\n").replace("module: auth", "module: system"), encoding="utf-8")
        self.cli("apply", "changes/0002-login.md")
        self.assertTrue(system.read_bytes().startswith(before))
        self.assertIn(b"### SYS-002\nModules MUST cooperate.\n\n", system.read_bytes())
        # A wrong-prefix section is rejected before either file is changed.
        bad = self.write("changes/0003-wrong.md", self.change_text("## Added\n### SYS-003\nWrong module.\n"))
        snapshot = self.snapshot()
        self.cli("apply", str(bad), expected=1)
        self.assertEqual(snapshot, self.snapshot())

    def test_append_empty_specs_section_without_trailing_newline(self):
        text = MODULE_TEMPLATE.format(name="auth", kind="service", prefix="AUTH").rstrip("\n")
        change = parse(self.change, self.change_text("## Added\n### AUTH-001\nBehavior.\n"))
        updated = apply_change(parse(self.spec, text), change)
        self.assertEqual(text + "\n\n### AUTH-001\nBehavior.\n\n", updated.text)

    def test_untouched_spec_body_is_byte_identical(self):
        original = parse(self.spec)
        text = self.change_text("## Added\n### AUTH-004\nNew behavior.\n")
        updated = apply_change(original, parse(self.change, text))
        self.assertEqual([original.section_text(item.section) for item in spec_items(original)], [updated.section_text(item.section) for item in spec_items(updated)[:2]])


class CommandTests(FixtureTest):
    def test_list_and_next_cli(self):
        output = self.cli("list")
        self.assertIn("auth service AUTH 2", output)
        self.assertIn("clock library CLOCK 1", output)
        self.assertIn("system system SYS 1", output)
        self.assertIn("0002-login auth", output)
        self.assertEqual("changes/0002-login.md\n", self.cli("next"))

    def test_show_current_projected_and_single_id_cli(self):
        original = parse(self.spec)
        self.assertEqual(original.section_text(specs_section(original)), self.cli("show", "auth"))
        before = self.snapshot()
        projected = self.cli("show", "auth", "--projected")
        self.assertIn("AUTH-003", projected)
        self.assertNotIn("AUTH-002", projected)
        self.assertEqual(before, self.snapshot())
        self.assertEqual("### AUTH-003 [blackbox]\nA logout MUST revoke the token.\n\n", self.cli("show", "auth", "AUTH-003", "--projected"))
        self.assertIn("SYS-001", self.cli("show", "system"))

    def test_projected_show_applies_only_target_modules_changes(self):
        self.write("changes/0003-clock-error.md", self.change_text("## Modified\n### CLOCK-999\nMissing clock behavior.\n").replace("module: auth", "module: clock"))
        self.assertIn("AUTH-003", self.cli("show", "auth", "--projected"))
        self.assertIn("C10", self.cli("show", "clock", "--projected", expected=1))

    def test_new_change_numbering_includes_archive(self):
        self.write("changes/archive/0042-high-number.md", self.change_text(""))
        output = self.cli("new-change", "auth", "bug-fix")
        self.assertEqual("changes/0043-bug-fix.md\n", output)
        created = self.root / output.strip()
        self.assertEqual(CHANGE_TEMPLATE.format(module="auth"), created.read_text())
        self.assertTrue(validate_change(parse(created), "AUTH"))
        self.assertEqual("changes/0044-next-one.md", new_change(Repository(self.root), "system", "next-one").relative_to(self.root).as_posix())

    def test_new_module_exact_templates_and_no_manifest(self):
        self.assertEqual("packages/new-lib/SPEC.md\n", self.cli("new-module", "new-lib", "--kind", "library", "--prefix", "NEW"))
        directory = self.root / "packages/new-lib"
        self.assertEqual(MODULE_TEMPLATE.format(name="new-lib", kind="library", prefix="NEW"), (directory / "SPEC.md").read_text())
        self.assertEqual(AGENT_TEMPLATE.format(name="new-lib"), (directory / "AGENTS.md").read_text())
        self.assertTrue((directory / "src/.gitkeep").is_file())
        self.assertTrue((directory / "evals/.gitkeep").is_file())
        self.assertFalse((directory / "package.json").exists())
        self.assertEqual(0, sum(m.level == "ERROR" for m in Repository(self.root).check()))

    def test_invalid_creation_arguments_and_taken_prefix(self):
        for args in [
            ("new-change", "auth", "Bad_slug"),
            ("new-change", "missing", "valid"),
            ("new-module", "system", "--kind", "service", "--prefix", "SYSTEM"),
            ("new-module", "new", "--kind", "service", "--prefix", "SYS"),
            ("new-module", "new", "--kind", "service", "--prefix", "AUTH"),
            ("new-module", "auth", "--kind", "service", "--prefix", "OTHER"),
            ("new-module", "../escape", "--kind", "service", "--prefix", "OTHER"),
        ]:
            with self.subTest(args=args):
                before = self.snapshot()
                self.cli(*args, expected=2)
                self.assertEqual(before, self.snapshot())

    def test_all_exit_codes(self):
        self.cli("check", expected=0)
        self.cli("show", "auth", "AUTH-999", expected=2)
        self.cli("unknown-command", expected=2)
        self.mutate("changes/0002-login.md", "module: auth", "module: auth\nblocked: waiting")
        self.assertIn("waiting", self.cli("next", expected=3))
        self.cli("apply", "changes/0002-login.md", expected=1)
        self.change.unlink()
        self.assertEqual("", self.cli("next", expected=4))

    def test_unexpected_exception_is_one_line_and_debug_opt_in(self):
        output = io.StringIO()
        with patch("specflow.cli.Repository", side_effect=RuntimeError("first\nsecond")), contextlib.redirect_stdout(output):
            self.assertEqual(2, main(["check"], self.root))
        self.assertEqual(["ERROR tooling spec: RuntimeError: first second"], output.getvalue().splitlines())
        errors = io.StringIO()
        with patch.dict(os.environ, {"SPECFLOW_DEBUG": "1"}), patch("specflow.cli.Repository", side_effect=RuntimeError("broken")), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
            self.assertEqual(2, main(["check"], self.root))
        self.assertIn("Traceback", errors.getvalue())

    def test_fresh_repository_acceptance_through_shell_entrypoint(self):
        fresh = Path(self.temporary.name) / "fresh"
        fresh.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=fresh, check=True, capture_output=True)
        shutil.copytree(TOOLS / "specflow", fresh / "tools/specflow", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(TOOLS / "spec", fresh / "tools/spec")
        (fresh / "SYSTEM.md").write_text("---\nname: system\nid_prefix: SYS\n---\n\n# System\n\nPython fixture.\n\n## Specs\n", encoding="utf-8")
        env = os.environ.copy()
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
        for args in (("new-module", "example", "--kind", "library", "--prefix", "EX"), ("check",)):
            result = subprocess.run(["tools/spec", *args], cwd=fresh, env=env, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("0 error(s)", result.stdout)


class ManifestTests(FixtureTest):
    def test_all_package_dependency_sections_and_unknown_workspace(self):
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            self.write("packages/auth/package.json", json.dumps({"name": "@fixture/auth", key: {"@fixture/clock": "workspace:*", "external": "^1.0"}}))
            self.assertNotIn("C12", self.codes())
        self.write("packages/auth/package.json", '{"name":"@fixture/auth","dependencies":{"unknown":"workspace:*"}}')
        output = self.cli("check")
        self.assertIn("workspace dependency unknown", output)
        self.assertIn("missing from manifest: clock", output)

    def test_python_dependency_normalization_and_extras(self):
        (self.root / "packages/auth/package.json").unlink()
        (self.root / "packages/clock/package.json").unlink()
        self.write("packages/auth/pyproject.toml", '[project]\nname="Auth_Pkg"\ndependencies=["Clock.Pkg[extra]>=1; python_version >= \'3.11\'", "external>=1"]\n')
        self.write("packages/clock/pyproject.toml", '[project]\nname="clock-pkg"\ndependencies=[]\n')
        self.assertNotIn("C12", self.codes())
        self.write("packages/auth/pyproject.toml", '[project]\nname="Auth_Pkg"\ndependencies=[]\n')
        self.assertIn("missing from manifest: clock", self.cli("check"))

    def test_package_json_takes_precedence_and_invalid_is_warning(self):
        self.write("packages/auth/pyproject.toml", "Invalid TOML !")
        self.assertNotIn("C12", self.codes())
        self.write("packages/auth/package.json", "[ invalid")
        self.assertIn("WARN C12", self.cli("check"))

    def test_python_adapter_maps_names_from_dual_manifest_module(self):
        (self.root / "packages/auth/package.json").unlink()
        self.write("packages/auth/pyproject.toml", '[project]\nname="auth"\ndependencies=["Clock.Pkg>=1"]\n')
        self.write("packages/clock/pyproject.toml", '[project]\nname="clock-pkg"\ndependencies=[]\n')
        self.assertNotIn("C12", self.codes())


class GlobTests(unittest.TestCase):
    def test_required_globs(self):
        for path in ("src/migrations/001.sql", "src/migrations/a/b.sql"):
            self.assertTrue(matches(path, "src/migrations/**"))
        self.assertFalse(matches("src/migrationsx/1.sql", "src/migrations/**"))
        self.assertTrue(matches("src/api.ts", "src/*.ts"))
        self.assertFalse(matches("src/a/api.ts", "src/*.ts"))

    def test_zero_or_multiple_segments_and_character_classes(self):
        for path in ("src/api.ts", "src/a/api.ts", "src/a/b/api.ts"):
            self.assertTrue(matches(path, "src/**/api.ts"))
        self.assertTrue(matches("src/a.ts", "src/?.ts"))
        self.assertFalse(matches("src//.ts", "src/?.ts"))
        self.assertTrue(matches("src/b.ts", "src/[a-c].ts"))
        self.assertFalse(matches("src//.ts", "src/[!x].ts"))
        self.assertFalse(matches("src/abc/a.ts", "src/*a*.ts"))
        self.assertTrue(matches("src/abca.ts", "src/*a*.ts"))


if __name__ == "__main__":
    unittest.main()
