"""SQLite persistence for LangGraph state using SqliteSaver.

Provides a thin wrapper around LangGraph's SqliteSaver to manage
checkpoint storage in a local SQLite database.  The database file lives
at ``data/checkpoints.sqlite3`` (relative to the project root) and the
``data/`` directory is created automatically if it does not already exist.

Typical usage::

    from app.services.persistence import get_checkpointer, get_thread_config

    checkpointer = get_checkpointer()
    config = get_thread_config("user-123-profile-1")
    # Pass both to your LangGraph graph.compile(checkpointer=checkpointer)
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Dict

from langgraph.checkpoint.sqlite import SqliteSaver

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_DATA_DIR: Path = _PROJECT_ROOT / "data"
_DB_PATH: Path = _DATA_DIR / "checkpoints.sqlite3"

# Module-level singleton so we don't open multiple connections needlessly.
_checkpointer: SqliteSaver | None = None


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_checkpointer() -> SqliteSaver:
    """Return a reusable :class:`SqliteSaver` backed by a local SQLite file.

    The database is stored at ``data/checkpoints.sqlite3``.  The ``data/``
    directory is created on first call if it does not exist.

    Returns:
        SqliteSaver: A LangGraph-compatible checkpointer instance.

    Raises:
        OSError: If the data directory cannot be created.
        sqlite3.Error: If the database connection fails.
    """
    global _checkpointer  # noqa: PLW0603

    if _checkpointer is not None:
        return _checkpointer

    # Ensure the data directory exists.
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Open (or create) the SQLite database.
    conn: sqlite3.Connection = sqlite3.connect(
        str(_DB_PATH),
        check_same_thread=False,  # LangGraph may access from different threads
    )

    _checkpointer = SqliteSaver(conn)

    # SqliteSaver.setup() creates the required tables if they are missing.
    _checkpointer.setup()

    return _checkpointer


def get_thread_config(thread_id: str) -> Dict[str, Any]:
    """Build a LangGraph-compatible thread configuration dictionary.

    Args:
        thread_id: Unique identifier for the conversation / workflow thread.
            Typically composed as ``"<user_id>-<profile_id>"``.

    Returns:
        dict: ``{"configurable": {"thread_id": thread_id}}``

    Raises:
        ValueError: If *thread_id* is empty or ``None``.
    """
    if not thread_id:
        raise ValueError("thread_id must be a non-empty string")

    return {"configurable": {"thread_id": thread_id}}
