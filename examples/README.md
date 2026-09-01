# Examples

Use these files to validate the j2p workflow before using a live Jira export.

From the repository root, product users should read `docs/user-guide.md` and `docs/configuration-reference.md` before adapting these examples to a real team export.

| File | Purpose |
| --- | --- |
| `config.example.yaml` | Mixed per-prefix YAML config: `TEAM` uses initiative, `PLAT` uses fixVersion |
| `config.fixversion.example.yaml` | Minimal fixVersion-mode YAML config |
| `project-wide-jira-initial.csv` | Initial project-wide Jira export |
| `project-wide-jira-update.csv` | Follow-on export with changed names, moved epics, new epics, exclusions, and dependency review cases |
| `project-wide-jira-fixversion.csv` | fixVersion-mode example |
| `test.md` | Complete hands-on scenario |
| `manager-report-example/` | Generated example of the full manager review report bundle |
| `large-scenario/` | 1,200-line baseline/update CSV scenario with manager walkthrough and generated report bundle |

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
