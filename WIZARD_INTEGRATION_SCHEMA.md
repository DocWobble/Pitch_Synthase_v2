# Wizard Frontend Integration Schema

Target: a commercial-facing step-by-step wizard UI wired to `ribotome.py`
(FastAPI backend, SQLite job store via `db.py`, workers in `workers.py`).
This document is the contract between that frontend and the current SAWC
pipeline — every field, endpoint, and shape below is read directly from the
running code, not aspirational.

All paths are relative to the API base (e.g. `http://host:8793`).

---

## 1. Mental model

A **job** is a single row in `jobs` plus a `stage_states` map. The backend is
a DAG dispatcher (`_advance()` in `ribotome.py`), not a linear script: every
time a field changes, the server re-checks which stages have all their
dependencies satisfied and fires them. The wizard's only real job is:

1. Create a job.
2. Repeatedly ask `GET /api/jobs/{job_id}/next-action` — it tells you exactly
   which screen to show next (or that the backend is thinking).
3. Submit whatever that screen collects to the matching endpoint below.
4. Repeat until `next-action` returns `"action": "complete"`.

Do not hardcode a fixed screen order in the frontend beyond what
`next-action` reports. `human_prototype_selection` is skipped entirely when
`infer_prototype` was false at creation — `next-action` already accounts for
that, a hardcoded step list will not.

---

## 2. Pipeline stage graph

| stage | kind | depends on | condition | produces |
|---|---|---|---|---|
| `design_refs_text` | worker | — | `has_prototype` | `prototype_candidates_json` |
| `approach_draft` | worker | `design_refs_text` | — | `approach_candidates_json`, `selected_archetype_id`, `selected_archetype_label` |
| `image_scan` | worker (event-triggered by image upload) | — | — | `image_analysis` |
| `human_intake` | **human gate** | `approach_draft` | — | `intake_options` |
| `design_refs_images` | worker | `design_refs_text` | `has_prototype` | `prototype_candidates_json` (enriched with `reference_image_path`) |
| `human_prototype_selection` | **human gate** | `design_refs_images` | `has_prototype` | `selected_prototype_id` |
| `previews` | worker | `approach_draft`, `human_prototype_selection`, `human_intake` | — | `approach_candidates_json` (enriched with `preview_image_path`) |
| `human_selection` | **human gate** | `previews` | — | `selected_candidate_id`, `selected_candidate_label`, `selected_archetype_id`, `selected_archetype_label` |
| `human_payment` | **human gate** | `human_selection` | — | `explicit_slide_count` |
| `generation` | worker | `human_payment` | — | `slide_specs`, `deck_proof_plan` |
| `human_review` | **human gate** | `generation` | — | `reviewed_slides` |
| `verification` | worker | `human_review` | — | (writes `export/` deliverables; no schema output field) |

`has_prototype` = job's `infer_prototype` flag, set once at creation and
never changed. If false, `design_refs_text`, `design_refs_images`, and
`human_prototype_selection` are auto-skipped — the wizard never shows a
prototype screen for those jobs.

Human gates never run a worker themselves; they just wait for the matching
POST endpoint to mark them `completed`, which lets `_advance()` continue.

---

## 3. Screen-by-screen flow

### Screen 1 — Pitch intake
**Create the job.**

```
POST /api/jobs
{
  "elevator_pitch": "string, required",
  "audience": "string, required",
  "conveys": "string, optional — key message/problem framing",
  "doc_text": "string, optional — pasted supporting document text",
  "association_words": ["word1", "word2", "word3"],   // exactly 3, required
  "infer_prototype": true | false,                     // whether to run the design/prototype branch
  "archetype_id": "arch_xxx",                          // optional pre-pin; leave unset in the standard flow
  "pitch_aspect_modes": { ... },                        // optional, see §6
  "excepted_inference_elements": ["pricing"]            // optional, defaults to ["pricing"]
}
→ { "job_id": "pd_xxxxxxxx", "recovery_token": "..." }
```

`association_words` is the wizard's most important creative-direction field.
It reaches the approach drafter directly and measurably changes tone — coach
users toward specific, evocative phrases ("seriously slick as hell") over
generic ones ("professionally composed"); the latter contributes almost
nothing.

If the pitch needs a supporting file (PDF/txt/md/image), call:

```
POST /api/jobs/{job_id}/intake   (multipart/form-data)
  elevator_pitch, conveys, audience   — optional overwrite of the above
  file                                 — PDF/txt/md extracted to doc_text;
                                          image saved to intake/ and triggers
                                          image_scan automatically
```

Persist `job_id` client-side (localStorage/URL) — it's the only handle for
every subsequent call. `recovery_token` exists for a job-recovery UI but the
current pipeline API has no endpoint that consumes it yet; do not build
around it.

### Screen 2 — Poll while the backend thinks
```
GET /api/jobs/{job_id}/next-action
```
Poll this (2–3s interval, or use the SSE stream — §5) until `"action"` stops
being `"processing"`. Response shape:

```json
{ "stage": "human_intake" | "human_selection" | "human_payment" | "human_review" | null,
  "action": "intake_options" | "select_approach" | "payment_required" | "review_required" | "processing" | "complete" | "stage_failed",
  "context": { ... },     // only for human_payment: amount_cents, slide_count
  "active_stages": [...], // only when action == "processing"
  "failed_stages": [...], // only when action == "stage_failed"
  "error": "..."          // only when action == "stage_failed"
}
```

### Screen 3 — Intake options (`human_intake`)
Shown when `action == "intake_options"`. If an image was uploaded, poll
`GET /api/jobs/{job_id}/image-analysis` first (`{"status": "ready"|"pending"|"in_progress", "analysis": {...}}`)
so the UI can show what the model saw before the user picks how to use it.

```
POST /api/jobs/{job_id}/intake-options
{
  "use_aesthetic": bool,     // match the uploaded image's visual style throughout
  "use_mockup": bool,        // uploaded image is a mockup/prototype — include it
  "use_product_shot": bool,  // uploaded image is a product photo — use in slides
  "use_facts": bool,         // uploaded image contains figures/data to source
  "infer_mockup": bool,      // generate UI mockups not present in any source image
  "infer_prototype": bool    // generate a prototype visualization
}
→ { "ok": true, "image_analysis": {...} }
```
These are the only six keys `intake_context_block()` reads — sending
anything else is silently ignored downstream.

### Screen 4 — Prototype selection (`human_prototype_selection`)
Only reachable if `infer_prototype` was true at creation. Fetch the job
(`GET /api/jobs/{job_id}`) and read `prototype_candidates_json`:

```json
[{
  "design_id": "design_1",
  "assigned_archetype_id": "design_dyson",
  "design_philosophy": "...",
  "physical_description": "...",
  "reference_image_path": "design_refs/design_1_design_ref.png"  // relative — fetch via §7
}, ...]  // exactly 4 entries
```
Render all 4 with their images; user picks one.

```
POST /api/jobs/{job_id}/select-prototype
{ "design_id": "design_2" }
→ { "ok": true, "selected_prototype_id": "design_2" }
```

### Screen 5 — Approach selection (`human_selection`)
Read `approach_candidates_json` off the job (populated by `approach_draft`,
enriched with preview images by `previews`):

```json
[{
  "approach_id": "approach_1",
  "label": "Hands-On Proof",
  "pitch_angle": "...",
  "key_differentiator": "...",
  "visual_direction": "...",
  "archetype_a": { "archetype_id": "arch_edison", "label": "Edison", ... },
  "archetype_b": { "archetype_id": "arch_lauder", "label": "Lauder", ... },
  "preview_image_path": "single_slide_previews/approach_1.png"
}, ...]  // exactly 4 entries, unique labels, no repeated archetype pair
```
Render all 4 preview images + pitch angles side by side — this is the core
"which pitch feels right" decision point.

```
POST /api/jobs/{job_id}/select-approach
{
  "approach_id": "approach_1",
  "approach_label": "Hands-On Proof",
  "archetype_id": "arch_edison",
  "archetype_label": "Edison"
}
→ { "ok": true }
```
Send the label fields exactly as received — they're stored verbatim and
surfaced later in job listings/telemetry.

### Screen 6 — Payment (`human_payment`)
`next-action`'s `context` gives `amount_cents` and a default `slide_count`.
Let the user choose content-slide count (4–10; total deck = N + 2 for
hero/close) if the commercial product exposes that as a pricing lever.

**This is the integration point that needs replacing for a commercial
build.** The only implementation today is a lab bypass:

```
POST /api/jobs/{job_id}/mock-payment/full
{ "slide_count": 6 }
→ { "ok": true }
```

For production, this must become a real Stripe flow: create a PaymentIntent
keyed to `job_id` and `amount_cents`, then on webhook success perform exactly
what `mock_payment()` does today (`ribotome.py:851`) — set
`deck_payment_status="succeeded"`, `deck_paid_at`, `deck_payment_intent_id`,
`explicit_slide_count`, mark `human_payment` stage `completed`, call
`_advance(job_id)`. The DB schema already carries the real columns
(`payment_intent_id`, `checkout_amount_cents`, `deck_payment_intent_id`,
`deck_payment_status` — see `db.py`); nothing needs to be added to the
schema, only a real webhook handler needs to replace the mock endpoint.
`identity`/Stripe-customer linkage already has a hook:

```
POST /api/jobs/{job_id}/identify
{ "email": "user@example.com", "stripe_customer_id": "cus_xxx", ... }
→ { "ok": true }
```
Call this once you have an email/customer id (checkout form, account login)
— it aliases the anonymous job to a real identity in PostHog and is where a
`stripe_customer_id` should be attached before or alongside the PaymentIntent
call.

### Screen 7 — Generation (no user action)
`generation` fires automatically once payment completes. This is the
expensive, multi-minute step (anchor writer → mandatory Visual Grammar
Synthesizer → storyboard → N+2 slide image renders). Use the SSE stream
(§5) here — polling `next-action` alone gives no progress detail, just
`"processing"`.

### Screen 8 — Review (`human_review`)
Fetch the job; `slide_specs` is a list of:
```json
{
  "slide_index": 1,
  "headline": "...",
  "body_points": ["...", "..."],
  "speaker_note": "..."
}
```
Rendered slide images live under `artifact_images.full` / `.preview` on the
job object (§7). Let the user edit headline/body/notes per slide and/or flag
some for regeneration:

```
POST /api/jobs/{job_id}/complete-review
{
  "verify_only": [3, 7],   // omit key or null = re-verify all slides; [] = none
  "slides": [
    { "slide_index": 1, "headline": "...", "body": "bullet one | bullet two", "notes": "...", "change_requests": "make the headline punchier" }
  ]
}
→ { "ok": true }
```
`body` is a single ` | `-joined string, not an array — join `body_points`
with `" | "` when populating the edit form's default value, and split back
out for display. Any slide omitted from `slides[]` is carried through
unedited automatically — you don't need to resend every slide, only the ones
the user touched.

If verification needs to be re-run after further edits without a full
re-review pass, `POST /api/jobs/{job_id}/reset-verification` resets both
gates to `pending` so `complete-review` can be called again.

### Screen 9 — Delivery (`complete`)
Once `verification` finishes, `next-action` returns `"action": "complete"`.
Deliverables are written to the job's `export/` directory:

| file | purpose |
|---|---|
| `deck.pdf` | final compiled deck |
| `deck.pptx` | editable PowerPoint |
| `preview.html` | in-browser preview |
| `slides.md` | plain-text/markdown export |
| `manifest.json` | export manifest |
| `pitch_deck_package.zip` | all of the above bundled |

Fetch any of these via §7's file endpoint, e.g.
`GET /api/files/{job_id}/export/deck.pdf`.

---

## 4. Polling vs. streaming

Two options, not mutually exclusive:

- **Poll** `GET /api/jobs/{job_id}/next-action` every 2–3s — cheap, tells you
  which screen to show. Sufficient for gate transitions.
- **Stream** `GET /api/jobs/{job_id}/stream` (SSE) for real-time progress
  during long worker stages (`generation` especially). Emits:
  ```
  data: {"type": "stage", "stage_id": "generation", "state": "in_progress"}
  data: {"type": "progress", "stage": "generation", "message": "Slide 3 of 8 rendered", "pct": 0.55, "ts": ...}
  data: {"type": "done", "state": "completed"|"failed"}
  ```
  Note: the `done` event currently only watches the `generation` stage
  reaching a terminal state — it does not fire again for `verification`.
  After receiving `done`, fall back to polling `next-action` for the review
  → verification → complete tail of the pipeline.

---

## 5. Asset & file serving

`GET /api/jobs/{job_id}` returns, in addition to raw DB columns:

```json
{
  "artifact_images": {
    "preview": [{ "path": "single_slide_previews/approach_1.png", "label": "approach 1", "mtime": 123 }],
    "full":    [{ "path": "slides/slide_01_proof.png", ... }],
    "refined": [...],
    "other":   [...]
  },
  "intake_image_names": ["photo.jpg"],
  "job_dir": "/absolute/path"   // server-side only, do not expose to end users
}
```
Every `path` field anywhere in the API (preview images, design refs, export
files) is relative to the job directory — fetch it with:
```
GET /api/files/{job_id}/{path}
```
This endpoint path-traverses safely (rejects anything resolving outside the
job dir) and sets correct MIME types for png/webp/jpg/json. Nothing else is
needed to display any image the API references.

---

## 6. Optional advanced fields

`pitch_aspect_modes` (job creation, rarely needed by a v1 wizard) lets power
users control per-category SOURCE/INFER/OMIT behavior across seven pitch
aspects (`customer_stakeholder_value`, `market_ecosystem_competition`,
`commercialization_pricing_financial`, `proof_validation_defensibility`,
`execution_team_risk`, `transaction_ask_use_of_proceeds`,
`founder_biography`). Valid values: `"SOURCE"`, `"INFER"`, `"OMIT"`.
`founder_biography` defaults to `SOURCE` (named people are never invented);
everything else defaults to `INFER`. Leave this field out entirely for a
standard consumer wizard — it exists for an advanced/expert mode.

---

## 7. Error handling

Every stage failure surfaces the same way:
- `next-action` returns `{"action": "stage_failed", "failed_stages": [...], "error": "..."}`.
- The job's own `status` column becomes `"stage_failed:{stage_id}"`.

Common causes worth distinct UI copy for:
- A required field was missing when a stage tried to dispatch (pre-dispatch
  check fires before any API spend) — the `error` string names the missing
  field(s) directly, safe to surface close to verbatim.
- The Visual Grammar Synthesizer or any generation-stage sub-call raised —
  this is a hard failure now (not silently skipped), surfaces as
  `stage_failed:generation`.

There is currently no retry/resume endpoint exposed to the wizard for a
failed job short of `reset-verification` (which only rewinds
review/verification). A commercial frontend should treat `stage_failed` as
terminal-for-that-job and route to support/contact, not attempt automatic
retry.

---

## 8. Explicitly out of scope for the commercial frontend

`GET/POST /api/prompts` and `POST /api/prompts/commit` are lab-only prompt
editing/hot-reload endpoints that write to and `git push` the server's own
source tree. Never expose these to an end-user-facing build. Same for
`GET /api/steps` and `/api/steps/{step_id}` — useful for internal debugging
of the DAG, not part of any user-facing flow. `GET /api/gallery/previews`
and `GET /api/jobs/by-label/{label}` are internal lab/QA lookups, not part of
the wizard's own flow.
