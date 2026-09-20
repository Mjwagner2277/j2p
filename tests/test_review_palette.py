"""Keep Project review colors distinct from cascade diagram roles."""

import tempfile
import unittest
from pathlib import Path

from j2p.config import load_config
from j2p.models import AuditItem, RunPlan
from j2p.project import PJ_COLOR_SILVER, project_color, project_pj_color
from j2p.reports import (
    color_key,
    color_label,
    render_color_examples,
    render_project_update_metrics,
    render_schedule_cascade_table,
    write_field_mapping,
)

ROOT = Path(__file__).resolve().parents[1]


def review_plan(audit_items=()):
    return RunPlan("2026-09-19", "synthetic.csv", "fixVersion", {}, {}, {}, {}, list(audit_items))


class ReviewPaletteTests(unittest.TestCase):
    def test_default_and_yerp_dependency_reviews_use_readable_gray_with_palette_fallback(self):
        for config in (load_config(None), load_config(ROOT / "yerp" / "ssn-812-config.yaml")):
            with self.subTest(config=config["resource_groups"]):
                color = config["colors"]["dependency_review"]
                self.assertEqual(color, "#F2F2F2")
                self.assertEqual(project_pj_color(color, project_color(color)), PJ_COLOR_SILVER)
                self.assertNotEqual(color, config["colors"]["changed_cell"])
        self.assertEqual(color_label("dependency_review"), "Light gray")

    def test_custom_dependency_palette_still_wins_over_the_default(self):
        config = load_config(None, {"colors": {"dependency_review": "#FFFFFF"}})
        self.assertEqual(config["colors"]["dependency_review"], "#FFFFFF")

    def test_driver_example_uses_category_even_when_its_project_cell_is_green(self):
        item = AuditItem(
            "Review", "CascadeBranchDriver", jira_key="TEAM-1", schedule_key="TEAM-1",
            field="Finish", old_value="2026-09-18", new_value="2026-09-21",
            color="changed_cell", message="Project moved the finish after scheduling.",
        )
        rendered = render_color_examples(review_plan([item]))
        red_row = next(row for row in rendered.split("<tr>") if 'class="dot cascade"' in row)
        self.assertIn("TEAM-1", red_row)
        self.assertIn("CascadeBranchDriver", red_row)
        self.assertIn("report diagram card only", red_row)
        self.assertIn("Project Finish cell remains green", red_row)
        self.assertNotIn("Project run only", red_row)
        self.assertEqual(item.color, "changed_cell")

    def test_cascade_detail_labels_diagram_colors_and_preserves_dates(self):
        item = AuditItem(
            "Review", "CascadeBranchDriver", jira_key="TEAM-1", schedule_key="TEAM-1",
            field="Finish", old_value="2026-09-18", new_value="2026-09-21", color="changed_cell",
        )
        rendered = render_schedule_cascade_table(review_plan([item]), {"TEAM-1": item})
        self.assertIn("Diagram Color", rendered)
        self.assertIn("Red", rendered)
        self.assertIn("2026-09-18", rendered)
        self.assertIn("2026-09-21", rendered)

    def test_validation_example_does_not_claim_red_project_cells(self):
        rendered = render_color_examples(review_plan())
        self.assertIn("Validate mode cannot identify red report diagram cards", rendered)
        self.assertIn("Project Finish cell remains green", rendered)
        self.assertIn("Light gray", rendered)
        self.assertNotIn("red cell", rendered)
        self.assertNotIn("Blue", rendered)

    def test_schedule_review_timing_is_labeled_as_a_formatting_subset(self):
        for mode in ("create", "update"):
            plan = review_plan()
            plan.stats.update({
                "project_run_mode": mode,
                "project_update_seconds": {"schedule_review": 2.5, "format_review": 10.0},
            })
            with self.subTest(mode=mode):
                rendered = render_project_update_metrics(plan)
                self.assertIn("Format review: schedule changes (subset)", rendered)
                self.assertIn("do not add them to its total again", rendered)
                self.assertIn("reads only scheduled Start/Finish", rendered)
                self.assertIn("2.500", rendered)
                self.assertIn("10.000", rendered)

    def test_html_and_generated_field_mapping_legends_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "FIELD_MAPPING.md"
            write_field_mapping(path, load_config(None))
            legends = (color_key(), path.read_text())
        for legend in legends:
            self.assertIn("including autoscheduled Start/Finish", legend)
            self.assertIn("report diagram only", legend)
            self.assertIn("Light gray: dependency review", legend)
            self.assertNotIn("overrides green", legend)
            self.assertNotIn("Blue", legend)


if __name__ == "__main__":
    unittest.main()
