"""One in-memory DuckDB database shared by the threads that serve requests."""

from __future__ import annotations

import threading
from typing import Any

import duckdb


class SharedConnection:
    """A DuckDB connection that is safe to use from many threads at once.

    A DuckDB connection is not: two threads querying it together read each other's results (or none). Each thread
    here gets its own cursor on the same database, so views, tables and settings are shared while results and
    registered Arrow tables are the thread's own. Use it like a connection.
    """

    def __init__(self, con: duckdb.DuckDBPyConnection | None = None):
        self._base = con or duckdb.connect()
        self._local = threading.local()

    def cursor_for_thread(self) -> duckdb.DuckDBPyConnection:
        cur = getattr(self._local, "cur", None)
        if cur is None:
            cur = self._local.cur = self._base.cursor()
        return cur

    def __getattr__(self, name: str) -> Any:
        return getattr(self.cursor_for_thread(), name)


def shared(con: duckdb.DuckDBPyConnection | SharedConnection | None = None) -> SharedConnection:
    """``con`` made safe to share between threads (a new in-memory database if none is given)."""
    return con if isinstance(con, SharedConnection) else SharedConnection(con)
