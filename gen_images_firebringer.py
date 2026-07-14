#!/usr/bin/env python3
"""
Shoot the 4 Firebringer trial candidates to real image_generation calls,
using the exact same call shape template_worker() uses in production:
one Responses call per candidate, image_generation tool, n=1 implicit
(the tool always returns one image), no extra parameters. The only
variable across the 4 calls is prompts.template_image_prompt(candidate)
built from that candidate's own image_prompt — label/description/doctrine
are never passed to the image call.
"""
from __future__ import annotations

import asyncio
import os
import sys
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
import db
import prompts
import workers

db.init_db()

FIREBRINGER_JOB_ID = "pd_991856652eca45c1"


async def _gen_one(job_id: str, i: int, candidate: dict, cand_dir: Path) -> str:
    await workers._throttle_image_generation()
    resp = await workers._responses_create(
        job_id,
        stage=f"template_image_{i}",
        model=prompts.DECK_DRAFTER_MODEL,
        input=prompts.template_image_prompt(candidate=candidate),
        tools=[prompts.image_generation_tool(stage="template")],
        output_path=(cand_dir / f"template_{i}.png").relative_to(workers.job_dir(job_id)),
    )
    images = workers._response_images(resp)
    if len(images) != 1:
        raise RuntimeError(f"Template image {i} returned {len(images)} images; expected 1")
    return images[0]


async def main():
    job = db.get_job(FIREBRINGER_JOB_ID)
    candidates = job["candidates_json"]
    print(f"Firing {len(candidates)} individual image calls for job {FIREBRINGER_JOB_ID}...", file=sys.stderr)

    cand_dir = workers.job_dir(FIREBRINGER_JOB_ID) / "candidates"
    cand_dir.mkdir(exist_ok=True)

    payloads = await asyncio.gather(*[
        _gen_one(FIREBRINGER_JOB_ID, i, c, cand_dir)
        for i, c in enumerate(candidates, start=1)
    ])

    for i, (b64, candidate) in enumerate(zip(payloads, candidates), start=1):
        path = cand_dir / f"template_{i}.png"
        await workers._write_image_from_b64(b64, path)
        print(f"  tmpl_{i} ({candidate['style_label']}) -> {path}")

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
