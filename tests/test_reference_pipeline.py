"""Reference synchronization participates in the final schedule and update lifecycle."""

import copy
import io
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from j2p.project import ProjectAutomationError
from j2p.rollups import build_summaries
from test_project_schedule_colors import schedule_plan
from yerp_project_support import project_from_yerp_plan


def fixture():
    plan, config = schedule_plan(2)
    first, second = plan.epics.values()
    for epic, version in ((first, 'Release A'), (second, 'Release B')):
        epic.rollup_mode = 'fixVersion'
        epic.rollup_key = epic.rollup_name = epic.fix_version = version
        epic.row_role = 'Primary'
        epic.primary_schedule_key = epic.key
    reference = replace(first, key=first.key + '@REFERENCE', rollup_key='Release B',
                        rollup_name='Release B', fix_version='Release B',
                        row_role='Reference', drives_schedule=False)
    plan.epics[reference.key] = reference
    plan.summaries = build_summaries(plan.epics, config)
    plan.audit_items = []
    session, epics, summaries = project_from_yerp_plan(plan, config)
    session.app.Calculation = -1
    session.prepare_formatting_view = Mock(return_value=[])
    session.project_table_column_positions = Mock(return_value={})
    session.color_project_cell_error = Mock(return_value='')
    session.app.FontStrikethrough = Mock(return_value=True)
    rows = {task.ID: task for task in session.project.Tasks.items}

    def select(**kwargs):
        session.app.ActiveCell = SimpleNamespace(Task=rows[kwargs['Row']])
        return True

    session.app.SelectRow = Mock(side_effect=select)
    return session, plan, config, epics, summaries, reference


class ReferencePipelineTests(unittest.TestCase):
    def test_final_formatting_reuses_one_scan_and_preserves_primary_schedule(self):
        session, plan, config, epics, summaries, reference = fixture()
        primary = epics[reference.primary_schedule_key]
        primary.task.Start = datetime(2026, 8, 25, 9, 17)
        primary.task.Finish = datetime(2026, 10, 8, 18, 43)
        original = copy.deepcopy(plan.epics)
        session._reference_summary_tasks = (id(plan), {
            (summary.rollup_mode, summary.key.upper()): summaries[key]
            for key, summary in plan.summaries.items()
        })
        session.index_rollup_summaries = Mock(side_effect=AssertionError('Extra rollup scan'))
        with redirect_stdout(io.StringIO()):
            session.apply_review_formatting(plan, config, before={})
        self.assertEqual(session.project.Tasks.item_reads, len(session.project.Tasks.items))
        self.assertEqual({field for field, _ in primary.writes}, {'Date3', 'Date4'})
        self.assertEqual((epics[reference.key].Start, epics[reference.key].Finish),
                         (primary.Start, primary.Finish))
        header = summaries['fixVersion:Release B']
        self.assertEqual((header.Date3, header.Date4), (primary.Start, primary.Finish))
        self.assertIs(header.Manual, False)
        self.assertFalse(any(field in {'Start', 'Finish', 'StartText', 'FinishText'} for field, _ in header.writes))
        self.assertIs(epics[reference.key].Active, False)
        self.assertEqual(session.app.Calculation, -1)
        session.app.FontStrikethrough.assert_called_once_with(False)
        self.assertEqual(session.app.ActiveCell.Task, epics[reference.key])
        self.assertEqual(plan.epics, original)
        self.assertTrue(session._reference_dates_synchronized)
        self.assertIn('reference_dates', plan.stats['project_update_seconds'])
        self.assertIn('reference_formatting', plan.stats['project_update_seconds'])

    def test_full_verification_rechecks_live_reference_dates_after_synchronization(self):
        session, plan, config, epics, summaries, reference = fixture()
        with redirect_stdout(io.StringIO()):
            session.apply_review_formatting(plan, config, before={})
            self.assertGreater(session.verify_plan(plan, config), 0)
        # Model saved readback losing a minute while the primary stays intact.
        epics[reference.key].task.Finish += timedelta(minutes=1)
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                ProjectAutomationError, 'synchronized reference dates'):
            session.verify_plan(plan, config)

    def test_unchanged_update_keeps_automatic_rollup_without_mode_or_date_churn(self):
        session, plan, config, epics, summaries, reference = fixture()
        with redirect_stdout(io.StringIO()):
            session.begin_selective_update()
            before = session.snapshot_tasks(config)
            session.apply_plan(plan, config)
            session.end_selective_update(plan)
            session.apply_review_formatting(plan, config, before=before)
        self.assertEqual([write for task in session.project.Tasks.items for write in task.writes], [])
        self.assertEqual(plan.stats['project_reference_dates']['written'], 0)
        self.assertIs(summaries['fixVersion:Release B'].Manual, False)

    def test_legacy_manual_summary_is_restored_to_automatic_before_schedule_readback(self):
        session, plan, config, epics, summaries, reference = fixture()
        header = summaries['fixVersion:Release B']
        header.task.Manual = True
        with redirect_stdout(io.StringIO()):
            session.begin_selective_update()
            session.snapshot_tasks(config)
            session.apply_plan(plan, config)
            session.end_selective_update(plan)
        self.assertIs(header.Manual, False)
        self.assertEqual([write for write in header.writes if write[0] == 'Manual'], [('Manual', False)])
        self.assertIs(epics[reference.key].Active, False)
        self.assertIs(epics[reference.key].Flag2, False)


if __name__ == '__main__':
    unittest.main()
