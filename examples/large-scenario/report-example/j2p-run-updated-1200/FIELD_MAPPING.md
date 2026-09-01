# j2p Microsoft Project Field Mapping

These Project task fields are used by j2p when it creates or updates a sandbox Project file.

Native Project fields used by j2p:

| j2p Value | Project Field | Purpose |
| --- | --- | --- |
| Resource Group | Resource Group | Populated by assigning a Project resource whose Group value comes from the Jira key prefix mapping in `resource_groups`. |

Custom task fields used by j2p:

| j2p Value | Project Field | Project Column Name |
| --- | --- | --- |
| `completed_story_points` | `Number2` | Completed Story Points |
| `dependency_review` | `Text8` | Dependency Review |
| `dependency_review_needed` | `Flag3` | Dependency Review Needed |
| `drives_schedule` | `Flag4` | Drives Schedule |
| `fix_version` | `Text12` | Jira Fix Version |
| `in_planning` | `Flag1` | In Planning |
| `j2p_key` | `Text10` | j2p Unique Key |
| `jira_issue_id` | `Text2` | Jira Issue ID |
| `jira_issue_type` | `Text3` | Jira Issue Type |
| `jira_key` | `Text1` | Jira Key |
| `jira_key_prefix` | `Text7` | Jira Key Prefix |
| `jira_status` | `Text9` | Jira Status |
| `jira_target_end` | `Date2` | Jira Target End |
| `jira_target_start` | `Date1` | Jira Target Start |
| `logged_hours` | `Number3` | Logged Hours |
| `primary_schedule_key` | `Text13` | Primary Schedule Key |
| `rollup_key` | `Text5` | Rollup Key |
| `rollup_mode` | `Text4` | Rollup Mode |
| `row_role` | `Text11` | j2p Row Role |
| `story_point_ratio` | `Number4` | Story Point Ratio |
| `total_story_points` | `Number1` | Total Story Points |
| `unmatched_project_task` | `Flag2` | Unmatched Project Task |

Review table visibility:

- Exposed columns: `jira_key`, `summary`, `resource_group`, `dependency_review`, `jira_status`, `start`, `finish`, `percent_complete`, `predecessors`
- Auto-include changed/review columns: no

Color key:

- Green: changed cell
- Red: first/root critical-path end-date driver; red overrides green
- Yellow/amber: unmatched or manager review needed
- Blue: dependency review marker
- Gray/green-gray: in planning
