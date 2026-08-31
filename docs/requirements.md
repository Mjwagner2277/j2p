# j2p Requirements And Design Decisions

## Source Of Truth

The main Microsoft Project `.mpp` is the source of truth. j2p does not edit it directly.

For each update run, j2p copies the main `.mpp` to a timestamped sandbox file and applies Jira updates to the sandbox.

## Jira Scope

j2p reads a project-wide Jira CSV.

Included as Project work rows:

- Epic

Used for calculations only:

- Story
- Task
- Sub-task
- Bug

Used as summary rollup parents in initiative mode:

- Initiative

## Rollup Modes

j2p supports a default rollup mode and optional per-prefix overrides. This lets one project-wide Jira CSV include teams that use initiative rollups and teams that use fixVersion rollups.

Example:

```yaml
rollup_mode: initiative

rollup_modes:
  TEAM: initiative
  PLAT: fixVersion
  DATA: initiative
```

Initiative mode:

```text
Initiative summary task
  Epic task
```

fixVersion mode:

```text
FixVersion summary task
  Epic task
```

For a prefix using initiative mode, each epic must have a parent initiative key and that initiative must appear in the Jira CSV.

For a prefix using fixVersion mode, each epic must have exactly one fixVersion by default. Missing or multiple fixVersions are excluded and reported unless configuration is changed later.

## Epic Identity

Epics map to Project tasks by Jira key, such as `TEAM-123`.

Initiatives map by Jira key.

fixVersion summary tasks map by exact fixVersion string.

## Completion Calculation

Epic `% Complete` is calculated from child story/task points:

```text
completed child story points / total child story points
```

A child story/task counts as completed when its Jira status is in the configured `done_statuses` list.

If an epic has no pointed child work, it is marked `In Planning`, excluded from percent-complete math, and reported for review.

Summary task `% Complete` is manually calculated from child epics using weighted story points.

## Dependencies

Only epic-level dependencies are written to Project.

Jira `blocked by` / `is blocked by` fields become Project predecessors.

Jira `blocks` fields become Project successors.

Dependencies are written as Finish-to-Start Project predecessors. Missing dependency targets are not created as placeholder tasks; they are marked in the Dependency Review field and listed in the manager report.

Circular dependencies are skipped and reported so the sandbox can still be generated.

## Dates

Jira `Target start` and `Target end` map into Project custom date fields and are used to update sandbox schedule dates.

The sandbox is auto-scheduled. If Project auto-scheduling shifts finish dates:

- the first/root detected critical-path finish shift is colored red
- downstream shifted finish dates are colored green
- any Project scheduled finish that does not match Jira `Target end` is reported

## Review Colors

| Color | Meaning |
| --- | --- |
| Green | Changed cell |
| Red | First/root critical-path end-date driver; overrides green |
| Yellow/amber | Unmatched or manager review needed |
| Blue | Dependency review marker |
| Gray/green-gray | In planning |

## Completed Epics

Completed epics remain in the sandbox and are reported. Microsoft Project does not expose an Excel-style hidden-row task property through the automation model, so j2p marks completed epics inactive and hides their Gantt bars when possible.

## Resource Groups

Resource Group is mapped from the Jira key prefix.

Example:

```yaml
resource_groups:
  TEAM: Product Delivery
  PLAT: Platform Engineering
  DATA: Data Engineering
```

Epics with unmapped prefixes are excluded and reported.

## Default Project Custom Fields

| j2p Value | Project Field |
| --- | --- |
| Jira Key | Text1 |
| Jira Issue ID | Text2 |
| Jira Issue Type | Text3 |
| Rollup Mode | Text4 |
| Rollup Key | Text5 |
| Resource Group | Text6 |
| Jira Key Prefix | Text7 |
| Dependency Review | Text8 |
| Jira Status | Text9 |
| Total Story Points | Number1 |
| Completed Story Points | Number2 |
| In Planning | Flag1 |
| Unmatched Project Task | Flag2 |
| Dependency Review Needed | Flag3 |
| Jira Target Start | Date1 |
| Jira Target End | Date2 |

The initial create/update process renames these fields in the sandbox for easier review.
