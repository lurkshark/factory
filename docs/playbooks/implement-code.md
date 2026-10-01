# Playbook: implement code for a change (phase 2)

You are writing implementation code. The evals are already written and committed.

1. Read the change file, the module's `SPEC.md`, `SYSTEM.md`, and the module's `evals/`.
2. Run `git status` and `git diff`. If there is earlier work for this change, continue from it.
3. You MUST NOT modify: anything in `evals/` or `system-evals/`, the module's interface file,
   any `SPEC.md` or `SYSTEM.md`, anything in `tools/`, `docs/playbooks/`, or any `AGENTS.md`.
   You MUST NOT modify or delete files matching `append_only` globs; you may add new ones.
4. If the evals appear wrong (they contradict the spec), do not edit them. Add
   `blocked: '<one-sentence reason>'` as the last line of the change file's frontmatter and stop.
5. Implement the change inside `packages/<module>/` (mainly `src/`). You may update the module's
   manifest (dependencies) if needed. Only depend on modules in `depends_on` (plus `set_depends_on`
   if the change has it).
6. For a maintenance change (no spec sections), do what the Motivation describes without changing behavior.
7. Run `tools/spec coverage <module> --with-change <change file>` until it exits 0.
   If `SYSTEM.md` has evals, also run `tools/spec coverage system`.
8. Record any behavioral choice the spec does not cover as a bullet under `## Decisions`
   in the change file. Keep each bullet to one or two sentences.
9. Stop. Do not commit.
