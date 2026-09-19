"""Prioritized review conserves evidence from the nine original yerp exports."""

import hashlib
import unittest
from collections import Counter
from pathlib import Path

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.reports import completed_fixversion_report_plan, resource_group_run_plan
from j2p.review_focus import build_review_focus


YERP = Path(__file__).resolve().parents[1] / 'yerp'


def fingerprints(paths):
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def review_items(plan):
    return [item for item in plan.audit_items
            if item.severity in {'Error', 'Warning', 'Review'}
            or item.category == 'FutureInPlanning']


class YerpReviewFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = sorted(YERP.glob('*.csv'))
        config_path = YERP / 'ssn-812-config.yaml'
        if len(cls.files) != 9 or not config_path.is_file():
            raise unittest.SkipTest('The nine private yerp exports are not available')
        cls.before = fingerprints(cls.files)
        cls.config = load_config(config_path)
        cls.plan = build_run_plan(cls.files, cls.config)
        cls.plan.generated_at = '2026-09-19T12:00:00'
        cls.display_plan = completed_fixversion_report_plan(cls.plan)
        cls.focus = build_review_focus(cls.display_plan)
        cls.groups = {group['key']: group for group in cls.focus['groups']}

    @classmethod
    def tearDownClass(cls):
        if fingerprints(cls.files) != cls.before:
            raise AssertionError('A source yerp CSV changed during review analysis')

    def assert_conserved(self, plan, focus):
        expected = review_items(plan)
        actual = [item for group in focus['groups'] for item in group['items']]
        # Identity comparisons detect both dropped evidence and duplicate copies.
        self.assertEqual(Counter(map(id, actual)), Counter(map(id, expected)))
        self.assertEqual(focus['total_audit_count'], len(expected))
        self.assertEqual(sum(group['audit_count'] for group in focus['groups']), len(expected))
        self.assertEqual(len({group['key'] for group in focus['groups']}), len(focus['groups']))
        self.assertEqual(focus['grouped_count'],
                         focus['focus_count'] + focus['later_count'] + focus['historical_count'])

    def test_every_actionable_entry_survives_grouping(self):
        self.assert_conserved(self.display_plan, self.focus)
        self.assertLess(self.focus['grouped_count'], self.focus['total_audit_count'])
        self.assertTrue(any(item.category == 'FutureInPlanning'
                            for item in review_items(self.display_plan)))

    def test_missing_initiatives_collect_their_epic_and_child_evidence(self):
        for key, epic_count, child_count in [('SSWSYS-1386', 53, 419),
                                             ('SSWSYS-1385', 82, 105)]:
            with self.subTest(initiative=key):
                group = self.groups[key]
                counts = Counter(item.category for item in group['items'])
                self.assertEqual(counts['ExcludedMissingRollup'], epic_count)
                self.assertEqual(counts['StoryEpicExcluded'], child_count)
                self.assertNotEqual(group['tier'], 'Historical')

    def test_completed_children_keep_the_open_parent_impact_visible(self):
        context = self.plan.stats['review_issue_context']
        self.assertFalse(context['SSWSYS-2607']['completed'])
        done_children = {key for key, value in context.items()
                         if value.get('parent_epic') == 'SSWSYS-2607'
                         and value.get('completed') is True}
        self.assertTrue(done_children)
        root = self.groups['SSWSYS-1386']
        preserved = {item.jira_key for item in root['items']
                     if item.category == 'StoryEpicExcluded'}
        audited = {item.jira_key for item in review_items(self.display_plan)
                   if item.category == 'StoryEpicExcluded'
                   and item.jira_key in done_children}
        self.assertTrue(audited)
        self.assertTrue(audited <= preserved)
        self.assertIn(root['tier'], {'Fix first', 'Focus now'})

    def test_shared_fixversion_rows_have_one_issue_action(self):
        key = 'SSWGUI-95'
        rows = [epic for epic in self.display_plan.epics.values()
                if (epic.jira_key or epic.key) == key]
        self.assertEqual(len(rows), 6)
        self.assertTrue(any(not epic.drives_schedule for epic in rows))
        candidates = [item for item in review_items(self.display_plan) if item.jira_key == key]
        self.assertGreater(len(candidates), 1)
        groups = [group for group in self.focus['groups']
                  if any(item.jira_key == key for item in group['items'])]
        self.assertEqual(len(groups), 1)
        self.assertEqual(Counter(id(item) for item in groups[0]['items'] if item.jira_key == key),
                         Counter(map(id, candidates)))

    def test_all_nine_resource_views_retain_excluded_and_child_warnings(self):
        original = review_items(self.plan)
        resources = self.config['resource_groups']
        self.assertEqual(len(resources), 9)
        scoped_all = []
        for prefix, resource in sorted(resources.items()):
            with self.subTest(resource=resource):
                scoped = resource_group_run_plan(self.plan, self.config, resource)
                actual = review_items(scoped)
                expected = [item for item in original if item.jira_key.startswith(prefix + '-')]
                self.assertEqual(Counter(map(id, actual)), Counter(map(id, expected)))
                self.assertTrue(any(item.category == 'ExcludedMissingRollup' for item in actual))
                self.assertTrue(any(item.category in {'StoryEpicExcluded', 'StoryEpicNotFound',
                                                      'StoryMissingEpicLink'} for item in actual))
                self.assert_conserved(scoped, build_review_focus(scoped, dependency_plan=self.plan))
                scoped_all.extend(actual)
        self.assertEqual(Counter(map(id, scoped_all)), Counter(map(id, original)))
        self.assertEqual(fingerprints(self.files), self.before)


if __name__ == '__main__':
    unittest.main()
