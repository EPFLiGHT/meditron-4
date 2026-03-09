#!/usr/bin/env python3
"""Build chat-only JSONL where conversations assistant turn uses distilled answer."""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

TARGET_KEYS = [
    "conversations",
    "distilled_model",
    "distilled_token_logprobs",
    "distilled_source_index",
    "distilled_source_path",
    "distilled_timestamp_utc",
    "id",
    "label_agreement",
]

ASSISTANT_ROLES = {"assistant", "gpt", "model"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite conversations assistant value from distilled answer fields."
    )
    parser.add_argument("--input", required=True, help="Input JSONL path")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument(
        "--preferred-answer-key",
        default="",
        help="Optional preferred distilled answer key (e.g., distilled_answer_gpt_oss_120b)",
    )
    return parser.parse_args()


def answer_keys(row: Dict[str, Any], preferred: str) -> Iterable[str]:
    if preferred:
        yield preferred
    if "distilled_answer" in row:
        yield "distilled_answer"
    suffix_keys = sorted(
        key for key in row.keys() if isinstance(key, str) and key.startswith("distilled_answer_")
    )
    for key in suffix_keys:
        if key != preferred:
            yield key


def pick_distilled_answer(row: Dict[str, Any], preferred: str) -> str:
    for key in answer_keys(row, preferred):
        value = row.get(key)
        if isinstance(value, str):
            return value
    return ""


def rewrite_conversations(conversations: Any, distilled_answer: str) -> List[Dict[str, Any]]:
    updated = []
    replaced_any = False

    if isinstance(conversations, list):
        for turn in conversations:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("from", "")).strip().lower()
            if role in ASSISTANT_ROLES:
                new_turn = dict(turn)
                new_turn["from"] = "assistant"
                new_turn["value"] = distilled_answer
                updated.append(new_turn)
                replaced_any = True
            else:
                updated.append(dict(turn))

    if not replaced_any:
        updated.append({"from": "assistant", "value": distilled_answer})

    return updated


def fallback_id(row: Dict[str, Any], line_no: int) -> str:
    for key in ("id", "question_id", "answer_id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
    src = row.get("distilled_source_path")
    idx = row.get("distilled_source_index")
    if isinstance(src, str) and src and isinstance(idx, int):
        return f"{Path(src).name}:{idx}"
    return f"line:{line_no}"


def normalize_target_row(row: Dict[str, Any], conversations: List[Dict[str, Any]], line_no: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in TARGET_KEYS:
        if key == "conversations":
            out[key] = conversations
        else:
            out[key] = row.get(key)

    if not isinstance(out["id"], str) or not out["id"].strip():
        out["id"] = fallback_id(row, line_no)
    if not isinstance(out["label_agreement"], str) or not out["label_agreement"].strip():
        out["label_agreement"] = "unknown"
    return out


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    row_count = 0
    with input_path.open("r", encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line_no, raw_line in enumerate(src, start=1):
            line = raw_line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue

            distilled_answer = pick_distilled_answer(row, args.preferred_answer_key)
            conversations = rewrite_conversations(row.get("conversations"), distilled_answer)
            out_row = normalize_target_row(row, conversations, line_no)
            dst.write(json.dumps(out_row, ensure_ascii=False) + "\n")
            row_count += 1

    print(f"Wrote {row_count} rows to {output_path}")


if __name__ == "__main__":
    main()
