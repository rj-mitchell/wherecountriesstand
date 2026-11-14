"""What is kept about a reader, and where a statement under the right of reply may go: the two checks every Where
They Stand site runs over its real pipeline (wts-core, ``wts_core.testing``; its docs/adr/0008 and 0012), and what
this site's privacy page says of the record of each briefing and of backups."""

from __future__ import annotations

import logging
import re

import pytest
import shared
from conftest import speech
from fastapi.testclient import TestClient
from wts_core.adapters.hashing import HashingEmbedder
from wts_core.events import Block, Citation, CitationDelta, Completed, TextDelta
from wts_core.observe import LOGGER
from wts_core.ports import Usage
from wts_core.testing import FixedClock, assert_no_reader_data, assert_statements_stay_out
from wts_core.testing.models import ScriptedModel

from wcs.api.app import SITE_DIR, create_app, start_logging
from wcs.engine import Engine
from wcs.index.build import Indexer, embed_pending, refresh_indices
from wcs.rag.pipeline import Pipeline
from wcs.reference.countries import countries
from wcs.state import BACKUPS, open_state
from wcs.store.parquet import write_by_year

CANARY = "What has been said about climate finance and the xylophonecanary since 2020?"
KENYA = "Kenya says climate finance for adaptation is too slow."


class Recording(HashingEmbedder):
    """The site's test embedder, keeping every text it is given: what was embedded, of documents and of questions."""

    def __init__(self) -> None:
        super().__init__(64)
        self.texts: list[str] = []

    def embed_documents(self, texts):
        self.texts += list(texts)
        return super().embed_documents(texts)


class IndexSeen:
    """What is in the site's real search index, and everything ever written to it, as the check reads them."""

    def __init__(self, index) -> None:
        self._index, self.added = index, []
        replace, add_vectors = index.replace, index.add_vectors
        index.replace = lambda keys, rows: (self.added.append(list(rows)), replace(keys, rows))[1]
        index.add_vectors = lambda keys, rows, vectors: (self.added.append(list(rows)),
                                                         add_vectors(keys, rows, vectors))[1]

    @property
    def rows(self):
        return self._index.passages().search().limit(None).to_list()

    @property
    def vector_rows(self):
        return self._index.vectors().search().select(["passage_id", "record_id", "country_code"]).limit(None).to_list()


def scripted_model() -> ScriptedModel:
    """A model that reads every question the same way and writes one cited sentence, keeping what it was asked."""
    usage = Usage(input_uncached=1000, output=200)
    return ScriptedModel(
        [TextDelta("Kenya said "), CitationDelta(0, KENYA), TextDelta("climate finance is too slow."),
         Completed(blocks=(Block("Kenya said "), Block("climate finance is too slow.", (Citation(0, KENYA),))),
                   model="claude-sonnet-5-5", stop_reason="end_turn", usage=usage)],
        structured={"search_text": "climate finance", "date_from": "2020-01-01", "date_to": None, "sources": [],
                    "countries": [], "groups": [], "group_by": "region", "wants_votes": False,
                    "interpretation": "Statements on climate finance since 2020."},
        structured_usage=Usage(input_uncached=300, output=40))


@pytest.fixture
def site(settings, caplog):
    """The site's real pipeline over a small real index, with the scripted model, the recording embedder and the
    structured log turned on; every line any logger writes is kept too."""
    caplog.set_level(logging.DEBUG)
    clock = FixedClock(shared.START)
    handler = start_logging(settings, clock)
    write_by_year(settings.parquet_dir, "sc", [
        speech("sc:S/PV.9000:0005", shared.D, "KEN", KENYA, n=5),
        speech("sc:S/PV.9000:0006", shared.D, "FJI", "Fiji warns climate finance must reach small islands.", n=6)])
    embedder = Recording()
    indexer = Indexer(settings, countries(), embedder=embedder)
    seen = IndexSeen(indexer.index)
    indexer.index_all()
    embed_pending(settings, indexer.index, indexer.state, embedder)
    refresh_indices(indexer.index, indexer.state)
    model = scripted_model()
    pipeline = Pipeline(settings, Engine(settings, embedder=embedder, today=shared.TODAY), client=model,
                        today=shared.TODAY)
    settings.raw["usage"] = {**settings.raw["usage"], "requests_per_minute": 1000}
    db = open_state(settings, clock)
    api = TestClient(create_app(settings, pipeline=pipeline, state=db, behind_proxy=True, clock=clock))
    api.model, api.embedder, api.seen, api.db, api.settings, api.indexer = model, embedder, seen, db, settings, indexer

    def log_lines() -> list[str]:
        written = (settings.logs_dir / "briefings.jsonl")
        return [*(written.read_text().splitlines() if written.exists() else []), *caplog.text.splitlines()]

    api.log_lines = log_lines
    yield api
    logging.getLogger(LOGGER).removeHandler(handler)
    handler.close()


def test_nothing_about_the_reader_is_kept_but_the_days_code_and_the_question(site):
    address = "203.0.113.77"
    headers = {"X-Forwarded-For": f"{address}, 10.0.0.9", "User-Agent": "a-browser-like-no-other"}
    sent = shared.events_of(site.post("/api/ask", json={"question": CANARY}, headers=headers).text)
    assert [e["type"] for e in sent if isinstance(e, dict)][-1] == "final"
    with site.db.connect() as con:
        (code,) = {r[0] for r in con.execute("SELECT visitor FROM wts_spending")}
    # The package's own check: the address is in no table and no log line; the day's code only beside the spending;
    # the question only where the privacy notice says a question is kept (wts-core docs/adr/0012).
    assert_no_reader_data(site.db, site.log_lines(), question=CANARY, address=address, visitor=code)
    # a report and a statement keep no code of who sent them: the forms pass none (nothing new is collected)
    bid = [e for e in sent if isinstance(e, dict)][-1]["briefing_id"]
    assert site.post("/api/report", json={"kind": "error", "page": f"/b/{bid}", "message": "A date is wrong."},
                     headers=headers).status_code == 200
    assert site.post("/api/statements", json={
        "page": f"/b/{bid}", "name": "Ms. Amina Odhiambo", "role": "Deputy Permanent Representative",
        "text": "Our position is in A/79/123.", "email": "press@mission.example"}, headers=headers).status_code == 200
    assert_no_reader_data(site.db, site.log_lines(), question=CANARY, address=address, visitor=code)
    with site.db.connect() as con:
        assert con.execute("SELECT count(*) FROM wts_reply_counts").fetchone()[0] == 0
        everything = "".join(str([tuple(r) for r in con.execute(f"SELECT * FROM {table}")])
                             for table in sorted(site.db.tables()))
    assert "a-browser-like-no-other" not in everything and "10.0.0.9" not in everything


def test_a_statement_never_reaches_the_model_the_embedder_the_index_the_record_or_a_log(site):
    first = [e for e in shared.events_of(site.post("/api/ask", json={"question": shared.Q}).text)
             if isinstance(e, dict)]
    beside = f"/b/{first[-1]['briefing_id']}"

    def ask(question):
        # the record is indexed and embedded again first, so that anything able to reach the index has its chance
        site.indexer.index_all()
        embed_pending(site.settings, site.indexer.index, site.indexer.state, site.embedder)
        return [e for e in shared.events_of(site.post("/api/ask", json={"question": question}).text)
                if isinstance(e, dict)]

    # The package's own check (wts-core docs/adr/0008): it publishes a statement carrying a marker beside the
    # briefing, asks a question on the same subject through the site's real pipeline, and fails if the marker is in
    # any request to the model, any text given to the embedder, the index, an event sent, any table but the right
    # of reply's own, or a log line.
    assert_statements_stay_out(
        replies=site.app.state.replies, ask=ask, model=site.model, embedder=site.embedder, index=site.seen,
        state=site.db, log_lines=site.log_lines, item=beside,
        question="What has Kenya said about climate finance for adaptation since 2021?")
    assert len(site.model.requests) == 2 and site.seen.added  # the question was written afresh; the index written to
    shown = site.get("/api/statements", params={"page": beside}).json()["statements"]
    assert len(shown) == 1 and "wtsmarker" in shown[0]["text"]  # and yet the statement is shown, beside the briefing
    assert "@example.org" not in str(shown)


def test_the_privacy_page_says_what_the_site_now_keeps():
    """The sentences the owner accepted with the shared package's design, word for word (wts-core docs/adr/0012 and
    docs/adr/0008, questions 3 and 4), and the figures in them are the ones the code keeps to."""
    from wts_core.observe import OWNER_ACCEPTED

    from wcs.replies import REPLY_RULES

    page = " ".join(re.sub(r"<[^>]+>", " ", (SITE_DIR / "privacy.html").read_text()).split())
    assert ("For each briefing the site also notes how long each step took, what it cost and what the automatic "
            "checks found, so that slow or poorly checked briefings can be found and fixed. These notes hold "
            "nothing that identifies you; they are kept for 13 months, and the question of a briefing that could "
            "not be written is removed from them after 90 days.") in page
    assert (OWNER_ACCEPTED.months, OWNER_ACCEPTED.failed_question_days) == (13, 90)
    assert ("is kept while the statement is waiting or shown, and deleted three months after it is withdrawn, "
            "declined or removed") in page
    assert REPLY_RULES.email_kept_months == 3
    assert ("Backups of the site's database are kept for up to two months, so a deleted address can remain in a "
            "backup until then.") in page.replace("’", "'")
    assert BACKUPS.keep_offsite == 60  # one a night: about two months
