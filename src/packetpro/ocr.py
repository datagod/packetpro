"""OCR clients for Ollama vision models and PaddleOCR."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from packetpro.config import OcrConfig

MAX_OCR_DIMENSION = 2048
OLLAMA_CHECK_TIMEOUT = 5.0

_paddle_lock = threading.Lock()
_paddle_ocr = None
_paddle_ocr_key: str | None = None


@dataclass(frozen=True)
class OcrBackend:
    label: str
    config: OcrConfig


def _backend_config(ocr: OcrConfig, *, primary: bool) -> OcrConfig:
    if primary:
        return ocr
    return OcrConfig(
        engine=ocr.engine,
        ollama_url=ocr.secondary_ollama_url,
        model=ocr.secondary_model,
        prompt=ocr.prompt,
        timeout_seconds=ocr.timeout_seconds,
        max_retries=ocr.max_retries,
        num_ctx=ocr.num_ctx,
        use_both_gpus=ocr.use_both_gpus,
        secondary_ollama_url=ocr.secondary_ollama_url,
        secondary_model=ocr.secondary_model,
        paddle_lang=ocr.paddle_lang,
        paddle_use_gpu=ocr.paddle_use_gpu,
        paddle_gpu_id=ocr.paddle_gpu_id,
    )


def _uses_paddleocr(ocr: OcrConfig) -> bool:
    return ocr.engine == "paddleocr"


def _paddle_config_key(ocr: OcrConfig) -> str:
    return f"{ocr.paddle_lang}|{ocr.paddle_use_gpu}|{ocr.paddle_gpu_id}"


def _configure_paddle_runtime() -> None:
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def _paddle_cuda_available() -> bool:
    try:
        import paddle

        return bool(
            paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0
        )
    except Exception:  # noqa: BLE001
        return False


def _resolve_paddle_device(ocr: OcrConfig) -> tuple[str, str]:
    if not ocr.paddle_use_gpu:
        return "cpu", "CPU"
    if not _paddle_cuda_available():
        return "cpu", "CPU (CUDA unavailable — install paddlepaddle-gpu)"
    gpu_id = max(0, int(ocr.paddle_gpu_id))
    return f"gpu:{gpu_id}", f"GPU{gpu_id}"


def reset_paddle_ocr() -> None:
    global _paddle_ocr, _paddle_ocr_key
    with _paddle_lock:
        if _paddle_ocr is not None:
            try:
                _paddle_ocr.close()
            except Exception:  # noqa: BLE001
                pass
        _paddle_ocr = None
        _paddle_ocr_key = None


def _get_paddle_ocr(ocr: OcrConfig):
    global _paddle_ocr, _paddle_ocr_key
    key = _paddle_config_key(ocr)
    with _paddle_lock:
        if _paddle_ocr is not None and _paddle_ocr_key == key:
            return _paddle_ocr

        _configure_paddle_runtime()
        from paddleocr import PaddleOCR

        if _paddle_ocr is not None:
            try:
                _paddle_ocr.close()
            except Exception:  # noqa: BLE001
                pass

        device, _ = _resolve_paddle_device(ocr)
        _paddle_ocr = PaddleOCR(
            lang=ocr.paddle_lang,
            device=device,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        _paddle_ocr_key = key
        return _paddle_ocr


def check_paddleocr_backend(ocr: OcrConfig) -> dict[str, Any]:
    try:
        _configure_paddle_runtime()
        import paddleocr  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "message": "PaddleOCR is not installed",
            "error": str(exc),
            "engine": "paddleocr",
            "lang": ocr.paddle_lang,
        }

    device, device_label = _resolve_paddle_device(ocr)
    cuda_compiled = _paddle_cuda_available()
    if ocr.paddle_use_gpu and not cuda_compiled:
        return {
            "ok": False,
            "message": "Paddle GPU requested but paddlepaddle-gpu is not available",
            "engine": "paddleocr",
            "lang": ocr.paddle_lang,
            "device": device,
            "device_label": device_label,
            "cuda": False,
        }

    return {
        "ok": True,
        "message": f"PaddleOCR ready on {device_label} ({ocr.paddle_lang})",
        "engine": "paddleocr",
        "lang": ocr.paddle_lang,
        "device": device,
        "device_label": device_label,
        "cuda": device.startswith("gpu"),
    }


def list_ollama_models(
    ollama_url: str,
    *,
    timeout: float = OLLAMA_CHECK_TIMEOUT,
) -> dict[str, Any]:
    base = ollama_url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{base}/api/tags")
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "message": f"Cannot reach Ollama at {base}",
            "error": str(exc),
            "url": base,
            "models": [],
        }

    models = sorted(
        {
            str(entry.get("name", "")).strip()
            for entry in data.get("models", [])
            if isinstance(entry, dict) and entry.get("name")
        }
    )
    return {
        "ok": True,
        "message": f"{len(models)} model(s) available",
        "url": base,
        "models": models,
    }


def check_ollama_backend(
    ollama_url: str,
    model: str,
    *,
    timeout: float = OLLAMA_CHECK_TIMEOUT,
) -> dict[str, Any]:
    base = ollama_url.rstrip("/")
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{base}/api/tags")
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "message": f"Cannot reach Ollama at {base}",
            "error": str(exc),
            "url": base,
            "model": model,
        }

    model_names = [
        str(entry.get("name", ""))
        for entry in data.get("models", [])
        if isinstance(entry, dict)
    ]
    available = any(
        name == model or name.startswith(f"{model}:")
        for name in model_names
    )
    if not available:
        return {
            "ok": False,
            "message": f"Model {model} is not available",
            "url": base,
            "model": model,
            "models": sorted(model_names),
        }

    return {
        "ok": True,
        "message": "Ollama is ready",
        "url": base,
        "model": model,
    }


def resolve_ocr_backends(ocr: OcrConfig) -> list[OcrBackend]:
    if _uses_paddleocr(ocr):
        status = check_paddleocr_backend(ocr)
        if not status["ok"]:
            return []
        device_label = check_paddleocr_backend(ocr).get("device_label", "PaddleOCR")
        return [OcrBackend(label=str(device_label), config=ocr)]

    primary = OcrBackend(label="GPU1", config=_backend_config(ocr, primary=True))
    if not ocr.use_both_gpus:
        return [primary]

    secondary_cfg = _backend_config(ocr, primary=False)
    secondary_status = check_ollama_backend(
        secondary_cfg.ollama_url,
        secondary_cfg.model,
    )
    if secondary_status["ok"]:
        return [primary, OcrBackend(label="GPU0", config=secondary_cfg)]

    return [primary]


def backend_index_for_job(job_id: str, backend_count: int) -> int:
    if backend_count <= 1:
        return 0
    digest = hashlib.sha256(job_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % backend_count


def describe_ocr_backends(ocr: OcrConfig) -> dict[str, Any]:
    if _uses_paddleocr(ocr):
        primary = check_paddleocr_backend(ocr)
        primary["label"] = "PaddleOCR"
        active = resolve_ocr_backends(ocr)
        return {
            "engine": "paddleocr",
            "use_both_gpus": False,
            "active_backends": [backend.label for backend in active],
            "parallel": False,
            "primary": primary,
            "secondary": None,
        }

    primary = check_ollama_backend(ocr.ollama_url, ocr.model)
    primary["label"] = "GPU1"
    secondary = None
    if ocr.use_both_gpus:
        secondary = check_ollama_backend(ocr.secondary_ollama_url, ocr.secondary_model)
        secondary["label"] = "GPU0"
    active = resolve_ocr_backends(ocr)
    return {
        "engine": "ollama",
        "use_both_gpus": ocr.use_both_gpus,
        "active_backends": [backend.label for backend in active],
        "parallel": len(active) > 1,
        "primary": primary,
        "secondary": secondary,
    }


def _encode_image(path: Path) -> str:
    with Image.open(path) as img:
        img = img.convert("RGB")
        width, height = img.size
        longest = max(width, height)
        if longest > MAX_OCR_DIMENSION:
            scale = MAX_OCR_DIMENSION / longest
            img = img.resize(
                (int(width * scale), int(height * scale)),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")


def _extract_text_ollama(image_path: Path, config: OcrConfig) -> str:
    payload = {
        "model": config.model,
        "messages": [
            {
                "role": "user",
                "content": config.prompt,
                "images": [_encode_image(image_path)],
            }
        ],
        "stream": False,
        "options": {"num_ctx": config.num_ctx},
    }
    url = f"{config.ollama_url.rstrip('/')}/api/chat"
    last_error: Exception | None = None

    for attempt in range(config.max_retries):
        try:
            with httpx.Client(timeout=config.timeout_seconds) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
            message = data.get("message", {})
            content = message.get("content", "")
            if not isinstance(content, str):
                raise ValueError("Ollama response missing text content")
            text = content.strip()
            if not text:
                raise ValueError("Ollama returned empty OCR text")
            return text
        except Exception as exc:  # noqa: BLE001 - retry on any transient failure
            last_error = exc
            if attempt + 1 < config.max_retries:
                time.sleep(2**attempt)

    raise RuntimeError(f"OCR failed after {config.max_retries} attempts: {last_error}") from last_error


def _extract_text_paddleocr(image_path: Path, config: OcrConfig) -> str:
    last_error: Exception | None = None

    for attempt in range(config.max_retries):
        try:
            engine = _get_paddle_ocr(config)
            results = engine.predict(str(image_path))
            if not results:
                raise ValueError("PaddleOCR returned no results")

            lines: list[str] = []
            for item in results:
                if isinstance(item, dict):
                    rec_texts = item.get("rec_texts") or []
                    for text in rec_texts:
                        cleaned = str(text).strip()
                        if cleaned:
                            lines.append(cleaned)
                    continue

                if isinstance(item, (list, tuple)):
                    for entry in item:
                        if not entry or len(entry) < 2:
                            continue
                        text_info = entry[1]
                        if isinstance(text_info, (list, tuple)) and text_info:
                            cleaned = str(text_info[0]).strip()
                            if cleaned:
                                lines.append(cleaned)

            text = "\n".join(lines).strip()
            if not text:
                raise ValueError("PaddleOCR returned empty OCR text")
            return text
        except Exception as exc:  # noqa: BLE001 - retry on any transient failure
            last_error = exc
            if attempt + 1 < config.max_retries:
                time.sleep(2**attempt)

    raise RuntimeError(
        f"PaddleOCR failed after {config.max_retries} attempts: {last_error}"
    ) from last_error


def extract_text(image_path: Path, config: OcrConfig) -> str:
    if _uses_paddleocr(config):
        return _extract_text_paddleocr(image_path, config)
    return _extract_text_ollama(image_path, config)