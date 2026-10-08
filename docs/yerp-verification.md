# Project-specific export verification — October 8, 2026

The current configuration reads all nine October 7 exports and uses fixVersion
rollups for every project. Its static accepted list contains the 62 exact names
observed in at least two Jira projects, across all source issue types. The other
206 version names are project-specific and do not create summary memberships.
`PI17` and `PI 17` remain separate. Each epic or remaining initiative estimate
uses its own accepted versions; parent and child links remain intact.

| Item | Current count/value |
| --- | ---: |
| CSV files / unique Jira issues | 9 / 11,522 |
| Epics read / included | 2,014 / 1,708 |
| Planned rows / active reference copies | 3,334 / 1,530 |
| Initiative remainder estimates / rows including copies | 96 / 220 |
| Shared fixVersion summaries | 62 |
| Included epic dependency links | 169 |
| Included child work items | 4,187 |
| Counted total / completed points | 20,522.0 / 2,021.9 |
| Epic estimates / initiative remainder points | 15,452.2 / 5,069.8 |
| Included logged hours | 12,171.51 |
| Epics excluded solely by the accepted list | 156 |

The independent CSV/Decimal oracle verifies every included epic and all 62
summaries. It also verifies the accepted list against cross-project source
membership, shared summaries across teams, duplicate input handling, and the
source CSV hashes. Changing `SSWSW-11866` under `SSWSW-10804` from three open
points to five done points in memory changes four accepted memberships and
their summaries. Valid unchanged updates still perform zero task-field writes.
Creation checkpoints are at rows 834, 1,667, 2,501, and 3,334.

Original Story Points are read separately from current child points. The oracle
checks every parent's selected estimate and every initiative remainder against
independent Decimal calculations over the raw CSVs. Example epic selections are
`SSWCYBER-3666` (To Do: max of 10 original and 0.5 child points = 10),
`SSWCYBER-3606` (In Progress: 0.5 child points), and `SSWIF-4752` (no children:
4 original points). `SSWCYBER-3597` contributes only 85.8 remaining initiative
points after deducting 64.2 child-epic points from its original 150. Estimates
never increase delivered/completed points. CSV source files remain unchanged.

The old `SSWCYBER-3219` fractional-completion case has no accepted version; its
six-percent regression is retained using an in-memory initiative configuration.
`SSWSW-10467` retains its FST primary and PI 17/PI 18 copies. The former TCAP GUI
memberships are not in the accepted program list. Both manual-date validity and
final primary-date protection remain covered.

Use a fresh Project baseline when changing to this outline. Ordinary updates
preserve unmatched legacy rows and do not remove old headers or deactivate
unmatched primary tasks. Portable Project fakes do not certify native Microsoft
Project scheduling or real MPP persistence; Windows acceptance is still needed.

## Historical September 19 verification

The following records the previous data and configuration, not current totals.

These checks read all nine September 18 CSV exports in `yerp/` with
`yerp/ssn-812-config.yaml`. They extend the earlier synthetic configuration and
Project adapter tests with the actual project data. Changes to later-export
values are made only in memory. SHA-256 checks confirm source CSVs are unchanged.

## Data covered

| Item | Observed count/value |
| --- | ---: |
| CSV files / unique Jira issues | 9 / 11,871 |
| Epics read / included | 2,237 / 1,521 |
| Planned epic rows / reference rows | 2,016 / 495 |
| Rollup summaries | 164 |
| Included epic dependency links | 87 |
| Counted total / completed points | 3,616.7 / 1,827.8 |
| All-rollup completion total / completed points | 8,784.6 / 4,366 |
| Included logged hours | 11,705.17 |

The all-rollup completion totals intentionally repeat credit for each applicable
fixVersion. They are not unique project totals.

## Checks performed

`tests/test_yerp_exports.py` reads the original exports independently with
Python's CSV reader and Decimal arithmetic. It checks every planned epic's child
points/time and all 164 rollups, including reference completion credit, per-file
coverage, and cross-file initiative links. Importing all nine files twice in
reverse order counts 11,871 matching duplicate issues once and retains the same
plan and warning contents. Completed-fixVersion audit rows choose a stable Jira
issue key so input-file order does not change the representative issue. An
in-memory conflicting duplicate is rejected with both source locations.

The real child task `SSWSW-11845`, under `SSWSW-10804`, is changed in memory from
3 points / To Do to 5 points / Done. Counted project totals change by +2 total
points and +5 completed points; each of its six fixVersions receives +2/+5
completion credit. Unaffected epic and summary values remain identical.

`tests/test_yerp_project_updates.py` populates a portable Project object-model
fake with all 2,016 actual planned rows, 164 rollups, resources, outline parents,
and all 87 dependencies. It checks:

- An identical update issues zero task-field assignments, retains its complete
  comparison audit, and performs over 52,000 field readback checks.
- The real child change writes only six affected epic rows and six affected
  rollups. Complete CSV outputs still retain all 2,016 rows and 164 rollups, every
  audit item, and manager/resource-group HTML reports.
- `SSWCYBER-3219` retains its actual 3.2 total / 0.2 completed points and 6% custom
  completion when native duration completion is simulated as recalculating to 0%.
- Completed reference copies stay active without assignments; native completion is not written to
  them. There are 80 completed reference rows in these exports.
- Simulated downstream date changes on real dependency rows are still reported
  when those rows received no direct field assignments.
- Deliberately corrupting an unchanged row's custom completion still fails full
  verification; skipping writes does not skip readback checks.

For the full-size portable Project run, the identical update recorded **0 task
field writes / 65,729 skipped write decisions**. The changed-child scenario
recorded **56 writes / 65,673 skips**, confined to six epic rows and six rollups.
Both performed **52,507 field readback checks**, plus the required activation,
outline, resource, and relationship checks. These are fake-object operation
counts, not measured Windows runtime savings.

`SSWSW-1039` is absent from these exports, so these checks do not claim actual-data
coverage for that specific issue. The earlier portable activation regressions
remain in the general test suite.

## Expanded configuration and source limitations

The configuration now maps all nine exported project prefixes. SSWIF, SSWNET,
and SSWTEST use `fixVersion` rollups with resource groups `Interfaces`,
`Network Architecture`, and `Test`, respectively. Existing prefix mappings are
unchanged. The new group names follow the exported project names/descriptions;
`Interfaces` reflects the SSWIF description of an ICD leads' scrum space.

The new prefixes use fixVersion because it covers substantially more epics in
these exports than Initiative links do. This is a per-prefix mode choice, not an
automatic fallback between the two hierarchies:

| Prefix | Exported epics | Included by fixVersion | Expanded rows | No fixVersion, valid Initiative | Neither usable rollup |
| --- | ---: | ---: | ---: | ---: | ---: |
| SSWIF | 318 | 254 | 278 | 8 | 56 |
| SSWNET | 81 | 72 | 74 | 7 | 2 |
| SSWTEST | 546 | 476 | 631 | 29 | 41 |

The expansion adds 802 unique epics, 983 planned rows, and 65 rollup summaries.
It removes exclusions caused by unmapped prefixes. It does not imply that every
exported epic can be scheduled: 143 epics in the new prefixes lack a fixVersion,
including 44 with a valid Initiative-only link and 99 with neither usable
rollup. Another 573 epics in the previously configured prefixes lack their
configured rollup, leaving 716 excluded epics in total. No hierarchy is inferred
for missing relationships and no source CSV is edited.

Of 9,441 child issues, 3,820 contribute to included epics. The other 5,621 have
no Epic Link (996), reference an absent epic (1,776), or belong to an excluded
epic (2,849).

There are 175 missing/unsupported dependency targets before historical warning
suppression. The configuration suppresses 1,287 historical Warning/Review items
and hides four stale completed fixVersion rollups, containing five epic rows,
in manager HTML. These configured filters are preserved, so raw source counts
can exceed visible report warnings.

## Reproduce and interpret

The September 19 expanded-configuration run passed all **233 tests**, including
12 actual-export tests, two release-audit ordering regressions, and nine
calculation-checkpoint/mode-recovery tests. The yerp no-op update also verifies
quarter checkpoints at rows 504, 1,008, 1,512, and 2,016. The full smoke
command also passed compilation, generated-fixture drift checks, and report
validation. A separate CLI validation of all nine exports generated 2,016 epic
rows and 164 rollups, with the documented source warnings retained. All nine
source CSV SHA-256 hashes remained unchanged.

From the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_yerp*.py'
python3 scripts/smoke_tests.py
```

On Windows, replace `python3` with the installed Python launcher, such as
`py -3.14`. The project-specific suites explicitly skip when the exports are
absent. Their expected counts describe this particular export snapshot and must
be reviewed when replacing it with a different dataset.

These are actual-input tests with a simulated Project boundary. They do not
measure Windows runtime, execute Microsoft's scheduling engine, validate live
Project edition restrictions, or prove persistence in a real `.mpp`. The
save/reopen and visual checks in [windows-acceptance.md](windows-acceptance.md)
still need to run on Windows with Microsoft Project Professional.
