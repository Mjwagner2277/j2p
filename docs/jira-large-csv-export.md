# Large Jira CSV Export For j2p

This guide explains how to create a j2p-ready Jira CSV when the project is larger than the Jira issue navigator export limit. It is intended for Jira Data Center or Jira Server style deployments, which are the locally hosted enterprise versions of Jira.

## Recommended Approach

For large projects, use the Jira REST API with pagination instead of the browser CSV export.

Why:

- Jira issue navigator CSV exports are commonly capped or slowed down by instance settings.
- Browser exports can time out or produce very large files that are hard to verify.
- REST pagination lets you pull all matching issues in controlled batches.
- You can select only the fields j2p needs instead of exporting every Jira field.
- You can check row counts and duplicate Jira keys before handing the file to j2p.

Use the browser export only for smaller filters or as a quick spot-check.

## What j2p Needs From Jira

The CSV must include the project-wide issue set, not just epics.

j2p includes only initiative/fixVersion rollups and epics in the Project schedule, but it uses child story/task rows to calculate percent complete, logged hours, and Story Point Ratio.

At minimum, export these fields or their configured equivalents:

| j2p Need | Typical Jira Field |
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

## Step 1: Build A Stable JQL Query

Start with the same logical scope every sprint. Do not export only changed issues.

Example for an initiative-based project:

```jql
project in (CORE, WEB, DATA, PLAT, OPS)
AND issuetype in (Initiative, Epic, Story, Task, Bug)
ORDER BY key ASC
```

Example for a fixVersion-based program:

```jql
project in (CORE, WEB, DATA, PLAT, OPS)
AND issuetype in (Epic, Story, Task, Bug)
AND fixVersion is not EMPTY
ORDER BY key ASC
```

Recommended rules:

- Include epics and their child stories/tasks.
- Include initiatives if your teams roll epics up to initiatives.
- Include fixVersions if your teams roll epics up to fixVersions.
- Include done and historical issues if they are still needed for completion or stale release logic.
- Keep `ORDER BY key ASC` so batches are deterministic.

## Step 2: Identify Jira Field IDs

Jira REST API results use field IDs for custom fields, such as `customfield_10016`.

Ask a Jira admin for the field IDs, or query Jira:

```powershell
$baseUrl = "https://jira.example.com"
$credential = Get-Credential

Invoke-RestMethod `
  -Uri "$baseUrl/rest/api/2/field" `
  -Credential $credential `
  -Headers @{ Accept = "application/json" } |
  Select-Object id, name |
  Sort-Object name
```

Record the IDs for fields such as Story Points, Epic Link, Target start, Target end, and any logged-hours field your Jira instance exposes.

## Step 3: Export With REST Pagination

This PowerShell example writes one consolidated CSV. It uses Jira REST API pagination with `startAt` and `maxResults`.

Update the field IDs before running.

```powershell
$baseUrl = "https://jira.example.com"
$credential = Get-Credential
$outFile = ".\jira-project-wide-export.csv"

$jql = @"
project in (CORE, WEB, DATA, PLAT, OPS)
AND issuetype in (Initiative, Epic, Story, Task, Bug)
ORDER BY key ASC
"@

$fields = @(
  "key",
  "id",
  "issuetype",
  "summary",
  "status",
  "resolution",
  "resolutiondate",
  "fixVersions",
  "parent",
  "issuelinks",
  "customfield_10008", # Epic Link example
  "customfield_10016", # Story Points example
  "customfield_12345", # Target start example
  "customfield_12346", # Target end example
  "customfield_12347"  # Logged hours example, if applicable
)

$batchSize = 500
$startAt = 0
$allRows = New-Object System.Collections.Generic.List[object]

do {
  $body = @{
    jql = $jql
    startAt = $startAt
    maxResults = $batchSize
    fields = $fields
  } | ConvertTo-Json -Depth 10

  Write-Host "Reading Jira issues starting at $startAt..."

  $response = Invoke-RestMethod `
    -Method Post `
    -Uri "$baseUrl/rest/api/2/search" `
    -Credential $credential `
    -ContentType "application/json" `
    -Headers @{ Accept = "application/json" } `
    -Body $body

  foreach ($issue in $response.issues) {
    $f = $issue.fields

    $blocks = @()
    $blockedBy = @()
    foreach ($link in @($f.issuelinks)) {
      if ($link.type.name -eq "Blocks" -and $link.outwardIssue) {
        $blocks += $link.outwardIssue.key
      }
      if ($link.type.name -eq "Blocks" -and $link.inwardIssue) {
        $blockedBy += $link.inwardIssue.key
      }
    }

    $allRows.Add([pscustomobject]@{
      "Issue key" = $issue.key
      "Issue id" = $issue.id
      "Issue Type" = $f.issuetype.name
      "Summary" = $f.summary
      "Epic Link" = $f.customfield_10008
      "Parent" = $f.parent.key
      "Fix versions" = (@($f.fixVersions) | ForEach-Object { $_.name }) -join "; "
      "Story Points" = $f.customfield_10016
      "Logged Hours" = $f.customfield_12347
      "Status" = $f.status.name
      "Resolution" = if ($f.resolution) { $f.resolution.name } else { "" }
      "Resolved" = $f.resolutiondate
      "Target start" = $f.customfield_12345
      "Target end" = $f.customfield_12346
      "Outward issue link (Blocks)" = $blocks -join "; "
      "Inward issue link (Blocks)" = $blockedBy -join "; "
    })
  }

  $startAt += $response.maxResults
} while ($startAt -lt $response.total)

$allRows |
  Sort-Object "Issue key" |
  Export-Csv -Path $outFile -NoTypeInformation -Encoding UTF8

Write-Host "Wrote $($allRows.Count) rows to $outFile"
```

Use a batch size your Jira admins are comfortable with. `500` is a conservative starting point. If your instance handles it well, you can raise it. If Jira is slow or returns errors, lower it.

## Step 4: Validate The CSV Before j2p

Check total rows:

```powershell
$rows = Import-Csv .\jira-project-wide-export.csv
$rows.Count
```

Check duplicate Jira keys:

```powershell
$rows |
  Group-Object "Issue key" |
  Where-Object Count -gt 1 |
  Select-Object Name, Count
```

Check issue type coverage:

```powershell
$rows |
  Group-Object "Issue Type" |
  Sort-Object Count -Descending |
  Select-Object Name, Count
```

Check missing rollup fields on epics:

```powershell
$rows |
  Where-Object { $_."Issue Type" -eq "Epic" -and -not $_."Parent" -and -not $_."Fix versions" } |
  Select-Object "Issue key", Summary, Status
```

The duplicate check should return no rows. The issue type count should include epics and the child issue types used for completion math.

## Step 5: Run j2p Validate

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\jira-project-wide-export.csv `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.10" `
  --compare-state
```

If this is the first accepted baseline export, use `--write-state` instead of `--compare-state`:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\jira-project-wide-export.csv `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --run-id baseline `
  --write-state
```

## Browser Export Fallback

Use the Jira issue navigator export only when the result set is under your instance limit.

Recommended browser-export rules:

- Export current fields, not all fields.
- Add only the columns j2p needs.
- Split large exports into non-overlapping batches.
- Use stable split criteria such as `created` ranges or project key.
- Keep each batch below the instance export limit.
- Confirm that the sum of batch counts equals the original full-query count.
- Merge the CSV files carefully and keep only one header row.

Example split by created date:

```jql
project in (CORE, WEB, DATA, PLAT, OPS)
AND created >= "2026-01-01"
AND created < "2026-04-01"
ORDER BY key ASC
```

```jql
project in (CORE, WEB, DATA, PLAT, OPS)
AND created >= "2026-04-01"
AND created < "2026-07-01"
ORDER BY key ASC
```

Do not mix UI export batches with REST API batches for the same export. Use one method for the entire file so it is easier to reason about missing or duplicate rows.

## When To Ask A Jira Admin

Ask a Jira admin for help when:

- You do not know the custom field IDs.
- The REST API denies access.
- Jira caps `maxResults` lower than expected.
- Worklog/logged-hours data is not available in the search result.
- Exporting thousands of issues slows the Jira node.
- You need service-account authentication instead of personal credentials.

For very large or recurring enterprise exports, a service account plus REST pagination is usually the cleanest operational model.

## Final Hand-Off Checklist

Before giving the CSV to a schedule owner:

- The CSV is UTF-8 encoded.
- The row count matches the Jira query total.
- There are no duplicate `Issue key` values.
- Epics are present.
- Child stories/tasks are present.
- Rollup fields are present for included epics.
- `Resolved` is present when stale completed fixVersion suppression is used.
- Dependency link columns are populated when Jira blockers are used.
- The CSV has the exact column headers configured in `j2p.yaml`.
