# j2p

Jira CSV to Microsoft Project IMS synchronization tooling.

This repository contains a PowerShell script and handoff materials for creating or updating a Microsoft Project schedule from Jira CSV exports. It is intentionally limited to the Jira-to-Project workflow and does not contain any web app, Next.js, or React assets.

## Start Here

- Quick start: `docs/Jira-to-IMS-Quick-Start.md`
- Full handoff documentation: `docs/jira-csv-to-project-ims-handoff.md`
- Complete worked example: `examples/full-working-scenario/test.md`
- Main script: `scripts/Sync-JiraCsvToProject.ps1`
- Validation tests: `scripts/Test-Sync-JiraCsvToProject.ps1`

## Requirements

- PowerShell 5.1 or newer
- Windows with Microsoft Project installed for `.mpp` create/update operations
- PowerShell only for validation-only examples and CSV/report generation

## Typical Usage

Preview a Jira CSV import without opening Microsoft Project:

```powershell
pwsh -File .\scripts\Sync-JiraCsvToProject.ps1 `
  -JiraCsv .\examples\jira-export-sample.csv `
  -OutputFolder .\output `
  -PreviewOnly
```

Run the validation test suite:

```powershell
pwsh -File .\scripts\Test-Sync-JiraCsvToProject.ps1
```

