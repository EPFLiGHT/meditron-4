#!/usr/bin/env python3

import argparse
import asyncio
import datetime as dt
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from distill_common import DEFAULT_LIST_FILE, load_dataset_paths, model_tag

SAMPLES_PER_DATASET = 2
BASE_URL = "http://127.0.0.1:8000/v1"
VLLM_HOST = "127.0.0.1"
VLLM_PORT = 8000
VLLM_TENSOR_PARALLEL_SIZE = 4
VLLM_GPU_MEMORY_UTILIZATION = float(os.getenv("VLLM_GPU_MEMORY_UTILIZATION", "0.90"))
VLLM_MAX_MODEL_LEN = 32768
REQUEST_CONCURRENCY = 8
TEMPERATURE = 0.2
MAX_TOKENS = 8192
RETRIES = 5
RETRY_BASE_SECONDS = 1.5
TIMEOUT_SECONDS = 180.0



def parse_args():
    parser = argparse.ArgumentParser(
        description="Run non-sharded distillation smoke test: first 2 examples per dataset."
    )
    parser.add_argument("--model", required=True, help="Model name for vLLM and completion requests")
    return parser.parse_args()


def wait_for_vllm_ready(base_url, wait_seconds=900):
    url = f"{base_url.rstrip('/')}/models"
    deadline = time.time() + wait_seconds
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if isinstance(payload, dict) and "data" in payload:
                    print(f"vLLM ready at {base_url}")
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(2)
    raise RuntimeError(f"vLLM not ready at {url} after {wait_seconds}s. Last error: {last_err}")


def extract_user_prompt(row):
    conversations = row.get("conversations")
    if not isinstance(conversations, list):
        return None
    for turn in conversations:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("from", "")).strip().lower()
        if role not in {"user", "human"}:
            continue
        value = turn.get("value")
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


def render_chat_prompt(tokenizer, prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        return ""
    messages = [
        {
            "role": "user",
            "content": prompt.strip()
        }
    ]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )


def _post_completion_request(base_url, payload, timeout_seconds):
    url = f"{base_url.rstrip('/')}/completions"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_completion_payload(payload):
    if not isinstance(payload, dict):
        return "", None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", None
    first = choices[0] if isinstance(choices[0], dict) else {}
    text = first.get("text")
    finish_reason = first.get("finish_reason")
    if isinstance(text, str):
        return text.strip(), finish_reason if isinstance(finish_reason, str) else None
    return "", finish_reason if isinstance(finish_reason, str) else None


async def request_completion(base_url, model, rendered_prompt):
    payload = {
        "model": model,
        "prompt": rendered_prompt,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "skip_special_tokens": False,
    }
    loop = asyncio.get_event_loop()
    response_payload = await loop.run_in_executor(
        None,
        lambda: _post_completion_request(base_url, payload, TIMEOUT_SECONDS),
    )
    return parse_completion_payload(response_payload)


async def generate_with_retry(base_url, semaphore, model, rendered_prompt):
    last_exc = None
    for attempt in range(1, RETRIES + 1):
        try:
            async with semaphore:
                return await request_completion(base_url, model, rendered_prompt)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == RETRIES:
                break
            await asyncio.sleep(RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
    raise RuntimeError(f"Completion failed after {RETRIES} retries: {last_exc}")


def select_examples(dataset_path):
    from datasets import load_dataset

    dataset = load_dataset("json", data_files=str(dataset_path), split="train")
    selected = []
    for idx, row in enumerate(dataset):
        prompt = extract_user_prompt(row)
        if prompt is None:
            continue
        selected.append((idx, prompt))
        if len(selected) >= SAMPLES_PER_DATASET:
            break
    return selected


def build_output_path(script_dir, model):
    out_dir = script_dir / "distilled_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return out_dir / f"distill_test_{model_tag(model)}_{ts}.jsonl"


async def run_generation(model, tokenizer, items):
    semaphore = asyncio.Semaphore(REQUEST_CONCURRENCY)

    async def one(item):
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        result = {
            "source_dataset_path": str(item["dataset_path"]),
            "source_dataset_name": item["dataset_name"],
            "source_row_index": item["source_row_index"],
            "distilled_prompt_used": item["prompt"],
            "distilled_model": model,
            "distilled_timestamp_utc": timestamp,
            "distilled_answer": "",
        }
        if not item["prompt"]:
            result["distilled_error"] = "No user/human prompt found in conversations"
            return result

        rendered_prompt = render_chat_prompt(tokenizer, item["prompt"])
        if not rendered_prompt:
            result["distilled_error"] = "Failed to render prompt with apply_chat_template"
            return result

        try:
            answer, finish_reason = await generate_with_retry(
                BASE_URL,
                semaphore,
                model=model,
                rendered_prompt=rendered_prompt,
            )
            result["distilled_answer"] = answer
            if finish_reason:
                result["distilled_finish_reason"] = finish_reason
        except Exception as exc:  # noqa: BLE001
            result["distilled_error"] = str(exc)
        return result

    tasks = [one(item) for item in items]
    return await asyncio.gather(*tasks)


def main():
    args = parse_args()
    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to load tokenizer for chat template rendering: {exc}")
        return 1

    script_dir = Path(__file__).resolve().parent
    list_file = script_dir / DEFAULT_LIST_FILE
    dataset_paths = load_dataset_paths(str(list_file))
    if not dataset_paths:
        print(f"No dataset paths found in {list_file}")
        return 1

    rows_to_generate = []
    for dataset_path in dataset_paths:
        if not dataset_path.exists():
            print(f"[WARN] Missing dataset, skipping: {dataset_path}")
            continue
        selected = select_examples(dataset_path)
        if len(selected) < SAMPLES_PER_DATASET:
            print(
                f"[WARN] {dataset_path.name}: only {len(selected)} valid prompt rows found "
                f"(expected {SAMPLES_PER_DATASET})"
            )
        for source_row_index, prompt in selected:
            rows_to_generate.append(
                {
                    "dataset_path": dataset_path,
                    "dataset_name": dataset_path.name,
                    "source_row_index": source_row_index,
                    "prompt": prompt,
                }
            )

    if not rows_to_generate:
        print("No rows selected for generation.")
        return 1

    vllm_cmd = [
        "python3",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        args.model,
        "--host",
        VLLM_HOST,
        "--port",
        str(VLLM_PORT),
        "--tensor-parallel-size",
        str(VLLM_TENSOR_PARALLEL_SIZE),
        "--gpu-memory-utilization",
        str(VLLM_GPU_MEMORY_UTILIZATION),
        "--max-model-len",
        str(VLLM_MAX_MODEL_LEN),
    ]

    print(f"Launching vLLM: {' '.join(vllm_cmd)}")
    vllm_proc = subprocess.Popen(vllm_cmd)  # noqa: S603
    try:
        wait_for_vllm_ready(BASE_URL, 900)
        results = asyncio.run(run_generation(args.model, tokenizer, rows_to_generate))
    finally:
        print("Stopping vLLM server...")
        vllm_proc.terminate()
        try:
            vllm_proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            vllm_proc.kill()
            vllm_proc.wait()

    output_path = build_output_path(script_dir, args.model)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in results:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    error_count = sum(1 for row in results if row.get("distilled_error"))
    print(f"Done: wrote {len(results)} rows to {output_path} (errors={error_count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
