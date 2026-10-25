"""The web service, with the fake model client from test_pipeline (no network)."""

import json

import pytest
from fastapi.testclient import TestClient
from test_pipeline import TODAY, FakeClient, indexed  # noqa: F401  (the fixture)

from wcs.api.app import create_app
from wcs.api.usage import StateDB, cost_usd, visitor_key
from wcs.embed.base import HashingEmbedder
from wcs.engine import Engine
from wcs.rag.pipeline import Pipeline

QUESTION = {"question": "What has been said about climate finance since 2020?"}


def events(response) -> list[dict]:
    out = []
    for chunk in response.text.split("\n\n"):
        data = [ln[6:] for ln in chunk.splitlines() if ln.startswith("data: ")]
        if data:
            out.append(json.loads(data[0]))
    return out


def make(settings, client=None, **usage):
    settings.raw["usage"] = {**settings.raw.get("usage", {}), **usage}
    eng = Engine(settings, embedder=HashingEmbedder(64), today=TODAY)
    pipe = Pipeline(settings, eng, client=client, today=TODAY)
    state = StateDB(settings.data_dir / "state.db")
    return TestClient(create_app(settings, pipeline=pipe, state=state)), state


def test_meta_and_lists(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    assert api.get("/api/health").json() == {"ok": True}
    meta = api.get("/api/meta").json()
    assert meta["briefings"] is True and meta["coverage"]["sc"]["speeches"] == 2
    countries = api.get("/api/countries").json()
    assert any(c["code"] == "KEN" and c["name"] == "Kenya" for c in countries)
    assert any(g["key"] == "p5" for g in api.get("/api/groups").json())


def test_search_costs_nothing(indexed):  # noqa: F811
    client = FakeClient()
    api, _ = make(indexed, client)
    out = api.post("/api/search", json=QUESTION).json()
    assert out["plan"]["parser"] == "offline" and len(out["sources"]) == 2
    assert client.calls == []


def test_ask_streams_records_spend_and_caches(indexed):  # noqa: F811
    client = FakeClient()
    api, state = make(indexed, client)
    evs = events(api.post("/api/ask", json=QUESTION))
    assert [e["type"] for e in evs][:2] == ["plan", "sources"] and evs[-1]["type"] == "final"
    assert any(e["type"] == "text" for e in evs) and evs[-1]["answer"]["citations_total"] == 1
    with state.connect() as con:
        kind, cost = con.execute("SELECT kind, cost_usd FROM usage").fetchone()
    assert kind == "answer" and cost == pytest.approx(cost_usd({"input_tokens": 1000, "output_tokens": 200},
                                                               indexed.usage, "claude-sonnet-5-5"))
    again = events(api.post("/api/ask", json=QUESTION))
    assert again[-1]["cached"] is True and len(client.calls) == 1   # answered from the cache


def test_caps_fall_back_to_sources(indexed):  # noqa: F811
    client = FakeClient()
    api, _ = make(indexed, client, daily_spend_cap_usd=0.0)
    evs = events(api.post("/api/ask", json=QUESTION))
    assert evs[-1]["answer"] is None and "budget" in evs[-1]["note"]
    assert len(evs[1]["sources"]) == 2 and client.calls == []


def test_rate_limit(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient(), requests_per_minute=2)
    assert [api.post("/api/search", json=QUESTION).status_code for _ in range(3)] == [200, 200, 429]


def test_without_an_api_key(indexed, monkeypatch):  # noqa: F811
    for name in ("WCS_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    api, _ = make(indexed, None)
    evs = events(api.post("/api/ask", json=QUESTION))
    assert evs[-1]["answer"] is None and "no Anthropic API key" in evs[-1]["note"]


def test_visitor_codes_change_daily(tmp_path):
    state = StateDB(tmp_path / "s.db")
    a, b = visitor_key(state, "203.0.113.5", "2026-10-24"), visitor_key(state, "203.0.113.5", "2026-10-25")
    assert a != b and "203.0.113.5" not in a


def test_question_length_is_checked(indexed):  # noqa: F811
    api, _ = make(indexed, FakeClient())
    assert api.post("/api/ask", json={"question": "x" * 501}).status_code == 422
