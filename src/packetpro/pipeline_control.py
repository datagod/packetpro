"""Pipeline pause/resume control and activity log."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packetpro.config import AppConfig
from packetpro.utils import atomic_write_text, write_json

ACTIVITY_MAX_ENTRIES = 500
ACTIVITY_DEFAULT_LIMIT = 150

WORKER_SERVICES: tuple[tuple[str, str], ...] = (
    ("enhance", "packetpro-enhance"),
    ("ocr", "packetpro-ocr"),
    ("watch", "packetpro-watch"),
)


def _control_dir(config: AppConfig) -> Path:
    return config.data_root / ".packetpro"


def _control_path(config: AppConfig) -> Path:
    return _control_dir(config) / "control.json"


def _activity_path(config: AppConfig) -> Path:
    return _control_dir(config) / "activity.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def is_processing_enabled(config: AppConfig) -> bool:
    path = _control_path(config)
    if not path.is_file():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    return bool(data.get("processing_enabled", True))


def get_control_state(config: AppConfig) -> dict[str, Any]:
    enabled = is_processing_enabled(config)
    path = _control_path(config)
    updated_at = None
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            updated_at = data.get("updated_at")
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "processing_enabled": enabled,
        "updated_at": updated_at,
    }


def set_processing_enabled(config: AppConfig, enabled: bool) -> dict[str, Any]:
    now = _utc_now()
    _write_json_atomic(
        _control_path(config),
        {
            "processing_enabled": enabled,
            "updated_at": now,
        },
    )
    from packetpro.config import SUPPORTED_EXTENSIONS

    inbox_count = 0
    if config.inbox.is_dir():
        inbox_count = sum(
            1
            for path in config.inbox.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    if enabled:
        message = f"Processing started — {inbox_count} file(s) in inbox"
    else:
        message = f"Processing stopped — {inbox_count} file(s) waiting in inbox"
    log_activity(config, worker="system", action="control", message=message)
    return get_control_state(config)


def _start_worker_service(service: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "start", service],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError:
        return False, "systemctl not available"
    except subprocess.TimeoutExpired:
        return False, "systemctl timed out"

    if result.returncode == 0:
        return True, "started"
    detail = (result.stderr or result.stdout or "start failed").strip()
    return False, detail


def kickstart_pipeline(config: AppConfig) -> dict[str, Any]:
    """Enable processing, start offline workers, and queue a watch-folder scan."""
    from packetpro.stats import read_heartbeat
    from packetpro.workers.watch_worker import request_watch_folder_scan

    actions: list[dict[str, Any]] = []
    summary_parts: list[str] = []
    workers_failed = False

    for worker, service in WORKER_SERVICES:
        heartbeat = read_heartbeat(config, worker)
        if heartbeat.get("alive"):
            actions.append(
                {
                    "kind": "worker",
                    "worker": worker,
                    "service": service,
                    "started": False,
                    "already_running": True,
                }
            )
            continue

        started, detail = _start_worker_service(service)
        actions.append(
            {
                "kind": "worker",
                "worker": worker,
                "service": service,
                "started": started,
                "already_running": False,
                "detail": detail,
            }
        )
        if started:
            summary_parts.append(f"started {service}")
        else:
            workers_failed = True
            summary_parts.append(f"could not start {service}")

    already_enabled = is_processing_enabled(config)
    if not already_enabled:
        set_processing_enabled(config, True)
        summary_parts.append("enabled processing")
    else:
        summary_parts.append("processing already enabled")
    actions.append(
        {
            "kind": "processing",
            "enabled": True,
            "already_enabled": already_enabled,
        }
    )

    watch_scan = request_watch_folder_scan(config)
    actions.append({"kind": "watch_scan", **watch_scan})
    if watch_scan.get("started"):
        summary_parts.append(watch_scan.get("message", "watch scan queued"))
    elif watch_scan.get("configured"):
        summary_parts.append(watch_scan.get("message", "watch folder checked"))

    message = "Kickstarted pipeline"
    if summary_parts:
        message = f"{message} — {'; '.join(summary_parts)}"
    log_activity(config, worker="system", action="kickstart", message=message)

    return {
        "ok": not workers_failed,
        "message": message,
        "actions": actions,
        "control": get_control_state(config),
        "watch_scan": watch_scan,
    }


def log_debug(
    config: AppConfig,
    *,
    worker: str,
    message: str,
    file: str | None = None,
) -> None:
    log_activity(config, worker=worker, action="debug", message=message, file=file)


def log_activity(
    config: AppConfig,
    *,
    worker: str,
    action: str,
    message: str,
    file: str | None = None,
    page: int | None = None,
) -> None:
    path = _activity_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _utc_now(),
        "worker": worker,
        "action": action,
        "message": message,
    }
    if file:
        entry["file"] = file
    if page is not None:
        entry["page"] = page

    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")

    _trim_activity_log(path)


def _trim_activity_log(path: Path) -> None:
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= ACTIVITY_MAX_ENTRIES:
        return
    trimmed = lines[-ACTIVITY_MAX_ENTRIES:]
    content = "\n".join(trimmed)
    if trimmed:
        content += "\n"
    atomic_write_text(path, content)


def read_activity(
    config: AppConfig,
    limit: int = ACTIVITY_DEFAULT_LIMIT,
    *,
    include_debug: bool = False,
) -> list[dict[str, Any]]:
    path = _activity_path(config)
    if not path.is_file():
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    valid_lines = [line for line in lines if line.strip()]

    entries: list[dict[str, Any]] = []
    for line in reversed(valid_lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not include_debug and entry.get("action") == "debug":
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    entries.reverse()
    return entries