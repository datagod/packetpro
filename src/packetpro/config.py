"""Configuration loader for PacketPro."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config.default.yaml"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp", ".bmp"}
PDF_EXTENSION = ".pdf"
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | {PDF_EXTENSION}


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


def _expand(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def load_config(config_path: Path | None = None) -> AppConfig:
    path = config_path or DEFAULT_CONFIG
    with path.open(encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    data_root = _expand(raw.get("data_root", "~/packetpro-data"))
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
    )


def ensure_data_dirs(config: AppConfig) -> None:
    for folder in (config.inbox, config.transformed, config.archive, config.failed):
        folder.mkdir(parents=True, exist_ok=True)