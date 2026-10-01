# Playbook: implement evals for a change (phase 1)

You are writing tests only. Do not change implementation code.

1. Read the change file you were given, the module's `SPEC.md`, and `SYSTEM.md`.
2. Run `git status` and `git diff`. If there is earlier work for this change, continue from it.
3. Read the change's assumptions. If the Motivation or specs contradict the current spec or
   code in a way you cannot resolve, add `blocked: '<one-sentence reason>'` as the last line of the
   change file's frontmatter and stop. Do not reinterpret the change.
4. If the Interface section is not empty, update the module's interface file (path in SPEC.md
   frontmatter `interface:`) exactly as described. Change nothing else in `src/`.
5. For each spec ID in Added, Modified, and `targets`:
   - Write or update tests in the module's `evals/` directory (for `system`, in `system-evals/`).
   - Every test name MUST contain the spec ID, e.g. `AUTH-014: rejects login after 5 failures`.
   - Test only through the public interface. Cover the main case and the edge cases the spec states.
   - Tests must be deterministic: no real clocks, random seeds, network, or sleeps unless the spec
     requires them; use fakes or injected dependencies exposed by the interface.
6. For each ID in Removed: delete or rename every test that references it.
7. Run `tools/spec coverage <module> --with-change <change file>`.
   New and modified tests SHOULD fail now (the code is not written yet). If a test passes, re-read
   the spec and make sure the test actually checks the new behavior.
   There must be no E01 or E02 errors.
8. If you had to make a choice the spec does not cover, add a bullet to `## Decisions` in the change file.
9. Stop. Do not commit.
