"""SQLite database access."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Any
from src.config import DB_PATH

_CONN: sqlite3.Connection | None = None

def get_conn(db_path: Path = DB_PATH) -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        _CONN = sqlite3.connect(str(db_path), check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.execute("PRAGMA journal_mode=WAL")
        _CONN.execute("PRAGMA cache_size=-64000")
        _CONN.execute("PRAGMA busy_timeout=5000")
    return _CONN

def execute(sql: str, params: tuple = (), conn: sqlite3.Connection | None = None) -> sqlite3.Cursor:
    c = conn or get_conn()
    return c.execute(sql, params)

def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    rows = get_conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]

def fetchone(sql: str, params: tuple = ()) -> dict | None:
    r = get_conn().execute(sql, params).fetchone()
    return dict(r) if r else None

def table_row_count(table: str) -> int:
    r = get_conn().execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()
    return r[0] if r else 0

def query_df(sql: str, params: tuple = ()):
    """Run a SELECT and return the rows as a list of dicts (pandas-free)."""
    return fetchall(sql, params)

def table_exists(table: str) -> bool:
    r = get_conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return r is not None

def max_date(table: str = "sales", col: str = "date") -> str | None:
    r = get_conn().execute(f"SELECT MAX({col}) AS d FROM [{table}]").fetchone()
    return r["d"] if r and r["d"] else None

def min_date(table: str = "sales", col: str = "date") -> str | None:
    r = get_conn().execute(f"SELECT MIN({col}) AS d FROM [{table}]").fetchone()
    return r["d"] if r and r["d"] else None

def scalar(sql: str, params: tuple = (), default: Any = 0) -> Any:
    r = get_conn().execute(sql, params).fetchone()
    return r[0] if r and r[0] is not None else default
