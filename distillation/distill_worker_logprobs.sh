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
#SBATCH -A a127

ulimit -c 0

DEFAULT_BASE_URL="http://127.0.0.1:8000/v1"
DEFAULT_REQUEST_CONCURRENCY=8
DEFAULT_LEASE_TIMEOUT_SECONDS=7200
DEFAULT_VLLM_GPU_MEMORY_UTILIZATION=0.90
DEFAULT_HEARTBEAT_SECONDS=60
DEFAULT_MAX_RETRIES_PER_SHARD=2

usage() {
  cat <<EOF2
Usage:
  bash $0 --run-dir <path> --model <name> [--request-concurrency C] [--limit N] [--lease-timeout-seconds N] [--heartbeat-seconds N] [--max-retries-per-shard N] [--top-logprobs K]
EOF2
}

parse_args() {
  RUN_DIR=""
  MODEL_NAME=""
  REQUEST_CONCURRENCY="$DEFAULT_REQUEST_CONCURRENCY"
  LIMIT_ARG=""
  LEASE_TIMEOUT_SECONDS="$DEFAULT_LEASE_TIMEOUT_SECONDS"
  HEARTBEAT_SECONDS="$DEFAULT_HEARTBEAT_SECONDS"
  MAX_RETRIES_PER_SHARD="$DEFAULT_MAX_RETRIES_PER_SHARD"
  TOP_LOGPROBS=4

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
      --top-logprobs)
        shift
        TOP_LOGPROBS="${1:-}"
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

wait_for_queue_ready() {
  local max_wait="${1:-600}"
  local start_ts now_ts elapsed
  start_ts="$(date +%s)"
  while true; do
    if [ -f "$RUN_DIR/queue.db" ] && python3 - "$RUN_DIR/queue.db" <<'PY'
import sqlite3
import sys

db_path = sys.argv[1]
conn = sqlite3.connect(db_path)
try:
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='shards'"
    ).fetchone()
    if row and int(row[0]) == 1:
        sys.exit(0)
except Exception:
    pass
finally:
    conn.close()
sys.exit(1)
PY
    then
      return 0
    fi
    now_ts="$(date +%s)"
    elapsed=$((now_ts - start_ts))
    if [ "$elapsed" -ge "$max_wait" ]; then
      echo "Timed out waiting for queue.db/shards table in $RUN_DIR after ${max_wait}s"
      return 1
    fi
    sleep 2
  done
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
      python3 - "$RUN_DIR" "$WORKER_ID" "$shard_id" "$LEASE_TIMEOUT_SECONDS" <<'PY'
import datetime as dt
import sqlite3
import sys
from pathlib import Path

UTC = dt.timezone.utc

def now_utc():
    return dt.datetime.now(UTC)

if __name__ == "__main__":
    run_dir = Path(sys.argv[1]).resolve()
    worker_id = sys.argv[2]
    shard_id = sys.argv[3]
    lease_timeout = int(sys.argv[4])
    db_path = run_dir / "queue.db"

    updated = False
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = now_utc()
        now_str = now.isoformat()
        conn.execute(
            """
            UPDATE shards
            SET status='pending', lease_owner=NULL, lease_expires_utc=NULL
            WHERE status='leased' AND lease_expires_utc < ?
            """,
            (now_str,),
        )
        lease_expires = (now + dt.timedelta(seconds=lease_timeout)).isoformat()
        cur = conn.execute(
            """
            UPDATE shards
            SET lease_expires_utc=?
            WHERE shard_id=? AND status='leased' AND lease_owner=?
            """,
            (lease_expires, shard_id, worker_id),
        )
        updated = cur.rowcount > 0
        conn.commit()
    finally:
        conn.close()

    if updated:
        print("1")
        sys.exit(0)
    print("0")
    sys.exit(1)
PY
    done
  ) &
  HEARTBEAT_PID=$!
}

claim_next_shard() {
  python3 - "$RUN_DIR" "$WORKER_ID" "$LEASE_TIMEOUT_SECONDS" <<'PY'
import datetime as dt
import sqlite3
import sys
from pathlib import Path

UTC = dt.timezone.utc

def now_utc():
    return dt.datetime.now(UTC)

if __name__ == "__main__":
    run_dir = Path(sys.argv[1]).resolve()
    worker_id = sys.argv[2]
    lease_timeout = int(sys.argv[3])
    db_path = run_dir / "queue.db"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = now_utc()
        now_str = now.isoformat()
        conn.execute(
            """
            UPDATE shards
            SET status='pending', lease_owner=NULL, lease_expires_utc=NULL
            WHERE status='leased' AND lease_expires_utc < ?
            """,
            (now_str,),
        )
        row = conn.execute(
            """
            SELECT shard_id, dataset_path, dataset_cache, shard_index, num_shards, attempt
            FROM shards
            WHERE status='pending'
            ORDER BY dataset_path, shard_index
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            conn.commit()
            print("0")
            sys.exit(0)
        shard_id, dataset_path, dataset_cache, shard_index, num_shards, attempt = row
        lease_expires = (now + dt.timedelta(seconds=lease_timeout)).isoformat()
        updated = conn.execute(
            """
            UPDATE shards
            SET status='leased', lease_owner=?, lease_expires_utc=?
            WHERE shard_id=? AND status='pending'
            """,
            (worker_id, lease_expires, shard_id),
        ).rowcount
        conn.commit()
    finally:
        conn.close()

    if not updated:
        print("0")
        sys.exit(0)

    print(
        "\t".join(
            [
                "1",
                str(shard_id),
                str(dataset_path),
                str(dataset_cache),
                str(shard_index),
                str(num_shards),
                str(attempt),
            ]
        )
    )
PY
}

update_shard_state() {
  local action="$1"
  local shard_id="$2"
  local error_message="$3"

  python3 - "$RUN_DIR" "$action" "$WORKER_ID" "$shard_id" "$error_message" "$MAX_RETRIES_PER_SHARD" <<'PY'
import datetime as dt
import sqlite3
import sys
from pathlib import Path

UTC = dt.timezone.utc

def now_utc():
    return dt.datetime.now(UTC)


def append_event(run_dir, worker_id, shard_id, transition, message):
    line = (
        f"{now_utc().isoformat()}\t{worker_id}\t{shard_id}\t{transition}\t"
        f"{message.replace(chr(9), ' ').replace(chr(10), ' ')}\n"
    )
    with (run_dir / "events.log").open("a", encoding="utf-8") as handle:
        handle.write(line)

if __name__ == "__main__":
    run_dir = Path(sys.argv[1]).resolve()
    action = sys.argv[2]
    worker_id = sys.argv[3]
    shard_id = sys.argv[4]
    error_message = sys.argv[5]
    max_retries = int(sys.argv[6])
    db_path = run_dir / "queue.db"

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status, lease_owner, attempt FROM shards WHERE shard_id=?",
            (shard_id,),
        ).fetchone()
        if row is None:
            raise SystemExit(f"Unknown shard id: {shard_id}")
        status, lease_owner, attempt = row
        if status != "leased":
            raise SystemExit(f"Shard {shard_id} is not leased")
        if lease_owner != worker_id:
            raise SystemExit(f"Shard {shard_id} leased by {lease_owner} not {worker_id}")

        if action == "success":
            conn.execute(
                """
                UPDATE shards
                SET status='done', lease_owner=NULL, lease_expires_utc=NULL, last_error=''
                WHERE shard_id=?
                """,
                (shard_id,),
            )
            transition = "done"
            message = "processed successfully"
        else:
            next_attempt = int(attempt or 0) + 1
            clean_err = error_message.strip().replace("\n", " ")
            if next_attempt <= max_retries:
                conn.execute(
                    """
                    UPDATE shards
                    SET status='pending', attempt=?, lease_owner=NULL, lease_expires_utc=NULL, last_error=?
                    WHERE shard_id=?
                    """,
                    (next_attempt, clean_err[:1000], shard_id),
                )
                transition = "retry_pending"
                message = f"attempt={next_attempt}/{max_retries}; {clean_err}"
            else:
                conn.execute(
                    """
                    UPDATE shards
                    SET status='failed', attempt=?, lease_owner=NULL, lease_expires_utc=NULL, last_error=?
                    WHERE shard_id=?
                    """,
                    (next_attempt, clean_err[:1000], shard_id),
                )
                transition = "failed"
                message = f"attempt={next_attempt}; {clean_err}"
        conn.commit()
    finally:
        conn.close()

    append_event(run_dir, worker_id, shard_id, transition, message)
PY
}

run_distill_shard() {
  local -a PYTHON_ARGS
  PYTHON_ARGS=(
    --dataset-cache "$DATASET_CACHE"
    --dataset-path "$DATASET_PATH"
    --base-url "$DEFAULT_BASE_URL"
    --model "$MODEL_NAME"
    --shard-index "$SHARD_INDEX"
    --num-shards "$NUM_SHARDS"
    --request-concurrency "$REQUEST_CONCURRENCY"
    --temperature 0.0
    --top-p 1.0
    --max-tokens 8192
    --top-logprobs "$TOP_LOGPROBS"
    --retries 5
    --retry-base-seconds 1.5
    --timeout-seconds 180.0
  )
  if [ -n "$LIMIT_ARG" ]; then
    PYTHON_ARGS+=(--limit "$LIMIT_ARG")
  fi

  python3 - "${PYTHON_ARGS[@]}" <<'PY'
import argparse
import asyncio
import datetime as dt
import hashlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional


def parse_args():
    parser = argparse.ArgumentParser(description="Distill one dataset shard using Hugging Face datasets primitives.")
    parser.add_argument("--dataset-cache", required=True)
    parser.add_argument("--dataset-path", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--num-shards", type=int, required=True)
    parser.add_argument("--request-concurrency", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=1.0)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--top-logprobs", type=int, default=4)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-base-seconds", type=float, default=1.5)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def model_tag(model: str) -> str:
    raw = (model or "").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw).strip("._-")
    return safe or "model"


def shard_output_path(dataset_path, shard_index, num_shards, model):
    src = Path(dataset_path)
    tag = model_tag(model)
    return src.with_name(
        f"{src.stem}_distillation_{tag}.shard-{shard_index:03d}-of-{num_shards:03d}.jsonl"
    )


def extract_messages(row):
    conversations = row.get("conversations")
    if not isinstance(conversations, list):
        return [], None

    user_message: Optional[str] = None

    for turn in conversations:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("from", "")).strip().lower()
        value = turn.get("value")
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        if role in {"user", "human"} and user_message is None:
            user_message = text
        if user_message is not None:
            break

    if user_message is None:
        return [], None

    messages = []
    messages.append({"role": "user", "content": user_message})
    return messages, user_message


def completion_key(row, prompt_override=None):
    row_id = row.get("id")
    if row_id is not None and str(row_id).strip() != "":
        return f"id:{row_id}"

    prompt = prompt_override
    if prompt is None:
        prompt = row.get("distilled_prompt_used") if isinstance(row.get("distilled_prompt_used"), str) else None
    if prompt is None:
        _messages, prompt = extract_messages(row)
    if prompt:
        return "prompt_sha1:" + hashlib.sha1(prompt.encode("utf-8")).hexdigest()

    source_index = row.get("distilled_source_index")
    return f"source_index:{source_index}"


def render_chat_prompt(tokenizer, messages):
    if not messages:
        return ""
    rendered_messages = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", "")).strip().lower()
        content = message.get("content")
        if role not in {"system", "user"} or not isinstance(content, str):
            continue
        rendered_messages.append({"role": role, "content": content})
    if not rendered_messages:
        return ""

    for idx in range(len(rendered_messages) - 1, -1, -1):
        if rendered_messages[idx]["role"] == "user":
            rendered_messages[idx] = {
                "role": "user",
                "content": rendered_messages[idx]["content"].rstrip()
            }
            break
    return tokenizer.apply_chat_template(
        rendered_messages,
        tokenize=False,
        add_generation_prompt=True,
    )


def _post_completion_request(base_url, payload, timeout_seconds, retries, retry_base_seconds):
    url = f"{base_url.rstrip('/')}/completions"
    body = json.dumps(payload).encode("utf-8")
    last_error = None
    attempts = max(1, int(retries))

    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
                status = getattr(response, "status", None)
                text = response.read().decode("utf-8")
            if status is not None and status >= 400:
                raise RuntimeError(f"Completion HTTP {status}: {text[:500]}")
            return json.loads(text) if text else {}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts:
                break
            sleep_seconds = float(retry_base_seconds) * (2 ** (attempt - 1))
            time.sleep(sleep_seconds)

    raise RuntimeError(
        f"Completion request failed after {attempts} attempts: {last_error}"
    )


def parse_completion_payload(payload: Any):
    if not isinstance(payload, dict):
        return "", None, None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", None, None
    first = choices[0] if isinstance(choices[0], dict) else {}
    text = first.get("text")
    finish_reason = first.get("finish_reason")
    token_logprobs = None
    logprobs_payload = first.get("logprobs")
    if isinstance(logprobs_payload, dict):
        tokens = logprobs_payload.get("tokens")
        token_logprobs_raw = logprobs_payload.get("token_logprobs")
        top_logprobs_raw = logprobs_payload.get("top_logprobs")
        if isinstance(tokens, list):
            token_logprobs = []
            for idx, token in enumerate(tokens):
                item = {
                    "token": token if isinstance(token, str) else str(token),
                    "logprob": None,
                    "top_logprobs": {},
                }
                if isinstance(token_logprobs_raw, list) and idx < len(token_logprobs_raw):
                    value = token_logprobs_raw[idx]
                    if isinstance(value, (int, float)):
                        item["logprob"] = float(value)
                if isinstance(top_logprobs_raw, list) and idx < len(top_logprobs_raw):
                    top_item = top_logprobs_raw[idx]
                    if isinstance(top_item, dict):
                        cleaned = {}
                        for k, v in top_item.items():
                            if isinstance(v, (int, float)):
                                cleaned[str(k)] = float(v)
                        item["top_logprobs"] = cleaned
                token_logprobs.append(item)
    if isinstance(text, str):
        return text.strip(), finish_reason if isinstance(finish_reason, str) else None, token_logprobs
    return "", finish_reason if isinstance(finish_reason, str) else None, token_logprobs


def _single_token_id(tokenizer, token_text: str):
    if not isinstance(token_text, str) or not token_text:
        return None
    try:
        encoded = tokenizer(token_text, add_special_tokens=False)
        ids = encoded.get("input_ids")
        if isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], int):
            return int(ids[0])
    except Exception:  # noqa: BLE001
        return None
    return None


def to_axolotl_kd_logprobs(tokenizer, token_logprobs, top_k: int):
    if not isinstance(token_logprobs, list):
        return None

    kd_rows = []
    keep_k = max(1, int(top_k))
    fallback_token_id = getattr(tokenizer, "unk_token_id", None)
    if not isinstance(fallback_token_id, int):
        fallback_token_id = 0

    for pos in token_logprobs:
        if not isinstance(pos, dict):
            continue
        candidates = []

        token_text = pos.get("token")
        token_lp = pos.get("logprob")
        if isinstance(token_text, str) and isinstance(token_lp, (int, float)):
            candidates.append((token_text, float(token_lp)))

        top = pos.get("top_logprobs")
        if isinstance(top, dict):
            for k, v in top.items():
                if isinstance(k, str) and isinstance(v, (int, float)):
                    candidates.append((k, float(v)))

        # Deduplicate on token id and keep max logprob for each id.
        by_id = {}
        for tok_text, lp in candidates:
            tid = _single_token_id(tokenizer, tok_text)
            if tid is None:
                continue
            prev = by_id.get(tid)
            if prev is None or lp > prev:
                by_id[tid] = lp

        if not by_id:
            # Keep a non-empty row to avoid KD loader failures on empty per-position entries.
            fallback_lp = float(token_lp) if isinstance(token_lp, (int, float)) else -20.0
            by_id[fallback_token_id] = fallback_lp

        sorted_items = sorted(by_id.items(), key=lambda x: x[1], reverse=True)[:keep_k]
        kd_rows.append(
            [{"token": f"token_id:{tid}", "logprob": float(lp)} for tid, lp in sorted_items]
        )

    return kd_rows


async def request_completion(
    base_url,
    timeout_seconds,
    model,
    prompt,
    temperature,
    max_tokens,
    top_p,
    top_logprobs,
    retries,
    retry_base_seconds,
):
    payload = {
        "model": model,
        "prompt": prompt,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "skip_special_tokens": False,
    }
    if top_logprobs is not None and int(top_logprobs) > 0:
        payload["logprobs"] = int(top_logprobs)
    payload["top_p"] = float(top_p)
    loop = asyncio.get_event_loop()
    response_payload = await loop.run_in_executor(
        None,
        lambda: _post_completion_request(base_url, payload, timeout_seconds, retries, retry_base_seconds),
    )
    return parse_completion_payload(response_payload)


async def generate_with_retry(
    base_url,
    timeout_seconds,
    semaphore,
    model,
    prompt,
    temperature,
    max_tokens,
    top_p,
    top_logprobs,
    retries,
    retry_base_seconds,
):
    async with semaphore:
        return await request_completion(
            base_url,
            timeout_seconds,
            model=model,
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            top_logprobs=top_logprobs,
            retries=retries,
            retry_base_seconds=retry_base_seconds,
        )


def load_existing_keys(output_path):
    if not output_path.exists():
        return set()

    keys = set()
    with output_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                keys.add(completion_key(row))
    return keys


def main():
    args = parse_args()
    from datasets import load_dataset, load_from_disk

    temperature = float(args.temperature)
    top_p = float(args.top_p)

    dataset_path = Path(args.dataset_path).resolve()
    cache_path = Path(args.dataset_cache).resolve()
    if not dataset_path.exists():
        print(f"Dataset does not exist: {dataset_path}")
        return 1
    if not cache_path.exists():
        print(f"Dataset cache does not exist: {cache_path}")
        print("Run distill_head.sh first (it prepares caches).")
        return 1
    if args.shard_index < 0 or args.shard_index >= args.num_shards:
        print("--shard-index must be in [0, --num-shards)")
        return 1

    output_path = shard_output_path(dataset_path, args.shard_index, args.num_shards, args.model)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dataset = load_from_disk(str(cache_path))
    shard_dataset = dataset.shard(num_shards=args.num_shards, index=args.shard_index, contiguous=False)
    if args.limit is not None:
        shard_dataset = shard_dataset.select(range(min(args.limit, len(shard_dataset))))

    processed_keys = load_existing_keys(output_path)
    print(f"Source dataset: {dataset_path}")
    print(f"Dataset cache: {cache_path}")
    print(f"Shard: {args.shard_index}/{args.num_shards}")
    print(f"Shard output: {output_path}")
    print(f"Resume processed keys: {len(processed_keys)}")

    pending_dataset = shard_dataset.filter(
        lambda row, idx: completion_key(row) not in processed_keys,
        with_indices=True,
        desc=f"Filtering completed rows for {dataset_path.name} shard {args.shard_index}",
    )
    print(f"Pending rows after filter: {len(pending_dataset)}")

    if len(pending_dataset) == 0:
        print("No pending rows for this shard.")
        return 0

    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to load tokenizer for chat template rendering: {exc}")
        return 1

    semaphore = asyncio.Semaphore(args.request_concurrency)

    async def enrich_row(row, idx):
        messages, prompt_used = extract_messages(row)
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        result = {
            "distilled_prompt_used": prompt_used or "",
            "distilled_model": args.model,
            "distilled_source_path": str(dataset_path),
            "distilled_timestamp_utc": timestamp,
            "distilled_source_index": row.get("distilled_source_index", idx),
            # Keep row schema stable for datasets.map writer finalization.
            "distilled_answer": "",
            "distilled_error": "",
            "distilled_finish_reason": "",
            "distilled_token_logprobs": [],
        }
        if not messages or prompt_used is None:
            result["distilled_error"] = "No user/human prompt found in conversations"
            return result

        rendered_prompt = render_chat_prompt(tokenizer, messages)
        if not rendered_prompt:
            result["distilled_error"] = "Failed to render prompt with apply_chat_template"
            return result

        try:
            answer, finish_reason, token_logprobs = await generate_with_retry(
                args.base_url,
                args.timeout_seconds,
                semaphore,
                model=args.model,
                prompt=rendered_prompt,
                temperature=temperature,
                max_tokens=args.max_tokens,
                top_p=top_p,
                top_logprobs=args.top_logprobs,
                retries=args.retries,
                retry_base_seconds=args.retry_base_seconds,
            )
            result["distilled_answer"] = answer
            result["distilled_finish_reason"] = finish_reason or ""
            if isinstance(token_logprobs, list):
                result["distilled_token_logprobs"] = to_axolotl_kd_logprobs(
                    tokenizer,
                    token_logprobs,
                    args.top_logprobs,
                )
        except Exception as exc:  # noqa: BLE001
            result["distilled_error"] = str(exc)
        return result

    enriched_pending = pending_dataset.map(
        enrich_row,
        with_indices=True,
        batched=False,
        desc=f"Distilling {dataset_path.name} shard {args.shard_index}",
        load_from_cache_file=False,
    )

    # Merge previous shard output and newly processed rows with schema-tolerant
    # dict handling so mixed numeric/null dtypes in source columns do not fail.
    existing_rows = []
    if output_path.exists():
        with output_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    existing_rows.append(row)
    new_rows = [dict(row) for row in enriched_pending]

    merged = {}
    for row in existing_rows:
        merged[completion_key(row)] = row
    for row in new_rows:
        merged[completion_key(row)] = row

    def _source_index(row):
        value = row.get("distilled_source_index")
        try:
            return int(value)
        except Exception:  # noqa: BLE001
            return 0

    combined_rows = sorted(merged.values(), key=_source_index)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in combined_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    error_count = sum(
        1
        for row in combined_rows
        if isinstance(row.get("distilled_error"), str) and row.get("distilled_error").strip()
    )
    written_count = len(combined_rows)
    newly_processed_count = len(new_rows)

    print(
        f"Done {dataset_path.name} shard {args.shard_index}/{args.num_shards}: "
        f"written={written_count} newly_processed={newly_processed_count} failed={error_count}",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
PY
}

parse_args "$@"

if [ -n "${SLURM_SUBMIT_DIR:-}" ] && [ -f "${SLURM_SUBMIT_DIR}/distillation/distill_worker.sh" ]; then
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

wait_for_queue_ready 600

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

  CLAIM_LINE="$(claim_next_shard)"
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

  run_distill_shard &
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
    update_shard_state "success" "$SHARD_ID" ""
    log_event "$SHARD_ID" "shard_done" "completed"
  else
    ERR_MSG="distill_one_dataset exited with rc=$TASK_RC"
    update_shard_state "failure" "$SHARD_ID" "$ERR_MSG" || true
    log_event "$SHARD_ID" "shard_error" "$ERR_MSG"
  fi
done
