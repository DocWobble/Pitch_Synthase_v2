#!/usr/bin/env python3
"""
Batch validation of the full-pitch-first design across 3 stress fixtures:

  A. gdp_polariton  -- different domain, archetype left blank (auto-pick)
  B. rf_cochlea     -- archetype FIXED by user (Firebringer), not yet tested
  C. thin_marx      -- deliberately thin elevator pitch, to check whether the
                       fix is really about timing (real content, early) or
                       just relocates the density problem without solving it

Runs approach drafting -> 4 single-slide previews for each, sequentially
(to keep telemetry/job dirs easy to inspect), and prints a compact summary
plus saves full detail to local_state/full_pitch_batch_report.json.
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

FIXTURES = {
    "gdp_polariton": dict(
        audience="deep-tech and advanced-manufacturing investors, quarrying/demolition industrials",
        elevator_pitch=(
            "Problem: rock, ceramic, and composite fracturing today relies on explosives, blades, "
            "or brute thermal energy that destroy fine structure and can't be aimed at a precise "
            "sub-surface volume. Solution: we concentrate a lower-frequency energy pump through "
            "engineered boundary and defect geometry to induce terahertz-band phonon and polariton "
            "populations directly inside a rigid material, locally exceeding its structural "
            "coherence threshold -- softening, embrittling, or fracturing exactly the targeted "
            "volume without bulk heating or explosive force. The system matches the pump's induced "
            "wavelength to the target's lattice spacing (roughly 0.1-10nm for direct bond-scale "
            "effects), uses existing defects, grain boundaries, or engineered periodicity to supply "
            "the missing momentum for high-k mode conversion, and requires the modal energy "
            "injection rate to exceed ordinary thermal diffusion during the pulse window -- "
            "otherwise the system just produces ordinary heating."
        ),
        conveys=(
            "That this is real, rigorous physics -- not magic -- and that precision (softening "
            "exactly the targeted sub-surface volume, nothing else) is the whole commercial unlock "
            "over demolition and quarrying's current blunt tools."
        ),
        doc_text=(
            "Validation logic: the effect must disappear when nonlinear coupling, high-k "
            "conversion, or target mode access is removed (confirms non-thermal mechanism). The "
            "effect must NOT disappear when the target is heated normally to the same bulk "
            "temperature via thermal control (rules out ordinary heating as the explanation). "
            "Material response validated across polar crystals/ceramics, silicate rock/stone, "
            "glass, and cementitious materials, each via a different induced mode class."
        ),
        archetype_id=None,
    ),
    "rf_cochlea": dict(
        audience="Shark Tank (yes, the show)",
        elevator_pitch=(
            "Problem: we're currently letting untold gigawatts of electricity dissipate into the "
            "air through RF transmissions because conventional thinking says the power per square "
            "meter is too low to be worth harvesting. Solution: we combine biomimetics and basic "
            "signal engineering to build a 'radio cochlea' -- a geometry-first rectifying antenna, "
            "shaped like a nautilus spiral, that captures broadband ambient RF (AM, FM, cellular, "
            "Wi-Fi) and converts it into a steady, battery-scale trickle of DC power by isolating "
            "each frequency band into its own resonator and rectification path before combining "
            "them, avoiding the cross-band cancellation that kills net output in conventional "
            "summed-node harvesters."
        ),
        conveys="Next-generation biomimetics, democratizing energy generation, seriously slick as hell.",
        doc_text=(
            "Conventional summed-node topology combines all bands at one rectifier, causing "
            "cross-band cancellation and reduced harvested power. Our separated-channel topology "
            "rectifies each band independently before combining, preserving per-band power and "
            "avoiding cancellation loss."
        ),
        archetype_id="arch_firebringer",
    ),
    "thin_marx": dict(
        audience="Shark Tank (yes, the show)",
        elevator_pitch="There isn't a way to amplify sound without input power or large resonators.",
        conveys="Something impressive.",
        doc_text=None,
        archetype_id=None,
    ),
}


async def run_fixture(name: str, fixture: dict) -> dict:
    job_id, _ = db.create_job(problem_need="", audience=fixture["audience"], association_words=[])
    db.update_job(
        job_id,
        elevator_pitch=fixture["elevator_pitch"],
        conveys=fixture["conveys"],
        doc_text=fixture.get("doc_text"),
        selected_archetype_id=fixture.get("archetype_id"),
    )

    await workers.approach_drafter_worker(job_id)
    job = db.get_job(job_id)
    result = {"fixture": name, "job_id": job_id, "approach_status": job.get("status")}

    if job.get("status") != "approaches_ready":
        result["error"] = job.get("error_message")
        return result

    result["archetype"] = {
        "archetype_id": job["selected_archetype_id"],
        "label": job["selected_archetype_label"],
        "was_fixed": fixture.get("archetype_id") is not None,
    }
    result["approaches"] = job["approach_candidates_json"]

    approach_ids = [a["approach_id"] for a in result["approaches"]]
    slide_errors = {}
    slide_paths = {}
    slide_results = await asyncio.gather(
        *[workers.single_slide_preview_worker(job_id, aid) for aid in approach_ids],
        return_exceptions=True,
    )
    for aid, r in zip(approach_ids, slide_results):
        if isinstance(r, Exception):
            slide_errors[aid] = str(r)
        else:
            slide_paths[aid] = str(r)
    result["slide_paths"] = slide_paths
    result["slide_errors"] = slide_errors
    return result


async def main():
    all_results = []
    for name, fixture in FIXTURES.items():
        print(f"\n=== Running fixture: {name} ===", file=sys.stderr)
        result = await run_fixture(name, fixture)
        all_results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k != "approaches"}, indent=2), file=sys.stderr)

    out_path = _HERE / "local_state" / "full_pitch_batch_report.json"
    out_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved full report to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
