"""Statements under the right of reply (decision 16): what is sent is checked, held until the owner publishes it,
shown beside its briefing in the family's words, and never given to the model or treated as part of the record."""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_api import QUESTION, events, make
from test_pipeline import FakeClient, indexed  # noqa: F401  (the fixture)
from typer.testing import CliRunner

from wcs.api import statements as st
from wcs.api.app import SITE_DIR as SITE
from wcs.api.usage import StateDB
from wcs.cli import app as cli

SAID = "Our delegation's position on adaptation finance is set out in full in document A/79/123, paragraph 4."
WORDS = "In their own words. Not checked or endorsed by Where Countries Stand."


def briefing(api) -> str:
    return events(api.post("/api/ask", json=QUESTION))[-1]["briefing_id"]


def body(bid: str, **kw) -> dict:
    return {"page": f"https://wherecountriesstand.com/b/{bid}", "name": "Ms. Amina Odhiambo",
            "role": "Deputy Permanent Representative of Kenya to the United Nations", "text": SAID,
            "link": "https://undocs.org/en/A/79/123", "email": "press@kenyamission.example", **kw}


# ---- what is sent is checked ------------------------------------------------------------------------------------

def test_the_item_is_a_briefing_on_this_site():
    assert st.item_address("https://wherecountriesstand.com/b/AbC123xy") == "/b/AbC123xy"
    assert st.item_address("wherecountriesstand.com/b/AbC123xy/") == "/b/AbC123xy"
    assert st.item_address("/b/AbC123xy") == "/b/AbC123xy"
    assert st.item_address("http://localhost/b/AbC123xy", ("localhost",)) == "/b/AbC123xy"
    for bad in ("", "https://example.org/b/AbC123xy", "https://wherecountriesstand.com/about.html",
                "https://wherecountriesstand.com/b/x", "javascript:alert(1)", "/b/AbC123xy/more"):
        with pytest.raises(ValueError):
            st.item_address(bad)


def test_a_statement_is_up_to_150_words_and_never_edited():
    assert st.check_text("  " + " ".join(["word"] * 150) + "\n") == " ".join(["word"] * 150)
    text = "First paragraph.\n\nSecond   paragraph, as written."
    assert st.check_text(text) == text                      # kept as written
    with pytest.raises(ValueError, match="up to 150 words; this one is 151"):
        st.check_text(" ".join(["word"] * 151))
    with pytest.raises(ValueError, match="1,500 characters"):
        st.check_text("x" * 1501)
    with pytest.raises(ValueError, match="Write the statement"):
        st.check_text("   ")


def test_name_role_link_email_and_country_are_checked():
    assert st.check_line("  Ms.  Amina\nOdhiambo ", "a name") == "Ms. Amina Odhiambo"
    for bad in ("", " x ", "x" * 201):
        with pytest.raises(ValueError):
            st.check_line(bad, "a name")
    assert st.check_link("") == "" and st.check_link(" https://undocs.org/en/A/79/123 ") == "https://undocs.org/en/A/79/123"
    for bad in ("javascript:alert(1)", "undocs.org/x", "https://", "https://un.org/a b",
                'https://un.org/a"onmouseover="x', "https://un.org/a'b", "https://un.org/<script>", "https://un.org/a>b"):
        with pytest.raises(ValueError, match="web address"):
            st.check_link(bad)
    assert st.check_email(" press@mission.example ") == "press@mission.example"
    for bad in ("", "press", "press@mission", "a b@c.d"):
        with pytest.raises(ValueError):
            st.check_email(bad)
    assert st.check_country(" ken ") == "KEN" and st.check_country(None) is None
    with pytest.raises(ValueError):
        st.check_country("Kenya")


def test_a_statement_sent_waits_and_is_not_shown(indexed):  # noqa: F811
    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    bid = briefing(api)
    r = api.post("/api/statements", json=body(bid))
    assert r.status_code == 200 and r.json()["ok"] is True
    row = st.statement(state, r.json()["id"])
    assert row["status"] == "pending" and row["page"] == f"/b/{bid}" and row["text"] == SAID
    assert row["email"] == "press@kenyamission.example" and row["log"][0]["action"] == "received"
    assert api.get("/api/statements", params={"page": f"/b/{bid}"}).json()["statements"] == []


def test_what_is_sent_is_refused_with_words_for_the_reader(indexed):  # noqa: F811
    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    bid = briefing(api)
    too_long = api.post("/api/statements", json=body(bid, text=" ".join(["word"] * 151)))
    assert too_long.status_code == 422 and too_long.json()["detail"] == "A statement can be up to 150 words; this one is 151."
    for field in ("page", "name", "role", "text", "email"):                         # required
        assert api.post("/api/statements", json={k: v for k, v in body(bid).items() if k != field}).status_code == 422
        assert api.post("/api/statements", json=body(bid, **{field: " "})).status_code == 422, field
    assert api.post("/api/statements", json={k: v for k, v in body(bid).items() if k != "link"}).status_code == 200
    for bad, says in ((body(bid, email="nobody"), "confirm who you are"),
                      (body(bid, link="javascript:alert(1)"), "web address"),
                      (body(bid, link='https://undocs.org/en/A/79/123"><b>'), "web address"),
                      (body(bid, page="https://example.org/b/" + bid), "wherecountriesstand.com"),
                      (body(bid, page="https://wherecountriesstand.com/about.html"), "a briefing on this site"),
                      (body(bid, page="/b/nosuchbriefing"), "no briefing at that address")):
        r = api.post("/api/statements", json=bad)
        assert r.status_code == 422 and says in r.json()["detail"], (bad, r.json())
    from wcs.api.usage import withdraw_briefing
    withdraw_briefing(state, bid, "It misread a statement.")
    assert api.post("/api/statements", json=body(bid)).status_code == 422          # not beside a withdrawn briefing
    assert len(st.statements(state)) == 1


def test_the_test_servers_own_address_is_this_site(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", "https://countries.example")
    api, _ = make(indexed, FakeClient(), requests_per_minute=1000)
    bid = briefing(api)
    assert api.post("/api/statements", json=body(bid, page=f"https://countries.example/b/{bid}")).status_code == 200
    assert api.post("/api/statements", json=body(bid, page=f"http://testserver/b/{bid}")).status_code == 200


# ---- the owner's commands ----------------------------------------------------------------------------------------

def run(*args: str) -> str:
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_the_owners_commands(indexed):  # noqa: F811
    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    bid = briefing(api)
    sid = api.post("/api/statements", json=body(bid)).json()["id"]
    other = api.post("/api/statements", json=body(bid, name="Mr. Second Sender")).json()["id"]

    listed = run("statements")
    assert f"#{sid}" in listed and "pending" in listed and "confirm with: press@kenyamission.example" in listed
    assert "(16 words)" in listed and SAID in listed
    assert run("list-statements") == listed                                          # Where Councils Stand's name

    assert run("publish-statement", str(sid), "--country", "ken") == "published\n"
    assert run("publish-statement", str(sid)) == "no waiting statement with that number\n"
    shown = st.statement(state, sid)
    assert shown["status"] == "published" and shown["country"] == "KEN"
    assert shown["shown_on"] == datetime.now(UTC).strftime("%Y-%m-%d")

    assert CliRunner().invoke(cli, ["decline-statement", str(other)]).exit_code != 0   # a reason is required
    assert run("decline-statement", str(other), "--reason", "It is about another delegation's conduct.") == "declined\n"
    assert st.statement(state, other)["reason"] == "It is about another delegation's conduct."
    assert f"#{other}" not in run("statements") and "declined: It is about" in run("statements", "--all")

    too_long = CliRunner().invoke(cli, ["update-statement", str(sid), "--text", " ".join(["word"] * 151)])
    assert too_long.exit_code == 1 and "up to 150 words" in too_long.output
    assert run("update-statement", str(sid), "--text", "New words, as the author sent them.") == "updated\n"
    assert st.statement(state, sid)["text"] == "New words, as the author sent them."
    assert [e["action"] for e in st.statement(state, sid)["log"]] == ["received", "published", "updated"]
    assert run("update-statement", str(sid)).startswith("nothing changed")

    assert run("withdraw-statement", str(sid)) == "withdrawn\n"
    assert st.statement(state, sid)["reason"] == "Withdrawn by its author."
    assert run("withdraw-statement", str(sid)) == "no waiting or published statement with that number\n"
    assert api.get("/api/statements", params={"page": f"/b/{bid}"}).json()["statements"] == []


def test_the_confirming_address_goes_three_months_after_a_statement_ends(tmp_path):
    state = StateDB(tmp_path / "s.db")
    ids = [st.add_statement(state, "/b/AbC123xy", "A", "Role", "Words.", "", f"{n}@mission.example") for n in range(4)]
    st.publish_statement(state, ids[0])
    st.withdraw_statement(state, ids[1], "Withdrawn by its author.")
    st.decline_statement(state, ids[2], "Off the subject.")
    now = datetime.now(UTC)
    assert st.forget_emails(state, now + timedelta(days=80)) == 0                    # not yet three months
    assert st.forget_emails(state, now + timedelta(days=93)) == 2
    emails = {r["id"]: r["email"] for r in st.statements(state, open_only=False)}
    assert emails == {ids[0]: "0@mission.example", ids[1]: "", ids[2]: "", ids[3]: "3@mission.example"}
    assert st.months_before(datetime(2026, 5, 31, tzinfo=UTC), 3) == datetime(2026, 2, 28, tzinfo=UTC)


def test_the_update_deletes_addresses_that_are_due(indexed):  # noqa: F811
    from wcs.update import Report, spending

    state = StateDB(indexed.data_dir / "state.db")
    sid = st.add_statement(state, "/b/AbC123xy", "A", "Role", "Words.", "", "a@mission.example")
    st.decline_statement(state, sid, "Off the subject.")
    with state.connect() as con:
        con.execute("UPDATE statements SET ended_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (sid,))
    spending(indexed, Report(started="test"))
    assert st.statement(state, sid)["email"] == ""


# ---- how it is shown --------------------------------------------------------------------------------------------

def test_a_published_statement_is_shown_beside_its_briefing_without_the_address(indexed):  # noqa: F811
    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    bid = briefing(api)
    sid = api.post("/api/statements", json=body(bid, text="First paragraph.\n\nSecond paragraph.")).json()["id"]
    st.publish_statement(state, sid, "KEN")
    out = api.get("/api/statements", params={"page": f"/b/{bid}"}).json()
    assert out["note"] == WORDS
    [shown] = out["statements"]
    assert shown == {"id": sid, "page": f"/b/{bid}", "country": "KEN", "name": "Ms. Amina Odhiambo",
                     "role": "Deputy Permanent Representative of Kenya to the United Nations",
                     "text": "First paragraph.\n\nSecond paragraph.", "link": "https://undocs.org/en/A/79/123",
                     "shown_on": datetime.now(UTC).strftime("%Y-%m-%d")}
    assert "mission.example" not in json.dumps(out)
    assert api.get("/api/statements", params={"country": "ken"}).json()["statements"] == [shown]
    assert api.get("/api/statements", params={"page": "/b/otherbriefing"}).json()["statements"] == []
    assert api.get("/api/statements").status_code == 422
    assert api.get("/api/statements", params={"page": "https://example.org/b/" + bid}).status_code == 422


def test_the_page_shows_statements_in_the_familys_words():
    """app.js shows them after the briefing's notes: a section headed "Statement from [name], [date]", the role, the
    words (set as text, never as HTML), a link to the record if it is a web address, then the family's sentence."""
    home = (SITE / "index.html").read_text()
    assert '<div id="notes"></div>\n      <div class="statements" id="statements"></div>' in home
    script = (SITE / "app.js").read_text()
    assert f'const NOT_ENDORSED = "{WORDS}";' in script
    assert "`Statement from ${s.name}, ${fmtDate(s.shown_on)}`" in script
    assert 'el("section", { class: "statement", "aria-labelledby": head }' in script
    assert "innerHTML" not in script and "/^https?:\\/\\//i.test(s.link" in script
    assert re.search(r'"briefing", "notes", "statements", "sources", "chips"', script)   # a new question clears them
    css = (SITE / "styles.css").read_text()
    assert ".statement {" in css and "border: 1px dashed" in css


# ---- never part of the record -----------------------------------------------------------------------------------

def test_statements_never_reach_the_model_the_index_or_a_briefing(indexed):  # noqa: F811
    client = FakeClient()
    api, state = make(indexed, client, requests_per_minute=1000)
    bid = briefing(api)
    marker = "Zanzibarian marker phrase about climate finance"
    sid = api.post("/api/statements", json=body(bid, text=f"{marker}. Climate finance for adaptation.")).json()["id"]
    st.publish_statement(state, sid, "KEN")
    calls_before = len(client.calls)
    again = api.post("/api/ask", json={"question": "What has Kenya said about climate finance for adaptation?"})
    assert len(client.calls) > calls_before                                          # a new briefing was written
    assert marker not in json.dumps(client.calls, default=str)                       # the model never saw it
    assert marker not in again.text
    found = api.post("/api/search", json={"question": marker}).json()               # not in the index
    assert found["sources"] and marker not in json.dumps(found["sources"])
    assert marker not in api.get(f"/api/briefings/{bid}").text                       # not part of the briefing
    with state.connect() as con:                                                     # nor its saved or cached events
        assert not any(marker in r[0] for r in con.execute("SELECT events FROM briefings UNION ALL "
                                                           "SELECT events FROM answer_cache"))


def test_the_code_that_builds_the_record_and_briefings_never_reads_statements():
    src = Path(__file__).resolve().parents[1] / "src" / "wcs"
    for path in [*src.glob("rag/*.py"), *src.glob("index/*.py"), *src.glob("rank/*.py"), *src.glob("embed/*.py"),
                 *src.glob("sources/*.py"), *src.glob("store/*.py"), src / "engine.py", src / "evaluate.py"]:
        text = path.read_text()
        assert "wcs.api" not in text and "FROM statements" not in text and "state.db" not in text, path


# ---- the pages ------------------------------------------------------------------------------------------------

def test_the_named_page_and_the_routes(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", "https://countries.example")
    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    named = api.get("/named.html")
    assert named.status_code == 200
    text = named.text
    assert "<h1>If you’re named</h1>" in text and "<title>If you’re named · Where Countries Stand</title>" in text
    assert "https://countries.example/named.html</loc>" in api.get("/sitemap.xml").text
    for route in ("Something is wrong", "You’ve been characterised unfairly", "Add a statement in your own words"):
        assert f"<h3>{route}</h3>" in text, route
    for kind in ("error", "unfair", "statement", "takedown"):
        assert f'href="/corrections.html?kind={kind}#report"' in text, kind
    times = "We reply within 5 working days, and decide within 20 working days."
    assert times in text and "Statement from [your name], [date]" in text and WORDS in text
    assert "mailto:admin@wheretheystand.uk" in text and "https://www.gov.uk/complain-about-charity" in text
    for label in ("permanent mission", "Group of 77", "regional groups", "not yet the official record",
                  "(spoke in French)", "rights of reply", "Where Lives Stand", "income groups"):
        assert label in " ".join(text.split()), label
    # every page's footer: "If you're named" beside the corrections link; the family's shelf untouched
    for page in ("/", "/about.html", "/privacy.html", "/missing"):
        footer = api.get(page).text.split('<footer class="foot">', 1)[1]
        assert re.search(r'<a href="/corrections.html">Corrections</a>\s*<a href="/named.html">If you’re named</a>',
                         footer), page
    assert '<a href="/named.html" aria-current="page">If you’re named</a>' in text
    # the corrections page: the response times, the link, the routes by their family names, the old values kept
    corrections = api.get("/corrections.html").text
    assert times in corrections and '<a href="/named.html">If you’re named on this site</a>' in corrections
    for value, label in (("error", "Something is wrong"), ("unfair", "You’ve been characterised unfairly"),
                         ("statement", "Add a statement in your own words"), ("takedown", None), ("other", None)):
        assert f'name="kind" value="{value}"' in corrections, value
        assert label is None or label in corrections
    assert "groups a country is counted in" in corrections
    script = (SITE / "corrections.js").read_text()
    assert 'params.get("kind") === "problem" ? "error"' in script
    privacy = api.get("/privacy.html").text
    assert "Statements under the right of reply" in privacy and "deleted three months after it is" in privacy


def test_reports_take_the_routes_and_the_old_kind(indexed):  # noqa: F811
    from wcs.api.usage import reports
    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    for kind in ("error", "unfair", "problem", "takedown", "other"):
        assert api.post("/api/report", json={"kind": kind, "message": "A group label is wrong."}).status_code == 200
    assert api.post("/api/report", json={"kind": "statement", "message": "Words."}).status_code == 422
    assert api.post("/api/report", json={"message": "No kind given."}).status_code == 200
    assert [r["kind"] for r in reports(state)] == ["error", "other", "takedown", "problem", "unfair", "error"]
