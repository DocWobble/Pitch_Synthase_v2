#!/usr/bin/env python3
"""Run a filled ps_test_input.schema.json object directly -- no re-derivation
of audience/elevator_pitch/conveys from looser notes. This run: the
"pitch_synthase_v2_argument_compiler" test, doc_text refreshed to the
current rev. 4 spec (was stale rev. 3 in the handed-off object)."""
from __future__ import annotations
import asyncio, json, os, sys
from pathlib import Path

_HERE = Path(__file__).parent
_INTELLURIC_ENV = Path("/home/director/intelluric/local/intelluric/intelluric-site.env")
for _line in _INTELLURIC_ENV.read_text().splitlines():
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

os.environ["INSTANT_DB_PATH"] = str(_HERE / "local_state" / "workshop.db")
os.environ["INSTANT_JOBS_DIR"] = str(_HERE / "local_state" / "jobs")
sys.path.insert(0, str(_HERE))
import db, workers, prompts

db.init_db()

TEST_INPUT = {
    "test_name": "pitch_synthase_v2_argument_compiler",
    "audience": "Seed-stage investors, strategic product leaders, and potential platform partners in generative-AI productivity software, presentation creation, creative automation, and enterprise knowledge workflows—especially evaluators who distinguish a controlled, source-grounded production system from a one-shot prompt wrapper.",
    "elevator_pitch": open("/home/director/.claude/uploads/aa8045cb-c5bb-45c3-8c67-b67b8f6562d2/b6592881-pitch_synthase_v2_test_input_pricing_strategy.json").read(),  # placeholder, overwritten below
    "conveys": "That Pitch Synthase v2 is a real, workshop-validated argument-compilation and slide-production architecture—not a concept deck, template marketplace, or thin prompt wrapper. The pitch should make the category inversion unmistakable: reliable decks come from controlling the causal chain from source material to strategic approach to visual grammar to per-slide rendering to source-checked finalization. It should land as technically credible, commercially legible, and unusually honest: show the measured 16-slide-plus-hero run, the structural fixes, exact outputs, spend-staging logic, fault isolation, and production path, while explicitly disclosing that the prototype is not yet merged and that structured outputs, clean-proof no-op handling, cross-run label uniqueness, hero source checking, and monetization placement remain open.",
    "archetype_id": "arch_copernicus",
    "target_stage": "approaches",
    "approach_id": None,
    "slide_count": 16,
    "universal_refinement_instruction": None,
}

# Load the real handed-off object properly instead of the placeholder above.
_handoff = json.loads(Path("/home/director/.claude/uploads/aa8045cb-c5bb-45c3-8c67-b67b8f6562d2/b6592881-pitch_synthase_v2_test_input_pricing_strategy.json").read_text())
TEST_INPUT["elevator_pitch"] = _handoff["elevator_pitch"]

# doc_text refreshed to the current rev. 4 spec (handed-off object had stale rev. 3).
TEST_INPUT["doc_text"] = Path(_HERE / "PITCH_SYNTHASE_V2_SPEC.md").read_text()


async def main():
    job_id, _ = db.create_job(
        problem_need="", audience=TEST_INPUT["audience"], association_words=[],
    )
    db.update_job(
        job_id,
        elevator_pitch=TEST_INPUT["elevator_pitch"],
        conveys=TEST_INPUT["conveys"],
        doc_text=TEST_INPUT["doc_text"],
        selected_archetype_id=TEST_INPUT["archetype_id"],
    )
    print(f"Job: {job_id}  test_name: {TEST_INPUT['test_name']}", file=sys.stderr)
    print(f"target_stage: {TEST_INPUT['target_stage']}", file=sys.stderr)

    if TEST_INPUT["target_stage"] == "approaches":
        await workers.approach_drafter_worker(job_id)
        job = db.get_job(job_id)
        print(f"status: {job.get('status')}", file=sys.stderr)
        if job.get("status") != "approaches_ready":
            print("FAILED:", job.get("error_message"), file=sys.stderr)
            return
        archetype = prompts.experiment_archetype_by_id(job["selected_archetype_id"])
        print(f"archetype: {archetype['label']} ({archetype['role']})", file=sys.stderr)
        for a in job["approach_candidates_json"]:
            print(f"\n  {a['approach_id']} :: {a['label']}", file=sys.stderr)
            print(f"    pitch_angle: {a['pitch_angle']}", file=sys.stderr)
            print(f"    key_differentiator: {a['key_differentiator']}", file=sys.stderr)
            print(f"    visual_direction: {a['visual_direction'][:200]}", file=sys.stderr)
        print(f"\nJob ID for follow-up: {job_id}", file=sys.stderr)
    else:
        raise NotImplementedError(f"target_stage {TEST_INPUT['target_stage']!r} not wired in this runner yet")


if __name__ == "__main__":
    asyncio.run(main())
