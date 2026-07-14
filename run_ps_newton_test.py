#!/usr/bin/env python3
"""New full-pipeline test: PS-pitches-itself content, arch_newton this time
(prior run pd_3d28020fc01f4a48 used arch_copernicus). Stage 1: create job +
run approach_drafter_worker only."""
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

PRIOR_JOB_ID = "pd_3d28020fc01f4a48"
ARCHETYPE_ID = "arch_newton"


async def main():
    prior = db.get_job(PRIOR_JOB_ID)
    job_id, _ = db.create_job(
        problem_need="", audience=prior.get("audience") or "", association_words=[],
    )
    db.update_job(
        job_id,
        elevator_pitch=prior.get("elevator_pitch"),
        conveys=prior.get("conveys"),
        doc_text=prior.get("doc_text"),
        selected_archetype_id=ARCHETYPE_ID,
        excepted_inference_elements=["pricing"],
    )
    print(f"NEW_JOB_ID={job_id}", file=sys.stderr)
    archetype = prompts.experiment_archetype_by_id(ARCHETYPE_ID)
    print(f"archetype: {archetype['label']} ({archetype['role']}) -- {archetype['win_condition'][:150]}", file=sys.stderr)

    print("Running approach_drafter_worker...", file=sys.stderr)
    await workers.approach_drafter_worker(job_id)
    job = db.get_job(job_id)
    print(f"status: {job.get('status')}", file=sys.stderr)
    if job.get("status") != "approaches_ready":
        print("FAILED:", job.get("error_message"), file=sys.stderr)
        return

    for a in job["approach_candidates_json"]:
        print(f"\n  {a['approach_id']} :: {a['label']}", file=sys.stderr)
        print(f"    pitch_angle: {a['pitch_angle']}", file=sys.stderr)
        print(f"    key_differentiator: {a['key_differentiator']}", file=sys.stderr)

asyncio.run(main())
