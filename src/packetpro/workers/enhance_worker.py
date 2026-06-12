"""Watch inbox and produce OCR-ready transformed images."""

from __future__ import annotations

import uuid
from pathlib import Path

from rich.console import Console
from watchfiles import Change, watch

from packetpro.config import SUPPORTED_EXTENSIONS, AppConfig, ensure_data_dirs
from packetpro.enhance import enhance_image, load_source_image, pdf_page_count, save_enhanced_image
from packetpro.stats import record_event, start_heartbeat_thread, write_heartbeat
from packetpro.utils import (
    file_sha256,
    move_to_failed,
    utc_now,
    wait_for_stable_file,
    write_json,
)

console = Console()


def _is_supported(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def _job_id(stem: str, page_number: int, file_hash: str) -> str:
    return f"{stem}_p{page_number:03d}_{file_hash[:12]}"


def process_file(config: AppConfig, source_path: Path) -> list[Path]:
    if not _is_supported(source_path):
        return []

    if not wait_for_stable_file(
        source_path,
        settle_seconds=config.watcher.settle_seconds,
        poll_interval=config.watcher.poll_interval,
    ):
        console.print(f"[yellow]Skipping unstable file:[/yellow] {source_path}")
        return []

    file_hash = file_sha256(source_path)
    created_at = utc_now()
    suffix = source_path.suffix.lower()
    pages = pdf_page_count(source_path) if suffix == ".pdf" else 1
    created_jobs: list[Path] = []

    for page_number in range(1, pages + 1):
        job_id = _job_id(source_path.stem, page_number, file_hash)
        enhanced_path = config.transformed / f"{job_id}.png"
        sidecar_path = config.transformed / f"{job_id}.json"

        if sidecar_path.exists():
            continue

        image = load_source_image(source_path, page_number, config.enhance.pdf_dpi)
        enhanced = enhance_image(image, config.enhance)
        save_enhanced_image(enhanced, enhanced_path)

        payload = {
            "job_id": job_id,
            "status": "pending",
            "original_path": str(source_path),
            "original_name": source_path.name,
            "enhanced_path": str(enhanced_path),
            "page_number": page_number,
            "page_count": pages,
            "file_hash": file_hash,
            "created_at": created_at,
        }
        write_json(sidecar_path, payload)
        created_jobs.append(sidecar_path)
        record_event(config, "enhanced", count=1, file=source_path.name, page=page_number)
        console.print(f"[green]Enhanced[/green] {source_path.name} page {page_number}/{pages}")

    return created_jobs


def run_enhance_worker(config: AppConfig) -> None:
    ensure_data_dirs(config)
    start_heartbeat_thread(config, "enhance")
    console.print(f"[bold]PacketPro enhancer watching[/bold] {config.inbox}")

    try:
        for changes in watch(config.inbox, recursive=False, debounce=500, step=500):
            for change, path_str in changes:
                if change not in {Change.added, Change.modified}:
                    continue
                path = Path(path_str)
                if not _is_supported(path):
                    continue
                try:
                    write_heartbeat(config, "enhance", "processing", file=path.name)
                    process_file(config, path)
                except Exception as exc:  # noqa: BLE001
                    console.print(f"[red]Enhance failed for {path}:[/red] {exc}")
                    try:
                        move_to_failed(path, config.failed, str(exc))
                    except Exception as move_exc:  # noqa: BLE001
                        console.print(f"[red]Failed to quarantine {path}:[/red] {move_exc}")
    except KeyboardInterrupt:
        console.print("[yellow]Enhancer stopped.[/yellow]")