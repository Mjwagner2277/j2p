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
            status = first_cell(headers, row, 'Status')
            issues[key] = {
                'key': key, 'type': first_cell(headers, row, 'Issue Type'),
                'epic': first_cell(headers, row, 'Custom field (Epic Link)').upper(),
                'parent': first_cell(headers, row, 'Custom field (Parent Link)').upper(),
                'versions': versions,
                'current_points': decimal_value(first_cell(headers, row, 'Custom field (Story Points)')),
                'original_points': decimal_value(first_cell(headers, row, 'Custom field (Original story points)')),
                'hours': logged_hours,
                'status': status,
                'status_category': first_cell(headers, row, 'Status Category'),
                'done': status.lower() in {'done', 'closed', 'resolved'},
                'path': path, 'row': row_number,
            }
    return issues


def independent_selected_points(parent, child_points, child_count):
    if not child_count:
        return parent['original_points']
    if parent['status'].casefold() in {'to do', 'open', 'backlog', 'new', 'selected for development'}:
        return max(child_points, parent['original_points'])
    return child_points


def independent_epic_metrics(issues, epic_keys):
    """Apply the requested estimate rule to independently read Decimal values."""
    totals = {key: [Decimal(0) for _ in range(4)] for key in epic_keys}
    children = defaultdict(list)
    for issue in issues.values():
        if issue['type'] not in {'Story', 'Task', 'Sub-task', 'Bug'} or issue['epic'] not in totals:
            continue
        children[issue['epic']].append(issue)
        bucket = totals[issue['epic']]
        bucket[0] += issue['current_points']
        bucket[2] += issue['hours']
        if issue['done']:
            bucket[1] += issue['current_points']
            bucket[3] += issue['hours']
    for key, bucket in totals.items():
        bucket[0] = independent_selected_points(issues[key], bucket[0], len(children[key]))
    return totals, children


def independent_initiative_metrics(issues, all_epic_metrics):
    """Only the estimate beyond all exported child epics adds new work."""
    children = defaultdict(list)
    for key in all_epic_metrics:
        children[issues[key]['parent']].append(key)
    remaining = {}
    for key, issue in issues.items():
        if issue['type'] != 'Initiative':
            continue
        child_points = sum((all_epic_metrics[child][0] for child in children[key]), Decimal(0))
        selected = independent_selected_points(issue, child_points, len(children[key]))
        remainder = selected - child_points
        if remainder > 0:
            remaining[key] = [remainder, Decimal(0), Decimal(0), Decimal(0)]
    return remaining, children


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
        cls.epic_eligible = {}
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
                cls.epic_eligible[key] = rollups
        cls.all_epic_metrics, cls.all_children = independent_epic_metrics(
            cls.issues, [key for key, issue in cls.issues.items() if issue['type'] == 'Epic'])
        cls.initiative_metrics, cls.initiative_children = independent_initiative_metrics(
            cls.issues, cls.all_epic_metrics)
        cls.initiative_eligible = {}
        for key in cls.initiative_metrics:
            issue = cls.issues[key]
            prefix = key.split('-')[0]
            if prefix not in cls.config['resource_groups']:
                continue
            if cls.config['rollup_modes'][prefix] == 'initiative':
                rollups = [('initiative:' + key, True)]
            else:
                versions = issue['versions']
                if cls.config['fixversion_scope']['enabled']:
                    versions = [v for v in versions if v in cls.config['fixversion_scope']['accepted']]
                rollups = [('fixVersion:' + version, index == 0)
                           for index, version in enumerate(versions)]
            if rollups:
                cls.initiative_eligible[key] = rollups
        cls.eligible = {**cls.epic_eligible, **cls.initiative_eligible}
        cls.metrics = {key: cls.all_epic_metrics[key] for key in cls.epic_eligible}
        cls.metrics.update({key: cls.initiative_metrics[key] for key in cls.initiative_eligible})
        cls.children = {key: cls.all_children[key] for key in cls.epic_eligible}
        cls.included_children = [child for children in cls.children.values() for child in children]

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
        self.assertEqual(Counter(epic.key_prefix for epic in self.plan.epics.values()
                                 if epic.drives_schedule and self.issues[epic.jira_key]['type'] == 'Epic'), {
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
            'epics_included': 1708, 'planned_epic_rows': 3334,
            'initiative_estimates_included': 96,
            'epics_excluded': 306, 'summary_rows': 62,
            'fixversion_scope_excluded_epics': 156,
        }
        for name, expected in expected_stats.items():
            with self.subTest(stat=name):
                self.assertEqual(self.plan.stats[name], expected)
        self.assertEqual(len(self.included_children), 4187)
        self.assertEqual({epic.jira_key for epic in self.plan.epics.values()}, set(self.eligible))
        cross_file_parents = [key for key in self.epic_eligible
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
        added_epics = {key: epic for key, epic in added.items() if self.issues[epic.jira_key]['type'] == 'Epic'}
        self.assertEqual(len(added_epics), 1384)
        self.assertEqual(len({epic.jira_key for epic in added_epics.values()}), 839)
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

    def test_every_actual_row_matches_independent_estimate_selection_and_child_time(self):
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
        self.assertEqual(len(driving), len(self.eligible))
        self.assertEqual(len(driving), 1804)
        self.assertEqual(sum(self.metrics[key][0] for key in self.epic_eligible), Decimal('15452.2'))
        self.assertEqual(sum(self.metrics[key][0] for key in self.initiative_eligible), Decimal('5069.8'))
        self.assertAlmostEqual(sum(epic.total_story_points for epic in driving),
                               float(sum(values[0] for values in self.metrics.values())))
        self.assertAlmostEqual(sum(epic.completed_story_points for epic in driving), 2021.9)
        self.assertEqual(self.plan.stats['logged_hours'], 12171.51)
        self.assertEqual(self.plan.stats['completed_logged_hours'], 11806.12)
        self.assertEqual(self.plan.column_map['story_points'], 'Custom field (Story Points)')
        self.assertEqual(self.plan.column_map['original_story_points'], 'Custom field (Original story points)')

    def test_actual_parent_status_and_child_presence_select_original_or_current_points(self):
        cases = {
            'SSWCYBER-3666': ('To Do', Decimal(10), 1, Decimal('0.5'), Decimal(10)),
            'SSWCYBER-3606': ('In Progress', Decimal(10), 1, Decimal('0.5'), Decimal('0.5')),
            'SSWCYBER-3698': ('To Do', Decimal(2), 0, Decimal(0), Decimal(2)),
            'SSWIF-4752': ('In Progress', Decimal(4), 0, Decimal(0), Decimal(4)),
        }
        for key, (status, original, count, current, selected) in cases.items():
            with self.subTest(key=key):
                issue = self.issues[key]
                children = self.children[key]
                self.assertEqual(issue['status'], status)
                self.assertEqual(issue['original_points'], original)
                self.assertEqual(len(children), count)
                self.assertEqual(sum((child['current_points'] for child in children), Decimal(0)), current)
                for epic in self.plan.epics.values():
                    if epic.jira_key == key:
                        self.assertEqual(Decimal(str(epic.total_story_points)), selected)
                        self.assertEqual(Decimal(str(epic.completed_story_points)),
                                         sum((child['current_points'] for child in children if child['done']), Decimal(0)))
        # A completed parent alone supplies no completed child points.
        completed_childless = self.plan.epics['SSWTEST-4863']
        self.assertEqual(self.issues['SSWTEST-4863']['status'], 'Done')
        self.assertEqual(self.children['SSWTEST-4863'], [])
        self.assertEqual((completed_childless.total_story_points,
                          completed_childless.completed_story_points,
                          completed_childless.percent_complete), (5, 0, 0))

    def test_actual_initiative_rows_add_only_unallocated_estimates(self):
        self.assertEqual(len(self.initiative_eligible), 96)
        estimate_rows = [epic for epic in self.plan.epics.values()
                         if self.issues[epic.jira_key]['type'] == 'Initiative']
        self.assertEqual(len(estimate_rows), 220)
        for epic in estimate_rows:
            with self.subTest(key=epic.key):
                self.assertTrue(epic.estimate_only)
                self.assertEqual(epic.issue_type, 'Initiative')
                self.assertEqual(epic.primary_schedule_key, epic.jira_key + '::ESTIMATE')
                self.assertEqual(epic.completed_story_points, 0)
                self.assertEqual(epic.percent_complete, 0)
                self.assertEqual(epic.logged_hours, 0)
                self.assertEqual(epic.completed_logged_hours, 0)
                self.assertIn(epic.fix_version, self.issues[epic.jira_key]['versions'])
        cases = {
            'SSWSYS-5153': (Decimal(875), Decimal(0), Decimal(875)),
            'SSWCYBER-3597': (Decimal(150), Decimal('64.2'), Decimal('85.8')),
            'SSWSYS-5065': (Decimal(188), Decimal(11), Decimal(177)),
        }
        for key, (original, accumulated, remaining) in cases.items():
            with self.subTest(initiative=key):
                self.assertEqual(self.issues[key]['original_points'], original)
                self.assertEqual(sum((self.all_epic_metrics[child][0]
                                      for child in self.initiative_children[key]), Decimal(0)), accumulated)
                primary = self.plan.epics[key + '::ESTIMATE']
                self.assertEqual(Decimal(str(primary.total_story_points)), remaining)
                self.assertEqual(Decimal(str(primary.child_story_points)), accumulated)
        # All exported epics count in the deduction even when a version filter
        # excludes a child. Started parents never add an estimate on top of them.
        self.assertEqual(len(self.initiative_children['SSWIF-3910']), 34)
        self.assertTrue(any(child not in self.epic_eligible
                            for child in self.initiative_children['SSWIF-3910']))
        for key in ('SSWSYS-5010', 'SSWIF-3910'):
            self.assertNotIn(key, self.initiative_metrics)
            self.assertNotIn(key + '::ESTIMATE', self.plan.epics)

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
        expected_total = float(sum(values[0] for values in self.metrics.values()))
        self.assertAlmostEqual(sum(item.total_story_points for item in self.plan.summaries.values()), expected_total)
        self.assertAlmostEqual(sum(item.completed_story_points for item in self.plan.summaries.values()), 2021.9)
        self.assertEqual(sum(not epic.drives_schedule for epic in self.plan.epics.values()),
                         sum(len(rollups) - 1 for rollups in self.eligible.values()))
        self.assertGreater(sum(item.completion_total_story_points for item in self.plan.summaries.values()), expected_total)

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
        self.assertEqual(issue['current_points'], Decimal(3))
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
        changed_batches = {**self.batches, issue['path']: (changed_rows, encoding)}
        updated_issues = independent_issues(changed_batches)
        updated_all_epic_metrics, _ = independent_epic_metrics(updated_issues, self.all_epic_metrics)
        updated_initiative_metrics, _ = independent_initiative_metrics(updated_issues, updated_all_epic_metrics)
        updated_metrics = {key: updated_all_epic_metrics[key] for key in self.epic_eligible}
        updated_metrics.update({key: updated_initiative_metrics[key] for key in self.initiative_eligible})
        total_delta = updated_metrics[CHANGED_EPIC][0] - self.metrics[CHANGED_EPIC][0]
        done_delta = updated_metrics[CHANGED_EPIC][1] - self.metrics[CHANGED_EPIC][1]
        self.assertEqual(done_delta, Decimal(5))
        affected_rollups = dict(self.eligible[CHANGED_EPIC])
        self.assertEqual(len(affected_rollups), 4)
        changed_epics = []
        for key, before in self.plan.epics.items():
            after = updated.epics[key]
            if before.jira_key != CHANGED_EPIC:
                self.assertEqual(after, before)
                continue
            changed_epics.append(key)
            self.assertEqual(after.total_story_points, float(updated_metrics[CHANGED_EPIC][0]))
            self.assertEqual(after.completed_story_points, float(updated_metrics[CHANGED_EPIC][1]))
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
            self.assertAlmostEqual(after.completion_total_story_points, before.completion_total_story_points + float(total_delta))
            self.assertAlmostEqual(after.completion_completed_story_points, before.completion_completed_story_points + float(done_delta))
            driving = affected_rollups[key]
            self.assertAlmostEqual(after.total_story_points, before.total_story_points + (float(total_delta) if driving else 0))
            self.assertAlmostEqual(after.completed_story_points, before.completed_story_points + (float(done_delta) if driving else 0))
        self.assertAlmostEqual(sum(item.total_story_points for item in updated.summaries.values()),
                               float(sum(metric[0] for metric in updated_metrics.values())))
        self.assertAlmostEqual(sum(item.completed_story_points for item in updated.summaries.values()),
                               float(sum(metric[1] for metric in updated_metrics.values())))
