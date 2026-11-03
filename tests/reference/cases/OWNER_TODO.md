# Owner-authored reference cases

The owner writes the content and expected behavior of new reference cases.
This file only reserves identifiers; it is not executable and adds no passing tests.
Keep the existing cases unchanged unless a stage specification requires an update.

The target suite contains at least 14 cases: 8 normal, 4 trap, and 2 edge cases.
The current four YAML files contain 1 normal, 2 trap, and 1 edge case.
See [the reference format](../../../docs/REFERENCE_CASES.md).

| Identifier | Owner task |
|------------|------------|
| 005 | TODO: choose category, slug, task, and expectations |
| 006 | TODO: choose category, slug, task, and expectations |
| 007 | TODO: choose category, slug, task, and expectations |
| 008 | TODO: choose category, slug, task, and expectations |
| 009 | TODO: choose category, slug, task, and expectations |
| 010 | TODO: choose category, slug, task, and expectations |
| 011 | TODO: choose category, slug, task, and expectations |
| 012 | TODO: choose category, slug, task, and expectations |
| 013 | TODO: choose category, slug, task, and expectations |
| 014 | TODO: choose category, slug, task, and expectations |

Replace each completed TODO with an owner-authored `NNN-slug.yaml` whose `id` matches
its filename. Only YAML files are loaded by the runner. Run `make reference` after
adding a case and report the actual executable count.
