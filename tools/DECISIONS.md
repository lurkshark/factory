# Implementation decisions

- Phase 1 includes the `tools/spec` entrypoint and `tools/requirements.txt` so its CLI and acceptance tests can run; the remaining Phase 3 scaffolding is deferred.
- CLI usage and unexpected failures print one stdout line with `usage` or `tooling` as the code; queue/apply refusals use C09 and exit 1.
- Archived changes supply historical removed IDs and numbering; their edits are not replayed or checked against current specs.
- `apply` uses `git mv` for tracked changes and a plain move for untracked changes or repositories without Git; it rolls back both files if archiving or writing fails.
- Missing or malformed supported manifests produce C12 warnings, and package-relative paths/globs must use POSIX segments without `.` or `..`.
- `list` includes the `system` pseudo-module with `system` in the informational kind column.
