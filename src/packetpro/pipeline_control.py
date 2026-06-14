"""Pipeline pause/resume control and activity log."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packetpro.config import AppConfig
from packetpro.utils import atomic_write_text, write_json

ACTIVITY_MAX_ENTRIES = 500
ACTIVITY_DEFAULT_LIMIT = 150


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