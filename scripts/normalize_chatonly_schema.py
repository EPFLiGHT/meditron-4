#!/usr/bin/env python3
"""Normalize distilled chat-only JSONL files to a stable GPT-OSS-style schema."""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict

TARGET_KEYS = [
    "conversations",
    "distilled_model",
    "distilled_source_index",
    "distilled_source_path",
    "distilled_timestamp_utc",
    "id",
    "label_agreement",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize a chat-only JSONL file to a consistent schema."
    )
    parser.add_argument("--input", required=True, help="Input JSONL path")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument(
        "--summary",
        default="",
        help="Optional summary file path (plain text, like GPT-OSS chatonly summary)",
    )
    parser.add_argument(
        "--backup",
        default="",
        help="Optional backup path for the original input file",
    )
    return parser.parse_args()


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


def normalize_row(row: Dict[str, Any], line_no: int) -> Dict[str, Any]:
    out = {}
    for key in TARGET_KEYS:
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

    if args.backup:
        backup_path = Path(args.backup)
        if backup_path.exists():
            raise FileExistsError(f"Backup already exists: {backup_path}")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.rename(backup_path)
        input_path = backup_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    missing_counter = Counter()
    rows = 0
    with input_path.open("r", encoding="utf-8") as src, output_path.open(
        "w", encoding="utf-8"
    ) as dst:
        for line_no, line in enumerate(src, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            for key in TARGET_KEYS:
                if key not in row:
                    missing_counter[key] += 1
            normalized = normalize_row(row, line_no)
            dst.write(json.dumps(normalized, ensure_ascii=False) + "\n")
            rows += 1

    if args.summary:
        summary_path = Path(args.summary)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            "\n".join(
                [
                    f"source={input_path}",
                    f"output={output_path}",
                    f"rows={rows}",
                    f"keys={TARGET_KEYS}",
                    f"missing_before={dict(missing_counter)}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    print(f"Normalized rows: {rows}")
    print(f"Output: {output_path}")
    if args.summary:
        print(f"Summary: {args.summary}")


if __name__ == "__main__":
    main()
