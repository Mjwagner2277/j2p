# Manager Review Guide

Open `Manager-Review-Report.html` first. It is self-contained and can be emailed or archived with the sandbox.

## Review Order

1. Review `Decision Briefing`.
2. Review `Project-Wide Hours Accuracy`.
3. Expand `Accuracy By Resource Group` when you need the active-work accuracy split by team/resource group.
4. Review `Rollup Status`.
5. Review `Reviewer Action Needed`.
6. Review `Review Type Summary`.
7. Check `Project Key Rollup Mapping`.
8. Check `Color Key` and `Color Case Examples`.
9. Expand `Detailed Review Sections` only when you need the category-level tables.
10. Expand `Full Planned Epic Rows` only when you need every planned schedule row.
11. Open the sandbox `.mpp`.
12. Review red finish-date cells first, then changed green cells, amber review cells, blue dependency cells, and in-planning rows.
13. Use `audit-detail.csv` only when you need row-level evidence outside the HTML report.

Large detail tables are collapsed by default so manager-level accuracy, status, and required-review items stay at the top of the report.

## Report Files

| File | Purpose |
| --- | --- |
| `Manager-Review-Report.html` | Manager-level review report |
| `audit-detail.csv` | Complete row-level audit register |
| `planned-epics.csv` | Final included epic set and calculated fields, including rolled-up logged hours |
| `summary-rollups.csv` | Initiative/fixVersion rollup summary values, including logged hours |
| `dependency-review.csv` | Dependency-specific warnings and changes |
| `FIELD_MAPPING.md` | Project custom field mapping |
| `j2p-state.after.json` | Snapshot of the run output |

Each run also writes split CSVs by Jira project key prefix:

- `by-project-key\<KEY>\audit-detail.csv`
- `by-project-key\<KEY>\planned-epics.csv`
- `by-project-key\<KEY>\summary-rollups.csv`
- `by-project-key\<KEY>\dependency-review.csv`

Use these when a manager only needs the rows for one Jira key prefix, such as `TEAM` or `PLAT`.

## Items That Need Manager Attention

Changed names:

- j2p updates the sandbox task name from Jira.
- The changed name appears in the report.
- The changed Project cell is colored green.

Moved epics:

- j2p moves the epic under the new initiative/fixVersion in the sandbox when Project automation can perform the move.
- The move is reported.

Multi-fixVersion epics:

- `reference` is the default policy. The first fixVersion gets the primary scheduled row; additional fixVersions get visible non-driving reference rows.
- `split` creates one driving schedule row per fixVersion and should be used only when that is the intended planning behavior.
- Review `j2p Row Role`, `Drives Schedule`, and `Primary Schedule Key` to understand how each row is being used.

Unmatched Project tasks:

- The task exists in the source-of-truth comparison baseline.
- The task was not included in the current Jira CSV plan.
- The task is marked amber for review.

Missing dependency targets:

- Jira references a blocker or blocked issue that is not an included epic.
- No placeholder is created.
- The Dependency Review field is marked blue.

In planning:

- The epic has no pointed child stories/tasks.
- `% Complete` is set to 0.
- The In Planning field is marked.

Logged hours:

- Logged hours are summed from child story/task/bug/sub-task rows to the epic row.
- The value appears in `Rollup Status`, `planned-epics.csv`, and the sandbox `Logged Hours` column.
- Logged hours are review information only; percent complete still comes from completed story points divided by total story points.

Hours accuracy:

- `Hours Accuracy %` compares completed child logged hours to completed story points multiplied by the configured hours-per-point value.
- `100%` means completed work logged exactly the expected hours.
- Values below or above `100%` show that completed work logged fewer or more hours than expected.
- `0%` with no completed story points means the ratio is not applicable yet.
- Project-wide and resource-group accuracy sections use only active scheduled epic rows: rows that drive the schedule and have `% Complete` from 1 to 99.
