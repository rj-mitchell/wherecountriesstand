"""Checked briefings as the family shows them (the charity's family/BRIEFINGS.md, kit 2.1.0, and the review plan of
21 November 2026, B3 to B6): the display rule for quotations on every answer a reader is shown, the check strip's
counts, the share card of a saved briefing, and the home page's proof of the checking.

The example is a fixed copy of saved briefing kGfKBI3cRLw (the veto, 18 November 2026), taken on 21 November 2026
(tests/fixtures/). In it the model attached Italy's citation to "in November 2025" rather than to Italy's quotation,
which was found word for word in that source, and the page showed the quotation as if nothing supported it. The
renderer's own tests (web/tests/briefing.test.js, run with ``node --test web/tests/``) read the answer as the server
shows it from web/tests/fixtures/kGfKBI3cRLw-shown.json, which the first test here checks is exactly what the server
sends: set WCS_WRITE_FIXTURES=1 to write it again after an intended change."""

import json
import os
import re
from dataclasses import asdict
from pathlib import Path

from test_api import QUESTION, events, make
from test_pipeline import FakeClient, indexed  # noqa: F401  (the fixture)
from wts_core.checks import CheckCounts, check_counts
from wts_core.dates import long_date

from wcs.api.app import SITE_DIR, SiteHooks

ROOT = Path(__file__).parents[1]
SAVED = json.loads((ROOT / "tests" / "fixtures" / "countries-kGfKBI3cRLw-2026-11-21.json").read_text())
SHOWN = ROOT / "web" / "tests" / "fixtures" / "kGfKBI3cRLw-shown.json"
ITALY = "“supports all initiatives that seek to impose self-limitations on the use of veto”"
# a quotation of three words or more, as the verifier counts them (wts-core checks.verify)
QUOTE = re.compile(r'["“]([^"“”\n]{6,}?)["”]')


def final_of(evs):
    return next(e for e in evs if e.get("type") == "final")


def quotes_with_no_source(answer):
    """The quotations found word for word that are shown inside a passage marked as having no source."""
    out = []
    for seg in answer["segments"]:
        if seg["status"] not in ("unsupported", "invalid") or seg.get("misquotes"):
            continue
        out += [q for q in QUOTE.findall(seg["text"]) if len(q.split()) >= 3]
    return out


def save_example(api):
    api.app.state.parts.archive.save(SAVED["question"], SAVED["options"], SAVED["events"], id=SAVED["id"])


def test_a_quotation_found_word_for_word_is_never_shown_as_having_no_source(indexed):  # noqa: F811
    api, _ = make(indexed)
    save_example(api)
    saved = final_of(SAVED["events"])["answer"]
    assert quotes_with_no_source(saved)  # as it was saved: Italy's quotation inside a passage with no source
    shown = final_of(api.get(f"/api/briefings/{SAVED['id']}").json()["events"])["answer"]
    assert quotes_with_no_source(shown) == []
    italy = next(s for s in shown["segments"] if s["text"] == ITALY)
    assert italy["status"] == "cited" and italy["sources"] == [1] and italy["beside"] is True
    # the words around it keep their mark, and the checks' own counts are unchanged
    assert shown["segments"][shown["segments"].index(italy) - 1]["status"] == "unsupported"
    assert {k: v for k, v in shown.items() if k != "segments"} == {k: v for k, v in saved.items() if k != "segments"}
    assert "".join(s["text"] for s in shown["segments"]) == "".join(s["text"] for s in saved["segments"])
    # the strip's counts: 16 of 16 citations, 15 of 15 quotations, 2 sentences without a source
    assert check_counts(shown) == CheckCounts(16, 16, 15, 15, 2)
    # the saved copy keeps the verifier's own segments: only what is shown changes
    kept = final_of(api.app.state.parts.archive.get(SAVED["id"]).events)["answer"]
    assert kept["segments"] == saved["segments"]
    # what the renderer's tests read is exactly what the server sends
    fixture = {"id": SAVED["id"], "answer": shown, "counts": asdict(check_counts(shown))}
    if os.environ.get("WCS_WRITE_FIXTURES"):
        SHOWN.write_text(json.dumps(fixture, ensure_ascii=False, indent=1) + "\n")
    assert json.loads(SHOWN.read_text()) == fixture


def test_new_cached_and_saved_briefings_are_all_shown_through_the_display_rule(indexed, monkeypatch):  # noqa: F811
    """A new briefing's final event, the same question again from the cache, and the saved briefing at its address
    each show the quotation as its source's; the copies kept are the verifier's. Version 2 of the checks reads such
    a passage, written in one go, as a lead-in to its citation and does not flag it; a briefing checked under
    version 1, as kGfKBI3cRLw was, is not judged again in this respect when shown. So the checks' answer is given
    here as kGfKBI3cRLw's was."""
    import wcs.rag.pipeline as pipeline

    quotation = "“climate finance for adaptation is too slow”"
    answer = {"segments": [
        {"text": f"Several small States spoke of finance for adaptation, as when Kenya said {quotation}", "sources": [],
         "cited": [], "status": "unsupported", "loaded": [], "misquotes": []},
        {"text": " in 2024", "sources": [1], "cited": [], "status": "cited", "loaded": [], "misquotes": []},
        {"text": ".", "sources": [], "cited": [], "status": "connective", "loaded": [], "misquotes": []}],
        "markdown": "", "no_records": False, "citations_total": 1, "citations_invalid": 0, "grounded_ratio": 0.5,
        "unsupported_segments": 1, "loaded_words": 0, "quotes_total": 1, "quotes_unverified": 0, "rules": 2}
    monkeypatch.setattr(pipeline, "checked", lambda blocks, sources: json.loads(json.dumps(answer)))
    api, _ = make(indexed, FakeClient())
    first = final_of(events(api.post("/api/ask", json=QUESTION)))
    again = final_of(events(api.post("/api/ask", json=QUESTION)))
    bid = first["briefing_id"]
    saved = final_of(api.get(f"/api/briefings/{bid}").json()["events"])
    assert again.get("cached") and again["briefing_id"] == bid
    for final in (first, again, saved):
        segs = final["answer"]["segments"]
        assert [s["status"] for s in segs] == ["unsupported", "cited", "cited", "connective"], segs
        assert segs[1]["text"] == quotation and segs[1]["beside"] is True and segs[1]["sources"] == [1]
        assert quotes_with_no_source(final["answer"]) == []
        assert check_counts(final["answer"]) == CheckCounts(1, 1, 1, 1, 1)
    kept = final_of(api.app.state.parts.archive.get(bid).events)["answer"]["segments"]
    assert kept == answer["segments"]


def test_the_hooks_show_cached_answers_and_new_ones_through_the_display_rule():
    hooks = SiteHooks(lambda: [])
    cached = hooks.on_cached(None, SAVED["events"])
    final = hooks.for_this_reader(None, final_of(SAVED["events"]))
    for answer in (final_of(cached)["answer"], final["answer"]):
        assert any(s["text"] == ITALY and s.get("beside") for s in answer["segments"])


def test_a_saved_briefing_has_its_own_share_card(indexed, monkeypatch):  # noqa: F811
    """Drawn once by wts-core's briefing_card and kept on disk; the link preview points at it, with the site's
    neutral title and the strip's counts in words, never the reader's question (decision 7)."""
    from PIL import Image

    monkeypatch.setenv("WCS_SITE_URL", "https://countries.example")
    api, _ = make(indexed)
    save_example(api)
    bid = SAVED["id"]
    page = api.get(f"/b/{bid}").text
    written = long_date(api.app.state.parts.archive.get(bid).created_at)
    assert f'<meta property="og:image" content="https://countries.example/b/{bid}/card.png">' in page
    alt = re.search(r'<meta property="og:image:alt" content="([^"]*)">', page)[1]
    assert alt.startswith(f"Briefing from UN records, {written}: a briefing written by AI, checked against the UN record.")
    assert "16 of 16 citations matched to the record, 15 of 15 quotations found word for word, 2 sentences " \
           "without a source" in alt
    assert SAVED["question"] not in page and "veto" not in alt
    card = api.get(f"/b/{bid}/card.png")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png"
    kept = list((indexed.data_dir / "cards").glob(f"{bid}.*.png"))
    assert len(kept) == 1 and kept[0].read_bytes() == card.content
    with Image.open(kept[0]) as image:
        assert image.size == (1200, 630)
    mtime = kept[0].stat().st_mtime_ns
    assert api.get(f"/b/{bid}/card.png").content == card.content and kept[0].stat().st_mtime_ns == mtime  # drawn once
    assert api.get("/b/nosuchone/card.png").status_code == 404
    assert api.app.state.parts.archive.withdraw(bid, "A test.")
    assert api.get(f"/b/{bid}/card.png").status_code == 404
    # the home page keeps the family's card
    assert '<meta property="og:image" content="https://wherecountriesstand.com/family/card.png">' in api.get("/").text


def test_the_home_page_shows_the_checking_without_a_script(indexed):  # noqa: F811
    """The proof (family/BRIEFINGS.md, "The home page"): three steps, a real sentence from saved briefing
    kGfKBI3cRLw with its record quotation and chip, beside the passage it came from with the same words highlighted,
    and a link to the methodology page, all in the page's HTML. The glossary's line and disclosure stay."""
    api, _ = make(indexed)
    home = api.get("/").text
    proof = home[home.index('<section class="wts-proof'):]
    proof = proof[:proof.index("</section>")]
    steps = re.findall(r"<li>([^<]+)</li>", proof[:proof.index("</ol>")])
    assert steps == ["Searches only the UN’s records", "AI writes only from what was found",
                     "Every citation and quotation is checked before you see it"]
    sources = next(e for e in SAVED["events"] if e["type"] == "sources")["sources"]
    answer = final_of(SAVED["events"])["answer"]
    # the sentence and its quotation are the briefing's own, and the highlighted words the record's own
    said = re.sub(r"<[^>]+>", "", re.search(r'<p class="wts-proof__said">(.*?)</p>', proof, re.S)[1])
    said = re.sub(r"\s+", " ", said)
    quoted = "it does not support expanding the category of permanent members, which “would inevitably generate " \
             "additional vetoes”"
    assert quoted in said and quoted in answer["markdown"]
    assert "Italy, Security Council, 14 November 2025" in said and "word for word" in said
    marked = re.findall(r"<mark[^>]*>([^<]+)</mark>", proof)
    assert marked == ["would inevitably generate additional vetoes"]
    assert marked[0] in sources[0]["text"] and sources[0]["symbol"] == "S/PV.10043 (Resumption 1)"
    passage = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", re.search(r'<p class="wts-passage"[^>]*>(.*?)</p>', proof, re.S)[1]))
    assert passage.strip("… ") in sources[0]["text"]
    assert "Mr. Greco</strong>, for Italy" in proof and 'href="/methodology.html#checked"' in proof
    assert "<script" not in proof
    # the glossary's line under the question box, its disclosure, and sources only as an equal choice
    assert "Briefings are written by AI from the UN’s records only, and every citation is\n" in home or \
        "Briefings are written by AI from the UN’s records only, and every citation is checked." in \
        re.sub(r"\s+", " ", home)
    assert "<summary>How the briefings are checked</summary>" in home
    assert re.search(r'<button type="submit"[^>]*value="sources"[^>]*>Sources only \(no AI\)</button>', home)
    assert '<link rel="stylesheet" href="/family/briefing.css?v=' in home


def test_the_page_carries_the_family_classes_for_a_briefing():
    page = (SITE_DIR / "index.html").read_text()
    assert 'class="result__grid wts-cloth--countries"' in page
    assert '<script src="/briefing.js" defer></script>' in page and page.index("/briefing.js") < page.index("/app.js")
    assert 'role="status"' in page and page.count('role="status"') == 1  # one region announces each stage once
