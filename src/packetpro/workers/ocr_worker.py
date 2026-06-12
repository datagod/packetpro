"""Watch transformed jobs, run OCR, archive originals, and persist results."""

from __future__ import annotations

import shutil
from pathlib import Path

from rich.console import Console
from watchfiles import Change, watch

from packetpro.config import AppConfig, ensure_data_dirs
from packetpro.db import init_db, insert_document
from packetpro.ocr import extract_text
from packetpro.utils import (
    archive_destination,
    file_sha256,
    move_to_failed,
    read_json,
    utc_now,
    write_json,
)

console = Console()


def _archive_marker_path(transformed_dir: Path, file_hash: str) -> Path:
    return transformed_dir / f".archive_{file_hash}.json"


def _resolve_archive_path(
    original_path: Path,
    archive_root: Path,
    transformed_dir: Path,
    file_hash: str,
) -> Path:
    marker = _archive_marker_path(transformed_dir, file_hash)
    if marker.exists():
        marker_data = read_json(marker)
        archive_path = Path(marker_data["archive_path"])
        if archive_path.exists():
            return archive_path

    if not original_path.exists():
        raise FileNotFoundError(f"Original file missing and no archive marker: {original_path}")

    dest = archive_destination(archive_root, original_path.name, file_hash)
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


def _cleanup_job(sidecar_path: Path, job: dict) -> None:
    enhanced_path = Path(job["enhanced_path"])
    if enhanced_path.exists():
        enhanced_path.unlink()
    if sidecar_path.exists():
        sidecar_path.unlink()


def process_job(config: AppConfig, sidecar_path: Path) -> None:
    job = read_json(sidecar_path)
    if job.get("status") != "pending":
        return

    enhanced_path = Path(job["enhanced_path"])
    original_path = Path(job["original_path"])
    file_hash = str(job["file_hash"])

    if not enhanced_path.exists():
        raise FileNotFoundError(f"Enhanced image missing: {enhanced_path}")

    text = extract_text(enhanced_path, config.ocr)
    archive_path = _resolve_archive_path(
        original_path,
        config.archive,
        config.transformed,
        file_hash,
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

    console.print(
        f"[green]OCR complete[/green] {job['original_name']} "
        f"page {job.get('page_number', 1)}"
    )


def _scan_pending(config: AppConfig) -> None:
    for sidecar_path in sorted(config.transformed.glob("*.json")):
        if sidecar_path.name.startswith(".archive_"):
            continue
        try:
            job = read_json(sidecar_path)
        except Exception:
            continue
        if job.get("status") == "pending":
            try:
                process_job(config, sidecar_path)
            except Exception as exc:  # noqa: BLE001
                _handle_job_failure(config, sidecar_path, job, exc)


def _handle_job_failure(
    config: AppConfig,
    sidecar_path: Path,
    job: dict,
    exc: Exception,
) -> None:
    console.print(f"[red]OCR failed for {sidecar_path.name}:[/red] {exc}")
    original_path = Path(job.get("original_path", ""))
    if original_path.exists():
        move_to_failed(original_path, config.failed, str(exc))
    job["status"] = "failed"
    job["error"] = str(exc)
    write_json(sidecar_path, job)


def run_ocr_worker(config: AppConfig) -> None:
    ensure_data_dirs(config)
    init_db(config.database)
    console.print(f"[bold]PacketPro OCR watching[/bold] {config.transformed}")

    _scan_pending(config)

    try:
        for changes in watch(config.transformed, recursive=False, debounce=500, step=500):
            json_changes = [
                (change, path_str)
                for change, path_str in changes
                if Path(path_str).suffix == ".json"
                and not Path(path_str).name.startswith(".archive_")
            ]
            if not json_changes:
                continue
            for _, path_str in sorted(json_changes, key=lambda item: item[1]):
                sidecar_path = Path(path_str)
                try:
                    job = read_json(sidecar_path)
                    process_job(config, sidecar_path)
                except Exception as exc:  # noqa: BLE001
                    try:
                        job = read_json(sidecar_path)
                    except Exception:
                        continue
                    _handle_job_failure(config, sidecar_path, job, exc)
    except KeyboardInterrupt:
        console.print("[yellow]OCR worker stopped.[/yellow]")