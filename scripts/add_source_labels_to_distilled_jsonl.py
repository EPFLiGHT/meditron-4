#!/usr/bin/env python3
"""Enrich a distilled JSONL with label fields from its source datasets."""

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Set, Tuple


CORE_LABEL_KEYS = (
    "true_label",
    "true_answer_text",
    "true_label_idx",
    "true_rationale",
    "label",
    "answer",
    "answer_id",
    "source_answer_idx",
    "has_label",
    "label_type",
    "label_match_mode",
    "label_join_status",
    "label_source_split",
)

SKIP_KEYS = {
    "conversations",
    "id",
    "split",
    "question_type",
    "task",
    "label_agreement",
    "distilled_model",
    "distilled_source_index",
    "distilled_source_path",
    "distilled_timestamp_utc",
    "distilled_prompt_used",
    "distilled_finish_reason",
    "distilled_error",
}

LABELISH_RE = re.compile(r"(label|answer|rationale|target|gold|correct)", re.IGNORECASE)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Copy label-like fields from distilled_source_path/distilled_source_index rows "
            "into a distilled JSONL."
        )
    )
    parser.add_argument("input_jsonl", help="Distilled JSONL to enrich")
    parser.add_argument(
        "--output-jsonl",
        default="",
        help="Optional output path. Defaults to rewriting the input file in place.",
    )
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not overwrite label-like fields already present in the distilled row.",
    )
    parser.add_argument(
        "--copy-all-source-fields",
        action="store_true",
        help="Merge every field from the source row, not just label-like fields.",
    )
    parser.add_argument(
        "--embed-source-row",
        action="store_true",
        help="Store the full parsed source row under source_row_payload.",
    )
    parser.add_argument(
        "--embed-source-raw-json",
        action="store_true",
        help="Store the exact raw source JSON line under source_row_raw_json.",
    )
    return parser.parse_args()


def is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip()
        return bool(text) and text.lower() not in {"none", "null", "nan"}
    return True


def iter_label_keys(row: Mapping[str, Any]) -> Iterable[str]:
    seen = set()
    for key in CORE_LABEL_KEYS:
        if key in row and key not in SKIP_KEYS:
            seen.add(key)
            yield key
    for key in row.keys():
        if key in seen or key in SKIP_KEYS:
            continue
        if LABELISH_RE.search(str(key)):
            yield key


def extract_label_fields(row: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in iter_label_keys(row):
        value = row.get(key)
        if is_nonempty(value):
            out[key] = value
    return out


def extract_source_payload(row: Mapping[str, Any], copy_all_source_fields: bool) -> Dict[str, Any]:
    if copy_all_source_fields:
        return {key: value for key, value in row.items() if key not in SKIP_KEYS}
    return extract_label_fields(row)


def collect_needed_indices(path: Path) -> Tuple[Dict[str, Set[int]], Counter]:
    needed: Dict[str, Set[int]] = defaultdict(set)
    stats = Counter()
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw_line in enumerate(handle, start=1):
            raw = raw_line.strip()
            if not raw:
                stats["blank_rows"] += 1
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                stats["invalid_rows"] += 1
                continue
            if not isinstance(row, dict):
                stats["non_object_rows"] += 1
                continue
            src_path = row.get("distilled_source_path")
            src_idx = row.get("distilled_source_index")
            if not isinstance(src_path, str) or not src_path.strip():
                stats["missing_source_path"] += 1
                continue
            try:
                src_idx_int = int(src_idx)
            except (TypeError, ValueError):
                stats["missing_source_index"] += 1
                continue
            needed[src_path].add(src_idx_int)
            stats["rows_with_source_ref"] += 1
    return needed, stats


def load_source_labels(
    needed: Mapping[str, Set[int]],
    copy_all_source_fields: bool,
    embed_source_row: bool,
    embed_source_raw_json: bool,
) -> Tuple[Dict[Tuple[str, int], Dict[str, Any]], Counter]:
    cache: Dict[Tuple[str, int], Dict[str, Any]] = {}
    stats = Counter()
    for src_path, wanted_indices in needed.items():
        path = Path(src_path)
        if not path.exists():
            stats["missing_source_files"] += 1
            continue
        found_here = 0
        with path.open("r", encoding="utf-8") as handle:
            for idx, raw_line in enumerate(handle):
                if idx not in wanted_indices:
                    continue
                raw = raw_line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    stats["invalid_source_rows"] += 1
                    continue
                if not isinstance(row, dict):
                    continue
                payload = {
                    "merged_fields": extract_source_payload(row, copy_all_source_fields),
                }
                if embed_source_row:
                    payload["source_row_payload"] = row
                if embed_source_raw_json:
                    payload["source_row_raw_json"] = raw
                cache[(src_path, idx)] = payload
                found_here += 1
                if found_here >= len(wanted_indices):
                    break
        stats["source_files_scanned"] += 1
        stats["source_rows_found"] += found_here
        stats["source_rows_requested"] += len(wanted_indices)
    return cache, stats


def write_enriched_jsonl(
    input_path: Path,
    output_path: Path,
    cache: Mapping[Tuple[str, int], Dict[str, Any]],
    keep_existing: bool,
    embed_source_row: bool,
    embed_source_raw_json: bool,
) -> Counter:
    stats = Counter()
    tmp_path = output_path.with_suffix(output_path.suffix + f".tmp.{os.getpid()}")
    with input_path.open("r", encoding="utf-8") as reader, tmp_path.open("w", encoding="utf-8") as writer:
        for raw_line in reader:
            raw = raw_line.strip()
            if not raw:
                writer.write("\n")
                stats["blank_rows"] += 1
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                writer.write(raw_line)
                stats["invalid_rows"] += 1
                continue
            if not isinstance(row, dict):
                writer.write(json.dumps(row, ensure_ascii=False) + "\n")
                stats["non_object_rows"] += 1
                continue

            src_path = row.get("distilled_source_path")
            src_idx = row.get("distilled_source_index")
            try:
                src_idx_int = int(src_idx)
            except (TypeError, ValueError):
                writer.write(json.dumps(row, ensure_ascii=False) + "\n")
                stats["rows_without_source_ref"] += 1
                continue

            payload = cache.get((src_path, src_idx_int))
            if payload is None:
                writer.write(json.dumps(row, ensure_ascii=False) + "\n")
                stats["source_lookup_miss"] += 1
                continue

            copied = payload.get("merged_fields", {})
            if not copied and not embed_source_row and not embed_source_raw_json:
                writer.write(json.dumps(row, ensure_ascii=False) + "\n")
                stats["source_row_without_labels"] += 1
                continue

            added_here = 0
            overwritten_here = 0
            for key, value in copied.items():
                if keep_existing and is_nonempty(row.get(key)):
                    continue
                if key in row and row.get(key) != value and is_nonempty(row.get(key)):
                    overwritten_here += 1
                elif key not in row:
                    added_here += 1
                row[key] = value

            if embed_source_row and "source_row_payload" in payload:
                row["source_row_payload"] = payload["source_row_payload"]
            if embed_source_raw_json and "source_row_raw_json" in payload:
                row["source_row_raw_json"] = payload["source_row_raw_json"]

            writer.write(json.dumps(row, ensure_ascii=False) + "\n")
            stats["rows_enriched"] += 1
            stats["fields_added"] += added_here
            stats["fields_overwritten"] += overwritten_here

        writer.flush()
        os.fsync(writer.fileno())
    os.replace(str(tmp_path), str(output_path))
    return stats


def main():
    args = parse_args()
    input_path = Path(args.input_jsonl).expanduser().resolve()
    if not input_path.exists():
        print(f"Input JSONL not found: {input_path}")
        return 1

    output_path = (
        Path(args.output_jsonl).expanduser().resolve() if args.output_jsonl.strip() else input_path
    )

    needed, scan_stats = collect_needed_indices(input_path)
    print(f"Input rows with source refs: {scan_stats['rows_with_source_ref']}")
    print(f"Unique source files: {len(needed)}")

    cache, source_stats = load_source_labels(
        needed,
        copy_all_source_fields=args.copy_all_source_fields,
        embed_source_row=args.embed_source_row,
        embed_source_raw_json=args.embed_source_raw_json,
    )
    print(
        f"Loaded source label payloads: {len(cache)} "
        f"(requested {source_stats['source_rows_requested']}, found {source_stats['source_rows_found']})"
    )

    write_stats = write_enriched_jsonl(
        input_path,
        output_path,
        cache,
        args.keep_existing,
        embed_source_row=args.embed_source_row,
        embed_source_raw_json=args.embed_source_raw_json,
    )
    print(f"Wrote enriched JSONL: {output_path}")
    for key in (
        "rows_enriched",
        "fields_added",
        "fields_overwritten",
        "source_lookup_miss",
        "source_row_without_labels",
        "rows_without_source_ref",
        "invalid_rows",
    ):
        print(f"{key}: {write_stats[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
