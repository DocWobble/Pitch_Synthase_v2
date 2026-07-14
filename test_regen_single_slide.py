#!/usr/bin/env python3
"""Test ONLY the regeneration call for slide 1, with the anchor-context fix,
reusing the disposition already on record from the last full run (same
corrective_constraints) so this costs exactly one image call, not a full
verify+judge+regen pass. Confirms whether wrapping the regen prompt through
_deck_drafter_input/_anchor_payload keeps it grounded in the AI-companion-
console concept instead of drifting to an unrelated product (e.g. the GPS
hiking-nav content seen without this fix)."""
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
JOB_ID = "pd_5072b5fa2fdb4ab2"

# Corrective constraints from the last real judge call on slide 1 (recorded
# in verification_report.json) -- reused here rather than re-running the
# verifiers/judge, since only the regen call itself is under test.
CORRECTIVE_CONSTRAINTS = """- The subtitle under the main headline must read exactly: "Working architecture • manufacturing + certification capital".
- In the dialogue log first system message at [00:00], the text must read exactly: "PetNavi online. Listening...".
- In the Events panel, the bullet must read exactly: "Kokoro-lite on CPU".
- Use the GPU text exactly as: "RTX 3060 6GB" everywhere on the slide; do not use a hyphenated form.
- Remove the unsupported storage reading "128 GB free" and replace it with source-accurate 16GB prototype storage context.
- The bottom proof banner must read exactly: "THE RAISE IS FOR HARDWARE PROTOTYPING, CERTIFICATION, AND INITIAL PRODUCTION — NOT CONCEPT VALIDATION."
- For the top-right callout chip labeled "manufacturing + certification capital", use an icon that represents both manufacturing and certification (for example, factory plus checkmark/badge), or otherwise ensure the icon semantics match the label."""


async def main():
    job = db.get_job(JOB_ID)
    plan = job.get("deck_proof_plan") or {}
    anchor_json = plan.get("anchor_writer_output") or {}
    storyboard_entry = next(s for s in plan["paid_storyboard"] if s["slide_number"] == 1)
    original_image_prompt = storyboard_entry["image_prompt"]

    strategy_path = workers.job_dir(JOB_ID) / "strategy" / "paid_storyboard.json"
    visual_grammar = (json.loads(strategy_path.read_text()) or {}).get("visual_grammar") or {}

    correction_block = (
        f"MANDATORY CORRECTIONS -- apply exactly, everything else in this "
        f"prompt is otherwise unchanged:\n{CORRECTIVE_CONSTRAINTS}"
    )
    regen_prompt = prompts.paid_slide_image_prompt(
        f"{original_image_prompt}\n\n{correction_block}", visual_grammar,
    )

    test_out = workers.job_dir(JOB_ID) / "slides" / "slide_01_regen_test.png"

    await workers._throttle_image_generation()
    resp = await workers._responses_create(
        JOB_ID, stage="test_regen_slide_1", model=prompts.DECK_DRAFTER_MODEL,
        input=workers._deck_drafter_input(
            workers._anchor_payload(anchor_json, focused_prompt=regen_prompt, slide_number=1),
            JOB_ID,
        ),
        tools=[prompts.image_generation_tool(stage="paid")],
        output_path=test_out.relative_to(workers.job_dir(JOB_ID)),
    )
    images = workers._response_images(resp)
    if images:
        await workers._write_image_from_b64(images[0], test_out)
        print("wrote", test_out, file=sys.stderr)
    else:
        print("NO IMAGE RETURNED", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
