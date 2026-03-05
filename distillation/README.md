# Distillation

This distillation workflow has been simplified to two scripts in `distillation/`:

- `distill_head.sh`: prepares caches, builds the shard queue, submits workers, monitors progress, merges outputs, and sends Slack notification on exit.
- `distill_worker.sh`: runs vLLM, claims shards, processes them, and updates the queue.

## Quickstart

From the repo root:

```bash
bash distillation/distill_head.sh distillation/datasets_to_distill.txt \
  --strict-repro \
  --deterministic \
  --seed 42 \
  --model-revision "$DISTILL_MODEL_REVISION"
```

## Environment variables

- `DISTILL_MODEL_REVISION`: model revision/commit for strict reproducibility.
- `SLACK_WEBHOOK_URL`: optional. If set, the head script sends a completion/failure message.
- `SLACK_TAG`: optional. Prefix for Slack messages.
- `requests` and `tenacity` are expected to be available in the runtime environment.

## Run state layout

Each run creates a directory under `distill_reports/`:

- `queue.db`: shard queue/state (SQLite)
- `summary.json`: aggregate counts
- `events.log`: state transitions

## Outputs

- Shard outputs: `*_distillation_<model>.shard-XXX-of-YYY.jsonl` next to the source dataset.
- Merged outputs: `*_distillation_<model>.jsonl` next to the source dataset.
- Strict reproducibility manifests: `*.manifest.json` alongside shard and merged outputs.

## Notes

- The head script prepares caches automatically.
- The worker starts a local vLLM server and expects GPU resources as configured in its SBATCH header.
