# Full Working Scenario: Jira CSV To Microsoft Project IMS

This scenario shows the complete workflow:

1. Validate an initial Jira CSV.
2. Create a new Microsoft Project IMS (`.mpp`) from that CSV.
3. Validate a follow-on Jira CSV.
4. Update the IMS from the follow-on CSV.
5. Review what was updated, added, excluded, or not found.

The script only includes Jira `Initiative` and `Epic` rows by default. Jira `Story`, `Task`, `Sub-task`, `Bug`, and other task-level rows are excluded from the IMS and listed in the sync report.

## Files In This Scenario

| File | Purpose |
| --- | --- |
| `jira-initial-import.csv` | First Jira export used to create the IMS |
| `jira-follow-on-update.csv` | Later Jira export used to update the IMS |
| `expected-initial-preview.csv` | Expected preview output for the first CSV |
| `expected-follow-on-preview.csv` | Expected preview output for the follow-on CSV |
| `Run-Scenario-Validation.ps1` | Runs both CSVs in validation mode |

## Prerequisites

Live `.mpp` creation and update requires:

- Windows.
- Microsoft Project desktop installed and licensed.
- Windows PowerShell.

Validation-only steps do not require Microsoft Project.

## Step 1: Open PowerShell

Open PowerShell in the folder that contains the `scripts`, `docs`, and `examples` folders.

Example:

```powershell
cd "C:\Path\To\This\Package"
```

## Step 2: Validate The Initial Jira CSV

Run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\examples\full-working-scenario\jira-initial-import.csv `
  -ValidateOnly `
  -OutputFolder .\examples\full-working-scenario\scenario-output\01-initial-validation
```

Expected output files:

| File | Purpose |
| --- | --- |
| `01-initial-validation\jira-initial-import.preview.csv` | Shows the rows that would go into the IMS |
| `01-initial-validation\jira-initial-import.sync-report.csv` | Shows included and excluded rows |
| `01-initial-validation\jira-initial-import.run.log` | Full run log |

Expected run summary:

| Item | Expected |
| --- | ---: |
| CSV rows read | 8 |
| Included rows sent to IMS | 5 |
| Excluded rows not sent to IMS | 3 |

Expected excluded issue types:

| Issue Type | Count |
| --- | ---: |
| Story | 1 |
| Task | 1 |
| Bug | 1 |

Expected preview rows:

| Jira Key | Issue Type | Story Points | Remaining Story Points | Percent Complete |
| --- | --- | ---: | ---: | ---: |
| INIT-100 | Initiative | 40 | 30 | 25 |
| EPIC-101 | Epic | 13 | 8 | 38 |
| EPIC-102 | Epic | 8 | 8 | 0 |
| EPIC-103 | Epic | 20 | 15 | 25 |
| EPIC-105 | Epic | 3 | 3 | 0 |

Open `expected-initial-preview.csv` to compare against the generated preview.

## Step 3: Create The Initial IMS

Run this on Windows with Microsoft Project installed:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\examples\full-working-scenario\jira-initial-import.csv `
  -OutputFolder .\examples\full-working-scenario\scenario-output\02-created-ims
```

Expected output files:

| File | Purpose |
| --- | --- |
| `02-created-ims\jira-initial-import.mpp` | New Microsoft Project IMS |
| `02-created-ims\jira-initial-import.preview.csv` | Preview rows used to create the IMS |
| `02-created-ims\jira-initial-import.sync-report.csv` | Audit report |
| `02-created-ims\jira-initial-import.run.log` | Full run log |

Open `jira-initial-import.mpp` in Microsoft Project.

Expected IMS tasks:

| Jira Key | Task Name | Percent Complete |
| --- | --- | ---: |
| INIT-100 | Customer Portal Modernization | 25 |
| EPIC-101 | Identity and access foundation | 38 |
| EPIC-102 | Reporting MVP | 0 |
| EPIC-103 | Billing integration | 25 |
| EPIC-105 | Legacy data cleanup | 0 |

The IMS should not contain `STORY-201`, `TASK-301`, or `BUG-401`.

To check the mapped Project fields:

1. In Microsoft Project, right-click a column heading.
2. Choose `Insert Column`.
3. Add `jira-key`.
4. Add `Story Points`.
5. Add `Remaining Story Points`.
6. Confirm the values match the preview CSV.

## Step 4: Validate The Follow-On Jira CSV

Run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\examples\full-working-scenario\jira-follow-on-update.csv `
  -ValidateOnly `
  -OutputFolder .\examples\full-working-scenario\scenario-output\03-follow-on-validation
```

Expected run summary:

| Item | Expected |
| --- | ---: |
| CSV rows read | 8 |
| Included rows sent to IMS | 5 |
| Excluded rows not sent to IMS | 3 |

Expected follow-on preview rows:

| Jira Key | Issue Type | Story Points | Remaining Story Points | Percent Complete |
| --- | --- | ---: | ---: | ---: |
| INIT-100 | Initiative | 40 | 20 | 50 |
| EPIC-101 | Epic | 13 | 0 | 100 |
| EPIC-102 | Epic | 8 | 4 | 50 |
| EPIC-103 | Epic | 20 | 10 | 50 |
| EPIC-104 | Epic | 5 | 5 | 0 |

Open `expected-follow-on-preview.csv` to compare against the generated preview.

Important scenario changes:

| Jira Key | Change |
| --- | --- |
| INIT-100 | Percent complete changes from 25 to 50 |
| EPIC-101 | Percent complete changes from 38 to 100 |
| EPIC-102 | Percent complete changes from 0 to 50 |
| EPIC-103 | Percent complete changes from 25 to 50 |
| EPIC-104 | New Epic appears in Jira |
| EPIC-105 | Was in the first IMS, but is missing from the follow-on Jira CSV |

## Step 5: Update The IMS Without Adding Missing Jira Items

This updates matching Initiative/Epic tasks only. It does not add new Jira items that are not already in the IMS.

Run this on Windows with Microsoft Project installed:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\examples\full-working-scenario\jira-follow-on-update.csv `
  -ProjectPath .\examples\full-working-scenario\scenario-output\02-created-ims\jira-initial-import.mpp `
  -OutputFolder .\examples\full-working-scenario\scenario-output\04-updated-without-adding
```

Expected output IMS:

```text
04-updated-without-adding\jira-initial-import.updated.mpp
```

Expected update behavior:

| Item | Expected |
| --- | ---: |
| Existing tasks updated | 4 |
| New tasks added | 0 |
| Jira items missing in IMS | 1 |
| IMS tasks not present in Jira CSV | 1 |

Expected sync report warnings:

| Action | Jira Key | Meaning |
| --- | --- | --- |
| `MissingInProject` | EPIC-104 | New Epic exists in Jira, but was not added because the add-missing option was not used |
| `ProjectTaskNotInJiraCsv` | EPIC-105 | Existing IMS task was not present in the follow-on Jira CSV |

Open the sync report CSV and filter `Severity` to `Warning` or `Error`.

## Step 6: Update The IMS And Add New Missing Epics

Use this when new Initiative/Epic rows from Jira should be appended to the IMS.

Run this on Windows with Microsoft Project installed:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\examples\full-working-scenario\jira-follow-on-update.csv `
  -ProjectPath .\examples\full-working-scenario\scenario-output\02-created-ims\jira-initial-import.mpp `
  -OutputFolder .\examples\full-working-scenario\scenario-output\05-updated-with-add-missing `
  -AddMissingInitiativesAndEpics
```

Expected output IMS:

```text
05-updated-with-add-missing\jira-initial-import.updated.mpp
```

Expected update behavior:

| Item | Expected |
| --- | ---: |
| Existing tasks updated | 4 |
| New tasks added | 1 |
| Jira items missing in IMS | 0 |
| IMS tasks not present in Jira CSV | 1 |

Expected sync report warnings:

| Action | Jira Key | Meaning |
| --- | --- | --- |
| `AddedMissingTask` | EPIC-104 | New Epic was appended to the IMS because `-AddMissingInitiativesAndEpics` was used |
| `ProjectTaskNotInJiraCsv` | EPIC-105 | Existing IMS task was not present in the follow-on Jira CSV |

Open `jira-initial-import.updated.mpp` in Microsoft Project.

Expected final IMS tasks:

| Jira Key | Task Name | Percent Complete |
| --- | --- | ---: |
| INIT-100 | Customer Portal Modernization | 50 |
| EPIC-101 | Identity and access foundation | 100 |
| EPIC-102 | Reporting MVP | 50 |
| EPIC-103 | Billing integration | 50 |
| EPIC-105 | Legacy data cleanup | 0 |
| EPIC-104 | Notification center | 0 |

The IMS should still not contain task-level rows such as `STORY-201`, `TASK-302`, or `BUG-402`.

## Step 7: Optional Dry Run

`-WhatIf` validates the CSV and writes preview/report/log files, but it does not open Microsoft Project. Because Project is not opened, `-WhatIf` does not prove which tasks match inside the `.mpp`.

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\examples\full-working-scenario\jira-follow-on-update.csv `
  -ProjectPath .\examples\full-working-scenario\scenario-output\02-created-ims\jira-initial-import.mpp `
  -WhatIf `
  -OutputFolder .\examples\full-working-scenario\scenario-output\dry-run
```

Use `-ValidateOnly` or `-WhatIf` to check CSV parsing and percentages. Use the live update steps to confirm `.mpp` matching.

## Step 8: Clean Up Test Output

After testing, it is safe to delete:

```text
examples\full-working-scenario\scenario-output
```

Do not delete the input CSV files or this `test.md` file from the package.
