"""Rollup deadlines reflect scope, independently of schedule placement."""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.reports import render_rollup_status, resource_group_run_plan, summary_rollup_rows_for_project_key
from j2p.state import run_plan_to_state


class RollupTargetDateTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(None, {
            'resource_groups': {'INIT': 'Initiatives', 'TEAM': 'Team', 'OTHER': 'Other'},
            'rollup_modes': {'INIT': 'initiative', 'TEAM': 'fixVersion', 'OTHER': 'fixVersion'},
        })
        rows = [
            ['Issue key', 'Issue Type', 'Summary', 'Parent', 'Epic Link', 'Fix versions', 'Story Points', 'Status', 'Target end'],
            ['INIT-1', 'Initiative', 'Initiative deadline', '', '', '', '', 'Open', '2026-09-18'],
            ['INIT-2', 'Epic', 'Later epic', 'INIT-1', '', '', '', 'Open', '2026-12-31'],
            ['INIT-3', 'Task', 'Initiative work', '', 'INIT-2', '', '3', 'Open', ''],
            ['TEAM-1', 'Epic', 'Early member', '', '', 'A', '', 'Open', '2026-09-10'],
            ['TEAM-2', 'Task', 'Early work', '', 'TEAM-1', '', '3', 'Done', ''],
            ['OTHER-1', 'Epic', 'Late shared member', '', '', 'B;A;C', '', 'Open', '2026-09-20'],
            ['OTHER-2', 'Task', 'Shared work', '', 'OTHER-1', '', '3', 'Done', ''],
            ['TEAM-3', 'Epic', 'Today member', '', '', 'Today', '', 'Open', '2026-09-19'],
            ['TEAM-4', 'Task', 'Today work', '', 'TEAM-3', '', '3', 'Open', ''],
            ['TEAM-5', 'Epic', 'Missing date', '', '', 'Unknown', '', 'Open', ''],
            ['TEAM-6', 'Task', 'Undated work', '', 'TEAM-5', '', '3', 'Open', ''],
            ['TEAM-7', 'Epic', 'Planning', '', '', 'Planning', '', 'Open', '2026-01-01'],
        ]
        with patch('j2p.jira.read_csv_rows', return_value=(rows, 'utf-8')):
            self.plan = build_run_plan(Path('synthetic.csv'), self.config)
        self.plan.generated_at = '2026-09-19T12:00:00'

    def test_initiative_uses_own_date_and_versions_include_references(self):
        summaries = self.plan.summaries
        self.assertEqual(summaries['initiative:INIT-1'].target_end, '2026-09-18')
        self.assertEqual(summaries['fixVersion:A'].target_end, '2026-09-20')
        reference = summaries['fixVersion:C']
        self.assertEqual(reference.driving_epic_count, 0)
        self.assertEqual((reference.target_end, reference.percent_complete), ('2026-09-20', 100))
        self.assertEqual(run_plan_to_state(self.plan)['summaries']['fixVersion:A']['target_end'], '2026-09-20')

    def test_report_orders_dates_marks_due_and_omits_planning(self):
        table = render_rollup_status(self.plan)
        rows = [re.findall(r'<td[^>]*>(.*?)</td>', row) for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table, re.S)]
        rows = [row for row in rows if row]
        self.assertEqual([row[1] for row in rows], ['INIT-1', 'Today', 'A', 'B', 'C', 'Unknown'])
        self.assertEqual([row[6] for row in rows], ['Past due', 'Due today', 'Upcoming', 'Upcoming', 'Upcoming', 'Not set'])
        self.assertEqual(rows[4][4], 'Complete')
        self.assertNotIn('Reference only', table)
        self.assertNotIn('Planning', table)

    def test_filtered_reports_keep_deadline_from_other_resource_group(self):
        filtered = resource_group_run_plan(self.plan, self.config, 'Team')
        self.assertEqual(filtered.summaries['fixVersion:A'].target_end, '2026-09-20')
        row = next(row for row in summary_rollup_rows_for_project_key(self.plan, 'TEAM') if row['rollup_key'] == 'A')
        self.assertEqual(row['target_end'], '2026-09-20')
