# factory

All six phases of the spec-driven workflow are implemented: parsing, static validation,
projected specs, applying and archiving changes, JUnit eval coverage, commit
history checks, and PR descriptions, plus agent instructions, playbooks, CI,
a supervised single-change worker, the nightly queue and Docker sandbox,
and isolated module regeneration. See the [implementation verification](docs/implementation-verification.md)
for phase acceptance evidence and remaining environment setup.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) once and
have Git available. The launchers automatically create `tools/.venv/`, select
Python 3.11+ (downloading it if needed), and install the locked dependencies:

```sh
tools/spec check
tools/spec --help
PYTHONPATH=tools uv run --project tools --locked python -m unittest discover -s tools/tests -t tools
```

No environment activation or manual dependency installation is required.
Dependencies are declared in `tools/pyproject.toml` and pinned in
`tools/uv.lock`. After intentionally changing dependencies, update the lockfile
with `uv lock --project tools` and commit both files. CI and Docker use the
same lockfile; launchers reject an outdated lockfile instead of changing it.

Available commands: `check`, `list`, `show`, `next`, `new-change`, `new-module`,
`apply`, `coverage`, `check-commits`, `pr-body`, and `regen`.

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
authenticated installed agent. The installed agent must support the configured
flags and model.

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
branch and performs no remote operations.

Build the sandbox and authenticate its agent before Docker-backed runs:

```sh
docker build -t specflow-worker -f .devcontainer/Dockerfile .
tools/night login
tools/night run --dry-run
tools/night run [--skip-blocked] [--max-changes N]
```

`run` starts from the remote base branch, processes queued changes until its
configured completion or time limit, pushes a `night/YYYY-MM-DD` branch, and
opens a pull request. A blocked queue head halts the loop unless `--skip-blocked`
is supplied. Review and merge the resulting PR manually.

Regenerate disposable source in an isolated worktree with a clean working tree:

```sh
tools/spec regen <module> [--agent]
```

This creates `.worktrees/regen-<module>` on `regen/<module>-<YYYYMMDD>` and
deletes source files that do not match the module's `preserve` globs. Without
`--agent`, it prints instructions for following the regeneration playbook.
With `--agent`, it runs the configured agent and verifies eval coverage. The
branch and worktree remain available for review.

The tests use complete miniature repositories under `tools/tests/fixtures/`
and verify CLI behavior as well as the Python functions.
