"""
LEGACY A/B HARNESS — not an end-to-end test of the active FNS_test workflow.

Deck A/B test: slides from OLD vs NEW anchor writer.

Uses pre-computed anchor DGPs from /tmp/anchor_ab/luxury_{old,new}.json.
Both variants run against the SAME job so that user_inputs, selected_template,
approach candidates, and vibe_semantics are identical — the only variable is
the anchor output passed to VGS + deck builder + image gen.

Output: /tmp/deck_ab/old/slide_NN.png, /tmp/deck_ab/new/slide_NN.png

This intentionally exercises the retired fictional-Anchor -> VGS -> copy/image-
authoring Deck Builder path. Current workflow validation must use
generation_worker's Canonical Anchor -> parallel FNS/VGS -> rhetorical Deck
Builder -> Ghostwriter -> Spirit Boarder -> exact-copy renderer chain.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
# ribotome must be imported first to set INSTANT_DB_PATH
from ribotome import _raw_user_inputs_full_pitch, _selected_template_payload_for_approach
import db as _db
import prompts
from workers import (
    _responses_create,
    _anchor_payload,
    _deck_drafter_input,
    _chunked_gather,
    _write_image_from_b64,
    _response_images,
    _response_text,
    _parse_model_json,
    job_dir,
    _throttle_image_generation,
)

RIBOTOME      = "http://localhost:8793"
OUT           = Path("/tmp/deck_ab")
SLIDE_COUNT   = 4   # content slides; deck_builder_slide_count adds cover + outro = 6 total
POLL_INTERVAL = 5
POLL_TIMEOUT  = 180

# Single shared job — both variants read identical user_inputs + selected_template.
# Reused from the luxury A/B test run; approach already selected.
SHARED_JOB_ID = "pd_6d86385f57144727"

LUXURY = {
    "archetype_id": "arch_chanel",
    "audience": (
        "Seed-round fashion investors and high-net-worth individual "
        "collectors at a private industry preview."
    ),
    "conveys": (
        "Luxury fashion credibility has been hollowed out by heritage "
        "brands licensing their names to mainstream retail. True scarcity — "
        "not artificial numbered editions of mass production — no longer "
        "exists at accessible price points."
    ),
    "elevator_pitch": (
        "A luxury streetwear label that manufactures its core pieces "
        "exclusively from proprietary deadstock industrial fabrics — fire "
        "retardant Nomex, ballistic-grade Dyneema composites, and surplus "
        "military canvas — in runs of under 200 units per style, each "
        "numbered and never restocked. The construction methods (bonded "
        "seams, laser-cut edges, structural quilting) require CNC equipment "
        "unavailable to fast-fashion supply chains, making meaningful "
        "imitation structurally impossible at consumer price points."
    ),
}


def ensure_job() -> str:
    """Return SHARED_JOB_ID if it exists with approach selected; else create and prepare one."""
    job = _db.get_job(SHARED_JOB_ID)
    if job and job.get("selected_candidate_id"):
        _db.update_job(SHARED_JOB_ID, explicit_slide_count=SLIDE_COUNT)
        print(f"  Reusing existing job {SHARED_JOB_ID}")
        return SHARED_JOB_ID

    print("  Shared job not found or approach not selected — creating fresh job...")
    r = requests.post(f"{RIBOTOME}/api/jobs", json={
        "audience":       LUXURY["audience"],
        "conveys":        LUXURY["conveys"],
        "elevator_pitch": LUXURY["elevator_pitch"],
        "archetype_id":   LUXURY["archetype_id"],
    }, timeout=10)
    r.raise_for_status()
    job_id = r.json()["job_id"]

    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        states = _db.get_stage_states(job_id)
        state = states.get("approach_draft", "pending")
        if state == "completed":
            break
        if state == "failed":
            raise RuntimeError(f"approach_draft failed for {job_id}")
        print(f"  [{job_id[-8:]}] approach_draft: {state} ...")
        time.sleep(POLL_INTERVAL)
    else:
        raise TimeoutError(f"approach_draft timed out for {job_id}")

    fresh_job = _db.get_job(job_id)
    approaches = fresh_job.get("approach_candidates_json") or []
    first = approaches[0]
    approach_id = first.get("approach_id") or ""
    arch_id = first.get("effective_archetype_id") or fresh_job.get("selected_archetype_id") or ""
    arch = prompts.archetype_by_id(arch_id) or {}
    requests.post(f"{RIBOTOME}/api/jobs/{job_id}/select-approach", json={
        "approach_id":     approach_id,
        "approach_label":  first.get("label") or "",
        "archetype_id":    arch_id,
        "archetype_label": arch.get("label") or "",
    }, timeout=10).raise_for_status()
    _db.update_job(job_id, explicit_slide_count=SLIDE_COUNT)
    print(f"  Created and prepared job {job_id}")
    return job_id


async def run_phases_1b_2_3(job_id: str, anchor_json: dict, label: str) -> None:
    """Phase 1b (VGS) + Phase 2 (deck builder) + Phase 3 (image gen).

    Both variants share job_id so all DB-sourced inputs are identical.
    The only variable is anchor_json.
    """

    total_slides = prompts.deck_builder_slide_count(explicit_slide_count=SLIDE_COUNT)
    deck_generation_prompt = anchor_json["deck_generation_prompt"]

    strategy_dir = job_dir(job_id) / "strategy"
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / f"anchor_writer_output_{label}.json").write_text(json.dumps(anchor_json, indent=2))

    # ── Phase 1b: Visual Grammar Synthesizer ──────────────────────────────────
    try:
        vg_prompt = prompts.visual_grammar_prompt(deck_generation_prompt)
        vg_resp = await _responses_create(
            job_id,
            stage=f"{label}_visual_grammar",
            model=prompts.VISUAL_GRAMMAR_MODEL,
            input=[{"role": "user", "content": [{"type": "input_text", "text": vg_prompt}]}],
        )
        vg_result = _parse_model_json(_response_text(vg_resp))
        addendum = (vg_result.get("visual_grammar_addendum") or "").strip()
        if addendum:
            deck_generation_prompt = deck_generation_prompt + "\n\n" + addendum
            anchor_json = {**anchor_json, "deck_generation_prompt": deck_generation_prompt}
        (strategy_dir / f"visual_grammar_{label}.json").write_text(json.dumps(vg_result, indent=2))
        print(f"  [{label}] VGS done — {len(addendum)} char addendum")
    except Exception as e:
        print(f"  [{label}] VGS failed (proceeding with anchor alone): {e}")

    # ── Phase 2: Deck builder storyboard ──────────────────────────────────────
    # Both variants read from the same job record — user_inputs and
    # selected_template are identical, anchor_json is the only variable.
    job = _db.get_job(job_id)
    user_inputs   = _raw_user_inputs_full_pitch(job)
    selected_tmpl = _selected_template_payload_for_approach(job)

    deck_builder_prompt_text = prompts.paid_deck_builder_prompt(
        deck_generation_prompt=deck_generation_prompt,
        user_inputs=user_inputs,
        selected_template=selected_tmpl,
        slide_count=total_slides,
        excepted_inference_elements=["pricing"],
    )

    storyboard_resp = await _responses_create(
        job_id,
        stage=f"{label}_storyboard",
        model=prompts.DECK_DRAFTER_MODEL,
        input=_deck_drafter_input(
            _anchor_payload(anchor_json, focused_prompt=deck_builder_prompt_text),
            job_id,
        ),
        output_path=(strategy_dir / f"storyboard_{label}.json").relative_to(job_dir(job_id)),
    )
    storyboard_json = _parse_model_json(_response_text(storyboard_resp))
    paid_storyboard = storyboard_json.get("storyboard") or []
    image_prompts   = {int(item["slide_number"]): item["image_prompt"] for item in paid_storyboard}
    visual_grammar  = storyboard_json.get("visual_grammar") or {}
    (strategy_dir / f"storyboard_{label}.json").write_text(json.dumps(storyboard_json, indent=2))
    print(f"  [{label}] Storyboard: {len(paid_storyboard)} slides "
          f"({', '.join(item.get('title', '?') for item in paid_storyboard[:3])}…)")

    # ── Phase 3: Image generation ─────────────────────────────────────────────
    slides_dir = OUT / label
    slides_dir.mkdir(parents=True, exist_ok=True)

    async def _gen_slide(slide_num: int):
        storyboard_item = next(
            item for item in paid_storyboard
            if int(item.get("slide_number") or 0) == slide_num
        )
        img_prompt = prompts.paid_slide_image_prompt(
            image_prompts.get(slide_num, ""),
            visual_grammar,
            title=str(storyboard_item.get("title") or ""),
            body_points=storyboard_item.get("body_points") or [],
        )
        out_path   = slides_dir / f"slide_{slide_num:02d}.png"
        await _throttle_image_generation()
        resp = await _responses_create(
            job_id,
            stage=f"{label}_slide_{slide_num}",
            model=prompts.DECK_DRAFTER_MODEL,
            input=_deck_drafter_input(
                _anchor_payload(anchor_json, focused_prompt=img_prompt, slide_number=slide_num),
                job_id,
            ),
            tools=[prompts.image_generation_tool(stage="paid")],
            output_path=(strategy_dir / f"{label}_slide_{slide_num:02d}.json").relative_to(job_dir(job_id)),
        )
        images = _response_images(resp)
        if images:
            await _write_image_from_b64(images[0], out_path)
            print(f"  [{label}] slide_{slide_num:02d} saved")
        else:
            print(f"  [{label}] slide_{slide_num:02d} — no image returned")
        return slide_num

    await _chunked_gather([_gen_slide(n) for n in range(1, total_slides + 1)])
    print(f"  [{label}] complete → {slides_dir}/")


async def main():
    OUT.mkdir(exist_ok=True)

    old_anchor = json.loads(Path("/tmp/anchor_ab/luxury_old.json").read_text())
    new_anchor = json.loads(Path("/tmp/anchor_ab/luxury_new.json").read_text())

    job_id = ensure_job()
    print(f"  explicit_slide_count set to {SLIDE_COUNT}")

    print("\nRunning old and new variants in parallel (VGS + storyboard + images)...")
    await asyncio.gather(
        run_phases_1b_2_3(job_id, old_anchor, "old"),
        run_phases_1b_2_3(job_id, new_anchor, "new"),
    )

    print(f"\nDone. Slides in {OUT}/")
    for label in ("old", "new"):
        slides = sorted((OUT / label).glob("slide_*.png"))
        print(f"  [{label}] {len(slides)} slides: {[s.name for s in slides]}")


if __name__ == "__main__":
    asyncio.run(main())
