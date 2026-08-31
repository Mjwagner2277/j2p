# j2p Quick Start

j2p reads a project-wide Jira CSV, calculates epic completion from child story/task points, and creates review outputs for Microsoft Project managers.

## Guardrail Summary

- The source-of-truth `.mpp` is not modified.
- `update` copies the source-of-truth `.mpp` to a timestamped sandbox and updates the sandbox only.
- Only epics become Project work rows.
- Stories, tasks, bugs, and sub-tasks are used only for epic percent-complete math.
- Epics without the required initiative or fixVersion rollup are excluded and reported.
- Epics with unmapped Jira key prefixes are excluded and reported.
- Standard Jira blocker links are mapped to Finish-to-Start predecessor links.
- Circular dependencies are skipped and reported.
- Manager review output is an HTML report plus CSV audit files.

## Install

On the Windows machine that will write `.mpp` files:

```powershell
py -3.14 -m pip install -e .[project]
```

For report-only validation on a non-Project machine:

```powershell
py -3.14 -m pip install -e .
```

## Validate Before Touching Project

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-update.csv `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```

Open the generated `Manager-Review-Report.html` first. Use `audit-detail.csv` when you need row-level evidence.

## Update A Sandbox From The Main Project

```powershell
py -3.14 -m j2p update `
  --jira-csv .\path\to\jira-export.csv `
  --main-project .\path\to\Program-Source-Of-Truth.mpp `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```

The output folder will contain a timestamped run folder:

```text
review-output\j2p-run-YYYYMMDD-HHMMSS\
  Program-Source-Of-Truth.sandbox.YYYYMMDD-HHMMSS.mpp
  Manager-Review-Report.html
  audit-detail.csv
  planned-epics.csv
  summary-rollups.csv
  dependency-review.csv
  FIELD_MAPPING.md
  j2p-state.after.json
```

The persistent state file is written to:

```text
review-output\j2p-state.json
```

## Compare Against A Previous Sandbox

Default comparison is against the fresh copy of the main Project file. For iterative review cycles, compare against a previous sandbox instead:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\path\to\jira-export.csv `
  --main-project .\path\to\Program-Source-Of-Truth.mpp `
  --previous-sandbox .\review-output\j2p-run-20260831-090000\Program-Source-Of-Truth.sandbox.20260831-090000.mpp `
  --comparison-source previous-sandbox `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```

## Rollup Modes

Initiative mode:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-update.csv `
  --config .\examples\config.example.yaml `
  --rollup-mode initiative `
  --output-dir .\review-output
```

fixVersion mode:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-fixversion.csv `
  --config .\examples\config.fixversion.example.yaml `
  --rollup-mode fixVersion `
  --output-dir .\review-output
```
