"""Folder discovery and stability checks independent of Project automation."""

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from j2p.input_folder import resolve_input_folder, verify_input_folder
from j2p.models import J2PError


class InputFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / "Project configuration and exports"
        self.folder.mkdir()

    def args(self, **overrides):
        values = {"config_folder": self.folder, "config": None, "jira_csv": None}
        values.update(overrides)
        return Namespace(**values)

    def write(self, name, content="test input"):
        path = self.folder / name
        path.write_text(content, encoding="utf-8")
        return path

    def populate(self):
        return self.write("project.yaml"), self.write("issues.csv")

    def symlink(self, path, target):
        try:
            path.symlink_to(target, target_is_directory=target.is_dir())
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks unavailable: {exc}")

    def test_discovers_top_level_files_with_spaces_and_case_insensitive_extensions(self):
        config = self.write("Project Configuration.YmL")
        second = self.write("z team.CSV")
        first = self.write("A team.csv")
        nested = self.folder / "generated reports"
        nested.mkdir()
        (nested / "report.csv").write_text("not an input")
        (nested / "resolved.yaml").write_text("not an input")
        (self.folder / "directory.csv").mkdir()
        (self.folder / "directory.yaml").mkdir()
        self.write("README.txt")
        args = self.args()
        resolve_input_folder(args)
        self.assertEqual(args.config_folder, self.folder.resolve())
        self.assertEqual(args.config, config.resolve())
        self.assertEqual(args.jira_csv, [first.resolve(), second.resolve()])
        self.assertTrue(args._input_folder_selection["config_discovered"])
        self.assertTrue(args._input_folder_selection["csv_discovered"])
        self.assertEqual(json.loads(json.dumps(args._input_folder_selection)), args._input_folder_selection)
        verify_input_folder(args._input_folder_selection)

    def test_explicit_config_overrides_multiple_folder_configurations(self):
        self.populate()
        self.write("other.YAML")
        explicit = self.root / "chosen.yaml"
        explicit.write_text("chosen")
        args = self.args(config=explicit)
        resolve_input_folder(args)
        self.assertEqual(args.config, explicit)
        self.assertFalse(args._input_folder_selection["config_discovered"])
        self.assertEqual(args._input_folder_selection["config_candidates"], [])
        self.write("new.yml")
        verify_input_folder(args._input_folder_selection)

    def test_explicit_csv_replaces_discovery_instead_of_merging(self):
        self.populate()
        chosen = self.root / "chosen.csv"
        chosen.write_text("chosen")
        paths = [chosen]
        args = self.args(jira_csv=paths)
        resolve_input_folder(args)
        self.assertIs(args.jira_csv, paths)
        self.assertFalse(args._input_folder_selection["csv_discovered"])
        self.assertEqual(args._input_folder_selection["csv_candidates"], [])
        self.write("new.csv")
        (self.folder / "issues.csv").unlink()
        verify_input_folder(args._input_folder_selection)

    def test_both_explicit_inputs_allow_an_empty_folder(self):
        config, csv = self.root / "chosen.yaml", self.root / "chosen.csv"
        args = self.args(config=config, jira_csv=[csv])
        resolve_input_folder(args)
        self.assertEqual(args.config, config)
        self.assertEqual(args.jira_csv, [csv])
        # Existing file readers remain responsible for readable/valid contents.
        verify_input_folder(args._input_folder_selection)

    def test_missing_or_non_directory_folder_is_actionable_even_with_overrides(self):
        regular_file = self.write("file.txt")
        for folder in (self.root / "missing", regular_file):
            with self.subTest(folder=folder):
                with self.assertRaisesRegex(J2PError, "existing directory"):
                    resolve_input_folder(self.args(config_folder=folder, config="x.yaml", jira_csv=["x.csv"]))

    def test_missing_configuration_and_multiple_configurations_are_actionable(self):
        args = self.args()
        with self.assertRaisesRegex(J2PError, "No YAML.*--config FILE"):
            resolve_input_folder(args)
        self.populate()
        self.write("other.yaml")
        with self.assertRaisesRegex(J2PError, "Multiple YAML.*--config FILE"):
            resolve_input_folder(args)
        self.assertIsNone(args.config)
        self.assertIsNone(args.jira_csv)
        self.assertFalse(hasattr(args, "_input_folder_selection"))

    def test_nested_csv_does_not_satisfy_required_exports(self):
        self.write("config.yaml")
        nested = self.folder / "archive"
        nested.mkdir()
        (nested / "issues.csv").write_text("archived")
        args = self.args()
        with self.assertRaisesRegex(J2PError, "No CSV.*Subfolders are not searched"):
            resolve_input_folder(args)
        self.assertIsNone(args.config)
        self.assertFalse(hasattr(args, "_input_folder_selection"))

    def test_profile_initialization_can_select_folder_without_csvs(self):
        self.write("config.yaml")
        args = self.args()
        resolve_input_folder(args, require_csv=False)
        self.assertEqual(args.jira_csv, [])
        verify_input_folder(args._input_folder_selection)
        with self.assertRaisesRegex(J2PError, "No Jira CSV inputs selected"):
            resolve_input_folder(args)
        self.write("later.csv")
        with self.assertRaisesRegex(J2PError, "CSV export selection changed"):
            verify_input_folder(args._input_folder_selection)

    def test_plain_file_mode_preserves_paths_and_requires_csv_only_when_requested(self):
        args = self.args(config_folder=None, config=Path("relative/config.yaml"),
                         jira_csv=[Path("relative/issues.csv")])
        resolve_input_folder(args)
        self.assertEqual(args.config, Path("relative/config.yaml"))
        self.assertEqual(args.jira_csv, [Path("relative/issues.csv")])
        self.assertFalse(hasattr(args, "_input_folder_selection"))
        with self.assertRaisesRegex(J2PError, "--jira-csv.*--config-folder"):
            resolve_input_folder(Namespace())
        resolve_input_folder(Namespace(), require_csv=False)
        verify_input_folder(None)

    def test_repeated_resolution_preserves_discovery_and_original_selection(self):
        self.populate()
        args = self.args()
        resolve_input_folder(args)
        selection = args._input_folder_selection
        original_csv = list(args.jira_csv)
        self.write("added.csv")
        resolve_input_folder(args)
        self.assertIs(args._input_folder_selection, selection)
        self.assertEqual(args.jira_csv, original_csv)
        with self.assertRaisesRegex(J2PError, "selection changed"):
            verify_input_folder(selection)

    def test_auto_selected_dimensions_reject_additions_removals_and_renames(self):
        config, csv = self.populate()
        for filename in (config.name, csv.name):
            for operation in ("add", "remove", "rename"):
                with self.subTest(filename=filename, operation=operation):
                    args = self.args()
                    resolve_input_folder(args)
                    original = self.folder / filename
                    other = self.folder / ("new" + original.suffix)
                    if operation == "add":
                        other.write_text("added")
                    elif operation == "remove":
                        original.unlink()
                    else:
                        original.rename(other)
                    try:
                        with self.assertRaisesRegex(J2PError, "selection changed"):
                            verify_input_folder(args._input_folder_selection)
                    finally:
                        if operation == "add":
                            other.unlink()
                        elif operation == "remove":
                            original.write_text("test input")
                        else:
                            other.rename(original)

    def test_content_and_unrelated_files_are_left_to_existing_hash_checks(self):
        config, csv = self.populate()
        args = self.args()
        resolve_input_folder(args)
        config.write_text("changed content")
        csv.write_text("changed content")
        self.write("notes.md")
        nested = self.folder / "output"
        nested.mkdir()
        (nested / "audit.csv").write_text("report")
        verify_input_folder(args._input_folder_selection)

    def test_csv_aliases_parse_once_but_all_names_are_tracked_for_stability(self):
        _, csv = self.populate()
        alias = self.folder / "alias.csv"
        self.symlink(alias, csv)
        args = self.args()
        resolve_input_folder(args)
        self.assertEqual(args.jira_csv, [csv.resolve()])
        self.assertEqual(len(args._input_folder_selection["csv_candidates"]), 2)
        alias.unlink()
        with self.assertRaisesRegex(J2PError, "selection changed"):
            verify_input_folder(args._input_folder_selection)

    def test_symlink_retargeting_of_discovered_config_and_csv_is_rejected(self):
        for suffix in ("yaml", "csv"):
            with self.subTest(suffix=suffix):
                config, csv = self.populate()
                candidate = config if suffix == "yaml" else csv
                first, second = self.root / f"first.{suffix}", self.root / f"second.{suffix}"
                first.write_text("same")
                second.write_text("same")
                candidate.unlink()
                self.symlink(candidate, first)
                args = self.args()
                resolve_input_folder(args)
                candidate.unlink()
                self.symlink(candidate, second)
                with self.assertRaisesRegex(J2PError, "symlink target changed"):
                    verify_input_folder(args._input_folder_selection)
                candidate.unlink()

    def test_explicit_symlinks_remain_unresolved_for_existing_pipeline_capture(self):
        self.populate()
        config_link, csv_link = self.root / "config.yaml", self.root / "export.csv"
        self.symlink(config_link, self.folder / "project.yaml")
        self.symlink(csv_link, self.folder / "issues.csv")
        args = self.args(config=config_link, jira_csv=[csv_link])
        resolve_input_folder(args)
        self.assertEqual(args.config, config_link)
        self.assertEqual(args.jira_csv, [csv_link])
        self.assertEqual(args._input_folder_selection["config"], str(config_link.resolve()))
        self.assertEqual(args._input_folder_selection["csv_paths"], [str(csv_link.resolve())])

    def test_retargeted_folder_symlink_is_rejected(self):
        self.populate()
        folder_link = self.root / "current"
        self.symlink(folder_link, self.folder)
        args = self.args(config_folder=folder_link)
        resolve_input_folder(args)
        other = self.root / "other snapshot"
        other.mkdir()
        folder_link.unlink()
        self.symlink(folder_link, other)
        with self.assertRaisesRegex(J2PError, "Config folder changed"):
            verify_input_folder(args._input_folder_selection)


if __name__ == "__main__":
    unittest.main()
