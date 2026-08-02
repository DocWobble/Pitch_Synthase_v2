import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import workers
import prompts


class PipelineAuthorityTests(unittest.TestCase):
    def test_finalization_resolves_paid_and_convert_storyboards(self):
        paid = [{"slide_number": 1, "title": "Paid"}]
        converted = [{"slide_number": 1, "title": "Converted"}]
        self.assertEqual(
            workers._completed_storyboard_from_plan({"paid_storyboard": paid}),
            paid,
        )
        self.assertEqual(
            workers._completed_storyboard_from_plan({
                "mode": "CONVERT",
                "convert_storyboard": converted,
            }),
            converted,
        )
        self.assertEqual(
            workers._storyboard_artifact_filename({"mode": "CONVERT"}),
            "convert_storyboard.json",
        )

    def test_default_review_values_do_not_count_as_real_replacements(self):
        entries = [{"slide_number": 2, "image_prompt": "Use proposed $50 pricing"}]
        decisions = {"pricing": True}
        inferences = {"pricing": "$50 pricing"}
        default_review = {
            "2": {
                "headline": "Unchanged",
                "body": "Unchanged body",
                "_edited": False,
            }
        }
        errors = workers._gate_check_inferred_element_decisions(
            decisions, inferences, default_review, entries
        )
        self.assertEqual(len(errors), 1)
        default_review["2"]["_edited"] = True
        self.assertEqual(
            workers._gate_check_inferred_element_decisions(
                decisions, inferences, default_review, entries
            ),
            [],
        )

    def test_reviewed_copy_can_replace_old_copy_in_render_contract(self):
        prompt = prompts.paid_slide_image_prompt(
            "Self-contained Spirit Boarder visual specification.",
            {},
            title="Reviewed headline",
            body_points=["Reviewed body"],
        )
        self.assertIn('"Reviewed headline"', prompt)
        self.assertIn('"Reviewed body"', prompt)
        self.assertNotIn("Old headline", prompt)

    def test_canonical_anchor_must_preserve_raw_inputs_exactly(self):
        raw = {"page_1": {"audience": "investors"}}
        valid = {
            "user_inputs": raw,
            "deck_generation_prompt": "Complete factual brief.",
        }
        self.assertIs(
            workers._validate_canonical_anchor_output(valid, raw), valid
        )
        with self.assertRaisesRegex(RuntimeError, "changed raw user_inputs"):
            workers._validate_canonical_anchor_output(
                {**valid, "user_inputs": {"page_1": {"audience": "buyers"}}},
                raw,
            )

    def test_rhetorical_planner_input_does_not_reopen_raw_sources(self):
        job = {
            "doc_text": "RAW SOURCE MUST NOT BE DUPLICATED",
            "elevator_pitch": "RAW ELEVATOR PITCH",
            "pitch_aspect_modes": {},
        }
        with patch.object(workers.db, "get_job", return_value=job):
            messages = workers._rhetorical_deck_builder_input(
                {
                    "deck_generation_prompt": "Canonical factual brief.",
                    "user_inputs": {"page_2": {"elevator_pitch": "preserved input"}},
                },
                "Rhetorical planning contract.",
                "job_test",
            )
        rendered = str(messages)
        self.assertIn("Canonical factual brief", rendered)
        self.assertIn("Rhetorical planning contract", rendered)
        self.assertNotIn("RAW SOURCE MUST NOT BE DUPLICATED", rendered)

    def test_render_input_separates_general_and_kept_preview_images(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            general = Path(temp_dir) / "general.png"
            kept = Path(temp_dir) / "kept.png"
            general.write_bytes(b"general")
            kept.write_bytes(b"kept")
            with (
                patch.object(
                    workers,
                    "_visual_reference_paths",
                    return_value=[general],
                ),
                patch.object(
                    workers,
                    "_image_content",
                    side_effect=lambda path: {
                        "type": "input_image",
                        "image_url": path.name,
                    },
                ),
            ):
                messages = workers._render_input(
                    "FINAL RENDER SPEC",
                    "job_test",
                    vertex_images=[kept],
                    vertex_instruction="THIS IS THE KEPT PREVIEW",
                )
        content = messages[0]["content"]
        self.assertEqual(content[0]["text"], "FINAL RENDER SPEC")
        self.assertEqual(content[2]["image_url"], "general.png")
        self.assertEqual(content[3]["text"], "THIS IS THE KEPT PREVIEW")
        self.assertEqual(content[4]["image_url"], "kept.png")


if __name__ == "__main__":
    unittest.main()
