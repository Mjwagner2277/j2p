"""Split Jira exports must produce the same plan as a complete export."""
import csv
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path

from j2p.cli import main, build_parser
from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.models import J2PError

FIXTURES = Path(__file__).parent / 'fixtures'


class MultiCsvTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(FIXTURES / 'mixed-config.yaml')
        self.source = FIXTURES / 'project-wide-jira-initial.csv'
        with self.source.open(newline='') as source:
            self.rows = list(csv.reader(source))

    def write_batch(self, path, rows, reverse=False, encoding='utf-8'):
        with path.open('w', newline='', encoding=encoding) as target:
            writer = csv.writer(target)
            for row in [self.rows[0], *rows]:
                writer.writerow(list(reversed(row)) if reverse else row)

    def test_cross_file_rollups_dependencies_encoding_and_overlap(self):
        baseline = build_run_plan(self.source, self.config)
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp)/'first.csv', Path(tmp)/'second.csv'
            self.write_batch(a, self.rows[1:5])
            self.write_batch(b, self.rows[4:], reverse=True, encoding='utf-16')
            plan = build_run_plan([a, b], self.config)
            self.assertEqual(set(plan.epics), set(baseline.epics))
            for key, epic in plan.epics.items():
                original = baseline.epics[key]
                for attr in ('total_story_points', 'completed_story_points', 'logged_hours',
                             'percent_complete', 'predecessors', 'successors', 'dependency_review'):
                    self.assertEqual(getattr(epic, attr), getattr(original, attr), (key, attr))
            self.assertEqual(plan.stats['duplicate_csv_issues_skipped'], 1)
            self.assertEqual(plan.stats['csv_files_read'], 2)
            self.assertIn(str(a), plan.jira_csv)
            self.assertIn(str(b), plan.jira_csv)
            self.assertEqual(plan.epics['TEAM-103'].source_file, str(b))
            self.assertEqual(plan.epics['TEAM-103'].source_row, 4)
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                result = main(['validate', '--jira-csv', str(a), '--jira-csv', str(b),
                               '--config', str(FIXTURES/'mixed-config.yaml'), '--project-name', 'Multi',
                               '--output-dir', str(Path(tmp)/'reports')])
            self.assertEqual(result, 0, err.getvalue())
            report = next((Path(tmp)/'reports').rglob('audit-detail.csv')).read_text()
            self.assertIn('source_file', report)
            self.assertIn('DuplicateCsvIssueSkipped', report)

    def test_conflicting_overlap_fails_with_both_locations(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp)/'first.csv', Path(tmp)/'second.csv'
            self.write_batch(a, self.rows[1:])
            changed = list(self.rows[1])
            changed[3] = 'Changed summary'
            self.write_batch(b, [changed])
            with self.assertRaises(J2PError) as raised:
                build_run_plan([a, b], self.config)
            self.assertIn(f'{a} row 2', str(raised.exception))
            self.assertIn(f'{b} row 2', str(raised.exception))
            self.assertIn('Conflicting duplicate', str(raised.exception))

    def test_required_headers_checked_for_each_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp)/'bad.csv'
            bad.write_text('Issue key,Summary\nTEAM-999,Invalid batch\n')
            with self.assertRaisesRegex(J2PError, 'bad.csv.*missing required'):
                build_run_plan([self.source, bad], self.config)
            with self.assertRaisesRegex(J2PError, 'Could not read Jira CSV'):
                build_run_plan([self.source, Path(tmp)/'absent.csv'], self.config)

    def test_all_commands_accept_lists_and_repeated_flags(self):
        for command in ('validate', 'create', 'update'):
            args = [command, '--jira-csv', 'one.csv', 'two.csv', '--jira-csv', 'three.csv',
                    '--project-name', 'Multi']
            if command == 'update':
                args.extend(['--sprint', 'Sprint 1', '--main-project', 'main.mpp'])
            parsed = build_parser().parse_args(args)
            self.assertEqual(parsed.jira_csv, [Path('one.csv'), Path('two.csv'), Path('three.csv')])

    def test_duplicate_child_is_not_counted_twice(self):
        baseline = build_run_plan(self.source, self.config)
        repeated = build_run_plan([self.source, self.source], self.config)
        self.assertEqual(repeated.stats['duplicate_csv_issues_skipped'], len(self.rows)-1)
        for key in baseline.epics:
            self.assertEqual(repeated.epics[key].total_story_points, baseline.epics[key].total_story_points)
