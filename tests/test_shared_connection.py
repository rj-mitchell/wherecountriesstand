import threading

from wts_core.adapters.duckdb import shared


def test_threads_get_their_own_results():
    con = shared()
    con.execute("CREATE TABLE t AS SELECT range AS i FROM range(200000)")
    errors: list[str] = []

    def work(n: int) -> None:
        try:
            for _ in range(20):
                got = con.execute("SELECT sum(i) + ? FROM t", [n]).fetchone()[0]
                assert got == 19999900000 + n
        except Exception as exc:  # collected so the test can report it
            errors.append(repr(exc))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_views_and_settings_are_shared_registrations_are_not():
    import pyarrow as pa

    con = shared()
    con.execute("CREATE VIEW v AS SELECT 1 AS x")
    con.register("mine", pa.table({"x": [1]}))
    seen: dict[str, object] = {}

    def other() -> None:
        seen["view"] = con.execute("SELECT x FROM v").fetchone()[0]
        try:
            con.execute("SELECT * FROM mine").fetchall()
            seen["registered"] = True
        except Exception:
            seen["registered"] = False

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert seen == {"view": 1, "registered": False}
    assert shared(con) is con


def test_a_row_read_singly_does_not_block_the_next_registration_or_relation():
    """DuckDB 2 refuses to register a table or run a relation while the cursor's last result has rows unread, which
    is how every fetchone() that finds its row leaves it. A request thread keeps its cursor, so one lookup would
    otherwise break the next search on that thread."""
    import pyarrow as pa

    con = shared()
    con.execute("CREATE TABLE t AS SELECT range AS i FROM range(5000)")
    assert con.execute("SELECT i FROM t ORDER BY i").fetchone() == (0,)
    con.register("mine", pa.table({"x": [7]}))
    assert con.execute("SELECT x FROM mine").fetchone() == (7,)
    assert con.sql("SELECT count(*) FROM t").fetchall() == [(5000,)]
    assert con.execute("SELECT i FROM t ORDER BY i").fetchone() == (0,)
    assert con.table("t").limit(1).fetchall() == [(0,)]
