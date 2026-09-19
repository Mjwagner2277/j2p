"""Bounded scheduling checkpoints and exception-safe application mode control."""
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import test_project_update_integration as fixtures
from j2p.project import MicrosoftProjectSession, ProjectAutomationError, apply_plan_to_sandbox, create_project_from_plan


class CalculationApp:
    def __init__(self, mode=-1, fail_pause=False, fail_restore=False):
        self.mode = mode
        self.writes = []
        self.fail_pause = fail_pause
        self.fail_restore = fail_restore

    @property
    def Calculation(self):
        return self.mode

    @Calculation.setter
    def Calculation(self, value):
        self.writes.append(value)
        if value == -1 and self.fail_restore:
            raise RuntimeError('restore rejected')
        self.mode = value
        if value == 0 and self.fail_pause:
            raise RuntimeError('pause setter partially succeeded')


class CalculationCheckpointTests(unittest.TestCase):
    def test_quarters_small_empty_and_uneven_row_counts(self):
        for count, expected in [(0, []), (1, [1]), (2, [1, 2]), (3, [1, 2, 3]),
                                (4, [1, 2, 3, 4]), (5, [2, 3, 4, 5]),
                                (10, [3, 5, 8, 10]), (12, [3, 6, 9, 12])]:
            with self.subTest(rows=count), redirect_stdout(io.StringIO()):
                session, plan, config = fixtures.ProjectUpdateIntegrationTests().fixture()
                plan.epics = dict(list(plan.epics.items())[:count])
                session.update_epic_task = Mock(wraps=session.update_epic_task)
                observed = []
                session.recalculate.side_effect = lambda: observed.append(session.update_epic_task.call_count)
                session.apply_plan(plan, config, write_dependencies=False)
                self.assertEqual(observed, expected)
                self.assertEqual(plan.stats['project_row_calculation_checkpoints'], expected)

    def test_dependencies_follow_last_row_calculation_without_duplicate_precalculation(self):
        session, plan, config = fixtures.ProjectUpdateIntegrationTests().fixture()
        events = []
        session.recalculate.side_effect = lambda: events.append('calculate')
        session.apply_dependencies = Mock(side_effect=lambda *args: events.append('dependencies'))
        with redirect_stdout(io.StringIO()):
            session.apply_plan(plan, config)
        self.assertEqual(events, ['calculate'] * 4 + ['dependencies'])

    def test_midway_scheduler_changes_survive_unchanged_source_and_are_reported(self):
        session, plan, config = fixtures.ProjectUpdateIntegrationTests().fixture()
        task = session.project.Tasks.items[-1]
        task.task.Start, task.task.Finish = task.task.Date1, task.task.Date2
        session.begin_selective_update()
        before = session.snapshot_tasks(config)
        original = task.task.Finish
        # Change a native date on a row not yet visited at the first checkpoint.
        def calculate():
            task.task.Finish = '2030-01-01'
        session.recalculate.side_effect = calculate
        with redirect_stdout(io.StringIO()):
            session.apply_plan(plan, config)
            session.end_selective_update(plan)
            session.add_schedule_review_items(plan, before, config)
            session.verify_plan(plan, config)
        self.assertNotEqual(original, task.task.Finish)
        self.assertFalse(any(field == 'Finish' for field, _ in task.writes))
        self.assertTrue(any(a.schedule_key == task.Text1 and a.field in {'Finish', 'Start/Finish'}
                            for a in plan.audit_items), [(a.category, a.field) for a in plan.audit_items])

    def session(self, app):
        session = object.__new__(MicrosoftProjectSession)
        session.app = app
        return session

    def test_original_automatic_and_manual_modes_are_restored(self):
        for mode in (-1, 0):
            for fail in (False, True):
                with self.subTest(mode=mode, fail=fail):
                    app = CalculationApp(mode)
                    with redirect_stdout(io.StringIO()):
                        try:
                            with self.session(app).defer_automatic_calculation():
                                self.assertEqual(app.Calculation, 0)
                                if fail:
                                    raise RuntimeError('write/checkpoint failed')
                        except RuntimeError as exc:
                            self.assertTrue(fail)
                            self.assertEqual(str(exc), 'write/checkpoint failed')
                    self.assertEqual(app.Calculation, mode)
                    self.assertEqual(app.writes, [0, -1] if mode == -1 else [])

    def test_partially_failed_pause_restores_setting_before_propagating_error(self):
        app = CalculationApp(fail_pause=True)
        with self.assertRaisesRegex(ProjectAutomationError, 'partially succeeded'):
            with self.session(app).defer_automatic_calculation():
                self.fail('Writes must not start after failed pause')
        self.assertEqual(app.Calculation, -1)
        self.assertEqual(app.writes, [0, -1])

    def test_unreadable_mode_blocks_bulk_writes(self):
        with self.assertRaisesRegex(ProjectAutomationError, 'Could not read'):
            with self.session(SimpleNamespace()).defer_automatic_calculation():
                self.fail('Writes must not start with unknown mode')

    def test_restore_failure_is_not_silently_ignored(self):
        app = CalculationApp(fail_restore=True)
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'Could not restore'):
            with self.session(app).defer_automatic_calculation():
                pass

    def test_update_and_creation_restore_before_review_save_and_verification(self):
        for create in (False, True):
            with self.subTest(create=create):
                session = MagicMock()
                session.app = CalculationApp()
                session.__enter__.return_value = session
                session.snapshot_tasks.return_value = {}
                session.defer_automatic_calculation.side_effect = lambda: MicrosoftProjectSession.defer_automatic_calculation(session)
                events = []
                def writing(*args, **kwargs):
                    self.assertEqual(session.app.Calculation, 0)
                    events.append('write')
                def calculating():
                    self.assertEqual(session.app.Calculation, 0)
                    events.append('calculate')
                def finished(*args):
                    self.assertEqual(session.app.Calculation, -1)
                    self.assertEqual(events[-1], 'calculate')
                session.apply_plan.side_effect = writing
                session.apply_plan_dependencies.side_effect = writing
                session.recalculate.side_effect = calculating
                session.add_schedule_review_items.side_effect = finished
                session.apply_review_formatting.side_effect = finished
                session.save.side_effect = finished
                session.verify_saved_plan.side_effect = finished
                plan = SimpleNamespace(epics={'A': object()}, stats={}, audit_items=[])
                with patch('j2p.project.MicrosoftProjectSession', return_value=session), redirect_stdout(io.StringIO()):
                    if create:
                        create_project_from_plan(Path('sandbox.mpp'), plan, {})
                    else:
                        apply_plan_to_sandbox(Path('sandbox.mpp'), plan, {})
                session.verify_saved_plan.assert_called_once_with(plan, {})
                if create:
                    session.cache_resources.assert_called_once_with()
                    session.apply_plan.assert_called_once_with(plan, {}, write_dependencies=False, append_only=True)
                    self.assertIn("apply_changes", plan.stats["project_update_seconds"])
                    self.assertEqual(plan.stats["project_run_mode"], "create")
                self.assertEqual(session.app.writes, [0, -1])

    def test_failed_checkpoint_stops_writes_and_restores_calculation_mode(self):
        session, plan, config = fixtures.ProjectUpdateIntegrationTests().fixture()
        session.app.Calculation = -1
        session.update_epic_task = Mock(wraps=session.update_epic_task)
        session.recalculate.side_effect = ProjectAutomationError('checkpoint failed')
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'checkpoint failed'):
            with session.defer_automatic_calculation():
                session.apply_plan(plan, config)
        self.assertEqual(session.update_epic_task.call_count, 3)
        self.assertEqual(session.app.Calculation, -1)
        self.assertEqual(plan.stats['project_row_calculation_checkpoints'], [])
