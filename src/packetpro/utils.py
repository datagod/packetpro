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


def write_error_sidecar(path: Path, error: str) -> None:
    sidecar = path.with_suffix(path.suffix + ".error.txt")
    sidecar.write_text(error, encoding="utf-8")


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