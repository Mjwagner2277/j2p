"""Fresh Project creation appends complete outline groups without moving old rows.

The COM fake models inherited outline levels and insertion-induced ID changes.
These tests cover row placement and scope retention, not native scheduling speed.
"""

import copy
import hashlib
import io
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from j2p.config import DEFAULT_CONFIG, load_config
from j2p.core import build_run_plan
from j2p.models import PlanEpic, RunPlan
from j2p.project import MicrosoftProjectSession, ProjectAutomationError, create_project_from_plan
from j2p.project_values import epic_assignments
from j2p.rollups import build_summaries
from yerp_project_support import FILES, YERP


class OutlineTask:
    def __init__(self, collection, name, unique_id, inherited_level):
        self.collection = collection
        self.Name = name
        self.UniqueID = unique_id
        self._outline_level = inherited_level
        self.level_writes = []

    @property
    def OutlineLevel(self):
        return self._outline_level

    @OutlineLevel.setter
    def OutlineLevel(self, value):
        self.level_writes.append(value)
        self._outline_level = value

    @property
    def OutlineParent(self):
        if self._outline_level == 1:
            return None
        if self.collection.reject_parent:
            return SimpleNamespace(ID=-1, UniqueID=-1)
        for task in reversed(self.collection.items[:self.ID - 1]):
            if task.OutlineLevel < self.OutlineLevel:
                return task
        return None

    @property
    def Summary(self):
        return (self.ID < len(self.collection.items)
                and self.collection.items[self.ID].OutlineLevel > self.OutlineLevel)


class OutlineTaskCollection:
    def __init__(self, reject_parent=False):
        self.items = []
        self.add_positions = []
        self.renumbered_existing = []
        self.reject_parent = reject_parent

    @property
    def Count(self):
        return len(self.items)

    def __call__(self, index):
        return self.items[index - 1]

    def Add(self, name, before=None):
        count_before = len(self.items)
        position = count_before + 1 if before is None else before
        self.add_positions.append((position, count_before))
        inherited_level = self.items[position - 2].OutlineLevel if position > 1 else 1
        task = OutlineTask(self, name, 10000 + count_before, inherited_level)
        self.items.insert(position - 1, task)
        for index, row in enumerate(self.items, start=1):
            if row is not task and row.ID != index:
                self.renumbered_existing.append((row.UniqueID, row.ID, index))
            row.ID = index
        return task


def creation_session(reject_parent=False):
    session = object.__new__(MicrosoftProjectSession)
    session.project = SimpleNamespace(Tasks=OutlineTaskCollection(reject_parent))
    session.app = SimpleNamespace(ActiveProject=session.project)
    session.assert_project_identity = Mock()
    session.recalculate = Mock()
    session.mark_unmatched_tasks = Mock()

    def update(task, epic, config, plan):
        # Keep the real row keys and role fields for the subsequent index pass.
        # Field setter and resource behavior have separate integration coverage.
        for field, value in epic_assignments(epic, config):
            setattr(task, field, value)

    session.update_epic_task = Mock(side_effect=update)
    return session


def creation_plan():
    config = copy.deepcopy(DEFAULT_CONFIG)
    epics = {}
    for index in range(10):
        mode, rollup = [('initiative', 'SHARED'), ('fixVersion', 'SHARED'),
                        ('fixVersion', 'Z-LAST')][index % 3]
        key = f'TEAM-{index + 1}'
        epic = PlanEpic(
            key=key, issue_id=str(index + 1), summary=f'Epic {index + 1}',
            status='Done' if index == 1 else 'To Do', rollup_mode=mode,
            rollup_key=rollup, rollup_name=rollup, resource_group='Team', key_prefix='TEAM',
            total_story_points=0 if index == 2 else 3,
            completed_story_points=3 if index == 1 else 0,
            logged_hours=0, completed_logged_hours=0, story_point_ratio=0,
            percent_complete=100 if index == 1 else 0, in_planning=index == 2,
            completed=index == 1, target_start='2026-01-01', target_end='2026-12-31',
            jira_key=key, row_role='Reference' if index == 1 else 'Scheduled',
            drives_schedule=index != 1,
        )
        epics[key] = epic
    summaries = build_summaries(epics, config)
    empty = replace(next(iter(summaries.values())), summary_id='initiative:A-EMPTY',
                    key='A-EMPTY', name='Empty initiative', rollup_mode='initiative',
                    child_epic_count=0, driving_epic_count=0, reference_epic_count=0,
                    total_story_points=0, completed_story_points=0,
                    completion_total_story_points=0, completion_completed_story_points=0)
    summaries[empty.summary_id] = empty
    return RunPlan(generated_at='2026-09-19', jira_csv='synthetic.csv', rollup_mode='mixed',
                   column_map={}, stats={}, summaries=summaries, epics=epics, audit_items=[]), config


class ProjectCreationOrderTests(unittest.TestCase):
    def assert_complete_outline(self, session, plan):
        rows = session.project.Tasks.items
        expected = []
        populated_groups = {(epic.rollup_mode, epic.rollup_key) for epic in plan.epics.values()}
        for summary in sorted(plan.summaries.values(), key=lambda item: (item.key, item.rollup_mode)):
            if (summary.rollup_mode, summary.key) not in populated_groups:
                continue
            expected.append((1, summary.rollup_mode, summary.key, ''))
            children = sorted((epic for epic in plan.epics.values()
                               if (epic.rollup_mode, epic.rollup_key) == (summary.rollup_mode, summary.key)),
                              key=lambda epic: epic.key)
            expected.extend((2, epic.rollup_mode, epic.rollup_key, epic.key) for epic in children)
        actual = [(row.OutlineLevel, row.Text4, row.Text5, getattr(row, 'Text10', ''))
                  for row in rows if (row.Text4, row.Text5) in populated_groups]
        self.assertEqual(actual, expected)
        self.assertCountEqual([(row.Text4, row.Text5) for row in rows if row.OutlineLevel == 1],
                              [(summary.rollup_mode, summary.key) for summary in plan.summaries.values()])
        self.assertEqual(len(rows), len(plan.epics) + len(plan.summaries))
        current_summary = None
        for row in rows:
            self.assertEqual(row.level_writes, [row.OutlineLevel])
            if row.OutlineLevel == 1:
                current_summary = row
                self.assertIsNone(row.OutlineParent)
            else:
                self.assertIs(row.OutlineParent, current_summary)
        self.assertEqual(session.project.Tasks.renumbered_existing, [])
        self.assertTrue(all(position == count + 1 for position, count in session.project.Tasks.add_positions))
        written = [call.args[1].key for call in session.update_epic_task.call_args_list]
        self.assertCountEqual(written, plan.epics)
        self.assertEqual(len(written), len(plan.epics))

    def test_new_project_workflow_selects_append_only_creation(self):
        plan, config = creation_plan()
        session = MagicMock()
        session.__enter__.return_value = session
        with patch('j2p.project.MicrosoftProjectSession', return_value=session), redirect_stdout(io.StringIO()):
            create_project_from_plan(Path('fresh-project.mpp'), plan, config)
        session.new.assert_called_once_with()
        session.apply_plan.assert_called_once_with(plan, config, write_dependencies=False, append_only=True, defer_undated_reviews=True)
        session.verify_saved_plan.assert_called_once_with(plan, config)

    def test_creation_groups_modes_and_preserves_every_row_with_quarter_checkpoints(self):
        plan, config = creation_plan()
        session = creation_session()
        checkpoints = []
        session.recalculate.side_effect = lambda: checkpoints.append(session.update_epic_task.call_count)
        with redirect_stdout(io.StringIO()):
            session.apply_plan(plan, config, write_dependencies=False, append_only=True)
        self.assert_complete_outline(session, plan)
        self.assertEqual(checkpoints, [3, 5, 8, 10])
        self.assertEqual(plan.stats['project_row_calculation_checkpoints'], checkpoints)
        written = [call.args[1] for call in session.update_epic_task.call_args_list]
        self.assertTrue(any(epic.in_planning for epic in written))
        self.assertTrue(any(epic.completed and not epic.drives_schedule for epic in written))

    def test_nonempty_project_is_rejected_before_any_mutation(self):
        plan, config = creation_plan()
        session = creation_session()
        existing = session.project.Tasks.Add('Existing task')
        before = dict(vars(existing))
        with redirect_stdout(io.StringIO()), self.assertRaises(ProjectAutomationError):
            session.apply_plan(plan, config, write_dependencies=False, append_only=True)
        self.assertEqual(session.project.Tasks.items, [existing])
        self.assertEqual(vars(existing), before)
        self.assertEqual(existing.level_writes, [])
        self.assertEqual(len(session.project.Tasks.add_positions), 1)
        session.update_epic_task.assert_not_called()
        session.recalculate.assert_not_called()

    def test_wrong_outline_parent_stops_before_epic_fields_are_written(self):
        plan, config = creation_plan()
        session = creation_session(reject_parent=True)
        with redirect_stdout(io.StringIO()), self.assertRaises(ProjectAutomationError):
            session.apply_plan(plan, config, write_dependencies=False, append_only=True)
        session.update_epic_task.assert_not_called()
        session.recalculate.assert_not_called()

    def test_summary_only_plan_creates_each_empty_group_once_without_calculation(self):
        plan, config = creation_plan()
        plan.epics = {}
        session = creation_session()
        with redirect_stdout(io.StringIO()):
            session.apply_plan(plan, config, write_dependencies=False, append_only=True)
        self.assert_complete_outline(session, plan)
        session.recalculate.assert_not_called()
        self.assertEqual(plan.stats['project_row_calculation_checkpoints'], [])

    @unittest.skipUnless(bool(FILES) and (YERP / 'ssn-812-config.yaml').exists(),
                         'Requires the nine project-specific yerp exports and configuration')
    def test_real_yerp_creation_appends_all_2016_epics_and_164_rollups(self):
        self.assertEqual(len(FILES), 9)
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}
        try:
            config = load_config(YERP / 'ssn-812-config.yaml')
            plan = build_run_plan(FILES, config)
            self.assertEqual((len(plan.epics), len(plan.summaries)), (2016, 164))
            session = creation_session()
            checkpoints = []
            session.recalculate.side_effect = lambda: checkpoints.append(session.update_epic_task.call_count)
            with redirect_stdout(io.StringIO()):
                session.apply_plan(plan, config, write_dependencies=False, append_only=True)
            self.assert_complete_outline(session, plan)
            self.assertEqual(checkpoints, [504, 1008, 1512, 2016])
            self.assertEqual(plan.stats['project_row_calculation_checkpoints'], checkpoints)
        finally:
            self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES})


if __name__ == '__main__':
    unittest.main()
