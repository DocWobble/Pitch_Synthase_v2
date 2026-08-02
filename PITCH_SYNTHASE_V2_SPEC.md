# Pitch Synthase v2 — FNS_test Engineering Spec

| Field | Value |
|---|---|
| Document ID | PS-V2-FNS-TEST |
| Version | 3.0.0 |
| Status | Branch contract — frontend-ready workflow candidate |
| Branch | `FNS_test` |
| Runtime | `ribotome.py` DAG dispatcher |
| Last updated | 2026-08-02 |

This document describes the active `FNS_test` workflow. Historical prompt-chain
reports and checked-in run logs are evidence of earlier experiments, not schema
authority. The frontend contract is `WIZARD_INTEGRATION_SCHEMA.md`; the executable
stage and field declarations in `ribotome.py` remain the final runtime authority.

## 1. Product flow

1. Collect the complete pitch, audience, desired associations, optional source
   documents/images, inference controls, and requested content-slide count.
2. Draft four rhetorical/strategic approaches.
3. Optionally draft prototype-design references when prototype inference is
   explicitly enabled.
4. Render one preview for each approach and let the user select one.
5. After payment, generate the complete deck through the six-step authority
   chain below.
6. Let the user review visible text and request verification/regeneration.
7. Export PDF, PPTX, HTML, Markdown, images, and the verification manifest.

The requested content-slide count becomes `N+2` rendered slides: one opening,
`N` content slides, and one close.

## 2. Full-deck authority chain

### 2.1 Canonical Anchor

The Canonical Anchor is a factual and rhetorical brief. It receives the selected
approach plus all relevant source material and must preserve:

- material facts, ranges, caveats, statuses, and phase gates;
- explicit absences, including no prototype, traction, partner, or validation;
- the intended audience, transaction, argument, and evidence burden;
- enough detail that downstream workers do not have to recover facts from hints.

It cannot invent founders, biographies, customers, partners, traction, room
events, or a presentation that supposedly already happened. It is not slide
copy, an image prompt, or visual direction.

### 2.2 FNS and VGS

The Founder Narrative Synthesizer and Visual Grammar Synthesizer run in parallel.
Neither output is sent to Deck Builder.

FNS returns one `ghostwriter_system_prompt`. It defines a speaker, not a list of
abstract style rules: the founder's relevant backstory, temperament, recurring
concerns, rhetorical habits, sentence behavior, judgment patterns, and factual
boundaries. It addresses the model as the founder: `YOU are this founder...`.
Invented character material controls voice only and can never become a company
claim.

VGS returns one `spirit_boarder_system_prompt`. It defines the deck-specific art
director: visual worldview, composition behavior, palette, typography, grids,
negative space, diagram vocabulary, reference-image handling, and truth
discipline. It never owns visible prose.

### 2.3 Rhetorical Deck Builder

Deck Builder receives the Canonical Anchor and selected approach. It owns only
rhetorical organization. Its strict JSON response is:

```json
{
  "rhetorical_storyboard": [
    {
      "slide_number": 1,
      "slide_role": "opening",
      "argument_job": "...",
      "required_points": ["..."],
      "logical_relationships": ["..."],
      "priority": "...",
      "copy_capacity": {
        "title_max_words": 8,
        "body_point_count": 1,
        "body_point_max_words": 14
      }
    }
  ]
}
```

It writes no title, body copy, layout, typography, visual grammar, composition,
or image prompt. Required points must be detailed enough that the Ghostwriter
does not need to infer facts already present in source material.

### 2.4 Ghostwriter

The Ghostwriter receives:

- FNS output as its **system prompt**;
- a **user prompt** beginning with the instruction that the storyboards are the
  rough draft for its presentation and it must rewrite all visible text as what
  it would actually say;
- the complete rhetorical storyboard;
- the Canonical Anchor and authoritative source text.

It is free to rewrite wholesale and exercise judgment. It cannot change facts,
slide coverage, slide numbers, rhetorical jobs, or copy-capacity constraints.
Its output is the sole visible-copy authority:

```json
{
  "storyboard_copy": [
    {
      "slide_number": 1,
      "title": "...",
      "body_points": ["..."]
    }
  ]
}
```

It receives no image references or image prompts because they do not improve
copy and can bias the writing.

### 2.5 Spirit Boarder

The Spirit Boarder receives:

- VGS output as its **system prompt**;
- the rhetorical storyboard;
- immutable Ghostwriter copy;
- all applicable actual visual-reference images.

It creates the only visual specification for each slide:

```json
{
  "visual_storyboard": [
    {
      "slide_number": 1,
      "composition_intent": "...",
      "image_prompt": "..."
    }
  ],
  "visual_reference_paths": ["..."]
}
```

It may make wholesale visual judgments within the rhetorical job and VGS
identity. It cannot add, quote, paraphrase, or rewrite visible prose in the image
prompt. Actual references outrank prose about factual appearance.

### 2.6 Image workers

Each image worker receives exactly one completed slide package:

- the Spirit Boarder's self-contained per-slide image prompt;
- the final Ghostwriter title and body points;
- an exhaustive exact-copy contract;
- the applicable actual reference images.

It does not receive the FNS prompt, VGS prompt, Canonical Anchor, raw source
prose, or a competing earlier storyboard version. The Ghostwriter copy is the
single visible-text authority. The renderer must reproduce it exactly and may
not invent captions, labels, legends, annotations, interface microcopy, page
numbers, or any other readable text.

## 3. Persistence and frontend schema

The internal strategy directory records each authority boundary:

```text
strategy/
  anchor_writer_output.json
  founder_narrative_output.json
  visual_grammar_output.json
  paid_storyboard.json
  paid_storyboard_ghostwriter_output.json
  paid_spirit_boarder_output.json
  deck_proof_plan.json
```

The frontend-facing output remains intentionally stable:

```json
{
  "slide_specs": [
    {
      "slide_index": 1,
      "headline": "...",
      "body_points": ["..."],
      "speaker_note": "..."
    }
  ],
  "deck_proof_plan": {
    "anchor_writer_output": {},
    "paid_storyboard": []
  }
}
```

`deck_proof_plan` also retains the completed rhetorical, copy, and Spirit
Boarder authorities needed by review, verification, and regeneration. These
internal substeps remain one `generation` DAG stage and one wizard screen; the
frontend must not create extra human gates for them.

## 4. Review and regeneration

User-edited title/body text supersedes the original Ghostwriter copy for that
slide. Verification compares the rendered proof with the slide's factual,
rhetorical, copy, and visual authorities.

When regeneration is required, the worker:

1. preserves the original Spirit Boarder prompt;
2. appends only the judge's corrective constraints;
3. recompiles the exact-copy contract from reviewed or original final copy;
4. restores applicable actual reference images;
5. performs a fresh text-to-image call.

Regeneration does not reopen the Anchor, raw source prose, FNS, or VGS. It does
not ask a judge or renderer to restoryboard the slide.

## 5. Truth and inference controls

`pitch_aspect_modes` controls six content domains as `OMIT`, `INFER`, or
`SOURCE`. Source facts take precedence. `OMIT` removes the domain. `SOURCE`
allows only supplied material. `INFER` permits bounded reconstruction where the
source is silent, subject to cross-slide consistency.

`excepted_inference_elements` remains a narrower permission for supported
elements such as proposed pricing and mockup prototype design. It cannot reopen
an aspect marked `SOURCE` or `OMIT`, and it cannot convert a proposal into
traction or validation. Planning values and conceptual designs must be labeled
honestly.

## 6. Validation invariants

The worker rejects outputs that violate any of these boundaries:

- wrong slide count, ordering, or coverage;
- missing or duplicate slide numbers;
- Deck Builder visible copy or visual instructions;
- Ghostwriter copy exceeding the Deck Builder's capacity budget;
- Ghostwriter changes to slide coverage;
- Spirit Boarder missing a slide or inserting visible prose into image prompts;
- missing title/body copy or image prompt at render time;
- a renderer prompt without the exhaustive exact-copy contract.

The DAG boot audit additionally rejects declared fields without producers or
consumers. `WIZARD_INTEGRATION_SCHEMA.md` defines the public request/response
contract and `architecture.md` describes the operational call graph.

## 7. Legacy surfaces

`anchor_writer_prompt`, `deck_builder_prompt`, `paid_deck_builder_prompt`,
`preview_deck_builder_prompt`, `convert_deck_builder_prompt`, and
`visual_grammar_prompt` remain as legacy compatibility/A-B helpers. They are not
the active `generation_worker` authority chain. Tests that exercise those APIs
must be labeled legacy and cannot be treated as end-to-end workflow validation.

Checked-in `samples/run-logs/` artifacts predate the FNS/Ghostwriter/Spirit
Boarder split and are retained only as historical render fixtures. They are not
examples of the current schema.

---

*Pitch Synthase v2 — FNS_test branch contract · rev. 10*
