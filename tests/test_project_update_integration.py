"""Update the full fake schedule without dropping comparison or verification work."""

import copy
import io
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

from j2p.baseline import compare_with_baseline
from j2p.project import MicrosoftProjectSession, ProjectAutomationError
from test_project_reliability import Assignments, Resources
from test_project_verification_scaling import verification_fixture


class RecordingTask:
    def __init__(self, task):
        object.__setattr__(self, 'task', task)
        object.__setattr__(self, 'writes', [])

    def __getattr__(self, name):
        return getattr(self.task, name)

    def __setattr__(self, name, value):
        self.writes.append((name, value))
        setattr(self.task, name, value)


class ProjectUpdateIntegrationTests(unittest.TestCase):
    def fixture(self):
        session, plan, config, epics, summaries = verification_fixture(epic_count=12, summary_count=3)
        for task in epics + summaries:
            task.HideBar = False
            task.Flag2 = False
        session.project.Tasks.items = [RecordingTask(task) for task in session.project.Tasks.items]
        session.recalculate = Mock()
        session.app.FieldNameToFieldConstant = Mock(side_effect=lambda name: name)
        names = {config['project_fields'][key]: value for key, value in config['project_field_names'].items()}
        session.app.CustomFieldGetName = Mock(side_effect=lambda name: names.get(name, ''))
        session.app.CustomFieldRename = Mock()
        return session, plan, config

    def test_full_identical_update_skips_setters_and_keeps_complete_verification(self):
        session, plan, config = self.fixture()
        normal = session.snapshot_tasks(config)
        resources = session.project.Resources
        resources.item_reads = resources.count_reads = 0
        with redirect_stdout(io.StringIO()):
            session.begin_selective_update()
            before = session.snapshot_tasks(config)
            session.configure_custom_fields(config)
            session.apply_plan(plan, config)
            session.end_selective_update(plan)
        self.assertEqual({k: asdict(v) for k, v in before.items()}, {k: asdict(v) for k, v in normal.items()})
        self.assertEqual([write for task in session.project.Tasks.items for write in task.writes], [])
        session.app.CustomFieldRename.assert_not_called()
        self.assertEqual(resources.item_reads, len(resources.items))
        self.assertEqual(resources.count_reads, 1)
        self.assertEqual(plan.stats['project_update_writes']['task_fields']['written'], 0)
        self.assertGreater(plan.stats['project_update_writes']['task_fields']['skipped'], 300)
        self.assertEqual(plan.stats['project_update_writes']['dependency_sets']['skipped'], 11)
        with redirect_stdout(io.StringIO()):
            verified = session.verify_plan(plan, config)
        self.assertGreater(verified, 300)
        session.project.Tasks.items[-1].task.Number7 = -1
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ProjectAutomationError, 'Number7'):
            session.verify_plan(plan, config)

    def test_point_change_updates_only_affected_epic_and_rollup_and_keeps_diff(self):
        session, plan, config = self.fixture()
        baseline = session.snapshot_tasks(config)
        epic = plan.epics['TEAM-1']
        epic.total_story_points = 10
        epic.percent_complete = 40
        summary = plan.summaries['initiative:INIT-1']
        summary.total_story_points += 2
        summary.completion_total_story_points += 2
        expected_audit = []
        compare_with_baseline(plan.epics, plan.summaries, baseline, config, expected_audit)
        with redirect_stdout(io.StringIO()):
            session.begin_selective_update()
            cached_baseline = session.snapshot_tasks(config)
            compare_with_baseline(plan.epics, plan.summaries, cached_baseline, config, plan.audit_items)
            session.apply_plan(plan, config)
            session.end_selective_update(plan)
            session.verify_plan(plan, config)
        self.assertEqual([asdict(a) for a in plan.audit_items], [asdict(a) for a in expected_audit])
        writes = {task.Text1: [field for field, value in task.writes]
                  for task in session.project.Tasks.items if task.writes}
        self.assertEqual(set(writes), {'TEAM-1', 'INIT-1'})
        self.assertEqual(set(writes['TEAM-1']), {'Number1', 'Number5', 'Number7', 'PercentComplete'})
        self.assertEqual(set(writes['INIT-1']), {'Number1', 'Number5'})

    def test_duplicate_rollup_is_rejected_before_any_task_setter(self):
        session, plan, config = self.fixture()
        duplicate = copy.copy(session.project.Tasks.items[0].task)
        duplicate.Text1 = 'OTHER-1'
        duplicate.UniqueID = 9999
        session.project.Tasks.items.append(RecordingTask(duplicate))
        session.begin_selective_update()
        with self.assertRaisesRegex(ProjectAutomationError, 'Duplicate Project rollup'):
            session.apply_plan(plan, config)
        self.assertFalse(any(task.writes for task in session.project.Tasks.items))

    def test_added_epic_does_not_rewrite_existing_rows_after_ids_shift(self):
        session, plan, config = self.fixture()
        epic = copy.deepcopy(plan.epics['TEAM-1'])
        epic.key = 'TEAM-NEW'
        epic.issue_id = '9999'
        epic.summary = 'New epic'
        plan.epics[epic.key] = epic
        collection = session.project.Tasks
        old_tasks = list(collection.items)

        def add(name, before):
            parent = collection.items[before - 2]
            task = SimpleNamespace(Name=name, UniqueID=9999, Summary=False, Active=True,
                                   Assignments=Assignments(), ResourceNames='', Predecessors='',
                                   TaskDependencies=Assignments())
            task.OutlineIndent = lambda: setattr(task, 'OutlineParent', parent)
            wrapped = RecordingTask(task)
            collection.items.insert(before - 1, wrapped)
            for index, row in enumerate(collection.items, start=1):
                row.task.ID = index  # Project assigns new row IDs as an insertion side effect.
            # The existing FS dependencies refer to task objects, so their display IDs move too.
            for row in old_tasks:
                dependencies = getattr(row, 'TaskDependencies', SimpleNamespace(items=[])).items
                row.task.Predecessors = ','.join(str(getattr(dep, 'From').ID) for dep in dependencies)
            return wrapped

        collection.Add = add
        with redirect_stdout(io.StringIO()):
            session.begin_selective_update()
            session.snapshot_tasks(config)
            session.apply_plan(plan, config)
            session.end_selective_update(plan)
            session.verify_plan(plan, config)
        self.assertFalse(any(task.writes for task in old_tasks))
        added = next(task for task in collection.items if task.UniqueID == 9999)
        self.assertEqual(added.Text10, epic.key)
        self.assertEqual(added.OutlineParent.Text1, epic.rollup_key)
        self.assertTrue(added.writes)

    def test_resource_reassignment_preserves_unmanaged_warnings_and_refreshes_cache(self):
        session = object.__new__(MicrosoftProjectSession)
        session.project = SimpleNamespace(Resources=Resources())
        task = SimpleNamespace(Assignments=Assignments(), ResourceNames='')
        person = session.project.Resources.Add('Person')
        person.Group = 'People'
        task.Assignments.Add(person.ID)
        session.set_native_resource_group(task, 'Team A')
        session.begin_selective_update()
        self.assertFalse(session.set_native_resource_group(task, 'Team A'))
        self.assertTrue(session.set_native_resource_group(task, 'Team B'))
        self.assertFalse(session.set_native_resource_group(task, 'Team B'))
        self.assertIn('Preserved unmanaged', session.resource_assignment_warnings[0])
        groups = [session.project.Resources(a.ResourceID).Group for a in task.Assignments.items]
        self.assertEqual(groups, ['People', 'Team B'])
        self.assertEqual(len(session.project.Resources.items), 3)
        session.verify_managed_resource_assignment(task, 'Team B')


if __name__ == '__main__':
    unittest.main()
