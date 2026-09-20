"""Missing-date provenance across all nine original yerp exports, read only."""

import csv
import hashlib
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.project_values import epic_assignments
from j2p.reports import write_dependency_review
from j2p.review_focus import build_review_focus


YERP = Path(__file__).resolve().parents[1] / 'yerp'


def fingerprints(paths):
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def missing_prefix(epic):
    if not epic.target_start and not epic.target_end:
        return 'Missing Jira target start/end.'
    if not epic.target_start:
        return 'Missing Jira target start.'
    if not epic.target_end:
        return 'Missing Jira target end.'
    return ''


class YerpMissingDateNotesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = sorted(YERP.glob('*.csv'))
        config_path = YERP / 'ssn-812-config.yaml'
        if len(cls.files) != 9 or not config_path.is_file():
            raise unittest.SkipTest('The nine private yerp exports are not available')
        cls.before = fingerprints(cls.files)
        cls.config = load_config(config_path)
        cls.plan = build_run_plan(cls.files, cls.config)
        # Isolate the annotation from every other planning and review behavior.
        with patch('j2p.core.add_missing_target_date_reviews'):
            cls.unannotated = build_run_plan(cls.files, cls.config)
        cls.plan.generated_at = cls.unannotated.generated_at = '2026-09-19T12:00:00'
        cls.missing = {key: epic for key, epic in cls.plan.epics.items()
                       if not epic.target_start or not epic.target_end}

    @classmethod
    def tearDownClass(cls):
        if fingerprints(cls.files) != cls.before:
            raise AssertionError('An original yerp CSV changed during missing-date checks')

    def test_exact_scope_and_complete_real_project_population(self):
        self.assertEqual(len(self.plan.epics), 2016)
        self.assertEqual(len({epic.jira_key for epic in self.plan.epics.values()}), 1521)
        self.assertTrue(self.missing)
        self.assertLess(len(self.missing), len(self.plan.epics))
        annotated = {key for key, epic in self.plan.epics.items()
                     if epic.dependency_review.startswith('Missing Jira target ')}
        self.assertEqual(annotated, set(self.missing))
        for key, epic in self.plan.epics.items():
            with self.subTest(schedule_key=key):
                if key in self.missing:
                    self.assertTrue(epic.dependency_review.startswith(missing_prefix(epic)))
                else:
                    self.assertEqual(epic.dependency_review,
                                     self.unannotated.epics[key].dependency_review)

    def test_active_and_reference_rows_keep_accurate_provenance_and_existing_notes(self):
        self.assertTrue(any(epic.drives_schedule for epic in self.missing.values()))
        self.assertTrue(any(not epic.drives_schedule for epic in self.missing.values()))
        old_review_count = 0
        for key, epic in self.missing.items():
            with self.subTest(schedule_key=key):
                if epic.drives_schedule:
                    self.assertIn(
                        'Project uses existing dates or the nearest available dates '
                        'allowed by dependencies and calendars.', epic.dependency_review)
                    self.assertNotIn('Reference row; primary', epic.dependency_review)
                else:
                    self.assertIn(
                        f'Reference row; primary {epic.primary_schedule_key} drives the schedule.', epic.dependency_review)
                    self.assertIn('Active copy.', epic.dependency_review)
                previous = self.unannotated.epics[key].dependency_review
                if previous:
                    old_review_count += 1
                    self.assertTrue(epic.dependency_review.endswith(previous))
        self.assertGreater(old_review_count, 0)

    def test_annotations_preserve_all_dates_dependencies_points_and_rollups(self):
        self.assertEqual(set(self.plan.epics), set(self.unannotated.epics))
        for key, epic in self.plan.epics.items():
            with self.subTest(schedule_key=key):
                actual = asdict(epic)
                expected = asdict(self.unannotated.epics[key])
                actual.pop('dependency_review')
                expected.pop('dependency_review')
                self.assertEqual(actual, expected)
        self.assertEqual(self.plan.summaries, self.unannotated.summaries)
        self.assertEqual(fingerprints(self.files), self.before)

    def test_project_display_bounds_keep_missing_date_note_visible(self):
        review_field = self.config['project_fields']['dependency_review']
        flag_field = self.config['project_fields']['dependency_review_needed']
        for key, epic in self.plan.epics.items():
            with self.subTest(schedule_key=key):
                values = dict(epic_assignments(epic, self.config))
                self.assertLessEqual(len(values[review_field]), 255)
                if key in self.missing:
                    self.assertTrue(values[review_field].startswith(missing_prefix(epic)))
                    self.assertTrue(values[flag_field])

    def test_informational_evidence_is_complete_without_inflating_focus_now(self):
        notes = [item for item in self.plan.audit_items
                 if item.category == 'MissingJiraTargetDates']
        self.assertEqual(len(notes), len(self.missing))
        self.assertEqual({item.schedule_key for item in notes}, set(self.missing))
        for item in notes:
            with self.subTest(schedule_key=item.schedule_key):
                self.assertEqual(item.severity, 'Info')
                self.assertEqual(item.field, 'Dependency Review')
                self.assertIn(missing_prefix(self.missing[item.schedule_key]), item.message)
        self.assertEqual(build_review_focus(self.plan), build_review_focus(self.unannotated))
        with tempfile.TemporaryDirectory(prefix='j2p-yerp-missing-dates-') as tmp:
            path = Path(tmp) / 'dependency-review.csv'
            write_dependency_review(path, self.plan.audit_items)
            with path.open(newline='', encoding='utf-8-sig') as source:
                rows = [row for row in csv.DictReader(source)
                        if row['category'] == 'MissingJiraTargetDates']
            self.assertEqual(len(rows), len(self.missing))
            self.assertEqual({row['schedule_key'] for row in rows}, set(self.missing))


if __name__ == '__main__':
    unittest.main()
