# Project And Sprint Workflow

This guide explains the recommended j2p operating flow from the first project setup through recurring sprint updates. It is written for product users, schedule owners, and managers who need to understand where files are written, how state is used, and what to review after each run.

## Mental Model

j2p organizes work at three levels:

| Level | Purpose | Example |
| --- | --- | --- |
| Project | The long-running program or portfolio that owns the source-of-truth schedule. | `Customer Portal Program` |
| Sprint | A specific review cycle, sprint, increment, or reporting period. | `Sprint 24.10` |
| Run | One execution of `validate`, `create`, or `update`. | `j2p-run-20260917-093000` |

The project folder contains the persistent state file:

```text
review-output\Customer-Portal-Program\j2p-state.json
```

Each run also writes its own snapshot:

```text
review-output\Customer-Portal-Program\sprints\Sprint-24.10\runs\j2p-run-20260917-093000\state\j2p-state.after.json
```

The project-root `j2p-state.json` is the rolling j2p comparison state. Sprint folders are review history. j2p does not search old sprint folders to decide what changed; it uses either the Microsoft Project file, the project-root state file, or an explicitly provided previous sandbox depending on the command options.

The Microsoft Project source-of-truth `.mpp` remains the schedule authority for normal `update` runs. The state file is a j2p reporting and comparison artifact; it is not a replacement for the controlled Project schedule.

## Output Structure

A typical updated sprint run looks like this:

```text
review-output\
  Customer-Portal-Program\
    j2p-state.json
    sprints\
      Sprint-24.10\
        .j2p-sprint
        runs\
          j2p-run-20260917-093000\
            project\
              Program-Source-Of-Truth.sandbox.20260917-093000.mpp
            reports\
              html\
                index.html
                Manager-Review-Report.html
                resource-groups\
                  Product_Delivery.html
              csv\
                audit-detail.csv
                planned-epics.csv
                summary-rollups.csv
                dependency-review.csv
                by-project-key\
                  TEAM\
                    audit-detail.csv
                    planned-epics.csv
                    summary-rollups.csv
                    dependency-review.csv
            docs\
              FIELD_MAPPING.md
            state\
              j2p-state.after.json
```

Use `reports\html\Manager-Review-Report.html` for the manager review. Use the CSV files when a reviewer needs exact row-level audit detail, filtering, or per-project-key outputs.

## Before The First Run

Gather these inputs:

| Input | Why It Matters |
| --- | --- |
| Project name | Creates the long-running project folder. Use a stable name such as `Customer Portal Program`. |
| Jira CSV | Must include initiatives or fixVersions, epics, and child story/task rows used for completion calculations. |
| YAML config | Maps Jira key prefixes, rollup mode, resource groups, done statuses, and CSV columns. |
| Source-of-truth `.mpp` | Required for recurring `update` runs. |
| Sprint name | Required for `update` runs and recommended for sprint-specific reporting. |

Choose project and sprint names carefully. j2p converts them to folder-safe names:

| User Input | Folder Name |
| --- | --- |
| `Customer Portal Program` | `Customer-Portal-Program` |
| `Sprint 24.10` | `Sprint-24.10` |

## Step 1: Validate The First CSV

Use `validate` first when you want reports without opening Microsoft Project.

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\exports\jira-baseline.csv `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --run-id baseline-review `
  --write-state
```

What this does:

| Result | Location |
| --- | --- |
| Manager HTML report | `review-output\Customer-Portal-Program\runs\j2p-run-baseline-review\reports\html\Manager-Review-Report.html` |
| CSV audit files | `review-output\Customer-Portal-Program\runs\j2p-run-baseline-review\reports\csv\` |
| Run state snapshot | `review-output\Customer-Portal-Program\runs\j2p-run-baseline-review\state\j2p-state.after.json` |
| Persistent project state | `review-output\Customer-Portal-Program\j2p-state.json` |

`--write-state` intentionally updates the project-root state file. Use it only when the baseline looks valid enough to become the comparison point for later report-only comparisons.

## Step 2: Review Baseline Reports

Open:

```text
review-output\Customer-Portal-Program\runs\j2p-run-baseline-review\reports\html\Manager-Review-Report.html
```

Review these sections before creating or updating Project files:

1. `Reviewer Action Needed By Planning Horizon`
2. `Rollup Status`
3. `Project Key Rollup Mapping`
4. `Color Key`
5. `CSV Column Mapping Used`
6. `Detailed Review Sections`

Correct Jira or YAML configuration issues before proceeding. Common blockers include unknown Jira key prefixes, missing initiative parents, missing fixVersions, unrecognized date formats, and unexpected done statuses.

## Step 3: Create The Initial Project File

Use `create` only for initial setup, demonstrations, or when a team does not yet have a source-of-truth `.mpp`.

```powershell
py -3.14 -m j2p create `
  --jira-csv .\exports\jira-baseline.csv `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --run-id initial-project `
  --output-project-name Customer-Portal-Source.mpp
```

What this writes:

```text
review-output\Customer-Portal-Program\runs\j2p-run-initial-project\project\Customer-Portal-Source.mpp
```

It also writes:

```text
review-output\Customer-Portal-Program\j2p-state.json
review-output\Customer-Portal-Program\runs\j2p-run-initial-project\state\j2p-state.after.json
```

After this step, the schedule owner should decide where the source-of-truth `.mpp` will live. Future `update` runs should point `--main-project` at that controlled source-of-truth file.

## Step 4: Run A Sprint Update

Use `update` for normal recurring work. This is the production flow.

```powershell
py -3.14 -m j2p update `
  --jira-csv .\exports\jira-sprint-24-10.csv `
  --main-project .\schedule\Customer-Portal-Source.mpp `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.10"
```

What this does:

1. Copies the source-of-truth `.mpp` into a sprint run folder.
2. Applies Jira-derived updates only to the sandbox copy.
3. Writes updated HTML and CSV review reports.
4. Writes a run-level state snapshot.
5. Updates the project-root `j2p-state.json`.

The source-of-truth `.mpp` is not edited by j2p.

If you already ran `validate` for this same sprint, add `--allow-existing-sprint` to the `update` command. j2p creates a sprint marker during the first sprint-scoped run so accidental duplicate sprint runs do not happen silently.

The sandbox file is written here:

```text
review-output\Customer-Portal-Program\sprints\Sprint-24.10\runs\j2p-run-YYYYMMDD-HHMMSS\project\
```

The manager report is written here:

```text
review-output\Customer-Portal-Program\sprints\Sprint-24.10\runs\j2p-run-YYYYMMDD-HHMMSS\reports\html\Manager-Review-Report.html
```

## Step 5: Review Sprint Outputs

Start with:

```text
reports\html\index.html
```

Then review:

| Output | Who Uses It | What To Check |
| --- | --- | --- |
| `Manager-Review-Report.html` | Managers, schedule owners | Rollup health, changed values, exclusions, date cascades, decisions needed. |
| Resource-group HTML reports | Team leads | Review items scoped to their resource group. |
| Sandbox `.mpp` | Schedule owner | Colored cells, predecessors, dates, percent complete, resource group. |
| `audit-detail.csv` | Detailed reviewers | Every warning, changed value, and manager-review item. |
| `planned-epics.csv` | Schedule owners | Exact planned epic rows written or expected in Project. |
| `summary-rollups.csv` | Product owners | Initiative/fixVersion status and Story Point Ratio summaries. |
| `by-project-key\<KEY>\*.csv` | Team-specific reviewers | Filtered CSV outputs by Jira key prefix. |

For Project review:

1. Open the sandbox `.mpp`, not the source-of-truth `.mpp`.
2. Use the Gantt Chart view.
3. Apply the `j2p Review` table if it is not already active.
4. Review red date cascade drivers first.
5. Review green changed cells.
6. Review amber items requiring manager action.
7. Review blue dependency review cells.

## Step 6: Decide What Becomes Source Of Truth

j2p does not automatically promote sandbox changes back into the source-of-truth `.mpp`.

After review, the schedule owner should decide one of these outcomes:

| Outcome | Action |
| --- | --- |
| Accept all sandbox changes | Manually save/promote the reviewed sandbox according to the team schedule-control process. |
| Accept some changes | Manually apply approved changes to the source-of-truth schedule. |
| Reject changes | Leave the source-of-truth schedule unchanged and correct Jira/configuration before rerunning. |
| Need another review pass | Rerun under the same sprint with `--allow-existing-sprint`. |

## Rerunning A Sprint

By default, j2p stops if a sprint folder already exists:

```text
Sprint output already exists: review-output\Customer-Portal-Program\sprints\Sprint-24.10
```

This is intentional. It prevents users from accidentally mixing repeated runs into the same sprint review area.

When a rerun is intentional, use:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\exports\jira-sprint-24-10-corrected.csv `
  --main-project .\schedule\Customer-Portal-Source.mpp `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.10" `
  --allow-existing-sprint
```

This creates another run folder inside the same sprint:

```text
review-output\Customer-Portal-Program\sprints\Sprint-24.10\runs\
  j2p-run-20260917-093000\
  j2p-run-20260917-110000\
```

Use distinct `--run-id` values when you want named review passes:

```powershell
--run-id manager-review-pass-2
```

## How Comparison Works

`update` supports three comparison sources:

| Option | Meaning | Use When |
| --- | --- | --- |
| `--comparison-source main` | Compare against the source-of-truth `.mpp` copy. This is the default. | Normal schedule update review. |
| `--comparison-source state` | Compare against `review-output\<Project>\j2p-state.json`. | Report-style comparison against the last written j2p state. |
| `--comparison-source previous-sandbox` | Compare against a specific prior sandbox `.mpp`. | Iterative review where managers want to see changes since a previous sandbox pass. |

Default production update:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\exports\jira-sprint-24-11.csv `
  --main-project .\schedule\Customer-Portal-Source.mpp `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.11"
```

State comparison:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\exports\jira-sprint-24-11.csv `
  --main-project .\schedule\Customer-Portal-Source.mpp `
  --comparison-source state `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.11"
```

Previous sandbox comparison:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\exports\jira-sprint-24-11-corrected.csv `
  --main-project .\schedule\Customer-Portal-Source.mpp `
  --previous-sandbox .\review-output\Customer-Portal-Program\sprints\Sprint-24.11\runs\j2p-run-manager-review-pass-1\project\Customer-Portal-Source.sandbox.manager-review-pass-1.mpp `
  --comparison-source previous-sandbox `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.11" `
  --allow-existing-sprint
```

## State File Rules

The project-root state file is:

```text
review-output\<Project>\j2p-state.json
```

Run snapshots are:

```text
review-output\<Project>\runs\j2p-run-<id>\state\j2p-state.after.json
review-output\<Project>\sprints\<Sprint>\runs\j2p-run-<id>\state\j2p-state.after.json
```

Important rules:

- `validate --write-state` updates the project-root `j2p-state.json`.
- `validate --compare-state` reads the project-root `j2p-state.json`.
- `create` writes the project-root `j2p-state.json`.
- `update` writes the project-root `j2p-state.json`.
- `update --comparison-source state` reads the project-root `j2p-state.json`.
- j2p does not automatically choose the previous sprint folder for comparison.
- Sprint folders are historical review folders; the project-root state is the rolling j2p state.
- The source-of-truth `.mpp` remains the schedule authority unless your process explicitly promotes a reviewed sandbox.

## Recommended Recurring Sprint Pattern

For each sprint:

1. Export a fresh project-wide Jira CSV.
2. Run `validate` with `--project-name` and `--sprint`.
3. Review report-only warnings.
4. Correct Jira/configuration issues.
5. Run `update` with the same project and sprint, adding `--allow-existing-sprint` because the sprint folder already exists from validation.
6. Review the sandbox `.mpp` and manager report.
7. Rerun with `--allow-existing-sprint` if corrections are needed.
8. Promote approved schedule changes through the team schedule-control process.
9. Keep the sprint folder as the review record.

Example sprint validation:

```powershell
py -3.14 -m j2p validate `
  --jira-csv .\exports\jira-sprint-24-11.csv `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.11" `
  --compare-state
```

Example sprint update:

```powershell
py -3.14 -m j2p update `
  --jira-csv .\exports\jira-sprint-24-11.csv `
  --main-project .\schedule\Customer-Portal-Source.mpp `
  --config .\config\j2p.yaml `
  --output-dir .\review-output `
  --project-name "Customer Portal Program" `
  --sprint "Sprint 24.11" `
  --allow-existing-sprint
```

## Troubleshooting Flow Questions

| Question | Answer |
| --- | --- |
| Does sprint 2 automatically compare against sprint 1? | No. It compares based on the selected comparison source. The root project state is used only with state comparison paths. |
| Where is the latest j2p state? | `review-output\<Project>\j2p-state.json`. |
| Where is the audit trail for a specific sprint run? | Inside that run folder under `reports`, `project`, `docs`, and `state`. |
| Can I rerun the same sprint? | Yes, with `--allow-existing-sprint`. |
| Does j2p edit the source-of-truth `.mpp`? | No. It copies the source file into a sandbox and updates the sandbox. |
| Can I use reports without Microsoft Project? | Yes. Use `validate`; it creates reports but does not create or update `.mpp` files. |

## Failed runs and saved settings

See [run operations](run-operations.md) for saved project profiles, `doctor`,
expected export counts, atomic state publication, and support bundles. A failed
run retains its diagnostics but does not advance root state or reserve a new
sprint. Retry with a new run ID. An existing successful sprint still needs
`--allow-existing-sprint`; this never permits overwriting an existing run ID.
