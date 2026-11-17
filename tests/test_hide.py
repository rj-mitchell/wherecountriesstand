"""Hiding an item while a concern about it is decided (the charity's impartiality policy, 7.3; wts-core
docs/adr/0008): `wcs hide <item> --note "…"`, `wcs unhide <item>`, `wcs hidden`.

Two kinds of item can be hidden on this site:

* a saved briefing, by its address (``/b/<id>``): its page and ``/api/briefings/<id>`` show the note in its
  place, any statement beside it is hidden with it, no new statement is taken beside it, and the same question
  asked again is answered with the sources and the note, not with the cached briefing;
* a record of the archive (a speech), by ``record:`` and the id on its source card: it is left out of every search
  and so of every new briefing's sources, and a briefing saved or cached before with the record among its sources
  is hidden with it, under the record's note (a briefing written from a record carries its words).

Hiding is not withdrawing: it is undone with ``unhide``, and nothing is listed on the corrections page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import model_of
from fastapi.testclient import TestClient
from test_api import QUESTION, events
from test_pipeline import TODAY, FakeClient, indexed  # noqa: F401  (the fixture)
from typer.testing import CliRunner
from wts_core.adapters.hashing import HashingEmbedder

from wcs.api.app import SITE_DIR, SiteHooks, create_app, hidden_now
from wcs.cli import app as cli
from wcs.engine import Engine
from wcs.rag.pipeline import Pipeline
from wcs.state import open_state

NOTE = "Hidden while a concern about it is looked into."
KENYA = "sc:S/PV.9000:0005"


@pytest.fixture
def site(indexed):  # noqa: F811
    client = FakeClient()
    pipeline = Pipeline(indexed, Engine(indexed, embedder=HashingEmbedder(64), today=TODAY), client=model_of(client),
                        today=TODAY)
    indexed.raw["usage"] = {**indexed.raw["usage"], "requests_per_minute": 1000}
    api = TestClient(create_app(indexed, pipeline=pipeline, state=open_state(indexed)))
    api.sdk = client
    return api


def run(*args, exit_code=0):
    result = CliRunner().invoke(cli, list(args))
    assert result.exit_code == exit_code, result.output
    return result.output


def test_a_hidden_briefing_shows_the_note_in_its_place(site):
    final = events(site.post("/api/ask", json=QUESTION))[-1]
    bid, address = final["briefing_id"], f"/b/{final['briefing_id']}"
    form = {"page": address, "name": "Ms. Amina Odhiambo", "role": "Deputy Permanent Representative",
            "text": "Our position is set out in document A/79/123.", "email": "press@mission.example"}
    sid = site.post("/api/statements", json=form).json()["id"]
    site.app.state.replies.publish(sid, checked="Reply from the mission's published address.")
    assert len(site.get("/api/statements", params={"page": address}).json()["statements"]) == 1

    assert run("hide", address, "--note", NOTE) == f"hidden: {address}\n"
    assert run("hidden").splitlines()[1] == f"    shown in its place: {NOTE}"
    hidden = site.get(f"/api/briefings/{bid}")
    assert hidden.status_code == 423 and hidden.json() == {"detail": NOTE}  # the note, and nothing of the briefing
    page = site.get(address)
    assert page.status_code == 200 and "noindex" in page.text
    assert QUESTION["question"] not in page.text  # not even the question in the page's title
    # its statement is hidden with it, and no new one is taken beside it
    assert site.get("/api/statements", params={"page": address}).json()["statements"] == []
    assert site.post("/api/statements", json=form).status_code == 422
    # the same question again is not answered with it: its cached copy went when it was hidden, and a briefing is
    # written afresh (the review of the adoption: "the sources and the note" was all a reader could have of that
    # question for as long as the copy lasted)
    calls = len(site.sdk.calls)
    again = events(site.post("/api/ask", json=QUESTION))
    assert len(site.sdk.calls) == calls + 1 and again[-1]["briefing_id"] != bid and again[-1]["answer"]
    # it is not a withdrawal: nothing on the corrections page, and it can be shown again
    assert site.get("/api/corrections").json() == []
    assert "already hidden" in run("hide", address, "--note", "Another note.", exit_code=1)
    assert run("unhide", address) == f"shown again: {address}\n" and run("hidden") == ""
    assert site.get(f"/api/briefings/{bid}").status_code == 200
    assert events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"] == again[-1]["briefing_id"]
    assert len(site.get("/api/statements", params={"page": address}).json()["statements"]) == 1
    # the page's script says so in its own words
    script = (SITE_DIR / "app.js").read_text()
    assert 'res.status === 423 ? "Briefing hidden for now"' in script


def test_a_hidden_record_is_left_out_of_searches_and_briefings_written_from_it_are_hidden_with_it(site):
    first = events(site.post("/api/ask", json=QUESTION))
    bid = first[-1]["briefing_id"]
    cards = first[1]["sources"]
    assert [c["record_id"] for c in cards] == [KENYA, "sc:S/PV.9000:0006"]
    search = {"question": "What has been said about climate finance for adaptation and small islands?"}
    assert KENYA in json.dumps(site.post("/api/search", json=search).json())

    run("hide", f"record:{KENYA}", "--note", NOTE)
    # left out of every search, and so of a new briefing's sources: the model is never given it
    found = site.post("/api/search", json=search).json()
    assert [c["record_id"] for c in found["sources"]] == ["sc:S/PV.9000:0006"]
    calls = len(site.sdk.calls)
    new = events(site.post("/api/ask", json=search))
    assert len(site.sdk.calls) == calls + 1 and "Kenya says" not in json.dumps(site.sdk.calls[-1], default=str)
    assert [c["record_id"] for c in new[1]["sources"]] == ["sc:S/PV.9000:0006"]
    # the briefing saved before was written from the record, and carries its words: it is hidden with it, under
    # the record's note, and names nothing (the review of the adoption found its text and its card's excerpt were
    # still served)
    saved = site.get(f"/api/briefings/{bid}")
    assert saved.status_code == 423 and saved.json() == {"detail": NOTE}
    page = site.get(f"/b/{bid}")
    assert "noindex" in page.text and QUESTION["question"] not in page.text
    # the same question again is not answered from the cache with that briefing: it is written afresh, without
    # the record
    calls = len(site.sdk.calls)
    again = events(site.post("/api/ask", json=QUESTION))
    assert len(site.sdk.calls) == calls + 1 and "Kenya says" not in json.dumps(site.sdk.calls[-1], default=str)
    assert [c["record_id"] for c in again[1]["sources"]] == ["sc:S/PV.9000:0006"]
    assert again[-1]["briefing_id"] != bid and KENYA not in json.dumps(again)
    # and that one is cached as any other, for as long as the same records are hidden
    assert events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"] == again[-1]["briefing_id"]
    assert len(site.sdk.calls) == calls + 1
    # should a cached copy with the record among its sources be about to be played all the same, the note is sent
    # in its place, with the sources that are not hidden
    hidden = site.app.state.replies.all_hidden()
    instead = hidden_now(bid, first, hidden)
    assert [e["type"] for e in instead] == ["plan", "sources", "final"]
    assert [c["record_id"] for c in instead[1]["sources"]] == ["sc:S/PV.9000:0006"]
    assert instead[-1] == {"type": "final", "answer": None, "usage": None, "note": NOTE}
    assert SiteHooks(lambda: hidden).on_cached(None, first) == instead and KENYA not in json.dumps(instead)
    # nothing was changed in what is kept: shown again, everything is as it was
    run("unhide", f"record:{KENYA}")
    assert site.get(f"/api/briefings/{bid}").json()["events"][1]["sources"][0]["record_id"] == KENYA
    assert KENYA in json.dumps(site.post("/api/search", json=search).json())
    back = events(site.post("/api/ask", json=QUESTION))
    assert back[-1]["briefing_id"] == bid and len(site.sdk.calls) == calls + 1
    assert '"hidden"' not in (SITE_DIR / "app.js").read_text()  # no card of its own: the whole briefing is hidden


def test_a_statement_beside_a_briefing_hidden_with_its_record_is_hidden_with_it(site):
    bid = events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"]
    address = f"/b/{bid}"
    form = {"page": address, "name": "Ms. Amina Odhiambo", "role": "Deputy Permanent Representative",
            "text": "Our position is set out in document A/79/123.", "email": "press@mission.example"}
    sid = site.post("/api/statements", json=form).json()["id"]
    site.app.state.replies.publish(sid, checked="Reply from the mission's published address.", about="KEN")
    for asked in ({"page": address}, {"country": "KEN"}):
        assert len(site.get("/api/statements", params=asked).json()["statements"]) == 1
    run("hide", f"record:{KENYA}", "--note", NOTE)
    for asked in ({"page": address}, {"country": "KEN"}, {"page": address, "country": "KEN"}):
        assert site.get("/api/statements", params=asked).json()["statements"] == []
    refused = site.post("/api/statements", json=form)
    assert refused.status_code == 422 and "press@mission.example" not in refused.text
    # the briefing can still be hidden by its own address, and shown again
    assert run("hide", address, "--note", NOTE) == f"hidden: {address}\n"
    assert run("unhide", address) == f"shown again: {address}\n"
    run("unhide", f"record:{KENYA}")
    assert len(site.get("/api/statements", params={"page": address}).json()["statements"]) == 1
    assert site.post("/api/statements", json=form).status_code == 200


def test_the_statements_route_shows_exactly_what_its_own_filter_showed(site):
    """wts-core 0.3.0 (`Replies(..., hidden_with=…)`) asks the site, inside ``published``, whether a
    briefing is hidden with something else; the statements route's own filter over what ``published`` returned
    is gone. No reader is to see a difference, so the route is held here to the old filter itself, worked out
    beside it from a desk that is given no ``hidden_with``, in every state a briefing can be in: shown, hidden
    with a record among its sources, hidden by its own address, and withdrawn.

    One thing the old filter did is kept as it was, and is the owner's to look at: while any record of the
    archive is hidden, a statement beside a briefing since withdrawn is not shown either, though with nothing
    hidden it is. No page asks for a withdrawn briefing's statements."""
    from wts_core.replies import Item, Replies

    from wcs.replies import REPLY_MESSAGES, REPLY_RULES, hidden_records

    parts, replies = site.app.state.parts, site.app.state.replies
    assert replies.hidden_with is not None
    without = Replies(site.app.state.db, items=replies.items, rules=REPLY_RULES, messages=REPLY_MESSAGES,
                      hideable=replies.hideable, clock=replies.clock)

    def as_the_route_filtered(page: str | None, country: str | None) -> list[int]:
        found = without.published(items=[Item(address=page)] if page else [], about=country)
        if page and country:
            found = [s for s in found if s["item"] == page and s["about"] == country]
        if hidden_records(replies.all_hidden()):
            found = [s for s in found if replies.items.shown(Item(address=s["item"]))]
        return [s["id"] for s in found]

    # two briefings: the first with the Kenyan record among its sources, the second written while it was hidden
    first = events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"]
    run("hide", f"record:{KENYA}", "--note", NOTE)
    second = events(site.post("/api/ask", json={"question": "How do small islands see climate finance?"}))[-1][
        "briefing_id"]
    run("unhide", f"record:{KENYA}")
    assert KENYA in json.dumps(parts.archive.get(first).events)
    assert KENYA not in json.dumps(parts.archive.get(second).events)
    for bid, country in ((first, "KEN"), (second, "FJI"), (second, "KEN")):
        sid = replies.submit(item=f"/b/{bid}", name="Ms. Amina Odhiambo", role="Deputy Permanent Representative",
                             text="Our position is set out in document A/79/123.", link=None,
                             email="press@mission.example")
        replies.publish(sid, checked="Reply from the mission's published address.", about=country)

    seen = {}

    def compare(state: str) -> None:
        shown = []
        for page in (None, f"/b/{first}", f"/b/{second}"):
            for country in (None, "KEN", "FJI"):
                if not page and not country:
                    continue
                asked = {k: v for k, v in (("page", page), ("country", country)) if v}
                now = [s["id"] for s in site.get("/api/statements", params=asked).json()["statements"]]
                assert now == as_the_route_filtered(page, country), (state, asked)
                shown.append(now)
        seen[state] = shown

    compare("nothing hidden")
    run("hide", f"record:{KENYA}", "--note", NOTE)
    compare("a record hidden: the first briefing with it")
    run("unhide", f"record:{KENYA}")
    replies.hide(f"/b/{second}", NOTE)
    compare("the second briefing hidden by its own address")
    replies.unhide(f"/b/{second}")
    assert parts.archive.withdraw(second, "It misattributed a statement.")
    compare("the second briefing withdrawn, nothing hidden")
    run("hide", f"record:{KENYA}", "--note", NOTE)
    compare("the second briefing withdrawn, and a record hidden")
    # and the states do differ from one another, so the comparison is of something
    assert seen["nothing hidden"] == seen["the second briefing withdrawn, nothing hidden"] == [
        [1, 3], [2], [1], [1], [], [2, 3], [3], [2]]
    assert seen["a record hidden: the first briefing with it"] == [[3], [2], [], [], [], [2, 3], [3], [2]]
    assert seen["the second briefing hidden by its own address"] == [[1], [], [1], [1], [], [], [], []]
    assert seen["the second briefing withdrawn, and a record hidden"] == [[], [], [], [], [], [], [], []]
    # the route has no filter of its own left
    source = (SITE_DIR.parents[1] / "src" / "wcs" / "api" / "app.py").read_text(encoding="utf-8")
    assert "replies.items.shown" not in source and "EVERY_CORRECTION" not in source


def cached_copies(site, bid):
    with site.app.state.db.connect() as con:
        return con.execute("SELECT count(*) FROM wts_answer_cache WHERE instr(events, ?) > 0", (bid,)).fetchone()[0]


def test_a_withdrawn_briefing_is_not_answered_from_the_cache(site):
    """The review found a withdrawn briefing was still played from the answer cache to anyone asking the same
    question, for up to a week: the old code did the same. `wcs withdraw` now drops its cached copies, under
    every wording and choice of options it was cached for."""
    spaced = {"question": "  " + QUESTION["question"].upper().replace(" ", "  ")}  # the same key: case and spacing
    comprehensive = {**QUESTION, "mode": "comprehensive"}                          # another key, another briefing
    bid = events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"]
    other = events(site.post("/api/ask", json=comprehensive))[-1]["briefing_id"]
    assert events(site.post("/api/ask", json=spaced))[-1]["briefing_id"] == bid and other != bid
    assert cached_copies(site, bid) == 1 and cached_copies(site, other) == 1
    assert run("withdraw", "--reason", "It misattributed a statement.", "--", bid) == "withdrawn\n"
    assert cached_copies(site, bid) == 0 and cached_copies(site, other) == 1
    calls = len(site.sdk.calls)
    again = events(site.post("/api/ask", json=QUESTION))
    assert len(site.sdk.calls) == calls + 1 and again[-1]["briefing_id"] not in (bid, other) and again[-1]["answer"]
    assert events(site.post("/api/ask", json=comprehensive))[-1]["briefing_id"] == other  # untouched


def test_a_cached_copy_the_commands_did_not_reach_is_not_played_and_the_briefing_is_written_afresh(site):
    """A briefing withdrawn before the commands dropped its cached copy (a copy may be up to a week old on the day
    of the deploy), or withdrawn or hidden by something other than the commands: the copy is not played. From
    wts-core 0.3.0, where `Hooks.on_cached` is asked before anything is sent and may refuse the replay, the site
    answers None for such a copy, and the reader's briefing is written afresh in the same request,
    counted and saved as any other, and stored over the copy. (On 0.2.1 that one reader was sent the sources and
    the words the briefing's address shows, and the reader after had the fresh briefing.)
    tests/test_parity.py holds the bytes sent, the spending and the place in progress."""
    parts, replies = site.app.state.parts, site.app.state.replies
    first = events(site.post("/api/ask", json=QUESTION))
    bid = first[-1]["briefing_id"]
    assert parts.archive.withdraw(bid, "It misattributed a statement.") and cached_copies(site, bid) == 1
    calls = len(site.sdk.calls)
    afresh = events(site.post("/api/ask", json=QUESTION))
    assert len(site.sdk.calls) == calls + 1  # written in that same request
    assert [e["type"] for e in afresh][:2] == ["plan", "sources"] and afresh[1] == first[1]
    assert afresh[-1]["answer"] and afresh[-1]["briefing_id"] != bid and "note" not in afresh[-1]
    assert "withdrawn" not in json.dumps(afresh)
    # the copy under that question's key is the new briefing's now, and the next reader is played it
    assert cached_copies(site, bid) == 0 and cached_copies(site, afresh[-1]["briefing_id"]) == 1
    again = events(site.post("/api/ask", json=QUESTION))
    assert len(site.sdk.calls) == calls + 1 and again[-1]["briefing_id"] == afresh[-1]["briefing_id"]
    assert site.get(f"/api/briefings/{bid}").status_code == 410  # the withdrawn one still says so at its address

    # hidden by its address, not through the command: the same, and never the note in a briefing's place
    hidden_id = afresh[-1]["briefing_id"]
    replies.hide(f"/b/{hidden_id}", NOTE)
    assert cached_copies(site, hidden_id) == 1
    third = events(site.post("/api/ask", json=QUESTION))
    assert len(site.sdk.calls) == calls + 2 and third[-1]["answer"] and third[-1]["briefing_id"] not in (bid, hidden_id)
    assert NOTE not in json.dumps(third) and cached_copies(site, hidden_id) == 0
    # shown again, the hidden briefing is back at its address; the question stays with the newer briefing
    replies.unhide(f"/b/{hidden_id}")
    assert site.get(f"/api/briefings/{hidden_id}").status_code == 200
    assert events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"] == third[-1]["briefing_id"]
    assert len(site.sdk.calls) == calls + 2


def test_a_copy_refused_while_the_budget_is_spent_is_still_not_played(site):
    """Writing afresh is a new briefing, so the caps are asked first (the desk does it): with the day's budget
    spent, the reader of a withdrawn briefing's question is shown the sources and why briefings are paused, as
    for any question with no cached answer, and never the withdrawn briefing. Nothing is spent, and nothing more
    is saved."""
    parts, db = site.app.state.parts, site.app.state.db
    bid = events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"]
    assert parts.archive.withdraw(bid, "It misattributed a statement.") and cached_copies(site, bid) == 1
    with db.connect() as con:  # the day's cap, spent many times over
        con.execute("INSERT INTO wts_spending (at, kind, model, visitor, cost_usd) SELECT at, kind, model, visitor, "
                    "1000 FROM wts_spending LIMIT 1")
        rows = con.execute("SELECT count(*) FROM wts_spending").fetchone()[0]
    calls = len(site.sdk.calls)
    paused = events(site.post("/api/ask", json=QUESTION))
    assert [e["type"] for e in paused] == ["plan", "sources", "final"] and len(site.sdk.calls) == calls
    assert paused[-1] == {"type": "final", "answer": None, "usage": None,
                          "note": "Today’s budget for new briefings has been used up. It resets at midnight UTC. "
                                  "The sources found are shown below."}
    assert "withdrawn" not in json.dumps(paused) and bid not in json.dumps(paused)
    with db.connect() as con:
        assert con.execute("SELECT count(*) FROM wts_spending").fetchone()[0] == rows
        assert con.execute("SELECT count(*) FROM wts_briefings").fetchone()[0] == 1


def test_an_item_is_hidden_under_one_address_however_it_was_typed_and_only_if_it_is_there(site):
    """The review of the adoption found `wcs hide https://wherecountriesstand.com/b/<id>` said "hidden" and hid
    nothing: the address was filed as typed, and the pages ask about `/b/<id>`. What the owner types is now put
    into the one spelling the site files (wts-core 0.2.1, ``Replies(hideable=…)``), and what is not a briefing or
    a record that is there is refused, in words for the owner."""
    bid = events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"]
    address = f"/b/{bid}"
    for typed in (f"https://wherecountriesstand.com/b/{bid}", f"https://www.wherecountriesstand.com/b/{bid}/",
                  f"wherecountriesstand.com/b/{bid}?from=email#top", f"  /b/{bid}/  ", address):
        assert run("hide", "--note", NOTE, "--", typed) == f"hidden: {address}\n"
        assert site.get(f"/api/briefings/{bid}").status_code == 423
        assert "already hidden" in run("hide", "--note", NOTE, "--", address, exit_code=1)
        assert run("unhide", "--", typed) == f"shown again: {address}\n"
        assert site.get(f"/api/briefings/{bid}").status_code == 200
    # a record, with the prefix in any case and spaces around the id
    for typed in (f"record:{KENYA}", f"Record: {KENYA} "):
        assert run("hide", typed, "--note", NOTE) == f"hidden: record:{KENYA}\n"
        assert run("unhide", typed) == f"shown again: record:{KENYA}\n"

    # what is not there, or is not a briefing or a record, is refused: nothing is filed, and the command fails
    refused = {
        "/b/NoSuchId1": "there is no briefing at /b/NoSuchId1, or it has been withdrawn",
        "https://example.org/b/" + bid: "only a saved briefing or a record of the archive can be hidden",
        "/about": "only a saved briefing or a record of the archive can be hidden",
        bid: "only a saved briefing or a record of the archive can be hidden",
        KENYA: "only a saved briefing or a record of the archive can be hidden",
        "record:sc:S/PV.9000:9999": "there is no record 'sc:S/PV.9000:9999' in the archive",
        "record:": "there is no record '' in the archive",
    }
    for typed, words in refused.items():
        assert words in " ".join(run("hide", "--note", NOTE, "--", typed, exit_code=1).split()), typed
    assert run("hidden") == "" and site.app.state.replies.all_hidden() == []
    # a withdrawn briefing is not hidden either: it is already gone, and listed on the corrections page
    run("withdraw", "--reason", "A date was wrong.", "--", bid)
    assert "or it has been withdrawn" in run("hide", "--note", NOTE, "--", address, exit_code=1)


def test_the_corrections_page_lists_withdrawn_briefings_as_it_did_and_a_correction_said_publicly(site):
    """The page's script draws a withdrawn briefing from the same fields as before the move, as a plain sentence; an
    entry of another kind is what the owner chose to say publicly when closing a report (new)."""
    script = (SITE_DIR / "corrections.js").read_text()
    assert ('el("li", {}, `“${c.question}”, written ${fmtDate(c.at)} and withdrawn ${fmtDate(c.withdrawn_at)}. `,'
            '\n       c.reason || "")') in script
    bid = events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"]
    rid = site.post("/api/report", json={"kind": "error", "page": f"/b/{bid}", "message": "A date is wrong.",
                                         "contact": "reader@example.org"}).json()["id"]
    run("reports", "close", str(rid), "--outcome", "resolved", "--note", "Checked with the record; reader told.",
        "--public-note", "A date in one briefing was wrong; the briefing was written again.")
    (entry,) = site.get("/api/corrections").json()
    assert entry["kind"] == "error" and entry["note"] == "A date in one briefing was wrong; the briefing was " \
                                                         "written again."
    assert "reader@example.org" not in json.dumps(entry) and "Checked with the record" not in json.dumps(entry)
    assert 'el("li", {}, `Corrected on ${fmtDate(c.date)}. `, c.note)' in script
    assert "resolved: Checked with the record; reader told. (said publicly: A date in one" in run("reports", "--all")


def test_a_reports_entry_in_the_corrections_log_is_its_date_its_kind_and_the_public_note_and_nothing_else(site):
    """The review found the log published what a reporter typed in the "page" box (one could type a name, a
    telephone number and an address there) and the id of the briefing a report was about, though saved briefings
    are listed nowhere. wts-core 0.2.1 gives a report's entry as three fields; the site adds none."""
    bid = events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"]
    typed = "Jane Example, 020 7946 0000, jane@example.org"
    replies = site.app.state.replies
    for page in (typed, f"/b/{bid}"):
        rid = site.post("/api/report", json={"kind": "error", "page": page, "message": "A date is wrong: 2019.",
                                             "contact": "reader@example.org"}).json()["id"]
        replies.close(rid, outcome="resolved", note="Private: checked with S/PV.9000.",
                      public_note="A date in one briefing was wrong; the briefing was written again.")
    log = site.get("/api/corrections")
    assert log.json() == [{"date": log.json()[0]["date"], "kind": "error",
                           "note": "A date in one briefing was wrong; the briefing was written again."}] * 2
    for kept_back in (typed, "Jane", "7946", "example.org", bid, "A date is wrong: 2019.", "Private"):
        assert kept_back not in log.text, kept_back


def test_the_corrections_log_is_given_whole(site):
    """The review found the log stopped at 100 entries (the package's default): the oldest corrections would have
    dropped off the public page. The old code listed every withdrawn briefing."""
    parts, replies = site.app.state.parts, site.app.state.replies
    for n in range(130):
        saved = parts.archive.save(f"Question {n}?", {}, [{"type": "final", "answer": None}], id=f"Briefing{n:03d}")
        assert parts.archive.withdraw(saved.id, f"Reason {n}.")
    for n in range(120):
        rid = replies.report(kind="error", page="", message=f"Mistake {n}.", contact=None)
        replies.close(rid, outcome="resolved", public_note=f"Correction {n}.")
    log = site.get("/api/corrections").json()
    assert len(log) == 250
    assert {e["reason"] for e in log if e["kind"] == "withdrawn"} == {f"Reason {n}." for n in range(130)}
    assert {e["note"] for e in log if e["kind"] == "error"} == {f"Correction {n}." for n in range(120)}


def test_what_is_waiting_for_the_owner(site):
    assert run("waiting") == "nothing is waiting for a decision; hidden for now: 0 items\n"
    site.post("/api/report", json={"kind": "takedown", "message": "Please remove my name."})
    run("hide", "record:sc:S/PV.9000:0006", "--note", NOTE)
    assert run("waiting").startswith("waiting for a decision: 1 reports, 0 statements; hidden for now: 1 items; "
                                     "the oldest has waited 0 working days")


# Test questions proposed from readers' questions: never one whose briefing is not shown


def proposed(site) -> str:
    """`wcs propose-questions`: the file it writes, read back (it is for the owner alone, like a weekly report)."""
    said = run("propose-questions", "--since", "7d").splitlines()
    path = Path(said[0])
    assert path.parent.name == "proposed-questions" and path.parent.parent == Path(site.app.state.db.path).parent
    assert oct(path.stat().st_mode & 0o777) == "0o600" and "Nothing has been added to the test set" in said[1]
    return path.read_text(encoding="utf-8")


def test_a_briefing_that_is_hidden_withdrawn_or_reported_is_never_proposed_as_a_test_question(site):
    """wts-core 0.3.0, `propose-questions`: readers' questions whose briefings the checks marked down
    are written to a file for the owner to consider as test questions. The site says which briefings are never
    proposed (``stats_commands(proposals_leave_out=…)``, here ``wcs.cli._never_proposed``): one hidden while a
    concern about it is decided, by its own address or with a record among its sources, and one withdrawn. One a
    reader has reported is left out by the mark the report leaves on its record. Each is tried here, and each
    without the commands that would also have marked the record, so that it is the site's answer that is
    tested."""
    parts, replies = site.app.state.parts, site.app.state.replies
    asked = {
        "shown": "What has been said about climate finance since 2020?",
        "hidden": "What has Kenya said about climate finance for adaptation?",
        "withdrawn": "Is climate finance for adaptation too slow?",
        "reported": "How has climate finance for adaptation been described?",
        "cites a hidden record": "What did Kenya say about adaptation and climate finance?",
    }
    ids = {}
    for name, question in asked.items():
        final = events(site.post("/api/ask", json={"question": question}))[-1]
        assert final["answer"]["loaded_words"] >= 1, name  # the checks marked each down: each could be proposed
        ids[name] = final["briefing_id"]
    assert len(set(ids.values())) == 5
    everything = proposed(site)
    assert all(f'question = "{question}"' in everything for question in asked.values())
    assert everything.count("[[q]]") == 5

    replies.hide(f"/b/{ids['hidden']}", NOTE)                           # by its own address
    assert parts.archive.withdraw(ids["withdrawn"], "It misattributed a statement.")
    assert site.post("/api/report", json={"kind": "unfair", "page": f"/b/{ids['reported']}",
                                          "message": "This characterises our position unfairly."}).json()["ok"]
    left = proposed(site)
    assert [name for name, question in asked.items() if f'question = "{question}"' in left] == [
        "shown", "cites a hidden record"]
    for name in ("hidden", "withdrawn", "reported"):
        assert asked[name] not in left and ids[name] not in left, name

    # a record hidden: every briefing with it among its sources is hidden with it, and none of them is proposed;
    # a question asked meanwhile is written without the record, and is
    assert KENYA in json.dumps(site.get(f"/api/briefings/{ids['shown']}").json())
    run("hide", f"record:{KENYA}", "--note", NOTE)
    since = "How do small islands see climate finance?"
    new = events(site.post("/api/ask", json={"question": since}))[-1]
    assert new["answer"] and KENYA not in json.dumps(new)
    left = proposed(site)
    assert left.count("[[q]]") == 1 and f'question = "{since}"' in left
    assert not [q for q in asked.values() if q in left] and not [b for b in ids.values() if b in left]

    # shown again, what was only hidden may be proposed again; what was withdrawn or reported never is
    run("unhide", f"record:{KENYA}")
    replies.unhide(f"/b/{ids['hidden']}")
    left = proposed(site)
    assert sorted(name for name, question in asked.items() if f'question = "{question}"' in left) == [
        "cites a hidden record", "hidden", "shown"]
    # the same when the owner's commands took them down: the record's own mark, and the site's answer, both say no
    run("withdraw", "--reason", "A date was wrong.", "--", ids["shown"])
    assert asked["shown"] not in proposed(site)
    # nothing was added to the test set, and the file is kept in the data folder, beside the weekly reports
    from wcs.config import load_settings
    assert (load_settings().data_dir / "proposed-questions").is_dir()


def test_the_briefings_never_proposed_are_those_not_shown_at_their_address(site):
    """``wcs.cli._never_proposed`` answers as the briefing's own address does: left out exactly when
    ``/api/briefings/<id>`` does not give the briefing."""
    from wcs.cli import _never_proposed

    parts, replies = site.app.state.parts, site.app.state.replies
    bid = events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"]

    def agree(expected: bool) -> None:
        assert _never_proposed(bid) is expected
        assert (site.get(f"/api/briefings/{bid}").status_code != 200) is expected

    agree(False)
    replies.hide(f"/b/{bid}", NOTE)
    agree(True)
    replies.unhide(f"/b/{bid}")
    agree(False)
    replies.hide(f"record:{KENYA}", NOTE)
    agree(True)
    replies.unhide(f"record:{KENYA}")
    agree(False)
    assert parts.archive.withdraw(bid, "It misattributed a statement.")
    agree(True)
    assert _never_proposed("NoSuchId123") is True  # and one that is not there
