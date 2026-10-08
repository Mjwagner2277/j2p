"""Folder input integration: explicit scope, actionable warnings, stable publication."""

import csv
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from j2p.cli import annotate_input_coverage, build_parser, main, make_context
from j2p.config import load_config
from j2p.core import build_run_plan
from j2p.operator_tools import expand_profile_args
from j2p.reports import write_reports
from j2p.run_lifecycle import file_identity, journal_path
from j2p.state import write_json


COVERAGE_CATEGORIES = {"MissingProjectExport", "UnconfiguredJiraProject"}


class ConfigFolderCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.folder = self.root / "input folder"
        self.folder.mkdir()
        self.output = self.root / "output"
        self.config = self.write_config(self.folder / "project.yaml", ("AAA", "BBB"))
        self.csv = self.write_csv(self.folder / "mixed.csv", ("AAA", "ZZZ"))

    def write_config(self, path, projects):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "resource_groups:\n" + "".join(f"  {key}: {key} Team\n" for key in projects)
            + "rollup_modes:\n" + "".join(f"  {key}: fixVersion\n" for key in projects)
            + "planning_horizon:\n  as_of_date: 2026-10-01\n"
            + "fixversion_completion_suppression:\n  enabled: false\n",
            encoding="utf-8",
        )
        return path

    def write_csv(self, path, projects):
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["Issue key", "Issue Type", "Summary", "Epic Link", "Fix versions",
                  "Story Points", "Status", "Target start", "Target end"]
        with path.open("w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            for prefix in projects:
                writer.writerow({"Issue key": f"{prefix}-1", "Issue Type": "Epic",
                                 "Summary": f"{prefix} epic", "Fix versions": "Shared",
                                 "Status": "In Progress", "Target start": "2026-10-01",
                                 "Target end": "2026-10-30"})
                writer.writerow({"Issue key": f"{prefix}-2", "Issue Type": "Task",
                                 "Summary": f"{prefix} child", "Epic Link": f"{prefix}-1",
                                 "Story Points": "3", "Status": "To Do"})
        return path

    def invoke(self, arguments):
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(arguments)
        return code, output.getvalue(), error.getvalue()

    def args(self, command="validate", run_id="folder"):
        arguments = [command, "--config-folder", str(self.folder), "--project-name", "Review",
                     "--output-dir", str(self.output), "--run-id", run_id]
        if command == "update":
            source = self.root / "main.mpp"
            source.write_bytes(b"synthetic main Project file")
            arguments += ["--main-project", str(source), "--sprint", "S1"]
        return arguments

    def manifest(self, run_id="folder", sprint=None):
        parent = self.output / "Review"
        if sprint:
            parent = parent / "sprints" / sprint
        return json.loads((parent / "runs" / f"j2p-run-{run_id}" / "run-manifest.json")
                          .read_text(encoding="utf-8"))

    def coverage_rows(self, manifest):
        with Path(manifest["outputs"]["audit_detail"]).open(encoding="utf-8", newline="") as source:
            return [row for row in csv.DictReader(source) if row["category"] in COVERAGE_CATEGORIES]

    def test_folder_validate_records_resolved_inputs_and_coverage_in_manifest_and_audit(self):
        before = {path: path.read_bytes() for path in (self.config, self.csv)}
        code, output, error = self.invoke(self.args())
        self.assertEqual(code, 0, error)
        self.assertEqual(output.count("WARNING:"), 2)
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "completed_with_warnings")
        self.assertEqual(manifest["inputs"], [file_identity(self.csv)])
        self.assertEqual(manifest["config"], file_identity(self.config))
        self.assertEqual(manifest["resolved_config"], load_config(self.config))
        selection = manifest["input_folder"]
        self.assertEqual(selection["path"], str(self.folder))
        self.assertTrue(selection["config_discovered"])
        self.assertTrue(selection["csv_discovered"])
        self.assertEqual(selection["config"], str(self.config))
        self.assertEqual(selection["csv_paths"], [str(self.csv)])
        coverage = manifest["stats"]["project_coverage"]
        self.assertEqual(coverage["missing_projects"], ["BBB"])
        self.assertEqual(coverage["unconfigured_projects"], ["ZZZ"])
        self.assertEqual(coverage["issue_counts_by_project"], {"AAA": 2, "ZZZ": 2})
        rows = self.coverage_rows(manifest)
        self.assertEqual([(row["category"], row["old_value"]) for row in rows],
                         [("MissingProjectExport", "BBB"), ("UnconfiguredJiraProject", "ZZZ")])
        self.assertTrue(all(row["source_file"] == str(self.folder) for row in rows))
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_folder_does_not_scan_nested_files_or_infer_coverage_from_names(self):
        self.write_config(self.folder / "nested" / "ignored.yaml", ("NESTED",))
        self.write_csv(self.folder / "nested" / "BBB.csv", ("BBB",))
        # A CSV's project-looking filename cannot supply missing issue keys.
        self.csv = self.csv.rename(self.folder / "BBB.csv")
        code, _, error = self.invoke(self.args())
        self.assertEqual(code, 0, error)
        manifest = self.manifest()
        self.assertEqual(manifest["stats"]["csv_files_read"], 1)
        self.assertEqual(manifest["stats"]["project_coverage"]["missing_projects"], ["BBB"])
        self.assertEqual(manifest["inputs"], [file_identity(self.csv)])

    def test_explicit_config_and_csv_override_only_their_own_folder_default(self):
        override_config = self.write_config(self.root / "override.yaml", ("AAA", "ZZZ"))
        override_csv = self.write_csv(self.root / "override.csv", ("AAA", "BBB"))
        cases = [
            ("config", ["--config", str(override_config)], override_config, self.csv, False, True),
            ("csv", ["--jira-csv", str(override_csv)], self.config, override_csv, True, False),
            ("both", ["--config", str(override_config), "--jira-csv", str(override_csv)],
             override_config, override_csv, False, False),
        ]
        for run_id, options, config, source, config_discovered, csv_discovered in cases:
            with self.subTest(run_id=run_id):
                code, output, error = self.invoke(self.args(run_id=run_id) + options)
                self.assertEqual(code, 0, error)
                manifest = self.manifest(run_id)
                self.assertEqual(manifest["config"], file_identity(config))
                self.assertEqual(manifest["inputs"], [file_identity(source)])
                self.assertEqual(manifest["input_folder"]["config_discovered"], config_discovered)
                self.assertEqual(manifest["input_folder"]["csv_discovered"], csv_discovered)
                if run_id in {"config", "csv"}:
                    self.assertNotIn("WARNING:", output)
                else:
                    self.assertIn("explicitly selected CSV exports", output)

    def test_doctor_json_exposes_nonblocking_coverage_warnings_without_writes(self):
        code, output, error = self.invoke([
            "doctor", "--config-folder", str(self.folder), "--project-name", "Review",
            "--output-dir", str(self.output), "--json",
        ])
        self.assertEqual(code, 0, error)
        report = json.loads(output)
        self.assertTrue(report["read_only"])
        self.assertEqual(report["input_folder"]["csv_paths"], [str(self.csv)])
        self.assertEqual(report["input_stats"]["project_coverage"]["missing_projects"], ["BBB"])
        warnings = [item for item in report["checks"] if item["name"] == "project coverage"]
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all(item["status"] == "warning" for item in warnings))
        self.assertFalse(self.output.exists())

    def test_init_profile_keeps_folder_dynamic_without_pinning_discovered_files(self):
        profile = self.root / "profile.json"
        code, _, error = self.invoke([
            "init-profile", "--path", str(profile), "--project-name", "Review",
            "--config-folder", str(self.folder), "--output-dir", str(self.output),
        ])
        self.assertEqual(code, 0, error)
        saved = json.loads(profile.read_text())
        self.assertEqual(saved["config_folder"], str(self.folder))
        self.assertNotIn("config", saved)
        self.assertNotIn("jira_csv", saved)
        self.write_csv(self.folder / "later.csv", ("BBB",))
        self.config.rename(self.folder / "renamed.yml")
        code, output, error = self.invoke(["doctor", "--profile", str(profile), "--json"])
        self.assertEqual(code, 0, error)
        report = json.loads(output)
        self.assertEqual(report["input_stats"]["csv_files_read"], 2)
        self.assertEqual(report["input_stats"]["project_coverage"]["missing_projects"], [])
        self.assertTrue(report["input_folder"]["config"].endswith("renamed.yml"))

    def test_init_profile_preserves_explicit_config_override(self):
        profile = self.root / "explicit-profile.json"
        override = self.write_config(self.root / "override.yml", ("AAA", "ZZZ"))
        code, _, error = self.invoke([
            "init-profile", "--path", str(profile), "--project-name", "Review",
            "--config-folder", str(self.folder), "--config", str(override),
        ])
        self.assertEqual(code, 0, error)
        saved = json.loads(profile.read_text())
        self.assertEqual(saved["config"], str(override))
        self.assertEqual(saved["config_folder"], str(self.folder))

    def test_profile_relative_folder_and_explicit_folder_override_stale_files(self):
        profile = self.root / "relative-profile.json"
        write_json(profile, {"version": 1, "project_name": "Review", "config_folder": "input folder",
                             "output_dir": "output"})
        parsed = build_parser().parse_args(expand_profile_args([
            "validate", "--profile", str(profile), "--run-id", "relative",
        ]))
        context = make_context(parsed)
        self.assertEqual(context["input_folder"]["path"], str(self.folder))
        self.assertEqual(parsed.config, self.config)
        self.assertEqual(parsed.jira_csv, [self.csv])
        # An explicit new folder replaces the profile's entire input selection.
        write_json(profile, {"version": 1, "project_name": "Review", "config_folder": "old-folder",
                             "config": "missing.yaml", "jira_csv": ["missing.csv"],
                             "output_dir": "output"})
        code, _, error = self.invoke([
            "validate", "--profile", str(profile), "--config-folder", str(self.folder),
            "--run-id", "new-folder",
        ])
        self.assertEqual(code, 0, error)
        manifest = self.manifest("new-folder")
        self.assertEqual(manifest["config"], file_identity(self.config))
        self.assertEqual(manifest["inputs"], [file_identity(self.csv)])

    def test_create_and_update_warn_once_before_project_and_keep_rebuilt_plan_audit(self):
        for command in ("create", "update"):
            with self.subTest(command=command):
                output, error = StringIO(), StringIO()
                observed = []

                def check_warnings(plan):
                    self.assertEqual(output.getvalue().count("WARNING:"), 2)
                    coverage = [item for item in plan.audit_items if item.category in COVERAGE_CATEGORIES]
                    self.assertEqual(len(coverage), 2)
                    observed.append(plan)

                def fake_create(path, plan, config, **options):
                    check_warnings(plan)
                    path.write_bytes(b"created sandbox")

                def fake_copy(source, project_dir, run_id):
                    self.assertEqual(output.getvalue().count("WARNING:"), 2)
                    sandbox = project_dir / "sandbox.mpp"
                    sandbox.write_bytes(source.read_bytes())
                    return sandbox

                def fake_update(path, plan, config, **options):
                    check_warnings(plan)
                    rebuilt = options["prepare_plan"]({})
                    self.assertIsNot(plan, rebuilt)
                    check_warnings(rebuilt)

                with patch("j2p.cli.create_project_from_plan", side_effect=fake_create) as create, \
                     patch("j2p.cli.prepare_sandbox_copy", side_effect=fake_copy) as copy, \
                     patch("j2p.cli.apply_plan_to_sandbox", side_effect=fake_update) as update, \
                     redirect_stdout(output), redirect_stderr(error):
                    code = main(self.args(command, run_id=command))
                self.assertEqual(code, 0, error.getvalue())
                self.assertEqual(create.call_count, int(command == "create"))
                self.assertEqual(copy.call_count, int(command == "update"))
                self.assertEqual(update.call_count, int(command == "update"))
                self.assertEqual(len(observed), 1 if command == "create" else 2)
                self.assertEqual(output.getvalue().count("WARNING:"), 2)
                manifest = self.manifest(command, "S1" if command == "update" else None)
                self.assertEqual(len(self.coverage_rows(manifest)), 2)
                self.assertEqual(manifest["stats"]["audit_items"], len(observed[-1].audit_items))

    def test_folder_changes_during_reports_cannot_advance_persistent_state(self):
        state = self.output / "Review" / "j2p-state.json"
        write_json(state, {"version": 1, "epics": {}})
        previous = state.read_bytes()
        mutations = ("add-csv", "remove-csv", "change-config", "add-config")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                original_config, original_csv = self.config.read_bytes(), self.csv.read_bytes()
                extra_csv, extra_config = self.folder / "new.csv", self.folder / "new.yaml"

                def changed_reports(*args, **kwargs):
                    paths = write_reports(*args, **kwargs)
                    if mutation == "add-csv":
                        self.write_csv(extra_csv, ("BBB",))
                    elif mutation == "remove-csv":
                        self.csv.unlink()
                    elif mutation == "change-config":
                        self.config.write_bytes(original_config + b"\n# changed during run\n")
                    else:
                        extra_config.write_bytes(original_config)
                    return paths

                try:
                    with patch("j2p.cli.write_reports", side_effect=changed_reports):
                        code, _, error = self.invoke(self.args(run_id=mutation) + ["--write-state"])
                    self.assertEqual(code, 2)
                    self.assertIn("changed during the run", error)
                    self.assertEqual(state.read_bytes(), previous)
                    self.assertFalse(journal_path(state).exists())
                    self.assertEqual(self.manifest(mutation)["status"], "failed")
                finally:
                    self.config.write_bytes(original_config)
                    self.csv.write_bytes(original_csv)
                    extra_csv.unlink(missing_ok=True)
                    extra_config.unlink(missing_ok=True)

    def test_invalid_folder_inputs_return_clear_code_two_before_project(self):
        empty = self.root / "empty"
        empty.mkdir()
        missing_csv = self.root / "config-only"
        missing_csv.mkdir()
        self.write_config(missing_csv / "project.yaml", ("AAA",))
        ambiguous = self.root / "ambiguous"
        ambiguous.mkdir()
        self.write_config(ambiguous / "one.yaml", ("AAA",))
        self.write_config(ambiguous / "two.yml", ("AAA",))
        cases = [
            ([], "No Jira CSV inputs selected"),
            (["--config-folder", str(self.root / "missing")], "not an existing directory"),
            (["--config-folder", str(empty)], "No YAML configuration"),
            (["--config-folder", str(missing_csv)], "No CSV exports"),
            (["--config-folder", str(ambiguous)], "Multiple YAML configurations"),
        ]
        for options, expected in cases:
            with self.subTest(options=options), patch("j2p.cli.create_project_from_plan") as create:
                code, _, error = self.invoke([
                    "create", "--project-name", "Review", "--output-dir", str(self.output), *options,
                ])
                self.assertEqual(code, 2)
                self.assertIn(expected, error)
                create.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_explicit_file_mode_preserves_existing_behavior(self):
        code, output, error = self.invoke([
            "validate", "--project-name", "Review", "--output-dir", str(self.output),
            "--run-id", "files", "--config", str(self.config), "--jira-csv", str(self.csv),
        ])
        self.assertEqual(code, 0, error)
        manifest = self.manifest("files")
        self.assertNotIn("input_folder", manifest)
        self.assertNotIn("WARNING:", output)
        self.assertEqual(self.coverage_rows(manifest), [])
        self.assertEqual(manifest["inputs"], [file_identity(self.csv)])
        self.assertEqual(manifest["stats"]["project_coverage"]["missing_projects"], ["BBB"])


class YerpConfigFolderIntegrationTests(unittest.TestCase):
    def test_folder_matches_explicit_real_project_plan_and_keeps_all_exports_unchanged(self):
        folder = Path(__file__).resolve().parents[1] / "yerp"
        sources = sorted(folder.glob("*.csv"))
        config = folder / "ssn-812-config.yaml"
        if not sources or not config.exists():
            self.skipTest("Private yerp exports and configuration are unavailable")
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
        try:
            with tempfile.TemporaryDirectory() as directory:
                args = build_parser().parse_args([
                    "validate", "--config-folder", str(folder), "--project-name", "Yerp",
                    "--output-dir", directory,
                ])
                context = make_context(args)
                folder_plan = build_run_plan(args.jira_csv, context["config"])
                annotate_input_coverage(folder_plan, args)
                explicit = build_run_plan(sources, load_config(config))
                self.assertEqual(set(args.jira_csv), set(sources))
                self.assertEqual(len(sources), 9)
                self.assertEqual({key: asdict(value) for key, value in folder_plan.epics.items()},
                                 {key: asdict(value) for key, value in explicit.epics.items()})
                self.assertEqual({key: asdict(value) for key, value in folder_plan.summaries.items()},
                                 {key: asdict(value) for key, value in explicit.summaries.items()})
                self.assertEqual(folder_plan.stats, explicit.stats)
                self.assertEqual(folder_plan.stats["csv_files_read"], 9)
                self.assertEqual(len(folder_plan.epics), 3334)
                self.assertEqual(len(folder_plan.summaries), 62)
                self.assertAlmostEqual(sum(row.total_story_points for row in folder_plan.epics.values()
                                           if row.drives_schedule), 20522)
                self.assertEqual(folder_plan.stats["project_coverage"]["missing_projects"], [])
                self.assertEqual(folder_plan.stats["project_coverage"]["unconfigured_projects"], [])
                self.assertFalse(any(item.category in COVERAGE_CATEGORIES
                                     for item in folder_plan.audit_items))
        finally:
            self.assertEqual({path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}, before)


if __name__ == "__main__":
    unittest.main()
