"""The web service, with the fake model client from test_pipeline (no network)."""

import json

import pytest
from fastapi.testclient import TestClient
from test_pipeline import TODAY, FakeClient, indexed  # noqa: F401  (the fixture)

from wcs.api.app import create_app
from wcs.api.usage import StateDB, cost_usd, visitor_key
from wcs.embed.base import HashingEmbedder
from wcs.engine import Engine
from wcs.rag.pipeline import Pipeline

QUESTION = {"question": "What has been said about climate finance since 2020?"}


def events(response) -> list[dict]:
    out = []
    for chunk in response.text.split("\n\n"):
        data = [ln[6:] for ln in chunk.splitlines() if ln.startswith("data: ")]
        if data:
            out.append(json.loads(data[0]))
    return out


def make(settings, client=None, **usage):
    settings.raw["usage"] = {**settings.raw.get("usage", {}), **usage}
    eng = Engine(settings, embedder=HashingEmbedder(64), today=TODAY)
    pipe = Pipeline(settings, eng, client=client, today=TODAY)
    state = StateDB(settings.data_dir / "state.db")
    return TestClient(create_app(settings, pipeline=pipe, state=state)), state


def test_meta_and_lists(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    assert api.get("/api/health").json() == {"ok": True}
    meta = api.get("/api/meta").json()
    assert meta["briefings"] is True and meta["coverage"]["sc"]["speeches"] == 2
    countries = api.get("/api/countries").json()
    assert any(c["code"] == "KEN" and c["name"] == "Kenya" for c in countries)
    assert any(g["key"] == "p5" for g in api.get("/api/groups").json())


def test_search_costs_nothing(indexed):  # noqa: F811
    client = FakeClient()
    api, _ = make(indexed, client)
    out = api.post("/api/search", json=QUESTION).json()
    assert out["plan"]["parser"] == "offline" and len(out["sources"]) == 2
    assert client.calls == []


def test_search_writes_sources_that_carry_dates(indexed):  # noqa: F811
    """A General Assembly vote's source has a date object; the live site failed on every question that found one."""
    from datetime import date

    api, _ = make(indexed, FakeClient())
    real = Pipeline.find

    class Dated:
        def __init__(self, s):
            self.s = s

        def card(self):
            return {**self.s.card(), "voted_on": date(2024, 12, 17)}

    def find(self, plan, req):
        result, votes, sources = real(self, plan, req)
        return result, votes, [Dated(s) for s in sources]

    Pipeline.find = find
    try:
        r = api.post("/api/search", json=QUESTION)
    finally:
        Pipeline.find = real
    assert r.status_code == 200 and r.json()["sources"][0]["voted_on"] == "2024-12-17"


def test_ask_streams_records_spend_and_caches(indexed):  # noqa: F811
    client = FakeClient()
    api, state = make(indexed, client)
    evs = events(api.post("/api/ask", json=QUESTION))
    assert [e["type"] for e in evs][:2] == ["plan", "sources"] and evs[-1]["type"] == "final"
    assert any(e["type"] == "text" for e in evs) and evs[-1]["answer"]["citations_total"] == 1
    with state.connect() as con:
        kind, cost = con.execute("SELECT kind, cost_usd FROM usage").fetchone()
    assert kind == "answer" and cost == pytest.approx(cost_usd({"input_tokens": 1000, "output_tokens": 200},
                                                               indexed.usage, "claude-sonnet-5-5"))
    again = events(api.post("/api/ask", json=QUESTION))
    assert again[-1]["cached"] is True and len(client.calls) == 1   # answered from the cache


def test_caps_fall_back_to_sources(indexed):  # noqa: F811
    client = FakeClient()
    api, _ = make(indexed, client, daily_spend_cap_usd=0.0)
    evs = events(api.post("/api/ask", json=QUESTION))
    assert evs[-1]["answer"] is None and "budget" in evs[-1]["note"]
    assert len(evs[1]["sources"]) == 2 and client.calls == []


def test_rate_limit(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient(), requests_per_minute=2)
    assert [api.post("/api/search", json=QUESTION).status_code for _ in range(3)] == [200, 200, 429]


def test_without_an_api_key(indexed, monkeypatch):  # noqa: F811
    for name in ("WCS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    api, _ = make(indexed, None)
    evs = events(api.post("/api/ask", json=QUESTION))
    assert evs[-1]["answer"] is None and "no Anthropic API key" in evs[-1]["note"]


def test_visitor_codes_change_daily(tmp_path):
    state = StateDB(tmp_path / "s.db")
    a, b = visitor_key(state, "203.0.113.5", "2026-10-24"), visitor_key(state, "203.0.113.5", "2026-10-25")
    assert a != b and "203.0.113.5" not in a


def test_question_length_is_checked(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    assert api.post("/api/ask", json={"question": "x" * 501}).status_code == 422


def test_pages_robots_sitemap_and_link_previews(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", "https://countries.example")
    api, _ = make(indexed, FakeClient())
    robots = api.get("/robots.txt").text
    assert "Disallow: /api/" in robots and "Sitemap: https://countries.example/sitemap.xml" in robots
    sitemap = api.get("/sitemap.xml").text
    for loc in ("https://countries.example/</loc>", "/privacy.html</loc>", "/accessibility.html</loc>"):
        assert loc in sitemap
    home = api.get("/").text
    assert '<meta property="og:image" content="https://countries.example/og-image.png">' in home
    assert "__SITE__" not in home and "noindex" not in home
    shared = api.get("/", params={"q": 'What has Chad said about the "Sahel"?'}).text
    assert '<meta property="og:title" content="What has Chad said about the &quot;Sahel&quot;?">' in shared
    assert "<title>What has Chad said" in shared and 'content="noindex"' in shared
    assert 'href="https://countries.example/?q=' in shared
    assert api.get("/privacy.html").status_code == 200 and api.get("/nope.html").status_code == 404
    assert api.get("/styles.css").status_code == 200


def test_failures_are_kept_for_90_days(tmp_path):
    from wcs.api.usage import record_failure
    state = StateDB(tmp_path / "s.db")
    with state.connect() as con:
        con.execute("INSERT INTO failures (at, kind, question, error) VALUES ('2020-01-01T00:00:00+00:00', 'x', "
                    "'old', 'e')")
    record_failure(state, "briefing", "new", "e")
    with state.connect() as con:
        assert [r[0] for r in con.execute("SELECT question FROM failures")] == ["new"]


def test_usage_report_warns_near_a_cap(tmp_path):
    from datetime import UTC, datetime

    from wcs.api.usage import record_failure, record_usage, summary, warnings
    state = StateDB(tmp_path / "s.db")
    limits = {"daily_spend_cap_usd": 1.0, "monthly_spend_cap_usd": 50.0, "price_input_per_mtok": 2.0,
              "price_output_per_mtok": 10.0}
    record_usage(state, "answer", "m", {"input_tokens": 300_000, "output_tokens": 10_000}, limits, "v1")  # $0.70
    report = summary(state, limits, datetime.now(UTC))
    assert report["today"]["briefings"] == 1 and report["today"]["spent_usd"] == 0.7 and warnings(report) == []
    record_usage(state, "answer", "m", {"input_tokens": 50_000}, limits, "v2")                             # $0.80
    assert warnings(summary(state, limits)) == ["spending: today's cap of $1.00 80% used ($0.80 spent)"]
    record_usage(state, "answer", "m", {"input_tokens": 100_000}, limits, "v2")
    record_failure(state, "briefing", "q", "APIError: overloaded")
    alerts = warnings(summary(state, limits))
    assert "reached: new briefings are paused" in alerts[0] and "APIError: overloaded" in alerts[1]
    assert summary(state, limits)["today"]["visitors"] == 2


def test_briefings_are_saved_shared_and_withdrawn(indexed, monkeypatch):  # noqa: F811
    from wcs.api.usage import close_report, reports, withdraw_briefing
    monkeypatch.setenv("WCS_SITE_URL", "https://countries.example")
    api, state = make(indexed, FakeClient())
    final = events(api.post("/api/ask", json=QUESTION))[-1]
    bid = final["briefing_id"]
    saved = api.get(f"/api/briefings/{bid}").json()
    assert saved["question"] == QUESTION["question"] and [e["type"] for e in saved["events"]][-1] == "final"
    assert saved["events"][-1]["briefing_id"] == bid and saved["events"][-1]["usage"] is None
    assert events(api.post("/api/ask", json=QUESTION))[-1]["briefing_id"] == bid   # the cached answer keeps it
    page = api.get(f"/b/{bid}").text
    assert f"<title>{QUESTION['question']} · Where Countries Stand</title>" in page
    assert f'<link rel="canonical" href="https://countries.example/b/{bid}">' in page and "noindex" in page
    assert api.get("/b/nosuchbriefing").status_code == 404 and api.get("/api/briefings/nope99").status_code == 404
    assert withdraw_briefing(state, bid, "It misread a statement.")
    gone = api.get(f"/api/briefings/{bid}")
    assert gone.status_code == 410 and "It misread a statement." in gone.json()["detail"]
    assert api.get("/api/corrections").json()[0]["reason"] == "It misread a statement."
    assert api.post("/api/report", json={"message": "x"}).status_code == 422
    r = api.post("/api/report", json={"kind": "problem", "page": f"/b/{bid}", "message": "A quotation is wrong.",
                                      "contact": "reader@example.org"}).json()
    assert reports(state)[0]["contact"] == "reader@example.org" and close_report(state, r["id"])
    assert reports(state) == [] and reports(state, open_only=False)[0]["contact"] == ""


def test_pages_use_absolute_paths(indexed):  # noqa: F811
    """The home page is also served at /b/<id>, so its files and links must not be relative."""
    import re
    api, _ = make(indexed, FakeClient())
    for name in ("/", "/about.html", "/corrections.html", "/privacy.html", "/accessibility.html"):
        html_text = api.get(name).text
        local = [u for u in re.findall(r'(?:href|src)="([^"]+)"', html_text)
                 if not u.startswith(("https://", "http://", "mailto:", "#", "/"))]
        assert local == [], (name, local)

def test_status_reports_the_last_update(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    assert api.get("/api/status").json() == {"last_update": None}
    logs = indexed.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "update-20261101T0300.json").write_text(json.dumps({"started": "2026-11-01T03:00:00+00:00", "ok": True,
        "warnings": ["the General Debate website refused access (403)"], "errors": [], "gd": {"81": {"website": 0}}}))
    api, _ = make(indexed, FakeClient())
    st = api.get("/api/status").json()
    assert st["last_update"]["warnings"] == ["the General Debate website refused access (403)"]
    assert st["general_debate"] == {"81": {"website": 0}}


def test_stream_stops_when_the_reader_leaves():
    """A briefing nobody is reading any more must stop being written: its events are closed, which closes the
    model's stream (found on the live site: a reader who left kept the briefing running, and paid for)."""
    import threading
    import time

    from wcs.api.app import sse

    closed = threading.Event()
    made = []

    def briefing():
        try:
            while True:
                made.append(1)
                yield {"type": "text", "text": "word "}
                time.sleep(0.01)
        finally:
            closed.set()

    events = briefing()  # held here, so only an explicit close (not the garbage collector) can stop it
    out = sse(events, keep_alive=1)
    assert next(out).startswith(b"event: text")
    out.close()  # the reader has gone: the server closes the response
    assert closed.wait(2)
    n = len(made)
    time.sleep(0.1)
    assert len(made) == n  # nothing more is written


def test_briefings_in_progress_count_against_the_caps(tmp_path):
    from wcs.api.usage import InFlight, check_caps, record_usage
    state = StateDB(tmp_path / "s.db")
    limits = {"daily_spend_cap_usd": 1.0, "monthly_spend_cap_usd": 50.0, "price_input_per_mtok": 2.0}
    record_usage(state, "answer", "m", {"input_tokens": 300_000}, limits, "v1")      # $0.60 spent
    flight = InFlight(per_key=2, total=3, estimate_usd=0.25)
    assert flight.start("a") is None and flight.start("a") is None
    assert "already being written" in flight.start("a")
    assert flight.start("b") is None and "Many briefings" in flight.start("c")
    assert flight.pending_usd(besides=1) == 0.5
    assert check_caps(state, limits, "v2") is None                                     # $0.60 alone is under $1
    assert "budget" in check_caps(state, limits, "v2", flight.pending_usd(besides=1))  # with $0.50 more on the way
    flight.finish("a"), flight.finish("a"), flight.finish("b")
    assert flight.running == {} and flight.pending_usd() == 0


def test_a_second_briefing_at_once_waits_its_turn(indexed):  # noqa: F811
    """While one briefing is being written for a visitor, another question from them gets the sources only."""
    import threading

    class SlowClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.started, self.release = threading.Event(), threading.Event()

        def _stream(self, **kw):
            self.started.set()
            assert self.release.wait(10)
            return super()._stream(**kw)

    client = SlowClient()
    api, _ = make(indexed, client, briefings_at_once_per_visitor=1)
    first: list = []
    t = threading.Thread(target=lambda: first.append(events(api.post("/api/ask", json=QUESTION))))
    t.start()
    try:
        assert client.started.wait(10)
        second = events(api.post("/api/ask", json={"question": "What has Fiji said about climate finance?"}))
    finally:
        client.release.set()
        t.join(10)
    assert second[-1]["answer"] is None and "already being written for you" in second[-1]["note"]
    assert first[0][-1]["answer"]["citations_total"] == 1 and len(client.calls) == 1
    third = events(api.post("/api/ask", json={"question": "What has Fiji said about climate finance?"}))
    assert third[-1]["answer"] is not None  # once the first is done, the next is written


def test_saved_briefings_are_shown_with_flags_judged_again(indexed):  # noqa: F811
    """A mark made by an older rule that has since been corrected (a lead-in taken for an unsupported claim, a
    neutral "claimed" taken for a loaded word) is not shown on a briefing saved before the correction."""
    from wcs.api.usage import save_briefing
    api, state = make(indexed, FakeClient())
    answer = {"markdown": "Kenya said climate finance is slow. The savings claimed were large.",
              "segments": [{"text": "Kenya said ", "status": "unsupported"},
                           {"text": "climate finance is slow.", "status": "cited", "sources": [1]},
                           {"text": " The savings claimed were large.", "status": "cited", "sources": [1],
                            "loaded": [{"start": 13, "end": 20, "word": "claimed"}]}],
              "unsupported_segments": 1, "grounded_ratio": 0.5, "loaded_words": 1}
    save_briefing(state, "OldOne12", QUESTION["question"], {}, [{"type": "final", "answer": answer}])
    shown = api.get("/api/briefings/OldOne12").json()["events"][-1]["answer"]
    assert shown["segments"][0]["status"] == "connective" and shown["unsupported_segments"] == 0
    assert shown["loaded_words"] == 0 and shown["segments"][2]["loaded"] == []
