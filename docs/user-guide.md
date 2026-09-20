# j2p Product User Guide

This guide is for people who run j2p, review its outputs, or decide whether a Jira change should be accepted into a Microsoft Project schedule. It does not assume you are changing the code.

For contributor and testing information, use `docs/contributing.md` and `docs/testing.md`.

For the full project lifecycle from first setup through recurring sprint updates, use `docs/project-sprint-workflow.md`.

For Jira Data Center or Server exports that exceed the browser CSV limit, use `docs/jira-large-csv-export.md`.

## What j2p Does

j2p reads a project-wide Jira CSV export and prepares Microsoft Project review material.

The normal workflow is:

1. Export a Jira CSV that includes initiatives, epics, and child stories/tasks.
2. Run j2p in `validate` mode to generate reports without opening Microsoft Project.
3. Review the manager report for exclusions, data-quality concerns, dependencies, and changed values.
4. Run j2p in `update` mode against the source-of-truth `.mpp`.
5. j2p copies the source-of-truth `.mpp` to a timestamped sandbox and updates only that sandbox.
6. Review the sandbox `.mpp`, `reports\html\Manager-Review-Report.html`, resource-group HTML reports, and CSV audit files.
7. A schedule owner decides what should be manually accepted into the source-of-truth schedule.

j2p does not automatically promote sandbox changes back into the source-of-truth `.mpp`.

## Product Guardrails

The source-of-truth Project file is protected:

- The main `.mpp` is never edited directly.
- Every `update` run creates a timestamped sandbox copy.
- The sandbox is auto-scheduled.
- Changed Project cells are colored for review.
- Excluded and concerning Jira rows are reported.
- The manager report is self-contained HTML.
- CSV audit files are written for detailed inspection and per-project-key filtering.

Jira scope is intentionally limited:

- Epics become Project work rows.
- Initiatives can become Project summary rollup rows.
- fixVersions can become Project summary rollup rows.
- Stories, tasks, bugs, and sub-tasks are used only for percent-complete calculations.
- Task-level Jira issues are not added as Project work rows.

## Required User Inputs

At minimum, a product user needs:

| Input | Required For | Description |
| --- | --- | --- |
| Jira CSV export | `validate`, `create`, `update` | Project-wide Jira export containing at least epics and enough child rows to calculate completion. |
| YAML config | Recommended for all modes | Tells j2p how Jira prefixes, rollups, columns, statuses, and Project custom fields should map. |
| Main `.mpp` file | `update` | Source-of-truth Microsoft Project schedule. j2p copies this file before changing anything. |
| Output folder | All modes | Folder where j2p writes timestamped run output, reports, CSVs, and state. |
| Previous sandbox | Optional `update` comparison | Used when reviewers want to compare against an earlier sandbox instead of the main source-of-truth file. |

## Installation And Environment

For full Microsoft Project create/update use, run j2p on a Windows machine with:

- Python 3.14.2
- Microsoft Project desktop
- the j2p repository folder
- the Python package installed with the Project automation extra

Install from the repository root:

```powershell
py -3.14 -m pip install -e ".[project]"
```

For report-only validation on Windows without Microsoft Project:

```powershell
py -3.14 -m pip install -e .
```

On macOS or Linux, install with `python3 -m pip install -e .` and run commands
with `python3 -m j2p` instead of the Windows Python launcher.

See [run operations](run-operations.md) for saved profiles, read-only setup checks,
expected export counts, failure recovery, and support bundles.

Report-only validation can parse Jira CSVs and create HTML/CSV reports. It cannot create or update `.mpp` files because that requires Microsoft Project desktop automation.

## Commands

Run commands from the repository root.

Validate a CSV and write reports without opening Microsoft Project:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\large-scenario\project-wide-jira-updated-1200.csv `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program"
```

Create an initial `.mpp` from Jira. This is intended for first setup or demonstrations:

```powershell
py -3.14 -m j2p create `
  --jira-csv .\examples\large-scenario\project-wide-jira-baseline-1200.csv `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --output-project-name j2p-initial-sandbox.mpp
```

If Microsoft Project blocks blank-file creation on a workstation, create a blank `.mpp` manually once and use `update` with that file as `--main-project`. Normal production runs should use `update` against an existing source-of-truth `.mpp`.

Update a sandbox copy from the source-of-truth `.mpp`:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\path\to\jira-export.csv `
  --main-project .\path\to\Program-Source-Of-Truth.mpp `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.10"
```

Compare against a previous sandbox instead of the main file:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\path\to\jira-export.csv `
  --main-project .\path\to\Program-Source-Of-Truth.mpp `
  --previous-sandbox .\review-output\Customer-Portal-Program\sprints\Sprint-24.10\runs\j2p-run-20260901-090000\project\Program-Source-Of-Truth.sandbox.20260901-090000.mpp `
  --comparison-source previous-sandbox `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.10" `
  --allow-existing-sprint
```

## Command Reference

Common arguments (values may also come from `--profile`):

| Argument | Applies To | Required | Meaning |
| --- | --- | --- | --- |
| `--jira-csv` | `validate`, `create`, `update` | Yes | One or more CSV batch paths; may be repeated. |
| `--profile` | `validate`, `create`, `update` | No | Saved JSON project settings; explicit arguments override them. |
| `--expected-issues` | `validate`, `create`, `update` | No | Expected count of unique issue keys across all batches. |
| `--config` | `validate`, `create`, `update` | Recommended | Path to YAML configuration. If omitted, built-in defaults are used. |
| `--output-dir` | `validate`, `create`, `update` | No | Base folder for reports, state, and timestamped run folders. Default is `review-output`. |
| `--state-path` | `validate`, `create`, `update` | No | Custom path for persistent state JSON. Default is `<output-dir>\<Project>\j2p-state.json`. |
| `--run-id` | `validate`, `create`, `update` | No | Overrides timestamp naming. Must be new and contain no path separators; existing run IDs are never overwritten. |
| `--project-name` | `validate`, `create`, `update` | Yes | Program/project folder name that groups all resource groups and sprint runs. |
| `--sprint` | `validate`, `update` | Required for `update` | Sprint or planning increment value encoded into the output folder. |
| `--allow-existing-sprint` | `validate`, `update` | No | Allows a second run under an existing project/sprint folder. Without this, j2p stops when that sprint already exists. |

`validate` arguments:

| Argument | Required | Meaning |
| --- | --- | --- |
| `--compare-state` | No | Compare current Jira plan against the persistent state file if it exists. |
| `--write-state` | No | Write the persistent state file after validation. |

`create` arguments:

| Argument | Required | Meaning |
| --- | --- | --- |
| `--output-project-name` | No | Name of the initial `.mpp` created inside the run folder. |
| `--debug-visible` | No | Debug only. Ask Microsoft Project to show its window while automation runs. Normal users should omit this. |

`update` arguments:

| Argument | Required | Meaning |
| --- | --- | --- |
| `--main-project` | Yes | Source-of-truth `.mpp` copied into a timestamped sandbox. |
| `--comparison-source` | No | Baseline for change reporting. Allowed values are `main`, `previous-sandbox`, and `state`. Default is `main`. |
| `--previous-sandbox` | Required only with `--comparison-source previous-sandbox` | Prior sandbox `.mpp` used for iterative review comparison. |
| `--debug-visible` | No | Debug only. Ask Microsoft Project to show its window while automation runs. Normal users should omit this. |
| `--dependency-write-mode` | No | `fast` by default. Use `diagnostic` only when troubleshooting blank predecessor fields because it tries more Project APIs and can run much slower. |

During normal runs, j2p prints timestamped progress messages in the terminal so users can see that the run is still moving through CSV parsing, Project automation, state writing, and report generation.

Review formatting reports task scanning, key indexing, schedule-change review, undated-task duration checks, color-candidate collection, and visible-column resolution separately, with completed counts and elapsed time. Task scans and key indexing print every 50 rows, or after a completed row when 10 seconds have passed; candidate collection prints every 1,000 audit items or 10 seconds. Each distinct column is resolved once during preparation instead of repeatedly for every audit item. All color candidates retain their original order, including repeated colors for the same cell, and duplicate-key checks still run. Schedule-change review reuses the task index and reads only scheduled Start/Finish dates. The timing report lists indexing, schedule-change review, undated-task duration checks, candidate preparation, and visible-column resolution as subsets of Format review; do not add those subsets to the formatting total again.

During Project automation, the slowest phases are usually Project recalculation, review cell coloring, and saving the `.mpp`. `--debug-visible` makes these phases easier to observe but can also make them slower because Microsoft Project is actively repainting its window while COM commands run.

`Project save complete` means Project returned from saving; the run still needs
to verify the saved sandbox. j2p checks the epic fields (including percent complete),
dependencies and rollups, takes a snapshot, then closes/reopens the file and repeats
the checks. The terminal prints each stage and row counters during verification.
Task and resource indexes are reused within each verification pass so a large
schedule is not rescanned for every epic. Wait for `Saved sandbox verification
passed` and the final run completion/status before treating the run as complete.
If progress stops, retain the last stage and row/key shown for troubleshooting;
one blocked Project COM call can still prevent the next progress message.

For `create` and `update`, the terminal also prints the number of Project predecessor links planned from Jira. If that count is greater than zero but the sandbox `Predecessors` column is blank, open `audit-detail.csv` and search for `ProjectDependencyWriteFailed`. Rerun with `--dependency-write-mode diagnostic` only when you need the full Project API fallback trace.

j2p accepts common Jira CSV encodings, including UTF-8 with BOM, UTF-16, Windows-1252, and Latin-1. If CSV parsing still fails with an encoding error, re-export the Jira issue list as UTF-8 CSV from Jira or resave the file as UTF-8 CSV in Excel before rerunning.

## Selective Project updates

Updates still calculate the complete plan and compare every included epic and
rollup. With the default `--comparison-source main`, j2p reuses the live sandbox
snapshot taken before changes for its report baseline. Other comparison sources
continue to control report comparisons; writes are always checked against the
actual sandbox.

Within the update, j2p keeps managed Project values in memory and skips field
assignments that already match. It also skips unchanged dependency sets and
resource assignments. If the Jira target date window has not changed, it leaves
Project's scheduled dates alone. If either target date changes, it reapplies the
target window so Project can reschedule. Unchanged Jira completion does not
reseed Project's duration-based percentage; completed driving rows still require
native 100%, and custom story-point completion is verified exactly.

The CSV outputs retain complete comparisons, audit details, and point rollups;
the compact HTML presents schedule impact, completion, and grouped review work.
Recalculation, formatting, saving, and verification before and after reopening
still run, including checks for schedule effects on rows receiving no direct edit.

Open `run-manifest.json` to inspect Project operations written/skipped/failed
and timings by phase. These measurements describe the entire run. Counts measure
operations, not unique tasks or fields. See [performance.md](performance.md) for
how to compare repeated updates; runtime savings depend on the Project file and
Windows installation.

This optimization does not turn `j2p-state.json` into an issue cache. Continue
providing complete current exports for the planning scope. Overlapping rows with
identical parsed values are counted once; conflicting versions of the same Jira
key stop the run rather than choosing the newest file.

## Output Folder

Each run writes a timestamped run folder:

```text
review-output\Customer-Portal-Program\
  j2p-state.json
  sprints\
    Sprint-24.10\
      .j2p-sprint
      runs\
        j2p-run-YYYYMMDD-HHMMSS\
          project\
            Program-Source-Of-Truth.sandbox.YYYYMMDD-HHMMSS.mpp
          reports\
            html\
              index.html
              Manager-Review-Report.html
              resource-groups\
                Product_Delivery.html
            csv\
              audit-detail.csv
              planned-epics.csv
              summary-rollups.csv
              dependency-review.csv
              by-project-key\
                TEAM\
                  audit-detail.csv
                  planned-epics.csv
                  summary-rollups.csv
                  dependency-review.csv
          docs\
            FIELD_MAPPING.md
          state\
            j2p-state.after.json
```

`--project-name` is converted to a folder-safe name, such as `Customer-Portal-Program`. `--sprint` is converted the same way, such as `Sprint-24.10`. The sprint folder contains a `.j2p-sprint` marker. If that marker already exists, j2p stops unless `--allow-existing-sprint` is supplied, which prevents accidental duplicate sprint folders while still allowing intentional reruns.

The state file lets future report-only validation compare against the last saved j2p state. It is not the source of truth for the schedule; the `.mpp` remains the schedule source of truth.

## Recommended Review Order

Open `reports\html\index.html` first, or open `reports\html\Manager-Review-Report.html` directly when you only need the overall manager view.

The manager report and each resource-group report contain only three sections, all collapsed by default:

1. **Cascading Schedule Drivers** — linked date changes affecting unfinished dated work, ordered by affected issue count. Expand a branch for its changed Start/Finish dates.
2. **Rollup and Completion** — initiative/fixVersion progress, sorted by Target End with missing dates last. In Planning rollups are omitted. Initiatives use their own Jira target end; fixVersions use the latest target end among member epics, including references. Past dates are marked Past due, today is Due today, and later dates are Upcoming. Completion credits every member of a fixVersion, including reference rows.
3. **Items for Review** — grouped fixes, with the highest-priority actions first and nested `More Current Fixes`, `Later Work`, `Unscheduled Work`, and `Historical Cleanup` lists. Repeated warnings for the same underlying issue are consolidated. The compact rows identify the issue, impact, target date, and next action.

The header shows only title, generation time, and scope. Links inside the sections open the complete audit, rollup, and planned-epic CSVs. Context tables, color legends, point-efficiency views, raw audit tables, and full planned rows are omitted from HTML. Their removal does not change calculations or CSV detail. Open the sandbox `.mpp` to compare the reported changes with Project.

`report_review.focus_days` defaults to 90 days from report generation; set it to `0` for all dated unfinished work. `report_review.max_focus_items` defaults to 25. Overdue unfinished work and serious errors on dated work remain visible. Work with neither Jira target date stays out of the highest-priority fixes, even when it has dependency impact or errors. Its grouped actions remain under Items for Review → Unscheduled Work, with full evidence in the audit CSV. A usable target start or end is enough to follow the normal ranking, and a grouped action with dated affected work remains eligible. Invalid supplied dates still require date-error review; report-wide errors remain eligible. Project auto-scheduled dates do not substitute for Jira target dates in this ranking, and `focus_days: 0` does not promote undated work. Completed work is moved to Historical Cleanup only when no known open parent/child scope or downstream dependency is affected. Completion follows configured `done_statuses`; Cancelled is not silently reclassified. These are report priorities, not a calculated Microsoft Project critical path. The older planning-horizon buckets remain in the CSV audit and do not decide the focus ranking.

Epics missing a Jira target start or end have a note at the beginning of the Project Dependency Review column (Text8 by default). It names the missing field and explains that Project uses existing dates or the nearest available dates allowed by dependencies and calendars. Reference rows identify which primary row drives the schedule. Jira target dates remain blank, and existing dependency notes are retained. The date note clears automatically when both Jira targets are supplied in a later export. These notes are informational and do not add high-priority warnings.

After the final Project recalculation, a driving epic with both Jira target dates missing gets a more specific note when its actual duration is one Project day: “Missing Jira target start/end. Project currently schedules this task for one day; Jira supplied no dates. Confirm the duration.” The check compares [Task.Duration in minutes](https://learn.microsoft.com/en-us/office/vba/api/project.task.duration) with the project’s configured [HoursPerDay](https://learn.microsoft.com/en-us/office/vba/api/project.project.hoursperday), so it does not assume an eight-hour day or infer duration from calendar dates. It reuses the formatting task index, updates only the review text/flag, and leaves Start, Finish, and Duration untouched. References retain their primary-row explanation. Partial dates, longer/zero durations, and unreadable durations retain the general missing-date note; validation without Project cannot confirm a duration. Updates write the final review note only when needed, and the one-day wording clears when the duration changes or Jira dates arrive.

`Cascading Schedule Drivers` is the single schedule-impact section. It lists changed upstream finishes linked to downstream Start or Finish changes on unfinished work with Jira target dates, ordered by the number of unique affected issues. Each collapsed branch shows the driver's key and name, previous/current Finish, and affected count. Expand it to inspect linked Start/Finish changes. Isolated date changes, Jira/Project mismatches without movement, and branches affecting only completed work stay out of this section. Work with both Jira target dates missing is not promoted as a driver, but can appear as context within a branch.

Existing rows compare against the input `.mpp`; new rows and creation runs compare against dates initially supplied or captured before scheduling. Red cards identify drivers and green cards identify affected changes; all changed Project Start/Finish cells remain green. The branches follow the Jira blocker links written as Project predecessors. They show related changes, not proof of causation or a calculated critical path. Resource-group reports show branches starting in that group, including affected downstream work in other groups. Complete date-change and Jira-target-mismatch evidence remains in `audit-detail.csv`. An amber date cell can reflect an existing Jira/Project mismatch or a rejected write without any new schedule movement.

## Color Key

| Color | Meaning | Typical Reviewer Decision |
| --- | --- | --- |
| Green | Changed cell, including autoscheduled Start/Finish. | Confirm the changed value or Project schedule. |
| Red (report diagram only) | Cascade branch driver card; Project date cells remain green. | Review first because this changed item has changed downstream successors. |
| Yellow/amber | Unmatched item, excluded item, or manager review needed. | Decide whether Jira/configuration/source Project data should be corrected. |
| Light gray | Dependency review marker. | Confirm blocker links or fix missing/circular dependencies in Jira. |
| Gray/green-gray | In planning when that column is exposed. | Confirm the epic is intentionally unpointed or add planned child work in Jira. |

j2p applies sandbox colors through Project cell background formatting. During `create` and `update`, it creates and applies a Microsoft Project task table named `j2p Review` before coloring so the review columns are visible without the user manually adding columns. By default, the table shows only manager-facing columns such as Jira key, summary, resource group, dependency review, status, Project start/finish, percent complete, and predecessors. Rollup categories, row role, fixVersion, internal matching keys, story/hour detail fields, Jira target dates, and review flag fields are hidden unless a schedule owner exposes them with `review_table.exposed_columns` in YAML. Hidden fields are still written and reported, but they are not colored in the default Project review table.

To view colored cells, open the generated sandbox `.mpp`, use the Gantt Chart task grid, and apply the `j2p Review` task table from Project's table menu if it is not already active. Cell formatting appears in the left task sheet, not on the right-side Gantt bars. The HTML manager report has its own cascade diagram colors; red branch driver cards do not make the corresponding Project date cells red.

If Project rejects table setup or cell formatting, the run continues and records `ProjectReviewTableSetupFailed` or `ProjectCellColoringFailed` in the audit, with grouped review actions in the report. The underlying task data is still written where Project accepted it. If a sandbox has no visible colors, open the sandbox, choose the `j2p Review` table if it is not already active, and check `audit-detail.csv` for those warning categories. Current j2p versions try exact RGB cell coloring first and then Project's built-in direct `CellColor` palette as a fallback.

Project stores predecessor links as Project task row IDs, not Jira keys. The manager report and audit CSV show Jira keys such as `CORE-1001`, but the sandbox `Predecessors` column normally shows values such as `12FS`. That is expected.

j2p does not use VBA macros or Project font-formatting commands for default coloring. Macros can be useful for a controlled engineering workstation, but they add Office macro security prompts and Trust Center settings that are not ideal for nontechnical handoff. Default highlighting is macro-free and avoids the Project Font dialog; if Project rejects both direct cell-color properties, the manager report calls that out explicitly.

## CSV Inputs

j2p accepts configurable column names. The examples use standard Jira export-style headers.

Recommended Jira CSV columns:

| Logical Field | Example Jira Headers | Required For |
| --- | --- | --- |
| Jira key | `Issue key`, `Key` | All rows. Rows without a key are skipped and reported. |
| Issue ID | `Issue id`, `Issue ID` | Traceability. |
| Issue type | `Issue Type`, `Work Item Type` | Scope decisions. |
| Summary | `Summary`, `Name` | Project task names. |
| Epic Link | `Epic Link` | Child story/task rollup to epics. |
| Parent | `Parent`, `Parent key` | Initiative-mode epic rollup. |
| Fix versions | `Fix versions`, `Fix Version/s` | fixVersion-mode epic rollup. |
| Story points | `Story Points`, `Story point estimate` | Completion calculations. |
| Logged hours | `Logged Hours`, `Time Spent`, `Worklog Hours` | Worklog hour rollup from stories/tasks to epics and summaries. |
| Status | `Status` | Completion calculations. |
| Resolution | `Resolution` | Traceability and future status rules. |
| Resolved | `Resolved` | Last completion date for stale completed fixVersion report suppression. |
| Target start | `Target start` | Project custom date field and schedule review. |
| Target end | `Target end` | Project custom date field and schedule review. |
| Warning suppression date | `Created`, `Resolved`, custom date field | Optional source for suppressing historical warning noise when target dates are not enough. |
| Predecessors | `Inward issue link (Blocks)`, `Blocked by`, `is blocked by` | Project predecessors. |
| Successors | `Outward issue link (Blocks)`, `Blocks` | Project successors. |

## Rollup Modes

j2p supports mixed rollup models in one Jira CSV by using Jira key prefixes.

Example:

```yaml
rollup_modes:
  CORE: initiative
  WEB: initiative
  DATA: initiative
  PLAT: fixVersion
  OPS: fixVersion
```

Initiative-mode teams:

- Each epic must have a parent initiative key.
- The parent initiative must also appear as an Initiative row in the CSV.
- The Project summary row maps to the initiative Jira key.

fixVersion-mode teams:

- Each epic must have at least one fixVersion.
- The Project summary row maps to the exact fixVersion string.
- Epics with no fixVersion are excluded and reported.

## Multi-FixVersion Epics

Some Jira epics are tagged to more than one fixVersion. For example, the same epic might be visible under a qualification event and a later shop deliverable.

j2p supports two policies:

| Policy | Default | Behavior |
| --- | --- | --- |
| `reference` | Yes | The first fixVersion gets the primary driving schedule row. Additional fixVersions get visible non-driving reference rows. |
| `split` | No | Every fixVersion gets its own driving schedule row. |

Recommended default:

```yaml
multi_fixversion_policy:
  default: reference
```

Per-prefix split example:

```yaml
multi_fixversion_policy:
  default: reference
  OPS: split
```

Reference rows are useful when the same Jira epic should be visible in multiple business views without double-counting story points in schedule summaries. Split rows are useful only when the team truly wants the same Jira epic to drive schedule placement under each listed fixVersion.

## Identity And Schedule Keys

The `Jira Key` Project field stores the original Jira key, such as `PLAT-4028`.

The `j2p Unique Key` Project field stores the stable schedule identity used by j2p.

For ordinary epics, both values are the same:

| Field | Value |
| --- | --- |
| Jira Key | `TEAM-123` |
| j2p Unique Key | `TEAM-123` |

For a reference or split row created from an additional fixVersion, the Jira key remains the same but the schedule key is composite:

| Field | Value |
| --- | --- |
| Jira Key | `PLAT-4028` |
| j2p Unique Key | `PLAT-4028::FV::SHOP-DELIVERABLE-A::DE89D3A4` |
| j2p Row Role | `Reference` |
| Primary Schedule Key | `PLAT-4028` |
| Drives Schedule | `No` |

This distinction lets j2p keep the same Jira epic visible in more than one rollup without confusing one Project row for another.

## Percent Complete

Epic percent complete is calculated from child story/task points:

```text
completed child story points / total child story points
```

A child row is complete when its status appears in `done_statuses`.

Example:

| Child Row | Story Points | Status | Counts As Complete |
| --- | ---: | --- | --- |
| Story A | 5 | Done | Yes |
| Story B | 3 | In Progress | No |
| Story C | 2 | Closed | Yes, if `Closed` is in `done_statuses` |

If `Done` and `Closed` are configured as done statuses, the epic is `7 / 10 = 70%` complete.

If an epic has no pointed child work, j2p marks it `In Planning` and sets percent complete to `0`. Items inside the immediate planning window are reported for review. Items more than six months out are reported as future planning items because detailed task breakdown is not expected yet.

## Logged Hours

Logged hours are optional. When the Jira CSV includes a mapped logged-hours column, j2p sums logged hours from child story/task/bug/sub-task rows and writes the result to the parent epic row.

Supported input examples:

| CSV Value | Interpreted As |
| --- | ---: |
| `1.5` | 1.5 hours |
| `1h 30m` | 1.5 hours |
| `1:15` | 1.25 hours |
| `1d 2h` | 10 hours, using an 8-hour day |

By default, logged hours are written to the Microsoft Project custom number field `Number3` and shown with the display name `Logged Hours`. Summary rollups also include logged hours from their driving epic rows. Reference-only rollups show referenced logged hours for visibility without making those rows drive the schedule.

A nonblank logged-hours value must parse completely; malformed, negative, or non-finite values stop validation with file, row, and issue context. Blank values remain zero. See [input validation](input-validation.md) for time units and aggregate-field rules.

## Story Point Ratio

This metric answers: how many completed story points did the team actually deliver for every configured work block of logged time? The default is:

```text
1 story point = 8 hours
```

Formula:

```text
completed child story points / (completed child logged hours / hours per story point)
```

Interpretation:

| Value | Meaning |
| --- | --- |
| `1.00` | The original estimate held: 1 story point per 8 logged hours. |
| Above `1.00` | The team completed more than 1 story point per 8 logged hours. |
| Below `1.00` | The team completed less than 1 story point per 8 logged hours. |
| `0` with no completed story points or no completed logged hours | Not applicable yet because there is no completed-work basis. |

By default, this is written to the Microsoft Project custom number field `Number4` and shown with the display name `Story Point Ratio`. Incomplete child work can still contribute to the total `Logged Hours` field, but it does not affect this metric until the child work is in a done status.

The row-level value is available on each included epic row and in `planned-epics.csv` as `story_point_ratio`. Rollup values remain in `summary-rollups.csv`. Point-efficiency tables are omitted from the compact HTML reports.

## Dependencies

j2p writes only epic-level dependencies to Project.

| Jira Meaning | Jira Column Examples | Project Result |
| --- | --- | --- |
| Epic is blocked by another epic | `Inward issue link (Blocks)`, `Blocked by`, `is blocked by` | The blocking epic becomes a predecessor. |
| Epic blocks another epic | `Outward issue link (Blocks)`, `Blocks` | The blocked epic becomes a successor. |

Dependencies are written as Finish-to-Start predecessor relationships.

j2p does not create placeholder tasks for missing dependency targets. Missing targets are marked in the Dependency Review field and listed in the manager report.

Self-dependencies and circular dependencies are skipped and reported.

## Dates

Jira target dates are stored in Project custom fields:

- `Jira Target Start`
- `Jira Target End`

Jira exports may include dates with times or `DD-MON-YY` syntax, such as `17-SEP-26 12:00 AM`. j2p normalizes supported Jira target-date values to `YYYY-MM-DD` before writing them to Microsoft Project.

The sandbox Project file is auto-scheduled. During a Windows Microsoft Project `create` or `update` run:

- Changed Jira target-date cells are colored green.
- If Project auto-scheduling shifts Start or Finish, the changed Project date cells are green, including cascade branch drivers.
- `Cascading Schedule Drivers` groups changed upstream finishes linked to downstream Start/Finish changes on unfinished dated work. Branch summaries show previous/current Finish and the affected count; expand them for the linked dates.
- Red driver cards and green affected cards describe roles within the HTML branch view, without changing Project date-cell colors.
- `audit-detail.csv` retains all native date movement and Jira-target differences, including isolated changes. A mismatch can remain amber without new movement and does not by itself qualify as schedule impact.

Project accepts only supported calendar dates in schedule fields. j2p converts Jira dates to Project date values before automation writes them. If Project still rejects a date because of range, calendar, or schedule constraints, j2p adds an amber review item instead of stopping the whole run.

Validate mode does not open Microsoft Project, so it cannot detect actual auto-schedule cascades. It can still report Jira date changes and likely review candidates.

## Manager Report Files

| File | Audience | Purpose |
| --- | --- | --- |
| `reports\html\index.html` | Product managers, schedule owners, reviewers | Landing page linking to the overall manager report and each resource-group report. |
| `reports\html\Manager-Review-Report.html` | Product managers, schedule owners, reviewers | Three collapsed sections: Cascading Schedule Drivers, Rollup and Completion, and Items for Review. |
| `reports\html\resource-groups\<Resource_Group>.html` | Resource-group leads, schedule owners | Resource-group scoped report. Cascading Schedule Drivers shows branches that start with that resource group. |
| `reports\csv\audit-detail.csv` | Reviewers needing detail | Full audit register of changed, added, excluded, dependency, and review items. |
| `reports\csv\planned-epics.csv` | Schedule owners | Final included Project epic rows after Jira parsing, logged-hours rollup, Story Point Ratio calculation, and rollup decisions. |
| `reports\csv\summary-rollups.csv` | Product managers, schedule owners | Initiative/fixVersion rollup summaries, percent complete, logged hours, and Story Point Ratio. |
| `reports\csv\dependency-review.csv` | Schedule owners, Jira owners | Dependency-specific review items. |
| `docs\FIELD_MAPPING.md` | Schedule owners, admins | Project custom fields used by this run. |
| `state\j2p-state.after.json` | Tooling/debug support | Machine-readable snapshot after the run. Product users normally do not edit this. |

Each `reports\csv\by-project-key\<KEY>` folder contains the same CSV types filtered to one Jira key prefix.

## Audit CSV Columns

`audit-detail.csv` columns:

| Column | Meaning |
| --- | --- |
| `severity` | `Info`, `Warning`, `Review`, or `Error`. |
| `category` | Machine-readable review category, such as `ChangedName` or `ExcludedMissingRollup`. |
| `jira_key` | Original Jira issue key. |
| `schedule_key` | Stable Project row key used by j2p. May differ from Jira key for secondary reference/split rows. |
| `project_key` | Jira key prefix, such as `TEAM`. |
| `issue_type` | Jira issue type. |
| `summary` | Jira summary or Project task name. |
| `field` | Field being reviewed or changed. |
| `old_value` | Baseline value, when available. |
| `new_value` | New value from the Jira CSV or j2p calculation. |
| `color` | Review color category used in the sandbox. |
| `message` | Plain-language explanation. |
| `reviewer_action` | Suggested manager or schedule-owner action. |
| `source_row` | Jira CSV row number, when known. |

## Planned Epics CSV Columns

`planned-epics.csv` columns:

| Column | Meaning |
| --- | --- |
| `jira_key` | Original Jira epic key. |
| `schedule_key` | Stable Project row identity. |
| `project_key` | Jira key prefix. |
| `summary` | Project task name from Jira summary. |
| `status` | Jira epic status. |
| `rollup_mode` | `initiative` or `fixVersion`. |
| `rollup_key` | Initiative Jira key or fixVersion string. |
| `rollup_name` | Display name for the rollup summary row. |
| `row_role` | `Scheduled`, `Primary`, `Reference`, or `Split`. |
| `fix_version` | FixVersion represented by this row, when applicable. |
| `drives_schedule` | `Yes` if Project dependencies and schedule logic should use this row. |
| `primary_schedule_key` | Primary row for reference/split relationship tracking. |
| `resource_group` | Project resource group derived from Jira key prefix. This is shown in the native Microsoft Project `Resource Group` field through a Project resource assignment. |
| `key_prefix` | Same prefix used for resource and rollup mapping. |
| `total_story_points` | Total child story/task points. |
| `completed_story_points` | Completed child story/task points. |
| `logged_hours` | Logged hours summed from child story/task rows. |
| `completed_logged_hours` | Logged hours from completed child story/task rows. Used with completed story points for the Story Point Ratio metric. |
| `story_point_ratio` | Completed story points delivered per configured 8-hour logged-time block. |
| `percent_complete` | Calculated epic percent complete. |
| `in_planning` | `Yes` when no pointed child work exists. |
| `completed` | `Yes` when the epic itself is in a done status. |
| `target_start` | Jira target start date. |
| `target_end` | Jira target end date. |
| `predecessors` | Schedule keys of predecessor rows. |
| `successors` | Schedule keys of successor rows. |
| `dependency_review` | Human-readable dependency note. |

## Summary Rollups CSV Columns

`summary-rollups.csv` columns:

| Column | Meaning |
| --- | --- |
| `rollup_key` | Initiative Jira key or exact fixVersion string. |
| `project_key` | Jira key prefix, or `MULTIPLE` if mixed. |
| `name` | Rollup display name. |
| `rollup_mode` | `initiative` or `fixVersion`. |
| `child_epic_count` | Number of Project epic rows under the rollup. |
| `driving_epic_count` | Number of rows that drive schedule and counted story points. |
| `reference_epic_count` | Number of non-driving reference rows. |
| `total_story_points` | Counted story points from driving rows only. |
| `completed_story_points` | Counted completed story points from driving rows only. |
| `completion_total_story_points` | Points assigned to this rollup, including references. Used as the completion denominator; do not sum across versions for portfolio totals. |
| `completion_completed_story_points` | Completed points assigned to this rollup, including references. Used as the completion numerator. |
| `logged_hours` | Logged hours from driving rows. Reference-only rollups show referenced hours for visibility. |
| `completed_logged_hours` | Logged hours from completed driving rows. Reference-only rollups show referenced completed hours for visibility. |
| `story_point_ratio` | Completed story points delivered per configured 8-hour logged-time block. |
| `percent_complete` | Completed completion points / total completion points, including references in mixed and reference-only rollups. |

In Microsoft Project, use **Story Point Completion %** (`Number7` by default)
for this calculation. Native `% Complete` on summary rows is recalculated by
Project from schedule durations. **Completion Total Points** (`Number5`) and
**Completion Completed Points** (`Number6`) show the denominator and numerator.
The supplied `yerp` configuration displays `completion_percent` in its review
table. Other configurations can add that logical column to `exposed_columns`.
All three custom fields can be remapped through `project_fields` if occupied.

Native `% Complete` on incomplete epic rows can also change when Project
recalculates duration. J2P seeds it after date/resource updates and reports a
valid scheduling difference as `ProjectNativeCompletionRecalculated`. The
custom Story Point Completion % must still match Jira exactly through save and
reopen. Completed driving rows must retain native 100%; missing or invalid
native percentages remain errors.

## Common Review Outcomes

| Report Category | Meaning | Typical Action |
| --- | --- | --- |
| `ChangedName` | Jira summary changed. | Confirm Project task name should follow Jira. |
| `ChangedField` | Percent, dates, resource group, or other tracked value changed. | Confirm the changed value is expected. |
| `AddedEpic` | Epic is included now but was not in the baseline. | Decide whether the sandbox addition is valid. |
| `RollupMove` | Epic moved to a different initiative or fixVersion rollup. | Confirm product ownership or release tagging. |
| `CompletedSinceLastUpdate` | Epic newly became done. | Confirm native 100% completion and configured Gantt-bar hiding; driving rows remain active. |
| `InPlanning` | Epic has no pointed child work. | Confirm it is intentionally in planning or fix Jira child work. |
| `MultiFixVersionReference` | Multi-fixVersion epic was handled with reference policy. | Confirm the first fixVersion should be primary. |
| `MultiFixVersionSplit` | Multi-fixVersion epic was handled with split policy. | Confirm each fixVersion should drive schedule. |
| `MissingDependencyTarget` | Jira dependency points outside the included epic set. | Add the target to the export/config or fix the Jira link. |
| Logged-hours validation error | A nonblank value was not completely readable as nonnegative time. | Correct the reported file and row, then rerun. |
| `CircularDependencySkipped` | Dependency would create a cycle. | Fix blocker links in Jira. |
| `SelfDependencySkipped` | Epic references itself. | Fix blocker links in Jira. |
| `ExcludedMissingRollup` | Required initiative or fixVersion is missing. | Fix Jira parent/fixVersion or confirm exclusion. |
| `ExcludedUnknownPrefix` | Jira key prefix is not configured. | Add prefix to YAML or confirm exclusion. |
| `UnmatchedProjectTask` | Project baseline task is not in the current Jira plan. | Decide whether it should remain in the source schedule. |
| `SuppressedHistoricalWarnings` | Older dated warning/review items were hidden by the configured historical cutoff. | Confirm the cutoff is intentional for this run. |
| `SuppressedCompletedFixVersion` | A completed fixVersion was old enough to hide from HTML manager reports. | No manager action; detailed CSVs keep the audit trace. |
| `CompletedFixVersionMissingResolvedDate` | A fixVersion is complete by status but one or more issues lack a usable `Resolved` date. | Populate Jira `Resolved` dates or leave the fixVersion visible. |

## Completed FixVersion Report Suppression

For fixVersion-based teams, j2p can hide old completed releases from the HTML manager reports without using a separate release metadata file.

This rule uses the Jira issues CSV:

- j2p groups every issue that declares a fixVersion.
- A fixVersion is considered complete only when every issue in that group has a status in `done_statuses`.
- j2p reads the `Resolved` date for every issue in that completed group.
- The latest `Resolved` date is treated as the fixVersion's last completion date.
- If that latest date is older than `fixversion_completion_suppression.stale_after_days`, the fixVersion rollup is hidden from HTML manager reports.

Default YAML:

```yaml
columns:
  resolved:
    - Resolved

fixversion_completion_suppression:
  enabled: true
  stale_after_days: 90
```

Important behavior:

- The Project sandbox is not pruned by this rule.
- Detailed CSV outputs still include the hidden completed fixVersion rows.
- If the CSV does not include a mapped `Resolved` column, this rule does not run.
- If a completed fixVersion has missing `Resolved` dates, it stays visible and is reported for review.

## Planning Horizon Review Buckets

The manager report separates review items by planning horizon so future work does not drown out near-term decisions.

Default YAML:

```yaml
planning_horizon:
  enabled: true
  immediate_months: 6
  bucket_months: 6
```

`Immediate` means less than six months from the run date, or from `planning_horizon.as_of_date` when that override is supplied. Later items are grouped into six-month buckets such as `6-12 Months` and `12-18 Months`.

For initiative teams, j2p uses the earliest target date on the epic or its child story/task rows. For fixVersion teams, j2p uses the earliest target date on any Jira issue that declares that fixVersion. If an in-planning item is outside the immediate window, it appears as `FutureInPlanning` instead of an immediate review action.

## Suppressing Historical Warning Noise

When a Jira export includes the full project history, older issues may violate rules that only apply to current work. j2p can suppress report and audit warnings for issues dated before a configurable cutoff.

YAML example:

```yaml
warning_suppression:
  before: 2025-01-01
```

One-off command example:

```powershell
python -m j2p validate `
  --jira-csv .\project-wide-jira.csv `
  --config .\config.yaml `
  --project-name "Customer Portal Program" `
  --suppress-warnings-before 2025-01-01
```

Important behavior:

- Suppression does not remove valid epics from the Project sandbox.
- By default, only `Warning` and `Review` audit items can be suppressed.
- j2p checks Jira `Target end` first, then `Target start`.
- If old issues lack target dates, map `columns.warning_suppression_date` to a Jira date such as `Created` or `Resolved`, then set `warning_suppression.date_fields` to check it.
- Items without a usable suppression date remain visible for review.
- The manager report shows a `Historical Items Suppressed` count when anything is hidden.

## Product User Checklist

Before running:

- Confirm the Jira CSV is project-wide enough to include initiatives, epics, and child stories/tasks.
- Confirm Jira key prefixes are listed in `resource_groups`.
- Confirm each prefix has the correct `rollup_modes` setting.
- Confirm fixVersion teams have the right `multi_fixversion_policy`.
- Confirm `done_statuses` matches the team's Jira workflow.
- Confirm column headers in the CSV match the YAML `columns` configuration.
- If the CSV contains old project history, confirm whether `warning_suppression.before` should be blank or set to the team's rule-enforcement start date.

After running `validate`:

- Open `reports\html\Manager-Review-Report.html`.
- Resolve unknown prefixes.
- Resolve missing initiative parents or missing fixVersions.
- Review multi-fixVersion rows and confirm reference versus split behavior.
- Review dependencies before running Project automation.

After running `update`:

- Open the sandbox `.mpp`, not the source-of-truth `.mpp`.
- Review Cascading Schedule Drivers in the manager report first.
- Review green changed cells.
- Review amber unmatched/excluded items.
- Review light-gray dependency review cells.
- Compare the sandbox against the manager report before accepting schedule changes.

## Full Training Scenario

Use `examples\large-scenario\README.md` for the comprehensive end-user walkthrough.

It includes:

- a baseline Jira CSV with 1,200 lines
- an updated Jira CSV with 1,200 lines
- examples of changed names, added epics, rollup moves, date changes, dependency changes, missing dependencies, circular dependencies, in-planning work, unknown prefixes, missing rollups, reference rows, and split rows
- generated manager reports and per-project-key CSVs

The authored training rows are documented in the walkthrough so the examples are stable and teachable.

## Project Write Preflight and Troubleshooting

All commands check planned Project values before writing task rows. `validate` now
fails with exit code 2 if a task name, custom text value, or resource group exceeds
255 characters, a planned number is not finite, or percent complete is outside
0–100. Dependency Review is a special case: when its generated warning text exceeds
255 characters, the Project field shows a shortened preview with an explicit
`Full details: reports/csv/dependency-review.csv` reference. The dependency-review
flag remains set. The full text remains in `planned-epics.csv`, and every dependency
warning remains in `dependency-review.csv` and `audit-detail.csv`; missing links
still require review. This applies to the configured Dependency Review field, even
if it is mapped to a field other than Text8. Other oversized values still fail
validation; shorten their source text and rerun.
An error stops at the first invalid value; rerun after correcting it. No successful
validation report or state snapshot is written for a failed preflight.

Custom-field mappings must use the correct family and range: Text1–30,
Number1–20, Flag1–20, or Date1–10. Each field may have only one mapped value.
NaN and infinity are rejected in CSV numeric inputs and metric configuration.

A failure during epic writes now identifies the epic schedule key, CSV row,
Project field, value type, and text length when applicable. For text values,
`attempted_text` prints the exact string being assigned (the shortened preview
for oversized Dependency Review text). Quotes and escapes make newlines and tabs visible.
Raw COM exception descriptions remain omitted.

Validation does not open Microsoft Project. Formula fields, lookup restrictions,
resource assignment rules, and other constraints in an existing `.mpp` can still
reject an otherwise valid value. Inspect the identified field in the sandbox.
The progress counter is printed before selected rows are written, so use the error's
CSV row and epic key rather than treating the progress counter as a CSV row number.

## Multiple Jira CSV Exports

Pass all export batches for one project snapshot to `--jira-csv`:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\exports\batch-01.csv .\exports\batch-02.csv `
  --config .\config\j2p.yaml `
  --project-name "Customer Portal Program" `
  --output-dir .\review-output
```

The same syntax works for `create` and `update`; their other required arguments
remain unchanged. You can also repeat the flag:
`--jira-csv .\batch-01.csv --jira-csv .\batch-02.csv`.
Quote paths containing spaces. Supply explicit file paths; wildcard expansion is
not performed by j2p.

j2p checks each file and combines issues before calculating points, completion,
rollups, and dependencies. Matching repeated issues are counted once and audited;
conflicting values for one Jira key stop the run with both file/row locations.
A single YAML mapping applies to every file. Keep headers in every CSV; column
order and supported file encodings may differ. Single-file commands still work.
Do not mix baseline and updated snapshots in one run. See
[jira-large-csv-export.md](jira-large-csv-export.md) for browser export instructions.

### Scheduling during a Project write

J2P keeps tasks Auto Scheduled and batches Project calculation while it writes.
New-project creation appends each rollup and its epic rows in final outline order, avoiding repeated insertion above rows already written. Explicit outline levels and parent readback validate the hierarchy. Creation also caches the shared team resources while continuing to verify each task's actual resource assignments. Existing-project updates retain their current placement behavior.

The report includes Project Creation Timing and Project Row Timing. The row breakdown separates placement/summary fields, epic values and resources, and quarter-point calculation time. Resource-assignment time is also shown as a subset of epic-value time; do not sum it twice. Saving, dependency writes, formatting, and close/reopen verification have separate phase timings. These measurements help distinguish slow Project automation calls from calculation time on the actual Windows machine.

It recalculates after about 25%, 50%, 75%, and 100% of planned epic rows, counting
reference rows and unchanged rows visited during updates. Progress logs show the
actual row counts. These are partial schedules until dependency writes finish;
a final calculation then feeds the schedule review and full saved verification.
The original Project application calculation setting is restored on success or
failure. This does not change the requirement to review the generated sandbox.
