"""Version progress includes references; counted portfolio points do not."""

import unittest
from pathlib import Path
from unittest.mock import patch

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.project import summary_assignments, review_table_columns
from j2p.reports import summary_rollup_rows, summary_rollup_rows_for_project_key, render_rollup_status
from j2p.state import run_plan_to_state


class FixVersionCompletionTests(unittest.TestCase):
    def plan(self, status, versions='A;B;C'):
        config = load_config(None, {'resource_groups': {'TEAM': 'Team'},
                                    'rollup_modes': {'TEAM': 'fixVersion'}})
        rows = [
            ['Issue key', 'Issue Type', 'Summary', 'Epic Link', 'Fix versions', 'Story Points', 'Status'],
            ['TEAM-1', 'Epic', 'Shared epic', '', versions, '', 'Open'],
            ['TEAM-2', 'Task', 'Shared three points', 'TEAM-1', versions, '3', status],
            ['TEAM-3', 'Epic', 'B only epic', '', 'B', '', 'Open'],
            ['TEAM-4', 'Task', 'B remaining work', 'TEAM-3', 'B', '7', 'Open'],
        ]
        with patch('j2p.jira.read_csv_rows', return_value=(rows, 'utf-8')):
            plan = build_run_plan(Path('synthetic.csv'), config)
        return plan, config

    def test_three_completed_points_burn_down_each_version_once(self):
        before, _ = self.plan('Open')
        after, _ = self.plan('Done')
        for version, expected_total, expected_percent in [('A', 3, 100), ('B', 10, 30), ('C', 3, 100)]:
            old = before.summaries['fixVersion:' + version]
            new = after.summaries['fixVersion:' + version]
            self.assertEqual(new.completion_total_story_points, expected_total)
            self.assertEqual(new.completion_completed_story_points, 3)
            self.assertEqual(new.percent_complete, expected_percent)
            self.assertEqual(old.completion_completed_story_points, 0)
            self.assertEqual(old.percent_complete, 0)
        self.assertEqual(sum(s.total_story_points for s in after.summaries.values()), 10)
        self.assertEqual(sum(s.completed_story_points for s in after.summaries.values()), 3)
        self.assertEqual(after.summaries['fixVersion:C'].total_story_points, 0)

    def test_completion_is_independent_of_primary_version_order(self):
        first, _ = self.plan('Done')
        reordered, _ = self.plan('Done', 'B;C;A')
        for key, summary in first.summaries.items():
            other = reordered.summaries[key]
            self.assertEqual(summary.percent_complete, other.percent_complete)
            self.assertEqual(summary.completion_total_story_points, other.completion_total_story_points)

    def test_reports_state_and_project_expose_both_point_scopes(self):
        plan, config = self.plan('Done')
        rows = summary_rollup_rows(plan)
        self.assertEqual(rows, summary_rollup_rows_for_project_key(plan, 'TEAM'))
        mixed = next(row for row in rows if row['rollup_key'] == 'B')
        self.assertEqual(mixed['completion_completed_story_points'], 3)
        self.assertEqual(mixed['completion_total_story_points'], 10)
        self.assertEqual(mixed['completed_story_points'], 0)
        self.assertEqual(mixed['total_story_points'], 7)
        self.assertIn('Completion Points (Done / Total)', render_rollup_status(plan))
        state = run_plan_to_state(plan)
        self.assertEqual(state['summaries']['fixVersion:B']['completion_completed_story_points'], 3)
        fields = dict(summary_assignments(plan.summaries['fixVersion:B'], config))
        self.assertEqual((fields['Number1'], fields['Number2']), (7, 0))
        self.assertEqual((fields['Number5'], fields['Number6'], fields['Number7']), (10, 3, 30))
        config['review_table']['exposed_columns'] = ['completion_percent']
        self.assertIn('Number7', review_table_columns(config, []))
