# Jira to IMS Examples

Use these files to practice the Jira CSV to Microsoft Project IMS workflow.

By default, the script only processes Jira `Initiative` and `Epic` rows. Story, Task, Sub-task, Bug, and other task-level rows are excluded and listed in the sync report CSV.

## Files

- `jira-export-sample.csv`: Default Jira column names with Initiative, Epic, Story, and Task rows.
- `jira-export-custom-columns-sample.csv`: Alternate Jira custom field names with Initiative, Epic, and Story rows.
- `jira-export-concerning-items-sample.csv`: Duplicate, blank-key, excluded task-level, and unusual story point examples.
- `Run-Validation-Example.ps1`: Creates a preview CSV and log file from the default sample.
- `Run-Jira-To-IMS-Template.ps1`: Editable operator template for real runs.
- `full-working-scenario\test.md`: Complete initial import and follow-on update walkthrough.
- `full-working-scenario\Run-Scenario-Validation.ps1`: Runs the full scenario CSVs in validation mode.

## Quick Validation Example

From the repository root:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\examples\Run-Validation-Example.ps1
```

Or run the main script directly:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsvPath .\examples\jira-export-sample.csv `
  -ValidateOnly `
  -OutputFolder .\examples\validation-output
```

Expected output files:

- `examples\validation-output\jira-export-sample.preview.csv`
- `examples\validation-output\jira-export-sample.sync-report.csv`
- `examples\validation-output\jira-export-sample.run.log`

This example does not create or update a Microsoft Project file.

Open the sync report CSV and filter `Severity` to `Warning` or `Error` to review concerning rows.

For the full handoff guide, see `docs\jira-csv-to-project-ims-handoff.md`.

For an end-to-end example with an initial Jira CSV and a follow-on update CSV, see `examples\full-working-scenario\test.md`.
