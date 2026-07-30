from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import WATCHER_DATABASE_FILE


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True, frozen=True)
class Watcher:
    id: int
    owner_user_id: int
    title: str
    destination: str
    enabled: bool
    created_at: str


@dataclass(slots=True, frozen=True)
class WatcherSource:
    id: int
    watcher_id: int
    platform: str
    profile_url: str
    interval_seconds: int
    next_check_at: float
    last_checked_at: str | None
    last_error: str | None
    error_count: int
    initial_scan_done: bool


class _ConnectionContext:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")

    def __enter__(self) -> sqlite3.Connection:
        return self.connection

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        try:
            if exc_type is not None:
                self.connection.rollback()
            else:
                self.connection.commit()
        finally:
            self.connection.close()


class WatcherDatabase:
    def __init__(self, path: Path = WATCHER_DATABASE_FILE) -> None:
        self.path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize)

    def _connect(self) -> _ConnectionContext:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return _ConnectionContext(self.path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS watchers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS watcher_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watcher_id INTEGER NOT NULL REFERENCES watchers(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    profile_url TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    next_check_at REAL NOT NULL,
                    last_checked_at TEXT,
                    last_error TEXT,
                    error_count INTEGER NOT NULL DEFAULT 0,
                    initial_scan_done INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(watcher_id, platform, profile_url)
                );
                CREATE TABLE IF NOT EXISTS watcher_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    watcher_id INTEGER NOT NULL REFERENCES watchers(id) ON DELETE CASCADE,
                    source_id INTEGER NOT NULL REFERENCES watcher_sources(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    post_key TEXT NOT NULL,
                    post_url TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    sent_at TEXT,
                    status TEXT NOT NULL DEFAULT 'discovered',
                    UNIQUE(watcher_id, post_key)
                );
                CREATE INDEX IF NOT EXISTS idx_watcher_sources_due
                    ON watcher_sources(next_check_at, watcher_id);
                CREATE INDEX IF NOT EXISTS idx_watcher_posts_watcher
                    ON watcher_posts(watcher_id, discovered_at DESC);
                """
            )
            try:
                connection.execute(
                    "ALTER TABLE watcher_sources ADD COLUMN initial_scan_done INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass

    async def create(
        self,
        owner_user_id: int,
        title: str,
        destination: str,
        sources: list[tuple[str, str, int]],
    ) -> Watcher:
        return await asyncio.to_thread(
            self._create, owner_user_id, title, destination, sources
        )

    def _create(
        self,
        owner_user_id: int,
        title: str,
        destination: str,
        sources: list[tuple[str, str, int]],
    ) -> Watcher:
        now = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO watchers(owner_user_id,title,destination,created_at,updated_at) "
                "VALUES(?,?,?,?,?)",
                (owner_user_id, title, destination, now, now),
            )
            watcher_id = int(cursor.lastrowid)
            for platform, profile_url, interval_seconds in sources:
                connection.execute(
                    "INSERT INTO watcher_sources(watcher_id,platform,profile_url,interval_seconds,next_check_at) "
                    "VALUES(?,?,?,?,?)",
                    (watcher_id, platform, profile_url, interval_seconds, 0.0),
                )
            row = connection.execute(
                "SELECT * FROM watchers WHERE id=?", (watcher_id,)
            ).fetchone()
        return self._watcher(row)

    async def list_for_user(self, owner_user_id: int) -> list[Watcher]:
        return await asyncio.to_thread(self._list_for_user, owner_user_id)

    def _list_for_user(self, owner_user_id: int) -> list[Watcher]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM watchers WHERE owner_user_id=? ORDER BY id",
                (owner_user_id,),
            ).fetchall()
        return [self._watcher(row) for row in rows]

    async def count_for_user(self, owner_user_id: int) -> int:
        return await asyncio.to_thread(self._count_for_user, owner_user_id)

    def _count_for_user(self, owner_user_id: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM watchers WHERE owner_user_id=?",
                (owner_user_id,),
            ).fetchone()
        return int(row["count"])

    async def sources_for_watchers(self, watcher_ids: list[int]) -> list[WatcherSource]:
        if not watcher_ids:
            return []
        return await asyncio.to_thread(self._sources_for_watchers, watcher_ids)

    def _sources_for_watchers(self, watcher_ids: list[int]) -> list[WatcherSource]:
        placeholders = ",".join("?" for _ in watcher_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT ws.* FROM watcher_sources ws "
                f"JOIN watchers w ON w.id=ws.watcher_id "
                f"WHERE w.enabled=1 AND w.id IN ({placeholders}) "
                "ORDER BY ws.next_check_at, ws.id",
                watcher_ids,
            ).fetchall()
        return [self._source(row) for row in rows]

    async def due_sources(self, now: float) -> list[WatcherSource]:
        return await asyncio.to_thread(self._due_sources, now)

    def _due_sources(self, now: float) -> list[WatcherSource]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT ws.* FROM watcher_sources ws JOIN watchers w ON w.id=ws.watcher_id "
                "WHERE w.enabled=1 AND ws.next_check_at<=? ORDER BY ws.next_check_at, ws.id",
                (now,),
            ).fetchall()
        return [self._source(row) for row in rows]

    async def watcher(self, watcher_id: int) -> Watcher | None:
        return await asyncio.to_thread(self._watcher_by_id, watcher_id)

    def _watcher_by_id(self, watcher_id: int) -> Watcher | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM watchers WHERE id=?", (watcher_id,)
            ).fetchone()
        return self._watcher(row) if row else None

    async def set_enabled(self, watcher_id: int, enabled: bool) -> None:
        await asyncio.to_thread(self._set_enabled, watcher_id, enabled)

    def _set_enabled(self, watcher_id: int, enabled: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE watchers SET enabled=?, updated_at=? WHERE id=?",
                (int(enabled), utc_now(), watcher_id),
            )

    async def delete(self, watcher_id: int) -> None:
        await asyncio.to_thread(self._delete, watcher_id)

    def _delete(self, watcher_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM watchers WHERE id=?", (watcher_id,))

    async def mark_checked(
        self,
        source_id: int,
        next_check_at: float,
        error: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._mark_checked, source_id, next_check_at, error
        )

    async def mark_initial_scan_done(self, source_id: int) -> None:
        await asyncio.to_thread(self._mark_initial_scan_done, source_id)

    def _mark_initial_scan_done(self, source_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE watcher_sources SET initial_scan_done=1 WHERE id=?",
                (source_id,),
            )

    def _mark_checked(
        self,
        source_id: int,
        next_check_at: float,
        error: str | None,
    ) -> None:
        with self._connect() as connection:
            if error:
                connection.execute(
                    "UPDATE watcher_sources SET next_check_at=?, last_checked_at=?, "
                    "last_error=?, error_count=error_count+1 WHERE id=?",
                    (next_check_at, utc_now(), error[:500], source_id),
                )
            else:
                connection.execute(
                    "UPDATE watcher_sources SET next_check_at=?, last_checked_at=?, "
                    "last_error=NULL, error_count=0 WHERE id=?",
                    (next_check_at, utc_now(), source_id),
                )

    async def add_post(
        self,
        watcher_id: int,
        source_id: int,
        platform: str,
        post_url: str,
        post_key: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._add_post,
            watcher_id,
            source_id,
            platform,
            post_url,
            post_key,
        )

    async def has_post(self, watcher_id: int, post_key: str) -> bool:
        return await asyncio.to_thread(self._has_post, watcher_id, post_key)

    def _has_post(self, watcher_id: int, post_key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM watcher_posts WHERE watcher_id=? AND post_key=? LIMIT 1",
                (watcher_id, post_key),
            ).fetchone()
        return row is not None

    def _add_post(
        self,
        watcher_id: int,
        source_id: int,
        platform: str,
        post_url: str,
        post_key: str | None,
    ) -> bool:
        key = post_key or hashlib.sha256(post_url.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO watcher_posts "
                "(watcher_id,source_id,platform,post_key,post_url,discovered_at) "
                "VALUES(?,?,?,?,?,?)",
                (watcher_id, source_id, platform, key, post_url, utc_now()),
            )
        return cursor.rowcount == 1

    async def mark_post_sent(self, watcher_id: int, post_key: str, status: str) -> None:
        await asyncio.to_thread(self._mark_post_sent, watcher_id, post_key, status)

    def _mark_post_sent(self, watcher_id: int, post_key: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE watcher_posts SET sent_at=?, status=? WHERE watcher_id=? AND post_key=?",
                (utc_now(), status, watcher_id, post_key),
            )

    @staticmethod
    def _watcher(row: sqlite3.Row | None) -> Watcher:
        if row is None:
            raise LookupError("Watcher no encontrado")
        return Watcher(
            id=int(row["id"]),
            owner_user_id=int(row["owner_user_id"]),
            title=str(row["title"]),
            destination=str(row["destination"]),
            enabled=bool(row["enabled"]),
            created_at=str(row["created_at"]),
        )

    @staticmethod
    def _source(row: sqlite3.Row) -> WatcherSource:
        return WatcherSource(
            id=int(row["id"]),
            watcher_id=int(row["watcher_id"]),
            platform=str(row["platform"]),
            profile_url=str(row["profile_url"]),
            interval_seconds=int(row["interval_seconds"]),
            next_check_at=float(row["next_check_at"]),
            last_checked_at=row["last_checked_at"],
            last_error=row["last_error"],
            error_count=int(row["error_count"]),
            initial_scan_done=bool(row["initial_scan_done"]),
        )


WATCHER_DATABASE = WatcherDatabase()
