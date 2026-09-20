"""Surface schedule drivers while keeping every native-date audit and cell color."""

import copy
import csv
import hashlib
import html as html_lib
import io
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict
from pathlib import Path

import test_project_formatting_scaling as formatting
from test_minimal_report import assert_minimal_report
from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.reports import (
    render_schedule_cascade_review, write_audit_csv, write_manager_html,
)
from test_project_schedule_colors import (
    date_changes, dates, prepare_display_fixture, review, review_session, schedule_plan,
)
from yerp_project_support import FILES, YERP


def text_content(rendered):
    return html_lib.unescape(re.sub(r'<[^>]*>', ' ', rendered))


def branches(rendered):
    return re.findall(r'<details class="cascade-branch">(.*?)</details>', rendered, re.S)


def branch_summary(branch):
    return text_content(branch.split('</summary>', 1)[0])


def dated_plan(count=3):
    plan, config = schedule_plan(count)
    for key, epic in plan.epics.items():
        epic.completed = False
        epic.in_planning = False
        epic.summary = f'Scheduled work {key}'
    return plan, config


def connect(plan, predecessor, successor):
    plan.epics[predecessor].successors.append(successor)
    plan.epics[successor].predecessors.append(predecessor)


def color_and_render(plan, config, before, after):
    tasks = []
    for index, (key, values) in enumerate(after.items(), start=1):
        task = formatting.task(key, index)
        task.Start, task.Finish = values.start, values.finish
        tasks.append(task)
    session = formatting.formatting_session(tasks)
    prepare_display_fixture(session, plan, tasks)
    with redirect_stdout(io.StringIO()):
        session.apply_review_formatting(plan, config, before=before)
    colors = {
        (call.args[0].ID, call.args[1]): call.args[2]
        for call in session.color_project_cell_error.call_args_list
    }
    return render_schedule_cascade_review(plan, project_update_run=True), colors


class ScheduleDateReportTests(unittest.TestCase):
    def assert_driver_section_only(self, rendered):
        self.assertEqual(re.findall(r'<h2>(.*?)</h2>', rendered), ['Schedule Drivers'])
        for redundant in ('Project Date Changes', 'Jira Target Differences',
                          'Schedule Cascade Detail', 'Red Branch Drivers',
                          'Green Finish Changes', 'Start Changes', 'Finish Changes'):
            self.assertNotIn(redundant, rendered)
        self.assertNotIn('briefing-item', rendered)

    def test_existing_jira_mismatches_stay_yellow_without_a_schedule_driver(self):
        plan, config = dated_plan(1)
        before = {'TEAM-1': dates('TEAM-1', '2026-09-08', '2026-09-11')}
        rendered, colors = color_and_render(plan, config, before, copy.deepcopy(before))
        self.assert_driver_section_only(rendered)
        self.assertEqual(branches(rendered), [])
        self.assertEqual(date_changes(plan), [])
        self.assertEqual({item.field for item in plan.audit_items
                          if item.category == 'ScheduledDateMismatch'}, {'Start', 'Finish'})
        self.assertEqual(colors[(1, 'Date3')], config['colors']['review_needed'])
        self.assertEqual(colors[(1, 'Date4')], config['colors']['review_needed'])
        self.assertNotIn('<div class="cascade-node ', rendered)

    def test_independent_start_and_finish_changes_stay_green_but_are_not_drivers(self):
        plan, config = dated_plan(2)
        before = {key: dates(key) for key in plan.epics}
        after = {'TEAM-1': dates('TEAM-1', start='2026-09-02'),
                 'TEAM-2': dates('TEAM-2', finish='2026-09-07')}
        rendered, colors = color_and_render(plan, config, before, after)
        self.assert_driver_section_only(rendered)
        self.assertEqual(branches(rendered), [])
        self.assertEqual({(item.schedule_key, item.field) for item in date_changes(plan)},
                         {('TEAM-1', 'Start'), ('TEAM-2', 'Finish')})
        self.assertEqual(colors[(1, 'Date3')], config['colors']['changed_cell'])
        self.assertEqual(colors[(2, 'Date4')], config['colors']['changed_cell'])

    def test_finish_driver_includes_start_only_downstream_impact_and_summary_dates(self):
        plan, config = dated_plan(2)
        connect(plan, 'TEAM-1', 'TEAM-2')
        before = {key: dates(key) for key in plan.epics}
        before['TEAM-2'].finish = '2026-09-11'
        after = {'TEAM-1': dates('TEAM-1', finish='2026-09-07'),
                 'TEAM-2': dates('TEAM-2', start='2026-09-08', finish='2026-09-11')}
        rendered, colors = color_and_render(plan, config, before, after)
        self.assert_driver_section_only(rendered)
        self.assertEqual(len(branches(rendered)), 1)
        summary = branch_summary(branches(rendered)[0])
        for expected in ('TEAM-1', plan.epics['TEAM-1'].summary, 'Finish',
                         '2026-09-04', '2026-09-07', '1 affected task'):
            self.assertIn(expected, summary)
        self.assertNotRegex(rendered, r'<details[^>]*\bopen\b')
        self.assertIn('TEAM-2', branches(rendered)[0])
        self.assertRegex(text_content(branches(rendered)[0]), r'Start:\s*2026-09-01\s*(?:->|→)\s*2026-09-08')
        self.assertEqual(colors[(1, 'Date4')], config['colors']['changed_cell'])
        self.assertEqual(colors[(2, 'Date3')], config['colors']['changed_cell'])

    def test_nodes_show_both_changed_dates_without_duplicate_branches(self):
        plan, config = dated_plan(3)
        connect(plan, 'TEAM-1', 'TEAM-2')
        connect(plan, 'TEAM-2', 'TEAM-3')
        before = {key: dates(key) for key in plan.epics}
        after = {key: dates(key, '2026-09-02', '2026-09-07') for key in plan.epics}
        review(review_session(), plan, before, config, after)
        rendered = render_schedule_cascade_review(plan, True)
        self.assertEqual(len(branches(rendered)), 1)
        self.assertIn('2 affected tasks', branch_summary(branches(rendered)[0]))
        self.assertEqual(len(re.findall(r'Start:\s*2026-09-01\s*(?:->|→)\s*2026-09-02', text_content(rendered))), 3)
        self.assertEqual(rendered.count('<div class="cascade-node '), 3)
        for key in plan.epics:
            self.assertIn(key, branches(rendered)[0])

    def test_link_alone_does_not_make_a_changed_finish_a_driver(self):
        plan, config = dated_plan(2)
        connect(plan, 'TEAM-1', 'TEAM-2')
        before = {key: dates(key) for key in plan.epics}
        after = copy.deepcopy(before)
        after['TEAM-1'].finish = '2026-09-07'
        review(review_session(), plan, before, config, after)
        self.assertEqual(branches(render_schedule_cascade_review(plan, True)), [])
        self.assertEqual(len(date_changes(plan)), 1)

    def test_start_only_upstream_shift_is_not_a_finish_driver(self):
        plan, config = dated_plan(2)
        connect(plan, 'TEAM-1', 'TEAM-2')
        before = {key: dates(key) for key in plan.epics}
        after = {'TEAM-1': dates('TEAM-1', start='2026-09-02'),
                 'TEAM-2': dates('TEAM-2', finish='2026-09-07')}
        review(review_session(), plan, before, config, after)
        self.assertEqual(branches(render_schedule_cascade_review(plan, True)), [])
        self.assertEqual(len(date_changes(plan)), 2)

    def test_undated_root_is_not_promoted_to_a_driver(self):
        plan, config = dated_plan(2)
        connect(plan, 'TEAM-1', 'TEAM-2')
        plan.epics['TEAM-1'].target_start = plan.epics['TEAM-1'].target_end = ''
        before = {key: dates(key) for key in plan.epics}
        after = {key: dates(key, finish='2026-09-07') for key in plan.epics}
        review(review_session(), plan, before, config, after)
        rendered = render_schedule_cascade_review(plan, True)
        self.assertEqual(branches(rendered), [])
        self.assertEqual(len(date_changes(plan)), 2)

    def test_completed_upstream_finish_can_still_impact_unfinished_dated_work(self):
        plan, config = dated_plan(2)
        connect(plan, 'TEAM-1', 'TEAM-2')
        plan.epics['TEAM-1'].completed = True
        before = {key: dates(key) for key in plan.epics}
        after = {key: dates(key, finish='2026-09-07') for key in plan.epics}
        review(review_session(), plan, before, config, after)
        rendered = render_schedule_cascade_review(plan, True)
        self.assertEqual(len(branches(rendered)), 1)
        self.assertIn('TEAM-1', branch_summary(branches(rendered)[0]))
        self.assertIn('1 affected task', branch_summary(branches(rendered)[0]))
        self.assertIn('TEAM-2', branches(rendered)[0])

    def test_undated_or_completed_only_descendants_do_not_create_a_driver(self):
        for excluded in ('undated', 'completed'):
            with self.subTest(excluded=excluded):
                plan, config = dated_plan(2)
                connect(plan, 'TEAM-1', 'TEAM-2')
                if excluded == 'undated':
                    plan.epics['TEAM-2'].target_start = plan.epics['TEAM-2'].target_end = ''
                else:
                    plan.epics['TEAM-2'].completed = True
                before = {key: dates(key) for key in plan.epics}
                after = {key: dates(key, finish='2026-09-07') for key in plan.epics}
                review(review_session(), plan, before, config, after)
                self.assertEqual(branches(render_schedule_cascade_review(plan, True)), [])

    def test_context_nodes_remain_visible_but_only_dated_unfinished_tasks_count(self):
        plan, config = dated_plan(4)
        connect(plan, 'TEAM-1', 'TEAM-3')
        connect(plan, 'TEAM-3', 'TEAM-4')
        connect(plan, 'TEAM-4', 'TEAM-2')
        plan.epics['TEAM-3'].completed = True
        plan.epics['TEAM-4'].target_start = plan.epics['TEAM-4'].target_end = ''
        # One Jira target is sufficient; no requirement to have both dates.
        plan.epics['TEAM-1'].target_start = ''
        plan.epics['TEAM-2'].target_end = ''
        before = {key: dates(key) for key in plan.epics}
        after = {key: dates(key, finish='2026-09-07') for key in plan.epics}
        review(review_session(), plan, before, config, after)
        rendered = render_schedule_cascade_review(plan, True)
        self.assertEqual(len(branches(rendered)), 1)
        self.assertIn('1 affected task', branch_summary(branches(rendered)[0]))
        for key in plan.epics:
            self.assertIn(key, branches(rendered)[0])

    def test_completed_and_undated_dead_end_siblings_do_not_clutter_the_driver(self):
        plan, config = dated_plan(4)
        for successor in ('TEAM-2', 'TEAM-3', 'TEAM-4'):
            connect(plan, 'TEAM-1', successor)
        plan.epics['TEAM-3'].completed = True
        plan.epics['TEAM-4'].target_start = plan.epics['TEAM-4'].target_end = ''
        before = {key: dates(key) for key in plan.epics}
        after = {key: dates(key, finish='2026-09-07') for key in plan.epics}
        review(review_session(), plan, before, config, after)
        rendered = render_schedule_cascade_review(plan, True)
        self.assertEqual(len(branches(rendered)), 1)
        self.assertIn('1 affected task', branch_summary(branches(rendered)[0]))
        self.assertIn('TEAM-2', rendered)
        self.assertNotIn('TEAM-3', rendered)
        self.assertNotIn('TEAM-4', rendered)
        self.assertEqual(len(date_changes(plan)), 4)

    def test_resource_report_starts_at_its_topmost_driver_and_includes_cross_team_impact(self):
        plan, config = dated_plan(5)
        for key, group in {'TEAM-1': 'Beta', 'TEAM-2': 'Alpha', 'TEAM-3': 'Beta',
                           'TEAM-4': 'Alpha', 'TEAM-5': 'Alpha'}.items():
            plan.epics[key].resource_group = group
        connect(plan, 'TEAM-1', 'TEAM-2')
        connect(plan, 'TEAM-2', 'TEAM-3')
        before = {key: dates(key) for key in plan.epics}
        after = {key: dates(key, finish='2026-09-07') for key in plan.epics}
        after['TEAM-5'] = dates('TEAM-5', start='2026-09-02')
        review(review_session(), plan, before, config, after)
        rendered = render_schedule_cascade_review(plan, True, root_resource_group='Alpha')
        self.assert_driver_section_only(rendered)
        self.assertEqual(len(branches(rendered)), 1)
        self.assertIn('TEAM-2', branch_summary(branches(rendered)[0]))
        self.assertIn('1 affected task', branch_summary(branches(rendered)[0]))
        self.assertIn('TEAM-3', rendered)
        for key in ('TEAM-1', 'TEAM-4', 'TEAM-5'):
            self.assertNotIn(key, rendered)

    def test_validation_without_project_does_not_claim_a_completed_schedule_comparison(self):
        plan, _ = dated_plan(1)
        rendered = render_schedule_cascade_review(plan, project_update_run=False)
        self.assert_driver_section_only(rendered)
        text = text_content(rendered).lower()
        self.assertIn('evaluated after project scheduling', text)
        self.assertIn('create/update', text)
        self.assertEqual(branches(rendered), [])

    def test_report_escapes_driver_and_affected_names_without_mutating_plan(self):
        plan, config = dated_plan(2)
        connect(plan, 'TEAM-1', 'TEAM-2')
        for epic in plan.epics.values():
            epic.summary = '<script>alert("unsafe")</script> & dates'
        before = {key: dates(key) for key in plan.epics}
        after = {key: dates(key, '2026-09-02', '2026-09-07') for key in plan.epics}
        review(review_session(), plan, before, config, after)
        original = copy.deepcopy(plan)
        rendered = render_schedule_cascade_review(plan, True)
        self.assertEqual(plan, original)
        self.assertEqual(len(branches(rendered)), 1)
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('&amp; dates', rendered)

    def test_minimal_html_omits_raw_date_tables_and_csv_retains_all_details(self):
        plan, config = dated_plan(4)
        connect(plan, 'TEAM-1', 'TEAM-2')
        before = {key: dates(key) for key in plan.epics}
        before['TEAM-4'] = dates('TEAM-4', '2026-09-08', '2026-09-11')
        after = copy.deepcopy(before)
        after['TEAM-1'].finish = '2026-09-07'
        after['TEAM-2'].start = '2026-09-08'
        after['TEAM-3'].start = '2026-09-02'
        review(review_session(), plan, before, config, after)
        before_render = copy.deepcopy(plan)
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            report = output / 'review.html'
            audit = output / 'audit-detail.csv'
            write_manager_html(report, plan, config, output / 'sandbox.mpp', None)
            write_audit_csv(audit, plan.audit_items)
            rendered = report.read_text(encoding='utf-8')
            with audit.open(encoding='utf-8', newline='') as handle:
                csv_rows = list(csv.DictReader(handle))
        self.assertEqual(plan, before_render)
        document = assert_minimal_report(self, rendered)
        self.assertNotIn('<h2>Date Review</h2>', rendered)
        self.assertEqual(len(csv_rows), len(plan.audit_items))
        for item in plan.audit_items:
            self.assertTrue(any(row['category'] == item.category
                                and row['schedule_key'] == item.schedule_key
                                and row['field'] == item.field
                                and row['old_value'] == item.old_value
                                and row['new_value'] == item.new_value for row in csv_rows))
        driver_fragment = document.sections[0].html
        self.assertEqual(len(branches(driver_fragment)), 1)
        self.assertNotIn('TEAM-3', driver_fragment)
        self.assertNotIn('TEAM-4', driver_fragment)

    def test_suppressed_completed_release_can_still_drive_visible_unfinished_work(self):
        rows = [
            ['Issue key', 'Issue id', 'Issue Type', 'Summary', 'Epic Link', 'Fix versions',
             'Story Points', 'Status', 'Resolution', 'Resolved', 'Target start', 'Target end',
             'Outward issue link (Blocks)', 'Inward issue link (Blocks)'],
            ['TEAM-1', '1', 'Epic', 'Old completed driver', '', 'Old Driver Release', '',
             'Done', 'Done', '2026-01-01', '2025-12-01', '2026-01-05', 'TEAM-2', ''],
            ['TEAM-11', '11', 'Story', 'Completed child', 'TEAM-1', 'Old Driver Release', '5',
             'Done', 'Done', '2026-01-05', '', '', '', ''],
            ['TEAM-2', '2', 'Epic', 'Current affected work', '', 'Current Release', '',
             'In Progress', '', '', '2026-09-01', '2026-09-04', '', 'TEAM-1'],
            ['TEAM-21', '21', 'Story', 'Open child', 'TEAM-2', 'Current Release', '3',
             'In Progress', '', '', '', '', '', ''],
            ['TEAM-3', '3', 'Epic', 'Old independent completed work', '', 'Old Hidden Release', '',
             'Done', 'Done', '2026-01-01', '2025-12-01', '2026-01-05', '', ''],
            ['TEAM-31', '31', 'Story', 'Other completed child', 'TEAM-3', 'Old Hidden Release', '2',
             'Done', 'Done', '2026-01-05', '', '', '', ''],
        ]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            source = output / 'synthetic-suppressed-release.csv'
            with source.open('w', encoding='utf-8', newline='') as handle:
                csv.writer(handle).writerows(rows)
            config = load_config(Path(__file__).parent / 'fixtures' / 'fixversion-config.yaml',
                                 {'fixversion_completion_suppression': {'as_of_date': '2026-09-17'}})
            plan = build_run_plan(source, config)
            self.assertEqual(set(plan.stats['suppressed_completed_fixversion_summary_ids']),
                             {'fixVersion:Old Driver Release', 'fixVersion:Old Hidden Release'})
            self.assertEqual(plan.epics['TEAM-1'].successors, ['TEAM-2'])
            before = {key: dates(key, epic.target_start, epic.target_end)
                      for key, epic in plan.epics.items()}
            after = copy.deepcopy(before)
            after['TEAM-1'].finish = '2026-09-07'
            after['TEAM-2'].start = '2026-09-08'
            after['TEAM-3'].finish = '2026-09-07'
            review(review_session(), plan, before, config, after)
            original = copy.deepcopy(plan)
            report = output / 'review.html'
            write_manager_html(report, plan, config, output / 'sandbox.mpp', None)
            rendered = report.read_text(encoding='utf-8')
        self.assertEqual(plan, original)
        document = assert_minimal_report(self, rendered)
        driver_fragment = document.sections[0].html
        self.assertEqual(len(branches(driver_fragment)), 1)
        self.assertIn('TEAM-1', branch_summary(branches(driver_fragment)[0]))
        self.assertIn('1 affected task', branch_summary(branches(driver_fragment)[0]))
        self.assertIn('Old completed driver', driver_fragment)
        self.assertIn('Current affected work', driver_fragment)
        self.assertNotIn('TEAM-3', driver_fragment)
        outside_driver = rendered.replace(driver_fragment, '')
        for hidden in ('Old completed driver', 'Old independent completed work',
                       'Old Driver Release', 'Old Hidden Release'):
            self.assertNotIn(hidden, outside_driver)
        self.assertIn('Current Release', outside_driver)

    @unittest.skipUnless(len(FILES) == 9 and (YERP / 'ssn-812-config.yaml').exists(),
                         'Requires the nine private yerp CSV exports')
    def test_real_yerp_uses_actual_dependency_links_without_mutating_plan_or_csvs(self):
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}
        try:
            config = load_config(YERP / 'ssn-812-config.yaml')
            plan = build_run_plan(FILES, config)
            original_epics = {key: asdict(epic) for key, epic in plan.epics.items()}
            eligible = {key for key, epic in plan.epics.items()
                        if epic.drives_schedule and not epic.completed
                        and (epic.target_start or epic.target_end)}
            pairs = [(key, successor) for key in sorted(eligible)
                     for successor in plan.epics[key].successors if successor in eligible]
            self.assertTrue(pairs, 'Expected an actual yerp dependency with dated unfinished work')
            driver, affected = pairs[0]
            before = {key: dates(key, epic.target_start or '2035-03-01',
                                 epic.target_end or '2035-03-02') for key, epic in plan.epics.items()}
            after = copy.deepcopy(before)
            after[driver].finish = '2035-04-03'
            after[affected].start = '2035-04-04'
            reference = next(key for key, epic in plan.epics.items() if not epic.drives_schedule)
            after[reference] = dates(reference, '2035-05-01', '2035-05-02')
            review(review_session(), plan, before, config, after)
            rendered = render_schedule_cascade_review(plan, True)
            self.assert_driver_section_only(rendered)
            self.assertEqual(len(branches(rendered)), 1)
            self.assertIn(driver, branch_summary(branches(rendered)[0]))
            self.assertIn('1 affected task', branch_summary(branches(rendered)[0]))
            self.assertIn(affected, branches(rendered)[0])
            self.assertNotIn(reference, rendered)
            self.assertEqual({item.schedule_key for item in date_changes(plan)}, {driver, affected})
            self.assertEqual({key: asdict(epic) for key, epic in plan.epics.items()}, original_epics)
        finally:
            self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}, hashes)


if __name__ == '__main__':
    unittest.main()
