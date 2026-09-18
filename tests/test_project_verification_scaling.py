"""Check verification's COM access cost without weakening persisted-plan checks."""

import copy
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock

from j2p.config import DEFAULT_CONFIG
from j2p.models import PlanEpic, PlanSummary, RunPlan
from j2p.project import (
    MicrosoftProjectSession,
    ProjectAutomationError,
    managed_resource_marker,
    summary_assignments,
)
from j2p.project_values import epic_assignments


class CountedCollection:
    """Model Project's 1-based COM collections and record every collection read."""

    def __init__(self, items=(), on_first_read=None):
        self.items = list(items)
        self.count_reads = 0
        self.item_reads = 0
        self.on_first_read = on_first_read

    def read(self):
        if self.on_first_read is not None:
            callback, self.on_first_read = self.on_first_read, None
            callback()

    @property
    def Count(self):
        self.read()
        self.count_reads += 1
        return len(self.items)

    def __call__(self, index):
        self.read()
        self.item_reads += 1
        return self.items[index - 1]


def verification_fixture(epic_count=120, summary_count=12, resource_count=40):
    config = copy.deepcopy(DEFAULT_CONFIG)
    resources = [SimpleNamespace(ID=index + 1, Name=f'Team {index}', Group=f'Team {index}',
                                 Notes=managed_resource_marker(f'Team {index}'))
                 for index in range(resource_count)]
    summaries, summary_tasks = {}, []
    for index in range(summary_count):
        key = f'INIT-{index + 1}'
        summary = PlanSummary(
            summary_id=f'initiative:{key}', key=key, name=f'Initiative {index + 1}',
            rollup_mode='initiative', project_key='TEAM', total_story_points=80,
            completed_story_points=40, logged_hours=100, completed_logged_hours=50,
            story_point_ratio=1.25, percent_complete=50, child_epic_count=10,
        )
        task = SimpleNamespace(ID=index + 1, UniqueID=1000 + index, Summary=True,
                               Manual=False, Assignments=CountedCollection())
        for field, value in summary_assignments(summary, config):
            setattr(task, field, value)
        summaries[summary.summary_id] = summary
        summary_tasks.append(task)

    epics, epic_tasks = {}, []
    for index in range(epic_count):
        parent = summary_tasks[index % summary_count]
        resource = resources[index % resource_count]
        key = f'TEAM-{index + 1}'
        epic = PlanEpic(
            key=key, issue_id=str(index + 1), summary=f'Epic {index + 1}',
            status='In Progress', rollup_mode='initiative', rollup_key=parent.Text1,
            rollup_name=parent.Name, resource_group=resource.Group, key_prefix='TEAM',
            total_story_points=8, completed_story_points=4, logged_hours=10,
            completed_logged_hours=5, story_point_ratio=1.25, percent_complete=50,
            in_planning=False, completed=False, target_start='2026-01-01',
            target_end='2026-06-30', predecessors=[f'TEAM-{index}'] if index else [],
            source_row=index + 2, source_file='synthetic.csv',
        )
        task = SimpleNamespace(
            ID=summary_count + index + 1, UniqueID=2000 + index, Summary=False,
            Manual=False, Active=True, OutlineParent=parent,
            Assignments=CountedCollection([SimpleNamespace(ResourceID=resource.ID)]),
            ResourceGroup=resource.Group, Date1=epic.target_start, Date2=epic.target_end,
            Predecessors=str(epic_tasks[-1].ID) if index else '',
        )
        for field, value in epic_assignments(epic, config):
            setattr(task, field, value)
        dependencies = []
        if epic_tasks:
            dependency = SimpleNamespace(To=task, Type=1, Lag=0)
            setattr(dependency, 'From', epic_tasks[-1])
            dependencies.append(dependency)
        task.TaskDependencies = CountedCollection(dependencies)
        epics[key] = epic
        epic_tasks.append(task)

    plan = RunPlan(generated_at='2026-09-18', jira_csv='synthetic.csv', rollup_mode='initiative',
                   column_map={}, stats={}, summaries=summaries, epics=epics, audit_items=[])
    session = object.__new__(MicrosoftProjectSession)
    session.project = SimpleNamespace(Tasks=CountedCollection(summary_tasks + epic_tasks),
                                      Resources=CountedCollection(resources))
    session.app = SimpleNamespace(ActiveProject=session.project)
    session.assert_project_identity = Mock()
    return session, plan, config, epic_tasks, summary_tasks


class ProjectVerificationScalingTests(unittest.TestCase):
    def test_snapshots_use_named_story_point_completion_but_preserve_legacy_native_values(self):
        session, _, config, epics, _ = verification_fixture(epic_count=1, summary_count=1)
        epics[0].Number7 = 0
        session.app.FieldNameToFieldConstant = Mock(return_value=7)
        session.app.CustomFieldGetName = Mock(return_value='Unrelated field')
        self.assertEqual(session.snapshot_tasks(config)['TEAM-1'].percent_complete, 50)
        session.app.CustomFieldGetName.return_value = 'Story Point Completion %'
        self.assertEqual(session.snapshot_tasks(config)['TEAM-1'].percent_complete, 0)

    def test_summary_writes_do_not_modify_native_progress_of_child_tasks(self):
        _, plan, config, _, _ = verification_fixture(epic_count=1, summary_count=1)
        fields = dict(summary_assignments(next(iter(plan.summaries.values())), config))
        self.assertNotIn('PercentComplete', fields)
        self.assertEqual(fields['Number7'], 50)

    def test_row_read_failure_retains_position_phase_and_original_error(self):
        session, _, _, _, _ = verification_fixture(epic_count=1, summary_count=1)
        failure = RuntimeError('COM call rejected')
        session.project.Tasks = Mock(side_effect=[None, failure])
        session.project.Tasks.Count = 2
        with self.assertRaisesRegex(ProjectAutomationError, 'row 2 of 2 during Saved readback: COM call rejected') as raised:
            session.iter_tasks(progress_label='Saved readback')
        self.assertIs(raised.exception.__cause__, failure)

    def test_task_count_failure_is_reported_as_automation_error(self):
        class BrokenCollection:
            @property
            def Count(self):
                raise RuntimeError('collection unavailable')
        session, _, _, _, _ = verification_fixture(epic_count=1)
        session.project.Tasks = BrokenCollection()
        with self.assertRaisesRegex(ProjectAutomationError, 'collection count: collection unavailable'):
            session.iter_tasks()

    def test_duplicate_rollups_with_distinct_task_keys_are_rejected(self):
        session, plan, config, _, summaries = verification_fixture(epic_count=1, summary_count=1)
        duplicate = copy.copy(summaries[0])
        duplicate.Text1 = 'OTHER-1'
        duplicate.Text10 = 'OTHER-1'
        duplicate.UniqueID = 9999
        duplicate.Text5 = duplicate.Text5.lower()
        session.project.Tasks.items.append(duplicate)
        with self.assertRaisesRegex(ProjectAutomationError, 'Duplicate Project rollup initiative:INIT-1'):
            self.verify_quietly(session, plan, config)

    def verify_quietly(self, session, plan, config):
        with redirect_stdout(io.StringIO()):
            return session.verify_plan(plan, config)

    def test_verification_does_not_rescan_tasks_or_resources_for_each_epic(self):
        for epic_count in (12, 120):
            with self.subTest(epic_count=epic_count):
                session, plan, config, epics, _ = verification_fixture(epic_count=epic_count)
                verified = self.verify_quietly(session, plan, config)
                self.assertGreater(verified, epic_count * 10)
                tasks, resources = session.project.Tasks, session.project.Resources
                self.assertLessEqual(tasks.item_reads, 2 * len(tasks.items))
                self.assertLessEqual(tasks.count_reads, 2)
                self.assertLessEqual(resources.item_reads, 2 * len(resources.items))
                self.assertLessEqual(resources.count_reads, 2)
                self.assertEqual(sum(task.Assignments.item_reads for task in epics), epic_count)

    def test_verification_announces_work_before_collection_reads_and_reports_progress(self):
        session, plan, config, _, _ = verification_fixture()
        output = io.StringIO()
        first_read_output = []
        session.project.Tasks.on_first_read = lambda: first_read_output.append(output.getvalue())
        with redirect_stdout(output):
            session.verify_plan(plan, config)
        self.assertTrue(first_read_output)
        self.assertRegex(first_read_output[0].lower(), r'verif')
        progress = output.getvalue()
        self.assertRegex(progress, r'1/120')
        self.assertRegex(progress, r'50/120')
        self.assertRegex(progress, r'120/120')

    def test_indexed_verification_still_rejects_corrupted_epic_fields(self):
        corruptions = (
            ('percentage', lambda task: setattr(task, 'PercentComplete', 0), 'PercentComplete'),
            ('metric', lambda task: setattr(task, 'Number2', 0), 'Number2'),
            ('schedule mode', lambda task: setattr(task, 'Manual', True), 'Manual'),
            ('active state', lambda task: setattr(task, 'Active', False), 'Active'),
            ('date', lambda task: setattr(task, 'Date2', '2026-07-01'), 'date readback'),
            ('outline', lambda task: setattr(task, 'OutlineParent', SimpleNamespace(UniqueID=999)),
             'outline parent'),
            ('resource', lambda task: task.Assignments.items.clear(), 'managed resource assignment'),
            ('dependency', lambda task: setattr(task.TaskDependencies.items[0], 'Lag', 60),
             'Dependency verification'),
        )
        for label, corrupt, error in corruptions:
            with self.subTest(label=label):
                session, plan, config, epics, _ = verification_fixture(epic_count=3)
                corrupt(epics[-1])
                with self.assertRaisesRegex(ProjectAutomationError, error):
                    self.verify_quietly(session, plan, config)

    def test_indexed_verification_rejects_wrong_resource_ownership(self):
        session, plan, config, _, _ = verification_fixture(epic_count=3)
        session.project.Resources.items[2].Notes = managed_resource_marker('Different Team')
        with self.assertRaisesRegex(ProjectAutomationError, 'managed resource assignment'):
            self.verify_quietly(session, plan, config)

    def test_indexed_verification_rejects_wrong_predecessor_identity(self):
        session, plan, config, epics, _ = verification_fixture(epic_count=3)
        wrong = SimpleNamespace(ID=epics[1].ID, UniqueID=9999)
        setattr(epics[-1].TaskDependencies.items[0], 'From', wrong)
        with self.assertRaisesRegex(ProjectAutomationError, 'different Project task identity'):
            self.verify_quietly(session, plan, config)

    def test_rollup_metrics_are_checked_but_native_summary_percentage_may_recalculate(self):
        session, plan, config, _, summaries = verification_fixture(epic_count=3)
        summaries[0].PercentComplete = 75
        self.verify_quietly(session, plan, config)
        summaries[0].Number7 = 75
        with self.assertRaisesRegex(ProjectAutomationError, 'rollup=INIT-1.*Number7'):
            self.verify_quietly(session, plan, config)
        summaries[0].Number7 = 50
        summaries[0].Number2 = 0
        with self.assertRaisesRegex(ProjectAutomationError, 'rollup=INIT-1.*Number2'):
            self.verify_quietly(session, plan, config)

    def test_indexed_verification_still_rejects_duplicate_project_keys(self):
        session, plan, config, epics, _ = verification_fixture(epic_count=3)
        epics[-1].Text10 = epics[0].Text10.lower()
        with self.assertRaisesRegex(ProjectAutomationError, 'Duplicate Project key TEAM-1'):
            self.verify_quietly(session, plan, config)

    def test_fixversion_rollup_lookup_retains_issue_type_fallback(self):
        session, plan, config, epics, summaries = verification_fixture(epic_count=1, summary_count=1)
        summary = next(iter(plan.summaries.values()))
        summary.rollup_mode = 'fixversion'
        next(iter(plan.epics.values())).rollup_mode = 'fixversion'
        summaries[0].Text1 = ''
        summaries[0].Summary = False
        for field, value in summary_assignments(summary, config):
            setattr(summaries[0], field, value)
        epics[0].Text4 = 'fixversion'
        self.verify_quietly(session, plan, config)

    def test_snapshot_reads_tasks_once_and_preserves_relationship_keys(self):
        session, plan, config, epics, summaries = verification_fixture()
        output = io.StringIO()
        with redirect_stdout(output):
            snapshots = session.snapshot_tasks(config, progress_label='Saved readback')
        self.assertEqual(len(snapshots), len(epics) + len(summaries))
        self.assertEqual(snapshots['TEAM-3'].predecessors, ['TEAM-2'])
        self.assertEqual(snapshots['TEAM-3'].percent_complete, 50)
        self.assertEqual(session.project.Tasks.item_reads, len(epics) + len(summaries))
        self.assertEqual(session.project.Tasks.count_reads, 1)
        self.assertIn('Saved readback progress: 50/132', output.getvalue())
        self.assertIn('Saved readback progress: 132/132', output.getvalue())


if __name__ == '__main__':
    unittest.main()
