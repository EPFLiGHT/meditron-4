# Meditron-4

Axolotl configs and Slurm helpers for training/evaluating of Meditron models on CSCS.

## Prerequisites
- CSCS account with access to the storage paths referenced in the configs.
- Python environment described by your EDF file (see `ENV` below).
- Clone of the lm-evaluation-harness fork alongside this repo: `git clone https://github.com/Xkrilandar/lm-evaluation-harness`.

## Environment setup
Create a `.env` in the repo root with your paths and tokens (do not commit secrets), following the `.env.example` format:

## Training
- Pick a config in `axolotl_config/` (for Meditron-4/Qwen-3 use `sft_meditron4_qwen3.yaml`).
- Submit via Slurm (self-submits and tails logs):
  ```
  bash meditron_train.sh axolotl_config/sft_meditron4_qwen3.yaml
  ```
  The script:
  - injects your `.env` values into the template and writes `axolotl_config/config.yaml`,
  - submits itself with `sbatch -J <config-name> ...`,
  - tails `reports/R-<job>.<jobid>.err` once the log appears.
- Adjust SBATCH resources at the top of `meditron_train.sh` if you need different GPUs/time.

## Script usage
- `meditron_train.sh`: submit a training run.
  ```
  bash train.sh axolotl_config/sft_meditron4_qwen3.yaml
  ```
- `meditron_eval.sh`: submit an eval run (data parallel via accelerate).
  ```
  bash eval.sh $STORAGE_ROOT/apertus/huggingface/Apertus8B
  ```
  Optional flags:
  - `--debug` adds `--limit 100` and sets verbosity to DEBUG.
  - `--model_parallelism` runs without accelerate and adds `parallelize=True` to model args (for the 70B)

- `summarise_evals.sh`: scan eval reports and summarize eval outputs.
  ```
  bash summarise_evals.sh
  ```
- `find_training_errors.sh`: scan reports for training errors.
  ```
  bash find_training_errors.sh
  ```
- `slack_helpers.sh`: helper functions for other scripts (not meant to be run directly).

## Distillation
From the `distillation/` directory:

1. Export immutable model revisions for provenance:
   ```bash
   export DISTILL_MODEL_REVISION=<medgemma_revision_or_commit>
   export JUDGE_MODEL_REVISION=<medgemma_revision_or_commit>
   ```
2. Prepare dataset caches:
   ```bash
   python3 prepare_distill_datasets.py datasets_to_distill.txt
   ```
3. Submit deterministic strict-repro shard jobs (defaults to `google/medgemma-27b-text-it`):
   ```bash
   bash meditron_distill_submit.sh datasets_to_distill.txt \
     --strict-repro \
     --deterministic \
     --seed 42 \
     --model-revision "$DISTILL_MODEL_REVISION"
   ```
4. Merge shard outputs and validate reproducibility manifests:
   ```bash
   python3 merge_distilled_shards.py \
     --list-file datasets_to_distill.txt \
     --model google/medgemma-27b-text-it \
     --strict-repro
   ```
5. Build final curated Meditron-4 mixture:
   ```bash
   python3 build_meditron4_mixture.py \
     --list-file datasets_to_distill.txt \
     --distill-model google/medgemma-27b-text-it \
     --judge-model google/medgemma-27b-text-it \
     --max-attempts 5 \
     --strict-repro \
     --deterministic \
     --seed 42 \
     --distill-model-revision "$DISTILL_MODEL_REVISION" \
     --judge-model-revision "$JUDGE_MODEL_REVISION" \
     --version "$(date -u +%Y%m%d)"
   ```
   Outputs:
   - `curated_mixtures/meditron4_mixture_<version>.jsonl`
   - `curated_mixtures/meditron4_mixture_<version>.manifest.json`
   - `curated_mixtures/meditron4_mixture_<version>_unresolved_labeled.jsonl`

### Reproducibility guarantees
- `--strict-repro` enforces seeded runs and required model revision provenance.
- Distillation workers store per-row `distilled_config_sha256` and `distilled_repro`.
- Shard and merged outputs emit manifest files with hashes/provenance.
- Curation writes dataset checksums, config hash, git commit SHA, attempt stats, and unresolved labeled rows.
