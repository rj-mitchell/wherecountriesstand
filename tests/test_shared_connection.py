import threading

from wcs.store.shared import shared


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

