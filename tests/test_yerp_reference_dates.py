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


def reference_rollup_keys(plan):
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
            self.session.recalculate()

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
        self.assertGreater(len(reference_rollup_keys(self.plan)), 0)
        fields = self.config['project_fields']
        for key, epic in self.plan.epics.items():
            task = self.epics[key]
            primary = self.epics[epic.key if epic.drives_schedule else epic.primary_schedule_key]
            self.assertEqual((task.Start, task.Finish),
                             (primary.Start, primary.Finish), key)
            self.assertIs(task.Manual, not epic.drives_schedule, key)
            if not epic.drives_schedule:
                self.assertEqual((task.Start, task.Finish), (primary.Start, primary.Finish), key)
                self.assertIs(task.Active, True, key)
        for identity, task in self.summary_map.items():
            members = [self.epics[epic.key if epic.drives_schedule else epic.primary_schedule_key]
                       for epic in self.plan.epics.values()
                       if (epic.rollup_mode, epic.rollup_key.upper()) == identity]
            self.assertEqual(task.Start, min(member.Start for member in members), identity)
            self.assertEqual(task.Finish, max(member.Finish for member in members), identity)
            self.assertIs(task.Manual, False, identity)

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
        reference_summaries = [summary for summary in self.plan.summaries.values()
                               if (summary.rollup_mode, summary.key.upper()) in reference_rollup_keys(self.plan)]
        self.assertEqual(len(references), 495)
        self.assertTrue(any(summary.driving_epic_count == 0 for summary in reference_summaries))
        self.assertTrue(any(summary.driving_epic_count > 0 for summary in reference_summaries))

    def test_sswsw_10467_creation_copies_receive_january_primary_start_through_manual_properties(self):
        primary = self.epics['SSWSW-10467']
        copies = [epic for epic in self.plan.epics.values()
                  if not epic.drives_schedule and epic.primary_schedule_key == 'SSWSW-10467']
        self.assertEqual(len(copies), 4)
        self.assertIn('SSWSW-10467::FV::PI-17::1C682DC7', {epic.key for epic in copies})
        self.assertEqual(primary.Start.strftime('%Y-%m-%d'), '2026-01-07')
        primary_dates = self.raw_dates()
        protected = self.protected_values()
        # Newly created manual copies still have their default one-day dates;
        # the primary has completed its scheduling pass. Never change the CSV.
        for epic in copies:
            row = self.epics[epic.key].task
            row.Start = primary.Start.replace(month=9, day=18)
            row.Finish = row.Start.replace(hour=17)
        self.synchronize()
        self.assertEqual(self.raw_dates(), primary_dates)
        self.assertEqual(self.protected_values(), protected)
        for epic in copies:
            row = self.epics[epic.key]
            self.assertEqual((row.Start, row.Finish), (primary.Start, primary.Finish))
            self.assertEqual([field for field, _ in row.writes], ['StartText', 'FinishText'])
        self.assertFalse(primary.writes)
        self.assertFalse(any(task.writes for task in self.summary_map.values()))
        self.assertEqual(self.session.app.DateFormat.call_count, 2)
        self.assert_reference_geometry()
        self.assertGreater(self.verify(), 0)

    def test_final_primary_time_shifts_propagate_to_every_reference_membership_once(self):
        refs_by_primary = {}
        for epic in self.plan.epics.values():
            if not epic.drives_schedule:
                refs_by_primary.setdefault(epic.primary_schedule_key, []).append(epic)
        reference_rollups = reference_rollup_keys(self.plan)
        candidates = [key for key in refs_by_primary
                      if (self.plan.epics[key].rollup_mode, self.plan.epics[key].rollup_key.upper()) in reference_rollups]
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
            self.assertEqual((rollup.Start, rollup.Finish),
                             (start, finish), epic.rollup_key)
        self.assert_reference_geometry()
        self.assertEqual(self.epics[primary_key].writes, [])
        self.assertEqual({key for key, task in self.epics.items() if task.writes},
                         {epic.key for epic in reference_rows})
        self.assertTrue(all(field in {'StartText', 'FinishText'}
                            for task in self.epics.values() for field, _ in task.writes))
        self.assertFalse(any(task.writes for task in self.summaries.values()))
        self.assertEqual(self.session.app.DateFormat.call_count, 2)
        self.assertEqual(self.protected_values(), protected)
        self.assertEqual({key: asdict(epic) for key, epic in self.plan.epics.items()}, source_epics)
        self.assertEqual({key: asdict(summary) for key, summary in self.plan.summaries.items()}, source_summaries)
        self.assertGreater(self.verify(), 0)
        for task in self.session.project.Tasks.items:
            task.writes.clear()
        self.synchronize()
        self.assertEqual([write for task in self.session.project.Tasks.items for write in task.writes], [])
        self.assertGreater(self.verify(), 0)

    def test_native_summaries_recalculate_stale_headers_without_setters(self):
        primary_dates = self.raw_dates()
        for task in self.summary_map.values():
            task.task._start_native += timedelta(days=3)
            task.task._finish_native += timedelta(days=4)
        first = next(iter(self.summary_map.values())).task
        with self.assertRaises(AttributeError):
            first.Start = first.Start + timedelta(days=1)
        with self.assertRaises(AttributeError):
            first.Finish = first.Finish + timedelta(days=1)
        self.synchronize()
        self.assert_reference_geometry()
        self.assertEqual(self.raw_dates(), primary_dates)
        self.assertFalse(any(task.writes for task in self.summary_map.values()))
        self.assertGreater(self.verify(), 0)

    def test_sswsw_10464_stays_unchanged_when_fst_native_window_needs_refresh(self):
        epic = self.plan.epics['SSWSW-10464']
        self.assertTrue(epic.drives_schedule)
        self.assertEqual((epic.rollup_mode, epic.rollup_key), ('fixVersion', 'FST'))
        self.assertEqual((epic.target_start, epic.target_end, epic.predecessors, epic.successors), ('', '', [], []))
        primary = self.epics[epic.key]
        fst = self.summary_map['fixVersion', 'FST']
        fst.task.summary_shift_children = [primary.task]
        # A summary StartText write would move this unconstrained child. The
        # active-copy calculation must not invoke that native summary edit.
        fst.task._start_native += timedelta(days=120)
        native_summary = (fst.Start, fst.Finish)
        primary_dates = self.raw_dates()
        protected = self.protected_values()
        self.synchronize()
        self.assertEqual(self.raw_dates(), primary_dates)
        self.assertEqual(primary.writes, [])
        self.assertNotEqual((fst.Start, fst.Finish), native_summary)
        self.assertEqual({field for field, _ in fst.writes}, set())
        self.assertFalse(any(field in {'Start', 'Finish', 'StartText', 'FinishText'}
                             for task in self.summary_map.values() for field, _ in task.writes))
        self.assertEqual(self.protected_values(), protected)
        self.assert_reference_geometry()
        self.assertGreater(self.verify(), 0)

    def test_fresh_verification_detects_a_real_reference_that_no_longer_matches_its_primary(self):
        self.synchronize()
        reference = next(epic for epic in self.plan.epics.values() if not epic.drives_schedule)
        self.epics[reference.key].task.Finish += timedelta(minutes=7)
        with self.assertRaises(ProjectAutomationError):
            self.verify()

    def test_old_manual_fst_must_be_migrated_before_schedule_capture(self):
        self.summary_map['fixVersion', 'FST'].task.Manual = True
        primary_dates = self.raw_dates()
        with self.assertRaises(ProjectAutomationError):
            self.synchronize()
        self.assertEqual(self.raw_dates(), primary_dates)
        self.assertIs(self.summary_map['fixVersion', 'FST'].Manual, True)
        self.assertFalse(self.session._reference_dates_synchronized)

    def test_verification_still_rejects_an_unexpected_sswsw_10464_primary_shift(self):
        self.synchronize()
        primary = self.epics['SSWSW-10464'].task
        primary.Start += timedelta(hours=1)
        primary.Finish += timedelta(hours=1)
        with self.assertRaisesRegex(ProjectAutomationError, 'primary=SSWSW-10464'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
