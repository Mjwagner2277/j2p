"""Original estimates reserve future scope without replacing current child work."""

import copy
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from j2p.config import ConfigError, deep_merge, load_config
from j2p.core import build_run_plan
from j2p.jira import CsvTable, parse_issues
from j2p.models import J2PError
from j2p.reports import write_planned_epics
from j2p.state import run_plan_to_state, snapshots_from_state, write_json


HEADERS = [
    'Issue key', 'Issue Type', 'Summary', 'Epic Link', 'Parent', 'Fix versions',
    'Story Points', 'Original Story Points', 'Status', 'Status Category',
]


def issue(key='TEAM-1', kind='Epic', parent='', current='', original='',
          status='Open', category='', versions='Release 1'):
    return [key, kind, key, parent if kind != 'Epic' else '', '', versions,
            current, original, status, category]


def configuration(**overrides):
    settings = {
        'resource_groups': {'TEAM': 'Team'},
        'rollup_modes': {'TEAM': 'fixVersion'},
        'fixversion_completion_suppression': {'enabled': False},
    }
    deep_merge(settings, overrides)
    return load_config(None, settings)


def plan_for(rows, config=None, headers=HEADERS):
    with patch('j2p.jira.read_csv_rows', return_value=(copy.deepcopy([headers, *rows]), 'utf-8')):
        return build_run_plan(Path('original-estimates.csv'), config or configuration())


def parse_rows(rows, headers=HEADERS):
    with patch('j2p.jira.read_csv_rows', return_value=(copy.deepcopy([headers, *rows]), 'utf-8')):
        return parse_issues(CsvTable(Path('original-estimates.csv')), configuration(), [])


def children():
    return [issue('TEAM-2', 'Story', 'TEAM-1', current='3', status='Done'),
            issue('TEAM-3', 'Task', 'TEAM-1', current='5', status='Open')]


class OriginalStoryPointParsingTests(unittest.TestCase):
    def test_all_original_aliases_are_separate_from_current_story_points(self):
        for alias in ('Original Story Points', 'Original Story Point',
                      'Custom field (Original story points)'):
            with self.subTest(alias=alias):
                headers = list(HEADERS)
                headers[7] = alias
                parsed = parse_rows([issue(current='2.5', original='13.25')], headers)[0]
                self.assertEqual(parsed.story_points, 2.5)
                self.assertEqual(parsed.original_story_points, 13.25)
                plan = plan_for([issue(current='2.5', original='13.25')], headers=headers)
                self.assertEqual(plan.column_map['original_story_points'], alias)
                self.assertEqual(plan.epics['TEAM-1'].total_story_points, 13.25)

    def test_blank_zero_and_fractional_original_values_remain_distinct(self):
        for raw, expected in (('', None), ('0', 0), ('1,000.25', 1000.25), ('.5', .5)):
            with self.subTest(raw=raw):
                parsed = parse_rows([issue(original=raw)])[0]
                self.assertEqual(parsed.original_story_points, expected)
                self.assertIsNone(parsed.story_points)

    def test_original_column_is_optional_and_parent_current_points_are_not_a_fallback(self):
        headers = HEADERS[:7] + HEADERS[8:]
        row = issue(current='34')
        parsed = parse_rows([row[:7] + row[8:]], headers)[0]
        self.assertIsNone(parsed.original_story_points)
        plan = plan_for([row[:7] + row[8:]], headers=headers)
        self.assertEqual(plan.epics['TEAM-1'].total_story_points, 0)

    def test_invalid_original_values_fail_even_when_current_children_have_points(self):
        for raw in ('five', '-2', 'NaN', 'Infinity', '1,5', '1e999', '3 points'):
            with self.subTest(raw=raw), self.assertRaises(J2PError) as raised:
                plan_for([issue(original=raw), *children()])
            message = str(raised.exception)
            for expected in ('original-estimates.csv', 'row 2', 'TEAM-1', 'Original Story Points', raw):
                self.assertIn(expected, message)

    def test_overlapping_exports_with_changed_original_points_fail_with_both_sources(self):
        original = [HEADERS, issue(original='13')]
        changed = [HEADERS, issue(original='21')]
        with patch('j2p.jira.read_csv_rows', side_effect=[(original, 'utf-8'), (changed, 'utf-8')]):
            with self.assertRaises(J2PError) as raised:
                build_run_plan([Path('first.csv'), Path('second.csv')], configuration())
        for expected in ('TEAM-1', 'first.csv row 2', 'second.csv row 2', 'original_story_points'):
            self.assertIn(expected, str(raised.exception))

    def test_identical_overlapping_original_estimates_are_counted_once(self):
        rows = [HEADERS, issue(original='13'), *children()]
        with patch('j2p.jira.read_csv_rows', return_value=(rows, 'utf-8')):
            plan = build_run_plan([Path('first.csv'), Path('second.csv')], configuration())
        self.assertEqual(plan.stats['duplicate_csv_issues_skipped'], 3)
        self.assertEqual(plan.epics['TEAM-1'].total_story_points, 13)
        self.assertEqual(plan.epics['TEAM-1'].completed_story_points, 3)


class OriginalStoryPointSelectionTests(unittest.TestCase):
    def test_no_children_uses_original_at_every_parent_status(self):
        for status in ('Open', 'In Progress', 'In Review', 'Blocked', 'Done', 'Unknown'):
            with self.subTest(status=status):
                epic = plan_for([issue(original='13', status=status)]).epics['TEAM-1']
                self.assertEqual(epic.total_story_points, 13)
                self.assertEqual(epic.completed_story_points, 0)
                self.assertEqual(epic.percent_complete, 0)
                self.assertFalse(epic.in_planning)
                self.assertEqual(epic.original_story_points, 13)
                self.assertEqual(epic.child_story_points, 0)
                self.assertEqual(epic.story_point_basis, 'Original estimate (no children)')

    def test_no_children_missing_or_zero_original_means_zero_estimate(self):
        for raw in ('', '0'):
            with self.subTest(raw=raw):
                epic = plan_for([issue(original=raw)]).epics['TEAM-1']
                self.assertEqual(epic.total_story_points, 0)
                self.assertEqual(epic.completed_story_points, 0)
                self.assertTrue(epic.in_planning)

    def test_not_started_uses_larger_original_or_current_sum_without_adding_both(self):
        for original, total in (('13', 13), ('5', 8), ('8', 8), ('', 8), ('0', 8)):
            with self.subTest(original=original):
                epic = plan_for([issue(original=original), *children()]).epics['TEAM-1']
                self.assertEqual(epic.total_story_points, total)
                self.assertEqual(epic.completed_story_points, 3)
                self.assertEqual(epic.percent_complete, round(3 / total * 100))
                self.assertEqual(epic.child_story_points, 8)
                self.assertEqual(epic.original_story_points, float(original) if original else None)
                self.assertEqual(epic.story_point_basis, 'Higher original estimate (not started)'
                                 if total > 8 else 'Child issues')

    def test_each_default_not_started_status_uses_the_original_floor(self):
        for status in ('To Do', 'Open', 'Backlog', 'New', 'Selected for Development', '  oPeN  '):
            with self.subTest(status=status):
                epic = plan_for([issue(original='13', status=status), *children()]).epics['TEAM-1']
                self.assertEqual(epic.total_story_points, 13)

    def test_started_review_blocked_done_and_unknown_statuses_use_only_current_children(self):
        for status in ('In Progress', 'In Review', 'Blocked', 'Done', 'Unknown', ''):
            with self.subTest(status=status):
                epic = plan_for([issue(original='13', status=status), *children()]).epics['TEAM-1']
                self.assertEqual(epic.total_story_points, 8)
                self.assertEqual(epic.completed_story_points, 3)
                self.assertEqual(epic.percent_complete, 38)

    def test_zero_point_children_are_present_children_for_started_parent(self):
        for current in ('', '0'):
            for status, total in (('Open', 13), ('In Progress', 0), ('Done', 0)):
                with self.subTest(current=current, status=status):
                    child = issue('TEAM-2', 'Task', 'TEAM-1', current=current, original='99', status='Done')
                    epic = plan_for([issue(original='13', status=status), child]).epics['TEAM-1']
                    self.assertEqual(epic.total_story_points, total)
                    self.assertEqual(epic.completed_story_points, 0)

    def test_child_original_estimates_never_substitute_for_current_story_points(self):
        rows = [issue(original='2'),
                issue('TEAM-2', 'Story', 'TEAM-1', current='3', original='50', status='Done'),
                issue('TEAM-3', 'Task', 'TEAM-1', original='100', status='Done')]
        epic = plan_for(rows).epics['TEAM-1']
        self.assertEqual(epic.total_story_points, 3)
        self.assertEqual(epic.completed_story_points, 3)
        self.assertEqual(epic.percent_complete, 100)

    def test_custom_not_started_statuses_use_exact_case_insensitive_matches(self):
        config = configuration(not_started_statuses=['Proposed'])
        for status, total in (('Proposed', 13), ('proposed', 13), ('Proposed Review', 8), ('Open', 8)):
            with self.subTest(status=status):
                epic = plan_for([issue(original='13', status=status), *children()], config).epics['TEAM-1']
                self.assertEqual(epic.total_story_points, total)

    def test_status_category_takes_precedence_over_status_name_and_blank_falls_back(self):
        for status, category, total in (('In Progress', 'To Do', 13), ('Custom', 'to do', 13),
                                        ('Open', 'In Progress', 8), ('Open', 'Done', 8),
                                        ('Open', 'Unknown', 8), ('Open', '', 13)):
            with self.subTest(status=status, category=category):
                rows = [issue(original='13', status=status, category=category), *children()]
                parsed = parse_rows(rows)[0]
                self.assertEqual(parsed.status_category, category)
                self.assertEqual(plan_for(rows).epics['TEAM-1'].total_story_points, total)

    def test_not_started_configuration_accepts_scalar_shorthand_but_rejects_invalid_statuses(self):
        self.assertEqual(configuration(not_started_statuses='Proposed')['not_started_statuses'], ['Proposed'])
        for value in ([None], [True], [''], 3):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                configuration(not_started_statuses=value)

    def test_state_and_csv_retain_estimate_provenance_and_snapshot_keeps_selected_total(self):
        plan = plan_for([issue(original='13'), *children()])
        state = run_plan_to_state(plan)
        stored = state['epics']['TEAM-1']
        self.assertEqual(stored['original_story_points'], 13)
        self.assertEqual(stored['child_story_points'], 8)
        self.assertEqual(stored['story_point_basis'], 'Higher original estimate (not started)')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_json(root / 'state.json', state)
            snapshot = snapshots_from_state(root / 'state.json')['TEAM-1']
            self.assertEqual(snapshot.total_story_points, 13)
            self.assertEqual(snapshot.completed_story_points, 3)
            write_planned_epics(root / 'planned-epics.csv', plan)
            with (root / 'planned-epics.csv').open(newline='') as source:
                exported = next(csv.DictReader(source))
        self.assertEqual(float(exported['original_story_points']), 13)
        self.assertEqual(float(exported['child_story_points']), 8)
        self.assertEqual(float(exported['total_story_points']), 13)
        self.assertEqual(exported['story_point_basis'], 'Higher original estimate (not started)')

    def test_each_accepted_version_receives_estimate_and_completion_while_overall_counts_once(self):
        config = configuration(fixversion_scope={'enabled': True, 'accepted': ['Release 1', 'Release 2']})
        rows = [issue(original='13', versions='Private;Release 1;Release 2'), *children()]
        plan = plan_for(rows, config)
        self.assertEqual(len(plan.epics), 2)
        self.assertEqual(set(plan.summaries), {'fixVersion:Release 1', 'fixVersion:Release 2'})
        for summary in plan.summaries.values():
            self.assertEqual(summary.completion_total_story_points, 13)
            self.assertEqual(summary.completion_completed_story_points, 3)
            self.assertEqual(summary.percent_complete, 23)
        self.assertEqual(sum(summary.total_story_points for summary in plan.summaries.values()), 13)
        self.assertEqual(sum(summary.completed_story_points for summary in plan.summaries.values()), 3)


if __name__ == '__main__':
    unittest.main()
