"""Statements under the right of reply (decision 16): what is sent is checked, held until the owner publishes it,
shown beside its briefing in the family's words, and never given to the model or treated as part of the record."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import typer
from test_api import QUESTION, events, make
from test_pipeline import FakeClient, indexed  # noqa: F401  (the fixture)
from typer.testing import CliRunner
from wts_core.replies import Refused, months_before
from wts_core.testing import FixedClock

from wcs.api.app import SITE_DIR as SITE
from wcs.cli import app as cli
from wcs.replies import REPLY_MESSAGES, REPLY_RULES, BriefingItems, check_country
from wcs.state import open_state

SAID = "Our delegation's position on adaptation finance is set out in full in document A/79/123, paragraph 4."
WORDS = "In their own words. Not checked or endorsed by Where Countries Stand."
CHECKED = "Reply from the mission's published address, 14 November."


def briefing(api) -> str:
    return events(api.post("/api/ask", json=QUESTION))[-1]["briefing_id"]


def body(bid: str, **kw) -> dict:
    return {"page": f"https://wherecountriesstand.com/b/{bid}", "name": "Ms. Amina Odhiambo",
            "role": "Deputy Permanent Representative of Kenya to the United Nations", "text": SAID,
            "link": "https://undocs.org/en/A/79/123", "email": "press@kenyamission.example", **kw}


# ---- what is sent is checked ------------------------------------------------------------------------------------

def test_the_item_is_a_briefing_on_this_site():
    address = BriefingItems.address
    assert address("https://wherecountriesstand.com/b/AbC123xy") == "/b/AbC123xy"
    assert address("wherecountriesstand.com/b/AbC123xy/") == "/b/AbC123xy"
    assert address("/b/AbC123xy") == "/b/AbC123xy"
    assert address("http://localhost/b/AbC123xy", ("localhost",)) == "/b/AbC123xy"
    for bad in ("", "https://example.org/b/AbC123xy", "https://wherecountriesstand.com/about.html",
                "https://wherecountriesstand.com/b/x", "javascript:alert(1)", "/b/AbC123xy/more"):
        with pytest.raises(Refused):
            address(bad)


def sent(replies, **kw):
    """A statement through the shared rules with this site's limits and words (``wcs.replies``)."""
    form = {"name": "Ms. Amina Odhiambo", "role": "Deputy Permanent Representative", "text": "Words.", "link": "",
            "email": "press@mission.example", **kw}
    return replies.submit(item="/b/AbC123xy", **form)


@pytest.fixture
def replies(settings):
    """The site's reports and statements over an empty database, with one saved briefing to sit beside."""
    from wts_core.usage import BriefingArchive

    from wcs.replies import open_replies

    db = open_state(settings)
    briefings = BriefingArchive(db, new_id=lambda: "AbC123xy")
    briefings.save("A question?", {}, [{"type": "final", "answer": None}])
    return open_replies(db, briefings)


def test_a_statement_is_up_to_150_words_and_never_edited(replies):
    assert REPLY_RULES.max_words == 150 and REPLY_RULES.max_chars == 1500 and REPLY_RULES.min_words == 1
    kept = replies.statement(sent(replies, text="  " + " ".join(["word"] * 150) + "\n"))
    assert kept["text"] == " ".join(["word"] * 150)
    text = "First paragraph.\n\nSecond   paragraph, as written."
    assert replies.statement(sent(replies, text=text))["text"] == text       # kept as written
    with pytest.raises(Refused, match="up to 150 words; this one is 151"):
        sent(replies, text=" ".join(["word"] * 151))
    with pytest.raises(Refused, match=r"up to 150 words \(1,500 characters\)"):
        sent(replies, text="x" * 1501)
    with pytest.raises(Refused, match="Write the statement"):
        sent(replies, text="   ")


def test_name_role_link_email_and_country_are_checked(replies):
    assert replies.statement(sent(replies, name="  Ms.  Amina\nOdhiambo "))["name"] == "Ms. Amina Odhiambo"
    for bad in ("", " x ", "x" * 201):
        with pytest.raises(Refused):
            sent(replies, name=bad)
    with pytest.raises(Refused, match=r"^Give your role, such as Permanent Representative of Kenya to the United "
                                      r"Nations\.$"):
        sent(replies, role=" ")
    with pytest.raises(Refused, match=r"^Your role can be up to 200 characters\.$"):
        sent(replies, role="x" * 201)
    with pytest.raises(Refused, match=r"^Your name can be up to 200 characters\.$"):
        sent(replies, name="x" * 201)
    assert replies.statement(sent(replies, link=""))["link"] is None
    assert replies.statement(sent(replies, link=" https://undocs.org/en/A/79/123 "))["link"] == \
        "https://undocs.org/en/A/79/123"
    for bad in ("javascript:alert(1)", "undocs.org/x", "https://", "https://un.org/a b",
                'https://un.org/a"onmouseover="x', "https://un.org/a'b", "https://un.org/<script>", "https://un.org/a>b"):
        with pytest.raises(Refused, match="web address"):
            sent(replies, link=bad)
    assert replies.statement(sent(replies, email=" press@mission.example "))["email"] == "press@mission.example"
    for bad in ("", "press", "press@mission", "a b@c.d"):
        with pytest.raises(Refused, match="confirm who you are"):
            sent(replies, email=bad)
    assert check_country(" ken ") == "KEN" and check_country(None) is None
    with pytest.raises(ValueError):
        check_country("Kenya")
    # the family's heading and sentence, in this site's name
    assert REPLY_MESSAGES.heading == "Statement from {name}, {date}"
    assert REPLY_MESSAGES.disclaimer.format(site=REPLY_MESSAGES.site) == WORDS


def test_a_statement_sent_waits_and_is_not_shown(indexed):  # noqa: F811
    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    bid = briefing(api)
    r = api.post("/api/statements", json=body(bid))
    assert r.status_code == 200 and r.json()["ok"] is True
    desk = api.app.state.replies
    row = desk.statement(r.json()["id"])
    assert row["status"] == "pending" and row["item"] == f"/b/{bid}" and row["text"] == SAID
    assert row["email"] == "press@kenyamission.example" and desk.log(row["id"])[0]["action"] == "received"
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
    api.app.state.parts.archive.withdraw(bid, "It misread a statement.")
    assert api.post("/api/statements", json=body(bid)).status_code == 422          # not beside a withdrawn briefing
    assert len(api.app.state.replies.statements()) == 1


def test_the_test_servers_own_address_is_this_site(indexed, monkeypatch):  # noqa: F811
    monkeypatch.setenv("WCS_SITE_URL", "https://countries.example")
    api, _ = make(indexed, FakeClient(), requests_per_minute=1000)
    bid = briefing(api)
    assert api.post("/api/statements", json=body(bid, page=f"https://countries.example/b/{bid}")).status_code == 200
    assert api.post("/api/statements", json=body(bid, page=f"http://testserver/b/{bid}")).status_code == 200


# ---- the owner's commands ----------------------------------------------------------------------------------------

def run(*args: str, exit_code: int = 0) -> str:
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == exit_code, result.output
    return result.output


def test_the_owners_commands(indexed):  # noqa: F811
    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    desk = api.app.state.replies
    bid = briefing(api)
    sid = api.post("/api/statements", json=body(bid)).json()["id"]
    other = api.post("/api/statements", json=body(bid, name="Mr. Second Sender")).json()["id"]

    listed = run("statements")
    assert f"#{sid}" in listed and "pending" in listed and "confirm with: press@kenyamission.example" in listed
    assert "(16 words)" in listed and SAID in listed
    assert run("statements", "list") == listed                                       # the family's shape of command
    assert run("list-statements") == listed                                          # Where Councils Stand's name

    # how the sender was confirmed is required, and kept in the log's entry for the publishing
    assert CliRunner().invoke(cli, ["statements", "publish", str(sid), "--country", "ken"]).exit_code == 2
    blank = CliRunner().invoke(cli, ["statements", "publish", str(sid), "--checked", "  "])
    assert blank.exit_code == 1 and "how you confirmed who sent it" in blank.output
    with_address = CliRunner().invoke(cli, ["statements", "publish", str(sid), "--checked",
                                            "Wrote to press@kenyamission.example."])
    assert with_address.exit_code == 1 and "leave their email" in with_address.output  # it would be kept for good
    assert desk.statement(sid)["status"] == "pending"
    assert run("statements", "publish", str(sid), "--checked", CHECKED, "--country", "ken") == "published\n"
    # a command that did nothing says why and exits 1
    assert "error: " in run("statements", "publish", str(sid), "--checked", CHECKED, exit_code=1)
    assert "error: " in run("statements", "decline", str(sid), "--reason", "Too late.", exit_code=1)
    shown = desk.statement(sid)
    assert shown["status"] == "published" and shown["about"] == "KEN"
    assert shown["published_at"][:10] == datetime.now(UTC).strftime("%Y-%m-%d")
    assert [(e["action"], e["note"]) for e in desk.log(sid)] == [("received", None), ("published", CHECKED)]
    assert f"sender confirmed: {CHECKED}" in run("statements")                       # the owner can read it back
    assert f"published: {CHECKED}" in run("statements", "log", str(sid))
    # the option's own help, not the help page as drawn (its boxes and colours differ from one terminal to another)
    for name in (("statements", "publish"), ("publish-statement",)):
        command = typer.main.get_command(cli)
        for word in name:
            command = command.commands[word]
        option = next(o for o in command.params if o.name == "checked")
        assert option.required and "never shown on the site" in option.help
        assert "Do not put the sender's email address in it" in option.help

    assert CliRunner().invoke(cli, ["statements", "decline", str(other)]).exit_code != 0   # a reason is required
    assert run("statements", "decline", str(other), "--reason", "It is about another delegation's conduct.") == \
        "declined\n"
    assert desk.statement(other)["reason"] == "It is about another delegation's conduct."
    assert f"#{other}" not in run("statements") and "declined: It is about" in run("statements", "--all")

    too_long = CliRunner().invoke(cli, ["statements", "update", str(sid), "--text", " ".join(["word"] * 151)])
    assert too_long.exit_code == 1 and "up to 150 words" in too_long.output
    assert run("statements", "update", str(sid), "--text", "New words, as the author sent them.") == "updated\n"
    assert desk.statement(sid)["text"] == "New words, as the author sent them."
    assert [e["action"] for e in desk.log(sid)] == ["received", "published", "updated"]
    assert desk.log(sid)[-1]["previous"] == {"text": SAID}                           # the words it replaced are kept
    assert "error: " in run("statements", "update", str(sid), exit_code=1)

    assert run("statements", "withdraw", str(sid)) == "withdrawn\n"
    assert desk.log(sid)[-1]["note"] == "Withdrawn by its author."
    assert "error: " in run("statements", "withdraw", str(sid), exit_code=1)
    # the same for reports and saved briefings
    assert "error: no open report 999" in run("reports", "close", "999", exit_code=1)
    assert run("withdraw", "nosuchbriefing", "--reason", "x", exit_code=1) == "no briefing with that id\n"
    rid = api.post("/api/report", json={"kind": "error", "page": f"/b/{bid}", "message": "A date is wrong."}).json()["id"]
    assert run("reports", "close", str(rid)) == "closed\n" and run("reports", "close", str(rid), exit_code=1)
    # (after "--", since one id in 64 begins with a hyphen, which the command line would take for an option)
    assert run("withdraw", "--reason", "It misread a statement.", "--", bid) == "withdrawn\n"
    assert api.get("/api/statements", params={"page": f"/b/{bid}"}).json()["statements"] == []
    # what happened next is on the briefing's record: it was reported, and then corrected
    with state.connect() as con:
        assert tuple(con.execute("SELECT reported, corrected FROM wts_briefing_log WHERE briefing_id = ?",
                                 (bid,)).fetchone()) == (1, 1)


def test_the_commands_as_they_were_named_before_still_work(indexed):  # noqa: F811
    """`wcs publish-statement 3` and the rest are kept for this release, as further names for the same commands,
    out of the help (wts-core docs/MIGRATION.md 1.9)."""
    api, _ = make(indexed, FakeClient(), requests_per_minute=1000)
    desk = api.app.state.replies
    bid = briefing(api)
    one, two, three = (api.post("/api/statements", json=body(bid, name=f"Sender {n}")).json()["id"] for n in "123")
    assert run("publish-statement", str(one), "--checked", CHECKED, "--country", "KEN") == "published\n"
    assert run("update-statement", str(one), "--link", "") == "updated\n" and desk.statement(one)["link"] is None
    assert run("decline-statement", str(two), "--reason", "Off the subject.") == "declined\n"
    assert run("withdraw-statement", str(three), "--reason", "They asked on 3 May.") == "withdrawn\n"
    assert [desk.statement(n)["status"] for n in (one, two, three)] == ["published", "declined", "withdrawn"]
    rid = api.post("/api/report", json={"kind": "error", "message": "A date is wrong."}).json()["id"]
    assert f"#{rid}" in run("reports") and run("close-report", str(rid)) == "closed\n"
    assert f"#{rid}" not in run("reports") and "(closed)" in run("reports", "--all")
    listed = typer.main.get_command(cli).commands
    assert listed["publish-statement"].hidden and not listed["statements"].hidden


def test_a_statement_the_site_takes_down_is_removed_and_one_its_author_takes_down_is_withdrawn(indexed):  # noqa: F811
    """New with the shared package (wts-core docs/adr/0008): the two used to be one command and one word, so the
    log could not say which had happened."""
    api, _ = make(indexed, FakeClient(), requests_per_minute=1000)
    desk = api.app.state.replies
    bid = briefing(api)
    sid = api.post("/api/statements", json=body(bid)).json()["id"]
    run("statements", "publish", str(sid), "--checked", CHECKED)
    assert CliRunner().invoke(cli, ["statements", "remove", str(sid)]).exit_code == 2  # the reason is required
    assert run("statements", "remove", str(sid), "--reason", "It names a private person.") == "removed\n"
    assert desk.statement(sid)["status"] == "removed" and "removed: It names a private" in run("statements", "--all")
    assert api.get("/api/statements", params={"page": f"/b/{bid}"}).json()["statements"] == []


def test_the_confirming_address_goes_three_months_after_a_statement_ends(settings):
    from wts_core.usage import BriefingArchive

    from wcs.replies import open_replies

    clock = FixedClock("2026-05-01T09:00:00+00:00")
    db = open_state(settings, clock)
    briefings = BriefingArchive(db, new_id=lambda: "AbC123xy", clock=clock)
    briefings.save("A question?", {}, [{"type": "final", "answer": None}])
    desk = open_replies(db, briefings, clock)
    ids = [sent(desk, email=f"{n}@mission.example") for n in range(4)]
    desk.publish(ids[0], checked=CHECKED)
    desk.withdraw(ids[1], "Withdrawn by its author.")
    desk.decline(ids[2], "Off the subject.")
    clock.advance(days=80)
    assert desk.forget_old_emails() == 0                                             # not yet three months
    clock.advance(days=13)
    assert desk.forget_old_emails() == 2
    # The owner's listing tidies first. The fourth has waited three months undecided by now: it lapses, and its
    # address goes too (wts-core docs/adr/0008, the second question). The published one keeps its address while
    # it is shown.
    emails = {r["id"]: r["email"] for r in desk.statements()}
    assert emails == {ids[0]: "0@mission.example", ids[1]: None, ids[2]: None, ids[3]: None}
    assert desk.statement(ids[3])["status"] == "declined"
    assert desk.statement(ids[3])["reason"] == "No decision was made in time; please send it again."
    assert months_before(datetime(2026, 5, 31, tzinfo=UTC), 3) == datetime(2026, 2, 28, tzinfo=UTC)


def test_the_update_deletes_addresses_that_are_due(indexed):  # noqa: F811
    from wcs.update import Report, spending

    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    desk = api.app.state.replies
    sid = api.post("/api/statements", json=body(briefing(api))).json()["id"]
    desk.decline(sid, "Off the subject.")
    with state.connect() as con:
        con.execute("UPDATE wts_statements SET closed_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (sid,))
    report = Report(started="test")
    spending(indexed, report)
    assert desk.statement(sid)["email"] is None and not any("could not read" in w for w in report.warnings)


# ---- how it is shown --------------------------------------------------------------------------------------------

def test_a_published_statement_is_shown_beside_its_briefing_without_the_address(indexed):  # noqa: F811
    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    bid = briefing(api)
    sid = api.post("/api/statements", json=body(bid, text="First paragraph.\n\nSecond paragraph.")).json()["id"]
    desk = api.app.state.replies
    desk.publish(sid, checked=CHECKED, about="KEN")
    out = api.get("/api/statements", params={"page": f"/b/{bid}"}).json()
    assert out["note"] == WORDS
    [shown] = out["statements"]
    assert shown == {"id": sid, "page": f"/b/{bid}", "country": "KEN", "name": "Ms. Amina Odhiambo",
                     "role": "Deputy Permanent Representative of Kenya to the United Nations",
                     "text": "First paragraph.\n\nSecond paragraph.", "link": "https://undocs.org/en/A/79/123",
                     "shown_on": datetime.now(UTC).strftime("%Y-%m-%d")}
    assert "mission.example" not in json.dumps(out)
    # nor the owner's note of how the sender was confirmed: it is in the log, which is never sent
    assert desk.log(sid)[-1]["note"] == CHECKED
    assert "published address" not in json.dumps(out) and "log" not in shown
    assert "published address" not in api.get("/api/statements", params={"country": "KEN"}).text
    assert "published address" not in api.get(f"/api/briefings/{bid}").text
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
    api.app.state.replies.publish(sid, checked=CHECKED, about="KEN")
    calls_before = len(client.calls)
    again = api.post("/api/ask", json={"question": "What has Kenya said about climate finance for adaptation?"})
    assert len(client.calls) > calls_before                                          # a new briefing was written
    assert marker not in json.dumps(client.calls, default=str)                       # the model never saw it
    assert marker not in again.text
    found = api.post("/api/search", json={"question": marker}).json()               # not in the index
    assert found["sources"] and marker not in json.dumps(found["sources"])
    assert marker not in api.get(f"/api/briefings/{bid}").text                       # not part of the briefing
    with state.connect() as con:                                                     # nor its saved or cached events
        assert not any(marker in r[0] for r in con.execute("SELECT events FROM wts_briefings UNION ALL "
                                                           "SELECT events FROM wts_answer_cache"))
        assert not any(marker in str(tuple(r)) for r in con.execute("SELECT * FROM wts_briefing_log"))


def test_the_code_that_builds_the_record_and_briefings_never_reads_statements():
    src = Path(__file__).resolve().parents[1] / "src" / "wcs"
    for path in [*src.glob("rag/*.py"), *src.glob("index/*.py"), *src.glob("rank/*.py"),
                 *src.glob("sources/*.py"), *src.glob("store/*.py"), src / "engine.py", src / "evaluate.py",
                 src / "adapters.py", src / "messages.py"]:
        text = path.read_text()
        assert "wcs.api" not in text and "wcs.replies" not in text and "wts_core.replies" not in text, path
        assert "wts_statements" not in text and "wcs.state" not in text and "state.db" not in text, path


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
    api, state = make(indexed, FakeClient(), requests_per_minute=1000)
    for kind in ("error", "unfair", "problem", "takedown", "other"):
        assert api.post("/api/report", json={"kind": kind, "message": "A group label is wrong."}).status_code == 200
    assert api.post("/api/report", json={"kind": "statement", "message": "Words."}).status_code == 422
    assert api.post("/api/report", json={"message": "No kind given."}).status_code == 200
    # for the owner: a request to remove something about a person first, then newest first (they were newest first)
    assert [r["kind"] for r in api.app.state.replies.reports()] == ["takedown", "error", "other", "problem", "unfair",
                                                                    "error"]
