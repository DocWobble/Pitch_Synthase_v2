import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pitch_ribotome import graph as pitch_graph
import db
from ribotome_graph import (
    Graph, GraphValidationError, Node, NodeExecutionError, PlanError, Port,
)


class RiboTomeGraphTests(unittest.TestCase):
    def test_range_plan_includes_parallel_side_dependency_and_external_cut(self):
        async def root(values):
            return {"anchor": {"x": values["source"]}}
        async def voice(values):
            return {"voice": {"from": values["anchor"]}}
        async def visual(values):
            return {"visual": {"from": values["anchor"]}}
        async def copy(values):
            return {"copy": [values["voice"], values["anchor"]]}
        async def finish(values):
            return {"deck": [values["copy"], values["visual"]]}

        graph = Graph([
            Node("anchor", {"source": Port("str")}, {"anchor": Port("dict")}, run=root),
            Node("voice", {"anchor": Port("dict")}, {"voice": Port("dict")}, ("anchor",), voice),
            Node("visual", {"anchor": Port("dict")}, {"visual": Port("dict")}, ("anchor",), visual),
            Node("copy", {"voice": Port("dict"), "anchor": Port("dict")}, {"copy": Port("list")}, ("voice",), copy),
            Node("finish", {"copy": Port("list"), "visual": Port("dict")}, {"deck": Port("list")}, ("copy", "visual"), finish),
        ])
        plan = graph.plan("anchor", "finish")
        self.assertEqual(plan.waves, (("anchor",), ("voice", "visual"), ("copy",), ("finish",)))
        self.assertEqual(set(plan.required_inputs), {"source"})

    def test_midgraph_range_reports_prior_outputs_as_external_inputs(self):
        graph = Graph([
            Node("a", {"source": Port("str")}, {"a_out": Port("str")}, run=lambda v: {"a_out": v["source"]}),
            Node("b", {"a_out": Port("str")}, {"b_out": Port("str")}, ("a",), lambda v: {"b_out": v["a_out"]}),
        ])
        plan = graph.plan("b", "b")
        self.assertEqual(set(plan.required_inputs), {"a_out"})
        self.assertEqual(plan.node_ids, ("b",))

    def test_validation_rejects_duplicate_producer_and_bad_runner_interface(self):
        with self.assertRaises(GraphValidationError):
            Graph([
                Node("a", outputs={"x": Port("str")}, run=lambda v: {"x": "a"}),
                Node("b", outputs={"x": Port("str")}, run=lambda v, extra: {"x": "b"}),
            ])

    def test_validation_rejects_dropped_internal_output(self):
        with self.assertRaisesRegex(GraphValidationError, "unconsumed output"):
            Graph([
                Node("a", outputs={"used": Port("str"), "dropped": Port("str")},
                     run=lambda v: {"used": "x", "dropped": "lost"}),
                Node("b", inputs={"used": Port("str")}, outputs={"done": Port("str")},
                     depends=("a",), run=lambda v: {"done": v["used"]}),
            ])

    def test_execution_rejects_dropped_and_undeclared_outputs(self):
        graph = Graph([
            Node("a", {"source": Port("str")}, {"x": Port("str")}, run=lambda v: {"wrong": v["source"]}),
        ])
        with self.assertRaisesRegex(NodeExecutionError, "output mismatch"):
            graph.run(graph.plan("a", "a"), {"source": "value"})

    def test_execution_rejects_undeclared_external_input_and_wrong_type(self):
        graph = Graph([
            Node("a", {"count": Port("int")}, {"x": Port("str")}, run=lambda v: {"x": str(v["count"])}),
        ])
        plan = graph.plan("a", "a")
        with self.assertRaisesRegex(NodeExecutionError, "undeclared external"):
            graph.run(plan, {"count": 1, "hidden": True})
        with self.assertRaisesRegex(NodeExecutionError, "expected int"):
            graph.run(plan, {"count": "1"})

    def test_invalid_range_is_rejected(self):
        graph = Graph([
            Node("a", outputs={"x": Port("str")}, run=lambda v: {"x": "x"}),
            Node("b", outputs={"y": Port("str")}, run=lambda v: {"y": "y"}),
        ])
        with self.assertRaises(PlanError):
            graph.plan("a", "b")

    def test_pitch_graph_executes_real_adapters_through_storyboard_without_images(self):
        inputs = {
            "audience": "pre-seed investors",
            "elevator_pitch": "A factual narrow-model pitch.",
            "association_words": ["plainspoken", "forensic", "spare"],
            "selected_approach_id": "approach_1",
            "explicit_slide_count": 4,
        }
        selected_approach = {
                "approach_id": "approach_1",
                "label": "Bounded Proof",
                "pitch_angle": "Prove one bounded model.",
                "key_differentiator": "No prototype claim.",
                "visual_direction": "Sparse industrial evidence.",
        }
        user_inputs = {
            "page_1": {"audience": inputs["audience"], "conveys": ""},
            "page_2": {
                "elevator_pitch": inputs["elevator_pitch"],
                "supporting_document_provided": False,
                "supporting_image": False,
                "optional_notes": None,
            },
        }

        async def fake_response(job_id, *, stage, **kwargs):
            if stage == "ribotome_canonical_anchor":
                payload = {"user_inputs": user_inputs, "deck_generation_prompt": "Factual brief."}
            elif stage == "ribotome_founder_narrative":
                payload = {"ghostwriter_system_prompt": "YOU are a plainspoken founder."}
            elif stage == "ribotome_visual_grammar":
                payload = {"spirit_boarder_system_prompt": "You are a sparse industrial art director."}
            elif stage == "ribotome_rhetorical_storyboard":
                payload = {"rhetorical_storyboard": [{
                    "slide_number": number,
                    "rhetorical_job": f"Job {number}",
                    "required_points": [f"Fact {number}"],
                    "logical_relationship": "sequence",
                    "information_priority": "primary",
                    "density": "edge" if number in {1, 6} else "low",
                    "layout_constraints": {
                        "title_max_words": 10, "body_points_max": 1 if number in {1, 6} else 3,
                        "body_point_max_words": 12,
                        "total_visible_words_max": 15 if number in {1, 6} else 40,
                        "negative_space_min_percent": 50 if number in {1, 6} else 35,
                    },
                } for number in range(1, 7)]}
            elif stage == "ribotome_storyboard_ghostwriter":
                payload = {"storyboard_copy": [{
                    "slide_number": number, "title": f"Point {number}",
                    "body_points": [] if number in {1, 6} else [f"Fact {number}"],
                } for number in range(1, 7)]}
            elif stage == "ribotome_spirit_boarder":
                payload = {"visual_storyboard": [{
                    "slide_number": number,
                    "composition_intent": f"Composition {number}",
                    "image_prompt": f"Sparse diagram composition number {number}; render no visible words.",
                } for number in range(1, 7)]}
            else:
                self.fail(f"unexpected stage {stage}")
            return SimpleNamespace(output_text=json.dumps(payload))

        async def fake_drafter(job_id):
            db.update_job(job_id, status="approaches_ready", approach_candidates_json=[selected_approach])

        pipeline = pitch_graph()
        plan = pipeline.plan("prepare_pitch", "spirit_boarder")
        with (
            patch("workers._responses_create", side_effect=fake_response),
            patch("workers._visual_reference_paths", return_value=[]),
            patch("workers.approach_drafter_worker", side_effect=fake_drafter),
        ):
            result = pipeline.run(plan, inputs)
        self.assertEqual(len(result.values["completed_storyboard"]), 6)
        self.assertEqual(result.values["completed_storyboard"][1]["title"], "Point 2")
        self.assertNotIn("render_slides", result.node_outputs)


if __name__ == "__main__":
    unittest.main()
