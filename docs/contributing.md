# Contributing To j2p

This document is for people changing the j2p code, fixtures, documentation, or generated examples.

Product users should start with:

- `docs/user-guide.md`
- `docs/configuration-reference.md`
- `examples/large-scenario/README.md`

## Repository Scope

j2p is intentionally a Python command-line tool for Jira CSV to Microsoft Project schedule review.

Keep the repository focused on that purpose:

- Keep Python package code under `j2p`.
- Keep tests under `tests`.
- Keep user examples under `examples`.
- Keep product and contributor documentation under `docs`.
- Do not add Next.js, React, web app scaffolding, node runtime dependencies, or unrelated UI projects.
- Do not reintroduce PowerShell implementation scripts as the main product path.

PowerShell snippets in documentation are fine when they show Windows users how to run the Python CLI.

## Code Areas

| Path | Purpose |
| --- | --- |
| `j2p/cli.py` | Command-line parsing and run orchestration. |
| `j2p/config.py` | YAML/default configuration loading and validation. |
| `j2p/core.py` | Jira CSV parsing, rollup logic, dependency logic, completion calculations, baseline comparison. |
| `j2p/project.py` | Microsoft Project COM automation for create/update/sandbox coloring. |
| `j2p/reports.py` | Manager HTML and CSV report generation. |
| `scripts/generate_large_examples.py` | Deterministic 1,200-line training fixture generator. |
| `scripts/smoke_tests.py` | Local pre-merge smoke test runner. |
| `tests/test_j2p.py` | Unit tests for core parsing, planning, reporting, and configuration behavior. |
| `tests/fixtures/` | Small private regression fixtures used by unit tests. These are not product walkthrough material. |

## Product Documentation Versus Contributor Documentation

Product documentation should answer:

- What files do users need?
- What command should they run?
- What does each report mean?
- What should a manager review?
- What YAML fields are safe to edit?
- What data-quality problems should be fixed in Jira?

Contributor documentation should answer:

- How is the code organized?
- What tests must pass?
- How are fixtures regenerated?
- What behavior must stay backward compatible?
- How should Microsoft Project automation changes be manually smoke tested?
- What does each Microsoft Project custom field enable?

When in doubt, keep user-facing workflow and configuration explanations in `docs/user-guide.md` or `docs/configuration-reference.md`, and keep implementation/test details here, in `docs/testing.md`, or in `docs/project-fields.md`.

## Development Setup

From the repository root:

```powershell
py -3.14 -m pip install -e ".[project]"
```

For report-only development on macOS or Linux:

```bash
python3 -m pip install -e .
```

Microsoft Project automation requires:

- Windows
- Microsoft Project desktop
- Python 3.14.2 in the target user environment
- `pywin32`, installed through the `project` extra

## Required Checks

Before merging or handing off a code change, run:

```powershell
py -3.14 .\scripts\smoke_tests.py
```

On macOS or Linux:

```bash
python3 scripts/smoke_tests.py
```

The smoke test runs unit tests, compilation, example validations, fixture checks, report-bundle checks, and repository hygiene checks.

See `docs/testing.md` for the full checklist.

## Microsoft Project Changes

Changes to `j2p/project.py` need additional care because cross-platform tests cannot open Microsoft Project.

Before release, run a Windows smoke test with a sanitized source-of-truth `.mpp`:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\examples\large-scenario\project-wide-jira-updated-1200.csv `
  --main-project .\path\to\sanitized-source-of-truth.mpp `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output
```

Manually confirm:

- the source-of-truth `.mpp` is unchanged
- the sandbox `.mpp` is timestamped
- custom fields are named correctly
- changed cells are green
- critical-path root finish-date cells are red when applicable
- dependency review cells are blue
- unmatched/excluded cells are amber
- in-planning cells are marked
- completed epics are inactive and Gantt bars are hidden when Project permits it
- report CSVs match visible sandbox changes

For details on why each Project custom field exists and which code paths depend on it, read `docs/project-fields.md`.

## Fixture Rules

The large scenario is intended for client walkthroughs, so it must stay deterministic and teachable.

Do not replace the authored walkthrough rows with random-only data.

Keep product examples under `examples/large-scenario`. Small focused CSVs/configs belong in `tests/fixtures` when they are needed for unit coverage.

The large CSVs must remain exactly 1,200 lines each:

- `examples/large-scenario/project-wide-jira-baseline-1200.csv`
- `examples/large-scenario/project-wide-jira-updated-1200.csv`

Regenerate deterministic fixtures:

```powershell
py -3.14 .\scripts\generate_large_examples.py
```

Check fixture line counts:

```powershell
py -3.14 .\scripts\generate_large_examples.py --check
```

When fixture behavior changes, update:

- `examples/large-scenario/README.md`
- `examples/large-scenario/expected-review-cases.csv`
- generated report examples under `examples/large-scenario/report-example`
- relevant smoke tests

## Multi-FixVersion Behavior

The product-supported policies are:

- `reference`
- `split`

`reference` is the default.

Do not reintroduce older modes such as excluding all multi-fixVersion epics by default. The legacy `behavior.multiple_fix_versions` key is intentionally rejected by configuration validation.

Any change to this behavior should update:

- `j2p/config.py`
- `j2p/core.py`
- `j2p/project.py`
- `j2p/reports.py`
- `docs/user-guide.md`
- `docs/configuration-reference.md`
- `docs/requirements.md`
- `examples/large-scenario/README.md`
- `tests/test_j2p.py`
- `scripts/smoke_tests.py`

## Commit Hygiene

Before committing:

```powershell
py -3.14 .\scripts\smoke_tests.py
git diff --check
git status --short
```

Use focused commits. Generated example report changes can be large, but they should correspond to intentional fixture or report behavior changes.

Do not revert unrelated user changes in the working tree.
