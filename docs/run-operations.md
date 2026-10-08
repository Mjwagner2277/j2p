# Running and recovering j2p projects

j2p 0.3.0 adds saved project settings, read-only setup checks, run manifests, and
recoverable state publication. Existing one-file and multi-file commands still work.

## Use a configuration and export folder

`--config-folder` accepts a folder containing the YAML configuration and Jira CSV
exports for a run. It is supported by `validate`, `create`, `update`, `doctor`, and
`init-profile`. From the repository root on Windows:

```powershell
# Read the current yerp exports and generate a report without opening Project
py -3.14 -m j2p validate `
  --config-folder .\yerp `
  --output-dir .\review-output `
  --project-name "SSN-812"

# Update a sandbox copied from your accepted Project baseline
py -3.14 -m j2p update `
  --config-folder .\yerp `
  --main-project .\Program.mpp `
  --output-dir .\review-output `
  --project-name "SSN-812" `
  --sprint "PI 20"
```

Replace `Program.mpp` with your baseline's path and `PI 20` with your current run
label. The main MPP is not modified. When first adopting the shared fixVersion
layout, use `create` to establish a fresh baseline before updating it; see
[shared fixVersion scope](configuration-reference.md#shared-fixversion-scope).

The folder is not recursive. j2p selects all immediate `.csv` files and requires
exactly one immediate `.yaml` or `.yml` configuration file; suffix matching is
case-insensitive. If no YAML is found or several are found, supply `--config` with
the intended file. Commands that require exports report an error when no CSV is
available. Keep one coherent export snapshot in the folder: old and new exports
of the same issues can conflict, and folder loading does not reconstruct missing
history. Existing explicit file commands still work.

You can override either discovery independently:

```powershell
py -3.14 -m j2p validate `
  --config-folder .\yerp `
  --config .\yerp\ssn-812-config.yaml `
  --jira-csv .\exports\batch-01.csv .\exports\batch-02.csv `
  --output-dir .\review-output `
  --project-name "SSN-812"
```

`--config` overrides the discovered YAML; `--jira-csv` replaces the discovered CSV
list. Neither override disables discovery for the other input.

Before opening Project, j2p compares the projects in `resource_groups` against
the unique issue-key prefixes in the selected CSVs. It warns about missing
configured projects and uploaded projects without a resource-group entry. A
project still counts as present when its issues do not produce schedule rows.
Filenames are not used to decide coverage. These nonblocking warnings also appear
in the audit and run manifest, and in `doctor --json` results. They identify absent
projects, not every missing issue; use `--expected-issues` with the corresponding
Jira export count when available.

## Save project settings once

On Windows, from the repository root:

```powershell
py -3.14 -m j2p init-profile `
  --path .\customer-portal.json `
  --project-name "Customer Portal Program" `
  --config .\config\j2p.yaml `
  --main-project .\schedules\Program-Source-Of-Truth.mpp `
  --output-dir .\review-output
```

This creates a JSON file and refuses to overwrite an existing profile. It records
absolute paths. You can edit it to use paths relative to the profile file:

```json
{
  "version": 1,
  "project_name": "Customer Portal Program",
  "config": "config/j2p.yaml",
  "main_project": "schedules/Program-Source-Of-Truth.mpp",
  "output_dir": "review-output"
}
```

The profile can also contain `config_folder`, `state_path`, `jira_csv` (a list of file paths),
`expected_issues`, `comparison_source`, and `dependency_write_mode`. An explicit
command argument overrides its profile setting. Explicit CSV arguments replace the
profile's CSV list rather than appending to it. Update-only settings are ignored
for commands that do not use them. Sprint, run ID, state-writing, and rerun flags
remain explicit command choices.

To save folder discovery instead of a fixed list of export filenames:

```powershell
py -3.14 -m j2p init-profile `
  --path .\ssn-812.json `
  --project-name "SSN-812" `
  --config-folder .\yerp `
  --main-project .\Program.mpp `
  --output-dir .\review-output

py -3.14 -m j2p doctor --profile .\ssn-812.json
py -3.14 -m j2p validate --profile .\ssn-812.json
```

The profile saves the folder path, not the YAML filename discovered inside it,
so subsequent commands discover the folder's current files. An explicitly
supplied `--config` remains saved as an override. A manually edited
`"config_folder": "yerp"` resolves relative to the profile file. An explicit
`--config-folder` on a later command replaces the profile folder and clears the
profile's saved `config` and `jira_csv` selections, allowing the new folder to be
discovered. Explicit file arguments on that command still override their own
part of discovery.

## Check setup and exports

```powershell
py -3.14 -m j2p --version
py -3.14 -m j2p doctor --profile .\customer-portal.json
py -3.14 -m j2p doctor --profile .\customer-portal.json `
  --jira-csv .\exports\batch-01.csv .\exports\batch-02.csv `
  --expected-issues 6200
```

`doctor` reads configuration, optional CSVs and existing state, and checks the
nearest existing output parent. On Windows it also checks Project COM registration
and pywin32 availability. It does not launch Project, create output folders, repair
state, or write probe files. `--json` prints machine-readable results. A failed
check returns exit code 2. A non-Windows host reports Project as unavailable but
can pass validation checks. Registration is not proof that live automation works;
use the [Windows acceptance harness](windows-acceptance.md) for that.

The expected count is the number of unique issue keys across the complete export,
including all issue types supplied. Matching overlapping rows are counted once.
Use the matching Jira search count for the same export scope. This checks total
coverage; it cannot prove that a same-sized export contains the correct issues.

## Baseline and sprint runs

```powershell
# First report-only baseline (optional if create is the first baseline)
py -3.14 -m j2p validate --profile .\customer-portal.json `
  --jira-csv .\exports\baseline-01.csv .\exports\baseline-02.csv --write-state

# Initial Project file
py -3.14 -m j2p create --profile .\customer-portal.json `
  --jira-csv .\exports\baseline-01.csv .\exports\baseline-02.csv

# Review the next complete export without modifying Project
py -3.14 -m j2p validate --profile .\customer-portal.json `
  --jira-csv .\exports\sprint-01.csv .\exports\sprint-02.csv `
  --sprint "Sprint 24.11" --compare-state

# Apply the same export to a fresh sandbox in that sprint
py -3.14 -m j2p update --profile .\customer-portal.json `
  --jira-csv .\exports\sprint-01.csv .\exports\sprint-02.csv `
  --sprint "Sprint 24.11" --allow-existing-sprint
```

Use the printed `Open report` path for the HTML landing page. Review the new
sandbox before your team's normal source-of-truth acceptance process. j2p does not
replace the source MPP or imply human acceptance. The root `j2p-state.json` tracks
the last successfully published **planned** snapshot. Actual Project readback is
recorded separately in the run manifest. `update` still compares to the main MPP
by default; explicit state comparison requires an existing valid state file.

On macOS/Linux, use `python3 -m j2p` for validation, doctor, profiles and support
bundles. Create/update require Windows and Microsoft Project.

## Run status and failure recovery

Every run owns a unique directory. A supplied `--run-id` must be a safe filename
and must not already exist. Default IDs include microseconds and a random suffix.
Project and state locks prevent simultaneous writers, including different projects
that explicitly share a state path. Operating-system locks release when a process
exits; persistent `.lock` files are normal and should not be deleted.
Custom `--state-path` locations must be outside run history and must not use j2p's
reserved lifecycle filenames or overwrite an input file.

`run-manifest.json` records one of:

| Status | Meaning |
| --- | --- |
| `in_progress` | The directory is staging incomplete outputs. Do not accept them. |
| `failed` | The run did not publish successfully; inspect `failure.json` when available. |
| `completed` | Required work completed without review/warning items. |
| `completed_with_warnings` | Required work completed, with audit items requiring review. |

Project saves and critical fields must pass save/reopen verification. Reports and
per-run state must finish before root state publication. Root JSON writes use a
temporary file and atomic replacement; a recovery journal retains the old state
until publication completes. If a caught error occurs during publication, j2p
rolls back root state and a newly created sprint marker. If a process is killed,
the next run under the same state/project locks completes recovery before proceeding.
A pre-publication crash is marked failed when the next run acquires the project lock.
Do not edit pending journals or root state during recovery. Unexpected external
state changes stop recovery for inspection rather than overwriting that state.

Failed runs do not reserve a new sprint. Correct the input and retry with a new
run ID (or let j2p choose one). Keep the failed directory for diagnosis. A sprint
with a previous successful run still requires `--allow-existing-sprint`. That flag
allows a new run, never overwriting an old run ID.

If the disk is unavailable, even failure diagnostics may be unwritable. Restore
access and rerun; the recovery journal is retained when recovery cannot finish.

## Share a reproducible support bundle

```powershell
py -3.14 -m j2p support-bundle `
  --run-dir .\review-output\Customer-Portal-Program\runs\j2p-run-EXAMPLE `
  --output .\support-example.zip
```

This packages the manifest, failure/verification diagnostics if present, and
reports/documentation. It includes configuration, local paths, and report contents;
review them before sharing. Original CSVs are excluded unless you add
`--include-inputs`. When included, their hashes must still match the recorded run.
The command only creates a local ZIP; it does not send it anywhere and does not
overwrite an existing ZIP.

The manifest records software/Python/pywin32 versions, Git commit and whether the
working tree has local changes when available,
resolved configuration and hash, input hashes/encodings/counts, duplicate totals,
baseline identity, comparison mode, audit counts, timings, Project verification,
and output paths. Original input files are not copied automatically.
Runs freeze supplied file paths to their initial resolved targets, so rotating a
`latest.csv` symlink cannot switch the export midway through processing. Input,
config, profile, and baseline changes detected during the run prevent publication.
For folder discovery, changing the discovered CSV or YAML file membership during
the run also prevents publication. Finish replacing exports before starting j2p.
