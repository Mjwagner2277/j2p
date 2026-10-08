"""Shared version scope remains exact and stable across export coverage."""
import copy
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from j2p.config import ConfigError, deep_merge, load_config
from j2p.core import build_run_plan
from j2p.reference_dates import synchronize_reference_dates, verify_reference_dates
from j2p.rollups import fix_version_schedule_key, summary_identity
from yerp_project_support import project_from_yerp_plan


HEADERS = ['Issue key', 'Issue Type', 'Summary', 'Epic Link', 'Parent',
           'Fix versions', 'Story Points', 'Status']
ROWS = [HEADERS,
        ['A-1', 'Epic', 'A epic', '', 'A-100', 'Private A;Shared;Next', '', 'Open'],
        ['A-2', 'Task', 'Three points', 'A-1', '', 'Private A', '3', 'Done'],
        ['A-3', 'Epic', 'Private epic', '', 'A-100', 'Private A', '', 'Open'],
        ['A-4', 'Task', 'Private points', 'A-3', '', '', '7', 'Open'],
        ['A-5', 'Epic', 'Untagged epic', '', '', '', '', 'Open'],
        ['B-1', 'Epic', 'B epic', '', '', 'Shared', '', 'Open'],
        ['B-2', 'Task', 'Five points', 'B-1', '', '', '5', 'Open'],
        ['A-100', 'Initiative', 'Logical parent', '', '', '', '', 'Open']]


def configuration(**overrides):
    settings = {
        'resource_groups': {'A': 'Team A', 'B': 'Team B'},
        'rollup_modes': {'A': 'fixVersion', 'B': 'fixVersion'},
        'fixversion_scope': {'enabled': True, 'accepted': ['Shared', 'Next']},
    }
    deep_merge(settings, overrides)
    return load_config(None, settings)


def plan_for(rows=ROWS, config=None):
    config = config or configuration()
    with patch('j2p.jira.read_csv_rows', return_value=(copy.deepcopy(rows), 'utf-8')):
        return build_run_plan(Path('synthetic.csv'), config), config


class FixVersionScopeTests(unittest.TestCase):
    def test_shared_header_merges_teams_and_credits_each_version_without_duplicate_totals(self):
        plan, _ = plan_for()
        self.assertEqual(set(plan.summaries), {'fixVersion:Shared', 'fixVersion:Next'})
        shared = plan.summaries['fixVersion:Shared']
        self.assertEqual((shared.project_key, shared.completion_total_story_points,
                          shared.completion_completed_story_points), ('MULTIPLE', 8, 3))
        other = plan.summaries['fixVersion:Next']
        self.assertEqual((other.completion_total_story_points, other.completion_completed_story_points), (3, 3))
        self.assertEqual(sum(s.total_story_points for s in plan.summaries.values()), 8)
        self.assertEqual(plan.epics['A-1'].rollup_key, 'Shared')
        self.assertTrue(plan.epics['A-1'].drives_schedule)
        reference = plan.epics[fix_version_schedule_key('A-1', 'Next')]
        self.assertFalse(reference.drives_schedule)
        self.assertEqual(reference.primary_schedule_key, 'A-1')
        self.assertEqual(plan.stats['fixversion_scope_excluded_epics'], 1)
        self.assertEqual(plan.stats['ignored_fixversion_memberships'], 2)
        for key in ('A-3', 'A-4'):
            exclusions = [a for a in plan.audit_items if a.jira_key == key
                          and a.category in {'ExcludedFixVersionScope', 'StoryEpicOutsideFixVersionScope',
                                             'ExcludedMissingRollup', 'StoryEpicExcluded'}]
            self.assertTrue(exclusions)
            self.assertTrue(all(a.severity == 'Info' for a in exclusions))
        self.assertTrue(any(a.category == 'ExcludedMissingRollup' and a.jira_key == 'A-5'
                            for a in plan.audit_items))

    def test_one_project_upload_still_uses_the_static_shared_list(self):
        rows = [ROWS[0], *[r for r in ROWS[1:] if r[0].startswith('A-')]]
        plan, _ = plan_for(rows)
        self.assertEqual(set(plan.summaries), {'fixVersion:Shared', 'fixVersion:Next'})
        self.assertEqual(plan.summaries['fixVersion:Shared'].completion_total_story_points, 3)

    def test_filter_is_opt_in_and_does_not_change_initiative_mode(self):
        unfiltered, _ = plan_for(config=configuration(fixversion_scope={'enabled': False}))
        self.assertIn('fixVersion:Private A', unfiltered.summaries)
        self.assertEqual(unfiltered.epics['A-1'].rollup_key, 'Private A')
        mixed, _ = plan_for(config=configuration(rollup_modes={'A': 'initiative', 'B': 'fixVersion'}))
        self.assertIn('initiative:A-100', mixed.summaries)
        self.assertIn('A-3', mixed.epics)
        self.assertEqual(mixed.stats['fixversion_scope_excluded_epics'], 0)

    def test_literal_case_and_internal_spacing_are_significant(self):
        rows = [HEADERS, ['A-1', 'Epic', 'A', '', '', 'PI17;PI 17;pi17', '', 'Open']]
        config = configuration(fixversion_scope={'accepted': ['PI17', 'PI 17']})
        plan, _ = plan_for(rows, config)
        self.assertEqual(set(plan.summaries), {'fixVersion:PI17', 'fixVersion:PI 17'})
        self.assertEqual(plan.stats['ignored_fixversion_memberships'], 1)

    def test_allowlist_order_does_not_change_jira_primary_order_or_source_values(self):
        first, _ = plan_for()
        second, _ = plan_for(config=configuration(fixversion_scope={'accepted': ['Next', 'Shared']}))
        self.assertEqual(first.epics, second.epics)
        self.assertEqual(ROWS[1][5], 'Private A;Shared;Next')

    def test_split_policy_still_applies_only_to_accepted_memberships(self):
        plan, _ = plan_for(config=configuration(multi_fixversion_policy={'default': 'split'}))
        rows = [e for e in plan.epics.values() if e.jira_key == 'A-1']
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(e.drives_schedule and e.row_role == 'Split' for e in rows))

    def test_case_distinct_headers_remain_separate_through_project_lookup_and_date_verification(self):
        rows = [HEADERS, ['A-1', 'Epic', 'A', '', '', 'Shared;shared', '', 'Open']]
        plan, config = plan_for(rows, configuration(fixversion_scope={'accepted': ['Shared', 'shared']}))
        session, epics, summaries = project_from_yerp_plan(plan, config)
        indexed = session.index_rollup_summaries(config, list(summaries.values()))
        self.assertEqual(set(indexed), {('fixVersion', 'Shared'), ('fixVersion', 'shared')})
        for name in ('Shared', 'shared'):
            self.assertIs(session.find_rollup_summary('fixVersion', name, config), summaries['fixVersion:' + name])
        self.assertEqual(summary_identity('initiative', 'a-100'), ('initiative', 'A-100'))
        raw = {'A-1': (epics['A-1'].Start, epics['A-1'].Finish)}
        with redirect_stdout(io.StringIO()):
            synchronize_reference_dates(session, plan, config, epics, indexed, raw)
            session.recalculate()
            self.assertGreater(verify_reference_dates(session, plan, config, epics, indexed), 0)

    def test_invalid_allowlist_fails_instead_of_silently_importing_everything(self):
        for settings in ({'accepted': []}, {'accepted': 'Shared'}, {'accepted': ['Shared', 'Shared']},
                         {'accepted': [' Shared']}, {'accepted': [3]}, {'enabled': 'true'}):
            with self.subTest(settings=settings), self.assertRaises(ConfigError):
                configuration(fixversion_scope=settings)


if __name__ == '__main__':
    unittest.main()
