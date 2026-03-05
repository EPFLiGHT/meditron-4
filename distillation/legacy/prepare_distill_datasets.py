#!/usr/bin/env python3

import argparse
from pathlib import Path

from distill_common import DEFAULT_LIST_FILE, dataset_cache_path, load_dataset_paths


def parse_args():
    parser = argparse.ArgumentParser(description="Prepare Arrow-backed Hugging Face datasets for distillation.")
    parser.add_argument("list_file", nargs="?", default=DEFAULT_LIST_FILE)
    parser.add_argument("--force", action="store_true", help="Rebuild existing caches")
    return parser.parse_args()


def main():
    args = parse_args()
    from datasets import load_dataset, load_from_disk

    dataset_paths = load_dataset_paths(args.list_file)
    if not dataset_paths:
        print(f"No dataset paths found in {args.list_file}")
        return 1

    built = 0
    skipped = 0

    for dataset_path in dataset_paths:
        if not dataset_path.exists():
            print(f"[WARN] Missing dataset, skipping: {dataset_path}")
            continue

        cache_path = dataset_cache_path(dataset_path)
        if cache_path.exists() and not args.force:
            cached = load_from_disk(str(cache_path))
            print(f"[SKIP] {dataset_path} -> {cache_path} ({len(cached)} rows)")
            skipped += 1
            continue

        dataset = load_dataset("json", data_files=str(dataset_path), split="train")
        dataset = dataset.map(
            lambda _row, idx: {"distilled_source_index": idx},
            with_indices=True,
            desc=f"Annotating source indices for {dataset_path.name}",
            load_from_cache_file=False,
        )
        dataset.save_to_disk(str(cache_path))
        print(f"[OK] {dataset_path} -> {cache_path} ({len(dataset)} rows)")
        built += 1

    print(f"Prepared caches: built={built} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
