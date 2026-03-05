#!/usr/bin/env python3

import argparse
import asyncio
import json
import math
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_LIST_FILE = "datasets_to_distill.txt"


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

LABEL_KEYS = ("true_label", "true_answer_text", "label", "answer")
VALID_STATUSES = {"pass", "fail", "unknown"}


def parse_args():
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Batch LM-judge distilled outputs by model suffix and write label_agreement in place."
    )
    parser.add_argument("--model-suffix", required=True, help="Model suffix/tag to match merged distilled files")
    parser.add_argument(
        "--list-file",
        default=str(script_dir / DEFAULT_LIST_FILE),
        help="Dataset list file (default: distillation/datasets_to_distill.txt)",
    )
    parser.add_argument("--judge-model", default="google/medgemma-27b-text-it")
    parser.add_argument("--judge-host", default="127.0.0.1")
    parser.add_argument("--judge-port", type=int, default=8000)
    parser.add_argument("--tensor-parallel-size", type=int, default=4)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.90)
    parser.add_argument("--max-model-len", type=int, default=32768)
    parser.add_argument("--request-concurrency", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-base-seconds", type=float, default=1.5)
    parser.add_argument("--resume", action="store_true", help="Skip rows with existing valid label_agreement")
    return parser.parse_args()


def normalize_suffix(text: str) -> str:
    raw = (text or "").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw)
    safe = re.sub(r"_+", "_", safe).strip("._-")
    return safe or "model"


def alnum_key(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch.isalnum())


def choose_input_files(list_file: str, model_suffix: str):
    dataset_paths = load_dataset_paths(list_file)
    suffix_norm = normalize_suffix(model_suffix)
    suffix_key = alnum_key(suffix_norm)

    selected = []
    missing = []
    warnings = []
    skipped_liveqa = []

    for dataset_path in dataset_paths:
        if "liveqa" in dataset_path.name.lower():
            skipped_liveqa.append(dataset_path)
            continue

        stem = dataset_path.stem
        prefix = f"{stem}_distillation_"
        candidates = []
        for path in dataset_path.parent.glob(f"{stem}_distillation_*.jsonl"):
            name_stem = path.stem
            if not name_stem.startswith(prefix):
                continue
            model_part = name_stem[len(prefix) :]
            if alnum_key(model_part).endswith(suffix_key):
                candidates.append(path)

        if not candidates:
            missing.append(dataset_path)
            continue

        if len(candidates) > 1:
            ordered = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
            selected.append(ordered[0])
            warnings.append(
                f"[WARN] Multiple matches for {dataset_path.name}; using newest: {ordered[0].name}"
            )
        else:
            selected.append(candidates[0])

    return selected, missing, warnings, suffix_norm, skipped_liveqa


def wait_for_vllm_ready(base_url: str, wait_seconds: int = 900):
    url = f"{base_url.rstrip('/')}/models"
    deadline = time.time() + wait_seconds
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if isinstance(payload, dict) and "data" in payload:
                    print(f"vLLM ready at {base_url}", flush=True)
                    return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
        time.sleep(2)
    raise RuntimeError(f"vLLM not ready at {url} after {wait_seconds}s. Last error: {last_err}")


def render_chat_prompt(tokenizer, text: str):
    messages = [{"role": "user", "content": text}]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )


def pick_reference_label(row: dict) -> Optional[str]:
    for key in LABEL_KEYS:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, float):
            if math.isnan(value):
                continue
        text = str(value).strip()
        if not text:
            continue
        if text.lower() in {"none", "null", "nan"}:
            continue
        return text
    return None


def pick_distilled_answer(row: dict, preferred_suffix: str) -> str:
    direct = row.get("distilled_answer")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    suffix = normalize_suffix(preferred_suffix)
    preferred_keys = [
        f"distilled_answer_{suffix}",
        f"distilled_answer_{suffix.replace('-', '_')}",
    ]
    for key in preferred_keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    fallback_keys = sorted(
        key for key in row.keys() if isinstance(key, str) and key.startswith("distilled_answer_")
    )
    for key in fallback_keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def parse_completion_payload(payload):
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    text = first.get("text")
    return text.strip() if isinstance(text, str) else ""


def parse_judge_decision(text: str) -> Optional[str]:
    if not isinstance(text, str):
        return None
    match = re.search(r"\b(PASS|FAIL)\b", text.strip().upper())
    if not match:
        return None
    return match.group(1)


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


async def judge_with_retry(
    *,
    base_url: str,
    timeout_seconds: float,
    model: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    retries: int,
    retry_base_seconds: float,
):
    payload = {
        "model": model,
        "prompt": prompt,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "skip_special_tokens": False,
    }
    loop = asyncio.get_event_loop()
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            response_payload = await loop.run_in_executor(
                None,
                lambda: _post_completion_request(base_url, payload, timeout_seconds),
            )
            return parse_completion_payload(response_payload)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == retries:
                break
            await asyncio.sleep(retry_base_seconds * (2 ** (attempt - 1)))
    raise RuntimeError(f"Judge completion failed after {retries} retries: {last_exc}")


def build_judge_prompt(reference_label: str, distilled_answer: str) -> str:
    answer = (distilled_answer or "").strip()
    return (
        "You are a strict medical QA evaluator.\n"
        "Task: Decide whether the model answer agrees with the reference label.\n"
        "Output only one token: PASS or FAIL.\n\n"
        f"Reference label:\n{reference_label}\n\n"
        f"Model answer:\n{answer}\n"
    )


async def process_file(path: Path, args, tokenizer, base_url: str):
    tmp_path = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    semaphore = asyncio.Semaphore(max(1, args.request_concurrency))

    stats = {
        "rows": 0,
        "pass": 0,
        "fail": 0,
        "unknown": 0,
        "resume_skips": 0,
        "judge_calls": 0,
        "warnings": 0,
    }
    file_warnings = []
    in_flight: Dict[asyncio.Task, int] = {}
    ready: Dict[int, str] = {}
    next_to_write = 0

    async def process_row(idx: int, row: dict) -> Tuple[int, str, int, Optional[str]]:
        if args.resume and row.get("label_agreement") in VALID_STATUSES:
            return idx, json.dumps(row, ensure_ascii=False) + "\n", 0, None

        label = pick_reference_label(row)
        if label is None:
            row["label_agreement"] = "unknown"
            return idx, json.dumps(row, ensure_ascii=False) + "\n", 0, None

        distilled_answer = pick_distilled_answer(row, args.model_suffix)
        prompt = build_judge_prompt(label, distilled_answer)
        rendered_prompt = render_chat_prompt(tokenizer, prompt)
        if not rendered_prompt:
            row["label_agreement"] = "fail"
            return idx, json.dumps(row, ensure_ascii=False) + "\n", 1, "Failed to render judge prompt"

        try:
            async with semaphore:
                raw = await judge_with_retry(
                    base_url=base_url,
                    timeout_seconds=args.timeout_seconds,
                    model=args.judge_model,
                    prompt=rendered_prompt,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                    retries=args.retries,
                    retry_base_seconds=args.retry_base_seconds,
                )
            decision = parse_judge_decision(raw)
            if decision == "PASS":
                row["label_agreement"] = "pass"
            elif decision == "FAIL":
                row["label_agreement"] = "fail"
            else:
                row["label_agreement"] = "fail"
                return idx, json.dumps(row, ensure_ascii=False) + "\n", 1, f"Unparseable judge output: {raw!r}"
        except Exception as exc:  # noqa: BLE001
            row["label_agreement"] = "fail"
            return idx, json.dumps(row, ensure_ascii=False) + "\n", 1, f"Judge request failed: {exc}"

        return idx, json.dumps(row, ensure_ascii=False) + "\n", 1, None

    async def flush_done(writer):
        nonlocal next_to_write
        if not in_flight:
            return
        done, _pending = await asyncio.wait(in_flight.keys(), return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            idx = in_flight.pop(task)
            out_idx, out_line, judge_called, warning = task.result()
            ready[out_idx] = out_line
            stats["judge_calls"] += judge_called
            if warning:
                stats["warnings"] += 1
                file_warnings.append(f"[WARN] {path.name} row {idx}: {warning}")

        while next_to_write in ready:
            writer.write(ready.pop(next_to_write))
            next_to_write += 1

    try:
        with path.open("r", encoding="utf-8") as reader, tmp_path.open("w", encoding="utf-8") as writer:
            for idx, line in enumerate(reader):
                stats["rows"] += 1
                raw = line.strip()
                if not raw:
                    row = {}
                else:
                    row = json.loads(raw)
                    if not isinstance(row, dict):
                        row = {"value": row}

                if args.resume and row.get("label_agreement") in VALID_STATUSES:
                    stats["resume_skips"] += 1

                task = asyncio.create_task(process_row(idx, row))
                in_flight[task] = idx
                if len(in_flight) >= max(1, args.request_concurrency * 4):
                    await flush_done(writer)

            while in_flight:
                await flush_done(writer)

            writer.flush()
            os.fsync(writer.fileno())

        os.replace(str(tmp_path), str(path))
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    with path.open("r", encoding="utf-8") as reader:
        for line in reader:
            raw = line.strip()
            if not raw:
                continue
            row = json.loads(raw)
            status = row.get("label_agreement")
            if status == "pass":
                stats["pass"] += 1
            elif status == "fail":
                stats["fail"] += 1
            elif status == "unknown":
                stats["unknown"] += 1

    return stats, file_warnings


def main():
    args = parse_args()
    selected_files, missing, match_warnings, suffix_norm, skipped_liveqa = choose_input_files(
        args.list_file, args.model_suffix
    )

    print(f"Model suffix input: {args.model_suffix}")
    print(f"Normalized suffix: {suffix_norm}")
    print(f"Dataset list file: {Path(args.list_file).resolve()}")
    print(f"Matched files: {len(selected_files)}  Missing datasets: {len(missing)}")
    print(f"Skipped liveqa datasets: {len(skipped_liveqa)}")
    for warning in match_warnings:
        print(warning)

    if missing:
        print("[INFO] Missing merged outputs (skipped):")
        for dataset_path in missing:
            print(f"  - {dataset_path}")

    if skipped_liveqa:
        print("[INFO] liveqa datasets skipped by policy:")
        for dataset_path in skipped_liveqa:
            print(f"  - {dataset_path}")

    if not selected_files:
        print("[INFO] No matching distilled outputs found. Nothing to do.")
        return 0

    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(args.judge_model, trust_remote_code=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to load tokenizer for judge model: {exc}")
        return 1

    base_url = f"http://{args.judge_host}:{args.judge_port}/v1"
    vllm_cmd = [
        "python3",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        args.judge_model,
        "--host",
        args.judge_host,
        "--port",
        str(args.judge_port),
        "--tensor-parallel-size",
        str(args.tensor_parallel_size),
        "--gpu-memory-utilization",
        str(args.gpu_memory_utilization),
        "--max-model-len",
        str(args.max_model_len),
    ]

    print(f"Launching vLLM judge: {' '.join(vllm_cmd)}", flush=True)
    vllm_proc = subprocess.Popen(vllm_cmd)  # noqa: S603

    summary = {
        "files": 0,
        "rows": 0,
        "pass": 0,
        "fail": 0,
        "unknown": 0,
        "resume_skips": 0,
        "judge_calls": 0,
        "warnings": len(match_warnings),
        "file_failures": 0,
    }
    all_warnings = list(match_warnings)
    failed_files = []

    try:
        wait_for_vllm_ready(base_url, 900)
        for path in selected_files:
            print(f"[INFO] Processing {path}", flush=True)
            try:
                file_stats, file_warnings = asyncio.run(process_file(path, args, tokenizer, base_url))
                summary["files"] += 1
                for key in ("rows", "pass", "fail", "unknown", "resume_skips", "judge_calls", "warnings"):
                    summary[key] += file_stats[key]
                all_warnings.extend(file_warnings)
                print(
                    "[OK] "
                    f"{path.name}: rows={file_stats['rows']} pass={file_stats['pass']} "
                    f"fail={file_stats['fail']} unknown={file_stats['unknown']} "
                    f"judge_calls={file_stats['judge_calls']} resume_skips={file_stats['resume_skips']} "
                    f"warnings={file_stats['warnings']}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                summary["file_failures"] += 1
                failed_files.append((path, str(exc)))
                print(f"[ERROR] Failed {path.name}: {exc}", flush=True)
                print("[INFO] Continuing with next dataset.", flush=True)
    finally:
        print("Stopping vLLM judge server...", flush=True)
        vllm_proc.terminate()
        try:
            vllm_proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            vllm_proc.kill()
            vllm_proc.wait()

    print("\n=== Judge Summary ===")
    print(f"Processed files: {summary['files']}")
    print(f"Rows: {summary['rows']}")
    print(f"label_agreement pass: {summary['pass']}")
    print(f"label_agreement fail: {summary['fail']}")
    print(f"label_agreement unknown: {summary['unknown']}")
    print(f"Judge calls: {summary['judge_calls']}")
    print(f"Resume skips: {summary['resume_skips']}")
    print(f"Missing merged outputs skipped: {len(missing)}")
    print(f"Warnings: {summary['warnings']}")
    print(f"Dataset file failures: {summary['file_failures']}")

    if all_warnings:
        print("\nWarnings:")
        for warning in all_warnings:
            print(warning)

    if failed_files:
        print("\nFailed files:")
        for path, err in failed_files:
            print(f"- {path}: {err}")

    return 1 if failed_files else 0


if __name__ == "__main__":
    raise SystemExit(main())
