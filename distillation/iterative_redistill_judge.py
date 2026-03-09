#!/usr/bin/env python3

import argparse
import asyncio
import datetime as dt
import json
import math
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


LABEL_KEYS = ("true_label", "true_answer_text", "label", "answer")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Iteratively re-distill rows with label_agreement=fail, then re-judge rows with "
            "empty label_agreement, up to max turns or until fail rate drops below threshold."
        )
    )
    parser.add_argument("--input-jsonl", required=True, help="Input JSONL path")
    parser.add_argument("--output-jsonl", required=True, help="Output JSONL path (rewritten file)")
    parser.add_argument("--distill-model", required=True, help="Model used for re-distillation")
    parser.add_argument("--judge-model", required=True, help="Model used for label agreement judging")
    parser.add_argument("--max-turns", type=int, default=5, help="Maximum iterative turns")
    parser.add_argument(
        "--fail-threshold",
        type=float,
        default=0.05,
        help="Early stop if fail/(pass+fail) is strictly below this threshold",
    )
    parser.add_argument("--report-json", default="", help="Optional sidecar report JSON path")

    parser.add_argument("--distill-host", default="127.0.0.1")
    parser.add_argument("--distill-port", type=int, default=8000)
    parser.add_argument("--judge-host", default="127.0.0.1")
    parser.add_argument("--judge-port", type=int, default=8001)
    parser.add_argument("--tensor-parallel-size", type=int, default=4)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.90)
    parser.add_argument("--max-model-len", type=int, default=32768)

    parser.add_argument("--request-concurrency", type=int, default=8)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-base-seconds", type=float, default=1.5)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)

    parser.add_argument("--distill-temperature", type=float, default=0.2)
    parser.add_argument("--distill-max-tokens", type=int, default=8192)
    parser.add_argument("--distill-seed", type=int, default=None)
    parser.add_argument("--distill-top-p", type=float, default=None)

    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--judge-max-tokens", type=int, default=32)
    return parser.parse_args()


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


def extract_messages(row: dict):
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
    return [{"role": "user", "content": user_message}], user_message


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
                "content": rendered_messages[idx]["content"].rstrip(),
            }
            break
    return tokenizer.apply_chat_template(
        rendered_messages,
        tokenize=False,
        add_generation_prompt=True,
    )


def render_single_user_prompt(tokenizer, text: str):
    messages = [{"role": "user", "content": text}]
    return tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )


def parse_completion_payload(payload: Any) -> Tuple[str, Optional[str]]:
    if not isinstance(payload, dict):
        return "", None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", None
    first = choices[0] if isinstance(choices[0], dict) else {}
    text = first.get("text")
    finish_reason = first.get("finish_reason")
    return (
        text.strip() if isinstance(text, str) else "",
        finish_reason if isinstance(finish_reason, str) else None,
    )


def parse_judge_decision(text: str) -> Optional[str]:
    if not isinstance(text, str):
        return None
    match = re.search(r"\b(PASS|FAIL)\b", text.strip().upper())
    if not match:
        return None
    return match.group(1)


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


def build_judge_prompt(reference_label: str, distilled_answer: str) -> str:
    answer = (distilled_answer or "").strip()
    return (
        "You are a strict medical QA evaluator.\n"
        "Task: Decide whether the model answer agrees with the reference label.\n"
        "Output only one token: PASS or FAIL.\n\n"
        f"Reference label:\n{reference_label}\n\n"
        f"Model answer:\n{answer}\n"
    )


def _post_completion_request(base_url: str, payload: dict, timeout_seconds: float):
    url = f"{base_url.rstrip('/')}/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


async def request_with_retry(
    *,
    base_url: str,
    payload: dict,
    timeout_seconds: float,
    retries: int,
    retry_base_seconds: float,
):
    loop = asyncio.get_event_loop()
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: _post_completion_request(base_url, payload, timeout_seconds),
            )
            return parse_completion_payload(resp)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == retries:
                break
            await asyncio.sleep(retry_base_seconds * (2 ** (attempt - 1)))
    raise RuntimeError(f"Completion failed after {retries} retries: {last_exc}")


def load_jsonl_rows(path: Path) -> Tuple[List[dict], int]:
    rows: List[dict] = []
    invalid_lines = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            raw = raw_line.strip()
            if not raw:
                rows.append({})
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                invalid_lines += 1
                rows.append(
                    {
                        "distilled_answer": "",
                        "label_agreement": "unknown",
                        "distilled_error": f"Invalid JSON at line {line_no}",
                    }
                )
                continue
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                rows.append({"value": obj, "label_agreement": "unknown"})
    return rows, invalid_lines


def write_jsonl_rows(path: Path, rows: List[dict]):
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(tmp), str(path))


def status_counts(rows: List[dict]) -> Dict[str, int]:
    out = {"pass": 0, "fail": 0, "unknown": 0, "empty": 0}
    for row in rows:
        status = row.get("label_agreement")
        text = "" if status is None else str(status).strip().lower()
        if text == "pass":
            out["pass"] += 1
        elif text == "fail":
            out["fail"] += 1
        elif text == "unknown":
            out["unknown"] += 1
        elif text == "":
            out["empty"] += 1
        else:
            out["unknown"] += 1
    return out


def compute_fail_rate(counts: Dict[str, int]) -> Optional[float]:
    judged = counts["pass"] + counts["fail"]
    if judged <= 0:
        return None
    return counts["fail"] / judged


async def redistill_fail_rows(
    rows: List[dict],
    *,
    tokenizer,
    base_url: str,
    model: str,
    concurrency: int,
    timeout_seconds: float,
    retries: int,
    retry_base_seconds: float,
    temperature: float,
    max_tokens: int,
    seed: Optional[int],
    top_p: Optional[float],
) -> Dict[str, int]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    stats = {
        "target_fail_rows": 0,
        "redistill_calls": 0,
        "redistill_success": 0,
        "redistill_errors": 0,
        "prompt_missing": 0,
    }

    async def process_idx(idx: int):
        row = rows[idx]
        stats["target_fail_rows"] += 1
        messages, prompt_used = extract_messages(row)
        timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
        row["distilled_model"] = model
        row["distilled_timestamp_utc"] = timestamp
        row["distilled_prompt_used"] = prompt_used or ""

        if not messages:
            row["distilled_answer"] = ""
            row["distilled_error"] = "No user/human prompt found in conversations"
            row["label_agreement"] = ""
            stats["prompt_missing"] += 1
            return

        rendered_prompt = render_chat_prompt(tokenizer, messages)
        if not rendered_prompt:
            row["distilled_answer"] = ""
            row["distilled_error"] = "Failed to render prompt with apply_chat_template"
            row["label_agreement"] = ""
            stats["prompt_missing"] += 1
            return

        payload = {
            "model": model,
            "prompt": rendered_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "skip_special_tokens": False,
        }
        if seed is not None:
            payload["seed"] = int(seed)
        if top_p is not None:
            payload["top_p"] = float(top_p)

        try:
            async with semaphore:
                answer, finish_reason = await request_with_retry(
                    base_url=base_url,
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                    retries=retries,
                    retry_base_seconds=retry_base_seconds,
                )
            stats["redistill_calls"] += 1
            row["distilled_answer"] = answer
            if finish_reason:
                row["distilled_finish_reason"] = finish_reason
            row.pop("distilled_error", None)
            row["label_agreement"] = ""
            stats["redistill_success"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["redistill_calls"] += 1
            row["distilled_answer"] = ""
            row["distilled_error"] = str(exc)
            row["label_agreement"] = ""
            stats["redistill_errors"] += 1

    indices = []
    for i, row in enumerate(rows):
        status = str(row.get("label_agreement") or "").strip().lower()
        if status == "fail":
            indices.append(i)

    tasks = [asyncio.create_task(process_idx(i)) for i in indices]
    if tasks:
        await asyncio.gather(*tasks)
    return stats


async def judge_empty_rows(
    rows: List[dict],
    *,
    tokenizer,
    base_url: str,
    model: str,
    concurrency: int,
    timeout_seconds: float,
    retries: int,
    retry_base_seconds: float,
    temperature: float,
    max_tokens: int,
) -> Dict[str, int]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    stats = {
        "target_empty_rows": 0,
        "judge_calls": 0,
        "judge_unparseable": 0,
        "judge_errors": 0,
        "judge_no_label": 0,
        "set_pass": 0,
        "set_fail": 0,
        "set_unknown": 0,
    }

    async def process_idx(idx: int):
        row = rows[idx]
        stats["target_empty_rows"] += 1
        label = pick_reference_label(row)
        if label is None:
            row["label_agreement"] = "unknown"
            stats["judge_no_label"] += 1
            stats["set_unknown"] += 1
            return

        distilled_answer = str(row.get("distilled_answer") or "").strip()
        prompt = build_judge_prompt(label, distilled_answer)
        rendered_prompt = render_single_user_prompt(tokenizer, prompt)
        if not rendered_prompt:
            row["label_agreement"] = "fail"
            stats["set_fail"] += 1
            stats["judge_unparseable"] += 1
            return

        payload = {
            "model": model,
            "prompt": rendered_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "skip_special_tokens": False,
        }
        try:
            async with semaphore:
                raw, _finish_reason = await request_with_retry(
                    base_url=base_url,
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                    retries=retries,
                    retry_base_seconds=retry_base_seconds,
                )
            stats["judge_calls"] += 1
            decision = parse_judge_decision(raw)
            if decision == "PASS":
                row["label_agreement"] = "pass"
                stats["set_pass"] += 1
            elif decision == "FAIL":
                row["label_agreement"] = "fail"
                stats["set_fail"] += 1
            else:
                row["label_agreement"] = "fail"
                stats["set_fail"] += 1
                stats["judge_unparseable"] += 1
        except Exception:  # noqa: BLE001
            stats["judge_calls"] += 1
            row["label_agreement"] = "fail"
            stats["set_fail"] += 1
            stats["judge_errors"] += 1

    indices = []
    for i, row in enumerate(rows):
        raw = row.get("label_agreement")
        text = "" if raw is None else str(raw).strip()
        if text == "":
            indices.append(i)

    tasks = [asyncio.create_task(process_idx(i)) for i in indices]
    if tasks:
        await asyncio.gather(*tasks)
    return stats


def main():
    args = parse_args()
    if args.max_turns < 1:
        print("--max-turns must be >= 1")
        return 1
    if args.fail_threshold < 0:
        print("--fail-threshold must be >= 0")
        return 1

    input_path = Path(args.input_jsonl).expanduser().resolve()
    output_path = Path(args.output_jsonl).expanduser().resolve()
    report_path = (
        Path(args.report_json).expanduser().resolve() if args.report_json.strip() else output_path.with_suffix(".report.json")
    )

    if not input_path.exists():
        print(f"Input JSONL not found: {input_path}")
        return 1

    rows, invalid_lines = load_jsonl_rows(input_path)
    print(f"Loaded rows: {len(rows)} (invalid lines recovered: {invalid_lines})", flush=True)

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

    distill_base_url = f"http://{args.distill_host}:{args.distill_port}/v1"
    judge_base_url = f"http://{args.judge_host}:{args.judge_port}/v1"

    distill_proc = None
    judge_proc = None
    turn_reports: List[dict] = []
    stop_reason = "max_turns_reached"
    executed_turns = 0

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

        if args.judge_model == args.distill_model and args.judge_host == args.distill_host and args.judge_port == args.distill_port:
            judge_base_url = distill_base_url
        elif args.judge_model == args.distill_model and args.judge_port == args.distill_port and args.judge_host == args.distill_host:
            judge_base_url = distill_base_url
        else:
            judge_proc = start_vllm(
                args.judge_model,
                args.judge_host,
                args.judge_port,
                args.tensor_parallel_size,
                args.gpu_memory_utilization,
                args.max_model_len,
            )
            wait_for_vllm_ready(judge_base_url, 900)

        for turn in range(1, args.max_turns + 1):
            executed_turns = turn
            before_counts = status_counts(rows)
            print(
                f"[TURN {turn}] Start pass={before_counts['pass']} fail={before_counts['fail']} "
                f"unknown={before_counts['unknown']} empty={before_counts['empty']}",
                flush=True,
            )

            redistill_stats = asyncio.run(
                redistill_fail_rows(
                    rows,
                    tokenizer=distill_tokenizer,
                    base_url=distill_base_url,
                    model=args.distill_model,
                    concurrency=args.request_concurrency,
                    timeout_seconds=args.timeout_seconds,
                    retries=args.retries,
                    retry_base_seconds=args.retry_base_seconds,
                    temperature=args.distill_temperature,
                    max_tokens=args.distill_max_tokens,
                    seed=args.distill_seed,
                    top_p=args.distill_top_p,
                )
            )

            judge_stats = asyncio.run(
                judge_empty_rows(
                    rows,
                    tokenizer=judge_tokenizer,
                    base_url=judge_base_url,
                    model=args.judge_model,
                    concurrency=args.request_concurrency,
                    timeout_seconds=args.timeout_seconds,
                    retries=args.retries,
                    retry_base_seconds=args.retry_base_seconds,
                    temperature=args.judge_temperature,
                    max_tokens=args.judge_max_tokens,
                )
            )

            after_counts = status_counts(rows)
            fail_rate = compute_fail_rate(after_counts)
            fail_rate_display = "n/a" if fail_rate is None else f"{fail_rate * 100:.2f}%"
            print(
                f"[TURN {turn}] End pass={after_counts['pass']} fail={after_counts['fail']} "
                f"unknown={after_counts['unknown']} empty={after_counts['empty']} fail_rate={fail_rate_display}",
                flush=True,
            )

            turn_report = {
                "turn": turn,
                "before": before_counts,
                "redistill": redistill_stats,
                "judge": judge_stats,
                "after": after_counts,
                "judged_rows": after_counts["pass"] + after_counts["fail"],
                "fail_rate": fail_rate,
            }
            turn_reports.append(turn_report)

            if fail_rate is not None and fail_rate < args.fail_threshold:
                stop_reason = f"fail_rate_below_threshold({fail_rate:.8f} < {args.fail_threshold:.8f})"
                break
    finally:
        print("Stopping vLLM servers...", flush=True)
        stop_proc(judge_proc)
        if distill_proc is not None and (judge_proc is None or distill_proc.pid != judge_proc.pid):
            stop_proc(distill_proc)

    write_jsonl_rows(output_path, rows)

    final_counts = status_counts(rows)
    final_fail_rate = compute_fail_rate(final_counts)
    report = {
        "input_jsonl": str(input_path),
        "output_jsonl": str(output_path),
        "report_json": str(report_path),
        "distill_model": args.distill_model,
        "judge_model": args.judge_model,
        "max_turns": args.max_turns,
        "fail_threshold": args.fail_threshold,
        "executed_turns": executed_turns,
        "stop_reason": stop_reason,
        "invalid_input_lines_recovered": invalid_lines,
        "final_counts": final_counts,
        "final_judged_rows": final_counts["pass"] + final_counts["fail"],
        "final_fail_rate": final_fail_rate,
        "turns": turn_reports,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Wrote output JSONL: {output_path}")
    print(f"Wrote report JSON: {report_path}")
    if final_fail_rate is None:
        print("Final fail rate: n/a (no judged rows)")
    else:
        print(f"Final fail rate: {final_fail_rate * 100:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
