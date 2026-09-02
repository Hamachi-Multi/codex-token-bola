"""Shared read-only access to analytics run metadata."""

from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any


def read_run_metadata(db_path: str | pathlib.Path) -> dict[str, Any]:
    target = pathlib.Path(db_path).expanduser()
    if not target.is_file():
        return {}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        rows = connection.execute("select key, value from run_metadata").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        if connection is not None:
            connection.close()

    metadata: dict[str, Any] = {}
    for key, value in rows:
        try:
            metadata[str(key)] = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            metadata[str(key)] = value
    return metadata
