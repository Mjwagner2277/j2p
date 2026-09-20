"""Guard review fidelity and COM access costs as audit volume grows."""

import copy
import hashlib
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from j2p.config import DEFAULT_CONFIG, load_config
from j2p.core import build_run_plan
from j2p.models import AuditItem, RunPlan
from j2p.project import (
    MicrosoftProjectSession,
    ProjectAutomationError,
    first_project_column_position,
    normalize_project_column_name,
    project_column_for_audit_field,
    review_table_columns,
    summary_assignments,
)
from j2p.project_values import epic_assignments


class TaskCollection:
    def __init__(self, items):
        self.items = list(items)
        self.count_reads = 0
        self.item_reads = []

    @property
    def Count(self):
        self.count_reads += 1
        return len(self.items)

    def __call__(self, index):
        self.item_reads.append(index)
        return self.items[index - 1]


class CountedProject:
    def __init__(self, tasks):
        self.collection = TaskCollection(tasks)
        self.collection_reads = 0

    @property
    def Tasks(self):
        self.collection_reads += 1
        return self.collection


def formatting_session(tasks):
    """Use the real index and alias code; replace only native UI operations."""
    session = object.__new__(MicrosoftProjectSession)
    session.project = CountedProject(tasks)
    session.app = SimpleNamespace(
        FieldNameToFieldConstant=Mock(side_effect=lambda name: name),
        FieldConstantToFieldName=Mock(side_effect=lambda name: f'Localized {name}'),
    )
    session.assert_project_identity = Mock()
    session.project_selection_aliases = Mock(wraps=session.project_selection_aliases)
    session.prepare_formatting_view = Mock(return_value=[])
    session.project_table_column_positions = Mock(return_value={})
    session.color_project_cell_error = Mock(return_value='')
    return session


def task(key, index=1, jira_key=None, config=None):
    fields = (config or DEFAULT_CONFIG)['project_fields']
    values = {
        'ID': index,
        'UniqueID': index + 1000,
        'Name': f'Task {index}',
        fields['jira_key']: jira_key or key,
        fields['j2p_key']: key,
    }
    return SimpleNamespace(**values)


def plan_for(items):
    return RunPlan(generated_at='2026-09-19', jira_csv='synthetic.csv',
                   rollup_mode='mixed', column_map={}, stats={}, summaries={},
                   epics={}, audit_items=list(items))


def review_item(field='Dependency Review', color='dependency_review',
                jira_key='TEAM-1', schedule_key=''):
    return AuditItem('Review', 'ReviewCandidate', jira_key=jira_key,
                     schedule_key=schedule_key, field=field, color=color)


def color_calls(session):
    return [(call.args[0].ID, *call.args[1:])
            for call in session.color_project_cell_error.call_args_list]


class ProjectFormattingScalingTests(unittest.TestCase):
    def format_quietly(self, session, plan, config):
        with redirect_stdout(io.StringIO()):
            session.apply_review_formatting(plan, config)

    def test_alias_com_resolutions_are_bounded_by_columns_not_audit_count(self):
        counts = []
        for repeats in (2, 200):
            with self.subTest(audit_items=repeats * 5):
                fields = ['Name', 'Dependency Review', 'Status', 'Jira Target End', 'Rollup Key']
                plan = plan_for(review_item(field=field) for _ in range(repeats) for field in fields)
                session = formatting_session([task('TEAM-1')])
                self.format_quietly(session, plan, copy.deepcopy(DEFAULT_CONFIG))
                alias_columns = [call.args[0] for call in session.project_selection_aliases.call_args_list]
                self.assertEqual(len(alias_columns), len(set(alias_columns)))
                counts.append((session.app.FieldNameToFieldConstant.call_count,
                               session.app.FieldConstantToFieldName.call_count))
                self.assertEqual(session.color_project_cell_error.call_count, repeats * 3)
                self.assertEqual(len(plan.audit_items), repeats * 5)
        self.assertEqual(counts[0], counts[1])
        self.assertGreater(counts[0][0], 0)
        self.assertLess(counts[1][0], 100)

    def test_reference_keys_custom_fields_and_repeated_cell_color_order_are_preserved(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config['project_fields'].update(jira_key='Text30', j2p_key='Text27',
                                        dependency_review='Text28', jira_status='Text29')
        config['project_field_names']['dependency_review'] = 'Schedule Review'
        config['review_table']['exposed_columns'] = ['dependency_review', 'status', 'finish']
        primary = task('TEAM-1', 1, config=config)
        reference = task('TEAM-1@SECOND', 2, jira_key='TEAM-1', config=config)
        items = [
            review_item(schedule_key='team-1@second'),
            review_item(field='Status', color='changed_cell'),
            review_item(schedule_key='TEAM-1@SECOND', color='review_needed'),
            review_item(field='Jira Target End'),  # Hidden by the configured table.
            review_item(field='Finish', color='cascade_root'),
            review_item(field='Unknown Field'),
            review_item(jira_key='ABSENT-1'),
            review_item(jira_key=''),
            review_item(color=''),
        ]
        plan = plan_for(items)
        session = formatting_session([primary, reference])
        session.project_table_column_positions.return_value = {'schedule review': 3, 'text29': 4, 'finish': 5}
        self.format_quietly(session, plan, config)
        actual = color_calls(session)
        self.assertEqual([(row, column, color, position) for row, column, color, _, position in actual], [
            (2, 'Text28', config['colors']['dependency_review'], 3),
            (1, 'Text29', config['colors']['changed_cell'], 4),
            (2, 'Text28', config['colors']['review_needed'], 3),
            (1, 'Finish', config['colors']['cascade_root'], 5),
        ])
        self.assertIn('Schedule Review', actual[0][3])
        self.assertIn('Localized Text28', actual[0][3])
        self.assertEqual(plan.audit_items, items)

    def test_alias_cache_is_fresh_when_mapping_and_titles_change_between_calls(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        plan = plan_for([review_item()])
        session = formatting_session([task('TEAM-1')])
        self.format_quietly(session, plan, config)
        first_count = session.app.FieldNameToFieldConstant.call_count
        config['project_fields']['dependency_review'] = 'Text30'
        config['project_field_names']['dependency_review'] = 'New Schedule Review'
        self.format_quietly(session, plan, config)
        first, second = color_calls(session)
        self.assertEqual(first[1], 'Text8')
        self.assertEqual(second[1], 'Text30')
        self.assertIn('New Schedule Review', second[3])
        self.assertNotIn('Dependency Review', second[3])
        self.assertGreater(session.app.FieldNameToFieldConstant.call_count, first_count)

    def test_include_audit_columns_still_exposes_previously_hidden_candidates(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        config['review_table'] = {'exposed_columns': ['name'], 'include_audit_columns': True}
        session = formatting_session([task('TEAM-1')])
        self.format_quietly(session, plan_for([review_item(field='Jira Target End')]), config)
        self.assertEqual(session.color_project_cell_error.call_count, 1)
        self.assertEqual(session.color_project_cell_error.call_args.args[1], 'Date2')
        self.assertIn('Date2', session.prepare_formatting_view.call_args.args[0])

    def test_task_scan_reuses_collection_and_keeps_every_nonblank_row(self):
        rows = [task(f'TEAM-{index}', index) for index in range(1, 122)]
        rows[49] = None
        session = formatting_session(rows)
        with patch('j2p.project.project_progress') as progress:
            actual = session.iter_tasks(progress_label='Review formatting')
        self.assertEqual(actual, [row for row in rows if row is not None])
        self.assertEqual(session.project.collection_reads, 1)
        self.assertEqual(session.project.collection.count_reads, 1)
        self.assertEqual(session.project.collection.item_reads, list(range(1, 122)))
        messages = [call.args[0] for call in progress.call_args_list]
        for boundary in (0, 1, 50, 100, 121):
            self.assertTrue(any(f'{boundary}/121' in message for message in messages), messages)
        self.assertTrue(any('elapsed' in message.lower() or 'seconds' in message.lower()
                            or 's)' in message for message in messages), messages)

    def test_slow_task_scan_reports_between_row_count_milestones(self):
        session = formatting_session([task(f'TEAM-{index}', index) for index in range(1, 6)])
        # Model slow COM row access without sleeping or making a real Project call.
        with patch('j2p.project.time.monotonic', side_effect=[0, 11, 22, 33, 44, 55]):
            with patch('j2p.project.project_progress') as progress:
                session.iter_tasks(progress_label='Slow review scan')
        messages = [call.args[0] for call in progress.call_args_list]
        self.assertTrue(any('2/5' in message and 'elapsed 22.0s' in message for message in messages))
        self.assertTrue(any('4/5' in message and 'elapsed 44.0s' in message for message in messages))

    def test_review_preparation_reports_progress_in_each_phase_and_records_timings(self):
        rows = [task(f'TEAM-{index}', index) for index in range(1, 122)]
        session = formatting_session(rows)
        plan = plan_for([review_item()] * 1502)
        with patch('j2p.project.project_progress') as progress:
            session.apply_review_formatting(plan, DEFAULT_CONFIG)
        messages = [call.args[0] for call in progress.call_args_list]
        for label in ('Review formatting task scan', 'Review formatting key index'):
            for boundary in (0, 1, 50, 100, 121):
                self.assertTrue(any(f'{label}: {boundary}/121' in message for message in messages))
        for boundary in (0, 1, 1000, 1502):
            self.assertTrue(any(f'Review color candidates: {boundary}/1502' in message for message in messages))
        column_count = len(session.prepare_formatting_view.call_args.args[0])
        for boundary in (0, 1, column_count):
            self.assertTrue(any(f'Review column resolution: {boundary}/{column_count}' in message for message in messages))
        timings = plan.stats['project_update_seconds']
        for phase in ('review_index', 'review_candidates', 'review_columns'):
            self.assertIn(phase, timings)
            self.assertGreaterEqual(timings[phase], 0)

    def test_supplied_task_list_needs_no_collection_read_and_still_rejects_duplicate_keys(self):
        session = formatting_session([])
        rows = [task('TEAM-1'), task('team-1', 2)]
        with self.assertRaisesRegex(ProjectAutomationError, 'Duplicate Project key TEAM-1'):
            session.index_tasks_by_key(DEFAULT_CONFIG, task_list=rows)
        self.assertEqual(session.project.collection_reads, 0)
        rows[1].Text10 = 'TEAM-1@REFERENCE'
        self.assertEqual(set(session.index_tasks_by_key(DEFAULT_CONFIG, task_list=rows)),
                         {'TEAM-1', 'TEAM-1@REFERENCE'})
        self.assertEqual(session.project.collection_reads, 0)

    def test_formatting_failure_still_reports_each_failed_candidate(self):
        session = formatting_session([task('TEAM-1')])
        session.color_project_cell_error.return_value = 'COM refused color'
        plan = plan_for([review_item(), review_item(color='review_needed')])
        self.format_quietly(session, plan, DEFAULT_CONFIG)
        self.assertEqual(session.color_project_cell_error.call_count, 2)
        failures = [item for item in plan.audit_items if item.category == 'ProjectCellColoringFailed']
        self.assertEqual(len(failures), 1)
        self.assertIn('2 Project cell(s)', failures[0].message)
        self.assertIn('Text8 (2)', failures[0].message)


class YerpFormattingFidelityTests(unittest.TestCase):
    def test_all_nine_exports_keep_same_visible_candidates_order_and_reference_targets(self):
        yerp = Path(__file__).resolve().parents[1] / 'yerp'
        paths = sorted(yerp.glob('*.csv'))
        config_path = yerp / 'ssn-812-config.yaml'
        if len(paths) != 9 or not config_path.exists():
            self.skipTest('The nine private yerp exports are not available')
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        config = load_config(config_path)
        plan = build_run_plan(paths, config)
        self.assertEqual(len(plan.epics), 2016)
        tasks = []
        for summary in plan.summaries.values():
            tasks.append(SimpleNamespace(ID=len(tasks) + 1, **dict(summary_assignments(summary, config))))
        for epic in plan.epics.values():
            tasks.append(SimpleNamespace(ID=len(tasks) + 1, **dict(epic_assignments(epic, config))))
        session = formatting_session(tasks)
        fields = config['project_fields']
        indexed = {str(getattr(row, fields['j2p_key'], '') or getattr(row, fields['jira_key'], '')).strip().upper(): row
                   for row in tasks}
        aliases = {}

        def resolved(column):
            if column not in aliases:
                aliases[column] = MicrosoftProjectSession.project_selection_aliases(session, column, config)
            return aliases[column]

        # The previous algorithm's inclusion rules and ordering, independently
        # applied to the actual project audit population before calling the code.
        candidates = []
        for item in plan.audit_items:
            if not item.jira_key or not item.color:
                continue
            row = indexed.get((item.schedule_key or item.jira_key).upper())
            column = project_column_for_audit_field(item.field, config)
            if row is not None and column:
                candidates.append((row, column, config['colors'].get(item.color, item.color), resolved(column)))
        columns = review_table_columns(config, [candidate[1] for candidate in candidates])
        visible = {normalize_project_column_name(alias) for column in columns for alias in resolved(column)}
        positions = {normalize_project_column_name(column): index for index, column in enumerate(columns, 1)}
        expected = [(row.ID, column, color, names, first_project_column_position(positions, names))
                    for row, column, color, names in candidates
                    if any(normalize_project_column_name(alias) in visible for alias in names)]
        session.project_table_column_positions.return_value = positions
        session.app.FieldNameToFieldConstant.reset_mock()
        session.app.FieldConstantToFieldName.reset_mock()
        audits_before = list(plan.audit_items)
        with redirect_stdout(io.StringIO()):
            session.apply_review_formatting(plan, config)
        self.assertEqual(color_calls(session), expected)
        self.assertEqual(session.prepare_formatting_view.call_args.args[0], columns)
        self.assertEqual(plan.audit_items, audits_before)
        self.assertGreater(len(expected), 500)
        reference_ids = {row.ID for row in tasks if getattr(row, fields['row_role'], '') == 'Reference'}
        self.assertTrue(reference_ids.intersection({candidate[0] for candidate in expected}))
        self.assertLess(session.app.FieldNameToFieldConstant.call_count, 150)
        self.assertEqual(session.project.collection_reads, 1)
        self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}, before)


if __name__ == '__main__':
    unittest.main()
