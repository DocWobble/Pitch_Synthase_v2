#!/usr/bin/env python3
"""
RiboTome — Pitch Synthase agentic pipeline server, port 8793.
Wired to the workshop pipeline: approach_drafter_worker, single_slide_preview_worker,
generation_worker (with full-pitch-first field bridge), and verification_worker.

Bypasses Stripe. Writes to the workshop DB and jobs dir.
Run: .venv/bin/python ribotome.py

Eventual goal: once the new frontend is built, this server's API surface and
parameter set become the integration contract for the deployed tool — the
workshop hooks here (approach selection, pitch_aspect_modes, excepted_inference_elements)
are the same knobs the production frontend will expose.
"""
import asyncio
import importlib
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Path setup ───────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
_PROMPTS_FILE = _HERE / "prompts.py"

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

import db as _db
import workers as _workers
import prompts as _prompts

_db.init_db()

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, StreamingResponse
import uvicorn

app = FastAPI(docs_url=None, redoc_url=None)

PORT = 8793


# ── Full-pitch-first worker bridge ────────────────────────────────────────────
# The real _raw_user_inputs / _selected_template_payload read the legacy
# production field shape. We swap them out per-thread when running the
# generation_worker so the full-pitch-first job fields reach the Anchor Writer
# correctly — identical to the technique validated in run_bloomos_fresh.py.

def _raw_user_inputs_full_pitch(job: dict) -> dict:
    return {
        "page_1": {
            "audience": job.get("audience") or "",
            "conveys": job.get("conveys") or "",
        },
        "page_2": {
            "elevator_pitch": job.get("elevator_pitch") or "",
            "supporting_document_provided": bool(job.get("doc_text")),
            "supporting_image": bool(
                _workers._supporting_image(job.get("id", "")) if job.get("id") else False
            ),
            "optional_notes": None,
        },
    }


def _selected_template_payload_for_approach(job: dict) -> dict:
    """Build the template payload from approach_candidates_json.

    Uses the approach's own effective_archetype_id (set by the two-archetype
    blend system) if present; falls back to the job-level selected_archetype_id
    for jobs drafted before the blend system was introduced.
    """
    approach_id = job.get("selected_candidate_id") or ""
    approach = next(
        (a for a in (job.get("approach_candidates_json") or [])
         if a.get("approach_id") == approach_id),
        None,
    )
    # Prefer the per-approach effective archetype (blend-system), fall back to job-level.
    archetype_id = (
        (approach or {}).get("effective_archetype_id")
        or job.get("selected_archetype_id")
        or ""
    )
    archetype = _prompts.archetype_by_id(archetype_id) or {}
    return {
        "candidate_id": approach_id,
        "focus_id": "",
        "style_label": (approach or {}).get("label") or "",
        "style_tags": [],
        "template_manifest": approach,
        "archetype": {
            "archetype_id": archetype_id,
            "label": archetype.get("label") or "",
            "posture": archetype.get("posture") or "",
        },
    }


# ── Artifact helpers ──────────────────────────────────────────────────────────
def _rel_job_file(job_id: str, path: Path) -> str:
    return path.relative_to(_workers.job_dir(job_id)).as_posix()


def _slide_sort_key(path: str) -> tuple[int, str]:
    m = re.search(r"(?:slide[_-]?)?(\d{1,3})", Path(path).stem, re.I)
    return (int(m.group(1)) if m else 9999, path)


def _collect_artifact_images(job_id: str) -> dict:
    try:
        root = _workers.job_dir(job_id)
    except Exception:
        return {"preview": [], "full": [], "refined": [], "other": []}
    if not root.exists():
        return {"preview": [], "full": [], "refined": [], "other": []}

    img_exts = {".png", ".jpg", ".jpeg", ".webp"}
    buckets: dict[str, list[dict]] = {"preview": [], "full": [], "refined": [], "other": []}
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in img_exts:
            continue
        rel = _rel_job_file(job_id, f)
        low = rel.lower()
        stem = f.stem
        label = stem.replace("_", " ").replace("-", " ")
        entry = {"path": rel, "label": label, "mtime": int(f.stat().st_mtime)}

        if "single_slide_previews" in low or ("preview" in low and "approach" in low):
            buckets["preview"].append(entry)
        elif any(tok in low for tok in ("final", "refined", "clean", "composited", "export")):
            buckets["refined"].append(entry)
        elif "proof" in low or low.startswith("slides/slide_"):
            buckets["full"].append(entry)
        else:
            buckets["other"].append(entry)

    for key in buckets:
        buckets[key].sort(key=lambda e: _slide_sort_key(e["path"]))
    return buckets


# ── Worker runner ─────────────────────────────────────────────────────────────
def _run_async_in_thread(coro_fn, *args):
    def _go():
        asyncio.run(coro_fn(*args))
    t = threading.Thread(target=_go, daemon=True)
    t.start()
    return t


def _run_generation_in_thread(job_id: str):
    """Run generation_worker with the full-pitch-first field bridge (legacy endpoint path)."""
    async def _run_with_cv():
        tok_raw = _workers._raw_user_inputs_cv.set(_raw_user_inputs_full_pitch)
        tok_tmpl = _workers._selected_template_payload_cv.set(_selected_template_payload_for_approach)
        try:
            await _workers.generation_worker(job_id)
        finally:
            _workers._raw_user_inputs_cv.reset(tok_raw)
            _workers._selected_template_payload_cv.reset(tok_tmpl)
    t = threading.Thread(target=lambda: asyncio.run(_run_with_cv()), daemon=True)
    t.start()
    return t


# ── SAWC dispatch layer ───────────────────────────────────────────────────────
PIPELINE_STAGES: list[dict] = []
_STAGE_REGISTRY: dict[str, dict] = {}


def register_stage(stage_def: dict) -> None:
    """[tool-broker]: one-step stage registration."""
    PIPELINE_STAGES.append(stage_def)
    _STAGE_REGISTRY[stage_def["id"]] = stage_def


def _stage_by_id(stage_id: str) -> dict:
    return _STAGE_REGISTRY[stage_id]


_CONDITIONS = {
    "has_image":      lambda job: bool(_workers._supporting_image(job.get("id", ""))),
    "needs_design":   lambda job: bool(
        (job.get("intake_options") or {}).get("infer_mockup")
        or (job.get("intake_options") or {}).get("infer_prototype")
    ),
    "has_prototype":  lambda job: bool(job.get("infer_prototype")),
}

_LOCAL_WORKERS: dict = {}


async def _run_generation(job_id: str) -> None:
    """Run generation_worker with the full-pitch-first field bridge."""
    tok_raw = _workers._raw_user_inputs_cv.set(_raw_user_inputs_full_pitch)
    tok_tmpl = _workers._selected_template_payload_cv.set(_selected_template_payload_for_approach)
    try:
        await _workers.generation_worker(job_id)
    finally:
        _workers._raw_user_inputs_cv.reset(tok_raw)
        _workers._selected_template_payload_cv.reset(tok_tmpl)


async def _run_previews(job_id: str) -> None:
    """[fan-out]+[fan-in]: 4 concurrent preview workers."""
    job = _db.get_job(job_id)
    approaches = (job.get("approach_candidates_json") or []) if job else []
    if not approaches:
        raise RuntimeError("no approach candidates — approach_draft must complete first")
    await asyncio.gather(*[
        _workers.single_slide_preview_worker(job_id, a["approach_id"])
        for a in approaches
    ])
    for a in approaches:
        _gallery_index_preview(job_id, a["approach_id"])


_GALLERY_DIR = _HERE / "local_state" / "preview_gallery"
_GALLERY_INDEX = _GALLERY_DIR / "index.json"


def _gallery_index_preview(job_id: str, approach_id: str) -> None:
    """Upsert one preview into the gallery index."""
    job = _db.get_job(job_id)
    if not job:
        return
    candidates = job.get("approach_candidates_json") or []
    candidate = next((c for c in candidates if c["approach_id"] == approach_id), None)
    if not candidate:
        return
    image_path = _HERE / "local_state" / "jobs" / job_id / "single_slide_previews" / f"{approach_id}.png"
    if not image_path.exists():
        return

    _GALLERY_DIR.mkdir(exist_ok=True)
    entries = json.loads(_GALLERY_INDEX.read_text()) if _GALLERY_INDEX.exists() else []
    entry_id = f"{job_id}__{approach_id}"
    entries = [e for e in entries if e.get("id") != entry_id]
    entries.append({
        "id": entry_id,
        "job_id": job_id,
        "approach_id": approach_id,
        "approach_label": candidate.get("label", ""),
        "visual_direction": candidate.get("visual_direction", ""),
        "pitch_angle": (candidate.get("pitch_angle") or "")[:300],
        "archetype_id": job.get("selected_archetype_id"),
        "archetype_label": job.get("selected_archetype_label"),
        "elevator_pitch_snippet": (job.get("elevator_pitch") or "")[:120],
        "selected": job.get("selected_candidate_id") == approach_id,
        "image_path": str(image_path),
        "created_at": job.get("created_at", ""),
    })
    _GALLERY_INDEX.write_text(json.dumps(entries, indent=2))


_LOCAL_WORKERS["_run_generation"] = _run_generation
_LOCAL_WORKERS["_run_previews"] = _run_previews

# [canonical-reference]: fields set at POST /api/jobs — always present, no upstream producer needed
CREATION_FIELDS: frozenset = frozenset({
    "elevator_pitch", "audience", "doc_text", "conveys",
    "infer_prototype", "selected_archetype_id", "pitch_aspect_modes",
    "excepted_inference_elements", "inferred_element_decisions",
    "association_words",
})


def _audit_pipeline() -> None:
    """[preflight-check] + [red-team-review]: verify every declared input_schema field
    (required or not) has an unbroken producer, and every declared output_fields entry
    is actually consumed by some stage. A field with no provenance or no destination is
    a defect, not an exception — marking a field optional does not exempt it from the
    provenance check, and there is no allowlist for unconsumed outputs. Runs at server
    startup — fail-fast before any job can dispatch. Raises RuntimeError naming every
    orphan field and the stage(s) involved.
    """
    # field → set of stage_ids that declare producing it
    field_producers: dict[str, set[str]] = {}
    for stage in PIPELINE_STAGES:
        for field in (stage.get("output_fields") or []):
            field_producers.setdefault(field, set()).add(stage["id"])

    # Fields produced by event-triggered stages (e.g. image_scan) are available at any
    # time, independent of DAG position — they are not constrained to the depends chain.
    event_triggered_outputs: set[str] = {
        field
        for stage in PIPELINE_STAGES if stage.get("event_trigger")
        for field in (stage.get("output_fields") or [])
    }

    def upstream_of(stage_id: str) -> set[str]:
        visited: set[str] = set()
        queue = [stage_id]
        while queue:
            sid = queue.pop()
            for dep in (_STAGE_REGISTRY.get(sid) or {}).get("depends", []):
                if dep not in visited:
                    visited.add(dep)
                    queue.append(dep)
        return visited

    errors = []

    # --- provenance: every declared input field must have a producer, required or not ---
    for stage in PIPELINE_STAGES:
        schema = stage.get("input_schema") or {}
        up = upstream_of(stage["id"])
        for field, spec in schema.items():
            if spec.get("source") == "filesystem":
                continue
            if field in CREATION_FIELDS or field in event_triggered_outputs:
                continue
            producers_up = field_producers.get(field, set()) & up
            if not producers_up:
                errors.append(
                    f"  [no provenance] stage '{stage['id']}' declares input '{field}' "
                    f"(required={spec.get('required', False)}) but no upstream stage in "
                    f"its dependency chain declares it in output_fields"
                )

    # --- destination: every declared output field must be consumed by some stage ---
    all_inputs: set[str] = set()
    for stage in PIPELINE_STAGES:
        all_inputs |= set((stage.get("input_schema") or {}).keys())
    for stage in PIPELINE_STAGES:
        for field in (stage.get("output_fields") or []):
            if field not in all_inputs:
                errors.append(
                    f"  [no destination] stage '{stage['id']}' produces '{field}' but no "
                    f"stage declares it in input_schema — nothing but the exported deck "
                    f"package may be a dead end"
                )

    if errors:
        raise RuntimeError("Pipeline field provenance audit FAILED:\n" + "\n".join(errors))
    total_checks = (
        sum(len(s.get("input_schema") or {}) for s in PIPELINE_STAGES)
        + sum(len(s.get("output_fields") or []) for s in PIPELINE_STAGES)
    )
    print(f"[pipeline] field provenance audit passed — {total_checks} field checks OK (provenance + destination)", flush=True)


register_stage({
    "id": "design_refs_text",
    "worker": "design_drafter_text_worker",
    "depends": [],
    "condition": "has_prototype",
    "input_schema": {
        "elevator_pitch":  {"type": "str",  "required": True,  "description": "Core pitch description"},
        "doc_text":        {"type": "str",  "required": False, "description": "Supporting document text"},
        "infer_prototype": {"type": "int",  "required": True,  "description": "Set at job creation; 1 = generate prototypes"},
    },
    "output_fields": ["prototype_candidates_json"],
})
register_stage({
    "id": "approach_draft",
    "worker": "approach_drafter_worker",
    "depends": ["design_refs_text"],
    "input_schema": {
        "audience":                   {"type": "str",  "required": True,  "description": "Target audience for the pitch"},
        "elevator_pitch":             {"type": "str",  "required": True,  "description": "Core pitch description"},
        "conveys":                    {"type": "str",  "required": False, "description": "Key message or problem framing"},
        "selected_archetype_id":      {"type": "str",  "required": False, "description": "Founder archetype ID (e.g. arch_chanel)"},
        "doc_text":                   {"type": "str",  "required": False, "description": "Supporting document text"},
        "pitch_aspect_modes":         {"type": "json", "required": False, "description": "Per-aspect inference mode overrides"},
        "excepted_inference_elements":{"type": "json", "required": False, "description": "Elements to exclude from inference"},
        "association_words":          {"type": "json", "required": True,  "description": "Exactly 3 user-supplied vibe/association words shaping the pitch"},
    },
    "output_fields": ["approach_candidates_json", "selected_archetype_id", "selected_archetype_label"],
})
register_stage({
    "id": "image_scan",
    "worker": "image_scan_worker",
    "depends": [],
    "event_trigger": True,
    "input_schema": {
        # source=filesystem: assembled at runtime from disk scan, never a DB column — audit skips it
        "intake_image_names": {"type": "json", "required": True, "source": "filesystem", "description": "Filenames of uploaded intake images"},
    },
    "output_fields": ["image_analysis"],
})
register_stage({
    "id": "human_intake",
    "human": True,
    "depends": ["approach_draft"],
    "output_fields": ["intake_options"],
})
register_stage({
    "id": "design_refs_images",
    "worker": "design_drafter_images_worker",
    "depends": ["design_refs_text"],
    "condition": "has_prototype",
    "input_schema": {
        "prototype_candidates_json": {"type": "json", "required": True, "description": "Text candidates from design_refs_text"},
    },
    # enriches prototype_candidates_json in-place (adds reference_image_path to each candidate)
    "output_fields": ["prototype_candidates_json"],
})
register_stage({
    "id": "human_prototype_selection",
    "human": True,
    "depends": ["design_refs_images"],
    "condition": "has_prototype",
    "output_fields": ["selected_prototype_id"],
})
register_stage({
    "id": "previews",
    "worker": "_run_previews",
    "depends": ["approach_draft", "human_prototype_selection", "human_intake"],
    "input_schema": {
        "approach_candidates_json": {"type": "json", "required": True, "description": "Approach candidates from approach_draft"},
    },
    # enriches approach_candidates_json in-place (adds preview_image_path to each candidate)
    "output_fields": ["approach_candidates_json"],
})
register_stage({
    "id": "human_selection",
    "human": True,
    "depends": ["previews"],
    "output_fields": ["selected_candidate_id", "selected_candidate_label", "selected_archetype_id", "selected_archetype_label"],
})
register_stage({
    "id": "human_payment",
    "human": True,
    "depends": ["human_selection"],
    "output_fields": ["explicit_slide_count"],
})
register_stage({
    "id": "generation",
    "worker": "_run_generation",
    "depends": ["human_payment"],
    "input_schema": {
        "audience":                 {"type": "str",  "required": True,  "description": "Target audience"},
        "elevator_pitch":           {"type": "str",  "required": True,  "description": "Core pitch description"},
        "conveys":                  {"type": "str",  "required": False, "description": "Key message or problem framing"},
        "approach_candidates_json": {"type": "json", "required": True,  "description": "Approach candidates (from approach_draft)"},
        "selected_candidate_id":    {"type": "str",  "required": True,  "description": "Which approach_id was selected"},
        "selected_archetype_id":    {"type": "str",  "required": False, "description": "Founder archetype ID (may come from approach blend)"},
        "explicit_slide_count":     {"type": "int",  "required": True,  "description": "Number of content slides (4–10); total = N+2"},
        "doc_text":                 {"type": "str",  "required": False, "description": "Supporting document text"},
        "image_analysis":           {"type": "json", "required": False, "description": "Result of image_scan (if image was uploaded); folded into intake context"},
        "intake_options":           {"type": "json", "required": False, "description": "Aesthetic/mockup/product-shot signals from human_intake; folded into intake context"},
        "selected_archetype_label": {"type": "str",  "required": False, "description": "Human-readable archetype label (from approach_draft/human_selection)"},
        "selected_candidate_label": {"type": "str",  "required": False, "description": "Human-readable approach label (from human_selection)"},
        # prototype path — optional; only present when infer_prototype=1
        "selected_prototype_id":    {"type": "str",  "required": False, "description": "Chosen prototype design_id (from human_prototype_selection)"},
        "prototype_candidates_json":{"type": "json", "required": False, "description": "Design candidates with reference_image_path (from design_refs_images)"},
    },
    "output_fields": ["slide_specs", "deck_proof_plan"],
})
register_stage({
    "id": "human_review",
    "human": True,
    "depends": ["generation"],
    "output_fields": ["reviewed_slides"],
})
register_stage({
    "id": "verification",
    "worker": "verification_worker",
    "depends": ["human_review"],
    "input_schema": {
        "slide_specs":      {"type": "json", "required": True,  "description": "Generated slide specifications"},
        "reviewed_slides":  {"type": "json", "required": True,  "description": "Per-slide review instructions from human_review"},
        "deck_proof_plan":  {"type": "json", "required": True,  "description": "Deck title, storyboard, and anchor narrative from generation — grounds every regeneration"},
    },
    "output_fields": [],
})


def _run_stage_in_thread(job_id: str, stage_id: str) -> None:
    """[durable-state]: run worker with failure ownership. Calls _advance on success."""
    async def _run():
        try:
            # [output-validator]: verify required inputs are non-null before making any API call.
            # Catches the case where a prior gate wrote nothing (e.g. select-prototype never called)
            # while the graph audit declared the field present. Fail-fast with named missing fields.
            stage = _stage_by_id(stage_id)
            schema = stage.get("input_schema") or {}
            if schema:
                job = _db.get_job(job_id) or {}
                missing = [
                    f for f, spec in schema.items()
                    if spec.get("required")
                    and spec.get("source") != "filesystem"
                    and not job.get(f)
                ]
                if missing:
                    err = f"Pre-dispatch field check failed — required fields are null: {missing}"
                    print(f"[STAGE BLOCKED] {job_id} / {stage_id}: {err}", flush=True)
                    _db.set_stage_state(job_id, stage_id, "failed", error=err)
                    _db.update_job(job_id, status=f"stage_failed:{stage_id}", error_message=err)
                    return
            worker_name = stage["worker"]
            if worker_name in _LOCAL_WORKERS:
                await _LOCAL_WORKERS[worker_name](job_id)
            else:
                await getattr(_workers, worker_name)(job_id)
        except Exception as e:
            import traceback, sys
            print(f"[STAGE FAILED] {job_id} / {stage_id}: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            _db.set_stage_state(job_id, stage_id, "failed", error=str(e))
            _db.update_job(job_id, status=f"stage_failed:{stage_id}")
            return
        job = _db.get_job(job_id)
        err = (job or {}).get("error_message")
        if err:
            _db.set_stage_state(job_id, stage_id, "failed", error=err)
            return
        _db.set_stage_state(job_id, stage_id, "completed")
        _advance(job_id)
    _run_async_in_thread(_run)


def _advance(job_id: str) -> None:
    """[dispatcher]: fire all eligible pending stages based on stage_states."""
    job = _db.get_job(job_id)
    if not job:
        return
    states = _db.get_stage_states(job_id)
    for stage in PIPELINE_STAGES:
        sid = stage["id"]
        if states.get(sid, "pending") != "pending":
            continue
        if stage.get("event_trigger"):
            continue
        # Human gates auto-skip if their condition is false (e.g. human_prototype_selection
        # skips when infer_prototype is not set).
        if stage.get("human"):
            condition = stage.get("condition")
            if condition and not _CONDITIONS[condition](job):
                _db.set_stage_state(job_id, sid, "skipped")
                states[sid] = "skipped"
            continue
        deps = stage["depends"]
        if not all(states.get(d) in ("completed", "skipped") for d in deps):
            continue
        condition = stage.get("condition")
        if condition and not _CONDITIONS[condition](job):
            _db.set_stage_state(job_id, sid, "skipped")
            states[sid] = "skipped"
            continue
        _db.set_stage_state(job_id, sid, "in_progress")
        states[sid] = "in_progress"
        _run_stage_in_thread(job_id, sid)


@app.on_event("startup")
def resume_incomplete_jobs():
    """On restart: audit field graph, reset in_progress stages to pending, re-dispatch."""
    _audit_pipeline()  # [preflight-check]: abort startup if field graph has gaps
    for job in _db.get_jobs_needing_resume():
        jid = job["id"]
        states = _db.get_stage_states(jid)
        if any(v == "in_progress" for v in states.values()):
            for sid, state in states.items():
                if state == "in_progress":
                    _db.set_stage_state(jid, sid, "pending", error="reset_on_restart")
            _advance(jid)
    _backfill_gallery()


def _backfill_gallery() -> None:
    """Index any preview PNGs on disk that are missing from the gallery index."""
    jobs_dir = _HERE / "local_state" / "jobs"
    indexed = set()
    if _GALLERY_INDEX.exists():
        for e in json.loads(_GALLERY_INDEX.read_text()):
            indexed.add(e.get("id", ""))
    for preview_png in sorted(jobs_dir.glob("*/single_slide_previews/approach_*.png")):
        job_id = preview_png.parent.parent.name
        approach_id = preview_png.stem  # approach_1, approach_2, etc.
        if f"{job_id}__{approach_id}" not in indexed:
            _gallery_index_preview(job_id, approach_id)


_ACTION_MAP = {
    "human_intake":    "intake_options",
    "human_selection": "select_approach",
    "human_payment":   "payment_required",
    "human_review":    "review_required",
}


# ── API: jobs ─────────────────────────────────────────────────────────────────
@app.get("/api/jobs")
def list_jobs(
    status: str | None = None,
    archetype: str | None = None,
    label: str | None = None,
    audience: str | None = None,
    vibe: str | None = None,
    limit: int = 100,
):
    return _db.filter_jobs(
        status=status, archetype=archetype, label=label,
        audience=audience, vibe=vibe, limit=limit,
    )


@app.get("/api/jobs/by-label/{label}")
def get_job_by_label(label: str):
    """Find the most recent job where selected_candidate_label or any approach label matches."""
    with _db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM jobs WHERE selected_candidate_label = ? "
            "ORDER BY created_at DESC LIMIT 1", (label,)
        ).fetchone()
        if not row:
            rows = conn.execute(
                "SELECT id, approach_candidates_json FROM jobs "
                "WHERE approach_candidates_json LIKE ? ORDER BY created_at DESC LIMIT 50",
                (f'%"{label}"%',)
            ).fetchall()
            for r in rows:
                try:
                    candidates = json.loads(r["approach_candidates_json"] or "[]")
                    if any(a.get("label") == label for a in candidates):
                        row = r
                        break
                except (json.JSONDecodeError, TypeError):
                    continue
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    job = _db.get_job(row["id"])
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = _db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        intake_dir = _workers.job_dir(job_id) / "intake"
        img_exts = {".png", ".jpg", ".jpeg", ".webp"}
        job["intake_image_names"] = (
            [f.name for f in intake_dir.iterdir() if f.suffix.lower() in img_exts]
            if intake_dir.exists() else []
        )
    except Exception:
        job["intake_image_names"] = []
    try:
        job["artifact_images"] = _collect_artifact_images(job_id)
        job["job_dir"] = str(_workers.job_dir(job_id))
    except Exception:
        job["artifact_images"] = {"preview": [], "full": [], "refined": [], "other": []}
        job["job_dir"] = ""
    return job


@app.get("/api/jobs/{job_id}/stream")
async def stream_job_progress(job_id: str):
    """SSE endpoint: pushes progress_log checkpoints and stage transitions as they happen.

    Polls the DB every 2s (SQLite is local, negligible cost). Emits:
      - data: {"type": "progress", "stage": "...", "message": "...", "pct": 0.0, "ts": ...}
      - data: {"type": "stage", "stage_id": "...", "state": "..."}
      - data: {"type": "done", "state": "completed"|"failed"}
    Plus ": heartbeat" comments every 15s to keep proxies from dropping the connection.
    """
    async def _generate():
        last_progress_count = 0
        last_stage_states: dict = {}
        idle_ticks = 0

        # Emit current state immediately so the client doesn't start from zero
        job = _db.get_job(job_id)
        if not job:
            yield f"data: {json.dumps({'type': 'error', 'message': 'job not found'})}\n\n"
            return
        for sid, sv in (job.get("stage_states") or {}).items():
            if sv != "pending":
                last_stage_states[sid] = sv
                yield f"data: {json.dumps({'type': 'stage', 'stage_id': sid, 'state': sv})}\n\n"
        for entry in (job.get("progress_log") or []):
            yield f"data: {json.dumps({'type': 'progress', **entry})}\n\n"
        last_progress_count = len(job.get("progress_log") or [])

        terminal_states = {"completed", "failed", "error"}

        while True:
            await asyncio.sleep(2)
            idle_ticks += 1

            job = _db.get_job(job_id)
            if not job:
                break

            # Push new progress_log entries
            log = job.get("progress_log") or []
            for entry in log[last_progress_count:]:
                yield f"data: {json.dumps({'type': 'progress', **entry})}\n\n"
                idle_ticks = 0
            last_progress_count = len(log)

            # Push stage state changes
            stage_states = job.get("stage_states") or {}
            for sid, sv in stage_states.items():
                if last_stage_states.get(sid) != sv:
                    last_stage_states[sid] = sv
                    yield f"data: {json.dumps({'type': 'stage', 'stage_id': sid, 'state': sv})}\n\n"
                    idle_ticks = 0

            # Terminal: all registered stages are done or skipped
            all_done = all(
                stage_states.get(s["id"], "pending") in terminal_states | {"skipped"}
                for s in PIPELINE_STAGES
                if not s.get("human") and not s.get("event_trigger")
            )
            gen_state = stage_states.get("generation")
            if gen_state in terminal_states:
                yield f"data: {json.dumps({'type': 'done', 'state': gen_state})}\n\n"
                break

            # Heartbeat every ~15s of idle
            if idle_ticks % 8 == 0:
                yield ": heartbeat\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/jobs")
async def create_job(request: Request):
    """Create a full-pitch-first job and immediately run the approach drafter."""
    body = await request.json()
    pitch_aspect_modes = body.get("pitch_aspect_modes") or {
        cat: "INFER" for cat in _prompts.PITCH_ASPECT_CATEGORIES
    }
    excepted = body.get("excepted_inference_elements") or ["pricing"]
    job_id, recovery = _db.create_job(
        problem_need="",
        audience=body.get("audience", ""),
        association_words=body.get("association_words") or [],
    )
    _db.update_job(
        job_id,
        elevator_pitch=body.get("elevator_pitch", ""),
        conveys=body.get("conveys", ""),
        doc_text=body.get("doc_text") or None,
        selected_archetype_id=body.get("archetype_id") or None,
        pitch_aspect_modes=pitch_aspect_modes,
        excepted_inference_elements=excepted,
        inferred_element_decisions={k: False for k in excepted},
        infer_prototype=1 if body.get("infer_prototype") else 0,
    )
    _advance(job_id)
    return {"job_id": job_id, "recovery_token": recovery}


@app.post("/api/jobs/{job_id}/intake")
async def intake(
    job_id: str,
    elevator_pitch: str = Form(""),
    conveys: str = Form(""),
    audience: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    """Update pitch fields and supporting file on an existing job."""
    doc_text = None
    if file and file.filename:
        content = await file.read()
        name = (file.filename or "").lower()
        ext = name.rsplit(".", 1)[-1] if "." in name else ""
        if ext in ("txt", "md"):
            doc_text = content.decode("utf-8", errors="replace")
        elif ext == "pdf":
            try:
                import pdfminer.high_level
                import io as _io
                doc_text = pdfminer.high_level.extract_text(_io.BytesIO(content))
            except Exception:
                doc_text = "[PDF attached — pdfminer unavailable]"
        elif ext in ("png", "jpg", "jpeg", "webp"):
            intake_dir = _workers.job_dir(job_id) / "intake"
            intake_dir.mkdir(exist_ok=True)
            (intake_dir / file.filename).write_bytes(content)
            _img_states = _db.get_stage_states(job_id)
            if _img_states.get("image_scan", "pending") == "pending":
                _db.set_stage_state(job_id, "image_scan", "in_progress")
                _run_stage_in_thread(job_id, "image_scan")

    update: dict = {}
    if elevator_pitch:
        update["elevator_pitch"] = elevator_pitch
    if conveys:
        update["conveys"] = conveys
    if audience:
        update["audience"] = audience
    if doc_text is not None:
        update["doc_text"] = doc_text
    if update:
        _db.update_job(job_id, **update)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/select-approach")
async def select_approach(job_id: str, request: Request):
    """Set the chosen approach_id and archetype before full-deck generation."""
    body = await request.json()
    _db.update_job(
        job_id,
        selected_candidate_id=body.get("approach_id"),
        selected_candidate_label=body.get("approach_label", ""),
        selected_archetype_id=body.get("archetype_id"),
        selected_archetype_label=body.get("archetype_label", ""),
    )
    _db.set_stage_state(job_id, "human_selection", "completed")
    _advance(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/select-prototype")
async def select_prototype(job_id: str, request: Request):
    """Mark the user's chosen design_id, complete the human_prototype_selection gate."""
    body = await request.json()
    selected_id = body.get("design_id")
    if not selected_id:
        return {"error": "design_id required"}, 400
    _db.update_job(job_id, selected_prototype_id=selected_id)
    _db.set_stage_state(job_id, "human_prototype_selection", "completed")
    _advance(job_id)
    return {"ok": True, "selected_prototype_id": selected_id}


@app.post("/api/jobs/{job_id}/mock-payment/{kind}")
async def mock_payment(job_id: str, kind: str, request: Request):
    ct = request.headers.get("content-type", "")
    body = {}
    if "application/json" in ct:
        body = await request.json()
    now = datetime.now(timezone.utc).isoformat()
    if kind == "full":
        slide_count = int(body.get("slide_count", 5))
        _db.update_job(
            job_id,
            status="paid",
            deck_payment_status="succeeded",
            deck_paid_at=now,
            deck_payment_intent_id="lab_bypass",
            explicit_slide_count=slide_count,
            error_message=None,
        )
        _db.set_stage_state(job_id, "human_payment", "completed")
        _advance(job_id)
    return {"ok": True}


@app.get("/api/steps")
def list_steps():
    """Return all pipeline stages with their input schemas.

    Human gates show which action satisfies them. Worker stages show
    what job fields must be populated before running them directly.
    """
    out = []
    for stage in PIPELINE_STAGES:
        entry: dict = {
            "id":      stage["id"],
            "depends": stage["depends"],
            "human":   bool(stage.get("human")),
        }
        if stage.get("condition"):
            entry["condition"] = stage["condition"]
        if stage.get("input_schema"):
            entry["input_schema"] = stage["input_schema"]
        if stage.get("human"):
            entry["satisfied_by"] = _ACTION_MAP.get(stage["id"], stage["id"])
        out.append(entry)
    return out


@app.get("/api/steps/{step_id}")
def get_step(step_id: str, job_id: str | None = None):
    """Return schema for one stage, optionally annotated with a job's current field values.

    If job_id is provided, each required field also shows its current value
    and whether it is populated, so you can see exactly what's missing before
    calling run/{step}.
    """
    stage = _STAGE_REGISTRY.get(step_id)
    if not stage:
        return JSONResponse({"error": f"unknown step: {step_id}"}, status_code=404)
    schema = stage.get("input_schema") or {}
    result: dict = {
        "id":      stage["id"],
        "depends": stage["depends"],
        "human":   bool(stage.get("human")),
        "input_schema": schema,
    }
    if job_id:
        job = _db.get_job(job_id)
        if not job:
            return JSONResponse({"error": f"job {job_id} not found"}, status_code=404)
        field_status = {}
        for field, meta in schema.items():
            val = job.get(field)
            populated = val is not None and val != "" and val != [] and val != {}
            field_status[field] = {
                **meta,
                "populated": populated,
                "value_preview": (
                    str(val)[:120] if isinstance(val, str)
                    else (f"[{type(val).__name__} with {len(val)} items]" if isinstance(val, (list, dict))
                          else str(val))
                ) if populated else None,
            }
        result["field_status"] = field_status
        missing = [f for f, s in field_status.items() if s["required"] and not s["populated"]]
        result["missing_required"] = missing
        result["ready"] = len(missing) == 0
    return result


@app.post("/api/jobs/{job_id}/run/{step}")
async def run_step(job_id: str, step: str, request: Request):
    """Run a pipeline step, optionally injecting field values for this run only.

    Body (optional JSON):
      {
        "fields": {          // temporarily override job fields before running
          "explicit_slide_count": 8,
          "selected_candidate_id": "approach_2",
          ...
        }
      }

    Omit the body to run with the job's current field values.
    If required fields are missing and no body overrides them, returns 409
    with {"error": "missing_fields", "missing": [...], "schema_url": "..."}.

    Legacy step aliases still work: approaches, designs, previews, paid, verification.
    """
    body: dict = {}
    ct = request.headers.get("content-type", "")
    if "application/json" in ct:
        try:
            body = await request.json()
        except Exception:
            pass
    field_overrides: dict = body.get("fields") or {}

    # Map legacy aliases to stage IDs
    _ALIASES = {
        "approaches":    "approach_draft",
        "designs":       "design_refs_text",
        "design_images": "design_refs_images",
        "previews":      "previews",
        "paid":          "generation",
        "verification":  "verification",
    }
    stage_id = _ALIASES.get(step, step)
    stage = _STAGE_REGISTRY.get(stage_id)
    if not stage:
        return JSONResponse({"error": f"unknown step: {step}"}, status_code=400)

    job = _db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)

    # Apply field overrides to job (persistent — caller owns the data)
    if field_overrides:
        _db.update_job(job_id, **field_overrides)
        job = _db.get_job(job_id)

    # Preflight: check required fields
    schema = stage.get("input_schema") or {}
    missing = [
        f for f, meta in schema.items()
        if meta["required"] and (
            job.get(f) is None or job.get(f) == "" or job.get(f) == [] or job.get(f) == {}
        )
    ]
    if missing:
        return JSONResponse({
            "error":      "missing_fields",
            "missing":    missing,
            "schema_url": f"/api/steps/{stage_id}?job_id={job_id}",
        }, status_code=409)

    # Dispatch
    if stage_id == "approach_draft":
        _db.update_job(job_id, status="created", approach_candidates_json=None, error_message=None)
        _db.set_stage_state(job_id, "approach_draft", "pending")
        _advance(job_id)
    elif stage_id == "design_refs_text":
        _run_async_in_thread(_workers.design_drafter_text_worker, job_id)
    elif stage_id == "design_refs_images":
        _run_async_in_thread(_workers.design_drafter_images_worker, job_id)
    elif stage_id == "previews":
        approaches = job.get("approach_candidates_json") or []
        approach_ids = [a["approach_id"] for a in approaches]
        async def _run_all_previews():
            await asyncio.gather(
                *[_workers.single_slide_preview_worker(job_id, aid) for aid in approach_ids],
                return_exceptions=True,
            )
        _run_async_in_thread(_run_all_previews)
    elif stage_id == "generation":
        _db.update_job(job_id, status="paid", error_message=None)
        _run_generation_in_thread(job_id)
    elif stage_id == "verification":
        _db.update_job(job_id, status="review_received", error_message=None)
        _run_async_in_thread(_workers.verification_worker, job_id)
    else:
        return JSONResponse({"error": f"step {stage_id!r} cannot be run directly"}, status_code=400)

    return {"ok": True, "started": stage_id}


@app.post("/api/jobs/{job_id}/complete-review")
async def complete_review(job_id: str, request: Request):
    """Submit per-slide review instructions and kick off verification_worker."""
    job = _db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    if job.get("status") not in {"awaiting_review", "review_received", "complete"}:
        return JSONResponse({"error": f"not ready (status: {job.get('status')})"}, status_code=409)
    body = await request.json()
    slide_specs = job.get("slide_specs") or []
    spec_map = {str(s["slide_index"]): s for s in slide_specs}
    verify_only = body.get("verify_only")  # None = all; [] = none; [3, 7] = only those
    verify_set = None if verify_only is None else {str(i) for i in verify_only}
    reviewed_slides: dict = {}
    for slide in body.get("slides", []):
        idx = str(slide.get("slide_index", ""))
        orig = spec_map.get(idx) or {}
        new_headline = slide.get("headline", orig.get("headline", ""))
        new_body = slide.get("body", " | ".join(orig.get("body_points", [])))
        new_notes = slide.get("notes", orig.get("speaker_note", ""))
        change_requests = slide.get("change_requests", "")
        edited = (
            new_headline != orig.get("headline", "")
            or new_body != " | ".join(orig.get("body_points", []))
            or new_notes != orig.get("speaker_note", "")
            or bool(change_requests.strip())
        )
        reviewed_slides[idx] = {
            "headline": new_headline,
            "body": new_body,
            "notes": new_notes,
            "change_requests": change_requests,
            "_edited": edited,
            "_reverify": (verify_set is None or idx in verify_set),
        }
    for s in slide_specs:
        idx = str(s["slide_index"])
        if idx not in reviewed_slides:
            reviewed_slides[idx] = {
                "headline": s.get("headline", ""),
                "body": " | ".join(s.get("body_points", [])),
                "notes": s.get("speaker_note", ""),
                "change_requests": "",
                "_edited": False,
                "_reverify": (verify_set is None or idx in verify_set),
            }
    _db.update_job(job_id, reviewed_slides=reviewed_slides, status="review_received")
    _db.set_stage_state(job_id, "human_review", "completed")
    _advance(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/reset-verification")
async def reset_verification(job_id: str):
    """Reset human_review and verification stages to pending so complete-review can re-trigger verification."""
    job = _db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    _db.set_stage_state(job_id, "verification", "pending")
    _db.set_stage_state(job_id, "human_review", "pending")
    return {"ok": True, "reset": ["human_review", "verification"]}


@app.get("/api/jobs/{job_id}/telemetry")
def get_telemetry(job_id: str):
    try:
        return _workers.telemetry_summary(job_id) or {"events": []}
    except Exception as e:
        return {"error": str(e), "events": []}


@app.get("/api/jobs/{job_id}/step-timing")
def get_step_timing(job_id: str):
    try:
        events_path = _workers.job_dir(job_id) / "telemetry" / "events.jsonl"
        if not events_path.exists():
            return {}
        events = []
        for line in events_path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
        stage_map = {
            "approaches_running": "approaches",
            "paid_generation": "paid",
            "verification": "verification",
        }
        starts: dict = {}
        timing: dict = {}
        for ev in events:
            if ev.get("event_type") != "workflow":
                continue
            stage = ev.get("stage", "")
            status = ev.get("status", "")
            ts = ev.get("timestamp_utc", "")
            key = stage_map.get(stage)
            if not key:
                st = stage.lower()
                if "approach" in st:
                    key = "approaches"
                elif "paid" in st or "generation" in st:
                    key = "paid"
                elif "verif" in st or "final" in st:
                    key = "verification"
                elif "preview" in st or "single" in st:
                    key = "previews"
            if not key or not ts:
                continue
            if status == "started":
                starts[key] = ts
            elif key in starts:
                try:
                    t_start = datetime.fromisoformat(starts[key])
                    t_end = datetime.fromisoformat(ts)
                    dur = int((t_end - t_start).total_seconds())
                    if dur >= 0:
                        timing[key] = dur
                except Exception:
                    pass
        return timing
    except Exception:
        return {}


@app.get("/api/jobs/{job_id}/output-dir", response_class=HTMLResponse)
def output_dir(job_id: str):
    try:
        job_dir = _workers.job_dir(job_id).resolve()
    except Exception:
        return HTMLResponse("<h1>Job not found</h1>", status_code=404)
    rows = []
    for f in sorted(job_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(job_dir).as_posix()
            size = f.stat().st_size
            rows.append(
                f'<tr><td><a href="/api/files/{job_id}/{rel}" target="_blank">{rel}</a></td>'
                f'<td>{size:,} bytes</td></tr>'
            )
    listing = "".join(rows) or '<tr><td colspan="2">No files yet.</td></tr>'
    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
    <style>body{{font-family:monospace;background:#09090c;color:#c4cfe0;padding:18px}}
    a{{color:#00d4ff}}table{{border-collapse:collapse;width:100%;margin-top:14px}}
    td{{border-bottom:1px solid #252535;padding:5px 7px}}</style></head><body>
    <h1>{job_id}</h1><p style="color:#5a6880">{job_dir}</p>
    <table><tr><th>file</th><th>size</th></tr>{listing}</table></body></html>""")


@app.get("/api/files/{job_id}/{rest_path:path}")
def serve_file(job_id: str, rest_path: str):
    try:
        job_dir = _workers.job_dir(job_id)
    except Exception:
        return JSONResponse({"error": "job not found"}, status_code=404)
    safe = (job_dir / rest_path).resolve()
    if not str(safe).startswith(str(job_dir.resolve())):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not safe.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    mime = {
        ".png": "image/png", ".webp": "image/webp",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".json": "application/json",
    }.get(safe.suffix.lower(), "application/octet-stream")
    return FileResponse(str(safe), media_type=mime)


@app.get("/api/prompts")
def get_prompts():
    return {"source": _PROMPTS_FILE.read_text(encoding="utf-8")}


@app.post("/api/prompts")
async def save_prompts(request: Request):
    body = await request.json()
    source = body.get("source", "")
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8", delete=False) as f:
        f.write(source)
        tmp = f.name
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        os.unlink(tmp)
        return JSONResponse({"error": str(e)}, status_code=422)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    _PROMPTS_FILE.write_text(source, encoding="utf-8")
    try:
        importlib.reload(_prompts)
    except Exception:
        pass
    return {"ok": True}


@app.post("/api/prompts/commit")
async def commit_prompts():
    """Commit prompts.py (and workers.py if staged) to the workshop repo."""
    log_lines = []
    files_to_stage = ["prompts.py", "workers.py", "db.py"]
    for rel in files_to_stage:
        subprocess.run(["git", "add", rel], capture_output=True, cwd=str(_HERE))
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(_HERE), timeout=10,
    )
    if diff.returncode == 0:
        return {"ok": True, "log": "Nothing staged — all files match HEAD"}
    commit = subprocess.run(
        ["git", "commit", "-m", "Update workshop pipeline via workshop lab"],
        capture_output=True, text=True, timeout=15, cwd=str(_HERE),
    )
    log_lines.append((commit.stdout + commit.stderr).strip())
    if commit.returncode != 0:
        return JSONResponse({"ok": False, "step": "commit", "log": "\n".join(log_lines)}, status_code=500)
    push = subprocess.run(
        ["git", "push", "origin", "main"],
        capture_output=True, text=True, timeout=30, cwd=str(_HERE),
    )
    log_lines.append((push.stdout + push.stderr).strip())
    if push.returncode != 0:
        return JSONResponse({"ok": False, "step": "push", "log": "\n".join(log_lines)}, status_code=500)
    return {"ok": True, "log": "\n".join(l for l in log_lines if l)}


@app.post("/api/jobs/{job_id}/intake-options")
async def set_intake_options(job_id: str, request: Request):
    """Save intake options, satisfy human_intake gate, advance pipeline."""
    job = _db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    payload = await request.json()
    _db.update_job(job_id, intake_options=json.dumps(payload))
    _db.set_stage_state(job_id, "human_intake", "completed")
    _advance(job_id)
    return {"ok": True, "image_analysis": job.get("image_analysis")}


@app.get("/api/jobs/{job_id}/image-analysis")
def get_image_analysis(job_id: str):
    """Poll for image_scan completion."""
    job = _db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    states = _db.get_stage_states(job_id)
    scan_state = states.get("image_scan", "pending")
    analysis = job.get("image_analysis")
    return {"status": "ready" if analysis else scan_state, "analysis": analysis}


@app.get("/api/jobs/{job_id}/next-action")
def next_action(job_id: str):
    """Return the current blocking human stage.

    UI calls this to decide what to render: intake form, approach selection,
    paywall, review modal, or processing/complete state.
    """
    job = _db.get_job(job_id)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    states = _db.get_stage_states(job_id)

    failed_stages = [s for s, v in states.items() if v == "failed"]
    if failed_stages:
        return {
            "stage": "failed",
            "action": "stage_failed",
            "failed_stages": failed_stages,
            "error": job.get("error_message"),
        }

    for stage in PIPELINE_STAGES:
        if not stage.get("human"):
            continue
        sid = stage["id"]
        if states.get(sid, "pending") not in ("pending",):
            continue
        deps = stage["depends"]
        if not all(states.get(d) in ("completed", "skipped") for d in deps):
            continue
        context: dict = {}
        if sid == "human_payment":
            context["amount_cents"] = job.get("checkout_amount_cents") or 2500
            context["slide_count"] = job.get("explicit_slide_count") or 5
        return {"stage": sid, "action": _ACTION_MAP.get(sid, sid), "context": context}

    in_progress = [s for s, v in states.items() if v == "in_progress"]
    if in_progress:
        return {"stage": None, "action": "processing", "active_stages": in_progress}
    if job.get("status") in {"complete", "error", "expired"}:
        return {"stage": None, "action": "complete"}
    return {"stage": None, "action": "processing", "active_stages": []}


@app.post("/api/jobs/{job_id}/identify")
async def identify_job(job_id: str, request: Request):
    """Merge user identity into job. When email present, aliases PostHog job_id → email."""
    payload = await request.json()
    _db.set_identity(job_id, payload)
    return {"ok": True}


@app.get("/api/gallery/previews")
def gallery_previews(archetype_id: str = None, selected_only: bool = False):
    """Return indexed preview gallery, optionally filtered by archetype or selected-only."""
    if not _GALLERY_INDEX.exists():
        return []
    entries = json.loads(_GALLERY_INDEX.read_text())
    if archetype_id:
        entries = [e for e in entries if e.get("archetype_id") == archetype_id]
    if selected_only:
        entries = [e for e in entries if e.get("selected")]
    return entries


# ── UI ────────────────────────────────────────────────────────────────────────
_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>PS Workshop Lab</title>
<style>
:root {
  --bg:#09090c;--bg2:#10101a;--bg3:#18182a;--border:#252535;
  --text:#c4cfe0;--dim:#5a6880;--accent:#00d4ff;--green:#00e87a;
  --warn:#ff6b35;--red:#ff4455;--font:'IBM Plex Mono','Courier New',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:12px;height:100vh;overflow:hidden}
::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:var(--bg)}::-webkit-scrollbar-thumb{background:var(--border)}
.shell{display:grid;grid-template-rows:40px 1fr;height:100vh}
.hdr{display:flex;align-items:center;padding:0 14px;background:var(--bg2);border-bottom:1px solid var(--border);gap:14px}
.hdr-brand{font-size:10px;letter-spacing:3px;text-transform:uppercase;color:var(--accent);margin-right:4px}
.tab{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--dim);cursor:pointer;padding:3px 6px;border:1px solid transparent}
.tab.active{color:var(--text);border-color:var(--border)}
.hdr-info{margin-left:auto;font-size:10px;color:var(--dim)}
.jobs-view{display:grid;grid-template-columns:256px 1fr;height:100%;overflow:hidden}
.list-panel{background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.list-hdr{padding:8px 10px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}
.list-title{font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--dim)}
.btn-new{font-family:var(--font);font-size:10px;background:var(--accent);color:var(--bg);border:none;padding:3px 8px;cursor:pointer}
.btn-new:hover{opacity:.85}
.new-form{padding:10px;border-bottom:1px solid var(--border);background:var(--bg3);display:none}
.new-form.open{display:block}
.fl{display:flex;flex-direction:column;gap:3px;margin-bottom:7px}
.fl label{font-size:9px;text-transform:uppercase;letter-spacing:1px;color:var(--dim)}
.fi{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:var(--font);font-size:11px;padding:4px 6px;outline:none}
.fi:focus{border-color:var(--accent)}
textarea.fi{resize:vertical;line-height:1.5}
.btn{font-family:var(--font);font-size:10px;border:1px solid var(--border);background:none;color:var(--text);padding:4px 8px;cursor:pointer}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-p{background:var(--accent);color:var(--bg);border-color:var(--accent)}
.btn-p:hover{opacity:.85}
.btn-g{border-color:var(--green);color:var(--green)}
.btn-g:hover{background:var(--green);color:var(--bg)}
.btn-w{border-color:var(--warn);color:var(--warn)}
.btn-w:hover{background:var(--warn);color:var(--bg)}
.btn-r{border-color:var(--red);color:var(--red)}
.btn-r:hover{background:var(--red);color:var(--bg)}
.btn-sm{padding:2px 6px;font-size:10px}
.jlist{flex:1;overflow-y:auto}
.ji{padding:8px 10px;border-bottom:1px solid var(--border);cursor:pointer}
.ji:hover{background:var(--bg3)}
.ji.sel{background:var(--bg3);border-left:2px solid var(--accent)}
.ji-id{font-size:10px;color:var(--accent);margin-bottom:2px;word-break:break-all}
.ji-st{font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:1px}
.ji-t{font-size:9px;color:var(--dim)}
.det{overflow-y:auto;padding:14px}
.det-empty{display:flex;align-items:center;justify-content:center;height:100%;color:var(--dim);font-size:11px}
.jh{margin-bottom:14px}
.jh-id{font-size:11px;color:var(--accent);margin-bottom:3px}
.jh-meta{font-size:10px;color:var(--dim)}
.jh-badge{display:inline-block;font-size:9px;text-transform:uppercase;letter-spacing:1px;padding:1px 5px;border:1px solid var(--border);margin-left:6px;vertical-align:middle}
.sec{margin-bottom:12px;border:1px solid var(--border);background:var(--bg2)}
.sec-hdr{padding:7px 10px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;cursor:pointer;user-select:none}
.sec-hdr:hover{background:var(--bg3)}
.sec-ttl{font-size:9px;text-transform:uppercase;letter-spacing:2px;color:var(--dim)}
.sec-st{font-size:9px;color:var(--dim)}
.sec-body{padding:10px}
.sec.col .sec-body{display:none}
.dot{display:inline-block;width:5px;height:5px;border-radius:50%;margin-right:4px;vertical-align:middle}
.dot-ok{background:var(--green)}.dot-run{background:var(--accent);animation:pulse 1s infinite}
.dot-err{background:var(--red)}.dot-pend{background:var(--dim)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.spin{display:inline-block;width:8px;height:8px;border:1px solid var(--accent);border-top-color:transparent;border-radius:50%;animation:spin .8s linear infinite;margin-left:5px;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
.approach-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px}
.ac{border:2px solid var(--border);cursor:pointer;position:relative;overflow:hidden;background:var(--bg3)}
.ac img{width:100%;height:auto;display:block}
.ac.sel{border-color:var(--accent)}
.ac-lbl{background:rgba(0,0,0,.75);font-size:8px;text-transform:uppercase;letter-spacing:1px;padding:3px 5px;color:var(--text)}
.ac-angle{font-size:9px;color:var(--dim);padding:4px 5px 5px;line-height:1.4}
.artifact-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:8px;margin:8px 0}
.artifact-card{border:1px solid var(--border);background:var(--bg3);padding:5px;min-width:0}
.artifact-card img{width:100%;height:auto;display:block;border:1px solid var(--border);cursor:pointer;background:var(--bg)}
.artifact-card .cap{font-size:9px;color:var(--dim);margin-top:4px;display:flex;justify-content:space-between;gap:6px;align-items:center}
.artifact-card .cap a{color:var(--accent);text-decoration:none}
.stage-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:8px}
.sel-row{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:6px}
.sel-row select{background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:var(--font);font-size:10px;padding:3px 6px}
.info{font-size:10px;color:var(--dim);margin-top:5px;line-height:1.6}
.err-box{background:rgba(255,68,85,.1);border:1px solid var(--red);color:var(--red);padding:6px 8px;font-size:10px;margin-top:6px;word-break:break-all}
.tbl{width:100%;border-collapse:collapse;font-size:10px}
.tbl th{text-align:left;padding:3px 6px;border-bottom:1px solid var(--border);color:var(--dim);font-weight:normal;font-size:9px;text-transform:uppercase;letter-spacing:1px}
.tbl td{padding:3px 6px;border-bottom:1px solid var(--border)}
.tbl td:first-child{color:var(--dim)}
.prompts-view{display:flex;flex-direction:column;height:100%;padding:12px;gap:8px}
.p-hdr{display:flex;gap:6px;align-items:center}
.p-title{font-size:9px;text-transform:uppercase;letter-spacing:2px;color:var(--dim)}
.p-fb{font-size:10px;padding:2px 6px}.p-fb.ok{color:var(--green)}.p-fb.err{color:var(--red)}
.p-ta{flex:1;background:var(--bg2);border:1px solid var(--border);color:var(--text);font-family:var(--font);font-size:11px;padding:10px;resize:none;outline:none;line-height:1.6;tab-size:4}
.p-ta:focus{border-color:var(--accent)}
.lbox{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;align-items:center;justify-content:center;z-index:100;cursor:pointer}
.lbox.on{display:flex}
.lbox img{max-width:92vw;max-height:92vh;object-fit:contain}
</style>
</head>
<body>
<div class="shell">
  <div class="hdr">
    <div class="hdr-brand">PS Workshop</div>
    <div class="tab active" id="tab-jobs" onclick="sv('jobs')">Jobs</div>
    <div class="tab" id="tab-prompts" onclick="sv('prompts')">Prompts</div>
    <div class="hdr-info">port LAB_PORT · LAB_PATH</div>
  </div>

  <div id="v-jobs" class="jobs-view">
    <div class="list-panel">
      <div class="list-hdr">
        <div class="list-title">Jobs</div>
        <button class="btn-new" onclick="togNJ()">+ New</button>
      </div>
      <div class="new-form" id="new-form">
        <div class="fl"><label>Audience</label>
          <textarea class="fi" id="nj-aud" rows="2" placeholder="Who is this pitch for?"></textarea>
        </div>
        <div class="fl"><label>Elevator Pitch</label>
          <textarea class="fi" id="nj-pitch" rows="4" placeholder="Problem + solution in the founder's own words"></textarea>
        </div>
        <div class="fl"><label>Conveys (impression the deck must land)</label>
          <textarea class="fi" id="nj-conveys" rows="2" placeholder="What should investors believe after seeing this?"></textarea>
        </div>
        <div style="display:flex;gap:5px">
          <button class="btn btn-p btn-sm" onclick="createJob()">Create + Draft Approaches</button>
          <button class="btn btn-sm" onclick="togNJ()">Cancel</button>
        </div>
      </div>
      <div class="jlist" id="jlist">
        <div style="padding:10px;color:var(--dim)">Loading...</div>
      </div>
    </div>

    <div class="det" id="det">
      <div class="det-empty">Select a job or create a new one</div>
    </div>
  </div>

  <div id="v-prompts" class="prompts-view" style="display:none">
    <div class="p-hdr">
      <span class="p-title">prompts.py</span>
      <button class="btn btn-p btn-sm" onclick="savePrompts()">Save</button>
      <button class="btn btn-sm" onclick="loadPrompts()">Reload</button>
      <button class="btn btn-w btn-sm" id="btn-deploy" onclick="deployPrompts()">Commit &amp; Push</button>
      <span class="p-fb" id="p-fb"></span>
    </div>
    <textarea class="p-ta" id="p-ta" spellcheck="false"></textarea>
    <div id="p-deploy-log" style="display:none;background:var(--bg2);border:1px solid var(--border);padding:8px 10px;font-size:10px;line-height:1.6;white-space:pre-wrap;word-break:break-all;max-height:120px;overflow-y:auto"></div>
  </div>
</div>

<div class="lbox" id="lbox" onclick="closeLbox()">
  <img id="lbox-img" src="" alt="">
</div>

<script>
let selJob = null, pollT = null, selApproachId = null, runningSteps = new Set(), stepTiming = {};

function sv(tab) {
  document.getElementById('v-jobs').style.display = tab==='jobs'?'grid':'none';
  document.getElementById('v-prompts').style.display = tab==='prompts'?'flex':'none';
  document.getElementById('tab-jobs').classList.toggle('active',tab==='jobs');
  document.getElementById('tab-prompts').classList.toggle('active',tab==='prompts');
  if (tab==='prompts') loadPrompts();
}

async function loadJobs() {
  try {
    const jobs = await req('/api/jobs');
    const el = document.getElementById('jlist');
    if (!jobs.length) { el.innerHTML='<div style="padding:10px;color:var(--dim)">No jobs yet</div>'; return; }
    el.innerHTML = jobs.map(j=>`
      <div class="ji ${selJob&&selJob.id===j.id?'sel':''}" onclick="pickJob('${j.id}')">
        <div class="ji-id">${j.id}</div>
        <div class="ji-st">${dot(j.status)} ${j.status}</div>
        <div class="ji-t">${rel(j.updated_at)}</div>
      </div>`).join('');
  } catch(e) { console.error(e); }
}

function dot(s) {
  const cls = s&&(s.includes('running')||s.includes('started')||s.includes('generating')||s.includes('queued')||s.includes('finalizing'))?'run'
    :s&&(s.includes('failed')||s==='failed')?'err'
    :(s==='approaches_ready'||s==='awaiting_review'||s==='complete')?'ok':'pend';
  return `<span class="dot dot-${cls}"></span>`;
}

function rel(iso) {
  if (!iso) return '';
  const s = Math.floor((Date.now()-new Date(iso.replace(' ','T')))/1000);
  return s<60?`${s}s ago`:s<3600?`${Math.floor(s/60)}m ago`:`${Math.floor(s/3600)}h ago`;
}

function fmtTime(secs) {
  if (!secs&&secs!==0) return '';
  return secs<60?secs+'s':Math.floor(secs/60)+'m '+(secs%60)+'s';
}

async function pickJob(id) {
  selJob={id}; selApproachId=null; runningSteps.clear(); stepTiming={};
  stopPoll();
  await refreshJob();
  await loadJobs();
  startPoll();
}

async function refreshJob() {
  if (!selJob) return;
  try {
    const [j, timing] = await Promise.all([
      req('/api/jobs/'+selJob.id),
      req('/api/jobs/'+selJob.id+'/step-timing').catch(()=>({})),
    ]);
    selJob=j; stepTiming=timing||{};
    const s=j.status||'';
    if ((j.approach_candidates_json||[]).length>0) runningSteps.delete('approaches');
    const prevImgs=(j.artifact_images||{}).preview||[];
    if (prevImgs.length>0) runningSteps.delete('previews');
    if (['awaiting_review','review_received','complete'].includes(s)) runningSteps.delete('paid');
    if (s==='complete') runningSteps.delete('verification');
    if (s.includes('failed')) runningSteps.clear();
    renderDet(j);
  } catch(e) { console.error(e); }
}

function startPoll() {
  if (pollT) return;
  pollT = setInterval(async()=>{
    if (!selJob){stopPoll();return;}
    const s=selJob.status||'';
    if (s.includes('running')||s.includes('started')||s.includes('generating')||s.includes('queued')||runningSteps.size>0){
      await refreshJob(); await loadJobs();
    }
  }, 3000);
}
function stopPoll(){if(pollT){clearInterval(pollT);pollT=null;}}

function imgUrl(jobId,path,ts){return `/api/files/${jobId}/${path}?t=${encodeURIComponent(ts||'')}`;}

function artifactGrid(j, items, emptyText='No images yet'){
  if (!items||!items.length) return `<div class="info">${esc(emptyText)}</div>`;
  return `<div class="artifact-grid">${items.map((it,i)=>{
    const src=imgUrl(j.id,it.path,(j.updated_at||'')+':'+(it.mtime||''));
    return `<div class="artifact-card">
      <img src="${src}" loading="lazy" onerror="this.style.opacity='.3'" onclick="openLbox(this.src)">
      <div class="cap"><span>${esc(it.label||'artifact '+(i+1))}</span><a href="${src}" target="_blank">open</a></div>
    </div>`;
  }).join('')}</div>`;
}

function renderDet(j) {
  const det=document.getElementById('det');
  det.innerHTML=`
    <div class="jh">
      <div class="jh-id">${j.id}<span class="jh-badge">${j.status}</span></div>
      <div class="jh-meta">Created ${j.created_at}</div>
      <div class="stage-actions">
        <a class="btn btn-sm" href="/api/jobs/${j.id}/output-dir" target="_blank">Output Dir</a>
        ${j.job_dir?`<span style="font-size:9px;color:var(--dim);word-break:break-all">${esc(j.job_dir)}</span>`:''}
      </div>
      ${j.error_message?`<div class="err-box">${esc(j.error_message)}</div>`:''}
    </div>
    ${secIntake(j)}${secApproaches(j)}${secPreviews(j)}${secFullDeck(j)}${secReview(j)}${secVerification(j)}${secTelem(j)}`;
}

function secIntake(j) {
  const done = j.elevator_pitch && j.audience;
  const imgs = j.intake_image_names||[];
  return `<div class="sec">
    <div class="sec-hdr" onclick="togSec(this)">
      <div class="sec-ttl">${dot(done?'ok':'pend')} 1 · Pitch Intake</div>
      <div class="sec-st">${done?'complete':''}</div>
    </div>
    <div class="sec-body">
      <div class="info"><b>Audience:</b> ${esc(j.audience||'—')}</div>
      <div class="info"><b>Conveys:</b> ${esc(j.conveys||'—')}</div>
      <div class="info" style="margin-bottom:8px"><b>Elevator pitch:</b> ${esc((j.elevator_pitch||'').substring(0,200))}${(j.elevator_pitch||'').length>200?'…':''}</div>
      ${imgs.length?`<div class="info" style="color:var(--green)">✓ intake image: ${esc(imgs.join(', '))}</div>`:''}
      ${j.doc_text?`<div class="info" style="color:var(--green)">✓ supporting doc (${j.doc_text.length} chars)</div>`:''}
      <form onsubmit="doIntake(event,'${j.id}')" style="margin-top:10px">
        <div class="fl"><label>Supporting file (image or .txt/.md/.pdf)</label>
          <input type="file" id="if-${j.id}" accept=".txt,.md,.pdf,.png,.jpg,.jpeg,.webp" style="color:var(--dim);font-size:10px">
        </div>
        <button type="submit" class="btn btn-sm">Update File</button>
      </form>
    </div>
  </div>`;
}

function secApproaches(j) {
  const approaches = j.approach_candidates_json || [];
  const isRunning = (j.status||'').includes('approaches_running') || runningSteps.has('approaches');
  const timing = stepTiming.approaches ? ' · '+fmtTime(stepTiming.approaches) : '';
  const curSel = selApproachId || j.selected_candidate_id;
  let body = '';
  if (isRunning) {
    body = `<div class="info">Drafting approaches…<span class="spin"></span></div>`;
  } else if (approaches.length) {
    body = `<div class="approach-grid">${approaches.map(a => {
      const previewPath = 'single_slide_previews/'+a.approach_id+'.png';
      const src = imgUrl(j.id, previewPath, j.updated_at);
      return `<div class="ac ${curSel===a.approach_id?'sel':''}" data-aid="${a.approach_id}" onclick="pickApproach('${a.approach_id}')">
        <img src="${src}" loading="lazy" onerror="this.style.display='none'" onclick="event.stopPropagation();openLbox(this.src)">
        <div class="ac-lbl">${esc(a.approach_id)} · ${esc(a.label||'')}</div>
        <div class="ac-angle">${esc((a.pitch_angle||'').substring(0,120))}…</div>
      </div>`;
    }).join('')}</div>
    <div class="sel-row">
      <button class="btn btn-sm" onclick="runStep('${j.id}','previews')">▶ Run Previews</button>
      <button class="btn btn-p btn-sm" onclick="doSelectApproach('${j.id}')">✓ Select This Approach</button>
      <button class="btn btn-sm" onclick="runStep('${j.id}','approaches')">↺ Re-draft</button>
    </div>
    ${curSel?`<div class="info" style="margin-top:5px;color:var(--green)">Selected: ${curSel}</div>`:''}`;
  } else {
    body = `<button class="btn btn-g btn-sm" onclick="runStep('${j.id}','approaches')">▶ Draft Approaches</button>`;
  }
  return `<div class="sec">
    <div class="sec-hdr" onclick="togSec(this)">
      <div class="sec-ttl">${dot(!isRunning&&approaches.length?'ok':isRunning?'run':'pend')} 2 · Approaches</div>
      <div class="sec-st">${!isRunning&&approaches.length?approaches.length+' approaches · archetype: '+(j.selected_archetype_label||'auto-picked')+timing:isRunning?'running…':''}</div>
    </div>
    <div class="sec-body">${body}</div>
  </div>`;
}

function secPreviews(j) {
  const prevImgs = (j.artifact_images||{}).preview||[];
  const isRunning = runningSteps.has('previews');
  const timing = stepTiming.previews?' · '+fmtTime(stepTiming.previews):'';
  if (!(j.approach_candidates_json||[]).length && !isRunning) return '';
  let body = isRunning
    ? `<div class="info">Generating single-slide previews…<span class="spin"></span></div>`
    : prevImgs.length
      ? artifactGrid(j, prevImgs)+'<div class="stage-actions"><button class="btn btn-sm" onclick="runStep(\''+j.id+'\',\'previews\')">↺ Re-run</button></div>'
      : `<button class="btn btn-sm" onclick="runStep('${j.id}','previews')">▶ Run Previews</button>`;
  return `<div class="sec">
    <div class="sec-hdr" onclick="togSec(this)">
      <div class="sec-ttl">${dot(!isRunning&&prevImgs.length?'ok':isRunning?'run':'pend')} 3 · Single-Slide Previews</div>
      <div class="sec-st">${!isRunning&&prevImgs.length?prevImgs.length+' previews'+timing:isRunning?'running…':''}</div>
    </div>
    <div class="sec-body">${body}</div>
  </div>`;
}

function secFullDeck(j) {
  const paid = j.deck_payment_status==='succeeded';
  const status = j.status||'';
  const proofImgs = (j.artifact_images||{}).full||[];
  const pastGeneration = ['awaiting_review','review_received','finalization_queued','finalizing','complete'].includes(status);
  const done = pastGeneration||proofImgs.length>0;
  const isRunning = runningSteps.has('paid')||(paid&&!pastGeneration&&(status.includes('generating')||status.includes('rendering')||status.includes('plan')));
  const timing = stepTiming.paid?' · '+fmtTime(stepTiming.paid):'';
  const selApproach = selApproachId || j.selected_candidate_id;
  let body = '';
  if (!selApproach) {
    body = `<div class="info">Select an approach above first</div>`;
  } else if (!paid) {
    body = `<div class="sel-row">
      <label style="font-size:9px;color:var(--dim);text-transform:uppercase;letter-spacing:1px">Content slides</label>
      <select id="slides-${j.id}">
        <option value="5">5 slides (6 total w/ title)</option>
        <option value="8">8 slides (9 total)</option>
        <option value="13">13 slides (14 total)</option>
        <option value="16">16 slides (17 total)</option>
      </select>
      <button class="btn btn-w btn-sm" onclick="mockFullPay('${j.id}')">⚡ Mock Payment + Run</button>
    </div><div class="info" style="margin-top:4px">Approach: ${selApproach}</div>`;
  } else if (isRunning) {
    body = `<div class="info">Generating full deck…<span class="spin"></span></div>`;
    if (proofImgs.length) body += artifactGrid(j, proofImgs);
  } else {
    body = artifactGrid(j, proofImgs, pastGeneration?'Generated — no proof images matched patterns yet.':'Run paid generation.')
      +`<div class="stage-actions">
        <button class="btn ${proofImgs.length?'':'btn-g'} btn-sm" onclick="rerunPaid('${j.id}')">${proofImgs.length?'↺ Re-run Full Deck':'▶ Run Full Deck'}</button>
        <a class="btn btn-sm" href="/api/jobs/${j.id}/output-dir" target="_blank">Output Dir</a>
      </div>`;
  }
  return `<div class="sec">
    <div class="sec-hdr" onclick="togSec(this)">
      <div class="sec-ttl">${dot(done?'ok':isRunning?'run':'pend')} 4 · Full Deck Generation</div>
      <div class="sec-st">${done?'generated'+timing:isRunning?'running…':paid?status:''}</div>
    </div>
    <div class="sec-body">${body}</div>
  </div>`;
}

function secReview(j) {
  const status=j.status||'';
  if (!['awaiting_review','review_received','finalization_queued','finalizing','complete'].includes(status)) return '';
  const slides=j.slide_specs||[];
  const reviewed=j.reviewed_slides||{};
  const finalizing=['finalization_queued','finalizing'].includes(status)||runningSteps.has('verification');
  const complete=status==='complete';
  const slideCards=slides.map(s=>{
    const idx=s.slide_index;
    const key=String(idx);
    const saved=reviewed[key]||{};
    const pad=String(idx).padStart(2,'0');
    const imgSrc=imgUrl(j.id,'slides/slide_'+pad+'_proof.png',j.updated_at);
    const headline=saved.headline!==undefined?saved.headline:(s.headline||'');
    const changes=saved.change_requests||'';
    return `<div style="display:grid;grid-template-columns:220px 1fr;gap:10px;margin-bottom:14px;padding-bottom:14px;border-bottom:1px solid var(--border)">
      <div>
        <img src="${imgSrc}" style="width:220px;height:auto;border:1px solid var(--border);display:block;cursor:pointer" onclick="openLbox(this.src)" onerror="this.style.opacity='.3'">
        <div style="font-size:9px;color:var(--dim);margin-top:4px">Slide ${idx}</div>
      </div>
      <div>
        <div style="font-size:9px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:3px">Headline</div>
        <input class="rev-inp" data-job="${j.id}" data-idx="${idx}" data-field="headline"
               value="${esc(headline)}"
               style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:var(--font);font-size:11px;padding:4px 6px;margin-bottom:6px"
               ${finalizing?'disabled':''}>
        <div style="font-size:9px;text-transform:uppercase;letter-spacing:1px;color:var(--dim);margin-bottom:3px">Revision instructions</div>
        <textarea class="rev-inp" data-job="${j.id}" data-idx="${idx}" data-field="change_requests"
                  rows="3"
                  style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:var(--font);font-size:11px;padding:4px 6px;resize:vertical"
                  ${finalizing?'disabled':''}
                  placeholder="e.g. remove the slide number; strengthen the headline; fix the diagram label">${esc(changes)}</textarea>
      </div>
    </div>`;
  }).join('');
  return `<div class="sec">
    <div class="sec-hdr" onclick="togSec(this)">
      <div class="sec-ttl">${dot(complete?'ok':finalizing?'run':'pend')} 5 · Review &amp; Verification Instructions</div>
      <div class="sec-st">${complete?'complete':finalizing?'verifying…':'awaiting review'}</div>
    </div>
    <div class="sec-body">
      ${slideCards}
      ${!finalizing?`<div class="stage-actions">
        <button class="btn btn-g btn-sm" onclick="completeReview('${j.id}')">${complete?'↺ Re-verify':'✓ Submit &amp; Verify'}</button>
        <button class="btn btn-sm" onclick="rerunPaid('${j.id}')">↺ Re-run Full Deck</button>
      </div>`:''}
    </div>
  </div>`;
}

function secVerification(j) {
  const status=j.status||'';
  if (!['finalization_queued','finalizing','complete'].includes(status)) return '';
  const finalizing=['finalization_queued','finalizing'].includes(status)||runningSteps.has('verification');
  const complete=status==='complete';
  const refinedImgs=(j.artifact_images||{}).refined||[];
  const timing=stepTiming.verification?' · '+fmtTime(stepTiming.verification):'';
  return `<div class="sec">
    <div class="sec-hdr" onclick="togSec(this)">
      <div class="sec-ttl">${dot(complete?'ok':finalizing?'run':'pend')} 6 · Verification &amp; Export</div>
      <div class="sec-st">${complete?'complete'+timing:finalizing?'running…':''}</div>
    </div>
    <div class="sec-body">
      ${finalizing?`<div class="info">Running verification pipeline…<span class="spin"></span></div>`:''}
      ${artifactGrid(j,refinedImgs,complete?'Verification complete.':'Final exports appear here.')}
      <div class="stage-actions">
        <a class="btn btn-sm" href="/api/jobs/${j.id}/output-dir" target="_blank">Output Dir</a>
      </div>
    </div>
  </div>`;
}

function secTelem(j) {
  return `<div class="sec col">
    <div class="sec-hdr" onclick="loadTelem('${j.id}',this)">
      <div class="sec-ttl"><span class="dot dot-pend"></span> Telemetry</div>
      <div class="sec-st" style="color:var(--accent)">click to load</div>
    </div>
    <div class="sec-body" id="tb-${j.id}">Loading…</div>
  </div>`;
}

// ── Actions ───────────────────────────────────────────────────────────────────
function togNJ(){document.getElementById('new-form').classList.toggle('open');}
function togSec(h){h.closest('.sec').classList.toggle('col');}

async function createJob() {
  const aud=document.getElementById('nj-aud').value.trim();
  const pitch=document.getElementById('nj-pitch').value.trim();
  const conveys=document.getElementById('nj-conveys').value.trim();
  if (!aud||!pitch) { alert('Audience and elevator pitch are required'); return; }
  const r=await req('/api/jobs','POST',{audience:aud,elevator_pitch:pitch,conveys:conveys});
  togNJ();
  await loadJobs();
  await pickJob(r.job_id);
}

function pickApproach(aid) {
  selApproachId=aid;
  document.querySelectorAll('.ac').forEach(el=>el.classList.toggle('sel',el.dataset.aid===aid));
}

async function doSelectApproach(jobId) {
  if (!selApproachId) { alert('Click an approach card first'); return; }
  const job=selJob;
  const approach=(job.approach_candidates_json||[]).find(a=>a.approach_id===selApproachId)||{};
  await req('/api/jobs/'+jobId+'/select-approach','POST',{
    approach_id: selApproachId,
    approach_label: approach.label||'',
    archetype_id: job.selected_archetype_id||'',
    archetype_label: job.selected_archetype_label||'',
  });
  await refreshJob();
}

async function doIntake(e, jobId) {
  e.preventDefault();
  const fileEl=document.getElementById('if-'+jobId);
  const fd=new FormData();
  if (fileEl.files[0]) fd.append('file',fileEl.files[0]);
  await fetch('/api/jobs/'+jobId+'/intake',{method:'POST',body:fd});
  await refreshJob();
}

async function mockFullPay(jobId) {
  const slideCount=parseInt((document.getElementById('slides-'+jobId)||{}).value||'5',10);
  await req('/api/jobs/'+jobId+'/mock-payment/full','POST',{slide_count:slideCount});
  runningSteps.add('paid');
  if (selJob) renderDet(selJob);
  await req('/api/jobs/'+jobId+'/run/paid','POST',{});
  startPoll();
  setTimeout(refreshJob,500);
}

async function rerunPaid(jobId) {
  _db_update_status=null;
  runningSteps.add('paid');
  if (selJob) renderDet(selJob);
  await req('/api/jobs/'+jobId+'/run/paid','POST',{});
  startPoll();
  setTimeout(refreshJob,500);
}

async function runStep(jobId, step) {
  runningSteps.add(step);
  if (selJob) renderDet(selJob);
  await req('/api/jobs/'+jobId+'/run/'+step,'POST',{});
  startPoll();
  setTimeout(refreshJob,500);
}

async function completeReview(jobId) {
  const byIdx={};
  document.querySelectorAll(`.rev-inp[data-job="${jobId}"]`).forEach(el=>{
    const idx=el.dataset.idx;
    if (!byIdx[idx]) byIdx[idx]={slide_index:parseInt(idx,10)};
    byIdx[idx][el.dataset.field]=el.value;
  });
  runningSteps.add('verification');
  await req('/api/jobs/'+jobId+'/complete-review','POST',{slides:Object.values(byIdx)});
  startPoll();
  await refreshJob();
}

async function loadTelem(jobId,hdr) {
  const sec=hdr.closest('.sec');
  sec.classList.toggle('col');
  if (sec.classList.contains('col')) return;
  const body=document.getElementById('tb-'+jobId);
  try {
    const t=await req('/api/jobs/'+jobId+'/telemetry');
    const rows=[['Event count',t.event_count||0],['Model calls',t.model_call_count||0]];
    for (const [k,v] of Object.entries(t.usage_totals||{})) rows.push([k,v]);
    for (const [m,c] of Object.entries(t.model_calls_by_model||{})) rows.push(['Model: '+m,c+' calls']);
    for (const [s,c] of Object.entries(t.model_calls_by_stage||{})) rows.push(['Stage: '+s,c]);
    body.innerHTML=`<table class="tbl">${rows.map(([k,v])=>`<tr><td>${esc(String(k))}</td><td>${esc(String(v))}</td></tr>`).join('')}</table>`;
  } catch(e) { body.innerHTML=`<div class="err-box">${esc(e.message)}</div>`; }
}

async function loadPrompts() {
  try { const r=await req('/api/prompts'); document.getElementById('p-ta').value=r.source; setFb('',''); }
  catch(e) { setFb(e.message,'err'); }
}
async function savePrompts() {
  setFb('Saving…','');
  try { const r=await req('/api/prompts','POST',{source:document.getElementById('p-ta').value});
    if (r.ok) setFb('Saved ✓','ok'); else if (r.error) setFb(r.error,'err'); }
  catch(e) { setFb(e.message,'err'); }
}
async function deployPrompts() {
  const btn=document.getElementById('btn-deploy');
  const logEl=document.getElementById('p-deploy-log');
  btn.disabled=true; btn.textContent='Deploying…'; setFb('',''); logEl.style.display='none'; logEl.textContent='';
  try {
    const r=await fetch('/api/prompts/commit',{method:'POST'});
    const d=await r.json().catch(()=>({}));
    logEl.textContent=d.log||''; logEl.style.display=d.log?'block':'none';
    if (r.ok&&d.ok) setFb('Committed & pushed ✓','ok'); else setFb('Failed: '+(d.step||'unknown'),'err');
  } catch(e) { setFb(e.message,'err'); }
  finally { btn.disabled=false; btn.textContent='Commit & Push'; }
}
function setFb(msg,cls){ const el=document.getElementById('p-fb'); el.textContent=msg; el.className='p-fb'+(cls?' '+cls:''); }
function openLbox(src){ document.getElementById('lbox-img').src=src; document.getElementById('lbox').classList.add('on'); }
function closeLbox(){ document.getElementById('lbox').classList.remove('on'); }
async function req(url,method='GET',body=null){
  const opts={method,headers:{}};
  if (body!==null){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body);}
  const r=await fetch(url,opts);
  const d=await r.json().catch(()=>({error:r.statusText}));
  if (!r.ok) throw new Error(d.error||r.statusText);
  return d;
}
function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

loadJobs();
setInterval(loadJobs, 12000);
</script>
</body>
</html>
"""

_HTML = (
    _HTML
    .replace("LAB_PORT", str(PORT))
    .replace("LAB_PATH", str(_HERE.name))
)


@app.get("/", response_class=HTMLResponse)
def ui():
    return _HTML


def main():
    import signal
    import socket

    def _kill_port(port: int) -> None:
        try:
            result = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
            for pid_str in result.stdout.strip().split():
                try:
                    pid = int(pid_str)
                    if pid != os.getpid():
                        os.kill(pid, signal.SIGTERM)
                except (ValueError, ProcessLookupError):
                    pass
            import time; time.sleep(0.5)
        except Exception:
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _s:
        if _s.connect_ex(("127.0.0.1", PORT)) == 0:
            print(f"Port {PORT} in use — releasing existing process...")
            _kill_port(PORT)

    print(f"\nPS Workshop Lab → http://127.0.0.1:{PORT}\n")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
