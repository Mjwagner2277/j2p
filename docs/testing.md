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
  --project-name "Large Scenario Project" `
  --run-id local-check
```

## Reliability and scale checks

The unit suite includes separate failure-injection tests for run publication,
atomic state recovery, Project saves/dependencies/resources, strict input/config
validation, profiles/support bundles, and bounded cascade rendering. These are
included in the normal smoke command.

Selective-update tests check unchanged setters are avoided, changed values are
written, date/completion seeds preserve Project-calculated values when Jira input
is unchanged, and full verification remains active. Report tests check update
operation counts/timings remain run-wide in resource-group reports and audit
rows are retained. See [Windows acceptance](windows-acceptance.md) for live
identical-export and small-change runs; portable tests do not measure COM speed.

Run the deterministic 5k/10k benchmark separately using
`scripts/benchmark_large_exports.py`; see [performance.md](performance.md).

## Windows Microsoft Project Smoke Test

Run the repeatable [Windows acceptance harness](windows-acceptance.md) and retain its JSON results with the release. The cross-platform smoke test does not open Microsoft Project. Before a release that changes `j2p/project.py`, also run this on a Windows machine with Microsoft Project desktop and `pywin32` installed:

```powershell
py -3.14 -m pip install -e ".[project]"

py -3.14 -m j2p update `
  --jira-csv .\examples\large-scenario\project-wide-jira-updated-1200.csv `
  --main-project .\path\to\sanitized-source-of-truth.mpp `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output `
  --project-name "Large Scenario Project" `
  --sprint "Sprint 24.10"
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
- dependency review cells are light gray
- unmatched review cells are amber
- no Microsoft Project Font formatting dialog appears, including when `--debug-visible` is used
- autoscheduled Start/Finish changes are green, including cascade branch drivers; red driver cards appear only in the report diagram
- overall and resource-group reports contain only Cascading Schedule Drivers, Rollup and Completion, and Items for Review, in that order and all collapsed by default; their headers contain only title, generation time, and scope
- HTML omits context, legends, point-efficiency views, raw audits, and full planned rows; section links retain access to full CSV outputs
- `Cascading Schedule Drivers` is the single schedule-impact section; changed upstream finishes must link to downstream Start/Finish movement affecting unfinished dated work
- branches sort by unique affected unfinished dated issues, collapse by default, and show previous/current Finish and affected counts; expansion shows linked Start/Finish changes
- isolated changes, Jira-target mismatches alone, completed-only branches, and drivers missing both Jira target dates are excluded; all date evidence remains in audit CSVs
- resource-group reports include Cascading Schedule Drivers branches starting in that group and affected downstream issues from other groups
- completed driving epics remain active at native 100%, with configured Gantt-bar hiding; reference rows remain inactive and show progress in Story Point Completion %
- reference rows display `Reference` in the default row-role column; removing strike-through leaves native activation, numeric values, task names, and review backgrounds unchanged
- wrong-task selection or rejected formatting produces one cosmetic warning without formatting another row or reactivating references
- reference dates match final primary Start/Finish, including times, and all-reference/mixed fixVersion summary windows include every member after save/reopen
- `reports\html\Manager-Review-Report.html`, resource-group HTML reports, and audit CSVs match visible sandbox changes
- `reports\csv\by-project-key\<KEY>\*.csv` files are present for each Jira key prefix

## Actual project exports

[Project-specific verification](yerp-verification.md) documents the additional
12 tests that read all nine `yerp` exports with `ssn-812-config.yaml`. These check
independent source aggregation, overlapping inputs, a real child-task change,
and selective Project writes/report retention across the full plan using a
portable Project fake. They fingerprint the CSVs and never edit source exports.

```bash
python3 -m unittest discover -s tests -p 'test_yerp*.py'
```

These tests explicitly skip when project exports are absent. Passing them does
not replace live Windows/Project scheduling and save/reopen acceptance.
