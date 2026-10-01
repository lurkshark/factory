# Playbook: author a change

Goal: turn the developer's request into one or more valid change files. Do not write implementation code.

1. Read `SYSTEM.md` and run `tools/spec list`.
2. For each affected module, read its projected spec: `tools/spec show <module> --projected`.
   The projected view includes pending changes; the new change must be written against it.
3. Ask the developer clarifying questions until the behavior is unambiguous. Prefer concrete examples.
4. Split the work so each change file targets exactly one module. If an interface change is needed,
   put the change that alters the interface's owner first (lower number) and dependents after it.
5. Create each file with `tools/spec new-change <module> <slug>`, then fill it in:
   - Motivation: why, in 1–5 sentences.
   - Interface: what to add/alter in the module's interface file, in prose. Leave empty if none.
   - Added: new specs. Pick the next unused number for the module's prefix (check `tools/spec show`).
     Never reuse a removed ID.
   - Modified: the full new text of existing specs (not a diff).
   - Removed: `### <ID>` for each removed spec.
   - Rejected alternatives: options discussed and why they were not chosen.
6. Write specs as observable behavior using MUST / MUST NOT / SHOULD. One behavior per spec.
   Include concrete values (limits, durations, error codes). Do not describe implementation
   (no file names, algorithms, or libraries) unless the developer requires it.
7. Tag a spec `[review]` only if it cannot be checked deterministically by a test
   (e.g. performance feel, security posture). Default is untagged (blackbox).
8. If a new module is needed, run `tools/spec new-module <name> --kind <kind> --prefix <PREFIX>` first.
9. Run `tools/spec check`. Fix every error. Show the developer the final change files.
10. Keep the pending queue short: about one night of work. Suggest deferring the rest.
