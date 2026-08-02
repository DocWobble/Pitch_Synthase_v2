# Historical run-log fixtures

The JSON files under this directory are preserved outputs from the pre-FNS
workshop pipeline. They support historical render comparison only.

They are **not current schema examples**. In particular, their
`strategy/paid_storyboard.json` files use the retired combined shape in which
Deck Builder owned visible copy, `visual_grammar`, and `image_prompt`.

The active `FNS_test` authority chain is:

```text
Canonical Anchor
  -> FNS and VGS in parallel
  -> rhetorical Deck Builder
  -> Ghostwriter final copy
  -> Spirit Boarder visual specification
  -> exact-copy image renderer
```

Use these files as current schema authority instead:

- `PITCH_SYNTHASE_V2_SPEC.md` — internal authority and output schemas
- `WIZARD_INTEGRATION_SCHEMA.md` — frontend request/response contract
- `ribotome.py` — executable DAG field declarations
- `workers.py` — persistence and worker handoffs

New checked-in samples must include the separate `rhetorical_storyboard`,
`storyboard_copy`, and `visual_storyboard` authorities or be explicitly marked
as historical fixtures.
