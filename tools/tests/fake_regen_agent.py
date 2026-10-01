"""Scripted regeneration agent used only by Phase 6 fixture tests."""

import json
import os
from pathlib import Path
import sys

root = Path.cwd()
log = root / ".night/regen-call.json"
log.parent.mkdir(parents=True, exist_ok=True)
log.write_text(json.dumps({"cwd": str(root), "prompt": sys.argv[1]}))
action = os.environ.get("SPECFLOW_TEST_REGEN_ACTION", "happy")
package = root / "packages/pilot"
(package / "src").mkdir(exist_ok=True)
answer = "None" if action == "failing-eval" else "42"
(package / "src/answer.py").write_text(f"def answer():\n    return {answer}\n")
(package / "REGEN-NOTES.md").write_text("No behavior gaps found.\n")
print("Scripted regeneration complete.")
sys.exit(1 if action == "agent-error" else 0)
