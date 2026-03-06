#!/bin/bash
#SBATCH --job-name distill-pool-head
#SBATCH --output distill_reports/R-%x.%j.err
#SBATCH --error distill_reports/R-%x.%j.err
#SBATCH --nodes 1
#SBATCH --ntasks-per-node 1
#SBATCH --cpus-per-task 2
#SBATCH --partition=normal
#SBATCH --time=12:00:00
#SBATCH -A a127

set -eo pipefail

DEFAULT_LIST_FILE="datasets_to_distill.txt"
DEFAULT_MODEL_NAME="google/medgemma-27b-text-it"
DEFAULT_REQUEST_CONCURRENCY=8
DEFAULT_NUM_WORKERS=4
DEFAULT_MAX_RETRIES_PER_SHARD=2
DEFAULT_LEASE_TIMEOUT_SECONDS=7200
DEFAULT_WORKER_TIME_LIMIT="05:59:59"
DEFAULT_MONITOR_INTERVAL_SECONDS=30
DEFAULT_SBATCH_ENV_FILE="${HOME}/.edf/inference.toml"

usage() {
  cat <<EOF2
Usage:
  bash $0 [list_file] [--model <name>] [--num-workers N] [--request-concurrency N] [--limit N] [--seed N] [--deterministic] [--strict-repro] [--model-revision REV] [--max-retries-per-shard N] [--lease-timeout-seconds N] [--worker-time-limit HH:MM:SS] [--monitor-interval-seconds N] [--sbatch-environment <path>]
EOF2
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
  MONITOR_INTERVAL_SECONDS="$DEFAULT_MONITOR_INTERVAL_SECONDS"
  SBATCH_ENV_FILE="${DISTILL_SBATCH_ENV_FILE:-$DEFAULT_SBATCH_ENV_FILE}"

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
      --monitor-interval-seconds)
        shift
        MONITOR_INTERVAL_SECONDS="${1:-}"
        ;;
      --sbatch-environment)
        shift
        SBATCH_ENV_FILE="${1:-}"
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
    "$SCRIPT_DIR/distill_worker.sh"
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
  if [ -n "${SBATCH_ENV_FILE:-}" ] && [ -f "${SBATCH_ENV_FILE:-}" ]; then
    SBATCH_CMD+=(--environment "$SBATCH_ENV_FILE")
  fi

  local out
  if ! out="$("${SBATCH_CMD[@]}" 2>&1)"; then
    echo "[ERROR] Failed to submit worker slot=$slot"
    echo "$out"
    return 1
  fi
  echo "$out"
  local jid
  jid="$(echo "$out" | awk '{print $4}')"
  if [ -n "$jid" ]; then
    WORKER_JOB_IDS+=("$jid")
    WORKERS_STARTED=$((WORKERS_STARTED + 1))
    log_event "-" "worker_submitted" "slot=$slot job_id=$jid"
  fi
  return 0
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

known_worker_job_ids() {
  {
    if [ "${#WORKER_JOB_IDS[@]}" -gt 0 ]; then
      printf "%s\n" "${WORKER_JOB_IDS[@]}"
    fi
    if [ -n "${RUN_DIR:-}" ] && [ -f "$RUN_DIR/prequeued_worker_ids.txt" ]; then
      sed -e 's/[[:space:]]//g' "$RUN_DIR/prequeued_worker_ids.txt"
    fi
  } | awk 'NF' | sort -u
}

cancel_workers_on_head_failure() {
  local rc="$1"
  if [ "$rc" -eq 0 ]; then
    return 0
  fi
  if ! command -v scancel >/dev/null 2>&1; then
    echo "[WARN] Head failed (rc=$rc) but scancel is not available; unable to cancel workers."
    return 0
  fi

  local workers_csv
  workers_csv="$(known_worker_job_ids | paste -sd, -)"
  if [ -z "$workers_csv" ]; then
    return 0
  fi

  echo "[HEAD] Head failed (rc=$rc). Cancelling worker jobs: $workers_csv"
  scancel "$workers_csv" >/dev/null 2>&1 || true
  if [ -n "${RUN_DIR:-}" ] && [ -f "$RUN_DIR/events.log" ] && [ -n "${HEAD_ID:-}" ]; then
    log_event "-" "workers_cancelled" "reason=head_failure rc=$rc jobs=$workers_csv"
  fi
}

require_python_module() {
  local module="$1"
  if ! python3 - "$module" <<'PY'
import importlib
import sys
module = sys.argv[1]
importlib.import_module(module)
PY
  then
    echo "[ERROR] Missing Python module: $module"
    echo "Run in your inference environment or install it (e.g., pip install ${module})."
    return 1
  fi
}

parse_args "$@"

if [ -n "${SLURM_SUBMIT_DIR:-}" ] && [ -f "${SLURM_SUBMIT_DIR}/distillation/distill_head.sh" ]; then
  PROJECT_ROOT="$(cd "${SLURM_SUBMIT_DIR}" && pwd)"
  SCRIPT_DIR="$PROJECT_ROOT/distillation"
else
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
cd "$PROJECT_ROOT" || exit 1

if [ -f .env ]; then
  set -o allexport
  source .env
  set +o allexport
fi

if [ "$LIST_FILE" = "$DEFAULT_LIST_FILE" ]; then
  LIST_FILE="$SCRIPT_DIR/$DEFAULT_LIST_FILE"
fi

if [ ! -f "$LIST_FILE" ]; then
  echo "List file not found: $LIST_FILE"
  exit 1
fi

if ! command -v sbatch >/dev/null 2>&1; then
  echo "[ERROR] sbatch is not available in PATH."
  exit 1
fi

if ! command -v squeue >/dev/null 2>&1; then
  echo "[ERROR] squeue is not available in PATH."
  exit 1
fi

if [ -n "${SBATCH_ENV_FILE:-}" ] && [ ! -f "${SBATCH_ENV_FILE:-}" ]; then
  echo "[WARN] SBATCH environment file not found: ${SBATCH_ENV_FILE}. Submissions will proceed without --environment."
fi

require_python_module datasets

if [ -f "$PROJECT_ROOT/scripts/slack_helpers.sh" ]; then
  source "$PROJECT_ROOT/scripts/slack_helpers.sh"
fi

START_TS="$(date +%s)"
START_HUMAN="$(date -Is)"
RUN_NAME="distill-pool-${MODEL_NAME}"
SLACK_REPORTS_DIR="$PROJECT_ROOT/distill_reports"
SLACK_PHASE="distill"
on_head_exit() {
  local rc=$?
  set +e
  cancel_workers_on_head_failure "$rc"
  if declare -F slack_notify >/dev/null 2>&1; then
    slack_notify "$rc" "$SLACK_PHASE"
  fi
  exit "$rc"
}
trap on_head_exit EXIT

JOB_PREFIX="$(python3 - "$MODEL_NAME" <<'PY'
import sys

def model_tag(model: str) -> str:
    raw = (model or "").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw).strip("._-")
    return safe or "model"

print(f"distill-{model_tag(sys.argv[1])}-")
PY
)"

RUN_DIR="${DISTILL_RUN_DIR:-}"
if [ -z "$RUN_DIR" ]; then
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
run = root / "distill_reports" / f"pool-{model_tag(model)}-{stamp}-{rid}"
print(run)
PY
)"
fi
RUN_DIR="$(python3 - "$RUN_DIR" <<'PY'
import sys
from pathlib import Path
print(Path(sys.argv[1]).expanduser().resolve())
PY
)"
mkdir -p "$RUN_DIR"
HEAD_ID="head:${SLURM_JOB_ID:-noslurm}:$(hostname):$$"
: > "$RUN_DIR/events.log"
log_event "-" "head_start" "run_dir=$RUN_DIR"

python3 - "$LIST_FILE" "$RUN_DIR" <<'PY'
import argparse
import sqlite3
from pathlib import Path

def normalize_dataset_path(raw_path: str) -> Path:
    return Path(raw_path.strip()).expanduser().resolve()

def load_dataset_paths(list_file):
    list_path = Path(list_file).expanduser().resolve()
    paths = []
    with list_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            paths.append(normalize_dataset_path(line))
    return paths

def dataset_cache_path(dataset_path):
    src = Path(dataset_path)
    return src.with_name(f"{src.stem}.hf_distill_cache")

def suggested_num_shards(dataset_path):
    name = Path(dataset_path).name
    if name == "medmcqa_with_labels.jsonl":
        return 32
    if name == "afrimedqa_v2_full_15275_with_labels.jsonl":
        return 4
    if name == "medqa_with_labels_5opt.jsonl":
        return 4
    if name == "healthsearchqa_3375_no_labels.jsonl":
        return 2
    if name == "afrimedqa_v1_test_3000_with_labels.jsonl":
        return 2
    return 1

def prepare_caches(list_file):
    from datasets import load_dataset, load_from_disk

    dataset_paths = load_dataset_paths(list_file)
    if not dataset_paths:
        raise SystemExit(f"No dataset paths found in {list_file}")

    built = 0
    skipped = 0
    for dataset_path in dataset_paths:
        if not dataset_path.exists():
            print(f"[WARN] Missing dataset, skipping: {dataset_path}")
            continue
        cache_path = dataset_cache_path(dataset_path)
        if cache_path.exists():
            cached = load_from_disk(str(cache_path))
            print(f"[SKIP] {dataset_path} -> {cache_path} ({len(cached)} rows)")
            skipped += 1
            continue
        dataset = load_dataset("json", data_files=str(dataset_path), split="train")
        dataset = dataset.map(
            lambda _row, idx: {"distilled_source_index": idx},
            with_indices=True,
            desc=f"Annotating source indices for {dataset_path.name}",
            load_from_cache_file=False,
        )
        dataset.save_to_disk(str(cache_path))
        print(f"[OK] {dataset_path} -> {cache_path} ({len(dataset)} rows)")
        built += 1
    print(f"Prepared caches: built={built} skipped={skipped}")


def init_queue(list_file, run_dir):
    rows = []
    for dataset_path in load_dataset_paths(list_file):
        cache_path = dataset_cache_path(dataset_path)
        if not Path(cache_path).exists():
            raise SystemExit(f"Missing prepared cache for {dataset_path}: {cache_path}")
        stem = Path(dataset_path).stem
        num_shards = suggested_num_shards(dataset_path)
        for shard_index in range(num_shards):
            shard_id = f"{stem}:s{shard_index + 1:03d}of{num_shards:03d}"
            rows.append(
                (
                    shard_id,
                    str(dataset_path),
                    str(cache_path),
                    int(shard_index),
                    int(num_shards),
                    0,
                    "pending",
                    None,
                    None,
                    "",
                )
            )

    rows.sort(key=lambda row: (row[1], row[3]))
    db_path = Path(run_dir) / "queue.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS shards (
                shard_id TEXT PRIMARY KEY,
                dataset_path TEXT NOT NULL,
                dataset_cache TEXT NOT NULL,
                shard_index INTEGER NOT NULL,
                num_shards INTEGER NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                lease_owner TEXT,
                lease_expires_utc TEXT,
                last_error TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shards_status ON shards(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shards_lease_expires ON shards(lease_expires_utc)")
        conn.execute("DELETE FROM shards")
        conn.executemany(
            """
            INSERT INTO shards (
                shard_id, dataset_path, dataset_cache, shard_index, num_shards,
                attempt, status, lease_owner, lease_expires_utc, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    print(f"Initialized queue with {len(rows)} shards at {db_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("list_file")
    parser.add_argument("run_dir")
    args = parser.parse_args()
    prepare_caches(args.list_file)
    init_queue(args.list_file, args.run_dir)
PY

WORKER_JOB_IDS=()
WORKERS_STARTED=0
if [ "${DISTILL_PREQUEUED_WORKERS:-0}" = "1" ]; then
  PREQUEUED_FILE="$RUN_DIR/prequeued_worker_ids.txt"
  if [ ! -s "$PREQUEUED_FILE" ]; then
    for _ in $(seq 1 30); do
      [ -s "$PREQUEUED_FILE" ] && break
      sleep 2
    done
  fi
  if [ -s "$PREQUEUED_FILE" ]; then
    while IFS= read -r jid; do
      jid="$(echo "$jid" | tr -d '[:space:]')"
      [ -z "$jid" ] && continue
      WORKER_JOB_IDS+=("$jid")
      WORKERS_STARTED=$((WORKERS_STARTED + 1))
      log_event "-" "worker_registered" "prequeued_job_id=$jid"
    done < "$PREQUEUED_FILE"
    echo "[HEAD] Registered ${#WORKER_JOB_IDS[@]} prequeued worker jobs."
  else
    echo "[WARN] DISTILL_PREQUEUED_WORKERS=1 but no prequeued worker ids file found at $PREQUEUED_FILE; submitting workers from head."
    for slot in $(seq 1 "$NUM_WORKERS"); do
      submit_worker "$slot"
    done
  fi
else
  for slot in $(seq 1 "$NUM_WORKERS"); do
    submit_worker "$slot"
  done
fi

while true; do
  WORKERS_ALIVE="$(active_worker_count)"

python3 - "$RUN_DIR" "$WORKERS_STARTED" "$WORKERS_ALIVE" <<'PY'
import datetime as dt
import json
import sys
import sqlite3
from pathlib import Path

UTC = dt.timezone.utc
DATE_FMT = "%Y-%m-%dT%H:%M:%S.%f%z"

def now_utc():
    return dt.datetime.now(UTC)

def ts(dt_obj):
    return dt_obj.strftime(DATE_FMT)

if __name__ == "__main__":
    run_dir = Path(sys.argv[1]).resolve()
    workers_started = int(sys.argv[2])
    workers_alive = int(sys.argv[3])
    db_path = run_dir / "queue.db"
    summary_path = run_dir / "summary.json"

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(status='pending') as pending,
                SUM(status='leased') as leased,
                SUM(status='done') as done,
                SUM(status='failed') as failed,
                SUM(attempt) as retried
            FROM shards
            """
        ).fetchone()
        total, pending, leased, done, failed, retried = row
        summary = {
            "total": int(total or 0),
            "pending": int(pending or 0),
            "leased": int(leased or 0),
            "done": int(done or 0),
            "failed": int(failed or 0),
            "retried": int(retried or 0),
            "workers_started": workers_started,
            "workers_alive": workers_alive,
            "updated_at_utc": ts(now_utc()),
        }
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, separators=(",", ":")))
    finally:
        conn.close()
PY

  SUMMARY_FIELDS="$(python3 - "$RUN_DIR" <<'PY'
import json
import sys
from pathlib import Path
summary = json.loads((Path(sys.argv[1]).resolve() / "summary.json").read_text(encoding="utf-8"))
print(summary["total"], summary["pending"], summary["leased"], summary["done"], summary["failed"])
PY
)"
  read -r TOTAL PENDING LEASED DONE FAILED <<<"$SUMMARY_FIELDS"

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
import sqlite3
import sys
from pathlib import Path

run_dir = Path(sys.argv[1]).resolve()
db_path = run_dir / "queue.db"
conn = sqlite3.connect(str(db_path))
try:
    rows = conn.execute(
        "SELECT shard_id, attempt, last_error FROM shards WHERE status='failed' ORDER BY dataset_path, shard_index"
    ).fetchall()
finally:
    conn.close()

print(f"[HEAD] Run dir: {run_dir}")
print(f"[HEAD] Failed shards: {len(rows)}")
for shard_id, attempt, last_error in rows:
    print(f"[HEAD][FAILED] {shard_id} attempts={attempt} error={last_error}")
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

python3 - "$LIST_FILE" "$MODEL_NAME" "$STRICT_REPRO" <<'PY'
import argparse
import datetime as dt
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path

def load_dataset_paths(list_file):
    list_path = Path(list_file).expanduser().resolve()
    paths = []
    with list_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            paths.append(Path(line).expanduser().resolve())
    return paths

def model_tag(model: str) -> str:
    raw = (model or "").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw).strip("._-")
    return safe or "model"

def final_output_path(dataset_path, model):
    src = Path(dataset_path)
    return src.with_name(f"{src.stem}_distillation_{model_tag(model)}.jsonl")

def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def git_commit_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None

def completion_key(row):
    row_id = row.get("id")
    if row_id is not None and str(row_id).strip() != "":
        return f"id:{row_id}"
    prompt = row.get("distilled_prompt_used")
    if isinstance(prompt, str) and prompt.strip():
        import hashlib
        return "prompt_sha1:" + hashlib.sha1(prompt.encode("utf-8")).hexdigest()
    return f"source_index:{row.get('distilled_source_index')}"

def _normalize_row_for_merge(row):
    value = row.get("id")
    if value is None:
        row["id"] = None
        return row
    if isinstance(value, float):
        if math.isnan(value):
            row["id"] = None
            return row
        if value.is_integer():
            row["id"] = str(int(value))
            return row
    text = str(value).strip()
    row["id"] = text or None
    return row

def _normalize_scalar_to_string(value):
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            return str(int(value))
    text = str(value).strip()
    return text or None

def _value_dtype(feature):
    return getattr(feature, "dtype", None)


def merge_one(dataset_path, model, strict_repro=False):
    from datasets import Value, concatenate_datasets, load_dataset

    shard_glob = f"{dataset_path.stem}_distillation_{model_tag(model)}.shard-*-of-*.jsonl"
    shard_files = sorted(dataset_path.parent.glob(shard_glob))
    if not shard_files:
        print(f"[WARN] No shard files found for {dataset_path}")
        return 0

    shard_datasets = []
    shard_manifests = []
    repro_hashes = set()
    for path in shard_files:
        if strict_repro:
            manifest_path = path.with_suffix(path.suffix + ".manifest.json")
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                shard_manifests.append(
                    {
                        "path": str(manifest_path),
                        "repro_config_sha256": manifest.get("repro_config_sha256"),
                    }
                )
                value = manifest.get("repro_config_sha256")
                if isinstance(value, str) and value.strip():
                    repro_hashes.add(value.strip())
            else:
                raise RuntimeError(f"Missing shard manifest for strict reproducibility: {manifest_path}")

        shard_ds = load_dataset("json", data_files=str(path), split="train")
        if "id" in shard_ds.column_names:
            shard_ds = shard_ds.map(
                _normalize_row_for_merge,
                desc=f"Normalizing id type in {path.name}",
            )
            shard_ds = shard_ds.cast_column("id", Value("string"))
        shard_datasets.append(shard_ds)

    all_columns = set()
    for shard_ds in shard_datasets:
        all_columns.update(shard_ds.column_names)

    conflict_columns = []
    for column in sorted(all_columns):
        dtypes = set()
        for shard_ds in shard_datasets:
            feature = shard_ds.features.get(column) if column in shard_ds.features else None
            dtype = _value_dtype(feature)
            if dtype is not None:
                dtypes.add(dtype)
        if len(dtypes) > 1:
            conflict_columns.append(column)

    if conflict_columns:
        print(f"[INFO] Normalizing conflicting scalar columns to string: {', '.join(conflict_columns)}")
        normalized = []
        for shard_ds in shard_datasets:
            for column in conflict_columns:
                if column not in shard_ds.column_names:
                    continue
                shard_ds = shard_ds.map(
                    lambda row, c=column: {c: _normalize_scalar_to_string(row.get(c))},
                    desc=f"Normalizing {column} type",
                )
                shard_ds = shard_ds.cast_column(column, Value("string"))
            normalized.append(shard_ds)
        shard_datasets = normalized

    merged = concatenate_datasets(shard_datasets) if len(shard_datasets) > 1 else shard_datasets[0]
    if "distilled_source_index" in merged.column_names:
        merged = merged.sort("distilled_source_index")

    seen_keys = set()
    merged = merged.filter(
        lambda row: completion_key(row) not in seen_keys and not seen_keys.add(completion_key(row)),
        desc=f"Deduplicating merged shards for {dataset_path.name}",
    )

    output_path = final_output_path(dataset_path, model)
    merged.to_json(str(output_path))
    print(f"[OK] Merged {len(shard_files)} shards into {output_path} ({len(merged)} rows)")

    if strict_repro and len(repro_hashes) != 1:
        raise RuntimeError(
            f"Strict reproducibility requires exactly one repro config hash for {dataset_path}, got {sorted(repro_hashes)}"
        )

    if strict_repro:
        manifest = {
            "type": "distillation_merged_manifest",
            "output_path": str(output_path),
            "dataset_path": str(dataset_path),
            "model": model,
            "num_rows": len(merged),
            "num_shards": len(shard_files),
            "shard_files": [str(p) for p in shard_files],
            "shard_manifests": shard_manifests,
            "repro_config_sha256_values": sorted(repro_hashes),
            "strict_repro": True,
            "output_sha256": file_sha256(output_path),
            "git_commit_sha": git_commit_sha(),
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[OK] Wrote merged manifest: {manifest_path}")
    return len(merged)

if __name__ == "__main__":
    list_file = Path(sys.argv[1]).expanduser().resolve()
    model = sys.argv[2]
    strict_repro = sys.argv[3].lower() in {"1", "true", "yes"}

    dataset_paths = load_dataset_paths(list_file)
    if not dataset_paths:
        raise SystemExit("No dataset paths to merge")

    for dataset_path in dataset_paths:
        merge_one(dataset_path, model, strict_repro=strict_repro)
PY
