# j2p Microsoft Project Field Mapping

These task custom fields are used by j2p when it creates or updates a sandbox Project file.

| j2p Value | Project Field | Project Column Name |
| --- | --- | --- |
| `completed_story_points` | `Number2` | Completed Story Points |
| `dependency_review` | `Text8` | Dependency Review |
| `dependency_review_needed` | `Flag3` | Dependency Review Needed |
| `in_planning` | `Flag1` | In Planning |
| `jira_issue_id` | `Text2` | Jira Issue ID |
| `jira_issue_type` | `Text3` | Jira Issue Type |
| `jira_key` | `Text1` | Jira Key |
| `jira_key_prefix` | `Text7` | Jira Key Prefix |
| `jira_status` | `Text9` | Jira Status |
| `jira_target_end` | `Date2` | Jira Target End |
| `jira_target_start` | `Date1` | Jira Target Start |
| `resource_group` | `Text6` | Resource Group |
| `rollup_key` | `Text5` | Rollup Key |
| `rollup_mode` | `Text4` | Rollup Mode |
| `total_story_points` | `Number1` | Total Story Points |
| `unmatched_project_task` | `Flag2` | Unmatched Project Task |

Color key:

- Green: changed cell
- Red: first/root critical-path end-date driver; red overrides green
- Yellow/amber: unmatched or manager review needed
- Blue: dependency review marker
- Gray/green-gray: in planning
