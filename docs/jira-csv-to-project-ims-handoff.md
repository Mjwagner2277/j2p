# Jira CSV to Microsoft Project IMS Handoff Guide

This package creates or updates a Microsoft Project schedule (`.mpp`) from a Jira CSV export.

By default, the script only processes Jira rows whose issue type is `Initiative` or `Epic`. Task-level rows such as `Story`, `Task`, `Sub-task`, and `Bug` are excluded from the IMS and listed in the sync report as `ExcludedIssueType`.

The script is designed for non-technical operators:

1. Validate the Jira CSV.
2. Review the generated preview CSV.
3. Create a new IMS or update an existing IMS.
4. Review the sync report CSV for items that were not found, added, skipped, or otherwise concerning.
5. Keep the generated log and sync report with the run record.

## Start Here

Most operators should start with the short guide:

```text
docs\Jira-to-IMS-Quick-Start.md
```

The short version is:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath "C:\Path\To\jira-export.csv" `
  -ValidateOnly `
  -OutputFolder "C:\Path\To\Jira-IMS-Run"
```

That command automatically creates:

- A preview CSV.
- A sync report CSV.
- A run log.

For a nontechnical handoff, operators can also copy and edit:

```text
examples\Run-Jira-To-IMS-Template.ps1
```

For a complete initial-import and follow-on-update scenario, use:

```text
examples\full-working-scenario\test.md
```

## Files

- `scripts/Sync-JiraCsvToProject.ps1`: Main script.
- `scripts/Test-Sync-JiraCsvToProject.ps1`: No-module test script for CSV parsing and percentage calculations.
- `docs/Jira-to-IMS-Quick-Start.md`: Short operator guide.
- `examples/jira-export-sample.csv`: Sample Jira export using default column names.
- `examples/jira-export-custom-columns-sample.csv`: Sample Jira export using alternate Jira custom field names.
- `examples/jira-export-concerning-items-sample.csv`: Sample Jira export with duplicate, blank-key, excluded task-level, and unusual story point rows.
- `examples/Run-Validation-Example.ps1`: Runs validation against the sample CSV.
- `examples/Run-Jira-To-IMS-Template.ps1`: Editable run template for operators.
- `examples/full-working-scenario/test.md`: Complete worked scenario with initial and follow-on Jira CSVs.
- `examples/full-working-scenario/Run-Scenario-Validation.ps1`: Validation runner for the full worked scenario.

## Requirements

- Windows.
- Microsoft Project desktop installed and licensed.
- PowerShell. Windows PowerShell 5.1 is preferred for Microsoft Project COM automation.
- A Jira CSV export with at least a Jira key column.

No extra PowerShell modules, Jira plugins, Project Online access, or Codex plugins are required.

## Scope and Limitations

- Create mode creates a flat Microsoft Project task list from the Jira CSV.
- Update mode matches existing Project tasks by `jira-key`.
- The script updates task `% Complete`, `Story Points`, and `Remaining Story Points`.
- By default, only `Initiative` and `Epic` Jira rows are processed.
- Task-level Jira rows are excluded and logged; they are not added to or updated in the IMS.
- The script does not currently map Jira hierarchy, dependencies, resources, start dates, finish dates, baselines, calendars, or critical path data.
- Summary tasks in Microsoft Project may reject direct `% Complete` updates. If that happens, the script logs a warning and continues.

## Jira CSV Columns

Most users do not need to set column mapping manually.

Column mapping means matching a Jira CSV header to a value the script needs. For example, the Jira key might be in a CSV column named `Issue key`.

The script looks for these columns automatically:

| Purpose | Default column names accepted |
| --- | --- |
| Jira key | `Issue key`, `Issue Key`, `Key`, `Jira key`, `Jira Key`, `jira-key` |
| Issue type | `Issue Type`, `Issue type`, `Type`, `Work Item Type`, `Work item type` |
| Task name | `Summary`, `Issue summary`, `Name`, `Title` |
| Total story points | `Story Points`, `Story points`, `Story point estimate`, `Custom field (Story Points)`, `Custom field (Story point estimate)` |
| Remaining story points | `Remaining Story Points`, `Remaining story points`, `Remaining Story points`, `Custom field (Remaining Story Points)`, `Custom field (Remaining story points)` |

If the Jira export uses these names, skip all column mapping options.

If validation says a column was not found, open the CSV, copy the exact header from the first row, and pass that one column name in the command:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\my-jira-export.csv `
  -ValidateOnly `
  -OutputFolder .\Jira-IMS-Run `
  -IssueTypeColumn "My Issue Type Column" `
  -StoryPointsColumn "My Total Points Column" `
  -RemainingStoryPointsColumn "My Remaining Points Column"
```

The issue type column is required unless `-IncludeAllIssueTypes` is used. This is intentional so task-level Jira rows are not accidentally imported.

## Initiative and Epic Filtering

Default included issue types:

```text
Initiative
Epic
```

To use a different set of higher-level Jira types:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\my-jira-export.csv `
  -ValidateOnly `
  -OutputFolder .\Jira-IMS-Run `
  -IncludedIssueTypes "Initiative","Epic","Feature" `
```

To intentionally process every Jira row in the CSV:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\my-jira-export.csv `
  -ValidateOnly `
  -OutputFolder .\Jira-IMS-Run `
  -IncludeAllIssueTypes `
```

Use `-IncludeAllIssueTypes` only when task-level rows are supposed to be included.

## Percent Complete Formula

For each Jira issue, the script calculates Microsoft Project `% Complete` as:

```text
(Story Points - Remaining Story Points) / Story Points
```

The result is written as a whole-number percentage from 0 to 100.

Examples:

| Story Points | Remaining Story Points | Percent Complete |
| ---: | ---: | ---: |
| 8 | 2 | 75 |
| 5 | 5 | 0 |
| 3 | 0 | 100 |

Rows with missing or unusual point data are flagged in the preview CSV.

## Logs and Sync Report

The easiest option is to use `-OutputFolder`. It automatically creates the preview CSV, sync report CSV, and run log in one folder:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\my-jira-export.csv `
  -ValidateOnly `
  -OutputFolder .\Jira-IMS-Run
```

Use both logging options on real runs:

- `-LogPath`: Creates a transcript log of the run.
- `-SyncReportCsvPath`: Creates a filterable CSV audit report.

If `-SyncReportCsvPath` is not provided during a real create or update run, the script creates a default report beside the output schedule:

```text
my-new-ims.sync-report.csv
current-ims.updated.sync-report.csv
```

The sync report includes these columns:

| Column | Meaning |
| --- | --- |
| `Action` | What happened to the row or task |
| `Severity` | `Info`, `Warning`, or `Error` |
| `JiraKey` | Jira issue key, when available |
| `IssueType` | Jira issue type, when available |
| `JiraSummary` | Jira summary or Project task name |
| `StoryPoints` | Total story points from Jira |
| `RemainingStoryPoints` | Remaining story points from Jira |
| `PercentComplete` | Calculated Microsoft Project `% Complete` |
| `ValidationStatus` | Story point validation result |
| `ProjectTaskId` | Microsoft Project task ID, when available |
| `ProjectTaskUniqueId` | Microsoft Project task unique ID, when available |
| `ProjectTaskName` | Microsoft Project task name, when available |
| `Detail` | Plain-English explanation |

Filter `Severity` to `Warning` or `Error` after every run.

Concerning `Action` values include:

| Action | Meaning |
| --- | --- |
| `MissingInProject` | Jira issue was in the CSV but no IMS task had that `jira-key`. |
| `AddedMissingTask` | Jira issue was missing in the IMS and was added because `-AddMissingInitiativesAndEpics` was used. |
| `ProjectTaskNotInJiraCsv` | IMS task has a `jira-key`, but that key was not present in the Jira CSV. |
| `DuplicateProjectJiraKey` | More than one IMS task has the same `jira-key`; only the first was eligible for update. |
| `DuplicateCsvJiraKey` | More than one Jira CSV row has the same key; only the first row was used. |
| `CsvRowMissingJiraKey` | A Jira CSV row had no key and was skipped. |
| `ExcludedIssueType` | A Jira CSV row was excluded because it was not an included issue type. |
| `MatchedNoCalculatedPercent` | Jira issue matched an IMS task, but `% Complete` was not updated because point data was missing or invalid. |
| `PercentUpdateFailed` | Microsoft Project rejected the `% Complete` update. |

## Microsoft Project Field Mapping

The script uses task custom fields:

| Microsoft Project Field | Renamed To | Purpose |
| --- | --- | --- |
| `Text30` | `jira-key` | Stores the Jira issue key used for matching |
| `Number1` | `Story Points` | Stores total story points |
| `Number2` | `Remaining Story Points` | Stores remaining story points |

When updating an existing IMS, each Microsoft Project task must already have its Jira issue key stored in the `jira-key` field. If the script created the IMS originally, that field is already populated.

## First-Time Validation

Run this before creating or updating an IMS:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\examples\jira-export-sample.csv `
  -ValidateOnly `
  -OutputFolder .\examples\validation-output
```

Expected result:

- A preview CSV is created.
- A sync report CSV is created.
- A log file is created.
- No Microsoft Project file is created or changed.

Open the preview CSV and review:

- `JiraKey`
- `IssueType`
- `StoryPoints`
- `RemainingStoryPoints`
- `PercentComplete`
- `ValidationStatus`

Only rows with `ValidationStatus` set to `OK` have clean story point data.

Open the sync report CSV and filter `Severity` to `Warning` or `Error`.

## Create a New IMS

Use this when you do not already have a Microsoft Project schedule:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\my-jira-export.csv `
  -OutputFolder .\Jira-IMS-Run
```

The script creates one Microsoft Project task per Jira issue.

## Update an Existing IMS

Use this when you already have a Microsoft Project schedule with `jira-key` populated:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\my-jira-export.csv `
  -ProjectPath .\current-ims.mpp `
  -OutputFolder .\Jira-IMS-Run
```

By default, this does not overwrite the original IMS. With `-OutputFolder`, it saves an updated copy in that folder:

```text
Jira-IMS-Run\current-ims.updated.mpp
```

Use `-InPlace` only when you intentionally want to update the original file:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\my-jira-export.csv `
  -ProjectPath .\current-ims.mpp `
  -InPlace `
  -OutputFolder .\Jira-IMS-Run
```

## Add Missing Jira Issues During Update

By default, update mode only updates existing Microsoft Project rows already found in the IMS by `jira-key`.

To append included Jira Initiatives/Epics that are in the CSV but not in the IMS:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\my-jira-export.csv `
  -ProjectPath .\current-ims.mpp `
  -AddMissingInitiativesAndEpics `
  -OutputFolder .\Jira-IMS-Run
```

## Dry Run Without Modifying Project

`-WhatIf` checks what the script would do after CSV validation, without opening Microsoft Project:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\my-jira-export.csv `
  -ProjectPath .\current-ims.mpp `
  -WhatIf `
  -OutputFolder .\Jira-IMS-Run
```

## Run the No-Module Tests

These tests do not require Microsoft Project. They validate script syntax, CSV parsing, alternate column names, preview generation, and percent calculations.

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Test-Sync-JiraCsvToProject.ps1
```

Expected final line:

```text
All non-COM tests passed.
```

## Windows Acceptance Test With Microsoft Project

Before handing this to production users, run this once on a Windows machine with Microsoft Project installed:

1. Close Microsoft Project.
2. Run validation mode against `examples\jira-export-sample.csv`.
3. Create a sample IMS:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\examples\jira-export-sample.csv `
  -OutputPath .\examples\sample-created-ims.mpp `
  -PreviewCsvPath .\examples\sample-created-ims.preview.csv `
  -SyncReportCsvPath .\examples\sample-created-ims.sync-report.csv `
  -LogPath .\examples\sample-created-ims.log `
  -Force
```

4. Open `examples\sample-created-ims.mpp` in Microsoft Project.
5. Confirm tasks were created.
6. Confirm the custom field `jira-key` is populated with Jira issue keys.
7. Confirm `% Complete` matches the preview CSV.
8. Run update mode against the created IMS:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\examples\jira-export-sample.csv `
  -ProjectPath .\examples\sample-created-ims.mpp `
  -PreviewCsvPath .\examples\sample-updated-ims.preview.csv `
  -SyncReportCsvPath .\examples\sample-updated-ims.sync-report.csv `
  -LogPath .\examples\sample-updated-ims.log `
  -Force
```

9. Confirm `examples\sample-created-ims.updated.mpp` was created.
10. Open the sync report CSV and filter `Severity` to `Warning` or `Error`.

## Common Troubleshooting

| Message | What it means | What to do |
| --- | --- | --- |
| `Could not find the Jira key column` | The Jira export does not have a recognized key column. | Re-export Jira with the issue key, or pass `-JiraKeyColumn "Your Column Name"`. |
| `Updating an existing IMS requires both total story points and remaining story points columns` | Update mode needs both values to calculate `% Complete`. | Add those fields to the Jira export or pass the correct column names. |
| `Output file already exists` | The script is protecting an existing file. | Use a different `-OutputPath` or add `-Force`. |
| `Microsoft Project automation requires Windows` | The script was run on macOS or Linux. | Run on Windows with Microsoft Project desktop installed, or use `-ValidateOnly` for CSV checks. |
| `Could not start Microsoft Project` | Microsoft Project is not installed, not licensed, or COM automation is unavailable. | Open Microsoft Project once manually, confirm it works, then rerun from Windows PowerShell. |

## Operator Checklist

Use this checklist for every real run:

1. Save the Jira CSV export locally.
2. Close Microsoft Project.
3. Run `-ValidateOnly` with `-PreviewCsvPath`.
4. Review the preview CSV.
5. Run create or update mode.
6. Open the generated `.mpp`.
7. Open the sync report CSV and filter `Severity` to `Warning` or `Error`.
8. Save the log file and sync report with the schedule package.

## Test Status

The no-Microsoft-Project path has been tested locally:

- PowerShell syntax parsing.
- Default Jira CSV column detection.
- Alternate Jira CSV column detection.
- Preview CSV generation.
- Sync report CSV generation.
- Log file generation.
- Percent calculations.
- Missing Jira key column failure.
- Required issue type filtering.
- Task-level issue type exclusion.
- Missing, zero, negative, and over-remaining story point validation.

The live `.mpp` create/update path requires Windows with Microsoft Project installed. Use the acceptance test above for final validation in that environment.
