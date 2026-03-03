#!/usr/bin/env python3

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Optional

from distill_common import shard_output_path


def parse_args():
    parser = argparse.ArgumentParser(description="Distill one dataset shard using Hugging Face datasets primitives.")
    parser.add_argument("--dataset-cache", required=True, help="Path to Arrow dataset cache created by prepare_distill_datasets.py")
    parser.add_argument("--dataset-path", required=True, help="Path to source JSONL dataset")
    parser.add_argument("--base-url", required=True, help="OpenAI-style base URL, e.g. http://127.0.0.1:8000/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--num-shards", type=int, required=True)
    parser.add_argument("--request-concurrency", type=int, default=8)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--strict-repro", action="store_true")
    parser.add_argument(
        "--model-revision",
        default=None,
        help="Model revision/commit for provenance (required in --strict-repro unless DISTILL_MODEL_REVISION is set)",
    )
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--retry-base-seconds", type=float, default=1.5)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def normalize_generation_config(args):
    temperature = args.temperature
    top_p = None
    if args.deterministic or args.strict_repro:
        temperature = 0.0
        top_p = 1.0
    return temperature, top_p


def resolve_model_revision(args):
    return (args.model_revision or os.getenv("DISTILL_MODEL_REVISION") or "").strip() or None


def build_repro_config(args, temperature, top_p, model_revision):
    return {
        "model": args.model,
        "model_revision": model_revision,
        "request_concurrency": args.request_concurrency,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": args.max_tokens,
        "retries": args.retries,
        "retry_base_seconds": args.retry_base_seconds,
        "timeout_seconds": args.timeout_seconds,
        "seed": args.seed,
        "deterministic": bool(args.deterministic or args.strict_repro),
        "strict_repro": bool(args.strict_repro),
    }


def config_hash(config):
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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

    # Add generation-time control directly in the final user message.
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


def parse_completion_payload(payload: Any):
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


async def request_completion(base_url, timeout_seconds, model, prompt, temperature, max_tokens, seed, top_p):
    payload = {
        "model": model,
        "prompt": prompt,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "skip_special_tokens": False,
    }
    if seed is not None:
        payload["seed"] = int(seed)
    if top_p is not None:
        payload["top_p"] = float(top_p)
    loop = asyncio.get_event_loop()
    response_payload = await loop.run_in_executor(
        None,
        lambda: _post_completion_request(base_url, payload, timeout_seconds),
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
    seed,
    top_p,
    retries,
    retry_base_seconds,
):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            async with semaphore:
                return await request_completion(
                    base_url,
                    timeout_seconds,
                    model=model,
                    prompt=prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    seed=seed,
                    top_p=top_p,
                )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == retries:
                break
            await asyncio.sleep(retry_base_seconds * (2 ** (attempt - 1)))
    raise RuntimeError(f"Completion failed after {retries} retries: {last_exc}")


def load_existing_keys(output_path):
    from datasets import load_dataset

    if not output_path.exists():
        return set()

    existing = load_dataset("json", data_files=str(output_path), split="train")
    return set(completion_key(row) for row in existing)


def main():
    args = parse_args()
    from datasets import concatenate_datasets, load_dataset, load_from_disk

    if args.strict_repro and args.seed is None:
        print("--seed is required when --strict-repro is enabled")
        return 1

    temperature, top_p = normalize_generation_config(args)
    model_revision = resolve_model_revision(args)
    repro = build_repro_config(args, temperature, top_p, model_revision)
    repro_hash = config_hash(repro)
    if args.strict_repro and (not model_revision or not repro_hash):
        print("Strict reproducibility requires model revision and config hash")
        return 1

    dataset_path = Path(args.dataset_path).resolve()
    cache_path = Path(args.dataset_cache).resolve()
    if not dataset_path.exists():
        print(f"Dataset does not exist: {dataset_path}")
        return 1
    if not cache_path.exists():
        print(f"Dataset cache does not exist: {cache_path}")
        print("Run prepare_distill_datasets.py first.")
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
        }
        if args.strict_repro or args.deterministic:
            result["distilled_config_sha256"] = repro_hash
            result["distilled_repro"] = repro
        if not messages or prompt_used is None:
            result["distilled_answer"] = ""
            result["distilled_error"] = "No user/human prompt found in conversations"
            return result

        rendered_prompt = render_chat_prompt(tokenizer, messages)
        if not rendered_prompt:
            result["distilled_answer"] = ""
            result["distilled_error"] = "Failed to render prompt with apply_chat_template"
            return result

        try:
            answer, finish_reason = await generate_with_retry(
                args.base_url,
                args.timeout_seconds,
                semaphore,
                model=args.model,
                prompt=rendered_prompt,
                temperature=temperature,
                max_tokens=args.max_tokens,
                seed=args.seed,
                top_p=top_p,
                retries=args.retries,
                retry_base_seconds=args.retry_base_seconds,
            )
            result["distilled_answer"] = answer
            if finish_reason:
                result["distilled_finish_reason"] = finish_reason
        except Exception as exc:  # noqa: BLE001
            result["distilled_answer"] = ""
            result["distilled_error"] = str(exc)
        return result

    enriched_pending = pending_dataset.map(
        enrich_row,
        with_indices=True,
        batched=False,
        desc=f"Distilling {dataset_path.name} shard {args.shard_index}",
        load_from_cache_file=False,
    )

    if output_path.exists():
        existing_dataset = load_dataset("json", data_files=str(output_path), split="train")
        combined = concatenate_datasets([existing_dataset, enriched_pending])
    else:
        combined = enriched_pending

    combined = combined.sort("distilled_source_index")
    seen_keys = set()
    combined = combined.filter(
        lambda row: completion_key(row) not in seen_keys and not seen_keys.add(completion_key(row)),
        desc=f"Deduplicating shard output for {dataset_path.name} shard {args.shard_index}",
    )
    combined.to_json(str(output_path))

    error_count = 0
    if "distilled_error" in combined.column_names:
        error_count = sum(1 for value in combined["distilled_error"] if isinstance(value, str) and value.strip())

    print(
        f"Done {dataset_path.name} shard {args.shard_index}/{args.num_shards}: "
        f"written={len(combined)} newly_processed={len(enriched_pending)} failed={error_count}",
        flush=True,
    )

    if args.strict_repro:
        manifest = {
            "type": "distillation_shard_manifest",
            "output_path": str(output_path),
            "dataset_path": str(dataset_path),
            "dataset_cache_path": str(cache_path),
            "num_rows": len(combined),
            "num_newly_processed": len(enriched_pending),
            "num_errors": error_count,
            "repro_config_sha256": repro_hash,
            "repro_config": repro,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote manifest: {manifest_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
