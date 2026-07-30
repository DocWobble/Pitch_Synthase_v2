# Pitch Synthase v2 — Production-Ready Semantic Description

| Field | Value |
|---|---|
| Source | Five-Manual Token Stack Analysis |
| Manuals | AI Coding · Boundary State · Runtime · Commercial · Outcome Field Manuals |
| Repo | DocWobble/Pitch_Synthase_v2 |
| Spec | PS-V2-SPEC v2.5.0 |
| Manifest | PS-V2-MANIFEST v1.0 |
| Date | 2026-07-30 |

---

## Canonical Stacked Token Grammar

```
Build  [guided-flow-pipeline] [creation-manipulation-surface]
       [quality-gate-pipeline] [semantic-governance-system]

with   [approach-drafting] [visual-grammar-lock]
       [per-slide-verification] [pitch-aspect-policy]

Run as [dag-dispatcher-runtime] [thread-pool-workers] [sse-stream]
       [sqlite-persistence] [image-rate-limiter] [startup-audit-gate]

Treat  [pitch-aspect-modes] [audit-pipeline] [inferred-element-decisions]
       as authority.

Deliver [production-ready] where
        [live-payment-integration] [authenticated-endpoints]
        [health-check-endpoint] [env-configurable-models]
        [production-database] [integration-pr-filed]
        [cross-batch-label-uniqueness] [dead-code-resolved]
        are simultaneously true.

Prove completion with [posthog-event-chain] [stripe-charge-receipt]
                      [operator-zero-interventions] [export-zip-delivered]
```

---

## Layer 01 · Artifact — What Pitch Synthase v2 Is

Pitch Synthase v2 is a compound artifact: six distinct functional surfaces that co-exist in the same codebase, each independently demoable and each contributing a different class of work to the pitch-deck generation goal.

### Artifact Tokens

| Token | Surface |
|---|---|
| `[guided-flow-pipeline]` | 12-stage DAG with 5 human gates (pitch intake → optional prototype selection → approach selection → payment → per-slide review). `GET /api/jobs/{id}/next-action` tells the client which gate is currently blocking. |
| `[creation-manipulation-surface]` | Generates slide deck artifacts in 5 formats: PDF, PPTX, standalone HTML, Markdown, and `pitch_deck_package.zip`. Per-slide PNG proofs stored pre-verification; composited finals and speaker-note views in `export/`. |
| `[intelligence-processing]` | Six model tiers: `gpt-5.4` anchor writer (most load-bearing), `gpt-5.4` template drafter, `gpt-5.4` deck builder/storyboard, `gpt-4.1-nano` closed-set classifier (single-token only), `gpt-image-2` image generation, `gpt-5.4` per-slide verification judge/regeneration chain. |
| `[control-operations]` | `ribotome.py`: FastAPI server (port 8793), `register_stage()` DAG declaration, `_advance(job_id)` dispatcher, `_audit_pipeline()` boot-time schema audit that refuses to start if any declared field has no producer or no consumer. |
| `[quality-gate-pipeline]` | Per-slide fan-out after human review: 3 independent narrow verifiers (spelling / source-accuracy / diagram-accuracy) → judge disposition → conditional regeneration using verbatim original `image_prompt` + corrective constraints splice. Every verifier sees all slides' user edits, not only its own. |
| `[semantic-governance-system]` | Six-axis Pitch Aspect controls (`customer/value`, `market/competition`, `commercialization/financials`, `proof/defensibility`, `execution/team/risk`, `transaction/ask`), each tri-state: `OMIT` / `INFER` / `SOURCE`. Modes propagate through generation, verification, judging, and regeneration. Default: all `INFER`. |

### Component Inventory

| File | Role | Size |
|---|---|---|
| `ribotome.py` | HTTP server + DAG dispatcher + boot audit | 89 KB |
| `prompts.py` | All prompt construction; FOUNDER_ARCHETYPES (12); DESIGN_ARCHETYPES (6); model constants | 209 KB |
| `workers.py` | All pipeline workers; image rate limiter; `_responses_create()` wrapper | 161 KB |
| `db.py` | SQLite job store; atomic `JSON_SET` stage state; PostHog telemetry | 18 KB |
| `model_gateway.py` | Shared OpenAI client boundary; `MODEL_ROUTES`; image semaphore | 1.2 KB |
| `workshop_lab.py` | Backward-compat shim only (`from ribotome import *`) | 0.2 KB |

---

## Layer 02 · Behavior — What Logic Is Inside

The full behavior contract runs in three phases: **intake and framing** (approaches, optional prototype), **generation** (anchor → visual grammar → storyboard → images), and **verification and export**. All phases are governed by Pitch Aspect modes set at intake time.

### Behavior Tokens

`[approach-drafting]` `[prototype-design-drafting]` `[visual-grammar-lock]` `[per-slide-verification]` `[pitch-aspect-policy]` `[inferred-element-removal]` `[multi-format-export]`

### Input Contract

- **Required:** `elevator_pitch`, `audience`, `association_words` (exactly 3)
- **Optional:** `doc_text` (supporting document), supporting image, `selected_archetype_id` (pin to one of the 12 FOUNDER_ARCHETYPES), `infer_prototype` flag
- **Governance:** `pitch_aspect_modes` — one tri-state value per axis; defaults to all `INFER` if not specified

### Phase 1 — Intake & Framing

- One text call → 4 strategic approach objects, each pairing two FOUNDER_ARCHETYPES entries (Buffett, Edison, Leonardo, Franklin, Lauder, Jobs, Barnum, Chanel, Rockefeller, Ford, Morgan, Disney). Each approach: `approach_id`, unique `label`, `pitch_angle`, `key_differentiator`, `visual_direction`, `archetype_a`, `archetype_b`.
- Optional prototype branch (when `infer_prototype`): 4 candidates selected from DESIGN_ARCHETYPES (Rams / Dyson / Eames / Mead / Fukasawa / Starck), each with text design philosophy + rendered reference image. User picks one before previews proceed.
- One preview slide rendered per approach (4 total); user selects one approach visually.
- Selected approach's preview slide is carried forward as a visual precedent into full-deck storyboard — decisions already made are not re-litigated by later stages.

### Phase 2 — Full Deck Generation (after payment gate)

- **Anchor writer** (`gpt-5.4`): produces the deck's authoritative narrative from user materials + chosen archetype pair + chosen preview slide. Single most load-bearing text call; everything downstream is grounded in it.
- **Visual Grammar Synthesizer**: extracts and locks one coherent visual grammar (palette, typography, layout system, production tools). Mandatory — failure here fails the entire `generation` stage; not silently swallowed.
- **Deck builder storyboard**: N+1 total slides (user-requested N content slides + exactly one title/hero at slide 1, no separate hero). Each slide: `slide_number`, `title`, `purpose`, `style_tags`, `body_points` (2–5 short bullet phrases), `image_prompt`.
- **Image generation**: per-slide calls combining anchor payload + locked visual grammar + per-slide `image_prompt`. Chunked and rate-limited. `partial_images: 0` enforced — only completed images consumed.

### Phase 3 — Verification & Export

- User may edit any slide's headline/body/notes and flag slides for re-verification.
- Per flagged slide: judge compares image vs. storyboard intent + source materials + user edits → emits `corrective_constraints` or accepts as-is → conditional regeneration using verbatim original `image_prompt` + constraints splice (never judge-rewritten).
- Composite text regions and speaker notes. Export: `deck.pdf`, `deck.pptx`, `preview.html`, `slides.md`, `manifest.json`, `verification_report.json` → `pitch_deck_package.zip`.

### Behavioral Invariants — Must Hold at Every Run

- `_audit_pipeline()` at boot: every declared field has both a producer and a consumer — `RuntimeError` otherwise; server refuses traffic
- `_validate_approach_manifest`: exactly 4 approaches; IDs `approach_1`–`approach_4` in order; all labels unique; no archetype pair repeated across the four
- Visual Grammar Synthesizer mandatory: failure fails entire `generation` stage
- Corrective-constraints splice: verbatim original `image_prompt` — judge cannot rewrite the prompt, only append constraints
- Pitch Aspect modes are senior to all narrower exceptions (`authorized_inferences`, `canon_overrides`)
- `partial_images: 0` on all image calls — no partial image consumption permitted
- Slide 1 is the sole title/hero slide — no separate `hero_proof.png` call; no pagination chrome on any slide
- `founder_biography` defaults to `SOURCE` mode — named people and their roles must come from user-supplied material, never inferred

---

## Layer 03 · Runtime — How Work Proceeds

The runtime is a FastAPI event loop with stages dispatched to a background thread pool. The DAG dispatcher fires stages automatically when their dependencies are satisfied; human gates block dispatch until satisfied via REST endpoints.

### Active Runtime Tokens

`[dag-dispatcher-runtime]` `[thread-pool-workers]` `[sse-stream]` `[sqlite-persistence]` `[image-rate-limiter]` `[startup-audit-gate]` `[per-event-telemetry]` `[debug-stub-mode]`

### Active Mechanisms

| Token | Description |
|---|---|
| `[dag-dispatcher-runtime]` | `_advance(job_id)` re-scans `PIPELINE_STAGES` after every state change, fires any stage whose dependencies are satisfied. Conditional stages auto-skip when their condition is false. |
| `[thread-pool-workers]` | `_run_stage_in_thread` dispatches each stage to a thread pool; workers are sync functions; event loop never blocked. Each stage's declared `input_schema` fields are null-checked before dispatch. |
| `[sse-stream]` | `GET /api/jobs/{id}/stream` delivers real-time stage progress as Server-Sent Events. |
| `[sqlite-persistence]` | Single file `local_state/workshop.db`. `set_stage_state()` uses atomic `JSON_SET`/`JSON_INSERT` — no read-modify-write race window. |
| `[image-rate-limiter]` | `_throttle_image_generation()` enforces a process-wide rolling 60-second window of 50 images/minute (`_IMAGE_RATE_LIMIT = 50`). |
| `[per-event-telemetry]` | Per-event JSONL logs per job in `telemetry/`. PostHog events emitted on stage transitions. |
| `[debug-stub-mode]` | `PS_DEBUG_MODE=1` stubs all image generation with a Pillow-rendered placeholder PNG (1456×816). Text calls pass through live. |

### Runtime Gaps — Blocking Production-Ready

| Token | Gap |
|---|---|
| `[missing: health-check-endpoint]` | No `/health` route. No liveness or readiness signal for a load balancer or orchestrator. |
| `[missing: authenticated-endpoints]` | All routes unauthenticated. `POST /api/jobs` can be called anonymously, spending model budget without identity. |
| `[missing: live-payment-integration]` | `POST /api/jobs/{id}/mock-payment/{kind}` is a lab-only bypass. No Stripe integration. See `WIZARD_INTEGRATION_SCHEMA.md §Payment`. |
| `[missing: env-configurable-models]` | `ANCHOR_MODEL`, `TEMPLATE_DRAFTER_MODEL`, `DECK_DRAFTER_MODEL` hardcoded to `"gpt-5.4"` in `prompts.py`. `model_gateway.py` migration is incomplete — a minority of call sites route through it. |
| `[missing: production-database]` | SQLite appropriate for workshop; not for concurrent multi-user load. |
| `[missing: deployment-descriptor]` | No Dockerfile, no container image. `requirements.txt` exists but dependencies are not version-pinned. |
| `[missing: circuit-breaker]` | `_responses_create()` wraps the OpenAI Responses API but timeout/retry/circuit-break contract is undocumented and unenforced. |
| `[missing: per-user-rate-limiting]` | Image rate limiter is process-wide. One large concurrent deck can starve other users. |
| `[missing: secrets-management]` | `OPENAI_API_KEY` sourced from ambient env file, not a secrets store. |

---

## Layer 04 · Authority — What Governs It

Four authority layers, strictly ordered: structural boot authority overrides everything; Pitch Aspect modes govern all content; narrower inference exceptions operate only within the space Pitch Aspect modes permit; user-authority checkboxes apply only at review time.

### Authority Tokens

`[audit-pipeline-gate]` `[pitch-aspect-modes]` `[excepted-inference-elements]` `[inferred-element-decisions]` `[approach-manifest-validator]`

### Authority Hierarchy (senior → junior)

1. **`[audit-pipeline-gate]`** — `_audit_pipeline()` runs at boot before any traffic is accepted. Raises `RuntimeError` if any declared field in the stage graph lacks a producer or a consumer. A broken field wire is a boot refusal, not a runtime warning.

2. **`[pitch-aspect-modes]`** — 6 tri-state controls (`OMIT`/`INFER`/`SOURCE`) governing what content categories the pipeline may freely generate, must source from user material, or must omit entirely. Senior to all other content decisions. Propagate through generation, verification, judging, and regeneration workers.

3. **`[excepted-inference-elements]`** — `authorized_inferences` / `canon_overrides` (`pricing` and `mockup_prototype_design`). Narrow permissions that only open within an `INFER` aspect — cannot reopen content marked `SOURCE` or `OMIT`. Each decided exactly once and enforced for deck-wide consistency.

4. **`[inferred-element-decisions]`** — Per-element user checkboxes at review. Unchecked: content relabeled "proposed." Checked with no edit: hard block, zero spend. Checked with valid edit: inferred/disclosure wording stripped, user value shipped as plain fact. Checked with invalid edit: slide dropped from composited exports only.

5. **`[approach-manifest-validator]`** — `_validate_approach_manifest` enforces output contract of the approach drafting stage: exactly 4 approaches, IDs in order, all labels unique, no archetype pair repeated. Rejects on any violation before the job can proceed.

### Authority Gap

`[missing: payment-authority]` — `human_payment` gate is the commercial authority checkpoint for spending generation budget. Currently satisfied by `mock-payment/{kind}` — a dev-only bypass with no real authority. A real payment receipt must be the only valid satisfier of this gate in production.

---

## Layer 05 · Boundary State — What Must Be True at Completion

`[production-ready]` for Pitch Synthase v2 is not a single condition — it is a set of 14 obligations that must hold **simultaneously**. The system is not production-ready until every one is true. No partial credit: an unauthenticated endpoint makes the whole system not production-ready regardless of all other completions.

### Boundary-State Tokens

`[production-ready]` `[live-payment-integration]` `[cross-batch-label-uniqueness]` `[dead-code-resolved]` `[design-archetype-collapse-fixed]` `[workshop-production-diff-explicit]` `[integration-pr-filed]` `[health-check-endpoint]` `[authenticated-endpoints]` `[circuit-breaker]` `[per-user-rate-limiting]` `[env-configurable-models]` `[secrets-management]` `[deployment-descriptor]` `[production-database]`

### Functional Completeness — 5 obligations

1. **`[live-payment-integration]`** ❌ Open — `mock-payment/{kind}` replaced with real Stripe; `human_payment` satisfiable only by a verified payment receipt in production.

2. **`[cross-batch-label-uniqueness]`** ❌ Open — Approach labels unique across all generation attempts for the same job, not just within one 4-approach call. Labels "Deterministic Chain," "Behavioral Truth," and "Proof Cascade" have recurred across independent runs. Requires cross-batch enforcement in `_validate_approach_manifest` or including prior labels in the prompt context.

3. **`[dead-code-resolved]`** ❌ Open — Stages 02 (`pitch_quality_gate_worker`), 03 (`pitch_elaborator_worker`), and 05 (`approach_regeneration_worker`) defined in `workers.py` but not registered in the DAG and not reachable from any endpoint. Each must be either restored as a registered stage with real wiring, or removed from the codebase.

4. **`[design-archetype-collapse-fixed]`** 🔶 Partial — `design_drafter_text_worker`'s 4-of-6 pick from DESIGN_ARCHETYPES reliably collapses to Rams/Dyson/Eames + one of {Mead, Starck}; Fukasawa has never been selected. Removing a JSON-shape anchor improved the 4th slot but did not fix the lock-in. Requires a structural fix: derive optimization axes from the product spec, not a fixed named-designer menu.

5. **`[workshop-production-diff-explicit]`** + **`[integration-pr-filed]`** ❌ Open — Fresh side-by-side diff of workshop `prompts.py`/`workers.py` vs. production `master` must be performed; every divergence declared as accepted improvement, accepted correction, or explicit fork; then integration PR filed against `intelluric/tools/instant/pitch-deck/backend/`. Current state: production shipped `V2_ARCHETYPES` (10-entry) and `verification_worker` independently on 2026-07-15; workshop now has 12-entry Buffett…Disney roster; both sides have drifted and no diff has been performed.

### Runtime Readiness — 7 obligations

6. **`[health-check-endpoint]`** ❌ Open — `/health` returning server status, stage graph validity, DB connectivity, and model API reachability.

7. **`[authenticated-endpoints]`** ❌ Open — All routes require verified user identity; `POST /api/jobs` cannot be called anonymously.

8. **`[circuit-breaker]`** ❌ Open — `_responses_create()` needs a documented and enforced timeout/retry/circuit-break contract. A degraded model API during a long generation stage must not silently hang the job.

9. **`[per-user-rate-limiting]`** ❌ Open — Process-wide 50 images/minute must be supplemented by per-job or per-user limiting.

10. **`[env-configurable-models]`** ❌ Open — `model_gateway.py` migration must complete; all model selection routes through `gateway.model_for(...)`; hardcoded constants in `prompts.py` removed.

11. **`[secrets-management]`** ❌ Open — `OPENAI_API_KEY` and payment credentials from a secrets store, not the ambient env file.

12. **`[deployment-descriptor]`** ❌ Open — Dockerfile or equivalent; `requirements.txt` dependencies version-pinned; environment documented independently of `intelluric-site.env`.

### Data Layer — 1 obligation

13. **`[production-database]`** ❌ Open — SQLite replaced with or abstracted behind Postgres or equivalent; concurrent writes and connection pooling supported.

### Deliberately Deferred (not blocking)

- ⏸ **Per-slide A/B seed variants** — Run each image call as `n=2` with different seeds so user picks between two candidates per slide. Roughly doubles image spend for a quality-of-life win, not a correctness fix. Revisit after the 14 blocking obligations are closed.
- ⏸ **Intrinsic-delight founder archetype** — All 12 FOUNDER_ARCHETYPES win conditions are rhetorical proof strategies; none argues via pure product desirability/joy/collectibility. Coverage gap, not a blocking defect.
- ⏸ **Gallery frontend** — `GET /api/gallery/previews` endpoint exists; no client UI. Infrastructure in place; UX surface is not.

---

## Layer 06 · Outcome — What External Change Must Result

Production-ready is not satisfied by a successful deployment. The outcome layer requires that a real person completes the full cycle — paid, generated, verified, downloaded — and that the PostHog event chain proves it happened without operator assistance.

### Outcome Tokens

`[target-outcome]` `[qualifying-event]` `[realization-threshold]` `[benefit-owner]` `[results-chain]` `[completion-proof]` `[post-implementation-verification]`

### Outcome Specification

| Token | Definition |
|---|---|
| `[target-outcome]` | A real user completes the full production pipeline: submits a pitch → selects an approach → completes a real payment transaction → receives a verified slide deck package → downloads a deck used in an actual funding or sales context. |
| `[qualifying-event]` | First verified paying user reaches `verification: completed` in production. `human_payment` satisfied by a live Stripe charge receipt (not mock). `pitch_deck_package.zip` delivered to a real user session. User identity established via `/api/jobs/{id}/identify`. |
| `[realization-threshold]` | 3 distinct users complete the full cycle in production within 30 days of launch, with zero `operator_override` events in any of those jobs' `stage_history`. |
| `[benefit-owner]` | InTelluric product team; workshop owner. |

### Results Chain

1. Resolve 5 functional blocking obligations: cross-batch label uniqueness, design-archetype selection collapse, mock payment, dead-code ambiguity, and workshop↔production diff.
2. Complete runtime hardening: health check endpoint, authenticated endpoints, circuit breaker, per-user rate limiting, env-configurable models, secrets management, deployment descriptor, production database.
3. Perform fresh side-by-side diff of workshop `prompts.py`/`workers.py` vs. production `master`. Declare disposition of every divergence.
4. File integration PR against `intelluric/tools/instant/pitch-deck/backend/` with the reconciled 12-entry FOUNDER_ARCHETYPES, 6-axis Pitch Aspect system, and any other workshop improvements accepted for production.
5. Deploy to production environment with live Stripe integration and full secrets management.
6. Smoke test with `PS_DEBUG_MODE=1` (zero model spend) through a complete cycle, then a full live run with real model calls on a real pitch.
7. First paying user completes full cycle. PostHog event chain complete. Outcome token triggered.

### Completion Proof — Required PostHog Event Chain

```
approach_draft.completed
  → previews.completed
  → human_selection.completed
  → payment.stripe_charge_success      ← must carry real Stripe charge_id
  → generation.completed
  → verification.completed
  → export.zip_delivered
```

All events carry a real user identity (established via `/api/jobs/{id}/identify`). Zero `operator_override` events in that job's `stage_history`. Stripe `charge_id` present in the payment event.

### Post-Implementation Verification (30 Days Post-Launch)

- PostHog funnel from `pitch_intake` to `export.zip_delivered` shows 3+ completions with no operator-override events in any of those job histories
- All `human_payment` events in production carry real Stripe `charge_id` values — zero mock-payment events present
- Zero jobs stuck in `generation: in_progress` beyond configured circuit-breaker timeout
- `_audit_pipeline()` passes on every production server boot (verifiable in deployment logs)
- Model API spend traceable per-job via `telemetry/` JSONL logs

---

## Acceptance Manifest

The 14 conditions that must be simultaneously true for the `[production-ready]` boundary state to be crossed. Any single unchecked item returns the system to workshop-only status.

### Functional — 5 items

| # | Token | Condition |
|---|---|---|
| 01 | `[live-payment-integration]` | `human_payment` gate satisfied only by verified Stripe charge receipt; mock endpoint removed or blocked in production |
| 02 | `[cross-batch-label-uniqueness]` | Approach labels unique across all generation attempts for the same job, not just within a single 4-approach call |
| 03 | `[dead-code-resolved]` | Stages 02/03/05 (`pitch_quality_gate`, `pitch_elaborator`, `approach_regeneration`) either registered in DAG with real wiring, or removed from `workers.py` |
| 04 | `[design-archetype-collapse-fixed]` | DESIGN_ARCHETYPES 4-of-6 selection produces genuine variance across all 6 designers; structural fix (axes derived from product spec), not prompt hygiene |
| 05 | `[integration-pr-filed]` | Fresh workshop-vs-production diff performed; every divergence declared; PR filed against `intelluric/tools/instant/pitch-deck/backend/` |

### Runtime & Security — 7 items

| # | Token | Condition |
|---|---|---|
| 06 | `[health-check-endpoint]` | `/health` returns server status, stage graph validity, DB connectivity, model API reachability |
| 07 | `[authenticated-endpoints]` | All routes require verified user identity; `POST /api/jobs` cannot be called anonymously |
| 08 | `[circuit-breaker]` | `_responses_create()` enforces documented timeout/retry/circuit-break; degraded model API cannot hang a job silently |
| 09 | `[per-user-rate-limiting]` | Per-job or per-user image rate limiting supplements the process-wide 50/min limiter |
| 10 | `[env-configurable-models]` | `model_gateway.py` migration complete; all model selection routes through `gateway.model_for(...)`; hardcoded constants in `prompts.py` removed |
| 11 | `[secrets-management]` | `OPENAI_API_KEY` and payment credentials sourced from a secrets store, not the ambient env file |
| 12 | `[deployment-descriptor]` | Dockerfile or equivalent; `requirements.txt` dependencies version-pinned; environment documented independently of `intelluric-site.env` |

### Data Layer — 1 item

| # | Token | Condition |
|---|---|---|
| 13 | `[production-database]` | SQLite replaced with or abstracted behind Postgres or equivalent; concurrent writes and connection pooling supported |

### Outcome — 1 item

| # | Token | Condition |
|---|---|---|
| 14 | `[qualifying-event]` | First verified paying user completes `verification: completed` in production with a real Stripe charge; `pitch_deck_package.zip` delivered; PostHog event chain intact; zero `operator_override` events in that job's `stage_history` |
