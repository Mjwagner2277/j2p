"""Failure injection and recovery tests for run publication and operator commands."""
import base64
import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from j2p.cli import main
from j2p.models import J2PError
from j2p.operator_tools import expand_profile_args
from j2p.run_lifecycle import exclusive_lock, journal_path, recover_pending_state
from j2p.state import write_json, write_bytes_atomic

FIXTURES = Path(__file__).parent / 'fixtures'


class RunLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = ['validate', '--jira-csv', str(FIXTURES/'project-wide-jira-initial.csv'),
                     '--config', str(FIXTURES/'mixed-config.yaml'), '--project-name', 'Review',
                     '--output-dir', str(self.root)]

    def invoke(self, args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = main(args)
        return result, out.getvalue(), err.getvalue()

    def test_report_failure_preserves_state_and_allows_sprint_retry(self):
        state = self.root/'Review/j2p-state.json'
        write_json(state, {'version': 1, 'epics': {}})
        previous = state.read_bytes()
        with patch('j2p.cli.write_reports', side_effect=OSError('synthetic disk error')):
            result, _, error = self.invoke(self.args+['--sprint', 'S1', '--run-id', 'failed', '--write-state'])
        self.assertEqual(result, 2)
        self.assertIn('synthetic disk error', error)
        self.assertEqual(state.read_bytes(), previous)
        sprint = self.root/'Review/sprints/S1'
        self.assertFalse((sprint/'.j2p-sprint').exists())
        manifest = json.loads((sprint/'runs/j2p-run-failed/run-manifest.json').read_text())
        self.assertEqual(manifest['status'], 'failed')
        self.assertTrue((sprint/'runs/j2p-run-failed/failure.json').exists())
        self.assertEqual(self.invoke(self.args+['--sprint', 'S1', '--run-id', 'retry'])[0], 0)

    def test_publication_failure_rolls_back_root_state(self):
        state = self.root/'Review/j2p-state.json'
        write_json(state, {'version': 1, 'epics': {}})
        old = state.read_bytes()
        from j2p import run_lifecycle
        original = run_lifecycle.write_json
        def fail_completion(path, data):
            if path.name == 'run-manifest.json' and data.get('status', '').startswith('completed'):
                raise OSError('manifest finalization failed')
            return original(path, data)
        with patch('j2p.run_lifecycle.write_json', side_effect=fail_completion):
            result, _, _ = self.invoke(self.args+['--write-state', '--run-id', 'rollback', '--sprint', 'S1'])
        self.assertEqual(result, 2)
        self.assertEqual(state.read_bytes(), old)
        self.assertFalse((self.root/'Review/sprints/S1/.j2p-sprint').exists())
        self.assertFalse(journal_path(state).exists())

    def test_failure_after_completed_manifest_replace_still_rolls_back(self):
        state = self.root/'Review/j2p-state.json'
        write_json(state, {'version': 1, 'epics': {}})
        old = state.read_bytes()
        from j2p import run_lifecycle
        original = run_lifecycle.write_json
        def fail_after_replace(path, data):
            original(path, data)
            if path.name == 'run-manifest.json' and data.get('status', '').startswith('completed'):
                raise OSError('directory fsync failed after replacement')
        with patch('j2p.run_lifecycle.write_json', side_effect=fail_after_replace):
            result, _, _ = self.invoke(self.args+['--write-state', '--run-id', 'after', '--sprint', 'S1'])
        self.assertEqual(result, 2)
        self.assertEqual(state.read_bytes(), old)
        self.assertFalse((self.root/'Review/sprints/S1/.j2p-sprint').exists())
        manifest = json.loads((self.root/'Review/sprints/S1/runs/j2p-run-after/run-manifest.json').read_text())
        self.assertEqual(manifest['status'], 'failed')

    def test_repeated_run_id_cannot_overwrite(self):
        args = self.args+['--run-id', 'same']
        self.assertEqual(self.invoke(args)[0], 0)
        snapshot = self.root/'Review/runs/j2p-run-same/state/j2p-state.after.json'
        old = snapshot.read_bytes()
        result, _, err = self.invoke(args)
        self.assertEqual(result, 2)
        self.assertIn('Run ID already exists', err)
        self.assertEqual(snapshot.read_bytes(), old)

    def test_atomic_replace_failure_keeps_existing_state(self):
        state = self.root/'state.json'
        write_bytes_atomic(state, b'previous')
        with patch('j2p.state.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaises(OSError):
                write_bytes_atomic(state, b'new')
        self.assertEqual(state.read_bytes(), b'previous')
        self.assertEqual(list(self.root.glob('*.tmp')), [])

    def test_kernel_lock_rejects_overlapping_owner(self):
        path = self.root/'state.lock'
        with exclusive_lock(path):
            with self.assertRaises(J2PError):
                with exclusive_lock(path):
                    pass
        with exclusive_lock(path):
            pass

    def test_interrupted_publication_recovers_previous_state(self):
        state = self.root/'state.json'
        old, new = b'{"version":1,"epics":{}}', b'{"version":1,"epics":{"NEW":{}}}'
        write_bytes_atomic(state, new)
        manifest = self.root/'run-manifest.json'
        write_json(manifest, {'status': 'in_progress'})
        marker = self.root/'.j2p-sprint'
        marker.write_text('reserved')
        write_json(journal_path(state), {'previous_state': base64.b64encode(old).decode(),
                   'old_sha256': hashlib.sha256(old).hexdigest(), 'new_sha256': hashlib.sha256(new).hexdigest(),
                   'manifest_path': str(manifest), 'new_sprint_marker': str(marker)})
        recover_pending_state(state)
        self.assertEqual(state.read_bytes(), old)
        self.assertFalse(marker.exists())
        self.assertFalse(journal_path(state).exists())
        self.assertEqual(json.loads(manifest.read_text())['status'], 'failed')

    def test_manifest_and_explicit_support_bundle(self):
        self.assertEqual(self.invoke(self.args+['--run-id', 'support'])[0], 0)
        run = self.root/'Review/runs/j2p-run-support'
        manifest = json.loads((run/'run-manifest.json').read_text())
        self.assertIn(manifest['status'], {'completed', 'completed_with_warnings'})
        self.assertEqual(len(manifest['inputs'][0]['sha256']), 64)
        self.assertIn('resolved_config', manifest)
        bundle = self.root/'support.zip'
        self.assertEqual(self.invoke(['support-bundle', '--run-dir', str(run), '--output', str(bundle)])[0], 0)
        with zipfile.ZipFile(bundle) as zipped:
            self.assertIn('run-manifest.json', zipped.namelist())
            self.assertFalse(any(x.startswith('inputs/') for x in zipped.namelist()))
        self.assertEqual(self.invoke(['support-bundle', '--run-dir', str(run), '--output', str(bundle)])[0], 2)

    def test_profile_overrides_paths_and_doctor_read_only(self):
        profile = self.root/'project.json'
        write_json(profile, {'version': 1, 'project_name': 'Saved', 'output_dir': 'runs',
                            'jira_csv': ['old.csv']})
        args = expand_profile_args(['validate', '--profile', str(profile), '--jira-csv', 'new.csv'])
        self.assertNotIn(str(self.root/'old.csv'), args)
        self.assertIn(str((self.root/'runs').resolve()), args)
        self.assertIn('Saved', args)
        result, output, error = self.invoke(['doctor', '--profile', str(profile), '--jira-csv',
                                            str(FIXTURES/'project-wide-jira-initial.csv'), '--config',
                                            str(FIXTURES/'mixed-config.yaml'), '--json'])
        self.assertEqual(result, 0, error)
        self.assertTrue(json.loads(output)['read_only'])
        self.assertFalse((self.root/'runs').exists())

    def test_unsafe_output_paths_are_rejected(self):
        for run_id in ('../../escape', 'x/y', 'x\\y', 'CON', 'bad:run'):
            result, _, _ = self.invoke(self.args+['--run-id', run_id])
            self.assertEqual(result, 2, run_id)
        self.assertFalse((self.root/'Review').exists())
