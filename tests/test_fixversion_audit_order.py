"""Release audit provenance is stable when overlapping exports are reordered."""

import unittest
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from j2p.config import load_config
from j2p.core import build_run_plan


class FixVersionAuditOrderTests(unittest.TestCase):
    def assert_release_audit_stable(self, resolved, category, hidden_count):
        config = load_config(None, {
            'rollup_modes': {'AAA': 'fixVersion', 'BBB': 'fixVersion'},
            'resource_groups': {'AAA': 'Alpha', 'BBB': 'Beta'},
            'fixversion_completion_suppression': {
                'enabled': True, 'as_of_date': '2026-09-19',
                'stale_after_days': 90, 'keep_audit_summary': True,
            },
        })
        headers = ['Issue key', 'Issue Type', 'Summary', 'Epic Link',
                   'Fix versions', 'Story Points', 'Status', 'Resolved']
        paths = [Path('alpha.csv'), Path('beta.csv')]
        batches = {
            paths[0]: [headers, ['AAA-1', 'Epic', 'Alpha work', '', 'Shared release', '', 'Done', resolved]],
            paths[1]: [headers, ['BBB-1', 'Epic', 'Beta work', '', 'Shared release', '', 'Done', resolved]],
        }
        with patch('j2p.jira.read_csv_rows', side_effect=lambda path, *_: (batches[path], 'utf-8')):
            first = build_run_plan(paths, config)
            repeated = build_run_plan(list(reversed(paths)) + paths, config)
        self.assertEqual(first.epics, repeated.epics)
        self.assertEqual(first.summaries, repeated.summaries)
        self.assertEqual(repeated.stats['duplicate_csv_issues_skipped'], 2)
        self.assertEqual(first.stats['suppressed_completed_fixversion_rollups'], hidden_count)
        self.assertEqual(repeated.stats['suppressed_completed_fixversion_rollups'], hidden_count)
        expected_audit = [item for item in first.audit_items if item.category == category]
        self.assertEqual(len(expected_audit), 1)
        self.assertEqual(expected_audit[0].jira_key, 'AAA-1')
        self.assertEqual(expected_audit[0].source_file, 'alpha.csv')

        def signature(plan):
            return Counter(repr(sorted(asdict(item).items())) for item in plan.audit_items
                           if item.category != 'DuplicateCsvIssueSkipped')

        self.assertEqual(signature(first), signature(repeated))

    def test_suppressed_release_audit_keeps_same_issue_provenance(self):
        self.assert_release_audit_stable('2025-11-14', 'SuppressedCompletedFixVersion', 1)

    def test_missing_resolved_audit_keeps_same_issue_provenance(self):
        self.assert_release_audit_stable('', 'CompletedFixVersionMissingResolvedDate', 0)
