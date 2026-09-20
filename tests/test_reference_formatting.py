"""Reference appearance must not alter scheduling or format an unintended row."""

import copy
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from j2p.models import RunPlan
from j2p.reference_formatting import format_reference_rows


def task(row, active=True):
    return SimpleNamespace(
        ID=row, UniqueID=1000 + row, Active=active, ExternalTask=False,
        Name=f'Epic {row}', Start='2026-09-01', Finish='2026-09-03',
        PercentComplete=40, Number1=5.0, Number2=2.0, Number7=40.0,
        CellColorEx=0xC6EFCE, FontColorEx=0x303030, Bold=False,
        Text11='Reference', Strikethrough=True,
    )


def plan_for(keys):
    return RunPlan(
        generated_at='2026-09-19', jira_csv='test.csv', rollup_mode='mixed',
        column_map={}, stats={}, summaries={},
        epics={key: SimpleNamespace(key=key, drives_schedule=drives)
               for key, drives in keys}, audit_items=[],
    )


class FormattingApp:
    def __init__(self, tasks):
        self.rows = {item.ID: item for item in tasks}
        self.ActiveCell = SimpleNamespace(Task=None)
        self.selected_rows = []
        self.font_calls = []
        self.selection_results = {}
        self.font_results = {}
        self.selection_targets = {}

    def SelectRow(self, **kwargs):
        self.selected_rows.append(kwargs)
        row = kwargs['Row']
        result = self.selection_results.get(row, True)
        if isinstance(result, Exception):
            raise result
        self.ActiveCell.Task = self.selection_targets.get(row, self.rows[row])
        return result

    def FontStrikethrough(self, value):
        selected = self.ActiveCell.Task
        self.font_calls.append((selected.ID, value))
        result = self.font_results.get(selected.ID, True)
        if isinstance(result, Exception):
            raise result
        if result:
            selected.Strikethrough = value
        return result


def apply(plan, tasks, app=None):
    app = app or FormattingApp(tasks.values())
    with redirect_stdout(io.StringIO()) as output:
        format_reference_rows(SimpleNamespace(app=app), plan, tasks)
    return app, output.getvalue()


class ReferenceFormattingTests(unittest.TestCase):
    def test_only_references_are_formatted_without_changing_data_or_colors(self):
        primary, reference = task(1, True), task(2)
        before_primary, before_reference = vars(primary).copy(), vars(reference).copy()
        plan = plan_for([('TEAM-1', True), ('TEAM-1::v2', False)])
        app, _ = apply(plan, {'TEAM-1': primary, 'TEAM-1::V2': reference})
        self.assertEqual(app.selected_rows, [dict(Row=2, RowRelative=False, Height=0,
                                                 Extend=False, Add=False)])
        self.assertEqual(app.font_calls, [(2, False)])
        self.assertEqual(vars(primary), before_primary)
        before_reference['Strikethrough'] = False
        self.assertEqual(vars(reference), before_reference)
        self.assertEqual(plan.stats['project_reference_formatting'],
                         dict(total=1, applied=1, failed=0))
        self.assertEqual(plan.audit_items, [])

    def test_native_row_order_avoids_ui_jumps_and_reuses_resolved_ids(self):
        class CountedTask(SimpleNamespace):
            def __getattribute__(self, name):
                if name == 'ID':
                    self.id_reads += 1
                return super().__getattribute__(name)

        references = {f'TEAM-{row}': CountedTask(**vars(task(row)), id_reads=0)
                      for row in (30, 2, 10)}
        app = FormattingApp(references.values())
        def remove_strike(value):
            app.font_calls.append((app.ActiveCell.Task.UniqueID, value))
            app.ActiveCell.Task.Strikethrough = value
            return True
        app.FontStrikethrough = remove_strike
        for reference in references.values():
            reference.id_reads = 0
        plan = plan_for([(key, False) for key in references])
        apply(plan, references, app)
        self.assertEqual([entry['Row'] for entry in app.selected_rows], [2, 10, 30])
        self.assertEqual([item.id_reads for item in references.values()], [1, 1, 1])
        self.assertEqual(plan.stats['project_reference_formatting'],
                         dict(total=3, applied=3, failed=0))

    def test_same_row_id_but_wrong_unique_identity_is_not_formatted(self):
        reference, wrong = task(2), task(2, True)
        wrong.UniqueID = 9000
        plan = plan_for([('TEAM-2', False)])
        app = FormattingApp([reference])
        app.selection_targets[2] = wrong
        apply(plan, {'TEAM-2': reference}, app)
        self.assertEqual(app.font_calls, [])
        self.assertEqual(plan.stats['project_reference_formatting']['failed'], 1)
        self.assertIn('does not match', plan.audit_items[0].message)
        self.assertTrue(reference.Active)
        self.assertTrue(wrong.Active)

    def test_external_task_with_same_unique_id_is_not_formatted(self):
        reference, wrong = task(2), task(2)
        wrong.ExternalTask = True
        plan = plan_for([('TEAM-2', False)])
        app = FormattingApp([reference])
        app.selection_targets[2] = wrong
        apply(plan, {'TEAM-2': reference}, app)
        self.assertEqual(app.font_calls, [])
        self.assertEqual(plan.stats['project_reference_formatting']['failed'], 1)

    def test_unreadable_selected_identity_is_not_formatted(self):
        reference = task(2)
        app = FormattingApp([reference])
        app.selection_targets[2] = SimpleNamespace()
        plan = plan_for([('TEAM-2', False)])
        apply(plan, {'TEAM-2': reference}, app)
        self.assertEqual(app.font_calls, [])
        self.assertEqual(len(plan.audit_items), 1)

    def test_rejected_selections_never_format_and_failures_are_aggregated(self):
        references = {f'TEAM-{i}': task(i) for i in range(1, 4)}
        app = FormattingApp(references.values())
        app.selection_results = {1: False, 2: RuntimeError('Cannot select task')}
        plan = plan_for([(key, False) for key in references])
        apply(plan, references, app)
        self.assertEqual(app.font_calls, [(3, False)])
        self.assertEqual(plan.stats['project_reference_formatting'],
                         dict(total=3, applied=1, failed=2))
        self.assertEqual(len(plan.audit_items), 1)
        audit = plan.audit_items[0]
        self.assertEqual((audit.category, audit.severity, audit.field, audit.color, audit.jira_key),
                         ('ProjectReferenceFormattingFailed', 'Warning', 'Reference Formatting',
                          'review_needed', ''))
        self.assertIn('2 of 3', audit.message)

    def test_font_failure_is_cosmetic_and_does_not_stop_later_rows(self):
        references = {f'TEAM-{i}': task(i) for i in range(1, 4)}
        app = FormattingApp(references.values())
        app.font_results = {1: False, 2: RuntimeError('Command unavailable')}
        before = {key: vars(item).copy() for key, item in references.items()}
        plan = plan_for([(key, False) for key in references])
        apply(plan, references, app)
        self.assertEqual(app.font_calls, [(1, False), (2, False), (3, False)])
        self.assertEqual(plan.stats['project_reference_formatting'],
                         dict(total=3, applied=1, failed=2))
        self.assertEqual(vars(references['TEAM-1']), before['TEAM-1'])
        self.assertEqual(vars(references['TEAM-2']), before['TEAM-2'])
        self.assertTrue(all(item.Active is True for item in references.values()))
        self.assertEqual(len(plan.audit_items), 1)

    def test_inactive_or_unreadable_reference_is_not_selected_or_reactivated(self):
        active, unreadable = task(1, False), task(2)
        del unreadable.Active
        plan = plan_for([('TEAM-1', False), ('TEAM-2', False)])
        app, _ = apply(plan, {'TEAM-1': active, 'TEAM-2': unreadable})
        self.assertEqual(app.selected_rows, [])
        self.assertEqual(app.font_calls, [])
        self.assertFalse(active.Active)
        self.assertFalse(hasattr(unreadable, 'Active'))
        self.assertEqual(plan.stats['project_reference_formatting']['failed'], 2)

    def test_activation_readback_failure_is_reported_without_rewriting_task(self):
        reference = task(1)
        app = FormattingApp([reference])
        def lose_active_readback(_value):
            del reference.Active
            return True
        app.FontStrikethrough = lose_active_readback
        plan = plan_for([('TEAM-1', False)])
        apply(plan, {'TEAM-1': reference}, app)
        self.assertEqual(plan.stats['project_reference_formatting'],
                         dict(total=1, applied=0, failed=1))
        self.assertFalse(hasattr(reference, 'Active'))
        self.assertIn('after formatting', plan.audit_items[0].message)

    def test_missing_and_invalid_rows_are_reported_without_ui_calls(self):
        plan = plan_for([('TEAM-1', False), ('TEAM-2', False)])
        invalid = task(0)
        app, _ = apply(plan, {'TEAM-2': invalid})
        self.assertEqual(app.selected_rows, [])
        self.assertEqual(app.font_calls, [])
        self.assertEqual(plan.stats['project_reference_formatting']['failed'], 2)

    def test_no_references_requires_no_project_ui_and_keeps_audit_unchanged(self):
        plan = plan_for([('TEAM-1', True)])
        before = copy.deepcopy(plan)
        app = SimpleNamespace()
        apply(plan, {}, app)
        self.assertEqual(plan.epics, before.epics)
        self.assertEqual(plan.audit_items, before.audit_items)
        self.assertEqual(plan.stats['project_reference_formatting'],
                         dict(total=0, applied=0, failed=0))

    def test_progress_is_bounded_and_error_examples_are_limited(self):
        plan = plan_for([(f'TEAM-{i}', False) for i in range(101)])
        _, output = apply(plan, {})
        self.assertIn('Reference row formatting: 50/101', output)
        self.assertIn('Reference row formatting: 100/101', output)
        self.assertIn('Reference row formatting: 101/101', output)
        self.assertEqual(len(plan.audit_items), 1)
        self.assertIn('101 of 101', plan.audit_items[0].message)
        self.assertEqual(plan.audit_items[0].message.count('formatting index'), 5)
        self.assertLess(len(plan.audit_items[0].message), 1800)


if __name__ == '__main__':
    unittest.main()
