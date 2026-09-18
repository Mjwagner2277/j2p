# Input validation and export batches

`validate`, `create`, and `update` use the same CSV parsing and planning checks.
Combine exports with `--jira-csv batch-01.csv batch-02.csv`. Relationships are
checked after all batches have been combined, so parents and children may be in
different files.

Matching duplicate Jira keys count once. Conflicting duplicates stop the run and
identify both files, both records, and the logical fields that differ. Export all
batches from the same snapshot where possible; j2p does not select a winning
version of an issue.

Run statistics contain `csv_batches`, with each input's absolute path, detected
encoding, record count, parsed issue count, unique issues added, duplicates
skipped, and selected column mappings. `unique_issues_read` counts normalized,
deduplicated issues. This count alone cannot establish that every expected Jira
issue was exported.

## Child work and completion

A child with an empty Epic Link, a missing epic, a link to a non-epic issue, or an
epic excluded by resource/rollup rules is reported for review. Its points and
hours do not contribute to included epic totals. Audit rows identify the source
file, CSV record, child key, and relevant parent key.

Statistics distinguish:

- `story_rows_read`: unique issues with a configured child issue type.
- `story_rows_attached_to_epic`: those linked to an epic present in the combined export.
- `story_rows_used_for_completion`: those linked to an included epic.
- `story_rows_omitted_from_completion`: child rows not used for included epic totals.

These counters include unestimated child rows; they count relationships, not the
number of rows that contributed a positive estimate. Subtasks still require an
Epic Link to contribute to epic metrics. Their Parent key is used to detect
overlapping aggregate time exports, not to silently infer an Epic Link.

## Story points and logged time

Blank story points mean unestimated; zero is a valid explicit estimate. A
nonblank malformed, negative, infinite, or NaN estimate stops the run. Errors
identify the file, record, issue, column, and attempted value. Use a decimal point
and, optionally, correctly grouped thousands: `1.5`, `1000`, or `1,000.25`.
Decimal-comma values such as `1,5` are rejected.

Blank logged time means zero. Every nonblank value must parse completely and be
nonnegative. For example, `1h 30m` is accepted; `1h unknown` fails. Supported forms
are numeric values, `HH:MM`, `HH:MM:SS`, and unit-bearing durations such as `1d 2h`,
`45m`, and `30s`. Duration days and weeks mean 8 and 40 hours respectively. Seconds
are retained through parsing and aggregation; displayed epic totals are rounded
to two decimals.

Numeric logged time defaults to hours for compatibility. Export formats and
custom columns can use different units. Verify the export's unit before relying
on totals; j2p does not infer a seconds conversion merely from a column name.
Configure a global unit or overrides for specific source headers:

```yaml
metrics:
  hours_per_story_point: 8
  logged_hours_unit: hours
  logged_hours_units:
    Time Spent: seconds
    Worklog Minutes: minutes
  logged_hours_source: direct
```

Units may be `hours`, `minutes`, or `seconds`. Per-header overrides are matched
without case or repeated-whitespace differences. They apply to the actual
nonblank alias selected for each record. Explicit duration suffixes and clock
formats retain their own meaning regardless of the configured numeric unit.

## Direct and aggregate time

Prefer direct issue-level logged time when exporting both stories and subtasks.
Known aggregate columns (`Σ Time Spent` and `Aggregate time spent`) require an
explicit `metrics.logged_hours_source: aggregate` setting; j2p will not silently
sum them as direct hours.

Aggregate mode requires one nonoverlapping child hierarchy level. If a child
and its Parent issue are both present as child-work rows, the run stops because
their totals can overlap. A subtask without a Parent key is also rejected in
aggregate mode. Use direct time or export only one hierarchy level. Custom
aggregate columns must also be declared with aggregate mode; j2p cannot infer
the semantics of arbitrary custom field names.

## CSV structure and limits

UTF-8 (with or without BOM), UTF-16, and Windows single-byte encodings remain
supported. Nonblank CSV records must have exactly the number of columns in the
header, including trailing empty fields. Duplicate headers remain supported for
Jira multi-value fields. Malformed quotes and inconsistent record widths produce
file/location errors. CSV record numbers are used for issues; parser failures
identify physical line numbers, which can differ for multiline fields.

Unused wide fields such as Description are supported up to a deliberate limit:

```yaml
input:
  csv_max_field_chars: 8388608
```

The default is 8,388,608 characters per field; the maximum configurable limit is
67,108,864. Errors identify the file and limit. Export fewer columns or raise the
limit within that bound when necessary. Batches are parsed sequentially, so raw
records from every export are not retained simultaneously.

## Configuration syntax

j2p always uses its bundled restricted YAML parser, regardless of whether
PyYAML happens to be installed. Supported syntax includes indented mappings,
block lists of scalars, inline scalar lists, strings, numbers, and unquoted
`true`/`false`. Empty mappings may be written as `{}`.

Quote text containing `#`, `: `, or commas inside an inline list. Both quoted
styles preserve their content; double-quoted strings use JSON-compatible
escapes and single-quoted strings escape an apostrophe as `''`.

```yaml
resource_groups:
  TEAM: "Team #1: Delivery"
rollup_modes:
  TEAM: fixVersion
done_statuses: [Done, "Closed, verified"]
planning_horizon:
  enabled: false
colors:
  changed_cell: '#C6EFCE'
```

Anchors, aliases, tags, YAML document separators, multiline scalar blocks, and
nonempty inline mappings are unsupported and fail with a line number. Expand
them into indented mappings and ordinary quoted strings. Duplicate keys and
prefixes that collide after normalization are rejected.

Unknown settings, wrong section types, invalid dates, nonpositive/fractional
integer settings, and quoted boolean strings such as `"false"` fail during
configuration loading. Boolean settings must use unquoted `true` or `false`.
This prevents a misspelled or misquoted setting from silently changing behavior.
