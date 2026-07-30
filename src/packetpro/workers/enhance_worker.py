"""Watch inbox and produce OCR-ready transformed images."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from rich.console import Console
from watchfiles import Change, watch

from packetpro.config import SUPPORTED_EXTENSIONS, AppConfig, ensure_data_dirs, is_watch_source
from packetpro.db import file_hash_exists, init_db
from packetpro.enhance import enhance_image, load_source_image, pdf_page_count, save_enhanced_image
from packetpro.pipeline_control import is_processing_enabled, log_activity, log_debug
from packetpro.stats import record_event, start_heartbeat_thread, write_heartbeat
from packetpro.utils import (
    ensure_stable_file,
    file_content_hash,
    file_identity_hash,
    file_settling_unnecessary,
    format_failure_message,
    move_to_failed,
    utc_now,
    write_json,
)

console = Console()
DUPLICATE_MESSAGE = "Duplicate file: already processed"
INBOX_SCAN_INTERVAL = 15.0
PAUSED_LOG_INTERVAL = 60.0
_last_paused_log = 0.0


def _is_supported(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def _job_id(stem: str, page_number: int, file_hash: str) -> str:
    return f"{stem}_p{page_number:03d}_{file_hash[:12]}"


def _list_inbox_files(config: AppConfig) -> list[Path]:
    if not config.inbox.is_dir():
        return []
    return sorted(
        path
        for path in config.inbox.iterdir()
        if _is_supported(path)
    )


def _has_pending_jobs(config: AppConfig, file_hash: str) -> bool:
    for sidecar_path in config.transformed.glob("*.json"):
        if sidecar_path.name.startswith(".archive_"):
            continue
        try:
            job = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if job.get("file_hash") == file_hash and job.get("status") == "pending":
            return True
    return False


def _is_already_processed(config: AppConfig, file_hash: str) -> bool:
    init_db(config.database)
    return file_hash_exists(config.database, file_hash)


def _source_file_hash(config: AppConfig, source_path: Path, *, in_place: bool) -> str:
    if in_place:
        return file_content_hash(source_path)
    return file_identity_hash(source_path)


def is_known_duplicate(config: AppConfig, source_path: Path) -> bool:
    if not _is_supported(source_path) or not source_path.exists():
        return False
    in_place = is_watch_source(config, source_path)
    init_db(config.database)
    file_hash = _source_file_hash(config, source_path, in_place=in_place)
    return _has_pending_jobs(config, file_hash) or _is_already_processed(config, file_hash)


def process_file(
    config: AppConfig,
    source_path: Path,
    *,
    skip_stability_wait: bool = False,
) -> list[Path]:
    if not _is_supported(source_path):
        return []

    if not source_path.exists():
        log_debug(
            config,
            worker="enhance",
            message=f"File no longer present, skipping: {source_path.name}",
            file=source_path.name,
        )
        return []

    in_place = is_watch_source(config, source_path)
    file_hash = _source_file_hash(config, source_path, in_place=in_place)

    if _has_pending_jobs(config, file_hash):
        log_debug(
            config,
            worker="enhance",
            message=f"Already queued for OCR, skipping: {source_path.name}",
            file=source_path.name,
        )
        return []

    if _is_already_processed(config, file_hash):
        if not in_place:
            move_to_failed(source_path, config.failed, DUPLICATE_MESSAGE)
        record_event(config, "duplicate_skipped", file=source_path.name)
        console.print(f"[yellow]Duplicate skipped:[/yellow] {source_path.name}")
        duplicate_msg = (
            f"Duplicate skipped (already indexed): {source_path.name}"
            if in_place
            else f"Duplicate skipped: {source_path.name}"
        )
        log_activity(
            config,
            worker="enhance",
            action="duplicate",
            message=duplicate_msg,
            file=source_path.name,
        )
        return []

    if not skip_stability_wait:
        settling_needed = not file_settling_unnecessary(source_path)
        if settling_needed:
            log_debug(
                config,
                worker="enhance",
                message=f"Waiting for file to finish copying: {source_path.name}",
                file=source_path.name,
            )
            if not in_place:
                log_activity(
                    config,
                    worker="enhance",
                    action="waiting",
                    message=f"Waiting for file to finish copying: {source_path.name}",
                    file=source_path.name,
                )
        if not ensure_stable_file(
            source_path,
            settle_seconds=config.watcher.settle_seconds,
            poll_interval=config.watcher.poll_interval,
        ):
            if not source_path.exists():
                log_debug(
                    config,
                    worker="enhance",
                    message=f"File removed during wait (likely archived), skipping: {source_path.name}",
                    file=source_path.name,
                )
            else:
                console.print(f"[yellow]Skipping unstable file:[/yellow] {source_path}")
                log_activity(
                    config,
                    worker="enhance",
                    action="skipped",
                    message=f"Skipped unstable file: {source_path.name}",
                    file=source_path.name,
                )
            return []

    file_hash = _source_file_hash(config, source_path, in_place=in_place)
    if _has_pending_jobs(config, file_hash):
        log_debug(
            config,
            worker="enhance",
            message=f"Already queued for OCR, skipping: {source_path.name}",
            file=source_path.name,
        )
        return []
    if _is_already_processed(config, file_hash):
        record_event(config, "duplicate_skipped", file=source_path.name)
        console.print(f"[yellow]Duplicate skipped:[/yellow] {source_path.name}")
        log_activity(
            config,
            worker="enhance",
            action="duplicate",
            message=(
                f"Duplicate skipped (already indexed): {source_path.name}"
                if in_place
                else f"Duplicate skipped: {source_path.name}"
            ),
            file=source_path.name,
        )
        return []

    file_size = source_path.stat().st_size
    created_at = utc_now()
    suffix = source_path.suffix.lower()
    pages = pdf_page_count(source_path) if suffix == ".pdf" else 1
    created_jobs: list[Path] = []
    source_label = "watch folder file" if in_place else "inbox file"

    log_activity(
        config,
        worker="enhance",
        action="processing",
        message=(
            f"Processing {source_label}: {source_path.name} "
            f"({pages} page{'s' if pages != 1 else ''})"
        ),
        file=source_path.name,
    )

    read_in_place = in_place and not config.enhance.enabled

    for page_number in range(1, pages + 1):
        job_id = _job_id(source_path.stem, page_number, file_hash)
        sidecar_path = config.transformed / f"{job_id}.json"
        ocr_image_path = config.transformed / f"{job_id}.png"

        if sidecar_path.exists():
            log_debug(
                config,
                worker="enhance",
                message=f"Job already exists for page {page_number} of {source_path.name}",
                file=source_path.name,
            )
            continue

        if read_in_place:
            ocr_image_path = source_path.resolve()
            log_activity(
                config,
                worker="enhance",
                action="preparing",
                message=(
                    f"Queueing page {page_number}/{pages} of {source_path.name} "
                    f"for in-place OCR"
                ),
                file=source_path.name,
                page=page_number,
            )
        else:
            log_activity(
                config,
                worker="enhance",
                action="loading",
                message=f"Loading page {page_number}/{pages} of {source_path.name}",
                file=source_path.name,
                page=page_number,
            )
            image = load_source_image(source_path, page_number, config.enhance.pdf_dpi)
            prepare_msg = (
                f"Applying image enhancements to page {page_number} of {source_path.name}"
                if config.enhance.enabled
                else f"Preparing page {page_number} of {source_path.name} for OCR"
            )
            log_activity(
                config,
                worker="enhance",
                action="enhancing" if config.enhance.enabled else "preparing",
                message=prepare_msg,
                file=source_path.name,
                page=page_number,
            )
            enhanced = enhance_image(
                image,
                config.enhance,
                skip_upscale=in_place,
            )
            save_enhanced_image(enhanced, ocr_image_path)

        payload = {
            "job_id": job_id,
            "status": "pending",
            "original_path": str(source_path.resolve()),
            "original_name": source_path.name,
            "enhanced_path": str(ocr_image_path),
            "page_number": page_number,
            "page_count": pages,
            "file_hash": file_hash,
            "file_size": file_size,
            "created_at": created_at,
            "in_place": in_place,
            "read_in_place": read_in_place,
        }
        write_json(sidecar_path, payload)
        created_jobs.append(sidecar_path)
        record_event(config, "enhanced", count=1, file=source_path.name, page=page_number)
        queued_msg = (
            f"Queued page {page_number} of {source_path.name} for in-place OCR"
            if read_in_place
            else f"Queued page {page_number} of {source_path.name} for OCR"
        )
        console.print(f"[green]Queued[/green] {source_path.name} page {page_number}/{pages}")
        log_activity(
            config,
            worker="enhance",
            action="queued",
            message=queued_msg,
            file=source_path.name,
            page=page_number,
        )

    return created_jobs


def _scan_inbox(config: AppConfig, *, reason: str) -> None:
    if not is_processing_enabled(config):
        waiting = len(_list_inbox_files(config))
        log_debug(
            config,
            worker="enhance",
            message=f"Inbox scan skipped ({reason}): processing paused, {waiting} file(s) waiting",
        )
        return

    files = _list_inbox_files(config)
    if not files:
        if reason != "periodic":
            log_debug(
                config,
                worker="enhance",
                message=f"Inbox scan ({reason}): 0 supported file(s) in {config.inbox}",
            )
        return

    log_debug(
        config,
        worker="enhance",
        message=f"Inbox scan ({reason}): {len(files)} supported file(s) in {config.inbox}",
    )

    started = 0
    skipped = 0
    for path in files:
        if not is_processing_enabled(config):
            log_debug(config, worker="enhance", message=f"Inbox scan ({reason}) interrupted: processing paused")
            break
        try:
            write_heartbeat(config, "enhance", "processing", file=path.name)
            jobs = process_file(config, path)
            if jobs:
                started += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Enhance failed for {path}:[/red] {exc}")
            log_activity(
                config,
                worker="enhance",
                action="error",
                message=format_failure_message(
                    "Enhancement failed",
                    file_name=path.name,
                    error=exc,
                ),
                file=path.name,
            )
            if not is_watch_source(config, path):
                try:
                    move_to_failed(path, config.failed, str(exc))
                except Exception as move_exc:  # noqa: BLE001
                    console.print(f"[red]Failed to quarantine {path}:[/red] {move_exc}")

    log_debug(
        config,
        worker="enhance",
        message=(
            f"Inbox scan ({reason}) finished: {started} file(s) enhanced, "
            f"{skipped} skipped, {len(files)} total"
        ),
    )


def _start_inbox_scan_thread(config: AppConfig) -> None:
    def loop() -> None:
        while True:
            time.sleep(INBOX_SCAN_INTERVAL)
            if is_processing_enabled(config):
                _scan_inbox(config, reason="periodic")

    thread = threading.Thread(target=loop, name="packetpro-inbox-scan", daemon=True)
    thread.start()


def run_enhance_worker(config: AppConfig) -> None:
    ensure_data_dirs(config)
    start_heartbeat_thread(config, "enhance")
    _start_inbox_scan_thread(config)
    control_dir = config.data_root / ".packetpro"

    waiting = len(_list_inbox_files(config))
    enabled = is_processing_enabled(config)
    log_debug(
        config,
        worker="enhance",
        message=(
            f"Enhance worker started (pid {os.getpid()}): "
            f"watching {config.inbox}, processing "
            f"{'enabled' if enabled else 'paused'}, {waiting} file(s) in inbox"
        ),
    )
    console.print(f"[bold]PacketPro enhancer watching[/bold] {config.inbox}")

    _scan_inbox(config, reason="startup")

    try:
        global _last_paused_log
        for changes in watch(config.inbox, control_dir, recursive=False, debounce=500, step=500):
            if not is_processing_enabled(config):
                waiting = len(_list_inbox_files(config))
                write_heartbeat(config, "enhance", "paused")
                now = time.monotonic()
                if now - _last_paused_log >= PAUSED_LOG_INTERVAL:
                    _last_paused_log = now
                    log_debug(
                        config,
                        worker="enhance",
                        message=f"Processing paused — {waiting} file(s) waiting in inbox",
                    )
                continue

            control_changed = any(
                Path(path_str).name == "control.json"
                for _, path_str in changes
            )
            if control_changed:
                log_debug(config, worker="enhance", message="Processing resumed — scanning inbox")
                _scan_inbox(config, reason="resume")

            for change, path_str in changes:
                if Path(path_str).parent.resolve() == control_dir.resolve():
                    continue
                if change not in {Change.added, Change.modified}:
                    continue
                path = Path(path_str)
                if not _is_supported(path):
                    continue
                if not is_processing_enabled(config):
                    write_heartbeat(config, "enhance", "paused")
                    break
                try:
                    write_heartbeat(config, "enhance", "processing", file=path.name)
                    log_debug(
                        config,
                        worker="enhance",
                        message=f"Filesystem event ({change.name}): {path.name}",
                        file=path.name,
                    )
                    process_file(config, path)
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]Enhance failed for {path}:[/red] {exc}")
                    log_activity(
                        config,
                        worker="enhance",
                        action="error",
                        message=format_failure_message(
                            "Enhancement failed",
                            file_name=path.name,
                            error=exc,
                        ),
                        file=path.name,
                    )
                    if not is_watch_source(config, path):
                        try:
                            move_to_failed(path, config.failed, str(exc))
                        except Exception as move_exc:  # noqa: BLE001
                            console.print(f"[red]Failed to quarantine {path}:[/red] {move_exc}")
    except KeyboardInterrupt:
        console.print("[yellow]Enhancer stopped.[/yellow]")