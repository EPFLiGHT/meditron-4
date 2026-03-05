#!/bin/bash

DEFAULT_LIST_FILE="datasets_to_distill.txt"
DEFAULT_MAX_ACTIVE_JOBS=16
DEFAULT_REQUEST_CONCURRENCY=8
DEFAULT_MODEL_NAME="google/medgemma-27b-text-it"
JOB_PREFIX=""
MODEL_NAME=""

usage() {
  cat <<EOF
Usage:
  bash $0 [list_file] [--model <name>] [--max-active-jobs N] [--request-concurrency N] [--limit N] [--seed N] [--deterministic] [--strict-repro] [--model-revision REV]
EOF
}

parse_args() {
  LIST_FILE="$DEFAULT_LIST_FILE"
  MAX_ACTIVE_JOBS="$DEFAULT_MAX_ACTIVE_JOBS"
  REQUEST_CONCURRENCY="$DEFAULT_REQUEST_CONCURRENCY"
  LIMIT_ARG=""
  MODEL_NAME="$DEFAULT_MODEL_NAME"
  SEED_ARG=""
  DETERMINISTIC=0
  STRICT_REPRO=0
  MODEL_REVISION_ARG=""

  if [ -n "${1:-}" ] && [[ "$1" != --* ]]; then
    LIST_FILE="$1"
    shift
  fi

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --max-active-jobs)
        shift
        MAX_ACTIVE_JOBS="${1:-}"
        ;;
      --request-concurrency)
        shift
        REQUEST_CONCURRENCY="${1:-}"
        ;;
      --model)
        shift
        MODEL_NAME="${1:-}"
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

  JOB_PREFIX="$(python3 - "$MODEL_NAME" <<'PY'
import sys
from distill_common import model_tag
print(f"distill-{model_tag(sys.argv[1])}-")
PY
)"
}

count_active_jobs() {
  squeue -u "$USER" -h -o '%j' | awk -v prefix="$JOB_PREFIX" 'index($0, prefix) == 1 {count++} END {print count+0}'
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

DATASET_SPECS=$(python3 - "$LIST_FILE" <<'PY'
import sys
from distill_common import iter_shard_specs
for dataset_path, cache_path, num_shards in iter_shard_specs(sys.argv[1]):
    print(f"{dataset_path}\t{cache_path}\t{num_shards}")
PY
)

if [ -z "$DATASET_SPECS" ]; then
  echo "No dataset specs generated from $LIST_FILE"
  exit 1
fi

mkdir -p "$PROJECT_ROOT/distill_reports"

submitted=0
submitted_job_ids=()
submitted_log_files=()
while IFS=$'\t' read -r dataset_path cache_path num_shards; do
  [ -z "$dataset_path" ] && continue

  if [ ! -d "$cache_path" ]; then
    echo "Missing prepared cache for $dataset_path: $cache_path"
    echo "Run: python3 prepare_distill_datasets.py $LIST_FILE"
    exit 1
  fi

  for (( shard_index=0; shard_index<num_shards; shard_index++ )); do
    while [ "$(count_active_jobs)" -ge "$MAX_ACTIVE_JOBS" ]; do
      sleep 10
    done

    job_name=$(python3 - "$dataset_path" "$shard_index" "$num_shards" "$MODEL_NAME" <<'PY'
import sys
from distill_common import shard_job_name
print(shard_job_name(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]))
PY
)

    SBATCH_CMD=(
      sbatch
      -J "$job_name"
      "$PROJECT_ROOT/old/meditron_distill_shard.sh"
      --dataset-cache "$cache_path"
      --dataset-path "$dataset_path"
      --shard-index "$shard_index"
      --num-shards "$num_shards"
      --model "$MODEL_NAME"
      --request-concurrency "$REQUEST_CONCURRENCY"
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

    SUBMISSION_OUTPUT="$(${SBATCH_CMD[@]})"
    echo "$SUBMISSION_OUTPUT"
    job_id="$(echo "$SUBMISSION_OUTPUT" | awk '{print $4}')"
    if [ -n "$job_id" ]; then
      submitted_job_ids+=("$job_id")
      submitted_log_files+=("$PROJECT_ROOT/distill_reports/R-${job_name}.${job_id}.err")
    fi
    submitted=$((submitted + 1))
  done
done <<< "$DATASET_SPECS"

echo "Submitted shard jobs: $submitted"

if [ "$submitted" -gt 0 ] && [ "${#submitted_log_files[@]}" -gt 0 ]; then
  echo "Auto-tail started for submitted distillation logs..."
  tail -n 0 -F "${submitted_log_files[@]}" &
  TAIL_PID=$!

  cleanup_tail() {
    if [ -n "${TAIL_PID:-}" ]; then
      kill "$TAIL_PID" 2>/dev/null || true
      wait "$TAIL_PID" 2>/dev/null || true
    fi
  }
  trap cleanup_tail EXIT INT TERM

  JOB_IDS_CSV="$(IFS=,; echo "${submitted_job_ids[*]}")"
  while squeue -j "$JOB_IDS_CSV" -h | grep -q .; do
    sleep 10
  done

  cleanup_tail
  trap - EXIT INT TERM
fi
