"""Minimal manager/resource reports remain structurally valid and CSV-complete."""

import copy
import csv
import hashlib
import tempfile
import unittest
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.models import AuditItem
from j2p.reports import write_reports
from j2p.rollups import build_summaries
from test_project_schedule_colors import dates, review, review_session, schedule_plan
from yerp_project_support import FILES, YERP


SECTION_TITLES = ['Cascading Schedule Drivers', 'Rollup and Completion', 'Items for Review']
VOID_ELEMENTS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
                 'meta', 'param', 'source', 'track', 'wbr'}


@dataclass
class HtmlNode:
    tag: str
    attrs: dict
    source: str
    start: int = 0
    end: int = 0
    parent: object = None
    children: list = field(default_factory=list)

    @property
    def elements(self):
        return [child for child in self.children if isinstance(child, HtmlNode)]

    @property
    def text(self):
        return ''.join(child.text if isinstance(child, HtmlNode) else child
                       for child in self.children)

    @property
    def html(self):
        return self.source[self.start:self.end]

    def descendants(self, tag=None):
        found = []
        for child in self.elements:
            if tag is None or child.tag == tag:
                found.append(child)
            found.extend(child.descendants(tag))
        return found


class ReportDocument(HTMLParser):
    """Keep source fragments and fail on unbalanced or structurally invalid HTML."""

    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.source = source
        self.line_offsets = [0]
        self.line_offsets.extend(index + 1 for index, value in enumerate(source) if value == '\n')
        self.root = HtmlNode('#document', {}, source)
        self.stack = [self.root]
        self.feed(source)
        self.close()
        if len(self.stack) != 1:
            raise AssertionError(f'Unclosed HTML elements: {[node.tag for node in self.stack[1:]]}')

    def source_offset(self):
        line, column = self.getpos()
        return self.line_offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if len(dict(attrs)) != len(attrs):
            raise AssertionError(f'Duplicate attributes on {tag}')
        parent = self.stack[-1]
        allowed_parents = {'thead': {'table'}, 'tbody': {'table'}, 'tfoot': {'table'},
                           'tr': {'table', 'thead', 'tbody', 'tfoot'},
                           'td': {'tr'}, 'th': {'tr'}, 'summary': {'details'}}
        if tag in allowed_parents and parent.tag not in allowed_parents[tag]:
            raise AssertionError(f'Invalid {tag} child of {parent.tag}')
        if parent.tag == 'p' and tag in {'div', 'section', 'p', 'table', 'details', 'h2', 'h3'}:
            raise AssertionError(f'Invalid block {tag} inside paragraph')
        node = HtmlNode(tag, dict(attrs), self.source, self.source_offset(), parent=parent)
        parent.children.append(node)
        if tag in VOID_ELEMENTS:
            node.end = self.source_offset() + len(self.get_starttag_text())
        else:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_ELEMENTS:
            self.stack.pop().end = self.source_offset() + len(self.get_starttag_text())

    def handle_endtag(self, tag):
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise AssertionError(f'Misnested closing {tag}; stack={[node.tag for node in self.stack]}')
        self.stack.pop().end = self.source.index('>', self.source_offset()) + 1

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def descendants(self, tag=None):
        return self.root.descendants(tag)

    @property
    def sections(self):
        mains = self.descendants('main')
        if len(mains) != 1:
            raise AssertionError(f'Expected one main; got {len(mains)}')
        return mains[0].elements


def section_title(section):
    summary = section.elements[0]
    return summary.elements[0].text.strip() if summary.elements else summary.text.strip()


def assert_minimal_report(test, rendered):
    document = ReportDocument(rendered)
    test.assertEqual([node.tag for node in document.sections], ['details'] * 3)
    test.assertEqual([section_title(node) for node in document.sections], SECTION_TITLES)
    for section in document.sections:
        test.assertEqual(section.attrs.get('class'), 'detail-block')
    for details in document.descendants('details'):
        test.assertNotIn('open', details.attrs)
        test.assertTrue(details.elements, 'Every details needs its summary')
        test.assertEqual(details.elements[0].tag, 'summary')
        test.assertEqual(sum(node.tag == 'summary' for node in details.elements), 1)
    for table in document.descendants('table'):
        ancestor = table.parent
        while ancestor is not None and ancestor not in document.sections:
            ancestor = ancestor.parent
        test.assertIsNotNone(ancestor, 'All report tables belong inside a collapsed section')
    headings = {node.text.strip() for tag in ('h2', 'h3') for node in document.descendants(tag)}
    for removed in ('Decision Briefing', 'Story Point Ratio', 'Prefix Rollup Map',
                    'Report Context', 'Color Key', 'Color Examples', 'Full Review Audit',
                    'Detailed Review Sections', 'Planned Epics', 'Column Map', 'Date Review',
                    'Review Type Summary', 'Planning Horizon Review'):
        test.assertNotIn(removed, headings)
    test.assertEqual(document.descendants('script'), [])
    return document


def minimal_plan():
    plan, config = schedule_plan(4)
    for index, epic in enumerate(plan.epics.values(), start=1):
        epic.in_planning = False
        epic.completed = False
        epic.summary = f'Work item {index}'
        epic.resource_group = 'Alpha' if index % 2 else 'Beta'
        epic.total_story_points = 3
        epic.completed_story_points = 1
        epic.rollup_mode = 'initiative'
        epic.rollup_key = 'INIT-1'
        epic.rollup_name = 'Current initiative'
    plan.epics['TEAM-1'].successors = ['TEAM-2']
    plan.epics['TEAM-2'].predecessors = ['TEAM-1']
    plan.epics['TEAM-3'].rollup_mode = 'fixVersion'
    plan.epics['TEAM-3'].rollup_key = 'Release 2027'
    plan.epics['TEAM-3'].rollup_name = 'Release 2027'
    plan.epics['TEAM-3'].target_end = '2027-02-05'
    plan.epics['TEAM-3'].completed_story_points = 3
    plan.epics['TEAM-3'].completed = True
    plan.epics['TEAM-4'].rollup_key = 'NO-POINTS'
    plan.epics['TEAM-4'].rollup_name = 'In planning without points'
    plan.epics['TEAM-4'].total_story_points = 0
    plan.epics['TEAM-4'].completed_story_points = 0
    plan.summaries = build_summaries(plan.epics, config,
                                   target_ends={'initiative:INIT-1': '2026-09-04',
                                                'fixVersion:Release 2027': '2027-02-05'})
    before = {key: dates(key, epic.target_start, epic.target_end) for key, epic in plan.epics.items()}
    after = copy.deepcopy(before)
    after['TEAM-1'].finish = '2026-09-07'
    after['TEAM-2'].start = '2026-09-08'
    review(review_session(), plan, before, config, after)
    plan.audit_items.append(AuditItem(
        severity='Review', category='DependencyReview', jira_key='TEAM-2', schedule_key='TEAM-2',
        summary=plan.epics['TEAM-2'].summary, field='Dependency Review', color='dependency_review',
        message='Review the blocked task', reviewer_action='Confirm predecessor timing.',
    ))
    return plan, config


def read_csv(path):
    with path.open(encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


class MinimalReportTests(unittest.TestCase):
    def test_manager_and_resource_reports_have_exactly_three_closed_sections(self):
        plan, config = minimal_plan()
        original = copy.deepcopy(plan)
        with tempfile.TemporaryDirectory() as temp:
            paths = write_reports(plan, Path(temp), config, Path(temp) / 'sandbox.mpp')
            reports = [paths['manager_report'], *sorted(paths['resource_group_reports'].glob('*.html'))]
            self.assertEqual(len(reports), 3)
            for report in reports:
                with self.subTest(report=report.name):
                    document = assert_minimal_report(self, report.read_text(encoding='utf-8'))
                    self.assertTrue(document.sections[1].descendants('table'))
            overall = ReportDocument(paths['manager_report'].read_text(encoding='utf-8'))
            self.assertEqual(len(overall.sections[0].descendants('details')), 1)
        self.assertEqual(plan, original)

    def test_each_section_links_existing_complete_csvs_using_relative_paths(self):
        plan, config = minimal_plan()
        with tempfile.TemporaryDirectory() as temp:
            paths = write_reports(plan, Path(temp), config, Path(temp) / 'sandbox.mpp')
            reports = [paths['manager_report'], *sorted(paths['resource_group_reports'].glob('*.html'))]
            for report in reports:
                document = assert_minimal_report(self, report.read_text(encoding='utf-8'))
                for index, expected_files in ((1, {'summary-rollups.csv'}),
                                               (2, {'audit-detail.csv', 'dependency-review.csv'})):
                    links = {}
                    for anchor in document.sections[index].descendants('a'):
                        href = anchor.attrs.get('href', '')
                        parsed = urlsplit(href)
                        if parsed.path.endswith('.csv'):
                            self.assertFalse(parsed.scheme or parsed.netloc)
                            self.assertFalse(Path(parsed.path).is_absolute())
                            target = (report.parent / unquote(parsed.path)).resolve()
                            self.assertTrue(target.is_file(), f'Broken CSV link in {report.name}: {href}')
                            links[target.name] = target
                            expected_labels = {
                                'summary-rollups.csv': 'Full rollup CSV',
                                'planned-epics.csv': 'Planned epic CSV',
                                'audit-detail.csv': 'Full audit CSV',
                                'dependency-review.csv': 'Dependency review CSV',
                            }
                            self.assertEqual(anchor.text.strip(), expected_labels[target.name])
                    self.assertTrue(expected_files <= set(links), f'Missing CSV links in {report.name}')
            audits = read_csv(paths['audit_detail'])
            self.assertEqual(len(audits), len(plan.audit_items))
            self.assertEqual({(row['category'], row['schedule_key'], row['field']) for row in audits},
                             {(a.category, a.schedule_key, a.field) for a in plan.audit_items})
            self.assertEqual(len(read_csv(paths['planned_epics'])), len(plan.epics))
            self.assertEqual(len(read_csv(paths['summary_rollups'])), len(plan.summaries))
            self.assertEqual(len(read_csv(paths['dependency_review'])), 1)

    def test_compact_rollup_keeps_completion_dates_and_omits_planning(self):
        plan, config = minimal_plan()
        with tempfile.TemporaryDirectory() as temp:
            paths = write_reports(plan, Path(temp), config)
            document = assert_minimal_report(self, paths['manager_report'].read_text(encoding='utf-8'))
            csv_rollups = {row['rollup_key']: row for row in read_csv(paths['summary_rollups'])}
        tables = document.sections[1].descendants('table')
        self.assertEqual(len(tables), 1)
        table = tables[0]
        self.assertEqual({node.text.strip() for node in table.descendants('th')},
                         {'Rollup', 'Rollup Key', 'Mode', 'Status', 'Target End', 'Due Status',
                          '% Complete', 'Completion Points (Done / Total)'})
        rows = [[cell.text.strip() for cell in row.elements if cell.tag == 'td']
                for row in table.descendants('tr') if any(cell.tag == 'td' for cell in row.elements)]
        self.assertEqual(len(rows), 2)
        self.assertTrue({'INIT-1', 'Current initiative', 'initiative', 'In progress',
                         '2026-09-04', 'Past due', '33%', '2 / 6'} <= set(rows[0]))
        self.assertTrue({'Release 2027', 'fixVersion', 'Complete', '2027-02-05',
                         '100%', '3 / 3'} <= set(rows[1]))
        self.assertNotIn('NO-POINTS', table.text)
        for row in rows:
            key = next(key for key in csv_rollups if key in row)
            source = csv_rollups[key]
            self.assertIn(source['percent_complete'] + '%', row)
            self.assertIn(source['target_end'], row)

    def test_completion_summary_excludes_reference_points_but_rollups_credit_them(self):
        plan, config = minimal_plan()
        reference_key = 'TEAM-3::FV::OTHER'
        plan.epics[reference_key] = replace(
            plan.epics['TEAM-3'], key=reference_key, drives_schedule=False, row_role='Reference',
            primary_schedule_key='TEAM-3', rollup_key='Other Release', rollup_name='Other Release',
            predecessors=[], successors=[],
        )
        plan.summaries = build_summaries(plan.epics, config)
        with tempfile.TemporaryDirectory() as temp:
            paths = write_reports(plan, Path(temp), config)
            document = assert_minimal_report(self, paths['manager_report'].read_text(encoding='utf-8'))
        rollups = document.sections[1]
        metrics = next(node for node in rollups.descendants('div')
                       if node.attrs.get('class') == 'completion-summary')
        values = {''.join(child for child in node.children if isinstance(child, str)).strip():
                  node.descendants('strong')[0].text.strip() for node in metrics.elements}
        self.assertEqual(values, {
            'Completed / Total Points': '5 / 9',
            'Point Completion': '55.6%',
            'Completed / Total Epics': '1 / 4',
        })
        table = rollups.descendants('table')[0]
        other = next(row for row in table.descendants('tr') if 'Other Release' in row.text)
        cells = {cell.text.strip() for cell in other.elements}
        self.assertTrue({'Complete', '100%', '3 / 3'} <= cells)
        self.assertEqual(plan.summaries['fixVersion:Other Release'].driving_epic_count, 0)

    def test_completion_summary_with_no_points_is_not_estimated(self):
        plan, config = minimal_plan()
        for epic in plan.epics.values():
            epic.total_story_points = epic.completed_story_points = 0
            epic.completed = False
        plan.summaries = build_summaries(plan.epics, config)
        with tempfile.TemporaryDirectory() as temp:
            paths = write_reports(plan, Path(temp), config)
            document = assert_minimal_report(self, paths['manager_report'].read_text(encoding='utf-8'))
        metrics = next(node for node in document.sections[1].descendants('div')
                       if node.attrs.get('class') == 'completion-summary')
        self.assertEqual([node.text.strip() for node in metrics.descendants('strong')],
                         ['0 / 0', 'Not estimated', '0 / 4'])

    def test_jira_text_cannot_break_the_three_sections_or_create_markup(self):
        plan, config = minimal_plan()
        hostile = '</details><script>bad()</script><table><tr><td> & "quoted"'
        plan.epics['TEAM-1'].summary = hostile
        plan.epics['TEAM-1'].resource_group = hostile
        plan.summaries['initiative:INIT-1'].name = hostile
        for audit in plan.audit_items:
            if audit.schedule_key == 'TEAM-1':
                audit.summary = hostile
            audit.message = hostile
        with tempfile.TemporaryDirectory() as temp:
            paths = write_reports(plan, Path(temp), config, Path(temp) / 'sandbox.mpp')
            for path in [paths['manager_report'], *paths['resource_group_reports'].glob('*.html')]:
                rendered = path.read_text(encoding='utf-8')
                document = assert_minimal_report(self, rendered)
                self.assertNotIn('<script>', rendered)
                self.assertEqual(len(document.sections), 3)
            overall = paths['manager_report'].read_text(encoding='utf-8')
            self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', overall)
            self.assertIn('&amp;', overall)

    @unittest.skipUnless(len(FILES) == 9 and (YERP / 'ssn-812-config.yaml').exists(),
                         'Requires the nine private yerp CSV exports')
    def test_real_yerp_all_reports_are_minimal_while_csv_evidence_is_complete(self):
        hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}
        try:
            config = load_config(YERP / 'ssn-812-config.yaml')
            plan = build_run_plan(FILES, config)
            original = copy.deepcopy(plan)
            with tempfile.TemporaryDirectory() as temp:
                paths = write_reports(plan, Path(temp), config)
                reports = [paths['manager_report'], *paths['resource_group_reports'].glob('*.html')]
                self.assertGreater(len(reports), 1)
                for report in reports:
                    with self.subTest(report=report.name):
                        assert_minimal_report(self, report.read_text(encoding='utf-8'))
                self.assertEqual(len(read_csv(paths['audit_detail'])), len(plan.audit_items))
                self.assertEqual(len(read_csv(paths['planned_epics'])), len(plan.epics))
                self.assertEqual(len(read_csv(paths['summary_rollups'])), len(plan.summaries))
            self.assertEqual(plan, original)
        finally:
            self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in FILES}, hashes)


if __name__ == '__main__':
    unittest.main()
