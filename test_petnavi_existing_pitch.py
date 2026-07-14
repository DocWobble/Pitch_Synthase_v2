#!/usr/bin/env python3
"""
New test shape: the user already has a complete investor pitch outline (16
slides -- problem, solution, market, competitive landscape, business model,
financials/BOM, ask, roadmap, team) plus a screenshot of a working UI
prototype, rather than a bare problem/solution paragraph. This is meant to
exercise the pipeline's handling of a pitch that already has real financial
specifics (BOM costs, MSRP, margins, funding ask) baked in -- the approach
drafter must reuse those real numbers, not invent different ones.

Archetype left unset (auto-pick). Runs template generation (approach
drafting) then the first actual visual output -- single-slide previews for
all 4 approaches. Does NOT proceed to full paid deck generation.

Note: single_slide_drafter_prompt/single_slide_image_prompt are text-only by
design at this stage (images are deliberately deferred to the paid Anchor
Writer stage to keep this phase cheap) -- so the UI mockup image is saved
into the job's intake/ dir for parity with the real pipeline, but is folded
into doc_text as a text description for THIS run, since nothing at the
approach-drafting or single-slide-preview stage currently consumes an image.
"""
from __future__ import annotations
import asyncio, json, os, shutil, sys
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

OUTLINE_PATH = Path("/home/director/.claude/uploads/aa8045cb-c5bb-45c3-8c67-b67b8f6562d2/c9fed096-PetNavi_Pitch_Deck.txt")
MOCKUP_PATH = Path("/home/director/.claude/uploads/aa8045cb-c5bb-45c3-8c67-b67b8f6562d2/80f6cff1-69FD92AC3E44474BBBBBA5B2513F1E5B.png")

OUTLINE_TEXT = OUTLINE_PATH.read_text()

MOCKUP_DESCRIPTION = (
    "Supporting UI mockup (screenshot of a working local prototype, described here "
    "since this stage is text-only): a dark cyberpunk-styled browser webapp titled "
    "'PetNavi // USB-Agent Console'. Left panel: anime-style avatar art, persona "
    "'Akari // EN/JP', voice 'Kokoro-lite', ASR on / TTS speaking indicators, a "
    "scrolling dialogue log of a real voice exchange ('What's my GPU and how fast "
    "are you running?' -> 'Detected RTX-3060 6GB.'). Center panel: 'Companion "
    "Console' with ASR/TTS/Proactive/Local-Only toggles, live reasoning-rate (22 "
    "t/s), ASR latency (80ms), TTS latency (120ms), context-buffer meter, and an "
    "editable system prompt box. Right panel: 'Hardware Profile' showing the "
    "actual detected host machine (Ryzen 7 6800H, RTX 3060 6GB, 32GB RAM, Linux, "
    "USB MSC+UAC+HID), live CPU/VRAM/context utilization bars, and an event log "
    "('USB composite enumerated', 'Whisper-tiny on CPU', 'Qwen-small on GPU', "
    "'Kokoro-lite on CPU', 'Sandbox RW disabled', 'Cloud adapter idle'). This is a "
    "real running prototype, not a mockup render -- the detected hardware and "
    "event log reflect an actual local session."
)

AUDIENCE = "early-stage hardware/consumer-AI seed investors and crowdfunding backers"

ELEVATOR_PITCH = (
    "Problem: AI today is cloud-locked (privacy/compliance risk), app-bound (installs, "
    "configs, residue), and vendor-owned -- there is no physical form factor for personal "
    "AI identity that a user actually owns, so enterprises reject cloud AI agents, hobbyists "
    "drown in DIY configs, and consumers rent their AI rather than own it. Solution: PetNavi "
    "is a $40-80 USB dongle -- an 'AI Soul Cartridge' -- with dual volumes (read-only system "
    "partition, read-write data partition) and built-in mic/speaker/Bluetooth audio, that "
    "enumerates to any host computer as an ordinary USB audio device plus mass storage. No "
    "install: a browser-based webapp runs inference on the host's own GPU/CPU via WebGPU/WASM "
    "using efficient quantized models (Qwen-3/DeepSeek-R1-class reasoning at a 6-8GB VRAM "
    "footprint, CPU fallback standard), while all memory, models, and persona live entirely on "
    "the dongle itself, leaving zero residue on the host machine. A 16GB Slim SKU has a ~$30 "
    "bill of materials at roughly 50% gross margin against a $60 MSRP; a 128GB+Bluetooth "
    "Deluxe SKU runs a ~$45 BOM at $80-90 MSRP. Funding will go toward hardware prototyping, "
    "FCC/CE certification, and an initial 5,000-10,000 unit production run, launching via "
    "crowdfunding (Kickstarter/Indiegogo) to VTubers, creators, and open-source AI hobbyists "
    "first, before expanding into education and compliance-friendly enterprise deployments."
)

CONVEYS = (
    "That this is a real, already-working hardware-plus-software architecture with concrete "
    "unit economics worked out (BOM, MSRP, margin, funding ask) -- not a concept pitch. The "
    "ask is for manufacturing scale and certification capital, not proof that the idea works."
)


async def main():
    job_id, _ = db.create_job(problem_need="", audience=AUDIENCE, association_words=[])
    intake_dir = workers.job_dir(job_id) / "intake"
    intake_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MOCKUP_PATH, intake_dir / f"mockup{MOCKUP_PATH.suffix}")

    db.update_job(
        job_id,
        elevator_pitch=ELEVATOR_PITCH,
        conveys=CONVEYS,
        doc_text=OUTLINE_TEXT + "\n\n" + MOCKUP_DESCRIPTION,
        selected_archetype_id=None,  # auto-pick
    )

    print(f"Job: {job_id}", file=sys.stderr)
    print("Running approach drafter (template generation)...", file=sys.stderr)
    await workers.approach_drafter_worker(job_id)
    job = db.get_job(job_id)
    print(f"approach status: {job.get('status')}", file=sys.stderr)
    if job.get("status") != "approaches_ready":
        print("FAILED:", job.get("error_message"), file=sys.stderr)
        return

    archetype_id = job["selected_archetype_id"]
    archetype = prompts.experiment_archetype_by_id(archetype_id)
    print(f"Auto-picked archetype: {archetype['label']} ({archetype['role']})", file=sys.stderr)
    for a in job["approach_candidates_json"]:
        print(f"  {a['approach_id']} :: {a['label']}", file=sys.stderr)
        print(f"    pitch_angle: {a['pitch_angle']}", file=sys.stderr)
        print(f"    key_differentiator: {a['key_differentiator']}", file=sys.stderr)

    print("\nRunning single-slide previews (first deck output pass)...", file=sys.stderr)
    approach_ids = [a["approach_id"] for a in job["approach_candidates_json"]]
    results = await asyncio.gather(
        *[workers.single_slide_preview_worker(job_id, aid) for aid in approach_ids],
        return_exceptions=True,
    )
    for aid, r in zip(approach_ids, results):
        if isinstance(r, Exception):
            print(f"  {aid}: FAILED - {r}", file=sys.stderr)
        else:
            print(f"  {aid}: {r}", file=sys.stderr)

    print(f"\nJob ID for follow-up: {job_id}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
