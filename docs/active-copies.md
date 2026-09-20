# Active fixVersion copies

The `reference` policy now creates one authoritative primary and active copies
under the other fixVersions. Stable Jira and j2p keys preserve identity when rows
move. The existing policy name and Reference row-role label remain compatible
with old configurations and state files. `split` continues to schedule each row
independently; initiative rows remain ordinary primary tasks.

| Behavior | Primary | Copy | Summary |
| --- | --- | --- | --- |
| Active | Yes | Yes | Yes |
| Task mode | Auto | Manual | Auto |
| Native Start/Finish | Project schedules from inputs | Follows final primary exactly | Project rolls up active children |
| Resource assignments | Existing managed ownership rules | None | None added by j2p |
| Dependency links | Jira graph | None, in either direction | None added by j2p |
| Unique counted points | Counts once | Excluded | Sum driving rows |
| FixVersion completion | Full member credit | Full member credit | All members, once per version |
| Progress shown by default | Story Point Completion % | Story Point Completion % | Story Point Completion % |

Manual task mode makes copies date followers without introducing constraints,
resource workload, or dependency paths into primary work. It does not make their
summary headers manual. Native duration-based % Complete on copies is not seeded:
that would create duplicate actuals. Native task counts/durations include copies;
use j2p's counted story-point totals for unique scope. Do not sum completion totals
across versions for a project or portfolio total.

## Scheduling and verification

1. Write primary inputs, copy metadata, hierarchy, and dependencies. Retain row
   quarter calculation checkpoints. Copies receive no native Jira date seeds.
2. Finish primary scheduling and capture native Start/Finish, including times.
3. Compare and write only changed copy endpoints. Never write a summary or primary
   date during this pass. Existing custom Date3/Date4 values are left untouched.
4. Recalculate native summaries when copy dates changed. Verify after restoring
   the original application calculation mode: all copies match their primaries,
   headers span all planned members, and every captured primary stays unchanged.
5. Repeat data verification after saving, before close, and after reopen. Any
   mismatch stops publication. Verification does not repair or hide drift.

An unchanged update writes no copy dates. Copies refresh on each j2p run; editing
Project directly does not create a live alias between task rows. Manager reports
retain their three-section structure and full detailed CSV evidence.

Microsoft documents that [automatic summary durations span their children,
including manual children](https://download.microsoft.com/download/9/9/8/998d6ff5-3425-40ff-a3f3-98a6b3af751c/AF102338668_en-us_project2010stepbystepchapter7.pdf).
The [TaskDependencies collection includes both predecessors and successors](https://learn.microsoft.com/en-us/office/vba/api/project.task.taskdependencies),
so requiring an empty collection checks both directions. Native [summary Start
is read-only](https://learn.microsoft.com/en-us/office/vba/api/project.task.start);
j2p lets Project calculate it.

## Updating an older baseline

Run the normal update against the previous `.mpp`; j2p still works on a sandbox.
Old reference identities are reused. j2p removes only its owned resource
assignments and clears redundant estimated Work (after confirming zero actuals) before activating copies, switches copies to manual task mode, and
restores summary headers to automatic mode before primary scheduling. Human
assignments, unexpected copy links, or copy actuals stop the run without erasing
those values. Removed copy memberships remain flagged and inactive for review.

The default review table and Gantt Summary bars return to native Start/Finish.
Old `schedule_start`/`schedule_finish` logical table entries now alias those native
columns. Their old custom-field mappings remain accepted but are neither renamed
nor written. Use `completion_percent` for the progress column. The yerp config
already selects these columns.

Source CSVs are not changed. Matching overlapping Jira rows are deduplicated
before membership expansion; conflicting duplicates still fail input validation.
Partial exports are not automatically a complete historical issue store.

## Validation boundary

Portable tests cover active-copy migration, isolation, native rollup contracts,
unchanged writes, corrupted readback, and the real nine-file yerp plan, including
SSWSW-10464/FST and issues with many memberships. They use Project stand-ins.
Real Windows Project calculation, rendering, and `.mpp` persistence require the
[Windows acceptance run](windows-acceptance.md#active-copy-upgrade-acceptance).
