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
    enabled: bool
    denoise: bool
    clahe: bool
    adaptive_threshold: bool
    deskew: bool
    upscale_min_side: int
    upscale_factor: float
    pdf_dpi: int


OCR_ENGINES = frozenset({"ollama", "paddleocr"})


@dataclass(frozen=True)
class OcrConfig:
    engine: str
    ollama_url: str
    model: str
    prompt: str
    timeout_seconds: int
    max_retries: int
    num_ctx: int
    use_both_gpus: bool = False
    secondary_ollama_url: str = "http://127.0.0.1:11435"
    secondary_model: str = "qwen2.5vl:7b"
    paddle_lang: str = "en"
    paddle_use_gpu: bool = True
    paddle_gpu_id: int = 1


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
    exports: Path
    database: Path
    watch_folder: Path | None
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


def is_under_path(path: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def is_watch_source(config: AppConfig, path: Path) -> bool:
    return is_under_path(path, config.watch_folder)


def is_archive_source(config: AppConfig, path: Path) -> bool:
    return is_under_path(path, config.archive)


def allowed_document_roots(config: AppConfig) -> list[Path]:
    roots = [config.archive.resolve()]
    if config.watch_folder is not None:
        roots.append(config.watch_folder.resolve())
    return roots


def resolve_allowed_document_path(config: AppConfig, path: Path) -> Path | None:
    resolved = path.resolve()
    for root in allowed_document_roots(config):
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    return None


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

    watch_folder_raw = raw.get("watch_folder")
    watch_folder = (
        _expand(watch_folder_raw)
        if isinstance(watch_folder_raw, str) and watch_folder_raw.strip()
        else None
    )

    return AppConfig(
        data_root=data_root,
        inbox=data_root / folders.get("inbox", "inbox"),
        transformed=data_root / folders.get("transformed", "transformed"),
        archive=data_root / folders.get("archive", "archive"),
        failed=data_root / folders.get("failed", "failed"),
        exports=data_root / folders.get("exports", "Exports"),
        database=data_root / raw.get("database", "packetpro.db"),
        watch_folder=watch_folder,
        enhance=EnhanceConfig(
            enabled=bool(enhance_raw.get("enabled", False)),
            denoise=bool(enhance_raw.get("denoise", True)),
            clahe=bool(enhance_raw.get("clahe", True)),
            adaptive_threshold=bool(enhance_raw.get("adaptive_threshold", True)),
            deskew=bool(enhance_raw.get("deskew", True)),
            upscale_min_side=int(enhance_raw.get("upscale_min_side", 1200)),
            upscale_factor=float(enhance_raw.get("upscale_factor", 2.0)),
            pdf_dpi=int(enhance_raw.get("pdf_dpi", 200)),
        ),
        ocr=OcrConfig(
            engine=str(ocr_raw.get("engine", "ollama")),
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
            use_both_gpus=bool(ocr_raw.get("use_both_gpus", False)),
            secondary_ollama_url=str(
                ocr_raw.get("secondary_ollama_url", "http://127.0.0.1:11435")
            ),
            secondary_model=str(ocr_raw.get("secondary_model", "qwen2.5vl:7b")),
            paddle_lang=str(ocr_raw.get("paddle_lang", "en")),
            paddle_use_gpu=bool(ocr_raw.get("paddle_use_gpu", True)),
            paddle_gpu_id=int(ocr_raw.get("paddle_gpu_id", 1)),
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
            "exports": folders.get("exports", "Exports"),
        },
        "database": str(raw.get("database", "packetpro.db")),
        "watch_folder": str(raw.get("watch_folder", "") or ""),
        "use_both_gpus": bool(raw.get("ocr", {}).get("use_both_gpus", False)),
        "ocr_engine": str(raw.get("ocr", {}).get("engine", "ollama")),
        "ocr": {
            "engine": str(raw.get("ocr", {}).get("engine", "ollama")),
            "paddle_lang": str(raw.get("ocr", {}).get("paddle_lang", "en")),
            "paddle_use_gpu": bool(raw.get("ocr", {}).get("paddle_use_gpu", True)),
            "paddle_gpu_id": int(raw.get("ocr", {}).get("paddle_gpu_id", 1)),
            "primary_url": str(raw.get("ocr", {}).get("ollama_url", "http://127.0.0.1:11434")),
            "secondary_url": str(
                raw.get("ocr", {}).get("secondary_ollama_url", "http://127.0.0.1:11435")
            ),
            "primary_model": str(raw.get("ocr", {}).get("model", "qwen2.5vl:7b")),
            "secondary_model": str(
                raw.get("ocr", {}).get("secondary_model", "qwen2.5vl:7b")
            ),
        },
    }


def _validate_folder_name(name: str, label: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ConfigError(f"{label} is required.")
    if cleaned in {".", ".."} or "/" in cleaned or "\\" in cleaned:
        raise ConfigError(f"{label} must be a simple folder name.")
    return cleaned


def _validate_watch_folder(path: str) -> str | None:
    cleaned = path.strip()
    if not cleaned:
        return None
    resolved = _expand(cleaned)
    if not resolved.is_absolute():
        raise ConfigError("Watch folder must be an absolute path.")
    return str(resolved)


def check_watch_folder_access(path: str) -> dict[str, Any]:
    cleaned = path.strip()
    if not cleaned:
        return {"ok": False, "empty": True, "message": ""}

    resolved = _expand(cleaned)
    if not resolved.is_absolute():
        return {"ok": False, "message": "Path must be absolute", "path": str(resolved)}

    if not resolved.exists():
        return {"ok": False, "message": "Path does not exist", "path": str(resolved)}

    if not resolved.is_dir():
        return {"ok": False, "message": "Path is not a directory", "path": str(resolved)}

    if not os.access(resolved, os.R_OK | os.X_OK):
        return {"ok": False, "message": "Directory is not readable", "path": str(resolved)}

    try:
        next(resolved.iterdir(), None)
    except OSError as exc:
        return {
            "ok": False,
            "message": f"Cannot read directory: {exc}",
            "path": str(resolved),
        }

    return {"ok": True, "message": "Folder is readable", "path": str(resolved)}


def _validate_ocr_model(name: str, label: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ConfigError(f"{label} is required.")
    if any(char in cleaned for char in ("\n", "\r", "\t")):
        raise ConfigError(f"{label} is invalid.")
    return cleaned


def _validate_ocr_engine(engine: str) -> str:
    cleaned = engine.strip().lower()
    if cleaned not in OCR_ENGINES:
        raise ConfigError(f"OCR engine must be one of: {', '.join(sorted(OCR_ENGINES))}")
    return cleaned


def save_ocr_engine_settings(
    *,
    engine: str,
    config_path: Path | None = None,
) -> AppConfig:
    resolved = resolve_config_path(config_path)
    existing = _load_yaml(resolved)
    ocr_existing = existing.get("ocr", {}) if isinstance(existing.get("ocr"), dict) else {}
    ocr_existing["engine"] = _validate_ocr_engine(engine)

    payload = dict(existing)
    payload["ocr"] = ocr_existing
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False)

    config = parse_config(_deep_merge(_load_yaml(PROJECT_DEFAULTS), payload), resolved)
    ensure_data_dirs(config)
    return config


def save_ocr_model_settings(
    *,
    primary_model: str,
    secondary_model: str | None = None,
    config_path: Path | None = None,
) -> AppConfig:
    resolved = resolve_config_path(config_path)
    existing = _load_yaml(resolved)
    ocr_existing = existing.get("ocr", {}) if isinstance(existing.get("ocr"), dict) else {}
    ocr_existing["model"] = _validate_ocr_model(primary_model, "Primary OCR model")
    if secondary_model is not None:
        ocr_existing["secondary_model"] = _validate_ocr_model(
            secondary_model,
            "Secondary OCR model",
        )

    payload = dict(existing)
    payload["ocr"] = ocr_existing
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False)

    config = parse_config(_deep_merge(_load_yaml(PROJECT_DEFAULTS), payload), resolved)
    ensure_data_dirs(config)
    return config


def save_paths_settings(
    *,
    data_root: str,
    inbox: str,
    transformed: str,
    archive: str,
    failed: str,
    database: str,
    watch_folder: str = "",
    use_both_gpus: bool = False,
    ocr_model: str | None = None,
    secondary_ocr_model: str | None = None,
    config_path: Path | None = None,
) -> AppConfig:
    resolved = resolve_config_path(config_path)
    existing = _load_yaml(resolved)
    root = _expand(data_root.strip())
    if not str(root):
        raise ConfigError("Data root is required.")

    payload: dict[str, Any] = {
        key: value
        for key, value in existing.items()
        if key not in {"data_root", "folders", "database", "watch_folder", "ocr"}
    }
    payload["data_root"] = str(root)
    payload["folders"] = {
        "inbox": _validate_folder_name(inbox, "Inbox folder"),
        "transformed": _validate_folder_name(transformed, "Transformed folder"),
        "archive": _validate_folder_name(archive, "Archive folder"),
        "failed": _validate_folder_name(failed, "Failed folder"),
    }
    payload["database"] = _validate_folder_name(database, "Database file")
    watch_path = _validate_watch_folder(watch_folder)
    if watch_path is not None:
        payload["watch_folder"] = watch_path

    ocr_existing = existing.get("ocr", {}) if isinstance(existing.get("ocr"), dict) else {}
    payload["ocr"] = {
        **ocr_existing,
        "use_both_gpus": use_both_gpus,
    }
    if ocr_model is not None and ocr_model.strip():
        payload["ocr"]["model"] = _validate_ocr_model(ocr_model, "Primary OCR model")
    if secondary_ocr_model is not None and secondary_ocr_model.strip():
        payload["ocr"]["secondary_model"] = _validate_ocr_model(
            secondary_ocr_model,
            "Secondary OCR model",
        )

    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False)

    config = parse_config(_deep_merge(_load_yaml(PROJECT_DEFAULTS), payload), resolved)
    ensure_data_dirs(config)
    return config


def ensure_data_dirs(config: AppConfig) -> None:
    for folder in (
        config.inbox,
        config.transformed,
        config.archive,
        config.failed,
        config.exports,
    ):
        folder.mkdir(parents=True, exist_ok=True)