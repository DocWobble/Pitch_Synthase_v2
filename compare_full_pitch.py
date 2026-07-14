#!/usr/bin/env python3
"""
Full-pitch-first workshop prototype: test the proposed redesign where the user
gives audience + elevator pitch + "what should this convey" + optional archetype
+ optional supporting doc, ALL upfront. First output is four cheap TEXT-ONLY
strategic pitch approaches (not template images). User can regenerate any of
them. Once approved, one merged Anchor-Writer/Template-Drafter call per approach
produces ONE real single-slide preview image -- 4 total, pre-paywall by design
for this test.

Run: .venv/bin/python compare_full_pitch.py
"""
from __future__ import annotations

import asyncio
import json
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
Path(os.environ["INSTANT_JOBS_DIR"]).mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(_HERE))
import db
import prompts
import workers

db.init_db()

FIXTURE = dict(
    audience="Shark Tank (yes, the show)",
    elevator_pitch=(
        "Problem: every way to make a loud, focused acoustic pulse today needs a big "
        "power-hungry transducer, a full phased array of individually-timed electronics, "
        "or a one-shot explosive charge -- because conventional thinking says amplifying "
        "pressure needs proportionally more electrical input or external timing control. "
        "Solution: we built the first acoustic Marx amplifier -- a self-triggering "
        "pressure-topology device that charges many small acoustic chambers in parallel off "
        "a weak source, then snaps them into a phase-aligned series discharge so their "
        "pressure stacks into one focused pulse, with zero extra power and zero control "
        "electronics. A 4-stage prototype at 200 kPa drive pressure produces an 800 kPa "
        "output pulse (520 kPa with realistic losses); an 8-stage version reaches the "
        "therapeutic HIFU pressure range without any phased-array electronics."
    ),
    conveys=(
        "That this is a real, physically grounded mechanism -- not hand-wavy tech -- and "
        "that its simplicity (no electronics, no big transducer, self-triggering) is the "
        "whole unlock."
    ),
    doc_text=(
        "Prior art gap: no publication describes N>=2 acoustic compliance chambers charged "
        "in parallel from a common acoustic input, threshold-switched into series discharge, "
        "for acoustic pressure amplification without electrical conversion. Three distinct "
        "physical gate mechanisms identified: nonlinear elastic membrane, liquid cavitation "
        "onset, and nonlinear Helmholtz resonator threshold -- each providing independent "
        "claim coverage. Device is passive once charged; no external trigger or timing "
        "signal is required."
    ),
    archetype_id=None,  # blank on purpose -- let the drafter choose
)


async def run_approaches(fixture: dict) -> tuple[str, dict]:
    job_id, _ = db.create_job(
        problem_need="",  # unused in this flow; full elevator_pitch carries the content
        audience=fixture["audience"],
        association_words=[],
    )
    db.update_job(
        job_id,
        elevator_pitch=fixture["elevator_pitch"],
        conveys=fixture["conveys"],
        doc_text=fixture.get("doc_text"),
        selected_archetype_id=fixture.get("archetype_id"),
    )
    await workers.approach_drafter_worker(job_id)
    return job_id, db.get_job(job_id)


async def main():
    print("Running full-pitch approach drafter (text-only, 4 approaches)...", file=sys.stderr)
    job_id, job = await run_approaches(FIXTURE)

    print(f"job_id={job_id} status={job.get('status')}", file=sys.stderr)
    if job.get("status") != "approaches_ready":
        print("FAILED:", job.get("error_message"), file=sys.stderr)
        return

    print(f"\narchetype used: {job['selected_archetype_label']} ({job['selected_archetype_id']})")
    for a in job["approach_candidates_json"]:
        print(f"\n=== {a['approach_id']} :: {a['label']} ===")
        print("pitch_angle:", a["pitch_angle"])
        print("key_differentiator:", a["key_differentiator"])
        print("visual_direction:", a["visual_direction"])

    out_path = _HERE / "local_state" / "full_pitch_approaches.json"
    out_path.write_text(json.dumps(job["approach_candidates_json"], indent=2))
    print(f"\nSaved approaches to {out_path}", file=sys.stderr)
    print(f"job_id for next steps: {job_id}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
