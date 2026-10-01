# Spec-Driven Regenerative Workflow — Implementation Specification

**Audience:** a coding agent implementing this system from scratch.
**Status:** final design. Implement it as written. Do not redesign.

---

## 0. How to use this document

1. Read the whole document once before writing any code.
2. Implement in the phase order given in **Section 12**. Finish each phase's acceptance criteria before starting the next one.
3. When this document gives exact text (file templates, playbooks, error formats), copy it exactly.
4. When something is not specified, choose the simplest option that satisfies the stated rules. Record the choice in `tools/DECISIONS.md` (one line per choice).
5. Do **not** add features, commands, flags, config keys, or dependencies that are not listed here. Section 13 lists common mistakes to avoid.

Words used with specific meaning:

- **MUST / MUST NOT**: required. Tests must enforce it where possible.
- **SHOULD**: strongly preferred; deviate only with a note in `tools/DECISIONS.md`.
- **module**: one package directory under `packages/`, plus the pseudo-module `system` (the root `SYSTEM.md`).
- **change**: one Markdown file in `changes/` describing an edit to one module's specs.
- **queue**: the pending change files in `changes/`, sorted lexically by filename.
- **worker**: the non-interactive coding agent run by the night loop.

---

## 1. What this system is

A single-developer (or two-developer) monorepo workflow where:

1. The developer chats interactively with a coding agent (Claude Code or Codex) to write **change files**. A change file says how one module's specification should change.
2. The developer merges change files to `main`. Merging a change file is the developer's approval.
3. A **night loop** script runs unattended. It takes changes from the queue in order and runs the coding agent non-interactively to implement each one in two phases: first write the tests (evals), then write the code.
4. The night loop commits each phase, applies the change to the module's `SPEC.md`, archives the change file, and opens one pull request (PR) on GitHub in the morning.
5. The developer reviews and merges the PR.

Design principles (these explain the rules; they are not extra features):

- `SPEC.md` files are the **current truth** about each module. Only the `spec apply` command edits their spec sections.
- Evals (black-box tests tied to spec IDs) are **durable**. Most of `src/` is **disposable**: it could be deleted and regenerated from the spec plus the evals.
- Deterministic scripts do validation, git, and GitHub work. The agent only reads and writes files in the working tree.
- Keep it simple. Enforcement is light because this is a solo project; some rules only produce warnings.

External dependencies allowed (no others):

- Git and GitHub (`gh` CLI on the host).
- One coding agent CLI: `claude` (Claude Code) or `codex` (Codex CLI).
- Python 3.11+ and **PyYAML** (the only Python package dependency for the tooling). Use the standard library for everything else (`tomllib`, `json`, `xml.etree.ElementTree`, `subprocess`, `fnmatch`, `unittest`, `argparse`, `pathlib`, `re`, `datetime`).
- Docker (only for the night loop sandbox; the loop can also run without it).

---

## 2. Decisions already made (do not revisit)

| Topic | Decision |
|---|---|
| Spec file format | Markdown with YAML frontmatter. Spec items are `###` headings with IDs. |
| Tooling language | Python 3.11+, stdlib + PyYAML. Tests use `unittest`. |
| Project language | Unknown/any. Tooling is language-agnostic. Manifest adapters exist for `package.json` and `pyproject.toml` only. |
| Change ordering | Lexical order of filenames. No change IDs inside files. No dependencies between changes. |
| Change scope | Exactly one module per change file. |
| Change state | Pending = file in `changes/`. Done = file in `changes/archive/`. Blocked = `blocked:` key in its frontmatter. |
| Interfaces | Declared in a native file inside the package (e.g. `src/api.ts`, `openapi.yaml`, a `.pyi` stub). Frontmatter points at it. No custom interface schema. |
| Eval → spec link | Test names contain the spec ID. Results are read from JUnit XML. |
| Worker protocol | Two phases, two commits: `evals(...)` then `impl(...)`. The implementation phase MUST NOT touch evals or the interface file. |
| Who runs git | Only the night loop script (host). The agent never commits, pushes, or opens PRs. |
| Nightly output | One branch `night/YYYY-MM-DD` and one PR per night. |
| Merging | Always by a human. No auto-merge. |
| Tooling ownership | `tools/` is human-maintained. Worker commits MUST NOT touch it. |
| Regeneration | Manual command `spec regen <module>`, built last. Never scheduled. Never blocks anything. |

---

## 3. Repository layout

The tooling MUST work with exactly this layout:

```
AGENTS.md                      # root instructions for all agents (template in §9.1)
CLAUDE.md                      # contains exactly one line: @AGENTS.md
SYSTEM.md                      # system-level spec (format in §4.2)
packages/
  <module>/
    AGENTS.md                  # generated stub (template in §9.2)
    SPEC.md                    # module spec (format in §4.1)
    package.json | pyproject.toml | ...   # the module's native manifest
    src/                       # implementation (mostly disposable)
    evals/                     # black-box tests for this module (durable)
system-evals/                  # cross-module tests tied to SYSTEM.md IDs (optional)
changes/
  AGENTS.md                    # template in §9.3
  0001-some-slug.md            # pending changes (format in §5)
  archive/                     # applied changes, never edited again
docs/
  playbooks/
    author-change.md           # interactive authoring (text in §10.1)
    implement-evals.md         # worker phase 1 (text in §10.2)
    implement-code.md          # worker phase 2 (text in §10.3)
    regen.md                   # regeneration (text in §10.4)
tools/
  spec                         # executable entrypoint: python3 -m specflow "$@"
  night                        # executable entrypoint: python3 -m specflow.night "$@"
  requirements.txt             # exactly: PyYAML>=6
  night.yaml                   # night loop config (format in §8.2)
  DECISIONS.md                 # implementer's notes on unspecified choices
  specflow/                    # Python package with all tooling code
  tests/                       # unittest tests + fixture repos
.github/workflows/check.yml    # CI (§11)
.devcontainer/                 # Dockerfile + devcontainer.json for the sandbox (§8.7)
.gitignore                     # must include: .night/  .worktrees/  **/evals/.results/
```

`tools/spec` and `tools/night` are small shell scripts:

```sh
#!/bin/sh
# tools/spec
cd "$(git rev-parse --show-toplevel)" || exit 2
PYTHONPATH=tools exec python3 -m specflow "$@"
```

```sh
#!/bin/sh
# tools/night
cd "$(git rev-parse --show-toplevel)" || exit 2
PYTHONPATH=tools exec python3 -m specflow.night "$@"
```

All tooling paths are relative to the repository root. All commands run from the root.

---

## 4. Spec file formats

### 4.1 Module spec: `packages/<module>/SPEC.md`

Example:

```markdown
---
name: auth
kind: service
id_prefix: AUTH
depends_on: [userstore, clock]
interface: src/api.ts
preserve:
  - src/api.ts
  - src/migrations/**
append_only:
  - src/migrations/**
evals:
  run: pnpm vitest run --dir evals --reporter=junit --outputFile=evals/.results/junit.xml
  report: evals/.results/junit.xml
---

# auth

Authenticates users and issues sessions. Any prose here is ignored by the tooling.

## Specs

### AUTH-001
A valid login MUST return a session token.

### AUTH-003
A token MUST expire 24 hours after it is issued.

### AUTH-020 [review]
Session tokens MUST be unguessable.
```

#### Frontmatter rules

The file MUST start with a line that is exactly `---`. The frontmatter ends at the next line that is exactly `---`. The text between them is parsed with `yaml.safe_load` and MUST produce a mapping.

| Key | Required | Type | Rules |
|---|---|---|---|
| `name` | yes | string | MUST equal the package directory name. Regex `^[a-z][a-z0-9-]*$`. The name `system` is reserved. |
| `kind` | yes | string | One of `library`, `service`, `cli`, `infra`. Informational only. |
| `id_prefix` | yes | string | Regex `^[A-Z][A-Z0-9]*$`. MUST be unique across all modules and MUST NOT be `SYS`. |
| `depends_on` | yes | list of strings | Written in YAML flow style **on one line**, e.g. `depends_on: [a, b]` or `depends_on: []`. Each entry MUST be an existing module name. No duplicates. No cycles across the repo. |
| `interface` | no | string | Path relative to the package dir. File MUST exist. MUST match at least one `preserve` glob. |
| `preserve` | no | list of strings | Globs relative to the package dir (see §4.4 for glob rules). Files matching are durable even inside `src/`. |
| `append_only` | no | list of strings | Globs relative to the package dir. Every `append_only` glob MUST also appear in `preserve` (exact string match). Files matching may be added but never modified or deleted by a change. |
| `evals` | no | mapping | Keys `run` (shell command string, run with the package dir as working dir) and `report` (path to a JUnit XML file, relative to the package dir). Both required if `evals` is present. |

Unknown keys are an error. If `evals` is absent, the module has no evals; every `blackbox` spec in it is then reported as uncovered by `spec coverage`.

#### Body rules

- The body is everything after the closing `---`.
- The body MUST contain exactly one line that is exactly `## Specs` (outside code fences). This starts the **specs section**.
- The specs section ends at the next `## ` or `# ` heading (outside code fences), or at end of file.
- Inside the specs section, the only allowed headings are spec headings (see below). Any other heading of any level (`#`, `##`, `####`, etc.) inside it is an error.
- Text inside the specs section that appears before the first spec heading MUST be blank lines only.
- Everything outside the specs section is free prose and is ignored.

**Spec heading grammar** (the whole line must match):

```
^### (?P<id>[A-Z][A-Z0-9]*-\d{3,})(?: \[(?P<tag>blackbox|review|quarantine)\])?$
```

- The ID's prefix (text before `-`) MUST equal the module's `id_prefix`.
- The ID MUST be unique across the whole repository (all modules and `SYSTEM.md`).
- Tag defaults to `blackbox` when absent. Writing `[blackbox]` explicitly is allowed.
- The **spec body** is every line after the heading up to (not including) the next heading of any level outside code fences, or the end of the specs section.
- The spec body MUST contain at least one non-blank line.

**Tag meanings:**

| Tag | Meaning |
|---|---|
| `blackbox` | MUST have at least one passing eval whose test name contains the ID. |
| `review` | Checked by a human, not by evals. Not required to have evals. Listed in PR bodies when added or modified. |
| `quarantine` | Its evals are known to be unreliable. Failing or missing evals are reported as warnings, not errors. |

**Code fences:** a line whose first non-space characters are ` ``` ` or `~~~` toggles "inside fence" state. Headings inside fences are ordinary text. The same fence rule applies to every Markdown parsing step in this document.

### 4.2 System spec: `SYSTEM.md`

Same parser and body rules as §4.1, with this frontmatter instead:

| Key | Required | Rules |
|---|---|---|
| `name` | yes | MUST be exactly `system`. |
| `id_prefix` | yes | MUST be exactly `SYS`. |
| `evals` | no | Same as §4.1, but paths are relative to `system-evals/`, and `run` executes with `system-evals/` as working dir. |

Unknown keys are an error. The free prose section of `SYSTEM.md` SHOULD record project-wide decisions: language, runtime, how import boundaries are enforced, test framework, deployment target. Tooling does not read that prose.

### 4.3 No ID reuse

An ID that was removed MUST NOT be reused. `spec new-change` does not allocate IDs; the authoring agent picks the next number. To enforce no reuse, `spec check` collects every ID ever mentioned as a `###` heading in `changes/archive/*.md` under `## Removed` and errors if any current spec or pending change adds that ID again (check `C13`).

### 4.4 Globs

Use this exact matching function everywhere globs appear (`preserve`, `append_only`):

- Paths are POSIX-style, relative to the package dir, with no leading `./`.
- `**` matches zero or more whole path segments. `*` matches any characters except `/`. `?` matches one character except `/`.
- Implement by converting the glob to a regex: split on `/`; a segment equal to `**` becomes `(?:[^/]+/)*` (and a trailing `**` becomes `.*`); other segments are converted with `fnmatch.translate`-style rules restricted to not match `/`. Anchor the whole regex with `^...$`.
- Tests MUST cover: `src/migrations/**` matches `src/migrations/001.sql` and `src/migrations/a/b.sql` but not `src/migrationsx/1.sql`; `src/*.ts` matches `src/api.ts` but not `src/a/api.ts`.

---

## 5. Change file format: `changes/NNNN-slug.md`

### 5.1 Filename

- Regex: `^(?P<num>\d{4})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.md$`.
- The **change name** is the filename without `.md`, e.g. `0007-login-rate-limit`. It is used in branch names, commit trailers, and messages.
- Two files (pending or archived) MUST NOT share the same `num` (check `C08`).
- `changes/AGENTS.md` is not a change file. Ignore it and any other file whose name does not match the regex, but emit `WARN C07` for unexpected `.md` files other than `AGENTS.md`.

### 5.2 Example

```markdown
---
module: auth
---

## Motivation
Credential-stuffing attempts appear in production logs. Limit repeated failures.

## Interface
Add the error code `RATE_LIMITED` to the `login` operation's error union in `src/api.ts`.

## Added

### AUTH-014
After 5 failed logins for the same email within 10 minutes, further login attempts for
that email MUST be rejected with `RATE_LIMITED` for 15 minutes.

## Modified

### AUTH-003
A token MUST expire 12 hours after it is issued.

## Removed

### AUTH-009

## Decisions

## Rejected alternatives
- Per-IP limiting: breaks for users behind shared NAT.
```

### 5.3 Frontmatter

| Key | Required | Type | Rules |
|---|---|---|---|
| `module` | yes | string | An existing module name, or `system`. |
| `targets` | no | list of spec IDs, flow style on one line | IDs of **existing** specs (in the projected spec) that this change needs new or fixed evals for, without changing their text. Used for bug fixes where the spec was right but evals missed the bug. |
| `set_depends_on` | no | list of strings, flow style on one line | Replaces the module's `depends_on` when applied. Not allowed when `module: system`. |
| `blocked` | no | string | Present only when the change is blocked. The value is the reason. Written by the worker or night loop. A human deletes the key to unblock. |

Unknown keys are an error.

### 5.4 Body sections

The body consists of `## ` sections. Allowed section titles (exact text), each at most once, in any order:

`Motivation`, `Interface`, `Added`, `Modified`, `Removed`, `Decisions`, `Rejected alternatives`, `Notes`.

Rules:

- `## Motivation` is required and MUST contain at least one non-blank line.
- Any other `## ` title is an error. Text before the first `## ` heading MUST be blank.
- Inside `Added` and `Modified`: only spec headings (grammar from §4.1) are allowed as headings. Each MUST have a non-empty body. The ID prefix MUST equal the target module's `id_prefix` (or `SYS` for system).
- Inside `Removed`: only `### <ID>` headings (tag not allowed). Body text under a removed heading is allowed and ignored (it can hold a reason).
- An ID MUST NOT appear more than once across `Added`, `Modified`, `Removed`, and `targets` in the same change.
- `Interface`, `Decisions`, `Rejected alternatives`, `Notes`: free prose. Headings of level `###` or deeper inside them are allowed and ignored.
- A change with no `Added`, `Modified`, `Removed`, `targets`, or `set_depends_on` is a **maintenance change** (for example a dependency bump or refactor). It is valid.

### 5.5 Change kinds (derived, used by the night loop)

- **Spec change**: has `Added`, `Modified`, `Removed`, `targets`, or a non-empty `Interface` section. The worker runs phase 1 (evals) and phase 2 (code).
- **Maintenance change**: none of the above. The worker skips phase 1 and runs phase 2 only.
- `set_depends_on` alone does not require phase 1.

---

## 6. Core algorithms

### 6.1 Parsing a Markdown spec document

Implement one parser used for `SPEC.md`, `SYSTEM.md`, and change files. It MUST keep the original text so that edits preserve every untouched byte.

Data model (Python dataclasses, names are suggestions):

```python
@dataclass
class Section:            # a heading and its body
    level: int            # 2 for "##", 3 for "###"
    title: str            # heading text after the hashes and one space
    start: int            # index of the heading line in `lines`
    end: int              # index one past the last body line
@dataclass
class Doc:
    path: Path
    lines: list[str]      # file split with str.splitlines(keepends=True)
    fm_text: str          # raw frontmatter text between the fences
    fm: dict              # yaml.safe_load(fm_text) or {}
    fm_end: int           # index of the line after the closing '---'
    body_start: int       # == fm_end
```

Steps:

1. Read the file as UTF-8. Normalize nothing. Split with `splitlines(keepends=True)`.
2. Line 0 MUST be `---` (after stripping the line ending). Find the next line that is `---`. Missing either is error `C03`.
3. Parse headings from `body_start` onward. Track code fences (§4.1). A heading is a line matching `^(#{1,6}) (.*?)\s*$` outside a fence.
4. Each heading's section ends at the next heading whose level is **less than or equal** to its own level, or at end of file. A `###` section inside a `##` section therefore ends at the next `###` or `##`.
5. Report line numbers to users as 1-based.

### 6.2 Projected spec

The **projected spec** of a module is its `SPEC.md` after applying, in queue order, every pending change for that module, including blocked ones (the same rule as the queue dry-run, check C10). Compute it in memory with the apply algorithm (§6.3). Never write it to disk.

`spec show --projected` and the authoring playbook use it. `spec coverage --with-change FILE` uses "current spec + that one change" instead.

### 6.3 Applying a change (`spec apply`)

Input: one change file and its target module's `SPEC.md` (or `SYSTEM.md`). Output: new spec file text, or a list of errors. On any error, write nothing.

Algorithm, in this order:

1. **Removed**: for each ID, find the spec section with that ID in the specs section. If missing → error `A01 cannot remove <ID>: not found`. Delete lines `[start, end)`.
2. **Modified**: for each ID, find its spec section. If missing → error `A02 cannot modify <ID>: not found`. Replace lines `[start, end)` with the change's section lines (heading + body) for that ID, normalized per step 5.
3. **Added**: for each ID in the order written, if it already exists → error `A03 cannot add <ID>: already exists`. Append the change's section lines at the end of the specs section, normalized per step 5.
4. **set_depends_on**: find the frontmatter line matching `^depends_on:\s*\[.*\]\s*$`. If there is not exactly one such line → error `A04 depends_on must be a single flow-style line`. Replace it with `depends_on: [` + `", ".join(names)` + `]` + original line ending.
5. **Normalization of inserted sections**: strip trailing blank lines from the section, then append exactly one blank line (`"\n"`). Also ensure the line immediately before an appended section is blank (insert `"\n"` if not). Use `"\n"` line endings for inserted text.
6. Re-parse the result and run the per-file checks (`C03`–`C05`) on it in memory. If any error → return errors and write nothing.

Recompute section indexes after every edit (simplest: re-parse after each step).

Round-trip guarantee (tests MUST verify): for a file with no pending edits, parse followed by joining `lines` reproduces the file byte-for-byte; applying a change changes only the targeted spec sections and the `depends_on` line.

### 6.4 JUnit results and coverage

1. Run the module's `evals.run` with `subprocess.run(cmd, shell=True, cwd=<package dir>)`. Delete the report file before running. A non-zero exit code is normal when tests fail; do not treat it as a tooling error.
2. If the report file does not exist after the run → error `E01 eval run produced no report: <path>`. Include the last 40 lines of combined stdout/stderr in the message.
3. Parse with `xml.etree.ElementTree`. Accept a root of `<testsuites>` or `<testsuite>`. Iterate all `<testcase>` elements at any depth.
4. For each testcase, the **test label** is `classname + " " + name` (either may be missing; use empty string). Status: `fail` if it has a `<failure>` or `<error>` child; `skip` if it has a `<skipped>` child; otherwise `pass`.
5. A testcase **references** spec ID `X` when this regex finds a match in the label: `(?<![A-Za-z0-9])` + `re.escape(X)` + `(?![0-9])`. This means `AUTH-003` does not match inside `AUTH-0031` or `XAUTH-003`.
6. Any substring of a label matching `(?<![A-Za-z0-9])[A-Z][A-Z0-9]*-\d{3,}(?![0-9])` is a **candidate ID**. A candidate ID whose prefix belongs to any module or `SYS` but which is not a spec in the projected set being checked is an **orphan** → error `E02 test references unknown spec <ID>: <label>`. Candidate IDs with unknown prefixes are ignored (so strings like `UTF-008` do not cause errors).
7. For each spec:
   - `blackbox`: needs at least one referencing testcase, and all referencing testcases must be `pass` (skips are ignored, but a spec whose only references are skips counts as uncovered). Otherwise error `E03 <ID> uncovered` or `E04 <ID> failing: <labels>`.
   - `quarantine`: same computation, but report `WARN E03`/`WARN E04` instead of errors.
   - `review`: not required. If tests reference it, report their status informationally.

### 6.5 File zones (used by the night loop, `check-commits`, and `pr-body`)

Classify every repo-relative path into exactly one zone, checked in this order:

| Zone | Rule |
|---|---|
| `tools` | starts with `tools/` or `.github/` or `.devcontainer/` |
| `changes` | starts with `changes/` |
| `playbooks` | starts with `docs/playbooks/` or is any `AGENTS.md`/`CLAUDE.md` |
| `system-spec` | is `SYSTEM.md` |
| `system-evals` | starts with `system-evals/` |
| `spec` | matches `packages/<m>/SPEC.md` |
| `evals` | starts with `packages/<m>/evals/` |
| `interface` | equals `packages/<m>/` + that module's `interface` value |
| `preserved` | under `packages/<m>/` and matches one of that module's `preserve` globs |
| `implementation` | starts with `packages/<m>/src/` |
| `package-meta` | any other path under `packages/<m>/` (manifests, configs) |
| `other` | anything else |

Use the module's `SPEC.md` as it exists at the commit being examined (read with `git show <rev>:<path>`), falling back to the working tree when examining uncommitted changes.

---

## 7. The `spec` command

Entry point: `tools/spec <subcommand> [args]`. Use `argparse` with subcommands.

### 7.1 Output and exit codes (all subcommands)

- Errors: `ERROR <code> <path>[:<line>]: <message>` on stdout, one per line.
- Warnings: `WARN <code> <path>[:<line>]: <message>`.
- Sort all messages by (path, line, code) before printing so output is deterministic.
- Last line when anything was reported: `<N> error(s), <M> warning(s)`.
- Exit codes: `0` success (warnings allowed), `1` validation or eval errors, `2` usage error or unexpected exception (print a one-line message, no traceback unless `SPECFLOW_DEBUG=1`), `3` queue head is blocked (`next` only), `4` queue empty (`next` only).

### 7.2 Subcommands

| Command | Behavior |
|---|---|
| `spec check` | Runs all static checks in §7.3. Does not run evals. Does not use git history. |
| `spec coverage <module\|system> [--with-change FILE]` | Runs the evals of one module and applies §6.4. The spec set is the current spec, or current + FILE applied if `--with-change` is given. Prints one line per spec: `<ID> <tag> <status> (<n> tests)` where status is `pass`, `fail`, `uncovered`, or `n/a` for review specs without tests. Then prints errors/warnings. Exit 1 on errors. |
| `spec list` | Prints every module (`name kind id_prefix spec-count`), then the queue: `<change-name> <module> [BLOCKED: reason]`. |
| `spec show <module\|system> [ID] [--projected]` | Prints the spec text (whole specs section, or one spec section if ID given). With `--projected`, uses the projected spec (§6.2). |
| `spec next` | Prints the path of the first pending change. Exit 4 if the queue is empty. Exit 3 (and prints the path plus reason) if that change is blocked. |
| `spec new-change <module\|system> <slug>` | Creates `changes/NNNN-<slug>.md` where NNNN is 1 + the largest number among pending and archived changes (0001 if none), zero-padded to 4 digits. Writes the template in §7.4. Prints the path. Errors if the slug is invalid or the module does not exist. |
| `spec new-module <name> --kind K --prefix P` | Creates `packages/<name>/SPEC.md` (template §7.5), `packages/<name>/AGENTS.md` (template §9.2), and empty `src/` and `evals/` directories (each with a `.gitkeep`). Does not create a manifest. Errors if the directory exists, the name or prefix is invalid, or the prefix is taken. |
| `spec apply <change-file> [--now]` | Applies the change (§6.3) to its module's spec file, then `git mv`s the change file to `changes/archive/` (use plain file move if not in a git repo). Without `--now`, the file MUST be the queue head and not blocked, else error. With `--now`, any pending non-blocked change is allowed; afterwards re-run the queue dry-run (check `C10`) and print its warnings. Does not commit. |
| `spec check-commits <rev-range>` | Runs the history checks in §7.3 (`H01`–`H03`) over the commits in the range (e.g. `origin/main..HEAD`). |
| `spec pr-body <rev-range> [--notes FILE]` | Prints a Markdown PR description (§8.6). |
| `spec regen <module> [--agent]` | Phase 6 feature. See §8.8. |

### 7.3 Checks

Static checks (`spec check`):

| Code | Level | Rule |
|---|---|---|
| C01 | error | `SYSTEM.md` exists and passes C03–C05 with the system frontmatter rules. |
| C02 | error | Every directory directly under `packages/` has a `SPEC.md`, and its `name` equals the directory name. |
| C03 | error | Frontmatter present, valid YAML mapping, required keys present, no unknown keys, value types and regexes as in §4 and §5.3. |
| C04 | error | Body grammar as in §4.1 / §5.4 (single `## Specs`, only spec headings inside, valid IDs, non-empty bodies, prefix matches). |
| C05 | error | Spec IDs unique across the repo; `id_prefix` unique across modules. |
| C06 | error | `depends_on` entries exist, no self-dependency, no cycles (report one cycle path). |
| C07 | error/warn | Change filenames valid; unexpected `.md` files in `changes/` are warnings. |
| C08 | error | No duplicate change number across pending and archived changes. |
| C09 | error | Change frontmatter and body valid (§5.3, §5.4); `module` exists; `targets` IDs exist in the target module's projected spec at that point in the queue; `set_depends_on` entries exist. |
| C10 | error | **Queue dry-run**: apply every pending change in lexical order to in-memory copies of all spec files. Report every A01–A04 failure as C10 with the change file path. Blocked changes are still dry-run applied. |
| C11 | error | `interface` file exists and matches a `preserve` glob; every `append_only` glob also appears in `preserve`. |
| C12 | warn | Manifest dependency agreement (§7.6): internal dependencies in the manifest equal `depends_on`. Warn once per module with no supported manifest. |
| C13 | error | No current spec or pending change adds an ID that an archived change removed (§4.3). |

History checks (`spec check-commits <range>`), using `git log --format=%H%x00%s%x00%b` and `git diff-tree --no-commit-id --name-status -r <sha>` per commit. A **worker commit** is one whose message body contains a trailer line matching `^Change: \d{4}-[a-z0-9-]+$`.

| Code | Level | Rule |
|---|---|---|
| H01 | error | A worker commit MUST NOT touch any path in zone `tools` or `playbooks`. |
| H02 | error | A worker commit whose subject starts with `impl(` MUST NOT touch zones `evals`, `system-evals`, or `interface`. |
| H03 | error | Across the whole range, a path matching an `append_only` glob MUST NOT have status `M`, `D`, or `R` (added `A` is fine). Applies to all commits, human or worker. |

### 7.4 Template written by `spec new-change`

```markdown
---
module: <module>
---

## Motivation
TODO: why this change is needed.

## Interface

## Added

## Modified

## Removed

## Decisions

## Rejected alternatives
```

`spec check` MUST error (`C09`) if the Motivation body is exactly `TODO: why this change is needed.` so unfinished templates cannot be merged. Empty `Interface`, `Added`, `Modified`, `Removed` sections are allowed and mean "nothing".

### 7.5 Template written by `spec new-module`

```markdown
---
name: <name>
kind: <kind>
id_prefix: <PREFIX>
depends_on: []
---

# <name>

TODO: one paragraph describing this module's purpose.

## Specs
```

(No `evals` key. The developer adds it once the module's test command exists.)

### 7.6 Manifest adapters (check C12)

Implement exactly two adapters. Choose by file presence in the package dir: `package.json` first, then `pyproject.toml`.

- **package.json**: collect names from `dependencies`, `devDependencies`, `peerDependencies`, `optionalDependencies` whose version string starts with `workspace:`. Map each name to a module by reading every other module's `package.json` `name` field. A `workspace:` dependency that maps to no module → warning. The mapped module names form the manifest dependency set.
- **pyproject.toml** (parse with `tomllib`): collect `[project].dependencies` entries; take the distribution name (text before any of `<>=!~;[ (`), normalized per PEP 503 (lowercase, runs of `-_.` become `-`). A dependency is internal if it equals the normalized `[project].name` of another module's `pyproject.toml`.

Compare the manifest set to `depends_on`. Report `WARN C12` listing names missing from either side. Warnings only, never errors.

---

## 8. The night loop (`tools/night`)

### 8.1 Commands

| Command | Behavior |
|---|---|
| `tools/night run [--dry-run] [--skip-blocked] [--max-changes N]` | Runs the loop (§8.3). `--dry-run` prints the plan (which changes, which phases) and exits without running the agent or git. |
| `tools/night once <change-file>` | Runs the loop for exactly one change on the current branch, without creating a night branch, pushing, or opening a PR. Used during Phase 4 to test supervised. |
| `tools/night login` | Starts the sandbox container interactively with the agent CLI so the developer can log in once (§8.7). Only when `sandbox: docker`. |

### 8.2 Config file: `tools/night.yaml`

Create this file with exactly these keys and defaults. The loop MUST error on unknown keys and fill missing keys with these defaults.

```yaml
agent: claude                  # claude | codex
base_branch: main
remote: origin
sandbox: docker                # docker | none
docker_image: specflow-worker  # built from .devcontainer/Dockerfile
docker_mounts:                 # extra -v mounts; agent login state lives here
  - specflow-agent-home:/home/worker
attempt_timeout_minutes: 90
max_attempts_per_phase: 2
max_changes: 10
max_hours: 8
limit_patterns:                # case-insensitive regexes matched against agent output
  - "usage limit"
  - "rate limit"
  - "limit reached"
  - "limit will reset"
  - "quota"
limit_sleep_minutes: 30
max_limit_waits: 16
agent_commands:
  claude: ["claude", "-p", "{prompt}", "--dangerously-skip-permissions", "--max-turns", "200"]
  codex:  ["codex", "exec", "--dangerously-bypass-approvals-and-sandbox", "{prompt}"]
agent_commands_no_sandbox:
  claude: ["claude", "-p", "{prompt}", "--permission-mode", "acceptEdits", "--allowedTools", "Read,Edit,Write,Glob,Grep,Bash", "--max-turns", "200"]
  codex:  ["codex", "exec", "--full-auto", "{prompt}"]
```

**Important:** the agent CLI flags above were chosen from public documentation and may have changed. Before finishing Phase 4, run `claude --help` and/or `codex exec --help` and correct the arrays. Record the verified CLI version in `tools/DECISIONS.md`. All CLI-specific knowledge MUST live only in this config file and in the single function that builds the command (§8.4). The permissive flags in `agent_commands` are acceptable only because the agent runs inside the container.

### 8.3 Loop algorithm (`tools/night run`)

Write it as straightforward sequential code. State lives in git and files only. The loop keeps a run log at `.night/<YYYY-MM-DD>/log.md` (gitignored) and saves each agent attempt's output to `.night/<date>/<change-name>-<phase>-<attempt>.log`.

```
START:
  require: working tree clean (git status --porcelain is empty), else exit 2
  require: `gh auth status` succeeds, else exit 2
  git fetch <remote>
  branch = "night/" + today (if it exists locally or remotely, append "-2", "-3", ...)
  git switch -c <branch> <remote>/<base_branch>
  run `tools/spec check`; if it fails -> log it, exit 1 (never start on a broken main)
  deadline = now + max_hours
  done_count = 0

LOOP:
  while done_count < max_changes and now < deadline:
    head = first pending change (lexical)
    if none: break
    if head is blocked:
        if --skip-blocked: pick the first non-blocked pending change instead; if none, break
        else: log "queue halted at <head>: <reason>"; break
    result = process_change(change)
    if result == BLOCKED and not --skip-blocked: break
    if result == DONE: done_count += 1

FINISH:
  if branch has no commits beyond <remote>/<base_branch>:
      git switch <base_branch>; git branch -D <branch>; exit 0
  git push -u <remote> <branch>
  body = `tools/spec pr-body <remote>/<base_branch>..HEAD --notes .night/<date>/log.md`
  gh pr create --base <base_branch> --head <branch> --title "Night <date>: <N> change(s)" --body-file <tmp>
  exit 0
```

```
process_change(change):
  name = change name; m = change's module
  pkg = "packages/<m>" (or "system-evals" + SYSTEM.md for system)

  if change is a spec change (§5.5):
      ok = run_phase(change, phase=1)
      if not ok: return mark_blocked(change, reason)
      if agent wrote `blocked:` into the change file: return commit_blocked(change)
      verify phase 1 (§8.3.1); on failure retry (counts as an attempt); after max attempts -> mark_blocked
      git add -A; git commit -m "evals(<m>): <name>" -m "Change: <name>"

  ok = run_phase(change, phase=2)
  if agent wrote `blocked:`: git restore everything except the change file; return commit_blocked(change)
  verify phase 2 (§8.3.2); on failure: retry with failure output in the prompt; after max attempts:
      discard phase-2 working tree changes (git restore --staged --worktree . ; git clean -fd -- packages system-evals);
      return mark_blocked(change, "phase 2 failed verification after N attempts: <first error line>")
  tools/spec apply <change file>          # must succeed; it was already verified with --with-change
  tools/spec check                        # must pass; if not, treat as a phase-2 verification failure
  git add -A; git commit -m "impl(<m>): <name>" -m "Change: <name>"
  return DONE

mark_blocked(change, reason):
  write `blocked: <reason>` into the change file frontmatter (insert as last frontmatter line; single-quote the YAML string)
  commit_blocked(change)

commit_blocked(change):
  git add <change file>; git commit -m "chore: block <name>" -m "Change: <name>"
  return BLOCKED
```

Note that phase 1's commit stays in the branch even if phase 2 later blocks. That is intentional: the evals are useful for the next attempt and the reviewer sees them.

After every agent attempt, before verification, the loop MUST check that `git rev-parse HEAD` is unchanged. If the agent created commits, run `git reset --soft <previous HEAD>` and record `WARN agent committed; commits were undone` in the log.

#### 8.3.1 Phase 1 verification

Compute the changed paths with `git status --porcelain` (include untracked). All must hold:

1. Every changed path is in zone `evals` or `interface` of the change's module, or `system-evals` (only when the module is `system`), or is the change file itself (the agent may add to `## Decisions`). Any other path → failure `P1-01 phase 1 touched <path>`.
2. `tools/spec coverage <m> --with-change <file>` produces no `E01` or `E02` errors.
3. Every ID in `Added`, `Modified`, and `targets` has at least one referencing testcase. Missing → failure `P1-02 no eval for <ID>`.
4. **Fail-first (warning only)**: if every referencing testcase for a given ID passes, log `WARN P1-03 evals for <ID> already pass before implementation`. Do not fail.
5. If the change's `Interface` section is non-empty and the interface file did not change, log `WARN P1-04 Interface section present but interface file unchanged`.

#### 8.3.2 Phase 2 verification

1. No changed path (compared to `HEAD` at the start of phase 2, including untracked files) is in zones `evals`, `system-evals`, `interface`, `tools`, `playbooks`, `spec`, or `system-spec`. Violation → failure `P2-01`. Changes in `changes/` are allowed only to the current change file.
2. No path matching an `append_only` glob is modified or deleted. Violation → failure `P2-02`.
3. `tools/spec coverage <m> --with-change <file>` exits 0.
4. If `SYSTEM.md` has an `evals` key and the module is not `system`, `tools/spec coverage system` also exits 0.
5. Paths in zone `preserved` that changed are recorded in the log (not a failure) so the PR body can flag them.

### 8.4 Running the agent

One function builds and runs the command:

```python
def run_agent(prompt: str, log_path: Path, cfg) -> AgentResult:
    # 1. pick cfg.agent_commands[cfg.agent] (sandbox: docker) or agent_commands_no_sandbox
    # 2. replace the element "{prompt}" with the prompt string (never use a shell)
    # 3. if sandbox == docker, prefix with:
    #    ["docker", "run", "--rm", "-v", f"{repo_root}:/workspace", "-w", "/workspace",
    #     *[x for mnt in cfg.docker_mounts for x in ("-v", mnt)], cfg.docker_image]
    # 4. subprocess.run(..., capture_output=True, text=True, timeout=attempt_timeout)
    # 5. write stdout+stderr to log_path
    # 6. return AgentResult(exit_code, output, timed_out, hit_limit)
```

`hit_limit` is true when any `limit_patterns` regex matches the output (case-insensitive) **and** the exit code is non-zero. When `hit_limit` is true, the loop sleeps `limit_sleep_minutes`, then re-runs the **same attempt** without counting it against `max_attempts_per_phase`. After `max_limit_waits` consecutive limit waits, or if the next wait would pass the deadline, stop the loop cleanly (go to FINISH). A timeout counts as a failed attempt.

A non-zero exit that is not a limit is a failed attempt. A zero exit is not proof of success: always run verification.

### 8.5 Prompts

Prompts are short and point at playbooks. Use exactly these templates (`{...}` are substituted):

Phase 1, first attempt:
```
You are running unattended. Follow docs/playbooks/implement-evals.md exactly.
Change file: {change_path}
Module: {module}
Do not run git commit, git push, or any git command that changes history.
```

Phase 2, first attempt:
```
You are running unattended. Follow docs/playbooks/implement-code.md exactly.
Change file: {change_path}
Module: {module}
Do not run git commit, git push, or any git command that changes history.
```

Any retry (either phase) appends:
```
This is attempt {n} of {max}. A previous attempt did not pass verification.
Your earlier work is still in the working tree; inspect it with `git status` and `git diff` before continuing.
Verification output:
{last 80 lines of verification output}
```

### 8.6 PR body (`spec pr-body <range> [--notes FILE]`)

Group commits in the range by their `Change:` trailer (commits without a trailer go under "Other commits"). For each change, in first-commit order, print:

```markdown
## 0007-login-rate-limit (auth) — DONE | BLOCKED

**Blocked reason:** ... (only if blocked)

**Read carefully**
- Evals changed: <list of paths in zones evals/system-evals>
- Interface changed: <paths>
- Preserved files changed: <paths>
- Review-tagged specs added/modified: <IDs>
- Decisions recorded by the agent:
  <verbatim contents of the change's ## Decisions section, or "none">

**Skim**
- Implementation files changed: <count> (<first 10 paths>)
- Other files: <paths>
```

The change file is read from `changes/archive/` (done) or `changes/` (blocked) at `HEAD`. Omit any list that is empty. After all changes, if `--notes` is given, append `## Night log` followed by the file's contents (warnings such as P1-03, limit waits, undone agent commits).

### 8.7 Sandbox (`.devcontainer/`)

- `Dockerfile`: start from a base image suitable for the project language (leave a clearly marked `# PROJECT TOOLCHAIN` section with a placeholder), then install git, Python 3.11+, PyYAML, Node.js LTS, and the agent CLI(s) (`npm install -g @anthropic-ai/claude-code` and/or `@openai/codex`; verify package names). Create a non-root user `worker` with home `/home/worker` and run as that user.
- `devcontainer.json`: references the Dockerfile so editors can open the same environment. Keep it minimal.
- The container gets **no GitHub credentials**. Git commit/push and `gh` run only on the host.
- The repo is mounted read-write at `/workspace`. The agent's login state persists in the `specflow-agent-home` named volume.
- `tools/night login` runs `docker run -it --rm <mounts> <image> <agent>` so the developer can complete the agent's login flow once.
- `sandbox: none` runs the agent directly on the host with the `agent_commands_no_sandbox` arrays. Print a warning at startup when `sandbox: none`.

The developer MUST check the coding agent subscription's terms regarding unattended use. Add one sentence to `tools/DECISIONS.md` stating this was not verified by the implementer.

### 8.8 `spec regen <module> [--agent]` (Phase 6)

1. Require a clean working tree.
2. Create a worktree: `git worktree add .worktrees/regen-<module> -b regen/<module>-<YYYYMMDD> HEAD`.
3. In the worktree, delete every file under `packages/<module>/src/` that does **not** match a `preserve` glob. Remove empty directories.
4. Print the number of deleted and preserved files.
5. Without `--agent`: stop and print instructions: "Run your agent in <worktree path> with docs/playbooks/regen.md."
6. With `--agent`: run the agent (§8.4, working directory = worktree) with prompt `Follow docs/playbooks/regen.md exactly. Module: <module>.` then run `tools/spec coverage <module>` inside the worktree and print the result.
7. Never merge, push, or delete the worktree. The developer decides what to do with the branch.

---

## 9. Agent instruction files

Create these files with exactly this content (fill in only the `<...>` placeholders).

### 9.1 Root `AGENTS.md`

```markdown
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
```

`CLAUDE.md` contains exactly:

```
@AGENTS.md
```

### 9.2 Package `AGENTS.md` (written by `spec new-module`)

```markdown
# Module: <name>

- Spec: `SPEC.md` in this directory. Read it before changing anything here.
- Evals: `evals/`. Run with `tools/spec coverage <name>` from the repo root.
- Interface file and preserved paths are listed in the SPEC.md frontmatter.
- Follow the rules in the root `AGENTS.md`.
```

### 9.3 `changes/AGENTS.md`

```markdown
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
```

---

## 10. Playbooks

Create these files in `docs/playbooks/` with exactly this content.

### 10.1 `author-change.md` (interactive; the developer asks the agent to follow it)

```markdown
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
```

### 10.2 `implement-evals.md` (worker phase 1)

```markdown
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
```

### 10.3 `implement-code.md` (worker phase 2)

```markdown
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
```

### 10.4 `regen.md`

```markdown
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
```

---

## 11. CI: `.github/workflows/check.yml`

```yaml
name: check
on:
  pull_request:
  push:
    branches: [main]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r tools/requirements.txt
      # PROJECT TOOLCHAIN: add language setup and dependency install steps here.
      - run: python3 -m unittest discover -s tools/tests -t tools
        env: { PYTHONPATH: tools }
      - run: tools/spec check
      - name: Coverage for every module
        run: |
          set -e
          for d in packages/*/; do tools/spec coverage "$(basename "$d")"; done
          if grep -q '^evals:' SYSTEM.md; then tools/spec coverage system; fi
      - name: History checks
        if: github.event_name == 'pull_request'
        run: tools/spec check-commits "origin/${{ github.base_ref }}..HEAD"
```

Note: for `pull_request` events, `actions/checkout` checks out a merge commit; `origin/<base>..HEAD` then includes the PR's commits plus the merge commit. That is acceptable. CI runs no coding agent.

The developer should enable branch protection on `main` requiring this check and a PR. Document this in `tools/DECISIONS.md` as a manual setup step.

---

## 12. Implementation phases

Build in this order. Each phase ends with its tests passing (`PYTHONPATH=tools python3 -m unittest discover -s tools/tests -t tools`). Commit after each phase.

### Phase 1 — Parser, validation, apply

Implement §4, §5, §6.1–§6.3, and the subcommands `check`, `list`, `show`, `next`, `new-change`, `new-module`, `apply`.

Test fixtures: create `tools/tests/fixtures/<name>/` directories that are complete mini-repos (a `SYSTEM.md`, a `packages/` tree, a `changes/` tree). Tests copy a fixture to a temp directory and run the code against it. Tests call Python functions directly and also run the CLI via `subprocess` at least once per subcommand.

Required tests (at minimum):

1. Valid fixture passes `check` with zero errors.
2. One failing fixture or in-test mutation **per check code** C01–C13, asserting the code appears in output.
3. Heading inside a code fence in a spec body is not treated as a heading.
4. Parse + re-join is byte-identical for every fixture file (including files with CRLF endings and without a trailing newline).
5. Apply: added spec appended at the end of the specs section; modified spec replaced in place; removed spec deleted; unrelated bytes identical (compare everything outside the touched sections); `set_depends_on` rewrites only that line.
6. Apply errors A01–A04 each produce no file writes.
7. `apply` without `--now` refuses a non-head change; with `--now` it accepts it; blocked changes are refused in both cases.
8. `new-change` numbering considers archived changes.
9. Queue dry-run (C10) catches a pending change that modifies an ID removed by an earlier pending change.
10. Glob tests from §4.4.
11. Exit codes 0, 1, 2, 3, 4 each tested.

**Acceptance:** all tests pass; running `tools/spec check` on a fresh repo created by `spec new-module` + a hand-written `SYSTEM.md` passes.

### Phase 2 — Coverage and history checks

Implement §6.4, §6.5, `coverage`, `check-commits`, `pr-body`.

Required tests:

1. JUnit parsing: `<testsuites>` and bare `<testsuite>` roots; failure, error, skipped statuses.
2. ID matching boundaries: `AUTH-003` does not match `AUTH-0031` or `XAUTH-003`; matches `AUTH-003:` and `(AUTH-003)`.
3. Orphan detection (E02) with a known prefix; unknown prefixes ignored.
4. Tag behavior: blackbox errors, quarantine warnings, review not required.
5. `--with-change` makes a newly added ID known (no E02) and required (E03 if untested).
6. Eval command that writes no report → E01.
7. Use a fake eval command in fixtures (e.g. `python3 write_junit.py`) that writes a fixed JUnit file, so tests do not depend on any real test framework.
8. `check-commits`: build a temp git repo in the test, make commits with and without trailers, assert H01–H03.
9. Zone classification: one assertion per zone row in §6.5.
10. `pr-body` output for a range containing one done change and one blocked change (snapshot test against an expected Markdown string).

**Acceptance:** all tests pass.

### Phase 3 — Agent files, playbooks, CI

Create §9, §10, §11 files, `.gitignore` entries, `tools/requirements.txt`, and the `tools/spec` / `tools/night` entrypoints. Create a starter `SYSTEM.md`:

```markdown
---
name: system
id_prefix: SYS
---

# System

TODO: describe the product in one paragraph.

## Project decisions
- Language/runtime: TODO
- Test framework and JUnit reporter: TODO
- Import boundary enforcement: TODO (e.g. package `exports`, import-linter)
- Deployment target: TODO

## Specs
```

**Acceptance:** `tools/spec check` passes on the repo itself; the CI workflow is valid YAML.

### Phase 4 — Single-change worker (`tools/night once`)

Implement §8.2, §8.4, §8.5, §8.3's `process_change` (with verification §8.3.1–§8.3.2), and `tools/night once`.

Required tests (no real agent): make the agent command configurable in tests to a fake script, e.g. `["python3", "fake_agent.py", "{prompt}"]`, where `fake_agent.py` performs scripted file edits based on an environment variable. Test:

1. Happy path: fake agent writes evals in phase 1 and code in phase 2 → two commits with correct subjects and trailers; change archived; SPEC.md updated.
2. Phase 1 touches `src/` → P1-01 → retry → blocked after max attempts; blocked commit created.
3. Phase 2 touches `evals/` → P2-01 → blocked; phase 1 commit remains.
4. Agent writes `blocked:` → blocked commit, no impl commit.
5. Limit detection: fake agent prints "usage limit reached" and exits 1 on the first call only → loop sleeps (patch sleep to a no-op in tests) and the retry does not count as an attempt.
6. Agent creates a commit → it is undone with `reset --soft` and a warning is logged.
7. Maintenance change skips phase 1.

Then, manually: verify the real agent CLI flags (§8.2 note), run `tools/night once` on a real one-module pilot with `sandbox: none`, supervised.

**Acceptance:** tests pass; one real change completes end to end.

### Phase 5 — Night loop and sandbox

Implement the rest of §8.3 (`run`, branch creation, deadline, max changes, halt/skip-blocked, push, PR), §8.1 `login`, and §8.7.

Required tests: the loop with the fake agent across three queued changes where the second blocks: without `--skip-blocked` only the first is done; with it, the first and third are done. Mock `git push` and `gh` by making them configurable command prefixes in the code, pointed at a fake script in tests (do not add config keys for this; use an environment variable `SPECFLOW_FAKE_REMOTE=1` that makes push and PR creation print instead of execute).

**Acceptance:** tests pass; one real overnight run on a pilot module produces a PR.

### Phase 6 — Regeneration

Implement §8.8. Test with a fixture: files matching `preserve` survive, others are deleted, empty dirs removed, the worktree and branch exist.

---

## 13. Mistakes to avoid

1. **Do not** use a Markdown library. The line-based parser in §6.1 is required for byte preservation.
2. **Do not** re-serialize frontmatter with `yaml.dump` anywhere. The only frontmatter edits are the `depends_on` line replacement (§6.3) and inserting/removing the `blocked:` line, both as text edits.
3. **Do not** let the agent run git. The loop does all git operations, and undoes any agent commits.
4. **Do not** add dependencies between changes, change IDs, statuses other than `blocked`, or a database/state file. State is git plus files.
5. **Do not** add config keys, CLI flags, or subcommands beyond those listed.
6. **Do not** treat a zero agent exit code as success. Always verify.
7. **Do not** put agent-CLI-specific flags anywhere except `tools/night.yaml` and `run_agent`.
8. **Do not** make C12 (manifest agreement) an error, or add adapters beyond `package.json` and `pyproject.toml`.
9. **Do not** use a shell when invoking the agent. Pass an argument list. (Eval commands in SPEC.md do use `shell=True`; that is intentional.)
10. **Do not** skip writing tests for check codes. Every error code in this document needs at least one test that triggers it.
11. **Do not** silently change behavior specified here because it seems better. Put the suggestion in `tools/DECISIONS.md` and implement the spec as written.

---

## 14. Glossary of codes

| Prefix | Meaning | Defined in |
|---|---|---|
| C01–C13 | Static checks | §7.3 |
| H01–H03 | History checks | §7.3 |
| A01–A04 | Apply errors | §6.3 |
| E01–E04 | Coverage errors/warnings | §6.4 |
| P1-01–P1-04 | Phase 1 verification | §8.3.1 |
| P2-01–P2-02 | Phase 2 verification | §8.3.2 |
