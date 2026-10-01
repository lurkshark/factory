# factory

Phase 1 of the spec-driven workflow implements parsing, static validation,
projected specs, and applying and archiving changes.

Requires Python 3.11+, Git, and PyYAML:

```sh
python3 -m pip install -r tools/requirements.txt
PYTHONPATH=tools python3 -m unittest discover -s tools/tests -t tools
tools/spec --help
```

Available commands: `check`, `list`, `show`, `next`, `new-change`, `new-module`,
and `apply`. Commands run from the repository root. A repository needs a
`SYSTEM.md` before `check` can pass; the starter system spec, root agent
instructions, playbooks, and CI are scheduled for Phase 3.

The tests use complete miniature repositories under `tools/tests/fixtures/`
and verify CLI behavior as well as the Python functions.
