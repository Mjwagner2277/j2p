# j2p YAML Configuration Reference

This document explains every supported YAML configuration section for product users and schedule owners.

For a complete working file, start with `examples/large-scenario/config.large-example.yaml`.

Developers changing Microsoft Project field behavior should also read `docs/project-fields.md`.

## Minimal Example

```yaml
rollup_mode: initiative

rollup_modes:
  TEAM: initiative
  PLAT: fixVersion

done_statuses:
  - Done
  - Closed
  - Resolved

resource_groups:
  TEAM: Product Delivery
  PLAT: Platform Engineering

multi_fixversion_policy:
  default: reference
  PLAT: split
```

## Full Example

```yaml
rollup_mode: initiative

rollup_modes:
  TEAM: initiative
  PLAT: fixVersion
  DATA: initiative

done_statuses:
  - Done
  - Closed
  - Resolved

resource_groups:
  TEAM: Product Delivery
  PLAT: Platform Engineering
  DATA: Data Engineering

multi_fixversion_policy:
  default: reference

columns:
  jira_key:
    - Issue key
    - Key
  issue_id:
    - Issue id
    - Issue ID
  issue_type:
    - Issue Type
    - Work Item Type
  summary:
    - Summary
    - Name
  epic_link:
    - Epic Link
  parent:
    - Parent
    - Parent key
  fix_versions:
    - Fix versions
    - Fix Version/s
  story_points:
    - Story Points
    - Story point estimate
  status:
    - Status
  resolution:
    - Resolution
  target_start:
    - Target start
  target_end:
    - Target end
  predecessors:
    - Inward issue link (Blocks)
    - Blocked by
    - is blocked by
  successors:
    - Outward issue link (Blocks)
    - Blocks

behavior:
  unknown_prefix: exclude
  hide_completed_epics: true
  write_state_on_validate: false

metrics:
  hours_per_story_point: 8

project_fields:
  jira_key: Text1
  jira_issue_id: Text2
  jira_issue_type: Text3
  rollup_mode: Text4
  rollup_key: Text5
  jira_key_prefix: Text7
  dependency_review: Text8
  jira_status: Text9
  j2p_key: Text10
  row_role: Text11
  fix_version: Text12
  primary_schedule_key: Text13
  total_story_points: Number1
  completed_story_points: Number2
  logged_hours: Number3
  hours_accuracy_percent: Number4
  in_planning: Flag1
  unmatched_project_task: Flag2
  dependency_review_needed: Flag3
  drives_schedule: Flag4
  jira_target_start: Date1
  jira_target_end: Date2
```

## Top-Level Fields

| Field | Required | Default | Allowed Values | Purpose |
| --- | --- | --- | --- | --- |
| `rollup_mode` | No | `initiative` | `initiative`, `fixVersion` | Default rollup behavior for prefixes not listed in `rollup_modes`. |
| `rollup_modes` | Recommended | `{}` | Mapping of Jira key prefix to `initiative` or `fixVersion` | Lets each team use its own rollup model in one project-wide Jira CSV. |
| `done_statuses` | Recommended | `Done` | List of Jira statuses | Statuses that count child story/task points as completed. |
| `resource_groups` | Yes for included epics | `{}` | Mapping of Jira key prefix to Project resource group name | Controls which Jira prefixes are included and what resource group each epic receives. |
| `multi_fixversion_policy` | No | `reference` | `reference`, `split` | Controls how fixVersion-mode epics with multiple fixVersions are represented. |
| `columns` | Recommended | Built-in defaults | Mapping of logical j2p fields to one or more CSV headers | Lets j2p read different Jira export header names. |
| `behavior` | No | Built-in defaults | Mapping | Operational guardrails. |
| `metrics` | No | Built-in defaults | Mapping | Controls conversion rates such as hours per story point. |
| `review_table` | No | Built-in defaults | `all` or list of exposed columns | Controls which columns are shown in the Microsoft Project `j2p Review` table. |
| `project_fields` | No | Built-in defaults | Microsoft Project custom field IDs | Controls which Project custom fields j2p writes. Resource Group is native and is not configured here. |
| `project_field_names` | No | Built-in defaults | Mapping of j2p fields to display names | Controls custom column names in the sandbox. Usually omitted because defaults are user-friendly. |
| `colors` | No | Built-in defaults | Hex colors | Controls cell highlight colors. Usually omitted. |

## `rollup_mode`

Sets the default rollup model.

```yaml
rollup_mode: initiative
```

Use `initiative` when most teams organize epics under Jira initiatives.

Use `fixVersion` when most teams organize epics under fixVersions.

If a prefix appears in `rollup_modes`, that prefix-specific value wins.

## `rollup_modes`

Maps Jira key prefixes to rollup models.

```yaml
rollup_modes:
  TEAM: initiative
  PLAT: fixVersion
  OPS: fixVersion
```

Rules:

- Prefixes are case-insensitive in configuration and normalized to uppercase.
- `initiative` means each epic must have a parent initiative key.
- `fixVersion` means each epic must have at least one fixVersion.
- Prefixes not listed here use `rollup_mode`.

Example Jira keys:

| Jira Key | Prefix | Rollup Mode From Example |
| --- | --- | --- |
| `TEAM-123` | `TEAM` | `initiative` |
| `PLAT-4028` | `PLAT` | `fixVersion` |
| `OPS-5019` | `OPS` | `fixVersion` |

## `done_statuses`

Lists Jira statuses that count child story/task points as completed.

```yaml
done_statuses:
  - Done
  - Closed
  - Resolved
```

Rules:

- Matching is case-insensitive.
- These statuses apply to child story/task rows for percent-complete calculations.
- These statuses also identify completed epics for completed-epic reporting.
- If a team uses statuses such as `Accepted`, `Released`, or `Complete`, add them here.

## `resource_groups`

Maps Jira key prefixes to Microsoft Project resource group names.

```yaml
resource_groups:
  TEAM: Product Delivery
  PLAT: Platform Engineering
  OPS: Operations
```

Rules:

- A Jira epic prefix must be present in `resource_groups` to be included.
- Unknown prefixes are excluded and reported by default.
- The mapped value is shown in the native Microsoft Project `Resource Group` field by assigning a Project resource whose `Group` value matches this mapping.

Use this section as the authoritative list of Jira project keys that j2p is allowed to schedule.

## `multi_fixversion_policy`

Controls fixVersion-mode epics that have multiple fixVersions.

```yaml
multi_fixversion_policy:
  default: reference
  OPS: split
```

Allowed policies:

| Policy | Behavior |
| --- | --- |
| `reference` | First fixVersion gets the primary driving row. Additional fixVersions get visible non-driving reference rows. |
| `split` | Every fixVersion gets a driving schedule row. |

Rules:

- `reference` is the default.
- Only `reference` and `split` are supported.
- Prefix-specific values override `default`.
- Values are normalized, so `Reference`, `REFERENCE`, and ` reference ` are accepted.
- Older `behavior.multiple_fix_versions` configuration is not supported.

Recommended approach:

- Use `reference` unless there is a clear schedule-management reason to split the epic.
- Use `split` only for teams that intentionally want the same Jira epic to drive schedule rows under multiple fixVersions.

## `columns`

Maps j2p logical fields to possible Jira CSV headers.

j2p uses the first matching header it finds in the CSV.

```yaml
columns:
  jira_key:
    - Issue key
    - Key
  story_points:
    - Story Points
    - Story point estimate
  logged_hours:
    - Logged Hours
    - Time Spent
    - Worklog Hours
```

If your Jira export says `Custom field (Story point estimate)`, add it:

```yaml
columns:
  story_points:
    - Story Points
    - Story point estimate
    - Custom field (Story point estimate)
```

### Supported Logical Columns

| Logical Column | Required | Used On | Purpose |
| --- | --- | --- | --- |
| `jira_key` | Yes | All rows | Unique Jira key. Blank keys are skipped and reported. |
| `issue_id` | No | All rows | Jira numeric issue ID for traceability. |
| `issue_type` | Yes | All rows | Distinguishes initiatives, epics, and child work. |
| `summary` | Recommended | Initiative and epic rows | Project task and summary names. |
| `epic_link` | Required for child completion math | Story/task/bug/sub-task rows | Links child work to the parent epic. |
| `parent` | Required for initiative-mode epics | Epic rows | Initiative parent key. |
| `fix_versions` | Required for fixVersion-mode epics | Epic rows | fixVersion rollup value or values. |
| `story_points` | Required for percent complete | Story/task/bug/sub-task rows | Points used in completion math. |
| `logged_hours` | Optional | Story/task/bug/sub-task rows | Hours summed to epic rows and summary rollups. Unparsed nonblank values are reported and counted as zero. |
| `status` | Required for percent complete | Epic and child rows | Determines done/in-progress status. |
| `resolution` | No | All rows | Captured for traceability and future workflow decisions. |
| `target_start` | Recommended | Epic rows | Jira target start date. |
| `target_end` | Recommended | Epic rows | Jira target end date and schedule review. |
| `predecessors` | Optional | Epic rows | Jira links meaning this epic is blocked by another epic. |
| `successors` | Optional | Epic rows | Jira links meaning this epic blocks another epic. |

### Multi-Value Columns

j2p splits multi-value fields on commas, semicolons, and line breaks.

This applies to:

- `fix_versions`
- `predecessors`
- `successors`

## `issue_types`

Most users do not need this section because defaults cover common Jira exports.

Defaults:

```yaml
issue_types:
  initiative:
    - Initiative
  epic:
    - Epic
  story:
    - Story
    - Task
    - Sub-task
    - Bug
```

Use this only if Jira exports different issue type names. For example:

```yaml
issue_types:
  initiative:
    - Initiative
    - Capability
  epic:
    - Epic
  story:
    - Story
    - Task
    - Bug
```

## `behavior`

Controls operational behavior.

```yaml
behavior:
  unknown_prefix: exclude
  hide_completed_epics: true
  write_state_on_validate: false
```

| Field | Default | Purpose |
| --- | --- | --- |
| `unknown_prefix` | `exclude` | Unknown Jira prefixes are excluded and reported. |
| `hide_completed_epics` | `true` | Completed epics are marked inactive and Gantt bars are hidden when Microsoft Project permits it. |
| `write_state_on_validate` | `false` | `validate` writes persistent state only when this is true or `--write-state` is passed. |

Current guardrail:

- `unknown_prefix` should remain `exclude` for product use.
- Completed epics remain in the sandbox and reports even when their Gantt bars are hidden.

## `metrics`

Controls derived calculations.

```yaml
metrics:
  hours_per_story_point: 8
```

| Field | Default | Purpose |
| --- | --- | --- |
| `hours_per_story_point` | `8` | Converts completed story points to expected completed hours for `Hours Accuracy %`. |

## `review_table`

Controls which columns appear in the Microsoft Project `j2p Review` table.

Default:

```yaml
review_table:
  exposed_columns:
    - jira_key
    - summary
    - rollup_key
    - resource_group
    - dependency_review
    - jira_status
    - start
    - finish
    - percent_complete
    - row_role
    - fix_version
    - predecessors
  include_audit_columns: false
```

The default table is intentionally manager-friendly. It hides internal matching keys, rollup mode, Jira key prefix, Jira issue type, Jira target dates, story point detail fields, logged-hours detail fields, in-planning flags, and other flag-style review indicators. Those values are still written into the Project file and included in the HTML/CSV reports.

Use `all` when an administrator wants every standard j2p field visible in Project:

```yaml
review_table:
  exposed_columns: all
  include_audit_columns: true
```

To make the Project review table even smaller, provide a shorter list:

```yaml
review_table:
  exposed_columns:
    - jira_key
    - summary
    - resource_group
    - dependency_review
    - finish
    - percent_complete
    - predecessors
  include_audit_columns: false
```

| Field | Default | Purpose |
| --- | --- | --- |
| `exposed_columns` | Manager-friendly list | Standard columns to show in the `j2p Review` table. Use `all` or a YAML list. |
| `include_audit_columns` | `false` | When `true`, automatically adds changed/review fields for the run even if they were not listed in `exposed_columns`. Keep this `false` for manager-facing sandboxes. |

Supported friendly names for `exposed_columns`:

| Name | Shows |
| --- | --- |
| `name` or `summary` | Project task name. |
| `jira_key` | Jira key custom field. |
| `j2p_key` | Stable j2p schedule row key. |
| `jira_issue_type` | Jira issue type. |
| `rollup_mode` | Initiative or fixVersion mode. |
| `rollup_key` | Initiative key or fixVersion value. |
| `jira_key_prefix` | Jira project key prefix. |
| `resource_group` | Native Project Resource Group. |
| `dependency_review` | Human-readable dependency review notes. |
| `jira_status` or `status` | Jira status. |
| `start` | Project scheduled start. |
| `finish` | Project scheduled finish. |
| `jira_target_start` or `target_start` | Jira target start. |
| `jira_target_end` or `target_end` | Jira target end. |
| `percent_complete` | Native Project `% Complete`. |
| `total_story_points` | Rolled-up total story points. |
| `completed_story_points` | Rolled-up completed story points. |
| `logged_hours` | Rolled-up logged hours. |
| `hours_accuracy_percent` | Logged-hours accuracy against completed story points. |
| `in_planning` | In-planning marker. |
| `unmatched_project_task` | Existing Project task missing from Jira plan. |
| `dependency_review_needed` | Dependency-review-needed flag. |
| `row_role` | Scheduled, Primary, Reference, or Split. |
| `fix_version` | Jira fixVersion represented by the row. |
| `drives_schedule` | Whether the row drives schedule logic. |
| `primary_schedule_key` | Primary row for reference/split rows. |
| `predecessors` | Native Project predecessors. |

You may also list raw Project field IDs such as `Text1`, `Number3`, `Date2`, `Flag3`, or native field names such as `Finish` and `Predecessors`.

## `project_fields`

Maps j2p values to Microsoft Project task custom fields.

`Resource Group` is not listed here because it is a native Microsoft Project field. Use the top-level `resource_groups` section to map Jira key prefixes to the value j2p shows in the native `Resource Group` column through Project resource assignments.

Most users should keep the defaults unless their Project template already uses these fields.

```yaml
project_fields:
  jira_key: Text1
  j2p_key: Text10
  total_story_points: Number1
  logged_hours: Number3
  hours_accuracy_percent: Number4
  in_planning: Flag1
  jira_target_start: Date1
```

Supported fields:

| j2p Field | Default Project Field | Purpose |
| --- | --- | --- |
| `jira_key` | `Text1` | Original Jira key. |
| `jira_issue_id` | `Text2` | Jira issue ID. |
| `jira_issue_type` | `Text3` | Jira issue type. |
| `rollup_mode` | `Text4` | `initiative` or `fixVersion`. |
| `rollup_key` | `Text5` | Initiative key or fixVersion string. |
| `jira_key_prefix` | `Text7` | Jira key prefix. |
| `dependency_review` | `Text8` | Human-readable dependency review note. |
| `jira_status` | `Text9` | Jira status. |
| `j2p_key` | `Text10` | Stable schedule row key. |
| `row_role` | `Text11` | `Scheduled`, `Primary`, `Reference`, or `Split`. |
| `fix_version` | `Text12` | FixVersion represented by this Project row. |
| `primary_schedule_key` | `Text13` | Primary row for a reference/split group. |
| `total_story_points` | `Number1` | Total child story/task points. |
| `completed_story_points` | `Number2` | Completed child story/task points. |
| `logged_hours` | `Number3` | Logged hours summed from child story/task rows. |
| `hours_accuracy_percent` | `Number4` | Completed logged hours divided by expected hours from completed story points. |
| `in_planning` | `Flag1` | Marks included epics with no pointed child work. |
| `unmatched_project_task` | `Flag2` | Marks Project tasks not matched to the current Jira plan. |
| `dependency_review_needed` | `Flag3` | Marks rows needing dependency review. |
| `drives_schedule` | `Flag4` | Indicates whether the row drives Project schedule logic. |
| `jira_target_start` | `Date1` | Jira target start. |
| `jira_target_end` | `Date2` | Jira target end. |

For developer-level detail on what each Project field enables in matching, rollups, dependency review, coloring, and reporting, see `docs/project-fields.md`.

## `project_field_names`

Controls the display names j2p applies to Microsoft Project custom fields.

Most users can omit this section. Defaults are already written in user-friendly language, such as:

```yaml
project_field_names:
  jira_key: Jira Key
  j2p_key: j2p Unique Key
  row_role: j2p Row Role
  drives_schedule: Drives Schedule
```

Use this section only if your organization needs different column names in the sandbox.

## `colors`

Controls sandbox cell colors.

Most users can omit this section.

Default values:

```yaml
colors:
  changed_cell: "#C6EFCE"
  cascade_root: "#FFC7CE"
  review_needed: "#FFEB9C"
  dependency_review: "#BDD7EE"
  in_planning: "#D9EAD3"
```

Default meanings:

| Color Key | Meaning |
| --- | --- |
| `changed_cell` | Green changed cells. |
| `cascade_root` | Red critical-path root finish-date driver. |
| `review_needed` | Yellow/amber review cells. |
| `dependency_review` | Blue dependency review cells. |
| `in_planning` | Gray/green-gray in-planning cells. |

## Configuration Checklist

Before running with a new team's Jira export:

1. Confirm each Jira key prefix appears in `resource_groups`.
2. Confirm each prefix has the right `rollup_modes` value.
3. Confirm `done_statuses` matches the team's workflow.
4. Confirm `columns` matches the actual CSV headers.
5. Confirm fixVersion teams use the intended `multi_fixversion_policy`.
6. Run `validate`.
7. Review `CSV Column Mapping Used` in `Manager-Review-Report.html`.
8. Review `FIELD_MAPPING.md`.
9. Fix YAML or Jira data-quality issues before running `update`.
