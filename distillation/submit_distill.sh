#!/bin/bash
set -euo pipefail

DEFAULT_LIST_FILE="distillation/datasets_to_distill.txt"
DEFAULT_MODEL_NAME="google/medgemma-27b-text-it"
DEFAULT_NUM_WORKERS=4
DEFAULT_REQUEST_CONCURRENCY=8
DEFAULT_WORKER_TIME_LIMIT="05:59:59"
DEFAULT_MAX_RETRIES_PER_SHARD=2
DEFAULT_LEASE_TIMEOUT_SECONDS=7200
DEFAULT_SBATCH_ENV_FILE="${HOME}/.edf/inference.toml"
DEFAULT_WORKER_DEPENDENCY_MODE="${DISTILL_WORKER_DEPENDENCY_MODE:-after}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

LIST_FILE="$DEFAULT_LIST_FILE"
MODEL_NAME="$DEFAULT_MODEL_NAME"
NUM_WORKERS="$DEFAULT_NUM_WORKERS"
REQUEST_CONCURRENCY="$DEFAULT_REQUEST_CONCURRENCY"
WORKER_TIME_LIMIT="$DEFAULT_WORKER_TIME_LIMIT"
MAX_RETRIES_PER_SHARD="$DEFAULT_MAX_RETRIES_PER_SHARD"
LEASE_TIMEOUT_SECONDS="$DEFAULT_LEASE_TIMEOUT_SECONDS"
SBATCH_ENV_FILE="${DISTILL_SBATCH_ENV_FILE:-$DEFAULT_SBATCH_ENV_FILE}"
WORKER_DEPENDENCY_MODE="$DEFAULT_WORKER_DEPENDENCY_MODE"
LIMIT_ARG=""
SEED_ARG=""
DETERMINISTIC=0
STRICT_REPRO=0
MODEL_REVISION_ARG=""

HEAD_ARGS=("$@")

if [ -n "${1:-}" ] && [[ "$1" != --* ]]; then
  LIST_FILE="$1"
  shift
fi

while [ "$#" -gt 0 ]; do
  case "$1" in
    --model)
      shift; MODEL_NAME="${1:-}" ;;
    --num-workers)
      shift; NUM_WORKERS="${1:-}" ;;
    --request-concurrency)
      shift; REQUEST_CONCURRENCY="${1:-}" ;;
    --worker-time-limit)
      shift; WORKER_TIME_LIMIT="${1:-}" ;;
    --max-retries-per-shard)
      shift; MAX_RETRIES_PER_SHARD="${1:-}" ;;
    --lease-timeout-seconds)
      shift; LEASE_TIMEOUT_SECONDS="${1:-}" ;;
    --limit)
      shift; LIMIT_ARG="${1:-}" ;;
    --seed)
      shift; SEED_ARG="${1:-}" ;;
    --deterministic)
      DETERMINISTIC=1 ;;
    --strict-repro)
      STRICT_REPRO=1 ;;
    --model-revision)
      shift; MODEL_REVISION_ARG="${1:-}" ;;
    --sbatch-environment)
      shift; SBATCH_ENV_FILE="${1:-}" ;;
    --worker-dependency)
      shift; WORKER_DEPENDENCY_MODE="${1:-}" ;;
  esac
  shift
done

# distill_head.sh does not accept --worker-dependency; strip it from forwarded args.
FILTERED_HEAD_ARGS=()
skip_next=0
for arg in "${HEAD_ARGS[@]}"; do
  if [ "$skip_next" -eq 1 ]; then
    skip_next=0
    continue
  fi
  if [ "$arg" = "--worker-dependency" ]; then
    skip_next=1
    continue
  fi
  FILTERED_HEAD_ARGS+=("$arg")
done

if [ "$WORKER_DEPENDENCY_MODE" != "after" ] && [ "$WORKER_DEPENDENCY_MODE" != "afterok" ]; then
  echo "--worker-dependency must be one of: after, afterok"
  exit 1
fi

if [ "$STRICT_REPRO" -eq 1 ] && [ -z "$SEED_ARG" ]; then
  echo "--seed is required with --strict-repro"
  exit 1
fi

if [ ! -f "$LIST_FILE" ]; then
  echo "List file not found: $LIST_FILE"
  exit 1
fi

MODEL_TAG="$(python3 - "$MODEL_NAME" <<'PY'
import sys
raw = (sys.argv[1] or "").strip().lower()
safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw).strip("._-")
print(safe or "model")
PY
)"

RUN_DIR="$(python3 - "$PROJECT_ROOT" "$MODEL_NAME" <<'PY'
import datetime as dt
import secrets
import sys
from pathlib import Path

def model_tag(model: str) -> str:
    raw = (model or "").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw).strip("._-")
    return safe or "model"

root = Path(sys.argv[1]).resolve()
model = sys.argv[2]
stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
rid = secrets.token_hex(4)
print(root / "distill_reports" / f"pool-{model_tag(model)}-{stamp}-{rid}")
PY
)"
mkdir -p "$RUN_DIR"
: > "$RUN_DIR/prequeued_worker_ids.txt"

HEAD_EXPORTS="ALL,DISTILL_RUN_DIR=${RUN_DIR},DISTILL_PREQUEUED_WORKERS=1,DISTILL_SBATCH_ENV_FILE=${SBATCH_ENV_FILE}"
HEAD_SBATCH=(sbatch --parsable --chdir "$PROJECT_ROOT" --export="$HEAD_EXPORTS")
if [ -n "$SBATCH_ENV_FILE" ] && [ -f "$SBATCH_ENV_FILE" ]; then
  HEAD_SBATCH+=(--environment "$SBATCH_ENV_FILE")
fi
HEAD_SBATCH+=("$SCRIPT_DIR/distill_head.sh")
HEAD_SBATCH+=("${FILTERED_HEAD_ARGS[@]}")

HEAD_JOB_ID_RAW="$("${HEAD_SBATCH[@]}")"
HEAD_JOB_ID="${HEAD_JOB_ID_RAW%%;*}"
echo "Submitted head job: $HEAD_JOB_ID (run_dir=$RUN_DIR)"

WORKER_IDS=()
for slot in $(seq 1 "$NUM_WORKERS"); do
  WORKER_NAME="distill-${MODEL_TAG}-pool-worker-preq-${slot}"
  WORKER_SBATCH=(
    sbatch
    --parsable
    --chdir "$PROJECT_ROOT"
    --dependency="${WORKER_DEPENDENCY_MODE}:${HEAD_JOB_ID}"
    --time "$WORKER_TIME_LIMIT"
    -J "$WORKER_NAME"
  )
  if [ -n "$SBATCH_ENV_FILE" ] && [ -f "$SBATCH_ENV_FILE" ]; then
    WORKER_SBATCH+=(--environment "$SBATCH_ENV_FILE")
  fi
  WORKER_SBATCH+=(
    "$SCRIPT_DIR/distill_worker.sh"
    --run-dir "$RUN_DIR"
    --model "$MODEL_NAME"
    --request-concurrency "$REQUEST_CONCURRENCY"
    --lease-timeout-seconds "$LEASE_TIMEOUT_SECONDS"
    --max-retries-per-shard "$MAX_RETRIES_PER_SHARD"
  )
  if [ -n "$SEED_ARG" ]; then
    WORKER_SBATCH+=(--seed "$SEED_ARG")
  fi
  if [ "$DETERMINISTIC" -eq 1 ]; then
    WORKER_SBATCH+=(--deterministic)
  fi
  if [ "$STRICT_REPRO" -eq 1 ]; then
    WORKER_SBATCH+=(--strict-repro)
  fi
  if [ -n "$MODEL_REVISION_ARG" ]; then
    WORKER_SBATCH+=(--model-revision "$MODEL_REVISION_ARG")
  fi
  if [ -n "$LIMIT_ARG" ]; then
    WORKER_SBATCH+=(--limit "$LIMIT_ARG")
  fi

  WORKER_JOB_RAW="$("${WORKER_SBATCH[@]}")"
  WORKER_JOB_ID="${WORKER_JOB_RAW%%;*}"
  WORKER_IDS+=("$WORKER_JOB_ID")
  echo "$WORKER_JOB_ID" >> "$RUN_DIR/prequeued_worker_ids.txt"
  echo "Submitted worker[$slot]: $WORKER_JOB_ID (dependency=${WORKER_DEPENDENCY_MODE}:${HEAD_JOB_ID})"
done

echo "Head job: $HEAD_JOB_ID"
echo "Workers: ${WORKER_IDS[*]}"
echo "Run dir: $RUN_DIR"
