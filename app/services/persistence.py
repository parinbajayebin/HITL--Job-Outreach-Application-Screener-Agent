"""app/services/persistence.py
SQLite checkpoint persistence for HITL workflow states.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_DATA_DIR: Path = _PROJECT_ROOT / "data"
_DB_PATH: Path = _DATA_DIR / "checkpoints.sqlite3"


class SqliteSaver:
    """Robust local SQLite state persistence."""

    def __init__(self, db_path: Path = _DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    thread_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    async def save(self, thread_id: str, state: Dict[str, Any]) -> None:
        try:
            serialized = json.dumps(state, default=str)
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO checkpoints (thread_id, state, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        state=excluded.state,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (thread_id, serialized),
                )
                conn.commit()
        except Exception as e:
            logger.error("Failed to save checkpoint for %s: %s", thread_id, e)

    async def load(self, thread_id: str) -> Optional[Dict[str, Any]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cur = conn.execute(
                    "SELECT state FROM checkpoints WHERE thread_id = ?",
                    (thread_id,),
                )
                row = cur.fetchone()
                if row:
                    return json.loads(row[0])
                return None
        except Exception as e:
            logger.error("Failed to load checkpoint for %s: %s", thread_id, e)
            return None


_instance: Optional[SqliteSaver] = None


def get_checkpointer() -> SqliteSaver:
    global _instance
    if _instance is None:
        _instance = SqliteSaver()
    return _instance


__all__ = ["SqliteSaver", "get_checkpointer"]
