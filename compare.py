#!/usr/bin/env python3
"""
Pitch Synthase archetype-first workshop — text-only comparison.

Runs the CURRENT Template Drafter (fixed archetype pool + fixed visual-focus
pool, late archetype pick) and the EXPERIMENTAL archetype-first Template
Drafter (archetype chosen up front, four dynamically-invented alignments)
against the same fixture inputs, using two separate workers
(template_text_worker_baseline / template_text_worker_alignment_experiment).

Text-only: no image generation calls either side. State lives entirely under
./local_state inside this workshop directory — never touches the real
Intelluric DB or job store.

Run: .venv/bin/python compare.py [fixture_name]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).parent

# ── Env: borrow the real API key, keep all state local to the workshop ──────
_INTELLURIC_ENV = Path("/home/director/intelluric/local/intelluric/intelluric-site.env")
if _INTELLURIC_ENV.exists():
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

# ── Fixtures ─────────────────────────────────────────────────────────────
# "shark_tank": real user-supplied inputs for this trial run. Same
# problem/audience/vibes held constant across baseline + every archetype
# variant, per the A/B comparison method in the protocol doc (§8).
SHARK_TANK_BASE = dict(
    problem_need=(
        "We are currently allowing untold gigawatts of electricity to just dissipate into the "
        "air through RF transmissions because the conventional thinking is the power per square "
        "meter is too low to be worth harvesting. This solves that by combining biomimetics and "
        "basic signal engineering to create a “radio cochlea”, a geometry-first "
        "rectifying antenna that can turn ambient RF into battery-scale constant energy."
    ),
    audience="Shark Tank (yes, the show)",
    association_words=[
        "Next-generation biomimetics",
        "Democratizing energy generation",
        "Seriously slick as hell",
    ],
)

FIXTURES = {
    "shark_tank_architect": dict(SHARK_TANK_BASE, archetype_id="arch_architect"),
    "shark_tank_firebringer": dict(SHARK_TANK_BASE, archetype_id="arch_firebringer"),
}

# "gdp_polariton": dense pseudo-physics fixture, framed toward the civilian/
# industrial reading of the source document (non-explosive material
# fracturing) rather than its biological-analogy section. No hand-picked
# "vibes" here -- association words are inferred from the document's own
# register, per the point of this trial.
GDP_POLARITON_BASE = dict(
    problem_need=(
        "Rock, ceramic, and composite fracturing today relies on explosives, blades, or brute "
        "thermal energy that destroy fine structure and can't be aimed at a precise sub-surface "
        "volume. This instead concentrates a lower-frequency energy pump through engineered "
        "boundary and defect geometry to induce terahertz-band phonon and polariton populations "
        "directly inside a rigid material, locally exceeding its structural coherence threshold — "
        "softening, embrittling, or fracturing exactly the targeted volume without bulk heating "
        "or explosive force."
    ),
    audience="deep-tech and advanced-manufacturing investors, quarrying/demolition industrials",
    association_words=[
        "no magic, just physics",
        "quietly total control",
        "deterministic to the atom",
    ],
)

FIXTURES["gdp_polariton_architect"] = dict(GDP_POLARITON_BASE, archetype_id="arch_architect")
FIXTURES["gdp_polariton_firebringer"] = dict(GDP_POLARITON_BASE, archetype_id="arch_firebringer")


async def run_baseline(fixture: dict) -> dict:
    job_id, _ = db.create_job(
        fixture["problem_need"], fixture["audience"], fixture["association_words"]
    )
    await workers.template_text_worker_baseline(job_id)
    return db.get_job(job_id)


async def run_experiment(fixture: dict) -> dict:
    job_id, _ = db.create_job(
        fixture["problem_need"], fixture["audience"], fixture["association_words"]
    )
    archetype = prompts.experiment_archetype_by_id(fixture["archetype_id"])
    db.update_job(
        job_id,
        selected_archetype_id=archetype["archetype_id"],
        selected_archetype_label=archetype["label"],
    )
    await workers.template_text_worker_alignment_experiment(job_id)
    return db.get_job(job_id)


def _read_if_exists(path: Path) -> str:
    return path.read_text() if path.exists() else "(missing)"


def _render_job_section(heading: str, job: dict, prompt_text: str, manifest_blob: str) -> str:
    return (
        f"## {heading}\n\n"
        f"status: {job.get('status')}\n\n"
        f"### Prompt sent\n```\n{prompt_text}\n```\n\n"
        f"### Parsed manifest\n```json\n{manifest_blob}\n```\n"
    )


def render_trial_report(base: dict, baseline_job: dict, experiment_jobs: dict[str, dict]) -> str:
    jobs_dir = Path(os.environ["INSTANT_JOBS_DIR"])

    out = ["# Archetype-first workshop — trial run\n"]
    out.append(f"problem_need: {base['problem_need']}")
    out.append(f"audience: {base['audience']}")
    out.append(f"vibes: {base['association_words']}\n")

    base_dir = jobs_dir / baseline_job["id"] / "candidates"
    out.append(_render_job_section(
        "Control — baseline (fixed archetype pool + fixed visual-focus pool)",
        baseline_job,
        _read_if_exists(base_dir / "template_prompt.txt"),
        json.dumps(baseline_job.get("candidates_json"), indent=2)
        + "\n\nselected_archetypes:\n" + json.dumps(baseline_job.get("archetypes_json"), indent=2),
    ))

    for archetype_id, job in experiment_jobs.items():
        exp_dir = jobs_dir / job["id"] / "candidates"
        out.append(_render_job_section(
            f"Experiment — archetype-first ({archetype_id})",
            job,
            _read_if_exists(exp_dir / "template_prompt.txt"),
            json.dumps(job.get("candidates_json"), indent=2),
        ))

    return "\n".join(out)


BASES = {
    "shark_tank": SHARK_TANK_BASE,
    "gdp_polariton": GDP_POLARITON_BASE,
}


async def main():
    base_name = sys.argv[1] if len(sys.argv) > 1 else "shark_tank"
    archetype_ids = sys.argv[2].split(",") if len(sys.argv) > 2 else ["arch_architect", "arch_firebringer"]
    base = BASES[base_name]

    print(f"Running 1 control + {len(archetype_ids)} experimental trials on '{base_name}' inputs...", file=sys.stderr)

    baseline_task = run_baseline(base)
    experiment_tasks = {
        archetype_id: run_experiment(dict(base, archetype_id=archetype_id))
        for archetype_id in archetype_ids
    }
    baseline_job, *experiment_results = await asyncio.gather(baseline_task, *experiment_tasks.values())
    experiment_jobs = dict(zip(experiment_tasks.keys(), experiment_results))

    report = render_trial_report(base, baseline_job, experiment_jobs)
    out_path = _HERE / "local_state" / f"comparison_{base_name}.md"
    out_path.write_text(report)
    print(report)
    print(f"\nSaved to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
