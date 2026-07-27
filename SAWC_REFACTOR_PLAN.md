# SAWC Refactor: workshop_lab.py as Dispatcher

## Context

Pitch Synthase's workshop currently uses a single `status` TEXT field as a linear state machine with workers having *implicit* knowledge of where they sit in the pipeline — the SAWC "loose calls + prompt glue" anti-pattern. Workers fire when the HTTP handler tells them to; the pipeline topology exists only in the handler code, not as data.

The fix is to make `workshop_lab.py` the authoritative SAWC dispatch layer: a symbolic stage DAG + a dispatcher (`_advance`) that fires workers based on which stages are already complete. Workers become pure async functions — they run, store their outputs, and return. The dispatcher decides what comes next. Human decision points (intake options, approach selection, payment, review) are just additional stage completions that unblock downstream stages.

This also enables the I2T intake flow: approach_draft fires immediately on job creation; while the user reviews the I2T scan result (~30s), approaches are already generating (~15s) — both are ready simultaneously when the user submits intake_options, and previews fire automatically.

**Constraint**: no changes to existing prompt strings. prompts.py changes are additive only. Workers stay logically identical; only the dispatch wrapper changes. Nothing pushed to git until local testing confirms no regression.

---

## 1. DB additions — `db.py`

Add three columns via `ALTER TABLE` in the existing migrate() pattern:

| Column | Type | Purpose |
|--------|------|---------|
| `stage_states` | TEXT | JSON dict: `{stage_id: "pending"\|"in_progress"\|"completed"\|"failed"\|"skipped"}` — current state, fast lookup |
| `stage_history` | TEXT | `[append-only-log]`: JSON list of `{stage, state, ts, error?}` entries — full transition record |
| `intake_options` | TEXT | JSON-serialized IntakeOptions dict |
| `image_analysis` | TEXT | JSON result from image_scan_worker |

**`stage_states` + `stage_history` are always updated in the same `update_job()` call** (single SQL UPDATE = one atomic write). It is structurally impossible for a state change to exist without a corresponding history entry, or vice versa — they are the same write. This is `[append-only-log]` as the baseline telemetry. No separate `_log_telemetry` call is needed for stage transitions; the history IS the telemetry.

```python
def set_stage_state(job_id: str, stage_id: str, state: str, error: str = None) -> None:
    job = get_job(job_id)
    states = json.loads(job.get("stage_states") or "{}")
    history = json.loads(job.get("stage_history") or "[]")
    states[stage_id] = state
    entry = {"stage": stage_id, "state": state, "ts": time.time()}
    if error:
        entry["error"] = error[:500]  # truncate for storage
    history.append(entry)
    # single UPDATE — both columns in one call = atomic
    update_job(job_id, stage_states=json.dumps(states), stage_history=json.dumps(history))
```

`_advance()` reads `stage_states` for fast dispatch decisions. UI and debugging read `stage_history` to reconstruct exact timeline.

States:
- `pending` — not yet started (default for all stages at job creation)
- `in_progress` — thread dispatched, not yet returned
- `completed` — worker returned without exception
- `failed` — worker raised an exception; `error` field present in history entry
- `skipped` — condition was false; auto-advanced past

`_advance()` fires stages whose state is `pending` and whose dependencies are all `completed` or `skipped`. It will NOT re-fire `in_progress` stages. On startup, `in_progress` stages are reset to `pending` (the reset itself is recorded in `stage_history`) and re-dispatched.

---

## 2. Pipeline stage DAG — `workshop_lab.py`

Add near the top of workshop_lab.py, after imports:

```python
PIPELINE_STAGES = [
    # fires immediately on job creation
    {"id": "approach_draft",   "worker": "approach_drafter_worker",  "depends": []},
    # fires immediately on image upload; condition skips if no image [event-trigger]
    {"id": "image_scan",       "worker": "image_scan_worker",        "depends": [], "condition": "has_image"},
    # [approval-gate]: satisfied by POST /intake-options
    {"id": "human_intake",     "human": True,  "depends": ["approach_draft"]},
    # [content-based-router]: skipped if intake_options has no infer_* flags
    {"id": "design_refs",      "worker": "design_drafter_worker",    "depends": ["human_intake"], "condition": "needs_design"},
    # [fan-out]+[fan-in]: fires 4 preview workers concurrently, waits for all
    {"id": "previews",         "worker": "_run_previews",            "depends": ["human_intake"]},
    # [approval-gate]: satisfied by POST /select-approach
    {"id": "human_selection",  "human": True,  "depends": ["previews"]},
    # [approval-gate]: satisfied by [webhook-ingress] or mock-payment
    {"id": "human_payment",    "human": True,  "depends": ["human_selection"]},
    {"id": "generation",       "worker": "_run_generation",          "depends": ["human_payment"]},
    # [approval-gate]: satisfied by POST /review
    {"id": "human_review",     "human": True,  "depends": ["generation"]},
    {"id": "verification",     "worker": "verification_worker",      "depends": ["human_review"]},
]

def _stage_by_id(stage_id: str) -> dict:
    return next(s for s in PIPELINE_STAGES if s["id"] == stage_id)
```

Condition functions (symbolic, no AI):

```python
_CONDITIONS = {
    "has_image": lambda job: bool(job.get("supporting_image_path")),
    "needs_design": lambda job: any([
        _parse_json_field(job, "intake_options").get("infer_mockup"),
        _parse_json_field(job, "intake_options").get("infer_prototype"),
    ]),
}
```

`_parse_json_field(job, field)` — small helper that returns `json.loads(job.get(field) or "{}")`.

---

## 3. Dispatcher — `_advance(job_id)` + failure semantics

`_advance` is the `[orchestrator]` / `[dispatcher]`. It reads `stage_states` (the single state authority) and fires the next eligible stages. It is called: (a) on job creation; (b) after every stage completes or fails (from within `_run_stage_in_thread`); (c) after every `[approval-gate]` is satisfied via API.

```python
def _advance(job_id: str) -> None:
    job = _db.get_job(job_id)
    states = _db.get_stage_states(job_id)

    for stage in PIPELINE_STAGES:
        sid = stage["id"]
        current = states.get(sid, "pending")
        if current not in ("pending",):
            continue  # already in_progress, completed, failed, or skipped
        if stage.get("human"):
            continue  # [approval-gate]: only satisfied via explicit API calls
        deps = stage["depends"]
        if not all(states.get(d) in ("completed", "skipped") for d in deps):
            continue  # dependencies not satisfied
        condition = stage.get("condition")
        if condition and not _CONDITIONS[condition](job):
            _db.set_stage_state(job_id, sid, "skipped")
            return _advance(job_id)  # re-enter to check next stage
        # fire: mark in_progress BEFORE spawning thread (guards double-dispatch)
        _db.set_stage_state(job_id, sid, "in_progress")
        _run_stage_in_thread(job_id, sid)
```

**Startup recovery**: on server startup, any job with `in_progress` stages had its thread killed. Reset them to `pending` and call `_advance` so they re-dispatch:

```python
@app.on_event("startup")
def resume_incomplete_jobs():
    for job in _db.get_jobs_needing_resume():  # status not in terminal states
        states = _db.get_stage_states(job["id"])
        if any(v == "in_progress" for v in states.values()):
            for sid, state in states.items():
                if state == "in_progress":
                    # reset recorded in stage_history — restart is auditable
                    _db.set_stage_state(job["id"], sid, "pending", error="reset_on_restart")
            _advance(job["id"])
```

**`_run_stage_in_thread` with `[durable-state]` failure tracking** — this is the fix for Field Manual Failure Mode #3 (async without ownership):

```python
def _run_stage_in_thread(job_id: str, stage_id: str) -> None:
    async def _run():
        try:
            worker_name = _stage_by_id(stage_id)["worker"]
            if worker_name in _LOCAL_WORKERS:
                await _LOCAL_WORKERS[worker_name](job_id)
            else:
                await getattr(_workers, worker_name)(job_id)
            # state change + history entry written atomically:
            _db.set_stage_state(job_id, stage_id, "completed")
        except Exception as e:
            # error stored in stage_history atomically with state change — no separate log call:
            _db.set_stage_state(job_id, stage_id, "failed", error=str(e))
            _db.update_job(job_id, status=f"stage_failed:{stage_id}")
            return  # do not advance on failure
        _advance(job_id)

    _run_async_in_thread(_run)
```

Every async execution now has: **owner** (workshop_lab dispatcher), **identity** (job_id + stage_id), **durable status** (`stage_states`), **terminal state** (completed or failed), **observable failure path** (`stage_history` — atomic with state, structurally impossible to lose).

---

## 4. Local worker wrappers + `_LOCAL_WORKERS` registry

The `_LOCAL_WORKERS` dict is defined at module level (populated after the function definitions below it):

```python
_LOCAL_WORKERS: dict[str, Callable] = {}
```

**`_run_generation`** — preserves the existing monkey-patch bridge from `_run_generation_in_thread`:
```python
async def _run_generation(job_id: str) -> None:
    orig_raw = _workers._raw_user_inputs
    orig_tmpl = _workers._selected_template_payload
    try:
        _workers._raw_user_inputs = _raw_user_inputs_full_pitch
        _workers._selected_template_payload = _selected_template_payload_for_approach
        await _workers.generation_worker(job_id)
    finally:
        _workers._raw_user_inputs = orig_raw
        _workers._selected_template_payload = orig_tmpl
```

**`_run_previews`** — `[fan-out]+[fan-in]` for 4 concurrent preview workers:
```python
async def _run_previews(job_id: str) -> None:
    job = _db.get_job(job_id)
    approaches = json.loads(job.get("approach_candidates_json") or "[]")
    await asyncio.gather(*[
        _workers.single_slide_preview_worker(job_id, a["approach_id"])
        for a in approaches
    ])
```
`single_slide_preview_worker` signature confirmed at workers.py:1493: `(job_id: str, approach_id: str)`.

```python
_LOCAL_WORKERS["_run_generation"] = _run_generation
_LOCAL_WORKERS["_run_previews"] = _run_previews
```

---

## 5. API surface changes — `workshop_lab.py`

All human decision endpoints are `[command-ingress]` surfaces: they accept a command (decision or acknowledgement), satisfy the corresponding `[approval-gate]` by writing `"completed"` to `stage_states`, then call `_advance`.

### Modified endpoints

| Endpoint | Change |
|----------|--------|
| `POST /api/jobs` | `[entrypoint]`: call `_advance(job_id)` instead of directly firing approach worker |
| `POST /api/jobs/{id}/intake` (image upload) | Call `_advance(job_id)` after write — fires `image_scan` [event-trigger] if condition met |
| `POST /api/jobs/{id}/select-approach` | Set `human_selection` → `completed` in `stage_states`, call `_advance` |
| `POST /api/jobs/{id}/paid` / payment `[webhook-ingress]` | Set `human_payment` → `completed`, call `_advance` |
| `POST /api/jobs/{id}/review` | Set `human_review` → `completed`, call `_advance` |
| `POST /api/jobs/{id}/run/{step}` | `[operator-override]`: keep as manual debug tool |

### New endpoint

```
POST /api/jobs/{id}/intake-options
Body: IntakeOptions JSON
```
Saves to `intake_options` column; marks `human_intake` complete; calls `_advance(job_id)`.

Also returns current `image_analysis` (if any) so UI can pre-populate the form:
```python
@app.post("/api/jobs/{job_id}/intake-options")
def set_intake_options(job_id: str, payload: dict):
    _db.update_job(job_id, intake_options=json.dumps(payload))
    _db.set_stage_state(job_id, "human_intake", "completed")
    _advance(job_id)
    return {"ok": True}
```

New GET for polling image analysis results:
```
GET /api/jobs/{id}/image-analysis
Returns: {"status": "pending"|"ready", "analysis": {...}}
```

---

## 6. New worker — `image_scan_worker` in `workers.py`

Add after existing imports/constants, before `template_worker`:

```python
async def image_scan_worker(job_id: str) -> None:
    job = db.get_job(job_id)
    image_path = job.get("supporting_image_path")
    if not image_path:
        return
    img_bytes = Path(image_path).read_bytes()
    b64 = base64.b64encode(img_bytes).decode()
    mime = mimetypes.guess_type(image_path)[0] or "image/png"
    from model_gateway import gateway as client
    resp = await client.chat.completions.create(
        model=client.model_for("classify"),  # gpt-4.1-nano via [model-router]
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompts.image_analysis_prompt()},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        response_format={"type": "json_object"},
    )
    result = json.loads(resp.choices[0].message.content)
    db.update_job(job_id, image_analysis=json.dumps(result))
```

No `claim_job_status` needed — image_scan is a read-only scan, idempotent if run twice.

---

## 7. Prompts additions — `prompts.py` (minimum necessary)

Add three things only:

### A. `IntakeOptions` TypedDict

```python
from typing import TypedDict

class IntakeOptions(TypedDict, total=False):
    use_aesthetic: bool      # apply uploaded image's visual style to all slides
    use_mockup: bool         # include mockup/prototype shown in uploaded image
    use_product_shot: bool   # include product photo from uploaded image
    use_facts: bool          # include figures/data visible in uploaded image
    infer_mockup: bool       # generate mockup interface (not from image)
    infer_prototype: bool    # generate prototype visualization (not from image)
```

### B. `image_analysis_prompt()`

```python
def image_analysis_prompt() -> str:
    return (
        "Analyze this uploaded image for use in a startup pitch deck. "
        "Return JSON with boolean keys: has_aesthetic, has_mockup, has_product_shot, has_figures. "
        "Also return: inferred_style (short string, e.g. 'minimal SaaS', 'industrial hardware'). "
        "Only set True if confidently present."
    )
```

### C. `intake_context_block(options: dict) -> str`

Returns a prose fragment to be injected into generation and verification prompts where intake context is relevant. Only called with non-empty dicts.

```python
def intake_context_block(options: dict) -> str:
    if not options:
        return ""
    parts = []
    if options.get("use_aesthetic"):
        parts.append("Match the visual aesthetic of the provided reference image throughout.")
    if options.get("use_mockup"):
        parts.append("The reference image contains a mockup or prototype — include it where relevant.")
    if options.get("use_product_shot"):
        parts.append("The reference image contains a product photo — use it in relevant slides.")
    if options.get("use_facts"):
        parts.append("The reference image contains figures or data — incorporate them as sourced facts.")
    if options.get("infer_mockup"):
        parts.append("The deck includes generated mockup UI not present in any source image.")
    if options.get("infer_prototype"):
        parts.append("The deck includes a generated prototype visualization.")
    return "\n".join(parts)
```

**Injection points** (minimal — do not rewrite prompt strings, only add an optional block):
- In `generation_worker` (workers.py): read `intake_options` from DB, call `prompts.intake_context_block(options)`, inject into the prompt payload where `visual_grammar` is currently assembled. Look for the `_build_generation_payload` or equivalent function.
- In `verification_worker` (workers.py): same injection into the verification checklist context.

If the injection point requires a prompt template to grow by one optional `{intake_context}` token, that is the only change to the prompt string itself.

---

## 8. Hardening for production traffic — `[gateway]` + `[entrypoint]` + `[tool-broker]`

The three forward-looking pieces the Field Manual vocabulary identifies:

### A. `[gateway]` + `[model-router]` — all OAI calls through one boundary

Currently every worker instantiates `AsyncOpenAI()` directly with hardcoded model IDs from prompts.py constants (`ANCHOR_MODEL = "gpt-5.4"`, etc.). The fix: `model_gateway.py` is the only place `AsyncOpenAI` is instantiated, and it carries the model routing table.

```python
# model_gateway.py
from openai import AsyncOpenAI
import asyncio

# [model-router]: task type → model endpoint mapping, all in one place
MODEL_ROUTES = {
    "anchor":   "gpt-5.4",
    "draft":    "gpt-5.4",
    "quality":  "gpt-5.4",
    "classify": "gpt-4.1-nano",
    "image":    "gpt-image-2",
}

class ModelGateway:
    """[gateway]: single boundary for all OAI API calls."""
    def __init__(self):
        self._client = AsyncOpenAI()
        self._img_sem = asyncio.Semaphore(4)  # [rate-limiter]: 4 concurrent image calls

    def model_for(self, task: str) -> str:
        return MODEL_ROUTES[task]

    @property
    def chat(self):
        return self._client.chat

    async def images_generate(self, **kwargs):
        async with self._img_sem:
            return await self._client.images.generate(**kwargs)

gateway = ModelGateway()
```

Workers replace `client = AsyncOpenAI()` with `from model_gateway import gateway as client`. They call `gateway.model_for("anchor")` instead of `prompts.ANCHOR_MODEL`. The prompts.py model constants become dead code and are removed (this IS minimum-necessary: removing stale constants is not paraphrasing prompts).

The `[model-router]` means: swapping models or adding fallback routing happens in `MODEL_ROUTES`, not scattered across 12 worker functions. "Dispatch is the only thing deciding which endpoint the OAI calls go to."

### B. `[entrypoint]` — proper installed command

`python workshop_lab.py` is a script invocation, not a governed runtime boundary. Add a `pyproject.toml` (or update the existing one) with:

```toml
[project.scripts]
pitch-synthase = "pitch_synthase_archetype_workshop.workshop_lab:main"
```

And in workshop_lab.py:

```python
def main():
    import uvicorn
    uvicorn.run("workshop_lab:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":
    main()
```

After `pip install -e .`, the server starts with `pitch-synthase`. The runtime boundary now has a stable, versioned name — essential once there are users other than localhost.

### C. `[tool-broker]` / stage registry — standardized "add worker"

Rather than maintaining a hardcoded `PIPELINE_STAGES` list, expose a `register_stage()` function. Adding a new pipeline stage becomes one-step:

```python
# In workshop_lab.py
_STAGE_REGISTRY: dict[str, dict] = {}

def register_stage(stage_def: dict):
    """Register a stage definition. Call after defining its worker."""
    _STAGE_REGISTRY[stage_def["id"]] = stage_def
```

All current stages are registered at module load:

```python
register_stage({"id": "approach_draft", "worker": "approach_drafter_worker", "depends": []})
register_stage({"id": "previews",       "worker": "_run_previews",            "depends": ["human_intake"]})
# ...etc
```

`_advance()` iterates `_STAGE_REGISTRY.values()` instead of `PIPELINE_STAGES`. Adding a new worker to the pipeline in the future: define the async function, call `register_stage()`. No other changes required.

---

## 9. PostHog analytics — `[metrics-emitter]` + `[structured-logging]`

`set_stage_state()` is the single state authority. It's also the perfect and **only** instrumentation point — every pipeline event flows through it. PostHog gets a copy of every transition automatically; no separate analytics calls scattered through the codebase.

### Integration point: `set_stage_state` in `db.py`

```python
def set_stage_state(job_id: str, stage_id: str, state: str, error: str = None) -> None:
    job = get_job(job_id)
    states = json.loads(job.get("stage_states") or "{}")
    history = json.loads(job.get("stage_history") or "[]")
    states[stage_id] = state
    entry = {"stage": stage_id, "state": state, "ts": time.time()}
    if error:
        entry["error"] = error[:500]
    history.append(entry)
    # atomic: both columns in one UPDATE
    update_job(job_id, stage_states=json.dumps(states), stage_history=json.dumps(history))
    # fire-and-forget PostHog — never blocks the pipeline
    _ph_capture(job_id, f"stage_{state}", {
        "stage": stage_id,
        "job_id": job_id,
        "error": error,
    })
```

### `_ph_capture` — non-blocking, never-fails

```python
import threading

_PH_TOKEN = os.environ.get("POSTHOG_TOKEN", "phc_DaZmzZ9fKszDeecfqdETWQwMq5uSskHG4UutVvj92GUQ")
_PH_HOST  = "https://us.i.posthog.com"

def _ph_capture(distinct_id: str, event: str, props: dict) -> None:
    """Fire-and-forget PostHog capture. Any failure is silently swallowed."""
    def _send():
        try:
            import urllib.request
            payload = json.dumps({
                "api_key": _PH_TOKEN,
                "event": event,
                "distinct_id": distinct_id,
                "properties": {**props, "$lib": "ps-pipeline"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }).encode()
            req = urllib.request.Request(
                f"{_PH_HOST}/capture/",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
    threading.Thread(target=_send, daemon=True).start()
```

No SDK dependency — uses only stdlib `urllib`. Fires in a daemon thread so it never blocks the pipeline. Any PostHog outage or network error is silently dropped; the pipeline is unaffected.

### Events captured automatically

Every call to `set_stage_state` produces one PostHog event. The full pipeline emits:

| Event | When |
|-------|------|
| `stage_in_progress` | Worker thread dispatched |
| `stage_completed` | Worker returned successfully |
| `stage_failed` | Worker raised exception |
| `stage_skipped` | Condition was false |
| `stage_pending` | Startup reset of in_progress stage |

Combined with `stage` and `job_id` properties, PostHog dashboards can show:
- Funnel from `approach_draft` → `previews` → `human_selection` → `human_payment` → `generation` → `verification`
- Stage failure rates by stage name
- Time-in-stage (from `in_progress` → `completed` timestamps in `stage_history`)
- Drop-off at each `[approval-gate]` (human stages that never advance)
- Conversion rate: jobs that reach `generation` vs. jobs that reach `verification`

### Additional events in API endpoints

Beyond stage transitions, wire PostHog captures in key `[command-ingress]` endpoints:

```python
# In set_intake_options endpoint:
_db._ph_capture(job_id, "intake_options_submitted", {
    **payload,  # which flags were checked
    "has_image": bool(_db.get_job(job_id).get("image_analysis")),
})

# In create_job endpoint (after job creation):
_db._ph_capture(job_id, "job_created", {
    "archetype_id": body.get("archetype_id"),
    "has_doc": bool(body.get("doc_text")),
})

# In mock_payment / payment webhook:
_db._ph_capture(job_id, "payment_received", {"kind": kind, "source": "mock"})
```

### `distinct_id` strategy

Use `job_id` as the PostHog `distinct_id`. Jobs are the unit of analysis. If user identity is added later (e.g., email at payment), call `posthog.alias(job_id, user_email)` at payment time to merge the session.

---

## 11. What does NOT change

- All existing worker function logic (body unchanged)
- All existing prompt strings (no paraphrasing, no consolidation)
- `claim_job_status` guards in workers (preserved as double-dispatch protection)
- `run/{step}` manual dispatch endpoints (kept as debug tools)
- `core/` directory sync (happens post-test, not during this refactor)
- Path A (template_worker, candidate_worker, etc.) — untouched

---

## 12. Verification sequence

After making changes, test locally in this order:

1. **Start server**: `cd /home/director/pitch_synthase_archetype_workshop && python workshop_lab.py`
2. **Create job**: `POST /api/jobs` — verify `approach_draft` → `in_progress` then `completed` in `stage_states` (~20s), no manual `run/approaches` call needed
3. **Upload image**: `POST /api/jobs/{id}/intake` with `file=@image.png` — verify `image_scan` → `completed` in `stage_states`, poll `GET /api/jobs/{id}/image-analysis` until `"ready"` (~5s)
4. **Submit intake-options with no infer flags**: `POST /api/jobs/{id}/intake-options` with `{"use_aesthetic": true}` — verify: (a) `design_refs` → `skipped`, (b) `previews` → `completed`, (c) 4 preview PNGs generated with no `run/previews` call
5. **Submit intake-options with infer_mockup**: repeat from step 2 with `{"infer_mockup": true}` — verify `design_refs` → `completed` before `previews` fires
6. **Failure recovery**: kill the server mid-approach_draft, restart, verify the stage resets to `pending` and re-fires
7. **Regression test**: PetNavi job through full path to `awaiting_review`, verify no output regression vs. current behavior
8. **`[operator-override]` still works**: call `POST /api/jobs/{id}/run/previews` manually — verify it fires and `stage_states` reflects completion

Only push to Pitch_Synthase_v2 remote after step 8 passes.
