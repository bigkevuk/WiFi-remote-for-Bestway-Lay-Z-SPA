#!/usr/bin/env python3
"""Poll ESP8266 /info/ endpoint, append CSV samples, and emit soak-test alerts."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import signal
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple


INT_KEYS = {
    "Stack size": "stack_size",
    "Free Dram Heap": "free_dram_heap",
    "Min Dram Heap": "min_dram_heap",
    "Max free Dram block size": "max_free_dram_block_size",
    "Free Iram Heap": "free_iram_heap",
    "Max free Iram block size": "max_free_iram_block_size",
    "CPU fq": "cpu_mhz",
    "Cycle count": "cycle_count",
    "Free cont stack": "free_cont_stack",
    "Sketch size": "sketch_size",
    "Free sketch space": "free_sketch_space",
}
STR_KEYS = {
    "Core version": "core_version",
}
CSV_COLUMNS = [
    "ts_utc",
    "epoch_s",
    "http_ok",
    "error",
    "stack_size",
    "free_dram_heap",
    "min_dram_heap",
    "max_free_dram_block_size",
    "free_iram_heap",
    "max_free_iram_block_size",
    "cpu_mhz",
    "cycle_count",
    "free_cont_stack",
    "sketch_size",
    "free_sketch_space",
    "core_version",
    "alerts",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll /info/, write CSV samples, and print soak-test alerts."
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Device info endpoint, e.g. http://192.168.1.50/info/",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=15.0,
        help="Polling interval in seconds (default: 15).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="HTTP timeout in seconds (default: 5).",
    )
    parser.add_argument(
        "--csv",
        default="tools/soak/http_info_samples.csv",
        help="CSV output path (default: tools/soak/http_info_samples.csv).",
    )
    parser.add_argument(
        "--downtrend-window",
        type=int,
        default=20,
        help="Samples used to detect monotonic downward free-heap trend (default: 20).",
    )
    parser.add_argument(
        "--downtrend-min-drop",
        type=int,
        default=4096,
        help="Minimum free-heap drop (bytes) across downtrend window (default: 4096).",
    )
    parser.add_argument(
        "--max-block-drop-bytes",
        type=int,
        default=8192,
        help="Alert if max free block drops by this many bytes in one sample (default: 8192).",
    )
    parser.add_argument(
        "--max-block-drop-percent",
        type=float,
        default=0.20,
        help="Alert if max free block drops by at least this fraction in one sample (default: 0.20).",
    )
    parser.add_argument(
        "--min-free-heap",
        type=int,
        default=0,
        help="Optional absolute low-heap alert threshold in bytes (0 disables).",
    )
    parser.add_argument(
        "--http-fail-alert-after",
        type=int,
        default=3,
        help="Consecutive HTTP failures before alert (default: 3).",
    )
    return parser.parse_args()


def parse_esp_info(payload: str) -> Dict[str, object]:
    parsed: Dict[str, object] = {}
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        label, value = line.split(":", 1)
        label = label.strip()
        value = value.strip()
        if label in INT_KEYS:
            match = re.search(r"-?\d+", value)
            parsed[INT_KEYS[label]] = int(match.group(0)) if match else None
        elif label in STR_KEYS:
            parsed[STR_KEYS[label]] = value
    return parsed


def fetch_info(url: str, timeout_s: float) -> Tuple[Optional[Dict[str, object]], Optional[str]]:
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            text = response.read().decode("utf-8", errors="replace")
        return parse_esp_info(text), None
    except urllib.error.URLError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def ensure_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def downtrend_alert(history: Deque[int], min_drop: int) -> Optional[str]:
    if len(history) < 2:
        return None
    values = list(history)
    monotonic_non_increasing = all(cur <= prev for prev, cur in zip(values, values[1:]))
    drop = values[0] - values[-1]
    if monotonic_non_increasing and drop >= min_drop:
        return (
            f"steady_downward_heap_trend: drop={drop}B over {len(values)} samples"
        )
    return None


def max_block_drop_alert(
    prev_value: Optional[int],
    current_value: Optional[int],
    min_drop_bytes: int,
    min_drop_fraction: float,
) -> Optional[str]:
    if prev_value is None or current_value is None or prev_value <= 0:
        return None
    drop = prev_value - current_value
    if drop <= 0:
        return None
    drop_fraction = drop / float(prev_value)
    if drop >= min_drop_bytes and drop_fraction >= min_drop_fraction:
        return (
            f"sudden_max_block_drop: {prev_value}B->{current_value}B "
            f"(drop={drop}B, {drop_fraction:.1%})"
        )
    return None


def utc_now() -> Tuple[str, int]:
    now = dt.datetime.now(dt.timezone.utc)
    return now.isoformat(timespec="seconds"), int(now.timestamp())


def main() -> int:
    args = parse_args()
    csv_path = Path(args.csv)
    ensure_csv(csv_path)

    keep_running = True

    def _stop_handler(_sig: int, _frame: object) -> None:
        nonlocal keep_running
        keep_running = False

    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)

    heap_history: Deque[int] = deque(maxlen=max(2, args.downtrend_window))
    prev_max_block: Optional[int] = None
    consecutive_http_failures = 0

    print(
        f"Polling {args.url} every {args.interval:.1f}s, writing CSV: {csv_path}",
        flush=True,
    )

    with csv_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)

        while keep_running:
            ts_utc, epoch_s = utc_now()
            sample, error = fetch_info(args.url, args.timeout)
            alerts: List[str] = []
            row: Dict[str, object] = {key: "" for key in CSV_COLUMNS}

            row["ts_utc"] = ts_utc
            row["epoch_s"] = epoch_s

            if error is not None or sample is None:
                consecutive_http_failures += 1
                row["http_ok"] = 0
                row["error"] = error or "unknown_error"
                if consecutive_http_failures >= args.http_fail_alert_after:
                    alerts.append(
                        f"http_failures={consecutive_http_failures} last_error={row['error']}"
                    )
            else:
                consecutive_http_failures = 0
                row["http_ok"] = 1
                row["error"] = ""
                row.update(sample)

                free_heap = sample.get("free_dram_heap")
                if isinstance(free_heap, int):
                    heap_history.append(free_heap)
                    if args.min_free_heap > 0 and free_heap < args.min_free_heap:
                        alerts.append(
                            f"low_free_heap: {free_heap}B below threshold {args.min_free_heap}B"
                        )
                    if len(heap_history) == heap_history.maxlen:
                        trend = downtrend_alert(heap_history, args.downtrend_min_drop)
                        if trend:
                            alerts.append(trend)

                current_max_block = sample.get("max_free_dram_block_size")
                if isinstance(current_max_block, int):
                    max_drop = max_block_drop_alert(
                        prev_max_block,
                        current_max_block,
                        args.max_block_drop_bytes,
                        args.max_block_drop_percent,
                    )
                    if max_drop:
                        alerts.append(max_drop)
                    prev_max_block = current_max_block

            row["alerts"] = " | ".join(alerts)
            writer.writerow(row)
            fh.flush()

            info = (
                f"{ts_utc} heap={row.get('free_dram_heap', '')} "
                f"min={row.get('min_dram_heap', '')} "
                f"maxblk={row.get('max_free_dram_block_size', '')}"
            )
            if alerts:
                print(f"ALERT {info} :: {row['alerts']}", flush=True)
            else:
                print(info, flush=True)

            end_time = time.monotonic() + args.interval
            while keep_running and time.monotonic() < end_time:
                time.sleep(0.1)

    print("Stopped poller.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
