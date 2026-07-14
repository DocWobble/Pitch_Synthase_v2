#!/usr/bin/env python3
"""Test ONLY the source-accuracy verifier's new [ambiguity-gate] +
[evidence-standard] framing against slide 16 -- the slide previously
confirmed clean by direct visual inspection, which the old prompt
incorrectly flagged with 13 false-positive issues (e.g. "vendor SKUs" not
being verbatim in the source). Does not run the judge or any regeneration."""
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
JOB_ID = "pd_5072b5fa2fdb4ab2"


async def main():
    job = db.get_job(JOB_ID)
    plan = job.get("deck_proof_plan") or {}
    storyboard_by_idx = {item["slide_number"]: item for item in plan.get("paid_storyboard") or []}
    elevator_pitch = job.get("elevator_pitch") or ""
    doc_text = job.get("doc_text")

    for slide_num in (16, 10, 15, 1):  # 16/10/15 previously clean-or-passed; 1 had a real regression
        storyboard_entry = storyboard_by_idx[slide_num]
        proof_path = workers.job_dir(JOB_ID) / "slides" / f"slide_{slide_num:02d}_proof.png"
        prompt_text = prompts.slide_source_accuracy_verifier_prompt(storyboard_entry, elevator_pitch, doc_text)
        resp = await workers._responses_create(
            JOB_ID, stage=f"test_source_verifier_{slide_num}", model=prompts.TEMPLATE_DRAFTER_MODEL,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": prompt_text},
                workers._image_content(proof_path),
            ]}],
        )
        result = workers._parse_model_json(workers._response_text(resp))
        issues = result.get("issues", [])
        actionable = [i for i in issues if i.get("actionable") is True]
        print(f"\n=== slide {slide_num} ===", file=sys.stderr)
        print(f"total issues: {len(issues)}  actionable: {len(actionable)}", file=sys.stderr)
        for iss in issues:
            print(f"  [{iss.get('actionable')}] {iss.get('location')}: {iss.get('problem')} -> {iss.get('correction')}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
