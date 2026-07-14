#!/usr/bin/env python3
"""
Targeted finalization for guiclid: run the refiner exactly ONCE, cache its
per-slide extraction prompts to disk, then extract each slide's final image
exactly once. On the next run, if the cached prompts exist, skip the refiner
call and skip any slide that already has a final PNG on disk -- so a failing
slide (slide 6, historically) can be retried in isolation without re-paying
for the refiner call or the 7 slides that already succeeded.
"""
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
JOB_ID = "pd_5e121a3bd64f4a61"
ONLY_SLIDES = {int(a) for a in sys.argv[1:]} or None  # e.g. `python finalize_targeted.py 6`

job_dir = workers.job_dir(JOB_ID)
slides_dir = job_dir / "slides"
export_dir = job_dir / "export"
export_slides_dir = export_dir / "slides"
export_dir.mkdir(exist_ok=True)
export_slides_dir.mkdir(exist_ok=True)
cache_path = job_dir / "strategy" / "finalize_extraction_prompts_cache.json"


async def get_extraction_prompts(job, slide_specs) -> dict[int, str]:
    if cache_path.exists():
        print("Using cached extraction prompts from", cache_path, file=sys.stderr)
        return {int(k): v for k, v in json.loads(cache_path.read_text()).items()}

    reviewed_slides = job.get("reviewed_slides") or {}
    refiner_content = [
        {"type": "input_text", "text": prompts.finalize_refiner_prompt(
            slide_specs, reviewed_slides, job.get("universal_refinement_instruction"),
        )},
    ]
    for spec in slide_specs:
        idx = spec["slide_index"]
        proof_path = slides_dir / f"slide_{idx:02d}_proof.png"
        if proof_path.exists():
            refiner_content.append(workers._image_content(proof_path))

    refiner_resp = await workers._responses_create(
        JOB_ID, stage="finalize_refiner_targeted", model=prompts.ANCHOR_MODEL,
        input=[{"role": "user", "content": refiner_content}],
    )
    manifest = workers._parse_model_json(workers._response_text(refiner_resp))
    extraction_prompts = {int(e["slide_index"]): e["image_model_prompt"] for e in manifest.get("slide_prompts") or []}
    cache_path.write_text(json.dumps(extraction_prompts, indent=2))
    print("Cached fresh extraction prompts to", cache_path, file=sys.stderr)
    return extraction_prompts


async def extract_one(idx: int, gen_prompt: str, attempts: int = 3) -> bool:
    proof_path = slides_dir / f"slide_{idx:02d}_proof.png"
    final_path = export_slides_dir / f"slide_{idx:02d}_final.png"
    last_resp = None
    for attempt in range(attempts):
        await workers._throttle_image_generation()
        resp = await workers._responses_create(
            JOB_ID, stage=f"finalize_slide_{idx}_targeted",
            model=prompts.DECK_DRAFTER_MODEL,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": gen_prompt},
                workers._image_content(proof_path),
            ]}],
            tools=[prompts.image_generation_tool(stage="paid")],
            output_path=final_path.relative_to(job_dir),
        )
        last_resp = resp
        images = workers._response_images(resp)
        if images:
            await workers._write_image_from_b64(images[0], final_path)
            print(f"slide {idx}: OK on attempt {attempt + 1}", file=sys.stderr)
            return True
        # Diagnostic dump -- what did the model actually return instead of an image?
        output_types = [getattr(item, "type", "?") for item in (getattr(resp, "output", None) or [])]
        text = workers._response_text(resp)
        status = getattr(resp, "status", None)
        incomplete = getattr(resp, "incomplete_details", None)
        print(f"slide {idx}: attempt {attempt + 1} returned NO image. "
              f"response.status={status!r} incomplete_details={incomplete!r} "
              f"output_item_types={output_types} output_text={text[:300]!r}", file=sys.stderr)
    return False


async def main():
    job = db.get_job(JOB_ID)
    slide_specs = job.get("slide_specs") or []
    extraction_prompts = await get_extraction_prompts(job, slide_specs)

    targets = [s for s in slide_specs if ONLY_SLIDES is None or s["slide_index"] in ONLY_SLIDES]
    for spec in targets:
        idx = spec["slide_index"]
        final_path = export_slides_dir / f"slide_{idx:02d}_final.png"
        if final_path.exists() and ONLY_SLIDES is None:
            print(f"slide {idx}: final already exists, skipping", file=sys.stderr)
            continue
        gen_prompt = prompts.finalize_slide_image_prompt(extraction_prompts.get(idx, ""))
        ok = await extract_one(idx, gen_prompt)
        if not ok:
            print(f"slide {idx}: FAILED after all attempts", file=sys.stderr)

if __name__ == "__main__":
    asyncio.run(main())
