"""Configuration loader for PacketPro."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_DEFAULTS = PROJECT_ROOT / "config.default.yaml"
USER_CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "packetpro"
USER_CONFIG_PATH = Path(os.environ.get("PACKETPRO_CONFIG", USER_CONFIG_DIR / "config.yaml"))

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp", ".bmp"}
PDF_EXTENSION = ".pdf"
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | {PDF_EXTENSION}


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class EnhanceConfig:
    denoise: bool
    clahe: bool
    adaptive_threshold: bool
    deskew: bool
    upscale_min_side: int
    upscale_factor: float
    pdf_dpi: int


@dataclass(frozen=True)
class OcrConfig:
    ollama_url: str
    model: str
    prompt: str
    timeout_seconds: int
    max_retries: int
    num_ctx: int


@dataclass(frozen=True)
class WebConfig:
    host: str
    port: int


@dataclass(frozen=True)
class WatcherConfig:
    settle_seconds: float
    poll_interval: float


@dataclass(frozen=True)
class AppConfig:
    data_root: Path
    inbox: Path
    transformed: Path
    archive: Path
    failed: Path
    database: Path
    enhance: EnhanceConfig
    ocr: OcrConfig
    web: WebConfig
    watcher: WatcherConfig
    config_path: Path


def resolve_config_path(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    return USER_CONFIG_PATH.expanduser().resolve()


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_raw_config(config_path: Path | None = None) -> tuple[dict[str, Any], Path]:
    resolved = resolve_config_path(config_path)
    raw = _load_yaml(PROJECT_DEFAULTS)
    if resolved != PROJECT_DEFAULTS.resolve():
        raw = _deep_merge(raw, _load_yaml(resolved))
    return raw, resolved


def paths_configured(raw: dict[str, Any]) -> bool:
    data_root = raw.get("data_root")
    return isinstance(data_root, str) and bool(data_root.strip())


def parse_config(raw: dict[str, Any], config_path: Path) -> AppConfig:
    if not paths_configured(raw):
        raise ConfigError(
            "Folder locations are not configured. "
            "Open the PacketPro web UI at /settings to set them."
        )

    data_root = _expand(raw["data_root"])
    folders = raw.get("folders", {})
    enhance_raw = raw.get("enhance", {})
    ocr_raw = raw.get("ocr", {})
    web_raw = raw.get("web", {})
    watcher_raw = raw.get("watcher", {})

    return AppConfig(
        data_root=data_root,
        inbox=data_root / folders.get("inbox", "inbox"),
        transformed=data_root / folders.get("transformed", "transformed"),
        archive=data_root / folders.get("archive", "archive"),
        failed=data_root / folders.get("failed", "failed"),
        database=data_root / raw.get("database", "packetpro.db"),
        enhance=EnhanceConfig(
            denoise=bool(enhance_raw.get("denoise", True)),
            clahe=bool(enhance_raw.get("clahe", True)),
            adaptive_threshold=bool(enhance_raw.get("adaptive_threshold", True)),
            deskew=bool(enhance_raw.get("deskew", True)),
            upscale_min_side=int(enhance_raw.get("upscale_min_side", 1200)),
            upscale_factor=float(enhance_raw.get("upscale_factor", 2.0)),
            pdf_dpi=int(enhance_raw.get("pdf_dpi", 200)),
        ),
        ocr=OcrConfig(
            ollama_url=str(ocr_raw.get("ollama_url", "http://127.0.0.1:11434")),
            model=str(ocr_raw.get("model", "qwen2.5vl:7b")),
            prompt=str(
                ocr_raw.get(
                    "prompt",
                    "Extract all visible text from this image verbatim. "
                    "Preserve line breaks and paragraph structure. "
                    "Output only the extracted text with no commentary.",
                )
            ),
            timeout_seconds=int(ocr_raw.get("timeout_seconds", 300)),
            max_retries=int(ocr_raw.get("max_retries", 3)),
            num_ctx=int(ocr_raw.get("num_ctx", 32768)),
        ),
        web=WebConfig(
            host=str(web_raw.get("host", "127.0.0.1")),
            port=int(web_raw.get("port", 8787)),
        ),
        watcher=WatcherConfig(
            settle_seconds=float(watcher_raw.get("settle_seconds", 1.0)),
            poll_interval=float(watcher_raw.get("poll_interval", 0.5)),
        ),
        config_path=config_path,
    )


def load_config(config_path: Path | None = None) -> AppConfig:
    raw, resolved = load_raw_config(config_path)
    return parse_config(raw, resolved)


def get_paths_settings(config_path: Path | None = None) -> dict[str, Any]:
    raw, resolved = load_raw_config(config_path)
    folders = raw.get("folders", {})
    return {
        "config_path": str(resolved),
        "configured": paths_configured(raw),
        "data_root": str(raw.get("data_root", "")),
        "folders": {
            "inbox": folders.get("inbox", "inbox"),
            "transformed": folders.get("transformed", "transformed"),
            "archive": folders.get("archive", "archive"),
            "failed": folders.get("failed", "failed"),
        },
        "database": str(raw.get("database", "packetpro.db")),
    }


def _validate_folder_name(name: str, label: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ConfigError(f"{label} is required.")
    if cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        raise ConfigError(f"{label} must be a simple folder name.")
    return cleaned


def save_paths_settings(
    *,
    data_root: str,
    inbox: str,
    transformed: str,
    archive: str,
    failed: str,
    database: str,
    config_path: Path | None = None,
) -> AppConfig:
    resolved = resolve_config_path(config_path)
    root = _expand(data_root.strip())
    if not str(root):
        raise ConfigError("Data root is required.")

    payload = {
        "data_root": str(root),
        "folders": {
            "inbox": _validate_folder_name(inbox, "Inbox folder"),
            "transformed": _validate_folder_name(transformed, "Transformed folder"),
            "archive": _validate_folder_name(archive, "Archive folder"),
            "failed": _validate_folder_name(failed, "Failed folder"),
        },
        "database": _validate_folder_name(database, "Database file"),
    }

    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False)

    config = parse_config(_deep_merge(_load_yaml(PROJECT_DEFAULTS), payload), resolved)
    ensure_data_dirs(config)
    return config


def ensure_data_dirs(config: AppConfig) -> None:
    for folder in (config.inbox, config.transformed, config.archive, config.failed):
        folder.mkdir(parents=True, exist_ok=True)