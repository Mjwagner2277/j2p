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
from j2p.project_values import check_project_value, validate_project_plan

FIXTURES = Path(__file__).parent / 'fixtures'


class ProjectValueTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(FIXTURES / 'mixed-config.yaml')
        self.plan = build_run_plan(FIXTURES / 'project-wide-jira-initial.csv', self.config)

    def test_validate_rejects_generated_long_dependency_review(self):
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
            self.assertEqual(result, 2)
            self.assertIn('epic=TEAM-101', error.getvalue())
            self.assertIn('field=Text8', error.getvalue())
            self.assertIn('text_length=379', error.getvalue())
            self.assertNotIn('ABSENT-100', error.getvalue())
            self.assertNotIn('Validation complete', output.getvalue())
            self.assertFalse(list(Path(tmp).rglob('j2p-state*.json')))

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

    def test_com_rejection_identifies_field_without_disclosing_value(self):
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
        self.assertNotIn('private-review-text', message)
        self.assertNotIn('secret-content', message)
        self.assertTrue(raised.exception.__suppress_context__)
