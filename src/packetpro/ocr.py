"""Ollama vision OCR client."""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path

import httpx
from PIL import Image

from packetpro.config import OcrConfig

MAX_OCR_DIMENSION = 2048


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


def extract_text(image_path: Path, config: OcrConfig) -> str:
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