# Jira to IMS Quick Start

Use this guide for the normal operator workflow.

For a full initial-import plus follow-on-update example, see:

```text
examples\full-working-scenario\test.md
```

## What The Script Includes

By default, the script includes only these Jira issue types:

```text
Initiative
Epic
```

It does not include Jira task-level rows such as:

```text
Story
Task
Sub-task
Bug
```

Excluded rows are not lost. They are listed in the sync report CSV as `ExcludedIssueType`.

## What Column Mapping Means

Column mapping means: "Which column in the Jira CSV contains each value?"

It does not mean Microsoft Project field setup.

The script normally finds these columns automatically:

| Value Needed | Jira CSV Column Header |
| --- | --- |
| Jira key | `Issue key` |
| Include/exclude decision | `Issue Type` |
| Task name | `Summary` |
| Total points | `Story Points` |
| Remaining points | `Remaining Story Points` |

If your Jira CSV has those headers, do not pass any column mapping options.

## Easiest Validation Command

Put your Jira CSV somewhere easy, then run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath "C:\Path\To\jira-export.csv" `
  -ValidateOnly `
  -OutputFolder "C:\Path\To\Jira-IMS-Run"
```

This creates three files in the output folder:

| File | Purpose |
| --- | --- |
| `*.preview.csv` | Shows Initiative/Epic rows and calculated `% Complete` |
| `*.sync-report.csv` | Shows included rows, excluded rows, skipped rows, and concerns |
| `*.run.log` | Full text log of the run |

Open the preview CSV first. It should only contain Initiatives and Epics.

Open the sync report CSV next. Filter `Action` to `ExcludedIssueType` to see Stories, Tasks, Bugs, and other rows that were intentionally excluded.

## Create A New IMS

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath "C:\Path\To\jira-export.csv" `
  -OutputFolder "C:\Path\To\Jira-IMS-Run"
```

The script creates:

- A new `.mpp` file
- A preview CSV
- A sync report CSV
- A run log

## Update An Existing IMS

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath "C:\Path\To\jira-export.csv" `
  -ProjectPath "C:\Path\To\current-ims.mpp" `
  -OutputFolder "C:\Path\To\Jira-IMS-Run"
```

The script creates an updated copy of the IMS in the output folder. It does not overwrite the original file unless `-InPlace` is used.

## If A Column Is Not Found

Open the Jira CSV in Excel and look at the first row. Copy the exact column header.

Example: if the issue type column is named `Work Item Type`:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath "C:\Path\To\jira-export.csv" `
  -ValidateOnly `
  -OutputFolder "C:\Path\To\Jira-IMS-Run" `
  -IssueTypeColumn "Work Item Type"
```

Example: if story points are named `Custom field (Story point estimate)`:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath "C:\Path\To\jira-export.csv" `
  -ValidateOnly `
  -OutputFolder "C:\Path\To\Jira-IMS-Run" `
  -StoryPointsColumn "Custom field (Story point estimate)"
```

Only use a column mapping option when validation says a column was not found or the preview shows the wrong values.

## If You Need To Include More Than Initiatives And Epics

Example: include `Feature` too:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath "C:\Path\To\jira-export.csv" `
  -ValidateOnly `
  -OutputFolder "C:\Path\To\Jira-IMS-Run" `
  -IncludedIssueTypes "Initiative","Epic","Feature"
```

Do not use `-IncludeAllIssueTypes` unless task-level Jira rows are supposed to be included.

## What To Check

In the preview CSV:

- Confirm only Initiative/Epic rows are present.
- Confirm `% Complete` looks right.
- Review rows where `ValidationStatus` is not `OK`.

In the sync report CSV:

- Filter `Severity` to `Warning` or `Error`.
- Filter `Action` to `ExcludedIssueType` to confirm task-level rows were intentionally excluded.
- Look for `MissingInProject`, `AddedMissingTask`, or `ProjectTaskNotInJiraCsv` after update runs.
