# Large Jira CSV Export For j2p

This guide explains how to create a j2p-ready Jira CSV when the Jira result set is larger than the browser export limit. It is written for non-technical users working in Jira Data Center or Jira Server, the locally hosted enterprise versions of Jira.

The preferred workflow is browser-based Jira export in several smaller batches. It is slower than an API export, but it is easier to teach, easier to repeat, and does not require users to know REST APIs, custom field IDs, service accounts, or scripts.

## What You Are Trying To Produce

At the end of this process, you should have one consolidated CSV file:

```text
jira-project-wide-export.csv
```

That file should contain the full project-wide issue set needed by j2p:

- initiatives, when your teams roll epics up to initiatives
- epics
- child stories/tasks/bugs used for percent complete and hours calculations
- completed issues, when they are needed for completion math or stale fixVersion decisions

Do not export only changed issues. j2p needs the full current project picture.

## Fields To Include

Before exporting, configure the Jira issue navigator columns so they include the fields j2p needs. Prefer `CSV (Current fields)` or the equivalent option in your Jira instance. Avoid `CSV (All fields)` unless a Jira admin tells you to use it.

At minimum, include these columns or your configured equivalents:

| j2p Need | Typical Jira Column |
| --- | --- |
| Stable issue key | `Issue key` |
| Jira internal issue id | `Issue id` |
| Type filtering | `Issue Type` |
| Display name | `Summary` |
| Epic child mapping | `Epic Link` or parent field used by your Jira instance |
| Initiative mapping | `Parent` |
| fixVersion mapping | `Fix versions` |
| Progress math | `Story Points` |
| Hours math | Logged hours/worklog field used by your export |
| Status/done detection | `Status` |
| Completion date for stale fixVersion suppression | `Resolved` |
| Date planning | `Target start`, `Target end` |
| Dependencies | Link columns for blocks/is blocked by |

Use the exact column names from your YAML config when they differ from these examples.

## Browser Workflow Overview

For large projects, the browser workflow is:

1. Create one master Jira search.
2. Confirm the total issue count.
3. Split the search into smaller non-overlapping batches.
4. Export each batch as CSV using the same columns.
5. Combine the batch CSVs into one CSV.
6. Check total row count and duplicate Jira keys.
7. Run `j2p validate`.

The most important rule is that every issue must appear in exactly one batch.

## Step 1: Create The Master Jira Search

In Jira:

1. Go to the issue search page or issue navigator.
2. Switch to advanced/JQL search if available.
3. Enter the full project query.
4. Add the columns listed in `Fields To Include`.
5. Save the filter if your organization allows it.

Example initiative-based project query:

```jql
project in (CORE, WEB, DATA, PLAT, OPS)
AND issuetype in (Initiative, Epic, Story, Task, Bug)
ORDER BY key ASC
```

Example fixVersion-based project query:

```jql
project in (CORE, WEB, DATA, PLAT, OPS)
AND issuetype in (Epic, Story, Task, Bug)
AND fixVersion is not EMPTY
ORDER BY key ASC
```

Record the total result count shown by Jira. You will use it later to confirm that the batches add back up to the same number.

## Step 2: Choose A Batch Strategy

If the master search is over the export limit, split it into batches. Use a field that will not overlap.

Recommended split options:

| Split Method | Best When | Notes |
| --- | --- | --- |
| By Jira project key | Work is naturally separated by teams or products. | Easiest to understand. Works well when each project key is under the limit. |
| By created date range | One project key still has too many issues. | Most reliable date-based split because `created` does not change. |
| By issue type | Initiatives/epics/stories are large but uneven groups. | Useful as a second split, not always enough by itself. |
| By fixVersion | fixVersion teams already organize work by release. | Watch for epics with multiple fixVersions. |

Avoid splitting by status if possible. Status changes over time and makes repeatability harder.

## Step 3: Export Batches By Project Key

Use this first if each Jira key prefix is under the export limit.

Batch 1:

```jql
project = CORE
AND issuetype in (Initiative, Epic, Story, Task, Bug)
ORDER BY key ASC
```

Batch 2:

```jql
project = WEB
AND issuetype in (Initiative, Epic, Story, Task, Bug)
ORDER BY key ASC
```

Batch 3:

```jql
project = DATA
AND issuetype in (Initiative, Epic, Story, Task, Bug)
ORDER BY key ASC
```

For each batch:

1. Run the JQL.
2. Confirm the result count is under your Jira export limit.
3. Confirm the visible columns are the j2p-required columns.
4. Export `CSV (Current fields)` or your instance's equivalent current-fields CSV option.
5. Save the file with a clear name, such as:

```text
batch-01-CORE.csv
batch-02-WEB.csv
batch-03-DATA.csv
```

After exporting all batches, add the result counts together. The sum should equal the master search count.

## Step 4: Split Large Project Keys By Created Date

If a single project key still has too many issues, split that project by created date.

Example for `CORE`:

```jql
project = CORE
AND issuetype in (Initiative, Epic, Story, Task, Bug)
AND created < "2024-01-01"
ORDER BY key ASC
```

```jql
project = CORE
AND issuetype in (Initiative, Epic, Story, Task, Bug)
AND created >= "2024-01-01"
AND created < "2025-01-01"
ORDER BY key ASC
```

```jql
project = CORE
AND issuetype in (Initiative, Epic, Story, Task, Bug)
AND created >= "2025-01-01"
ORDER BY key ASC
```

Use half-open ranges:

```text
created >= start date
created < next start date
```

This avoids overlap on boundary dates.

If a date range is still too large, split it into smaller ranges such as quarters or months.

## Step 5: Export The Same Columns Every Time

Every batch must use the same columns in the same order.

Before each export:

1. Confirm the Jira issue navigator columns have not changed.
2. Export current fields, not all fields.
3. Save the CSV without editing it.

Do not mix export types. For example, do not export some batches as current fields and others as all fields.

## Step 6: Keep The Batch CSVs Separate

j2p accepts multiple export files directly. Keep each file's header row. You no
longer need to combine the files in Excel. Supply all batches from the same
project snapshot together so child work, rollups, and dependencies can be resolved
across files. Do not combine a baseline export with a later sprint export.

Each batch is decoded separately, so supported encodings can differ. Columns are
mapped by their headers and may appear in different orders. Each file must contain
the required mapped columns; exporting the same current fields each time is still
the simplest workflow.

## Step 7: Check Batch Coverage

Compare the files against your batch tracking table. Include all initiatives,
epics, child story/task rows, and completed work needed for the project.

- Repeated Jira keys with identical parsed issue values are counted once and
  recorded as `DuplicateCsvIssueSkipped` in the audit CSV.
- Conflicting parsed values for the same key stop the run and identify both source
  files and row numbers. Re-export or correct the overlapping batches; file order
  does not choose a winner.
- j2p cannot detect issues missing from every supplied file. Check batch counts
  against Jira and review missing-parent/dependency warnings.
- Audit CSVs include `source_file` alongside `source_row`.

## Step 8: Run j2p Validate

For a normal sprint review:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\jira-batch-01.csv .\jira-batch-02.csv .\jira-batch-03.csv `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.10" `
  --compare-state
```

For the first accepted baseline export:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\jira-batch-01.csv .\jira-batch-02.csv .\jira-batch-03.csv `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --run-id baseline `
  --write-state
```

Then open:

```text
review-output\Customer-Portal-Program\sprints\Sprint-24.10\runs\<run-folder>\reports\html\Manager-Review-Report.html
```

## Batch Tracking Template

Use this table while exporting:

| Batch File | JQL Split | Jira Count | Exported? | Notes |
| --- | --- | ---: | --- | --- |
| `batch-01-CORE.csv` | `project = CORE` |  |  |  |
| `batch-02-WEB.csv` | `project = WEB` |  |  |  |
| `batch-03-DATA-2024.csv` | `project = DATA AND created >= "2024-01-01" AND created < "2025-01-01"` |  |  |  |
| Total | Must equal master search count |  |  |  |

Keep this tracking table with the exported files so reviewers can confirm how the full CSV was built.

## When To Ask For Help

Ask a Jira admin or technical teammate for help when:

- A batch cannot be made small enough for browser export.
- Excel cannot open or save the combined CSV.
- The export options do not include current-fields CSV.
- Required fields are not available as issue navigator columns.
- Logged hours/worklog data is not available in the browser export.
- The duplicate key check finds duplicates and the overlap is not obvious.
- The combined row count does not match the expected total.

## Admin/Advanced Fallback: REST API Pagination

For very large or recurring enterprise exports, a Jira admin may prefer REST API pagination. This is more reliable and easier to automate, but it is not the preferred workflow for non-technical users.

The Jira REST API search endpoint supports paginated JQL search with `startAt`, `maxResults`, and selected `fields`.

Admin users can:

1. Query `/rest/api/2/search`.
2. Select only the fields j2p needs.
3. Page through results with `startAt`.
4. Write one consolidated UTF-8 CSV.
5. Run duplicate and row-count checks.

Use this path only when the browser workflow is too slow, too large, or blocked by Jira limits.

## Final Hand-Off Checklist

Before giving the CSV to a schedule owner:

- The CSV is saved as UTF-8 when possible.
- The row count matches the Jira master search total.
- There are no duplicate `Issue key` values.
- Epics are present.
- Child stories/tasks are present.
- Rollup fields are present for included epics.
- `Resolved` is present when stale completed fixVersion suppression is used.
- Dependency link columns are populated when Jira blockers are used.
- The CSV has the exact column headers configured in `j2p.yaml`.
