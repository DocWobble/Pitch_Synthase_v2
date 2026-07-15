# Pitch Synthase Workshop — Project Manifest

| Field | Value |
|---|---|
| Document ID | PS-V2-MANIFEST |
| Version | 1.0 (living — see note) |
| Status | Living — Updated Continuously, Not a Point-in-Time Release |
| Owner | Workshop (isolated) |
| Last Updated | 2026-07-15 |

The single place to check "what's actually true right now" about this workshop. Other docs describe the system as designed (`PITCH_SYNTHASE_V2_SPEC.md`, PS-V2-SPEC) or exactly how it mechanically works (`PROMPT_CHAIN_TECHNICAL_REPORT.txt`, PS-V2-PROMPTCHAIN). This file tracks *status*: what's fixed, what's validated, what's still open, and what's been deliberately deferred. Update this whenever something in that status changes — don't let findings live only in chat history. Unlike the spec (versioned releases with a change history), this document does not carry a change log — it is pruned and rewritten in place, so "Last Updated" is the only temporal marker that matters.

**Workshop path:** `/home/director/pitch_synthase_archetype_workshop`
**Workshop repo:** `github.com/DocWobble/Pitch_Synthase_v2` (private, `main`) — local-only, no integration PR against production yet, see below.
**Real backend (read-only reference, never edited):** `/home/director/intelluric/tools/instant/pitch-deck/backend/`

---

## Status at a glance

| Item | Status | Notes |
|---|---|---|
| Full-pitch-first pipeline (10 stages) | ✅ Working | Validated end-to-end multiple times |
| 10-archetype catalog (Odysseus...Michelangelo) | ✅ Working | Replaced original 8-entry set |
| Visual grammar structured splice | ✅ Fixed & validated | A/B tested directly against old prose-contract mechanism |
| Pagination/chrome removal (paid mode) | ✅ Fixed & validated | Root-caused, chrome concept removed entirely |
| Slide-count cap (was 10, now 24) | ✅ Fixed & validated | 16-slide real deck ran clean, ~8 min wall-clock |
| Model config (mini → full gpt-5.4) | ✅ Fixed | Hardcoded in workshop `prompts.py`, ignores shared env |
| Parallel verification pipeline (`verification_worker`) | ✅ Fixed & validated | Replaces `finalization_worker` (kept in codebase for A/B reference only, not current). Fan-out 3 narrow verifiers + judge, per slide. See spec §08 |
| Selective non-canon inference exceptions (`excepted_inference_elements` / `authorized_inferences` / `canon_overrides`) | ✅ Working | Pricing tested live: deck builder decides once, verifier checks consistency not blanket-exempts, user edits become deck-wide canon |
| Inferred-element removal checkbox (`inferred_element_decisions`) | ✅ Fixed & validated | All 4 paths confirmed live: unchecked→relabel "proposed", checked+no-edit→hard block (0.01s, zero spend), checked+valid-edit→regenerate + strip disclosure entirely, checked+invalid-edit→drop from composited exports only |
| Judge `corrective_constraints` string/array bug | ✅ Fixed | Prompt wording let the model emit a JSON array instead of a string, crashing the merge; fixed prompt + defensive normalization |
| Source-material adherence check (refiner) | 🔶 Superseded, ceiling finding still holds | This applied to the old `finalization_worker`'s single-batch refiner (now reference-only). The underlying ceiling finding (checklist has no leverage over generic invented text with no source ground truth) is architecture-independent and still true of the new verifiers |
| Batch refiner JSON fragility | ✅ Resolved (architecturally) | Not fixed via Structured Outputs on the same mechanism — resolved by replacing the single big-blob call with `verification_worker`'s small independent per-verifier/per-slide calls, which structurally can't zero out the whole deck on one parse failure |
| Cross-batch approach-label uniqueness | ❌ Open | Only enforced within one 4-approach call. Unchanged by the verification-pipeline work |
| Per-slide A/B seed variants (n=2) | ⏸ Deferred | Explicitly deprioritized as a "luxury," not a correctness fix |
| Workshop ↔ production integration | ❌ Not started | No PR proposing any workshop mechanism into the real backend. Production has continued independent development in parallel (see below) — the workshop's copy of `prompts.py`/`workers.py` now predates real changes merged directly to production |

---

## Fixed & validated

- **Visual grammar structured splice.** `visual_grammar` object chosen once by the Deck Builder, spliced byte-identical into every slide's prompt by code (`format_visual_grammar_block`), instead of asking the model to repeat a prose paragraph across independent calls. Validated via direct A/B test (`compare_style_contract.py`): the old mechanism's consistency was emergent/lucky, the new one's is structural.
- **Pagination/chrome removal.** Root cause was structural (presenter-view framing inherently implies a page counter), not a wording problem. Fixed by removing the presenter-view/chrome concept from paid mode entirely — full-bleed screenshots, speaker notes delivered as separate text.
- **Archetype label leak.** `_contains_casefold` word-boundary regex fix (was matching "Architect" inside "architecture").
- **Slide-count ceiling.** `MAX_SELF_SERVE_SLIDE_COUNT` raised from 10 to 24 — the old cap assumed slide rendering was a single `n=10` image call; it's actually N independent per-slide calls regardless of count, throttled by a shared rate limiter, not slide count. Validated: 16-slide + hero PetNavi deck rendered with zero failures in ~8 minutes wall-clock (see `PROMPT_CHAIN_TECHNICAL_REPORT.txt` §7 for full timing).
- **Model configuration.** `ANCHOR_MODEL`/`TEMPLATE_DRAFTER_MODEL`/`DECK_DRAFTER_MODEL` hardcoded to `"gpt-5.4"` in workshop `prompts.py`, ignoring the shared `intelluric-site.env`'s `-mini` overrides. Reasoning: smaller context on the "-mini" variants raises hallucination/incoherence risk across longer source material, which is exactly what this workshop is now testing against (16-slide real outlines, multi-page supporting docs). Shared env file itself was not touched.
- **Parallel verification pipeline.** `verification_worker` replaces `finalization_worker` end-to-end: per-slide fan-out (spelling / source-accuracy / diagram-accuracy verifiers) → judge/disposition → conditional regeneration, spliced onto the verbatim original prompt rather than judge-rewritten. Iteratively debugged through real live rounds: false-positive over-triggering (fixed via ambiguity-gate/evidence-standard), layout drift on regeneration (fixed via corrective-constraints-only splice), product-concept drift on regeneration (fixed by wrapping regen calls through the same anchor-context wrapping as original generation). See spec §08 for full detail.
- **Selective non-canon inference exceptions.** `excepted_inference_elements`/`authorized_inferences`/`canon_overrides` — the deck builder decides an inferred value (e.g. pricing) exactly once per deck, every slide referencing it is checked for *consistency* with that decided value (not exempted from checking), and user refinement edits become deck-wide canon the same way. Validated live: a real pricing structure was decided once, applied consistently across the deck, and an unrelated real wording defect on the same slide was still correctly caught.
- **Inferred-element removal checkbox.** `inferred_element_decisions` — per-element checkbox gating whether inferred content is kept (default, relabeled "proposed") or must be replaced. Hard-blocks before any spend if checked with no edit; drops the slide from composited exports (not the archive) if an edit is supplied but doesn't resolve the claim; strips the "inferred"/disclosure wording entirely and ships the user's value as plain fact if the edit does resolve it. All four paths validated live against real model calls (job `pd_ed26cfd043e14b31`, Newton archetype).
- **Judge `corrective_constraints` type bug.** The disposition prompt's wording ("a short, explicit list of the concrete fixes") let the model return a JSON array instead of a string, crashing the `.strip()` merge the moment a second consumer touched it. Fixed the prompt wording ("ONE STRING, not a JSON array") and added defensive normalization in `_verify_and_finalize_slide` immediately after the judge call.

## Open (correctness/reliability, blocking "production ready")

1. **Cross-batch approach-label uniqueness.** `_validate_approach_manifest` only checks uniqueness within one 4-approach call. Duplicate labels ("Deterministic Chain," "Behavioral Truth," "Proof Cascade") have recurred across independent runs/archetypes on the same pitch. No code enforcement across batches. Unchanged by the verification-pipeline work.
2. **Source-material adherence has a real ceiling, not just a tuning problem.** Originally found against `finalization_worker`'s single-batch refiner (now reference-only), but the finding is architecture-independent: a checklist can only fix drift on *source-verifiable* terms (real terms from the mockup/outline) — it has no leverage over the base per-call transcription-fidelity of arbitrary generated slide text with no ground truth anywhere in `elevator_pitch`/`doc_text` to check it against (e.g. invented UI filler dialogue). This is a ceiling on the technique, still true of the new verifiers, not a defect to iterate away.
3. **Hero slide has no source-accuracy check.** Carried over from spec §11 — the hero's fixed prompt template carries less specific factual text than a numbered slide, so risk is lower, but it's an asymmetry, not a deliberate decision.
4. **Full regeneration doesn't guarantee pixel-level continuity.** A fresh text-to-image regeneration (not an I2I edit) can shift exact composition details relative to its own proof even with the anchor-context and verbatim-prompt-splice fixes. Accepted trade-off for speed and avoiding I2I's own fidelity loss, not a bug — see spec §11.

## Workshop ↔ production integration — not started

No PR has ever proposed any workshop mechanism (verification pipeline, archetype catalog v2, pricing exceptions, checkbox feature) into the real backend. This has been a deliberate, strict workshop/reference-only boundary the whole time — but it means the workshop's copy of `prompts.py`/`workers.py` (forked once at the start of this workshop) has been drifting from production, which has kept moving independently:

- Production merged PR #16 ("Add free hero/title slide; stop laundering anchor-writer fiction into team slides") on 2026-07-15 — a free, uncounted hero slide generated concurrently with the numbered-slide fan-out, plus a sourcing-authority rule stopping the Anchor Writer's invented "supergroup" narrative from being cited as literal team-slide fact. This is conceptually adjacent to (but independent of and differently solved from) this workshop's own anchor-writer/deck-builder framing addition (graphic-designer-not-AI reminder).
- As of this writing, production's `fix/pitch-synthase-hero-slide-and-team-fabrication-2026-07-08` branch has further **uncommitted** local changes: an archetype-vs-template-image hierarchy-rule clarification in `_deck_drafter_input`, and in-place enrichment of the *original* 8-entry `FOUNDER_ARCHETYPES` catalog (added "motivated by X" language per archetype) — a different catalog from this workshop's 10-entry replacement (Odysseus...Michelangelo). **These would collide if both were merged as-is** — production is deepening the original 8-entry catalog while the workshop replaced it outright with an unrelated 10-entry one. Any integration PR touching archetypes needs an explicit decision on how to reconcile the two, not a blind overwrite.
- Production's `finalization_worker` has not been touched by any of this — `verification_worker` genuinely doesn't exist there yet, so that specific mechanism has no live collision to reconcile, only the drift from having been forked before PR #16.

## Deferred (deliberately, not forgotten)

- **Per-slide A/B seed variants.** Run each full-deck slide's image call as `n=2` with different seeds so the user picks between two candidates per slide. Mechanically trivial (`image_request_params` already takes `n`); deferred because it roughly doubles image spend for a quality-of-life win, not a correctness fix. Revisit once the four open items above are closed.

## Design principles established this cycle (apply elsewhere, not yet done everywhere)

- **Structured fields beat prose repetition** when consistency across independent model calls matters. Applied to `visual_grammar`. **Not yet applied to:** `visual_direction` in `approach_drafter_prompt`/`single_slide_drafter_prompt`, which is still a free-prose ask.
- **Concrete, itemized checklists beat abstract instructions** when exhaustive scrutiny matters ("be more concrete" is itself ambiguous — the fix has to be the concrete instruction, not a request for more concreteness). Applied to the finalize-refiner source-check (v2). Same principle validated externally by the user-provided Protea persona-generation prompt, which forces named-dimension enumeration (literal, chromatic, thematic, tonal, emotional, cultural, historical, archetypal...) before synthesis rather than a generic "analyze the image."
- **Per-slide independence is real and should be exploited, not fought.** Every image_generation call is a one-off from the model's point of view with no shared state — this justified both the slide-count cap removal (§ above) and the failure-isolation fix (one slide's outcome must never cascade into failing the others).
- **A supporting reference image is not automatically consumed everywhere it's provided.** Approach drafting and single-slide preview are text-only by design (cost control); only the paid Anchor Writer stage currently accepts an image. Confirmed directly on the PetNavi UI-mockup test: folding the image into a text description let 3 of 4 independent calls reconstruct the real hardware specs exactly, but the 4th hallucinated different ones anyway — same mechanism-fidelity finding as everywhere else in this workshop, not solved by having the fact in-context.

## Test registry (jobs run, for reference)

| Job ID | Pitch | Archetype | Furthest stage reached | Notes |
|---|---|---|---|---|
| `pd_5e121a3bd64f4a61` | guiclid | Architect (fixed) | Full deck + finalization, multiple re-runs | Pagination bug, color-inversion bug both found & fixed here |
| `pd_b39004eeefac463f` | THGM (DoD SBIR) | Firebringer (fixed) | Single-slide previews; used as basis for style-contract A/B test | 4 approaches drafted, not taken to full deck |
| — (ephemeral, not persisted) | THGM "Ground as Antenna" | Firebringer | 4-slide full deck, both conditions | Style-contract A/B test only (`compare_style_contract.py`) |
| `pd_5072b5fa2fdb4ab2` | PetNavi (real 16-slide investor outline + UI mockup) | Copernicus (auto-picked) | Full 16-slide deck + finalization (x2, testing refiner checklist v1 vs v2) | First test past the old 10-slide cap; first test of source-adherence refiner checklist |
| (10 ephemeral jobs) | guiclid | All 10 catalog archetypes | Approach drafting only | Full-catalog comparison on one pitch |
| (5 ephemeral jobs) | 8 untested archetypes total (2 batches) | Commander/Oracle/Alchemist/Healer/Steward (old catalog) | Approach drafting only | Predates the old→new catalog swap |

---
*Update this file as status changes. It is the answer to "where do things actually stand," not a historical log — prune completed/superseded entries rather than letting it grow forever.*
