# Implementation verification

Verified on 2026-10-01 against Section 12 of
[`spec-driven-workflow-implementation.md`](../spec-driven-workflow-implementation.md).
All six implementation phases are present, committed, and covered by the
tooling test suite. The table distinguishes checks rerun during this audit
from earlier supervised acceptance recorded in `tools/DECISIONS.md`.

| Phase | Implementation commit | Acceptance evidence |
| --- | --- | --- |
| 1 — Parser, validation, apply | `3d98630` | Phase 1 tests pass, including C01–C13, A01–A04, byte preservation, queue projection, globs, CLI subcommands and exit codes, and fresh-repository acceptance. |
| 2 — Coverage and history | `858b16f` | Phase 2 tests pass, including JUnit statuses and boundaries, E01–E04, H01–H03, every file zone, and the done/blocked PR snapshot. |
| 3 — Instructions, playbooks, CI | `713a2a8` | Root/change instructions and all four playbooks match the document's exact templates. CI YAML and devcontainer JSON parse; requirements, ignore entries, executable entrypoints, and repository static checks pass. |
| 4 — Single-change worker | `cf77583` | Phase 4 tests pass, including phase restrictions, blocking, retries, usage limits, undone agent commits, and maintenance changes. Earlier supervised real Codex pilot completed both commits, apply/archive, coverage, and history checks. CLI flag/version verification is recorded in `tools/DECISIONS.md`. |
| 5 — Night loop and sandbox | `bc65a9d` | Phase 5 tests pass, including halt/skip behavior across queued changes, deadlines, publication, and sandbox login construction. Earlier real Codex run produced [pilot PR #1](https://github.com/lurkshark/factory/pull/1), whose successful GitHub `check` was independently confirmed during this audit. Earlier Docker build/runtime and scripted-worker acceptance are recorded in `tools/DECISIONS.md`. |
| 6 — Regeneration | `87c0c5f` | Phase 6 tests pass, including preserved files, disposable-file deletion, empty-directory removal, retained worktree/branch, agent and coverage failures, symlinks, and Docker worktree metadata mounts. Earlier Docker-backed scripted regeneration acceptance is recorded in `tools/DECISIONS.md`. |

## Checks rerun

- `PYTHONPATH=tools python3 -m unittest discover -s tools/tests -t tools`: 182 tests passed.
- `tools/spec check`: passed without diagnostics.
- `tools/spec check-commits origin/main..HEAD`: passed without diagnostics.
- `tools/spec coverage system`: passed; the starter system currently has no spec items or configured evals.
- `tools/night run --dry-run`: passed; the starter repository has no pending changes.
- Exact instruction/playbook template comparison and scaffolding format checks: passed.
- `git fetch origin`: completed; the implementation descends from remote `main` and can be published without rewriting history.

There are no product modules in the starter repository, so module evals are
exercised through the miniature fixture repositories in the tooling suite.
The audit did not rerun a paid coding agent or rebuild Docker; those supervised
acceptance results were already recorded. The live Phase 5 pilot targeted the
isolated `codex/phase5-pilot-base` branch and exercised the nightly command end
to end; it was not an eight-hour duration test.

## Environment setup remaining

Before operating the default Docker worker, run `tools/night login` to
authenticate the configured agent. Interactive authentication in the container
was not part of the recorded Docker validation. Fill in the starter `SYSTEM.md`
project decisions when adding the actual product, enable the documented branch
protection settings, and review the coding-agent subscription terms for
unattended use. These are deployment/account setup tasks rather than missing
implementation phases.
