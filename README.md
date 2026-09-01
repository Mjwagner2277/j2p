# j2p

Jira CSV to Microsoft Project sandbox review tooling.

j2p is a Python CLI for creating manager-reviewable Microsoft Project sandbox schedules from project-wide Jira CSV exports. It rolls child story/task points and logged hours up to epic and summary rows, calculates hours accuracy against the configured hours-per-story-point rule, and produces HTML/CSV review packets.

The repository is intentionally limited to the Jira-to-Project workflow. It does not contain a web app, Next.js, React, or browser runtime.

## Start Here

For product users and schedule reviewers:

- Large project walkthrough: `examples/large-scenario/README.md`
- Product user guide: `docs/user-guide.md`
- YAML configuration reference: `docs/configuration-reference.md`

For contributors:

- Developer Project field reference: `docs/project-fields.md`
- Contributor guide: `docs/contributing.md`
- Testing and pre-merge checks: `docs/testing.md`
- Requirements and design decisions: `docs/requirements.md`

## Requirements

- Python 3.14.2 for the target user environment
- Windows with Microsoft Project desktop installed for `.mpp` create/update operations
- `pywin32` for Microsoft Project automation
- No React, Next.js, node runtime, browser app, or web server

Validation/report generation does not require Microsoft Project and can run on any machine with Python.

## Install

From the repository root:

```powershell
py -3.14 -m pip install -e ".[project]"
```

If the machine does not need to write `.mpp` files, install without the Project automation extra:

```powershell
py -3.14 -m pip install -e .
```

## Typical Usage

The supported end-user walkthrough uses the large project-wide example:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\large-scenario\project-wide-jira-updated-1200.csv `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output
```

Create a timestamped sandbox from a source-of-truth Project file and apply Jira updates:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\path\to\jira-export.csv `
  --main-project .\path\to\Program-Source-Of-Truth.mpp `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output
```

The main `.mpp` is never modified. The script copies it to a timestamped sandbox run folder, updates the sandbox, colors review cells, and writes a manager report plus audit CSVs.

For fixVersion teams, epics with multiple fixVersions default to `reference`: one driving primary row plus non-driving reference rows. The only alternate policy is `split`, configured per Jira key prefix when every fixVersion should receive a driving schedule row.

## Pre-Merge Checks

Run the local smoke test before merging or handing off changes:

```powershell
py -3.14 .\scripts\smoke_tests.py
```

This runs unit tests, syntax compilation, large-scenario validations, report-bundle checks, and repository hygiene checks. See `docs/testing.md` for the full checklist.
