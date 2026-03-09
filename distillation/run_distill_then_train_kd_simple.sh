#!/bin/bash
set -euo pipefail

# Minimal no-arg orchestrator:
# 1) submit distillation
# 2) wait for terminal state
# 3) submit training only if distillation completed

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# -------- Distillation command (hardcoded) --------
INPUT_JSONL="/users/theimer/meditron-4/meditron_4_merged_all_source.jsonl"
MODEL_NAME="google/medgemma-27b-text-it"
NUM_WORKERS=4
NUM_SHARDS=48
REQUEST_CONCURRENCY=8
TOP_LOGPROBS=4

# -------- Training command (hardcoded) --------
KD_CONFIG="/users/theimer/meditron-4/axolotl_config/kd_llama-3-1_medgemma_logprobs.yaml"
TRAIN_COMMAND="axolotl train $KD_CONFIG"

# -------- Slurm defaults --------
SBATCH_ENV_FILE="${DISTILL_SBATCH_ENV_FILE:-$HOME/.edf/inference.toml}"
POLL_SECONDS=30

if [ ! -f "$INPUT_JSONL" ]; then
  echo "Missing input JSONL: $INPUT_JSONL"
  exit 1
fi
if [ ! -f "$KD_CONFIG" ]; then
  echo "Missing KD config: $KD_CONFIG"
  exit 1
fi
if ! command -v sbatch >/dev/null 2>&1; then
  echo "sbatch not found in PATH"
  exit 1
fi
if ! command -v sacct >/dev/null 2>&1; then
  echo "sacct not found in PATH"
  exit 1
fi

NORMALIZED_INPUT_JSONL="$PROJECT_ROOT/meditron_4_merged_all_source.idstr.jsonl"
echo "Normalizing input JSONL id field -> string/null ..."
python3 - "$INPUT_JSONL" "$NORMALIZED_INPUT_JSONL" <<'PY'
import json
import math
import sys
from pathlib import Path

src = Path(sys.argv[1]).resolve()
dst = Path(sys.argv[2]).resolve()

written = 0
with src.open("r", encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
    for raw in fin:
        line = raw.strip()
        if not line:
            continue
        row = json.loads(line)
        out = {}
        if isinstance(row, dict):
            v = row.get("id")
            if v is None:
                out["id"] = None
            elif isinstance(v, float):
                if math.isnan(v):
                    out["id"] = None
                elif v.is_integer():
                    out["id"] = str(int(v))
                else:
                    out["id"] = str(v)
            else:
                s = str(v).strip()
                out["id"] = s if s else None
            conv = row.get("conversations")
            out["conversations"] = conv if isinstance(conv, list) else []
        else:
            out["id"] = None
            out["conversations"] = []
        fout.write(json.dumps(out, ensure_ascii=False) + "\n")
        written += 1
print(f"Normalized rows: {written}")
print(f"Output: {dst}")
PY
INPUT_JSONL="$NORMALIZED_INPUT_JSONL"

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

echo "Submitting distillation head..."
DISTILL_CMD=(
  sbatch
  --parsable
  --chdir "$PROJECT_ROOT"
  --export "ALL,DISTILL_RUN_DIR=$RUN_DIR"
)
if [ -f "$SBATCH_ENV_FILE" ]; then
  DISTILL_CMD+=(--environment "$SBATCH_ENV_FILE")
fi
DISTILL_CMD+=(
  "$PROJECT_ROOT/distillation/distill_head_logprobs.sh"
  "$INPUT_JSONL"
  --model "$MODEL_NAME"
  --num-workers "$NUM_WORKERS"
  --num-shards "$NUM_SHARDS"
  --request-concurrency "$REQUEST_CONCURRENCY"
  --top-logprobs "$TOP_LOGPROBS"
)

HEAD_JOB_RAW="$("${DISTILL_CMD[@]}")"
HEAD_JOB_ID="${HEAD_JOB_RAW%%;*}"
echo "Head job: $HEAD_JOB_ID"
echo "Run dir: $RUN_DIR"

terminal_state() {
  case "$1" in
    COMPLETED|FAILED|CANCELLED|TIMEOUT|OUT_OF_MEMORY|NODE_FAIL|PREEMPTED|BOOT_FAIL|DEADLINE) return 0 ;;
    *) return 1 ;;
  esac
}

normalize_state() {
  echo "$1" | awk '{print $1}' | sed 's/+.*$//'
}

echo "Waiting for distillation completion..."
FINAL_STATE=""
while true; do
  RAW_STATE="$(sacct -j "$HEAD_JOB_ID" --format=State --noheader 2>/dev/null | head -n1 | xargs || true)"
  STATE="$(normalize_state "${RAW_STATE:-UNKNOWN}")"
  if [ -f "$RUN_DIR/summary.json" ]; then
    python3 - "$RUN_DIR/summary.json" "$STATE" <<'PY'
import json
import sys
from pathlib import Path

p = Path(sys.argv[1])
state = sys.argv[2]
try:
    s = json.loads(p.read_text())
    print(
        f"[DISTILL] state={state} total={s.get('total',0)} "
        f"pending={s.get('pending',0)} leased={s.get('leased',0)} "
        f"done={s.get('done',0)} failed={s.get('failed',0)}"
    )
except Exception:
    print(f"[DISTILL] state={state} summary=unavailable")
PY
  else
    echo "[DISTILL] state=$STATE summary=not_ready"
  fi

  if terminal_state "$STATE"; then
    FINAL_STATE="$STATE"
    break
  fi
  sleep "$POLL_SECONDS"
done

echo "Distillation final state: $FINAL_STATE"
if [ "$FINAL_STATE" != "COMPLETED" ]; then
  echo "Distillation did not complete successfully. Training not launched."
  exit 1
fi

echo "Submitting training job..."
TRAIN_SBATCH=(
  sbatch
  --parsable
  --chdir "$PROJECT_ROOT"
  --job-name "meditron-kd-train"
  --time "24:00:00"
  --gres "gpu:4"
  --cpus-per-task "64"
  --partition "normal"
  -A "a127"
)
if [ -f "$SBATCH_ENV_FILE" ]; then
  TRAIN_SBATCH+=(--environment "$SBATCH_ENV_FILE")
fi
TRAIN_SBATCH+=(--wrap "cd '$PROJECT_ROOT' && $TRAIN_COMMAND")

TRAIN_JOB_RAW="$("${TRAIN_SBATCH[@]}")"
TRAIN_JOB_ID="${TRAIN_JOB_RAW%%;*}"
echo "Training job submitted: $TRAIN_JOB_ID"
echo "Training command: $TRAIN_COMMAND"
