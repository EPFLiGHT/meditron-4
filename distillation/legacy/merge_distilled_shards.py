#!/usr/bin/env python3

import argparse
import math
import datetime as dt
import hashlib
import json
import subprocess

from distill_common import DEFAULT_LIST_FILE, final_output_path, load_dataset_paths, model_tag


def parse_args():
    parser = argparse.ArgumentParser(description="Merge distilled shard outputs into the final JSONL.")
    parser.add_argument("--dataset-path", help="Single source dataset path to merge")
    parser.add_argument("--list-file", default=None, help="Merge all datasets from a list file")
    parser.add_argument("--model", required=True, help="Model name used for distillation (for file matching)")
    parser.add_argument("--strict-repro", action="store_true", help="Fail if shard manifests/config hashes are missing")
    return parser.parse_args()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit_sha():
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL)
            .strip()
        )
    except Exception:  # noqa: BLE001
        return None


def completion_key(row):
    row_id = row.get("id")
    if row_id is not None and str(row_id).strip() != "":
        return f"id:{row_id}"
    prompt = row.get("distilled_prompt_used")
    if isinstance(prompt, str) and prompt.strip():
        import hashlib

        return "prompt_sha1:" + hashlib.sha1(prompt.encode("utf-8")).hexdigest()
    return f"source_index:{row.get('distilled_source_index')}"


def _normalize_row_for_merge(row):
    # Normalize id to a canonical string so mixed int/float/string shard schemas align.
    value = row.get("id")
    if value is None:
        row["id"] = None
        return row

    if isinstance(value, float):
        if math.isnan(value):
            row["id"] = None
            return row
        if value.is_integer():
            row["id"] = str(int(value))
            return row

    text = str(value).strip()
    row["id"] = text or None
    return row


def _normalize_scalar_to_string(value):
    if value is None:
        return None
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if value.is_integer():
            return str(int(value))
    text = str(value).strip()
    return text or None


def _value_dtype(feature):
    # datasets.Value exposes a dtype attribute; nested features do not.
    return getattr(feature, "dtype", None)


def merge_one(dataset_path, model, strict_repro=False):
    from datasets import Value, concatenate_datasets, load_dataset

    shard_glob = f"{dataset_path.stem}_distillation_{model_tag(model)}.shard-*-of-*.jsonl"
    shard_files = sorted(dataset_path.parent.glob(shard_glob))
    if not shard_files:
        print(f"[WARN] No shard files found for {dataset_path}")
        return 0

    shard_datasets = []
    shard_manifests = []
    repro_hashes = set()
    for path in shard_files:
        if strict_repro:
            manifest_path = path.with_suffix(path.suffix + ".manifest.json")
            if manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                shard_manifests.append(
                    {
                        "path": str(manifest_path),
                        "repro_config_sha256": manifest.get("repro_config_sha256"),
                    }
                )
                value = manifest.get("repro_config_sha256")
                if isinstance(value, str) and value.strip():
                    repro_hashes.add(value.strip())
            else:
                raise RuntimeError(f"Missing shard manifest for strict reproducibility: {manifest_path}")

        shard_ds = load_dataset("json", data_files=str(path), split="train")
        if "id" in shard_ds.column_names:
            shard_ds = shard_ds.map(
                _normalize_row_for_merge,
                desc=f"Normalizing id type in {path.name}",
            )
            shard_ds = shard_ds.cast_column("id", Value("string"))
        shard_datasets.append(shard_ds)

    # Normalize any conflicting scalar feature types across shards.
    all_columns = set()
    for shard_ds in shard_datasets:
        all_columns.update(shard_ds.column_names)

    conflict_columns = []
    for column in sorted(all_columns):
        dtypes = set()
        for shard_ds in shard_datasets:
            feature = shard_ds.features.get(column) if column in shard_ds.features else None
            dtype = _value_dtype(feature)
            if dtype is not None:
                dtypes.add(dtype)
        if len(dtypes) > 1:
            conflict_columns.append(column)

    if conflict_columns:
        print(f"[INFO] Normalizing conflicting scalar columns to string: {', '.join(conflict_columns)}")
        normalized = []
        for shard_ds in shard_datasets:
            for column in conflict_columns:
                if column not in shard_ds.column_names:
                    continue
                shard_ds = shard_ds.map(
                    lambda row, c=column: {c: _normalize_scalar_to_string(row.get(c))},
                    desc=f"Normalizing {column} type",
                )
                shard_ds = shard_ds.cast_column(column, Value("string"))
            normalized.append(shard_ds)
        shard_datasets = normalized

    merged = concatenate_datasets(shard_datasets) if len(shard_datasets) > 1 else shard_datasets[0]
    if "distilled_source_index" in merged.column_names:
        merged = merged.sort("distilled_source_index")

    seen_keys = set()
    merged = merged.filter(
        lambda row: completion_key(row) not in seen_keys and not seen_keys.add(completion_key(row)),
        desc=f"Deduplicating merged shards for {dataset_path.name}",
    )

    output_path = final_output_path(dataset_path, model)
    merged.to_json(str(output_path))
    print(f"[OK] Merged {len(shard_files)} shards into {output_path} ({len(merged)} rows)")

    if strict_repro and len(repro_hashes) != 1:
        raise RuntimeError(
            f"Strict reproducibility requires exactly one repro config hash for {dataset_path}, got {sorted(repro_hashes)}"
        )

    if strict_repro:
        manifest = {
            "type": "distillation_merged_manifest",
            "output_path": str(output_path),
            "dataset_path": str(dataset_path),
            "model": model,
            "num_rows": len(merged),
            "num_shards": len(shard_files),
            "shard_files": [str(p) for p in shard_files],
            "shard_manifests": shard_manifests,
            "repro_config_sha256_values": sorted(repro_hashes),
            "strict_repro": True,
            "output_sha256": file_sha256(output_path),
            "git_commit_sha": git_commit_sha(),
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        manifest_path = output_path.with_suffix(output_path.suffix + ".manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[OK] Wrote merged manifest: {manifest_path}")
    return len(merged)


def main():
    args = parse_args()

    dataset_paths = []
    if args.dataset_path:
        from distill_common import normalize_dataset_path

        dataset_paths = [normalize_dataset_path(args.dataset_path)]
    else:
        list_file = args.list_file or DEFAULT_LIST_FILE
        dataset_paths = load_dataset_paths(list_file)

    if not dataset_paths:
        print("No dataset paths to merge")
        return 1

    for dataset_path in dataset_paths:
        merge_one(dataset_path, args.model, strict_repro=args.strict_repro)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
