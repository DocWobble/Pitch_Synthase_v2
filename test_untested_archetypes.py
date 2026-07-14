#!/usr/bin/env python3
"""
Run one pitch (PetNavi) through approach_drafter_worker (the "template
generation" stage: 4 dynamically invented strategic approaches) once per
untested archetype: Commander, Oracle, Alchemist, Healer, Steward. Firebringer,
Trickster, and Architect have already been exercised end-to-end in prior tests.

Archetype is pinned explicitly per job (not auto-picked), so each of the 5
untested archetypes gets a guaranteed run on the same pitch for comparison.
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
    audience="developer-tools/AI-infrastructure investors and enterprise IT/security buyers",
    elevator_pitch=(
        "Problem: AI copilots and streaming avatar systems today are built ad hoc -- a "
        "language model can emit an unpredictable number of simultaneous actions per input, "
        "there's no audit trail tying an output back to the message that caused it, and "
        "swapping an ASR/LLM/TTS vendor usually means rewriting the whole pipeline. "
        "Enterprise IT also can't allow native AI copilot software on locked-down machines "
        "at all. Solution: a strict architecture (GDP) that converts every input event into "
        "exactly one Intent, one policy Beat, and one bounded ActionPlan (speech : visemes : "
        "expressions : scene : sfx capped at 1 : 1 : <=1 : <=1 : <=1), gated through a single "
        "non-bypassable Safety Sentinel that logs and can allow, rewrite, or deny any output, "
        "with every engine behind a swappable capability-flagged adapter interface. The same "
        "architecture that drives a VTuber avatar's expressions and scene changes also "
        "drives a portable desktop copilot -- and in its most locked-down form runs entirely "
        "inside a sanctioned enterprise browser via WebGPU/WASM from a USB drive, persisting "
        "all models and memory on the removable device and leaving zero residue on the host."
    ),
    conveys=(
        "That this is a disciplined, safety-first systems architecture -- not a chatbot "
        "wrapper -- and that the same rigorous core scales from a hobbyist VTuber avatar all "
        "the way to an enterprise-safe, zero-install portable AI copilot."
    ),
    doc_text=(
        "Falsification tests enforced in CI: boundary swap (voice-only vs chat-only still "
        "emits valid beats), phase denial (dropping expressions doesn't destabilize the "
        "avatar), cross-medium analogy (same RendererCmd grammar drives Live2D or VRM/Unity), "
        "10x chat-flood perturbation (bounded backlog, no starvation), and adapter swap "
        "(changing ASR/TTS/LLM vendor doesn't change Beat plans beyond capability flags)."
    ),
)

UNTESTED_ARCHETYPE_IDS = [
    "arch_commander",
    "arch_oracle",
    "arch_alchemist",
    "arch_healer",
    "arch_steward",
]


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
    result = {
        "archetype_id": archetype_id,
        "job_id": job_id,
        "status": job.get("status"),
        "archetype_label_recorded": job.get("selected_archetype_label"),
    }
    if job.get("status") != "approaches_ready":
        result["error"] = job.get("error_message")
    else:
        result["approaches"] = job["approach_candidates_json"]
    return result


async def main():
    all_results = []
    for archetype_id in UNTESTED_ARCHETYPE_IDS:
        archetype = prompts.experiment_archetype_by_id(archetype_id)
        print(f"\n=== {archetype['label']} ===", file=sys.stderr)
        result = await run_one(archetype_id)
        all_results.append(result)
        if result["status"] != "approaches_ready":
            print("FAILED:", result.get("error"), file=sys.stderr)
            continue
        for a in result["approaches"]:
            print(f"  {a['approach_id']} :: {a['label']}", file=sys.stderr)
            print(f"    pitch_angle: {a.get('pitch_angle', '')[:180]}", file=sys.stderr)
            print(f"    visual_direction: {a.get('visual_direction', '')[:180]}", file=sys.stderr)

    out_path = _HERE / "local_state" / "untested_archetypes_report.json"
    out_path.write_text(json.dumps(all_results, indent=2))
    print(f"\nSaved full report to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
