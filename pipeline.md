# Pitch Synthase v2 — Pipeline DAG

This is the actual stage graph registered in `ribotome.py` via
`register_stage()`, dispatched by `_advance()` off each job's
`stage_states` map — not a linear script. Companion doc: `architecture.md`
(component-level detail), `WIZARD_INTEGRATION_SCHEMA.md` (REST contract).

```mermaid
flowchart TD
    A1([User: POST /api/jobs\nelevator_pitch · audience · conveys\nassociation_words · infer_prototype]) --> A2[design_refs_text\nworker · condition: has_prototype]
    A1 --> A3[image_scan\nevent-triggered on image upload]

    A2 --> B1[approach_draft\nworker\n→ 4 approach objects\narchetype_a × archetype_b pair each]
    A2 --> A4[design_refs_images\nworker · condition: has_prototype\nrenders 1 reference image per design candidate]

    B1 --> C1{{human_intake\nhuman gate\nPOST /intake-options}}
    A3 --> C1
    A4 --> C2{{human_prototype_selection\nhuman gate · condition: has_prototype\nPOST /select-prototype}}

    B1 --> D1[previews\nworker\n1 preview slide per approach]
    C1 --> D1
    C2 --> D1

    D1 --> E1{{human_selection\nhuman gate\nPOST /select-approach}}
    E1 --> E2{{human_payment\nhuman gate\nPOST /mock-payment/full — lab bypass only,\nsee WIZARD_INTEGRATION_SCHEMA.md for the\nreal Stripe integration point}}

    E2 --> F1[generation\nworker]
    F1 --> F1a["1 · Anchor writer — ANCHOR_MODEL\ninvents the context this pitch already won in"]
    F1a --> F1b["2 · Visual Grammar Synthesizer\nMANDATORY — failure fails the whole stage"]
    F1b --> F1c["3 · Deck builder storyboard\nN+2 slides · title/purpose/body_points/image_prompt\nextracts the locked visual grammar, does not invent one"]
    F1c --> F1d["4 · Slide image gen × (N+2)\nchunked · rate-limited (50 img/min)"]
    F1d --> G1{{human_review\nhuman gate\nPOST /complete-review}}

    G1 --> H1[verification\nworker]
    H1 --> H2["Per flagged slide:\njudge → corrective_constraints\nsplice onto original image_prompt\n+ visual_grammar + anchor payload"]
    H2 --> H3[Composite: text overlay + speaker notes]
    H3 --> H4([Export: deck.pdf · deck.pptx\npreview.html · slides.md\nverification_report.json\npitch_deck_package.zip])
```

## Stage dependency table

| stage | depends on | condition |
|---|---|---|
| `design_refs_text` | — | `has_prototype` |
| `approach_draft` | `design_refs_text` | — |
| `image_scan` | — (event-triggered) | — |
| `human_intake` | `approach_draft` | — |
| `design_refs_images` | `design_refs_text` | `has_prototype` |
| `human_prototype_selection` | `design_refs_images` | `has_prototype` |
| `previews` | `approach_draft`, `human_prototype_selection`, `human_intake` | — |
| `human_selection` | `previews` | — |
| `human_payment` | `human_selection` | — |
| `generation` | `human_payment` | — |
| `human_review` | `generation` | — |
| `verification` | `human_review` | — |

When `has_prototype` (the job's `infer_prototype` flag) is false,
`design_refs_text`, `design_refs_images`, and `human_prototype_selection`
are auto-skipped (`stage_states[id] = "skipped"`) rather than routed around
— `previews` still fires once its other two dependencies clear.

## Job status column (legacy layer, still written alongside `stage_states`)

The `jobs.status` string column predates the stage-DAG rewrite and is still
updated by workers as a human-readable summary, but it is not what the
dispatcher reads — `stage_states` is authoritative. Observed values on the
primary (SAWC) path:

```
created
  → approaches_ready       (approach_draft worker done)
  → paid                   (human_payment gate satisfied)
  → generating_plan        (generation worker started)
  → rendering_slides       (storyboard complete, images rendering)
  → awaiting_review        (all slides rendered)
  → review_received        (human_review gate satisfied)
  → complete               (verification worker done)
  → stage_failed:{stage_id}  (any stage raises — includes the stage id)
```

`candidate_ready`, `template_*` status values also exist in `workers.py`
but belong to the legacy Path A workers (`template_worker`,
`candidate_worker`, `convert_generation_worker`) — not reachable from the
primary DAG above.

## Rate limits

| Call type | Limit | Enforced by |
|---|---|---|
| `gpt-image-2` | 50 img/min | `_throttle_image_generation()`, process-wide rolling window (`workers.py`, `_IMAGE_RATE_LIMIT = 50`) |
| Text models | not throttled | not a bottleneck at current volume |
