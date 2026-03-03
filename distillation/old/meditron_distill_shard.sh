#!/bin/bash
#SBATCH --job-name distill-shard
#SBATCH --output distill_reports/R-%x.%j.err
#SBATCH --error distill_reports/R-%x.%j.err
#SBATCH --nodes 1
#SBATCH --ntasks-per-node 1
#SBATCH --gres gpu:4
#SBATCH --cpus-per-task 64
#SBATCH --partition=normal
#SBATCH --time=5:59:59
#SBATCH --environment ../../.edf/inference.toml
#SBATCH -A a127

ulimit -c 0

DEFAULT_BASE_URL="http://127.0.0.1:8000/v1"
DEFAULT_REQUEST_CONCURRENCY=8
DEFAULT_VLLM_GPU_MEMORY_UTILIZATION=0.90

usage() {
  cat <<EOF
Usage:
  bash $0 --dataset-cache <path> --dataset-path <path> --shard-index I --num-shards N --model <name> [--limit M] [--request-concurrency C] [--seed N] [--deterministic] [--strict-repro] [--model-revision REV]
EOF
}

parse_args() {
  DATASET_CACHE=""
  DATASET_PATH=""
  SHARD_INDEX=""
  NUM_SHARDS=""
  MODEL_NAME=""
  LIMIT_ARG=""
  REQUEST_CONCURRENCY="$DEFAULT_REQUEST_CONCURRENCY"
  SEED_ARG=""
  DETERMINISTIC=0
  STRICT_REPRO=0
  MODEL_REVISION_ARG=""

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dataset-cache)
        shift
        DATASET_CACHE="${1:-}"
        ;;
      --dataset-path)
        shift
        DATASET_PATH="${1:-}"
        ;;
      --shard-index)
        shift
        SHARD_INDEX="${1:-}"
        ;;
      --num-shards)
        shift
        NUM_SHARDS="${1:-}"
        ;;
      --model)
        shift
        MODEL_NAME="${1:-}"
        ;;
      --limit)
        shift
        LIMIT_ARG="${1:-}"
        ;;
      --request-concurrency)
        shift
        REQUEST_CONCURRENCY="${1:-}"
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

  if [ -z "$DATASET_CACHE" ] || [ -z "$DATASET_PATH" ] || [ -z "$SHARD_INDEX" ] || [ -z "$NUM_SHARDS" ] || [ -z "$MODEL_NAME" ]; then
    usage
    exit 1
  fi
  if [ "$STRICT_REPRO" -eq 1 ] && [ -z "$SEED_ARG" ]; then
    echo "--seed is required with --strict-repro"
    exit 1
  fi
}

wait_for_vllm_ready() {
  local base_url="$1"
  local wait_seconds="${2:-900}"
  python3 - "$base_url" "$wait_seconds" <<'PY'
import json
import sys
import time
import urllib.request

base_url = sys.argv[1].rstrip('/')
wait_seconds = int(sys.argv[2])
url = f"{base_url}/models"
deadline = time.time() + wait_seconds
last_err = None
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode('utf-8'))
            if isinstance(payload, dict) and 'data' in payload:
                print(f"vLLM ready at {base_url}")
                sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        last_err = exc
    time.sleep(2)
raise RuntimeError(f"vLLM not ready at {url} after {wait_seconds}s. Last error: {last_err}")
PY
}

if [ -z "${SLURM_JOB_ID:-}" ]; then
  parse_args "$@"
  export PROJECT_ROOT=${SLURM_SUBMIT_DIR:-$(pwd)}
  cd "$PROJECT_ROOT" || exit 1

  SELF_JOB_NAME="$(python3 - "$MODEL_NAME" <<'PY'
import sys
from distill_common import model_tag
print(f"distill-{model_tag(sys.argv[1])}-shard")
PY
)"

  mkdir -p "$PROJECT_ROOT/distill_reports"
  SBATCH_CMD=(
    sbatch
    -J "$SELF_JOB_NAME"
    "$0"
    --dataset-cache "$DATASET_CACHE"
    --dataset-path "$DATASET_PATH"
    --shard-index "$SHARD_INDEX"
    --num-shards "$NUM_SHARDS"
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
  exit 0
fi

parse_args "$@"
export PROJECT_ROOT=${SLURM_SUBMIT_DIR:-$(pwd)}
cd "$PROJECT_ROOT" || exit 1

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi

VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-$DEFAULT_VLLM_GPU_MEMORY_UTILIZATION}"
if [ -z "$MODEL_REVISION_ARG" ] && [ -n "${DISTILL_MODEL_REVISION:-}" ]; then
  MODEL_REVISION_ARG="$DISTILL_MODEL_REVISION"
fi

BASE_URL="$DEFAULT_BASE_URL"

VLLM_CMD=(
  python3 -m vllm.entrypoints.openai.api_server
  --model "$MODEL_NAME"
  --host 127.0.0.1
  --port 8000
  --tensor-parallel-size 4
  --gpu-memory-utilization "$VLLM_GPU_MEMORY_UTILIZATION"
  --max-model-len 32768
)

echo "Launching vLLM: ${VLLM_CMD[*]}"
"${VLLM_CMD[@]}" &
VLLM_PID=$!

cleanup() {
  local rc=$?
  echo "Stopping vLLM server..."
  if [ -n "${VLLM_PID:-}" ] && kill -0 "$VLLM_PID" 2>/dev/null; then
    kill "$VLLM_PID" 2>/dev/null || true
    wait "$VLLM_PID" 2>/dev/null || true
  fi
  exit "$rc"
}
trap cleanup EXIT INT TERM

wait_for_vllm_ready "$BASE_URL" 900

PYTHON_CMD=(
  python3 "$PROJECT_ROOT/distill_one_dataset.py"
  --dataset-cache "$DATASET_CACHE"
  --dataset-path "$DATASET_PATH"
  --base-url "$BASE_URL"
  --model "$MODEL_NAME"
  --shard-index "$SHARD_INDEX"
  --num-shards "$NUM_SHARDS"
  --request-concurrency "$REQUEST_CONCURRENCY"
)
if [ -n "$LIMIT_ARG" ]; then
  PYTHON_CMD+=(--limit "$LIMIT_ARG")
fi
if [ -n "$SEED_ARG" ]; then
  PYTHON_CMD+=(--seed "$SEED_ARG")
fi
if [ "$DETERMINISTIC" -eq 1 ]; then
  PYTHON_CMD+=(--deterministic)
fi
if [ "$STRICT_REPRO" -eq 1 ]; then
  PYTHON_CMD+=(--strict-repro)
fi
if [ -n "$MODEL_REVISION_ARG" ]; then
  PYTHON_CMD+=(--model-revision "$MODEL_REVISION_ARG")
fi

echo "Running shard worker: ${PYTHON_CMD[*]}"
"${PYTHON_CMD[@]}"
