# Large 1,200-Line Walkthrough Scenario

This folder is the full training scenario for j2p. It gives reviewers a project-wide Jira export that feels closer to a real portfolio.

Rows 2-35 start the client walkthrough. Rows 17-35 are the authored epic examples that map directly to the report sections below, and rows 201-202 are authored child-row data-quality examples. The remaining rows are named scale data; they exist to prove the tool can handle a larger project-wide export, but they are not where the training story lives.

At least 60% of included schedule-driving epic rows have valid predecessors. This is intentional so the Microsoft Project sandbox visibly exercises dependency population at portfolio scale, not just the handful of authored teaching rows.

## Files

| File | Purpose |
| --- | --- |
| `config.large-example.yaml` | Maps each Jira key prefix to a resource group and rollup type |
| `project-wide-jira-baseline-1200.csv` | Baseline Jira export with exactly 1,200 lines |
| `project-wide-jira-updated-1200.csv` | Updated Jira export with exactly 1,200 lines |
| `expected-review-cases.csv` | Checklist of Jira keys that demonstrate each color/review behavior |
| `report-example\j2p-run-updated-1200\Manager-Review-Report.html` | Generated manager report for the updated CSV |
| `report-example\j2p-run-updated-1200\by-project-key\index.csv` | Index of the per-project-key CSV outputs |

No `.mpp` files are committed in this example folder. The committed `report-example` folders were created with `validate`, so they contain report files only. To create Project files you can open in Microsoft Project, use Step 5 on a Windows machine with Microsoft Project installed.

The CSVs are authored fixtures maintained by a generator so line counts and scale rows stay consistent. They are not random. Rebuild them only when changing the training scenario:

```powershell
py -3.14 .\scripts\generate_large_examples.py
```

Verify they have not drifted from the committed expected files:

```powershell
py -3.14 .\scripts\generate_large_examples.py --check
```

The smoke test also validates that predecessor coverage remains at or above 60% of driving epic rows.

## Step 1: Confirm The CSV Size

From the repository root:

```powershell
(Get-Content .\examples\large-scenario\project-wide-jira-baseline-1200.csv).Count
(Get-Content .\examples\large-scenario\project-wide-jira-updated-1200.csv).Count
```

Expected result:

```text
1200
1200
```

Then open the updated CSV and look at rows 17-35, then rows 201-202. Those rows are the complete teaching path.

## Step 2: Review The Prefix Rollup Mapping

Open:

```text
examples\large-scenario\config.large-example.yaml
```

The important section is:

```yaml
rollup_modes:
  CORE: initiative
  WEB: initiative
  DATA: initiative
  PLAT: fixVersion
  OPS: fixVersion

multi_fixversion_policy:
  default: reference
  OPS: split
```

That means:

| Jira Key Prefix | Resource Group | Rollup Type |
| --- | --- | --- |
| `CORE` | Core Product Engineering | Initiative parent |
| `WEB` | Web Experience | Initiative parent |
| `DATA` | Data Engineering | Initiative parent |
| `PLAT` | Platform Engineering | fixVersion with default reference handling |
| `OPS` | Operations | fixVersion with split handling |

The `UNK` prefix is intentionally not configured. It appears in the report as an unknown-prefix review case.

## Step 3: Run The Baseline Export

This report-only command writes the baseline state file used for comparison. It does not create a Microsoft Project file.

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\large-scenario\project-wide-jira-baseline-1200.csv `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\examples\large-scenario\report-example `
  --run-id baseline-1200 `
  --write-state
```

Expected output folder:

```text
examples\large-scenario\report-example\j2p-run-baseline-1200\
```

## Step 4: Run The Updated Export Against The Baseline

This report-only command writes the updated manager report and CSV audit files. It does not create a Microsoft Project file.

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\large-scenario\project-wide-jira-updated-1200.csv `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\examples\large-scenario\report-example `
  --run-id updated-1200 `
  --compare-state
```

Open the generated manager report:

```text
examples\large-scenario\report-example\j2p-run-updated-1200\Manager-Review-Report.html
```

## Step 5: Create Project Files For Windows Review

Use this step when training users need to open `.mpp` files in Microsoft Project.

Important command distinction:

| Command | Creates An `.mpp`? | Use In This Scenario |
| --- | --- | --- |
| `validate` | No | Create report-only examples and check Jira/config quality. |
| `create` | Yes | Create a baseline training `.mpp` from the baseline CSV. |
| `update` | Yes | Copy the baseline `.mpp` to a sandbox and apply the updated CSV. |

Prerequisites on the Windows machine:

- Python 3.14.2
- Microsoft Project desktop
- j2p installed with Project automation support

Install from the repository root:

```powershell
py -3.14 -m pip install -e ".[project]"
```

### Create The Baseline Training Project

This command creates a baseline `.mpp` from the baseline Jira CSV. For this walkthrough, treat the created file as the training source-of-truth Project file.

```powershell
py -3.14 -m j2p create `
  --jira-csv .\examples\large-scenario\project-wide-jira-baseline-1200.csv `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output\large-scenario-project `
  --run-id baseline-project `
  --output-project-name Large-Scenario-Baseline-Source.mpp
```

Expected baseline Project file:

```text
review-output\large-scenario-project\j2p-run-baseline-project\Large-Scenario-Baseline-Source.mpp
```

Open this file if you want to see the baseline schedule before the update. Do not edit it during the walkthrough; the next command copies it and updates the sandbox copy.

### Update A Sandbox From The Baseline Project

This command copies the baseline `.mpp` to a timestamped sandbox and applies the updated Jira CSV.

```powershell
py -3.14 -m j2p update `
  --jira-csv .\examples\large-scenario\project-wide-jira-updated-1200.csv `
  --main-project .\review-output\large-scenario-project\j2p-run-baseline-project\Large-Scenario-Baseline-Source.mpp `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output\large-scenario-project `
  --run-id updated-project-review
```

j2p prints timestamped progress messages in the terminal during the run. Use `--debug-visible` only when troubleshooting with a technical reviewer and you intentionally want Microsoft Project to show its window while automation is running.

Expected sandbox Project file:

```text
review-output\large-scenario-project\j2p-run-updated-project-review\Large-Scenario-Baseline-Source.sandbox.updated-project-review.mpp
```

Expected manager report for the Project update:

```text
review-output\large-scenario-project\j2p-run-updated-project-review\Manager-Review-Report.html
```

Open the sandbox `.mpp`, not the baseline source file, for review.

### Review The Sandbox In Microsoft Project

In Microsoft Project:

1. Open `Large-Scenario-Baseline-Source.sandbox.updated-project-review.mpp`.
2. Use the Gantt Chart view.
3. Apply the `j2p Review` task table from Project's table menu if it is not already active.
4. Start with the default manager-facing columns: `Jira Key`, `Name`, `Resource Group`, `Dependency Review`, `Jira Status`, `Start`, `Finish`, `% Complete`, and `Predecessors`.
5. Look for colored cells in the left task grid. The colors are not shown on the right-side Gantt bars.
6. Use `Manager-Review-Report.html` beside the `.mpp` and search/filter by Jira key.
7. Review red finish-date cells first, then green changed cells, amber review cells, blue dependency cells, and the in-planning entries in the manager report.

The default `j2p Review` table intentionally hides rollup categories, j2p row role, Jira fixVersion, internal mapping keys, Jira target dates, story point fields, hours fields, in-planning fields, and flag-style review indicators. Those values are still available in `Manager-Review-Report.html`, the CSV files, and the hidden Project custom fields. Edit `review_table.exposed_columns` in YAML only when your review process needs those fields visible in Project.

Training keys to find in the sandbox:

| Jira Key | What To Review In The `.mpp` |
| --- | --- |
| `CORE-1000` | Name changed from the baseline; changed cell should be green. |
| `CORE-1001` | Percent/date changes from child story updates. |
| `CORE-1004` | Intended red finish-date driver candidate after Project auto-scheduling. |
| `CORE-1005` | Downstream schedule item from the `CORE-1004` dependency path. |
| `CORE-1006` | Missing dependency target in the Dependency Review field. |
| `CORE-1007` | Valid dependency change. |
| `CORE-1980` | New epic added to the sandbox. |
| `WEB-2010` | In-planning epic with no pointed child work. |
| `PLAT-4028` | Default reference multi-fixVersion behavior. Confirm primary and reference rows. |
| `OPS-5019` | Split multi-fixVersion behavior. Confirm both rows drive schedule. |

For `PLAT-4028`, the sandbox should show two rows with the same `Jira Key`:

| Row | Expected Values |
| --- | --- |
| Primary row | `j2p Row Role` = `Primary`, `Drives Schedule` = `Yes`, `j2p Unique Key` = `PLAT-4028` |
| Reference row | `j2p Row Role` = `Reference`, `Drives Schedule` = `No`, `Primary Schedule Key` = `PLAT-4028` |

For `OPS-5019`, the sandbox should show split rows where `Drives Schedule` is `Yes`.

### If You Do Not See A Project File

Use this quick check:

| What Happened | Likely Cause | Fix |
| --- | --- | --- |
| You see HTML/CSV reports but no `.mpp`. | You ran `validate`. | Run `create` or `update`. |
| `update` failed before writing a sandbox. | `--main-project` did not point to an existing `.mpp`. | Create the baseline `.mpp` first or point to a real source-of-truth `.mpp`. |
| Microsoft Project did not open. | The machine is missing Microsoft Project desktop or `pywin32`. | Install with `py -3.14 -m pip install -e ".[project]"` and run on Windows with Project installed. |
| Microsoft Project shows a Font dialog while j2p is running. | Project may have a stale modal dialog from an older run, or an older j2p build that still used a dialog-prone formatting fallback may be installed. | Update to the latest j2p, close the Font dialog, close Project, use Task Manager to end any `WINPROJ.EXE` process if needed, then rerun. |
| The terminal gets past predecessor progress and then appears quiet for several minutes. | Project is likely recalculating, preparing the review table, coloring changed cells, or saving the sandbox. `--debug-visible` can make this slower. | Update to the latest j2p so those phases print their own progress messages. If it stops on cell coloring, rerun without `--debug-visible` for a faster normal run. |
| The sandbox opens without highlighted cells. | The active Project table may not be `j2p Review`, the table may be stale, or Project rejected cell formatting during the run. | Pull the latest j2p and rerun so `j2p Review` is rebuilt. Then apply the `j2p Review` table in Project and check the manager report for `ProjectReviewTableSetupFailed` or `ProjectCellColoringFailed`. Current j2p builds try exact RGB coloring first and then Project's direct `CellColor` palette fallback automatically. |
| The terminal says predecessor links were planned, but the Project `Predecessors` column is blank. | Microsoft Project rejected or failed to retain the fast dependency-write path. | Open `audit-detail.csv` and search for `ProjectDependencyWriteFailed`; rerun once with `--dependency-write-mode diagnostic` if you need the full Project API fallback trace. |
| The report shows a predecessor like `CORE-1001`, but Project shows `12FS`. | This is expected. Project stores predecessors as task row IDs with link types, not Jira keys. | Use the Jira Key / j2p Unique Key columns beside the predecessor column to confirm which epic row `12FS` refers to. |
| You opened the source file and do not see updates. | j2p updated the sandbox copy, not the source file. | Open the `.sandbox.updated-project-review.mpp` file in the update run folder. |

## Step 6: Walk The Manager Report

Start at `Decision Briefing`.

Confirm these high-level expectations:

| Metric | What It Means |
| --- | --- |
| Needs Review | Warnings and review decisions that need manager attention |
| Rollups In Progress | Initiative and fixVersion groups that are underway |
| Dependency Items | Changed, missing, skipped, or circular dependency cases |
| Completed Epics | Epics that became done since the comparison baseline |
| Logged Hours | Child story/task worklog hours rolled up to the scheduled epic rows |
| Story Point Ratio | Completed story points delivered per 8 completed logged hours, limited to active scheduled epic rows |

Then read sections in this order:

1. `Story Point Ratio`
2. Expand `Story Point Ratio By Resource Group` for the active-work split by team/resource group.
3. `Rollup Status`
4. `Reviewer Action Needed`
5. `Review Type Summary`
6. `Project Key Rollup Mapping`
7. Expand `Report Context` only when you want file paths, CSV row counts, and raw processing totals.
8. `Color Case Examples`
9. Expand `Detailed Review Sections` for `Changed Names`, `Added Epics`, `Multi-FixVersion Epics`, `Parent Or Rollup Moves`, `Completed Since Last Update`, `In Planning`, `Dependency Review`, `Date Review`, `Unmatched Project Tasks`, and `Excluded Items`.
10. Expand `Full Planned Epic Rows` only when you want to inspect every planned schedule row.
11. Expand `CSV Column Mapping Used` only when you want to confirm how the Jira CSV headers were interpreted.

The large planned-epic table is intentionally collapsed so managers see project-wide Story Point Ratio, rollup status, and required-review items before the full detail.

Logged hours appear in three places:

| Where | What To Look For |
| --- | --- |
| `Decision Briefing` | Total logged hours across driving scheduled epic rows. |
| `Story Point Ratio` | Completed story points delivered per 8 completed logged hours across active scheduled epic rows only. |
| `Story Point Ratio By Resource Group` | The same active-work Story Point Ratio split by resource group. |
| `Rollup Status` | Logged hours and Story Point Ratio grouped by initiative or fixVersion. |
| `Full Planned Epic Rows` and `planned-epics.csv` | Logged hours and `story_point_ratio` on each included epic row. |

In the authored rows, `CORE-1001` is the easiest logged-hours example. Its updated child stories have additional logged time, so the follow-on report shows progress movement, a `Logged Hours` changed-cell item, and a `Story Point Ratio` changed-cell item.

## Color And Review Cases In This Scenario

The same checklist is also available as:

```text
examples\large-scenario\expected-review-cases.csv
```

| Color | Case | Example Jira Key | What The Reviewer Should Learn |
| --- | --- | --- | --- |
| Green | Changed Jira value | updated row 17, `CORE-1000` | The Jira summary changed, so the sandbox task name is updated and logged |
| Green | Percent/date changes | updated row 18, `CORE-1001` | Child story completion changes percent complete; Jira target dates can also change |
| Green | Logged hours and Story Point Ratio changed | updated row 18, `CORE-1001` | Child story worklog hours roll up to the epic and changed logged-hour and Story Point Ratio cells are highlighted |
| Green | New epic | updated row 25, `CORE-1980` | A new valid epic is added to the sandbox and appears in `Added Epics` |
| Green | Dependency changed | updated row 24, `CORE-1007` | A new valid predecessor is written as a changed predecessor cell |
| Red | Cascade branch driver finish change | updated row 21, `CORE-1004` | This is the intended Project scheduling branch driver candidate; the actual red cell is only selected during `update` on Windows with Microsoft Project |
| Green | Downstream cascade item | updated row 22, `CORE-1005` | This is the intended downstream dependency item after `CORE-1004` |
| Yellow/amber | Unknown prefix | updated row 35, `UNK-9000` | The prefix is not in `resource_groups`, so the item is excluded and reported |
| Yellow/amber | Missing initiative parent | updated row 26, `CORE-1049` | `CORE` uses initiative rollup, so a missing parent excludes the epic |
| Yellow/amber | Missing fixVersion | updated row 33, `PLAT-4029` | fixVersion-mode teams still need at least one fixVersion |
| Report-only | Default reference multi-fixVersion | updated row 32, `PLAT-4028` | `PLAT` uses the default reference policy: one primary scheduled row plus one non-driving reference row |
| Report-only | Configured split multi-fixVersion | updated row 34, `OPS-5019` | `OPS` uses split policy: one driving schedule row per fixVersion |
| Yellow/amber | Baseline-only unmatched item | baseline row 25, `CORE-1048` | The item existed in the baseline but is not in the updated plan |
| Blue | Missing dependency target | updated row 23, `CORE-1006` | Jira references `EXT-999`, which is not an included epic |
| Blue | Self dependency | updated row 27, `WEB-2008` | An epic cannot block itself, so the dependency is skipped and reported |
| Blue | Circular dependency | updated rows 29-30, `DATA-3008` and `DATA-3009` | One dependency is skipped to prevent a schedule cycle |
| Gray/green-gray | In planning | updated row 28, `WEB-2010` | The epic is included but has no pointed child stories/tasks |

For `PLAT-4028`, the `planned-epics.csv` output has two rows with the same Jira key. The primary row has `Drives Schedule` set to `Yes`; the reference row has `Drives Schedule` set to `No` and points back to the primary schedule key. The multi-fixVersion audit item is informational; if the reference row is new relative to the baseline, its new Project cells are still colored green like any other added row.

For `OPS-5019`, both generated rows have `Drives Schedule` set to `Yes` because `OPS` is configured with `split`.

Data-quality rows that do not produce Project cell colors:

| Case | Example Row | What The Reviewer Should Learn |
| --- | --- | --- |
| Blank Jira key | updated row 201 | The CSV row is skipped and reported in `Reviewer Action Needed` |
| Orphan child story | updated row 202, `CORE-899999` | The story is not counted toward any epic because `Epic Link` is blank |

## Step 7: Review Per-Project-Key CSVs

Open:

```text
examples\large-scenario\report-example\j2p-run-updated-1200\by-project-key\index.csv
```

Each project key has its own folder:

```text
by-project-key\CORE\
by-project-key\WEB\
by-project-key\DATA\
by-project-key\PLAT\
by-project-key\OPS\
by-project-key\UNK\
by-project-key\UNASSIGNED\
```

Each folder contains:

| File | Use |
| --- | --- |
| `planned-epics.csv` | The included epics for that key prefix |
| `summary-rollups.csv` | The rollup summary rows for that key prefix |
| `audit-detail.csv` | All audit rows for that key prefix |
| `dependency-review.csv` | Only dependency-related audit rows for that key prefix |

`UNK` and `UNASSIGNED` are useful review folders. They do not represent included teams; they show data-quality issues that need cleanup.

## Step 8: Understand The Red Case

The red cascade-branch-driver color cannot be selected in `validate` mode because no Microsoft Project schedule engine is running. It is selected during a Windows `update` run, such as the sandbox update command in Step 5.

For training, use `CORE-1004` and `CORE-1005` as the schedule-change pair:

- `CORE-1004` has a target-end date change and blocks `CORE-1005`.
- `CORE-1005` is the downstream dependent epic.
- In a real `.mpp` update, j2p asks Microsoft Project to auto-schedule the sandbox, then reports every changed finish with changed downstream successors as red. Changed finish dates with no changed downstream successor remain green.

## Step 9: Manager Decisions

Use the report to make these decisions:

| Decision | Where To Look |
| --- | --- |
| Should missing rollups be fixed in Jira? | `Reviewer Action Needed`, `Excluded Items` |
| Should unknown prefixes be added to config? | `Reviewer Action Needed`, `by-project-key\UNK\audit-detail.csv` |
| Are changed names acceptable? | `Changed Names` |
| Are moved epics under the right rollup? | `Parent Or Rollup Moves` |
| Are completed epics safe to hide/inactivate in the sandbox? | `Completed Since Last Update` |
| Are dependency links valid? | `Dependency Review`, per-key `dependency-review.csv` |
| Should baseline-only tasks remain in the source schedule? | `Unmatched Project Tasks` |
