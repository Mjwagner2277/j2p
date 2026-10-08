"""Integration coverage for the nine supplied yerp exports, read without edits.

These tests deliberately use the real project configuration and exports. A
checkout without the private exports skips this suite explicitly. Mutations
representing later exports exist only in memory; source files are fingerprinted.
"""

import csv
import hashlib
import io
import re
import unittest
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.models import J2PError


YERP = Path(__file__).resolve().parents[1] / 'yerp'
EXPECTED_FILE_COUNTS = {
    'SSWCYBER': 937, 'SSWGUI': 199, 'SSWHW': 1256,
    'SSWIF': 1737, 'SSWNET': 531, 'SSWSW': 1492,
    'SSWSYS': 2665, 'SSWTEST': 1947, 'SSWUMS': 758,
}
CHANGED_CHILD = 'SSWSW-11866'
CHANGED_EPIC = 'SSWSW-10804'


def fingerprints(paths):
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def raw_batches(paths):
    """Read independently of J2P's parser, retaining duplicate Jira headers."""
    batches = {}
    for path in paths:
        data = path.read_bytes()
        for encoding in ('utf-8-sig', 'cp1252', 'latin-1'):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        rows = list(csv.reader(io.StringIO(text, newline='')))
        batches[path] = (rows, encoding)
    return batches


def cells(headers, row, name):
    return [row[index].strip() for index, header in enumerate(headers)
            if header.strip() == name and row[index].strip()]


def first_cell(headers, row, *names):
    for name in names:
        values = cells(headers, row, name)
        if values:
            return values[0]
    return ''


def decimal_value(value):
    return Decimal(value.replace(',', '') or '0')


def independent_issues(batches):
    """Use literal export fields and Decimal totals, not planning helpers."""
    issues = {}
    for path, (rows, _) in batches.items():
        headers = rows[0]
        for row_number, row in enumerate(rows[1:], start=2):
            key = first_cell(headers, row, 'Issue key').upper()
            if not key:
                continue
            versions = []
            for value in cells(headers, row, 'Fix Version/s'):
                for part in re.split(r'[;,\n]+', value):
                    version = part.strip()
                    if version and version not in versions:
                        versions.append(version)
            hours = first_cell(headers, row, 'Custom field (Time Spent Total (hrs))')
            seconds = first_cell(headers, row, 'Time Spent')
            logged_hours = (decimal_value(hours) if hours else
                            decimal_value(seconds) / Decimal(3600) if seconds else
                            decimal_value(first_cell(headers, row, 'Worklog Hours')))
            if key in issues:
                raise AssertionError('The supplied baseline unexpectedly contains duplicate keys')
            issues[key] = {
                'key': key, 'type': first_cell(headers, row, 'Issue Type'),
                'epic': first_cell(headers, row, 'Custom field (Epic Link)').upper(),
                'parent': first_cell(headers, row, 'Custom field (Parent Link)').upper(),
                'versions': versions,
                'points': decimal_value(first_cell(
                    headers, row, 'Custom field (Story Points)',
                    'Custom field (Original story points)')),
                'hours': logged_hours,
                'done': first_cell(headers, row, 'Status').lower() in {'done', 'closed', 'resolved'},
                'path': path, 'row': row_number,
            }
    return issues


class YerpExportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = sorted(YERP.glob('*.csv'))
        if not cls.paths or not (YERP / 'ssn-812-config.yaml').exists():
            raise unittest.SkipTest('Private yerp CSV exports/configuration are not available')
        cls.before_hashes = fingerprints(cls.paths)
        cls.config = load_config(YERP / 'ssn-812-config.yaml')
        cls.batches = raw_batches(cls.paths)
        cls.issues = independent_issues(cls.batches)
        cls.plan = build_run_plan(cls.paths, cls.config)
        cls.eligible = {}
        for key, issue in cls.issues.items():
            prefix = key.split('-')[0]
            if issue['type'] != 'Epic' or prefix not in cls.config['resource_groups']:
                continue
            mode = cls.config['rollup_modes'][prefix]
            if mode == 'initiative':
                parent = cls.issues.get(issue['parent'])
                if not parent or parent['type'] != 'Initiative':
                    continue
                rollups = [('initiative:' + issue['parent'], True)]
            else:
                versions = issue['versions']
                if cls.config['fixversion_scope']['enabled']:
                    versions = [v for v in versions if v in cls.config['fixversion_scope']['accepted']]
                rollups = [('fixVersion:' + version, index == 0)
                           for index, version in enumerate(versions)]
            if rollups:
                cls.eligible[key] = rollups
        cls.metrics = {key: [Decimal(0) for _ in range(4)] for key in cls.eligible}
        cls.included_children = []
        for issue in cls.issues.values():
            if issue['type'] not in {'Story', 'Task', 'Sub-task', 'Bug'} or issue['epic'] not in cls.metrics:
                continue
            cls.included_children.append(issue)
            totals = cls.metrics[issue['epic']]
            totals[0] += issue['points']
            totals[2] += issue['hours']
            if issue['done']:
                totals[1] += issue['points']
                totals[3] += issue['hours']

    @classmethod
    def tearDownClass(cls):
        if fingerprints(cls.paths) != cls.before_hashes:
            raise AssertionError('An original yerp CSV changed during integration testing')

    def test_all_nine_actual_exports_and_cross_file_parent_links_are_included(self):
        actual_counts = {}
        for path, (rows, _) in self.batches.items():
            prefixes = {first_cell(rows[0], row, 'Issue key').split('-')[0]
                        for row in rows[1:]}
            self.assertEqual(len(prefixes), 1)
            actual_counts[prefixes.pop()] = len(rows) - 1
        self.assertEqual(actual_counts, EXPECTED_FILE_COUNTS)
        self.assertEqual(len(self.issues), 11522)
        self.assertEqual(set(self.config['resource_groups']), set(EXPECTED_FILE_COUNTS))
        self.assertEqual(set(self.config['rollup_modes']), set(EXPECTED_FILE_COUNTS))
        self.assertFalse(any(item.category == 'ExcludedUnknownPrefix' for item in self.plan.audit_items))
        self.assertEqual(Counter(epic.key_prefix for epic in self.plan.epics.values() if epic.drives_schedule), {
            'SSWCYBER': 77, 'SSWGUI': 49, 'SSWHW': 151, 'SSWIF': 265, 'SSWNET': 65,
            'SSWSW': 132, 'SSWSYS': 311, 'SSWTEST': 509, 'SSWUMS': 149,
        })
        for prefix in ('SSWIF', 'SSWNET', 'SSWTEST'):
            self.assertEqual(self.config['rollup_modes'][prefix], 'fixVersion')
        expected_stats = {
            'csv_files_read': 9, 'csv_rows_read': 11522,
            'jira_issues_read': 11522, 'unique_issues_read': 11522,
            'duplicate_csv_issues_skipped': 0, 'initiatives_read': 401,
            'epics_read': 2014, 'story_rows_read': 9107,
            'story_rows_used_for_completion': 4187,
            'epics_included': 1708, 'planned_epic_rows': 3114,
            'epics_excluded': 306, 'summary_rows': 62,
            'fixversion_scope_excluded_epics': 156,
        }
        for name, expected in expected_stats.items():
            with self.subTest(stat=name):
                self.assertEqual(self.plan.stats[name], expected)
        self.assertEqual(len(self.included_children), 4187)
        self.assertEqual({epic.jira_key for epic in self.plan.epics.values()}, set(self.eligible))
        cross_file_parents = [key for key in self.eligible
                              if self.config['rollup_modes'][key.split('-')[0]] == 'initiative'
                              and self.issues[key]['parent'] in self.issues
                              and self.issues[key]['path'] != self.issues[self.issues[key]['parent']]['path']]
        self.assertEqual(len(cross_file_parents), 0)
        for key in cross_file_parents:
            self.assertEqual(self.plan.epics[key].rollup_key, self.issues[key]['parent'])

    def test_expanded_project_scope_preserves_existing_rows_and_shared_list(self):
        prior_config = deepcopy(self.config)
        added_prefixes = {'SSWIF', 'SSWNET', 'SSWTEST'}
        for prefix in added_prefixes:
            prior_config['rollup_modes'].pop(prefix)
            prior_config['resource_groups'].pop(prefix)
        prior = build_run_plan(self.paths, prior_config)
        added = {key: epic for key, epic in self.plan.epics.items() if key not in prior.epics}
        self.assertEqual(len(added), 1384)
        self.assertEqual(len({epic.jira_key for epic in added.values()}), 839)
        self.assertEqual({epic.key_prefix for epic in added.values()}, added_prefixes)
        for key, before in prior.epics.items():
            after = self.plan.epics[key]
            # Expanding scope can resolve previously missing cross-project links.
            for field, value in asdict(before).items():
                if field not in {'predecessors', 'successors', 'dependency_review'}:
                    self.assertEqual(getattr(after, field), value, (key, field))
        unresolved = Counter(issue['key'].split('-')[0] for issue in self.issues.values()
                             if issue['type'] == 'Epic'
                             and issue['key'].split('-')[0] in added_prefixes
                             and not issue['versions'])
        self.assertEqual(unresolved, {'SSWIF': 45, 'SSWNET': 10, 'SSWTEST': 26})
        # Missing source memberships stay explicit; no invented release is assigned.
        for issue in self.issues.values():
            if issue['type'] == 'Epic' and issue['key'].split('-')[0] in added_prefixes and not issue['versions']:
                self.assertNotIn(issue['key'], self.plan.epics)

    def test_every_actual_epic_matches_independent_child_points_and_time(self):
        for epic in self.plan.epics.values():
            expected = self.metrics[epic.jira_key]
            for index, field in enumerate(('total_story_points', 'completed_story_points',
                                           'logged_hours', 'completed_logged_hours')):
                with self.subTest(key=epic.key, field=field):
                    actual = getattr(epic, field)
                    self.assertEqual(actual, round(actual, 2))
                    # The implementation uses binary floats. At exact decimal
                    # midpoints such as 216.525 hours, either adjacent cent can
                    # result; converting this independent Decimal oracle to a
                    # float before rounding would add its own tie-breaking bias.
                    # Still require every result to be within half a cent of the
                    # exact raw total (epsilon only for recurring seconds/3600).
                    self.assertLessEqual(abs(Decimal(str(actual)) - expected[index]),
                                         Decimal('0.005000000001'))
        driving = [epic for epic in self.plan.epics.values() if epic.drives_schedule]
        self.assertEqual(len(driving), 1708)
        self.assertAlmostEqual(sum(epic.total_story_points for epic in driving), 4081.5)
        self.assertAlmostEqual(sum(epic.completed_story_points for epic in driving), 2021.9)
        self.assertEqual(self.plan.stats['logged_hours'], 12171.51)
        self.assertEqual(self.plan.stats['completed_logged_hours'], 11806.12)
        self.assertEqual(self.plan.column_map['story_points'], 'Custom field (Story Points)')

    def test_static_list_equals_exact_cross_project_names_in_the_october_exports(self):
        projects = defaultdict(set)
        for issue in self.issues.values():
            for version in issue['versions']:
                projects[version].add(issue['key'].split('-')[0])
        shared = {name for name, prefixes in projects.items() if len(prefixes) >= 2}
        self.assertEqual(len(shared), 62)
        self.assertEqual(len(projects) - len(shared), 206)
        self.assertEqual(set(self.config['fixversion_scope']['accepted']), shared)
        self.assertEqual(set(self.config['rollup_modes'].values()), {'fixVersion'})
        self.assertEqual({summary.key for summary in self.plan.summaries.values()}, shared)
        self.assertIn('PI17', shared)
        self.assertIn('PI 17', shared)
        self.assertEqual(self.plan.summaries['fixVersion:FST'].project_key, 'MULTIPLE')
        self.assertFalse(any(summary.rollup_mode == 'initiative' for summary in self.plan.summaries.values()))

    def test_every_rollup_counts_references_for_completion_only(self):
        expected = defaultdict(lambda: [Decimal(0) for _ in range(4)])
        for key, rollups in self.eligible.items():
            total, done = self.metrics[key][:2]
            for rollup_id, driving in rollups:
                expected[rollup_id][2] += total
                expected[rollup_id][3] += done
                if driving:
                    expected[rollup_id][0] += total
                    expected[rollup_id][1] += done
        self.assertEqual(set(expected), set(self.plan.summaries))
        for key, summary in self.plan.summaries.items():
            for index, field in enumerate(('total_story_points', 'completed_story_points',
                                           'completion_total_story_points', 'completion_completed_story_points')):
                with self.subTest(rollup=key, field=field):
                    self.assertAlmostEqual(getattr(summary, field), float(expected[key][index]), places=2)
        self.assertAlmostEqual(sum(item.total_story_points for item in self.plan.summaries.values()), 4081.5)
        self.assertAlmostEqual(sum(item.completed_story_points for item in self.plan.summaries.values()), 2021.9)
        self.assertEqual(sum(not epic.drives_schedule for epic in self.plan.epics.values()), 1406)
        self.assertGreater(sum(item.completion_total_story_points for item in self.plan.summaries.values()), 4081.5)

    def test_actual_fractional_completion_and_completed_reference_inputs(self):
        # This historical regression epic has no accepted version. Keep its
        # fractional-progress coverage using supported initiative mode in memory.
        config = deepcopy(self.config)
        config['rollup_modes']['SSWCYBER'] = 'initiative'
        epic = build_run_plan(self.paths, config).epics['SSWCYBER-3219']
        self.assertNotIn('SSWCYBER-3219', self.plan.epics)
        self.assertEqual((epic.total_story_points, epic.completed_story_points, epic.percent_complete), (3.2, 0.2, 6))
        self.assertFalse(epic.completed)
        completed_references = [epic for epic in self.plan.epics.values()
                                if epic.completed and not epic.drives_schedule]
        self.assertEqual(len(completed_references), 129)
        for reference in completed_references:
            primary = self.plan.epics[reference.primary_schedule_key]
            self.assertTrue(primary.drives_schedule)
            self.assertTrue(primary.completed)
            self.assertEqual(reference.percent_complete, primary.percent_complete)

    def test_reversed_overlapping_actual_exports_do_not_change_plan_or_double_count(self):
        repeated = build_run_plan(list(reversed(self.paths)) + self.paths, self.config)
        self.assertEqual(repeated.stats['csv_files_read'], 18)
        self.assertEqual(repeated.stats['duplicate_csv_issues_skipped'], 11522)
        self.assertEqual(repeated.stats['unique_issues_read'], 11522)
        self.assertEqual(repeated.epics, self.plan.epics)
        self.assertEqual(repeated.summaries, self.plan.summaries)
        # The only additional audit content describes intentionally repeated rows.
        def audit_signature(plan):
            return Counter(repr(sorted(asdict(item).items())) for item in plan.audit_items
                           if item.category != 'DuplicateCsvIssueSkipped')
        actual_audit, expected_audit = audit_signature(repeated), audit_signature(self.plan)
        self.assertEqual((actual_audit - expected_audit, expected_audit - actual_audit),
                         (Counter(), Counter()))

    def test_conflicting_actual_child_overlap_reports_both_locations(self):
        issue = self.issues[CHANGED_CHILD]
        rows, encoding = self.batches[issue['path']]
        conflict = list(rows[issue['row'] - 1])
        conflict[rows[0].index('Custom field (Story Points)')] = '5'
        extra = YERP / 'in-memory-conflicting-overlap.csv'
        def load(path, *args):
            return ([rows[0], conflict], encoding) if path == extra else self.batches[path]
        with patch('j2p.jira.read_csv_rows', side_effect=load):
            with self.assertRaises(J2PError) as raised:
                build_run_plan(self.paths + [extra], self.config)
        error = str(raised.exception)
        self.assertIn('Conflicting duplicate Jira key ' + CHANGED_CHILD, error)
        self.assertIn(f"{issue['path']} row {issue['row']}", error)
        self.assertIn(f'{extra} row 2', error)
        self.assertIn('story_points', error)
        self.assertFalse(extra.exists())

    def test_real_child_update_recomputes_all_four_accepted_versions_and_only_affected_rollups(self):
        issue = self.issues[CHANGED_CHILD]
        self.assertEqual(issue['epic'], CHANGED_EPIC)
        self.assertEqual(issue['points'], Decimal(3))
        self.assertFalse(issue['done'])
        rows, encoding = self.batches[issue['path']]
        changed_rows = list(rows)
        changed_row = list(rows[issue['row'] - 1])
        changed_row[rows[0].index('Custom field (Story Points)')] = '5'
        changed_row[rows[0].index('Status')] = 'Done'
        changed_rows[issue['row'] - 1] = changed_row
        def load(path, *args):
            return (changed_rows, encoding) if path == issue['path'] else self.batches[path]
        with patch('j2p.jira.read_csv_rows', side_effect=load):
            updated = build_run_plan(self.paths, self.config)
        affected_rollups = dict(self.eligible[CHANGED_EPIC])
        self.assertEqual(len(affected_rollups), 4)
        changed_epics = []
        for key, before in self.plan.epics.items():
            after = updated.epics[key]
            if before.jira_key != CHANGED_EPIC:
                self.assertEqual(after, before)
                continue
            changed_epics.append(key)
            self.assertEqual(after.total_story_points, before.total_story_points + 2)
            self.assertEqual(after.completed_story_points, before.completed_story_points + 5)
            self.assertEqual(after.percent_complete, round(after.completed_story_points / after.total_story_points * 100))
            self.assertEqual(after.logged_hours, before.logged_hours)
            self.assertAlmostEqual(after.completed_logged_hours,
                                   round(before.completed_logged_hours + float(issue['hours']), 2), places=2)
            self.assertEqual(after.predecessors, before.predecessors)
        self.assertEqual(len(changed_epics), 4)
        for key, before in self.plan.summaries.items():
            after = updated.summaries[key]
            if key not in affected_rollups:
                self.assertEqual(after, before)
                continue
            self.assertAlmostEqual(after.completion_total_story_points, before.completion_total_story_points + 2)
            self.assertAlmostEqual(after.completion_completed_story_points, before.completion_completed_story_points + 5)
            driving = affected_rollups[key]
            self.assertAlmostEqual(after.total_story_points, before.total_story_points + (2 if driving else 0))
            self.assertAlmostEqual(after.completed_story_points, before.completed_story_points + (5 if driving else 0))
        self.assertAlmostEqual(sum(item.total_story_points for item in updated.summaries.values()), 4083.5)
        self.assertAlmostEqual(sum(item.completed_story_points for item in updated.summaries.values()), 2026.9)
