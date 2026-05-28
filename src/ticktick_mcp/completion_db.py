"""
SQLite-backed store for tracking which TickTick completions have been
processed by domain agents.

Schema:
    completion_tracking(
        task_id       TEXT PRIMARY KEY,
        project_id    TEXT NOT NULL,
        title         TEXT,
        completed_time TEXT,
        processed_at  TEXT NOT NULL,   -- ISO timestamp when an agent processed it
        notes         TEXT             -- optional agent notes
    )
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import dotenv_dir_path

_DB_PATH: Optional[Path] = None


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = dotenv_dir_path / "completion_tracking.db"
    return _DB_PATH


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_get_db_path()))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the tracking table if it does not exist."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS completion_tracking (
                task_id        TEXT PRIMARY KEY,
                project_id     TEXT NOT NULL,
                title          TEXT,
                completed_time TEXT,
                processed_at   TEXT NOT NULL,
                notes          TEXT
            )
        """)
        conn.commit()
    logging.debug("completion_tracking table ready at %s", _get_db_path())


def is_processed(task_id: str) -> bool:
    """Return True if this task_id has already been recorded."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM completion_tracking WHERE task_id = ?", (task_id,)
        ).fetchone()
    return row is not None


def mark_processed(
    task_id: str,
    project_id: str,
    title: Optional[str],
    completed_time: Optional[str],
    notes: Optional[str] = None,
) -> None:
    """
    Insert a row marking task_id as processed.
    No-op (with a warning) if already present.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO completion_tracking
                    (task_id, project_id, title, completed_time, processed_at, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (task_id, project_id, title, completed_time, now, notes),
            )
            conn.commit()
        logging.debug("Marked task %s as processed.", task_id)
    except sqlite3.IntegrityError:
        logging.warning("Task %s already in completion_tracking; skipping.", task_id)


def get_processed_ids_for_project(project_id: str) -> set:
    """Return the set of task_ids already processed for a given project."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT task_id FROM completion_tracking WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    return {row["task_id"] for row in rows}
