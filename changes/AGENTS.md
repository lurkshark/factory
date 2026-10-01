# Change files

- One file per change: `NNNN-short-slug.md`. Create with `tools/spec new-change <module> <slug>`.
- A change targets exactly one module (`module:` in frontmatter) or `system`.
- Changes are applied in filename order. To reorder, rename files (`git mv`). Nothing references the numbers.
- Sections: Motivation (required), Interface, Added, Modified, Removed, Decisions, Rejected alternatives, Notes.
- `Added`/`Modified` contain full `### <ID> [tag]` spec items. `Removed` contains `### <ID>` headings only.
- Use `targets: [ID, ...]` for bug fixes where the spec is correct but evals missed the bug.
- A change with no spec sections is a maintenance change (refactor, dependency bump).
- `blocked: <reason>` in frontmatter means the change is stuck. Delete the key to unblock.
- Never edit files in `changes/archive/`.
