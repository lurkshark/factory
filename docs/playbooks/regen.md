# Playbook: regenerate a module

The module's disposable source files were deleted. Preserved files (see `preserve` in SPEC.md) remain.

1. Read `SYSTEM.md`, the module's `SPEC.md`, its interface file, its preserved files, and all of `evals/`.
2. Do not modify `evals/`, the interface file, preserved files, or any spec file.
3. Write a fresh implementation in `src/` that implements the interface and satisfies every spec.
   Do not try to reconstruct the old code; implement from the spec.
4. Run `tools/spec coverage <module>` until it exits 0.
5. In `packages/<module>/REGEN-NOTES.md`, list every place where the spec or evals were not enough
   to decide behavior. These are gaps the developer should turn into spec changes.
6. Stop. Do not commit.
