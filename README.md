# j2p

Jira CSV to Microsoft Project sandbox review tooling.

This repository contains a Python CLI for creating manager-reviewable Microsoft Project sandbox schedules from project-wide Jira CSV exports. It is intentionally limited to the Jira-to-Project workflow and does not contain any web app, Next.js, or React assets.

## Start Here

- Quick start: `docs/quick-start.md`
- Requirements and guardrails: `docs/requirements.md`
- Manager review guide: `docs/manager-review-guide.md`
- Testing and pre-merge checks: `docs/testing.md`
- Complete worked example: `examples/test.md`
- Full manager report example: `examples/manager-report-example/j2p-run-follow-on-manager-review/Manager-Review-Report.html`
- Large 1,200-line walkthrough: `examples/large-scenario/README.md`
- Large manager report example: `examples/large-scenario/report-example/j2p-run-updated-1200/Manager-Review-Report.html`
- Example configuration: `examples/config.example.yaml`

## Requirements

- Python 3.14.2 for the target user environment
- Windows with Microsoft Project desktop installed for `.mpp` create/update operations
- `pywin32` for Microsoft Project automation
- No React, Next.js, node runtime, browser app, or web server

Validation/report generation does not require Microsoft Project and can run on any machine with Python.

## Install

From the repository root:

```powershell
py -3.14 -m pip install -e .[project]
```

If the machine does not need to write `.mpp` files, install without the Project automation extra:

```powershell
py -3.14 -m pip install -e .
```

## Typical Usage

Validate a Jira CSV and generate manager/audit reports without opening Microsoft Project:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-update.csv `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```

Create a timestamped sandbox from the source-of-truth Project file and apply Jira updates:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\path\to\jira-export.csv `
  --main-project .\path\to\Program-Source-Of-Truth.mpp `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```

The main `.mpp` is never modified. The script copies it to a timestamped sandbox run folder, updates the sandbox, colors review cells, and writes a manager report plus audit CSVs.

## Pre-Merge Checks

Run the local smoke test before merging or handing off changes:

```powershell
py -3.14 .\scripts\smoke_tests.py
```

This runs unit tests, syntax compilation, example validations, report-bundle checks, and repository hygiene checks. See `docs/testing.md` for the full checklist.
