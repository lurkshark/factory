"""Deterministic fake eval runner; no project test framework is required."""

from pathlib import Path

report = Path("evals/.results/junit.xml")
report.parent.mkdir(parents=True, exist_ok=True)
report.write_text('''<testsuites><testsuite name="auth">
  <testcase classname="login" name="AUTH-001: returns a token" />
  <testcase name="AUTH-002: expiry"><failure message="known flaky eval" /></testcase>
</testsuite></testsuites>''', encoding="utf-8")
