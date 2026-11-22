"""Security Council votes (decision 30): the voting data, the drafts not adopted read from the meeting records, the
check against the UN Library's list of vetoes, the pages and the briefings' sources."""

import json
import re
import shutil
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from conftest import speech
from test_api import make
from test_pipeline import FakeClient, indexed  # noqa: F401  (the fixture)
from test_votes import title_of, votes_file

from wcs.engine import Filters
from wcs.reference.countries import countries
from wcs.sources import council
from wcs.sources.vote_result import Chunk, names, votes_in
from wcs.store.parquet import council_votes_path, votes_path, write_by_year
from wcs.votes import VoteFinder

FIXTURES = Path(__file__).parent / "fixtures" / "council"
BASE = "https://countries.example"
# the meetings in the fixtures (real records, cut down: tests/fixtures/council/), with their dates
MEETINGS = {"S/PV.9584": date(2024, 3, 22), "S/PV.9630": date(2024, 5, 20), "S/PV.9866": date(2025, 2, 24),
            "S/PV.9520": date(2023, 12, 22), "S/PV.4305": date(2001, 3, 27), "S/PV.6627": date(2011, 10, 4),
            "S/2020/870": date(2020, 8, 31), "S/PV.10224": date(2026, 9, 17)}


def chunks(symbol: str) -> list[Chunk]:
    name = re.sub(r"[^a-z0-9]+", "-", symbol.lower()).strip("-")
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    return [Chunk(i, c["chair"], c["text"], c["agenda"]) for i, c in enumerate(data["chunks"])]


def only(symbol: str) -> list:
    return [v for v in votes_in(chunks(symbol)) if v.drafts or v.what != "proposal"]


# The vote parser, on real record text

def test_a_draft_two_permanent_members_voted_against():
    [v] = votes_in(chunks("S/PV.9584"))
    assert v.drafts == ["S/2024/239"] and v.what == "draft resolution" and v.adopted is False and v.reason == "veto"
    assert v.totals == {"Y": 11, "N": 3, "A": 1} and v.complete
    assert v.lists["N"] == ["Algeria", "China", "Russian Federation"] and v.lists["A"] == ["Guyana"]
    assert "United Kingdom of Great Britain and Northern Ireland" in v.lists["Y"] and len(v.lists["Y"]) == 11
    assert v.words == ("The draft resolution received 11 votes in favour, 3 against and 1 abstention. The draft "
                       "resolution was not adopted, owing to the negative vote of a permanent member of the Council.")
    found = council.not_adopted("S/PV.9584", MEETINGS["S/PV.9584"], chunks("S/PV.9584"), countries())
    assert [f.against_p5(countries()) for f in found] == [["CHN", "RUS"]]


def test_a_draft_that_failed_for_lack_of_votes_is_not_a_veto():
    # three permanent members voted against, but the draft had only 7 votes in favour: the record says why
    [v] = votes_in(chunks("S/PV.9630"))
    assert v.drafts == ["S/2024/383"] and v.adopted is False and v.reason == "votes"
    assert v.totals == {"Y": 7, "N": 7, "A": 1} and v.complete
    assert v.words.endswith("The draft resolution has not been adopted, having failed to obtain the required number "
                            "of votes.")


def test_amendments_a_procedural_vote_and_the_draft_adopted_at_one_meeting():
    votes = votes_in(chunks("S/PV.9866"))
    assert [(v.what, v.drafts, v.reason, v.adopted) for v in votes] == [
        ("proposal", [], "votes", False),  # to adjourn: no document, so no page
        ("proposed amendment", ["S/2025/118"], "votes", False),
        ("proposed amendment", ["S/2025/114"], "votes", False),
        ("proposed amendment", ["S/2025/115"], "veto", False),
        ("proposed amendment", ["S/2025/116"], "veto", False),
        ("proposed amendment", ["S/2025/117"], "votes", False),
        ("draft resolution", ["S/2025/112"], None, True)]
    assert votes[-1].resolution == "2774 (2025)" and votes[-1].lists["N"] == []
    found = council.not_adopted("S/PV.9866", MEETINGS["S/PV.9866"], chunks("S/PV.9866"), countries())
    assert [f.draft for f in found] == ["S/2025/118", "S/2025/114", "S/2025/115", "S/2025/116", "S/2025/117"]
    assert {f.amends for f in found} == {"S/2025/112"}


def test_an_oral_amendment_has_no_document():
    oral, adopted = votes_in(chunks("S/PV.9520"))
    assert oral.what == "proposed amendment" and oral.drafts == [] and oral.reason == "veto"
    assert oral.lists["N"] == ["United States of America"]
    assert adopted.drafts == ["S/2023/1029"] and adopted.adopted and adopted.resolution == "2720 (2023)"


def test_older_wordings():
    # 2001: "The result of the voting is as follows", a member not participating, and lists ending in full stops
    [v] = only("S/PV.4305")
    assert v.drafts == ["S/2001/270"] and v.reason == "veto" and v.totals == {"Y": 9, "N": 1, "A": 4, "X": 1}
    assert v.lists["N"] == ["United States of America"] and v.complete
    # 2011: "There were 9 votes in favour", "Abstentions:", and a name the PDF split ("C olombia")
    [v] = only("S/PV.6627")
    assert v.drafts == ["S/2011/612"] and v.reason == "veto" and v.lists["A"] == ["Brazil", "India", "Lebanon",
                                                                                   "South Africa"]
    found = council.not_adopted("S/PV.6627", MEETINGS["S/PV.6627"], chunks("S/PV.6627"), countries())
    assert found[0].unknown == [] and "COL" in found[0].codes["Y"]
    # 2026: "the negative votes of two permanent members of the Security Council"
    [v] = only("S/PV.10224")
    assert v.drafts == ["S/2026/740"] and v.reason == "veto" and v.lists["N"] == ["China", "Russian Federation"]


def test_a_vote_taken_by_letter_names_only_the_members_in_brackets():
    [v] = votes_in(chunks("S/2020/870"))
    assert v.drafts == ["S/2020/852"] and v.reason == "veto" and v.totals == {"Y": 14, "N": 1, "A": 0}
    assert v.lists == {"N": ["United States of America"]} and not v.complete  # the others are in the annexes


def test_names_in_a_printed_list():
    assert names("United Kingdom of Great Britain and\n\nNorthern Ireland, United States of America") == [
        "United Kingdom of Great Britain and Northern Ireland", "United States of America"]
    assert names("None") == [] and names("Tunisia.") == ["Tunisia"]
    assert names("the United States of America") == ["United States of America"]


# The list of vetoes, and the check

def vetoes():
    return council.parse_veto_list((FIXTURES / "veto-list.html").read_text(encoding="utf-8"))


def test_the_list_of_vetoes_is_read():
    listed = vetoes()
    assert len(listed) == 13
    first = listed[0]
    assert (first.on, first.drafts, first.records, first.members) == (date(2026, 9, 17), ["S/2026/740"],
                                                                      ["S/PV.10224"], ["CHN", "RUS"])
    by_day = {v.on: v for v in listed}
    assert by_day[date(2025, 2, 24)].drafts == ["S/2025/115", "S/2025/116"]
    assert by_day[date(2023, 12, 22)].oral and by_day[date(2023, 12, 22)].drafts == ["S/2023/1029"]
    assert by_day[date(2020, 8, 31)].records == ["S/2020/870", "S/2020/865"]
    assert by_day[date(1996, 11, 19)].note.startswith("Not identified in private meeting communiqué")
    assert by_day[date(1985, 7, 26)].members == ["GBR", "USA"] and by_day[date(1985, 7, 26)].note == "*"
    assert by_day[date(1955, 12, 14)].members == ["SUN"]
    assert council.veto_frame('<iframe src="//example.org/dev/render_meeting_veto/sc_veto_list/EN">') == \
        "https://example.org/dev/render_meeting_veto/sc_veto_list/EN"


def found_in_fixtures():
    out = []
    for symbol, on in MEETINGS.items():
        out += council.not_adopted(symbol, on, chunks(symbol), countries())
    return out


def test_every_listed_veto_since_the_records_begin_is_found_with_the_same_permanent_members():
    found = found_in_fixtures()
    assert council.check_vetoes(found, vetoes(), countries(), date(2001, 1, 1)) == []
    # the private meeting of 1996 (only a communiqué, which names no one) is before then, and would not count
    assert council.check_vetoes(found, vetoes(), countries(), date(1996, 1, 1)) == []


def test_the_check_reports_a_veto_not_found_or_with_other_members_against():
    found = [f for f in found_in_fixtures() if f.draft != "S/2011/612"]
    problems = council.check_vetoes(found, vetoes(), countries(), date(2001, 1, 1))
    assert problems == ["S/2011/612 (4 October 2011, S/PV.6627): on the list of vetoes, not found in the records"]
    listed = vetoes()
    listed[0].members = ["RUS"]
    problems = council.check_vetoes(found_in_fixtures(), listed, countries(), date(2001, 1, 1))
    assert problems == ["S/2026/740 (17 September 2026, S/PV.10224): the record has CHN, RUS against; the list of "
                        "vetoes has RUS"]


# The loader, end to end

def stored_records(settings):
    """The fixtures' meetings as the stored Security Council records: the presiding officer's speeches as the
    chair's, the others as members'."""
    rows = []
    for symbol, on in MEETINGS.items():
        for i, c in enumerate(chunks(symbol)):
            rows.append(speech(f"sc:{symbol}:{i + 1:04d}", on, None if c.chair else "FRA", c.text, symbol=symbol,
                               n=i + 1, kind="chair" if c.chair else "member_state", agenda=c.agenda,
                               url=f"https://undocs.org/en/{symbol}"))
    write_by_year(settings.parquet_dir, "sc", rows)


def council_data(settings):
    raw = council.raw_dir(settings)
    raw.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "2026_10_16_sc_votes.csv", raw / "2026_10_16_sc_votes.csv")
    shutil.copy(FIXTURES / "veto-list.html", raw / "veto-list.html")
    stored_records(settings)
    return council.build(settings, countries(), docs=None)


def test_the_votes_file_holds_the_resolutions_the_drafts_and_the_vetoes_from_the_list(settings):
    built = council_data(settings)
    assert built.resolutions == 6 and built.first_record == "2001-03-27"
    assert built.problems == []
    rows = pq.read_table(council_votes_path(settings.parquet_dir)).to_pylist()
    by = {}
    for r in rows:
        by.setdefault(r["vote_id"], []).append(r)
    draft = by["draft:S/2024/239"]
    assert {r["outcome"] for r in draft} == {"not adopted"} and draft[0]["reason"] == "veto" and len(draft) == 15
    assert draft[0]["vetoed_by"] == ["CHN", "RUS"] and draft[0]["source"] == "meeting record"
    assert {r["country_code"]: r["vote"] for r in draft}["GUY"] == "A"
    assert by["draft:S/2024/383"][0]["reason"] == "votes" and by["draft:S/2024/383"][0]["vetoed_by"] == []
    # a veto from before the records begin: one row, from the list, with no member's vote
    [old] = by["draft:S/17354/Rev.1"]
    assert old["country_code"] is None and old["vote"] is None and old["vetoed_by"] == ["GBR", "USA"]
    assert old["source"] == "veto list"
    # the vote taken by letter names only some members, so its lists are not trusted: no member's vote is stored,
    # only the totals (and, for a veto, the permanent members the list of vetoes gives)
    letter = by["draft:S/2020/852"]
    assert [(r["country_code"], r["vote"]) for r in letter] == [(None, None)] and not letter[0]["complete"]
    # adopted resolutions, from the voting data
    adopted = by[next(k for k, rs in by.items() if rs[0]["resolution"] == "S/RES/2728(2024)")]
    assert len(adopted) == 15 and {r["vote"] for r in adopted if r["country_code"] == "USA"} == {"A"}
    # no oral amendment is stored: it has no document
    assert not any(r["draft"] is None and r["outcome"] == "not adopted" for r in rows)
    assert json.loads((council.raw_dir(settings) / "built.json").read_text())["oral_vetoes"] == 1


def test_the_update_builds_again_only_when_something_changed(settings, monkeypatch):
    from wcs.update import Report, update_council_votes

    council_data(settings)
    council.mark_built(settings)
    monkeypatch.setattr(council, "file_url", lambda s: "https://example.org/files/2026_10_16_sc_votes.csv")
    monkeypatch.setattr(council, "fetch_veto_list", lambda s: (_ for _ in ()).throw(AssertionError("read again")))
    report = Report(started="now")
    assert update_council_votes(settings, countries(), None, report) is False
    # a record the list names, with the wrong member against: the check reports it as the parser checks do
    listed = (FIXTURES / "veto-list.html").read_text(encoding="utf-8").replace("China<br>Russian Federation",
                                                                                 "Russian Federation", 1)
    (council.raw_dir(settings) / "veto-list.html").write_text(listed, encoding="utf-8")
    report = Report(started="now")
    assert update_council_votes(settings, countries(), None, report) is True
    assert report.problems["Security Council votes"] == [
        "S/2026/740 (17 September 2026, S/PV.10224): the record has CHN, RUS against; the list of vetoes has RUS"]
    assert report.council_votes["drafts"] >= 9


# The pages

@pytest.fixture
def site(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", BASE)
    votes_file(indexed)
    council_data(indexed)
    api, _ = make(indexed, FakeClient(), requests_per_minute=1000)
    api.app.state.countries.today = lambda: date(2026, 11, 22)
    return api


def main_of(page: str) -> str:
    return page[page.index("<main"):page.index("</main>")]


def words(page: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", main_of(page)).split())


def test_a_vetoed_draft_has_a_page_with_the_records_words(site):
    r = site.get("/votes/s-2024-239.html")
    assert r.status_code == 200
    page = r.text
    assert title_of(page) == "S/2024/239: draft resolution not adopted | Where Countries Stand"
    assert re.findall(r"<h1[ >].*?</h1>", page) == ["<h1>S/2024/239: draft resolution not adopted</h1>"]
    assert f'<link rel="canonical" href="{BASE}/votes/s-2024-239.html">' in page
    text = words(page)
    assert "“The draft resolution received 11 votes in favour, 3 against and 1 abstention. The draft resolution was " \
           "not adopted, owing to the negative vote of a permanent member of the Council.” (the meeting record)" in text
    assert "Permanent members voting against China and Russian Federation" in text
    assert "(China and the Russian Federation voted against)" in text and "commonly called a veto" in text
    assert "22 March 2024" in text and "9584th meeting" in text
    assert "The situation in the Middle East, including the Palestinian question" in text
    assert '<a href="https://undocs.org/en/S/PV.9584" data-wts-conversion="source_opened">S/PV.9584</a>' in page
    assert '<a href="https://undocs.org/en/S/2024/239" data-wts-conversion="source_opened">S/2024/239</a>' in page
    # each member under how it voted, the permanent members marked in words, linked to their pages
    lists = dict(re.findall(r'<h3 id="[a-z-]+">(.*?)</h3><ul class="vote-list"[^>]*>(.*?)</ul>', page))
    assert set(lists) == {"In favour (11)", "Against (3)", "Abstaining (1)"}
    assert lists["Against (3)"] == ('<li><a href="/countries/dza.html">Algeria</a></li><li><a href="/countries/chn.html">'
                                    'China</a> <span class="vote-list__p5">(permanent member)</span></li><li><a href="/countries/rus.html">Russian '
                                    "Federation</a> <span class=\"vote-list__p5\">(permanent member)</span></li>")
    assert "If you’re named" in page and "<!--statements-->" not in page


def test_a_resolution_page_and_one_adopted_without_a_vote(site):
    page = site.get("/votes/s-res-2728-2024.html").text
    assert title_of(page) == "S/RES/2728(2024): Ceasefire in the Gaza Strip during Ramadan | Where Countries Stand"
    text = words(page)
    assert "Adopted as resolution 2728 (2024): 14 votes in favour, none against and 1 abstention" in text
    assert "Draft S/2024/254" in text and "Meeting record S/PV.9586" in text
    assert "United States of America (permanent member)" in text.split("Abstaining (1)")[1]
    page = site.get("/votes/s-res-735-1992.html").text
    assert "adopted without a vote, so no member’s vote is recorded" in words(page)
    assert '<table class="vote-totals"' not in page
    # 1950: the USSR was not represented, as the voting data notes
    page = site.get("/votes/s-res-83-1950.html").text
    text = words(page)
    assert "The UN’s voting data notes: “Egypt and India did not participate in the voting; USSR was not " \
           "represented”" in text
    assert "Did not participate in the voting (3) Egypt India Union of Soviet Socialist Republics (permanent member)" in text


def test_a_veto_from_before_the_records_is_as_the_list_gives_it(site):
    page = site.get("/votes/s-17354-rev-1.html").text
    text = words(page)
    assert "Not adopted, owing to the negative vote of a permanent member of the Council (the United Kingdom of Great " \
           "Britain and Northern Ireland and the United States of America voted against). (the UN Library’s list of " \
           "vetoes)" in text
    assert "How each member voted is not given here" in text and "begin on 27 March 2001" in text
    assert '<ul class="vote-list"' not in page


def test_the_index_and_a_years_list(site):
    page = site.get("/security-council-votes.html").text
    assert title_of(page) == "Votes in the Security Council | Where Countries Stand"
    assert re.findall(r"<h1[ >].*?</h1>", page) == ["<h1>Votes in the Security Council</h1>"]
    failed = page[page.index('id="not-adopted"'):page.index('id="years"')]
    assert "S/2024/239: draft resolution not adopted" in failed and "S/RES/" not in failed
    assert "(China and the Russian Federation voted against)" in failed
    assert "Not adopted, having failed to obtain the required number of votes" in failed
    assert '<a href="/security-council-votes/2024.html">2024</a>' in page
    year = site.get("/security-council-votes/2024.html")
    assert year.status_code == 200 and title_of(year.text) == "Votes in the Security Council, 2024 | Where Countries Stand"
    assert '<a href="/votes/s-2024-239.html">' in year.text and '<a href="/votes/s-res-2728-2024.html">' in year.text
    # the General Assembly's index keeps its own title, and points here
    ga = site.get("/votes.html").text
    assert title_of(ga) == "Recorded votes in the General Assembly | Where Countries Stand"
    assert '<a href="/security-council-votes.html">Votes in the Security Council</a>' in ga
    home = site.get("/").text
    assert '<a href="/security-council-votes.html">Votes in the Security Council</a>' in home
    assert re.search(r'<a href="/security-council-votes.html">Votes in the Security\s+Council</a>',
                     site.get("/methodology.html").text)


def test_other_forms_of_an_address_are_sent_to_the_one_address(site):
    for asked, to in (("/votes/S-2024-239.html", "/votes/s-2024-239.html"), ("/votes/S/2024/239", "/votes/s-2024-239.html"),
                      ("/votes/s-2024-239/", "/votes/s-2024-239.html"), ("/votes/s-2024-239", "/votes/s-2024-239.html"),
                      ("/votes/S/RES/2728(2024)", "/votes/s-res-2728-2024.html"),
                      ("/security-council-votes", "/security-council-votes.html"),
                      ("/security-council-votes/2024", "/security-council-votes/2024.html"),
                      ("/security-council-votes/2024/", "/security-council-votes/2024.html")):
        r = site.get(asked, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == to, asked
    undl = next(i for i, s in site.app.state.council.catalog().by_id.items() if s == "s-res-2728-2024")
    r = site.get(f"/votes/{undl}", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/votes/s-res-2728-2024.html"
    for asked in ("/votes/s-2024-999.html", "/security-council-votes/1900.html", "/security-council-votes/x.html",
                  "/countries/fji/security-council-votes.html"):
        assert site.get(asked, follow_redirects=False).status_code == 404, asked


def test_no_council_address_is_a_general_assembly_one(site):
    ga = set(site.app.state.votes.catalog().votes)
    sc = set(site.app.state.council.catalog().votes)
    assert ga and sc and not ga & sc
    assert all(s.startswith("a-") for s in ga) and all(s.startswith("s-") for s in sc)


def sitemap_paths(site):
    paths = ["/"]
    for name in ("pages", "votes", "council-votes", "topics", "countries"):
        xml = site.get(f"/sitemap-{name}.xml").text
        paths += [loc.removeprefix(BASE) or "/" for loc in re.findall(r"<loc>(.*?)</loc>", xml)]
    return list(dict.fromkeys(paths))


def test_every_council_page_is_in_the_sitemap_and_no_two_pages_share_a_title(site):
    index = site.get("/sitemap.xml").text
    assert f"<loc>{BASE}/sitemap-council-votes.xml</loc><lastmod>2026-09-17</lastmod>" in index
    paths = sitemap_paths(site)
    for p in ("/security-council-votes.html", "/security-council-votes/2024.html", "/votes/s-2024-239.html",
              "/votes/s-res-2728-2024.html", "/votes/s-17354-rev-1.html", "/countries/usa/security-council-votes.html",
              "/countries/dza/security-council-votes.html"):
        assert p in paths, p
    xml = site.get("/sitemap-council-votes.xml").text
    assert f"<url><loc>{BASE}/votes/s-2024-239.html</loc><lastmod>2024-03-22</lastmod></url>" in xml
    seen: dict[str, str] = {}
    for path in paths:
        page = site.get(path)
        assert page.status_code == 200, path
        title = title_of(page.text)
        assert title not in seen, (path, seen.get(title))
        seen[title] = path


SITES_VOICE = re.compile(r"\bblock(?:s|ed|ing)?\b|\bkill(?:s|ed)?\b|\bthwart|\bsank\b|\bshot down\b|\btorpedo", re.I)
NO_COMPARISON = re.compile(r"%|per ?cent|voted with|same way as|agreement (?:score|rate)|alignment|aligned with|"
                           r"in line with|closest to|furthest from", re.I)


def test_the_sites_voice_is_neutral_and_compares_no_one(site):
    for path in sitemap_paths(site):
        if not (path.startswith("/votes/s-") or "security-council" in path or path.startswith("/countries/")):
            continue
        text = words(site.get(path).text)
        assert not SITES_VOICE.search(text), (path, SITES_VOICE.search(text))
        assert not NO_COMPARISON.search(text), (path, NO_COMPARISON.search(text))


def test_country_pages_have_their_council_votes(site):
    page = site.get("/countries/usa.html").text
    section = page[page.index('id="council-votes"'):page.index("All the Council’s votes")]
    assert "A permanent member of the Council." in section
    assert '<h3 id="council-not-adopted">Drafts not adopted owing to its negative vote</h3>' in section
    assert '<a href="/votes/s-2020-852.html">' in section and '<a href="/votes/s-17354-rev-1.html">' in section
    assert "Its 7 most recent Council votes" not in section  # counted and listed as the votes are
    assert re.search(r"<caption>Its votes in its last \d+ years on the Council \(", section)
    assert '<a href="/countries/usa/security-council-votes.html">Every Security Council vote of the United States of ' \
           "America, by year</a>" in section
    # an elected member: its terms, from the voting data, and the drafts it voted against
    page = site.get("/countries/dza.html").text
    assert "Elected member in 2024 to 2025, as the Council’s voting data shows it" in page
    section = page[page.index('id="council-votes"'):page.index("All the Council’s votes")]
    assert "Its terms on the Council, as its voting data shows them: 2024 to 2025." in section
    assert '<h3 id="council-not-adopted">Drafts not adopted that it voted against</h3>' in section
    assert '<a href="/votes/s-2024-239.html">' in section
    # a country that has never sat on it has no such section
    assert 'id="council-votes"' not in site.get("/countries/fji.html").text
    full = site.get("/countries/dza/security-council-votes.html")
    assert full.status_code == 200
    assert title_of(full.text) == "Algeria: votes in the Security Council | Where Countries Stand"
    assert '<h2 id="year-2024">2024</h2>' in full.text and "22 March 2024: Against." in words(full.text)


# Briefings: Council votes as sources

def test_a_question_on_the_war_in_ukraine_can_cite_a_vetoed_draft(settings):
    council_data(settings)
    shutil.copy(FIXTURES / "veto-list.html", council.raw_dir(settings) / "veto-list.html")
    finder = VoteFinder(settings, countries())
    found = finder.find("amendment Ukraine peace", Filters(date_from=date(2016, 1, 1)), 6, body="Security Council")
    assert [v["resolution"] for v in found][:1] and all(v["body"] == "Security Council" for v in found)
    vetoed = [v for v in found if v["reason"] == "veto"]
    assert {v["resolution"] for v in vetoed} == {"S/2025/115", "S/2025/116"}
    assert vetoed[0]["vetoed_by"] == ["Russian Federation"]
    from wcs.api.briefing_html import chip_label
    from wcs.rag.sources import build_sources

    [source] = build_sources([], vetoed[:1])
    card = source.card()
    assert source.title.startswith("Security Council vote on proposed amendment S/2025/11")
    assert "(not adopted) — 24 February 2025 — Maintenance of peace and security of Ukraine" in source.title
    assert any("owing to the negative vote of a permanent member of the Council" in b for b in source.blocks)
    assert "Permanent members voting against: Russian Federation." in source.blocks
    assert any(b.startswith("Against (1): Russian Federation") for b in source.blocks)
    assert card["body"] == "Security Council" and chip_label(card) == "Vote, Security Council, 24 February 2025"


def test_the_pipeline_finds_council_votes_beside_the_assemblys(indexed):  # noqa: F811
    from wcs.rag.pipeline import AskRequest, Pipeline
    from wcs.rag.query_parse import parse_offline

    votes_file(indexed)
    council_data(indexed)
    p = Pipeline(indexed, client=FakeClient(), today=date(2026, 11, 22))
    req = AskRequest(question="How have countries voted on Ukraine since 2022?")
    plan = parse_offline(req.question, p.engine.countries, date(2026, 11, 22))
    _, votes, sources = p.find(plan, req)
    bodies = [v.get("body", "General Assembly") for v in votes]
    assert "General Assembly" in bodies and "Security Council" in bodies
    assert any(s.meta.get("body") == "Security Council" for s in sources if s.kind == "vote")
    # a question limited to the General Debate gets none of the Council's
    plan.filters.sources = ["gd"]
    _, votes, _ = p.find(plan, req)
    assert all(v.get("body", "General Assembly") == "General Assembly" for v in votes)
    assert votes_path(indexed.parquet_dir).exists()


def test_the_topic_briefing_on_the_war_in_ukraine_can_cite_a_vetoed_draft(settings):
    """The topic's own question (decision 27): the place it names is enough to find the Council's votes on it, as
    the Council's agenda items name the place, not the war."""
    from wcs.rag.query_parse import parse_offline

    council_data(settings)
    question = ("What have countries and groups said about the war in Ukraine at the United Nations since 2022, and "
                "how have they voted?")
    plan = parse_offline(question, countries(), date(2026, 11, 22))
    found = VoteFinder(settings, countries()).find(plan.search_text, plan.filters, 3, body="Security Council")
    assert any(v["reason"] == "veto" and v["agenda_title"].startswith("Maintenance of peace and security of Ukraine")
               for v in found)



def test_names_with_and_are_read_whole_and_untrusted_lists_name_no_member():
    """A name with "and" in it is one member, a page number is dropped, and a list that runs on into the President's
    words, or holds a name that is no country's, names no member (the owner found three pages naming the wrong
    permanent members, 22 November 2026)."""
    c = countries()
    codes, unknown = council.segment(["Bolivia (Plurinational State of)", "United Kingdom of Great Britain and",
                                      "Northern Ireland and United States of America"], c)
    assert codes == ["BOL", "GBR", "USA"] and unknown == []
    codes, unknown = council.segment(["Saint Vincent and the Grenadines and Viet Nam", "2 Bulgaria"], c)
    assert codes == ["VCT", "VNM", "BGR"] and unknown == []
    codes, unknown = council.segment(["Senegal The President: The draft resolution has not been adopted"], c)
    assert codes == ["SEN"] and unknown == []
    codes, unknown = council.segment(["United States is begging for confrontation in the Security Council"], c)
    assert unknown


def test_a_veto_names_the_permanent_members_the_list_gives_whatever_the_record_seems_to_say(settings):
    built = council_data(settings)
    rows = pq.read_table(council_votes_path(settings.parquet_dir)).to_pylist()
    for r in rows:
        if r["reason"] == "veto" and r["source"] != "veto list" and not r["complete"]:
            assert r["country_code"] is None and r["vote"] is None
    assert built.problems == []
