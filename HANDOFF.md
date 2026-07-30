# Handoff — Pitch Synthase / RiboTome
**Date:** 2026-07-27  
**Repo:** `DocWobble/Pitch_Synthase_v2` — branch `main` (commit `38e9f4f`)  
**Server:** `ribotome.py` on port 8793  
**Working dir:** `/home/director/pitch_synthase_archetype_workshop/`

> **This is a point-in-time session log, kept as history — two corrections as of 2026-07-29:**
> the pipeline DAG diagram below omits `design_refs_text`/`design_refs_images` being
> split apart and the `human_prototype_selection` gate added between them; the exact
> current graph is in `pipeline.md`. Also, "not done" item 1 below (`selected_candidate_id`
> never written for SAWC-path jobs) has since been fixed — `/api/jobs/{id}/select-approach`
> writes it directly. For current architecture/endpoint reference, see `architecture.md`,
> `pipeline.md`, and `WIZARD_INTEGRATION_SCHEMA.md`.

---

## What happened this session

### 1. SAWC dispatch layer (fully implemented, tested, pushed)

`workshop_lab.py` was a linear implicit state machine. It's now `ribotome.py` — a declarative stage DAG with an `_advance()` dispatcher. Stage states live in a `stage_states` JSON column; every transition is atomically co-written to `stage_history` and emits a PostHog event.

**Pipeline DAG:**
```
approach_draft → [image_scan*] → human_intake → design_refs† → previews
  → human_selection → human_payment → generation → human_review → verification
```
`*` event_trigger: fired by `/intake` image upload, not by `_advance`  
`†` skipped if no `infer_mockup`/`infer_prototype` in intake_options

Key files:
- `ribotome.py` — canonical server (renamed from `workshop_lab.py`)
- `workshop_lab.py` — 6-line compatibility shim (`from ribotome import *`)
- `model_gateway.py` — single OAI boundary, `MODEL_ROUTES`, lazy `AsyncOpenAI` init, `Semaphore(4)` for image rate-limiting
- `db.py` — `set_stage_state`, `get_stage_states`, `_ph_capture`, `set_identity`, `get_jobs_needing_resume`

### 2. Anchor writer funding ask (prompts.py)

Two targeted additions:
- "The fiction may describe" bullet: the deck's **slides** named a specific quantitative ask (inferred from domain/audience — crowdfunding target, seed raise, grant, etc.). Phrasing: "every pitch is a document that makes an ask."
- `deck_generation_prompt` requirements: must include "what specific quantitative ask the deck's slides stated explicitly — a figure the slides themselves named."

Also added:
- **Deck builder** `ANCHOR NARRATIVE SCOPE` block: anchor = atmosphere/structure, not slide copy — **except** elements the anchor explicitly describes as being in the deck (e.g. a stated ask) which ARE canon and must appear in slides.
- **Verifier** `[anchor-derived figures]` clause: investor asks absent from source material are INFER-class; only flagged if they contradict something the source actually states.

### 3. Preview gallery

- `local_state/preview_gallery/index.json` — 104 entries indexed at startup
- Fields per entry: `approach_label`, `visual_direction` (full aesthetic description), `pitch_angle` snippet, `archetype_id/label`, `elevator_pitch_snippet`, `selected` bool, `image_path`, `created_at`
- `_backfill_gallery()` runs on every server startup — idempotent, indexes any on-disk PNG not yet in the index
- `_run_previews` calls `_gallery_index_preview` for all 4 approaches after generation
- `GET /api/gallery/previews?archetype_id=arch_architect&selected_only=false`

**Current gallery distribution (104 previews / 26 jobs):**
```
36  Architect
16  unknown (pre-archetype jobs)
12  Athena, The Strategic Rationalist
 8  Cicero, The Market Evangelist
 8  Firebringer
 8  Newton
 4  Da Vinci, The Paradigm Shifter
 4  Trickster
 4  Copernicus, The Contrarian Pivot
 4  Alexander
```

### 4. PS_DEBUG_MODE=1

Set `PS_DEBUG_MODE=1` before starting ribotome to stub all image generation. Image calls return a 1456×816 white PNG with bold centred "PLACEHOLDER | INFER IMAGE CONTENTS" (Pillow-rendered, ~14KB). Text calls pass through live with prompt/response printed to stdout. `_throttle_image_generation` returns immediately.

### 5. image_scan_worker

Vision worker that fires on image upload. Uses `gateway.model_for("anchor")` = `gpt-5.4` (not nano — nano was tested and failed to detect figures in a clear console screenshot). Returns `{has_aesthetic, has_mockup, has_product_shot, has_figures, inferred_style}`. Stored in `image_analysis` DB column. Retrieved via `GET /api/jobs/{id}/image-analysis`.

### 6. Samples

`samples/petnavi_pocket_persona.pdf` — 8-slide Pocket Persona deck generated this session. Approach 2 from job `pd_6e56c43d1818406c`. Notable: slide 2 reproduced exact console metrics (22 t/s, 78ms ASR, 112ms TTS, 38% context) from the uploaded UI mockup screenshot via `use_facts: true`. Slide 8 is the ask slide — `$750,000 pre-seed + launch readiness raise`.

---

## Current state / what's NOT done

- **`selected_candidate_id` not being set for SAWC-path jobs.** The `selected` flag in gallery entries is `false` for all new jobs because `selected_candidate_id` is only written by the old select-approach path. Check `POST /api/jobs/{id}/select-approach` handler in `ribotome.py` — it sets `human_selection` completed and calls `_advance` but may not be writing `selected_candidate_id` to the DB.

- **Verifier typo on slide 7.** The PetNavi Pocket Persona deck slide 7 contains "eertification" (double-e). The verifier caught it in review but the human_review gate was never submitted for this job (`pd_6e56c43d1818406c` is still at `human_review: pending`).

- **`workshop_lab.py` ↔ `pipeline_lab` gap still open.** The workshop server (`ribotome.py`) and the production `pipeline_lab.py` in the main intelluric repo are not synced. The SAWC architecture exists only in the workshop. See memory: `project_pitch_synthase_workshop_pipeline_lab_gap.md`.

- **Frontend.** The gallery `GET /api/gallery/previews` endpoint exists but there's no UI for it. The user mentioned "if none of the generated slides are what the user wants, they can look through a gallery of examples" — this is the intended UX but not built.

- **`PS_DEBUG_MODE` not tested end-to-end after the Pillow placeholder change.** The dark PNG stub was replaced with the Pillow-rendered text image in `workers.py`. It was smoke-tested standalone but not through a full debug pipeline run.

---

## API surface (ribotome.py, port 8793)

```
POST /api/jobs                          create job + fire approach_draft
POST /api/jobs/{id}/intake              image upload → fires image_scan
POST /api/jobs/{id}/intake-options      submit IntakeOptions → fires previews
GET  /api/jobs/{id}/image-analysis      poll image_scan result
GET  /api/jobs/{id}/next-action         UI routing: what gate is blocking
POST /api/jobs/{id}/select-approach     satisfy human_selection gate
POST /api/jobs/{id}/mock-payment/{kind} satisfy human_payment gate (dev only)
POST /api/jobs/{id}/complete-review     satisfy human_review gate
POST /api/jobs/{id}/identify            merge identity dict, PostHog alias
GET  /api/gallery/previews              ?archetype_id=&selected_only=
GET  /api/jobs/{id}/telemetry
GET  /api/jobs/{id}/step-timing
GET  /api/files/{id}/{path}
POST /api/jobs/{id}/run/{step}          operator override (debug)
```

---

## Key environment facts

- Server: `.venv/bin/python ribotome.py` (or `workshop_lab.py` via shim)
- Env file: `/home/director/intelluric/local/intelluric/intelluric-site.env`
- DB: `local_state/workshop.db`
- Jobs dir: `local_state/jobs/`
- Gallery index: `local_state/preview_gallery/index.json`
- Model routes: `model_gateway.py` — anchor/draft/quality = `gpt-5.4`, classify = `gpt-4.1-nano`, image = `gpt-image-2`

---

## Git

- `main` — current, commit `38e9f4f`
- `archive/2026-07-27-pre-sawc` — remote-only, state of main just before this push
- `archive/2026-07-26-pre-upgrade` — older archive

---

## Suggested skills

- `/code-review` — before syncing the SAWC layer to `pipeline_lab.py`
- `/handoff` — at the start of the next session to reload context
