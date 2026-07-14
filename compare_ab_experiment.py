#!/usr/bin/env python3
"""
Same minimal-vs-dense A/B as compare_ab_control.py, but through the
archetype-first EXPERIMENT worker with archetype fixed to Architect --
testing whether the "confidently wrong mechanism vs. correct mechanism"
finding holds when archetype timing/invention is added to the pipeline too.

Run: .venv/bin/python compare_ab_experiment.py
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

from compare_ab_control import MINIMAL, DENSE

ARCHETYPE_ID = "arch_architect"


async def run_experiment(fixture: dict) -> dict:
    job_id, _ = db.create_job(
        fixture["problem_need"], fixture["audience"], fixture["association_words"]
    )
    archetype = prompts.experiment_archetype_by_id(ARCHETYPE_ID)
    db.update_job(
        job_id,
        selected_archetype_id=archetype["archetype_id"],
        selected_archetype_label=archetype["label"],
    )
    await workers.template_text_worker_alignment_experiment(job_id)
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
            f"{json.dumps(job.get('candidates_json'), indent=2)}\n```\n"
        )

    out = ["# Test-Architect A/B: minimal vs. dense input (Acoustic Marx Amplifier, archetype-first)\n"]
    out.append(section("A -- minimum prompt, basic vibes, Architect", MINIMAL, minimal_job))
    out.append(section("B -- dense prompt, Shark-Tank-register vibes, Architect", DENSE, dense_job))
    return "\n".join(out)


async def main():
    print(f"Running Test-Architect A/B on Acoustic Marx Amplifier (minimal vs dense, archetype={ARCHETYPE_ID})...", file=sys.stderr)
    minimal_job, dense_job = await asyncio.gather(run_experiment(MINIMAL), run_experiment(DENSE))

    report = render_report(minimal_job, dense_job)
    out_path = _HERE / "local_state" / "comparison_acoustic_marx_ab_architect.md"
    out_path.write_text(report)
    print(report)
    print(f"\nSaved to {out_path}", file=sys.stderr)
    print(f"minimal job_id={minimal_job['id']} status={minimal_job.get('status')}", file=sys.stderr)
    print(f"dense   job_id={dense_job['id']} status={dense_job.get('status')}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
