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
