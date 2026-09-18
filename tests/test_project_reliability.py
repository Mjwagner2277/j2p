"""Portable fault-injection coverage for the Project automation boundary."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from j2p.models import ProjectTaskSnapshot
from j2p.project import (
    MicrosoftProjectSession, ProjectAutomationError, managed_resource_marker,
    parse_project_dependency_text, project_task_identity, snapshot_relationship_keys,
    verify_project_predecessors, write_required_project_value,
)


class Collection:
    def __init__(self, items=()):
        self.items = list(items)

    @property
    def Count(self):
        return len(self.items)

    def __call__(self, index):
        return self.items[index - 1]


class Resources(Collection):
    def Add(self, name):
        resource = SimpleNamespace(ID=len(self.items) + 1, Name=name, Group='', Notes='')
        self.items.append(resource)
        return resource


class Assignments(Collection):
    def Add(self, ResourceID):
        assignment = SimpleNamespace(ResourceID=ResourceID)
        def delete():
            self.items.remove(assignment)
        assignment.Delete = delete
        self.items.append(assignment)
        return assignment


class SaveApp:
    def __init__(self, path, result=True, creates=True):
        self.ActiveProject = SimpleNamespace(FullName=str(path))
        self.result = result
        self.creates = creates
        self.Version = 'fake-1'

    def FileSave(self):
        return self.result

    def FileSaveAs(self, Name):
        if self.creates:
            Path(Name).write_bytes(b'fake persisted project')
        self.ActiveProject.FullName = Name
        return self.result


class ProjectReliabilityTests(unittest.TestCase):
    def test_active_state_already_correct_does_not_write_readonly_property(self):
        for expected in (True, False):
            class ReadOnlyActive:
                @property
                def Active(self):
                    return expected
                @Active.setter
                def Active(self, value):
                    raise AssertionError('Redundant Active write')
            write_required_project_value(ReadOnlyActive(), 'Active', expected, 'epic=TEAM-1')

    def test_required_active_transition_preserves_original_failure_and_value(self):
        failure = RuntimeError('Task cannot be inactivated')
        class ReadOnlyActive:
            @property
            def Active(self):
                return True
            @Active.setter
            def Active(self, value):
                raise failure
        with self.assertRaises(ProjectAutomationError) as raised:
            write_required_project_value(ReadOnlyActive(), 'Active', False, 'epic=TEAM-1')
        message = str(raised.exception)
        self.assertIn('attempted_value=False', message)
        self.assertIn('not a CSV mapping', message)
        self.assertIn('Task cannot be inactivated', message)
        self.assertIsNone(raised.exception.__cause__)

    def test_required_active_transition_is_written_and_verified(self):
        task = SimpleNamespace(Active=False)
        write_required_project_value(task, 'Active', True, 'epic=TEAM-1')
        self.assertIs(task.Active, True)

    def session(self, app=None):
        session = object.__new__(MicrosoftProjectSession)
        session.app = app
        session.project = app.ActiveProject if app else None
        session.project_path = None
        session.saved_successfully = False
        return session

    def test_false_save_never_sets_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sandbox.mpp'
            path.write_bytes(b'old')
            session = self.session(SaveApp(path, result=False))
            session.project_path = path
            with self.assertRaisesRegex(ProjectAutomationError, 'returned False'):
                session.save()
            self.assertFalse(session.saved_successfully)

    def test_false_save_as_never_sets_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.session(SaveApp(Path(tmp) / 'blank', result=False, creates=False))
            with self.assertRaisesRegex(ProjectAutomationError, 'returned False'):
                session.save_as(Path(tmp) / 'new.mpp')
            self.assertFalse(session.saved_successfully)

    def test_save_as_requires_persisted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.session(SaveApp(Path(tmp) / 'blank', creates=False))
            with self.assertRaisesRegex(ProjectAutomationError, 'nonempty sandbox'):
                session.save_as(Path(tmp) / 'missing.mpp')
            self.assertFalse(session.saved_successfully)

    def test_save_as_binds_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = self.session(SaveApp(Path(tmp) / 'blank'))
            output = Path(tmp) / 'nested' / 'new.mpp'
            session.save_as(output)
            self.assertTrue(session.saved_successfully)
            self.assertEqual(session.project_path, output.resolve())

    def test_active_project_switch_blocks_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected = Path(tmp) / 'sandbox.mpp'
            app = SaveApp(Path(tmp) / 'source-of-truth.mpp')
            app.FileSave = Mock(return_value=True)
            session = self.session(app)
            session.project_path = expected
            with self.assertRaisesRegex(ProjectAutomationError, 'does not match the sandbox'):
                session.save()
            app.FileSave.assert_not_called()

    def test_recalculation_uses_fallback_on_false_and_fails_closed(self):
        session = self.session(SimpleNamespace(ActiveProject=None, CalculateProject=Mock(return_value=False),
                                                CalculateAll=Mock(return_value=False)))
        with self.assertRaisesRegex(ProjectAutomationError, 'could not recalculate'):
            session.recalculate()
        self.assertEqual(session.app.CalculateAll.call_count, 1)
        session.app.CalculateAll.return_value = True
        session.recalculate()

    def test_close_does_not_use_default_save_policy(self):
        session = self.session(SimpleNamespace(ActiveProject=None, FileCloseEx=Mock(return_value=False),
                                                FileClose=Mock(return_value=False)))
        with self.assertRaisesRegex(ProjectAutomationError, 'close the sandbox safely'):
            session.close_project(False)
        session.app.FileClose.assert_called_once_with(Save=0)

    def test_close_success_requires_sandbox_to_leave_active_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sandbox.mpp'
            app = SaveApp(path)
            app.FileCloseEx = Mock(return_value=True)
            app.FileClose = Mock(return_value=True)
            session = self.session(app)
            session.project_path = path
            with self.assertRaisesRegex(ProjectAutomationError, 'sandbox is still active'):
                session.close_project(False)

    def test_close_accepts_no_active_project_com_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sandbox.mpp'
            class ClosingApp:
                closed = False
                project = SimpleNamespace(FullName=str(path))
                @property
                def ActiveProject(self):
                    if self.closed:
                        raise RuntimeError('No active project')
                    return self.project
                def FileCloseEx(self, **kwargs):
                    self.closed = True
                    return True
            app = ClosingApp()
            session = self.session(app)
            session.project_path = path
            session.close_project(False)
            self.assertTrue(app.closed)

    def test_close_accepts_return_to_previously_open_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sandbox.mpp'
            app = SaveApp(path)
            def close(**kwargs):
                app.ActiveProject = SimpleNamespace(FullName=str(Path(tmp) / 'source.mpp'))
                return True
            app.FileCloseEx = Mock(side_effect=close)
            session = self.session(app)
            session.project_path = path
            session.close_project(False)
            app.FileCloseEx.assert_called_once()

    def test_close_fallback_never_closes_a_different_active_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sandbox.mpp'
            app = SaveApp(path)
            def close_and_switch(**kwargs):
                app.ActiveProject = SimpleNamespace(FullName=str(Path(tmp) / 'other.mpp'))
                return False
            app.FileCloseEx = Mock(side_effect=close_and_switch)
            app.FileClose = Mock(return_value=True)
            session = self.session(app)
            session.project_path = path
            with self.assertRaisesRegex(ProjectAutomationError, 'does not match the sandbox'):
                session.close_project(False)
            self.assertEqual(app.FileCloseEx.call_count, 1)
            app.FileClose.assert_not_called()

    def test_acceptance_harness_cannot_pass_a_failed_check(self):
        path = Path(__file__).resolve().parents[1] / 'scripts' / 'windows_acceptance.py'
        spec = importlib.util.spec_from_file_location('windows_acceptance', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with self.assertRaisesRegex(RuntimeError, 'saved_file_exists'):
            module.require_acceptance_checks({'saved_file_exists': False, 'save_reopen': True})
        module.require_acceptance_checks({'saved_file_exists': True})

    def test_false_open_does_not_bind_active_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sandbox.mpp'
            path.write_bytes(b'fake')
            session = self.session(SimpleNamespace(ActiveProject=None, FileOpen=Mock(return_value=False)))
            with self.assertRaisesRegex(ProjectAutomationError, 'returned False'):
                session.open(path)
            self.assertIsNone(session.project)

    def test_save_reopen_records_verification_and_detects_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sandbox.mpp'
            path.write_bytes(b'fake persisted data')
            session = self.session(SaveApp(path))
            session.project_path = path
            session.verify_plan = Mock(return_value=20)
            session.close_project = Mock()
            session.open = Mock()
            snapshot = ProjectTaskSnapshot(key='TEAM-1', name='before')
            session.snapshot_tasks = Mock(return_value={'TEAM-1': snapshot})
            plan = SimpleNamespace(epics={'TEAM-1': object()}, stats={})
            session.verify_saved_plan(plan, {})
            self.assertTrue(plan.stats['project_verification']['save_reopen'])
            self.assertEqual(plan.stats['project_verification']['verified_fields'], 20)
            self.assertEqual(len(plan.stats['project_verification']['sha256']), 64)
            session.snapshot_tasks = Mock(side_effect=[{'TEAM-1': snapshot}, {}])
            with self.assertRaisesRegex(ProjectAutomationError, 'changed task state'):
                session.verify_saved_plan(plan, {})
            self.assertFalse(session.saved_successfully)
            self.assertNotIn('project_verification', plan.stats)

    def test_dependency_type_and_lag_are_verified(self):
        for value in ('3SS', '3FF', '3SF', '3FS+2d', '3FS-1.5d', '3FS+50%'):
            with self.subTest(value=value):
                self.assertIn('Finish-to-Start with zero lag', verify_project_predecessors(
                    SimpleNamespace(Predecessors=value), ['3']))
        self.assertEqual(verify_project_predecessors(SimpleNamespace(Predecessors='3FS+0d'), ['3']), '')
        self.assertEqual(parse_project_dependency_text('3FS-1.5d;4SS+2d'), [('3', 1, '-1.5d'), ('4', 3, '+2d')])

    def test_wrong_relationship_is_rewritten(self):
        session = self.session()
        task = SimpleNamespace(Predecessors='3SS+2d')
        self.assertEqual(session.write_project_predecessors(task, '3FS', ['3']), '')
        self.assertEqual(task.Predecessors, '3FS')

    def test_object_links_verify_stable_identity_and_localized_text(self):
        expected = SimpleNamespace(ID=3, UniqueID=40)
        other = SimpleNamespace(ID=3, UniqueID=99)
        task = SimpleNamespace(ID=5, UniqueID=80, Predecessors='3localized')
        dependency = SimpleNamespace(To=task, Type=1, Lag=0)
        setattr(dependency, 'From', other)
        task.TaskDependencies = Collection([dependency])
        self.assertIn('different Project task identity', verify_project_predecessors(task, ['3'], [expected]))
        setattr(dependency, 'From', expected)
        self.assertEqual(verify_project_predecessors(task, ['3'], [expected]), '')
        dependency.Lag = 60
        self.assertIn('zero lag', verify_project_predecessors(task, ['3'], [expected]))

    def test_snapshot_resolves_numeric_dependencies_to_generated_schedule_keys(self):
        predecessor = SimpleNamespace(ID=3, UniqueID=40, Text1='TEAM-1', Text10='TEAM-1::VERSION-B')
        task = SimpleNamespace(Predecessors='3FS')
        self.assertEqual(snapshot_relationship_keys(task, 'Predecessors', {'3': predecessor}, {}),
                         ['TEAM-1::VERSION-B'])
        task.PredecessorTasks = Collection([predecessor])
        task.Predecessors = 'localized text'
        predecessor.ID = 17
        self.assertEqual(snapshot_relationship_keys(task, 'Predecessors', {'17': predecessor}, {}),
                         ['TEAM-1::VERSION-B'])

    def test_snapshot_fails_if_dependency_cannot_be_resolved(self):
        with self.assertRaisesRegex(ProjectAutomationError, 'Cannot resolve'):
            snapshot_relationship_keys(SimpleNamespace(Predecessors='3FS'), 'Predecessors', {}, {})

    def test_duplicate_project_keys_rejected_with_row_context(self):
        session = self.session()
        session.iter_tasks = lambda: [SimpleNamespace(ID=2, UniqueID=10, Text10='TEAM-1'),
                                     SimpleNamespace(ID=8, UniqueID=20, Text10='team-1')]
        with self.assertRaisesRegex(ProjectAutomationError, 'Duplicate Project key TEAM-1.*UniqueID=10.*UniqueID=20'):
            session.index_tasks_by_key({})

    def test_required_property_silent_rejection_is_error(self):
        class IgnoringTask:
            Manual = True
            def __setattr__(self, name, value):
                pass
        with self.assertRaisesRegex(ProjectAutomationError, 'did not retain.*Manual'):
            write_required_project_value(IgnoringTask(), 'Manual', False, 'epic=TEAM-1')

    def test_required_boolean_readback_rejects_unknown_value(self):
        class UnknownBoolean:
            def __setattr__(self, field, value):
                object.__setattr__(self, field, 'unreadable')
        with self.assertRaisesRegex(ProjectAutomationError, 'did not retain.*Active'):
            write_required_project_value(UnknownBoolean(), 'Active', True, 'epic=TEAM-1')

    def test_unsaved_active_project_switch_is_rejected(self):
        session = self.session(SimpleNamespace(ActiveProject=SimpleNamespace(Name='Source')))
        session.unsaved_project_name = 'Project1'
        with self.assertRaisesRegex(ProjectAutomationError, 'active unsaved Project changed'):
            session.assert_project_identity()

    def test_missing_relationship_readback_is_not_empty_success(self):
        self.assertIn('Could not read Project predecessor', verify_project_predecessors(SimpleNamespace(), []))

    def test_outline_requires_parent_identity(self):
        session = self.session()
        parent = SimpleNamespace(ID=2, UniqueID=20)
        task = SimpleNamespace(OutlineParent=SimpleNamespace(ID=2, UniqueID=30))
        with self.assertRaisesRegex(ProjectAutomationError, 'required outline parent'):
            session.verify_outline_parent(task, parent, 'epic=TEAM-1')
        task.OutlineParent = parent
        session.verify_outline_parent(task, parent, 'epic=TEAM-1')

    def resource_session(self):
        session = self.session()
        session.project = SimpleNamespace(Resources=Resources())
        return session, SimpleNamespace(Assignments=Assignments(), ResourceNames='')

    def test_reassign_replaces_only_owned_resource(self):
        session, task = self.resource_session()
        person = session.project.Resources.Add('Jane')
        person.Group = 'People'
        task.Assignments.Add(person.ID)
        session.set_native_resource_group(task, 'Team A')
        session.set_native_resource_group(task, 'Team B')
        ids = [a.ResourceID for a in task.Assignments.items]
        names = [session.project.Resources(i).Name for i in ids]
        self.assertEqual(names, ['Jane', 'Team B'])
        self.assertEqual(person.Group, 'People')
        self.assertIn('Preserved unmanaged', session.resource_assignment_warnings[0])
        session.set_native_resource_group(task, '')
        self.assertEqual([a.ResourceID for a in task.Assignments.items], [person.ID])

    def test_resource_verification_requires_matching_ownership_marker(self):
        session, task = self.resource_session()
        session.set_native_resource_group(task, 'Team A')
        session.project.Resources.items[0].Notes = managed_resource_marker('Team B')
        with self.assertRaisesRegex(ProjectAutomationError, 'managed resource assignment'):
            session.verify_managed_resource_assignment(task, 'Team A')

    def test_unreadable_ownership_does_not_create_duplicate_resource(self):
        session, task = self.resource_session()
        class UnreadableResource:
            ID = 1
            Name = 'Team A'
            Group = 'Team A'
            @property
            def Notes(self):
                raise RuntimeError('COM getter failed')
        session.project.Resources.items.append(UnreadableResource())
        with self.assertRaisesRegex(ProjectAutomationError, 'read resource ownership marker'):
            session.set_native_resource_group(task, 'Team A')
        self.assertEqual(len(session.project.Resources.items), 1)

    def test_existing_unmanaged_name_is_never_claimed_or_rewritten(self):
        session, task = self.resource_session()
        resource = session.project.Resources.Add('Team A')
        resource.Group = 'User maintained'
        resource.Notes = 'Human notes'
        session.set_native_resource_group(task, 'Team A')
        self.assertEqual(resource.Group, 'User maintained')
        self.assertEqual(resource.Notes, 'Human notes')
        created = session.project.Resources.items[1]
        self.assertTrue(created.Name.startswith('[j2p:'))
        self.assertEqual(created.Notes, managed_resource_marker('Team A'))
        session.set_native_resource_group(task, 'Team A')
        self.assertEqual(len(task.Assignments.items), 1)

    def test_summary_name_and_failure_context(self):
        session = self.session()
        summary = SimpleNamespace(key='INIT-1', name='Renamed initiative', rollup_mode='initiative',
                                  summary_id='initiative:INIT-1', total_story_points=8, completed_story_points=3,
                                  completion_total_story_points=8, completion_completed_story_points=3,
                                  logged_hours=4, story_point_ratio=2, percent_complete=38)
        task = SimpleNamespace(Name='Old name')
        plan = SimpleNamespace(summaries={'INIT-1': summary})
        result = session.ensure_summaries(plan, {}, {'INIT-1': task})
        self.assertEqual(task.Name, summary.name)
        self.assertIs(result[summary.summary_id], task)
        class RejectName:
            def __setattr__(self, field, value):
                if field == 'Name':
                    raise RuntimeError('rejected')
                object.__setattr__(self, field, value)
        with self.assertRaisesRegex(ProjectAutomationError, 'rollup=INIT-1.*field=Name'):
            session.ensure_summaries(plan, {}, {'INIT-1': RejectName()})


if __name__ == '__main__':
    unittest.main()
