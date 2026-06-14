"""Pipeline stats, worker heartbeats, and GPU telemetry."""

from __future__ import annotations

import json
import math
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packetpro.config import SUPPORTED_EXTENSIONS, AppConfig
from packetpro.utils import atomic_write_text

WATCH_STATS_STALE_SECONDS = 90

EVENT_RETENTION_SECONDS = 3600
HEARTBEAT_STALE_SECONDS = 15
OCR_PROCESS_HINTS = ("ollama", "llama-server", "packetpro", "paddle")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stats_dir(config: AppConfig) -> Path:
    return config.data_root / ".packetpro"


def _events_path(config: AppConfig) -> Path:
    return _stats_dir(config) / "events.jsonl"


def _heartbeat_path(config: AppConfig, worker: str) -> Path:
    return _stats_dir(config) / f"heartbeat-{worker}.json"


def _watch_stats_path(config: AppConfig) -> Path:
    return _stats_dir(config) / "watch-stats.json"


def write_watch_queue_stats(
    config: AppConfig,
    *,
    pending: int,
    available: bool,
) -> None:
    if config.watch_folder is None:
        return

    path = _watch_stats_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "watch_folder": str(config.watch_folder.resolve()),
        "folder_name": config.watch_folder.name,
        "pending": pending,
        "available": available,
        "ts": _utc_now(),
    }
    atomic_write_text(path, json.dumps(payload))


def _live_watch_pending_count(config: AppConfig) -> int:
    watch_root = config.watch_folder
    if watch_root is None or not watch_root.is_dir():
        return 0

    watermark = 0.0
    state_path = _stats_dir(config) / "watch-state.json"
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if data.get("watch_folder") == str(watch_root.resolve()):
                watermark = float(data.get("last_processed_mtime", 0.0))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    count = 0
    for path in watch_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            if path.stat().st_mtime > watermark:
                count += 1
        except OSError:
            continue
    return count


def _count_in_place_pending_jobs(folder: Path) -> int:
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
        if data.get("status") != "pending":
            continue
        if data.get("read_in_place") or data.get("in_place"):
            count += 1
    return count


def read_watch_queue_stats(config: AppConfig) -> dict[str, Any]:
    if config.watch_folder is None:
        return {
            "configured": False,
            "folder_name": "",
            "pending": 0,
            "ocr_pending": 0,
            "available": False,
        }

    folder_name = config.watch_folder.name
    available = config.watch_folder.is_dir()
    live_pending = _live_watch_pending_count(config)
    result = {
        "configured": True,
        "folder_name": folder_name,
        "pending": live_pending,
        "ocr_pending": _count_in_place_pending_jobs(config.transformed),
        "available": available,
    }

    path = _watch_stats_path(config)
    if not path.is_file():
        return result

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return result

    if data.get("watch_folder") != str(config.watch_folder.resolve()):
        return result

    ts = _parse_ts(str(data.get("ts", "")))
    stale = ts is None or (time.time() - ts) > WATCH_STATS_STALE_SECONDS
    if not stale:
        result["folder_name"] = str(data.get("folder_name") or folder_name)
        result["pending"] = int(data.get("pending", live_pending))
        result["available"] = bool(data.get("available", available))

    return result


def record_event(config: AppConfig, event_type: str, **fields: Any) -> None:
    path = _events_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"ts": _utc_now(), "type": event_type, **fields}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def start_heartbeat_thread(config: AppConfig, worker: str, interval: float = 5.0) -> None:
    def loop() -> None:
        from packetpro.pipeline_control import is_processing_enabled

        while True:
            try:
                state = "watching" if is_processing_enabled(config) else "paused"
                write_heartbeat(config, worker, state)
            except OSError:
                pass
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
    atomic_write_text(path, json.dumps(payload))


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


def _pick_rate_per_minute(rate_1m: float, rate_5m: float) -> float:
    if rate_1m > 0:
        return rate_1m
    return rate_5m


def _estimate_completion_seconds(pending: int, rate_per_minute: float) -> int | None:
    if pending <= 0:
        return 0
    if rate_per_minute <= 0:
        return None
    return int(math.ceil((pending / rate_per_minute) * 60))


def _format_duration_hms(seconds: int | None) -> str:
    if seconds is None:
        return "--:--:--"
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


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
    from packetpro.db import count_distinct_sources, count_documents, init_db
    from packetpro.ocr import describe_ocr_backends
    from packetpro.pipeline_control import get_control_state

    events = _read_events(config)
    init_db(config.database)
    indexed_documents = count_documents(config.database)
    indexed_sources = count_distinct_sources(config.database)
    ocr_pending = _count_pending_jobs(config.transformed)
    inbox_pending = _count_supported_files(config.inbox)
    watch_stats = read_watch_queue_stats(config)
    import_pending = inbox_pending + int(watch_stats.get("pending", 0))
    ocr_rate_1m = _count_in_window(events, "ocr_complete", 60)
    ocr_rate_5m = _count_in_window(events, "ocr_complete", 300)
    enhance_rate_1m = _count_in_window(events, "enhanced", 60)
    enhance_rate_5m = _count_in_window(events, "enhanced", 300)
    ocr_rate = _pick_rate_per_minute(ocr_rate_1m, ocr_rate_5m)
    enhance_rate = _pick_rate_per_minute(enhance_rate_1m, enhance_rate_5m)
    ocr_eta_seconds = _estimate_completion_seconds(ocr_pending, ocr_rate)
    import_eta_seconds = _estimate_completion_seconds(import_pending, enhance_rate)
    if ocr_pending <= 0 and import_pending <= 0:
        total_eta_seconds: int | None = 0
    elif ocr_eta_seconds is None and import_eta_seconds is None:
        total_eta_seconds = None
    else:
        total_eta_seconds = (ocr_eta_seconds or 0) + (import_eta_seconds or 0)
    control = get_control_state(config)
    return {
        "ts": _utc_now(),
        "database": {
            "indexed_documents": indexed_documents,
            "indexed_sources": indexed_sources,
        },
        "queues": {
            "inbox": inbox_pending,
            "transformed_pending": ocr_pending,
            "watch_ocr_pending": _count_in_place_pending_jobs(config.transformed),
            "import_pending": import_pending,
            "failed": _count_supported_files(config.failed),
        },
        "rates": {
            "ocr_per_minute_1m": ocr_rate_1m,
            "ocr_per_minute_5m": ocr_rate_5m,
            "enhance_per_minute_1m": enhance_rate_1m,
            "enhance_per_minute_5m": enhance_rate_5m,
        },
        "completion": {
            "eta_seconds": total_eta_seconds,
            "eta_hms": _format_duration_hms(total_eta_seconds),
            "ocr_pending": ocr_pending,
            "import_pending": import_pending,
            "processing_enabled": bool(control.get("processing_enabled", True)),
        },
        "control": control,
        "totals": {
            "enhanced_events_1h": sum(1 for e in events if e.get("type") == "enhanced"),
            "ocr_complete_events_1h": sum(1 for e in events if e.get("type") == "ocr_complete"),
            "duplicate_skipped_1h": sum(1 for e in events if e.get("type") == "duplicate_skipped"),
        },
        "workers": {
            "enhance": _read_heartbeat(config, "enhance"),
            "ocr": _read_heartbeat(config, "ocr"),
            "watch": _read_heartbeat(config, "watch"),
        },
        "watch": read_watch_queue_stats(config),
        "ocr_backends": describe_ocr_backends(config.ocr),
        "gpu": get_gpu_stats(),
    }