"""The web service, with the fake model client from test_pipeline (no network)."""

import json

import pytest
from fastapi.testclient import TestClient
from test_pipeline import TODAY, FakeClient, RefusingClient, indexed  # noqa: F401  (the fixture)

from wcs.api.app import SITE_DIR as SITE
from wcs.api.app import create_app
from wcs.api.usage import StateDB, cost_usd, network_of, visitor_key
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


def test_a_refusal_is_an_error_and_nothing_is_saved_or_cached(indexed):  # noqa: F811
    """The model declining to answer was once shown, saved and cached as an empty briefing with nothing to check.
    The reader is told, the owner has a note of it, and the call is still counted as spent."""
    client = RefusingClient()
    api, state = make(indexed, client)
    evs = events(api.post("/api/ask", json=QUESTION))
    assert [e["type"] for e in evs] == ["plan", "sources", "error"]
    assert evs[-1] == {"type": "error", "message": "The model declined to answer this question."}
    with state.connect() as con:
        assert con.execute("SELECT count(*) FROM briefings").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM answer_cache").fetchone()[0] == 0
        assert con.execute("SELECT question, error FROM failures WHERE kind = 'briefing'").fetchall() == [
            (QUESTION["question"], "The model declined to answer this question.")]
        kind, cost = con.execute("SELECT kind, cost_usd FROM usage").fetchone()
    assert kind == "answer" and cost == pytest.approx(cost_usd({"input_tokens": 1000, "output_tokens": 200},
                                                               indexed.usage, "claude-sonnet-5-5"))
    again = events(api.post("/api/ask", json=QUESTION))
    assert again[-1]["type"] == "error" and len(client.calls) == 2   # asked afresh: nothing was kept to replay


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


def test_ipv6_visitors_are_counted_by_their_64():
    """One connection is given a whole IPv6 /64, so each address in it is the same visitor for the limits."""
    assert network_of("2001:db8:1:2:aaaa::1") == network_of("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert network_of("2001:db8:1:3::1") != network_of("2001:db8:1:2::1")
    assert network_of("203.0.113.5") == "203.0.113.5"
    assert network_of("::ffff:203.0.113.5") == "203.0.113.5"
    assert network_of("::1") == "::1"
    assert network_of(None) == "-" and network_of("testclient") == "testclient"


def test_visitor_codes_group_an_ipv6_64(tmp_path):
    state = StateDB(tmp_path / "s.db")
    day = "2026-11-06"
    assert visitor_key(state, "2001:db8::1", day) == visitor_key(state, "2001:db8::abcd:1", day)
    assert visitor_key(state, "2001:db8::1", day) != visitor_key(state, "2001:db8:0:1::1", day)


def test_question_length_is_checked(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    assert api.post("/api/ask", json={"question": "x" * 501}).status_code == 422


def test_pages_robots_sitemap_and_link_previews(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", "https://countries.example")
    api, _ = make(indexed, FakeClient())
    robots = api.get("/robots.txt").text
    assert "Disallow: /api/" in robots and "Sitemap: https://countries.example/sitemap.xml" in robots
    sitemap = api.get("/sitemap.xml").text
    for loc in ("https://countries.example/</loc>", "/privacy.html</loc>", "/accessibility.html</loc>",
                "/terms.html</loc>", "/glossary.html</loc>"):
        assert loc in sitemap
    home = api.get("/").text
    # the family's link card, in Record blue (the kit's head partial)
    assert '<meta property="og:image" content="https://wherecountriesstand.com/family/card.png">' in home
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
    for name in ("/", "/about.html", "/corrections.html", "/named.html", "/privacy.html", "/accessibility.html"):
        html_text = api.get(name).text
        local = [u for u in re.findall(r'(?:href|src)="([^"]+)"', html_text)
                 if not u.startswith(("https://", "http://", "mailto:", "#", "/", "data:"))]  # data: the kit's inline cartridge
        assert local == [], (name, local)

def test_status_reports_the_last_update(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    st = api.get("/api/status").json()
    assert st["last_update"] is None and st["briefings"] == "available" and st["briefings_note"] is None
    assert st["coverage"]["sc"]["to"] and st["coverage"]["sc"]["speeches"] == 2
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


def test_saved_briefings_are_shown_with_curly_quotes(indexed):  # noqa: F811
    """A briefing saved before quotes were curled is shown with them curled, its marks where they were."""
    from wcs.api.usage import save_briefing
    api, state = make(indexed, FakeClient())
    text = 'Kenya called it "too slow" and the Council\'s regime was mentioned.'
    mark = {"start": text.index("regime"), "end": text.index("regime") + 6, "word": "regime"}
    answer = {"segments": [{"text": text, "status": "cited", "sources": [1], "loaded": [mark]}], "loaded_words": 1}
    save_briefing(state, "Quoted12", QUESTION["question"], {}, [{"type": "final", "answer": answer}])
    shown = api.get("/api/briefings/Quoted12").json()["events"][-1]["answer"]["segments"][0]
    assert shown["text"] == "Kenya called it “too slow” and the Council’s regime was mentioned."
    assert shown["loaded"] == [mark] and shown["text"][mark["start"]:mark["end"]] == "regime"


def test_a_saved_curled_briefing_keeps_its_points_to_check(indexed):  # noqa: F811
    """The recheck reads a briefing saved with curly quotes as it read the straight ones: a sentence that ends in a
    closing quote is still a claim of its own, not a lead-in to the quotation after it."""
    from wcs.api.usage import save_briefing
    from wcs.rag.typography import curl_answer
    api, state = make(indexed, FakeClient())
    answer = {"segments": [{"text": 'He said "no." Mr Smith said ', "status": "unsupported"},
                           {"text": "we will act", "status": "cited", "sources": [1]}],
              "unsupported_segments": 1}
    for bid, saved in (("Straight1", answer), ("Curled12", curl_answer(answer))):
        save_briefing(state, bid, QUESTION["question"], {}, [{"type": "final", "answer": saved}])
        shown = api.get(f"/api/briefings/{bid}").json()["events"][-1]["answer"]["segments"][0]
        assert shown["status"] == "unsupported", bid
        assert shown["text"] == "He said “no.” Mr Smith said ", bid


def test_no_generated_api_pages(indexed):  # noqa: F811
    """Swagger's page needs a CDN and inline script, which the site's content security policy blocks: it was blank."""
    api, _ = make(indexed, FakeClient())
    for path in ("/api/docs", "/api/openapi.json", "/redoc", "/docs", "/openapi.json"):
        assert api.get(path).status_code == 404, path


def test_scripts_and_styles_carry_their_hash_and_are_kept_long(indexed):  # noqa: F811
    """Without a hash in their addresses, browsers asked again for the scripts and styles on every visit."""
    import re
    api, _ = make(indexed, FakeClient())
    home = api.get("/")
    assert home.headers["cache-control"] == "no-cache"
    refs = re.findall(r'(?:href|src)="(/[a-z/]+\.(?:css|js)[^"]*)"', home.text)
    assert {r.split("?")[0] for r in refs} == {"/fonts.css", "/styles.css", "/app.js", "/family/fonts.css",
                                              "/family/family.css"}
    for ref in refs:
        assert re.fullmatch(r"/[a-z/]+\.(css|js)\?v=[0-9a-f]{12}", ref), ref
        assert api.get(ref).headers["cache-control"] == "public, max-age=31536000, immutable"
    assert api.get("/styles.css").headers["cache-control"] == "no-cache"          # no hash: checked each time
    assert api.get("/styles.css?v=000000000000").headers["cache-control"] == "no-cache"   # an old hash
    font = re.search(r"url\((fonts/[^)]+\.woff2)\)", api.get("/fonts.css").text)[1]
    assert api.get(f"/{font}").headers["cache-control"] == "public, max-age=31536000, immutable"
    for page in ("/about.html", "/corrections.html", "/b/nosuchbriefing"):
        r = api.get(page)
        assert r.headers["cache-control"] == "no-cache" and "?v=" in r.text, page


def test_unknown_addresses_get_a_page_and_the_api_keeps_json(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    for path in ("/nothing-here", "/nope.html", "/404.html", "/fonts/missing.woff2", "/a/b/c"):
        r = api.get(path)
        assert r.status_code == 404 and r.headers["content-type"].startswith("text/html"), path
        assert "<h1>Page not found</h1>" in r.text and "?v=" in r.text and 'content="noindex"' in r.text
    for path in ("/api/nothing", "/api/briefings/nope99"):
        r = api.get(path)
        assert r.status_code == 404 and r.headers["content-type"] == "application/json", path
    assert api.post("/api/nothing", json={}).status_code in (404, 405)
    assert "404" not in api.get("/sitemap.xml").text


def test_alpha_notice_links_to_the_report_form(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    for page in ("/", "/about.html", "/corrections.html", "/privacy.html", "/accessibility.html", "/missing"):
        assert '<a href="/corrections.html#report">report anything wrong</a>' in api.get(page).text, page
    assert 'id="report"' in api.get("/corrections.html").text
    assert "<noscript>" in api.get("/").text


PAGES = ("/", "/about.html", "/corrections.html", "/named.html", "/privacy.html", "/accessibility.html", "/terms.html",
         "/glossary.html", "/press.html", "/status.html", "/missing")
INDEPENDENCE = ("Where Countries Stand is independent. It is not affiliated with or endorsed by the United Nations, "
                "and it does not use the UN's name or emblem.")


def test_every_page_carries_the_family_binding_and_the_standard_furniture(indexed):  # noqa: F811
    """The masthead and footer are written once (web/partials/) and put into every page as it is served."""
    api, _ = make(indexed, FakeClient())
    for page in PAGES:
        text = api.get(page).text
        assert "<!--#include" not in text, page
        assert '<body class="wts-headings">' in text and '<meta name="theme-color" content="#1F5F8B" media="(prefers-color-scheme: light)">' in text, page
        assert '<a class="wts-endorse" href="https://wheretheystand.uk/">' in text, page    # under the name
        assert '<nav class="wts-shelf"' in text, page                                         # the footer shelf
        assert INDEPENDENCE in text and "Independent. Not affiliated with the United Nations." in text, page
        assert "Where They Stand is applying to register as a charity in England and Wales." in text, page
        assert "the charity Where They Stand" not in text and "gmail" not in text.lower(), page
        assert '<a href="https://github.com/rj-mitchell/wherecountriesstand">Source code</a>' in text, page
        footer = text[text.index('<footer class="foot">'):]
        order = ["/about.html", "/corrections.html", "/named.html", "/accessibility.html", "/privacy.html", "/terms.html",
                 "/press.html", "/status.html", "https://github.com/rj-mitchell/wherecountriesstand"]
        assert [footer.index(f'href="{u}"') for u in order] == sorted(footer.index(f'href="{u}"') for u in order)


def test_the_current_page_is_marked_in_the_navigation(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    assert '<a href="/" aria-current="page">Ask</a>' in api.get("/").text
    about = api.get("/about.html").text
    assert about.count('<a href="/about.html" aria-current="page">') == 2      # the sections and the footer
    assert '<a href="/" aria-current="page">' not in about
    assert 'aria-current="page"' not in api.get("/missing").text
    assert '<a href="/">Where Countries Stand</a>' in about                   # the masthead's name is not marked


def test_family_files_are_served_and_its_partials_are_not(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    icon = api.get("/favicon.ico")
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/x-icon"
    assert icon.content == (SITE / "family" / "favicon.ico").read_bytes()
    assert api.get("/family/card.png").headers["cache-control"] == "public, max-age=86400"
    font = api.get("/family/fonts/source-serif-4-normal-latin.woff2")
    assert font.status_code == 200 and font.headers["cache-control"] == "public, max-age=604800"
    for path in ("/family/shelf.html", "/family/head.html", "/family/"):
        assert api.get(path).status_code == 404, path
    # the kit's VERSION is served, for the charity's nightly family check
    version = api.get("/family/VERSION")
    assert version.status_code == 200 and version.text.startswith("Where They Stand family kit")
    assert "site countries" in version.text


def test_a_missing_partial_stops_the_site_starting(tmp_path):
    from wcs.api.app import expand_includes
    (tmp_path / "header.html").write_text("<p>masthead</p>\n")
    assert expand_includes("<body>\n  <!--#include header-->\n</body>", tmp_path, tmp_path) == \
        "<body>\n  <p>masthead</p>\n</body>"
    with pytest.raises(FileNotFoundError):
        expand_includes("<!--#include footer-->", tmp_path, tmp_path)


def test_the_family_kit_copy_matches_its_version_file():
    """web/site/family/ is the kit as synced (family/kit/sync.mjs, which CI also checks when it can): its VERSION
    names the site and the hash of the files, and none of them has been edited since."""
    import hashlib
    family = SITE / "family"
    lines = dict(line.split(" ", 1) for line in (family / "VERSION").read_text().splitlines()[1:5])
    assert lines["site"] == "countries" and lines["base"] == "/family/"
    digest = hashlib.sha256()
    for path in sorted(p.relative_to(family).as_posix() for p in family.rglob("*")
                       if p.is_file() and p.name != "VERSION"):
        digest.update(path.encode() + b"\0" + (family / path).read_bytes() + b"\0")
    assert digest.hexdigest() == lines["sha256"]


def test_a_briefing_links_to_sister_sites(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    home = api.get("/").text
    assert 'id="sisters"' in home and 'id="sister-links"' in home
    script = (SITE / "app.js").read_text()
    assert 'const MPS = "https://wherempsstand.com/";' in script
    assert 'const LIVES = "https://wherelivesstand.com/country/";' in script


def test_cost_copes_with_the_sdks_own_names_and_records_cache_writes(tmp_path):
    """Token counts in the SDK's own names cost the same as the site's (whose input includes the cache writes), and
    cache writes are recorded (as Where Councils Stand does)."""
    from wcs.api.usage import record_usage, token_counts

    prices = {"price_input_per_mtok": 4.0, "price_output_per_mtok": 20.0, "price_cache_read_per_mtok": 0.4}
    ours = {"input_tokens": 1500, "output_tokens": 200, "cache_read_tokens": 3000, "cache_write_tokens": 500}
    raw = {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 3000,
           "cache_creation_input_tokens": 500}
    assert token_counts(raw) == ours
    assert cost_usd(raw, prices) == cost_usd(ours, prices) == pytest.approx(
        1000 / 1e6 * 4 + 500 / 1e6 * 4 * 1.25 + 200 / 1e6 * 20 + 3000 / 1e6 * 0.4)
    assert cost_usd({"input_tokens": 1000, "cache_read_input_tokens": None}, prices) == pytest.approx(0.004)
    state = StateDB(tmp_path / "s.db")
    record_usage(state, "answer", "m", raw, prices, "v1")
    with state.connect() as con:
        row = con.execute("SELECT input_tokens, cache_read_tokens, cache_write_tokens FROM usage").fetchone()
    assert row == (1500, 3000, 500)


def test_an_older_state_database_gains_the_cache_writes_column(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY, visitor TEXT, at TEXT, kind TEXT, model TEXT, "
                "input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cost_usd REAL)")
    con.execute("INSERT INTO usage (visitor, at, kind, input_tokens, cost_usd) VALUES ('v', '2026-01-01', 'answer', "
                "10, 0.5)")
    con.commit()
    con.close()
    state = StateDB(path)
    with state.connect() as con:
        assert con.execute("SELECT cache_write_tokens, cost_usd FROM usage").fetchone() == (0, 0.5)


def test_status_says_when_briefings_are_paused_or_unavailable(indexed, monkeypatch):  # noqa: F811
    from wcs.api.usage import record_usage

    api, state = make(indexed, FakeClient(), daily_spend_cap_usd=0.5)
    record_usage(state, "answer", "m", {"input_tokens": 300_000}, {"price_input_per_mtok": 2.0}, "v1")
    st = api.get("/api/status").json()
    assert st["briefings"] == "paused" and "Today's budget" in st["briefings_note"]
    for name in ("WCS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    api, _ = make(indexed, None)
    assert api.get("/api/status").json()["briefings"] == "unavailable"


def test_status_keeps_the_backups_private(indexed):  # noqa: F811
    logs = indexed.data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "update-20261101T0300.json").write_text(json.dumps({"started": "2026-11-01T03:00:00+00:00", "ok": True,
        "warnings": [], "errors": [], "needs_attention": True,
        "backups": [{"level": "error", "text": "the last off-site copy failed: secret detail"}]}))
    api, _ = make(indexed, FakeClient())
    st = api.get("/api/status")
    assert st.json()["last_update"]["ok"] is True and "secret detail" not in st.text and "backups" not in st.text


def test_the_press_page(indexed):  # noqa: F811
    """Contact, how to cite a briefing and a record, the site in brief, and logo files that are really there."""
    import re

    api, _ = make(indexed, FakeClient())
    text = api.get("/press.html").text
    main = " ".join(text[text.index("<main"):text.index("</main>")].split())
    assert "mailto:admin@wherecountriesstand.com" in main and INDEPENDENCE.replace("'", "’") in main
    assert "https://wherecountriesstand.com/b/…" in text and "S/PV.[number]" in text and "CC BY-NC 4.0" in text
    assert 'id="coverage"' in text and "from 1992" in text  # filled in from /api/meta, readable without it
    files = re.findall(r'href="(/(?:family/)?[\w-]+\.(?:svg|png))"', main)
    assert {"/icon.svg", "/family/icon.svg", "/family/icon-512.png", "/family/card.png"} <= set(files)
    for f in files:
        assert api.get(f).status_code == 200, f


def test_the_status_page_reads_without_javascript(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    text = api.get("/status.html").text
    noscript = text[text.index("<noscript>"):text.index("</noscript>")]
    assert '<a href="/api/status">/api/status</a>' in noscript
    assert '<script src="/status.js?v=' in text and 'id="status-live"' in text
    assert "/status.html</loc>" in api.get("/sitemap.xml").text and "/press.html</loc>" in api.get("/sitemap.xml").text
