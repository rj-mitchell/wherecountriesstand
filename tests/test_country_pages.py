"""A page for each country in the record, and their index (decision 29)."""

import json
import re
import sys
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from conftest import speech
from test_api import make
from test_pipeline import FakeClient, indexed  # noqa: F401  (the fixture)
from test_votes import title_of, vote, votes_file

from wcs.api.country_pages import UNLISTED, in_a_sentence, terms
from wcs.index.build import Indexer
from wcs.reference.countries import countries
from wcs.store.parquet import votes_path, write_by_year, write_votes

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import sample_topic  # noqa: E402

BASE = "https://countries.example"
TODAY = date(2026, 11, 22)


def records(settings):
    """Statements in the record: Italy's twelve of its own and one for the European Union, the Republic of China's
    and China's either side of 25 October 1971, the USSR's, Kosovo's and the two observer States'."""
    sc = [speech(f"sc:S/PV.99{i:02d}:0001", date(2025, 1 + i % 12, 1 + i), "ITA", text=f"Italy statement {i}. " * 20,
                 symbol=f"S/PV.99{i:02d}", url=f"https://undocs.org/en/S/PV.99{i:02d}", participant="guest")
          for i in range(12)]
    sc += [speech("sc:S/PV.9950:0002", date(2025, 12, 30), "ITA", "On behalf of the European Union, we call for "
                  "restraint.", behalf="European Union", symbol="S/PV.9950", participant="guest"),
           speech("sc:S/PV.9951:0003", date(2025, 9, 1), "XKX", "Kosovo statement.", kind="other_state",
                  symbol="S/PV.9951", participant="guest"),
           speech("sc:S/PV.9952:0004", date(2025, 3, 2), "PSE", "Palestine statement.", kind="observer_state",
                  symbol="S/PV.9952", participant="guest"),
           speech("sc:S/PV.7900:0004", date(2017, 3, 2), "ITA", "Italy at the table.", symbol="S/PV.7900")]
    gd = [speech("gd:25:CHN", date(1970, 10, 1), "CHN", "Republic of China statement in 1970.", source="gd",
                 url="https://undocs.org/en/A/PV.1870"),
          speech("gd:26:CHN", date(1971, 11, 15), "CHN", "China statement in 1971.", source="gd",
                 url="https://undocs.org/en/A/PV.1983"),
          speech("gd:40:SUN", date(1985, 9, 24), "SUN", "USSR statement.", source="gd", kind="former_member_state",
                 url="https://undocs.org/en/A/40/PV.5"),
          speech("gd:79:VAT", date(2024, 9, 28), "VAT", "Holy See statement.", source="gd", kind="observer_state",
                 url="https://undocs.org/en/A/79/PV.12")]
    write_by_year(settings.parquet_dir, "sc", sc)
    write_by_year(settings.parquet_dir, "gd", gd)
    Indexer(settings, countries()).index_all()  # so that a record can be hidden


def older_votes():
    """Votes before and after 25 October 1971, and the USSR's last ones."""
    rows = vote("70001", "A/RES/2500(XXIV)", date(1969, 11, 20), "An older vote : resolution / adopted by the General "
                "Assembly", {"CHN": "Y", "SUN": "N", "ITA": "A"}, session=24)
    rows += vote("70002", "A/RES/2800(XXVI)", date(1971, 12, 1), "A later vote : resolution / adopted by the General "
                 "Assembly", {"CHN": "N", "SUN": "Y", "ITA": "Y"}, session=26)
    rows += vote("70003", "A/RES/46/1", date(1991, 12, 10), "The USSR's last vote : resolution / adopted by the "
                 "General Assembly", {"SUN": "Y", "ITA": "Y", "CHN": "Y"}, session=46)
    return rows


@pytest.fixture
def site(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", BASE)
    votes_file(indexed)
    rows = pq.read_table(votes_path(indexed.parquet_dir)).to_pylist()
    write_votes(indexed.parquet_dir, rows + older_votes())
    records(indexed)
    sample_topic.write(indexed.data_dir)  # cites Kenya's statement
    api, _ = make(indexed, FakeClient(), requests_per_minute=1000)
    api.app.state.countries.today = lambda: TODAY
    return api


def main_of(page: str) -> str:
    return page[page.index("<main"):page.index("</main>")]


def words(page: str) -> str:
    text = re.sub(r"<[^>]+>", " ", main_of(page))
    return " ".join(text.split())


def test_a_member_state_has_a_page_of_its_own(site):
    r = site.get("/countries/ita.html")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    page = r.text
    assert title_of(page) == "Italy | Where Countries Stand"
    assert re.findall(r"<h1[ >].*?</h1>", page) == ["<h1>Italy</h1>"]
    assert f'<link rel="canonical" href="{BASE}/countries/ita.html">' in page
    assert "noindex" not in page and "__SITE__" not in page and "<!--#" not in page
    data = json.loads(re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)[0])
    assert [i["name"] for i in data["itemListElement"]] == ["Where Countries Stand", "Countries in the record",
                                                            "Italy"]
    assert "<script src=" not in page  # written on the server: nothing to run
    text = words(page)
    # the status in the UN's words: the site holds no date of admission for today's members, so none is given
    assert "Member State of the United Nations." in text and "since" not in text.split("Groups on")[0]
    # its groups on today's date, with the Security Council terms the meeting records hold
    assert "Groups on 22 November 2026" in text
    for row in ("<dt>Region</dt><dd>Europe, Southern Europe</dd>",
                "<dt>UN regional group</dt><dd>Western European and other States</dd>",
                "<dt>Security Council</dt><dd>Elected member in 2017, as the Council’s meeting records since 1992 "
                "show it</dd>",
                "<dt>Other groups</dt><dd>European Union member States<br>NATO members</dd>",
                "<dt>Income group</dt><dd>High-income economies (World Bank)</dd>"):
        assert row in page, row
    assert page.index('id="groups"') < page.index('id="ask-head"') < page.index('id="statements"') \
        < page.index('id="votes"')


def test_the_ask_form_works_without_a_script_and_names_the_country(site):
    page = site.get("/countries/ita.html").text
    form = page[page.index('<form class="ask panel country-ask"'):page.index("</form>")]
    assert 'action="/" method="get"' in form
    assert '<textarea id="q" name="q"' in form and ">What has Italy said about </textarea>" in form
    # the same two choices as the home page, the second sending what the home page reads as sources only
    assert '<button type="submit">Prepare briefing</button>' in form
    assert '<button type="submit" class="button--quiet" name="sources" value="1">Sources only (no AI)</button>' in form
    assert "<script" not in form
    assert ">What has the Russian Federation said about </textarea>" in site.get("/countries/rus.html").text
    assert in_a_sentence("Holy See") == "the Holy See" and in_a_sentence("Niger") == "the Niger"
    assert in_a_sentence("Nigeria") == "Nigeria" and in_a_sentence("Democratic Yemen") == "Democratic Yemen"


def test_its_latest_statements_are_its_own_newest_first(site):
    page = site.get("/countries/ita.html").text
    found = re.findall(r'<li class="country-statement"><p class="country-statement__meta">(.*?)</p>', page)
    assert len(found) == 10
    assert found[0].startswith("12 December 2025, Security Council, 9000th meeting, "
                               '<a href="https://undocs.org/en/S/PV.9911" data-wts-conversion="source_opened">'
                               "S/PV.9911</a>")
    assert "Italy statement 11. Italy statement 11." in page and "…" in page  # the first words only
    assert '<a href="https://undocs.org/en/S/PV.9911" data-wts-conversion="source_opened">Read S/PV.9911</a>' in page
    assert "Maintenance of international peace and security" in page
    # a statement made on behalf of a group is the group's (decision 7)
    assert "we call for restraint" not in page
    # a record hidden while a concern is decided is left out
    site.app.state.replies.hide("record:sc:S/PV.9911:0001", "Hidden while a concern is decided.")
    page = site.get("/countries/ita.html").text
    assert "S/PV.9911" not in page and "S/PV.9901" in page
    assert "No statements in the record." in site.get("/countries/fra.html").text


def test_its_recorded_votes_are_counted_and_listed_and_never_compared(site):
    page = site.get("/countries/gbr.html").text
    section = page[page.index('id="votes"'):]
    assert "<caption>Its recorded votes from 22 November 2016 to 22 November 2026</caption>" in section
    assert '<tr><th scope="row">In favour</th><td>1</td></tr>' in section
    assert '<tr><th scope="row">Abstaining</th><td>1</td></tr>' in section
    assert '<tr><th scope="row">Against</th><td>0</td></tr>' in section
    assert "Its 2 most recent recorded votes" in section
    assert ('<a href="/votes/a-res-79-81.html">A/RES/79/81: Peaceful settlement of the question of Palestine</a>'
            '<span class="vote-index__meta">3 December 2024. Its vote: Abstaining. Result: adopted by 5 votes to 1, '
            "with 1 abstention.</span>") in section
    assert '<a href="/countries/gbr/votes.html">Every recorded vote of the United Kingdom of Great Britain and ' \
           "Northern Ireland, by session</a>: 2 votes, from 2 March 2022." in section
    full = site.get("/countries/gbr/votes.html")
    assert full.status_code == 200
    assert title_of(full.text) == ("United Kingdom of Great Britain and Northern Ireland: recorded votes in the "
                                   "General Assembly | Where Countries Stand")
    assert '<h2 id="session-79">79th session</h2>' in full.text and \
           '<h2 id="session-es-11">11th emergency special session</h2>' in full.text
    assert full.text.index("session-79") < full.text.index("session-es-11")


NO_COMPARISON = re.compile(r"%|per ?cent|voted with|voted as|same way as|agreement (?:score|rate)|alignment|"
                           r"aligned with|coincidence|similarity|in common with|score|in line with|closest to|"
                           r"furthest from", re.I)


def test_no_page_compares_one_countrys_votes_with_anothers(site):
    paths = ["/countries.html"] + [loc.removeprefix(BASE) for loc in
                                   re.findall(r"<loc>(.*?)</loc>", site.get("/sitemap-countries.xml").text)]
    assert len(paths) > 200
    for path in dict.fromkeys(paths):
        text = words(site.get(path).text)
        found = NO_COMPARISON.findall(text)
        assert not found, (path, found)


def test_an_observer_state_does_not_vote_and_has_its_status(site):
    pse = site.get("/countries/pse.html").text
    text = words(pse)
    assert "Non-member observer State of the United Nations since 29 November 2012." in text
    assert "As a non-member observer State, it does not vote in the General Assembly." in text
    assert "Palestine statement." in pse and "/countries/pse/votes.html" not in pse
    assert ">What has the State of Palestine said about </textarea>" in pse
    vat = site.get("/countries/vat.html").text
    assert "Non-member observer State of the United Nations." in words(vat)
    assert "As a non-member observer State, it does not vote in the General Assembly." in words(vat)
    assert site.get("/countries/pse/votes.html").status_code == 404


def test_a_former_member_state_shows_its_record_to_its_last_day(site):
    page = site.get("/countries/sun.html").text
    text = words(page)
    assert title_of(page) == "Union of Soviet Socialist Republics | Where Countries Stand"
    assert "Former Member State, 1945 to 1991." in text
    assert "The Russian Federation continued the USSR’s membership of the United Nations" in text
    assert 'Successor: <a href="/countries/rus.html">Russian Federation</a>.' in page
    assert "Groups on its last day as a Member State, 23 December 1991" in text
    assert "<dt>Security Council</dt><dd>Permanent member</dd>" in page
    assert "<caption>Its recorded votes from 23 December 1981 to 23 December 1991</caption>" in page
    assert "Its last 3 recorded votes" in text and "A/RES/46/1" in page
    assert "a-res-79-81" not in page  # nothing after its last day
    assert "USSR statement." in page
    assert ">Between 1945 and 1991, what did the Union of Soviet Socialist Republics say about </textarea>" in page
    assert site.get("/countries/sun/votes.html").status_code == 200


def test_kosovo_has_a_page_under_the_records_own_name(site):
    page = site.get("/countries/xkx.html").text
    text = words(page)
    assert title_of(page) == "Kosovo | Where Countries Stand"
    assert "Not a member of the United Nations." in text
    assert "Kosovo has no vote in the General Assembly" in text and "Kosovo statement." in text
    assert site.get("/countries/xkx/votes.html").status_code == 404
    index = site.get("/countries.html").text
    others = index[index.index('id="others"'):index.index('id="no-page"')]
    assert '<a href="/countries/xkx.html">Kosovo</a>' in others
    assert "/countries/xkx.html" not in index[:index.index('id="others"')]


def test_the_sister_link_is_for_members_palestine_and_kosovo_only(site):
    link = re.compile(r'<a href="https://wherelivesstand.com/country/([A-Z]{3})"><svg class="wts-spine '
                      r'wts-cloth--lives"[^>]*>.*?</svg>This country on Where Lives Stand</a>', re.S)
    for code, shown in (("ita", True), ("pse", True), ("xkx", True), ("vat", False), ("sun", False),
                        ("ddr", False)):
        page = site.get(f"/countries/{code}.html").text
        found = link.findall(page)
        assert found == ([code.upper()] if shown else []), code
        assert ("Where Lives Stand</a>" in main_of(page)) is shown, code


def test_topic_briefings_that_cite_a_country_are_linked(site):
    ken = site.get("/countries/ken.html").text
    assert '<h2 id="topics">Topic briefings that cite it</h2>' in ken
    assert '<a href="/topics/war-in-ukraine.html">' in ken
    assert 'id="topics"' not in site.get("/countries/ita.html").text


def test_its_published_statements_are_shown(site):
    replies = site.app.state.replies
    assert 'id="right-of-reply"' not in site.get("/countries/ken.html").text
    r = site.post("/api/statements", json={
        "page": "/votes/a-res-79-81.html", "name": "Ms. Amina Odhiambo",
        "role": "Deputy Permanent Representative of Kenya to the United Nations",
        "text": "Kenya voted in favour, as it has since 1993.", "link": "", "email": "press@kenyamission.example"})
    replies.publish(r.json()["id"], checked="Replied from the mission's published address.", about="KEN")
    page = site.get("/countries/ken.html").text
    assert '<h2 id="right-of-reply">Statements in their own words</h2>' in page
    assert "Statement from Ms. Amina Odhiambo" in page and "data-nosnippet" in page
    assert 'Published beside <a href="/votes/a-res-79-81.html">the vote’s page</a>.' in page
    assert "In their own words. Not checked or endorsed by Where Countries Stand." in page


def test_chinas_seat_before_25_october_1971_has_a_section_of_its_own(site):
    page = site.get("/countries/chn.html").text
    head = '<h2 id="period-head">China’s seat, 1945 to 1971: the Republic of China</h2>'
    assert head in page
    main, period = page[:page.index(head)], page[page.index(head):page.index("</section>", page.index(head))]
    # the main sections begin on 25 October 1971
    assert "China statement in 1971." in main and "Republic of China statement in 1970." not in main
    assert "A/RES/2800(XXVI)" in main and "A/RES/2500(XXIV)" not in main
    # the section holds that period's statements and votes, labelled as decision 5 labels them
    assert "Republic of China statement in 1970." in period and "China statement in 1971." not in period
    assert "Recorded as China (Republic of China)." in period
    assert "A/RES/2500(XXIV)" in period and "A/RES/2800(XXVI)" not in period
    assert "Recorded as China (Republic of China), before 25 October 1971." in period
    # the counts of the last 10 years are unaffected
    assert "<caption>Its recorded votes from 22 November 2016 to 22 November 2026</caption>" in main
    # the full list marks the votes before 25 October 1971
    full = site.get("/countries/chn/votes.html").text
    marked = re.findall(r'<li><a href="/votes/([a-z0-9-]+)\.html">[^<]*</a> ?<span class="vote-index__meta">([^<]*)'
                        r"</span>", full)
    before = {slug for slug, meta in marked if "Recorded as China (Republic of China), before 25 October 1971" in meta}
    assert before == {"a-res-2500-xxiv"}
    # no page for Taiwan or the Republic of China, and neither is called a former Member State
    for path in ("/countries/twn.html", "/countries/roc.html", "/countries/twn/votes.html"):
        assert site.get(path).status_code == 404, path


def test_taiwan_and_the_republic_of_china_are_never_called_former_members(site):
    pages = [site.get(p).text for p in ("/countries.html", "/countries/chn.html", "/countries/chn/votes.html")]
    for page in pages:
        text = words(page)
        for m in re.finditer(r"Taiwan|Republic of China", text):
            near = text[max(0, m.start() - 120):m.end() + 120]
            assert not re.search(r"former", near, re.I), near
    index = pages[0]
    former = index[index.index('id="former"'):index.index('id="others"')]
    assert "Taiwan" not in former and "Republic of China" not in former and "China" not in former


def test_the_index_lists_every_place_with_a_page(site):
    r = site.get("/countries.html")
    assert r.status_code == 200
    page = r.text
    assert title_of(page) == "Countries in the record | Where Countries Stand"
    assert re.findall(r"<h1[ >].*?</h1>", page) == ["<h1>Countries in the record</h1>"]
    az = page[page.index('id="a-z"'):page.index('id="regions"')]
    assert az.count("<li>") == 195
    assert '<li><a href="/countries/pse.html">State of Palestine</a><span class="country-list__status">, non-member ' \
           "observer State</span></li>" in az  # the status in words, not by colour
    assert '<a href="/countries/sun.html">' not in az and '<a href="/countries/xkx.html">' not in az
    for region in ("africa", "americas", "asia", "europe", "oceania"):
        assert f'<h3 id="region-{region}">' in page and f'href="#region-{region}"' in page
    europe = page[page.index('id="region-europe"'):page.index('id="region-oceania"')]
    assert '<a href="/countries/ita.html">Italy</a>' in europe and "/countries/ken.html" not in europe
    former = page[page.index('id="former"'):page.index('id="others"')]
    assert former.count("<li>") == 8
    assert ('<li><a href="/countries/sun.html">Union of Soviet Socialist Republics</a><span class="country-list__status">'
            ", 1945 to 1991</span></li>") in former
    assert page.index('id="former"') < page.index('id="others"') < page.index('id="no-page"')
    assert "implies no view on any place’s status" in page
    # in the menu, after Topics; the footer keeps the family's six links
    menu = page[page.index('<nav class="sections"'):]
    assert re.findall(r'href="([^"]+)"', menu[:menu.index("</nav>")]) == [
        "/", "/topics.html", "/countries.html", "/about.html", "/methodology.html", "/glossary.html"]
    assert '<a href="/countries.html" aria-current="page">Countries</a>' in page
    assert '<a href="/countries.html">Countries in the record</a>' in site.get("/").text


def test_the_places_with_no_page_have_a_sentence_and_a_search(site):
    page = site.get("/countries.html").text
    section = page[page.index('<h2 id="no-page">Places with no page of their own</h2>'):]
    assert "This follows the UN’s own lists and records, and implies no view on any place’s status." in section
    for slug, name, sentence in UNLISTED:
        assert f'<h3 id="no-page-{slug}">{name}</h3><p>{sentence}</p>' in section
    assert ("China’s seat at the United Nations was held by the Republic of China until 25 October 1971 (General "
            "Assembly resolution 2758 (XXVI)). Taiwan has not spoken or voted in the United Nations’ records since."
            ) in section
    assert section.count("A Special Administrative Region of China. It does not speak or vote separately in the "
                         "United Nations’ records.") == 2
    assert ("A Non-Self-Governing Territory on the United Nations’ list. It is discussed in the records but does not "
            "speak or vote in them.") in section
    # a search of the records, sources only, as the home page's form sends one with no script
    assert '<a href="/?q=What+has+been+said+about+Taiwan%3F&amp;sources=1">Search the records about Taiwan</a>' \
           in section
    assert '<a href="/?q=What+has+been+said+about+Western+Sahara%3F&amp;sources=1">' in section
    for code in ("twn", "hkg", "mac", "esh"):
        r = site.get(f"/countries/{code}.html", follow_redirects=False)
        assert r.status_code == 404 and "<h1>Page not found</h1>" in r.text, code
        assert f"/countries/{code}.html" not in site.get("/sitemap-countries.xml").text


def test_other_forms_of_a_countrys_address_are_sent_to_its_own_for_good(site):
    for asked, sent in (("/countries", "/countries.html"), ("/countries/", "/countries.html"),
                        ("/countries.html/", "/countries.html"),
                        ("/countries/ita", "/countries/ita.html"), ("/countries/ita/", "/countries/ita.html"),
                        ("/countries/ita.html/", "/countries/ita.html"), ("/countries/ITA.html", "/countries/ita.html"),
                        ("/countries/ITA", "/countries/ita.html"), ("/countries/Ita.HTML", "/countries/ita.html"),
                        ("/countries/sun", "/countries/sun.html"),
                        ("/countries/gbr/votes", "/countries/gbr/votes.html"),
                        ("/countries/GBR/votes.html", "/countries/gbr/votes.html"),
                        ("/countries/gbr/votes/", "/countries/gbr/votes.html"),
                        ("/countries/ita?from=list", "/countries/ita.html?from=list")):
        for method in (site.get, site.head):
            r = method(asked, follow_redirects=False)
            assert (r.status_code, r.headers["location"]) == (301, sent), asked


def test_an_unknown_country_is_not_found(site):
    for path in ("/countries/zzz.html", "/countries/zzz", "/countries/it.html", "/countries/italy.html",
                 "/countries/twn.html", "/countries/hkg", "/countries/pse/votes.html", "/countries/vat/votes",
                 "/countries/ita/other.html", "/countries/%3Cscript%3E.html"):
        r = site.get(path, follow_redirects=False)
        assert r.status_code == 404 and "<h1>Page not found</h1>" in r.text, path


def test_no_two_pages_on_the_site_share_a_title(site):
    seen: dict[str, str] = {}
    paths = ["/"]
    for name in ("pages", "votes", "topics", "countries"):
        paths += [loc.removeprefix(BASE) or "/" for loc in
                  re.findall(r"<loc>(.*?)</loc>", site.get(f"/sitemap-{name}.xml").text)]
    assert "/countries/ita.html" in paths and "/countries/gbr/votes.html" in paths
    for path in dict.fromkeys(paths):
        page = site.get(path)
        assert page.status_code == 200, path
        title = title_of(page.text)
        assert title not in seen, (path, seen.get(title))
        seen[title] = path


def test_the_sitemap_lists_the_country_pages_with_dates_from_the_record(site):
    index = site.get("/sitemap.xml").text
    assert re.findall(r"<loc>(.*?)</loc>", index)[-1] == f"{BASE}/sitemap-countries.xml"
    xml = site.get("/sitemap-countries.xml").text
    assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<urlset ')
    for line in (f"<url><loc>{BASE}/countries.html</loc><lastmod>2025-12-12</lastmod></url>",
                 f"<url><loc>{BASE}/countries/ita.html</loc><lastmod>2025-12-12</lastmod></url>",
                 f"<url><loc>{BASE}/countries/gbr.html</loc><lastmod>2024-12-03</lastmod></url>",
                 f"<url><loc>{BASE}/countries/gbr/votes.html</loc><lastmod>2024-12-03</lastmod></url>",
                 f"<url><loc>{BASE}/countries/xkx.html</loc><lastmod>2025-09-01</lastmod></url>",
                 f"<url><loc>{BASE}/countries/tza.html</loc></url>"):  # nothing in the record: no date
        assert line in xml, line
    assert xml.count("<url>") == 1 + 204 + len(re.findall(r"/votes\.html</loc>", xml))
    assert "/countries/pse/votes.html" not in xml and "/countries/hkg.html" not in xml


def test_a_votes_page_links_each_country_to_its_page(site):
    page = site.get("/votes/a-res-es-11-1.html").text
    assert '<li><a href="/countries/rus.html">Russian Federation</a></li>' in page
    assert '<li><a href="/countries/ven.html">Venezuela (Bolivarian Republic of)</a></li>' in page


def test_the_security_council_terms_are_joined_into_runs():
    assert terms([1995, 1996, 2007, 2008, 2017]) == ["1995 to 1996", "2007 to 2008", "2017"]
    assert terms([]) == []


def test_with_nothing_read_in_the_pages_still_stand(indexed, monkeypatch):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    assert api.get("/countries.html").status_code == 200
    page = api.get("/countries/ita.html").text
    assert "No recorded votes in the record." in page
    assert api.get("/countries/ita/votes.html").status_code == 404
