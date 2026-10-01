# Agent instructions

This repository uses a spec-driven workflow. Specs are the source of truth; code implements them.

## Where things are
- `SYSTEM.md` — system-wide spec and project decisions (language, tooling, conventions). Read it first.
- `packages/<module>/SPEC.md` — one module's spec: frontmatter (name, dependencies, interface file,
  preserved files, eval command) and a `## Specs` section of `### <ID>` items.
- `packages/<module>/src/` — implementation. Mostly disposable, except paths listed under `preserve`.
- `packages/<module>/evals/` — black-box tests. Every test name MUST contain the spec ID it verifies,
  e.g. `AUTH-003: token expires after 24 hours`.
- `system-evals/` — cross-module tests for `SYS-` IDs in `SYSTEM.md`.
- `changes/` — pending change files, applied in filename order. `changes/archive/` — applied changes (never edit).
- `docs/playbooks/` — step-by-step procedures. Follow the one you are told to follow exactly.
- `tools/` — workflow tooling. Do NOT modify anything in `tools/`, `.github/`, or `.devcontainer/`
  unless the developer explicitly asks in an interactive session.

## Rules
1. Never edit the `## Specs` section of any `SPEC.md` or `SYSTEM.md` by hand. Spec changes go through
   change files and `tools/spec apply`.
2. Evals test behavior through the module's public interface only. Never import from a module's `src/`
   internals by relative path; import the package by its public name/entrypoint.
3. A module may only depend on the modules listed in its `depends_on`.
4. Files matching `append_only` globs (e.g. migrations) may be added but never edited or deleted.
5. If you make a behavioral choice the spec does not cover, record it under `## Decisions` in the
   change file you are working on. Do not silently invent behavior.
6. Run `tools/spec check` before finishing any task. Use `tools/spec coverage <module>` to run evals.

## Useful commands
- `tools/spec list` — modules and the pending queue
- `tools/spec show <module> [ID] [--projected]` — read specs (`--projected` includes pending changes)
- `tools/spec check` — validate all specs and changes
- `tools/spec coverage <module> [--with-change <file>]` — run evals and map results to spec IDs
