"""The rest of the General Assembly's plenary record (decision 31): plenary statements as source "ga", explanations
of vote linked to the votes they explain, their pages, the backfill's store and the search."""

import json
import re
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from conftest import speech
from test_api import make
from test_pipeline import FakeClient, indexed  # noqa: F401  (the fixture)
from test_votes import title_of, vote, votes_file

from wcs.engine import Engine, Filters
from wcs.index.build import Indexer
from wcs.rag.query_parse import parse_offline
from wcs.reference.countries import countries
from wcs.sources import undocs, unga
from wcs.sources.verbatim import Meeting, Speech
from wcs.store.parquet import read_year, record_path, votes_path, write_by_year, write_votes
from wcs.update import live_plenary_sessions, plenary_prefix, stale_years, store_plenary

FIXTURES = Path(__file__).parent / "fixtures" / "plenary"
BASE = "https://countries.example"


def meeting(name: str) -> Meeting:
    """A plenary meeting as the parser reads it, from a fixture of its real record (cut down)."""
    d = json.loads((FIXTURES / f"{name}.json").read_text())
    return Meeting(d["symbol"], date.fromisoformat(d["date"]), speeches=[Speech(**s) for s in d["speeches"]])


# Explanations of vote, on real record text

def test_explanations_are_linked_to_the_votes_they_name_or_the_one_vote_of_their_round():
    m = meeting("a-79-pv-46")
    found = unga.explanations(m)
    who = {i: m.speeches[i].label for i in found}
    assert {who[i]: (e.when, e.votes) for i, e in found.items()} == {
        # names both resolutions voted on at the meeting under the item
        "Islamic Republic of Iran": ("before", ["A/RES/79/81", "A/RES/79/82"]),
        "Germany": ("after", ["A/RES/79/81"]), "Canada": ("after", ["A/RES/79/81"]),
        "Australia": ("after", ["A/RES/79/81"]),
        # Hungary's, on behalf of the European Union, after the vote on the other item's resolution
        "Hungary": ("after", ["A/RES/79/83"])}
    assert all(e.certain for e in found.values())


def test_an_emergency_special_session_with_explanations_before_and_after_the_vote():
    m = meeting("a-es-11-pv-5")
    found = unga.explanations(m)
    after = [m.speeches[i].label for i, e in sorted(found.items()) if e.when == "after"]
    before = [m.speeches[i].label for i, e in sorted(found.items()) if e.when == "before"]
    assert len(after) == 20 and after[:3] == ["Tunisia", "Rwanda", "China"]
    assert before == ["Russian Federation", "Syrian Arab Republic", "Saint Vincent and the Grenadines"]
    assert {v for e in found.values() for v in e.votes} == {"A/RES/ES-11/1"}
    # one the fixture cuts before it names the draft, in a round with a call for explanations of position: not placed
    assert [e.votes for e in found.values() if e.when == "position"] == [[]]


def test_plenary_records_are_labelled_and_the_presidents_words_are_not_statements(settings):
    m = meeting("a-79-pv-46")
    rows = unga.plenary_records_for(m, {}, countries(), settings)
    assert all(r["source"] == "ga" and r["body"] == "General Assembly" for r in rows)
    assert {r["meeting"] for r in rows} == {"79th session, 46th plenary meeting"}
    assert not any(r["speaker_name"].startswith("The President") or r["speaker_name"].startswith("The Acting")
                   for r in rows)
    eu = next(r for r in rows if r["on_behalf_of"] == "European Union")
    assert eu["country_code"] == "HUN" and eu["eov"] == "after" and eu["explains"] == "A/RES/79/83"
    canada = next(r for r in rows if r["country_code"] == "CAN")
    assert canada["agenda"] == "Agenda item 35: Question of Palestine" and canada["record_id"].startswith("ga:A/79/PV.46:")
    assert unga.eov_counts(rows) == {"placed": 5, "drafts_only": 0, "unplaced": 0, "positions": 0}
    es = unga.plenary_records_for(meeting("a-es-11-pv-5"), {}, countries(), settings)
    assert {r["meeting"] for r in es} == {"11th emergency special session, 5th plenary meeting"}
    assert unga.plenary_label("A/S-27/PV.3") == "27th special session, 3rd plenary meeting"
    assert unga.plenary_session("A/80/PV.45") == ("80th session", 80) and unga.plenary_session("S/PV.1") is None


def test_the_sessions_the_update_and_the_backfill_look_at():
    assert live_plenary_sessions(date(2026, 11, 22)) == ["81", "80", "es-10", "es-11"]
    assert plenary_prefix("es-11") == "A/ES-11/PV." and plenary_prefix("s-27") == "A/S-27/PV."
    assert plenary_prefix("80") == "A/80/PV."


# The manifest shared by two jobs, the store and the index

def test_two_jobs_share_one_manifest_without_losing_each_others_entries(settings):
    a = undocs.Manifest.load(settings, "ga")
    b = undocs.Manifest.load(settings, "ga")
    a.entries["A/79/PV.46"] = {"status": "parsed"}
    a.save()
    b.entries["A/50/PV.82"] = {"status": "parsed"}
    b.save()
    assert set(b.entries) == {"A/79/PV.46", "A/50/PV.82"}
    assert set(undocs.Manifest.load(settings, "ga").entries) == {"A/79/PV.46", "A/50/PV.82"}
    # one job's change to an entry is kept when the other saves its own
    a.entries["A/79/PV.46"]["stored_ga"] = True
    a.save()
    b.entries["A/50/PV.82"]["stored_ga"] = False
    b.save()
    assert undocs.Manifest.load(settings, "ga").entries["A/79/PV.46"] == {"status": "parsed", "stored_ga": True}


def parsed_meeting(settings, name: str) -> list[dict]:
    m = meeting(name)
    rows = unga.plenary_records_for(m, {}, countries(), settings)
    manifest = undocs.Manifest.load(settings, "ga")
    manifest.entries[m.symbol] = {"status": "parsed", "plenary_version": unga.PLENARY_VERSION, "stored_ga": False,
                                  "date": m.meeting_date.isoformat()}
    manifest.save()
    out = undocs.cache_dir(settings) / "parsed" / f"{undocs.safe_name(m.symbol)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, default=str))
    return rows


def test_the_store_writes_each_year_once_and_the_update_indexes_what_the_backfill_wrote(settings):
    rows = parsed_meeting(settings, "a-79-pv-46") + parsed_meeting(settings, "a-es-11-pv-5")
    assert store_plenary(settings) == {2022: 42, 2024: 22}
    assert len(read_year(settings.parquet_dir, "ga", 2024)) == 22 and len(rows) == 64
    assert store_plenary(settings) == {}  # nothing left unstored
    assert all(e["stored_ga"] for e in undocs.Manifest.load(settings, "ga").entries.values())
    assert stale_years(settings, "ga") == {2022, 2024}
    Indexer(settings, countries()).index_year("ga", 2024)
    assert stale_years(settings, "ga") == {2022}
    assert record_path(settings.parquet_dir, "ga", 2024).exists()


# Searching the plenary record

def test_a_plenary_statement_is_found_and_told_apart_from_a_general_debate_one(settings):
    parsed_meeting(settings, "a-es-11-pv-5")
    store_plenary(settings)
    write_by_year(settings.parquet_dir, "gd", [speech("gd:76:UKR", date(2021, 9, 23), "UKR",
                                                      "Ukraine speaks of the war in Donbas and sovereignty.",
                                                      source="gd")])
    Indexer(settings, countries()).index_all()
    engine = Engine(settings, today=date(2026, 11, 22))
    found = engine.search("sovereignty Ukraine", Filters(sources=["ga"]), limit=6)
    assert found.records and all(r["source"] == "ga" for r in found.records)
    assert found.records[0]["meeting"] == "11th emergency special session, 5th plenary meeting"
    both = engine.search("sovereignty Ukraine", Filters(), limit=30, diversify=False)
    assert {r["source"] for r in both.records} == {"ga", "gd"}
    from wcs.rag.sources import record_source

    title = record_source(1, found.records[0]).title
    assert "General Assembly, 11th emergency special session, 5th plenary meeting, 2 March 2022" in title
    assert "Agenda item 5: Letter dated 28 February 2014" in title


def test_the_general_assembly_named_in_a_question_is_both_its_debates():
    ref = countries()
    assert parse_offline("What did countries say in the General Assembly about Ukraine?", ref,
                         date(2026, 11, 22)).filters.sources == ["gd", "ga"]
    assert parse_offline("What did countries say in the General Debate about Ukraine?", ref,
                         date(2026, 11, 22)).filters.sources == ["gd"]


# The pages

@pytest.fixture
def site(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", BASE)
    votes_file(indexed)
    rows = pq.read_table(votes_path(indexed.parquet_dir)).to_pylist()
    # the votes at the two meetings, as the voting data gives them
    rows += vote("4069010", "A/RES/79/81", date(2024, 12, 3), "Peaceful settlement of the question of Palestine : "
                 "resolution / adopted by the General Assembly", {"DEU": "Y", "CAN": "Y", "AUS": "Y", "IRN": "Y",
                                                                   "USA": "N"}, session=79, draft="A/79/L.23",
                 meeting="A/79/PV.45", agenda="Question of Palestine.")
    rows = [r for r in rows if r["undl_id"] != "4069004"]
    write_votes(indexed.parquet_dir, rows)
    parsed_meeting(indexed, "a-79-pv-46")
    parsed_meeting(indexed, "a-es-11-pv-5")
    store_plenary(indexed)
    api, _ = make(indexed, FakeClient(), requests_per_minute=1000)
    api.app.state.countries.today = lambda: date(2026, 11, 22)
    return api


def words(page: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", page[page.index("<main"):page.index("</main>")]).split())


def test_a_vote_page_lists_the_explanations_of_vote(site):
    page = site.get("/votes/a-res-es-11-1.html").text
    assert '<h2 id="explanations">Explanations of vote</h2>' in page
    section = page[page.index('id="explanations"'):page.index("</ol>", page.index('id="explanations"'))]
    assert "23 statements" in section
    assert '<a href="/countries/tun.html">Tunisia</a>. After the vote, A/ES-11/PV.5' in section
    assert "Russian Federation</a>. Before the vote, A/ES-11/PV.5" in section
    assert 'data-wts-conversion="source_opened">Read it in A/ES-11/PV.5</a>' in section
    # a vote at another meeting whose record names the meeting wrongly: linked by its resolution, not its meeting
    page = site.get("/votes/a-res-79-81.html").text
    section = page[page.index('id="explanations"'):]
    assert section.count('class="country-statement"') == 4  # Iran before; Germany, Canada and Australia after
    assert "On behalf of the European Union" not in section  # its explanation is of 79/83, which has no page here


def test_a_countrys_page_has_its_plenary_statements_and_its_explanations_of_vote(site):
    page = site.get("/countries/can.html").text
    assert '<h2 id="explanations">Its explanations of vote</h2>' in page
    section = page[page.index('id="explanations"'):page.index("</ol>", page.index('id="explanations"'))]
    assert "3 December 2024, after the vote on <a href=\"/votes/a-res-79-81.html\">A/RES/79/81</a>" in section
    latest = page[page.index('id="statements"'):page.index('id="votes"')]
    assert "General Assembly, 79th session, 46th plenary meeting" in latest
    assert "Agenda item 35: Question of Palestine" in latest
    # a group's explanation is the group's, not the member's that spoke for it (decision 7)
    assert 'id="explanations"' not in site.get("/countries/hun.html").text


def test_no_two_pages_share_a_title_with_the_plenary_record_loaded(site):
    seen: dict[str, str] = {}
    paths = ["/"]
    for name in ("pages", "votes", "council-votes", "topics", "countries"):
        paths += [loc.removeprefix(BASE) or "/" for loc in
                  re.findall(r"<loc>(.*?)</loc>", site.get(f"/sitemap-{name}.xml").text)]
    for path in dict.fromkeys(paths):
        page = site.get(path)
        assert page.status_code == 200, path
        title = title_of(page.text)
        assert title not in seen, (path, seen.get(title))
        seen[title] = path
