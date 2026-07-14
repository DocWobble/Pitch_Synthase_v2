# Using `ps_test_input.schema.json`

| Field | Value |
|---|---|
| Document ID | PS-V2-TESTSCHEMA-USAGE |
| Version | 1.0.0 |
| Companion to | PS-V2-TESTSCHEMA (`ps_test_input.schema.json`) |
| Last Updated | 2026-07-13 |

Fill out an object matching this schema and hand it off directly — every field maps 1:1 to a real job field or worker call parameter, so no interpretation is needed on the runner's end.

## Stage semantics (what `target_stage` actually runs)

| `target_stage` | Worker(s) called | Cost | Needs `approach_id`? |
|---|---|---|---|
| `approaches` | `approach_drafter_worker` | 1 text call | No |
| `single_slide_previews` | `approach_drafter_worker` + `single_slide_preview_worker` × 4 | 1 text call + 4 image calls | No |
| `full_deck` | full generation pass (Anchor Writer → Deck Builder → per-slide + hero render) | 1-2 text calls + N+1 image calls | **Yes** |
| `finalized` | parallel verification pipeline (`verification_worker`) on top of an existing `full_deck` run | ~4 text calls/slide + only-if-flagged regen image calls | **Yes** |

`finalized` assumes `full_deck` has already been run for this test (it operates on the existing proof images, not from scratch).

## Known limitation to plan around

`supporting_image_path` is only consumed starting at the paid Anchor Writer stage. If you want image content to influence `approaches` or `single_slide_previews`, describe it in `doc_text` as text instead — those stages are text-only by design (cost control).

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
