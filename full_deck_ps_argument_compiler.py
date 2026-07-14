#!/usr/bin/env python3
"""Full deck generation for the pitch_synthase_v2_argument_compiler test job,
approach_2 ("Proof Over Prompt"), 16 slides. Mirrors full_deck_petnavi.py."""
from __future__ import annotations
import asyncio, os, sys
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
import db, workers

db.init_db()

JOB_ID = "pd_a8f624a50b464f63"
APPROACH_ID = "approach_2"  # "Proof Over Prompt"
SLIDE_COUNT = 16


def _raw_user_inputs_full_pitch(job: dict) -> dict:
    return {
        "page_1": {"audience": job.get("audience") or "", "conveys": job.get("conveys") or ""},
        "page_2": {
            "elevator_pitch": job.get("elevator_pitch") or "",
            "supporting_document_provided": bool(job.get("doc_text")),
            "supporting_image": False,
            "optional_notes": None,
        },
    }


def _selected_template_payload_from_approach(job: dict, approach: dict, archetype: dict) -> dict:
    return {
        "candidate_id": approach["approach_id"],
        "focus_id": "",
        "style_label": approach["label"],
        "style_tags": [],
        "template_manifest": approach,
        "archetype": {
            "archetype_id": archetype["archetype_id"],
            "label": archetype["label"],
            "posture": archetype["posture"],
        },
    }


async def main():
    import prompts
    job = db.get_job(JOB_ID)
    approach = next(a for a in job["approach_candidates_json"] if a["approach_id"] == APPROACH_ID)
    archetype = prompts.experiment_archetype_by_id(job["selected_archetype_id"])

    print(f"Job: {JOB_ID}  Approach: {approach['label']}  Archetype: {archetype['label']}", file=sys.stderr)
    print(f"Generating {SLIDE_COUNT}-slide PAID deck...", file=sys.stderr)

    workers._raw_user_inputs = _raw_user_inputs_full_pitch
    workers._selected_template_payload = lambda j: _selected_template_payload_from_approach(j, approach, archetype)

    db.update_job(JOB_ID, status="paid", explicit_slide_count=SLIDE_COUNT, error_message=None)
    await workers.generation_worker(JOB_ID)

    job = db.get_job(JOB_ID)
    print(f"\nFinal status: {job['status']}", file=sys.stderr)
    if job.get("error_message"):
        print("error:", job["error_message"], file=sys.stderr)

    slides_dir = workers.job_dir(JOB_ID) / "slides"
    if slides_dir.exists():
        for p in sorted(slides_dir.glob("*.png")):
            print(" ", p, file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
