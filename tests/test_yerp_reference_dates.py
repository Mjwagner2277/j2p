"""Real yerp membership dates through the Project fake; source exports stay read-only."""

import copy
import hashlib
import io
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from datetime import timedelta

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.project import ProjectAutomationError
from j2p.reference_dates import (
    reference_rollup_ids, synchronize_reference_dates, verify_reference_dates,
)
from yerp_project_support import FILES, YERP, project_from_yerp_plan


def manual_rollup_keys(plan):
    ids = reference_rollup_ids(plan)
    return {(summary.rollup_mode, summary.key.upper()) for summary in plan.summaries.values()
            if summary.summary_id in ids}


@unittest.skipUnless(len(FILES) == 9 and (YERP / 'ssn-812-config.yaml').exists(),
                     'Requires all nine private yerp exports and project configuration')
class YerpReferenceDateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}
        cls.config = load_config(YERP / 'ssn-812-config.yaml')
        cls.source_plan = build_run_plan(FILES, cls.config)

    @classmethod
    def tearDownClass(cls):
        if {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES} != cls.hashes:
            raise AssertionError('A source yerp CSV changed during reference date tests')

    def setUp(self):
        self.plan = copy.deepcopy(self.source_plan)
        self.session, self.epics, self.summaries = project_from_yerp_plan(self.plan, self.config, scheduled_dates=True)
        self.summary_map = {
            (summary.rollup_mode, summary.key.upper()): self.summaries[summary.summary_id]
            for summary in self.plan.summaries.values()
        }

    def raw_dates(self):
        return {key: (self.epics[key].Start, self.epics[key].Finish)
                for key, epic in self.plan.epics.items() if epic.drives_schedule}

    def synchronize(self):
        with redirect_stdout(io.StringIO()):
            synchronize_reference_dates(self.session, self.plan, self.config,
                                        self.epics, self.summary_map, self.raw_dates())

    def verify(self):
        with redirect_stdout(io.StringIO()):
            return verify_reference_dates(self.session, self.plan, self.config,
                                          self.epics, self.summary_map)

    def protected_values(self):
        fields = self.config['project_fields']
        names = [fields['jira_target_start'], fields['jira_target_end'], 'Predecessors']
        names.extend(field for field in fields.values() if field.startswith('Number'))
        return {
            key: {name: getattr(task, name, None) for name in names}
            for key, task in {**self.epics, **self.summaries}.items()
        }

    def assert_reference_geometry(self):
        manual = manual_rollup_keys(self.plan)
        self.assertGreater(len(manual), 0)
        for key, epic in self.plan.epics.items():
            task = self.epics[key]
            if epic.drives_schedule:
                continue
            primary = self.epics[epic.primary_schedule_key]
            self.assertEqual((task.Start, task.Finish), (primary.Start, primary.Finish), key)
            self.assertIs(task.Active, False, key)
            self.assertIs(task.Manual, False, key)
        for identity, task in self.summary_map.items():
            if identity not in manual:
                self.assertIs(task.Manual, False, identity)
                continue
            members = [self.epics[epic.key if epic.drives_schedule else epic.primary_schedule_key]
                       for epic in self.plan.epics.values()
                       if (epic.rollup_mode, epic.rollup_key.upper()) == identity]
            self.assertEqual(task.Start, min(member.Start for member in members), identity)
            self.assertEqual(task.Finish, max(member.Finish for member in members), identity)
            self.assertIs(task.Manual, True, identity)

    def test_current_real_plan_reference_dates_are_already_stable_and_verify(self):
        original = copy.deepcopy(self.plan)
        protected = self.protected_values()
        self.synchronize()
        self.assert_reference_geometry()
        self.assertEqual([write for task in self.session.project.Tasks.items for write in task.writes], [])
        self.assertGreater(self.verify(), 0)
        self.assertEqual(self.protected_values(), protected)
        self.assertEqual(self.plan.epics, original.epics)
        self.assertEqual(self.plan.summaries, original.summaries)
        references = [epic for epic in self.plan.epics.values() if not epic.drives_schedule]
        manual_summaries = [summary for summary in self.plan.summaries.values()
                            if (summary.rollup_mode, summary.key.upper()) in manual_rollup_keys(self.plan)]
        self.assertEqual(len(references), 495)
        self.assertTrue(any(summary.driving_epic_count == 0 for summary in manual_summaries))
        self.assertTrue(any(summary.driving_epic_count > 0 for summary in manual_summaries))

    def test_final_primary_time_shifts_propagate_to_every_reference_membership_once(self):
        refs_by_primary = {}
        for epic in self.plan.epics.values():
            if not epic.drives_schedule:
                refs_by_primary.setdefault(epic.primary_schedule_key, []).append(epic)
        manual = manual_rollup_keys(self.plan)
        candidates = [key for key in refs_by_primary
                      if (self.plan.epics[key].rollup_mode, self.plan.epics[key].rollup_key.upper()) in manual]
        primary_key = max(candidates, key=lambda key: len(refs_by_primary[key]))
        reference_rows = refs_by_primary[primary_key]
        self.assertGreater(len(reference_rows), 1)
        protected = self.protected_values()
        source_epics = {key: asdict(epic) for key, epic in self.plan.epics.items()}
        source_summaries = {key: asdict(summary) for key, summary in self.plan.summaries.items()}
        raw = self.raw_dates()
        start = (min(pair[0] for pair in raw.values()) - timedelta(days=7)).replace(hour=9, minute=17)
        finish = (max(pair[1] for pair in raw.values()) + timedelta(days=7)).replace(hour=18, minute=43)
        # Simulate native Project scheduling on the one active driving row.
        self.epics[primary_key].task.Start = start
        self.epics[primary_key].task.Finish = finish
        self.synchronize()
        memberships = [self.plan.epics[primary_key], *reference_rows]
        for epic in memberships:
            row = self.epics[epic.key]
            self.assertEqual((row.Start, row.Finish), (start, finish), epic.key)
            rollup = self.summary_map[(epic.rollup_mode, epic.rollup_key.upper())]
            self.assertEqual((rollup.Start, rollup.Finish), (start, finish), epic.rollup_key)
        self.assert_reference_geometry()
        self.assertEqual(self.epics[primary_key].writes, [])
        self.assertEqual({key for key, task in self.epics.items() if task.writes},
                         {epic.key for epic in reference_rows})
        self.assertTrue(all(field in {'Start', 'Finish'}
                            for task in self.epics.values() for field, _ in task.writes))
        self.assertTrue(all(field in {'Manual', 'StartText', 'FinishText'}
                            for task in self.summaries.values() for field, _ in task.writes))
        self.assertTrue(self.session.app.DateFormat.called)
        self.assertTrue(all(call.args[1] == 2 for call in self.session.app.DateFormat.call_args_list))
        self.assertEqual(self.protected_values(), protected)
        self.assertEqual({key: asdict(epic) for key, epic in self.plan.epics.items()}, source_epics)
        self.assertEqual({key: asdict(summary) for key, summary in self.plan.summaries.items()}, source_summaries)
        self.assertGreater(self.verify(), 0)
        for task in self.session.project.Tasks.items:
            task.writes.clear()
        self.synchronize()
        self.assertEqual([write for task in self.session.project.Tasks.items for write in task.writes], [])
        self.assertGreater(self.verify(), 0)

    def test_old_automatic_rollups_migrate_using_text_fields_with_read_only_native_dates(self):
        manual = manual_rollup_keys(self.plan)
        original_primary_dates = self.raw_dates()
        for identity in manual:
            task = self.summary_map[identity]
            task.task.Manual = False
            task.task._start_native += timedelta(days=3)
            task.task._finish_native += timedelta(days=4)
        first = self.summary_map[next(iter(manual))].task
        with self.assertRaises(AttributeError):
            first.Start = first.Start + timedelta(days=1)
        with self.assertRaises(AttributeError):
            first.Finish = first.Finish + timedelta(days=1)
        self.synchronize()
        self.assert_reference_geometry()
        self.assertEqual(self.raw_dates(), original_primary_dates)
        self.assertEqual({identity for identity, task in self.summary_map.items() if task.writes}, manual)
        for identity in manual:
            fields = {field for field, _ in self.summary_map[identity].writes}
            self.assertEqual(fields, {'Manual', 'StartText', 'FinishText'})
        self.assertGreater(self.verify(), 0)

    def test_fresh_verification_detects_a_real_reference_that_no_longer_matches_its_primary(self):
        self.synchronize()
        reference = next(epic for epic in self.plan.epics.values() if not epic.drives_schedule)
        self.epics[reference.key].task.Finish += timedelta(minutes=7)
        with self.assertRaises(ProjectAutomationError):
            self.verify()


if __name__ == '__main__':
    unittest.main()
