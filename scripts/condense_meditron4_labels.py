#!/usr/bin/env python3
"""Condense heterogeneous Meditron-4 label fields into label_letter/label_text."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_INPUT = "/users/theimer/meditron-4/meditron_4_merged_all_source.jsonl"
DEFAULT_OUTPUT = "/users/theimer/meditron-4/meditron_4_merged_all_source.condensed_labels.jsonl"
DEFAULT_LIST_FILE = "/users/theimer/meditron-4/distillation/datasets_to_distill.txt"

OPTION_RE = re.compile(r"^option\s*([1-9][0-9]*)$", re.IGNORECASE)
LETTER_RE = re.compile(r"^[A-Z]$")

LABEL_FIELDS_TO_DROP = {
    "answer_id",
    "question_id",
    "question_type",
    "split",
    "task",
    "true_label",
    "true_label_abc",
    "true_label_idx",
    "true_answer_text",
    "true_rationale",
    "source_answer_idx",
    "label_type",
    "has_label",
    "label_match_mode",
    "label_join_status",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rewrite Meditron-4 merged source JSONL so label information is "
            "condensed into label_letter and label_text."
        )
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Input JSONL path")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSONL path")
    parser.add_argument(
        "--list-file",
        default=DEFAULT_LIST_FILE,
        help="Dataset list file used to build meditron_4_merged_all_source.jsonl",
    )
    parser.add_argument(
        "--keep-original-label-fields",
        action="store_true",
        help="Keep the original heterogeneous label fields instead of dropping them.",
    )
    return parser.parse_args()


def nonempty_string(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    return text


def idx_to_letter(value: Any) -> Optional[str]:
    if not isinstance(value, int):
        return None
    if 0 <= value < 26:
        return chr(ord("A") + value)
    return None


def normalize_letter_candidate(value: Any) -> Optional[str]:
    text = nonempty_string(value)
    if text is None:
        return None
    upper = text.upper()
    if LETTER_RE.fullmatch(upper):
        return upper
    match = OPTION_RE.fullmatch(text)
    if match:
        number = int(match.group(1))
        if 1 <= number <= 26:
            return chr(ord("A") + number - 1)
    return None


def derive_label_letter(row: Dict[str, Any]) -> Optional[str]:
    for key in ("true_label_abc", "source_answer_idx", "true_label"):
        letter = normalize_letter_candidate(row.get(key))
        if letter is not None:
            return letter
    return idx_to_letter(row.get("true_label_idx"))


def derive_label_text(row: Dict[str, Any]) -> Optional[str]:
    answer_text = nonempty_string(row.get("true_answer_text"))
    if answer_text is not None:
        return answer_text

    true_label = nonempty_string(row.get("true_label"))
    if true_label is None:
        return None

    if normalize_letter_candidate(true_label) is not None:
        return None

    return true_label


def load_source_ranges(list_file: Path) -> List[Tuple[int, int, str]]:
    dataset_paths: List[str] = []
    for raw_line in list_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            dataset_paths.append(str(Path(line).expanduser().resolve()))

    ranges: List[Tuple[int, int, str]] = []
    start = 0
    for dataset_path in dataset_paths:
        count = 0
        with open(dataset_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                if raw_line.strip():
                    count += 1
        end = start + count - 1
        ranges.append((start, end, dataset_path))
        start = end + 1
    return ranges


def source_ref_for_row(row_index: int, source_ranges: List[Tuple[int, int, str]]) -> Tuple[Optional[str], Optional[int]]:
    for start, end, source_path in source_ranges:
        if start <= row_index <= end:
            return source_path, row_index - start
    return None, None


def transform_row(
    row: Dict[str, Any],
    row_index: int,
    source_ranges: List[Tuple[int, int, str]],
    keep_original_label_fields: bool,
) -> Dict[str, Any]:
    out = dict(row)
    out["label_letter"] = derive_label_letter(row)
    out["label_text"] = derive_label_text(row)
    source_path, source_index = source_ref_for_row(row_index, source_ranges)
    out["source_path"] = source_path
    out["source_index"] = source_index

    if not keep_original_label_fields:
        for key in LABEL_FIELDS_TO_DROP:
            out.pop(key, None)

    return out


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    list_file_path = Path(args.list_file).expanduser().resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input JSONL not found: {input_path}")
    if not list_file_path.exists():
        raise FileNotFoundError(f"Dataset list file not found: {list_file_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_ranges = load_source_ranges(list_file_path)

    stats = Counter()
    with input_path.open("r", encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line_no, raw_line in enumerate(src, start=1):
            raw = raw_line.strip()
            if not raw:
                continue

            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError(f"Expected JSON object at line {line_no}")

            transformed = transform_row(
                row,
                row_index=line_no - 1,
                source_ranges=source_ranges,
                keep_original_label_fields=args.keep_original_label_fields,
            )
            if transformed.get("label_letter") is not None:
                stats["rows_with_label_letter"] += 1
            if transformed.get("label_text") is not None:
                stats["rows_with_label_text"] += 1
            if transformed.get("source_path") is not None:
                stats["rows_with_source_path"] += 1
            if transformed.get("source_index") is not None:
                stats["rows_with_source_index"] += 1
            if (
                transformed.get("label_letter") is None
                and transformed.get("label_text") is None
            ):
                stats["rows_without_label"] += 1

            dst.write(json.dumps(transformed, ensure_ascii=False) + "\n")
            stats["rows_written"] += 1

    print(f"Input: {input_path}")
    print(f"Output: {output_path}")
    print(f"rows_written: {stats['rows_written']}")
    print(f"rows_with_label_letter: {stats['rows_with_label_letter']}")
    print(f"rows_with_label_text: {stats['rows_with_label_text']}")
    print(f"rows_without_label: {stats['rows_without_label']}")
    print(f"rows_with_source_path: {stats['rows_with_source_path']}")
    print(f"rows_with_source_index: {stats['rows_with_source_index']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
