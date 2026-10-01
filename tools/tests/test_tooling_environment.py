import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]


class ToolingEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        shutil.copytree(TOOLS / "specflow", self.root / "tools/specflow",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("spec", "night", "pyproject.toml", "uv.lock"):
            shutil.copy2(TOOLS / name, self.root / "tools" / name)
        (self.root / "SYSTEM.md").write_text("---\nname: system\nid_prefix: SYS\n---\n\n## Specs\n")
        (self.root / ".gitignore").write_text("tools/.venv/\n__pycache__/\n")
        # A product's Python project must not affect tooling dependencies.
        (self.root / "pyproject.toml").write_text(
            '[project]\nname = "product"\nversion = "0.1.0"\n'
            'dependencies = ["specflow-nonexistent-product-dependency"]\n')
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True, capture_output=True)
        self.env = os.environ | {
            "UV_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "UV_PROJECT_ENVIRONMENT": str(self.root / "tools/.venv"),
        }
        self.env.pop("VIRTUAL_ENV", None)
        self.env.pop("PYTHONPATH", None)

    def launch(self, command, *args, cwd=None):
        return subprocess.run([str(self.root / "tools" / command), *args],
                              cwd=cwd or self.root, env=self.env,
                              capture_output=True, text=True)

    def test_launchers_bootstrap_locked_environment_from_nested_directory(self):
        environment = self.root / "tools/.venv"
        self.assertFalse(environment.exists())
        lock = (self.root / "tools/uv.lock").read_bytes()
        result = self.launch("spec", "check", cwd=self.root / "tools/specflow")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((environment / "pyvenv.cfg").is_file())
        result = self.launch("night", "run", "--dry-run", cwd=self.root / "tools")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(lock, (self.root / "tools/uv.lock").read_bytes())
        result = subprocess.run([str(environment / "bin/python"), "-c",
                                 "import yaml; print(yaml.__version__)"],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(result.stdout.strip())
        ignored = subprocess.run(["git", "check-ignore", "tools/.venv/pyvenv.cfg"],
                                 cwd=self.root, capture_output=True, text=True)
        self.assertEqual(0, ignored.returncode, ignored.stderr)

    def test_launchers_refuse_stale_lock_without_rewriting_it(self):
        project = self.root / "tools/pyproject.toml"
        project.write_text(project.read_text().replace('dependencies = ["PyYAML>=6"]', 'dependencies = []'))
        lock = (self.root / "tools/uv.lock").read_bytes()
        for command, args in (("spec", ("check",)), ("night", ("run", "--dry-run"))):
            with self.subTest(command=command):
                result = self.launch(command, *args)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("--locked", result.stderr)
                self.assertEqual(lock, (self.root / "tools/uv.lock").read_bytes())

    def test_launchers_refuse_to_run_outside_a_git_repository(self):
        directory = self.root.parent / "not-a-repository"
        directory.mkdir()
        for command in ("spec", "night"):
            with self.subTest(command=command):
                result = self.launch(command, "--help", cwd=directory)
                self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                self.assertFalse((self.root / "tools/.venv").exists())
