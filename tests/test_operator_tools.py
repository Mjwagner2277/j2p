"""Operator commands reject damaged metadata and preserve explicit input opt-in."""

import json
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from j2p.cli import build_parser, main
from j2p.models import J2PError
from j2p.operator_tools import expand_profile_args
from j2p.run_lifecycle import file_identity, journal_path, recover_pending_state
from j2p.state import snapshots_from_state


class OperatorToolsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write_payload(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Deliberately permit nonstandard numeric values to model damaged state.
        path.write_text(json.dumps(payload), encoding="utf-8")

    def invoke(self, arguments):
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(arguments)
        return code, output.getvalue(), error.getvalue()

    def test_profile_dependency_mode_applies_to_create_and_respects_override(self):
        profile = self.root / "profile.json"
        self.write_payload(profile, {
            "version": 1, "project_name": "Saved", "jira_csv": ["batch.csv"],
            "main_project": "main.mpp", "comparison_source": "state",
            "dependency_write_mode": "diagnostic",
        })
        for command in ("create", "update"):
            arguments = [command, "--profile", str(profile)]
            if command == "update":
                arguments += ["--sprint", "S1"]
            parsed = build_parser().parse_args(expand_profile_args(arguments))
            self.assertEqual(parsed.dependency_write_mode, "diagnostic")
            parsed = build_parser().parse_args(expand_profile_args(arguments + ["--dependency-write-mode=fast"]))
            self.assertEqual(parsed.dependency_write_mode, "fast")
        for command in ("validate", "doctor"):
            expanded = expand_profile_args([command, "--profile", str(profile)])
            self.assertNotIn("--dependency-write-mode", expanded)
            build_parser().parse_args(expanded)

    def test_malformed_profile_objects_fail_cleanly(self):
        profile = self.root / "profile.json"
        for payload in ([], None, {"version": 99}, {"version": 1, "unknown": "value"}):
            with self.subTest(payload=payload):
                self.write_payload(profile, payload)
                code, _, error = self.invoke(["doctor", "--profile", str(profile)])
                self.assertEqual(code, 2)
                self.assertTrue(error.startswith("ERROR:"))

    def test_damaged_state_entries_report_path_and_issue(self):
        state = self.root / "state.json"
        for epic in (None, {"predecessors": None}, {"percent_complete": float("inf")},
                     {"logged_hours": float("nan")}, {"summary": []}, {"drives_schedule": "false"}):
            with self.subTest(epic=epic):
                self.write_payload(state, {"version": 1, "epics": {"TEAM-1": epic}})
                with self.assertRaises(J2PError) as raised:
                    snapshots_from_state(state)
                self.assertIn(str(state), str(raised.exception))
                self.assertIn("TEAM-1", str(raised.exception))
        for payload in ([], None, {"version": 1, "epics": []}):
            with self.subTest(payload=payload):
                self.write_payload(state, payload)
                with self.assertRaises(J2PError):
                    snapshots_from_state(state)

    def test_doctor_reports_malformed_state_without_writing_outputs(self):
        state = self.root / "state.json"
        self.write_payload(state, {"version": 1, "epics": {"TEAM-1": {"percent_complete": float("inf")}}})
        before = state.read_bytes()
        output_dir = self.root / "uncreated-output"
        code, output, error = self.invoke([
            "doctor", "--state-path", str(state), "--output-dir", str(output_dir), "--json",
        ])
        self.assertEqual(code, 2, error)
        report = json.loads(output)
        check = next(item for item in report["checks"] if item["name"] == "state")
        self.assertEqual(check["status"], "failed")
        self.assertIn("TEAM-1", check["detail"])
        self.assertTrue(report["read_only"])
        self.assertEqual(state.read_bytes(), before)
        self.assertFalse(output_dir.exists())

    def test_malformed_journal_and_manifest_preserve_recovery_evidence(self):
        state = self.root / "state.json"
        self.write_payload(state, {"version": 1, "epics": {}})
        before = state.read_bytes()
        manifest = self.root / "run-manifest.json"
        self.write_payload(manifest, [])
        for payload in ([], None, {"manifest_path": str(manifest)}):
            with self.subTest(payload=payload):
                journal = journal_path(state)
                self.write_payload(journal, payload)
                with self.assertRaises(J2PError):
                    recover_pending_state(state)
                self.assertTrue(journal.exists())
                self.assertEqual(state.read_bytes(), before)

    def test_support_bundle_rejects_malformed_manifest_and_input_identity(self):
        run = self.root / "run"
        output = self.root / "support.zip"
        for payload in ([], None, {"inputs": None}, {"inputs": [None]}, {"inputs": [{"path": "missing.csv"}]}):
            with self.subTest(payload=payload):
                self.write_payload(run / "run-manifest.json", payload)
                code, _, error = self.invoke([
                    "support-bundle", "--run-dir", str(run), "--output", str(output), "--include-inputs",
                ])
                self.assertEqual(code, 2)
                self.assertTrue(error.startswith("ERROR:"))
                self.assertFalse(output.exists())

    def test_support_inputs_require_opt_in_and_match_recorded_hashes(self):
        run = self.root / "run"
        sources = [self.root / "first" / "batch.csv", self.root / "second" / "batch.csv"]
        for index, source in enumerate(sources):
            source.parent.mkdir()
            source.write_text(f"Issue key,Summary\nTEAM-{index},Example {index}\n")
        self.write_payload(run / "run-manifest.json", {"inputs": [file_identity(path) for path in sources]})
        arguments = ["support-bundle", "--run-dir", str(run)]
        default_zip, full_zip = self.root / "metadata.zip", self.root / "full.zip"
        self.assertEqual(self.invoke(arguments + ["--output", str(default_zip)])[0], 0)
        with zipfile.ZipFile(default_zip) as bundle:
            self.assertFalse(any(name.startswith("inputs/") for name in bundle.namelist()))
        self.assertEqual(self.invoke(arguments + ["--output", str(full_zip), "--include-inputs"])[0], 0)
        with zipfile.ZipFile(full_zip) as bundle:
            for index, source in enumerate(sources, start=1):
                self.assertEqual(bundle.read(f"inputs/{index:03d}-batch.csv"), source.read_bytes())
        sources[0].write_text("changed since this run")
        failed_zip = self.root / "changed.zip"
        code, _, error = self.invoke(arguments + ["--output", str(failed_zip), "--include-inputs"])
        self.assertEqual(code, 2)
        self.assertIn("changed since this run", error)
        self.assertFalse(failed_zip.exists())
