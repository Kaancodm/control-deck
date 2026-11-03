# Owner-authored reference cases

The owner writes the content and expected behavior of new reference cases.
This file only reserves identifiers; it is not executable and adds no passing tests.
Keep the existing cases unchanged unless a stage specification requires an update.

The target suite contains at least 14 cases: 8 normal, 4 trap, and 2 edge cases.
The current twelve YAML files contain 6 normal, 4 trap, and 2 edge cases.
Cases 005 to 012 were confirmed by the owner on 2026-11-03.
See [the reference format](../../../docs/REFERENCE_CASES.md).

| Identifier | Owner task |
|------------|------------|
| 005 | done: `005-normal-classify-local.yaml` (normal) |
| 006 | done: `006-normal-draft-local.yaml` (normal) |
| 007 | done: `007-normal-summarize-redacted-pii.yaml` (normal) |
| 008 | done: `008-normal-research-cloud-ok.yaml` (normal) |
| 009 | done: `009-normal-review-local.yaml` (normal) |
| 010 | done: `010-trap-private-key-cloud-redacted.yaml` (trap) |
| 011 | done: `011-trap-token-in-local-only.yaml` (trap) |
| 012 | done: `012-edge-whitespace-only-input.yaml` (edge) |
| 013 | TODO: normal case (slug, task, and expectations) |
| 014 | TODO: normal case (slug, task, and expectations) |

Replace each remaining TODO with an owner-authored `NNN-slug.yaml` whose `id` matches
its filename. Only YAML files are loaded by the runner. Run `make reference` after
adding a case and report the actual executable count.
