#!/usr/bin/env python3
"""
Control-only A/B: same worker (template_text_worker_baseline) both sides,
same underlying invention (Acoustic Marx Amplifier), only the INPUT quality
varies -- isolating problem_need/vibe density as the tested variable, no
archetype machinery involved at all.

  A = minimum prompt + basic/generic vibes
  B = dense mechanism-rich prompt + vibes in the same register as the
      original RF-cochlea/Shark-Tank trial

Run: .venv/bin/python compare_ab_control.py
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

MINIMAL = dict(
    problem_need="There isn't a way to amplify sound without input power or large resonators.",
    audience="Shark Tank (yes, the show)",
    association_words=[
        "engineering precision",
        "clean design",
        "solid performance",
    ],
)

DENSE = dict(
    problem_need=(
        "Every high-intensity acoustic pulse today needs a big power-hungry transducer, a full "
        "phased array of individually-timed electronics, or a one-shot explosive charge, because "
        "the conventional thinking is that pressure amplification requires proportionally more "
        "electrical input or external timing control. This solves that by combining Marx-generator "
        "pulse topology and basic acoustic-network engineering to create an “acoustic Marx "
        "amplifier”, a self-triggering pressure-topology device that charges many small chambers "
        "in parallel off a weak source and snaps them into a phase-aligned series discharge, "
        "stacking their pressure into one focused pulse without extra power or electronics."
    ),
    audience="Shark Tank (yes, the show)",
    association_words=[
        "next-generation pressure engineering",
        "loud without the electronics",
        "seriously slick as hell",
    ],
)


async def run_baseline(fixture: dict) -> dict:
    job_id, _ = db.create_job(
        fixture["problem_need"], fixture["audience"], fixture["association_words"]
    )
    await workers.template_text_worker_baseline(job_id)
    return db.get_job(job_id)


def _read_if_exists(path: Path) -> str:
    return path.read_text() if path.exists() else "(missing)"


def render_report(minimal_job: dict, dense_job: dict) -> str:
    jobs_dir = Path(os.environ["INSTANT_JOBS_DIR"])

    def section(name, fixture, job):
        d = jobs_dir / job["id"] / "candidates"
        return (
            f"## {name}\n\n"
            f"problem_need: {fixture['problem_need']}\n\n"
            f"vibes: {fixture['association_words']}\n\n"
            f"status: {job.get('status')}\n\n"
            f"### Prompt sent\n```\n{_read_if_exists(d / 'template_prompt.txt')}\n```\n\n"
            f"### Parsed manifest\n```json\n"
            f"{json.dumps(job.get('candidates_json'), indent=2)}\n\n"
            f"selected_archetypes:\n{json.dumps(job.get('archetypes_json'), indent=2)}\n```\n"
        )

    out = ["# Control-only A/B: minimal prompt vs. dense prompt (Acoustic Marx Amplifier)\n"]
    out.append(section("A -- minimum prompt, basic vibes", MINIMAL, minimal_job))
    out.append(section("B -- dense prompt, Shark-Tank-register vibes", DENSE, dense_job))
    return "\n".join(out)


async def main():
    print("Running control-only A/B on Acoustic Marx Amplifier (minimal vs dense input)...", file=sys.stderr)
    minimal_job, dense_job = await asyncio.gather(run_baseline(MINIMAL), run_baseline(DENSE))

    report = render_report(minimal_job, dense_job)
    out_path = _HERE / "local_state" / "comparison_acoustic_marx_ab.md"
    out_path.write_text(report)
    print(report)
    print(f"\nSaved to {out_path}", file=sys.stderr)
    print(f"minimal job_id={minimal_job['id']} status={minimal_job.get('status')}", file=sys.stderr)
    print(f"dense   job_id={dense_job['id']} status={dense_job.get('status')}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
