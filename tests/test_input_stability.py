"""Input identity must describe the bytes used, and state must avoid run metadata."""

import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import j2p.cli as cli
from j2p.config import load_config
from j2p.core import build_run_plan


FIXTURES = (Path(__file__).parent / "fixtures").resolve()


class InputStabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = FIXTURES / "project-wide-jira-initial.csv"
        self.config = FIXTURES / "mixed-config.yaml"

    def args(self, **changes):
        options = {
            "jira-csv": str(self.source), "config": str(self.config),
            "project-name": "Review", "output-dir": str(self.root), "run-id": "checked",
        }
        options.update(changes)
        result = ["validate"]
        for name, value in options.items():
            result.extend(["--" + name, str(value)])
        return [*result, "--write-state"]

    def invoke(self, args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    def assert_no_completed_publication(self):
        self.assertFalse((self.root / "Review" / "j2p-state.json").exists())
        for path in self.root.rglob("run-manifest.json"):
            manifest = json.loads(path.read_text())
            self.assertNotIn(manifest.get("status"), {"completed", "completed_with_warnings"})

    def test_state_cannot_use_active_run_pointer(self):
        state = self.root / "Review" / ".j2p-active-run.json"
        code, _, error = self.invoke(self.args(**{"state-path": state}))
        self.assertEqual(code, 2, error)
        self.assertIn("state", error.casefold())
        self.assertFalse(state.exists())
        self.assert_no_completed_publication()

    def test_state_cannot_overwrite_a_previous_run_artifact(self):
        historical = self.root / "Review" / "runs" / "j2p-run-prior" / "state" / "snapshot.json"
        historical.parent.mkdir(parents=True)
        original = b'{"version": 1, "epics": {}}\n'
        historical.write_bytes(original)
        code, _, error = self.invoke(self.args(**{"state-path": historical}))
        self.assertEqual(code, 2, error)
        self.assertEqual(historical.read_bytes(), original)
        self.assert_no_completed_publication()

    def test_config_edit_after_parsing_cannot_be_fingerprinted_as_used_config(self):
        config = self.root / "config.yaml"
        config.write_text(self.config.read_text())
        original_load = cli.load_config

        def edit_after_load(path, *args, **kwargs):
            parsed = original_load(path, *args, **kwargs)
            config.write_text(config.read_text().replace("TEAM: Product Delivery", "TEAM: Different Team"))
            return parsed

        with patch("j2p.cli.load_config", side_effect=edit_after_load):
            code, _, error = self.invoke(self.args(config=config))
        self.assertEqual(code, 2, error)
        self.assertIn("chang", error.casefold())
        self.assert_no_completed_publication()

    def test_profile_edit_after_expansion_cannot_be_fingerprinted_as_used_profile(self):
        profile = self.root / "project.json"
        data = {"version": 1, "project_name": "Review", "output_dir": str(self.root),
                "config": str(self.config), "jira_csv": [str(self.source)]}
        profile.write_text(json.dumps(data))
        original_expand = cli.expand_profile_args

        def edit_after_expansion(*args, **kwargs):
            expanded = original_expand(*args, **kwargs)
            profile.write_text(json.dumps(dict(data, project_name="Different Project")))
            return expanded

        with patch("j2p.cli.expand_profile_args", side_effect=edit_after_expansion):
            code, _, error = self.invoke(["validate", "--profile", str(profile), "--run-id", "checked", "--write-state"])
        self.assertEqual(code, 2, error)
        self.assertIn("chang", error.casefold())
        self.assert_no_completed_publication()

    def test_rotated_csv_symlink_uses_captured_target_or_fails(self):
        latest = self.root / "latest.csv"
        try:
            latest.symlink_to(self.source)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symlinks unavailable on this host: {exc}")
        original_build = cli.build_run_plan
        expected = build_run_plan(self.source, load_config(self.config))
        rotated = []

        def rotate_before_parse(*args, **kwargs):
            if not rotated:
                latest.unlink()
                latest.symlink_to(FIXTURES / "project-wide-jira-update.csv")
                rotated.append(True)
            return original_build(*args, **kwargs)

        with patch("j2p.cli.build_run_plan", side_effect=rotate_before_parse):
            code, _, error = self.invoke(self.args(**{"jira-csv": latest}))
        self.assertTrue(rotated, "The export must rotate after capture and before parsing.")
        if code == 2:
            self.assertIn("chang", error.casefold())
            self.assert_no_completed_publication()
            return
        self.assertEqual(code, 0, error)
        run = self.root / "Review" / "runs" / "j2p-run-checked"
        manifest = json.loads((run / "run-manifest.json").read_text())
        captured = manifest["inputs"][0]
        self.assertEqual(captured["path"], str(self.source))
        self.assertEqual(captured["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(manifest["stats"]["csv_batches"][0]["path"], captured["path"])
        state = json.loads((self.root / "Review" / "j2p-state.json").read_text())
        self.assertEqual(set(state["epics"]), set(expected.epics))
        for key, epic in expected.epics.items():
            for field in ("summary", "total_story_points", "completed_story_points", "logged_hours", "percent_complete"):
                self.assertEqual(state["epics"][key][field], getattr(epic, field), (key, field))


if __name__ == "__main__":
    unittest.main()
