"""Scripted agent used only by worker and night-loop integration tests."""

import json
import os
import re
from pathlib import Path
import subprocess
import sys

root = Path.cwd()
calls_path = root / ".night/calls.json"
calls_path.parent.mkdir(exist_ok=True)
calls = json.loads(calls_path.read_text()) if calls_path.exists() else []
prompt = sys.argv[1]
calls.append(prompt)
calls_path.write_text(json.dumps(calls))
phase = 1 if "implement-evals.md" in prompt else 2
action = os.environ.get("SPECFLOW_TEST_ACTION", "happy")
change_path = root / next(line.removeprefix("Change file: ") for line in prompt.splitlines()
                         if line.startswith("Change file: "))

if action == "limit-once" and len(calls) == 1 or action == "limit-always":
    print("Usage limit reached")
    sys.exit(1)
if action == "exit":
    print("Agent failed before verification.")
    sys.exit(7)
if action == "malformed":
    (root / "changes/0001-answer.md").write_text("damaged frontmatter\n")
    sys.exit(0)
if action == "retarget":
    (root / "changes/0001-answer.md").write_text("---\nmodule: system\n---\n\n## Motivation\nRetargeted request.\n")
    sys.exit(0)
if action == "system":
    if phase == 1:
        write_path = root / "system-evals/run.py"
        write_path.parent.mkdir(exist_ok=True)
        write_path.write_text('from pathlib import Path\nPath("junit.xml").write_text(\'<testsuite><testcase name="SYS-001"><failure/></testcase></testsuite>\')\n')
    else:
        (root / "system-evals/run.py").write_text("forbidden edit\n")
    sys.exit(0)

def write(path, text):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)

def block():
    path = change_path
    text = path.read_text()
    opening, body = text.split("---", 2)[1:]
    path.write_text("---" + opening + "blocked: 'Need a clearer interface.'\n---" + body)

if action == "queue-block-middle":
    if change_path.name.startswith("0002-"):
        block()
    elif phase == 1:
        ids = re.findall(r"^### (PILOT-\d+)", (root / "packages/pilot/SPEC.md").read_text(), re.MULTILINE)
        ids += re.findall(r"^### (PILOT-\d+)", change_path.read_text(), re.MULTILINE)
        source = (root / "tools/eval-template.py").read_text().replace('(\"PILOT-001\", \"PILOT-002\")', repr(tuple(ids)))
        write("packages/pilot/evals/run.py", source)
    else:
        write("packages/pilot/src/answer.py", "def answer():\n    return 42\n")
    sys.exit(0)

if action == f"block-{phase}":
    write("packages/pilot/src/answer.py", "unauthorized work\n")
    block()
    sys.exit(0)

if phase == 1:
    source = (root / "tools/eval-template.py").read_text()
    if action == "missing":
        source = source.replace("PILOT-002", "UNRELATED-002")
    if action == "orphan":
        source = source.replace("PILOT-002", "PILOT-999")
    if action == "no-report":
        source = "print('No JUnit report')\n"
    write("packages/pilot/evals/run.py", source)
    if action == "p1-src":
        write("packages/pilot/src/forbidden.py", "forbidden\n")
    if action == "other-module":
        write("packages/other/evals/forbidden.py", "forbidden\n")
    if action == "interface":
        write("packages/pilot/src/api.pyi", "def answer() -> int: ... # updated\n")
    if action == "commit":
        subprocess.run(["git", "add", "packages/pilot/evals/run.py"], check=True)
        subprocess.run(["git", "commit", "-m", "Agent's forbidden commit"], check=True)
else:
    write("packages/pilot/src/answer.py", "def answer():\n    return 42\n")
    if action == "p2-evals":
        write("packages/pilot/evals/extra.py", "forbidden\n")
    if action == "p2-interface":
        write("packages/pilot/src/api.pyi", "forbidden\n")
    if action == "p2-tools":
        write("tools/forbidden.py", "forbidden\n")
    if action == "p2-change":
        write("changes/0002-unrelated.md", "forbidden\n")
    if action == "append-modify":
        write("packages/pilot/src/migrations/001.sql", "forbidden\n")
    if action == "append-delete":
        (root / "packages/pilot/src/migrations/001.sql").unlink(missing_ok=True)
    if action == "append-type":
        path = root / "packages/pilot/src/migrations/001.sql"
        path.unlink(missing_ok=True)
        path.symlink_to("../settings.json")
    if action == "append-rename":
        source = root / "packages/pilot/src/migrations/001.sql"
        target = root / "packages/pilot/src/old.sql"
        if source.exists():
            subprocess.run(["git", "mv", str(source), str(target)], check=True)
    if action == "append-add":
        write("packages/pilot/src/migrations/002.sql", "SELECT 2;\n")
    if action == "append-staged":
        write("packages/pilot/src/migrations/002.sql", "SELECT 2;\n")
        subprocess.run(["git", "add", "packages/pilot/src/migrations/002.sql"], check=True)
        write("packages/pilot/src/migrations/002.sql", "SELECT 3;\n")
    if action == "preserved":
        write("packages/pilot/src/settings.json", '{"value": 42}\n')
    if action == "repair":
        path = root / "packages/pilot/evals/extra.py"
        if "This is attempt 2" in prompt:
            path.unlink()
        else:
            write(path.relative_to(root), "forbidden\n")
    if action == "bad-apply":
        path = root / "changes/0001-answer.md"
        if "This is attempt 2" in prompt:
            path.write_text(path.read_text().replace("set_depends_on: [pilot]\n", ""))
        else:
            path.write_text(path.read_text().replace("module: pilot\n", "module: pilot\nset_depends_on: [pilot]\n"))
    if action == "wrong-code":
        write("packages/pilot/src/answer.py", "def answer():\n    return 0\n")
