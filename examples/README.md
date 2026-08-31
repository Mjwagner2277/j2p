# Examples

Use these files to validate the j2p workflow before using a live Jira export.

| File | Purpose |
| --- | --- |
| `config.example.yaml` | Default initiative-mode YAML config |
| `config.fixversion.example.yaml` | Minimal fixVersion-mode YAML config |
| `project-wide-jira-initial.csv` | Initial project-wide Jira export |
| `project-wide-jira-update.csv` | Follow-on export with changed names, moved epics, new epics, exclusions, and dependency review cases |
| `project-wide-jira-fixversion.csv` | fixVersion-mode example |
| `test.md` | Complete hands-on scenario |

Run the initiative-mode validation example:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-update.csv `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```

Run the fixVersion-mode validation example:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-fixversion.csv `
  --config .\examples\config.fixversion.example.yaml `
  --output-dir .\review-output
```
