"""Hiding an item while a concern about it is decided (the charity's impartiality policy, 7.3; wts-core
docs/adr/0008): `wcs hide <item> --note "…"`, `wcs unhide <item>`, `wcs hidden`.

Two kinds of item can be hidden on this site:

* a saved briefing, by its address (``/b/<id>``): its page and ``/api/briefings/<id>`` show the note in its
  place, any statement beside it is hidden with it, no new statement is taken beside it, and the same question
  asked again is answered with the sources and the note, not with the cached briefing;
* a record of the archive (a speech), by ``record:`` and the id on its source card: it is left out of every search
  and so of every new briefing's sources, and in a briefing saved or cached before, its source card shows the note
  in its place.

Hiding is not withdrawing: it is undone with ``unhide``, and nothing is listed on the corrections page.
"""

from __future__ import annotations

import json

import pytest
from conftest import model_of
from fastapi.testclient import TestClient
from test_api import QUESTION, events
from test_pipeline import TODAY, FakeClient, indexed  # noqa: F401  (the fixture)
from typer.testing import CliRunner
from wts_core.adapters.hashing import HashingEmbedder

from wcs.api.app import SITE_DIR, create_app
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
    # the same question again: the sources, and the note where the cached briefing would have been
    calls = len(site.sdk.calls)
    again = events(site.post("/api/ask", json=QUESTION))
    assert [e["type"] for e in again] == ["plan", "sources", "final"] and len(site.sdk.calls) == calls
    assert again[-1] == {"type": "final", "answer": None, "usage": None, "note": NOTE}
    assert "briefing_id" not in json.dumps(again)
    # it is not a withdrawal: nothing on the corrections page, and it can be shown again
    assert site.get("/api/corrections").json() == []
    assert "already hidden" in run("hide", address, "--note", "Another note.", exit_code=1)
    assert run("unhide", address) == f"shown again: {address}\n" and run("hidden") == ""
    assert site.get(f"/api/briefings/{bid}").status_code == 200
    assert events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"] == bid
    assert len(site.get("/api/statements", params={"page": address}).json()["statements"]) == 1
    # the page's script says so in its own words
    script = (SITE_DIR / "app.js").read_text()
    assert 'res.status === 423 ? "Briefing hidden for now"' in script


def test_a_hidden_record_is_left_out_of_searches_and_new_briefings_and_noted_in_old_ones(site):
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
    # in the briefing saved before, and in its cached copy, its card shows the note in its place, under its number
    for sources in (site.get(f"/api/briefings/{bid}").json()["events"][1]["sources"],
                    events(site.post("/api/ask", json=QUESTION))[1]["sources"]):
        assert sources[0] == {"n": 1, "kind": "hidden", "note": NOTE} and sources[1]["record_id"].endswith("0006")
    # nothing was changed in what is kept: shown again, everything is as it was
    run("unhide", f"record:{KENYA}")
    assert site.get(f"/api/briefings/{bid}").json()["events"][1]["sources"][0]["record_id"] == KENYA
    assert KENYA in json.dumps(site.post("/api/search", json=search).json())
    script = (SITE_DIR / "app.js").read_text()
    assert 'if (s.kind === "hidden") {' in script


def test_the_corrections_page_lists_withdrawn_briefings_as_it_did_and_a_correction_said_publicly(site):
    """The page's script draws a withdrawn briefing with the same line as before the move, from the same fields; an
    entry of another kind is what the owner chose to say publicly when closing a report (new)."""
    script = (SITE_DIR / "corrections.js").read_text()
    assert ('el("li", {}, el("strong", {}, c.question), ` (written ${fmtDate(c.at)}; withdrawn '
            '${fmtDate(c.withdrawn_at)}): `,\n       c.reason || "withdrawn")') in script
    bid = events(site.post("/api/ask", json=QUESTION))[-1]["briefing_id"]
    rid = site.post("/api/report", json={"kind": "error", "page": f"/b/{bid}", "message": "A date is wrong.",
                                         "contact": "reader@example.org"}).json()["id"]
    run("reports", "close", str(rid), "--outcome", "resolved", "--note", "Checked with the record; reader told.",
        "--public-note", "A date in one briefing was wrong; the briefing was written again.")
    (entry,) = site.get("/api/corrections").json()
    assert entry["kind"] == "error" and entry["note"] == "A date in one briefing was wrong; the briefing was " \
                                                         "written again."
    assert "reader@example.org" not in json.dumps(entry) and "Checked with the record" not in json.dumps(entry)
    assert 'el("li", {}, el("strong", {}, "Corrected"), ` (${fmtDate(c.date)}): `, c.note)' in script
    assert "resolved: Checked with the record; reader told. (said publicly: A date in one" in run("reports", "--all")


def test_what_is_waiting_for_the_owner(site):
    assert run("waiting") == ("waiting for a decision: 0 reports, 0 statements; hidden for now: 0 items; the oldest "
                              "has waited None working days\n")
    site.post("/api/report", json={"kind": "takedown", "message": "Please remove my name."})
    run("hide", "record:sc:S/PV.9000:0006", "--note", NOTE)
    assert run("waiting").startswith("waiting for a decision: 1 reports, 0 statements; hidden for now: 1 items; "
                                     "the oldest has waited 0 working days")
