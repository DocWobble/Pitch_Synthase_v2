# Pitch Synthase v2 — Architecture

## What this is

A DAG-dispatched pipeline that turns a raw pitch description into a
fully-rendered, human-reviewed slide deck. Each stage is an async Python
worker. All LLM prompt construction lives in `prompts.py`; all orchestration
and API calls live in `workers.py`. The server, `ribotome.py`, owns job
state, the stage graph, and dispatch — it is the canonical entry point.
`workshop_lab.py` is kept only as a backward-compatible import shim
(`from ribotome import *`); it contains no logic of its own.

---

## Components

### `ribotome.py` — HTTP server + DAG dispatcher
FastAPI server running on port 8793. Pipeline stages are declared with
`register_stage({...})` (one call per stage, near the top of the file) into
a `PIPELINE_STAGES` list / `_STAGE_REGISTRY` map. Each job has a
`stage_states` JSON column (`pending` / `in_progress` / `completed` /
`skipped` / `failed` per stage). `_advance(job_id)` is the dispatcher: every
time any endpoint changes job state, it re-scans `PIPELINE_STAGES` and fires
any stage whose dependencies are now satisfied. This is not a linear script
— stage order is whatever the dependency graph allows, and conditional
stages (e.g. the prototype-design branch) auto-skip when their condition is
false rather than being explicitly routed around.

At startup, `_audit_pipeline()` runs before the server accepts traffic and
raises `RuntimeError` (refusing to boot) if any stage's `input_schema` field
has no upstream producer, or any stage's `output_fields` entry is never
consumed anywhere in the graph. This makes a broken field wire a boot-time
failure, not a runtime surprise.

Key endpoints (see `WIZARD_INTEGRATION_SCHEMA.md` for the full contract):
- `POST /api/jobs` — create job, dispatches whatever stages have no
  dependencies (`design_refs_text`, `approach_draft` once that resolves)
- `POST /api/jobs/{id}/intake` — update pitch fields, save supporting file
- `POST /api/jobs/{id}/select-prototype` — satisfy `human_prototype_selection`
- `POST /api/jobs/{id}/select-approach` — satisfy `human_selection`
- `POST /api/jobs/{id}/mock-payment/{kind}` — lab-only payment bypass;
  satisfies `human_payment` (no live Stripe integration is wired up yet —
  see `WIZARD_INTEGRATION_SCHEMA.md` §Payment for the real integration point)
- `POST /api/jobs/{id}/complete-review` — submit per-slide edits, satisfies
  `human_review`, triggers `verification_worker`
- `GET /api/jobs/{id}/next-action` — tells a client which human gate (if
  any) is currently blocking the job
- `GET /api/jobs/{id}/stream` — SSE progress feed

Workers run in a background thread pool (`_run_stage_in_thread`) so they
don't block the event loop. Before dispatch, each stage's declared
`input_schema` required fields are checked non-null against the live job
row — a gate that was skipped or never called fails fast with a named
missing-field error instead of the worker discovering it mid-call.

### `prompts.py` — all prompt construction
The only file that constructs strings sent to the model API. Workers import
it and call named functions; no prompt strings are built in `workers.py`.

Model constants (still used directly by most call sites):
```python
ANCHOR_MODEL           = "gpt-5.4"        # anchor writer — most load-bearing text call
TEMPLATE_DRAFTER_MODEL = "gpt-5.4"        # most other text stages
DECK_DRAFTER_MODEL     = "gpt-5.4"        # storyboard builder + image gen orchestration
CLASSIFY_MODEL         = "gpt-4.1-nano"   # single-token closed-set classification only
IMAGE_MODEL            = "gpt-image-2"    # all image generation
```
`model_gateway.py` additionally defines a `ModelGateway`/`gateway` singleton
with its own `MODEL_ROUTES` table (`anchor`, `draft`, `quality`, `classify`,
`image`) and an image-generation semaphore. A minority of call sites route
through `gateway.model_for(...)`/`gateway.responses`; most still call the
`prompts.py` constants directly — both exist side by side today, the gateway
has not fully replaced the constants.

Model selection rationale: `gpt-5.4` (last-gen flagship) has proven quality
at half or less the token cost of current-gen mini/nano tiers. `gpt-4.1-nano`
is reserved only for calls that produce a single token from a fixed set.
Do not upgrade to current-gen mini/nano as a cost-reduction measure — they
are more expensive than the last-gen flagship for this workload.

### `workers.py` — pipeline workers
One async function per pipeline stage (plus a legacy "Path A" set —
`template_worker`, `candidate_worker`, `convert_generation_worker` — kept
for backward compatibility but not part of the primary DAG). All model calls
go through `_responses_create()`, which wraps the OpenAI Responses API, logs
telemetry, and writes output to disk. Image generation calls await
`_throttle_image_generation()` before each call — a process-wide rolling
60-second-window rate limiter (`_IMAGE_RATE_LIMIT = 50`).

### `db.py` — job state
SQLite via a thin wrapper. Each job is a row; several columns are JSON blobs
for flexible/nested fields (`stage_states`, `approach_candidates_json`,
`prototype_candidates_json`, `slide_specs`, `reviewed_slides`, etc.).
`set_stage_state()` does an atomic single-statement `UPDATE` with
`JSON_SET`/`JSON_INSERT` — no read-modify-write race window.

---

## Pipeline stages

Registered in `ribotome.py` via `register_stage()`; this is the exact
current stage list, not an approximation:

| stage | depends on | condition | notes |
|---|---|---|---|
| `design_refs_text` | — | `has_prototype` | drafts 4 prototype design candidates (text only) |
| `approach_draft` | `design_refs_text` | — | drafts 4 strategic approaches (archetype pairs) |
| `image_scan` | — (event-triggered) | — | fires on intake image upload, analyzes it |
| `human_intake` | `approach_draft` | — | human gate: how to use any uploaded image |
| `design_refs_images` | `design_refs_text` | `has_prototype` | renders a reference image per design candidate |
| `human_prototype_selection` | `design_refs_images` | `has_prototype` | human gate: pick one prototype design |
| `previews` | `approach_draft`, `human_prototype_selection`, `human_intake` | — | renders one preview slide per approach |
| `human_selection` | `previews` | — | human gate: pick one approach |
| `human_payment` | `human_selection` | — | human gate: pay, set slide count |
| `generation` | `human_payment` | — | full deck: factual Anchor → parallel FNS/VGS → rhetorical storyboard → Ghostwriter → Spirit Boarder → N slide images |
| `human_review` | `generation` | — | human gate: per-slide edits/regeneration requests |
| `verification` | `human_review` | — | judge/regenerate per slide, composite, export |

`has_prototype` is the job's `infer_prototype` flag, fixed at creation. When
false, the three prototype-branch stages above auto-skip and the deck is
built without a rendered product/prototype reference image.

### Approach drafting (`approach_drafter_worker`)

Generates four distinct strategic framings for the pitch. Each framing pairs
two `FOUNDER_ARCHETYPES` entries (archetype_a, archetype_b) — the 12-entry
roster of historical rhetorical postures (Buffett, Edison, Leonardo,
Franklin, Lauder, Jobs, Barnum, Chanel, Rockefeller, Ford, Morgan, Disney).
Each archetype's `win_condition` describes a domain-agnostic rhetorical
proof strategy (how that persona convinces a skeptic), not a business
mechanism — e.g. Edison's is "proof through performance: the working
demonstration itself is the argument."

**Output contract:** 4 approach objects (`approach_1`..`approach_4`), each
with `approach_id`, unique `label`, `pitch_angle`, `key_differentiator`,
`visual_direction`, `archetype_a`, `archetype_b`. Validator rejects: wrong
count, duplicate labels, or any archetype pair repeated across the four.

### Prototype design drafting (`design_drafter_text_worker` / `design_drafter_images_worker`)

Only runs when `infer_prototype` is set. Selects 4 candidates from
`DESIGN_ARCHETYPES` — a separate 6-entry pool of named industrial-design
personas (Rams, Dyson, Eames, Mead, Fukasawa, Starck) — each producing a
`design_philosophy`, `physical_description`, and a rendered reference image.
The user picks one (`human_prototype_selection`) before preview generation.

### Previews (`previews` stage, one preview slide per approach, parallel)

Renders one representative slide per approach so the human can compare
strategic framings visually, not just as text. The winning approach's
preview slide is later reused as a visual precedent when the full deck's
storyboard call runs (see below) — the design/approach decisions the human
already made are carried forward, not re-litigated by later stages.

### Full deck generation (`generation_worker`, fires once `human_payment` completes)

Six internal sub-phases (still one DAG stage and one wizard screen):

1. **Canonical Anchor** (`ANCHOR_MODEL`): produces a complete, source-grounded
   rhetorical brief. It preserves material facts, ranges, caveats, statuses,
   phase gates, and absences. It cannot invent founders, team biographies,
   traction, room events, or a presentation that supposedly happened.
2. **FNS and VGS**, in parallel: FNS produces the direct system prompt for the
   founder Ghostwriter; VGS produces the direct system prompt for the Spirit
   Boarder. Neither output reaches Deck Builder.
3. **Rhetorical Deck Builder**: plans all N+2 slide jobs, detailed required
   propositions, logical relationships, priorities, and capacity constraints.
   It writes no visible copy, layout, visual grammar, or image prompt.
4. **Ghostwriter**: receives the FNS system prompt, rhetorical storyboard, and
   authoritative source text. It authors the only final title/body copy. It
   receives no image prompts or visual references.
5. **Spirit Boarder**: receives the VGS system prompt, rhetorical storyboard,
   immutable final copy, and all applicable actual image references. It authors
   the only per-slide composition intent and self-contained image prompt, but
   cannot add or rewrite visible prose.
6. **Slide image generation × (N+2)**, chunked and rate-limited. Each renderer
   receives only the self-contained Spirit Boarder specification, exhaustive
   exact-copy contract, and applicable actual images. Anchor/source prose is not
   redundantly reopened.

Output fields remain unchanged for frontend compatibility: `slide_specs`
(per-slide headline/body_points/speaker_note) and `deck_proof_plan` (bundles
the canonical Anchor plus the completed rhetorical, copy, and Spirit Boarder
authorities used by verification and regeneration).

### Human review & verification (`human_review` → `verification_worker`)

The user may edit any slide's headline/body/notes and flag specific slides
for re-verification (or none, or all). Per flagged slide:

1. **Judge** (`_judge_slide`): compares the generated image against the
   storyboard intent, source materials, and any user review notes/edits.
   Accepts as-is, or emits `corrective_constraints` for regeneration.
2. **Regenerate if needed** (`_verify_and_finalize_slide`): splices the
   judge's corrective constraints onto the original Spirit Boarder prompt,
   recompiles the exact-copy contract from any reviewed headline/body text,
   and restores applicable actual image references. It does not reopen Anchor
   or raw-source prose.
3. **Composite**: overlays text regions and speaker notes.
4. **Export**: `deck.pdf`, `deck.pptx`, `preview.html`, `slides.md`,
   `manifest.json`, `verification_report.json`, bundled into
   `pitch_deck_package.zip`.

Every slide's verifier call sees every other slide's user edits too (deck-
wide `reviewed_slides` context), not just its own slide in isolation.

The `excepted_inference_elements` list controls which content categories the
verifier is permitted to infer freely (default: `["pricing"]` — pricing
claims may never be invented). `founder_biography` defaults to `SOURCE`
mode for the same reason: named people and their roles are falsifiable facts
that must come from user-supplied material, never be inferred.

---

## Data on disk

```
local_state/jobs/{job_id}/
  intake/                 uploaded supporting images
  approaches/             approach_manifest.json, approach prompt/response logs
  design_references/      prototype design candidates + reference images (has_prototype only)
  single_slide_previews/  one preview image per approach
  strategy/               anchor_writer_output.json, founder_narrative_output.json,
                           visual_grammar_output.json, paid_storyboard.json,
                           paid_storyboard_ghostwriter_output.json,
                           paid_spirit_boarder_output.json, deck_proof_plan.json
  slides/                 slide_NN_proof.png (as-generated, pre-verification)
  export/
    slides/               composited final slide images
    presenter-view/       speaker-note view
    deck.pdf / deck.pptx / preview.html / slides.md
    manifest.json / verification_report.json
    pitch_deck_package.zip
  telemetry/              per-event JSONL logs
candidates/                legacy Path A only (template_worker/candidate_worker) — not used by the primary DAG
```

---

## Rebuilding from scratch

The minimum viable rebuild requires:
1. `prompts.py` — all prompt functions, `FOUNDER_ARCHETYPES`,
   `DESIGN_ARCHETYPES`, model constants
2. `workers.py` — all pipeline workers and helper functions
3. `model_gateway.py` — shared OpenAI client boundary
4. `db.py` — SQLite job store with `create_job`, `get_job`, `update_job`,
   `set_stage_state`, `get_stage_states`
5. `ribotome.py` — FastAPI server, `register_stage`/`_advance` DAG
   dispatcher, `_audit_pipeline` boot gate, all REST endpoints
6. OpenAI account at a tier supporting ≥50 image generations/minute
7. Environment: `OPENAI_API_KEY`

`pipeline.md` (the companion diagram) is the authoritative visual map of how
these components connect. `WIZARD_INTEGRATION_SCHEMA.md` documents the full
REST contract for a client application.
