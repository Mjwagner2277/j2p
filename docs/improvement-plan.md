# j2p improvement plan

Review date: 2026-09-18. Baseline: `87887b7` (`Support multiple Jira CSV export batches`).

Recommendation: make the next release a reliability release. The main workflow is
useful and the current separation between portable planning and Windows Project
automation is worth keeping. The highest-value work is to make results trustworthy,
failed runs recoverable, and large exports predictable before adding more features.

## Review scope and evidence

Reviewed CSV ingestion, multi-file merging, configuration, calculations, rollups,
dependencies, COM writes, reports, state, CLI workflow, documentation and tests.
The full local smoke suite passed, including **91 unit tests**, compilation,
generated example checks and the 1,200-line scenario.

Additional synthetic tests exposed gaps that the existing suite does not cover.
They used temporary CSVs and fake Project objects; no customer CSV was needed.
**No live Windows/Microsoft Project verification was performed.** COM findings
below distinguish code-level failures from behavior still requiring a real Project
installation. The analysis below records the review baseline. The implementation status at the end records the subsequent 0.3.0 work.

## What to preserve

- The source-of-truth MPP and sandbox workflow.
- Combining CSV batches before computing completion and dependencies.
- Counting matching repeated issues once and rejecting conflicting duplicates.
- Keeping complete dependency details in reports while bounding the Project display field.
- Project/sprint/run organization and per-run state snapshots.
- The large-scenario walkthrough as the main teaching example.
- A portable validation/report path that does not require Project.

## Findings and proposed changes

### 1. Make run completion and state updates dependable — P1

**Evidence:** `j2p/cli.py:275` creates run folders with `exist_ok=True`;
`:278` reserves a sprint before reading the CSV. State is written before reports
at `:187`, `:223` and `:250`. `j2p/state.py:63` writes JSON directly.

Reproduced three cases:

- A missing input CSV causes validation to fail, but its sprint marker remains;
  the corrected command is then rejected as an existing sprint.
- Reusing the same run ID overwrites the earlier state snapshot.
- A simulated report-write failure leaves the project-root state advanced.

**Change:** give each run an explicit status (`in_progress`, `failed`,
`completed`, or `completed_with_warnings`). Reserve run IDs exclusively; prevent
concurrent updates to one project's state. Write outputs to a staging location,
finish report generation and Project verification, then atomically replace the
root state and mark the run complete. Keep failed diagnostics without treating a
failed attempt as a completed sprint. Validate output names and keep them inside
the intended run directory.

**Acceptance:** interrupted writes preserve the previous valid root state; a
corrected failed run is retryable; duplicate IDs cannot overwrite history;
concurrent runs cannot advance the same state independently. Clearly document that
root state is the last successful planned snapshot, not proof of human acceptance.

### 2. Verify Project operations before claiming success — P1

**Evidence:** `j2p/project.py:299` and `:304` ignore save results and set
`saved_successfully=True`. Fake `False` results produced success messages even
when SaveAs created no output. Recalculation at `:333` also ignores return values
and suppresses failure after both attempts. Microsoft documents Boolean returns
for [FileSave](https://learn.microsoft.com/en-us/office/vba/api/project.application.filesave),
[FileSaveAs](https://learn.microsoft.com/en-us/office/vba/api/project.application.filesaveas)
and [CalculateProject](https://learn.microsoft.com/en-us/office/vba/api/project.application.calculateproject).

**Change:** check critical operation results; confirm the active Project path
before mutation; verify the sandbox exists, save it, reopen it, and read back
critical fields. Distinguish failure from successful output with review warnings.
Extend field-specific errors to summary rows and keep a structured failed-run log.
Detect duplicate Project schedule keys before indexing rather than silently
selecting the last matching task (`project.py:490`).

**Acceptance:** save/recalculate rejection cannot print completion or advance
state. Saved/reopened values match the intended plan. Source MPP hashes stay
unchanged. Duplicate task identities identify the conflicting Project rows.

### 3. Correct dependency comparison and verification — P1

**Evidence:** `j2p/project.py:398` passes numeric Project predecessor text to a
Jira-key parser (`:2330`). `3FS,4FS` becomes an empty list. Comparisons can therefore
report unchanged dependencies as changes. Separately, verification at `:2387`
checks only predecessor IDs. A synthetic existing `3SS+2d` is accepted unchanged
when the intended link is `3FS`, because `:920` skips the write after that check.

**Change:** resolve Project ID/UniqueID to stable j2p schedule keys. Model and
verify each link's identity, relationship type and lag. Use dependency objects
where possible; report unmapped/external links explicitly. Preserve generated
multi-fixVersion schedule identities.

**Acceptance:** unchanged MPPs generate no dependency-change items; removed links
retain useful before/after details; SS/FF/SF and lagged links cannot masquerade as
the configured finish-to-start relationship. Verify after save/reopen on Windows.

### 4. Stop malformed input from silently changing metrics — P1

**Evidence:** `j2p/jira.py:171` converts malformed points to `None` and
`j2p/core.py:162` treats that as zero. A completed 3-point child plus an unfinished
child with `five` points produced **100% point-based completion**, without an
invalid-number warning. This does not itself set the epic's Jira completion status.
Negative points also pass. Duration parsing accepts a recognized fragment such as
`1h 99unrecognised` as one hour (`jira.py:227`).

A nonblank Epic Link to an absent epic receives no orphan warning; its bucket is
never consumed (`core.py:147`, `:163`, `:230`). This is particularly relevant when
an export batch is omitted.

**Change:** distinguish blank/unestimated values from invalid values. Reject
malformed, negative and non-finite estimates with file/row/field context. Require
whole-value duration parsing and retain seconds until aggregation. Reconcile
children with included, excluded, missing and wrong-type parents after all files
are merged. Report parsed, attached and omitted child counts separately.

**Acceptance:** invalid unfinished work cannot inflate completion; every omitted
child has an explicit reason. Blank estimates remain a deliberate planning case.
Unit handling for numeric Jira time fields must be explicit: verify a representative
Data Center export before choosing a seconds/hours conversion. Do not assume API
units apply to every CSV export or treat direct and aggregate time as interchangeable.

### 5. Make configuration mean the same thing on every machine — P1

**Evidence:** `j2p/config.py:184` uses PyYAML if installed, otherwise a custom
subset parser. The fallback strips the quoted `#1` from `"Core #1"` (`:437`) and
splits `"Done, Accepted"` into separate values in an inline list (`:512`). Boolean
normalization also treats the quoted string `"false"` as true in some sections.

**Change:** choose one supported parser with a reproducible install, or make the
restricted parser reject unsupported syntax instead of silently changing it.
Centralize section types, booleans, ranges and unknown-key validation. Return
configuration-path errors instead of raw exceptions.

**Acceptance:** punctuation inside quotes is preserved; false remains false;
null or invalid sections fail clearly; parser behavior does not depend on unrelated
packages installed on the user's machine.

### 6. Bound cascade report size and traversal depth — P1

**Evidence:** `j2p/reports.py:1254` recursively expands shared descendants for
every incoming path. A synthetic 33-epic graph with repeated branches and joins
rendered **131,071 cards and approximately 31 MB of HTML**. A 1,101-epic chain
raised `RecursionError`. Collapsing cards in the browser does not prevent this
server-side expansion.

**Change:** use iterative traversal; render shared nodes once with cross-references;
cache downstream counts; enforce a visible display budget and preserve complete
detail tables/CSVs. Keep the existing ordering by downstream impact.

**Acceptance:** long chains do not overflow recursion; shared descendants do not
multiply exponentially; a displayed limit is explained in the report; all issues
remain available in detailed output. Benchmark 5,000- and 10,000-issue exports,
including wide CSVs and realistic joins, before setting performance targets.

### 7. Replace only j2p-owned resource assignments — P2

**Evidence:** `j2p/project.py:746` adds the desired resource but does not remove
an old managed assignment. A fake Team A → Team B update retained both. Placeholder
resources are identified by name alone (`:760`).

**Change:** record ownership of j2p placeholders, replace only obsolete managed
assignments, preserve human assignments, and verify native Resource Group afterward.
Also verify required outline, auto-scheduling and inactive/reference-row state
instead of silently ignoring write failures. Update existing summary names when
initiatives are renamed.

**Acceptance:** repeated runs are idempotent; a group change does not accumulate
placeholders or modify unrelated resources. Check scheduling/calendar effects on
Windows before release.

### 8. Improve large-export diagnostics and reproducibility — P2

**Evidence:** a 150,000-character unused Description field raises an unhandled
`csv.Error` at `j2p/jira.py:78`. All raw batches are retained at
`j2p/core.py:69`. Report context has no input hashes, per-file counts or resolved
configuration (`j2p/reports.py:1469`).

**Change:** set a deliberate supported CSV field-size limit, validate row widths,
and contextualize parse errors. Add a run manifest with app version/commit,
configuration hash, input paths/hashes/encodings/counts, duplicate counts, baseline
identity, comparison mode, timings, output paths and final status. State the exact
fields that differ in a duplicate conflict. Add an optional batch inventory or
expected count so users can check export coverage. Keep input-file copying optional.

**Acceptance:** wide exports either load or fail with an actionable file/record
error; a support bundle identifies the exact inputs and settings; missing batches
can be detected when an inventory is provided. Do not claim issue-level checks can
prove that an entire missing batch was exported.

## Delivery order

| Delivery | Scope | Completion gate |
| --- | --- | --- |
| 1. Trustworthy completion | Findings 1–2: run lifecycle, atomic state, critical COM failures | Injected failures preserve prior state; no false success; Windows save/reopen evidence |
| 2. Correct comparisons and inputs | Findings 3–5: dependency semantics, metrics, relationship reconciliation, deterministic config | Synthetic edge cases pass; unchanged input/MPP produces no false differences |
| 3. Larger project support | Findings 6 and 8: bounded reports, wide CSVs, run manifests | 5k/10k export and branch/join benchmarks; bounded output; actionable errors |
| 4. Project ownership rules | Finding 7 and required property readback | Sanitized Windows fixture proves idempotence and preservation of human assignments |
| 5. Operator and release workflow | Read-only `doctor`, `--version`, saved project run profile, clearer result/status, Windows harness | Nontechnical user completes setup → validate → create → sprint update → retry from one walkthrough |
| 6. Focused maintenance | Extract pure report traversal and input validation; split tests by subsystem | Existing behavior retained; no broad COM rewrite without Windows evidence |

Each delivery should be reviewable independently. Begin the Windows fixture harness
in delivery 1 and expand it alongside later COM changes. Keep the documented local
release-check workflow; this plan does not require adopting GitHub Actions.

The Windows release evidence should record Python, pywin32 and Project versions,
source hashes, save/reopen results, key fields, relationship type/lag, resource
assignment, inactive rows and colors. Portable tests remain the fast first check,
but cannot substitute for this evidence.

For operator improvements, first reduce repeated CLI typing with a saved project
profile and a command that checks setup without changing files. Add a guided UI
only after those operations are reliable and if the user workflow still needs it.
Correct stale documentation along the way: state default paths in
`docs/user-guide.md:145` and `j2p/cli.py:153`, and platform-specific launcher examples.

## First recommended implementation scope

Start with delivery 1 as two small changes: (a) exclusive run directories, failure
status and atomic state publication; (b) checked Project save/recalculate results,
followed by save/reopen verification. In parallel with that work, preserve the
synthetic reproducers as regression tests for the data and report issues above.
The immediate goal is that a run can only say it succeeded when its outputs and
state are consistent, and that a failed run is straightforward to diagnose and retry.

## Implementation status — 0.3.0

All six implementation deliveries are now represented in code and documentation:

| Area | Implementation |
| --- | --- |
| Run lifecycle | `run_lifecycle.py`, atomic state writes, exclusive kernel locks, recovery journal and failed-run diagnostics |
| Project verification | Checked save/recalculate, active-path guards, duplicate task identity rejection, required field/outline/state readback, save/reopen verification |
| Dependencies and resources | Stable snapshot identities, type/lag checks, persisted placeholder ownership, preservation of unmanaged assignments |
| Inputs/configuration | Contextual strict values, child reconciliation, sequential batches, deliberate CSV limits, deterministic restricted YAML |
| Reports/scale | Extracted `cascade.py`, shared-node references, iterative rendering budgets, repeatable 5k/10k benchmark |
| Operators/release | `doctor`, `--version`, `init-profile`, support bundles, expected issue counts, run manifests, Windows acceptance script |
| Maintenance | New focused lifecycle, operator, input, cascade, and COM regression suites; corrected user workflow/reference docs |

Portable integration and benchmark results are recorded in the final task report
and [performance.md](performance.md). Live Windows acceptance is still required;
its harness is implemented, but this macOS run cannot certify Microsoft Project.
Visual review-table/colors remain a documented Windows acceptance step.

No guided UI was added: the plan made it conditional on need after saved profiles
and reliable operations. No GitHub Actions workflow was added, preserving the
repository's local release-check policy.
