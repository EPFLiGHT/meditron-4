#!/usr/bin/env python3

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate distill_reports/example.md from the latest distillation output."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Path to distillation JSONL. Default: newest file in distillation/distilled_outputs/.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("distill_reports/example.md"),
        help="Output markdown path. Default: distill_reports/example.md",
    )
    return parser.parse_args()


def latest_output_file() -> Path:
    out_dir = Path("distillation/distilled_outputs")
    candidates = sorted(out_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No .jsonl files found in {out_dir}")
    return candidates[0]


def read_jsonl(path: Path):
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def count_lines(path: Path) -> int:
    n = 0
    with path.open(encoding="utf-8") as handle:
        for _ in handle:
            n += 1
    return n


def get_row(path: Path, row_index: int):
    with path.open(encoding="utf-8") as handle:
        for i, line in enumerate(handle):
            if i == row_index:
                return json.loads(line)
    return None


def extract_turn(conversations, roles):
    if not isinstance(conversations, list):
        return None
    wanted = {r.lower() for r in roles}
    for turn in conversations:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("from", "")).strip().lower()
        if role not in wanted:
            continue
        value = turn.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def preview(text, limit=220):
    if not isinstance(text, str):
        return "None"
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3] + "..."


def has_thought_markers(text: str) -> bool:
    return bool(re.search(r"<unused\d+>thought|<think>|</think>", text, re.IGNORECASE))


def main():
    args = parse_args()
    input_path = args.input or latest_output_file()
    rows = read_jsonl(input_path)
    if not rows:
        raise RuntimeError(f"Input file has no rows: {input_path}")

    by_dataset = defaultdict(list)
    for row in rows:
        by_dataset[row["source_dataset_name"]].append(row)

    source_counts = {}
    for dataset_name, dataset_rows in by_dataset.items():
        src = Path(dataset_rows[0]["source_dataset_path"])
        source_counts[dataset_name] = count_lines(src) if src.exists() else 0

    lines = []
    lines.append("# Distillation Output Examples")
    lines.append("")
    lines.append(f"Source file: `{input_path.as_posix()}`")
    lines.append("")
    lines.append("| dataset | source_rows | sampled_rows | empty_answer_rate | finish_reason_counts | notes |")
    lines.append("|---|---:|---:|---:|---|---|")

    for dataset_name in sorted(by_dataset):
        dataset_rows = by_dataset[dataset_name]
        sampled_rows = len(dataset_rows)
        empty_rows = sum(1 for row in dataset_rows if not str(row.get("distilled_answer") or "").strip())
        empty_rate = (empty_rows / sampled_rows * 100.0) if sampled_rows else 0.0
        finish_counts = Counter((row.get("distilled_finish_reason") or "None") for row in dataset_rows)
        finish_counts_text = ", ".join(f"{k}:{v}" for k, v in sorted(finish_counts.items()))
        thought_rows = sum(
            1 for row in dataset_rows if has_thought_markers(str(row.get("distilled_answer") or ""))
        )

        notes = []
        if empty_rows:
            notes.append(f"{empty_rows} empty answers")
        if thought_rows:
            notes.append(f"{thought_rows} thought-marker answers")
        if not notes:
            notes.append("-")

        lines.append(
            f"| {dataset_name} | {source_counts.get(dataset_name, 0)} | {sampled_rows} | "
            f"{empty_rate:.2f}% | {finish_counts_text} | {'; '.join(notes)} |"
        )

    lines.append("")
    lines.append("## Examples")
    lines.append("")

    for dataset_name in sorted(by_dataset):
        sample = by_dataset[dataset_name][0]
        source_path = Path(sample["source_dataset_path"])
        source_row_index = int(sample["source_row_index"])
        source_row = get_row(source_path, source_row_index) if source_path.exists() else None

        sample_id = None
        question = None
        source_answer = None
        true_label = None
        true_label_idx = None
        true_answer_text = None

        if isinstance(source_row, dict):
            sample_id = source_row.get("id")
            true_label = source_row.get("true_label")
            true_label_idx = source_row.get("true_label_idx")
            true_answer_text = source_row.get("true_answer_text")
            conversations = source_row.get("conversations")
            question = extract_turn(conversations, {"user", "human"})
            source_answer = extract_turn(conversations, {"assistant", "gpt"})

        lines.append(f"### {dataset_name}")
        lines.append("")
        lines.append(f"- id: `{sample_id}`")
        lines.append(f"- true_label: `{true_label}`")
        if true_label_idx is not None:
            lines.append(f"- true_label_idx: `{true_label_idx}`")
        if true_answer_text is not None:
            lines.append(f"- true_answer_text: `{true_answer_text}`")
        lines.append(f"- source_row_index: `{source_row_index}`")
        lines.append(f"- question_preview: {preview(question)}")
        lines.append(f"- distilled_model: `{sample.get('distilled_model')}`")
        lines.append(f"- distilled_timestamp_utc: `{sample.get('distilled_timestamp_utc')}`")
        lines.append(f"- distilled_finish_reason: `{sample.get('distilled_finish_reason')}`")
        lines.append("")
        lines.append("#### Source answer(s)")
        lines.append("")
        if source_answer:
            lines.append("> " + source_answer.replace("\n", "\n> "))
        else:
            lines.append("_(no source assistant answer found for this sample)_")
        lines.append("")
        lines.append("#### Distilled answer")
        lines.append("")
        distilled_answer = str(sample.get("distilled_answer") or "").strip()
        if distilled_answer:
            lines.append(distilled_answer)
        else:
            lines.append("_(empty distilled answer)_")
        lines.append("")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {args.output}")
    print(f"Input: {input_path}")
    print(f"Datasets: {len(by_dataset)}  Rows: {len(rows)}")


if __name__ == "__main__":
    main()
