"""Verified schedule display fields and cosmetic Gantt formatting boundaries."""

import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from j2p.config import load_config
from j2p.schedule_display import configure_schedule_display


def fixture(result=True, aliases=None):
    app = SimpleNamespace(GanttBarStyleEdit=Mock(return_value=result))
    session = SimpleNamespace(
        app=app,
        project_selection_aliases=Mock(side_effect=lambda column, config: (aliases or {}).get(column, [column])),
    )
    plan = SimpleNamespace(summaries={'fixVersion:A': object()}, stats={}, audit_items=[])
    return session, plan, load_config(None)


class ScheduleDisplayTests(unittest.TestCase):
    def test_default_date_fields_and_two_visible_schedule_columns(self):
        config = load_config(None)
        self.assertEqual(config['project_fields']['schedule_start'], 'Date3')
        self.assertEqual(config['project_fields']['schedule_finish'], 'Date4')
        self.assertEqual(config['project_field_names']['schedule_start'], 'Schedule Start')
        self.assertEqual(config['project_field_names']['schedule_finish'], 'Schedule Finish')
        exposed = config['review_table']['exposed_columns']
        self.assertTrue({'schedule_start', 'schedule_finish'} <= set(exposed))
        self.assertFalse({'start', 'finish'} & set(exposed))

    def test_yerp_uses_the_same_schedule_display_fields(self):
        path = Path(__file__).resolve().parents[1] / 'yerp' / 'ssn-812-config.yaml'
        if not path.exists():
            self.skipTest('Requires project-specific yerp configuration')
        config = load_config(path)
        self.assertEqual(config['project_fields']['schedule_start'], 'Date3')
        self.assertEqual(config['project_fields']['schedule_finish'], 'Date4')
        exposed = config['review_table']['exposed_columns']
        self.assertTrue({'schedule_start', 'schedule_finish'} <= set(exposed))
        self.assertFalse({'start', 'finish'} & set(exposed))

    def test_summary_style_edit_uses_only_explicit_field_names_without_creating_rows(self):
        session, plan, config = fixture()
        self.assertTrue(configure_schedule_display(session, plan, config))
        session.app.GanttBarStyleEdit.assert_called_once_with(
            Item='Summary', Create=False, From='Date3', To='Date4',
        )
        self.assertEqual(plan.audit_items, [])
        self.assertEqual(plan.stats['project_schedule_display'], {'configured': True, 'attempts': 1})

    def test_repeated_run_edits_same_style_without_appending_duplicates(self):
        session, plan, config = fixture()
        configure_schedule_display(session, plan, config)
        configure_schedule_display(session, plan, config)
        self.assertEqual(session.app.GanttBarStyleEdit.call_count, 2)
        self.assertTrue(all(call.kwargs['Item'] == 'Summary' and call.kwargs['Create'] is False
                            for call in session.app.GanttBarStyleEdit.call_args_list))

    def test_custom_remapped_fields_and_resolved_localized_style_are_supported(self):
        session, plan, config = fixture(aliases={'Summary': ['Résumé'], 'Date8': ['Début'], 'Date9': ['Fin']})
        config['project_fields'].update(schedule_start='Date8', schedule_finish='Date9')
        config['project_field_names'].update(schedule_start='Visible Start', schedule_finish='Visible Finish')
        session.app.GanttBarStyleEdit.side_effect = lambda **kwargs: (
            kwargs == {'Item': 'Résumé', 'Create': False, 'From': 'Début', 'To': 'Fin'}
        )
        self.assertTrue(configure_schedule_display(session, plan, config))
        self.assertEqual(session.app.GanttBarStyleEdit.call_args.kwargs,
                         {'Item': 'Résumé', 'Create': False, 'From': 'Début', 'To': 'Fin'})
        self.assertEqual(plan.audit_items, [])

    def test_failure_is_one_cosmetic_warning_without_other_mutations(self):
        for result in (False, None):
            session, plan, config = fixture(result=result)
            before_config = copy.deepcopy(config)
            with self.subTest(result=result):
                self.assertFalse(configure_schedule_display(session, plan, config))
                self.assertEqual(len(plan.audit_items), 1)
                audit = plan.audit_items[0]
                self.assertEqual(audit.category, 'ProjectScheduleDisplayFormattingFailed')
                self.assertEqual(audit.severity, 'Warning')
                self.assertIn('display-only', audit.message)
                self.assertIn('Schedule Start and Schedule Finish', audit.reviewer_action)
                self.assertFalse(plan.stats['project_schedule_display']['configured'])
                self.assertEqual(config, before_config)
                self.assertTrue(all(call.kwargs['Create'] is False for call in session.app.GanttBarStyleEdit.call_args_list))

    def test_rejected_alias_resolution_falls_back_to_known_summary_name(self):
        session, plan, config = fixture()
        session.project_selection_aliases.side_effect = RuntimeError('No field resolver')
        self.assertTrue(configure_schedule_display(session, plan, config))
        self.assertEqual(session.app.GanttBarStyleEdit.call_args.kwargs['Item'], 'Summary')

    def test_exception_path_is_bounded_and_never_guesses_style_row_numbers(self):
        session, plan, config = fixture()
        session.project_selection_aliases.side_effect = lambda column, config: [f'{column}-{n}' for n in range(100)]
        session.app.GanttBarStyleEdit.side_effect = RuntimeError('Style unavailable')
        self.assertFalse(configure_schedule_display(session, plan, config))
        self.assertLessEqual(session.app.GanttBarStyleEdit.call_count, 64)
        self.assertEqual(len(plan.audit_items), 1)
        self.assertIn('Style unavailable', plan.audit_items[0].message)
        self.assertTrue(all(not call.kwargs['Item'].lstrip('-').isdigit()
                            for call in session.app.GanttBarStyleEdit.call_args_list))

    def test_no_summary_rows_need_no_style_operation(self):
        session, plan, config = fixture()
        plan.summaries = {}
        self.assertTrue(configure_schedule_display(session, plan, config))
        session.app.GanttBarStyleEdit.assert_not_called()
        self.assertEqual(plan.audit_items, [])


if __name__ == '__main__':
    unittest.main()
