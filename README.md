# Meditron-4

Axolotl configs and Slurm helpers for training/evaluating of Meditron models on CSCS.

## Prerequisites
- CSCS account with access to the storage paths referenced in the configs.
- Python environment described by your EDF file (see `ENV` below).
- Clone of the lm-evaluation-harness fork alongside this repo: `git clone https://github.com/Xkrilandar/lm-evaluation-harness`.

## Environment setup
1. Create a `.env` in the repo root with your paths and tokens (do not commit secrets), following the `.env.example` format:
2. Log in to CSCS and load your environment (quick commands live in `setup.md` and `script_login.bash`).

## Training
- Pick a config in `axolotl_config/` (e.g., `apertus-8b-only-mediset.yaml`, `apertus-8b-ablation-no-mediset.yaml`, `apertus-70b.yaml`).
- Submit via Slurm (self-submits and tails logs):
  ```
  bash meditron_train.sh axolotl_config/apertus-8b-only-mediset.yaml
  ```
  The script:
  - injects your `.env` values into the template and writes `axolotl_config/config.yaml`,
  - submits itself with `sbatch -J <config-name> ...`,
  - tails `reports/R-<job>.<jobid>.err` once the log appears.
- Adjust SBATCH resources at the top of `meditron_train.sh` if you need different GPUs/time.

## Script usage
- `meditron_train.sh`: submit a training run.
  ```
  bash train.sh axolotl_config/apertus-8b-only-mediset.yaml
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
