#!/usr/bin/env python3
"""
A/B test: the old "style contract" mechanism (prose grammar paragraph the LLM
is asked to repeat verbatim inside every independent per-slide image_prompt,
no code-level enforcement) versus the fixed mechanism (structured
visual_grammar object, chosen once, spliced into every slide's prompt in
code). Same job, same approach, same archetype, same Anchor Writer output --
only the storyboard/rendering mechanism varies. 4-slide deck, both conditions.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from textwrap import dedent

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
import db
import prompts
import workers

db.init_db()

SOURCE_JOB_ID = "pd_b39004eeefac463f"  # THGM, Firebringer
APPROACH_ID = "approach_1"  # "Ground as Antenna"
SLIDE_COUNT = 4


def naive_deck_builder_prompt(*, deck_generation_prompt, user_inputs, selected_template, slide_count):
    """Reconstructs the pre-fix mechanism: single 'storyboard' output key, no
    visual_grammar object, per-slide image_prompt expected to self-contain a
    repeated grammar paragraph via prose instruction alone. No-chrome and
    no-pagination instructions are kept (unrelated to what's being tested)."""
    numbers = list(range(1, slide_count + 1))
    artifact_request = prompts._artifact_request_block(artifact_mode="paid", slide_numbers=numbers)
    return dedent(
        f"""
        Follow this Deck Builder prompt exactly:
        {deck_generation_prompt}

        Application transport constraints:
        - Preserve raw user_inputs exactly; use them as source material, not as
          text to rewrite.
        - Do not invent traction, customers, revenue, approvals, partnerships,
          deployments, endorsements, or proof.
        - Do NOT generate any images. Return only the JSON storyboard below.

        {artifact_request}

        VISUAL GRAMMAR REQUIREMENT (style contract) -- this is the core function
        of this call: before writing any slide image_prompt, synthesize one
        coherent visual grammar for this specific deck (palette, background
        treatment, typography, grid/layout rhythm, diagram style, line weight,
        overall presentation posture). Every image_prompt must carry that same
        visual grammar explicitly and consistently -- use the exact same
        visual-grammar paragraph inside every image_prompt. Slide content may
        vary, but the visual system must not. A reader looking at the generated
        images must be unable to tell they came from separate calls.

        PAGINATION REQUIREMENT: every image_prompt must instruct that this slide
        has no page number, slide count, or pagination indicator anywhere in the
        image, on the slide canvas or in any chrome.

        Output format:
        - Emit a JSON object with exactly one top-level key: "storyboard".
        - "storyboard" must contain exactly {len(numbers)} objects, one per
          requested slide, in ascending slide_number order.
        - The slide_number values must be exactly {json.dumps(numbers)}.
        - Each storyboard object must have slide_number, title, purpose,
          style_tags, and image_prompt.
        - image_prompt must be a complete, self-contained image generation prompt
          for that requested slide artifact, and must itself restate the shared
          visual grammar paragraph identically every time.

        Raw user_inputs:
        {prompts._json(user_inputs)}

        Selected visual direction metadata:
        {prompts._json(selected_template)}

        JSON shape:
        {{
          "storyboard": [
            {{"slide_number": {numbers[0]}, "title": "...", "purpose": "...", "style_tags": ["..."], "image_prompt": "..."}}
          ]
        }}
        """
    ).strip()


async def main():
    job = db.get_job(SOURCE_JOB_ID)
    approach = next(a for a in job["approach_candidates_json"] if a["approach_id"] == APPROACH_ID)
    archetype = prompts.experiment_archetype_by_id(job["selected_archetype_id"])
    print(f"Basis: {approach['label']} / {archetype['label']}", file=sys.stderr)

    user_inputs = {
        "page_1": {"audience": job.get("audience") or "", "conveys": job.get("conveys") or ""},
        "page_2": {
            "elevator_pitch": job.get("elevator_pitch") or "",
            "supporting_document_provided": bool(job.get("doc_text")),
            "supporting_image": False,
            "optional_notes": None,
        },
    }
    selected_template = {
        "candidate_id": approach["approach_id"],
        "style_label": approach["label"],
        "style_tags": [],
        "template_manifest": approach,
        "archetype": {"archetype_id": archetype["archetype_id"], "label": archetype["label"], "posture": archetype["posture"]},
    }

    # One shared Anchor Writer call -- held constant across both conditions.
    anchor_job_id = "compare_style_contract_anchor"
    anchor_prompt = prompts.anchor_writer_prompt(
        mode="PAID", user_inputs=user_inputs, selected_template=selected_template,
        paid_slide_count_value=SLIDE_COUNT, supporting_document=job.get("doc_text"), supporting_image=False,
    )
    anchor_resp = await workers._responses_create(
        anchor_job_id, stage="compare_anchor", model=prompts.ANCHOR_MODEL,
        input=workers._anchor_input(anchor_prompt, anchor_job_id),
    )
    anchor_json = workers._parse_model_json(workers._response_text(anchor_resp))
    deck_generation_prompt = anchor_json["deck_generation_prompt"]
    print("Anchor writer done.", file=sys.stderr)

    async def run_condition(condition_name: str, use_naive: bool):
        job_id = f"compare_style_{condition_name}"
        slides_dir = workers.job_dir(job_id) / "slides"
        slides_dir.mkdir(parents=True, exist_ok=True)

        if use_naive:
            storyboard_prompt_text = naive_deck_builder_prompt(
                deck_generation_prompt=deck_generation_prompt, user_inputs=user_inputs,
                selected_template=selected_template, slide_count=SLIDE_COUNT,
            )
        else:
            storyboard_prompt_text = prompts.paid_deck_builder_prompt(
                deck_generation_prompt=deck_generation_prompt, user_inputs=user_inputs,
                selected_template=selected_template, slide_count=SLIDE_COUNT,
            )

        storyboard_resp = await workers._responses_create(
            job_id, stage="compare_storyboard", model=prompts.DECK_DRAFTER_MODEL,
            input=workers._deck_drafter_input(
                workers._anchor_payload(anchor_json, focused_prompt=storyboard_prompt_text), job_id,
            ),
        )
        storyboard_json = workers._parse_model_json(workers._response_text(storyboard_resp))
        (workers.job_dir(job_id) / "storyboard.json").write_text(json.dumps(storyboard_json, indent=2))
        image_prompts = {int(item["slide_number"]): item["image_prompt"] for item in storyboard_json["storyboard"]}
        visual_grammar = storyboard_json.get("visual_grammar") or {}
        print(f"[{condition_name}] visual_grammar: {json.dumps(visual_grammar)}", file=sys.stderr)

        async def gen_slide(idx: int):
            gp = prompts.paid_slide_image_prompt(image_prompts[idx], None if use_naive else visual_grammar)
            await workers._throttle_image_generation()
            resp = await workers._responses_create(
                job_id, stage=f"compare_slide_{idx}", model=prompts.DECK_DRAFTER_MODEL,
                input=workers._deck_drafter_input(
                    workers._anchor_payload(anchor_json, focused_prompt=gp, slide_number=idx), job_id,
                ),
                tools=[prompts.image_generation_tool(stage="paid")],
                output_path=(slides_dir / f"slide_{idx:02d}.png").relative_to(workers.job_dir(job_id)),
            )
            images = workers._response_images(resp)
            if images:
                await workers._write_image_from_b64(images[0], slides_dir / f"slide_{idx:02d}.png")
                print(f"[{condition_name}] slide {idx} ok", file=sys.stderr)
            else:
                print(f"[{condition_name}] slide {idx} FAILED (no image)", file=sys.stderr)

        await asyncio.gather(*[gen_slide(i) for i in range(1, SLIDE_COUNT + 1)])

    await run_condition("contract", use_naive=True)
    await run_condition("fixed", use_naive=False)
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
