# RiboTome workflow engine

RiboTome compiles a typed stage range, then operates it as a durable run. The
CLI and HTTP API are adapters over the same `RiboTomeRuntime`; neither owns a
second execution model.

## Core model

- A graph declares stages, dependencies, typed ports, workers, and decisions.
- `plan` validates a bounded range and computes its external input cut.
- `start` creates a durable run ID. Inputs may include later decisions such as
  `selected_approach_id`, but do not have to.
- Each accepted stage commits a new state revision, an output artifact, its
  SHA-256 hash, worker identity, input/output hashes, and an append-only event.
- Missing inputs suspend at the first stage that needs them. `resume` merges
  typed inputs and continues from the last accepted stage.
- Worker failures preserve accepted stages. Retrying the run executes only the
  failed/incomplete stage and its successors.
- Human stages suspend explicitly and are completed with typed outputs.

State is checkout-local under `local_state/ribotome/` (SQLite in WAL mode plus
JSON artifacts), so a process restart does not lose run authority.

## CLI

```bash
python ribotome_cli.py validate
python ribotome_cli.py steps --json
python ribotome_cli.py plan --from prepare_pitch --to spirit_boarder

# Start with partial input; execution suspends at selected_approach_id.
python ribotome_cli.py start --from prepare_pitch --to spirit_boarder --inputs pitch.json
python ribotome_cli.py status RUN_ID
python ribotome_cli.py resume RUN_ID --inputs choice.json

# Or pre-supply the choice in pitch.json for uninterrupted execution.
python ribotome_cli.py run --from prepare_pitch --to spirit_boarder --inputs pitch.json

python ribotome_cli.py events RUN_ID
python ribotome_cli.py artifacts RUN_ID
python ribotome_cli.py runs
python ribotome_cli.py cancel RUN_ID
```

`complete RUN_ID --outputs human-output.json` is the human-worker equivalent of
resume. The submitted object must exactly match that stage's declared outputs.

## HTTP control surface

- `GET /api/graph/steps`
- `GET /api/graph/plan?start=...&end=...`
- `POST /api/graph/runs` — `{from, to, inputs, execute?}`
- `GET /api/graph/runs`
- `GET /api/graph/runs/{id}`
- `POST /api/graph/runs/{id}/resume` — `{inputs}`
- `POST /api/graph/runs/{id}/complete` — `{outputs}`
- `GET /api/graph/runs/{id}/events`
- `GET /api/graph/runs/{id}/artifacts`

`POST /api/graph/run` remains as a compatibility alias that creates the same
durable run and returns when it completes, suspends, or fails.
