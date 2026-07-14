#!/usr/bin/env python3
"""
Shoot image candidates for multiple jobs in one process, so the shared,
process-wide _throttle_image_generation() rate limiter (5 calls / rolling 60s)
actually gets exercised across all of them together -- the same way it would
if multiple real jobs' template workers ran concurrently in production.

Usage: python3 gen_images_batch.py <job_id>:<run_label> [<job_id>:<run_label> ...]
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
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

_START = time.monotonic()


def _t() -> str:
    return f"{time.monotonic() - _START:6.1f}s"


async def _gen_one(job_id: str, run_label: str, i: int, candidate: dict, cand_dir: Path) -> str:
    print(f"[{_t()}] {run_label} tmpl_{i} queued (waiting on throttle if needed)...", file=sys.stderr)
    await workers._throttle_image_generation()
    print(f"[{_t()}] {run_label} tmpl_{i} -> firing request", file=sys.stderr)
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
        raise RuntimeError(f"{run_label} tmpl_{i} returned {len(images)} images; expected 1")
    print(f"[{_t()}] {run_label} tmpl_{i} <- image received", file=sys.stderr)
    return images[0]


async def run_job(job_id: str, run_label: str):
    job = db.get_job(job_id)
    candidates = job["candidates_json"]
    cand_dir = workers.job_dir(job_id) / "candidates"
    cand_dir.mkdir(exist_ok=True)

    payloads = await asyncio.gather(*[
        _gen_one(job_id, run_label, i, c, cand_dir)
        for i, c in enumerate(candidates, start=1)
    ])
    for i, (b64, candidate) in enumerate(zip(payloads, candidates), start=1):
        path = cand_dir / f"template_{i}.png"
        await workers._write_image_from_b64(b64, path)
        label = candidate.get("style_label") or candidate.get("label") or ""
        print(f"  {run_label} tmpl_{i} ({label}) -> {path}")


async def main():
    specs = [arg.split(":", 1) for arg in sys.argv[1:]]
    if not specs:
        print("Usage: gen_images_batch.py <job_id>:<label> [...]", file=sys.stderr)
        sys.exit(1)

    print(f"Firing {len(specs)} job(s) x 4 candidates concurrently -- shared rate limiter, watch the stagger:", file=sys.stderr)
    await asyncio.gather(*[run_job(job_id, label) for job_id, label in specs])
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
