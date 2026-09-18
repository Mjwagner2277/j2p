"""Exercise the supplied export configuration with synthetic, in-memory rows."""

import unittest
from pathlib import Path
from unittest.mock import patch

from j2p.config import load_config
from j2p.core import build_run_plan


class ExportConfigTests(unittest.TestCase):
    def test_current_points_take_precedence_and_jira_seconds_become_hours(self):
        config = load_config(Path(__file__).resolve().parents[1] / 'yerp/ssn-812-config.yaml')
        headers = ['Issue key', 'Issue Type', 'Summary', 'Custom field (Epic Link)',
                   'Custom field (Parent Link)', 'Fix versions',
                   'Custom field (Story Points)', 'Custom field (Original story points)',
                   'Status', 'Custom field (Time Spent Total (hrs))', 'Time Spent']
        rows = [headers,
                ['SSWSW-1', 'Epic', 'Example', '', '', 'R1', '', '', 'Open', '', ''],
                ['SSWSW-2', 'Task', 'Done child', 'SSWSW-1', '', '', '3', '30', 'Done', '', '7200'],
                ['SSWSW-3', 'Task', 'Open child', 'SSWSW-1', '', '', '2', '20', 'Open', '1.5', '3600'],
                ['SSWSW-4', 'Task', 'Explicit zero', 'SSWSW-1', '', '', '0', '40', 'Open', '', ''],
                ['SSWSW-5', 'Task', 'Original fallback', 'SSWSW-1', '', '', '', '1', 'Open', '', '']]
        with patch('j2p.jira.read_csv_rows', return_value=(rows, 'utf-8')):
            plan = build_run_plan(Path('synthetic.csv'), config)
        epic = next(e for e in plan.epics.values() if e.drives_schedule)
        self.assertEqual(epic.total_story_points, 6)
        self.assertEqual(epic.completed_story_points, 3)
        self.assertEqual(epic.percent_complete, 50)
        self.assertEqual(epic.logged_hours, 3.5)
        self.assertEqual(epic.completed_logged_hours, 2)
