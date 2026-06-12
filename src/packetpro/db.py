"""SQLite + FTS5 persistence for PacketPro."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    job_id        TEXT UNIQUE NOT NULL,
    original_name TEXT NOT NULL,
    archive_path  TEXT NOT NULL,
    page_number   INTEGER NOT NULL DEFAULT 1,
    ocr_text      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    processed_at  TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    original_name,
    ocr_text,
    content='documents',
    content_rowid='id',
    tokenize='porter'
);

CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, original_name, ocr_text)
    VALUES (new.id, new.original_name, new.ocr_text);
END;

CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, original_name, ocr_text)
    VALUES ('delete', old.id, old.original_name, old.ocr_text);
END;

CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, original_name, ocr_text)
    VALUES ('delete', old.id, old.original_name, old.ocr_text);
    INSERT INTO documents_fts(rowid, original_name, ocr_text)
    VALUES (new.id, new.original_name, new.ocr_text);
END;
"""


@dataclass(frozen=True)
class Document:
    id: int
    job_id: str
    original_name: str
    archive_path: str
    page_number: int
    ocr_text: str
    created_at: str
    processed_at: str


@dataclass(frozen=True)
class SearchResult:
    document: Document
    snippet: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def insert_document(
    db_path: Path,
    *,
    job_id: str,
    original_name: str,
    archive_path: str,
    page_number: int,
    ocr_text: str,
    created_at: str | None = None,
) -> int:
    now = utc_now()
    created = created_at or now
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO documents (
                job_id, original_name, archive_path, page_number,
                ocr_text, created_at, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, original_name, archive_path, page_number, ocr_text, created, now),
        )
        conn.commit()
        return int(cursor.lastrowid)


def get_document(db_path: Path, doc_id: int) -> Document | None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if row is None:
        return None
    return Document(**dict(row))


def _fts_query(query: str) -> str:
    terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not terms:
        return ""
    return " ".join(f'"{term}"' for term in terms)


def _make_snippet(text: str, query: str, radius: int = 80) -> str:
    if not text:
        return ""
    lowered = text.lower()
    for term in re.findall(r"[\w]+", query, flags=re.UNICODE):
        idx = lowered.find(term.lower())
        if idx >= 0:
            start = max(0, idx - radius)
            end = min(len(text), idx + len(term) + radius)
            snippet = text[start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(text):
                snippet = snippet + "..."
            pattern = re.compile(re.escape(term), re.IGNORECASE)
            return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", snippet)
    return text[: radius * 2] + ("..." if len(text) > radius * 2 else "")


def search_documents(db_path: Path, query: str, limit: int = 50) -> list[SearchResult]:
    fts_q = _fts_query(query)
    if not fts_q:
        return []

    sql = """
        SELECT d.*
        FROM documents_fts fts
        JOIN documents d ON d.id = fts.rowid
        WHERE documents_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, (fts_q, limit)).fetchall()

    results: list[SearchResult] = []
    for row in rows:
        doc = Document(**dict(row))
        results.append(SearchResult(document=doc, snippet=_make_snippet(doc.ocr_text, query)))
    return results