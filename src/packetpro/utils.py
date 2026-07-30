"""Shared utilities for PacketPro workers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_identity_hash(path: Path) -> str:
    stat = path.stat()
    payload = f"{path.name}:{stat.st_size}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def watch_file_identity_hash(path: Path) -> str:
    """Legacy path-based identity; prefer file_content_hash for watch deduplication."""
    stat = path.stat()
    payload = f"{path.resolve()}:{stat.st_size}:{int(stat.st_mtime)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_content_hash(path: Path) -> str:
    """SHA-256 of file bytes — used to skip duplicate watch-folder images."""
    return file_sha256(path)


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def file_settling_unnecessary(path: Path, *, min_age_seconds: float = 60.0) -> bool:
    """Skip copy-settle waits for files that have not changed recently."""
    try:
        stat = path.stat()
    except OSError:
        return False
    return stat.st_size > 0 and (time.time() - stat.st_mtime) >= min_age_seconds


def wait_for_stable_file(path: Path, settle_seconds: float, poll_interval: float) -> bool:
    if not path.exists():
        return False
    last_size = -1
    stable_since: float | None = None
    deadline = time.monotonic() + max(settle_seconds * 10, 30.0)
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last_size and size > 0:
            if stable_since is None:
                stable_since = time.monotonic()
            elif time.monotonic() - stable_since >= settle_seconds:
                return True
        else:
            last_size = size
            stable_since = None
        time.sleep(poll_interval)
    return False


def ensure_stable_file(
    path: Path,
    *,
    settle_seconds: float,
    poll_interval: float,
    min_age_seconds: float = 60.0,
) -> bool:
    if not path.exists():
        return False
    if file_settling_unnecessary(path, min_age_seconds=min_age_seconds):
        try:
            return path.stat().st_size > 0
        except OSError:
            return False
    return wait_for_stable_file(path, settle_seconds, poll_interval)


def _unique_tmp_path(path: Path) -> Path:
    token = f"{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}"
    return path.with_name(f".{path.name}.{token}.tmp")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _unique_tmp_path(path)
    try:
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def explain_failure(error: str | BaseException | None) -> str:
    """Turn a raw exception/message into a short human-readable failure reason."""
    if error is None:
        return "Unknown failure (no error details recorded)."
    raw = str(error).strip() or type(error).__name__
    lower = raw.lower()

    if "duplicate file" in lower or "already processed" in lower or "already indexed" in lower:
        return (
            "Duplicate file — already processed or already indexed. "
            "Delete the existing document first if you need a fresh OCR pass."
        )
    if "unique constraint failed" in lower and "job_id" in lower:
        return (
            "Already indexed under this job id (database unique constraint). "
            "This page is already in the search index; reprocessing will fail again "
            "unless the existing document is deleted."
        )
    if "unique constraint" in lower:
        return f"Database unique constraint conflict: {raw}"
    if "empty ocr text" in lower or "returned empty" in lower:
        return (
            "OCR returned no text — the image may be blank, mostly decorative, "
            "too low quality, or the model could not read it."
        )
    if "unsupported gpu architecture" in lower:
        return (
            "PaddleOCR GPU architecture is not supported on this machine. "
            "Switch OCR engine to Ollama or use CPU PaddleOCR."
        )
    if "connection refused" in lower or "connecterror" in lower or "connect error" in lower:
        return (
            "Could not reach the OCR backend (connection refused). "
            "Check that Ollama/PaddleOCR is running."
        )
    if "timed out" in lower or "timeout" in lower:
        return "OCR request timed out — the model may be overloaded or stuck."
    if "500 internal server error" in lower:
        return (
            "OCR backend returned HTTP 500 (server crash/overload). "
            "Check Ollama logs and GPU memory, then reprocess."
        )
    if "404" in lower and ("model" in lower or "not found" in lower):
        return "OCR model not found on the backend — pull/install the configured model."
    if "enhanced image missing" in lower:
        return "Enhanced image was missing when OCR started (job files may have been cleaned up)."
    if "no such file or directory" in lower and (
        "heartbeat" in lower or "activity.jsonl" in lower or ".packetpro" in lower
    ):
        return (
            "Transient status-file write race while updating heartbeats/activity log. "
            "Usually safe to reprocess."
        )
    if "no such file or directory" in lower:
        return f"Missing file or path: {raw}"
    if "permission denied" in lower:
        return f"Permission denied while reading or writing a file: {raw}"
    if "unstable file" in lower:
        return "File never finished copying (size kept changing); skipped as unstable."
    if "paddleocr" in lower and "failed after" in lower:
        return f"PaddleOCR failed after retries: {raw}"
    if "ocr failed after" in lower:
        return f"OCR failed after retries: {raw}"
    return raw


def format_failure_message(
    context: str,
    *,
    file_name: str | None = None,
    error: str | BaseException | None = None,
) -> str:
    """Build an activity-log message with a clear failure reason."""
    reason = explain_failure(error)
    raw = str(error).strip() if error is not None else ""
    subject = f"{context} for {file_name}" if file_name else context
    if raw and raw != reason and raw.lower() not in reason.lower():
        return f"{subject}: {reason} — technical detail: {raw}"
    return f"{subject}: {reason}"


def read_error_sidecar(path: Path) -> str | None:
    sidecar = path.with_suffix(path.suffix + ".error.txt")
    if not sidecar.is_file():
        return None
    try:
        text = sidecar.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def write_error_sidecar(path: Path, error: str) -> None:
    sidecar = path.with_suffix(path.suffix + ".error.txt")
    reason = explain_failure(error)
    raw = str(error).strip()
    if raw and raw != reason:
        body = f"{reason}\n\nTechnical detail:\n{raw}\n"
    else:
        body = f"{reason}\n"
    sidecar.write_text(body, encoding="utf-8")


def move_to_failed(source: Path, failed_dir: Path, error: str) -> Path:
    failed_dir.mkdir(parents=True, exist_ok=True)
    dest = failed_dir / source.name
    if dest.exists():
        dest = failed_dir / f"{source.stem}_{int(time.time())}{source.suffix}"
    shutil.move(str(source), str(dest))
    write_error_sidecar(dest, error)
    return dest


def archive_destination(archive_root: Path, original_name: str, file_hash: str) -> Path:
    now = datetime.now(timezone.utc)
    folder = archive_root / f"{now:%Y}" / f"{now:%m}"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / original_name
    if dest.exists():
        stem = Path(original_name).stem
        suffix = Path(original_name).suffix
        dest = folder / f"{stem}_{file_hash[:8]}{suffix}"
    return dest