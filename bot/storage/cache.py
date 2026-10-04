"""SQLite cache of finished answers.

One row per page and language: the `Summary` as JSON plus what is needed to decide whether it can
still be shown (when it was stored, a hash of the page text it was made from, a fingerprint of the
model and prompt). The cache is an optimisation and must never be the reason a request fails: an
unreadable database, a bad row or a full disk all behave like a miss, are logged without the URL,
and the request goes on to the normal pipeline.

Nothing about users is stored, and only answers about public pages (files sent into the chat are
never cached).
"""

import asyncio
import hashlib
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
from pydantic import ValidationError

from bot.pipeline.schema import Summary

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# Rows older than this are deleted. It also bounds how long a refresh button keeps working and
# how long an unchanged page can keep serving the same stored answer.
RETENTION_S = 7 * 24 * 3600
MAX_ROWS = 5000


@dataclass(frozen=True)
class CacheEntry:
    summary: Summary
    content_hash: str  # of the cleaned page text the summary was made from
    stored_at: float  # unix seconds
    url: str  # the link as it was sent, for the refresh button
    button_id: str


def button_id(url_key: str, language: str) -> str:
    """Short and unguessable-enough id for a Telegram callback (64 bytes at most, no URL)."""
    return hashlib.sha256(f"{url_key}\n{language}".encode()).hexdigest()[:16]


class SummaryCache:
    def __init__(
        self,
        path: str | Path,
        *,
        retention_s: float = RETENTION_S,
        max_rows: int = MAX_ROWS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = Path(path)
        self._retention_s = retention_s
        self._max_rows = max_rows
        self._clock = clock
        self._db: aiosqlite.Connection | None = None
        self._open_lock = asyncio.Lock()
        self._unavailable = False

    async def get(self, url_key: str, language: str, fingerprint: str) -> CacheEntry | None:
        """The stored answer, or None: no row, too old, made under another fingerprint, or
        unreadable. Whether it is still *fresh* is the caller's decision."""
        db = await self._connection()
        if db is None:
            return None
        try:
            cursor = await db.execute(
                "SELECT summary_json, content_hash, stored_at, url, button_id, fingerprint "
                "FROM summaries WHERE url_key = ? AND language = ?",
                (url_key, language),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return None
            summary_json, content_hash, stored_at, url, row_button, row_fingerprint = row
            if stored_at < self._clock() - self._retention_s:
                await self._delete(db, url_key, language)
                return None
            if row_fingerprint != fingerprint:
                return None
            try:
                summary = Summary.model_validate_json(summary_json)
            except ValidationError:
                logger.warning("Cache row did not validate, deleted")
                await self._delete(db, url_key, language)
                return None
            return CacheEntry(summary, content_hash, stored_at, url, row_button)
        except (sqlite3.Error, aiosqlite.Error):
            logger.warning("Cache read failed", exc_info=True)
            return None

    async def put(
        self,
        url_key: str,
        language: str,
        *,
        url: str,
        fingerprint: str,
        content_hash: str,
        summary: Summary,
    ) -> None:
        db = await self._connection()
        if db is None:
            return
        stored = summary.model_copy(update={"cached_minutes_ago": None})
        now = self._clock()
        try:
            await db.execute(
                "INSERT INTO summaries (url_key, language, button_id, url, fingerprint, "
                "content_hash, summary_json, stored_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(url_key, language) DO UPDATE SET button_id = excluded.button_id, "
                "url = excluded.url, fingerprint = excluded.fingerprint, "
                "content_hash = excluded.content_hash, summary_json = excluded.summary_json, "
                "stored_at = excluded.stored_at",
                (
                    url_key,
                    language,
                    button_id(url_key, language),
                    url,
                    fingerprint,
                    content_hash,
                    stored.model_dump_json(),
                    now,
                ),
            )
            await db.execute(
                "DELETE FROM summaries WHERE stored_at < ?", (now - self._retention_s,)
            )
            await db.execute(
                "DELETE FROM summaries WHERE rowid IN "
                "(SELECT rowid FROM summaries ORDER BY stored_at DESC LIMIT -1 OFFSET ?)",
                (self._max_rows,),
            )
            await db.commit()
        except (sqlite3.Error, aiosqlite.Error):
            logger.warning("Cache write failed", exc_info=True)

    async def target_for(self, button: str) -> tuple[str, str] | None:
        """(link, language) behind a refresh button, or None if the row is gone or too old."""
        db = await self._connection()
        if db is None:
            return None
        try:
            cursor = await db.execute(
                "SELECT url, language, stored_at FROM summaries WHERE button_id = ?", (button,)
            )
            row = await cursor.fetchone()
            await cursor.close()
        except (sqlite3.Error, aiosqlite.Error):
            logger.warning("Cache read failed", exc_info=True)
            return None
        if row is None or row[2] < self._clock() - self._retention_s:
            return None
        return row[0], row[1]

    async def aclose(self) -> None:
        async with self._open_lock:
            db, self._db = self._db, None
        if db is not None:
            try:
                await db.close()
            except (sqlite3.Error, aiosqlite.Error):
                logger.warning("Cache did not close cleanly", exc_info=True)

    async def _delete(self, db: aiosqlite.Connection, url_key: str, language: str) -> None:
        await db.execute(
            "DELETE FROM summaries WHERE url_key = ? AND language = ?", (url_key, language)
        )
        await db.commit()

    async def _connection(self) -> aiosqlite.Connection | None:
        """The open database, opened on first use. None if it cannot be opened (reported once)."""
        if self._db is not None:
            return self._db
        if self._unavailable:
            return None
        async with self._open_lock:
            if self._db is not None:
                return self._db
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                db = await aiosqlite.connect(self._path)
                try:
                    await self._prepare(db)
                except BaseException:
                    await db.close()
                    raise
            except (OSError, sqlite3.Error, aiosqlite.Error):
                logger.warning("Answer cache is unavailable, continuing without it", exc_info=True)
                self._unavailable = True
                return None
            self._db = db
            return db

    async def _prepare(self, db: aiosqlite.Connection) -> None:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.execute("PRAGMA synchronous = NORMAL")
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        await cursor.close()
        if row is not None and row[0] != SCHEMA_VERSION:
            # A cache has nothing worth migrating: start clean when the layout changes.
            await db.execute("DROP TABLE IF EXISTS summaries")
        await db.execute(
            "CREATE TABLE IF NOT EXISTS summaries ("
            "url_key TEXT NOT NULL, language TEXT NOT NULL, button_id TEXT NOT NULL UNIQUE, "
            "url TEXT NOT NULL, fingerprint TEXT NOT NULL, content_hash TEXT NOT NULL, "
            "summary_json TEXT NOT NULL, stored_at REAL NOT NULL, "
            "PRIMARY KEY (url_key, language))"
        )
        await db.execute("CREATE INDEX IF NOT EXISTS summaries_stored_at ON summaries (stored_at)")
        await db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        await db.commit()
