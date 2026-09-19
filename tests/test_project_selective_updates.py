"""Regression coverage for selective writes without reducing update verification."""
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.project import MicrosoftProjectSession, apply_plan_to_sandbox, project_date_to_iso

FIXTURES = Path(__file__).parent / 'fixtures'


class CountingTask:
    """Observe every assignment, including redundant COM-equivalent writes."""
    def __init__(self):
        object.__setattr__(self, 'writes', [])
        object.__setattr__(self, 'ID', 1)
        object.__setattr__(self, 'UniqueID', 101)

    def __setattr__(self, field, value):
        self.writes.append((field, value))
        object.__setattr__(self, field, value)


class ProjectSelectiveUpdateTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(FIXTURES / 'mixed-config.yaml')
        self.plan = build_run_plan(FIXTURES / 'project-wide-jira-initial.csv', self.config)
        self.epic = deepcopy(self.plan.epics['TEAM-101'])
        self.session = object.__new__(MicrosoftProjectSession)
        self.task = CountingTask()
        self.task.ResourceGroup = self.epic.resource_group
        self.resources = patch.object(self.session, 'set_native_resource_group', return_value=False)
        self.resources.start()
        self.addCleanup(self.resources.stop)
        self.session.update_epic_task(self.task, self.epic, self.config, self.plan)
        self.task.writes.clear()

    def update(self):
        self.session.update_epic_task(self.task, self.epic, self.config, self.plan)

    def test_unchanged_epic_has_no_task_field_assignments(self):
        self.session.begin_selective_update()
        self.update()
        self.assertEqual(self.task.writes, [])

    def test_summary_change_writes_only_name(self):
        self.epic.summary = 'Updated Jira epic name'
        self.session.begin_selective_update()
        self.update()
        self.assertEqual(self.task.writes, [('Name', self.epic.summary)])

    def test_scheduler_native_progress_and_dates_survive_unchanged_jira(self):
        object.__setattr__(self.task, 'PercentComplete', 1)
        object.__setattr__(self.task, 'Finish', '2026-10-02')
        self.session.begin_selective_update()
        self.update()
        self.assertEqual(self.task.writes, [])
        self.assertEqual(self.task.PercentComplete, 1)
        self.assertEqual(self.task.Finish, '2026-10-02')

    def test_changed_completion_reseeds_native_progress(self):
        self.epic.percent_complete = 63
        self.session.begin_selective_update()
        self.update()
        completion_field = self.config['project_fields']['completion_percent']
        self.assertEqual(dict(self.task.writes), {completion_field: 63, 'PercentComplete': 63})
        self.assertEqual(len(self.task.writes), 2)

    def test_changed_target_end_updates_custom_and_native_date_only(self):
        self.epic.target_end = '2026-10-08'
        self.session.begin_selective_update()
        self.update()
        fields = [field for field, _value in self.task.writes]
        self.assertEqual(fields, [self.config['project_fields']['jira_target_end'], 'Finish'])
        self.assertEqual(project_date_to_iso(self.task.Date2), self.epic.target_end)
        self.assertEqual(project_date_to_iso(self.task.Finish), self.epic.target_end)

    def test_changed_start_reseeds_end_when_scheduler_shifts_finish(self):
        # A native Start assignment can move Finish to preserve duration. Reapply
        # the entire changed Jira window, while leaving its unchanged custom end.
        class ReschedulingTask(CountingTask):
            def __setattr__(self, field, value):
                super().__setattr__(field, value)
                if field == 'Start':
                    object.__setattr__(self, 'Finish', '2026-10-15')
                    object.__setattr__(self, 'PercentComplete', 1)
        rescheduling = ReschedulingTask()
        for field, value in vars(self.task).items():
            if field != 'writes':
                object.__setattr__(rescheduling, field, value)
        self.task = rescheduling
        self.epic.target_start = '2026-09-10'
        self.session.begin_selective_update()
        self.update()
        fields = [field for field, _value in self.task.writes]
        self.assertEqual(fields, ['Date1', 'Start', 'Finish', 'PercentComplete'])
        self.assertEqual(project_date_to_iso(self.task.Finish), self.epic.target_end)
        self.assertEqual(self.task.PercentComplete, self.epic.percent_complete)

    def test_completion_transition_enforces_native_one_hundred(self):
        self.epic.completed = True
        self.epic.status = 'Done'
        self.session.begin_selective_update()
        self.update()
        self.assertEqual(self.task.PercentComplete, 100)
        self.assertIn(('PercentComplete', 100), self.task.writes)
        self.assertTrue(self.task.Active)

    def test_reopened_issue_reseeds_even_when_source_percent_did_not_change(self):
        object.__setattr__(self.task, 'Text9', 'Done')
        object.__setattr__(self.task, 'PercentComplete', 100)
        self.session.begin_selective_update()
        self.update()
        self.assertEqual(self.task.PercentComplete, self.epic.percent_complete)
        self.assertEqual(dict(self.task.writes), {
            'Text9': self.epic.status, 'PercentComplete': self.epic.percent_complete,
        })

    def test_full_report_and_saved_verification_run_for_unchanged_update(self):
        before = {'TEAM-101': object()}
        session = MagicMock()
        session.snapshot_tasks.return_value = before
        session.__enter__.return_value = session
        with patch('j2p.project.MicrosoftProjectSession', return_value=session):
            result = apply_plan_to_sandbox(Path('sandbox.mpp'), self.plan, self.config)
        session.snapshot_tasks.assert_called_once_with(self.config)
        session.add_schedule_review_items.assert_called_once_with(self.plan, before, self.config)
        session.apply_review_formatting.assert_called_once_with(self.plan, self.config)
        session.recalculate.assert_called_once_with()
        session.save.assert_called_once_with()
        session.verify_saved_plan.assert_called_once_with(self.plan, self.config)
        self.assertIs(result, self.plan.audit_items)


if __name__ == '__main__':
    unittest.main()
