# Pitch Synthase FNS_test — Project Manifest

| Field | Value |
|---|---|
| Document ID | PS-V2-FNS-MANIFEST |
| Status | Living branch status |
| Branch | `FNS_test` |
| Last updated | 2026-08-02 |

This is the current status surface for `FNS_test`. The engineering contract is
`PITCH_SYNTHASE_V2_SPEC.md`; the wizard contract is
`WIZARD_INTEGRATION_SCHEMA.md`; executable authority lives in `ribotome.py` and
`workers.py`. Archived reports and `samples/run-logs` are not current schemas.

## Status

| Item | Status | Current mechanism |
|---|---|---|
| DAG orchestration | Working | `ribotome.py` stage graph and boot-time producer/consumer audit |
| Approach workshop | Working | Four rhetorical approaches and one preview per approach |
| Optional prototype references | Working | Conditional design-reference branch |
| Canonical Anchor | Implemented | Factual/rhetorical authority; no fictional founders, traction, or room events |
| Founder voice | Implemented | FNS creates the Ghostwriter system prompt defining a speaker |
| Visual identity | Implemented | VGS creates the Spirit Boarder system prompt |
| Rhetorical organization | Implemented | Deck Builder writes argument jobs and capacity constraints only |
| Human-sounding copy | Implemented | Ghostwriter authors the sole final title/body copy |
| Visual specification | Implemented | Spirit Boarder sees final copy and actual references, then authors image prompts |
| Exact-copy rendering | Implemented and manually proven on one slide | Renderers receive one final copy authority and may not invent prose |
| Review/regeneration integrity | Implemented | Preserves Spirit Boarder prompt, reviewed copy contract, and references |
| Frontend contract | Updated | Extra calls remain internal to one Generation screen |
| Fresh complete rendered/exported deck on this revision | Not yet claimed | A one-slide render is not a full delivery proof |
| Live payment/auth/deployment | Outside this workshop proof | Separate production integration work |

## Current authority chain

```text
source material + selected approach
  -> Canonical Anchor
  -> FNS and VGS in parallel
  -> rhetorical Deck Builder
  -> Ghostwriter final visible copy
  -> Spirit Boarder visual specifications + actual references
  -> per-slide image workers with exhaustive exact-copy contracts
  -> review / verification / constrained regeneration
  -> exports
```

No renderer receives two versions of slide copy. Deck Builder never receives FNS
or VGS output. Ghostwriter receives no visual references. Spirit Boarder cannot
rewrite copy. Image workers do not reopen Anchor/source prose.

## Validation completed

- Worker handoffs retain the Anchor, raw authoritative sources, selected
  approach, final copy, and applicable images at the stages that require them.
- FNS output is the Ghostwriter **system prompt**; rewrite instructions and
  source/storyboard material are the **user prompt**.
- VGS output is the Spirit Boarder **system prompt**.
- Ghostwriter may rewrite wholesale while slide coverage, facts, and capacity
  budgets remain enforced.
- Spirit Boarder receives actual visual-reference images but does not embed
  visible copy in its image prompts.
- Renderer prompts contain the exhaustive final-copy contract and prohibit all
  additional readable prose.
- One RunReady slide rendered through the repaired final package with exact
  visible text and usable negative space.
- Prompt/worker/runtime modules compile, the DAG audit passes, and the wizard
  retains the frontend-facing `slide_specs` and `deck_proof_plan` fields.

## Remaining proof boundary

Do not call this production-deployed merely because its contracts and one render
work. The remaining meaningful validation is one fresh complete paid deck
through review and export on the exact revision, followed by the separate
frontend/payment/deployment integration. That proof should verify:

- every slide uses Ghostwriter copy verbatim;
- no image contains invented labels or prose;
- slide density stays within declared copy budgets;
- Spirit Boarder maintains visual identity and reference fidelity;
- review edits survive regeneration without reopening earlier authorities;
- PDF, PPTX, HTML, Markdown, and manifest agree on slide coverage.

## Explicit legacy surfaces

The old fictional `anchor_writer_prompt`, combined `deck_builder_prompt`, paid /
preview / convert wrappers, and `visual_grammar_prompt` remain compatibility and
A/B APIs only. `test_deck_ab.py` and relevant cases in
`test_fns_summary_register.py` are labeled legacy and cannot validate the active
workflow.

`PROMPT_CHAIN_TECHNICAL_REPORT.txt`,
`VERIFICATION_PIPELINE_IMPLEMENTATION_PROMPT.md`, and
`PRODUCTION_READY_SEMANTIC_DESCRIPTION.md` are archived historical records.
Their old schemas are preserved behind prominent non-authority notices.

---

*Update this file when branch behavior or validation status changes.*
