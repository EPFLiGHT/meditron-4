#!/usr/bin/env python3
"""Convert legacy <unused94>/<unused95> reasoning delimiters to <think></think>."""

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Tuple


START_RE = re.compile(r"<unused94>\s*(?:thought)?\s*", re.IGNORECASE)
END_TOKEN = "<unused95>"
ASSISTANT_ROLES = {"assistant", "gpt", "model"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite assistant reasoning tags in JSONL chat rows."
    )
    parser.add_argument("--input", required=True, help="Input JSONL path")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Rewrite the output path in place (requires --input == --output)",
    )
    parser.add_argument(
        "--drop-empty-assistant",
        action="store_true",
        help="Drop rows where assistant value is empty after normalization",
    )
    parser.add_argument(
        "--summary-json",
        default="",
        help="Optional path for conversion summary JSON",
    )
    parser.add_argument(
        "--think-open",
        default="<think>",
        help="Opening thinking tag",
    )
    parser.add_argument(
        "--think-close",
        default="</think>",
        help="Closing thinking tag",
    )
    return parser.parse_args()


def normalize_assistant_value(
    text: str, think_open: str, think_close: str
) -> Tuple[str, str]:
    """Return normalized text and a status label."""
    if not isinstance(text, str):
        return text, "not_string"
    if not text:
        return text, "empty"

    has_start = bool(START_RE.search(text))
    has_end = END_TOKEN in text

    if not has_start and not has_end:
        return text, "no_legacy_tags"

    if think_open in text and think_close in text and not has_start and not has_end:
        return text, "already_think_tags"

    start_match = START_RE.search(text)
    start_idx = start_match.start() if start_match else -1
    start_end_idx = start_match.end() if start_match else -1
    end_idx = text.find(END_TOKEN, start_end_idx) if start_match else -1

    if has_start and end_idx != -1:
        prefix = text[:start_idx]
        rationale = text[start_end_idx:end_idx].strip("\n")
        answer = text[end_idx + len(END_TOKEN) :].replace(END_TOKEN, "").lstrip("\n")
        parts = []
        if prefix:
            parts.append(prefix.rstrip("\n"))
        parts.append(think_open)
        parts.append(rationale)
        parts.append(think_close)
        if answer:
            parts.append(answer)
        return "\n".join(parts), "paired_rewritten"

    if not has_start and has_end:
        end_idx = text.find(END_TOKEN)
        rationale = text[:end_idx].strip("\n")
        answer = text[end_idx + len(END_TOKEN) :].replace(END_TOKEN, "").lstrip("\n")
        parts = [think_open, rationale, think_close]
        if answer:
            parts.append(answer)
        return "\n".join(parts), "end_only_wrapped"

    # Start tag without end tag: convert opener and close at end.
    rewritten = START_RE.sub(f"{think_open}\n", text)
    if has_start and think_close not in rewritten:
        rewritten = rewritten.rstrip("\n") + f"\n{think_close}"
    return rewritten, "start_only_closed"


def rewrite_row(row: dict, think_open: str, think_close: str) -> Tuple[dict, str]:
    conversations = row.get("conversations")
    if not isinstance(conversations, list):
        return row, "no_conversations"

    status = "no_assistant_turn"
    updated = []
    for turn in conversations:
        if not isinstance(turn, dict):
            updated.append(turn)
            continue
        role = str(turn.get("from", "")).strip().lower()
        if role in ASSISTANT_ROLES:
            new_turn = dict(turn)
            new_value, status = normalize_assistant_value(
                str(turn.get("value", "")), think_open, think_close
            )
            new_turn["value"] = new_value
            updated.append(new_turn)
        else:
            updated.append(turn)

    out = dict(row)
    out["conversations"] = updated
    return out, status


def assistant_text(row: dict) -> str:
    conversations = row.get("conversations")
    if not isinstance(conversations, list):
        return ""
    for turn in conversations:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("from", "")).strip().lower()
        if role in ASSISTANT_ROLES:
            value = turn.get("value", "")
            return value if isinstance(value, str) else str(value)
    return ""


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    if args.in_place and input_path != output_path:
        raise ValueError("--in-place requires --input and --output to be the same path")

    stats: Dict[str, int] = {}
    rows = 0
    kept_rows = 0
    dropped_empty_rows = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.in_place:
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=output_path.name + ".tmp.", dir=str(output_path.parent)
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        if tmp_path.exists():
            tmp_path.unlink()
        write_path = Path(tmp_name)
    else:
        if output_path.exists():
            raise FileExistsError(f"Output already exists: {output_path}")
        write_path = output_path

    try:
        with input_path.open("r", encoding="utf-8") as src, write_path.open(
            "w", encoding="utf-8"
        ) as dst:
            for raw_line in src:
                line = raw_line.strip()
                if not line:
                    continue
                row = json.loads(line)
                out_row, status = rewrite_row(row, args.think_open, args.think_close)
                stats[status] = stats.get(status, 0) + 1
                rows += 1

                if args.drop_empty_assistant and not assistant_text(out_row).strip():
                    dropped_empty_rows += 1
                    continue

                dst.write(json.dumps(out_row, ensure_ascii=False) + "\n")
                kept_rows += 1

        if args.in_place:
            write_path.replace(output_path)
    finally:
        if args.in_place and write_path.exists() and write_path != output_path:
            write_path.unlink()

    summary = {
        "input": str(input_path),
        "output": str(output_path),
        "rows_in": rows,
        "rows_out": kept_rows,
        "dropped_empty_assistant_rows": dropped_empty_rows,
        "stats": stats,
        "think_open": args.think_open,
        "think_close": args.think_close,
        "in_place": args.in_place,
        "drop_empty_assistant": args.drop_empty_assistant,
    }

    if args.summary_json:
        summary_path = Path(args.summary_json)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
