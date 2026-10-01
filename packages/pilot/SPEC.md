---
name: pilot
kind: library
id_prefix: PILOT
depends_on: []
interface: src/api.pyi
preserve: [src/api.pyi]
evals:
  run: python3 evals/run.py
  report: evals/.results/junit.xml
---

# pilot

TODO: one paragraph describing this module's purpose.

## Specs

### PILOT-001
Calling the public answer() function MUST return the integer 42.

