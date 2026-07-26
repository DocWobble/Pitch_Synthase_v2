# Using `ps_test_input.schema.json`

| Field | Value |
|---|---|
| Document ID | PS-V2-TESTSCHEMA-USAGE |
| Version | 1.0.0 |
| Companion to | PS-V2-TESTSCHEMA (`ps_test_input.schema.json`) |
| Last Updated | 2026-07-24 |

Fill out an object matching this schema and hand it off directly — every field maps 1:1 to a real job field or worker call parameter, so no interpretation is needed on the runner's end.

## Stage semantics (what `target_stage` actually runs)

| `target_stage` | Worker(s) called | Cost | Needs `approach_id`? |
|---|---|---|---|
| `approaches` | `approach_drafter_worker` | 1 text call | No |
| `single_slide_previews` | `approach_drafter_worker` + `single_slide_preview_worker` × 4 | 1 text call + 4 image calls | No |
| `full_deck` | full generation pass (Anchor Writer → Deck Builder → one title + N content-slide renders) | 1-2 text calls + N+1 image calls | **Yes** |
| `finalized` | parallel verification pipeline (`verification_worker`) on top of an existing `full_deck` run | ~4 text calls/slide + only-if-flagged regen image calls | **Yes** |

`finalized` assumes `full_deck` has already been run for this test (it operates on the existing proof images, not from scratch).

## Known limitation to plan around

`supporting_image_path` is only consumed starting at the paid Anchor Writer stage. If you want image content to influence `approaches` or `single_slide_previews`, describe it in `doc_text` as text instead — those stages are text-only by design (cost control).

## Authorized mockup/prototype design

When the source material describes the system but provides no actual product mockup or prototype, set `excepted_inference_elements` to include `"mockup_prototype_design"`. This authorizes the Deck Builder to decide one **proposed** visual and interaction system in `authorized_inferences`, then reuse it as deck-wide canon alongside `visual_grammar`. It does not authorize invented product, customer, or business facts. Do not set `inferred_element_decisions.mockup_prototype_design` to `true` unless refinement will provide replacement design direction; that later checkbox is a removal/replace instruction, not the inference authorization itself.

## Pitch Aspect modes

`pitch_aspect_modes` independently controls six variable content domains. Each key accepts `OMIT`, `INFER`, or `SOURCE`; omitted keys default to `INFER`.

- `OMIT`: exclude the aspect even when the source contains it.
- `INFER`: use the source and plausibly reconstruct missing details; source wins wherever it speaks.
- `SOURCE`: use only user-provided material for that aspect.

The six keys are `customer_stakeholder_value`, `market_ecosystem_competition`, `commercialization_pricing_financial`, `proof_validation_defensibility`, `execution_team_risk`, and `transaction_ask_use_of_proceeds`.

The modes continue through refinement. `INFER` values absent from the source are permitted, but the first storyboard use becomes deck-wide canon and later uses must match; any source conflict is corrected to the source. `SOURCE` content must be traceable to user material or removed. `OMIT` permits no direct examples or substantive content from that aspect.

## Pricing within commercialization

`commercialization_pricing_financial: INFER` permits the Deck Builder to reconstruct a proposed commercialization strategy when the source does not provide one. `SOURCE` restricts that aspect to user material; `OMIT` excludes it.

Include `"pricing"` in `excepted_inference_elements` only when the aspect is `INFER` and the model may also propose an explicit price, tier/package, licensing fee, subscription fee, per-unit charge, contract price, or related unit-economics structure for the pitch company's own offer. Without that narrower permission, an inferred commercialization strategy cannot invent the amount ultimately charged.

## Worked example

```json
{
  "test_name": "petnavi-existing-pitch",
  "audience": "early-stage hardware/consumer-AI seed investors and crowdfunding backers",
  "elevator_pitch": "Problem: AI today is cloud-locked, app-bound, and vendor-owned... Solution: PetNavi is a $40-80 USB dongle...",
  "conveys": "That this is a real, already-working hardware-plus-software architecture with concrete unit economics worked out -- not a concept pitch.",
  "doc_text": "<full 16-slide outline text>",
  "supporting_image_path": null,
  "archetype_id": null,
  "target_stage": "full_deck",
  "approach_id": "approach_3",
  "slide_count": 16,
  "universal_refinement_instruction": null
}
```

## What happens on handoff

Given a filled object, the runner:
1. Creates a job (or reuses one if `test_name` matches an existing run).
2. Sets every field directly on the job record — no paraphrasing, no re-deriving `audience`/`conveys` from looser notes.
3. Runs workers up to `target_stage` and reports results (approaches list, preview images, full deck, or finalized deck + acceptance manifest) exactly as it would for any other test in this workshop.
