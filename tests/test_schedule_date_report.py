"""Keep Project date colors and the HTML schedule review mutually explainable."""

import copy
import hashlib
import html as html_lib
import io
import re
import unittest
from contextlib import redirect_stdout
from dataclasses import asdict

import test_project_formatting_scaling as formatting
from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.reports import render_schedule_cascade_review, schedule_cascade_change_items
from test_project_schedule_colors import (
    date_changes, dates, review, review_session, schedule_plan,
)
from yerp_project_support import FILES, YERP


def metric(rendered, label):
    found = re.search(
        r'<span>' + re.escape(html_lib.escape(label)) + r'</span>\s*<strong>(\d+)</strong>',
        rendered,
    )
    if found is None:
        raise AssertionError(f'Missing metric {label!r} in schedule review')
    return int(found.group(1))


def table_rows(rendered, title):
    """Read actual visible table cells, independent of column positions."""
    section = re.search(
        r'<section><h2>' + re.escape(html_lib.escape(title)) + r'</h2>(.*?)</section>',
        rendered, re.S,
    )
    if section is None:
        raise AssertionError(f'Missing detail table {title!r}')
    return [
        [html_lib.unescape(re.sub(r'<[^>]*>', '', cell))
         for cell in re.findall(r'<td>(.*?)</td>', row, re.S)]
        for row in re.findall(r'<tr>(.*?)</tr>', section.group(1), re.S)
        if '<td>' in row
    ]


def assert_collapsed(test, rendered, title):
    test.assertRegex(
        rendered,
        r'<details class="detail-block"><summary><span>'
        + re.escape(html_lib.escape(title)) + r'</span>',
    )


def color_and_render(plan, config, before, after):
    tasks = []
    for index, (key, values) in enumerate(after.items(), start=1):
        task = formatting.task(key, index)
        task.Start, task.Finish = values.start, values.finish
        tasks.append(task)
    session = formatting.formatting_session(tasks)
    with redirect_stdout(io.StringIO()):
        session.apply_review_formatting(plan, config, before=before)
    colors = {
        (call.args[0].ID, call.args[1]): call.args[2]
        for call in session.color_project_cell_error.call_args_list
    }
    return render_schedule_cascade_review(plan, project_update_run=True), colors


class ScheduleDateReportTests(unittest.TestCase):
    def test_yellow_existing_jira_differences_are_explained_without_inventing_changes(self):
        plan, config = schedule_plan(1)
        before = {'TEAM-1': dates('TEAM-1', '2026-09-08', '2026-09-11')}
        rendered, colors = color_and_render(plan, config, before, copy.deepcopy(before))
        self.assertEqual(metric(rendered, 'Start Changes'), 0)
        self.assertEqual(metric(rendered, 'Finish Changes'), 0)
        self.assertEqual(metric(rendered, 'Jira Target Differences'), 2)
        self.assertEqual(date_changes(plan), [])
        self.assertEqual(schedule_cascade_change_items(plan), {})
        self.assertEqual(colors[(1, 'Start')], config['colors']['review_needed'])
        self.assertEqual(colors[(1, 'Finish')], config['colors']['review_needed'])
        rows = table_rows(rendered, 'Jira Target Differences')
        self.assertEqual(len(rows), 2)
        self.assertTrue(any({'Start', '2026-09-01', '2026-09-08'} <= set(row) for row in rows))
        self.assertTrue(any({'Finish', '2026-09-04', '2026-09-11'} <= set(row) for row in rows))
        self.assertTrue(all('No new change recorded' in row for row in rows))
        assert_collapsed(self, rendered, 'Jira Target Differences')
        self.assertIn('baseline', rendered.lower())
        self.assertNotIn('<div class="cascade-node ', rendered)

    def test_start_only_shift_is_green_and_visible_without_a_finish_cascade(self):
        plan, config = schedule_plan(1)
        before = {'TEAM-1': dates('TEAM-1')}
        after = {'TEAM-1': dates('TEAM-1', start='2026-09-02')}
        rendered, colors = color_and_render(plan, config, before, after)
        self.assertEqual(metric(rendered, 'Start Changes'), 1)
        self.assertEqual(metric(rendered, 'Finish Changes'), 0)
        self.assertEqual(metric(rendered, 'Jira Target Differences'), 1)
        self.assertEqual(colors[(1, 'Start')], config['colors']['changed_cell'])
        self.assertEqual(schedule_cascade_change_items(plan), {})
        native_rows = table_rows(rendered, 'Project Date Changes')
        self.assertEqual(len(native_rows), 1)
        self.assertTrue({'TEAM-1', 'Start', '2026-09-01', '2026-09-02'} <= set(native_rows[0]))
        self.assertEqual(len(table_rows(rendered, 'Jira Target Differences')), 1)
        self.assertIn('Changed this run', table_rows(rendered, 'Jira Target Differences')[0])
        assert_collapsed(self, rendered, 'Project Date Changes')
        self.assertNotIn('<div class="cascade-node ', rendered)
        self.assertNotIn('No Project date changes', rendered)

    def test_mixed_changes_and_existing_differences_use_field_specific_status(self):
        plan, config = schedule_plan(3)
        plan.epics['TEAM-1'].successors = ['TEAM-2']
        plan.epics['TEAM-2'].predecessors = ['TEAM-1']
        before = {key: dates(key) for key in plan.epics}
        before['TEAM-1'] = dates('TEAM-1', start='2026-09-02')
        before['TEAM-3'] = dates('TEAM-3', finish='2026-09-11')
        after = copy.deepcopy(before)
        after['TEAM-1'] = dates('TEAM-1', '2026-09-02', '2026-09-07')
        after['TEAM-2'] = dates('TEAM-2', '2026-09-02', '2026-09-07')
        review(review_session(), plan, before, config, after)
        rendered = render_schedule_cascade_review(plan, project_update_run=True)
        self.assertEqual(metric(rendered, 'Start Changes'), 1)
        self.assertEqual(metric(rendered, 'Finish Changes'), 2)
        self.assertEqual(metric(rendered, 'Jira Target Differences'), 5)
        self.assertEqual(len(table_rows(rendered, 'Project Date Changes')), 3)
        rows = table_rows(rendered, 'Jira Target Differences')
        self.assertEqual(sum('Changed this run' in row for row in rows), 3)
        self.assertEqual(sum('No new change recorded' in row for row in rows), 2)
        for row in rows:
            if 'TEAM-1' in row and 'Start' in row:
                self.assertIn('No new change recorded', row)
            if 'TEAM-1' in row and 'Finish' in row:
                self.assertIn('Changed this run', row)
        self.assertEqual(rendered.count('<details class="cascade-branch">'), 1)
        self.assertIn('TEAM-1', rendered)
        self.assertIn('TEAM-2', rendered)

    def test_undated_new_row_shift_is_a_project_change_without_a_jira_target_difference(self):
        plan, config = schedule_plan(1)
        plan.epics['TEAM-1'].target_start = plan.epics['TEAM-1'].target_end = ''
        session = review_session()
        session._new_task_schedule_dates = {'TEAM-1': dates('TEAM-1', '2026-09-19', '2026-09-19')}
        review(session, plan, {}, config, {'TEAM-1': dates('TEAM-1', '2026-09-22', '2026-09-22')})
        rendered = render_schedule_cascade_review(plan, project_update_run=True)
        self.assertEqual(metric(rendered, 'Start Changes'), 1)
        self.assertEqual(metric(rendered, 'Finish Changes'), 1)
        self.assertEqual(metric(rendered, 'Jira Target Differences'), 0)
        rows = table_rows(rendered, 'Project Date Changes')
        self.assertEqual(len(rows), 2)
        self.assertTrue(all({'2026-09-19', '2026-09-22'} <= set(row) for row in rows))
        self.assertFalse(any(item.category == 'ScheduledDateMismatch' for item in plan.audit_items))
        self.assertIn('Previous / Initial Date', rendered)
        self.assertIn('initial dates for new rows', rendered)
        plan.stats['project_run_mode'] = 'create'
        creating = render_schedule_cascade_review(plan, project_update_run=True)
        self.assertIn('Initial Scheduling Date', creating)
        self.assertNotIn('input Project baseline', creating)

    def test_validation_without_project_does_not_claim_a_completed_date_comparison(self):
        plan, _ = schedule_plan(1)
        rendered = render_schedule_cascade_review(plan, project_update_run=False)
        text = html_lib.unescape(re.sub(r'<[^>]*>', ' ', rendered)).lower()
        self.assertRegex(text, r'(not|no .*?)\s.*evaluated')
        self.assertIn('create/update', text)
        self.assertNotIn('were detected in this update run', text)
        self.assertNotIn('no new change recorded', text)

    def test_resource_date_details_include_owned_leaves_and_owned_branch_downstream(self):
        plan, config = schedule_plan(7)
        # Alpha owns a branch (1->2), an external branch leaf (4), and independent rows (5, 7).
        for key, group in {
            'TEAM-1': 'Alpha', 'TEAM-2': 'Beta', 'TEAM-3': 'Beta',
            'TEAM-4': 'Alpha', 'TEAM-5': 'Alpha', 'TEAM-6': 'Beta', 'TEAM-7': 'Alpha',
        }.items():
            plan.epics[key].resource_group = group
        plan.epics['TEAM-1'].successors = ['TEAM-2']
        plan.epics['TEAM-2'].predecessors = ['TEAM-1']
        plan.epics['TEAM-3'].successors = ['TEAM-4']
        plan.epics['TEAM-4'].predecessors = ['TEAM-3']
        before = {key: dates(key) for key in plan.epics}
        after = {key: dates(key, finish='2026-09-07') for key in plan.epics}
        after['TEAM-7'] = dates('TEAM-7', start='2026-09-02')
        review(review_session(), plan, before, config, after)
        rendered = render_schedule_cascade_review(plan, True, root_resource_group='Alpha')
        self.assertEqual(metric(rendered, 'Start Changes'), 1)
        self.assertEqual(metric(rendered, 'Finish Changes'), 4)
        self.assertEqual(metric(rendered, 'Jira Target Differences'), 5)
        native_rows = table_rows(rendered, 'Project Date Changes')
        jira_rows = table_rows(rendered, 'Jira Target Differences')
        for rows in (native_rows, jira_rows):
            self.assertEqual(len(rows), 5)
            self.assertEqual({key for key in plan.epics if any(key in row for row in rows)},
                             {'TEAM-1', 'TEAM-2', 'TEAM-4', 'TEAM-5', 'TEAM-7'})
        self.assertEqual(rendered.count('<details class="cascade-branch">'), 1)
        graph = rendered.split('<div class="cascade-flow">', 1)[1].split('</section>', 1)[0]
        self.assertIn('TEAM-1', graph)
        self.assertIn('TEAM-2', graph)
        for key in ('TEAM-3', 'TEAM-4', 'TEAM-5', 'TEAM-6', 'TEAM-7'):
            self.assertNotIn(key, graph)

    def test_resource_start_only_and_existing_target_difference_are_not_filtered_out(self):
        plan, config = schedule_plan(2)
        plan.epics['TEAM-1'].resource_group = 'Alpha'
        plan.epics['TEAM-2'].resource_group = 'Beta'
        before = {'TEAM-1': dates('TEAM-1', finish='2026-09-11'), 'TEAM-2': dates('TEAM-2')}
        after = {'TEAM-1': dates('TEAM-1', '2026-09-02', '2026-09-11'),
                 'TEAM-2': dates('TEAM-2', '2026-09-08', '2026-09-11')}
        review(review_session(), plan, before, config, after)
        rendered = render_schedule_cascade_review(plan, True, root_resource_group='Alpha')
        self.assertEqual(metric(rendered, 'Start Changes'), 1)
        self.assertEqual(metric(rendered, 'Finish Changes'), 0)
        self.assertEqual(metric(rendered, 'Jira Target Differences'), 2)
        self.assertEqual(len(table_rows(rendered, 'Project Date Changes')), 1)
        self.assertEqual(len(table_rows(rendered, 'Jira Target Differences')), 2)
        self.assertNotIn('TEAM-2', rendered)

    def test_new_date_details_escape_text_and_never_mutate_plan(self):
        plan, config = schedule_plan(1)
        plan.epics['TEAM-1'].summary = '<script>alert("unsafe")</script> & dates'
        review(review_session(), plan, {'TEAM-1': dates('TEAM-1')}, config,
               {'TEAM-1': dates('TEAM-1', '2026-09-02', '2026-09-07')})
        original = copy.deepcopy(plan)
        rendered = render_schedule_cascade_review(plan, project_update_run=True)
        self.assertEqual(plan, original)
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        for title in ('Project Date Changes', 'Jira Target Differences'):
            self.assertTrue(all(plan.epics['TEAM-1'].summary in row
                                for row in table_rows(rendered, title)))
            assert_collapsed(self, rendered, title)

    @unittest.skipUnless(len(FILES) == 9 and (YERP / 'ssn-812-config.yaml').exists(),
                         'Requires the nine private yerp CSV exports')
    def test_real_yerp_preserves_reference_exclusion_and_source_csvs_in_date_summary(self):
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}
        try:
            config = load_config(YERP / 'ssn-812-config.yaml')
            plan = build_run_plan(FILES, config)
            self.assertEqual(len(plan.epics), 2016)
            original_epics = {key: asdict(epic) for key, epic in plan.epics.items()}
            before = {key: dates(key, epic.target_start or '2035-03-01',
                                 epic.target_end or '2035-03-02') for key, epic in plan.epics.items()}
            dated = [key for key, epic in plan.epics.items()
                     if epic.drives_schedule and epic.target_start and epic.target_end]
            changed_key, existing_difference = dated[:2]
            before[existing_difference] = dates(existing_difference, '2035-02-01', '2035-02-02')
            after = copy.deepcopy(before)
            after[changed_key].start = '2035-01-01'
            reference = next(key for key, epic in plan.epics.items() if not epic.drives_schedule)
            after[reference] = dates(reference, '2035-04-01', '2035-04-02')
            review(review_session(), plan, before, config, after)
            rendered = render_schedule_cascade_review(plan, project_update_run=True)
            self.assertEqual(metric(rendered, 'Start Changes'), 1)
            self.assertEqual(metric(rendered, 'Finish Changes'), 0)
            self.assertEqual(metric(rendered, 'Jira Target Differences'), 3)
            self.assertEqual(len(table_rows(rendered, 'Project Date Changes')), 1)
            self.assertEqual(len(table_rows(rendered, 'Jira Target Differences')), 3)
            self.assertEqual({item.schedule_key for item in date_changes(plan)}, {changed_key})
            self.assertEqual({key: asdict(epic) for key, epic in plan.epics.items()}, original_epics)
        finally:
            self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}, hashes)


if __name__ == '__main__':
    unittest.main()
