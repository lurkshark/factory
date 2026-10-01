---
module: pilot
---

## Motivation
Exercise the Phase 5 unattended night loop on an isolated pilot module. The public Python entrypoint is pilot.answer; evals/run.py discovers unittest test_*.py files and emits JUnit using test method docstrings as labels. Add a deterministic black-box eval and implement the observable answer.

## Added

### PILOT-001
Calling the public answer() function MUST return the integer 42.

## Decisions
