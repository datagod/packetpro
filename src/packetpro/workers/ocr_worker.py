"""Watch transformed jobs, run OCR, archive originals, and persist results."""

from __future__ import annotations

import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from watchfiles import Change, watch

from packetpro.config import AppConfig, ConfigError, OcrConfig, ensure_data_dirs, load_config
from packetpro.db import init_db, insert_document, register_processed_file
from packetpro.ocr import (
    OcrBackend,
    backend_index_for_job,
    extract_text,
    resolve_ocr_backends,
)
from packetpro.pipeline_control import is_processing_enabled, log_activity, log_debug
from packetpro.stats import record_event, start_heartbeat_thread, write_heartbeat
from packetpro.utils import (
    archive_destination,
    explain_failure,
    format_failure_message,
    move_to_failed,
    read_json,
    utc_now,
    write_json,
)

console = Console()
PAUSED_LOG_INTERVAL = 60.0
OCR_SCAN_INTERVAL = 20.0
# Jobs left in "processing" after a worker crash/restart are reclaimed after this age.
STALE_PROCESSING_SECONDS = 120.0
_last_paused_log = 0.0
_active_backends: list[OcrBackend] = []


def _count_pending_jobs(config: AppConfig) -> int:
    count = 0
    for sidecar_path in config.transformed.glob("*.json"):
        if sidecar_path.name.startswith(".archive_"):
            continue
        try:
            job = read_json(sidecar_path)
        except Exception:
            continue
        if job.get("status") == "pending":
            count += 1
    return count


def reclaim_stale_processing_jobs(
    config: AppConfig,
    *,
    max_age_seconds: float = STALE_PROCESSING_SECONDS,
    force: bool = False,
) -> int:
    """Reset abandoned processing jobs back to pending so the queue can move again."""
    if not config.transformed.is_dir():
        return 0

    now = time.time()
    reclaimed = 0
    for sidecar_path in config.transformed.glob("*.json"):
        if sidecar_path.name.startswith(".archive_"):
            continue
        try:
            job = read_json(sidecar_path)
        except Exception:
            continue
        if job.get("status") != "processing":
            continue
        try:
            age = now - sidecar_path.stat().st_mtime
        except OSError:
            age = max_age_seconds + 1
        if not force and age < max_age_seconds:
            continue

        name = str(job.get("original_name") or sidecar_path.name)
        page = int(job.get("page_number", 0)) or None
        backend = job.get("ocr_backend") or "unknown"
        job["status"] = "pending"
        job.pop("error", None)
        try:
            write_json(sidecar_path, job)
        except OSError:
            continue
        reclaimed += 1
        log_activity(
            config,
            worker="ocr",
            action="warning",
            message=(
                f"Reclaimed stuck OCR job for {name} "
                f"(was processing on {backend} for {int(age)}s). "
                "Reset to pending so the queue can continue."
            ),
            file=name,
            page=page,
        )
    return reclaimed


def _archive_marker_path(transformed_dir: Path, file_hash: str) -> Path:
    return transformed_dir / f".archive_{file_hash}.json"


def _resolve_archive_path(
    config: AppConfig,
    original_path: Path,
    archive_root: Path,
    transformed_dir: Path,
    file_hash: str,
    *,
    in_place: bool = False,
) -> Path:
    marker = _archive_marker_path(transformed_dir, file_hash)
    if marker.exists():
        marker_data = read_json(marker)
        archive_path = Path(marker_data["archive_path"])
        if archive_path.exists():
            return archive_path

    if not original_path.exists():
        raise FileNotFoundError(f"Original file missing and no archive marker: {original_path}")

    if in_place:
        source_path = original_path.resolve()
        log_activity(
            config,
            worker="ocr",
            action="indexed",
            message=f"Keeping watch-folder source in place: {original_path.name}",
            file=original_path.name,
        )
        write_json(
            marker,
            {
                "archive_path": str(source_path),
                "original_name": original_path.name,
                "file_hash": file_hash,
                "in_place": True,
                "archived_at": utc_now(),
            },
        )
        return source_path

    dest = archive_destination(archive_root, original_path.name, file_hash)
    log_activity(
        config,
        worker="ocr",
        action="archiving",
        message=f"Archiving {original_path.name} to {dest.parent.name}/{dest.name}",
        file=original_path.name,
    )
    shutil.move(str(original_path), str(dest))
    write_json(
        marker,
        {
            "archive_path": str(dest),
            "original_name": original_path.name,
            "file_hash": file_hash,
            "archived_at": utc_now(),
        },
    )
    return dest


def _pending_siblings(config: AppConfig, file_hash: str) -> list[Path]:
    siblings: list[Path] = []
    for sidecar_path in config.transformed.glob("*.json"):
        if sidecar_path.name.startswith(".archive_"):
            continue
        try:
            job = read_json(sidecar_path)
        except Exception:
            continue
        if job.get("file_hash") == file_hash and job.get("status") == "pending":
            siblings.append(sidecar_path)
    return siblings


def _cleanup_marker_if_done(config: AppConfig, file_hash: str) -> None:
    if _pending_siblings(config, file_hash):
        return
    marker = _archive_marker_path(config.transformed, file_hash)
    if marker.exists():
        marker.unlink()


def _register_if_complete(config: AppConfig, job: dict, file_hash: str) -> None:
    if _pending_siblings(config, file_hash):
        return

    file_size = int(job.get("file_size", 0))
    if file_size == 0:
        archive_path = job.get("archive_path")
        if archive_path and Path(archive_path).is_file():
            file_size = Path(archive_path).stat().st_size

    register_processed_file(
        config.database,
        file_hash=file_hash,
        original_name=str(job["original_name"]),
        file_size=file_size,
    )


def _cleanup_job(sidecar_path: Path, job: dict) -> None:
    if not job.get("read_in_place"):
        enhanced_path = Path(job["enhanced_path"])
        if enhanced_path.exists():
            enhanced_path.unlink()
    if sidecar_path.exists():
        sidecar_path.unlink()


def process_job(
    config: AppConfig,
    sidecar_path: Path,
    ocr_config: OcrConfig,
    *,
    backend_label: str = "GPU1",
) -> None:
    job = read_json(sidecar_path)
    if job.get("status") != "pending":
        return

    job["status"] = "processing"
    job["ocr_backend"] = backend_label
    write_json(sidecar_path, job)

    enhanced_path = Path(job["enhanced_path"])
    original_path = Path(job["original_path"])
    file_hash = str(job["file_hash"])

    if not enhanced_path.exists():
        raise FileNotFoundError(f"Enhanced image missing: {enhanced_path}")

    page_number = int(job.get("page_number", 1))
    log_activity(
        config,
        worker="ocr",
        action="ocr",
        message=(
            f"Running OCR on {backend_label}, page {page_number} "
            f"of {job['original_name']}"
        ),
        file=job["original_name"],
        page=page_number,
    )
    text = extract_text(enhanced_path, ocr_config)
    archive_path = _resolve_archive_path(
        config,
        original_path,
        config.archive,
        config.transformed,
        file_hash,
        in_place=bool(job.get("in_place")),
    )

    insert_document(
        config.database,
        job_id=job["job_id"],
        original_name=job["original_name"],
        archive_path=str(archive_path),
        page_number=int(job.get("page_number", 1)),
        ocr_text=text,
        created_at=job.get("created_at"),
    )

    job["status"] = "archived"
    job["archive_path"] = str(archive_path)
    job["processed_at"] = utc_now()
    write_json(sidecar_path, job)
    _cleanup_job(sidecar_path, job)
    _cleanup_marker_if_done(config, file_hash)
    _register_if_complete(config, job, file_hash)
    record_event(
        config,
        "ocr_complete",
        count=1,
        file=job["original_name"],
        page=int(job.get("page_number", 1)),
    )

    console.print(
        f"[green]OCR complete[/green] [{backend_label}] {job['original_name']} "
        f"page {page_number}"
    )
    log_activity(
        config,
        worker="ocr",
        action="complete",
        message=(
            f"Indexed on {backend_label}, page {page_number} of {job['original_name']} "
            f"({len(text)} characters)"
        ),
        file=job["original_name"],
        page=page_number,
    )


def _handle_job_failure(
    config: AppConfig,
    sidecar_path: Path,
    job: dict,
    exc: Exception,
) -> None:
    name = str(job.get("original_name", sidecar_path.name))
    page = int(job.get("page_number", 0)) or None
    message = format_failure_message("OCR failed", file_name=name, error=exc)
    if page:
        message = f"{message} (page {page})"
    console.print(f"[red]OCR failed for {name}:[/red] {exc}")
    log_activity(
        config,
        worker="ocr",
        action="error",
        message=message,
        file=name,
        page=page,
    )
    original_path = Path(job.get("original_path", ""))
    if original_path.exists() and not job.get("in_place"):
        move_to_failed(original_path, config.failed, str(exc))
    job["status"] = "failed"
    job["error"] = str(exc)
    write_json(sidecar_path, job)


def _is_retriable_backend_error(exc: BaseException) -> bool:
    """True when another Ollama/GPU backend might succeed."""
    text = str(exc).lower()
    markers = (
        "500 internal server error",
        "502",
        "503",
        "504",
        "timeout",
        "timed out",
        "connection refused",
        "connecterror",
        "connect error",
        "connection reset",
        "server disconnected",
        "temporarily unavailable",
    )
    return any(marker in text for marker in markers)


def _process_job_safe(
    config: AppConfig,
    sidecar_path: Path,
    backend: OcrBackend,
    all_backends: list[OcrBackend] | None = None,
) -> bool:
    try:
        job = read_json(sidecar_path)
    except Exception:
        return False
    if job.get("status") != "pending":
        return False

    backends_to_try: list[OcrBackend] = [backend]
    if all_backends and len(all_backends) > 1:
        for candidate in all_backends:
            if candidate.label != backend.label:
                backends_to_try.append(candidate)

    last_exc: BaseException | None = None
    for attempt_index, active_backend in enumerate(backends_to_try):
        try:
            job = read_json(sidecar_path)
        except Exception:
            return False
        if job.get("status") not in {"pending", "processing"}:
            return False
        if attempt_index > 0:
            # Reset so process_job can claim the job again on the failover backend.
            job["status"] = "pending"
            job.pop("error", None)
            write_json(sidecar_path, job)
            previous = backends_to_try[attempt_index - 1]
            log_activity(
                config,
                worker="ocr",
                action="warning",
                message=(
                    f"Backend {previous.label} failed for "
                    f"{job.get('original_name', sidecar_path.name)}; "
                    f"retrying on {active_backend.label}. "
                    f"Previous error: {explain_failure(last_exc)}"
                ),
                file=str(job.get("original_name", "")),
                page=int(job.get("page_number", 0)) or None,
            )

        try:
            write_heartbeat(
                config,
                "ocr",
                "processing",
                file=job.get("original_name", sidecar_path.name),
            )
            process_job(
                config,
                sidecar_path,
                active_backend.config,
                backend_label=active_backend.label,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            can_failover = (
                attempt_index + 1 < len(backends_to_try)
                and _is_retriable_backend_error(exc)
            )
            if can_failover:
                console.print(
                    f"[yellow]OCR backend {active_backend.label} failed, "
                    f"trying failover:[/yellow] {exc}"
                )
                continue
            try:
                job = read_json(sidecar_path)
            except Exception:
                return False
            _handle_job_failure(config, sidecar_path, job, exc)
            return False

    return False


def _collect_pending_jobs(
    config: AppConfig,
    backends: list[OcrBackend],
) -> list[tuple[Path, OcrBackend]]:
    """Return pending jobs with preferred backend assignment.

    When multiple backends exist, interleave jobs so dual-GPU batches use both
    GPUs instead of stacking the first N alphabetically onto one device.
    """
    if not backends:
        return []

    buckets: list[list[tuple[Path, OcrBackend]]] = [[] for _ in backends]
    for sidecar_path in sorted(config.transformed.glob("*.json")):
        if sidecar_path.name.startswith(".archive_"):
            continue
        try:
            job = read_json(sidecar_path)
        except Exception:
            continue
        if job.get("status") != "pending":
            continue
        index = backend_index_for_job(str(job["job_id"]), len(backends))
        buckets[index].append((sidecar_path, backends[index]))

    if len(backends) == 1:
        return buckets[0]

    # Round-robin across backend buckets for healthier dual-GPU utilization.
    assignments: list[tuple[Path, OcrBackend]] = []
    indexes = [0] * len(backends)
    while True:
        added = False
        for backend_i, bucket in enumerate(buckets):
            pos = indexes[backend_i]
            if pos < len(bucket):
                assignments.append(bucket[pos])
                indexes[backend_i] = pos + 1
                added = True
        if not added:
            break
    return assignments


def _scan_pending(
    config: AppConfig,
    backends: list[OcrBackend],
    *,
    reason: str,
) -> None:
    # Recover jobs abandoned when the worker restarted mid-OCR.
    reclaimed = reclaim_stale_processing_jobs(
        config,
        force=reason in {"startup", "resume"},
    )
    if reclaimed:
        log_activity(
            config,
            worker="ocr",
            action="warning",
            message=f"Reclaimed {reclaimed} stuck processing job(s) during {reason} scan",
        )

    pending = _count_pending_jobs(config)
    if not is_processing_enabled(config):
        log_debug(
            config,
            worker="ocr",
            message=f"OCR scan skipped ({reason}): processing paused, {pending} job(s) pending",
        )
        return

    if not backends:
        log_activity(
            config,
            worker="ocr",
            action="error",
            message=f"OCR scan ({reason}): no OCR backends available; {pending} job(s) waiting",
        )
        return

    if not pending:
        if reason not in {"periodic", "watch"}:
            log_debug(
                config,
                worker="ocr",
                message=f"OCR scan ({reason}): 0 pending job(s) in {config.transformed}",
            )
        return

    assignments = _collect_pending_jobs(config, backends)
    log_debug(
        config,
        worker="ocr",
        message=(
            f"OCR scan ({reason}): {pending} pending job(s), "
            f"{len(backends)} backend(s): "
            f"{', '.join(backend.label for backend in backends)}"
        ),
    )
    if reason in {"startup", "resume", "periodic"} and pending > 0:
        log_activity(
            config,
            worker="ocr",
            action="scanning",
            message=(
                f"OCR queue scan ({reason}): {pending} pending job(s), "
                f"backends {', '.join(backend.label for backend in backends)}"
            ),
        )

    processed = 0
    # Process only a limited concurrent batch so we don't pin hundreds of futures
    # and so periodic reclaim/scans can interleave.
    batch_size = max(2, len(backends) * 4)
    batch = assignments[:batch_size]

    if len(backends) == 1:
        for sidecar_path, backend in batch:
            if not is_processing_enabled(config):
                return
            if _process_job_safe(config, sidecar_path, backend, backends):
                processed += 1
    else:
        with ThreadPoolExecutor(max_workers=len(backends)) as executor:
            futures = {
                executor.submit(
                    _process_job_safe,
                    config,
                    sidecar_path,
                    backend,
                    backends,
                ): sidecar_path
                for sidecar_path, backend in batch
            }
            for future in as_completed(futures):
                if not is_processing_enabled(config):
                    break
                try:
                    if future.result():
                        processed += 1
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]OCR worker batch error:[/red] {exc}")

    remaining = _count_pending_jobs(config)
    log_debug(
        config,
        worker="ocr",
        message=(
            f"OCR scan ({reason}) finished: {processed} job(s) processed, "
            f"{remaining} still pending"
        ),
    )


def _refresh_backends(config: AppConfig) -> list[OcrBackend]:
    global _active_backends
    backends = resolve_ocr_backends(config.ocr)
    if backends != _active_backends:
        labels = ", ".join(backend.label for backend in backends)
        log_debug(
            config,
            worker="ocr",
            message=f"OCR backends active: {labels}",
        )
        if (
            config.ocr.engine == "ollama"
            and config.ocr.use_both_gpus
            and len(backends) < 2
        ):
            log_activity(
                config,
                worker="ocr",
                action="warning",
                message=(
                    "Dual GPU requested but only using backends where the OCR model "
                    "is already loaded (PacketPro will not load other models). "
                    f"Active: {labels or 'none'}"
                ),
            )
        if config.ocr.engine == "ollama" and not backends:
            log_activity(
                config,
                worker="ocr",
                action="warning",
                message=(
                    f"No OCR backends ready: model {config.ocr.model} is not loaded "
                    f"on primary ({config.ocr.ollama_url})"
                    + (
                        f" or secondary ({config.ocr.secondary_ollama_url})"
                        if config.ocr.use_both_gpus
                        else ""
                    )
                    + ". Warm qwen2.5vl:7b before OCR will proceed."
                ),
            )
    _active_backends = backends
    return backends


def _start_ocr_scan_thread(config: AppConfig) -> None:
    def loop() -> None:
        nonlocal config
        while True:
            time.sleep(OCR_SCAN_INTERVAL)
            try:
                try:
                    config = load_config(config.config_path)
                except ConfigError:
                    pass
                if not is_processing_enabled(config):
                    continue
                backends = _refresh_backends(config)
                pending = _count_pending_jobs(config)
                if pending <= 0:
                    reclaim_stale_processing_jobs(config)
                    continue
                _scan_pending(config, backends, reason="periodic")
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]OCR periodic scan failed:[/red] {exc}")
                try:
                    log_activity(
                        config,
                        worker="ocr",
                        action="error",
                        message=format_failure_message(
                            "Periodic OCR scan failed",
                            error=exc,
                        ),
                    )
                except Exception:  # noqa: BLE001
                    pass

    thread = threading.Thread(target=loop, name="packetpro-ocr-scan", daemon=True)
    thread.start()


def run_ocr_worker(config: AppConfig) -> None:
    ensure_data_dirs(config)
    init_db(config.database)
    start_heartbeat_thread(config, "ocr")
    control_dir = config.data_root / ".packetpro"
    backends = _refresh_backends(config)
    reclaimed = reclaim_stale_processing_jobs(config, force=True)
    pending = _count_pending_jobs(config)
    enabled = is_processing_enabled(config)
    backend_labels = ", ".join(backend.label for backend in backends) or "none"
    log_activity(
        config,
        worker="ocr",
        action="idle",
        message=(
            f"OCR worker started (pid {os.getpid()}): "
            f"watching {config.transformed}, backends {backend_labels}, "
            f"processing {'enabled' if enabled else 'paused'}, "
            f"{pending} pending, reclaimed {reclaimed} stuck job(s)"
        ),
    )
    console.print(
        f"[bold]PacketPro OCR watching[/bold] {config.transformed} "
        f"({backend_labels})"
    )

    _scan_pending(config, backends, reason="startup")
    _start_ocr_scan_thread(config)
    last_backend_refresh = time.monotonic()

    try:
        global _last_paused_log
        for changes in watch(config.transformed, control_dir, recursive=False, debounce=500, step=500):
            if time.monotonic() - last_backend_refresh >= 30.0:
                try:
                    config = load_config(config.config_path)
                except ConfigError:
                    pass
                backends = _refresh_backends(config)
                last_backend_refresh = time.monotonic()

            if not is_processing_enabled(config):
                write_heartbeat(config, "ocr", "paused")
                now = time.monotonic()
                if now - _last_paused_log >= PAUSED_LOG_INTERVAL:
                    _last_paused_log = now
                    pending = _count_pending_jobs(config)
                    log_debug(
                        config,
                        worker="ocr",
                        message=f"Processing paused — {pending} job(s) in OCR queue",
                    )
                continue

            control_changed = any(
                Path(path_str).name == "control.json"
                for _, path_str in changes
            )
            if control_changed:
                backends = _refresh_backends(config)
                log_debug(config, worker="ocr", message="Processing resumed — scanning OCR queue")
                _scan_pending(config, backends, reason="resume")

            json_changes = [
                (change, path_str)
                for change, path_str in changes
                if Path(path_str).parent.resolve() != control_dir.resolve()
                and Path(path_str).suffix == ".json"
                and not Path(path_str).name.startswith(".archive_")
            ]
            if not json_changes:
                continue
            _scan_pending(config, backends, reason="watch")
    except KeyboardInterrupt:
        console.print("[yellow]OCR worker stopped.[/yellow]")