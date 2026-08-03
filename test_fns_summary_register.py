import unittest

import prompts


class FounderSummaryRegisterTests(unittest.TestCase):
    def test_canonical_anchor_is_factual_and_lossless(self):
        prompt = prompts.canonical_anchor_writer_prompt(
            mode="PAID",
            user_inputs={"page_2": {"elevator_pitch": "A factual pitch."}},
            selected_template={"archetype": {"label": "Operator"}},
            paid_slide_count_value=6,
            supporting_document="The ask is $2.5M and no prototype exists.",
        )
        self.assertIn("lossless rhetorical brief", prompt)
        self.assertIn("Do not invent a", prompt)
        self.assertIn("no prototype exists", prompt)
        self.assertNotIn("supergroup", prompt)
        self.assertNotIn("presentation that actually happened", prompt)

    def test_deck_builder_only_returns_rhetorical_structure(self):
        prompt = prompts.rhetorical_deck_builder_prompt(
            deck_generation_prompt="Canonical argument.",
            slide_numbers=[1, 2, 3],
            total_slide_count=3,
        )
        self.assertIn('"rhetorical_storyboard"', prompt)
        self.assertIn("required_points", prompt)
        self.assertIn("Do not write titles", prompt)
        self.assertNotIn('"image_prompt"', prompt)
        self.assertNotIn('"visual_grammar"', prompt)

    def test_spirit_boarder_is_visual_authority_not_copy_authority(self):
        prompt = prompts.spirit_boarder_user_prompt(
            rhetorical_storyboard=[{
                "slide_number": 2,
                "rhetorical_job": "Explain the mechanism.",
                "required_points": ["One exact fact"],
            }],
            storyboard_copy=[{
                "slide_number": 2,
                "title": "How It Works",
                "body_points": ["One exact fact"],
            }],
        )
        self.assertIn("Actual images attached to this call are visual evidence", prompt)
        self.assertIn("immutable text slots", prompt)
        self.assertIn("Every image_prompt must be self-contained", prompt)
        self.assertIn("supplied with this specification as finished", prompt)
        self.assertIn("typography in those placements. Add no other visible prose", prompt)
        self.assertIn("How It Works", prompt)

    def test_fns_builds_roleplay_character_for_ghostwriter_only(self):
        prompt = prompts.founder_narrative_synthesis_prompt(
            deck_generation_prompt="Anchor narrative",
            user_inputs={"audience": "investors"},
            selected_template={"archetype": "operator"},
        )

        self.assertIn("Build a roleplay", prompt)
        self.assertIn("YOU ARE", prompt)
        self.assertIn("WHAT SHAPED YOU", prompt)
        self.assertIn("WHAT YOU NOTICE", prompt)
        self.assertIn("HOW YOU THINK", prompt)
        self.assertIn("HOW YOU TALK", prompt)
        self.assertIn("WORDS YOU USE", prompt)
        self.assertIn("YOUR RHETORICAL HABITS", prompt)
        self.assertIn("WHEN YOU ARE PUSHED", prompt)
        self.assertIn("WHEN YOU WRITE THESE SLIDES", prompt)
        self.assertIn("write the page again from a blank page", prompt)
        self.assertIn("addressed to the dedicated storyboard ghostwriter", prompt)
        self.assertIn('"ghostwriter_system_prompt"', prompt)
        self.assertIn('open with "You are..."', prompt)
        self.assertIn("maintain that subject position throughout", prompt)
        self.assertIn("The system prompt installs an identity", prompt)
        self.assertIn("The target is a person, not an optimized presenter", prompt)
        self.assertIn("ordinary words, contractions where natural", prompt)
        self.assertIn("human subjectivity, not a rhetorical framework", prompt)
        self.assertIn("The Deck Builder never receives it", prompt)
        self.assertNotIn('"archetype": "operator"', prompt)
        self.assertNotIn("Write one continuous 140-240 word summary", prompt)

    def test_deck_builder_has_hard_copy_and_space_budget(self):
        prompt = prompts.paid_deck_builder_prompt(
            deck_generation_prompt="Deck prompt",
            user_inputs={"audience": "investors"},
            selected_template={"approach": "restrained"},
            slide_count=5,
        )

        self.assertIn("COPY AND SPACE BUDGET", prompt)
        self.assertIn("at least 35%", prompt)
        self.assertIn("no more than 40 visible words", prompt)
        self.assertIn("1-3 body_points", prompt)
        self.assertIn("image_prompt contains no visible prose", prompt)
        self.assertIn("must not contain or", prompt)
        self.assertIn("repeat any visible wording", prompt)

    def test_storyboard_copy_validator_rejects_dense_slide(self):
        dense = [{
            "slide_number": 2,
            "title": "This headline contains far too many words for one readable presentation slide",
            "body_points": [
                "This first bullet is much too long to preserve any meaningful negative space",
                "This second bullet repeats the same density problem across another crowded line",
                "This third bullet ensures the total copy budget is exceeded by a lot",
                "This fourth bullet should not be permitted on a sparse investor slide",
            ],
            "image_prompt": "Use a dense matrix with paragraphs in every panel.",
        }]

        errors = prompts.storyboard_copy_errors(dense, total_slide_count=3)

        self.assertTrue(any("title" in error for error in errors))
        self.assertTrue(any("body_points" in error for error in errors))
        self.assertTrue(any("visible words" in error for error in errors))

    def test_storyboard_copy_validator_accepts_sparse_slide(self):
        sparse = [
            {"slide_number": 1, "title": "RunReady", "body_points": [], "image_prompt": "Sparse cover."},
            {
                "slide_number": 2,
                "title": "Practice before consequence",
                "body_points": [
                    "Operators rehearse the procedure before touching productive equipment",
                    "The live evaluation verifies readiness",
                ],
                "image_prompt": "Reserve 40% negative space and use the supplied copy only.",
            },
            {"slide_number": 3, "title": "Thank you", "body_points": [], "image_prompt": "Sparse close."},
        ]

        self.assertEqual([], prompts.storyboard_copy_errors(sparse, total_slide_count=3))

    def test_image_call_binds_visible_text_to_storyboard_verbatim(self):
        prompt = prompts.paid_slide_image_prompt(
            "Create a process diagram with helpful annotations.",
            title="Practice before consequence",
            body_points=["Simulation first", "Authenticated evaluation"],
        )

        self.assertIn("STORYBOARD TEXT CONTRACT — VERBATIM AND EXHAUSTIVE", prompt)
        self.assertIn('"Practice before consequence"', prompt)
        self.assertIn('"Simulation first"', prompt)
        self.assertIn('"Authenticated evaluation"', prompt)
        self.assertIn("sole authority for visible prose", prompt)
        self.assertIn("leave it unlabeled", prompt)

    def test_deck_builder_image_prompt_cannot_embed_rough_copy(self):
        storyboard = [{
            "slide_number": 2,
            "title": "Rough title",
            "body_points": ["Rough body"],
            "image_prompt": "Place the exact title 'Rough title' above the diagram.",
        }]

        errors = prompts.storyboard_embedded_copy_errors(storyboard)

        self.assertTrue(errors)
        self.assertIn("embeds visible storyboard copy", errors[0])

    def test_ghostwriter_rewrites_copy_without_replanning_storyboard(self):
        prompt = prompts.storyboard_ghostwriter_prompt(
            founder_voice="A founder-voiced summary.",
            storyboard=[{
                "slide_number": 2,
                "title": "Generic title",
                "body_points": ["Generic point"],
                "image_prompt": "A carefully planned composition.",
            }],
            total_slide_count=3,
        )

        self.assertIn("The storyboards are the rough draft for your presentation", prompt)
        self.assertIn("rewrite all the visible text they'll contain", prompt)
        self.assertIn("SYSTEM PROMPT", prompt)
        self.assertIn("USER PROMPT", prompt)
        self.assertNotIn("A carefully planned composition.", prompt)
        self.assertIn('"visible_text_capacity"', prompt)
        self.assertIn('"title_max_words": 10', prompt)
        self.assertNotIn('"image_prompt":', prompt)
        self.assertNotIn("Copy limits remain binding", prompt)
        self.assertNotIn("wholesale rewrite", prompt)

        clean_prompt = prompts.storyboard_ghostwriter_prompt(
            founder_voice="A founder system prompt.",
            storyboard=[{
                "slide_number": 2,
                "title": "Rough title",
                "body_points": ["Rough point"],
                "image_prompt": "Reserve the left column for a title text slot.",
            }],
            total_slide_count=3,
        )
        self.assertNotIn("Reserve the left column", clean_prompt)


if __name__ == "__main__":
    unittest.main()
"""Legacy prompt-register regression tests.

Some cases below intentionally exercise pre-FNS compatibility prompt APIs. They
are not current workflow/schema tests; active authority-chain coverage belongs
in the generation-worker tests.
"""
