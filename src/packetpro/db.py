"""SQLite + FTS5 persistence for PacketPro."""

from __future__ import annotations

import html
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

CREATE TABLE IF NOT EXISTS processed_files (
    file_hash     TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    file_size     INTEGER NOT NULL,
    processed_at  TEXT NOT NULL
);
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


def file_hash_exists(db_path: Path, file_hash: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_files WHERE file_hash = ? LIMIT 1",
            (file_hash,),
        ).fetchone()
    return row is not None


def register_processed_file(
    db_path: Path,
    *,
    file_hash: str,
    original_name: str,
    file_size: int,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO processed_files (
                file_hash, original_name, file_size, processed_at
            ) VALUES (?, ?, ?, ?)
            """,
            (file_hash, original_name, file_size, utc_now()),
        )
        conn.commit()


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


def _highlight_full_text(text: str, query: str) -> str:
    if not text:
        return ""
    highlighted = html.escape(text)
    for term in re.findall(r"[\w]+", query, flags=re.UNICODE):
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        highlighted = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", highlighted)
    return highlighted


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
        results.append(
            SearchResult(document=doc, snippet=_highlight_full_text(doc.ocr_text, query))
        )
    return results