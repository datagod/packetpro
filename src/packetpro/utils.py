"""Shared utilities for PacketPro workers."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_identity_hash(path: Path) -> str:
    stat = path.stat()
    payload = f"{path.name}:{stat.st_size}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    tmp.replace(path)


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