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

CREATE INDEX IF NOT EXISTS idx_documents_archive_path ON documents(archive_path);
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


class SearchQueryError(Exception):
    """Raised when an FTS5 query is malformed or cannot be executed."""


SEARCH_LIMIT_DEFAULT = 50
SEARCH_LIMIT_CHOICES = frozenset({50, 100})
SEARCH_LIMIT_ALL_MAX = 1000

_FTS_OPERATORS = frozenset({"AND", "OR", "NOT", "NEAR"})

_FILENAME_YMD = re.compile(
    r"(?<!\d)(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})"
    r"(?:[^\d](\d{1,2})[:_.](\d{1,2})(?:[:_.](\d{1,2}))?)?(?!\d)"
)
_FILENAME_YMD_COMPACT = re.compile(
    r"(?<!\d)(20\d{2})(\d{2})(\d{2})"
    r"(?:[_\-.T]?(\d{2})(\d{2})(\d{2}))?(?!\d)"
)
_FILENAME_MDY = re.compile(r"(?<!\d)(\d{1,2})[-_.](\d{1,2})[-_.](20\d{2})(?!\d)")


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


def count_documents_with_archive(db_path: Path, archive_path: str) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE archive_path = ?",
            (archive_path,),
        ).fetchone()
    return int(row[0]) if row else 0


def count_documents(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
    return int(row[0]) if row else 0


def count_distinct_sources(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT archive_path) FROM documents"
        ).fetchone()
    return int(row[0]) if row else 0


def delete_document(db_path: Path, doc_id: int) -> bool:
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.commit()
        return cursor.rowcount > 0


def _fts_query(query: str) -> str:
    """Normalize an FTS5 query and quote dotted filename-like tokens."""
    query = query.strip()
    if not query:
        return ""
    return _quote_dotted_fts_tokens(query)


def _quote_dotted_fts_tokens(query: str) -> str:
    """Quote tokens that contain dots so FTS5 does not treat them as syntax."""
    tokens: list[str] = []
    for match in re.finditer(r'"([^"]*)"|(\S+)', query, flags=re.UNICODE):
        if match.group(1) is not None:
            phrase = match.group(1).strip()
            tokens.append(f'"{phrase}"' if phrase else '""')
            continue

        token = match.group(2) or ""
        if ":" in token and not token.endswith(":"):
            column, value = token.split(":", 1)
            if (
                value
                and "." in value
                and not (value.startswith('"') and value.endswith('"'))
            ):
                token = f'{column}:"{value}"'
        elif "." in token:
            token = f'"{token}"'
        tokens.append(token)
    return " ".join(tokens)


def _filename_match_score(name: str, query: str) -> int:
    """Score how strongly a filename matches the user's search terms."""
    if not name or not query.strip():
        return 0

    stem = Path(name).stem.lower()
    full = name.lower()
    score = 0
    for term, is_prefix in _extract_highlight_terms(query):
        t = term.lower()
        if not t:
            continue
        if stem == t or full == t:
            score += 100
        elif is_prefix and stem.startswith(t):
            score += 60
        elif not is_prefix and t in stem:
            score += 40
        elif is_prefix and full.startswith(t):
            score += 30
        elif not is_prefix and t in full:
            score += 20
    return score


def _extract_highlight_terms(query: str) -> list[tuple[str, bool]]:
    """Return (term, is_prefix) pairs parsed from an FTS5 query."""
    terms: list[tuple[str, bool]] = []
    pattern = re.compile(r'"([^"]*)"|(\S+)', flags=re.UNICODE)
    for match in pattern.finditer(query.strip()):
        if match.group(1) is not None:
            phrase = match.group(1).strip()
            if phrase:
                terms.append((phrase, False))
            continue

        token = match.group(2) or ""
        token = token.strip("()")
        if not token:
            continue
        if ":" in token:
            token = token.split(":", 1)[1]
            if not token:
                continue
        if token.upper() in _FTS_OPERATORS:
            continue

        is_prefix = token.endswith("*")
        term = token.rstrip("*")
        if term and term.upper() not in _FTS_OPERATORS:
            terms.append((term, is_prefix))
    return terms


def _build_search_snippet(doc: Document, query: str) -> str:
    """Build highlighted OCR text, surfacing filename matches when OCR does not hit."""
    ocr = doc.ocr_text or ""
    highlighted = _highlight_full_text(ocr, query)
    if "<mark>" in highlighted:
        return highlighted

    if _filename_match_score(doc.original_name, query) <= 0:
        return highlighted

    name_line = _highlight_full_text(doc.original_name, query)
    if ocr.strip():
        return f"<strong>Filename:</strong> {name_line}\n\n{highlighted}"
    return f"<strong>Filename:</strong> {name_line}"


def _highlight_full_text(text: str, query: str) -> str:
    if not text:
        return ""
    highlighted = html.escape(text)
    for term, is_prefix in sorted(
        _extract_highlight_terms(query), key=lambda item: len(item[0]), reverse=True
    ):
        if " " in term or not is_prefix:
            pattern = re.compile(re.escape(term), re.IGNORECASE)
        else:
            pattern = re.compile(r"\b" + re.escape(term) + r"\w*\b", re.IGNORECASE)
        highlighted = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", highlighted)
    return highlighted


def _friendly_search_error(exc: sqlite3.OperationalError) -> str:
    message = str(exc).strip()
    if "fts5:" in message.lower():
        return f"Invalid search syntax: {message}"
    return f"Search failed: {message}"


def _valid_ymd(year: int, month: int, day: int) -> bool:
    return 1990 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31


def _datetime_to_timestamp(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> float:
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).timestamp()


def _parse_date_from_filename(name: str) -> float | None:
    """Extract a document date embedded in the filename stem."""
    stem = Path(name).stem
    best: float | None = None

    for match in _FILENAME_YMD.finditer(stem):
        year, month, day = (int(match.group(i)) for i in range(1, 4))
        if not _valid_ymd(year, month, day):
            continue
        hour = int(match.group(4) or 0)
        minute = int(match.group(5) or 0)
        second = int(match.group(6) or 0)
        ts = _datetime_to_timestamp(year, month, day, hour, minute, second)
        best = ts if best is None else max(best, ts)

    for match in _FILENAME_YMD_COMPACT.finditer(stem):
        year, month, day = (int(match.group(i)) for i in range(1, 4))
        if not _valid_ymd(year, month, day):
            continue
        hour = int(match.group(4) or 0)
        minute = int(match.group(5) or 0)
        second = int(match.group(6) or 0)
        ts = _datetime_to_timestamp(year, month, day, hour, minute, second)
        best = ts if best is None else max(best, ts)

    for match in _FILENAME_MDY.finditer(stem):
        month, day, year = (int(match.group(i)) for i in range(1, 4))
        if not _valid_ymd(year, month, day):
            continue
        ts = _datetime_to_timestamp(year, month, day)
        best = ts if best is None else max(best, ts)

    return best


def _file_timestamp(archive_path: str) -> float | None:
    """Return the latest create/modify timestamp for an archived file."""
    try:
        path = Path(archive_path)
        if not path.is_file():
            return None
        stat = path.stat()
        stamps = [stat.st_mtime, stat.st_ctime]
        birth = getattr(stat, "st_birthtime", None)
        if birth is not None:
            stamps.append(birth)
        return float(max(stamps))
    except OSError:
        return None


def _iso_to_timestamp(value: str) -> float | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _document_sort_timestamp(
    doc: Document, file_ts_cache: dict[str, float | None]
) -> float:
    """Prefer filename dates, then archive file timestamps, then DB timestamps."""
    name_ts = _parse_date_from_filename(doc.original_name)
    if name_ts is not None:
        return name_ts

    if doc.archive_path not in file_ts_cache:
        file_ts_cache[doc.archive_path] = _file_timestamp(doc.archive_path)
    file_ts = file_ts_cache[doc.archive_path]
    if file_ts is not None:
        return file_ts

    for field in (doc.created_at, doc.processed_at):
        iso_ts = _iso_to_timestamp(field)
        if iso_ts is not None:
            return iso_ts
    return 0.0


def resolve_search_limit(limit: str | int | None) -> int | None:
    """Return a page size or None for unlimited results."""
    if limit is None:
        return SEARCH_LIMIT_DEFAULT
    if isinstance(limit, str):
        normalized = limit.strip().lower()
        if not normalized:
            return SEARCH_LIMIT_DEFAULT
        if normalized == "all":
            return None
        try:
            limit = int(normalized)
        except ValueError:
            return SEARCH_LIMIT_DEFAULT
    if limit in SEARCH_LIMIT_CHOICES:
        return limit
    return SEARCH_LIMIT_DEFAULT


def search_documents(
    db_path: Path, query: str, limit: int | None = SEARCH_LIMIT_DEFAULT
) -> list[SearchResult]:
    fts_q = _fts_query(query)
    if not fts_q:
        return []

    effective_limit = SEARCH_LIMIT_ALL_MAX if limit is None else limit
    sql = """
        SELECT d.*
        FROM documents_fts fts
        JOIN documents d ON d.id = fts.rowid
        WHERE documents_fts MATCH ?
    """
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, (fts_q,)).fetchall()
    except sqlite3.OperationalError as exc:
        raise SearchQueryError(_friendly_search_error(exc)) from exc

    file_ts_cache: dict[str, float | None] = {}
    documents = [Document(**dict(row)) for row in rows]
    documents.sort(
        key=lambda doc: (
            -_filename_match_score(doc.original_name, query),
            -_document_sort_timestamp(doc, file_ts_cache),
            doc.page_number,
            -doc.id,
        )
    )
    documents = documents[:effective_limit]

    results: list[SearchResult] = []
    for doc in documents:
        results.append(
            SearchResult(document=doc, snippet=_build_search_snippet(doc, query))
        )
    return results