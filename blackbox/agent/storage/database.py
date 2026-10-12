"""Persistence: the learned model survives the process that produced it.

The schema is deliberately portable SQL so the same repository code runs on
SQLite (the zero-setup default used for local experiments and tests) and on
PostgreSQL (the deployment target in ``docker-compose.yml``).  Model objects are
stored as JSON documents keyed by their content-addressed ids, with relational
tables for the things that are queried: states, transitions, workflows,
constraints, hypotheses, evidence and experiments.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS targets (
        target_id TEXT PRIMARY KEY,
        base_url TEXT NOT NULL,
        allowed_origins TEXT NOT NULL,
        mode TEXT NOT NULL,
        max_steps INTEGER,
        max_duration_seconds REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS models (
        target_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        created_at REAL NOT NULL,
        site_url TEXT,
        browser_version TEXT,
        prompt_version TEXT,
        parent_version INTEGER,
        notes TEXT,
        payload TEXT,
        PRIMARY KEY (target_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS states (
        state_id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        model_version INTEGER NOT NULL,
        url TEXT,
        url_key TEXT,
        title TEXT,
        page_id TEXT,
        page_identity TEXT,
        status TEXT,
        confidence REAL,
        visit_count INTEGER,
        semantic_summary TEXT,
        screenshot_ref TEXT,
        fingerprint_key TEXT,
        first_seen TEXT,
        last_seen TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS transitions (
        transition_id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        model_version INTEGER NOT NULL,
        source_state TEXT NOT NULL,
        target_state TEXT NOT NULL,
        action_signature TEXT,
        action_type TEXT,
        risk TEXT,
        status TEXT,
        confidence REAL,
        execution_count INTEGER,
        success_count INTEGER,
        failure_count INTEGER,
        updated_at TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflows (
        workflow_id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        model_version INTEGER NOT NULL,
        name TEXT,
        goal TEXT,
        expected_end_state TEXT,
        confidence REAL,
        verification_count INTEGER,
        updated_at TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS constraints (
        constraint_id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        model_version INTEGER NOT NULL,
        kind TEXT,
        scope TEXT,
        subject TEXT,
        expression TEXT,
        message TEXT,
        status TEXT,
        confidence REAL,
        updated_at TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hypotheses (
        hypothesis_id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        model_version INTEGER NOT NULL,
        statement TEXT,
        kind TEXT,
        status TEXT,
        confidence REAL,
        updated_at TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        evidence_id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        model_version INTEGER NOT NULL,
        kind TEXT,
        experiment_id TEXT,
        transition_id TEXT,
        source_state TEXT,
        target_state TEXT,
        message TEXT,
        created_at TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS experiments (
        experiment_id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        kind TEXT,
        seed INTEGER,
        browser_version TEXT,
        model_version INTEGER,
        prompt_version TEXT,
        status TEXT,
        started_at TEXT,
        finished_at TEXT,
        metrics TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS observations (
        observation_id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        state_id TEXT,
        url TEXT,
        title TEXT,
        element_count INTEGER,
        captured_ms REAL,
        screenshot_ref TEXT,
        created_at REAL NOT NULL,
        payload TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pages (
        page_id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        name TEXT,
        url_pattern TEXT,
        section TEXT,
        parent_page_id TEXT,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id TEXT NOT NULL,
        session_id TEXT,
        kind TEXT,
        created_at REAL NOT NULL,
        payload TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        kind TEXT,
        status TEXT,
        started_at REAL,
        finished_at REAL,
        summary TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_states_target ON states (target_id, model_version)",
    "CREATE INDEX IF NOT EXISTS idx_transitions_source ON transitions (source_state)",
    "CREATE INDEX IF NOT EXISTS idx_transitions_target ON transitions (target_state)",
    "CREATE INDEX IF NOT EXISTS idx_evidence_transition ON evidence (transition_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_target ON events (target_id, created_at)",
)


class Database:
    """A tiny portable SQL layer over SQLite or PostgreSQL."""

    def __init__(self, dsn: str = "sqlite:///artifacts/blackbox.db", *, echo: bool = False) -> None:
        self.dsn = dsn
        self.echo = echo
        self.backend = "sqlite" if dsn.startswith("sqlite") else "postgres"
        self._lock = threading.RLock()
        self._sqlite: sqlite3.Connection | None = None
        self._pg: Any | None = None

    # -- connections -------------------------------------------------------
    def connect(self) -> None:
        if self.backend == "sqlite":
            path = self.dsn.split("sqlite:///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._sqlite = sqlite3.connect(path or ":memory:", check_same_thread=False)
            self._sqlite.row_factory = sqlite3.Row
            self._sqlite.execute("PRAGMA journal_mode=WAL")
            self._sqlite.execute("PRAGMA foreign_keys=ON")
        else:
            try:
                import psycopg  # type: ignore

                self._pg = psycopg.connect(self.dsn)
            except Exception as exc:  # noqa: BLE001 - report clearly, do not hide
                raise RuntimeError(
                    f"PostgreSQL backend requested but psycopg is unavailable ({exc}). "
                    "Install psycopg or use sqlite:///path for local runs."
                ) from exc
        self.init_schema()

    def init_schema(self) -> None:
        with self.cursor() as cur:
            for statement in SCHEMA:
                cur.execute(self._adapt(statement))

    def close(self) -> None:
        if self._sqlite is not None:
            self._sqlite.close()
            self._sqlite = None
        if self._pg is not None:
            self._pg.close()
            self._pg = None

    def _adapt(self, sql: str) -> str:
        if self.backend == "postgres":
            return sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        return sql

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        with self._lock:
            if self.backend == "sqlite":
                if self._sqlite is None:
                    self.connect()
                assert self._sqlite is not None
                cur = self._sqlite.cursor()
                try:
                    yield cur
                    self._sqlite.commit()
                except Exception:
                    self._sqlite.rollback()
                    raise
                finally:
                    cur.close()
            else:
                if self._pg is None:
                    self.connect()
                assert self._pg is not None
                cur = self._pg.cursor()
                try:
                    yield cur
                    self._pg.commit()
                except Exception:
                    self._pg.rollback()
                    raise
                finally:
                    cur.close()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.cursor() as cur:
            cur.execute(self._adapt(sql), params)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute(self._adapt(sql), params)
            rows = cur.fetchall()
            if not rows:
                return []
            if isinstance(rows[0], sqlite3.Row):
                return [dict(row) for row in rows]
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in rows]

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def upsert(self, table: str, keys: dict[str, Any], values: dict[str, Any]) -> None:
        """Portable upsert: delete-then-insert keeps SQL identical across engines."""
        columns = list({**keys, **values}.keys())
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self.cursor() as cur:
            cur.execute(
                self._adapt(f"UPDATE {table} SET {assignments} WHERE " + " AND ".join(f"{k} = ?" for k in keys)),
                (*values.values(), *keys.values()),
            )
            if cur.rowcount == 0:
                cur.execute(
                    self._adapt(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"),
                    tuple({**keys, **values}[name] for name in columns),
                )

    def now(self) -> float:
        return time.time()


def dumps(payload: Any) -> str:
    return json.dumps(payload, default=str, ensure_ascii=False)


def loads(payload: str | None) -> Any:
    if not payload:
        return None
    try:
        return json.loads(payload)
    except ValueError:
        return None
