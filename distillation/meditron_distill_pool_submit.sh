#!/bin/bash

DEFAULT_LIST_FILE="datasets_to_distill.txt"
DEFAULT_MODEL_NAME="google/medgemma-27b-text-it"
DEFAULT_REQUEST_CONCURRENCY=8
DEFAULT_NUM_WORKERS=4
DEFAULT_MAX_RETRIES_PER_SHARD=2
DEFAULT_LEASE_TIMEOUT_SECONDS=7200
DEFAULT_WORKER_TIME_LIMIT="05:59:59"

usage() {
  cat <<EOF
Usage:
  bash $0 [list_file] [--model <name>] [--num-workers N] [--request-concurrency N] [--limit N] [--seed N] [--deterministic] [--strict-repro] [--model-revision REV] [--max-retries-per-shard N] [--lease-timeout-seconds N] [--worker-time-limit HH:MM:SS]
EOF
}

parse_args() {
  LIST_FILE="$DEFAULT_LIST_FILE"
  MODEL_NAME="$DEFAULT_MODEL_NAME"
  NUM_WORKERS="$DEFAULT_NUM_WORKERS"
  REQUEST_CONCURRENCY="$DEFAULT_REQUEST_CONCURRENCY"
  LIMIT_ARG=""
  SEED_ARG=""
  DETERMINISTIC=0
  STRICT_REPRO=0
  MODEL_REVISION_ARG=""
  MAX_RETRIES_PER_SHARD="$DEFAULT_MAX_RETRIES_PER_SHARD"
  LEASE_TIMEOUT_SECONDS="$DEFAULT_LEASE_TIMEOUT_SECONDS"
  WORKER_TIME_LIMIT="$DEFAULT_WORKER_TIME_LIMIT"

  if [ -n "${1:-}" ] && [[ "$1" != --* ]]; then
    LIST_FILE="$1"
    shift
  fi

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --model)
        shift
        MODEL_NAME="${1:-}"
        ;;
      --num-workers)
        shift
        NUM_WORKERS="${1:-}"
        ;;
      --request-concurrency)
        shift
        REQUEST_CONCURRENCY="${1:-}"
        ;;
      --limit)
        shift
        LIMIT_ARG="${1:-}"
        ;;
      --seed)
        shift
        SEED_ARG="${1:-}"
        ;;
      --deterministic)
        DETERMINISTIC=1
        ;;
      --strict-repro)
        STRICT_REPRO=1
        ;;
      --model-revision)
        shift
        MODEL_REVISION_ARG="${1:-}"
        ;;
      --max-retries-per-shard)
        shift
        MAX_RETRIES_PER_SHARD="${1:-}"
        ;;
      --lease-timeout-seconds)
        shift
        LEASE_TIMEOUT_SECONDS="${1:-}"
        ;;
      --worker-time-limit)
        shift
        WORKER_TIME_LIMIT="${1:-}"
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "Unknown arg: $1"
        usage
        exit 1
        ;;
    esac
    shift
  done

  if [ "$STRICT_REPRO" -eq 1 ] && [ -z "$SEED_ARG" ]; then
    echo "--seed is required with --strict-repro"
    exit 1
  fi
}

parse_args "$@"
export PROJECT_ROOT=${SLURM_SUBMIT_DIR:-$(pwd)}
cd "$PROJECT_ROOT" || exit 1

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi

if [ ! -f "$LIST_FILE" ]; then
  echo "List file not found: $LIST_FILE"
  exit 1
fi

mkdir -p "$PROJECT_ROOT/distill_reports"

HEAD_JOB_NAME="$(python3 - "$MODEL_NAME" <<'PY'
import sys
from distill_common import model_tag
print(f"distill-{model_tag(sys.argv[1])}-pool-head")
PY
)"

SBATCH_CMD=(
  sbatch
  -J "$HEAD_JOB_NAME"
  "$PROJECT_ROOT/meditron_distill_head.sh"
  "$LIST_FILE"
  --model "$MODEL_NAME"
  --num-workers "$NUM_WORKERS"
  --request-concurrency "$REQUEST_CONCURRENCY"
  --max-retries-per-shard "$MAX_RETRIES_PER_SHARD"
  --lease-timeout-seconds "$LEASE_TIMEOUT_SECONDS"
  --worker-time-limit "$WORKER_TIME_LIMIT"
)
if [ -n "$SEED_ARG" ]; then
  SBATCH_CMD+=(--seed "$SEED_ARG")
fi
if [ "$DETERMINISTIC" -eq 1 ]; then
  SBATCH_CMD+=(--deterministic)
fi
if [ "$STRICT_REPRO" -eq 1 ]; then
  SBATCH_CMD+=(--strict-repro)
fi
if [ -n "$MODEL_REVISION_ARG" ]; then
  SBATCH_CMD+=(--model-revision "$MODEL_REVISION_ARG")
fi
if [ -n "$LIMIT_ARG" ]; then
  SBATCH_CMD+=(--limit "$LIMIT_ARG")
fi

SUBMISSION_OUTPUT="$("${SBATCH_CMD[@]}")"
echo "$SUBMISSION_OUTPUT"
HEAD_JOB_ID="$(echo "$SUBMISSION_OUTPUT" | awk '{print $4}')"
if [ -n "$HEAD_JOB_ID" ]; then
  echo "Head job submitted: $HEAD_JOB_ID"
  echo "Track status: squeue -j $HEAD_JOB_ID"
  echo "Recent logs: ls -lt distill_reports/R-distill-*pool-head* | head"
fi
