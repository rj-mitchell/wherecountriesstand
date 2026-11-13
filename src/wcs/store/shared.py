"""One in-memory DuckDB database shared by the threads that serve requests."""

from __future__ import annotations

import threading
from typing import Any

import duckdb

# From version 2, DuckDB refuses to register a table, or to run a relation, on a cursor whose last result was not read
# to its end ("connection has an unfinished result from execute()"): a fetchone() that got its row leaves it so, and
# a thread's cursor here lives as long as the thread. These calls run that result to its end first.
_FINISH_FIRST = frozenset({"register", "create_function", "sql", "query", "from_query", "table", "view", "values",
                           "table_function", "from_arrow", "from_df", "from_parquet", "read_parquet", "from_csv_auto",
                           "read_csv", "read_json"})


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
        cur = self.cursor_for_thread()
        attr = getattr(cur, name)
        if name not in _FINISH_FIRST:
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            finish = getattr(cur, "complete", None)  # DuckDB 2; version 1 dropped the old result unasked
            if finish is not None:
                finish()
            return attr(*args, **kwargs)

        return call


def shared(con: duckdb.DuckDBPyConnection | SharedConnection | None = None) -> SharedConnection:
    """``con`` made safe to share between threads (a new in-memory database if none is given)."""
    return con if isinstance(con, SharedConnection) else SharedConnection(con)
