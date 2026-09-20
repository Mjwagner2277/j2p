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

j2p uses Jira key prefixes to choose each team's rollup mode. This lets one project-wide Jira CSV include teams that use initiative rollups and teams that use fixVersion rollups without relying on a global default.

Example:

```yaml
rollup_modes:
  TEAM: initiative
  PLAT: fixVersion
  DATA: initiative

multi_fixversion_policy:
  default: reference
  OPS: split
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

For a prefix using fixVersion mode, each epic must have at least one fixVersion.

If a fixVersion-mode epic has multiple fixVersions, j2p uses `multi_fixversion_policy`. Only two policies are supported:

| Policy | Behavior | Best Use |
| --- | --- | --- |
| `reference` | Default. The first Jira fixVersion becomes the primary scheduled row. Each additional fixVersion gets a non-driving reference row. | The same work should be visible under qualification events, shop deliverables, or other commitments without double-counting work. |
| `split` | Each fixVersion gets a driving Project row with its own stable schedule key. | The team intentionally wants the same Jira epic to drive schedule placement under every listed fixVersion. |

Rollup summaries keep counted story points limited to driving rows. Completion
uses every epic assigned to that version, including references, even in groups
that also contain driving rows. A completed 3-point item shared by two versions
adds 3 completed points to each version's completion calculation, while adding
only 3 to overall counted points under the reference policy. Separate completion
point fields make this distinction visible. These per-version completion totals
must not be summed as a portfolio total.

For fixVersion rollups, j2p can hide stale completed releases from HTML manager reports without a release metadata file. A fixVersion is considered complete when every issue in the CSV that declares that fixVersion has a status in `done_statuses`. If every completed issue has a usable `Resolved` date and the latest `Resolved` date is older than the configured threshold, the rollup is hidden from HTML manager reports while remaining in detailed CSV outputs and the Project sandbox.

## Epic Identity

Epics map to Project tasks by a stable j2p schedule key.

For ordinary epics, the schedule key is the Jira key, such as `TEAM-123`.

For secondary reference or split rows created from additional fixVersions, the schedule key is a stable composite value based on Jira key plus fixVersion. The original Jira key is still written to the `Jira Key` Project field.

Initiatives map by Jira key.

fixVersion summary tasks map by exact fixVersion string.

## Completion Calculation

Epic `% Complete` is calculated from child story/task points:

```text
completed child story points / total child story points
```

A child story/task counts as completed when its Jira status is in the configured `done_statuses` list.

If an epic has no pointed child work, it is marked `In Planning` and excluded from percent-complete math. Inside the immediate planning horizon, it is reported for review. Outside the immediate horizon, it is reported as future planning work because detailed task breakdown is not expected yet.

Summary task `% Complete` is manually calculated from child epics using weighted story points.

Logged hours are calculated separately from percent complete. If the Jira CSV includes a mapped logged-hours column, j2p sums logged hours from child story/task/bug/sub-task rows to the parent epic and then to summary rollups. Logged hours do not change percent complete.

Story Point Ratio is calculated from completed child work only:

```text
completed child story points / (completed child logged hours / hours_per_story_point)
```

The default conversion is 8 hours per story point and is configurable in YAML. With the default, a value of `1.00` means one completed story point per eight completed logged hours.

Manager report project-wide and resource-group Story Point Ratio views must use only active scheduled epic rows. For this purpose, active means the row drives the schedule and has `% Complete` from 1 to 99. Completed, not-started, in-planning, and reference-only rows are excluded from those aggregate views.

## Dependencies

Only epic-level dependencies are written to Project.

Jira `blocked by` / `is blocked by` fields become Project predecessors.

Jira `blocks` fields become Project successors.

Dependencies are written as Finish-to-Start Project predecessors. Missing dependency targets are not created as placeholder tasks; they are marked in the Dependency Review field and listed in the manager report.

Circular dependencies are skipped and reported so the sandbox can still be generated.

## Dates

Jira `Target start` and `Target end` map into Project custom date fields and are used to update sandbox schedule dates. Supported Jira export date values, including date-time values such as `17-SEP-26 12:00 AM`, are normalized to `YYYY-MM-DD` before Microsoft Project automation writes them.

The sandbox is auto-scheduled. During creation and updates, if Project auto-scheduling shifts dates:

- every changed Project Start/Finish cell is colored green, including cascade branch drivers
- the report diagram uses red cards for changed finishes with changed downstream successors and green cards for other changed finishes
- any Project scheduled Start/Finish that does not match the corresponding Jira target date is reported
- the HTML reports include a `Schedule Cascade Review` section that visualizes changed finish dates by dependency branch, orders branches by downstream impact, collapses every branch by default, and includes a collapsible detail table
- each resource-group HTML report includes schedule cascade branches whose starting issue belongs to that resource group

## Review Priorities

Focus Now and Highest Priority Fixes use Jira target dates when prioritizing issue work. Work with neither target date belongs in a collapsed Unscheduled Work section, even when it has dependency impact or errors; Decision Briefing includes an unscheduled grouped-action count. The complete audit and diagnostics remain available.

A usable target start or end follows normal ranking. Grouped actions with dated affected work remain eligible, malformed supplied dates retain date-error review, and report-wide errors remain eligible. Project auto-scheduled dates do not make undated Jira work high priority. Setting `report_review.focus_days` to `0` widens the window for dated unfinished work without promoting undated work.

## Historical Warning Suppression

j2p supports a configurable warning-suppression cutoff for full-history Jira CSV exports.

When `warning_suppression.before` is set:

- matching report/audit items dated before the cutoff can be suppressed
- the default eligible severities are `Warning` and `Review`
- the default date lookup uses Jira `Target end` first and `Target start` second
- teams can map an optional `warning_suppression_date` CSV column for Created, Resolved, or another governance date
- items without a usable suppression date remain visible
- included epics are still parsed and scheduled; suppression only reduces manager-report and audit CSV noise
- the manager report shows the number of suppressed historical items

## Review Colors

| Color | Meaning |
| --- | --- |
| Green | Changed cell |
| Red (report diagram only) | Cascade branch driver card; Project date cells remain green |
| Yellow/amber | Unmatched or manager review needed |
| Light gray | Dependency review marker |
| Gray/green-gray | In planning |

## Completed Epics

Completed scheduled epics remain active in the sandbox with native completion
set to 100%; their story-point percentage remains in the custom completion
field. Inactivation is not used to archive completed work. When configured,
their Gantt bars are hidden where Project permits it. Reference rows are made
inactive before progress writes and use only custom completion fields, avoiding
native actuals on non-driving copies. Existing actuals are never erased to force
inactivation. Summary progress is written only to custom fields, because writing
native summary completion can change child-task actuals.

## Resource Groups

Resource Group is mapped from the Jira key prefix and shown in the native Microsoft Project `Resource Group` field. It does not use a j2p custom text field. Because Project calculates the task-level `Resource Group` field from assigned resources, j2p creates or reuses a Project resource with the same name as the configured group, sets that resource's `Group` value, and assigns it to the epic task.

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
| Jira Key Prefix | Text7 |
| Dependency Review | Text8 |
| Jira Status | Text9 |
| j2p Unique Key | Text10 |
| j2p Row Role | Text11 |
| Jira Fix Version | Text12 |
| Primary Schedule Key | Text13 |
| Total Story Points | Number1 |
| Completed Story Points | Number2 |
| Logged Hours | Number3 |
| Story Point Ratio | Number4 |
| In Planning | Flag1 |
| Unmatched Project Task | Flag2 |
| Dependency Review Needed | Flag3 |
| Drives Schedule | Flag4 |
| Jira Target Start | Date1 |
| Jira Target End | Date2 |

The initial create/update process renames these fields in the sandbox for easier review.
