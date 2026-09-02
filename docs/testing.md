# Contributor Testing And Pre-Merge Checks

This page is contributor information for people changing j2p code, fixtures, or generated examples. Product users should start with `docs/user-guide.md`.

This project intentionally does not use GitHub Actions right now. Run the checks below locally before merging or handing off a change.

## Required Local Smoke Test

From the repository root:

```powershell
py -3.14 .\scripts\smoke_tests.py
```

On macOS or Linux:

```bash
python3 scripts/smoke_tests.py
```

The smoke test verifies:

- retired PowerShell files are not present
- Next.js/React/web app files are not present
- all unit tests pass
- all Python files compile
- large 1,200-line baseline/update CSV fixtures have not drifted
- at least 60% of large-scenario driving epic rows have predecessors
- large scenario reports expected manager-review categories and per-project-key CSVs
- manager HTML reports are self-contained

To keep generated smoke-test output for inspection:

```powershell
py -3.14 .\scripts\smoke_tests.py --keep-output
```

To write output to a specific folder:

```powershell
py -3.14 .\scripts\smoke_tests.py --output-dir .\review-output\smoke
```

## Individual Checks

Run unit tests:

```powershell
py -3.14 -m unittest discover -s tests
```

Compile Python files:

```powershell
py -3.14 -m compileall j2p tests scripts
```

Run a documented validation example:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\large-scenario\project-wide-jira-updated-1200.csv `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output `
  --run-id local-check
```

## Windows Microsoft Project Smoke Test

The cross-platform smoke test does not open Microsoft Project. Before a release that changes `j2p/project.py`, also run this on a Windows machine with Microsoft Project desktop and `pywin32` installed:

```powershell
py -3.14 -m pip install -e ".[project]"

py -3.14 -m j2p update `
  --jira-csv .\examples\large-scenario\project-wide-jira-updated-1200.csv `
  --main-project .\path\to\sanitized-source-of-truth.mpp `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output
```

Review the generated sandbox `.mpp` and confirm:

- source-of-truth `.mpp` was not modified
- sandbox `.mpp` was timestamped
- Jira Key and other custom fields were created/renamed
- the active task table is `j2p Review`, or that table is available from Project's table menu
- epics are under the correct initiative/fixVersion summary tasks
- multi-fixVersion epics have the expected `j2p Row Role`, `Drives Schedule`, and `Primary Schedule Key` values
- logged hours are visible in the `Logged Hours` custom number field
- Story Point Ratio is visible in the `Story Point Ratio` custom number field
- predecessor links match Jira blocker relationships; Project may display row IDs such as `12FS` instead of Jira keys
- changed cells are green
- dependency review cells are blue
- unmatched review cells are amber
- no Microsoft Project Font formatting dialog appears, including when `--debug-visible` is used
- cascade branch driver finish changes are red when applicable
- `Schedule Cascade Review` appears in the overall manager report and resource-group reports, shows red branch drivers with downstream changed finish dates, orders branches from most affected to least affected, and collapses every branch by default
- Resource-group HTML reports include schedule cascade branches that start with that resource group
- completed epics are inactive and have hidden Gantt bars when Project permits it
- `html-report\Manager-Review-Report.html`, resource-group HTML reports, and audit CSVs match visible sandbox changes
- `by-project-key\<KEY>\*.csv` files are present for each Jira key prefix
