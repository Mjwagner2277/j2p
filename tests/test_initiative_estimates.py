"""Initiative estimates cover only scope not already represented by child epics."""

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from j2p.config import deep_merge, load_config
from j2p.core import build_run_plan
from j2p.initiative_estimates import add_initiative_estimates
from j2p.models import JiraIssue
from j2p.rollups import build_summaries


def source(key, kind='Initiative', **values):
    fields = dict(
        key=key, issue_id=key, issue_type=kind, summary='Scope ' + key,
        epic_link='', parent='', fix_versions=['Shared'], story_points=None,
        original_story_points=None, logged_hours=0, status='Open', status_category='',
        resolution='', resolved='', target_start='2027-01-04', target_end='2027-03-01',
        warning_suppression_date='', predecessors=set(), successors=set(),
        source_row=2, source_file='in-memory.csv',
    )
    fields.update(values)
    return JiraIssue(**fields)


def configuration(**overrides):
    settings = {
        'resource_groups': {'TEAM': 'Team'},
        'rollup_modes': {'TEAM': 'fixVersion'},
        'fixversion_scope': {'enabled': True, 'accepted': ['Shared', 'Next']},
    }
    deep_merge(settings, overrides)
    return load_config(None, settings)


def estimates(initiative, epics=(), stories=None, config=None):
    rows, audit = {}, []
    add_initiative_estimates(rows, {initiative.key: initiative}, epics, stories or {},
                            config or configuration(), audit)
    return rows, audit


class InitiativeEstimateTests(unittest.TestCase):
    def test_empty_initiative_uses_original_estimate_and_preserves_provenance(self):
        initiative = source('TEAM-100', original_story_points=21)
        rows, audit = estimates(initiative)
        self.assertEqual(set(rows), {'TEAM-100::ESTIMATE'})
        row = rows['TEAM-100::ESTIMATE']
        self.assertEqual((row.total_story_points, row.completed_story_points), (21, 0))
        self.assertEqual((row.original_story_points, row.child_story_points), (21, 0))
        self.assertEqual(row.story_point_basis, 'Remaining initiative estimate')
        self.assertEqual(row.issue_type, 'Initiative')
        self.assertTrue(row.estimate_only)
        self.assertFalse(row.in_planning)
        self.assertEqual(row.row_role, 'Estimate')
        self.assertEqual(row.jira_key, initiative.key)
        self.assertEqual(row.primary_schedule_key, row.key)
        self.assertEqual(row.summary, initiative.summary + ' — Remaining estimate')
        self.assertEqual((row.target_start, row.target_end), (initiative.target_start, initiative.target_end))
        self.assertEqual((row.logged_hours, row.completed_logged_hours, row.story_point_ratio), (0, 0, 0))
        self.assertEqual((row.resource_group, row.key_prefix, row.source_file), ('Team', 'TEAM', 'in-memory.csv'))
        self.assertTrue(all(item.severity == 'Info' for item in audit))
        note = next(item for item in audit if item.category == 'InitiativeEstimate')
        for value in ('original=21', 'exported child epics=0', 'selected total=21', 'remaining estimate=21'):
            self.assertIn(value, note.message)

    def test_initiative_mode_places_remainder_under_its_own_summary_without_requiring_parent(self):
        config = configuration(rollup_modes={'TEAM': 'initiative'})
        rows, _ = estimates(source('TEAM-100', original_story_points=13, fix_versions=[]), config=config)
        row = rows['TEAM-100::ESTIMATE']
        self.assertEqual((row.rollup_mode, row.rollup_key, row.fix_version), ('initiative', 'TEAM-100', ''))
        summaries = build_summaries(rows, config)
        self.assertEqual(set(summaries), {'initiative:TEAM-100'})
        self.assertEqual(summaries['initiative:TEAM-100'].total_story_points, 13)

    def test_unstarted_parent_deducts_effective_child_epic_original_estimates(self):
        parent = source('TEAM-100', original_story_points=55)
        epics = [source('TEAM-1', 'Epic', parent=parent.key, original_story_points=13),
                 source('TEAM-2', 'Epic', parent=parent.key, original_story_points=21)]
        story = source('TEAM-3', 'Story', epic_link='TEAM-2', story_points=3)
        rows, _ = estimates(parent, epics, {'TEAM-2': [story]})
        row = rows['TEAM-100::ESTIMATE']
        self.assertEqual(row.child_story_points, 34)
        self.assertEqual(row.total_story_points, 21)

    def test_started_child_uses_current_work_in_initiative_deduction(self):
        parent = source('TEAM-100', original_story_points=21)
        epic = source('TEAM-1', 'Epic', parent=parent.key, original_story_points=13, status='In Progress')
        story = source('TEAM-2', 'Story', epic_link=epic.key, story_points=3, status='Done')
        rows, _ = estimates(parent, [epic], {epic.key: [story]})
        self.assertEqual(rows['TEAM-100::ESTIMATE'].total_story_points, 18)
        self.assertEqual(rows['TEAM-100::ESTIMATE'].completed_story_points, 0)

    def test_started_initiative_with_children_has_no_extra_original_estimate(self):
        for status in ('In Progress', 'In Review', 'Blocked', 'Done', 'Unknown'):
            with self.subTest(status=status):
                parent = source('TEAM-100', original_story_points=55, status=status)
                epic = source('TEAM-1', 'Epic', parent=parent.key, original_story_points=13)
                self.assertEqual(estimates(parent, [epic])[0], {})

    def test_no_child_started_or_done_initiative_still_uses_original_without_delivered_points(self):
        for status in ('In Progress', 'Done'):
            with self.subTest(status=status):
                rows, _ = estimates(source('TEAM-100', original_story_points=13, status=status))
                row = rows['TEAM-100::ESTIMATE']
                self.assertEqual((row.total_story_points, row.completed_story_points), (13, 0))
                self.assertEqual(row.completed, status == 'Done')

    def test_unstarted_parent_with_equal_or_larger_child_scope_has_no_remainder(self):
        for original in (None, 0, 8, 13):
            with self.subTest(original=original):
                parent = source('TEAM-100', original_story_points=original)
                epic = source('TEAM-1', 'Epic', parent=parent.key, original_story_points=13)
                self.assertEqual(estimates(parent, [epic])[0], {})

    def test_all_exported_child_scope_is_deducted_even_when_children_are_not_accepted(self):
        parent = source('TEAM-100', original_story_points=34)
        epics = [source('TEAM-1', 'Epic', parent=parent.key, original_story_points=13, fix_versions=['Private']),
                 source('OTHER-1', 'Epic', parent=parent.key, original_story_points=8, fix_versions=[])]
        rows, _ = estimates(parent, epics)
        self.assertEqual(rows['TEAM-100::ESTIMATE'].child_story_points, 21)
        self.assertEqual(rows['TEAM-100::ESTIMATE'].total_story_points, 13)

    def test_initiative_uses_own_versions_and_does_not_inherit_child_tags(self):
        parent = source('TEAM-100', original_story_points=21, fix_versions=['Next'])
        epic = source('TEAM-1', 'Epic', parent=parent.key, original_story_points=8, fix_versions=['Shared'])
        rows, _ = estimates(parent, [epic])
        self.assertEqual({row.fix_version for row in rows.values()}, {'Next'})
        parent.fix_versions = []
        rows, audit = estimates(parent, [epic])
        self.assertFalse(rows)
        self.assertEqual(audit[0].category, 'ExcludedInitiativeEstimateScope')
        self.assertIn('not inherited', audit[0].message)

    def test_accepted_versions_share_completion_scope_without_duplicating_overall_points(self):
        parent = source('TEAM-100', original_story_points=21, fix_versions=['Private', 'Shared', 'Next'])
        rows, audit = estimates(parent)
        self.assertEqual(len(rows), 2)
        primary = rows['TEAM-100::ESTIMATE']
        reference = next(row for row in rows.values() if not row.drives_schedule)
        self.assertEqual((primary.fix_version, primary.row_role), ('Shared', 'Estimate'))
        self.assertEqual((reference.fix_version, reference.row_role), ('Next', 'Reference'))
        self.assertEqual(reference.primary_schedule_key, primary.key)
        summaries = build_summaries(rows, configuration())
        self.assertEqual(sum(summary.total_story_points for summary in summaries.values()), 21)
        self.assertEqual({summary.completion_total_story_points for summary in summaries.values()}, {21})
        self.assertEqual({summary.completion_completed_story_points for summary in summaries.values()}, {0})
        self.assertTrue(any(item.category == 'IgnoredInitiativeEstimateFixVersion' for item in audit))

    def test_estimate_key_remains_stable_until_child_scope_consumes_it(self):
        parent = source('TEAM-100', original_story_points=13)
        expected_key = 'TEAM-100::ESTIMATE'
        for child_points, remainder in ((0, 13), (3, 10), (8, 5), (13, 0), (21, 0)):
            with self.subTest(child_points=child_points):
                epic = source('TEAM-1', 'Epic', parent=parent.key, original_story_points=child_points)
                rows, _ = estimates(parent, [epic])
                self.assertEqual(set(rows), {expected_key} if remainder else set())
                if remainder:
                    self.assertEqual(rows[expected_key].total_story_points, remainder)

    def test_scope_and_resource_rejections_remain_explicit_informational_audits(self):
        for key, versions, category in (
            ('OTHER-100', ['Shared'], 'ExcludedInitiativeEstimateResource'),
            ('TEAM-100', ['Private'], 'ExcludedInitiativeEstimateScope'),
            ('TEAM-100', ['shared'], 'ExcludedInitiativeEstimateScope'),
        ):
            with self.subTest(key=key, versions=versions):
                rows, audit = estimates(source(key, original_story_points=21, fix_versions=versions))
                self.assertFalse(rows)
                self.assertEqual([(item.severity, item.category, item.jira_key) for item in audit],
                                 [('Info', category, key)])

    def test_status_category_and_custom_not_started_setting_apply_to_initiative(self):
        epic = source('TEAM-1', 'Epic', parent='TEAM-100', original_story_points=8)
        for status, category, expected in (('Proposed', '', 13), ('Open', 'In Progress', 0),
                                           ('Custom', 'To Do', 13)):
            with self.subTest(status=status, category=category):
                parent = source('TEAM-100', original_story_points=21, status=status, status_category=category)
                rows, _ = estimates(parent, [epic], config=configuration(not_started_statuses=['Proposed']))
                self.assertEqual(sum(row.total_story_points for row in rows.values()), expected)

    def test_split_policy_preserves_driving_estimate_roles(self):
        config = configuration(multi_fixversion_policy={'default': 'split'})
        parent = source('TEAM-100', original_story_points=13, fix_versions=['Shared', 'Next'])
        rows, _ = estimates(parent, config=config)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.row_role == 'Estimate' and row.drives_schedule for row in rows.values()))

    def test_calculation_does_not_mutate_source_issues_or_unrelated_planned_rows(self):
        parent = source('TEAM-100', original_story_points=21, fix_versions=['Private', 'Shared'])
        epic = source('TEAM-1', 'Epic', parent=parent.key, original_story_points=8)
        before = copy.deepcopy((parent, epic))
        marker = object()
        planned = {'EXISTING': marker}
        add_initiative_estimates(planned, {parent.key: parent}, [epic], {}, configuration(), [])
        self.assertEqual((parent, epic), before)
        self.assertIs(planned['EXISTING'], marker)
        self.assertEqual(planned['TEAM-100::ESTIMATE'].total_story_points, 13)

    def test_full_plan_accounts_for_epic_and_remaining_initiative_scope_in_both_modes(self):
        csv_rows = [
            ['Issue key', 'Issue Type', 'Summary', 'Epic Link', 'Parent', 'Fix versions',
             'Story Points', 'Original Story Points', 'Status'],
            ['TEAM-100', 'Initiative', 'Initiative', '', '', 'Next', '', '21', 'Open'],
            ['TEAM-1', 'Epic', 'Epic', '', 'TEAM-100', 'Shared', '', '8', 'Open'],
            ['TEAM-2', 'Story', 'Story', 'TEAM-1', '', 'Shared', '3', '', 'Done'],
        ]
        for mode in ('fixVersion', 'initiative'):
            with self.subTest(mode=mode):
                config = configuration(rollup_modes={'TEAM': mode})
                with patch('j2p.jira.read_csv_rows', return_value=(copy.deepcopy(csv_rows), 'utf-8')):
                    plan = build_run_plan(Path('in-memory.csv'), config)
                self.assertEqual(set(plan.epics), {'TEAM-1', 'TEAM-100::ESTIMATE'})
                self.assertEqual(plan.epics['TEAM-1'].total_story_points, 8)
                self.assertEqual(plan.epics['TEAM-100::ESTIMATE'].total_story_points, 13)
                self.assertEqual(sum(row.total_story_points for row in plan.summaries.values()), 21)
                self.assertEqual(sum(row.completed_story_points for row in plan.summaries.values()), 3)
                self.assertEqual(set(plan.summaries), {'fixVersion:Shared', 'fixVersion:Next'}
                                 if mode == 'fixVersion' else {'initiative:TEAM-100'})


if __name__ == '__main__':
    unittest.main()
