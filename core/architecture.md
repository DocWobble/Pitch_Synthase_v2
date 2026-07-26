# Pitch Synthase v2 — Architecture

## What this is

A five-stage pipeline that turns a raw pitch description into a fully-rendered slide deck. Each stage is an async Python worker called by a FastAPI server. All LLM prompt construction lives in `prompts.py`; all orchestration and API calls live in `workers.py`. The server (`workshop_lab.py`) manages job state and dispatches workers via background threads.

---

## Components

### `workshop_lab.py` — HTTP server + orchestrator
FastAPI server running on port 8793. Exposes REST endpoints that create jobs, accept uploads, trigger individual pipeline steps, and serve a browser UI for monitoring. Workers run in a background thread pool (`_run_async_in_thread`) to avoid blocking the event loop.

Key endpoints:
- `POST /api/jobs` — create job, immediately dispatch approach_drafter_worker
- `POST /api/jobs/{id}/intake` — update pitch fields, save supporting image
- `POST /api/jobs/{id}/run/{step}` — manually trigger a pipeline step (approaches / designs / previews / paid / verification)
- `POST /api/jobs/{id}/select-approach` — record chosen approach before payment
- `POST /api/jobs/{id}/complete-review` — submit per-slide edits, trigger verification_worker

Hot-reload: `workshop_lab.py` hot-reloads `prompts.py` on `/api/save-prompts`. Changes to `workers.py` require a full server restart (`lsof -ti :8793 | xargs kill -9`).

### `prompts.py` — all prompt construction
The only file that constructs strings sent to the API. Workers import it and call named functions. No prompt strings are built in `workers.py`.

Model constants:
```python
ANCHOR_MODEL           = "gpt-5.4"        # anchor writer — most load-bearing text call
TEMPLATE_DRAFTER_MODEL = "gpt-5.4"        # all other text stages
DECK_DRAFTER_MODEL     = "gpt-5.4"        # storyboard builder + image gen orchestration
CLASSIFY_MODEL         = "gpt-4.1-nano"   # single-token closed-set classification only
IMAGE_MODEL            = "gpt-image-2"    # all image generation
```

Model selection rationale: `gpt-5.4` (last-gen flagship) has proven quality at half or less the token cost of current-gen mini/nano tiers. `gpt-4.1-nano` is reserved only for calls that produce a single token from a fixed set (e.g. "product / brand / neither"). Do not upgrade to current-gen mini/nano as a cost-reduction measure — they are more expensive than the last-gen flagship.

### `workers.py` — pipeline workers
One async function per pipeline stage. All model calls go through `_responses_create()`, which wraps the OpenAI Responses API, logs telemetry, and writes output to disk. Image generation calls must await `_throttle_image_generation()` before each call — this is a process-wide rolling-window rate limiter (50 img/min, Tier 3 limit).

### `db.py` — job state
SQLite via a thin wrapper. Each job is a row with a JSON blob for flexible fields. `claim_job_status(job_id, allowed_statuses, new_status)` is an atomic check-and-set that prevents duplicate worker runs.

---

## Pipeline stages

### Phase 0 — Intake

User posts pitch text (elevator_pitch, conveys, audience) and an optional supporting file:
- `.txt` / `.md` / `.pdf` → extracted as `doc_text`, passed verbatim to text prompts as source material
- `.png` / `.jpg` / `.webp` → saved to `jobs/{id}/intake/`, used as multimodal image input in downstream stages

The supporting image is the key branching variable. The system classifies it once in Phase 2 and routes all downstream image generation accordingly.

### Phase 1 — Approach Drafting (`approach_drafter_worker`)

Generates four distinct strategic framings for the pitch. Each framing is an **archetype interference pair** — two archetypes (A, B) whose properties are summed: where they conflict a dimension flattens, where they align it amplifies.

If a supporting image is present, it is attached as a multimodal message alongside the text prompt with the label "BRAND REFERENCE IMAGE." The `has_brand_reference` flag tells the prompt to ground each approach's `visual_direction` in that existing identity rather than inventing from scratch.

**Output contract:** 4 approach objects, each containing:
- `archetype_a`, `archetype_b` — full archetype objects (may be identical for a doubled pair)
- `effective_archetype_id` — dominant archetype in the interference sum
- `label`, `pitch_angle`, `key_differentiator`, `visual_direction`

**Validator rules:** 4 distinct unordered pairs (frozenset deduplication), no pair repeated across the four approaches. One approach may double the same archetype (X, X) for maximum constructive interference — full amplitude at the cost of zero cancellation. No shared primary across all four; each approach picks its own pair independently.

### Phase 2 — Design Drafting (`design_drafter_worker`)

Determines the visual reference for each approach. Two paths:

**Path A — Supporting image provided:**
1. Classification call (CLASSIFY_MODEL): "Is this image a product, brand, or neither?"
2. If `product` or `brand`: store `design_reference_image_path` and `design_reference_type` on each approach object. Return early — no new image is generated. The existing image becomes the reference.
3. If `neither`: fall through to Path B.

**Path B — No usable reference:**
1. Text call (TEMPLATE_DRAFTER_MODEL): `design_drafter_prompt` generates 4 design specs with `reference_image_prompt` per approach.
2. Image gen × 4 (DECK_DRAFTER_MODEL + image_generation_tool): one reference image per approach.

The `design_reference_type` field (`"brand"` or `"product"`) is stored on each approach and used in Phase 3 to select the appropriate prompt label for image generation.

### Phase 3 — Approach Previews (`single_slide_preview_worker × 4`, parallel)

One preview slide per approach. Runs concurrently for all four.

1. Text call (TEMPLATE_DRAFTER_MODEL): `single_slide_drafter_prompt` receives `archetype_a` and `archetype_b` for the approach's interference blend. Produces a single slide draft (headline, body, visual notes).
2. Image gen (DECK_DRAFTER_MODEL + image_generation_tool): uses `design_reference_type` to branch:
   - `brand` → "BRAND VISUAL REFERENCE — match color palette, typography, iconographic style"
   - `product` → "PRODUCT DESIGN REFERENCE — replicate exact form factor, materials, proportions"

The user sees four preview slides and selects one approach before payment.

### Phase 4 — Full Deck Generation (`generation_worker`)

Three sequential sub-phases after the job reaches status `paid`:

**4a — Anchor writer (ANCHOR_MODEL):** Produces `deck_generation_prompt` — the single authoritative pitch narrative that all slide generation is grounded in. This is the most load-bearing text call in the system. Errors here propagate to every slide.

**4b — Deck builder storyboard (DECK_DRAFTER_MODEL):** Plans all N slides at once. Output: per-slide `title`, `purpose`, and `image_prompt`, plus a `visual_grammar` object that enforces deck-wide visual consistency. Validates: exact slide count, sequential slide numbers, no missing image_prompts.

**4c — Slide image gen × N (DECK_DRAFTER_MODEL + image_generation_tool):** Generates each slide image in parallel, chunked in groups of 4, each call passing through the rate limiter. The prompt for each slide combines the anchor narrative with the per-slide `image_prompt` from the storyboard. Up to 2 per-slide failures are tolerated; more than 2 fails the job.

### Phase 5 — Verification & Finalization (`verification_worker`)

Runs after the user optionally submits per-slide edit instructions. Per-slide:

1. **Judge** (`_judge_slide`): compare the generated slide image against the storyboard intent, source materials, and any user review notes. Accept as-is or flag for regeneration.
2. **Regenerate if needed** (`_verify_and_finalize_slide`): uses the same anchor narrative as the original generation call (not just the per-slide image_prompt) to avoid concept drift.
3. **Composite** (`_composite_slide`): overlays text regions and speaker notes.
4. **Export** (`_build_pdf`, `_build_pptx`, `_build_html`, `_build_markdown`): renders all formats.

The `excepted_inference_elements` list controls which content categories the verifier is permitted to infer (e.g. `"pricing"` means pricing claims may not be inferred — the verifier blocks or strips them). Default excepted: `["pricing"]`. `mockup_prototype_design` is not excepted by default and must be set explicitly.

---

## Archetype interference system

Archetypes are rhetorical personas — each has a `win_condition`, `rhetorical_temperature`, `evidence_posture`, and visual preferences. The interference model:

- **Distinct A ≠ B:** Properties that conflict cancel (flatten to neutral); properties that align amplify. The `effective_archetype_id` is the dominant one.
- **Doubled A = B:** Full constructive interference. Every dimension pushed to that archetype's functional extreme. Maximum rhetorical temperature and most committed evidence posture. Valid for at most one of the four approaches.

This produces four approaches that differ not just in label but in fundamental rhetorical strategy, making the preview selection a meaningful creative decision rather than a cosmetic one.

---

## Data on disk

```
jobs/{job_id}/
  intake/           uploaded brand/product images
  candidates/       approach_manifest.json, design ref images
  strategy/         anchor_writer_output.json, paid_storyboard.json, slide_specs.json
  slides/           slide_NN_proof.png (generated)
  single_slide_previews/  approach preview images
  export/
    slides/         slide_NN_final.png (verified + composited)
    presenter-view/ slide_NN_proof.png copies
    deck.pdf / deck.pptx / deck.html / deck.md
  telemetry/        per-event JSONL logs
```

---

## Rebuilding from scratch

The minimum viable rebuild requires:
1. `prompts.py` — all prompt functions and model constants
2. `workers.py` — all pipeline workers and helper functions
3. `db.py` — SQLite job store with `create_job`, `get_job`, `update_job`, `claim_job_status`
4. `workshop_lab.py` — FastAPI server, background thread runner, UI
5. OpenAI account at Tier 3+ for image generation rate limits
6. Environment: `OPENAI_API_KEY`, optional `PITCH_SYNTHASE_IMAGE_MODEL`, `PITCH_SYNTHASE_QUALITY_MODEL`

`pipeline.md` (the companion diagram) is the authoritative visual map of how these components connect.
