#!/usr/bin/env python3
"""
Run one pitch (guiclid) through approach_drafter_worker (the "template
generation" stage: 4 dynamically invented strategic approaches) once per each
of the 10 new founder archetypes (Odysseus, Caesar, Da Vinci, Athena, Newton,
Cicero, Copernicus, Gutenberg, Alexander, Michelangelo). Text only, no images.
Archetype is pinned explicitly per job so every archetype gets a guaranteed
run on the identical pitch for direct comparison.
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

PITCH = dict(
    audience="developer-tools and productivity-software investors",
    elevator_pitch=(
        "Problem: conventional desktop file managers project the filesystem as storage -- "
        "folders, names, icons, timestamps -- forcing users to reconstruct what a file "
        "actually does by parsing serial text labels one at a time. Solution: guiclid "
        "replaces that projection with a deterministic geometric GUI layer derived directly "
        "from each object's real invocation behavior: files that activate become circles, "
        "containers become boxes, support/dependency files tessellate into connected "
        "honeycombs, opaque app-owned payloads become rounded triangles, and presentable "
        "content becomes a live-content surface. Geometry is never decorative -- it's a "
        "strict, ordered rule cascade (container test, activation test, runtime-boundary "
        "test, presentation test, payload test, support test, breakage test, inert test) "
        "computed from an event-driven dependency graph that updates only at file-operation "
        "boundaries, so a user can navigate and understand an entire workspace by shape and "
        "adjacency alone, even with filenames hidden."
    ),
    conveys=(
        "That this is a rigorous, deterministic system -- not a decorative 'smart desktop' "
        "skin -- and that it fundamentally changes how fast a person can understand an "
        "unfamiliar folder, project, or codebase at a glance."
    ),
    doc_text=(
        "Acceptance tests include: a directory remains navigable with filenames hidden; "
        "changing a script from read-only to executable changes its shape to a circle; a "
        "set of mutually related support files tessellates into a honeycomb; removing an "
        "owner application causes all dependent rounded triangles to become broken sharp "
        "triangles."
    ),
)


async def run_one(archetype_id: str) -> dict:
    job_id, _ = db.create_job(problem_need="", audience=PITCH["audience"], association_words=[])
    db.update_job(
        job_id,
        elevator_pitch=PITCH["elevator_pitch"],
        conveys=PITCH["conveys"],
        doc_text=PITCH["doc_text"],
        selected_archetype_id=archetype_id,
    )
    await workers.approach_drafter_worker(job_id)
    job = db.get_job(job_id)
    result = {"archetype_id": archetype_id, "job_id": job_id, "status": job.get("status")}
    if job.get("status") != "approaches_ready":
        result["error"] = job.get("error_message")
    else:
        result["approaches"] = job["approach_candidates_json"]
    return result


async def main():
    all_results = []
    for archetype in prompts.EXPERIMENT_FOUNDER_ARCHETYPES:
        print(f"\n=== {archetype['label']}, {archetype['role']} ===", file=sys.stderr)
        result = await run_one(archetype["archetype_id"])
        all_results.append(result)
        if result["status"] != "approaches_ready":
            print("FAILED:", result.get("error"), file=sys.stderr)
            continue
        for a in result["approaches"]:
            print(f"  {a['approach_id']} :: {a['label']}", file=sys.stderr)
            print(f"    pitch_angle: {a.get('pitch_angle', '')}", file=sys.stderr)
            print(f"    key_differentiator: {a.get('key_differentiator', '')}", file=sys.stderr)
            print(f"    visual_direction: {a.get('visual_direction', '')}", file=sys.stderr)

    out_path = _HERE / "local_state" / "all_archetypes_one_pitch_report.json"
    out_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved full report to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
