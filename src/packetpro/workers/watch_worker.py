"""Watch an external folder and process new files in place."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from watchfiles import Change, watch

from packetpro.config import IMAGE_EXTENSIONS, AppConfig, ensure_data_dirs
from packetpro.pipeline_control import log_activity, log_debug
from packetpro.stats import (
    record_event,
    start_heartbeat_thread,
    write_heartbeat,
    write_watch_queue_stats,
)
from packetpro.utils import (
    ensure_stable_file,
    file_settling_unnecessary,
    utc_now,
    write_json,
)
from packetpro.workers.enhance_worker import is_known_duplicate, process_file

console = Console()
IMPORT_LOG_NAME = "imported.jsonl"
STATE_FILE_NAME = "watch-state.json"
SCAN_REQUEST_NAME = "watch-scan-request.json"
MIN_PROCESSED_MTIME = 0.0
SCAN_INTERVAL = 30.0
SCAN_REQUEST_POLL_INTERVAL = 2.0
UNAVAILABLE_LOG_INTERVAL = 120.0
_import_lock = threading.Lock()
_scan_lock = threading.Lock()
_last_unavailable_log = 0.0


@dataclass
class WatchState:
    watch_root: Path
    last_processed_mtime: float
    lock: threading.Lock


def _is_supported(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS


def _state_path(config: AppConfig) -> Path:
    return config.data_root / ".packetpro" / STATE_FILE_NAME


def _import_log_path(config: AppConfig) -> Path:
    return config.data_root / ".packetpro" / IMPORT_LOG_NAME


def _scan_request_path(config: AppConfig) -> Path:
    return config.data_root / ".packetpro" / SCAN_REQUEST_NAME


def _load_watch_state(config: AppConfig, watch_root: Path) -> tuple[WatchState, bool]:
    resolved_root = watch_root.resolve()
    state_file = _state_path(config)

    if state_file.is_file():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if data.get("watch_folder") == str(resolved_root):
                return (
                    WatchState(
                        watch_root=resolved_root,
                        last_processed_mtime=float(
                            data.get("last_processed_mtime", MIN_PROCESSED_MTIME)
                        ),
                        lock=threading.Lock(),
                    ),
                    False,
                )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    return (
        WatchState(
            watch_root=resolved_root,
            last_processed_mtime=MIN_PROCESSED_MTIME,
            lock=threading.Lock(),
        ),
        True,
    )


def _save_watch_state(config: AppConfig, state: WatchState) -> None:
    with state.lock:
        payload = {
            "watch_folder": str(state.watch_root),
            "last_processed_mtime": state.last_processed_mtime,
            "updated_at": utc_now(),
        }
    write_json(_state_path(config), payload)


def _advance_watermark(state: WatchState, config: AppConfig, mtime: float) -> None:
    with state.lock:
        state.last_processed_mtime = max(state.last_processed_mtime, mtime)
    _save_watch_state(config, state)


def _is_newer_than_watermark(source: Path, state: WatchState) -> bool:
    with state.lock:
        watermark = state.last_processed_mtime
    return source.stat().st_mtime > watermark


def _record_processed(
    config: AppConfig,
    *,
    source: Path,
    mtime: float,
) -> None:
    path = _import_log_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": utc_now(),
        "source": str(source),
        "mtime": mtime,
    }
    with _import_lock:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")


def _list_watch_files(watch_root: Path) -> list[Path]:
    if not watch_root.is_dir():
        return []
    return sorted(
        path
        for path in watch_root.rglob("*")
        if _is_supported(path)
    )


def _list_pending_watch_files(watch_root: Path, state: WatchState) -> list[Path]:
    if not watch_root.is_dir():
        return []
    pending = [
        path
        for path in _list_watch_files(watch_root)
        if _is_newer_than_watermark(path, state)
    ]
    # Oldest first so advancing the watermark during a batch scan does not
    # skip files that are still waiting in the same scan pass.
    return sorted(pending, key=lambda path: (path.stat().st_mtime, path.name))


def _count_pending_watch_files(watch_root: Path, state: WatchState) -> int:
    return len(_list_pending_watch_files(watch_root, state))


def _write_scan_request(config: AppConfig) -> None:
    write_json(
        _scan_request_path(config),
        {
            "requested_at": utc_now(),
            "reason": "manual",
        },
    )


def _consume_scan_request(config: AppConfig) -> bool:
    path = _scan_request_path(config)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def _describe_watermark(mtime: float) -> dict[str, Any]:
    if mtime <= MIN_PROCESSED_MTIME:
        return {
            "mtime": mtime,
            "label": "all files (no prior watermark)",
            "iso": None,
            "local": None,
        }

    dt_utc = datetime.fromtimestamp(mtime, tz=timezone.utc)
    dt_local = datetime.fromtimestamp(mtime).astimezone()
    return {
        "mtime": mtime,
        "label": dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "iso": dt_utc.isoformat(),
        "local": dt_local.strftime("%Y-%m-%d %H:%M"),
    }


def _local_timezone() -> timezone:
    return datetime.now().astimezone().tzinfo or timezone.utc


def _parse_watermark_since(
    *,
    since: str | None = None,
    all_files: bool = False,
    days_back: int | None = None,
) -> float:
    if all_files:
        return MIN_PROCESSED_MTIME

    if days_back is not None:
        if days_back < 0:
            raise ValueError("days_back must be zero or positive")
        anchor = datetime.now().astimezone() - timedelta(days=days_back)
        return anchor.timestamp()

    if since is None or not str(since).strip():
        raise ValueError("Provide since, days_back, or all_files")

    cleaned = str(since).strip()
    if len(cleaned) == 10 and cleaned[4] == "-" and cleaned[7] == "-":
        dt = datetime.strptime(cleaned, "%Y-%m-%d")
        dt = dt.replace(tzinfo=_local_timezone())
        return dt.timestamp()

    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_timezone())
    return dt.timestamp()


def reload_watch_state_from_disk(config: AppConfig, state: WatchState) -> bool:
    """Apply watermark changes written by the web UI or another process."""
    state_file = _state_path(config)
    if not state_file.is_file():
        return False

    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        if data.get("watch_folder") != str(state.watch_root):
            return False
        new_mtime = float(data.get("last_processed_mtime", MIN_PROCESSED_MTIME))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return False

    with state.lock:
        if new_mtime == state.last_processed_mtime:
            return False
        state.last_processed_mtime = new_mtime
        return True


def get_watch_watermark_status(config: AppConfig) -> dict[str, Any]:
    watch_root = config.watch_folder
    if watch_root is None:
        return {
            "configured": False,
            "message": "Watch folder is not configured",
        }

    resolved = watch_root.resolve()
    state, first_examination = _load_watch_state(config, resolved)
    return {
        "configured": True,
        "watch_folder": str(resolved),
        "folder_name": resolved.name,
        "available": resolved.is_dir(),
        "first_examination": first_examination,
        "watermark": _describe_watermark(state.last_processed_mtime),
        "ts": utc_now(),
    }


def set_watch_watermark(
    config: AppConfig,
    *,
    since: str | None = None,
    all_files: bool = False,
    days_back: int | None = None,
) -> dict[str, Any]:
    watch_root = config.watch_folder
    if watch_root is None:
        return {
            "configured": False,
            "message": "Watch folder is not configured",
        }

    resolved = watch_root.resolve()
    mtime = _parse_watermark_since(
        since=since,
        all_files=all_files,
        days_back=days_back,
    )
    state = WatchState(
        watch_root=resolved,
        last_processed_mtime=mtime,
        lock=threading.Lock(),
    )
    _save_watch_state(config, state)
    watermark = _describe_watermark(mtime)

    available = resolved.is_dir()
    pending = _count_pending_watch_files(resolved, state) if available else 0
    _refresh_watch_queue_stats(config, state)

    message = (
        f"Scan start set to {watermark['label']}; {pending} image file(s) now eligible"
        if available
        else f"Scan start set to {watermark['label']}; watch folder unavailable"
    )
    log_activity(
        config,
        worker="watch",
        action="watermark",
        message=message,
    )
    log_debug(
        config,
        worker="watch",
        message=f"Watch watermark updated: mtime {watermark['mtime']:.0f}, pending {pending}",
    )

    return {
        "configured": True,
        "watch_folder": str(resolved),
        "folder_name": resolved.name,
        "available": available,
        "watermark": watermark,
        "pending": pending,
        "message": message,
        "ts": utc_now(),
    }


def request_watch_folder_scan(config: AppConfig) -> dict[str, Any]:
    """Queue a manual watch-folder scan in the watch worker."""
    watch_root = config.watch_folder
    if watch_root is None:
        return {
            "configured": False,
            "message": "Watch folder is not configured",
        }

    resolved = watch_root.resolve()
    available = resolved.is_dir()
    state, first_examination = _load_watch_state(config, resolved)
    watermark = _describe_watermark(state.last_processed_mtime)
    pending_files = _list_pending_watch_files(resolved, state) if available else []
    pending = len(pending_files)

    started = available and pending > 0
    if started:
        _write_scan_request(config)
        message = f"Processing {pending} file(s) since {watermark['label']}"
    elif available:
        message = f"No new files since {watermark['label']}"
    else:
        message = f"Watch folder is not available: {resolved}"

    _refresh_watch_queue_stats(config, state)
    log_activity(
        config,
        worker="watch",
        action="scanning" if started else "scan",
        message=message,
    )
    log_debug(
        config,
        worker="watch",
        message=(
            f"Manual watch scan requested: started={started}, pending={pending}, "
            f"watermark mtime {watermark['mtime']:.0f}"
        ),
    )

    return {
        "configured": True,
        "watch_folder": str(resolved),
        "folder_name": resolved.name,
        "available": available,
        "first_examination": first_examination,
        "watermark": watermark,
        "pending": pending,
        "started": started,
        "message": message,
        "ts": utc_now(),
    }


def _refresh_watch_queue_stats(config: AppConfig, state: WatchState) -> None:
    watch_root = config.watch_folder
    if watch_root is None:
        return
    available = watch_root.is_dir()
    pending = _count_pending_watch_files(watch_root, state) if available else 0
    write_watch_queue_stats(config, pending=pending, available=available)


def _process_watch_file(
    config: AppConfig,
    source: Path,
    state: WatchState,
) -> bool:
    reload_watch_state_from_disk(config, state)

    if not _is_supported(source):
        return False

    if not _is_newer_than_watermark(source, state):
        log_debug(
            config,
            worker="watch",
            message=f"Skipped file older than last processed: {source.name}",
            file=source.name,
        )
        return False

    if not file_settling_unnecessary(source):
        log_debug(
            config,
            worker="watch",
            message=f"Waiting for file to finish copying: {source.name}",
            file=source.name,
        )
    if not ensure_stable_file(
        source,
        settle_seconds=config.watcher.settle_seconds,
        poll_interval=config.watcher.poll_interval,
    ):
        console.print(f"[yellow]Skipping unstable file:[/yellow] {source}")
        log_activity(
            config,
            worker="watch",
            action="skipped",
            message=f"Skipped unstable file: {source.name}",
            file=source.name,
        )
        return False

    mtime = source.stat().st_mtime
    if not _is_newer_than_watermark(source, state):
        return False

    if is_known_duplicate(config, source):
        _advance_watermark(state, config, mtime)
        _record_processed(config, source=source, mtime=mtime)
        record_event(config, "duplicate_skipped", file=source.name)
        log_activity(
            config,
            worker="watch",
            action="duplicate",
            message=f"Duplicate skipped (already indexed): {source.name}",
            file=source.name,
        )
        log_debug(
            config,
            worker="watch",
            message=f"Duplicate watch file skipped: {source.name}",
            file=source.name,
        )
        return False

    jobs = process_file(config, source, skip_stability_wait=True)
    _advance_watermark(state, config, mtime)
    _record_processed(config, source=source, mtime=mtime)

    if jobs:
        record_event(config, "watch_import", file=source.name)
        console.print(f"[green]Queued from watch folder:[/green] {source.name}")
        log_activity(
            config,
            worker="watch",
            action="queued",
            message=f"Queued in-place processing: {source.name}",
            file=source.name,
        )
        return True

    if is_known_duplicate(config, source):
        record_event(config, "duplicate_skipped", file=source.name)
        log_activity(
            config,
            worker="watch",
            action="duplicate",
            message=f"Duplicate skipped (already indexed): {source.name}",
            file=source.name,
        )
    else:
        log_debug(
            config,
            worker="watch",
            message=f"Watch file skipped: {source.name}",
            file=source.name,
        )
    return False


def _scan_watch_folder(
    config: AppConfig,
    watch_root: Path,
    state: WatchState,
    *,
    reason: str,
) -> None:
    with _scan_lock:
        _scan_watch_folder_locked(config, watch_root, state, reason=reason)


def _scan_watch_folder_locked(
    config: AppConfig,
    watch_root: Path,
    state: WatchState,
    *,
    reason: str,
) -> None:
    reload_watch_state_from_disk(config, state)
    files = _list_pending_watch_files(watch_root, state)
    watermark = _describe_watermark(state.last_processed_mtime)
    if reason == "manual":
        log_activity(
            config,
            worker="watch",
            action="scanning",
            message=(
                f"Manual scan started: {len(files)} file(s) since {watermark['label']}"
            ),
        )
    log_debug(
        config,
        worker="watch",
        message=(
            f"Watch scan ({reason}): {len(files)} pending file(s) in {watch_root}, "
            f"watermark mtime {watermark['mtime']:.0f}"
        ),
    )
    queued = 0
    if not files:
        if reason == "manual":
            log_activity(
                config,
                worker="watch",
                action="scan",
                message=f"Manual scan finished: no files since {watermark['label']}",
            )
        _refresh_watch_queue_stats(config, state)
        return
    skipped = 0
    for path in files:
        try:
            write_heartbeat(config, "watch", "processing", file=path.name)
            if _process_watch_file(config, path, state):
                queued += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Watch processing failed for {path}:[/red] {exc}")
            log_activity(
                config,
                worker="watch",
                action="error",
                message=f"Watch processing failed for {path.name}: {exc}",
                file=path.name,
            )

    summary = (
        f"Watch scan ({reason}) finished: {queued} queued, "
        f"{skipped} skipped, {len(files)} pending"
    )
    log_debug(config, worker="watch", message=summary)
    if reason == "manual":
        log_activity(
            config,
            worker="watch",
            action="scan",
            message=(
                f"Manual scan finished: {queued} queued, {skipped} skipped "
                f"since {watermark['label']}"
            ),
        )
    _refresh_watch_queue_stats(config, state)


def _start_scan_request_thread(
    config: AppConfig,
    watch_root: Path,
    state: WatchState,
) -> None:
    def loop() -> None:
        while True:
            time.sleep(SCAN_REQUEST_POLL_INTERVAL)
            if not _consume_scan_request(config):
                continue
            try:
                if watch_root.is_dir():
                    _scan_watch_folder(config, watch_root, state, reason="manual")
                else:
                    _refresh_watch_queue_stats(config, state)
                    log_activity(
                        config,
                        worker="watch",
                        action="scan",
                        message=f"Manual scan skipped: watch folder unavailable ({watch_root})",
                    )
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Manual watch scan failed:[/red] {exc}")
                log_activity(
                    config,
                    worker="watch",
                    action="error",
                    message=f"Manual scan failed: {exc}",
                )

    thread = threading.Thread(
        target=loop,
        name="packetpro-watch-scan-request",
        daemon=True,
    )
    thread.start()


def _start_scan_thread(config: AppConfig, watch_root: Path, state: WatchState) -> None:
    def loop() -> None:
        while True:
            time.sleep(SCAN_INTERVAL)
            try:
                if watch_root.is_dir():
                    _scan_watch_folder(config, watch_root, state, reason="periodic")
                else:
                    _refresh_watch_queue_stats(config, state)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Watch periodic scan failed:[/red] {exc}")
                log_debug(
                    config,
                    worker="watch",
                    message=f"Periodic scan failed: {exc}",
                )

    thread = threading.Thread(target=loop, name="packetpro-watch-scan", daemon=True)
    thread.start()


def run_watch_worker(config: AppConfig) -> None:
    ensure_data_dirs(config)
    start_heartbeat_thread(config, "watch")

    watch_root = config.watch_folder
    if watch_root is None:
        console.print("[yellow]Watch folder not configured. Set it in Settings.[/yellow]")
        log_activity(
            config,
            worker="watch",
            action="idle",
            message="Watch folder not configured — set it in Settings",
        )
        try:
            while True:
                write_heartbeat(config, "watch", "idle")
                time.sleep(5)
        except KeyboardInterrupt:
            console.print("[yellow]Watch worker stopped.[/yellow]")
        return

    state, first_examination = _load_watch_state(config, watch_root)
    if first_examination:
        log_activity(
            config,
            worker="watch",
            action="scanning",
            message=(
                f"First scan of watch folder; processing all image files in {watch_root}"
            ),
        )
        log_debug(
            config,
            worker="watch",
            message=(
                f"First examination of {watch_root}; "
                f"watermark set to minimum date (all existing files eligible)"
            ),
        )

    _start_scan_thread(config, watch_root, state)
    _start_scan_request_thread(config, watch_root, state)

    log_debug(
        config,
        worker="watch",
        message=(
            f"Watch worker started (pid {os.getpid()}): "
            f"watching {watch_root} in place, "
            f"watermark mtime {state.last_processed_mtime:.0f}, "
            f"first_examination={first_examination}"
        ),
    )
    console.print(f"[bold]PacketPro watch folder[/bold] {watch_root}")

    if watch_root.is_dir():
        _refresh_watch_queue_stats(config, state)
        _scan_watch_folder(config, watch_root, state, reason="startup")
    else:
        _refresh_watch_queue_stats(config, state)
        console.print(f"[yellow]Watch folder not available yet:[/yellow] {watch_root}")

    try:
        global _last_unavailable_log
        while True:
            if not watch_root.is_dir():
                _refresh_watch_queue_stats(config, state)
                write_heartbeat(config, "watch", "waiting")
                now = time.monotonic()
                if now - _last_unavailable_log >= UNAVAILABLE_LOG_INTERVAL:
                    _last_unavailable_log = now
                    log_debug(
                        config,
                        worker="watch",
                        message=f"Watch folder not available: {watch_root}",
                    )
                for _ in range(max(1, int(SCAN_INTERVAL / 5))):
                    write_heartbeat(config, "watch", "waiting")
                    time.sleep(5)
                continue

            for changes in watch(watch_root, recursive=True, debounce=500, step=500):
                if not watch_root.is_dir():
                    break

                reload_watch_state_from_disk(config, state)
                write_heartbeat(config, "watch", "watching")
                for change, path_str in changes:
                    if change not in {Change.added, Change.modified}:
                        continue
                    path = Path(path_str)
                    if not _is_supported(path):
                        continue
                    try:
                        write_heartbeat(config, "watch", "processing", file=path.name)
                        log_debug(
                            config,
                            worker="watch",
                            message=f"Filesystem event ({change.name}): {path.name}",
                            file=path.name,
                        )
                        with _scan_lock:
                            _process_watch_file(config, path, state)
                    except Exception as exc:  # noqa: BLE001
                        console.print(f"[red]Watch processing failed for {path}:[/red] {exc}")
                        log_activity(
                            config,
                            worker="watch",
                            action="error",
                            message=f"Watch processing failed for {path.name}: {exc}",
                            file=path.name,
                        )
                _refresh_watch_queue_stats(config, state)
    except KeyboardInterrupt:
        console.print("[yellow]Watch worker stopped.[/yellow]")