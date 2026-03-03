#!/bin/bash
#SBATCH --job-name distill-pool-worker
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
DEFAULT_LEASE_TIMEOUT_SECONDS=7200
DEFAULT_VLLM_GPU_MEMORY_UTILIZATION=0.90
DEFAULT_HEARTBEAT_SECONDS=60
DEFAULT_MAX_RETRIES_PER_SHARD=2

usage() {
  cat <<EOF
Usage:
  bash $0 --run-dir <path> --model <name> [--request-concurrency C] [--limit N] [--seed N] [--deterministic] [--strict-repro] [--model-revision REV] [--lease-timeout-seconds N] [--heartbeat-seconds N] [--max-retries-per-shard N]
EOF
}

parse_args() {
  RUN_DIR=""
  MODEL_NAME=""
  REQUEST_CONCURRENCY="$DEFAULT_REQUEST_CONCURRENCY"
  LIMIT_ARG=""
  SEED_ARG=""
  DETERMINISTIC=0
  STRICT_REPRO=0
  MODEL_REVISION_ARG=""
  LEASE_TIMEOUT_SECONDS="$DEFAULT_LEASE_TIMEOUT_SECONDS"
  HEARTBEAT_SECONDS="$DEFAULT_HEARTBEAT_SECONDS"
  MAX_RETRIES_PER_SHARD="$DEFAULT_MAX_RETRIES_PER_SHARD"

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --run-dir)
        shift
        RUN_DIR="${1:-}"
        ;;
      --model)
        shift
        MODEL_NAME="${1:-}"
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
      --lease-timeout-seconds)
        shift
        LEASE_TIMEOUT_SECONDS="${1:-}"
        ;;
      --heartbeat-seconds)
        shift
        HEARTBEAT_SECONDS="${1:-}"
        ;;
      --max-retries-per-shard)
        shift
        MAX_RETRIES_PER_SHARD="${1:-}"
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

  if [ -z "$RUN_DIR" ] || [ -z "$MODEL_NAME" ]; then
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

log_event() {
  local shard_id="$1"
  local transition="$2"
  local message="$3"
  local msg_clean
  msg_clean="$(echo "$message" | tr '\n\t' '  ')"
  printf "%s\t%s\t%s\t%s\t%s\n" "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" "$WORKER_ID" "$shard_id" "$transition" "$msg_clean" >> "$RUN_DIR/events.log"
}

start_vllm() {
  VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-$DEFAULT_VLLM_GPU_MEMORY_UTILIZATION}"
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
  wait_for_vllm_ready "$DEFAULT_BASE_URL" 900
  log_event "-" "vllm_started" "pid=$VLLM_PID"
}

ensure_vllm_running() {
  if [ -n "${VLLM_PID:-}" ] && kill -0 "$VLLM_PID" 2>/dev/null; then
    return 0
  fi
  log_event "-" "vllm_restart" "restarting after crash or missing process"
  start_vllm
}

start_heartbeat() {
  local shard_id="$1"
  local task_pid="$2"
  (
    while kill -0 "$task_pid" 2>/dev/null; do
      sleep "$HEARTBEAT_SECONDS"
      python3 "$PROJECT_ROOT/distill_claim_next_shard.py" \
        --run-dir "$RUN_DIR" \
        --worker-id "$WORKER_ID" \
        --action heartbeat \
        --shard-id "$shard_id" \
        --lease-timeout-seconds "$LEASE_TIMEOUT_SECONDS" >/dev/null 2>&1 || true
    done
  ) &
  HEARTBEAT_PID=$!
}

parse_args "$@"
export PROJECT_ROOT=${SLURM_SUBMIT_DIR:-$(pwd)}
cd "$PROJECT_ROOT" || exit 1

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi

RUN_DIR="$(python3 - "$RUN_DIR" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"
if [ ! -d "$RUN_DIR" ]; then
  echo "Run dir not found: $RUN_DIR"
  exit 1
fi

WORKER_ID="${SLURM_JOB_ID:-noslurm}:$(hostname):$$"
log_event "-" "worker_start" "request_concurrency=$REQUEST_CONCURRENCY"

cleanup() {
  local rc=$?
  if [ -n "${HEARTBEAT_PID:-}" ] && kill -0 "$HEARTBEAT_PID" 2>/dev/null; then
    kill "$HEARTBEAT_PID" 2>/dev/null || true
    wait "$HEARTBEAT_PID" 2>/dev/null || true
  fi
  if [ -n "${VLLM_PID:-}" ] && kill -0 "$VLLM_PID" 2>/dev/null; then
    echo "Stopping vLLM server..."
    kill "$VLLM_PID" 2>/dev/null || true
    wait "$VLLM_PID" 2>/dev/null || true
  fi
  log_event "-" "worker_exit" "rc=$rc"
  exit "$rc"
}
trap cleanup EXIT INT TERM

start_vllm

while true; do
  ensure_vllm_running || exit 1

  CLAIM_LINE="$(python3 "$PROJECT_ROOT/distill_claim_next_shard.py" \
    --run-dir "$RUN_DIR" \
    --worker-id "$WORKER_ID" \
    --action claim \
    --lease-timeout-seconds "$LEASE_TIMEOUT_SECONDS")"
  CLAIMED="$(echo "$CLAIM_LINE" | cut -f1)"
  if [ "$CLAIMED" != "1" ]; then
    log_event "-" "queue_empty" "no pending shard to claim"
    break
  fi

  SHARD_ID="$(echo "$CLAIM_LINE" | cut -f2)"
  DATASET_PATH="$(echo "$CLAIM_LINE" | cut -f3)"
  DATASET_CACHE="$(echo "$CLAIM_LINE" | cut -f4)"
  SHARD_INDEX="$(echo "$CLAIM_LINE" | cut -f5)"
  NUM_SHARDS="$(echo "$CLAIM_LINE" | cut -f6)"
  ATTEMPT="$(echo "$CLAIM_LINE" | cut -f7)"
  log_event "$SHARD_ID" "start_shard" "attempt=$ATTEMPT"

  PYTHON_CMD=(
    python3 "$PROJECT_ROOT/distill_one_dataset.py"
    --dataset-cache "$DATASET_CACHE"
    --dataset-path "$DATASET_PATH"
    --base-url "$DEFAULT_BASE_URL"
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
  "${PYTHON_CMD[@]}" &
  TASK_PID=$!
  start_heartbeat "$SHARD_ID" "$TASK_PID"
  wait "$TASK_PID"
  TASK_RC=$?
  if [ -n "${HEARTBEAT_PID:-}" ] && kill -0 "$HEARTBEAT_PID" 2>/dev/null; then
    kill "$HEARTBEAT_PID" 2>/dev/null || true
    wait "$HEARTBEAT_PID" 2>/dev/null || true
  fi
  HEARTBEAT_PID=""

  if [ "$TASK_RC" -eq 0 ]; then
    python3 "$PROJECT_ROOT/distill_update_shard_state.py" \
      --run-dir "$RUN_DIR" \
      --action success \
      --worker-id "$WORKER_ID" \
      --shard-id "$SHARD_ID" >/dev/null
    log_event "$SHARD_ID" "shard_done" "completed"
  else
    ERR_MSG="distill_one_dataset exited with rc=$TASK_RC"
    python3 "$PROJECT_ROOT/distill_update_shard_state.py" \
      --run-dir "$RUN_DIR" \
      --action failure \
      --worker-id "$WORKER_ID" \
      --shard-id "$SHARD_ID" \
      --max-retries-per-shard "$MAX_RETRIES_PER_SHARD" \
      --error-message "$ERR_MSG" >/dev/null || true
    log_event "$SHARD_ID" "shard_error" "$ERR_MSG"
  fi
done
