"""
A/B test: anchor writer with old vs. new creative director section.

Flow:
  1. Create a real job per pitch via the ribotome API (already running on :8793).
  2. Wait for approach_draft to complete (polls stage_states).
  3. Auto-select the first approach candidate.
  4. Set explicit_slide_count directly in DB (no generation triggered).
  5. Extract real user_inputs + selected_template via the bridge functions.
  6. Fire 2 anchor calls in parallel (old/new prompt) per pitch.
  7. Write results to /tmp/anchor_ab/.

Three pitches × 2 variants = 6 anchor calls total.
"""

import asyncio
import json
import os
import sys
import textwrap
import time
from pathlib import Path

import requests
from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).parent))
# ribotome sets INSTANT_DB_PATH at import time — must come before db import
from ribotome import (
    _raw_user_inputs_full_pitch,
    _selected_template_payload_for_approach,
)
import db as _db
import prompts

RIBOTOME = "http://localhost:8793"
OUT = Path("/tmp/anchor_ab")
OUT.mkdir(exist_ok=True)
SLIDE_COUNT = 8
POLL_INTERVAL = 5     # seconds between status polls
POLL_TIMEOUT  = 120   # seconds before giving up on approach_draft

# ── Old creative director section (verbatim from before this session) ─────────
OLD_DESIGNER_SECTION = textwrap.dedent("""\
        The supergroup must always include one member who is the presentation's own
        creative director — the person who actually built this deck
        by hand. Cast them the same way as every other member: purposeful and
        non-interchangeable, typecast to this specific pitch, embodying the
        archetype's visual and rhetorical logic rather than generic design-agency
        polish. This is the person whose taste and craft the deck's visuals belong
        to; the presentation was designed and produced by them, not generated.
        If a product_design_philosophy is provided in the input packet, it describes
        the specific design logic this team developed for the physical object —
        the material reasoning, geometric philosophy, and aesthetic priorities that
        shaped how the product looks. Anchor the creative director's
        background, sensibility, and choices in that design philosophy. Let it
        determine what they cared about when building the deck's visual language,
        and make it legible — without naming it as a design system or framework —
        through their described choices and point of view.\
""")

# Sentinel that marks the START and END of the new section in the rendered prompt
NEW_SECTION_START = "        This member requires a specific biography across three dimensions:"
# The new section ends just before "Once that team exists"
NEW_SECTION_NEXT  = "\n        Once that team exists"

# ── Pitch definitions ──────────────────────────────────────────────────────────
PITCHES = {
    "luxury": {
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
    },
    "fintech": {
        "archetype_id": "arch_morgan",
        "audience": (
            "Series A fintech VCs, compliance-aware, in a formal Sand Hill "
            "Road meeting room."
        ),
        "conveys": (
            "Underbanked small businesses cannot access working capital. "
            "Traditional yield products for retail investors return under 5%. "
            "There is an untapped arbitrage between DeFi yield generation "
            "and traditional credit demand."
        ),
        "elevator_pitch": (
            "A regulated digital-asset yield platform that deploys user "
            "deposits into a proprietary basket of yield-bearing tokens "
            "(internally developed, audited by a third party we selected), "
            "then uses the stable-yield tranche of that basket as collateral "
            "to underwrite micro-credit lines to underbanked SMBs at 18% APR. "
            "Users see a 7–9% annual yield on their deposits. "
            "The platform earns the spread."
        ),
    },
    "gizmo": {
        "archetype_id": "arch_barnum",
        "audience": (
            "Shark Tank panel and live studio audience. One shark is known "
            "to have passed on a similar concept three years ago."
        ),
        "conveys": (
            "The fidget-toy category commoditized instantly after 2017. "
            "There is no collectible-mechanic product in the physical toy "
            "aisle that combines genuine gameplay (spinner performance "
            "variance by configuration) with gacha-style acquisition."
        ),
        "elevator_pitch": (
            "A collectible fidget-spinner system sold in sealed blind packs. "
            "Each pack contains a randomized subset of parts to assemble "
            "a single spinner. Rarer configurations include counterweighted "
            "arms, LED cores, and magnetic bearing upgrades that are "
            "measurably superior in spin time and balance. A standard pack "
            "retails at $6.99. A complete set requires 24 unique parts across "
            "6 rarity tiers. The product is already in 340 Target locations "
            "and doing $2.1M in monthly GMV at 4 months post-launch."
        ),
    },
}


# ── Pipeline helpers ───────────────────────────────────────────────────────────

def create_job(pitch_key: str) -> str:
    cfg = PITCHES[pitch_key]
    r = requests.post(f"{RIBOTOME}/api/jobs", json={
        "audience":    cfg["audience"],
        "conveys":     cfg["conveys"],
        "elevator_pitch": cfg["elevator_pitch"],
        "archetype_id": cfg["archetype_id"],
    }, timeout=10)
    r.raise_for_status()
    job_id = r.json()["job_id"]
    print(f"  [{pitch_key}] job created: {job_id}")
    return job_id


def wait_for_approach_draft(job_id: str, pitch_key: str) -> None:
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        states = _db.get_stage_states(job_id)
        state = states.get("approach_draft", "pending")
        if state == "completed":
            print(f"  [{pitch_key}] approach_draft completed")
            return
        if state == "failed":
            raise RuntimeError(f"approach_draft failed for {job_id}")
        print(f"  [{pitch_key}] approach_draft: {state} — waiting ...")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"approach_draft timed out for {job_id} after {POLL_TIMEOUT}s")


def select_first_approach(job_id: str, pitch_key: str) -> None:
    job = _db.get_job(job_id)
    approaches = job.get("approach_candidates_json") or []
    if not approaches:
        raise RuntimeError(f"No approach candidates found for {job_id}")
    first = approaches[0]
    approach_id = first.get("approach_id") or first.get("candidate_id") or ""
    arch_id = first.get("effective_archetype_id") or job.get("selected_archetype_id") or ""
    arch = prompts.archetype_by_id(arch_id) or {}
    r = requests.post(f"{RIBOTOME}/api/jobs/{job_id}/select-approach", json={
        "approach_id":     approach_id,
        "approach_label":  first.get("label") or first.get("style_label") or "",
        "archetype_id":    arch_id,
        "archetype_label": arch.get("label") or "",
    }, timeout=10)
    r.raise_for_status()
    print(f"  [{pitch_key}] approach selected: {approach_id!r}")


def set_slide_count(job_id: str) -> None:
    _db.update_job(job_id, explicit_slide_count=SLIDE_COUNT)


def get_real_inputs(job_id: str) -> tuple[dict, dict, str | None]:
    job = _db.get_job(job_id)
    user_inputs     = _raw_user_inputs_full_pitch(job)
    selected_tmpl   = _selected_template_payload_for_approach(job)
    approach_id     = job.get("selected_candidate_id") or ""
    approach        = next(
        (a for a in (job.get("approach_candidates_json") or [])
         if a.get("approach_id") == approach_id),
        None,
    )
    design_philosophy = (approach or {}).get("design_philosophy") or None
    return user_inputs, selected_tmpl, design_philosophy


# ── Prompt construction ────────────────────────────────────────────────────────

def build_prompt(
    user_inputs: dict,
    selected_template: dict,
    design_philosophy: str | None,
    variant: str,
) -> str:
    prompt_str = prompts.anchor_writer_prompt(
        mode="PAID",
        user_inputs=user_inputs,
        selected_template=selected_template,
        paid_slide_count_value=SLIDE_COUNT,
        design_philosophy=design_philosophy,
    )
    if variant == "old":
        start_idx = prompt_str.find(NEW_SECTION_START)
        once_idx  = prompt_str.find(NEW_SECTION_NEXT, start_idx)
        if start_idx == -1 or once_idx == -1:
            raise ValueError("Could not locate new designer section boundaries in prompt")
        prompt_str = (
            prompt_str[:start_idx]
            + OLD_DESIGNER_SECTION
            + "\n"
            + prompt_str[once_idx:]
        )
    return prompt_str


# ── API call ───────────────────────────────────────────────────────────────────

async def call_anchor(
    client: AsyncOpenAI,
    pitch_key: str,
    variant: str,
    user_inputs: dict,
    selected_template: dict,
    design_philosophy: str | None,
) -> dict:
    prompt = build_prompt(user_inputs, selected_template, design_philosophy, variant)
    resp = await client.responses.create(
        model=prompts.ANCHOR_MODEL,
        input=[{"role": "user", "content": prompt}],
        text={"format": {"type": "json_object"}},
    )
    raw = resp.output[0].content[0].text
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"raw": raw, "error": "json_decode_failed"}
    return {"pitch": pitch_key, "variant": variant, "result": result}


# ── Runner ─────────────────────────────────────────────────────────────────────

async def main():
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # Step 1: Create all three jobs (sequentially to avoid rate limit bursts)
    print("\n=== Step 1: Creating jobs and waiting for approach drafts ===")
    job_ids: dict[str, str] = {}
    for pitch_key in PITCHES:
        job_ids[pitch_key] = create_job(pitch_key)

    # Step 2: Wait for all three approach_drafts (concurrent polls)
    print("\n=== Step 2: Waiting for approach drafts ===")
    for pitch_key, job_id in job_ids.items():
        wait_for_approach_draft(job_id, pitch_key)

    # Step 3: Select first approach and set slide count for each job
    print("\n=== Step 3: Selecting approaches ===")
    for pitch_key, job_id in job_ids.items():
        select_first_approach(job_id, pitch_key)
        set_slide_count(job_id)

    # Step 4: Extract real pipeline inputs
    print("\n=== Step 4: Extracting real pipeline inputs ===")
    pitch_inputs: dict[str, tuple] = {}
    for pitch_key, job_id in job_ids.items():
        user_inputs, selected_template, design_philosophy = get_real_inputs(job_id)
        pitch_inputs[pitch_key] = (user_inputs, selected_template, design_philosophy)
        arch = selected_template.get("archetype", {})
        tmpl = selected_template.get("template_manifest") or {}
        print(f"  [{pitch_key}] archetype={arch.get('archetype_id')!r} "
              f"approach={tmpl.get('approach_id') or tmpl.get('label')!r} "
              f"design_philosophy={'yes' if design_philosophy else 'no'}")

    # Step 5: Fire 6 anchor calls in parallel
    print(f"\n=== Step 5: Firing 6 anchor calls (3 pitches × old/new) ===")
    tasks = [
        call_anchor(
            client, pitch_key, variant,
            pitch_inputs[pitch_key][0],
            pitch_inputs[pitch_key][1],
            pitch_inputs[pitch_key][2],
        )
        for pitch_key in PITCHES
        for variant in ("old", "new")
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Step 6: Write results
    print("\n=== Step 6: Results ===")
    for r in results:
        if isinstance(r, Exception):
            print(f"  ERROR: {r}")
            continue
        pitch, variant = r["pitch"], r["variant"]
        out_path = OUT / f"{pitch}_{variant}.json"
        out_path.write_text(json.dumps(r["result"], indent=2))
        dgp = r["result"].get("deck_generation_prompt", "")
        preview_path = OUT / f"{pitch}_{variant}_prompt.txt"
        preview_path.write_text(dgp)
        print(f"  [{pitch}/{variant}] {len(dgp)} chars → {out_path.name}")

    print(f"\nAll results in {OUT}/")


if __name__ == "__main__":
    asyncio.run(main())
