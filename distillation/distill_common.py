#!/usr/bin/env python3

from pathlib import Path
from typing import List

DEFAULT_LIST_FILE = "datasets_to_distill.txt"
DEFAULT_REQUEST_CONCURRENCY = 8
DEFAULT_MAX_ACTIVE_JOBS = 16


def normalize_dataset_path(raw_path: str) -> Path:
    path = raw_path.strip()
    return Path(path).expanduser().resolve()


def load_dataset_paths(list_file):
    list_path = Path(list_file).expanduser().resolve()
    paths: List[Path] = []
    with list_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            paths.append(normalize_dataset_path(line))
    return paths


def dataset_cache_path(dataset_path):
    src = Path(dataset_path)
    return src.with_name(f"{src.stem}.hf_distill_cache")


def model_tag(model: str) -> str:
    raw = (model or "").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in raw).strip("._-")
    return safe or "model"


def final_output_path(dataset_path, model):
    src = Path(dataset_path)
    return src.with_name(f"{src.stem}_distillation_{model_tag(model)}.jsonl")


def shard_output_path(dataset_path, shard_index, num_shards, model):
    src = Path(dataset_path)
    tag = model_tag(model)
    return src.with_name(
        f"{src.stem}_distillation_{tag}.shard-{shard_index:03d}-of-{num_shards:03d}.jsonl"
    )


def shard_job_name(dataset_path, shard_index, num_shards, model):
    stem = Path(dataset_path).stem.lower()
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in stem).strip("_")
    return f"distill-{model_tag(model)}-{safe}-s{shard_index + 1:03d}of{num_shards:03d}"


def suggested_num_shards(dataset_path):
    name = Path(dataset_path).name
    if name == "medmcqa_with_labels.jsonl":
        return 32
    if name == "afrimedqa_v2_full_15275_with_labels.jsonl":
        return 4
    if name == "medqa_with_labels_5opt.jsonl":
        return 4
    if name == "healthsearchqa_3375_no_labels.jsonl":
        return 2
    if name == "afrimedqa_v1_test_3000_with_labels.jsonl":
        return 2
    return 1


def iter_shard_specs(list_file):
    for dataset_path in load_dataset_paths(list_file):
        yield dataset_path, dataset_cache_path(dataset_path), suggested_num_shards(dataset_path)
