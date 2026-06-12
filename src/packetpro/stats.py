"""Pipeline stats, worker heartbeats, and GPU telemetry."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packetpro.config import SUPPORTED_EXTENSIONS, AppConfig

EVENT_RETENTION_SECONDS = 3600
HEARTBEAT_STALE_SECONDS = 15
OCR_PROCESS_HINTS = ("ollama", "llama-server", "packetpro")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stats_dir(config: AppConfig) -> Path:
    return config.data_root / ".packetpro"


def _events_path(config: AppConfig) -> Path:
    return _stats_dir(config) / "events.jsonl"


def _heartbeat_path(config: AppConfig, worker: str) -> Path:
    return _stats_dir(config) / f"heartbeat-{worker}.json"


def record_event(config: AppConfig, event_type: str, **fields: Any) -> None:
    path = _events_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": _utc_now(), "type": event_type, **fields}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def start_heartbeat_thread(config: AppConfig, worker: str, interval: float = 5.0) -> None:
    def loop() -> None:
        while True:
            write_heartbeat(config, worker, "watching")
            time.sleep(interval)

    thread = threading.Thread(target=loop, name=f"packetpro-heartbeat-{worker}", daemon=True)
    thread.start()


def write_heartbeat(config: AppConfig, worker: str, state: str, **fields: Any) -> None:
    path = _heartbeat_path(config, worker)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "worker": worker,
        "state": state,
        "pid": os.getpid(),
        "ts": _utc_now(),
        **fields,
    }
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    tmp.replace(path)


def _parse_ts(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _read_events(config: AppConfig) -> list[dict[str, Any]]:
    path = _events_path(config)
    if not path.is_file():
        return []

    cutoff = time.time() - EVENT_RETENTION_SECONDS
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(str(event.get("ts", "")))
        if ts is None or ts < cutoff:
            continue
        events.append(event)
    return events


def _count_in_window(events: list[dict[str, Any]], event_type: str, seconds: int) -> float:
    cutoff = time.time() - seconds
    count = 0
    for event in events:
        if event.get("type") != event_type:
            continue
        ts = _parse_ts(str(event.get("ts", "")))
        if ts is None or ts < cutoff:
            continue
        count += int(event.get("count", 1))
    minutes = seconds / 60
    return round(count / minutes, 2) if minutes else 0.0


def _count_supported_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(
        1
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def _count_pending_jobs(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    count = 0
    for path in folder.glob("*.json"):
        if path.name.startswith(".archive_"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("status") == "pending":
            count += 1
    return count


def _read_heartbeat(config: AppConfig, worker: str) -> dict[str, Any]:
    path = _heartbeat_path(config, worker)
    if not path.is_file():
        return {"worker": worker, "state": "offline", "alive": False}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"worker": worker, "state": "offline", "alive": False}

    ts = _parse_ts(str(data.get("ts", "")))
    alive = ts is not None and (time.time() - ts) <= HEARTBEAT_STALE_SECONDS
    data["alive"] = alive
    if not alive:
        data["state"] = "offline"
    return data


def _run_nvidia_smi(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["nvidia-smi", *args],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def get_gpu_stats() -> dict[str, Any]:
    gpu_lines = _run_nvidia_smi(
        [
            "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ]
    )
    proc_lines = _run_nvidia_smi(
        [
            "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    uuid_lines = _run_nvidia_smi(
        ["--query-gpu=index,uuid", "--format=csv,noheader,nounits"]
    )

    uuid_to_index: dict[str, str] = {}
    for line in uuid_lines.splitlines():
        parts = [part.strip() for part in line.split(",", 1)]
        if len(parts) == 2:
            uuid_to_index[parts[1]] = parts[0]

    gpus: list[dict[str, Any]] = []
    for line in gpu_lines.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue
        idx, name, mem_used, mem_total, util = parts[:5]
        gpus.append(
            {
                "index": int(idx),
                "name": name,
                "memory_used_mib": int(float(mem_used)),
                "memory_total_mib": int(float(mem_total)),
                "utilization_pct": int(float(util)),
                "processes": [],
            }
        )

    gpu_by_index = {gpu["index"]: gpu for gpu in gpus}
    relevant_processes: list[dict[str, Any]] = []

    for line in proc_lines.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        gpu_uuid, pid, process_name, mem = parts[:4]
        gpu_index = int(uuid_to_index.get(gpu_uuid, -1))
        entry = {
            "gpu_index": gpu_index,
            "pid": int(pid),
            "name": process_name,
            "memory_mib": int(float(mem)),
            "relevant": any(hint in process_name.lower() for hint in OCR_PROCESS_HINTS),
        }
        if gpu_index in gpu_by_index:
            gpu_by_index[gpu_index]["processes"].append(entry)
        if entry["relevant"]:
            relevant_processes.append(entry)

    return {
        "available": bool(gpus),
        "gpus": gpus,
        "relevant_processes": relevant_processes,
    }


def collect_stats(config: AppConfig) -> dict[str, Any]:
    events = _read_events(config)
    return {
        "ts": _utc_now(),
        "queues": {
            "inbox": _count_supported_files(config.inbox),
            "transformed_pending": _count_pending_jobs(config.transformed),
            "failed": _count_supported_files(config.failed),
        },
        "rates": {
            "ocr_per_minute_1m": _count_in_window(events, "ocr_complete", 60),
            "ocr_per_minute_5m": _count_in_window(events, "ocr_complete", 300),
            "enhance_per_minute_1m": _count_in_window(events, "enhanced", 60),
            "enhance_per_minute_5m": _count_in_window(events, "enhanced", 300),
        },
        "totals": {
            "enhanced_events_1h": sum(1 for e in events if e.get("type") == "enhanced"),
            "ocr_complete_events_1h": sum(1 for e in events if e.get("type") == "ocr_complete"),
        },
        "workers": {
            "enhance": _read_heartbeat(config, "enhance"),
            "ocr": _read_heartbeat(config, "ocr"),
        },
        "gpu": get_gpu_stats(),
    }