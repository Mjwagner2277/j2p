"""Missing Jira dates remain visible without changing scheduling or audit priority."""
import unittest
from pathlib import Path
from unittest.mock import patch

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.project import MicrosoftProjectSession
from j2p.project_values import epic_assignments
from test_project_selective_updates import CountingTask


class MissingDateReviewTests(unittest.TestCase):
    def plan(self, start='', end='', blocker='', versions='A'):
        config = load_config(None, {
            'resource_groups': {'TEAM': 'Team'},
            'rollup_modes': {'TEAM': 'fixVersion'},
        })
        rows = [
            ['Issue key', 'Issue Type', 'Summary', 'Epic Link', 'Fix versions',
             'Story Points', 'Status', 'Target start', 'Target end', 'Outward issue link (Blocks)'],
            ['TEAM-1', 'Epic', 'Epic', '', versions, '', 'Open', start, end, blocker],
            ['TEAM-2', 'Task', 'Work', 'TEAM-1', versions, '3', 'Open', '', '', ''],
        ]
        with patch('j2p.jira.read_csv_rows', return_value=(rows, 'utf-8')):
            plan = build_run_plan(Path('synthetic.csv'), config)
        return plan, config

    def test_missing_both_or_one_date_has_precise_informational_note(self):
        for start, end, missing in [
            ('', '', 'start/end'), ('', '2026-10-30', 'start'), ('2026-10-01', '', 'end'),
        ]:
            with self.subTest(missing=missing):
                plan, config = self.plan(start, end)
                epic = plan.epics['TEAM-1']
                note = epic.dependency_review
                self.assertTrue(note.startswith(f'Missing Jira target {missing}.'))
                self.assertIn('existing dates or the nearest available dates', note)
                self.assertEqual((epic.target_start, epic.target_end), (start, end))
                assignments = dict(epic_assignments(epic, config))
                self.assertEqual(assignments['Text8'], note)
                self.assertTrue(assignments['Flag3'])
                audits = [a for a in plan.audit_items if a.category == 'MissingJiraTargetDates']
                self.assertEqual(len(audits), 1)
                self.assertEqual((audits[0].severity, audits[0].field), ('Info', 'Dependency Review'))
                self.assertEqual(audits[0].new_value, note)

    def test_dated_epic_is_not_flagged_because_its_child_lacks_dates(self):
        plan, _ = self.plan('2026-10-01', '2026-10-30')
        self.assertEqual(plan.epics['TEAM-1'].dependency_review, '')
        self.assertFalse(any(a.category == 'MissingJiraTargetDates' for a in plan.audit_items))

    def test_reference_note_points_to_primary_and_preserves_reference_explanation(self):
        plan, config = self.plan(versions='A;B')
        reference = next(e for e in plan.epics.values() if not e.drives_schedule)
        self.assertIn('Reference row; primary TEAM-1 drives the schedule.', reference.dependency_review)
        self.assertIn('Active copy.', reference.dependency_review)
        self.assertNotIn('Project uses existing dates', reference.dependency_review)
        config['project_fields']['dependency_review'] = 'Text30'
        self.assertTrue(dict(epic_assignments(reference, config))['Text30'].startswith('Missing Jira target start/end.'))

    def test_note_is_visible_before_long_dependency_warnings(self):
        keys = [f'MISSING-{n}' for n in range(20)]
        plan, config = self.plan(blocker=','.join(keys))
        epic = plan.epics['TEAM-1']
        displayed = dict(epic_assignments(epic, config))['Text8']
        self.assertTrue(displayed.startswith('Missing Jira target start/end.'))
        self.assertLessEqual(len(displayed), 255)
        self.assertIn('Full details:', displayed)
        for key in keys:
            self.assertIn(key, epic.dependency_review)

    def test_later_dates_remove_note_and_preserve_other_dependency_reviews(self):
        for blocker in ('', 'MISSING-1'):
            with self.subTest(blocker=blocker):
                before, config = self.plan(blocker=blocker)
                after, _ = self.plan('2026-10-01', '2026-10-30', blocker=blocker)
                session = object.__new__(MicrosoftProjectSession)
                task = CountingTask()
                task.ResourceGroup = 'Team'
                with patch.object(session, 'set_native_resource_group', return_value=False):
                    session.update_epic_task(task, before.epics['TEAM-1'], config, before)
                    self.assertIn('Missing Jira target', task.Text8)
                    task.writes.clear()
                    session.begin_selective_update()
                    session.update_epic_task(task, after.epics['TEAM-1'], config, after)
                self.assertNotIn('Missing Jira target', task.Text8)
                self.assertEqual(task.Text8, after.epics['TEAM-1'].dependency_review)
                self.assertEqual(task.Flag3, bool(blocker))
                if blocker:
                    self.assertIn('Missing dependency target MISSING-1.', task.Text8)

    def test_unchanged_note_does_not_cause_repeated_project_writes(self):
        plan, config = self.plan()
        session = object.__new__(MicrosoftProjectSession)
        task = CountingTask()
        task.ResourceGroup = 'Team'
        with patch.object(session, 'set_native_resource_group', return_value=False):
            session.update_epic_task(task, plan.epics['TEAM-1'], config, plan)
            task.writes.clear()
            session.begin_selective_update()
            session.update_epic_task(task, plan.epics['TEAM-1'], config, plan)
        self.assertEqual(task.writes, [])


if __name__ == '__main__':
    unittest.main()
