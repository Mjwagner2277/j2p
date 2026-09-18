# Windows Microsoft Project acceptance gate

Portable tests exercise parsing, planning, reports, and simulated COM failures. They do not certify the behavior of a particular Microsoft Project installation. Run this gate on Windows before releasing Project-adapter changes. A passing JSON result is evidence for the listed environment and fixture only.

Use a sanitized `.mpp` and synthetic Jira exports containing representative completed tasks, reference rows, moved rollups, and dependencies. Close other Project windows before running. Install the Project extra:

```powershell
py -3.14 -m pip install -e ".[project]"
py -3.14 .\scripts\windows_acceptance.py `
  --main-project .\acceptance-fixtures\sanitized-source.mpp `
  --jira-csv .\examples\large-scenario\project-wide-jira-updated-1200.csv `
  --config .\examples\large-scenario\config.large-example.yaml `
  --output-dir .\review-output\windows-acceptance
```

To exercise blank-project creation and Save As, run the same command with `--mode create` and omit `--main-project`. Use that produced sandbox as the source for a subsequent update run.

The harness creates a unique output folder and sandbox copy, applies the plan, saves, closes without saving again, reopens, and verifies retained data. It checks the source file's SHA-256 before and after, including on failure. `windows-acceptance.json` records Python, platform, pywin32 and Project versions; input hashes; verification counts; warnings; timestamps; and the result. Exit codes are 0 for passed, 1 for failed, and 2 when the host is not Windows and no live checks ran.

Verification prints task/epic/summary progress and separate snapshot, close, hash,
and reopen stages. Its recorded `elapsed_seconds` covers the full verification
cycle. Include a large schedule in acceptance testing and retain these timings;
portable collection-count tests check that task/resource enumeration stays linear,
but do not measure Windows COM latency or rule out a blocked Project call.

Required adapter checks now include:

- The active file path matches the sandbox before mutations and saves. Failed open/save/recalculate Boolean results are errors.
- Task matching keys are unique. Duplicate keys identify the Project row and UniqueID.
- Managed epic fields, custom target dates, required auto-scheduling and active state, outline parent, and owned resource assignment survive save/reopen.
- Dependencies retain the intended Project task identity, Finish-to-Start type, and zero lag. Object-model relationships are preferred so localized display strings do not determine identity.
- Existing summary names are updated. Native summary percent complete remains Project's calculation from child durations; its custom story-point metrics are verified against the plan.
- On an incomplete driving epic, native `% Complete` may recalculate after scheduling. Confirm a different valid native percentage produces `ProjectNativeCompletionRecalculated`, while Story Point Completion % retains the exact planned value. Missing/invalid native values and completed driving rows below native 100% still fail. Custom completion corruption must fail before and after reopening.

The Project edition must support writing and reading the required `Active` task property. Reference rows must remain inactive and show progress through Story Point Completion %. Completed driving rows remain active with native completion at 100%. Test a shared epic with partial completion and a completed epic. Confirm summary updates do not change child actuals. Existing reference actuals must produce a clear failure without being cleared. A rejected Active transition includes its attempted boolean value and Project's original error.

## Fixture matrix and release evidence

Run the harness on each supported Project version/edition with these fixture variations, and retain the JSON files with the release:

| Scenario | Expected result |
| --- | --- |
| Normal create-produced source and update export | Passed, source hash unchanged, review fields retained |
| Existing SS/FF/SF dependency or nonzero lag | Link changed to FS with zero lag and verified after reopening |
| Identical dependency set on a second update | No dependency-change audit noise; same relationships |
| Reordered Project row IDs, generated multi-fixVersion keys | Relationships resolve to the same schedule keys/UniqueIDs |
| Duplicate matching keys in the MPP | Failed before task mutation; useful duplicate-key error |
| A managed resource group changed between runs | Previous owned assignment removed; new owned assignment retained |
| Existing human resources with the same names as group labels | Human resource metadata/assignments retained; separate owned placeholder used |
| Restricted fields, unsupported Active, or invalid outline | Failed with field/row context; no successful verification result |
| Save cancelled, disk unavailable, or active window switched | Failed; source hash check still recorded |

Also inspect the sandbox's review table and colors visually; the automated JSON checks do not certify visual formatting. Check its warning list for formatting/date issues and preserved unmanaged resources.

## Resource ownership and earlier files

New group placeholders carry a `j2p-managed-resource-v1:<hash>` marker in Resource Notes. Only assignments to marked resources are replaced or removed. Existing human assignments and old placeholders without a marker remain untouched; the audit warns when these may leave multiple native resource groups. Review and remove obsolete legacy assignments manually after confirming their ownership. Do not add ownership markers to human resources.

## Microsoft object-model references

The adapter checks documented Boolean results from [FileSave](https://learn.microsoft.com/en-us/office/vba/api/project.application.filesave), [FileSaveAs](https://learn.microsoft.com/en-us/office/vba/api/project.application.filesaveas), and [CalculateProject](https://learn.microsoft.com/en-us/office/vba/api/project.application.calculateproject). Dependency verification reads [TaskDependency.Type](https://learn.microsoft.com/en-us/office/vba/api/project.taskdependency.type) and [TaskDependency.Lag](https://learn.microsoft.com/en-us/office/vba/api/project.taskdependency.lag); numeric lag is minutes and `pjFinishToStart` is 1 in [PjTaskLinkType](https://learn.microsoft.com/en-us/office/vba/api/project.pjtasklinktype). Resource ownership uses the persisted [Resource.Notes](https://learn.microsoft.com/en-us/office/vba/api/project.resource.notes) property.
