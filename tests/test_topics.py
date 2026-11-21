"""Topic briefings (decision 27): the topics in config/topics.toml, each topic's page written on the server with
the family's checked briefing (strip, chips, marks, the check details and the sources), its share card, the index,
the home page's list and the sitemap; the weekly refresh, which writes each briefing through the site's own
pipeline and keeps the last good one when a run fails, finds no records or is stopped at a cap; and the timer that
runs it.

The server's HTML of a briefing must be the page script's: the first test writes it for two briefings into
web/tests/fixtures/server-briefings.json, which web/tests/briefing.test.js renders with the script and compares
(``node --test web/tests/``). Set WCS_WRITE_FIXTURES=1 to write it again after an intended change."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest
from conftest import model_of
from fastapi.testclient import TestClient
from test_api import make
from test_pipeline import TODAY, FakeClient, indexed  # noqa: F401  (the fixture)
from wts_core.adapters.hashing import HashingEmbedder
from wts_core.checks import check_counts
from wts_core.dates import long_date
from wts_core.testing import FixedClock

from wcs.api import briefing_html as B
from wcs.api.app import create_app, site_parts
from wcs.api.topic_pages import final_and_sources
from wcs.engine import Engine
from wcs.rag.pipeline import Pipeline
from wcs.rag.query_parse import QueryPlan
from wcs.state import open_state
from wcs.topics import keep_version, last_run, load_topics, refresh, topics_file, versions

ROOT = Path(__file__).parents[1]
SAVED = json.loads((ROOT / "tests" / "fixtures" / "countries-kGfKBI3cRLw-2026-11-21.json").read_text())
SERVER = ROOT / "web" / "tests" / "fixtures" / "server-briefings.json"
VETO = "security-council-reform-and-the-veto"
sys.path.insert(0, str(ROOT / "scripts"))
import sample_topic  # noqa: E402

TOPICS = ["war-in-ukraine", "israel-gaza-and-palestine", "sudan", "afghanistan", "haiti", VETO,
          "climate-change-and-climate-finance", "nuclear-weapons-and-non-proliferation", "women-peace-and-security",
          "unilateral-sanctions"]


def topic_of(settings, slug):
    return next(t for t in load_topics(topics_file(settings)) if t.slug == slug)


def save_as_topic(api, slug=VETO, events=None, bid=None, up_to="2026-11-20"):
    """The fixed copy of kGfKBI3cRLw (the veto) kept as a topic's newest version."""
    archive = api.app.state.parts.archive
    b = archive.save(SAVED["question"], SAVED["options"], events or SAVED["events"], id=bid)
    keep_version(api.app.state.db, slug, b.id, b.created_at, up_to)
    return b


# The topics


def test_the_ten_topics_load_from_config_each_a_neutral_question_the_record_can_answer(settings):
    topics = load_topics(topics_file(settings))
    assert [t.slug for t in topics] == TOPICS
    assert len({t.title for t in topics}) == len({t.question for t in topics}) == 10
    for t in topics:
        # what countries and groups said, at the UN, over a stated period; how they voted only where it is asked
        assert t.question.startswith("What have countries and groups said about ") and t.question.endswith("?")
        assert "at the United Nations since 2022" in t.question and t.date_from == "2022-01-01"
        assert t.path == f"/topics/{t.slug}.html" and t.sources == ()
        assert not re.search(r"\b(regime|rogue|the West|failed|illegal|aggression|genocide|terror)", t.question)
    voted = {t.slug for t in topics if t.question.endswith("and how have they voted?")}
    assert voted == {"war-in-ukraine", "israel-gaza-and-palestine", "nuclear-weapons-and-non-proliferation",
                     "unilateral-sanctions"}
    # both names for unilateral sanctions, so neither side's word is the site's
    assert "unilateral coercive measures" in topic_of(settings, "unilateral-sanctions").question


def test_a_topic_list_with_a_mistake_stops_the_site(tmp_path):
    bad = tmp_path / "topics.toml"
    bad.write_text('[[topic]]\nslug = "a"\ntitle = "A"\nquestion = "Q?"\n[[topic]]\nslug = "a"\ntitle = "B"\n'
                   'question = "Q?"\n')
    with pytest.raises(ValueError, match="unique"):
        load_topics(bad)
    bad.write_text('[[topic]]\nslug = "Not A Slug"\ntitle = "A"\nquestion = "Q?"\n')
    with pytest.raises(ValueError):
        load_topics(bad)
    bad.write_text('[[topic]]\nslug = "a"\ntitle = "A"\nquestion = "Q?"\ndate_from = "2022"\n')
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        load_topics(bad)
    assert load_topics(tmp_path / "none.toml") == []


# The page, written on the server


def test_the_server_writes_the_briefing_as_the_page_script_does():
    """What web/tests/briefing.test.js renders with the script and compares: the strip, the text and the check
    details, for kGfKBI3cRLw as shown and for the made-up briefing with every kind of mark."""
    cases = []
    for name, events in (("kGfKBI3cRLw", SAVED["events"]), ("every-mark", sample_topic.events("A sample?"))):
        answer, sources, _, _ = final_and_sources(events)
        by_n = {s["n"]: s for s in sources}
        cases.append({"name": name, "answer": answer, "sources": [
            {k: s.get(k) for k in ("n", "kind", "date", "date_precision", "on_behalf_of", "country_name",
                                   "speaker_name", "body")} for s in sources],
            "found": len(sources), "strip": B.to_html(B.strip(answer)),
            "blocks": B.html_of(B.briefing_blocks(answer, by_n)), "details": B.to_html(B.details(answer, len(sources)))})
    fixture = {"cases": cases}
    if os.environ.get("WCS_WRITE_FIXTURES"):
        SERVER.write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n")
    assert json.loads(SERVER.read_text()) == fixture
    # and what the server writes is what the strip's counts say: wts-core's check_counts on the answer as shown
    veto = cases[0]
    counts = check_counts(veto["answer"])
    assert (counts.citations_matched, counts.quotes_found, counts.no_source) == (16, 15, 2)
    assert re.search(r'<span class="wts-check__num">16</span><span>of 16</span>', veto["strip"])


def test_a_topic_page_shows_its_saved_briefing_without_a_script(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", "https://countries.example")
    api, _ = make(indexed)
    b = save_as_topic(api)
    t = topic_of(indexed, VETO)
    r = api.get(t.path)
    assert r.status_code == 200
    page = r.text
    # its own address and title, indexed, its share card, and only breadcrumbs as structured data
    assert f'<link rel="canonical" href="https://countries.example{t.path}">' in page
    assert "noindex" not in page
    assert f"<title>{t.title}: briefing from UN records | Where Countries Stand</title>" in page
    assert page.count("<h1") == 1 and f'<h1 id="question">{t.title}</h1>' in page
    assert f'<meta property="og:image" content="https://countries.example/topics/{VETO}/card.png">' in page
    data = [json.loads(x) for x in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)]
    assert [d["@type"] for d in data] == ["BreadcrumbList"]
    assert [i["name"] for i in data[0]["itemListElement"]] == ["Where Countries Stand", "Topic briefings", t.title]
    # the question the site asks, the day it was prepared and the record it could use
    assert t.question in page
    assert f"Briefing of {long_date(b.created_at)}, written by AI from the sources below, from the UN’s records up " \
           "to 20 November 2026." in page
    # the strip, the chips, the record's quotations, the marks, the details and the sources, all in the HTML
    main = page[page.index("<main"):page.index("</main>")]
    assert '<h2 class="wts-check__title" id="check-title">Checked against the UN record</h2>' in main
    assert re.search(r"✓</span><span class=\"wts-check__num\">16</span><span>of 16</span>", main)
    chips = re.findall(r'<button type="button" class="wts-chip" data-source="\d+" data-seg="\d+">(.*?)</button>', main)
    assert len(chips) > 10
    assert re.sub(r"<[^>]+>", "", chips[0]) == "Source 1 Italy, Security Council, 14 November 2025. Show the passage"
    assert main.count('class="wts-quote wts-quote--checked"') == 15
    assert main.count('class="wts-nosource"') == 2 and main.count("wts-nosource__tag") == 1
    assert 'id="check-details"' in main and "How this briefing was checked" in main
    sources = next(e for e in SAVED["events"] if e["type"] == "sources")["sources"]
    assert main.count('<li class="source') == len(sources) and "Read S/PV.10043 (Resumption 1)" in main
    # its saved address for reports, and the script that adds the record beside it and the sheet
    assert f'<a href="/corrections.html?page=/b/{b.id}">Report a problem with it</a>' in main
    assert f'<body class="wts-headings" data-briefing="{b.id}">' in page
    assert re.search(r'<script src="/app\.js\?v=[0-9a-f]+" defer></script>', page)
    assert '<a href="/topics.html" aria-current="page">Topics</a>' in page
    # nothing from the record or the model is written as markup
    assert "<script" not in main


def test_a_topic_has_its_own_share_card_with_its_question(indexed, monkeypatch):  # noqa: F811
    from PIL import Image

    api, _ = make(indexed)
    b = save_as_topic(api)
    t = topic_of(indexed, VETO)
    alt = re.search(r'<meta property="og:image:alt" content="([^"]*)">', api.get(t.path).text)[1]
    assert alt.startswith(t.question + " A briefing written by AI, checked against the UN record.")
    assert "16 of 16 citations matched to the record, 15 of 15 quotations found word for word, 2 sentences" in alt
    card = api.get(f"/topics/{VETO}/card.png")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png"
    kept = list((indexed.data_dir / "cards").glob(f"topic-{VETO}-{b.id}.*.png"))
    assert len(kept) == 1 and kept[0].read_bytes() == card.content
    with Image.open(kept[0]) as image:
        assert image.size == (1200, 630)
    # the same briefing at its own address keeps the site's neutral card (decision 7)
    assert api.get(f"/b/{b.id}/card.png").content != card.content
    assert api.get("/topics/sudan/card.png").status_code == 404  # not written yet


def test_a_withdrawn_or_hidden_version_gives_way_to_the_one_before(indexed):  # noqa: F811
    api, _ = make(indexed)
    older = save_as_topic(api)
    newer = save_as_topic(api)
    t = topic_of(indexed, VETO)
    assert f'data-briefing="{newer.id}"' in api.get(t.path).text
    assert f'<a href="/b/{older.id}">' in api.get(t.path).text  # earlier versions are linked
    assert api.app.state.parts.archive.withdraw(newer.id, "A test.")
    assert f'data-briefing="{older.id}"' in api.get(t.path).text
    api.app.state.replies.hide(f"/b/{older.id}", note="Being looked at.")
    page = api.get(t.path).text
    assert "data-briefing" not in page and "has not been written yet, or is not available just now" in page
    assert '<meta name="robots" content="noindex">' in page
    assert t.path not in api.get("/sitemap-topics.xml").text


def test_a_topic_not_written_yet_has_a_page_out_of_the_index_and_the_sitemap(indexed):  # noqa: F811
    api, _ = make(indexed)
    page = api.get("/topics/sudan.html")
    assert page.status_code == 200 and '<meta name="robots" content="noindex">' in page.text
    index = api.get("/topics.html").text
    assert "Not written yet" in index and 'href="/topics/sudan.html"' not in index
    assert 'id="topics-head"' not in api.get("/").text  # the home page lists only topics with a briefing
    assert api.get("/topics/no-such-topic.html").status_code == 404


def test_the_index_the_home_page_and_the_sitemap_list_the_topics(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", "https://countries.example")
    api, _ = make(indexed)
    b = save_as_topic(api)
    t = topic_of(indexed, VETO)
    index = api.get("/topics.html").text
    assert "<title>Topic briefings | Where Countries Stand</title>" in index and "noindex" not in index
    assert f'<a href="{t.path}">{t.title}</a>' in index and t.question in index
    assert index.count("<h2") == 2  # the two areas: countries and conflicts, and themes
    home = api.get("/").text
    topics = home[home.index('id="topics-head"'):]
    assert f'<a href="{t.path}">{t.title}</a>' in topics[:topics.index("</section>")]
    assert '<a href="/topics.html">All topic briefings</a>' in topics
    assert '<a href="/topics.html">Topics</a>' in home  # in the menu
    day = b.created_at[:10]
    sitemap = api.get("/sitemap-topics.xml").text
    assert sitemap.count("<url>") == 2
    assert f"<url><loc>https://countries.example/topics.html</loc><lastmod>{day}</lastmod></url>" in sitemap
    assert f"<url><loc>https://countries.example{t.path}</loc><lastmod>{day}</lastmod></url>" in sitemap
    assert f"<sitemap><loc>https://countries.example/sitemap-topics.xml</loc><lastmod>{day}</lastmod>" in \
        api.get("/sitemap.xml").text
    assert "/b/" not in sitemap  # a reader's briefing, and a topic's at its own address, stay out


def test_other_forms_of_a_topics_address_are_sent_to_its_own_for_good(indexed):  # noqa: F811
    api, _ = make(indexed)
    for asked, sent in (("/topics", "/topics.html"), ("/topics/", "/topics.html"), ("/topics.html/", "/topics.html"),
                        ("/topics/sudan", "/topics/sudan.html"), ("/topics/sudan/", "/topics/sudan.html"),
                        ("/topics/Sudan.html", "/topics/sudan.html"), ("/topics/sudan.html/", "/topics/sudan.html")):
        r = api.get(asked, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == sent, asked
    assert api.get("/topics/nowhere", follow_redirects=False).status_code == 404


# The weekly refresh


def pipeline_of(settings, client=None):
    return Pipeline(settings, Engine(settings, embedder=HashingEmbedder(64), today=TODAY),
                    client=model_of(client if client is not None else FakeClient()), today=TODAY)


class Stub:
    """A pipeline whose briefing is what the test says: the question read, then ``run``'s events."""

    def __init__(self, run):
        self._run, self.calls = run, 0

    def has_model(self):
        return True

    def make_plan(self, req):
        return QueryPlan.from_dict({"question": req.question, "search_text": "x", "parser": "model",
                                    "filters": {}}), {"input_tokens": 1000, "output_tokens": 100}

    def run(self, req):
        self.calls += 1
        yield from self._run(req)


def setup(settings, **topics):
    clock = FixedClock("2026-11-22T11:23:00+00:00")
    settings.raw["topics"] = {**settings.raw["topics"], **topics}
    db = open_state(settings, clock)
    return db, site_parts(settings, db, clock), clock


def test_refresh_writes_each_briefing_through_the_sites_pipeline_and_counts_it(indexed):  # noqa: F811
    db, parts, clock = setup(indexed)
    done = refresh(indexed, db, pipeline_of(indexed), parts, only=["climate-change-and-climate-finance"],
                   records_up_to=lambda: "2026-11-20", clock=clock, progress=lambda _: None)
    assert done.ok and len(done.outcomes) == 1 and done.spent_usd > 0
    bid = done.outcomes[0].briefing_id
    v = versions(db, "climate-change-and-climate-finance")
    assert [x["briefing_id"] for x in v] == [bid] and v[0]["records_up_to"] == "2026-11-20"
    saved = parts.archive.get(bid)
    assert saved.options == {"mode": "summary", "topic": "climate-change-and-climate-finance"}
    assert [e["type"] for e in saved.events] == ["plan", "sources", "text", "final"]
    final = saved.events[-1]
    assert final["briefing_id"] == bid and final["usage"] is None
    assert final["answer"]["citations_total"] == 1 and final["answer"]["loaded_words"] == 1  # every check ran
    # the topic's own dates are laid over the reading of its question
    assert saved.events[0]["plan"]["filters"]["date_from"] == "2022-01-01"
    # its cost is in the spending record, as kind "topic", and counts against the caps
    with db.connect() as con:
        kinds = {r[0] for r in con.execute("SELECT kind FROM wts_spending")}
    assert kinds == {"topic"} and parts.spending.spent_since("2026-11") == pytest.approx(done.spent_usd)
    assert last_run(db, "climate-change-and-climate-finance")["outcome"] == f"written ({bid})"
    # written six days ago or less: not paid for again unless asked
    again = refresh(indexed, db, pipeline_of(indexed), parts, only=["climate-change-and-climate-finance"],
                    clock=clock, progress=lambda _: None)
    assert again.ok and again.outcomes[0].briefing_id is None and "not written again" in again.outcomes[0].outcome
    clock.advance(days=7)
    assert refresh(indexed, db, pipeline_of(indexed), parts, only=["climate-change-and-climate-finance"],
                   clock=clock, progress=lambda _: None).outcomes[0].briefing_id


def test_refresh_keeps_the_last_good_briefing_when_a_run_fails(indexed):  # noqa: F811
    db, parts, clock = setup(indexed)
    keep_version(db, "haiti", "goodone0001", "2026-11-01T11:23:00+00:00", "2026-10-31")

    def broken(req):
        yield {"type": "plan", "plan": {}, "usage": None}
        raise RuntimeError("the service is overloaded")

    stub = Stub(broken)
    done = refresh(indexed, db, stub, parts, only=["haiti", "sudan"], clock=clock, progress=lambda _: None)
    assert not done.ok and done.stopped == "a briefing could not be written"
    assert [x["briefing_id"] for x in versions(db, "haiti")] == ["goodone0001"]
    assert stub.calls == 1  # the same cause would stop the next: it is not tried
    assert [o.outcome for o in done.outcomes] == ["failed: kept the previous version (see the failures in wcs usage)",
                                                  "not refreshed: a briefing could not be written"]
    assert any("overloaded" in f["error"] for f in parts.failures.recent())
    # the model declining is a failure too, and its call is paid for
    declined = Stub(lambda req: iter([{"type": "error", "message": "declined", "model": "claude-sonnet-5-5",
                                       "usage": {"input_tokens": 500, "output_tokens": 0}}]))
    done = refresh(indexed, db, declined, parts, only=["haiti"], clock=clock, progress=lambda _: None)
    assert not done.ok and versions(db, "haiti")[0]["briefing_id"] == "goodone0001" and done.spent_usd > 0


def test_refresh_keeps_the_last_good_briefing_when_no_records_are_found(indexed):  # noqa: F811
    from wcs.rag.pipeline import no_records

    db, parts, clock = setup(indexed)
    keep_version(db, "haiti", "goodone0001", "2026-11-01T11:23:00+00:00", "2026-10-31")
    stub = Stub(lambda req: iter([{"type": "plan", "plan": {}, "usage": None},
                                  {"type": "sources", "sources": [], "evidence": {}}, no_records()]))
    done = refresh(indexed, db, stub, parts, only=["haiti"], clock=clock, progress=lambda _: None)
    assert done.ok and done.outcomes[0].outcome == "no records found: kept the previous version"
    assert [x["briefing_id"] for x in versions(db, "haiti")] == ["goodone0001"]
    assert parts.archive.recent() == []  # nothing saved


def test_refresh_stops_at_a_spending_cap_and_at_its_own_limit(indexed):  # noqa: F811
    # the day's cap: below the cost a briefing is reserved at, so the first topic would reach it
    indexed.raw["usage"]["daily_spend_cap_usd"] = 0.2
    db, parts, clock = setup(indexed)
    stub = Stub(lambda req: iter([]))
    done = refresh(indexed, db, stub, parts, clock=clock, progress=lambda _: None)
    assert not done.ok and done.stopped.startswith("a spending cap would be reached")
    assert stub.calls == 0 and done.spent_usd == 0 and all(not versions(db, t) for t in TOPICS)
    assert len(done.outcomes) == 10 and all(o.outcome.startswith("not refreshed") for o in done.outcomes)
    # the run's own limit: enough for one briefing at its estimate, and no more
    indexed.raw["usage"]["daily_spend_cap_usd"] = 25.0
    db, parts, clock = setup(indexed, max_cost_usd=0.25)
    done = refresh(indexed, db, pipeline_of(indexed), parts, clock=clock, progress=lambda _: None)
    assert len([o for o in done.outcomes if o.briefing_id]) == 1  # (the topics before it found no records)
    assert done.stopped == "this run's limit of $0.25 would be reached" and not done.ok
    assert done.outcomes[-1].outcome == "not refreshed: this run's limit of $0.25 would be reached"


def test_refresh_without_a_model_writes_nothing(indexed):  # noqa: F811
    db, parts, clock = setup(indexed)

    class NoModel(Stub):
        def has_model(self):
            return False

    done = refresh(indexed, db, NoModel(lambda req: iter([])), parts, clock=clock, progress=lambda _: None)
    assert done.ok and {o.outcome for o in done.outcomes} == {"skipped: no API key"}
    with pytest.raises(ValueError, match="no such topic"):
        refresh(indexed, db, NoModel(lambda req: iter([])), parts, only=["atlantis"], clock=clock)


def test_a_briefing_written_by_the_refresh_is_shown_on_its_page(indexed):  # noqa: F811
    clock = FixedClock("2026-11-22T11:23:00+00:00")
    db = open_state(indexed, clock)
    api = TestClient(create_app(indexed, pipeline=pipeline_of(indexed), state=db, clock=clock))
    slug = "climate-change-and-climate-finance"  # the test's record is two statements on climate finance
    done = refresh(indexed, db, pipeline_of(indexed), api.app.state.parts, only=[slug], clock=clock,
                   progress=lambda _: None)
    bid = done.outcomes[0].briefing_id
    page = api.get(f"/topics/{slug}.html").text
    assert f'data-briefing="{bid}"' in page and "Briefing of 22 November 2026" in page
    assert '<li class="chip">2022 to today</li>' in page and ", Security Council, 1 March 2023</span>" in page
    assert api.get(f"/api/briefings/{bid}").status_code == 200  # what the page's script opens


# The timer, and the accessibility check's sample


def test_the_timer_runs_the_refresh_weekly_and_update_sh_installs_it():
    deploy = ROOT / "deploy"
    service = (deploy / "wcs-topics.service").read_text()
    timer = (deploy / "wcs-topics.timer").read_text()
    assert "ExecStart=/usr/bin/docker compose run --rm --no-deps -T app wcs topics refresh" in service
    assert "OnFailure=alert@%n.service" in service and "flock" not in service.split("ExecStart=")[1]
    assert "OnCalendar=Sun *-*-* 11:23:00 UTC" in timer and "Persistent=true" in timer
    update = (deploy / "update.sh").read_text()
    assert re.search(r'^UNITS="[^"]*\bwcs-topics\b[^"]*"', update, re.M)
    # clear of the site's own update, twice a day at 05:41 and 17:41 (with up to 15 minutes' delay)
    assert "05:41" in (deploy / "wcs-update.timer").read_text() and "17:41" in (deploy / "wcs-update.timer").read_text()


def test_the_sample_topic_briefing_has_the_words_the_accessibility_check_looks_for():
    script = (ROOT / "scripts" / "a11y.mjs").read_text()
    for name in ("CHECKED", "BESIDE", "UNSUPPORTED", "INVALID", "MISQUOTE", "LOADED"):
        value = re.search(rf'^const {name} = "([^"]+)";', script, re.M)[1]
        assert getattr(sample_topic, name) == value, name


def test_the_sample_is_written_as_the_first_topics_newest_version(tmp_path, monkeypatch):
    monkeypatch.setenv("WCS_DATA_DIR", str(tmp_path))
    assert sample_topic.write(tmp_path) == "/topics/war-in-ukraine.html"
    assert sample_topic.write(tmp_path) == "/topics/war-in-ukraine.html"  # again: the same briefing, kept once more
