"""AI-ready text export and PDF generation."""

from __future__ import annotations

import re
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import fitz

from packetpro.db import Document

EXPORT_SEARCH_LIMIT = 500
PDF_FONT = "helv"
PDF_FONT_SIZE = 9
PDF_LINE_HEIGHT = 12.5
PDF_MARGIN = 54
PDF_CHARS_PER_LINE = 92


def format_ai_export(query: str, documents: list[Document]) -> str:
    exported_at = datetime.now(timezone.utc).isoformat()
    lines = [
        "PACKETPRO EXPORT — AI ANALYSIS DOCUMENT",
        "=" * 80,
        "",
        "METADATA",
        f'  query: "{query}"',
        f"  exported_at: {exported_at}",
        f"  document_count: {len(documents)}",
        "  source: PacketPro OCR archive",
        "",
        "INSTRUCTIONS FOR AI",
        "  The document blocks below contain OCR-extracted text from archived files.",
        "  Each block lists the source filename, page number, and processing timestamp.",
        "  OCR may contain recognition errors; use filenames and context when interpreting.",
        "",
        "=" * 80,
        "",
    ]

    total = len(documents)
    for index, doc in enumerate(documents, start=1):
        page_label = doc.page_number if doc.page_number > 1 else 1
        lines.extend(
            [
                f"--- DOCUMENT {index} OF {total} ---",
                f"source_file: {doc.original_name}",
                f"page: {page_label}",
                f"document_id: {doc.id}",
                f"processed_at: {doc.processed_at}",
                "",
                doc.ocr_text.strip() or "[no text extracted]",
                "",
                "-" * 80,
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _wrap_lines(text: str) -> list[str]:
    wrapped: list[str] = []
    for raw_line in text.splitlines():
        if not raw_line:
            wrapped.append("")
            continue
        if len(raw_line) <= PDF_CHARS_PER_LINE:
            wrapped.append(raw_line)
            continue
        wrapped.extend(
            textwrap.wrap(
                raw_line,
                width=PDF_CHARS_PER_LINE,
                break_long_words=True,
                replace_whitespace=False,
            )
        )
    return wrapped


def write_export_pdf(output_path: Path, text: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page_width, page_height = fitz.paper_size("a4")
    max_y = page_height - PDF_MARGIN

    page = doc.new_page(width=page_width, height=page_height)
    y = PDF_MARGIN

    for line in _wrap_lines(text):
        if y + PDF_LINE_HEIGHT > max_y:
            page = doc.new_page(width=page_width, height=page_height)
            y = PDF_MARGIN
        page.insert_text(
            (PDF_MARGIN, y),
            line,
            fontsize=PDF_FONT_SIZE,
            fontname=PDF_FONT,
        )
        y += PDF_LINE_HEIGHT

    doc.save(output_path)
    doc.close()


def export_filename(query: str, suffix: str = "pdf") -> str:
    slug = re.sub(r"[^\w]+", "-", query.strip().lower()).strip("-")
    slug = slug[:48] or "search"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"export_{timestamp}_{slug}.{suffix}"