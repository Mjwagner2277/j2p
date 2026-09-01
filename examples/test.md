# Full Working Scenario

This scenario validates the Python j2p workflow using the included sample Jira exports.

For a larger training scenario with 1,200-line baseline and updated Jira CSVs, use
`examples\large-scenario\README.md`.

## Prerequisites

For validation only:

- Python

For `.mpp` create/update:

- Windows
- Microsoft Project desktop
- `pywin32`

Install:

```powershell
py -3.14 -m pip install -e .[project]
```

## Step 1: Validate The Initial Export

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-initial.csv `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output `
  --run-id initial-demo
```

Expected:

- `TEAM-101`, `TEAM-102`, and `TEAM-103` use initiative rollup.
- `PLAT-201` uses fixVersion rollup because `PLAT` is configured that way.
- `PROD-100`, `PLAT-100`, and `Portal 2026` are rollup summary rows.
- Story/task rows are used for percent complete and are not Project work rows.

Per-project-key CSVs are written under:

```text
review-output\j2p-run-initial-demo\by-project-key\
```

Open:

```text
review-output\j2p-run-initial-demo\Manager-Review-Report.html
```

## Step 2: Validate A Follow-On Export Against State

Write state during the initial validation:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-initial.csv `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output `
  --run-id initial-state `
  --write-state
```

Then compare the follow-on export against that state:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-update.csv `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output `
  --run-id follow-on-demo `
  --compare-state
```

Expected manager-review cases:

| Case | Expected Item |
| --- | --- |
| Changed name | `TEAM-101` |
| Completed since last update | `TEAM-101` |
| Moved epic | `TEAM-103` moved from `PROD-100` to `PLAT-100` |
| New epic | `TEAM-104` |
| Missing rollup | `TEAM-105` excluded |
| Unknown prefix | `UNK-106` excluded |
| Missing dependency target | `TEAM-107` references `EXT-999` |

Open the split CSVs for a single Jira key prefix:

```text
review-output\j2p-run-follow-on-demo\by-project-key\TEAM\planned-epics.csv
review-output\j2p-run-follow-on-demo\by-project-key\PLAT\planned-epics.csv
```

## Step 3: Run fixVersion Mode

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-fixversion.csv `
  --config .\examples\config.fixversion.example.yaml `
  --output-dir .\review-output `
  --run-id fixversion-demo
```

Expected:

- `TEAM-501` and `TEAM-502` are included under fixVersion `Portal 2026`.
- `TEAM-503` has two fixVersions. Because `reference` is the default, the first fixVersion gets the scheduled primary row and the second fixVersion gets a non-driving reference row.
- The manager report includes a `Multi-FixVersion Epics` section that explains the primary/reference placement.

## Step 4: Update A Real Sandbox

Run this on Windows with Microsoft Project installed:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\path\to\jira-export.csv `
  --main-project .\path\to\Program-Source-Of-Truth.mpp `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```

Expected output:

```text
review-output\j2p-run-YYYYMMDD-HHMMSS\
  Program-Source-Of-Truth.sandbox.YYYYMMDD-HHMMSS.mpp
  Manager-Review-Report.html
  audit-detail.csv
  planned-epics.csv
  summary-rollups.csv
  dependency-review.csv
  FIELD_MAPPING.md
```

## Step 5: Iterative Review Against A Previous Sandbox

Use this only when reviewers are working iteratively before final promotion to the source-of-truth schedule.

```powershell
py -3.14 -m j2p update `
  --jira-csv .\path\to\jira-export.csv `
  --main-project .\path\to\Program-Source-Of-Truth.mpp `
  --comparison-source previous-sandbox `
  --previous-sandbox .\review-output\j2p-run-YYYYMMDD-HHMMSS\Program-Source-Of-Truth.sandbox.YYYYMMDD-HHMMSS.mpp `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```
