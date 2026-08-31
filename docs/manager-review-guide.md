# Manager Review Guide

Open `Manager-Review-Report.html` first. It is self-contained and can be emailed or archived with the sandbox.

## Review Order

1. Review `Reviewer Action Needed`.
2. Check the color key.
3. Open the sandbox `.mpp`.
4. Review changed green cells.
5. Review red finish-date cells first.
6. Review amber unmatched or excluded items.
7. Review blue dependency review markers.
8. Use `audit-detail.csv` only when you need row-level evidence.

## Report Files

| File | Purpose |
| --- | --- |
| `Manager-Review-Report.html` | Manager-level review report |
| `audit-detail.csv` | Complete row-level audit register |
| `planned-epics.csv` | Final included epic set and calculated fields |
| `summary-rollups.csv` | Initiative/fixVersion rollup summary values |
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
