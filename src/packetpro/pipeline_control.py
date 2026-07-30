"""Pipeline pause/resume control and activity log."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packetpro.config import SUPPORTED_EXTENSIONS, AppConfig
from packetpro.utils import (
    atomic_write_text,
    explain_failure,
    read_error_sidecar,
    read_json,
    write_json,
)

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


def _is_supported_document(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def _unique_inbox_destination(inbox: Path, name: str) -> Path:
    dest = inbox / name
    if not dest.exists():
        return dest
    stem = Path(name).stem
    suffix = Path(name).suffix
    return inbox / f"{stem}_retry_{int(time.time())}{suffix}"


def _cleanup_failed_job(sidecar_path: Path, job: dict[str, Any]) -> None:
    if not job.get("read_in_place"):
        enhanced = Path(job.get("enhanced_path", ""))
        if enhanced.is_file():
            try:
                enhanced.unlink()
            except OSError:
                pass
    try:
        sidecar_path.unlink(missing_ok=True)
    except OSError:
        pass


def _reset_or_cleanup_failed_jobs(config: AppConfig) -> dict[str, int]:
    """Re-queue recoverable OCR failures; drop jobs that cannot be retried as-is."""
    reset = 0
    cleaned = 0
    skipped_indexed = 0
    if not config.transformed.is_dir():
        return {"reset": 0, "cleaned": 0, "skipped_indexed": 0}

    for sidecar_path in sorted(config.transformed.glob("*.json")):
        if sidecar_path.name.startswith(".archive_"):
            continue
        try:
            job = read_json(sidecar_path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if job.get("status") != "failed":
            continue

        name = str(job.get("original_name") or sidecar_path.name)
        raw_error = str(job.get("error") or "")
        reason = explain_failure(raw_error)
        page = int(job.get("page_number", 0)) or None
        enhanced = Path(job.get("enhanced_path", ""))
        original = Path(job.get("original_path", ""))
        lower = raw_error.lower()

        # Already in the index — re-OCR would hit the same unique constraint.
        if "unique constraint" in lower:
            _cleanup_failed_job(sidecar_path, job)
            skipped_indexed += 1
            log_activity(
                config,
                worker="system",
                action="skipped",
                message=(
                    f"Removed failed OCR job for {name}: already indexed. "
                    f"Reason: {reason}"
                ),
                file=name,
                page=page,
            )
            continue

        can_retry_in_place = (
            enhanced.is_file()
            and (bool(job.get("in_place")) or original.is_file())
        )
        if can_retry_in_place:
            job["status"] = "pending"
            job.pop("error", None)
            job.pop("ocr_backend", None)
            write_json(sidecar_path, job)
            reset += 1
            log_activity(
                config,
                worker="system",
                action="reprocess",
                message=(
                    f"Re-queued failed OCR job for {name} (page {page or 1}). "
                    f"Previous failure: {reason}"
                ),
                file=name,
                page=page,
            )
            continue

        _cleanup_failed_job(sidecar_path, job)
        cleaned += 1
        log_activity(
            config,
            worker="system",
            action="reprocess",
            message=(
                f"Cleared failed OCR job for {name}; original will re-enter from inbox if present. "
                f"Previous failure: {reason}"
            ),
            file=name,
            page=page,
        )

    return {"reset": reset, "cleaned": cleaned, "skipped_indexed": skipped_indexed}


def _failure_is_non_retriable(raw_error: str) -> bool:
    lower = (raw_error or "").lower()
    return (
        "duplicate file" in lower
        or "already processed" in lower
        or "already indexed" in lower
        or ("unique constraint" in lower and "job_id" in lower)
    )


def reprocess_failures(config: AppConfig) -> dict[str, Any]:
    """Move failed originals back to inbox and re-queue recoverable OCR jobs."""
    config.failed.mkdir(parents=True, exist_ok=True)
    config.inbox.mkdir(parents=True, exist_ok=True)

    moved: list[dict[str, str]] = []
    discarded: list[dict[str, str]] = []
    move_errors: list[dict[str, str]] = []

    failed_files = sorted(
        path for path in config.failed.iterdir() if _is_supported_document(path)
    )
    for source in failed_files:
        raw_error = read_error_sidecar(source) or "No failure reason was recorded."
        # Prefer the human explanation line if sidecar was written in new format.
        first_line = raw_error.splitlines()[0].strip() if raw_error else ""
        reason = explain_failure(first_line or raw_error)
        error_sidecar = source.with_suffix(source.suffix + ".error.txt")

        if _failure_is_non_retriable(raw_error) or _failure_is_non_retriable(reason):
            try:
                source.unlink(missing_ok=True)
                if error_sidecar.is_file():
                    error_sidecar.unlink(missing_ok=True)
            except OSError as exc:
                move_errors.append({"file": source.name, "error": str(exc)})
                log_activity(
                    config,
                    worker="system",
                    action="error",
                    message=(
                        f"Could not discard non-retriable failed file {source.name}: "
                        f"{explain_failure(exc)}"
                    ),
                    file=source.name,
                )
                continue
            discarded.append({"file": source.name, "reason": reason})
            log_activity(
                config,
                worker="system",
                action="skipped",
                message=(
                    f"Discarded failed file {source.name} without reprocessing "
                    f"(not retriable). Reason: {reason}"
                ),
                file=source.name,
            )
            continue

        dest = _unique_inbox_destination(config.inbox, source.name)
        try:
            shutil.move(str(source), str(dest))
            if error_sidecar.is_file():
                error_sidecar.unlink(missing_ok=True)
        except OSError as exc:
            move_errors.append({"file": source.name, "error": str(exc)})
            log_activity(
                config,
                worker="system",
                action="error",
                message=(
                    f"Could not requeue failed file {source.name}: {explain_failure(exc)}"
                ),
                file=source.name,
            )
            continue

        moved.append({"file": dest.name, "reason": reason})
        log_activity(
            config,
            worker="system",
            action="reprocess",
            message=(
                f"Moved failed file back to inbox for reprocessing: {dest.name}. "
                f"Previous failure: {reason}"
            ),
            file=dest.name,
        )

    # Drop orphaned .error.txt files left after a successful move/delete.
    if config.failed.is_dir():
        for sidecar in list(config.failed.glob("*.error.txt")):
            # Only remove if the matching document is gone.
            candidate = Path(str(sidecar)[: -len(".error.txt")])
            if not candidate.exists():
                try:
                    sidecar.unlink(missing_ok=True)
                except OSError:
                    pass

    job_stats = _reset_or_cleanup_failed_jobs(config)

    total = (
        len(moved)
        + len(discarded)
        + job_stats["reset"]
        + job_stats["cleaned"]
        + job_stats["skipped_indexed"]
    )
    if total == 0 and not move_errors:
        message = "No failed files or failed OCR jobs to reprocess."
    else:
        parts = [
            f"moved {len(moved)} file(s) to inbox",
            f"re-queued {job_stats['reset']} OCR job(s)",
            f"cleared {job_stats['cleaned']} unrecoverable job(s)",
        ]
        if discarded:
            parts.append(f"discarded {len(discarded)} non-retriable (duplicate/already indexed)")
        if job_stats["skipped_indexed"]:
            parts.append(
                f"removed {job_stats['skipped_indexed']} already-indexed job(s)"
            )
        if move_errors:
            parts.append(f"{len(move_errors)} move error(s)")
        message = "Reprocess failures — " + ", ".join(parts)

    log_activity(config, worker="system", action="reprocess", message=message)

    return {
        "ok": not move_errors,
        "message": message,
        "moved": len(moved),
        "moved_files": moved,
        "discarded": len(discarded),
        "discarded_files": discarded,
        "jobs_reset": job_stats["reset"],
        "jobs_cleaned": job_stats["cleaned"],
        "jobs_skipped_indexed": job_stats["skipped_indexed"],
        "move_errors": move_errors,
        "failed_remaining": sum(
            1 for path in config.failed.iterdir() if _is_supported_document(path)
        )
        if config.failed.is_dir()
        else 0,
    }


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
    worker: str | None = None,
) -> list[dict[str, Any]]:
    path = _activity_path(config)
    if not path.is_file():
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    valid_lines = [line for line in lines if line.strip()]
    worker_filter = worker.strip().lower() if worker else None

    entries: list[dict[str, Any]] = []
    for line in reversed(valid_lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not include_debug and entry.get("action") == "debug":
            continue
        if worker_filter and str(entry.get("worker", "")).lower() != worker_filter:
            continue
        entries.append(entry)
        if len(entries) >= limit:
            break
    entries.reverse()
    return entries