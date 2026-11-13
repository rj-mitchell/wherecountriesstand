from datetime import date
from types import SimpleNamespace as NS

import pytest
from conftest import speech

from wcs.embed.base import HashingEmbedder
from wcs.engine import Engine
from wcs.index.build import Indexer, embed_pending
from wcs.rag.pipeline import AskRequest, Pipeline, answer_once
from wcs.reference.countries import countries
from wcs.store.parquet import write_by_year

TODAY = date(2026, 10, 24)
D = date(2023, 3, 1)


class FakeStream:
    def __init__(self, blocks, stop_reason="end_turn"):
        self.blocks, self.stop_reason = blocks, stop_reason

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for b in self.blocks:
            yield NS(type="content_block_delta", delta=NS(type="text_delta", text=b["text"]))
            for c in b.get("citations", []):
                yield NS(type="content_block_delta", delta=NS(type="citations_delta", citation=NS(**c)))

    def get_final_message(self):
        content = [NS(type="text", text=b["text"], citations=[NS(type="search_result_location", **c)
                                                              for c in b.get("citations", [])])
                   for b in self.blocks]
        return NS(content=content, stop_reason=self.stop_reason, model="claude-sonnet-5-5",
                  usage=NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0))


class FakeClient:
    """Answers by citing the first block of the first source it is given, then adds a loaded word."""

    def __init__(self):
        self.calls = []
        self.beta = NS(messages=NS(stream=self._stream))
        self.messages = NS(stream=self._stream, parse=self._parse)

    def _parse(self, **kw):
        raise RuntimeError("force offline parse in tests")

    def _stream(self, **kw):
        self.calls.append(kw)
        first = next(b for b in kw["messages"][0]["content"] if b["type"] == "search_result")
        quote = first["content"][0]["text"]
        return FakeStream([
            {"text": "Kenya said "},
            {"text": quote[:60], "citations": [{"search_result_index": 0, "cited_text": quote, "source": "x"}]},
            {"text": " The regime was mentioned."},
        ])


@pytest.fixture
def indexed(settings):
    recs = [
        speech("sc:S/PV.9000:0005", D, "KEN", "Kenya says climate finance for adaptation is too slow.", n=5),
        speech("sc:S/PV.9000:0006", D, "FJI", "Fiji warns climate finance must reach small islands.",
               participant="guest", n=6, language="French"),
    ]
    write_by_year(settings.parquet_dir, "sc", recs)
    idx = Indexer(settings, countries())
    idx.index_all()
    embed_pending(settings, idx.index, idx.state, HashingEmbedder(64))
    return settings


def test_pipeline_end_to_end_is_grounded(indexed):
    client = FakeClient()
    eng = Engine(indexed, embedder=HashingEmbedder(64), today=TODAY)
    out = answer_once(Pipeline(indexed, eng, client=client, today=TODAY),
                      AskRequest("What has been said about climate finance since 2020?"))
    plan = out["plan"]["plan"]
    assert plan["parser"] == "offline" and plan["filters"]["date_from"] == "2020-01-01"
    cards = out["sources"]["sources"]
    assert len(cards) == 2 and cards[0]["n"] == 1
    assert any("English text is the UN's translation from French" in c["title"] for c in cards)
    final = out["final"]["answer"]
    assert final["citations_total"] == 1 and final["citations_invalid"] == 0
    assert "[Source 1]" in final["markdown"] and final["loaded_words"] == 1
    # The model only ever sees our sources (as search results) and the grounding rules.
    call = client.calls[0]
    blocks = call["messages"][0]["content"]
    assert [b["type"] for b in blocks[:-1]] == ["search_result"] * len(cards)
    assert "Use only the supplied search results" in call["system"][0]["text"]
    assert "on behalf of" in call["system"][0]["text"]


class QuotingClient(FakeClient):
    """Quotes its source in straight quotes, as the model does."""

    def _stream(self, **kw):
        self.calls.append(kw)
        first = next(b for b in kw["messages"][0]["content"] if b["type"] == "search_result")
        quote = first["content"][0]["text"]
        return FakeStream([
            {"text": "Kenya's delegate said \""},
            {"text": quote[:40], "citations": [{"search_result_index": 0, "cited_text": quote, "source": "x"}]},
            {"text": "\" in 2023."},
        ])


def test_briefings_are_written_with_curly_quotes(indexed):
    eng = Engine(indexed, embedder=HashingEmbedder(64), today=TODAY)
    out = answer_once(Pipeline(indexed, eng, client=QuotingClient(), today=TODAY),
                      AskRequest("What has Kenya said about climate finance?"))
    final = out["final"]["answer"]
    text = "".join(s["text"] for s in final["segments"])
    assert text.startswith("Kenya’s delegate said “") and "”" in text and '"' not in text and "'" not in text
    assert final["quotes_total"] == 1  # the quotation was checked before its marks were curled


class RefusingClient(FakeClient):
    """Declines to answer: no text, and the stop reason the API gives for it."""

    def _stream(self, **kw):
        self.calls.append(kw)
        return FakeStream([], stop_reason="refusal")


def test_pipeline_reports_refusals_instead_of_answering(indexed):
    """As Where MPs Stand: the model declining is an error, not an empty briefing with nothing to mark."""
    eng = Engine(indexed, embedder=HashingEmbedder(64), today=TODAY)
    out = answer_once(Pipeline(indexed, eng, client=RefusingClient(), today=TODAY),
                      AskRequest("What has been said about climate finance since 2020?"))
    assert "final" not in out and len(out["sources"]["sources"]) == 2
    assert out["error"]["message"] == "The model declined to answer this question."
    assert out["error"]["usage"]["input_tokens"] == 1000  # the call was still paid for
