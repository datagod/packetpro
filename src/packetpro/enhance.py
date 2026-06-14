"""Image and PDF enhancement for OCR."""

from __future__ import annotations

from pathlib import Path

import cv2
import fitz
import numpy as np

from packetpro.config import EnhanceConfig


def render_pdf_page(pdf_path: Path, page_index: int, dpi: int) -> np.ndarray:
    with fitz.open(pdf_path) as doc:
        page = doc.load_page(page_index)
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    return img


def load_image(path: Path) -> np.ndarray:
    data = path.read_bytes()
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Unable to decode image: {path}")
    return img


def _deskew(gray: np.ndarray) -> np.ndarray:
    coords = np.column_stack(np.where(gray < 128))
    if len(coords) < 50:
        return gray
    rect = cv2.minAreaRect(coords.astype(np.float32))
    angle = rect[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5 or abs(angle) > 15:
        return gray
    h, w = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        gray,
        matrix,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def enhance_image(
    image: np.ndarray,
    config: EnhanceConfig,
    *,
    skip_upscale: bool = False,
) -> np.ndarray:
    if not config.enabled:
        return image.copy()

    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    if config.denoise:
        gray = cv2.fastNlMeansDenoising(gray, h=10)

    if config.clahe:
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

    if config.deskew:
        gray = _deskew(gray)

    min_side = min(gray.shape[:2])
    if not skip_upscale and min_side < config.upscale_min_side:
        scale = config.upscale_factor
        gray = cv2.resize(
            gray,
            None,
            fx=scale,
            fy=scale,
            interpolation=cv2.INTER_CUBIC,
        )

    if config.adaptive_threshold:
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        binary = cv2.adaptiveThreshold(
            blurred,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11,
        )
        if np.mean(binary) < 127:
            binary = cv2.bitwise_not(binary)
        return binary

    return gray


def save_enhanced_image(image: np.ndarray, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError(f"Failed to encode enhanced image: {output_path}")
    output_path.write_bytes(encoded.tobytes())


def pdf_page_count(pdf_path: Path) -> int:
    with fitz.open(pdf_path) as doc:
        return doc.page_count


def load_source_image(source_path: Path, page_number: int, pdf_dpi: int) -> np.ndarray:
    if source_path.suffix.lower() == ".pdf":
        return render_pdf_page(source_path, page_number - 1, pdf_dpi)
    return load_image(source_path)