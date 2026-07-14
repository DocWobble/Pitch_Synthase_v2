# Pitch Synthase — Prototype v2 Engineering Spec

| Field | Value |
|---|---|
| Document ID | PS-V2-SPEC |
| Version | 2.2.0 |
| Status | Workshop-Validated — Not Merged to Production |
| Owner | Workshop (isolated) |
| Workshop path | `/home/director/pitch_synthase_archetype_workshop` |
| Last Updated | 2026-07-14 |

### Change history

| Version | Summary |
|---|---|
| 1.0.0 | Original spec (designed-HTML artifact, rev. 1) |
| 1.1.0 | Corrections verified against OpenAI docs (rev. 2) |
| 2.0.0 | Full rewrite to plain Markdown — export-oriented docs use Markdown/monospace, not designed HTML (rev. 3) |
| 2.1.0 | Parallel verification pipeline replaces the single-batch finalization refiner; slide-count ceiling corrected 4–24; image rate limit corrected to 20/min; ambiguity-gate / evidence-standard / confidence-gate mechanisms documented; testing harness section added (rev. 4) |
| 2.2.0 | Selective non-canon exceptions (`excepted_inference_elements` / `authorized_inferences` / `canon_overrides`) and the per-element inferred-content removal checkbox (`inferred_element_decisions`) documented; pre-finalization hard-block gate added; judge `corrective_constraints` normalized to always be a string after a live JSON-array regression was caught and fixed (rev. 5) |

> This spec documents the isolated workshop prototype only. Nothing described here has been merged into the real backend at `intelluric/tools/instant/pitch-deck/backend/` — that code has been read for reference and copied, never edited. Every mechanism below has been run against real model calls at least once; results are cited inline where they came from a specific test. If a mechanism is later re-validated further, it becomes a *proposal* for new production canon, not a silent replacement of it.

## Contents

00. [Pipeline overview](#00-pipeline-overview)
01. [Stage-by-stage operation](#01-stage-by-stage-operation)
02. [Job data model](#02-job-data-model)
03. [Status state machine](#03-status-state-machine)
04. [Founder archetype catalog](#04-founder-archetype-catalog)
05. [Approach generation contract](#05-approach-generation-contract)
06. [Visual grammar contract](#06-visual-grammar-contract)
07. [Artifact modes & image generation contract](#07-artifact-modes--image-generation-contract)
08. [Finalization & export](#08-finalization--export)
09. [Dependencies](#09-dependencies)
10. [Validated findings](#10-validated-findings)
11. [Known staleness & open items](#11-known-staleness--open-items)
12. [Testing harness](#12-testing-harness)

---

## 00. Pipeline overview

Prototype v2 replaces the template-first funnel with a **full-pitch-first** design: the complete pitch is collected before any generation happens, four cheap strategic approaches are drafted in text only, one image is spent per approach as a single-slide preview, and only after the user picks an approach does the pipeline spend on a full paid deck.

| # | Stage | What happens |
|---|---|---|
| 01 | Pitch intake | Audience, elevator pitch (problem/solution), what it should convey, optional archetype pin, optional supporting document/image. |
| 02 | Pitch quality gate | Cheap text-only check: is there enough real content here to work with, or is the pitch too thin. |
| 03 | Pitch elaborator | Optional "write my whole pitch" button — ghostwrites a draft, never saved without explicit user approval. |
| 04 | Approach drafting | One text call → four distinct strategic approaches under one founder archetype (fixed or auto-picked). |
| 05 | Approach regeneration | Optional — regenerate only the flagged approach(es), holding the rest fixed. |
| 06 | Single-slide preview | One text + one image call per approach the user wants to see rendered. |
| 07 | Preview checkout | Paywall gate before full deck spend (production concept; not built as a gate in the workshop). |
| 08 | Full deck generation | Anchor Writer → Deck Builder storyboard (with one shared visual grammar) → per-slide + hero image renders. |
| 09 | Finalization | Parallel per-slide verification pipeline: 3 independent verifiers → judge → conditional regeneration, universal + per-slide instructions, silent quality bar always applied. |
| 10 | Export | Zip containing per-slide PNGs, PDF, PPTX, standalone HTML, and Markdown. |

---

## 01. Stage-by-stage operation

### Pitch intake

A job is created with `db.create_job(problem_need, audience, association_words)` and then updated with the full-pitch-first fields: `elevator_pitch`, `conveys`, optional `doc_text`, optional `selected_archetype_id`. No generation happens at this stage — it only persists what the user typed.

### Pitch quality gate — `pitch_quality_gate_worker`

A single cheap text call (`prompts.pitch_quality_gate_prompt`) asks the model whether the elevator pitch has enough real content to ground four distinct approaches, returning `{"is_real_pitch", "reason", "missing_elements"}`. This call mutates no job state beyond telemetry — the caller decides whether to block progress or offer the elaborator.

### Pitch elaborator — `pitch_elaborator_worker`

The "write my whole pitch" button. Ghostwrites a complete draft from whatever rough input currently sits in `elevator_pitch`, returning `{"is_nonsense", "elevator_pitch", "invented_elements", "conveys_suggestion", "reason_for_placeholder"}`. Critically, this worker **does not overwrite** `job.elevator_pitch` — the draft is shown to the user, who explicitly saves it or discards it. If the rough input is genuine nonsense, the elaborator writes a self-aware placeholder pitch about the meta-failure itself, not a generic unrelated joke.

### Approach drafting ("template generation") — `approach_drafter_worker`

One text call, grounded in the real elevator pitch (and optional document), returns exactly four strategic pitch approaches under a single founder archetype — fixed by the user, or auto-picked by the drafter from the catalog if none was specified. No images are generated at this stage.

Output is validated by `_validate_approach_manifest`: exactly 4 approaches, IDs `approach_1`–`approach_4` in order, unique labels, all of `pitch_angle`/`key_differentiator`/`visual_direction` non-empty.

| Allowed from | Claims | On success | On failure |
|---|---|---|---|
| `created`, `approaches_queued`, `approaches_failed` | `approaches_running` | `approaches_ready` | `approaches_failed` |

### Approach regeneration — `approach_regenerate_worker`

Regenerates only the flagged `approach_id`(s), given the kept approaches as context so the replacement doesn't collide with what the user already liked on label, claim order, or visual doctrine.

### Single-slide preview — `single_slide_preview_worker`

Merges the roles of the old Anchor Writer and Template Drafter into one path per approach: one text call (`single_slide_drafter_prompt`) writes an `image_prompt` grounded in the real pitch and the chosen approach, then one image call renders it. In the workshop this intentionally runs **before** the paywall, for comparison purposes — in production this stage sits after preview checkout.

### Full deck generation — `generation_worker`

Claims status `paid` → `generating_plan`. Three sub-phases:

1. **Anchor Writer** (`anchor_writer_prompt`, mode `PAID`) writes the fictional "successful pitch" narrative frame that everything downstream reads from — the sole fiction layer; it never writes slide copy, a storyboard, or per-slide prompts directly.
2. **Deck Builder storyboard** (`paid_deck_builder_prompt`) — one text call that plans all *N* slides and returns two top-level keys: `visual_grammar` (one structured object, chosen once) and `storyboard` (per-slide title/purpose/style_tags/image_prompt). Validated for exact slide count and sequential `slide_number`s before proceeding.
3. **Rendering** — status moves to `rendering_slides`; the hero slide and every numbered slide render concurrently (bounded to 4 at a time via `_chunked_gather`), each slide's `image_prompt` passed through `paid_slide_image_prompt` with the shared `visual_grammar` spliced in by code. A slide gets one retry on failure; the job only fails outright if more than 2 slides fail after retry.

### Finalization — `verification_worker` (current mechanism, rev. 4)

Claims `review_received`/`finalization_queued`/`awaiting_review` → `finalizing`. Replaces the original single-batch `finalization_worker` mechanism (still present in the codebase, unused by current test scripts, kept for A/B reference — see §08 for why it was replaced). Per slide, fully independently and in parallel across all slides:

1. **Fan-out to 3 narrow verifiers** (`_run_slide_verifiers`) — spelling, source-accuracy, diagram-accuracy — each a single-purpose multimodal text call seeing only that slide's proof image plus its own relevant context (storyboard entry; source material for the source-accuracy verifier only).
2. **Fan-in** — the three verifiers' issue lists are concatenated in code (`_judge_slide`'s caller), no model call spent combining them.
3. **Judge/disposition call** (`_judge_slide`) — decides `pass` or `regenerate` by reading only the structured `actionable` boolean already set by each verifier, never by parsing free text.
4. **Conditional regeneration** — only for slides the judge flags. A fresh text-to-image call (never I2I), built by splicing the corrections onto the *original, verbatim* `image_prompt` plus `visual_grammar`, wrapped through the same `_deck_drafter_input`/`_anchor_payload` context as the original generation (deck-wide narrative + `user_inputs`) — see §08 for why that wrapping is required.

A slide that passes ships its existing clean proof unchanged — no new image call at all. Composited output feeds the same four export builders as before, plus a new `verification_report.json` acceptance-manifest artifact, zipped, and the job reaches `complete`.

---

## 02. Job data model

One SQLite table, `jobs`, holds the entire lifecycle. Fields below are grouped by the stage that owns them; JSON-typed columns are serialized on write and parsed back on read via `db._JSON_FIELDS`.

| Field | Type | Owner stage | Purpose |
|---|---|---|---|
| `id, status, created_at, updated_at` | text | lifecycle | Primary key `pd_<hex16>` and the state-machine status field (§03). |
| `audience, elevator_pitch, conveys, doc_text` | text | pitch intake | The full-pitch-first inputs. `conveys` replaces the old free-text "vibes" association words for this path. |
| `problem_need, association_words` | text / json | legacy intake | Original production intake shape, still written by `create_job` for compatibility; unused by the full-pitch-first flow. |
| `selected_archetype_id, selected_archetype_label` | text | approach drafting | Either user-pinned before drafting, or written back by the drafter after an auto-pick. |
| `approach_candidates_json` | json | approach drafting | The four `{approach_id, label, pitch_angle, key_differentiator, visual_direction}` objects. |
| `candidates_json, archetypes_json` | json | legacy template stage | Original production template-candidate shape; not written by the full-pitch-first path. |
| `explicit_slide_count` | int | deck generation | User-chosen slide count, 4–24 inclusive (`MIN/MAX_SELF_SERVE_SLIDE_COUNT`) — raised from the original 4–10, which assumed rendering was a single `n=10` image call rather than N independent per-slide calls. |
| `deck_proof_plan, slide_specs, expected_text_map` | json | deck generation | Plan snapshot and per-slide specs written once the storyboard is accepted, ahead of rendering. |
| `reviewed_slides, universal_refinement_instruction` | json / text | finalization | Per-slide user edit requests, plus one optional deck-wide instruction applied on top of every slide. |
| `quality_results, convert_keep_candidate_ids` | json | legacy / convert path | Carried over from production; convert path lets a user keep specific preview vertex images as the paid deck's style reference. |
| `export_zip_path` | text | export | Path to the final deliverable zip once status reaches `complete`. |
| `*_payment_intent_id, *_checkout_*, *_paid_at` | text / int | checkout | Preview and deck checkout/payment bookkeeping, mirrored from production; not exercised by workshop test scripts. |
| `telemetry_context_json, error_message` | json / text | all stages | Structured context for the telemetry log, and the last fatal error if `status = failed`. |

---

## 03. Status state machine

Every worker that mutates job state does so through `db.claim_job_status(job_id, allowed_from, next_status)` — an atomic compare-and-set against the current status. A second concurrent call against the same job simply finds no matching row and no-ops; this is how duplicate task dispatch is made safe without a separate lock.

| Status | Meaning | Set by |
|---|---|---|
| `created` | Job exists, pitch fields populated, nothing generated yet. | intake |
| `approaches_running` → `approaches_ready` / `approaches_failed` | Template-generation stage in flight, then resolved. | `approach_drafter_worker` |
| `paid` → `generating_plan` → `rendering_slides` → `plan_ready` | Full deck generation: anchor + storyboard, then concurrent rendering. | `generation_worker` |
| `review_received` / `finalization_queued` / `awaiting_review` → `finalizing` → `complete` | User has reviewed proofs and requested finalization; export builds run; deliverable zip is ready. | `verification_worker` (current) or `finalization_worker` (superseded, kept for A/B reference) |
| `failed` | Terminal — `error_message` holds the cause. Reachable from any stage. | any worker |

Legacy statuses (`template_generation_running`, `template_prompt_running`, `preview_generation_started`, `candidate_ready`, `awaiting_review`) belong to the original production template-first flow and the convert path, both still present in the workshop copy for reference but not exercised by the full-pitch-first test scripts.

---

## 04. Founder archetype catalog

`prompts.EXPERIMENT_FOUNDER_ARCHETYPES` — ten entries, each a fixed `{archetype_id, label, role, posture, win_condition}` object. Every approach in a batch shares exactly one archetype, fixed by the user at intake or auto-picked by the drafter from this list. This is the current catalog, replacing the original eight-entry set (Firebringer, Trickster, Architect, Commander, Oracle, Alchemist, Healer, Steward) after validation showed the new set produces more distinct rhetorical fingerprints per archetype.

| Archetype | Role | Posture | Win condition |
|---|---|---|---|
| Odysseus | The Tactical Gap-Finder | Scrappy, clever, opportunistic, insight-driven. | Finds the blind spot or back door incumbents are too rigid to see, and gets there first through cunning, non-linear navigation. |
| Caesar | The Scale Architect | Authoritative, systematic, disciplined, expansionist. | Operational dominance — superior execution and organized rapid expansion — outproduces less disciplined competitors. |
| Da Vinci | The Paradigm Shifter | Visionary, experimental, synthesis-oriented, pioneering. | Combines two previously unrelated fields into an entirely new category. |
| Athena | The Strategic Rationalist | Analytical, poised, risk-mitigated, structural. | Structural superiority — calculated risk, rigorous logic, a fortress-like moat — outlasts speed or charisma alone. |
| Newton | The Efficiency Optimizer | Precise, data-backed, optimized, corrective. | Takes an existing process and makes it an order of magnitude faster or cheaper via provable understanding of its governing laws. |
| Cicero | The Market Evangelist | Persuasive, charismatic, narrative-heavy, outward-facing. | Captures the market's mindshare and builds a movement that makes the product feel inevitable. |
| Copernicus | The Contrarian Pivot | Provocative, challenging, intellectual, disruptive. | Proves the industry's core assumption is wrong and relocates the value proposition around that inversion. |
| Gutenberg | The Democratizer | Inclusive, scaling-focused, empowering, transformative. | Takes a high-value service reserved for the few and makes it radically accessible via a technological lever. |
| Alexander | The Aggressive Captor | High-energy, assertive, velocity-obsessed, dominant. | Sheer velocity and aggression achieve market dominance before competitors or incumbents can react. |
| Michelangelo | The Product Perfectionist | Detail-oriented, aesthetic, uncompromising, quality-centric. | The product is so qualitatively superior that users switch on the strength of the product alone. |

---

## 05. Approach generation contract

Every archetype produces the same output shape. This is what `approach_drafter_worker` validates before writing `approach_candidates_json`:

```json
{
  "archetype": {"archetype_id": "arch_odysseus", "label": "Odysseus", "role": "The Tactical Gap-Finder", "posture": "...", "win_condition": "..."},
  "approaches": [
    {
      "approach_id": "approach_1",
      "label": "...",               // unique 2-3 word label, not a paraphrase of the archetype
      "pitch_angle": "...",         // which claim leads, and why it wins for this audience
      "key_differentiator": "...",  // what distinguishes this from the other three
      "visual_direction": "..."     // artifact tradition, typography, material language, composition
    }
    // x4, approach_1 - approach_4, in order
  ]
}
```

All four approaches must stay strictly faithful to the real mechanism in the elevator pitch — they may differ in emphasis, evidence posture, and visual doctrine, never in the underlying facts. `_validate_approach_manifest` enforces exact count, sequential IDs, non-empty required fields, and label uniqueness *within the batch* (see §11 for the cross-batch gap).

---

## 06. Visual grammar contract

The single largest structural fix made to the Deck Builder this cycle. Every paid deck needs one coherent visual system across independently-generated slide images — a shared palette, typography, and diagram language. The naive approach asks the model to describe that system in prose and repeat it verbatim in every per-slide prompt; nothing enforces that repetition actually stays identical across separate model calls.

**Before — prose contract:** One `"storyboard"` key. Each `image_prompt` is asked to restate a shared "visual-grammar paragraph" identically. Consistency depends on the model choosing to repeat itself faithfully, call after call.

**Now — structured splice:** Two top-level keys: `visual_grammar` (chosen once) and `storyboard`. The object is rendered once by `format_visual_grammar_block` and spliced byte-identical into every slide's prompt by code — never re-typed by the model.

### The eight fields

| Field | Governs |
|---|---|
| `background` | Ground treatment — may be a gradient, texture, or imagery, not just a flat hex. |
| `foreground` | Primary text and line color. |
| `accent` | The single signal color and what it's reserved for. |
| `typography` | Headline/body/label face register. |
| `diagram_style` | How diagrams and shapes are drawn. |
| `panel_style` | Callout/panel treatment — borders, corners, shadow. |
| `grid_system` | Column structure and margins. |
| `line_weight` | Stroke weights and what each weight is used for. |

Values are deliberately **not** locked to hex codes — the model can describe a gradient or textured background — but every field must be filled with something precise enough to be repeatable. This loosening followed direct feedback that fixed hex values over-constrain a system that might legitimately want a background image or gradient; the fields stay mandatory, the values don't.

### Validated by direct A/B test

A same-pitch, same-archetype, 4-slide comparison (THGM / Firebringer, "Ground as Antenna") ran both mechanisms side by side. Neither deck broke visibly on that run — the naive condition happened to stay internally consistent (one dual-hue amber/red scheme, applied the same way across all 4 slides). But that consistency was emergent, not guaranteed: nothing in the naive prompt fixes accent count or polarity, so a looser or more ambiguous input could still reproduce the color-inversion bug that motivated this fix in the first place (see §10). The structured condition's consistency is structural — identical spliced block, every slide, by construction.

---

## 07. Artifact modes & image generation contract

| Setting | Value |
|---|---|
| Model | `gpt-image-2` |
| Size | `2048x1152` (exact 16:9) |
| Quality / format / background | medium / png / opaque |
| Call path | Responses API `image_generation` tool (`prompts.image_generation_tool`) |
| Rate limit | 20 calls per rolling 60-second window (raised from 5 after an account recharge; text-call limits are in the hundreds of thousands/minute and are not a bottleneck) — process-wide, shared across concurrent jobs, enforced by `_throttle_image_generation()` ahead of every image call |
| Batch concurrency | Bounded to 4 simultaneous coroutines via `_chunked_gather` for image generation; verification-pipeline verifier/judge calls (text-only, no rate limit) are bounded to 6 concurrent slides at a time as an ordinary connection-limit safeguard, not a cost control |

Text-model calls (Anchor Writer, Deck Builder, Approach Drafter, all verifiers, the judge) are hardcoded to the full `gpt-5.4` identifier in this workshop's `prompts.py`, ignoring any `-mini` variant the shared production env may configure — smaller-context mini variants carry a higher hallucination/incoherence risk across longer source material, which is exactly what this workshop tests against.

### preview vs. paid

**Preview** artifacts request a photographed slide from a live presentation — camera perspective, screen feel, presentation atmosphere are allowed, but no presenter notes, no presenter-view UI, no page number.

**Paid** artifacts request a clean, full-bleed digital screenshot filling the frame edge-to-edge — no application chrome, browser UI, presenter-view panel, speaker-note sidebar, or navigation control of any kind, and explicitly no photographed/projected framing.

> **Why paid mode has no chrome concept at all:** The first fix for a pagination bug ("1 of 20" baked into a slide image) kept asking for a "presenter-view screenshot" while separately instructing "no pagination" — a self-contradictory instruction, since presenter view inherently implies a page counter. The actual fix removed the presenter-view/chrome framing from paid mode entirely: slides are full-bleed exports with speaker notes delivered as separate text, never composited into the image.

---

## 08. Finalization & export

### Why the single-batch refiner was replaced

The original mechanism (`finalization_worker`, still in the codebase for reference) sent every proof slide to one batch call (`finalize_refiner_prompt`) that returned one JSON blob covering all *N* slides at once, then ran each slide's I2I extraction independently. Two structural problems: a JSON parse failure on that one big blob zeroed out extraction prompts for the *entire* deck (observed multiple times), and any single slide's extraction outcome (including "nothing to fix, already clean") could hard-fail the whole job (observed live: a clean slide and the clean hero both failed a decode-and-strip step against nothing to strip).

### The parallel verification pipeline (`verification_worker`, current)

Per slide, independently:

- **Spelling verifier**, **source-accuracy verifier**, **diagram-accuracy verifier** — three narrow, single-purpose calls, fanned out in parallel. Each returns `{"verifier", "issues": [{"location", "problem", "correction", "actionable": true|false}]}`.
- The source-accuracy verifier is gated by an **ambiguity-gate**: it does not fire on mere non-verbatim wording, only on a **materiality test** — would this element, if untrue, produce a genuinely divergent claim, not just different phrasing of the same fact. Governed by an explicit **evidence-standard**: a contradicted number/name/proof-point clears the gate and is `"actionable": true`; a paraphrase, category label, or reasonable elaboration does not clear the gate at all. (Early version without this gate flagged 13–161 false-positive "issues" per slide on content that was actually correct; the gated version collapsed that to 0–6 per slide, mostly non-actionable, while still catching real defects, including one genuine factual contradiction the ungated version had never surfaced.)
- **Judge/disposition call** — a **confidence-gate**: reads only the `actionable`-true issues (already gated upstream, not re-litigated), weighs the cost of one more image generation against the evidentiary weight of what was found. Zero actionable issues → `pass`, ship the existing proof unchanged. One or more → `regenerate`.
- On `regenerate`, the judge writes **only** `corrective_constraints` — the fixes, not a full prompt. The caller splices those constraints onto the *original, verbatim* `image_prompt` plus `visual_grammar` (the same code-level splice discipline as §06), rather than asking the judge to reproduce composition from memory. (An earlier version asked the judge to write a complete replacement prompt; it correctly avoided the reported defects but silently reorganized the slide's layout — paraphrase is not preservation.)
- The regeneration call is wrapped through the same `_deck_drafter_input`/`_anchor_payload(anchor_json, ...)` context the *original* per-slide generation used — carrying `deck_generation_prompt` and `user_inputs`, not just the bare slide text. Without this, a regeneration reasons about one slide's text in total isolation and can invent a different product concept from an underspecified line (observed: a console-style screenshot line, stripped of deck-wide narrative context, rendered as an unrelated GPS-navigation app instead of the AI companion console every other slide depicts — same literal prompt text, different sampled subject matter).

### Refinement instructions (unchanged from rev. 3)

- **Universal instruction** — `job.universal_refinement_instruction`, an optional deck-wide instruction adapted per slide, not pasted verbatim.
- **Silent quality bar** — `"immaculate spelling, precise professional typography, and production-grade visual polish"`, appended regardless of what was requested.

Page numbers are an explicit exception to "preserve legitimate content on the slide" — if a proof slide has one, it's treated as an upstream defect and removed, not preserved as intentional design.

### Selective non-canon exceptions (`excepted_inference_elements` / `authorized_inferences` / `canon_overrides`)

A job-level field, `excepted_inference_elements` (starting set: `["pricing"]`), lets the user authorize the model to *infer* a specific element from latent training knowledge when the source material is silent on it, rather than leaving the deck structurally incomplete. This is **not** a blanket exemption from fact-checking: the deck builder decides the inferred value exactly once per deck (`authorized_inferences`, a structured value sibling to `visual_grammar`, spliced verbatim into every slide that touches it), and every slide referencing it must stay internally consistent with that one decided value — inconsistency is still an actionable finding. Direct user edits made during refinement (`reviewed_slides`, aggregated deck-wide as `canon_overrides`) work the same way in reverse: they become canon and every slide touching the same fact must match them, not just the one edited slide. Neither mechanism relaxes spelling, diagram, or internal-consistency checks — they only change *which* value counts as ground truth for that one element.

### Per-element inferred-content removal checkbox (`inferred_element_decisions`)

A job-level `dict[str, bool]`, keyed by element name (e.g. `{"pricing": true}`), presented as a checkbox right before the refinement stage for any element also present in `excepted_inference_elements`. Three outcomes, in order of how the pipeline actually reaches them:

- **Unchecked / absent (default)** — the inferred content is kept, values unchanged. The verifier still checks it against the decided `authorized_inferences` value as usual, but `_verify_and_finalize_slide` forces a standing relabel edit wherever the decided value actually appears on the slide: `"inferred <element>"` / `"not a source-verified fact"` wording softens to `"proposed <element>"` — content stays, only the disclosure wording changes.
- **Checked, zero edits specified** — hard-blocked before any verifier/judge/image call runs. `_gate_check_inferred_element_decisions` (in `workers.py`, called at the top of `verification_worker`) finds every slide whose content actually carries the checked element's decided value and requires a real, non-blank `reviewed_slides` entry on each one; if any is missing, the job fails immediately with `"Blocked: Element '<element>' is checked for removal but slide <N> has no replacement specified..."`. Confirmed live: fires in 0.01s, zero model-call spend.
- **Checked, with a real edit specified** — the element's decided value is withheld from that slide's verifier call, so the user's edit (`reviewed_slides` / `canon_overrides`) is the only thing that can settle the claim. Two sub-outcomes, both handled by the *existing* verifier/judge machinery (no new verdict type):
  - The edit actually covers the element → verifier flags the mismatch against the override as an ordinary actionable correction, judge regenerates, **and** `_verify_and_finalize_slide` additionally strips the "inferred"/"proposed"/disclosure banner language entirely (not softened, removed) — the user specified this value directly, so it ships as a plain, unqualified fact. Confirmed live (job `pd_ed26cfd043e14b31`, slide 7): old tiered pricing fully replaced with the flat per-seat override, disclosure banner gone, no leftover figures.
  - The edit doesn't actually address the element → the verifier's existing "no decided value to check against" rule fires (`actionable: false, authorized_inference: true, element: <name>`) exactly as it would for any other unverifiable claim; `_verify_and_finalize_slide` detects this specific combination and returns a `"drop"` verdict instead of calling the judge. The slide is omitted from the composited deliverables (PDF/PPTX/HTML/MD) via the existing `dropped_slides` mechanism, with the reason recorded in `verification_report.json`; the raw proof image is untouched in the archive — nothing is deleted, only the concatenated exports skip it.

Live regression caught and fixed while validating this: the judge's `corrective_constraints` prompt wording ("a short, explicit list of the concrete fixes") let the model return a JSON array instead of a string, crashing the `.strip()` merge logic. Fixed both ways — prompt now says "ONE STRING (not a JSON array)", and `_verify_and_finalize_slide` normalizes whatever shape comes back immediately after the judge call, before either merge block touches it.

### Acceptance manifest

`verification_report.json` — per slide: which verifiers ran, every issue found (verifier, location, problem, correction, actionable), the judge's verdict and reasoning, and whether the shipped final image is the original proof or a regeneration. Traceable to a specific verifier and slide, unlike the old mechanism's one opaque batch decision.

### Export builders (unchanged)

Once every slide is finalized, four formats are built from the same composited slide set and zipped together: `_build_pdf` (reportlab), `_build_pptx` (python-pptx), `_build_html` (standalone, self-contained), `_build_markdown`. The zip is validated by `_validate_export_manifest` before the job is marked `complete`.

---

## 09. Dependencies

| Dependency | Version | Used for |
|---|---|---|
| `openai` | 2.43.0 | Responses API — all text and image generation calls |
| `gpt-5.4` | model | Anchor Writer, Deck Builder, Template/Approach Drafter text calls |
| `gpt-image-2` | model | All slide and hero image rendering |
| `reportlab` | 5.0.0 | PDF export |
| `python-pptx` | 1.0.2 | PPTX export |
| `pillow` | 12.2.0 | Image compositing ahead of PDF export |
| `sqlite3` | stdlib | Job store (`db.py`) — single-file WAL-mode database |
| `python-docx` | ad hoc | Used once outside the app to extract text from an uploaded `.docx` test fixture; not a standing runtime dependency of `workers.py` |

Every model call runs through an OpenAI API key loaded read-only from `intelluric-site.env` at process start; nothing hardcodes or logs it.

---

## 10. Validated findings

- **Mechanism-fidelity hallucination.** Thin input causes confident mechanism hallucination regardless of whether archetype selection happens before or after template generation. Restructuring stage order alone does not fix this — mitigated (not solved) by the pitch quality gate and elaborator.

- **Candidate divergence tracks input density.** How different four candidates end up looking depends on how much real content is in the input, not on which archetype mechanism generated them.

- **Pagination / chrome bug — fixed.** Presenter-view framing inherently implies a page counter; the self-contradiction was structural, not a prompt-wording problem. Fixed by removing the chrome concept entirely from paid mode (§07).

- **Cross-slide visual-grammar inconsistency — fixed.** Prose repetition across independent model calls is not guaranteed byte-identical. Fixed via the structured `visual_grammar` object + code-level splice (§06), confirmed by direct A/B test.

- **Archetype label leak — fixed.** `_contains_casefold` originally matched "Architect" inside "architecture" as a false-positive leak. Fixed with a word-boundary regex.

- **Archetype differentiation is real but uneven.** Running all ten archetypes against one pitch produced genuinely distinct fingerprints where a facet has an obvious archetype-specific angle (Alexander's conquest framing, Gutenberg's "who gets empowered" angle). On facets with no obvious archetype-specific angle, multiple archetypes converge toward similar labels and framing — observed both at small scale (2 archetypes) and at full 10-archetype scale on a single pitch.

- **Slide-count ceiling was never a technical limit — fixed.** Raised from 10 to 24. A real 16-slide + hero deck rendered with zero failures and zero retries in ~8 minutes wall-clock (19 model calls total), confirming sub-linear scaling: every slide is its own independent per-slide call regardless of *N*, throttled by a shared rate limiter, not by slide count.

- **Batch refiner JSON fragility — fixed by architecture change, not a patch.** The single-batch finalization mechanism's one-big-JSON-blob failure mode (a parse error zeroing out the whole deck's extraction prompts) is structurally eliminated by the parallel verification pipeline (§08) — many small independent calls instead of one large one, each independently retriable.

- **Finalization hard-failing on already-clean slides — fixed by the same architecture change.** The old mechanism's "any single slide's outcome can fail the whole deck" behavior no longer exists: a passing slide ships its proof unchanged, a regenerating slide is isolated to its own call, and nothing about one slide's outcome affects any other slide.

- **Source-accuracy verification needed a materiality filter, not just source text — fixed.** An ungated verifier treated "not a verbatim quote" as equivalent to "contradicts the source," producing 13–161 false-positive issues per slide on content that was actually fine. Adding an explicit ambiguity-gate/evidence-standard (only flag genuine divergent-consequence claims) collapsed false positives to near-zero while preserving detection of real defects, including one genuine factual contradiction (a UI screenshot's displayed state contradicting the source's stated event log) the ungated version never caught.

- **Full regeneration requires the same anchor context as original generation — fixed.** A regeneration call given only the corrected per-slide prompt, without the deck-wide `deck_generation_prompt`/`user_inputs` context the original generation call included, can independently sample a different product concept from the same underspecified line. Wrapping the regeneration call through the identical `_deck_drafter_input`/`_anchor_payload` context as the original fixed this.

---

## 11. Known staleness & open items

- **Stale docstrings/comments.** `paid_deck_builder_prompt`'s docstring still says "presenter-view screenshot" though the function body no longer requests one. The old `finalization_worker`'s comments still frame chrome-stripping as the primary job of the I2I extraction pass; that function is superseded by `verification_worker` (§08) but left in the codebase for A/B reference, so its stale comments remain, scoped to dead-for-current-purposes code. Cosmetic; no behavioral impact.

- **Label uniqueness only enforced within a batch.** `_validate_approach_manifest` checks label uniqueness inside one 4-approach call, not across regenerations or across separate archetype runs. Duplicate labels ("Deterministic Chain," "Behavioral Truth," "Proof Cascade") have recurred across independent runs on the same pitch.

- **Full regeneration doesn't guarantee pixel-level continuity.** Even with the anchor-context fix and verbatim-prompt splice, a fresh text-to-image regeneration is not an I2I edit — it can still shift exact composition details (panel arrangement, spacing) that an I2I patch would have held fixed, in exchange for speed and avoiding I2I's own fidelity loss on edits. Observed directly: a regenerated slide preserved theme, content, and visual grammar correctly but reorganized which UI panels appeared where, relative to its own proof. Accepted trade-off, not a bug, but worth knowing before assuming regenerated slides are visually identical to their proofs.

- **Hero slide has no source-accuracy check.** The hero's fixed prompt template carries less specific factual text than a numbered content slide, so the risk is lower, but this is an asymmetry in the current pipeline, not a deliberate design decision.

- **Open business decisions, not engineering tasks.** Where the paywall sits relative to the four-approach / single-slide-preview stages, and whether a "reroll" loop is offered at all, are monetization and product decisions this workshop surfaces but does not resolve.

---

## 12. Testing harness

`ps_test_input.schema.json` (workshop root) — a JSON Schema mirroring every real intake field 1:1 (`audience`, `elevator_pitch`, `conveys`, `doc_text`, `archetype_id` with the live 10-value enum, `slide_count` bounded 4–24, plus a `target_stage` selector: `approaches` / `single_slide_previews` / `full_deck` / `finalized`). A filled instance can be handed off directly and run without re-deriving intent from looser notes. Companion usage notes and a worked example: `PS_TEST_INPUT_USAGE.md`.

---
*Pitch Synthase — Prototype v2 · isolated workshop · rev. 4*
