# Export issue resolution — 2026-09-18

The nine exports in `yerp/` were read without modification. The supplied
`yerp/ssn-812-config.yaml` now selects the actual `Custom field (Story Points)`
header rather than the misspelled `Custom field (Custom field (Story Points)`.
The existing Original story points fallback remains in place for blank current
estimates; explicit zero values do not use the fallback.

The configuration also declares `Time Spent: seconds` under
`metrics.logged_hours_units`. The custom hours field retains its hours unit.
This avoids multiplying logged hours by 3,600 when the fallback is used.

## Verified results

Planning all nine exports together reads 11,871 unique issues and includes 719
unique epics, expanded to 1,033 schedule rows by fixVersion references.

| Metric | Previous configuration | Corrected configuration |
| --- | ---: | ---: |
| Included story points | 33 | 2,525 |
| Included completed story points | 0 | 1,105.2 |
| Unique included epics above 0% | 0 | 142 |
| Included logged hours | 28,470,972 | 7,908.62 |
| Unique included epics in planning | 716 | 456 |

For example, SSWCYBER-3289 now calculates 3.7 / 9.6 = 39%, and SSWHW-2395
calculates 3 / 5 = 60%. Totals count driving rows once, not their reference copies.

## Project verification

The pending verification optimization indexes task, summary and resource
collections once per verification pass, with progress during scanning and
before/after reopen snapshots. It retains field, dependency, resource and
persisted-file checks. Duplicate rollup identities now fail explicitly even
when the rows have distinct task keys. Task collection and row-read errors
retain the underlying exception; row errors include position and scan phase.

Synthetic regression tests cover these behaviors and the export configuration.
The full local smoke checks passed, including unit tests, compilation, fixture
drift checks and generated report validation. Native Microsoft Project COM
verification still requires the Windows acceptance harness. The earlier live
row-read failure has not been reproduced on Windows, so improved diagnostics
must not be interpreted as proof that its underlying cause is fixed.

## Remaining source and policy issues

The earlier read-only investigation identified the following unresolved scope
issues. They are not repaired by changing estimates or inventing relationships:

- 1,776 child rows reference epic keys absent from the combined exports, and
  996 child rows have no Epic Link.
- 945 epics are outside configured prefixes (SSWIF, SSWNET and SSWTEST).
  Another 573 epics lack usable rollups. The corrected plan still excludes
  1,518 epics in total and omits 7,076 child rows from included completion totals.
- Cancelled work remains in the denominator because the current policy does
  not exclude it. Excluding it would change percentages on 14 included epics.
- Most remaining zero percentages reflect missing linked child work, no
  positive estimates, or no completed estimated work. The Original fallback
  contributes 15 points to one included epic with blank current estimates.

These counts describe the supplied export snapshot. Historical warning
suppression can reduce visible audit counts without changing the omitted work.
Changing prefix scope, cancellation policy, or estimate fallback policy needs
an explicit business choice; missing relationships need corrected Jira exports.

## Follow-up: reference completion credit

Per the requested policy, version completion now includes reference rows in
both mixed and reference-only groups. Counted totals still use driving rows.
Separate completion numerator and denominator fields are included in summary
reports and state. Project receives Completion Total Points (Number5),
Completion Completed Points (Number6), and Story Point Completion % (Number7),
with configurable mappings. The supplied review table uses the custom
percentage so native summary duration calculations do not replace it.

This changes 12 mixed fixVersion percentages in the supplied exports and raises
the number of fixVersions above zero completion from 28 to 33. Overall counted
totals remain 2,525 points and 1,105.2 completed points. All 181 tests and full
smoke checks pass. The three-point shared-work regression covers credit to
every version, preservation of overall totals, and primary-version reordering.
CSV inputs remain unchanged. Native Project acceptance remains outstanding.
