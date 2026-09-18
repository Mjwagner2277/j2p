# j2p 0.3.0 reliability changes

This release hardens the CSV → plan → Project sandbox → review-report workflow.
See [run operations](run-operations.md), [input validation](input-validation.md),
[performance](performance.md), and [Windows acceptance](windows-acceptance.md).

## User-visible changes

- Failed runs keep diagnostics, preserve the prior root state, and do not reserve
  a new sprint. Run IDs cannot overwrite history. Concurrent writers are rejected.
- Project writes must retain required fields, outline/active state and dependency
  identity/type/lag through save/reopen. Save and recalculation failures stop the run.
- Changed resource groups replace only j2p-owned assignments. Legacy unmarked
  placeholders and human assignments are preserved and reported for review.
- Nonblank malformed/negative story points or logged time now fail validation;
  malformed hours no longer silently count as zero. Missing estimates remain a
  planning case. Missing/excluded/wrong-type parent epics have explicit audit items.
- Configuration is parsed identically everywhere. Quoted booleans, unknown keys,
  unsupported YAML features and malformed CSV record widths fail clearly.
- Numeric time units can be configured per source header. Aggregate time requires
  explicit configuration to prevent double-counting exported hierarchy levels.
- Multi-file runs record per-batch metadata and identify conflicting fields on
  duplicate issues. `--expected-issues` checks the combined unique-key count.
- Cascade visuals reference shared descendants and bound displayed depth/card
  count; complete details remain in tables and CSVs.
- `init-profile`, `doctor`, `--version`, and `support-bundle` simplify setup and
  support. Every run includes a manifest and a clear completion status.

## Before upgrading a production Windows workflow

Run the portable smoke checks and the Windows acceptance harness against a
sanitized copy of the program schedule. The Project installation must support the
required inactive task property. Review any preserved legacy group assignments;
only explicitly marked j2p resources are automatically replaced. Check colors
visually in the saved sandbox.

Use a new run ID after failures and for repeatable walkthroughs. Existing completed
sprints still require `--allow-existing-sprint`. Profiles supply defaults, while
explicit CLI arguments override them. The root state remains a successful planned
snapshot and does not signify human approval or replace the source MPP.
