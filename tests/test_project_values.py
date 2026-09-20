"""Regression coverage for validation versus the Project write boundary."""
import csv
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from j2p.cli import main
from j2p.config import ConfigError, load_config
from j2p.core import build_run_plan
from j2p.jira import parse_number
from j2p.models import J2PError
from j2p.project import MicrosoftProjectSession, ProjectAutomationError
from j2p.project_values import check_project_value, validate_project_plan, project_dependency_review

FIXTURES = Path(__file__).parent / 'fixtures'


class ProjectValueTests(unittest.TestCase):
    def test_native_completion_is_seeded_after_schedule_dates(self):
        epic = next(iter(self.plan.epics.values()))
        epic.percent_complete = 6
        epic.completed = False
        task = type('Task', (), {})()
        session = object.__new__(MicrosoftProjectSession)
        def schedule_changes(*args):
            task.PercentComplete = 0
        with patch.object(session, 'set_native_resource_group'), patch.object(session, 'write_project_date', side_effect=schedule_changes):
            session.update_epic_task(task, epic, self.config, self.plan)
        self.assertEqual(task.PercentComplete, 6)
        self.assertEqual(task.Number7, 6)

    def test_completed_epics_and_reference_progress_obey_project_activation_rules(self):
        class ProjectTask:
            def __init__(self):
                self._active = True
                self._percent = 0
                self.Work = self.ActualWork = self.ActualDuration = 0
                from types import SimpleNamespace
                self.Assignments = SimpleNamespace(Count=0)
                self.TaskDependencies = SimpleNamespace(Count=0)
            @property
            def Active(self):
                return self._active
            @Active.setter
            def Active(self, value):
                if not value and self._percent:
                    raise RuntimeError('Cannot inactivate a task with progress')
                self._active = value
            @property
            def PercentComplete(self):
                return self._percent
            @PercentComplete.setter
            def PercentComplete(self, value):
                if not self._active:
                    raise RuntimeError('Cannot record progress on an inactive reference')
                self._percent = value
                self.ActualDuration = value

        for driving, completed, points_percent, native_percent in (
            (True, True, 60, 100), (False, False, 60, 0), (False, True, 100, 0),
        ):
            with self.subTest(driving=driving, completed=completed):
                epic = next(iter(self.plan.epics.values()))
                epic.drives_schedule = driving
                epic.completed = completed
                epic.percent_complete = points_percent
                task = ProjectTask()
                session = object.__new__(MicrosoftProjectSession)
                with patch.object(session, 'resource_assignments', return_value=[]), patch.object(session, 'set_native_resource_group'), patch.object(session, 'write_project_date'):
                    session.update_epic_task(task, epic, self.config, self.plan)
                self.assertTrue(task.Active)
                self.assertEqual(task.Manual, not driving)
                self.assertEqual(task.PercentComplete, native_percent)
                self.assertEqual(task.Number7, points_percent)

        # Do not erase existing actual progress to force copy isolation.
        task = ProjectTask()
        task.PercentComplete = 20
        epic.drives_schedule = False
        with self.assertRaisesRegex(ProjectAutomationError, 'Active copy has actuals'):
            session.update_epic_task(task, epic, self.config, self.plan)
        self.assertEqual(task.PercentComplete, 20)

    def setUp(self):
        self.config = load_config(FIXTURES / 'mixed-config.yaml')
        self.plan = build_run_plan(FIXTURES / 'project-wide-jira-initial.csv', self.config)

    def test_validate_retains_full_long_dependency_review_in_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (FIXTURES / 'project-wide-jira-initial.csv').open(newline='') as source:
                reader = csv.DictReader(source)
                headers, rows = reader.fieldnames, list(reader)
            for row in rows:
                if row['Issue key'] == 'TEAM-101':
                    row['Outward issue link (Blocks)'] = ', '.join(f'ABSENT-{n}' for n in range(100,110))
            path = Path(tmp) / 'input.csv'
            with path.open('w', newline='') as target:
                writer = csv.DictWriter(target, fieldnames=headers)
                writer.writeheader()
                writer.writerows(rows)
            output, error = StringIO(), StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                result = main(['validate', '--jira-csv', str(path), '--config', str(FIXTURES / 'mixed-config.yaml'),
                               '--project-name', 'Preflight', '--output-dir', str(Path(tmp) / 'output')])
            self.assertEqual(result, 0, error.getvalue())
            self.assertIn('Validation complete', output.getvalue())
            plan = build_run_plan(path, self.config)
            epic = plan.epics['TEAM-101']
            self.assertEqual(len(epic.dependency_review), 379)
            full_text = epic.dependency_review
            # Exercise the actual write path with a Project stand-in.
            task = type('Task', (), {})()
            session = object.__new__(MicrosoftProjectSession)
            with patch.object(session, 'set_native_resource_group'), patch.object(session, 'write_project_date'):
                session.update_epic_task(task, epic, self.config, plan)
            self.assertLessEqual(len(task.Text8), 255)
            self.assertIn('Full details: reports/csv/dependency-review.csv', task.Text8)
            self.assertTrue(task.Flag3)
            self.assertEqual(epic.dependency_review, full_text)
            for filename in ('planned-epics.csv', 'dependency-review.csv', 'audit-detail.csv'):
                report = next((Path(tmp) / 'output').rglob(filename)).read_text()
                for n in range(100, 110):
                    self.assertIn(f'ABSENT-{n}', report)

    def test_dependency_review_boundary_and_custom_mapping(self):
        for length in (0, 254, 255):
            value = 'x' * length
            self.assertEqual(project_dependency_review(value), value)
        for length in (256, 10000):
            self.assertLessEqual(len(project_dependency_review('x' * length)), 255)
            self.assertIn('Full details:', project_dependency_review('x' * length))
        self.config['project_fields']['dependency_review'] = 'Text30'
        epic = next(iter(self.plan.epics.values()))
        epic.dependency_review = 'Long dependency warning. ' * 100
        validate_project_plan(self.plan, self.config)
        task = type('Task', (), {})()
        session = object.__new__(MicrosoftProjectSession)
        with patch.object(session, 'set_native_resource_group'), patch.object(session, 'write_project_date'):
            session.update_epic_task(task, epic, self.config, self.plan)
        self.assertLessEqual(len(task.Text30), 255)
        self.assertTrue(task.Flag3)
        epic.summary = 'x' * 256
        with self.assertRaisesRegex(J2PError, 'field=Name'):
            validate_project_plan(self.plan, self.config)

    def test_attempted_text_preserves_full_value_and_escapes_controls(self):
        from j2p.project_values import value_metadata
        value = "warning\nnext\titem" + "x" * 300
        self.assertIn(f"attempted_text={value!r}", value_metadata(value))
        self.assertNotIn("\n", value_metadata(value))

    def test_text_boundary_and_summary_checks(self):
        check_project_value('Text8', 'x' * 255, 'test')
        with self.assertRaises(J2PError):
            check_project_value('Text8', 'x' * 256, 'test')
        next(iter(self.plan.summaries.values())).name = 'x' * 256
        with self.assertRaisesRegex(J2PError, 'rollup=.*field=Name'):
            validate_project_plan(self.plan, self.config)

    def test_nonfinite_input_and_aggregates(self):
        for value in ('NaN', 'inf', '-Infinity', '1e999'):
            with self.subTest(value=value), self.assertRaises(J2PError):
                parse_number(value)
        next(iter(self.plan.epics.values())).logged_hours = float('inf')
        with self.assertRaisesRegex(J2PError, 'field=Number3'):
            validate_project_plan(self.plan, self.config)

    def test_mapping_families_ranges_and_collisions(self):
        for mapping in ({'dependency_review': 'Number8'}, {'dependency_review': 'Text31'},
                        {'dependency_review': 'Text1'}, {'unknown': 'Text30'}):
            with self.subTest(mapping=mapping), self.assertRaises(ConfigError):
                load_config(FIXTURES / 'mixed-config.yaml', {'project_fields': mapping})
        for value in (float('nan'), float('inf')):
            with self.assertRaises(ConfigError):
                load_config(FIXTURES / 'mixed-config.yaml', {'metrics': {'hours_per_story_point': value}})

    def test_com_rejection_identifies_field_and_attempted_text(self):
        class RejectingTask:
            def __setattr__(self, field, value):
                if field == 'Text8':
                    raise RuntimeError('secret-content-from-com')
                object.__setattr__(self, field, value)
        epic = next(iter(self.plan.epics.values()))
        epic.dependency_review = 'private-review-text'
        session = object.__new__(MicrosoftProjectSession)
        with patch.object(session, 'set_native_resource_group'):
            with self.assertRaises(ProjectAutomationError) as raised:
                session.update_epic_task(RejectingTask(), epic, self.config, self.plan)
        message = str(raised.exception)
        self.assertIn('field=Text8', message)
        self.assertIn('CSV row=', message)
        self.assertIn('type=str, text_length=19', message)
        self.assertIn("attempted_text='private-review-text'", message)
        self.assertNotIn('secret-content', message)
        self.assertTrue(raised.exception.__suppress_context__)
