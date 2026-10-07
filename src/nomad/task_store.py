"""SQLite-backed persistence for managed MCP task inputs and terminal states."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol, TypeVar

from fastmcp.server.dependencies import get_access_token

from . import metrics as nomad_metrics
from .task_protocol import TERMINAL_TASK_STATUSES, TaskStatus

MIN_TASK_CHARGE_BYTES = 1024
_SCHEMA_VERSION = 1
_OWNER_KEY_DOMAIN = b"nomad-task-owner-v1\0"
T = TypeVar("T")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def task_owner_key() -> bytes:
    """Hash the authenticated principal without persisting credentials or claims."""
    token = get_access_token()
    if token is None:
        principal: list[str | None] = [None, None, None, None]
    else:
        claims = token.claims or {}
        issuer = claims.get("iss")
        subject = token.subject or claims.get("sub")
        principal = [
            str(issuer) if issuer is not None else None,
            token.client_id,
            str(subject) if subject is not None else None,
            token.resource,
        ]
    encoded = json.dumps(
        principal,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(_OWNER_KEY_DOMAIN + encoded).digest()


class TaskCapacityError(RuntimeError):
    """The configured logical task-state budget cannot admit an operation."""


@dataclass(frozen=True, slots=True)
class StoredTask:
    task_id: str
    owner_key: bytes
    method: str
    input_json: bytes
    input_bytes: int
    status: TaskStatus
    created_at: str
    updated_at: str
    expires_at: float
    ttl_ms: int
    poll_interval_ms: int
    terminal_json: bytes | None
    terminal_bytes: int

    @property
    def charged_bytes(self) -> int:
        return self.input_bytes + max(MIN_TASK_CHARGE_BYTES, self.terminal_bytes)


class TaskStore(Protocol):
    ttl_ms: int
    max_bytes: int
    active_count: int
    retained_bytes: int

    async def create(
        self,
        *,
        task_id: str,
        owner_key: bytes,
        method: str,
        input_json: bytes,
        poll_interval_ms: int,
    ) -> tuple[StoredTask, tuple[str, ...]]: ...

    async def get(self, task_id: str, owner_key: bytes) -> StoredTask | None: ...

    async def get_any(self, task_id: str) -> StoredTask | None: ...

    async def list_tasks(self) -> tuple[StoredTask, ...]: ...

    async def list_working(self) -> tuple[StoredTask, ...]: ...

    async def set_terminal(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        updated_at: str,
        terminal_json: bytes,
    ) -> tuple[bool, tuple[str, ...]]: ...

    async def delete(self, task_id: str, *, reason: str) -> bool: ...

    async def prune_expired(self, *, now: float | None = None) -> tuple[str, ...]: ...

    async def close(self) -> None: ...


class SQLiteTaskStore:
    """Persist task state in one WAL-enabled SQLite database.

    SQLite work runs on a dedicated thread so multi-megabyte BLOB reads and
    writes never block the server event loop. The byte budget is logical: each
    row is charged for its input plus the larger of its terminal JSON and a
    1-KiB metadata/terminal reservation.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        ttl_seconds: float,
        max_bytes: int,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        if max_bytes < MIN_TASK_CHARGE_BYTES:
            raise ValueError(f"max_bytes must be at least {MIN_TASK_CHARGE_BYTES}")

        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self.ttl_ms = max(1, int(ttl_seconds * 1000))
        self.max_bytes = max_bytes
        self.active_count = 0
        self.retained_bytes = 0
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="nomad-task-store",
        )
        self._closed = False
        self._connection = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()
        nomad_metrics.register_task_store(self)

    def _initialize(self) -> None:
        connection = self._connection
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
        if self.path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("PRAGMA wal_autocheckpoint = 1000")

        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if schema_version not in {0, _SCHEMA_VERSION}:
            raise RuntimeError(
                f"Unsupported task store schema version {schema_version}; "
                f"expected {_SCHEMA_VERSION}"
            )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                owner_key BLOB NOT NULL,
                method TEXT NOT NULL,
                input_json BLOB NOT NULL,
                input_bytes INTEGER NOT NULL CHECK (input_bytes >= 0),
                status TEXT NOT NULL CHECK (
                    status IN (
                        'working', 'input_required', 'completed',
                        'failed', 'cancelled'
                    )
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at REAL NOT NULL,
                ttl_ms INTEGER NOT NULL,
                poll_interval_ms INTEGER NOT NULL,
                terminal_json BLOB,
                terminal_bytes INTEGER NOT NULL DEFAULT 0
                    CHECK (terminal_bytes >= 0)
            );
            CREATE INDEX IF NOT EXISTS tasks_expires_at
                ON tasks (expires_at);
            CREATE INDEX IF NOT EXISTS tasks_terminal_age
                ON tasks (status, updated_at);
            CREATE TABLE IF NOT EXISTS task_store_meta (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO task_store_meta (key, value)
                VALUES ('logical_bytes', 0);
            """
        )
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        connection.execute(
            """
            UPDATE task_store_meta
            SET value = (
                SELECT COALESCE(
                    SUM(input_bytes + MAX(?, terminal_bytes)),
                    0
                )
                FROM tasks
            )
            WHERE key = 'logical_bytes'
            """,
            (MIN_TASK_CHARGE_BYTES,),
        )
        self._refresh_stats_sync()

    async def _run(self, fn: Callable[..., T], /, *args: Any) -> T:
        if self._closed:
            raise RuntimeError("TaskStore is closed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, partial(fn, *args))

    def _refresh_stats_sync(self) -> None:
        self.active_count = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'working'"
            ).fetchone()[0]
        )
        self.retained_bytes = int(
            self._connection.execute(
                "SELECT value FROM task_store_meta WHERE key = 'logical_bytes'"
            ).fetchone()[0]
        )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> StoredTask:
        terminal_json = row["terminal_json"]
        return StoredTask(
            task_id=row["task_id"],
            owner_key=bytes(row["owner_key"]),
            method=row["method"],
            input_json=bytes(row["input_json"]),
            input_bytes=row["input_bytes"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            ttl_ms=row["ttl_ms"],
            poll_interval_ms=row["poll_interval_ms"],
            terminal_json=(bytes(terminal_json) if terminal_json is not None else None),
            terminal_bytes=row["terminal_bytes"],
        )

    def _logical_bytes_sync(self) -> int:
        return int(
            self._connection.execute(
                "SELECT value FROM task_store_meta WHERE key = 'logical_bytes'"
            ).fetchone()[0]
        )

    def _adjust_logical_bytes_sync(self, delta: int) -> None:
        self._connection.execute(
            """
            UPDATE task_store_meta
            SET value = value + ?
            WHERE key = 'logical_bytes'
            """,
            (delta,),
        )

    def _make_room_sync(
        self,
        additional_bytes: int,
        *,
        exclude_task_id: str | None = None,
    ) -> tuple[str, ...]:
        if additional_bytes <= 0:
            return ()
        retained = self._logical_bytes_sync()
        if retained + additional_bytes <= self.max_bytes:
            return ()

        query = """
            SELECT task_id, input_bytes + MAX(?, terminal_bytes) AS charge
            FROM tasks
            WHERE status IN ('completed', 'failed', 'cancelled')
        """
        params: list[Any] = [MIN_TASK_CHARGE_BYTES]
        if exclude_task_id is not None:
            query += " AND task_id != ?"
            params.append(exclude_task_id)
        query += " ORDER BY updated_at ASC"

        evicted: list[str] = []
        reclaimed = 0
        for row in self._connection.execute(query, params):
            evicted.append(row["task_id"])
            reclaimed += int(row["charge"])
            if retained - reclaimed + additional_bytes <= self.max_bytes:
                break
        if evicted:
            self._connection.executemany(
                "DELETE FROM tasks WHERE task_id = ?",
                ((task_id,) for task_id in evicted),
            )
            self._adjust_logical_bytes_sync(-reclaimed)
        return tuple(evicted)

    async def create(
        self,
        *,
        task_id: str,
        owner_key: bytes,
        method: str,
        input_json: bytes,
        poll_interval_ms: int,
    ) -> tuple[StoredTask, tuple[str, ...]]:
        try:
            task, evicted = await self._run(
                self._create_sync,
                task_id,
                owner_key,
                method,
                input_json,
                poll_interval_ms,
            )
        except TaskCapacityError:
            nomad_metrics.record_task_rejection("capacity")
            raise
        nomad_metrics.record_task_created()
        for _ in evicted:
            nomad_metrics.record_task_removal("capacity")
        return task, evicted

    def _create_sync(
        self,
        task_id: str,
        owner_key: bytes,
        method: str,
        input_json: bytes,
        poll_interval_ms: int,
    ) -> tuple[StoredTask, tuple[str, ...]]:
        created_at = now_iso()
        expires_at = time.time() + self.ttl_seconds
        charge = len(input_json) + MIN_TASK_CHARGE_BYTES
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            evicted = self._make_room_sync(charge)
            if self._logical_bytes_sync() + charge > self.max_bytes:
                raise TaskCapacityError("task state byte budget exhausted")
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, owner_key, method, input_json, input_bytes,
                    status, created_at, updated_at, expires_at, ttl_ms,
                    poll_interval_ms, terminal_json, terminal_bytes
                ) VALUES (?, ?, ?, ?, ?, 'working', ?, ?, ?, ?, ?, NULL, 0)
                """,
                (
                    task_id,
                    owner_key,
                    method,
                    input_json,
                    len(input_json),
                    created_at,
                    created_at,
                    expires_at,
                    self.ttl_ms,
                    poll_interval_ms,
                ),
            )
            self._adjust_logical_bytes_sync(charge)
        except Exception:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        self._refresh_stats_sync()
        row = connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        assert row is not None
        return self._row_to_task(row), evicted

    async def get(self, task_id: str, owner_key: bytes) -> StoredTask | None:
        return await self._run(self._get_sync, task_id, owner_key)

    def _get_sync(self, task_id: str, owner_key: bytes) -> StoredTask | None:
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE task_id = ? AND owner_key = ?",
            (task_id, owner_key),
        ).fetchone()
        return self._row_to_task(row) if row is not None else None

    async def get_any(self, task_id: str) -> StoredTask | None:
        return await self._run(self._get_any_sync, task_id)

    def _get_any_sync(self, task_id: str) -> StoredTask | None:
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return self._row_to_task(row) if row is not None else None

    async def list_tasks(self) -> tuple[StoredTask, ...]:
        return await self._run(self._list_tasks_sync)

    def _list_tasks_sync(self) -> tuple[StoredTask, ...]:
        return tuple(
            self._row_to_task(row)
            for row in self._connection.execute(
                "SELECT * FROM tasks ORDER BY created_at"
            )
        )

    async def list_working(self) -> tuple[StoredTask, ...]:
        return await self._run(self._list_working_sync)

    def _list_working_sync(self) -> tuple[StoredTask, ...]:
        return tuple(
            self._row_to_task(row)
            for row in self._connection.execute(
                "SELECT * FROM tasks WHERE status = 'working' ORDER BY created_at"
            )
        )

    async def set_terminal(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        updated_at: str,
        terminal_json: bytes,
    ) -> tuple[bool, tuple[str, ...]]:
        if status not in TERMINAL_TASK_STATUSES:
            raise ValueError(f"{status!r} is not a terminal task status")
        try:
            changed, evicted = await self._run(
                self._set_terminal_sync,
                task_id,
                status,
                updated_at,
                terminal_json,
            )
        except TaskCapacityError:
            nomad_metrics.record_task_rejection("result_too_large")
            raise
        for _ in evicted:
            nomad_metrics.record_task_removal("capacity")
        return changed, evicted

    def _set_terminal_sync(
        self,
        task_id: str,
        status: TaskStatus,
        updated_at: str,
        terminal_json: bytes,
    ) -> tuple[bool, tuple[str, ...]]:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT input_bytes, terminal_bytes, status FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None or row["status"] in TERMINAL_TASK_STATUSES:
                connection.execute("COMMIT")
                return False, ()

            previous_charge = int(row["input_bytes"]) + max(
                MIN_TASK_CHARGE_BYTES,
                int(row["terminal_bytes"]),
            )
            desired_charge = int(row["input_bytes"]) + max(
                MIN_TASK_CHARGE_BYTES,
                len(terminal_json),
            )
            additional = desired_charge - previous_charge
            evicted = self._make_room_sync(
                additional,
                exclude_task_id=task_id,
            )
            if self._logical_bytes_sync() + additional > self.max_bytes:
                raise TaskCapacityError("task terminal state exceeds byte budget")
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, updated_at = ?, terminal_json = ?,
                    terminal_bytes = ?
                WHERE task_id = ? AND status = 'working'
                """,
                (status, updated_at, terminal_json, len(terminal_json), task_id),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                connection.execute("ROLLBACK")
                return False, ()
            self._adjust_logical_bytes_sync(additional)
        except Exception:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        self._refresh_stats_sync()
        return True, evicted

    async def delete(self, task_id: str, *, reason: str) -> bool:
        deleted = await self._run(self._delete_sync, task_id)
        if deleted:
            nomad_metrics.record_task_removal(reason)
        return deleted

    def _delete_sync(self, task_id: str) -> bool:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """
                SELECT input_bytes + MAX(?, terminal_bytes) AS charge
                FROM tasks WHERE task_id = ?
                """,
                (MIN_TASK_CHARGE_BYTES, task_id),
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return False
            connection.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
            self._adjust_logical_bytes_sync(-int(row["charge"]))
        except Exception:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        self._refresh_stats_sync()
        return True

    async def prune_expired(self, *, now: float | None = None) -> tuple[str, ...]:
        task_ids = await self._run(
            self._prune_expired_sync,
            time.time() if now is None else now,
        )
        for _ in task_ids:
            nomad_metrics.record_task_removal("expired")
        return task_ids

    def _prune_expired_sync(self, now: float) -> tuple[str, ...]:
        connection = self._connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = tuple(
                connection.execute(
                    """
                    SELECT task_id,
                           input_bytes + MAX(?, terminal_bytes) AS charge
                    FROM tasks WHERE expires_at <= ?
                    """,
                    (MIN_TASK_CHARGE_BYTES, now),
                )
            )
            if not rows:
                connection.execute("COMMIT")
                return ()
            connection.executemany(
                "DELETE FROM tasks WHERE task_id = ?",
                ((row["task_id"],) for row in rows),
            )
            self._adjust_logical_bytes_sync(-sum(int(row["charge"]) for row in rows))
        except Exception:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        self._refresh_stats_sync()
        connection.execute("PRAGMA incremental_vacuum(128)")
        return tuple(row["task_id"] for row in rows)

    async def close(self) -> None:
        if self._closed:
            return
        await self._run(self._close_sync)
        self._closed = True
        self._executor.shutdown(wait=True)

    def _close_sync(self) -> None:
        if self.path != ":memory:":
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._connection.close()
