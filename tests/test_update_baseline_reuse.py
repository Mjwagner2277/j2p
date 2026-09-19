"""Update comparison uses the live prewrite snapshot without losing report detail."""

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import j2p.cli as cli
from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.models import AuditItem, ProjectTaskSnapshot
from j2p.state import run_plan_to_state, snapshots_from_state, write_json


FIXTURES = (Path(__file__).parent / "fixtures").resolve()


class UpdateBaselineReuseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.main_project = self.root / "main.mpp"
        self.main_project.write_bytes(b"source schedule")
        self.config_path = FIXTURES / "mixed-config.yaml"
        self.config = load_config(self.config_path)
        self.csv_path = FIXTURES / "project-wide-jira-update.csv"
        self.state_path = self.root / "baseline.json"
        initial = build_run_plan(FIXTURES / "project-wide-jira-initial.csv", self.config)
        write_json(self.state_path, run_plan_to_state(initial))
        self.baseline = snapshots_from_state(self.state_path)
        self.baseline["TEAM-9999"] = ProjectTaskSnapshot(key="TEAM-9999", name="Unmatched task")

    def args(self, *extra):
        return ["update", "--jira-csv", str(self.csv_path), "--config", str(self.config_path),
                "--main-project", str(self.main_project), "--project-name", "Review",
                "--sprint", "S1", "--run-id", "checked", "--output-dir", str(self.root), *extra]

    def invoke(self, args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_main_baseline_uses_update_snapshot_and_preserves_full_audit(self):
        expected = build_run_plan(self.csv_path, self.config, self.baseline)
        captured = []
        schedule_item = AuditItem("Review", "ScheduleDateChanged", jira_key="TEAM-10",
                                  field="Finish", old_value="2026-01-01", new_value="2026-01-02")

        def apply(sandbox, preflight, config, **options):
            plan = options["prepare_plan"](self.baseline)
            self.assertEqual([asdict(item) for item in plan.audit_items],
                             [asdict(item) for item in expected.audit_items])
            self.assertIsNot(plan, preflight)
            plan.audit_items.append(schedule_item)
            captured.append(plan)

        with patch("j2p.cli.snapshot_project_file") as separate_snapshot, \
                patch("j2p.cli.apply_plan_to_sandbox", side_effect=apply), \
                patch("j2p.cli.write_reports", wraps=cli.write_reports) as reports:
            code, _, error = self.invoke(self.args())
        self.assertEqual(code, 0, error)
        separate_snapshot.assert_not_called()
        self.assertEqual(len(captured), 1)
        self.assertIs(reports.call_args.args[0], captured[0])
        self.assertIn(schedule_item, reports.call_args.args[0].audit_items)
        self.assertTrue(any(item.category == "UnmatchedProjectTask" for item in captured[0].audit_items))
        self.assertEqual(self.main_project.read_bytes(), b"source schedule")
        state = json.loads((self.root / "Review" / "j2p-state.json").read_text())
        self.assertEqual(set(state["epics"]), set(expected.epics))

    def test_alternate_baselines_keep_their_selected_comparison(self):
        previous = self.root / "previous.mpp"
        previous.write_bytes(b"previous schedule")
        for source in ("previous-sandbox", "state"):
            with self.subTest(source=source):
                selected = self.baseline if source == "previous-sandbox" else snapshots_from_state(self.state_path)
                expected = build_run_plan(self.csv_path, self.config, selected)
                def apply(sandbox, plan, config, **options):
                    self.assertNotIn("prepare_plan", options)
                    self.assertEqual([asdict(item) for item in plan.audit_items],
                                     [asdict(item) for item in expected.audit_items])
                extra = ["--comparison-source", source, "--run-id", source, "--sprint", source]
                extra += (["--previous-sandbox", str(previous)] if source == "previous-sandbox"
                          else ["--state-path", str(self.state_path)])
                with patch("j2p.cli.snapshot_project_file", return_value=self.baseline) as snapshot, \
                        patch("j2p.cli.apply_plan_to_sandbox", side_effect=apply):
                    code, _, error = self.invoke(self.args(*extra))
                self.assertEqual(code, 0, error)
                if source == "previous-sandbox":
                    snapshot.assert_called_once_with(previous, self.config, visible=False)
                else:
                    snapshot.assert_not_called()

    def test_invalid_coverage_never_opens_or_copies_project(self):
        with patch("j2p.cli.prepare_sandbox_copy") as copy, \
                patch("j2p.cli.snapshot_project_file") as snapshot, \
                patch("j2p.cli.apply_plan_to_sandbox") as apply:
            code, _, error = self.invoke(self.args("--expected-issues", "0"))
        self.assertEqual(code, 2)
        self.assertIn("Export coverage mismatch", error)
        copy.assert_not_called()
        snapshot.assert_not_called()
        apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
