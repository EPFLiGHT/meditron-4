#!/usr/bin/env python3

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Dict, Optional

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


def model_tag(model: str) -> str:
    raw = (model or "").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw).strip("._-")
    return safe or "model"


def final_output_path(dataset_path, model):
    src = Path(dataset_path)
    return src.with_name(f"{src.stem}_distillation_{model_tag(model)}.jsonl")

LABEL_KEYS = ("true_label", "true_answer_text", "label", "answer")


def parse_args():
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Build reproducible Meditron-4 curated mixture from merged distillation outputs."
    )
    parser.add_argument(
        "--list-file",
        default=str(script_dir / DEFAULT_LIST_FILE),
        help="Dataset list file (default: distillation/datasets_to_distill.txt)",
    )
    parser.add_argument("--distill-model", default="google/medgemma-27b-text-it")
    parser.add_argument("--judge-model", default="google/medgemma-27b-text-it")
    parser.add_argument("--distill-model-revision", default=os.getenv("DISTILL_MODEL_REVISION", ""))
    parser.add_argument("--judge-model-revision", default=os.getenv("JUDGE_MODEL_REVISION", ""))
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--strict-repro", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--judge-max-tokens", type=int, default=32)
    parser.add_argument("--request-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-base-seconds", type=float, default=1.5)
    parser.add_argument("--distill-host", default="127.0.0.1")
    parser.add_argument("--distill-port", type=int, default=8000)
    parser.add_argument("--judge-host", default="127.0.0.1")
    parser.add_argument("--judge-port", type=int, default=8001)
    parser.add_argument("--tensor-parallel-size", type=int, default=4)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.90)
    parser.add_argument("--max-model-len", type=int, default=32768)
    parser.add_argument("--output-dir", default=str(script_dir / "curated_mixtures"))
    parser.add_argument(
        "--version",
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"),
        help="Version suffix used in output filenames",
    )
    return parser.parse_args()


def normalize_generation_settings(args):
    deterministic = bool(args.deterministic or args.strict_repro)
    distill_temperature = 0.0 if deterministic else float(args.temperature)
    top_p = 1.0 if deterministic else None
    return deterministic, distill_temperature, top_p


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def row_count_jsonl(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def git_commit_sha() -> Optional[str]:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:  # noqa: BLE001
        return None


def load_jsonl(path: Path):
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            rows.append(json.loads(raw))
    return rows


def extract_prompt(row: dict) -> Optional[str]:
    value = row.get("distilled_prompt_used")
    if isinstance(value, str) and value.strip():
        return value.strip()

    conversations = row.get("conversations")
    if not isinstance(conversations, list):
        return None

    for turn in conversations:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("from", "")).strip().lower()
        text = turn.get("value")
        if role in {"user", "human"} and isinstance(text, str) and text.strip():
            return text.strip()
    return None


def pick_reference_label(row: dict) -> Optional[str]:
    for key in LABEL_KEYS:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, float) and math.isnan(value):
            continue
        text = str(value).strip()
        if not text or text.lower() in {"none", "null", "nan"}:
            continue
        return text
    return None


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


def post_completion_request(base_url: str, payload: dict, timeout_seconds: float):
    url = f"{base_url.rstrip('/')}/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def completion_with_retry(base_url: str, payload: dict, timeout_seconds: float, retries: int, retry_base_seconds: float):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return parse_completion_payload(post_completion_request(base_url, payload, timeout_seconds))
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == retries:
                break
            time.sleep(retry_base_seconds * (2 ** (attempt - 1)))
    raise RuntimeError(f"Completion failed after {retries} retries: {last_exc}")


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


def render_chat_prompt(tokenizer, text: str) -> str:
    return tokenizer.apply_chat_template(
        [{"role": "user", "content": text}],
        tokenize=False,
        add_generation_prompt=True,
    )


def build_judge_prompt(reference_label: str, distilled_answer: str) -> str:
    answer = (distilled_answer or "").strip()
    return (
        "You are a strict medical QA evaluator.\n"
        "Task: Decide whether the model answer agrees with the reference label.\n"
        "Output only one token: PASS or FAIL.\n\n"
        f"Reference label:\n{reference_label}\n\n"
        f"Model answer:\n{answer}\n"
    )


def load_source_index_rows(dataset_path: Path) -> Dict[int, dict]:
    out = {}
    with dataset_path.open("r", encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
            raw = line.strip()
            if not raw:
                continue
            row = json.loads(raw)
            if isinstance(row, dict):
                out[idx] = row
    return out


def start_vllm(model: str, host: str, port: int, tp: int, gpu_mem: float, max_model_len: int):
    cmd = [
        "python3",
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        model,
        "--host",
        host,
        "--port",
        str(port),
        "--tensor-parallel-size",
        str(tp),
        "--gpu-memory-utilization",
        str(gpu_mem),
        "--max-model-len",
        str(max_model_len),
    ]
    print(f"Launching vLLM: {' '.join(cmd)}", flush=True)
    return subprocess.Popen(cmd)  # noqa: S603


def stop_proc(proc):
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def main():
    args = parse_args()

    if args.max_attempts < 1:
        print("--max-attempts must be >= 1")
        return 1

    deterministic, distill_temperature, top_p = normalize_generation_settings(args)

    distill_revision = (args.distill_model_revision or "").strip() or None
    judge_revision = (args.judge_model_revision or "").strip() or None

    if args.strict_repro:
        if args.seed is None:
            print("--seed is required with --strict-repro")
            return 1
        if not distill_revision or not judge_revision:
            print("--strict-repro requires --distill-model-revision and --judge-model-revision")
            return 1

    try:
        from transformers import AutoTokenizer

        distill_tokenizer = AutoTokenizer.from_pretrained(args.distill_model, trust_remote_code=True)
        if args.judge_model == args.distill_model:
            judge_tokenizer = distill_tokenizer
        else:
            judge_tokenizer = AutoTokenizer.from_pretrained(args.judge_model, trust_remote_code=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to load tokenizer(s): {exc}")
        return 1

    dataset_paths = load_dataset_paths(args.list_file)
    if not dataset_paths:
        print(f"No dataset paths found in {args.list_file}")
        return 1

    distill_base_url = f"http://{args.distill_host}:{args.distill_port}/v1"
    judge_base_url = f"http://{args.judge_host}:{args.judge_port}/v1"

    distill_proc = None
    judge_proc = None

    config = {
        "list_file": str(Path(args.list_file).resolve()),
        "distill_model": args.distill_model,
        "distill_model_revision": distill_revision,
        "judge_model": args.judge_model,
        "judge_model_revision": judge_revision,
        "max_attempts": args.max_attempts,
        "seed": args.seed,
        "deterministic": deterministic,
        "strict_repro": bool(args.strict_repro),
        "distill_temperature": distill_temperature,
        "top_p": top_p,
        "max_tokens": args.max_tokens,
        "judge_temperature": args.judge_temperature,
        "judge_max_tokens": args.judge_max_tokens,
        "request_timeout_seconds": args.request_timeout_seconds,
        "retries": args.retries,
        "retry_base_seconds": args.retry_base_seconds,
    }
    config_sha = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = output_dir / f"meditron4_mixture_{args.version}.jsonl"
    unresolved_jsonl = output_dir / f"meditron4_mixture_{args.version}_unresolved_labeled.jsonl"
    manifest_path = output_dir / f"meditron4_mixture_{args.version}.manifest.json"

    dataset_manifest = []
    curated_rows = []
    unresolved_rows = []

    total_rows = 0
    curated_count = 0
    labeled_rows = 0
    labeled_pass = 0
    unlabeled_rows = 0
    distill_calls = 0
    judge_calls = 0

    try:
        distill_proc = start_vllm(
            args.distill_model,
            args.distill_host,
            args.distill_port,
            args.tensor_parallel_size,
            args.gpu_memory_utilization,
            args.max_model_len,
        )
        wait_for_vllm_ready(distill_base_url, 900)

        if args.judge_model != args.distill_model:
            judge_proc = start_vllm(
                args.judge_model,
                args.judge_host,
                args.judge_port,
                args.tensor_parallel_size,
                args.gpu_memory_utilization,
                args.max_model_len,
            )
            wait_for_vllm_ready(judge_base_url, 900)
        else:
            judge_base_url = distill_base_url

        for dataset_path in sorted(dataset_paths, key=lambda p: str(p)):
            merged_path = final_output_path(dataset_path, args.distill_model)
            merged_manifest_path = merged_path.with_suffix(merged_path.suffix + ".manifest.json")
            if not merged_path.exists():
                print(f"[WARN] Missing merged distillation output, skipping: {merged_path}")
                continue
            if args.strict_repro and not merged_manifest_path.exists():
                print(f"[ERROR] Missing merged manifest in strict mode: {merged_manifest_path}")
                return 1

            merged_rows = load_jsonl(merged_path)
            merged_rows.sort(
                key=lambda r: (
                    int(r.get("distilled_source_index", -1)) if str(r.get("distilled_source_index", "")).strip() else -1,
                    str(r.get("distilled_prompt_used") or ""),
                )
            )

            source_rows = load_source_index_rows(dataset_path)

            merged_manifest = None
            if merged_manifest_path.exists():
                merged_manifest = json.loads(merged_manifest_path.read_text(encoding="utf-8"))
                if args.strict_repro:
                    hashes = merged_manifest.get("repro_config_sha256_values")
                    if not isinstance(hashes, list) or len(hashes) != 1 or not str(hashes[0]).strip():
                        print(f"[ERROR] Invalid merged manifest repro hashes in strict mode: {merged_manifest_path}")
                        return 1

            dataset_manifest.append(
                {
                    "dataset_path": str(dataset_path),
                    "dataset_sha256": file_sha256(dataset_path),
                    "dataset_row_count": row_count_jsonl(dataset_path),
                    "merged_path": str(merged_path),
                    "merged_sha256": file_sha256(merged_path),
                    "merged_row_count": len(merged_rows),
                    "merged_manifest_path": str(merged_manifest_path) if merged_manifest_path.exists() else None,
                    "merged_repro_hashes": (merged_manifest or {}).get("repro_config_sha256_values") if merged_manifest else None,
                }
            )

            for merged_row in merged_rows:
                total_rows += 1
                source_index = merged_row.get("distilled_source_index")
                if source_index is None:
                    if args.strict_repro:
                        print("[ERROR] Missing distilled_source_index in strict mode")
                        return 1
                    continue
                source_index = int(source_index)
                source_row = source_rows.get(source_index) or {}
                prompt = extract_prompt(merged_row) or extract_prompt(source_row)
                if not prompt:
                    if args.strict_repro:
                        print(f"[ERROR] Missing prompt in strict mode: {dataset_path} index={source_index}")
                        return 1
                    continue

                reference_label = pick_reference_label(source_row)
                has_label = reference_label is not None
                if has_label:
                    labeled_rows += 1
                else:
                    unlabeled_rows += 1

                attempts = []
                passed = False
                final_answer = ""
                final_status = "unknown"

                max_attempts = args.max_attempts if has_label else 1
                for attempt_idx in range(1, max_attempts + 1):
                    attempt_seed = None
                    if args.seed is not None:
                        attempt_seed = int(args.seed) + (attempt_idx - 1)

                    distill_prompt = render_chat_prompt(distill_tokenizer, prompt)
                    distill_payload = {
                        "model": args.distill_model,
                        "prompt": distill_prompt,
                        "temperature": distill_temperature,
                        "max_tokens": args.max_tokens,
                        "skip_special_tokens": False,
                    }
                    if attempt_seed is not None:
                        distill_payload["seed"] = attempt_seed
                    if top_p is not None:
                        distill_payload["top_p"] = top_p

                    answer = completion_with_retry(
                        distill_base_url,
                        distill_payload,
                        args.request_timeout_seconds,
                        args.retries,
                        args.retry_base_seconds,
                    )
                    distill_calls += 1

                    decision = None
                    judge_raw = None
                    if has_label:
                        judge_prompt = build_judge_prompt(reference_label, answer)
                        rendered_judge_prompt = render_chat_prompt(judge_tokenizer, judge_prompt)
                        judge_payload = {
                            "model": args.judge_model,
                            "prompt": rendered_judge_prompt,
                            "temperature": args.judge_temperature,
                            "max_tokens": args.judge_max_tokens,
                            "skip_special_tokens": False,
                        }
                        judge_raw = completion_with_retry(
                            judge_base_url,
                            judge_payload,
                            args.request_timeout_seconds,
                            args.retries,
                            args.retry_base_seconds,
                        )
                        judge_calls += 1
                        decision = parse_judge_decision(judge_raw)

                    attempts.append(
                        {
                            "attempt": attempt_idx,
                            "seed": attempt_seed,
                            "distilled_answer": answer,
                            "judge_decision": decision,
                            "judge_raw": judge_raw,
                        }
                    )

                    if not has_label:
                        final_answer = answer
                        final_status = "unknown"
                        passed = True
                        break

                    if decision == "PASS":
                        final_answer = answer
                        final_status = "pass"
                        passed = True
                        break

                if not has_label:
                    row_out = {
                        "conversations": [
                            {"from": "user", "value": prompt},
                            {"from": "assistant", "value": final_answer},
                        ],
                        "source_dataset_path": str(dataset_path),
                        "source_dataset_name": dataset_path.name,
                        "source_row_index": source_index,
                        "distilled_model": args.distill_model,
                        "distilled_model_revision": distill_revision,
                        "judge_model": args.judge_model,
                        "judge_model_revision": judge_revision,
                        "label_agreement": "unknown",
                        "attempt_count": len(attempts),
                        "distillation_attempts": attempts,
                        "mixture_config_sha256": config_sha,
                    }
                    curated_rows.append(row_out)
                    curated_count += 1
                    continue

                if passed:
                    labeled_pass += 1
                    row_out = {
                        "conversations": [
                            {"from": "user", "value": prompt},
                            {"from": "assistant", "value": final_answer},
                        ],
                        "source_dataset_path": str(dataset_path),
                        "source_dataset_name": dataset_path.name,
                        "source_row_index": source_index,
                        "true_label": reference_label,
                        "distilled_model": args.distill_model,
                        "distilled_model_revision": distill_revision,
                        "judge_model": args.judge_model,
                        "judge_model_revision": judge_revision,
                        "label_agreement": final_status,
                        "attempt_count": len(attempts),
                        "distillation_attempts": attempts,
                        "mixture_config_sha256": config_sha,
                    }
                    curated_rows.append(row_out)
                    curated_count += 1
                else:
                    unresolved_rows.append(
                        {
                            "source_dataset_path": str(dataset_path),
                            "source_dataset_name": dataset_path.name,
                            "source_row_index": source_index,
                            "true_label": reference_label,
                            "attempt_count": len(attempts),
                            "distillation_attempts": attempts,
                        }
                    )

    finally:
        print("Stopping vLLM servers...", flush=True)
        stop_proc(judge_proc)
        if distill_proc is not None and (judge_proc is None or distill_proc.pid != judge_proc.pid):
            stop_proc(distill_proc)

    curated_rows.sort(key=lambda r: (r["source_dataset_path"], int(r["source_row_index"]), r["attempt_count"]))

    with output_jsonl.open("w", encoding="utf-8") as handle:
        for row in curated_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    with unresolved_jsonl.open("w", encoding="utf-8") as handle:
        for row in unresolved_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    manifest = {
        "type": "meditron4_mixture_manifest",
        "version": args.version,
        "output_jsonl": str(output_jsonl),
        "output_sha256": file_sha256(output_jsonl),
        "output_row_count": len(curated_rows),
        "unresolved_jsonl": str(unresolved_jsonl),
        "unresolved_sha256": file_sha256(unresolved_jsonl),
        "unresolved_labeled_row_count": len(unresolved_rows),
        "total_input_rows": total_rows,
        "curated_rows": curated_count,
        "labeled_rows": labeled_rows,
        "labeled_pass_rows": labeled_pass,
        "unlabeled_rows": unlabeled_rows,
        "distill_calls": distill_calls,
        "judge_calls": judge_calls,
        "config_sha256": config_sha,
        "config": config,
        "dataset_manifest": dataset_manifest,
        "git_commit_sha": git_commit_sha(),
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote curated mixture: {output_jsonl}")
    print(f"Wrote unresolved labeled rows: {unresolved_jsonl}")
    print(f"Wrote manifest: {manifest_path}")
    print(
        "Summary: "
        f"input_rows={total_rows} curated={curated_count} labeled={labeled_rows} "
        f"labeled_pass={labeled_pass} unresolved_labeled={len(unresolved_rows)} "
        f"unlabeled={unlabeled_rows}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
