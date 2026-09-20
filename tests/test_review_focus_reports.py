"""The focused report links complete audit evidence without repeating it."""
import csv
import tempfile
from copy import deepcopy
from dataclasses import asdict
import unittest
from pathlib import Path
from unittest.mock import patch

from j2p.config import ConfigError, load_config
from j2p.core import build_run_plan
from j2p.models import AuditItem
from j2p.reports import render_review_focus, resource_group_run_plan, write_reports
from j2p.review_focus import build_review_focus


class ReviewFocusReportTests(unittest.TestCase):
    def plan(self):
        config = load_config(None, {
            'resource_groups': {'TEAM': 'Team'}, 'rollup_modes': {'TEAM': 'initiative'},
            'warning_suppression': {'before': ''},
        })
        rows = [
            ['Issue key', 'Issue Type', 'Summary', 'Parent', 'Epic Link', 'Story Points', 'Status', 'Target end'],
            ['TEAM-1', 'Epic', 'Done parent with active scope', 'TEAM-999', '', '', 'Done', '2026-09-20'],
            ['TEAM-2', 'Task', 'Open omitted work', '', 'TEAM-1', '3', 'Open', '2026-09-20'],
            ['TEAM-3', 'Task', 'Done omitted work', '', 'TEAM-1', '2', 'Done', '2026-09-20'],
        ]
        with patch('j2p.jira.read_csv_rows', return_value=(rows, 'utf-8')):
            plan = build_run_plan(Path('synthetic.csv'), config)
        plan.generated_at = '2026-09-19T12:00:00'
        return plan, config

    def test_source_context_keeps_done_parent_with_open_children_active(self):
        plan, config = self.plan()
        self.assertFalse(plan.stats['review_issue_context']['TEAM-1']['completed'])
        focus = build_review_focus(plan)
        group = next(g for g in focus['groups'] if g['key'] == 'TEAM-999')
        self.assertEqual(group['tier'], 'Fix first')
        self.assertEqual(group['audit_count'], 3)
        filtered = resource_group_run_plan(plan, config, 'Team')
        self.assertEqual(len(filtered.audit_items), len(plan.audit_items))

    def test_grouped_review_links_complete_audit_without_raw_html_rows(self):
        plan, config = self.plan()
        before = list(plan.audit_items)
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_reports(plan, Path(tmp), config)
            content = paths['manager_report'].read_text()
            with paths['audit_detail'].open(newline='') as handle:
                audit_rows = list(csv.DictReader(handle))
        self.assertIn('Highest Priority Fixes', content)
        self.assertIn('audit-detail.csv', content)
        self.assertNotIn('Full Review Audit', content)
        self.assertNotIn('StoryEpicExcluded', content)
        self.assertIn('TEAM-999', content)
        self.assertLess(content.index('Rollup and Completion'), content.index('Items for Review'))
        self.assertEqual([(row['jira_key'], row['category']) for row in audit_rows],
                         [(item.jira_key, item.category) for item in before])
        for item, row in zip(before, audit_rows):
            for field, value in asdict(item).items():
                self.assertEqual(row[field], '' if value is None else str(value),
                                 f'{item.jira_key} {field} lost detail in the audit CSV')
        self.assertTrue({'TEAM-2', 'TEAM-3'} <= {row['jira_key'] for row in audit_rows})
        self.assertEqual(before, plan.audit_items)

    def test_focus_limit_keeps_remainder_available_and_escapes_source_text(self):
        plan, _ = self.plan()
        focus = build_review_focus(plan)
        group = focus['groups'][0]
        group['summary'] = '<script>unsafe</script>'
        focus['groups'] = [dict(group, key=str(n)) for n in range(3)]
        content = render_review_focus(focus, 90, 1)
        self.assertIn('More Current Fixes', content)
        self.assertIn('2 more grouped actions', content)
        self.assertNotIn('<script>', content)
        self.assertIn('&lt;script&gt;', content)

    def test_undated_actions_are_collapsed_outside_high_priority_with_evidence_retained(self):
        plan, config = self.plan()
        for context in plan.stats['review_issue_context'].values():
            context['target_start'] = ''
            context['target_end'] = ''
        plan.audit_items.append(AuditItem(
            'Error', 'ProjectDependencyWriteFailed', jira_key='TEAM-1',
            summary='Undated dependency error', field='Predecessors',
            message='The supplied dependency was rejected.',
            reviewer_action='Review the dependency details.',
        ))
        plan.stats['review_issue_context']['TEAM-4'] = {
            'summary': 'Dated work needing review', 'completed': False,
            'target_start': '', 'target_end': '2026-09-20',
            'resource_group': 'Team',
        }
        plan.audit_items.append(AuditItem(
            'Review', 'InPlanning', jira_key='TEAM-4',
            summary='Dated work needing review', reviewer_action='Confirm the planned work.',
        ))
        before = deepcopy(plan)
        focus = build_review_focus(plan)
        self.assertEqual(focus['unscheduled_count'], 2)
        self.assertEqual(focus['focus_count'], 1)
        with tempfile.TemporaryDirectory() as tmp:
            paths = write_reports(plan, Path(tmp), config)
            content = paths['manager_report'].read_text()
            with paths['audit_detail'].open(newline='') as handle:
                audit_rows = list(csv.DictReader(handle))
        highest = content.split('<h2>Highest Priority Fixes</h2>', 1)[1].split('</section>', 1)[0]
        self.assertIn('TEAM-4', highest)
        self.assertNotIn('TEAM-1', highest)
        self.assertNotIn('TEAM-999', highest)
        unscheduled = content.split('<summary><span>Unscheduled Work</span>', 1)
        self.assertTrue(unscheduled[0].endswith('<details class="detail-block">'))
        self.assertIn('TEAM-1', unscheduled[1].split('</details>', 1)[0])
        self.assertIn('TEAM-999', unscheduled[1].split('</details>', 1)[0])
        self.assertIn('2 grouped actions without Jira target dates', content)
        self.assertNotIn('Full Review Audit', content)
        self.assertTrue({'TEAM-2', 'TEAM-3'} <= {row['jira_key'] for row in audit_rows})
        rejected = next(row for row in audit_rows if row['category'] == 'ProjectDependencyWriteFailed')
        self.assertEqual(rejected['message'], 'The supplied dependency was rejected.')
        self.assertEqual(len(audit_rows), len(before.audit_items))
        self.assertEqual(plan, before)

    def test_unlimited_focus_window_still_keeps_undated_work_outside_priority_table(self):
        plan, _ = self.plan()
        for context in plan.stats['review_issue_context'].values():
            context['target_start'] = ''
            context['target_end'] = ''
        focus = build_review_focus(plan, days=0)
        content = render_review_focus(focus, 0, 25)
        highest = content.split('<h2>Highest Priority Fixes</h2>', 1)[1].split('</section>', 1)[0]
        self.assertIn('No items.', highest)
        self.assertNotIn('TEAM-999', highest)
        self.assertIn('Unscheduled Work', content)
        self.assertIn('all dated unfinished work', content)
        self.assertNotIn('unknown dates', content)
        self.assertNotIn('href="#review-audit"', content)

    def test_focus_configuration_is_validated(self):
        self.assertEqual(load_config(None, {'report_review': {'focus_days': 0}})['report_review']['focus_days'], 0)
        for settings in ({'focus_days': -1}, {'focus_days': True}, {'max_focus_items': 0}):
            with self.subTest(settings=settings), self.assertRaises(ConfigError):
                load_config(None, {'report_review': settings})
