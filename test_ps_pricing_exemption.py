#!/usr/bin/env python3
"""Re-test the Pitch Synthase v2 self-pitch, this time with the 'pricing'
inference exemption checked, 8 slides. Fresh job so the original
pd_a8f624a50b464f63 (no exemption) stays available for comparison."""
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

_handoff = json.loads(Path("/home/director/.claude/uploads/aa8045cb-c5bb-45c3-8c67-b67b8f6562d2/b6592881-pitch_synthase_v2_test_input_pricing_strategy.json").read_text())

AUDIENCE = _handoff["audience"]
ELEVATOR_PITCH = _handoff["elevator_pitch"]
CONVEYS = _handoff["conveys"]
DOC_TEXT = Path(_HERE / "PITCH_SYNTHASE_V2_SPEC.md").read_text()
ARCHETYPE_ID = "arch_copernicus"


async def main():
    job_id, _ = db.create_job(problem_need="", audience=AUDIENCE, association_words=[])
    db.update_job(
        job_id,
        elevator_pitch=ELEVATOR_PITCH,
        conveys=CONVEYS,
        doc_text=DOC_TEXT,
        selected_archetype_id=ARCHETYPE_ID,
        excepted_inference_elements=["pricing"],
    )
    print(f"Job: {job_id}  (pricing exemption: ON)", file=sys.stderr)

    await workers.approach_drafter_worker(job_id)
    job = db.get_job(job_id)
    print(f"approach status: {job.get('status')}", file=sys.stderr)
    if job.get("status") != "approaches_ready":
        print("FAILED:", job.get("error_message"), file=sys.stderr)
        return

    archetype = prompts.experiment_archetype_by_id(job["selected_archetype_id"])
    print(f"archetype: {archetype['label']} ({archetype['role']})", file=sys.stderr)
    for a in job["approach_candidates_json"]:
        print(f"\n  {a['approach_id']} :: {a['label']}", file=sys.stderr)
        print(f"    pitch_angle: {a['pitch_angle']}", file=sys.stderr)
        print(f"    key_differentiator: {a['key_differentiator']}", file=sys.stderr)

    print(f"\nJob ID for follow-up: {job_id}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
