---
description: Read-only code review against REVIEW.md (security, data_class, test DB, migrations)
argument-hint: [files or diff context]
---

Perform a **read-only** review for: $ARGUMENTS

You may use Read, Grep, and Glob only. Do not edit files, run shell commands, or propose merging.

1. Read `REVIEW.md` and apply every section to the changed code (or the paths named in the arguments).
2. Cross-check `docs/DECISIONS/` ADRs when a finding touches architecture, data classes, or workers.
3. Report findings ordered by severity (bugs, regressions, security, missing tests first). For each finding: file and line, violated rule, smallest fix.
4. End with one line: `GO-ready: yes` or `GO-ready: no` plus blocking items.

Leave style and formatting to `ruff` and `mypy`.
