# Developer Project Field Reference

This document is for contributors changing Project automation, report generation, comparison logic, or YAML defaults.

Product users usually need only `docs/user-guide.md`, `docs/configuration-reference.md`, and the large walkthrough in `examples/large-scenario/README.md`.

## Why These Fields Exist

j2p needs Microsoft Project rows to be stable across runs. Jira exports can change order, names, dates, rollups, and multi-fixVersion placement, so Project row position is not enough identity. The custom fields below let j2p:

- match an existing Project task to a Jira epic on the next run
- distinguish the original Jira key from generated schedule row keys
- place epics under initiative or fixVersion summary rows
- compare Jira-derived values against the Project baseline
- color only the cells that need review
- keep reference rows visible without letting them drive schedule math
- write manager reports and audit CSVs that line up with the sandbox `.mpp`

The defaults live in `j2p/config.py` under `DEFAULT_CONFIG["project_fields"]` and `DEFAULT_CONFIG["project_field_names"]`.

## Native Project Columns

These are not configured in `project_fields`, but j2p depends on them.

| Project Column | Written By j2p | Read By j2p | Enables |
| --- | --- | --- | --- |
| `Name` | Yes | Yes | Human-readable task name. Used for changed-name detection and green name-cell coloring. |
| `% Complete` / `PercentComplete` | Yes | Yes | Epic and summary percent complete. Used for baseline comparison, active-work accuracy filtering, and manager rollup status. |
| `Start` | Yes, from Jira target start when present | Yes | Scheduled start date. Used with `Date1` to show Jira target start and with Project scheduling for review. |
| `Finish` | Yes, from Jira target end when present | Yes | Scheduled finish date. Used for Project auto-schedule comparison, green cascading changes, and red critical-path root finish changes. |
| `Predecessors` | Yes | Yes | Finish-to-Start dependency links. Jira `blocked by` / `is blocked by` becomes Project predecessors. Project displays task IDs such as `12FS`, so reports keep Jira keys for reviewer clarity. |
| `Successors` | No direct write | Snapshot/audit helper only | Project derives successors from predecessor links. j2p may map audit findings to the Successors column, but dependency writes should remain predecessor-based. |
| `Resource Group` | Yes, through resource assignment | Yes | Team/resource-group ownership. j2p creates or reuses a Project resource, sets its `Group`, and assigns it to the task so Project's native `Resource Group` field is populated. |
| `Active` | Best effort for completed epics | Yes | Completed-epic treatment. j2p marks completed epics inactive and hides Gantt bars when Project permits it. |
| `Summary` / outline parent | Yes, by creating/indenting rows | Yes | Initiative/fixVersion hierarchy. Used to place epics under the correct rollup and detect/move changed rollups. |

## Default Custom Fields

| j2p Field | Default Project Field | Display Name | Row Types | Enables |
| --- | --- | --- | --- | --- |
| `jira_key` | `Text1` | `Jira Key` | Initiative summaries, epics | Stores the original Jira key. It is the human-facing lookup key and a fallback identity field for matching older Project rows that do not yet have `j2p_key`. |
| `jira_issue_id` | `Text2` | `Jira Issue ID` | Epics | Stores Jira's numeric issue ID for traceability. Not used as the primary matching key because exports and user expectations center on Jira keys. |
| `jira_issue_type` | `Text3` | `Jira Issue Type` | Summary rows, epics | Marks rows as `Initiative`, `FixVersion`, or `Epic`. Helps snapshots, review tables, and developers distinguish generated summary rows from scheduled epic rows. |
| `rollup_mode` | `Text4` | `Rollup Mode` | Summary rows, epics | Stores `initiative` or `fixVersion`. Used with `rollup_key` to find existing summary rows and keep mixed-mode schedules stable. |
| `rollup_key` | `Text5` | `Rollup Key` | Summary rows, epics | Stores the initiative Jira key or exact fixVersion string. Enables rollup comparison, summary row lookup, and moving epics under a changed parent/rollup. |
| `jira_key_prefix` | `Text7` | `Jira Key Prefix` | Epics | Stores prefixes such as `CORE`, `WEB`, or `PLAT`. Used for review visibility and per-project-key output alignment. `Text6` is intentionally unused because resource group is native. |
| `dependency_review` | `Text8` | `Dependency Review` | Epics | Stores human-readable dependency notes, such as missing targets, self-dependencies, circular skips, or reference-row notes. Cells using this field are colored blue when dependency review is needed. |
| `jira_status` | `Text9` | `Jira Status` | Epics | Stores the Jira epic status. Used for baseline comparison and completed-since-last-update reporting. Child story status is not written to Project rows. |
| `j2p_key` | `Text10` | `j2p Unique Key` | Epics, generated secondary rows | Primary stable schedule row identity. Ordinary epics use the Jira key. Secondary reference/split rows use a generated key such as `PLAT-4028::FV::SHOP-DELIVERABLE-A::DE89D3A4`. This field prevents multi-fixVersion rows from overwriting one another. |
| `row_role` | `Text11` | `j2p Row Role` | Epics, generated secondary rows | Shows `Scheduled`, `Primary`, `Reference`, or `Split`. Enables reviewer understanding of multi-fixVersion handling and helps developers reason about whether a row is a normal epic, a primary row, a non-driving reference, or a split row. |
| `fix_version` | `Text12` | `Jira Fix Version` | fixVersion-mode rows | Stores the specific fixVersion represented by this row. Needed because one Jira epic may produce multiple Project rows when it has multiple fixVersions. |
| `primary_schedule_key` | `Text13` | `Primary Schedule Key` | Reference/split rows | Points a secondary row back to its primary schedule key. Reference rows use this to show which driving row owns schedule logic. |
| `total_story_points` | `Number1` | `Total Story Points` | Summary rows, epics | Stores total child story/task points. Used for percent-complete math, rollup weighting, baseline comparison, changed-cell coloring, and in-planning detection. |
| `completed_story_points` | `Number2` | `Completed Story Points` | Summary rows, epics | Stores completed child story/task points. Used with total points for percent complete and with logged hours for hours-accuracy calculations. |
| `logged_hours` | `Number3` | `Logged Hours` | Summary rows, epics | Stores all logged hours rolled up from child story/task rows. Used for review reporting and changed-cell coloring. It does not affect percent complete. |
| `hours_accuracy_percent` | `Number4` | `Hours Accuracy %` | Summary rows, epics | Stores completed logged hours divided by expected completed hours. Expected hours are completed story points times `metrics.hours_per_story_point`. The manager report also calculates active-work project-wide and resource-group accuracy from in-progress scheduled rows only. |
| `in_planning` | `Flag1` | `In Planning` | Epics | Marks included epics with no pointed child work. Enables gray/green-gray review coloring and manager review of intentionally unestimated work. |
| `unmatched_project_task` | `Flag2` | `Unmatched Project Task` | Existing Project rows | Marks Project tasks found in the baseline/sandbox but not included in the current Jira plan. Enables amber review for stale or out-of-scope schedule items. |
| `dependency_review_needed` | `Flag3` | `Dependency Review Needed` | Epics | Boolean quick filter for rows with dependency-review text. Enables fast review of rows that should be inspected for blocker issues. |
| `drives_schedule` | `Flag4` | `Drives Schedule` | Epics, generated secondary rows | Distinguishes rows that should participate in schedule dependencies and counted rollup math. Reference rows set this to `No`; scheduled, primary, and split rows set this to `Yes`. |
| `jira_target_start` | `Date1` | `Jira Target Start` | Epics | Stores the Jira target start date separately from Project's native `Start`. Enables baseline comparison and review of Jira-driven date changes. |
| `jira_target_end` | `Date2` | `Jira Target End` | Epics | Stores the Jira target end date separately from Project's native `Finish`. Enables baseline comparison, changed-date coloring, and reporting when Project auto-scheduled finish differs from Jira target end. |

## Field Interactions

Identity:

- `j2p_key` is the canonical Project row key after a file has been touched by j2p.
- `jira_key` remains the reviewer-facing Jira key.
- `index_tasks_by_key()` looks for `j2p_key` first, then `jira_key`.
- For ordinary epics, `j2p_key == jira_key`.
- For secondary reference/split rows, `jira_key` stays the same and `j2p_key` becomes composite.

Rollups:

- Initiative summary rows use `jira_key`, `jira_issue_type`, `rollup_mode`, and `rollup_key`.
- fixVersion summary rows use `jira_issue_type`, `rollup_mode`, and `rollup_key`; they may not have a Jira key.
- `rollup_mode + rollup_key` is how j2p finds existing fixVersion summaries.
- When an epic's rollup changes, `ensure_epic_under_summary()` moves the row in the sandbox and `rollup_key` is colored/logged for review.

Completion and metrics:

- `total_story_points` and `completed_story_points` are calculated from child Jira rows, not from Project children.
- `PercentComplete` is manually written from the story-point ratio.
- `logged_hours` is separate from completion and includes all child logged hours.
- `hours_accuracy_percent` uses only completed child logged hours and completed child story points.
- Manager-report aggregate accuracy uses only active scheduled epics: `drives_schedule == True` and `0 < percent_complete < 100`.

Dependencies:

- j2p writes dependencies through Project's native `Predecessors` field, but the automation path should prefer Project object-link APIs such as `TaskDependencies.Add()` and `Task.LinkPredecessors()`.
- Direct text assignment such as `Task.Predecessors = "12"` or `Task.Predecessors = "12FS"` is kept only as a fallback because it depends on Project parsing a field string correctly.
- The default dependency write mode is `fast`, which uses a bounded set of methods and skips rows that already match. The `diagnostic` mode intentionally tries more Project APIs and should be used only to capture detailed failure evidence.
- `dependency_review` stores notes for skipped or concerning dependencies.
- `dependency_review_needed` is a filterable flag for those notes.
- Reference rows should not receive schedule-driving dependencies because `drives_schedule` is `False`.

Review coloring:

- `project_column_for_audit_field()` maps audit fields to the Project column that should be colored.
- `review_table_columns()` builds the `j2p Review` table so target columns are visible before coloring.
- Red `cascade_root` coloring applies to native `Finish` after Project scheduling analysis.
- Green changed-cell coloring applies to the changed native/custom field.
- Amber review coloring commonly applies to `unmatched_project_task` or excluded/review fields.
- Blue dependency coloring commonly applies to `dependency_review`.
- Gray/green-gray planning coloring applies to `in_planning`.

## Adding Or Changing A Field

When adding a Project field, update all relevant locations:

| Area | File | What To Check |
| --- | --- | --- |
| Defaults | `j2p/config.py` | Add `project_fields` ID and `project_field_names` display name. |
| Planning model | `j2p/core.py` | Add dataclass attributes, calculations, baseline snapshots, and changed-field audit rows. |
| Project write path | `j2p/project.py` | Write the field to summary/epic tasks, read it in snapshots, and map audit fields for coloring. |
| Review table | `j2p/project.py` | Add the field to `review_table_columns()` if it should be visible and colorable. |
| Reports | `j2p/reports.py` | Add report/CSV columns and manager HTML sections as needed. |
| Product docs | `docs/user-guide.md`, `docs/configuration-reference.md` | Explain what users see and what they can configure. |
| Developer docs | `docs/project-fields.md` | Explain what the field enables and any interactions. |
| Examples | `examples/large-scenario/README.md` and generated report examples | Keep the large walkthrough teachable and regenerated. |
| Tests | `tests/test_j2p.py`, `scripts/smoke_tests.py` | Add unit coverage and smoke assertions. |

Field changes are risky when they affect identity, rollup movement, dependency writes, or color selection. Keep backward compatibility in mind: existing `.mpp` files may already contain j2p data using the previous default field IDs.

## Collision Guidance

Organizations may already use fields such as `Text1` or `Number1` in their Project templates. The YAML `project_fields` section can remap j2p to other Project custom fields, but developers must preserve the logical field names.

Do not hard-code defaults such as `Text1` outside fallback paths. Use:

```python
fields = config.get("project_fields", {})
fields.get("jira_key", "Text1")
```

When a field is remapped:

- `configure_custom_fields()` should rename the selected Project custom field.
- `snapshot_tasks()` must read the selected field.
- `update_epic_task()` and `ensure_summaries()` must write the selected field.
- audit coloring must map to the selected field through `project_column_for_audit_field()`.

## Manual Windows Verification

Cross-platform tests cannot open Microsoft Project. Any change to field writing, task matching, review table setup, or coloring needs a Windows smoke test with Microsoft Project desktop installed.

Confirm:

- custom fields are renamed as expected
- the `j2p Review` table includes every field needed for review/coloring
- `j2p Unique Key` remains stable across update runs
- multi-fixVersion reference rows do not overwrite primary rows
- resource group appears in native `Resource Group`
- changed cells are colored in the intended columns
- Project predecessor IDs correspond to the Jira dependencies shown in the report
- manager report values match the visible sandbox fields
