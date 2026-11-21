"""A page for each General Assembly recorded vote, each session's list and their index (decision 26)."""

import json
import re
from datetime import date

import pytest
from test_api import make
from test_pipeline import FakeClient, indexed  # noqa: F401  (the fixture)

from wcs.api.votes import catalog_of, clean_title, result_words, session_of, slug_of
from wcs.store.parquet import write_votes

UKRAINE = "4045155"
PALESTINE = "4069004"


def vote(undl_id, symbol, on, title, how, **kw):
    """One vote as the UN's voting data has it: a row for each Member State listed."""
    totals = {k: sum(1 for v in how.values() if v == k) for k in "YNAX"}
    return [{"undl_id": undl_id, "country_code": code, "country_name": code, "vote": v, "date": on,
             "session": kw.get("session", 76), "resolution": symbol, "draft": kw.get("draft", "A/ES-11/L.1"),
             "meeting": kw.get("meeting", "A/ES-11/PV.5"), "title": title, "agenda_title": kw.get("agenda"),
             "subjects": None, "vote_note": kw.get("note"), "total_yes": totals["Y"], "total_no": totals["N"],
             "total_abstentions": totals["A"], "total_non_voting": totals["X"], "total_ms": len(how),
             "url": f"https://digitallibrary.un.org/record/{undl_id}"} for code, v in how.items()]


def votes_file(settings):
    rows = vote(UKRAINE, "A/RES/ES-11/1", date(2022, 3, 2),
                "Aggression against Ukraine : resolution / adopted by the General Assembly",
                {"GBR": "Y", "FRA": "Y", "KEN": "Y", "RUS": "N", "BLR": "N", "CHN": "A", "IND": "A", "VEN": "X"},
                session=None, agenda="Letter from the Permanent Representative of Ukraine.",
                draft="A/ES-11/L.1|A/ES-11/L.1/Add.1")
    rows += vote(PALESTINE, "A/RES/79/81", date(2024, 12, 3),
                 "Peaceful settlement of the question of Palestine : resolution / adopted by the General Assembly",
                 {"GBR": "A", "FRA": "Y", "KEN": "Y", "USA": "N", "PRK": "Y", "CIV": "Y", "TUR": "Y"},
                 session=79, draft="A/79/L.24", meeting="A/79/PV.45", agenda="Question of Palestine.")
    # a record with no vote listed for anyone: no table, so no page
    rows += [{**rows[0], "undl_id": "1", "resolution": "A/RES/1/1", "vote": None}]
    write_votes(settings.parquet_dir, rows)


@pytest.fixture
def site(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", "https://countries.example")
    votes_file(indexed)
    api, _ = make(indexed, FakeClient(), requests_per_minute=1000)
    return api


def title_of(page: str) -> str:
    return re.search(r"<title>(.*?)</title>", page, re.S)[1]


def test_a_vote_has_a_page_of_its_own(site):
    r = site.get("/votes/a-res-es-11-1.html")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    page = r.text
    assert title_of(page) == "A/RES/ES-11/1: Aggression against Ukraine | Where Countries Stand"
    assert re.findall(r"<h1[ >].*?</h1>", page) == ["<h1>A/RES/ES-11/1: Aggression against Ukraine</h1>"]
    assert '<link rel="canonical" href="https://countries.example/votes/a-res-es-11-1.html">' in page
    assert '<meta property="og:url" content="https://countries.example/votes/a-res-es-11-1.html">' in page
    assert "noindex" not in page and "__SITE__" not in page and "<!--#" not in page and "<!--statements" not in page
    # the date in full, the session from the symbol, the meeting, and the result in the UN's words
    assert "on 2 March 2022, at its 11th emergency special session: adopted by 3 votes to 2, with 2 abstentions." \
           in page and "1 Member State did not vote." in page
    assert '<a href="/votes/session-es-11.html">11th emergency special session</a>' in page
    # the UN's record of it: the resolution and the meeting through undocs.org, and the Digital Library's record
    for link in ('href="https://undocs.org/en/A/RES/ES-11/1"', 'href="https://undocs.org/en/A/ES-11/PV.5"',
                 'href="https://undocs.org/en/A/ES-11/L.1"', 'href="https://undocs.org/en/A/ES-11/L.1/Add.1"', f'href="https://digitallibrary.un.org/record/{UKRAINE}"'):
        assert link in page, link
    assert "<dt>Drafts</dt>" in page and "|" not in page[page.index("<main"):page.index("</main>")]
    # the totals, and every country under how it voted, by the UN's name for it on the day, alphabetically
    assert '<tr><th scope="row">In favour</th><td>3</td></tr>' in page
    assert '<tr><th scope="row">Not voting</th><td>1</td></tr>' in page
    assert "Totals, of 8 Member States listed" in page
    lists = dict(re.findall(r'<h3 id="[a-z-]+">(.*?)</h3><ul class="vote-list"[^>]*>(.*?)</ul>', page))
    assert lists == {"In favour (3)": "<li>France</li><li>Kenya</li><li>United Kingdom of Great Britain and Northern Ireland</li>",
                     "Against (2)": "<li>Belarus</li><li>Russian Federation</li>",
                     "Abstaining (2)": "<li>China</li><li>India</li>",
                     "Not voting (1)": "<li>Venezuela (Bolivarian Republic of)</li>"}
    # votes are counts in words, never coloured (docs/DESIGN.md)
    assert "green" not in page and "red;" not in page


def test_a_vote_page_carries_only_breadcrumbs_and_the_site_furniture(site):
    page = site.get("/votes/a-res-79-81.html").text
    found = re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    assert len(found) == 1
    data = json.loads(found[0])
    assert data == {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Where Countries Stand", "item": "https://countries.example/"},
        {"@type": "ListItem", "position": 2, "name": "Recorded votes in the General Assembly",
         "item": "https://countries.example/votes.html"},
        {"@type": "ListItem", "position": 3, "name": "A/RES/79/81: Peaceful settlement of the question of Palestine",
         "item": "https://countries.example/votes/a-res-79-81.html"}]}
    assert "<script src=" not in page  # written on the server: nothing to fetch
    assert '<a class="wts-endorse" href="https://wheretheystand.uk/">' in page and '<nav class="wts-shelf"' in page
    footer = page[page.index('<nav class="foot__links"'):]
    assert re.findall(r'href="([^"]+)"', footer[:footer.index("</nav>")]) == [
        "/about.html", "/methodology.html", "/corrections.html", "/accessibility.html", "/privacy.html",
        "https://github.com/rj-mitchell/wherecountriesstand"]
    menu = page[page.index('<nav class="sections"'):]
    assert re.findall(r'href="([^"]+)"', menu[:menu.index("</nav>")]) == ["/", "/about.html", "/methodology.html",
                                                                         "/glossary.html"]
    assert '<link rel="stylesheet" href="/styles.css?v=' in page
    assert "Côte d’Ivoire" in page and "Democratic People’s Republic of Korea" in page and "Türkiye" in page
    assert " · " not in page and " — " not in page


def test_a_vote_page_says_how_to_reply_if_you_are_named(site):
    page = site.get("/votes/a-res-79-81.html").text
    side = page[page.index('<aside class="vote-side'):]
    assert "<h2 id=\"named-head\">If you’re named</h2>" in side
    assert '<a href="/named.html">If you’re named on this site</a>' in side
    assert '<a href="/corrections.html?kind=statement&amp;page=/votes/a-res-79-81.html#report">Add a statement</a>' \
           in side
    assert "We reply within 5 working days, and decide within 20 working days." in side
    named = site.get("/named.html").text
    assert '<a href="/votes.html">pages for each recorded vote</a>' in named
    assert "beside the briefing or the vote it is about" in named


def test_a_statement_sits_beside_a_vote_and_a_hidden_vote_shows_only_its_note(site):
    replies = site.app.state.replies
    body = {"page": "https://wherecountriesstand.com/votes/a-res-79-81.html", "name": "Ms. Amina Odhiambo",
            "role": "Deputy Permanent Representative of Kenya to the United Nations",
            "text": "Kenya voted in favour, as it has since 1993.", "link": "https://undocs.org/en/A/79/PV.45",
            "email": "press@kenyamission.example"}
    r = site.post("/api/statements", json=body)
    assert r.status_code == 200
    assert site.post("/api/statements", json={**body, "page": "/votes/a-res-99-1.html"}).status_code == 422
    assert site.post("/api/statements", json={**body, "page": "/votes/session-79.html"}).status_code == 422
    assert "Statement from" not in site.get("/votes/a-res-79-81.html").text  # held until it is confirmed
    replies.publish(r.json()["id"], checked="Replied from the mission's published address.", about="KEN")
    page = site.get("/votes/a-res-79-81.html").text
    assert re.search(r'<section class="statement" aria-labelledby="statement-\d+" data-nosnippet><h2 '
                     r'class="statement__head" id="statement-\d+">Statement from Ms\. Amina Odhiambo, \d{1,2} '
                     r'[A-Z][a-z]+ \d{4}</h2>', page)
    assert "In their own words. Not checked or endorsed by Where Countries Stand." in page
    assert site.get("/api/statements", params={"page": "/votes/a-res-79-81.html"}).json()["statements"][0][
        "page"] == "/votes/a-res-79-81.html"
    assert "Statement from" not in site.get("/votes/a-res-es-11-1.html").text
    # hidden while a concern is decided: its name and the note, nothing of the vote, and out of the sitemap
    replies.hide("https://wherecountriesstand.com/votes/A-RES-79-81.html", "Hidden while a concern is decided.")
    hidden = site.get("/votes/a-res-79-81.html")
    assert hidden.status_code == 200 and "Hidden while a concern is decided." in hidden.text
    assert "noindex" in hidden.text and "Kenya" not in hidden.text and "Statement from" not in hidden.text
    assert "/votes/a-res-79-81.html</loc>" not in site.get("/sitemap-votes.xml").text
    replies.unhide("/votes/a-res-79-81.html")
    assert "Statement from" in site.get("/votes/a-res-79-81.html").text


def test_no_two_vote_pages_share_a_title():
    """The lesson of Where Councils Stand's decision 28: every page's title and heading is its own, even where two
    votes in the record share a symbol, or a symbol and a day."""
    def row(undl_id, symbol, on, title="Question of Palestine : resolution / adopted by the General Assembly"):
        return (undl_id, symbol, on, 41, "A/41/PV.94", None, title, None, None, 1, 1, 0, 0, 2, "", 1, 1, 0, 0)
    cat = catalog_of([row("1", "A/RES/41/60A", date(1986, 12, 3)), row("2", "A/RES/41/60A", date(1986, 12, 4)),
                      row("3", "A/RES/41/60A", date(1986, 12, 4)), row("4", "A/RES/41/61", date(1986, 12, 4)),
                      row("5", "A/RES/42/1", date(1987, 10, 1))])
    titles = [v.page_title for v in cat.votes.values()]
    headings = [v.heading for v in cat.votes.values()]
    paths = [v.path for v in cat.votes.values()]
    assert len(set(titles)) == len(set(headings)) == len(set(paths)) == 5
    assert cat.votes["a-res-41-60a"].name == "A/RES/41/60A, 3 December 1986"  # the earliest keeps the address
    assert {v.name for v in cat.votes.values()} >= {"A/RES/41/60A, 4 December 1986, UN Digital Library record 2",
                                                     "A/RES/41/60A, 4 December 1986, UN Digital Library record 3"}
    assert cat.votes["a-res-41-61"].name == "A/RES/41/61"


def test_no_two_pages_on_the_site_share_a_title(site):
    seen = {}
    paths = ["/votes.html"] + [loc.removeprefix("https://countries.example") for loc in
                               re.findall(r"<loc>(.*?)</loc>", site.get("/sitemap-votes.xml").text)]
    paths += [loc.removeprefix("https://countries.example") or "/" for loc in
              re.findall(r"<loc>(.*?)</loc>", site.get("/sitemap-pages.xml").text)]
    for path in dict.fromkeys(paths):
        page = site.get(path)
        assert page.status_code == 200, path
        title = title_of(page.text)
        assert title not in seen, (path, seen.get(title))
        seen[title] = path
        assert " | Where Countries Stand" in title or path == "/", path


def test_the_index_and_each_sessions_list_link_every_vote(site):
    index = site.get("/votes.html")
    assert index.status_code == 200
    page = index.text
    assert title_of(page) == "Recorded votes in the General Assembly | Where Countries Stand"
    assert re.findall(r"<h1[ >].*?</h1>", page) == ["<h1>Recorded votes in the General Assembly</h1>"]
    assert '<link rel="canonical" href="https://countries.example/votes.html">' in page
    assert "2 votes, from 2 March 2022 to 3 December 2024" in page
    latest = page[page.index('id="latest"'):page.index('id="regular"')]
    assert latest.index("/votes/a-res-79-81.html") < latest.index("/votes/a-res-es-11-1.html")  # newest first
    assert "a-res-1-1" not in page  # no table, no page
    assert '<a href="/votes/session-79.html">79th session</a>, ' in page
    assert '<a href="/votes/session-es-11.html">11th emergency special session</a>, ' in page
    assert page.index('id="regular"') < page.index('id="emergency"')
    session = site.get("/votes/session-79.html").text
    assert title_of(session) == "Recorded votes, 79th session | Where Countries Stand"
    assert '<a href="/votes/a-res-79-81.html">A/RES/79/81: Peaceful settlement of the question of Palestine</a>' \
           in session and "3 December 2024, adopted by 5 votes to 1, with 1 abstention" in session
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', session, re.S)[1])
    assert data["@type"] == "BreadcrumbList" and [i["name"] for i in data["itemListElement"]] == [
        "Where Countries Stand", "Recorded votes in the General Assembly", "Recorded votes, 79th session"]
    # reached from the home page, in the record's section, with no script
    home = site.get("/").text
    assert '<a href="/votes.html">Recorded votes in the General Assembly</a>' in home[home.index('id="record-head"'):]


def test_the_sitemap_is_an_index_of_the_pages_and_the_votes(site):
    index = site.get("/sitemap.xml")
    assert index.headers["content-type"] == "application/xml"
    assert re.findall(r"<loc>(.*?)</loc>", index.text) == ["https://countries.example/sitemap-pages.xml",
                                                          "https://countries.example/sitemap-votes.xml"]
    assert "<sitemap><loc>https://countries.example/sitemap-votes.xml</loc><lastmod>2024-12-03</lastmod>" in index.text
    votes = site.get("/sitemap-votes.xml").text
    assert votes.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<urlset ')
    # each dated by the newest vote on it, a date from the record
    for line in ("<url><loc>https://countries.example/votes.html</loc><lastmod>2024-12-03</lastmod></url>",
                 "<url><loc>https://countries.example/votes/session-79.html</loc><lastmod>2024-12-03</lastmod></url>",
                 "<url><loc>https://countries.example/votes/session-es-11.html</loc><lastmod>2022-03-02</lastmod></url>",
                 "<url><loc>https://countries.example/votes/a-res-es-11-1.html</loc><lastmod>2022-03-02</lastmod></url>",
                 "<url><loc>https://countries.example/votes/a-res-79-81.html</loc><lastmod>2024-12-03</lastmod></url>"):
        assert line in votes, line
    assert votes.count("<url>") == 5
    assert "/votes" not in site.get("/sitemap-pages.xml").text
    assert "Sitemap: https://countries.example/sitemap.xml\n" in site.get("/robots.txt").text


def test_other_forms_of_a_votes_address_are_sent_to_its_own_for_good(site):
    for asked, sent in (("/votes", "/votes.html"), ("/votes/", "/votes.html"), ("/votes.html/", "/votes.html"),
                        ("/votes/a-res-79-81", "/votes/a-res-79-81.html"),
                        ("/votes/a-res-79-81/", "/votes/a-res-79-81.html"),
                        ("/votes/a-res-79-81.html/", "/votes/a-res-79-81.html"),
                        ("/votes/A-RES-79-81.html", "/votes/a-res-79-81.html"),
                        ("/votes/A/RES/79/81", "/votes/a-res-79-81.html"),
                        (f"/votes/{UKRAINE}", "/votes/a-res-es-11-1.html"),
                        (f"/votes/{UKRAINE}.html", "/votes/a-res-es-11-1.html"),
                        ("/votes/session-79", "/votes/session-79.html"),
                        ("/votes/a-res-79-81?from=list", "/votes/a-res-79-81.html?from=list")):
        for method in (site.get, site.head):
            r = method(asked, follow_redirects=False)
            assert (r.status_code, r.headers["location"]) == (301, sent), asked


def test_an_unknown_vote_or_session_is_not_found(site):
    for path in ("/votes/a-res-99-1.html", "/votes/session-12.html", "/votes/a-res-1-1.html", "/votes/nope",
                 "/votes/session-.html", "/votes/%3Cscript%3E.html"):
        r = site.get(path, follow_redirects=False)
        assert r.status_code == 404 and "<h1>Page not found</h1>" in r.text, path


def test_with_no_votes_read_in_the_index_says_so(indexed, monkeypatch):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    page = api.get("/votes.html")
    assert page.status_code == 200 and "No recorded votes have been read in yet." in page.text
    assert api.get("/votes/a-res-79-81.html").status_code == 404
    assert "<url>" in api.get("/sitemap-votes.xml").text  # the index alone, undated
    assert "<url><loc>http://testserver/votes.html</loc></url>" in api.get("/sitemap-votes.xml").text


def test_a_new_votes_file_is_read_again(site):
    """Pages are kept until the votes file changes, and then written afresh."""
    first = site.get("/votes/a-res-79-81.html").text
    assert site.get("/votes/a-res-79-81.html").text == first
    votes = site.app.state.votes
    write_votes(votes.path.parent, vote("9", "A/RES/80/1", date(2025, 9, 9), "Something new : resolution / adopted "
                                        "by the General Assembly", {"KEN": "Y"}, session=80))
    import os
    st = votes.path.stat()
    os.utime(votes.path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert site.get("/votes/a-res-79-81.html").status_code == 404
    assert "Something new" in site.get("/votes/a-res-80-1.html").text


def test_the_record_is_read_as_the_un_writes_it():
    assert slug_of("A/RES/ES-11/1") == "a-res-es-11-1" and slug_of("A/RES/104(S-1)") == "a-res-104-s-1"
    assert session_of("A/RES/ES-11/1", None).label == "11th emergency special session"
    assert session_of("A/RES/997(ES-I)", None).key == "es-1"
    assert session_of("A/RES/1622(S-III)", None).label == "3rd special session"
    assert session_of("A/RES/S-8/2", None).key == "s-8"
    assert session_of("A/RES/80/288", None).label == "80th session"
    assert session_of("A/RES/3379(XXX)", 30).label == "30th session"
    assert session_of("A/RES/3379(XXX)", None).label == "30th session"
    assert session_of("A/RES/41/60A", 41).key == "41" and session_of("A/RES/11/1", 11).label == "11th session"
    assert clean_title("Review : World Disarmament Campaign : resolution / adopted by the General Assembly") == \
           "Review: World Disarmament Campaign"
    assert clean_title("Report of the Special Committee : resolutions / adopted by the General Assembly.") == \
           "Report of the Special Committee"
    assert clean_title("Peoples' rights : decision / adopted by the General Assembly") == "Peoples’ rights"
    assert result_words({"Y": 120, "N": 1, "A": 0}) == "adopted by 120 votes to 1, with no abstentions"
