#!/usr/bin/env python3
"""
Pitch Synthase Workshop Lab — port 8793.
Mirrors pipeline_lab.py from the production intelluric repo but wired to the
workshop pipeline: approach_drafter_worker, single_slide_preview_worker,
generation_worker (with full-pitch-first field bridge), and verification_worker.

Bypasses Stripe. Writes to the workshop DB and jobs dir.
Run: .venv/bin/python workshop_lab.py

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
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
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
    """Build the template payload from approach_candidates_json + selected_archetype_id."""
    approach_id = job.get("selected_candidate_id") or ""
    approach = next(
        (a for a in (job.get("approach_candidates_json") or [])
         if a.get("approach_id") == approach_id),
        None,
    )
    archetype_id = job.get("selected_archetype_id") or ""
    archetype = _prompts.experiment_archetype_by_id(archetype_id) or {}
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
    """Run generation_worker with the full-pitch-first field bridge patched in."""
    def _go():
        orig_raw = _workers._raw_user_inputs
        orig_tmpl = _workers._selected_template_payload
        try:
            _workers._raw_user_inputs = _raw_user_inputs_full_pitch
            _workers._selected_template_payload = _selected_template_payload_for_approach
            asyncio.run(_workers.generation_worker(job_id))
        finally:
            _workers._raw_user_inputs = orig_raw
            _workers._selected_template_payload = orig_tmpl
    t = threading.Thread(target=_go, daemon=True)
    t.start()
    return t


# ── API: jobs ─────────────────────────────────────────────────────────────────
@app.get("/api/jobs")
def list_jobs():
    with _db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, status, created_at, updated_at, audience "
            "FROM jobs ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return [dict(r) for r in rows]


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


@app.post("/api/jobs")
async def create_job(request: Request):
    """Create a full-pitch-first job and immediately run the approach drafter."""
    body = await request.json()
    pitch_aspect_modes = body.get("pitch_aspect_modes") or {
        cat: "INFER" for cat in _prompts.PITCH_ASPECT_CATEGORIES
    }
    excepted = body.get("excepted_inference_elements") or ["pricing", "mockup_prototype_design"]
    job_id, recovery = _db.create_job(
        problem_need="",
        audience=body.get("audience", ""),
        association_words=[],
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
    )
    _run_async_in_thread(_workers.approach_drafter_worker, job_id)
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
    return {"ok": True}


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
    return {"ok": True}


@app.post("/api/jobs/{job_id}/run/{step}")
def run_step(job_id: str, step: str):
    """
    steps:
      approaches      → approach_drafter_worker (resets to created)
      previews        → single_slide_preview_worker ×4 (all approaches, parallel)
      paid            → generation_worker via full-pitch-first bridge
      verification    → verification_worker
    """
    if step == "approaches":
        _db.update_job(job_id, status="created", approach_candidates_json=None, error_message=None)
        _run_async_in_thread(_workers.approach_drafter_worker, job_id)
    elif step == "previews":
        job = _db.get_job(job_id)
        approaches = job.get("approach_candidates_json") or [] if job else []
        if not approaches:
            return JSONResponse({"error": "no approaches drafted yet"}, status_code=409)
        approach_ids = [a["approach_id"] for a in approaches]

        async def _run_all_previews():
            await asyncio.gather(
                *[_workers.single_slide_preview_worker(job_id, aid) for aid in approach_ids],
                return_exceptions=True,
            )
        _run_async_in_thread(_run_all_previews)
    elif step == "paid":
        job = _db.get_job(job_id)
        if not job:
            return JSONResponse({"error": "job not found"}, status_code=404)
        _db.update_job(job_id, status="paid", error_message=None)
        _run_generation_in_thread(job_id)
    elif step == "verification":
        _db.update_job(job_id, status="review_received", error_message=None)
        _run_async_in_thread(_workers.verification_worker, job_id)
    else:
        return JSONResponse({"error": f"unknown step: {step}"}, status_code=400)

    return {"ok": True, "started": step}


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
    reviewed_slides: dict = {}
    for slide in body.get("slides", []):
        idx = str(slide.get("slide_index", ""))
        orig = spec_map.get(idx) or {}
        new_headline = slide.get("headline", orig.get("headline", ""))
        new_body = slide.get("body", " | ".join(orig.get("body_points", [])))
        new_notes = slide.get("notes", orig.get("speaker_note", ""))
        change_requests = slide.get("change_requests", "")
        reviewed_slides[idx] = {
            "headline": new_headline,
            "body": new_body,
            "notes": new_notes,
            "change_requests": change_requests,
            "_edited": (
                new_headline != orig.get("headline", "")
                or new_body != " | ".join(orig.get("body_points", []))
                or new_notes != orig.get("speaker_note", "")
                or bool(change_requests.strip())
            ),
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
            }
    _db.update_job(job_id, reviewed_slides=reviewed_slides, status="review_received")
    _run_async_in_thread(_workers.verification_worker, job_id)
    return {"ok": True}


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


if __name__ == "__main__":
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
