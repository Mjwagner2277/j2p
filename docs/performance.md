# Large export performance

The cascade visual tree displays each changed schedule row once. When several
paths reach the same row, subsequent paths show a shared-issue reference.
Branches still sort by the number of unique changed downstream rows, largest
first. Counts include descendants that are not expanded in the visual tree.

Each report limits its visual tree to **500 cards/references** and **32 levels**.
A visible notice identifies shortened trees. These limits do not remove audit
data: the complete Schedule Cascade Detail table, `audit-detail.csv`, and planned
epic/dependency CSVs remain available. Resource-group reports retain the full
detail for branches starting in that group, including downstream rows belonging
to other groups.

Graph analysis caches unique descendant counts using a reverse topological pass
and integer bitsets. Presentation uses an iterative traversal. Long chains do
not consume Python recursion depth, and shared descendants do not produce an
exponential number of cards. The graph cache is scoped to one rendered report.

## Reproduce the benchmark

From the repository root on Windows:

```powershell
py -3.14 .\scripts\benchmark_large_exports.py --output .\review-output\benchmark.json
```

On macOS or Linux:

```bash
python3 scripts/benchmark_large_exports.py --output review-output/benchmark.json
```

The default sizes are 5,000 and 10,000 issues. Use `--sizes 1000 5000` for a
smaller run. Input generation is deterministic and uses temporary files removed
afterward. The benchmark never opens Microsoft Project.

The wide-export scenario parses a CSV with 107 columns and one epic per row,
builds a plan, and writes the full report bundle. Its 100 unused custom columns
model an all-fields Jira export. Each graph scenario measures cascade analysis
and HTML rendering for an existing synthetic plan: a long chain or two nodes
per layer fully joined to the next layer. The latter has a linear edge count
and an exponential number of possible paths.

## Recorded results

One local run on September 18, 2026 used Python 3.9.6 on macOS 26.6.2 ARM64.
Timings include `tracemalloc` overhead. Peak memory is traced Python allocation,
not total process memory; graph-plan/input generation is outside the timed
region. Results are measurements, not Windows performance guarantees.

| Scenario | Issues | Seconds | Peak Python MiB | HTML bytes | Visual cards / references |
| --- | ---: | ---: | ---: | ---: | ---: |
| Wide export + complete reports | 5,000 | 7.087 | 75.99 | 19,769,431 | — |
| Joined cascade | 5,000 | 0.101 | 8.02 | 871,826 | 64 / 62 |
| Chain cascade | 5,000 | 0.091 | 7.55 | 789,719 | 32 / 0 |
| Wide export + complete reports | 10,000 | 13.954 | 150.46 | 39,559,443 | — |
| Joined cascade | 10,000 | 0.242 | 21.40 | 1,716,828 | 64 / 62 |
| Chain cascade | 10,000 | 0.226 | 20.48 | 1,569,721 | 32 / 0 |

Wide-export HTML sizes refer to the overall manager report. Cascade sizes refer
to the cascade section, including its complete detail table. The wide export has
no child stories, so existing review sections repeat many per-epic review items.
It is deliberately more demanding than a 10,000-row export containing mostly
stories. Full manager reports of this size can still take time to open in a
browser; use resource-group reports and per-project-key CSVs for focused review.
The visual-tree limits do not limit the size of the complete detail tables.

Regression tests cover a joined graph that previously produced 131,071 cards
from 33 issues, a 1,500-issue chain, a budget shared across 600 branches, complete
CSV retention, and optional relative manifest links. Run them with:

```bash
python3 -m unittest discover -s tests -p test_report_scaling.py
```

## Measure selective Project updates

Project updates use a transient cache of live managed values to avoid assigning
values that already match. The default `main` report baseline reuses the same
prewrite snapshot, avoiding a separate baseline Project session. The complete
plan, comparison reports, schedule cascade review, recalculation, and save/reopen
verification still run. This is independent of persistent JSON state and still
requires complete current Jira exports.

The manager report's **Report Context** and `run-manifest.json` expose:

- `project_update_writes`: written, skipped, and failed operation counts for
  task fields, resource fields, custom field names, dependency sets, and resource
  assignments. These are decisions/operations, not counts of unique tasks or
  fields; a field may be checked in more than one pass. Resource assignments
  count additions/removals, and dependencies count sets.
- `project_update_seconds`: session open, prewrite read, complete comparison,
  custom field configuration, applying changes, final recalculation, schedule
  review, review formatting, saving, save/reopen verification, and total Project
  update time. Applying changes includes the row-quarter calculation checkpoints
  before dependency writes. The final recalculation has its own timing. Schedule
  review runs inside review formatting after task indexing, reuses that index, and
  reads only Start/Finish. Its time, task indexing, undated-task duration checks,
  candidate preparation, and visible-column resolution are formatting subsets;
  do not add these subsets to the formatting total again. Total includes session
  startup/shutdown but excludes CSV preflight, sandbox copying, and HTML/CSV
  report generation.

Metrics remain run-wide when displayed in filtered resource-group reports.
Skipped date/completion seeds also count as skipped operations when unchanged
Jira inputs mean Project's calculated schedule/progress should be preserved.

To measure on Windows:

1. Create a representative sanitized Project file and retain its complete Jira
   export and configuration.
2. Update that file with the identical export. Use the successfully saved sandbox
   as the source of another identical update to measure a settled schedule.
3. Prepare a separate synthetic export with a small known change. Update a copy
   of the settled sandbox and compare the operation counts and phase timings.
4. Confirm unchanged fields/dependency sets are skipped, the intended values
   survive save/reopen, and full audit, rollup, and cascade details remain present.
   Check a dependency-driven date change on a row with no direct Jira edit.

Retain each report and manifest with the exact inputs. Do not change production
CSV exports just to benchmark. Existing review formatting and Project scheduling
may still dominate elapsed time; fewer writes do not imply a particular speedup.
Portable tests validate decisions and report retention, not Windows COM latency.
Live Windows measurements are required before claiming a runtime improvement.

## Calculate at row-quarter checkpoints

Create and update keep tasks Auto Scheduled but temporarily set Project's
application calculation mode to manual while writing. They explicitly calculate
after approximately 25%, 50%, 75%, and 100% of planned epic rows have been
processed, rounding each threshold upward. Reference rows and unchanged rows
visited by selective updates count toward the denominator; summary rows and
individual field assignments do not. Small plans coalesce repeated thresholds,
and empty plans have no row checkpoints.

For the 2,016-row yerp plan, the checkpoints are rows 504, 1,008, 1,512, and 2,016.
Progress logs identify each checkpoint, and plan statistics record the completed
row numbers as `project_row_calculation_checkpoints`. The 100% row checkpoint
supplies the pre-dependency calculation on updates; creation also retains its
pre-dependency calculation after the initial save. Both paths calculate after
all dependency writes before schedule review, formatting, and saved verification.

The original application calculation setting is restored before final review
and saving, including on write or calculation failure. Reading/setting/restoring
the setting must succeed; failures are reported rather than silently proceeding
with unknown calculation behavior. The calculation setting is distinct from a
task's Auto Scheduled mode, which remains enabled. Native date/progress values
remain live during writes so checkpoint recalculations cannot be hidden by the
selective-update cache. Full reports and save/reopen verification remain enabled.

Checkpoint calculations see a partial schedule. The final calculation after
links are written is the basis for reviewing dependency-driven changes. Windows
acceptance must measure actual scheduling behavior and runtime; portable fakes
do not establish a speed improvement.
