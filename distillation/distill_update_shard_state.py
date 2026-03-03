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


def parse_args():
    parser = argparse.ArgumentParser(description="Update shard state and write summary for pool distillation.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--action", choices=["success", "failure", "write-summary"], required=True)
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--shard-id", default="")
    parser.add_argument("--error-message", default="")
    parser.add_argument("--max-retries-per-shard", type=int, default=2)
    parser.add_argument("--workers-started", type=int, default=None)
    parser.add_argument("--workers-alive", type=int, default=None)
    parser.add_argument("--lock-timeout-seconds", type=int, default=30)
    parser.add_argument("--stale-lock-seconds", type=int, default=300)
    return parser.parse_args()


def load_rows(shards_path: Path) -> List[Dict[str, str]]:
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
    line = (
        f"{ts(now_utc())}\t{worker_id}\t{shard_id}\t{transition}\t"
        f"{message.replace(chr(9), ' ').replace(chr(10), ' ')}\n"
    )
    with (run_dir / "events.log").open("a", encoding="utf-8") as handle:
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


def compute_summary(rows: List[Dict[str, str]], workers_started: int, workers_alive: int) -> Dict[str, int]:
    total = len(rows)
    pending = sum(1 for row in rows if row.get("status") == "pending")
    leased = sum(1 for row in rows if row.get("status") == "leased")
    done = sum(1 for row in rows if row.get("status") == "done")
    failed = sum(1 for row in rows if row.get("status") == "failed")
    retried = sum(int(row.get("attempt", "0") or 0) for row in rows)
    return {
        "total": total,
        "pending": pending,
        "leased": leased,
        "done": done,
        "failed": failed,
        "retried": retried,
        "workers_started": workers_started,
        "workers_alive": workers_alive,
        "updated_at_utc": ts(now_utc()),
    }


def main():
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    shards_path = run_dir / "shards.tsv"
    summary_path = run_dir / "summary.json"
    if not run_dir.exists() or not shards_path.exists():
        print(f"Missing run state in {run_dir}", file=sys.stderr)
        return 1

    with QueueLock(run_dir, args.lock_timeout_seconds, args.stale_lock_seconds):
        rows = load_rows(shards_path)

        if args.action in {"success", "failure"}:
            if not args.shard_id or not args.worker_id:
                print("--shard-id and --worker-id are required for success/failure", file=sys.stderr)
                return 2
            target = None
            for row in rows:
                if row.get("shard_id") == args.shard_id:
                    target = row
                    break
            if target is None:
                print(f"Unknown shard id: {args.shard_id}", file=sys.stderr)
                return 1
            if target.get("status") != "leased":
                print(f"Shard {args.shard_id} is not leased", file=sys.stderr)
                return 1
            if target.get("lease_owner") != args.worker_id:
                print(
                    f"Shard {args.shard_id} leased by {target.get('lease_owner')} not {args.worker_id}",
                    file=sys.stderr,
                )
                return 1

            if args.action == "success":
                target["status"] = "done"
                target["lease_owner"] = ""
                target["lease_expires_utc"] = ""
                target["last_error"] = ""
                transition = "done"
                message = "processed successfully"
            else:
                prev_attempt = int(target.get("attempt", "0") or 0)
                next_attempt = prev_attempt + 1
                target["attempt"] = str(next_attempt)
                target["lease_owner"] = ""
                target["lease_expires_utc"] = ""
                clean_err = args.error_message.strip().replace("\n", " ")
                target["last_error"] = clean_err[:1000]
                if next_attempt <= args.max_retries_per_shard:
                    target["status"] = "pending"
                    transition = "retry_pending"
                    message = f"attempt={next_attempt}/{args.max_retries_per_shard}; {clean_err}"
                else:
                    target["status"] = "failed"
                    transition = "failed"
                    message = f"attempt={next_attempt}; {clean_err}"
            write_rows(shards_path, rows)
            append_event(run_dir, args.worker_id, args.shard_id, transition, message)

        workers_started = int(args.workers_started) if args.workers_started is not None else 0
        workers_alive = int(args.workers_alive) if args.workers_alive is not None else 0
        if summary_path.exists():
            try:
                existing = json.loads(summary_path.read_text(encoding="utf-8"))
                if args.workers_started is None:
                    workers_started = int(existing.get("workers_started", 0))
                if args.workers_alive is None:
                    workers_alive = int(existing.get("workers_alive", 0))
            except Exception:
                pass

        summary = compute_summary(rows, workers_started, workers_alive)
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
