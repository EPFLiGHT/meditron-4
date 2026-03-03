#!/bin/bash
#SBATCH --job-name distill-pool-head
#SBATCH --output distill_reports/R-%x.%j.err
#SBATCH --error distill_reports/R-%x.%j.err
#SBATCH --nodes 1
#SBATCH --ntasks-per-node 1
#SBATCH --cpus-per-task 2
#SBATCH --partition=normal
#SBATCH --time=12:00:00
#SBATCH --environment ../../.edf/inference.toml
#SBATCH -A a127

DEFAULT_LIST_FILE="datasets_to_distill.txt"
DEFAULT_MODEL_NAME="google/medgemma-27b-text-it"
DEFAULT_REQUEST_CONCURRENCY=8
DEFAULT_NUM_WORKERS=4
DEFAULT_MAX_RETRIES_PER_SHARD=2
DEFAULT_LEASE_TIMEOUT_SECONDS=7200
DEFAULT_WORKER_TIME_LIMIT="05:59:59"
DEFAULT_MONITOR_INTERVAL_SECONDS=30

usage() {
  cat <<EOF
Usage:
  bash $0 [list_file] [--model <name>] [--num-workers N] [--request-concurrency N] [--limit N] [--seed N] [--deterministic] [--strict-repro] [--model-revision REV] [--lease-timeout-seconds N] [--max-retries-per-shard N] [--worker-time-limit HH:MM:SS]
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
  LEASE_TIMEOUT_SECONDS="$DEFAULT_LEASE_TIMEOUT_SECONDS"
  MAX_RETRIES_PER_SHARD="$DEFAULT_MAX_RETRIES_PER_SHARD"
  WORKER_TIME_LIMIT="$DEFAULT_WORKER_TIME_LIMIT"
  MONITOR_INTERVAL_SECONDS="$DEFAULT_MONITOR_INTERVAL_SECONDS"

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
      --lease-timeout-seconds)
        shift
        LEASE_TIMEOUT_SECONDS="${1:-}"
        ;;
      --max-retries-per-shard)
        shift
        MAX_RETRIES_PER_SHARD="${1:-}"
        ;;
      --worker-time-limit)
        shift
        WORKER_TIME_LIMIT="${1:-}"
        ;;
      --monitor-interval-seconds)
        shift
        MONITOR_INTERVAL_SECONDS="${1:-}"
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

log_event() {
  local shard_id="$1"
  local transition="$2"
  local message="$3"
  local msg_clean
  msg_clean="$(echo "$message" | tr '\n\t' '  ')"
  printf "%s\t%s\t%s\t%s\t%s\n" "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" "$HEAD_ID" "$shard_id" "$transition" "$msg_clean" >> "$RUN_DIR/events.log"
}

submit_worker() {
  local slot="$1"
  local worker_job_name="${JOB_PREFIX}pool-worker-${slot}"
  SBATCH_CMD=(
    sbatch
    --time "$WORKER_TIME_LIMIT"
    -J "$worker_job_name"
    "$PROJECT_ROOT/meditron_distill_worker.sh"
    --run-dir "$RUN_DIR"
    --model "$MODEL_NAME"
    --request-concurrency "$REQUEST_CONCURRENCY"
    --lease-timeout-seconds "$LEASE_TIMEOUT_SECONDS"
    --max-retries-per-shard "$MAX_RETRIES_PER_SHARD"
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

  local out
  out="$("${SBATCH_CMD[@]}")"
  echo "$out"
  local jid
  jid="$(echo "$out" | awk '{print $4}')"
  if [ -n "$jid" ]; then
    WORKER_JOB_IDS+=("$jid")
    WORKERS_STARTED=$((WORKERS_STARTED + 1))
    log_event "-" "worker_submitted" "slot=$slot job_id=$jid"
  fi
}

active_worker_count() {
  if [ "${#WORKER_JOB_IDS[@]}" -eq 0 ]; then
    echo 0
    return
  fi
  local csv
  csv="$(IFS=,; echo "${WORKER_JOB_IDS[*]}")"
  squeue -h -j "$csv" | wc -l | tr -d ' '
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

JOB_PREFIX="$(python3 - "$MODEL_NAME" <<'PY'
import sys
from distill_common import model_tag
print(f"distill-{model_tag(sys.argv[1])}-")
PY
)"

RUN_DIR="$(python3 - "$PROJECT_ROOT" "$MODEL_NAME" <<'PY'
import datetime as dt
import os
import secrets
import sys
from pathlib import Path
from distill_common import model_tag

root = Path(sys.argv[1]).resolve()
model = sys.argv[2]
stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
rid = secrets.token_hex(4)
run = root / "distill_reports" / f"pool-{model_tag(model)}-{stamp}-{rid}"
print(run)
PY
)"
mkdir -p "$RUN_DIR"
HEAD_ID="head:${SLURM_JOB_ID:-noslurm}:$(hostname):$$"
touch "$RUN_DIR/events.log"
log_event "-" "head_start" "run_dir=$RUN_DIR"

echo "[HEAD] Preparing dataset caches..."
log_event "-" "head_prepare_start" "list_file=$LIST_FILE"
python3 "$PROJECT_ROOT/prepare_distill_datasets.py" "$LIST_FILE"
log_event "-" "head_prepare_done" "list_file=$LIST_FILE"

python3 - "$LIST_FILE" "$RUN_DIR" <<'PY'
import csv
import sys
from pathlib import Path
from distill_common import iter_shard_specs

list_file = sys.argv[1]
run_dir = Path(sys.argv[2]).resolve()
rows = []
for dataset_path, cache_path, num_shards in iter_shard_specs(list_file):
    if not Path(cache_path).exists():
        raise SystemExit(f"Missing prepared cache for {dataset_path}: {cache_path}")
    stem = Path(dataset_path).stem
    for shard_index in range(num_shards):
        shard_id = f"{stem}:s{shard_index + 1:03d}of{num_shards:03d}"
        rows.append({
            "shard_id": shard_id,
            "dataset_path": str(dataset_path),
            "dataset_cache": str(cache_path),
            "shard_index": str(shard_index),
            "num_shards": str(num_shards),
            "attempt": "0",
            "status": "pending",
            "lease_owner": "",
            "lease_expires_utc": "",
            "last_error": "",
        })

rows.sort(key=lambda row: (row["dataset_path"], int(row["shard_index"])))
shards_path = run_dir / "shards.tsv"
with shards_path.open("w", encoding="utf-8", newline="") as handle:
    fieldnames = [
        "shard_id",
        "dataset_path",
        "dataset_cache",
        "shard_index",
        "num_shards",
        "attempt",
        "status",
        "lease_owner",
        "lease_expires_utc",
        "last_error",
    ]
    writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
print(f"Initialized queue with {len(rows)} shards at {shards_path}")
PY

WORKER_JOB_IDS=()
WORKERS_STARTED=0
for slot in $(seq 1 "$NUM_WORKERS"); do
  submit_worker "$slot"
done

while true; do
  WORKERS_ALIVE="$(active_worker_count)"
  python3 "$PROJECT_ROOT/distill_update_shard_state.py" \
    --run-dir "$RUN_DIR" \
    --action write-summary \
    --workers-started "$WORKERS_STARTED" \
    --workers-alive "$WORKERS_ALIVE" >/dev/null

  read -r TOTAL PENDING LEASED DONE FAILED <<<"$(python3 - "$RUN_DIR" <<'PY'
import json
import sys
from pathlib import Path
summary = json.loads((Path(sys.argv[1]).resolve() / "summary.json").read_text(encoding="utf-8"))
print(summary["total"], summary["pending"], summary["leased"], summary["done"], summary["failed"])
PY
)"
  echo "[HEAD] total=$TOTAL pending=$PENDING leased=$LEASED done=$DONE failed=$FAILED workers_alive=$WORKERS_ALIVE workers_started=$WORKERS_STARTED"

  if [ $((DONE + FAILED)) -eq "$TOTAL" ] && [ "$PENDING" -eq 0 ] && [ "$LEASED" -eq 0 ]; then
    log_event "-" "head_complete" "done=$DONE failed=$FAILED"
    break
  fi

  if [ "$PENDING" -gt 0 ] && [ "$WORKERS_ALIVE" -lt "$NUM_WORKERS" ]; then
    TO_ADD=$((NUM_WORKERS - WORKERS_ALIVE))
    for _ in $(seq 1 "$TO_ADD"); do
      submit_worker "r$WORKERS_STARTED"
    done
  fi

  sleep "$MONITOR_INTERVAL_SECONDS"
done

python3 - "$RUN_DIR" <<'PY'
import csv
import sys
from pathlib import Path
run_dir = Path(sys.argv[1]).resolve()
rows = list(csv.DictReader((run_dir / "shards.tsv").open("r", encoding="utf-8"), delimiter="\t"))
failed = [r for r in rows if r.get("status") == "failed"]
print(f"[HEAD] Run dir: {run_dir}")
print(f"[HEAD] Failed shards: {len(failed)}")
for row in failed:
    print(f"[HEAD][FAILED] {row.get('shard_id')} attempts={row.get('attempt')} error={row.get('last_error')}")
PY

FAILED_COUNT="$(python3 - "$RUN_DIR" <<'PY'
import json
import sys
from pathlib import Path
summary = json.loads((Path(sys.argv[1]).resolve() / "summary.json").read_text(encoding="utf-8"))
print(summary.get("failed", 0))
PY
)"
if [ "$FAILED_COUNT" -gt 0 ]; then
  exit 1
fi

MERGE_CMD=(
  python3 "$PROJECT_ROOT/merge_distilled_shards.py"
  --list-file "$LIST_FILE"
  --model "$MODEL_NAME"
)
if [ "$STRICT_REPRO" -eq 1 ]; then
  MERGE_CMD+=(--strict-repro)
fi
echo "[HEAD] Merging distilled shard outputs..."
log_event "-" "head_merge_start" "model=$MODEL_NAME strict_repro=$STRICT_REPRO"
"${MERGE_CMD[@]}"
log_event "-" "head_merge_done" "model=$MODEL_NAME strict_repro=$STRICT_REPRO"
