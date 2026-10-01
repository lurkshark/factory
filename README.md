# factory

Phases 1–3 of the spec-driven workflow implement parsing, static validation,
projected specs, applying and archiving changes, JUnit eval coverage, commit
history checks, and PR descriptions, plus agent instructions, playbooks, and CI.

Requires Python 3.11+, Git, and PyYAML:

```sh
python3 -m pip install -r tools/requirements.txt
PYTHONPATH=tools python3 -m unittest discover -s tools/tests -t tools
tools/spec --help
```

Available commands: `check`, `list`, `show`, `next`, `new-change`, `new-module`,
`apply`, `coverage`, `check-commits`, and `pr-body`.

```sh
tools/spec coverage <module> [--with-change changes/NNNN-slug.md]
tools/spec check-commits <base>..HEAD
tools/spec pr-body <base>..HEAD [--notes path/to/log.md]
```

`coverage` runs the module's configured eval command and checks its JUnit report
against spec IDs. Blackbox failures or missing coverage are errors; quarantined
specs produce warnings, and review specs have no eval requirement. `--with-change`
checks the current spec plus that change in memory.

`check-commits` enforces H01–H03 using each commit's module metadata. `pr-body`
groups worker commits by their `Change:` trailer and highlights evals, interfaces,
preserved files, review specs, decisions, and blocked changes.

Commands run from the repository root. The starter `SYSTEM.md` records the
project decisions to fill in before creating modules. Root `AGENTS.md` and
`changes/AGENTS.md` describe the workflow; `CLAUDE.md` points to the root rules.
The four procedures in `docs/playbooks/` cover authoring changes, writing evals,
implementing code, and regeneration.

The GitHub Actions `check` workflow runs tooling tests, static validation, module
and system evals when configured, and PR history checks. It also works before any
modules exist. Enable branch protection on `main` requiring `check` and a PR.

The executable `tools/night` entrypoint is in place; its Python worker and
configuration arrive in Phase 4, the night loop and sandbox in Phase 5, and
regeneration in Phase 6.

The tests use complete miniature repositories under `tools/tests/fixtures/`
and verify CLI behavior as well as the Python functions.
