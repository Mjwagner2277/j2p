"""Prioritized review conserves evidence from the nine original yerp exports."""

import hashlib
import unittest
from collections import Counter, defaultdict
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
        cls.contexts = cls.plan.stats['review_issue_context']
        cls.children = defaultdict(set)
        for key, context in cls.contexts.items():
            if context.get('parent_epic'):
                cls.children[context['parent_epic']].add(key)

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
                         focus['focus_count'] + focus['later_count']
                         + focus['unscheduled_count'] + focus['historical_count'])

    def assert_priority_has_jira_dates(self, focus):
        for group in focus['groups']:
            if group['tier'] not in {'Fix first', 'Focus now'}:
                continue
            # An omission is one parent/initiative action, so its unfinished
            # parent and siblings are relevant alongside the audited issues.
            entity_keys = {item.jira_key for item in group['items'] if item.jira_key}
            parents = set()
            for item in group['items']:
                context = self.contexts.get(item.jira_key, {})
                if item.category in {'StoryEpicExcluded', 'StoryEpicNotFound'}:
                    parents.add(context.get('parent_epic') or item.old_value)
                elif item.category == 'ExcludedMissingRollup' and context.get('missing_rollup_parent'):
                    parents.add(item.jira_key)
            for parent in parents:
                entity_keys.update(self.children.get(parent, ()))
                if parent in self.contexts:
                    entity_keys.add(parent)
            entities = [self.contexts.get(key, {}) for key in entity_keys]
            relevant = [entity for entity in entities if entity.get('completed') is not True] or entities
            with self.subTest(high_priority_group=group['key']):
                self.assertTrue(any(entity.get('target_start') or entity.get('target_end')
                                    for entity in relevant),
                                'High-priority issue groups need a Jira target-date anchor')

    def test_high_priority_groups_have_jira_dates(self):
        self.assert_priority_has_jira_dates(self.focus)
        self.assertGreater(self.focus['unscheduled_count'], 0)

    def test_undated_omitted_epic_and_all_43_audits_remain_unscheduled(self):
        key = 'SSWSW-11775'
        group = self.groups[key]
        affected = self.children[key] | {key}
        unfinished = [self.contexts[item_key] for item_key in affected
                      if self.contexts[item_key].get('completed') is not True]
        self.assertEqual(len(unfinished), 43)
        self.assertTrue(all(not item.get('target_start') and not item.get('target_end')
                            for item in unfinished))
        self.assertEqual(group['tier'], 'Unscheduled')
        self.assertEqual(group['audit_count'], 43)
        self.assertEqual(Counter(item.category for item in group['items']),
                         Counter({'ExcludedMissingRollup': 1, 'StoryEpicExcluded': 42}))
        self.assertTrue(group['actions'])
        self.assertTrue(any('No Jira target dates' in reason for reason in group['reasons']))

    def test_undated_dependency_drivers_do_not_enter_high_priority(self):
        all_unfinished = {group['key']: group for group in
                          build_review_focus(self.display_plan, days=0)['groups']}
        for key in ['SSWSYS-3846', 'SSWSYS-3848', 'SSWSYS-3849']:
            with self.subTest(epic=key):
                context = self.contexts[key]
                self.assertFalse(context['completed'])
                self.assertFalse(context['target_start'])
                self.assertFalse(context['target_end'])
                group = self.groups[key]
                self.assertEqual(group['tier'], 'Unscheduled')
                self.assertEqual(all_unfinished[key]['tier'], 'Unscheduled')
                self.assertEqual(group['downstream_count'], 2)
                self.assertTrue(any('Impacts 2 unfinished downstream' in reason
                                    for reason in group['reasons']))
                self.assertEqual(group['audit_count'], 1)

    def test_undated_parent_with_dated_unfinished_children_still_has_priority(self):
        key = 'SSWSW-10328'
        self.assertFalse(self.contexts[key]['target_start'])
        self.assertFalse(self.contexts[key]['target_end'])
        dated_open_children = {child for child in self.children[key]
                               if self.contexts[child].get('completed') is not True
                               and (self.contexts[child].get('target_start')
                                    or self.contexts[child].get('target_end'))}
        self.assertEqual(dated_open_children,
                         {'SSWSW-11847', 'SSWSW-11848', 'SSWSW-11849', 'SSWSW-11850'})
        self.assertIn(self.groups[key]['tier'], {'Fix first', 'Focus now'})
        self.assertEqual(self.groups[key]['audit_count'], 25)

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
                scoped_focus = build_review_focus(scoped, dependency_plan=self.plan)
                self.assert_conserved(scoped, scoped_focus)
                self.assert_priority_has_jira_dates(scoped_focus)
                scoped_all.extend(actual)
        self.assertEqual(Counter(map(id, scoped_all)), Counter(map(id, original)))
        self.assertEqual(fingerprints(self.files), self.before)


if __name__ == '__main__':
    unittest.main()
