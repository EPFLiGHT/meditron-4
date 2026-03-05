#!/usr/bin/env python3

import argparse
import csv
import datetime as dt
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Dict, List

UTC = dt.timezone.utc
DATE_FMT = "%Y-%m-%dT%H:%M:%S.%f%z"
LOCK_NAME = "queue.lock"
LOCK_META = "owner.json"


def now_utc() -> dt.datetime:
    return dt.datetime.now(UTC)


def ts(dt_obj: dt.datetime) -> str:
    return dt_obj.strftime(DATE_FMT)


def parse_ts(value: str) -> dt.datetime:
    if not value:
        return dt.datetime.min.replace(tzinfo=UTC)
    return dt.datetime.strptime(value, DATE_FMT)


def parse_args():
    parser = argparse.ArgumentParser(description="Claim or heartbeat shard leases for pool distillation.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument(
        "--action",
        choices=["claim", "heartbeat"],
        default="claim",
        help="claim: reclaim expiries + claim next pending shard; heartbeat: extend lease for active shard",
    )
    parser.add_argument("--shard-id", default="", help="Required for --action heartbeat")
    parser.add_argument("--lease-timeout-seconds", type=int, default=7200)
    parser.add_argument("--lock-timeout-seconds", type=int, default=30)
    parser.add_argument("--stale-lock-seconds", type=int, default=300)
    return parser.parse_args()


def load_rows(shards_path: Path) -> List[Dict[str, str]]:
    if not shards_path.exists():
        raise FileNotFoundError(f"Missing shards file: {shards_path}")
    with shards_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return list(reader)


def write_rows(shards_path: Path, rows: List[Dict[str, str]]):
    fieldnames = [
        "shard_id",
        "dataset_path",
        "dataset_cache",
        "shard_index",
        "num_shards",
        "attempt",
        "status",
        "lease_owner",
        "lease_expires_utc",
        "last_error",
    ]
    with shards_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def append_event(run_dir: Path, worker_id: str, shard_id: str, transition: str, message: str):
    event_path = run_dir / "events.log"
    line = (
        f"{ts(now_utc())}\t{worker_id}\t{shard_id}\t{transition}\t"
        f"{message.replace(chr(9), ' ').replace(chr(10), ' ')}\n"
    )
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


class QueueLock:
    def __init__(self, run_dir: Path, timeout_seconds: int, stale_lock_seconds: int):
        self.run_dir = run_dir
        self.lock_dir = run_dir / LOCK_NAME
        self.meta_path = self.lock_dir / LOCK_META
        self.timeout_seconds = timeout_seconds
        self.stale_lock_seconds = stale_lock_seconds

    def _clear_stale_lock(self):
        if not self.lock_dir.exists():
            return
        try:
            age = time.time() - self.lock_dir.stat().st_mtime
        except OSError:
            return
        if age < self.stale_lock_seconds:
            return
        try:
            if self.meta_path.exists():
                self.meta_path.unlink()
            self.lock_dir.rmdir()
        except OSError:
            return

    def __enter__(self):
        deadline = time.time() + self.timeout_seconds
        owner_payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "acquired_at_utc": ts(now_utc()),
        }
        while True:
            try:
                self.lock_dir.mkdir()
                self.meta_path.write_text(json.dumps(owner_payload, indent=2) + "\n", encoding="utf-8")
                return self
            except FileExistsError:
                self._clear_stale_lock()
                if time.time() > deadline:
                    raise TimeoutError(f"Timed out acquiring queue lock at {self.lock_dir}")
                time.sleep(0.2)

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.meta_path.exists():
                self.meta_path.unlink()
            self.lock_dir.rmdir()
        except OSError:
            pass
        return False


def reclaim_expired(rows: List[Dict[str, str]], now: dt.datetime) -> int:
    reclaimed = 0
    for row in rows:
        if row.get("status") != "leased":
            continue
        expires = parse_ts(row.get("lease_expires_utc", ""))
        if expires >= now:
            continue
        row["status"] = "pending"
        row["lease_owner"] = ""
        row["lease_expires_utc"] = ""
        reclaimed += 1
    return reclaimed


def action_claim(args, run_dir: Path, shards_path: Path):
    with QueueLock(run_dir, args.lock_timeout_seconds, args.stale_lock_seconds):
        rows = load_rows(shards_path)
        now = now_utc()
        reclaimed = reclaim_expired(rows, now)

        pending_sorted = sorted(
            [row for row in rows if row.get("status") == "pending"],
            key=lambda row: (row.get("dataset_path", ""), int(row.get("shard_index", "0"))),
        )
        if not pending_sorted:
            if reclaimed:
                write_rows(shards_path, rows)
                append_event(run_dir, args.worker_id, "-", "reclaim_expired", f"reclaimed={reclaimed}")
            print("0")
            return 0

        chosen = pending_sorted[0]
        chosen["status"] = "leased"
        chosen["lease_owner"] = args.worker_id
        chosen["lease_expires_utc"] = ts(now + dt.timedelta(seconds=args.lease_timeout_seconds))
        write_rows(shards_path, rows)

    append_event(
        run_dir,
        args.worker_id,
        chosen["shard_id"],
        "claimed",
        f"attempt={chosen.get('attempt', '0')}; lease_timeout={args.lease_timeout_seconds}s",
    )
    print(
        "\t".join(
            [
                "1",
                chosen.get("shard_id", ""),
                chosen.get("dataset_path", ""),
                chosen.get("dataset_cache", ""),
                chosen.get("shard_index", ""),
                chosen.get("num_shards", ""),
                chosen.get("attempt", "0"),
            ]
        )
    )
    return 0


def action_heartbeat(args, run_dir: Path, shards_path: Path):
    if not args.shard_id:
        print("--shard-id is required for --action heartbeat", file=sys.stderr)
        return 2

    updated = False
    with QueueLock(run_dir, args.lock_timeout_seconds, args.stale_lock_seconds):
        rows = load_rows(shards_path)
        now = now_utc()
        reclaim_expired(rows, now)
        for row in rows:
            if row.get("shard_id") != args.shard_id:
                continue
            if row.get("status") != "leased":
                break
            if row.get("lease_owner") != args.worker_id:
                break
            row["lease_expires_utc"] = ts(now + dt.timedelta(seconds=args.lease_timeout_seconds))
            updated = True
            break
        write_rows(shards_path, rows)

    if updated:
        print("1")
        return 0
    print("0")
    return 1


def main():
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    shards_path = run_dir / "shards.tsv"
    if not run_dir.exists():
        print(f"Run dir does not exist: {run_dir}", file=sys.stderr)
        return 1

    if args.action == "claim":
        return action_claim(args, run_dir, shards_path)
    return action_heartbeat(args, run_dir, shards_path)


if __name__ == "__main__":
    raise SystemExit(main())
