# Implementation decisions

- Phase 1 includes the `tools/spec` entrypoint and `tools/requirements.txt` so its CLI and acceptance tests can run; the remaining Phase 3 scaffolding is deferred.
- CLI usage and unexpected failures print one stdout line with `usage` or `tooling` as the code; queue/apply refusals use C09 and exit 1.
- Archived changes supply historical removed IDs and numbering; their edits are not replayed or checked against current specs.
- `apply` uses `git mv` for tracked changes and a plain move for untracked changes or repositories without Git; it rolls back both files if archiving or writing fails.
- Missing or malformed supported manifests produce C12 warnings, and package-relative paths/globs must use POSIX segments without `.` or `..`.
- `list` includes the `system` pseudo-module with `system` in the informational kind column.
- Phase 2 reports malformed XML or an unsupported JUnit root as E01; missing-report diagnostics include the last 40 combined output lines separated by ` | ` to keep one diagnostic per line.
- Coverage test counts include skipped testcases, but skips do not satisfy coverage; review specs with only skipped references display `uncovered` informationally.
- Git history uses NUL-delimited records and explicit rename detection; H01/H02 examine both rename paths, and H03 checks commit and parent metadata so deleting a module or removing protection cannot hide an append-only edit.
- PR descriptions deduplicate and sort file paths, show both rename paths, group changes in first-commit order, and list other commits as abbreviated SHA plus subject; state comes from the change's archive/pending location at HEAD.
- PR descriptions preserve the raw Decisions body, use `none` for an empty body or an absent blocked reason, and omit empty path lists.
