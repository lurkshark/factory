# factory

Phases 1–4 of the spec-driven workflow implement parsing, static validation,
projected specs, applying and archiving changes, JUnit eval coverage, commit
history checks, and PR descriptions, plus agent instructions, playbooks, CI,
and a supervised single-change worker.

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

Run one queued change on the current branch with a clean working tree:

```sh
tools/night once changes/NNNN-slug.md
```

Configure the agent and its argument lists in `tools/night.yaml`. Defaults use
Claude inside Docker; for a supervised host run, select `sandbox: none` and an
authenticated installed agent. The Docker image and full nightly loop arrive
in Phase 5. The installed agent must support the configured flags and model.

`once` verifies the queue head, runs the eval and implementation playbooks,
and creates `evals(<module>)` and `impl(<module>)` commits with `Change:` trailers.
Maintenance changes skip the eval phase. Success applies the spec change and
archives it. Failed attempts retain earlier work for a retry; exhausted attempts
discard that phase's edits and commit a quoted `blocked:` reason. A completed
eval commit stays when implementation blocks. The host undoes agent-created
commits before checking edits.

Logs and captured agent output live in `.night/YYYY-MM-DD/`. Usage limits pause
and retry the same attempt; the wait budget or deadline ends the run cleanly.
`once` returns 0 for completion or a clean stop, 1 for a blocked change or failed
static checks, and 2 for configuration/usage errors. It stays on the current
branch and performs no remote operations. Regeneration arrives in Phase 6.

The tests use complete miniature repositories under `tools/tests/fixtures/`
and verify CLI behavior as well as the Python functions.
