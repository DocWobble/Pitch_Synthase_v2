# Pitch Synthase Workshop — Project Manifest

| Field | Value |
|---|---|
| Document ID | PS-V2-MANIFEST |
| Version | 1.0 (living — see note) |
| Status | Living — Updated Continuously, Not a Point-in-Time Release |
| Owner | Workshop (isolated) |
| Last Updated | 2026-07-13 |

The single place to check "what's actually true right now" about this workshop. Other docs describe the system as designed (`PITCH_SYNTHASE_V2_SPEC.md`, PS-V2-SPEC) or exactly how it mechanically works (`PROMPT_CHAIN_TECHNICAL_REPORT.txt`, PS-V2-PROMPTCHAIN). This file tracks *status*: what's fixed, what's validated, what's still open, and what's been deliberately deferred. Update this whenever something in that status changes — don't let findings live only in chat history. Unlike the spec (versioned releases with a change history), this document does not carry a change log — it is pruned and rewritten in place, so "Last Updated" is the only temporal marker that matters.

**Workshop path:** `/home/director/pitch_synthase_archetype_workshop`
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
| Per-slide failure isolation (finalization) | ✅ Implemented | **Not yet validated** — no run has hit the fallback path since the fix landed |
| Source-material adherence check (refiner) | 🔶 Partially working, ceiling identified | v2 checklist fixed both v1 defects (duplicate "Kokoro-itte", "parittion" regression) but introduced a *new* regression on unrelated text ("Listening…" → "Listendug…") — see below |
| Batch refiner JSON fragility | ❌ Open | `_parse_model_json` on free-text has failed multiple times; Structured Outputs not yet implemented |
| Cross-batch approach-label uniqueness | ❌ Open | Only enforced within one 4-approach call |
| Per-slide A/B seed variants (n=2) | ⏸ Deferred | Explicitly deprioritized as a "luxury," not a correctness fix |

---

## Fixed & validated

- **Visual grammar structured splice.** `visual_grammar` object chosen once by the Deck Builder, spliced byte-identical into every slide's prompt by code (`format_visual_grammar_block`), instead of asking the model to repeat a prose paragraph across independent calls. Validated via direct A/B test (`compare_style_contract.py`): the old mechanism's consistency was emergent/lucky, the new one's is structural.
- **Pagination/chrome removal.** Root cause was structural (presenter-view framing inherently implies a page counter), not a wording problem. Fixed by removing the presenter-view/chrome concept from paid mode entirely — full-bleed screenshots, speaker notes delivered as separate text.
- **Archetype label leak.** `_contains_casefold` word-boundary regex fix (was matching "Architect" inside "architecture").
- **Slide-count ceiling.** `MAX_SELF_SERVE_SLIDE_COUNT` raised from 10 to 24 — the old cap assumed slide rendering was a single `n=10` image call; it's actually N independent per-slide calls regardless of count, throttled by a shared rate limiter, not slide count. Validated: 16-slide + hero PetNavi deck rendered with zero failures in ~8 minutes wall-clock (see `PROMPT_CHAIN_TECHNICAL_REPORT.txt` §7 for full timing).
- **Model configuration.** `ANCHOR_MODEL`/`TEMPLATE_DRAFTER_MODEL`/`DECK_DRAFTER_MODEL` hardcoded to `"gpt-5.4"` in workshop `prompts.py`, ignoring the shared `intelluric-site.env`'s `-mini` overrides. Reasoning: smaller context on the "-mini" variants raises hallucination/incoherence risk across longer source material, which is exactly what this workshop is now testing against (16-slide real outlines, multi-page supporting docs). Shared env file itself was not touched.

## Implemented, not yet validated

- **Per-slide failure isolation in `finalization_worker`.** Previously: any single slide that wasn't cleanly extracted (or whose proof was missing) hard-failed the *entire* deck, discarding N-1 good slides. Now: a missing proof drops just that slide (logged, not fatal); a non-extracted slide falls back to its raw proof (paid-mode proofs are already clean full-bleed screenshots by construction, so this is a legitimate final slide, not a defect). The job only fails if *zero* slides produce anything usable.
  - **Why unvalidated:** every finalization run since the fix landed has succeeded cleanly on all slides — the fallback/drop code paths have literally never executed. Needs a run that actually hits a slide failure to confirm the isolation behaves as designed.

## Open (correctness/reliability, blocking "production ready")

1. **Batch refiner JSON fragility.** `finalize_refiner_prompt`'s output is parsed as free-text JSON via `_parse_model_json`; malformed output has zeroed out extraction prompts for an entire deck at least twice this cycle. Fix: adopt OpenAI Structured Outputs (`strict: true`, `json_schema`) to remove this failure mode structurally. Not implemented.
2. **Cross-batch approach-label uniqueness.** `_validate_approach_manifest` only checks uniqueness within one 4-approach call. Duplicate labels ("Deterministic Chain," "Behavioral Truth," "Proof Cascade") have recurred across independent runs/archetypes on the same pitch. No code enforcement across batches.
3. **Source-material adherence has a real ceiling, not just a tuning problem.** v1 instruction (abstract "cross-check against source") caught some drift but missed a duplicate occurrence of the same error on one slide, *and* introduced a new typo while correcting another ("partition" → "parittion"). v2 replaced the abstract instruction with a concrete, itemized checklist (enumerate every text element first; check every occurrence independently, not just the first; proofread the correction itself before finalizing) — same principle as the Protea persona-generation prompt the user shared (exhaustive *named-dimension* enumeration beats "look carefully"). Re-validated: v2 fixed both v1 defects cleanly (duplicate "Kokoro-itte" now consistently "Kokoro-lite"; "parittion" regression corrected back to "partition") — **but introduced a new regression on unrelated text**: "Listening…" (correct in v1) came back as "Listendug…" in v2. Root cause is structural, not a prompt-quality gap: "Kokoro-lite" and "Dual-partition" are checkable against real source material (they're terms from the mockup description/outline); "Listening…" is generic invented UI filler dialogue with no ground truth anywhere in `elevator_pitch`/`doc_text` to check it against. The source-adherence checklist can only fix drift on source-verifiable terms — it has no leverage over the base per-call transcription-fidelity of arbitrary generated slide text, which stays probabilistic regardless of prompt quality. This is a ceiling on the technique, not a defect to iterate away.
4. **Per-slide failure isolation** — see above, implemented but unvalidated; counts as open until confirmed on a real failure.

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
