# j2p Product User Guide

This guide is for people who run j2p, review its outputs, or decide whether a Jira change should be accepted into a Microsoft Project schedule. It does not assume you are changing the code.

For contributor and testing information, use `docs/contributing.md` and `docs/testing.md`.

## What j2p Does

j2p reads a project-wide Jira CSV export and prepares Microsoft Project review material.

The normal workflow is:

1. Export a Jira CSV that includes initiatives, epics, and child stories/tasks.
2. Run j2p in `validate` mode to generate reports without opening Microsoft Project.
3. Review the manager report for exclusions, data-quality concerns, dependencies, and changed values.
4. Run j2p in `update` mode against the source-of-truth `.mpp`.
5. j2p copies the source-of-truth `.mpp` to a timestamped sandbox and updates only that sandbox.
6. Review the sandbox `.mpp`, `Manager-Review-Report.html`, and CSV audit files.
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

For report-only validation on macOS, Linux, or a Windows machine without Microsoft Project:

```powershell
py -3.14 -m pip install -e .
```

Report-only validation can parse Jira CSVs and create HTML/CSV reports. It cannot create or update `.mpp` files because that requires Microsoft Project desktop automation.

## Commands

Run commands from the repository root.

Validate a CSV and write reports without opening Microsoft Project:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\examples\project-wide-jira-update.csv `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```

Create an initial `.mpp` from Jira. This is intended for first setup or demonstrations:

```powershell
py -3.14 -m j2p create `
  --jira-csv .\examples\project-wide-jira-update.csv `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output `
  --output-project-name j2p-initial-sandbox.mpp
```

Update a sandbox copy from the source-of-truth `.mpp`:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\path\to\jira-export.csv `
  --main-project .\path\to\Program-Source-Of-Truth.mpp `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```

Compare against a previous sandbox instead of the main file:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\path\to\jira-export.csv `
  --main-project .\path\to\Program-Source-Of-Truth.mpp `
  --previous-sandbox .\review-output\j2p-run-20260901-090000\Program-Source-Of-Truth.sandbox.20260901-090000.mpp `
  --comparison-source previous-sandbox `
  --config .\examples\config.example.yaml `
  --output-dir .\review-output
```

## Command Reference

Common arguments:

| Argument | Applies To | Required | Meaning |
| --- | --- | --- | --- |
| `--jira-csv` | `validate`, `create`, `update` | Yes | Path to the project-wide Jira CSV export. |
| `--config` | `validate`, `create`, `update` | Recommended | Path to YAML configuration. If omitted, built-in defaults are used. |
| `--output-dir` | `validate`, `create`, `update` | No | Base folder for reports, state, and timestamped run folders. Default is `review-output`. |
| `--state-path` | `validate`, `create`, `update` | No | Custom path for persistent state JSON. Default is `<output-dir>\j2p-state.json`. |
| `--rollup-mode` | `validate`, `create`, `update` | No | Temporarily overrides `rollup_mode` from YAML. |
| `--run-id` | `validate`, `create`, `update` | No | Overrides timestamp naming. Useful for repeatable tests or examples. |

`validate` arguments:

| Argument | Required | Meaning |
| --- | --- | --- |
| `--compare-state` | No | Compare current Jira plan against the persistent state file if it exists. |
| `--write-state` | No | Write the persistent state file after validation. |

`create` arguments:

| Argument | Required | Meaning |
| --- | --- | --- |
| `--output-project-name` | No | Name of the initial `.mpp` created inside the run folder. |
| `--visible` | No | Leave Microsoft Project visible during automation. |

`update` arguments:

| Argument | Required | Meaning |
| --- | --- | --- |
| `--main-project` | Yes | Source-of-truth `.mpp` copied into a timestamped sandbox. |
| `--comparison-source` | No | Baseline for change reporting. Allowed values are `main`, `previous-sandbox`, and `state`. Default is `main`. |
| `--previous-sandbox` | Required only with `--comparison-source previous-sandbox` | Prior sandbox `.mpp` used for iterative review comparison. |
| `--visible` | No | Leave Microsoft Project visible during automation. |

## Output Folder

Each run writes a timestamped run folder:

```text
review-output\j2p-run-YYYYMMDD-HHMMSS\
  Program-Source-Of-Truth.sandbox.YYYYMMDD-HHMMSS.mpp
  Manager-Review-Report.html
  audit-detail.csv
  planned-epics.csv
  summary-rollups.csv
  dependency-review.csv
  FIELD_MAPPING.md
  j2p-state.after.json
  by-project-key\
    TEAM\
      audit-detail.csv
      planned-epics.csv
      summary-rollups.csv
      dependency-review.csv
```

The base output folder also stores the persistent state file:

```text
review-output\j2p-state.json
```

The state file lets future report-only validation compare against the last saved j2p state. It is not the source of truth for the schedule; the `.mpp` remains the schedule source of truth.

## Recommended Review Order

Open `Manager-Review-Report.html` first.

1. Review `Executive Summary`.
2. Review `Rollup Status` for initiative/fixVersion progress.
3. Review `Reviewer Action Needed`.
4. Review `Review Type Summary` to see counts by issue category.
5. Review `Project Key Rollup Mapping`.
6. Review `Color Key` and `Color Case Examples`.
7. Expand `Detailed Review Sections` only when you need category-level detail such as changed names, added epics, dependencies, or exclusions.
8. Expand `Full Planned Epic Rows` only when you need the full row-level planned schedule table.
9. Expand `CSV Column Mapping Used` when verifying how Jira headers were interpreted.
10. Open the sandbox `.mpp` and compare colored cells with the report.

The manager report intentionally keeps rollup status and review-required items at the top. Large detail tables are collapsed so a manager does not have to scroll through hundreds of planned epic rows before seeing the decisions that matter.

## Color Key

| Color | Meaning | Typical Reviewer Decision |
| --- | --- | --- |
| Green | Changed cell. | Confirm the Jira value should update the sandbox schedule. |
| Red | First/root critical-path finish-date driver. Red overrides green. | Review first because this is the likely source of cascading schedule movement. |
| Yellow/amber | Unmatched item, excluded item, or manager review needed. | Decide whether Jira/configuration/source Project data should be corrected. |
| Blue | Dependency review marker. | Confirm blocker links or fix missing/circular dependencies in Jira. |
| Gray/green-gray | In planning. | Confirm the epic is intentionally unpointed or add planned child work in Jira. |

j2p applies sandbox colors through Project cell background formatting. If Project rejects a specific cell-formatting operation, the run continues and adds a `ProjectCellColoringFailed` item to the report instead of opening a formatting dialog.

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
| Status | `Status` | Completion calculations. |
| Resolution | `Resolution` | Traceability and future status rules. |
| Target start | `Target start` | Project custom date field and schedule review. |
| Target end | `Target end` | Project custom date field and schedule review. |
| Predecessors | `Inward issue link (Blocks)`, `Blocked by`, `is blocked by` | Project predecessors. |
| Successors | `Outward issue link (Blocks)`, `Blocks` | Project successors. |

## Rollup Modes

j2p supports mixed rollup models in one Jira CSV by using Jira key prefixes.

Example:

```yaml
rollup_mode: initiative

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

If an epic has no pointed child work, j2p marks it `In Planning`, sets percent complete to `0`, and reports it for review.

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

The sandbox Project file is auto-scheduled. During a Windows Microsoft Project `update` run:

- Changed Jira target-date cells are colored green.
- If Project auto-scheduling shifts finish dates, the first/root detected finish-date driver is colored red.
- Downstream cascading finish-date shifts remain green.
- If a Project scheduled finish does not match Jira `Target end`, the mismatch is reported.

Project accepts only supported calendar dates in schedule fields. j2p converts Jira dates to Project date values before automation writes them. If Project still rejects a date because of range, calendar, or schedule constraints, j2p adds an amber review item instead of stopping the whole run.

Validate mode does not open Microsoft Project, so it cannot detect actual auto-schedule cascades. It can still report Jira date changes and likely review candidates.

## Manager Report Files

| File | Audience | Purpose |
| --- | --- | --- |
| `Manager-Review-Report.html` | Product managers, schedule owners, reviewers | Self-contained review report with summary sections and review guidance. |
| `audit-detail.csv` | Reviewers needing detail | Full audit register of changed, added, excluded, dependency, and review items. |
| `planned-epics.csv` | Schedule owners | Final included Project epic rows after Jira parsing and rollup decisions. |
| `summary-rollups.csv` | Product managers, schedule owners | Initiative/fixVersion rollup summaries and percent complete. |
| `dependency-review.csv` | Schedule owners, Jira owners | Dependency-specific review items. |
| `FIELD_MAPPING.md` | Schedule owners, admins | Project custom fields used by this run. |
| `j2p-state.after.json` | Tooling/debug support | Machine-readable snapshot after the run. Product users normally do not edit this. |

Each `by-project-key\<KEY>` folder contains the same CSV types filtered to one Jira key prefix.

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
| `percent_complete` | Weighted percent complete. Reference-only rollups show visible referenced progress but keep counted points at zero. |

## Common Review Outcomes

| Report Category | Meaning | Typical Action |
| --- | --- | --- |
| `ChangedName` | Jira summary changed. | Confirm Project task name should follow Jira. |
| `ChangedField` | Percent, dates, resource group, or other tracked value changed. | Confirm the changed value is expected. |
| `AddedEpic` | Epic is included now but was not in the baseline. | Decide whether the sandbox addition is valid. |
| `RollupMove` | Epic moved to a different initiative or fixVersion rollup. | Confirm product ownership or release tagging. |
| `CompletedSinceLastUpdate` | Epic newly became done. | Confirm inactive/hidden schedule treatment. |
| `InPlanning` | Epic has no pointed child work. | Confirm it is intentionally in planning or fix Jira child work. |
| `MultiFixVersionReference` | Multi-fixVersion epic was handled with reference policy. | Confirm the first fixVersion should be primary. |
| `MultiFixVersionSplit` | Multi-fixVersion epic was handled with split policy. | Confirm each fixVersion should drive schedule. |
| `MissingDependencyTarget` | Jira dependency points outside the included epic set. | Add the target to the export/config or fix the Jira link. |
| `CircularDependencySkipped` | Dependency would create a cycle. | Fix blocker links in Jira. |
| `SelfDependencySkipped` | Epic references itself. | Fix blocker links in Jira. |
| `ExcludedMissingRollup` | Required initiative or fixVersion is missing. | Fix Jira parent/fixVersion or confirm exclusion. |
| `ExcludedUnknownPrefix` | Jira key prefix is not configured. | Add prefix to YAML or confirm exclusion. |
| `UnmatchedProjectTask` | Project baseline task is not in the current Jira plan. | Decide whether it should remain in the source schedule. |

## Product User Checklist

Before running:

- Confirm the Jira CSV is project-wide enough to include initiatives, epics, and child stories/tasks.
- Confirm Jira key prefixes are listed in `resource_groups`.
- Confirm each prefix has the correct `rollup_modes` setting.
- Confirm fixVersion teams have the right `multi_fixversion_policy`.
- Confirm `done_statuses` matches the team's Jira workflow.
- Confirm column headers in the CSV match the YAML `columns` configuration.

After running `validate`:

- Open `Manager-Review-Report.html`.
- Resolve unknown prefixes.
- Resolve missing initiative parents or missing fixVersions.
- Review multi-fixVersion rows and confirm reference versus split behavior.
- Review dependencies before running Project automation.

After running `update`:

- Open the sandbox `.mpp`, not the source-of-truth `.mpp`.
- Review red finish-date cells first.
- Review green changed cells.
- Review amber unmatched/excluded items.
- Review blue dependency review cells.
- Compare the sandbox against the manager report before accepting schedule changes.

## Full Training Scenario

Use `examples\large-scenario\README.md` for the comprehensive end-user walkthrough.

It includes:

- a baseline Jira CSV with 1,200 lines
- an updated Jira CSV with 1,200 lines
- examples of changed names, added epics, rollup moves, date changes, dependency changes, missing dependencies, circular dependencies, in-planning work, unknown prefixes, missing rollups, reference rows, and split rows
- generated manager reports and per-project-key CSVs

The authored training rows are documented in the walkthrough so the examples are stable and teachable.
