from __future__ import annotations

import logging
import sqlite3
import zipfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

INP_SEP = "\x04"
PAGE_SIZE = 8


@dataclass
class Book:
    file_id: str
    author: str
    title: str
    series: str
    serno: str
    genre: str
    archive: str
    filename: str
    size: int
    lang: str
    deleted: int

    def caption(self) -> str:
        parts = [self.title]
        if self.author:
            parts.append(self.author)
        if self.series:
            ser = self.series
            if self.serno:
                ser = f"{ser} #{self.serno}"
            parts.append(ser)
        return "\n".join(parts)

    def button_label(self, available: bool) -> str:
        mark = "📗" if available else "📦"
        author = self.author.split(",")[0] if self.author else ""
        text = f"{mark} {self.title}"
        if author:
            text += f" — {author}"
        return text[:64]


def format_authors(raw: str) -> str:
    authors = []
    for chunk in raw.split(":"):
        chunk = chunk.strip().strip(",")
        if not chunk:
            continue
        bits = [b.strip() for b in chunk.split(",") if b.strip()]
        if not bits:
            continue
        last = bits[0]
        rest = " ".join(bits[1:])
        authors.append(f"{last} {rest}".strip())
    return ", ".join(authors)


def archive_name_from_inp(inp_name: str) -> str:
    stem = Path(inp_name).stem
    return f"{stem}.zip"


class Catalog:
    def __init__(self, library_dir: Path, db_path: Path, index_path: Path | None = None):
        self.library_dir = library_dir
        self.db_path = db_path
        self.index_path = index_path or library_dir / "flibusta_fb2_local.inpx"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()
        self.rebuild_if_needed()

    def _ensure_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS books (
                file_id TEXT PRIMARY KEY,
                author TEXT,
                title TEXT,
                series TEXT,
                serno TEXT,
                genre TEXT,
                archive TEXT,
                filename TEXT,
                size INTEGER,
                lang TEXT,
                deleted INTEGER
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
                file_id UNINDEXED,
                author,
                title,
                series,
                tokenize = 'unicode61'
            );
            """
        )
        self.conn.commit()

    def rebuild_if_needed(self) -> None:
        if not self.index_path.exists():
            log.warning("Index file not found: %s", self.index_path)
            return
        mtime = str(self.index_path.stat().st_mtime)
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'inpx_mtime'"
        ).fetchone()
        count = self.conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        if row and row["value"] == mtime and count:
            log.info("Catalog is up to date (%s books)", count)
            return
        log.info("Building catalog from %s", self.index_path)
        self._rebuild()
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('inpx_mtime', ?)",
            (mtime,),
        )
        self.conn.commit()

    def _rebuild(self) -> None:
        self.conn.execute("DELETE FROM books")
        self.conn.execute("DELETE FROM books_fts")
        inserted = 0
        with zipfile.ZipFile(self.index_path) as zf:
            for name in zf.namelist():
                if not name.lower().endswith(".inp"):
                    continue
                archive = archive_name_from_inp(name)
                raw = zf.read(name)
                text = raw.decode("utf-8-sig", errors="replace")
                rows_books = []
                rows_fts = []
                for line in text.splitlines():
                    if not line.strip():
                        continue
                    fields = line.split(INP_SEP)
                    if len(fields) < 10:
                        continue
                    file_id = fields[5].strip()
                    ext = fields[9].strip() or "fb2"
                    del_raw = fields[8].strip() or "0"
                    if not file_id.isdigit() or del_raw not in {"0", "1"}:
                        continue
                    deleted = int(del_raw)
                    try:
                        size = int(fields[6] or "0")
                    except ValueError:
                        size = 0
                    author = format_authors(fields[0])
                    title = fields[2].strip()
                    series = fields[3].strip()
                    rows_books.append(
                        (
                            file_id,
                            author,
                            title,
                            series,
                            fields[4].strip(),
                            fields[1].strip(),
                            archive,
                            f"{file_id}.{ext}",
                            size,
                            fields[11].strip() if len(fields) > 11 else "",
                            deleted,
                        )
                    )
                    rows_fts.append((file_id, author, title, series))
                self.conn.executemany(
                    """
                    INSERT OR REPLACE INTO books(
                        file_id, author, title, series, serno, genre,
                        archive, filename, size, lang, deleted
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_books,
                )
                self.conn.executemany(
                    "INSERT INTO books_fts(file_id, author, title, series) VALUES (?, ?, ?, ?)",
                    rows_fts,
                )
                inserted += len(rows_books)
        self.conn.commit()
        log.info("Catalog built: %s books", inserted)

    def available_archives(self) -> set[str]:
        return {p.name for p in self.library_dir.glob("*.zip")}

    def archive_exists(self, archive: str) -> bool:
        return (self.library_dir / archive).is_file()

    def get(self, file_id: str) -> Book | None:
        row = self.conn.execute(
            "SELECT * FROM books WHERE file_id = ?", (file_id,)
        ).fetchone()
        return self._book(row) if row else None

    def search(self, query: str, limit: int = 50) -> list[Book]:
        query = query.strip()
        if not query:
            return []
        fts = _to_fts_query(query)
        try:
            rows = self.conn.execute(
                """
                SELECT books.*
                FROM books_fts
                JOIN books ON books.file_id = books_fts.file_id
                WHERE books_fts MATCH ? AND books.deleted = 0
                ORDER BY rank
                LIMIT ?
                """,
                (fts, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            like = f"%{query}%"
            rows = self.conn.execute(
                """
                SELECT * FROM books
                WHERE deleted = 0 AND (
                    title LIKE ? OR author LIKE ? OR series LIKE ?
                )
                LIMIT ?
                """,
                (like, like, like, limit),
            ).fetchall()
        return [self._book(r) for r in rows]

    def extract(self, book: Book) -> bytes:
        archive_path = self.library_dir / book.archive
        if not archive_path.is_file():
            raise FileNotFoundError(book.archive)
        with zipfile.ZipFile(archive_path) as zf:
            names = {Path(n).name: n for n in zf.namelist()}
            inner = names.get(book.filename)
            if inner is None:
                raise FileNotFoundError(book.filename)
            return zf.read(inner)

    @staticmethod
    def _book(row: sqlite3.Row) -> Book:
        return Book(
            file_id=row["file_id"],
            author=row["author"] or "",
            title=row["title"] or "",
            series=row["series"] or "",
            serno=row["serno"] or "",
            genre=row["genre"] or "",
            archive=row["archive"] or "",
            filename=row["filename"] or "",
            size=row["size"] or 0,
            lang=row["lang"] or "",
            deleted=row["deleted"] or 0,
        )


def _to_fts_query(raw: str) -> str:
    tokens = [t for t in raw.replace('"', " ").split() if t]
    if not tokens:
        return raw
    parts = []
    for token in tokens:
        cleaned = "".join(ch for ch in token if ch.isalnum() or ch in "-_")
        if not cleaned:
            continue
        parts.append(f"{cleaned}*")
    return " AND ".join(parts) if parts else raw
