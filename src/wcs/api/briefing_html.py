"""A checked briefing as HTML written on the server, in the family's markup (family/BRIEFINGS.md in the charity's
repository, with the kit's /family/briefing.css): the check strip, the text with record quotations, source chips and
marks for text with no source, the check details at the end, and the list of sources.

It is the page script's renderer (``web/site/briefing.js``: ``strip``, ``briefingBlocks``, ``details``; and
``sourceCard`` in ``web/site/app.js``) written again in Python, so that a page the server writes (a topic briefing,
decision 27) shows the whole briefing to a reader without scripts and to a search engine, exactly as the script
shows it. The two must stay the same: ``tests/test_topics.py`` writes this module's HTML for two briefings into
``web/tests/fixtures/server-briefings.json``, and ``web/tests/briefing.test.js`` checks that the script makes the
same HTML from them. Change both together.

Every piece of text is escaped: nothing from the record or the model is ever written as markup.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from wts_core.dates import long_date as _long_date

# ---------- elements as plain trees, as briefing.js's h() and toHTML() ----------


@dataclass
class Node:
    tag: str
    attrs: dict[str, Any]
    children: list[Any] = field(default_factory=list)


def _flat(children: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    for c in children:
        if isinstance(c, (list, tuple)):
            out += _flat(c)
        elif c is None or c is False or c == "":
            continue
        else:
            out.append(c)
    return out


def h(tag: str, attrs: Mapping[str, Any] | None = None, *children: Any) -> Node:
    """A tree node, as briefing.js's ``h``: children are flattened, and None, False and "" are left out."""
    return Node(tag, dict(attrs or {}), _flat(children))


def esc(s: Any) -> str:
    return html.escape(str(s), quote=False).replace('"', "&quot;")


VOID = frozenset({"br"})


def to_html(node: Any) -> str:
    """The tree as HTML, as briefing.js's ``toHTML`` (and ``<br>`` as HTML writes it)."""
    if isinstance(node, str):
        return esc(node)
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        return esc(node)
    attrs = "".join(f" {k}" if v is True else f' {k}="{esc(v)}"' for k, v in node.attrs.items()
                    if v is not None and v is not False)
    if node.tag in VOID:
        return f"<{node.tag}{attrs}>"
    return f"<{node.tag}{attrs}>{''.join(to_html(c) for c in node.children)}</{node.tag}>"


def html_of(nodes: Any) -> str:
    return "".join(to_html(n) for n in (nodes if isinstance(nodes, list) else [nodes]))


# ---------- words ----------


def long_date(iso: Any) -> str:
    """"14 November 2025", or the year alone where there is no day (as briefing.js's ``longDate``)."""
    if not iso:
        return ""
    text = str(iso)[:10]
    parts = text.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts) and int(parts[1]) and int(parts[2]):
        return _long_date(text)
    return parts[0] if parts and parts[0].isdigit() and int(parts[0]) else ""


def plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def source_date(s: Mapping[str, Any]) -> str:
    return str(s.get("date") or "")[:4] if s.get("date_precision") == "session" else long_date(s.get("date"))


def vote_words(s: Mapping[str, Any]) -> list[str]:
    """A vote's kind and body: "Recorded vote", "General Assembly"; a Security Council vote (decision 30) is "Vote",
    "Security Council", as its records do not call it a recorded vote."""
    return ["Vote", "Security Council"] if s.get("body") == "Security Council" else ["Recorded vote", "General Assembly"]


def chip_label(s: Mapping[str, Any] | None) -> str:
    if not s:
        return ""
    if s.get("kind") == "vote":
        return ", ".join([*vote_words(s), long_date(s.get("date"))])
    who = s.get("on_behalf_of") or s.get("country_name") or s.get("speaker_name") or "Speaker"
    return ", ".join(x for x in (who, s.get("body"), source_date(s)) if x)


# ---------- quotations, as the verifier finds them (wts-core checks.verify) ----------

QUOTE = re.compile(r"[\"“]([^\"“”\n]{6,}?)[\"”]|‘([^‘’\n]{6,}?)’(?![a-z])")


def words_in(s: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", re.sub(r"[’']", "", s.lower())) if w]


def quotations(segments: list[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    starts, pos = [], 0
    for s in segments:
        starts.append(pos)
        pos += len(s.get("text") or "")
    full = "".join(s.get("text") or "" for s in segments)
    out: list[list[dict[str, Any]]] = [[] for _ in segments]
    for m in QUOTE.finditer(full):
        quote = m[1] or m[2]
        if len(words_in(quote)) < 3:
            continue
        a, b = m.start(), m.end()
        sources: list[int] = []
        for i, s in enumerate(segments):
            s0 = starts[i]
            s1 = s0 + len(s.get("text") or "")
            if (s0 < b and a < s1) or (s0 >= b and s0 - b <= 3 and (s.get("sources") or [])):
                for n in s.get("sources") or []:
                    if n not in sources:
                        sources.append(n)
        for i, s in enumerate(segments):
            s0 = starts[i]
            s1 = s0 + len(s.get("text") or "")
            if not (s0 < b and a < s1):
                continue
            start, end = max(a, s0) - s0, min(b, s1) - s0
            missing = any(x["start"] < end and start < x["end"] for x in s.get("misquotes") or [])
            out[i].append({"start": start, "end": end, "quote": quote, "missing": missing, "ends_here": b <= s1,
                           "sources": sources})
    return out


def flagged(seg: Mapping[str, Any]) -> bool:
    return seg.get("status") in ("unsupported", "invalid")


def _blank(seg: Mapping[str, Any]) -> bool:
    return seg.get("status") == "connective" and not (seg.get("text") or "").strip()


def counts(answer: Mapping[str, Any]) -> dict[str, int]:
    total, quotes = int(answer.get("citations_total") or 0), int(answer.get("quotes_total") or 0)
    return {"citations_matched": total - int(answer.get("citations_invalid") or 0), "citations_total": total,
            "quotes_found": quotes - int(answer.get("quotes_unverified") or 0), "quotes_total": quotes,
            "no_source": sum(1 for s in answer.get("segments") or [] if flagged(s))}


def firsts(answer: Mapping[str, Any]) -> dict[str, str | None]:
    segs = answer.get("segments") or []
    nosource = next((i for i, s in enumerate(segs) if flagged(s)), None)
    invalid = next((i for i, s in enumerate(segs) if s.get("status") == "invalid"), None)
    missing = next((f"misquote-{i}-{q['start']}" for i, qs in enumerate(quotations(segs)) for q in qs
                    if q["missing"]), None)
    return {"nosource": None if nosource is None else f"nosource-{nosource}",
            "invalid": None if invalid is None else f"nosource-{invalid}", "missing": missing}


# ---------- the briefing's text ----------


def _sources_of(seg: Mapping[str, Any]) -> list[Any]:
    return list(seg.get("sources") or [])


def chips_shown(segments: list[Mapping[str, Any]]) -> list[bool]:
    out = []
    for i, seg in enumerate(segments):
        if seg.get("status") != "cited" or not _sources_of(seg):
            out.append(False)
            continue
        j = i + 1
        while j < len(segments) and _blank(segments[j]):
            j += 1
        nxt = segments[j] if j < len(segments) else None
        same = nxt is not None and nxt.get("status") == "cited" and \
            ",".join(map(str, _sources_of(nxt))) == ",".join(map(str, _sources_of(seg)))
        out.append(not same)
    return out


def lines(segments: list[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    out: list[list[dict[str, Any]]] = [[]]
    shows = chips_shown(segments)
    for i, seg in enumerate(segments):
        off, last = 0, None
        for k, part in enumerate((seg.get("text") or "").split("\n")):
            if k > 0:
                out.append([])
            if part:
                out[-1].append({"seg": i, "start": off, "end": off + len(part)})
                last = out[-1]
            off += len(part) + 1
        if shows[i]:
            (last if last is not None else out[-1]).append({"chips": _sources_of(seg), "seg": i})
    return out


def chip(n: Any, source: Mapping[str, Any] | None, seg: int) -> Node:
    label = chip_label(source) or f"Source {n}"
    return h("button", {"type": "button", "class": "wts-chip", "data-source": n, "data-seg": seg,
                        "data-wts-conversion": "source_opened"},
             h("span", {"class": "wts-chip__in"},
               h("span", {"class": "wts-chip__n"}, h("span", {"class": "wts-vh"}, "Source"), f" {n}"),
               " ", h("span", {}, label)),
             h("span", {"class": "wts-vh"}, ". Show the passage"))


LOADED_NOTE = " (a judgemental word in the AI’s own voice)"


def _piece_nodes(seg: Mapping[str, Any], i: int, start: int, end: int, quotes: list[dict[str, Any]],
                 ctx: dict[str, Any]) -> list[Any]:
    text = seg["text"][start:end]
    drop: set[int] = set()
    bold: list[tuple[int, int]] = []
    for m in re.finditer(r"\*\*([^*]+)\*\*", text):
        bold.append((m.start() + 2, m.start() + 2 + len(m[1])))
        drop.update((m.start(), m.start() + 1, m.end() - 2, m.end() - 1))

    def local(a: int, b: int) -> tuple[int, int]:
        return max(a, start) - start, min(b, end) - start

    qs = [{**q, "range": local(q["start"], q["end"])} for q in quotes if q["start"] < end and start < q["end"]]
    loaded = [local(x["start"], x["end"]) for x in seg.get("loaded") or [] if x["start"] < end and start < x["end"]]
    cuts = {0, len(text)}
    for a, b in [*bold, *loaded, *(q["range"] for q in qs)]:
        cuts.update((a, b))
    at = sorted(cuts)
    runs = []
    for k in range(len(at) - 1):
        a, b = at[k], at[k + 1]
        t = "".join(text[x] for x in range(a, b) if x not in drop)
        if not t:
            continue
        q = next((n for n, r in enumerate(qs) if r["range"][0] <= a and b <= r["range"][1]), -1)
        runs.append({"t": t, "quote": q, "bold": any(s <= a and b <= e for s, e in bold),
                     "loaded": any(s <= a and b <= e for s, e in loaded)})

    def run_node(r: dict[str, Any]) -> Any:
        n: Any = r["t"]
        if r["loaded"]:
            n = h("span", {"class": "loaded"}, n, h("span", {"class": "wts-vh"}, LOADED_NOTE))
        return h("strong", {}, n) if r["bold"] else n

    out: list[Any] = []
    k = 0
    while k < len(runs):
        q = runs[k]["quote"]
        j = k
        while j < len(runs) and runs[j]["quote"] == q:
            j += 1
        inner = [run_node(r) for r in runs[k:j]]
        out += inner if q < 0 else _quote_node(qs[q], seg, i, inner, ctx)
        k = j
    return out


def _quote_node(q: dict[str, Any], seg: Mapping[str, Any], i: int, inner: list[Any], ctx: dict[str, Any]) -> list[Any]:
    n = q["sources"][0] if q["sources"] else None
    where = f"source {n}" if n else "its source"
    if q["missing"]:
        ident = f"misquote-{i}-{q['start']}"
        fresh = ident not in ctx["missing_ids"]
        ctx["missing_ids"].add(ident)
        return [h("span", {"class": "wts-quote wts-quote--missing", "id": ident if fresh else None, "data-seg": i},
                  inner,
                  q["ends_here"] and h("span", {"class": "wts-quote__tick", "aria-hidden": "true"},
                                       "Not found word for word"),
                  q["ends_here"] and h("span", {"class": "wts-vh"},
                                       f" (quotation not found word for word in {where})"))]
    if not n or flagged(seg):
        return inner
    return [h("span", {"class": "wts-quote wts-quote--checked", "data-seg": i, "data-source": n},
              inner,
              q["ends_here"] and h("span", {"class": "wts-quote__tick", "aria-hidden": "true"}, "✓ word for word"),
              q["ends_here"] and h("span", {"class": "wts-vh"}, f" (quotation, found word for word in {where})"))]


LEAD = re.compile(r"^\s*(?:[-*•]|#+)\s+")
TRAIL = re.compile(r"\S(\s+)$")  # the space a passage with no source ends with


def briefing_blocks(answer: Mapping[str, Any], sources_by_n: Mapping[Any, Mapping[str, Any]]) -> list[Node]:
    """The briefing's text as blocks (headings, paragraphs and lists), as briefing.js's ``briefingBlocks``."""
    segments = answer.get("segments") or []
    quotes = quotations(segments)
    ctx: dict[str, Any] = {"missing_ids": set(),
                           "first_no_source": next((i for i, s in enumerate(segments) if flagged(s)), -1)}
    every = lines(segments)
    last_piece: dict[int, str] = {}
    for li, line in enumerate(every):
        for pi, p in enumerate(line):
            if "chips" not in p:
                last_piece[p["seg"]] = f"{li}:{pi}"
    seen: set[int] = set()

    def inline_of(line: list[dict[str, Any]], li: int) -> list[Any]:
        out: list[Any] = []
        for pi, p in enumerate(line):
            if "chips" in p:
                out.append("⁠")
                out += [chip(n, sources_by_n.get(n), p["seg"]) for n in p["chips"]]
                continue
            seg = segments[p["seg"]]
            start = p["start"]
            if pi == 0:  # a list item's or heading's marker is not text
                lead = LEAD.match(seg["text"][p["start"]:p["end"]])
                if lead:
                    start += len(lead[0])
            if not flagged(seg):
                out += _piece_nodes(seg, p["seg"], start, p["end"], quotes[p["seg"]], ctx)
                continue
            # the space a passage ends with goes after its mark, so that the underline stops at its last word and
            # the "No source" tag never touches the next word
            trail = TRAIL.search(seg["text"][start:p["end"]])
            end = p["end"] - len(trail[1]) if trail else p["end"]
            nodes = _piece_nodes(seg, p["seg"], start, end, quotes[p["seg"]], ctx)
            first = p["seg"] not in seen
            seen.add(p["seg"])
            last = last_piece.get(p["seg"]) == f"{li}:{pi}"
            why = ("its citation did not match the source and was removed" if seg.get("status") == "invalid"
                   else "no source")
            out.append(h("span", {"class": "wts-nosource", "id": f"nosource-{p['seg']}" if first else None,
                                  "data-seg": p["seg"]},
                         nodes,
                         last and p["seg"] == ctx["first_no_source"]
                         and h("span", {"class": "wts-nosource__tag", "aria-hidden": "true"}, "No source"),
                         last and h("span", {"class": "wts-vh"}, f" ({why})")))
            if trail:
                out.append(" ")
        return out

    blocks: list[Node] = []
    para: Node | None = None
    listed: Node | None = None
    for li, line in enumerate(every):
        plain = "".join("" if "chips" in p else segments[p["seg"]]["text"][p["start"]:p["end"]] for p in line).strip()
        cites = any("chips" in p for p in line)
        if not plain and not cites:
            para = listed = None
            continue
        if not cites and (re.match(r"^#+\s", plain) or re.match(r"^\*\*[^*]+\*\*:?$", plain)):
            blocks.append(h("h2", {"class": "briefing-head"},
                            re.sub(r"^\*\*|\*\*:?$", "", re.sub(r"^#+\s*", "", plain))))
            para = listed = None
            continue
        if re.match(r"^[-*•]\s", plain):
            if listed is None:
                listed = h("ul", {})
                blocks.append(listed)
            listed.children.append(h("li", {}, inline_of(line, li)))
            para = None
            continue
        listed = None
        if para is None:
            para = h("p", {})
            blocks.append(para)
        else:
            para.children.append(" ")
        para.children += inline_of(line, li)
    return blocks


# ---------- the check strip, and the check details at the end ----------


def _strip_item(*, shown: int, total: int | None, words: str, problem: bool, first: str | None) -> Node:
    return h("li", {"class": "wts-check__item"},
             h("span", {"class": "wts-check__count"},
               h("span", {"class": "wts-check__glyph", "aria-hidden": "true"}, "!" if problem else "✓"),
               h("span", {"class": "wts-check__num"}, str(shown)),
               total is not None and h("span", {}, f"of {total}"),
               problem and h("span", {"class": "wts-check__badge"}, "To check")),
             h("span", {}, words,
               problem and first and [". ", h("a", {"class": "wts-check__first", "href": f"#{first}"},
                                              "Show the first")]))


def strip(answer: Mapping[str, Any]) -> Node:
    """The strip: the three counts in words, under the title that names the record (briefing.js's ``strip``, with
    the counts finished)."""
    c, f = counts(answer), firsts(answer)
    loaded = int(answer.get("loaded_words") or 0)
    return h("section", {"class": "wts-check", "aria-labelledby": "check-title", "id": "check"},
             h("div", {"class": "wts-check__head"},
               h("h2", {"class": "wts-check__title", "id": "check-title"}, "Checked against the UN record"),
               h("a", {"class": "wts-check__more", "href": "#check-details"}, "How it was checked")),
             h("ul", {"class": "wts-check__list", "aria-hidden": None},
               _strip_item(shown=c["citations_matched"], total=c["citations_total"],
                           words=plural(c["citations_total"], "citation matched to the record",
                                        "citations matched to the record"),
                           problem=c["citations_matched"] < c["citations_total"], first=f["invalid"]),
               _strip_item(shown=c["quotes_found"], total=c["quotes_total"],
                           words=plural(c["quotes_total"], "quotation found word for word",
                                        "quotations found word for word"),
                           problem=c["quotes_found"] < c["quotes_total"], first=f["missing"]),
               _strip_item(shown=c["no_source"], total=None,
                           words=plural(c["no_source"], "sentence without a source", "sentences without a source"),
                           problem=c["no_source"] > 0, first=f["nosource"])),
             loaded > 0 and h("p", {"class": "check-note"},
                              f"{loaded} {plural(loaded, 'judgemental word', 'judgemental words')} in the AI’s own "
                              f"voice {plural(loaded, 'is', 'are')} underlined: check {plural(loaded, 'it', 'them')} "
                              "against the record."))


def details(answer: Mapping[str, Any], found: int, briefing_id: str | None = None) -> Node:
    """"How this briefing was checked", at the end: the verifier's own counts, and what each mark means. As the end
    of a finished briefing it carries the family kit's mark for a briefing read, with the briefing's id, inert unless
    measurement is on (decision 28)."""
    total, invalid = int(answer.get("citations_total") or 0), int(answer.get("citations_invalid") or 0)
    quotes, missing = int(answer.get("quotes_total") or 0), int(answer.get("quotes_unverified") or 0)
    unsupported, loaded = int(answer.get("unsupported_segments") or 0), int(answer.get("loaded_words") or 0)
    beside = sum(1 for s in answer.get("segments") or [] if s.get("beside"))
    p = plural
    points = [
        f"The AI was given only the {found} {p(found, 'record', 'records')} found for the question, listed under "
        "Sources, and may use nothing else." if found else None,
        f"Each of the {total} {p(total, 'citation', 'citations')} was checked against the text of its source: "
        + (f"{invalid} did not match, so {p(invalid, 'it was', 'they were')} removed, and the words "
           f"{p(invalid, 'it was', 'they were')} attached to are marked “No source”: check the record before relying "
           "on them." if invalid else "all matched."),
        f"Each of the {quotes} {p(quotes, 'quotation', 'quotations')} was looked for word for word in its source: "
        + (f"{missing} could not be found and {p(missing, 'is', 'are')} marked “Not found word for word” with a wavy "
           "underline." if missing else "all were found, and each is marked “✓ word for word”.") if quotes else None,
        f"{unsupported} {p(unsupported, 'sentence', 'sentences')} that no source supports {p(unsupported, 'is', 'are')} "
        "marked with a dotted underline, and the first with “No source”. Short joining phrases are not counted."
        if unsupported else "No sentence was found without a source. Short joining phrases are not counted.",
        (f"{'A quotation' if beside == 1 else f'{beside} quotations'} found word for word in the source cited straight "
         f"after {'it is' if beside == 1 else 'them are'} shown as that source’s quotation; the words around "
         f"{'it keep their' if beside == 1 else 'them keep their'} marks.") if beside else None,
        f"{loaded} {p(loaded, 'judgemental word', 'judgemental words')} in the AI’s own voice "
        f"{p(loaded, 'is', 'are')} underlined." if loaded else None,
        "Some errors can still get through: check the record before relying on a point.",
    ]
    return h("section", {"class": "check-details", "id": "check-details", "aria-labelledby": "check-details-head",
                         "data-wts-conversion": "briefing_read", "data-wts-briefing": briefing_id},
             h("h2", {"id": "check-details-head"}, "How this briefing was checked"),
             h("ul", {}, [h("li", {}, x) for x in points if x]),
             h("p", {}, h("a", {"href": "/methodology.html#checked"},
                           "How every briefing is checked, on the methodology page")))


# ---------- the sources under the briefing (app.js's ``sourceCard``) ----------

TEXT_NOTES = {
    "delegation": "Text issued by the delegation, as prepared; not yet the official record.",
    "annex": "A statement shown as a recorded video: its text is printed in an annex to the record, as provided by "
             "the delegation.",
    "transcript": "AI transcript of the UN’s English interpretation; not yet the official record.",
    "corpus": "From the UN General Debate Corpus (the delegation’s text or a transcript); not yet the official "
              "record.",
}


def source_card(s: Mapping[str, Any]) -> Node:
    """One source in the list under the briefing, the printed reference list, as the page's script writes it."""
    n = s.get("n")
    if s.get("kind") == "vote":
        t = s.get("totals") or {}
        return h("li", {"class": "source source--vote", "id": f"s{n}"},
                 h("p", {"class": "source__meta"},
                   f"{vote_words(s)[1]}, {vote_words(s)[0].lower()}, {long_date(s.get('date'))}"),
                 h("p", {"class": "source__speaker"},
                   h("b", {}, s.get("vote_name") or f"Resolution {s.get('resolution')}"), f" {s.get('title') or ''}"),
                 h("p", {"class": "procedural"},
                   f"{t.get('yes')} in favour, {t.get('no')} against, {t.get('abstain')} abstaining"),
                 h("p", {"class": "source__links"},
                   h("a", {"href": s.get("url"), "rel": "noopener", "data-wts-conversion": "source_opened"},
                     "UN Digital Library record")))
    when = None if s.get("date_precision") == "session" else long_date(s.get("date"))
    rows = [", ".join(x for x in row if x) for row in ((s.get("body"), s.get("meeting")), (when, s.get("symbol")),
                                                        (s.get("agenda"),))]
    meta: list[Any] = []
    for k, row in enumerate(r for r in rows if r):
        meta += [h("br"), row] if k else [row]
    who: list[Any] = [h("b", {}, s.get("speaker_name") or "Speaker")]
    if s.get("country_name"):
        who.append(f" ({s['country_name']})")
    if s.get("on_behalf_of"):
        who.append(f", on behalf of the {s['on_behalf_of']}")
    if s.get("language"):
        who += [" ", h("i", {}, f"(spoke in {s['language']})")]
    who.append(":")
    text = s.get("text") or ""
    words = re.split(r"\s+", text)
    short = " ".join(words[:90]) + (" …" if len(words) > 90 else "")
    notes = [x for x in ("English text: UN translation." if s.get("language") else None,
                         TEXT_NOTES.get(s.get("text_source") or ""),
                         "Text recognised from a scanned record." if s.get("ocr") else None) if x]
    return h("li", {"class": f"source{' source--ga' if s.get('body') == 'General Assembly' else ''}", "id": f"s{n}"},
             h("p", {"class": "source__meta"}, meta),
             h("p", {"class": "source__speaker"}, who),
             h("blockquote", {}, short),
             len(words) > 90 and h("details", {"class": "source__more"}, h("summary", {}, "Full statement"),
                                   h("p", {}, text)),
             notes and h("p", {"class": "source__notes"}, " ".join(notes)),
             h("p", {"class": "source__links"},
               h("a", {"href": s.get("url"), "rel": "noopener", "data-wts-conversion": "source_opened"},
                 f"Read {s['symbol']}" if s.get("symbol") else "Read the source")))


# ---------- the question as read (app.js's ``chips`` and ``summary``) ----------

BODY = {"sc": "Security Council", "gd": "General Assembly"}


def plan_chips(plan: Mapping[str, Any], names: Mapping[str, str] | None = None,
               groups: Mapping[str, str] | None = None) -> list[Node]:
    f = plan.get("filters") or {}
    names, groups = names or {}, groups or {}
    out = [h("li", {"class": f"chip chip--{'sc' if s == 'sc' else 'ga'}"}, BODY.get(s, s)) for s in f.get("sources") or []]
    if not f.get("sources"):
        out.append(h("li", {"class": "chip"}, "Security Council and General Assembly"))
    if f.get("date_from") or f.get("date_to"):
        a = str(f["date_from"])[:4] if f.get("date_from") else "1946"
        b = str(f["date_to"])[:4] if f.get("date_to") else "today"
        out.append(h("li", {"class": "chip"}, a if a == b else f"{a} to {b}"))
    out += [h("li", {"class": "chip"}, names.get(c, c)) for c in f.get("countries") or []]
    out += [h("li", {"class": "chip"}, groups.get(g, g)) for g in f.get("groups") or []]
    if not f.get("countries") and not f.get("groups"):
        out.append(h("li", {"class": "chip"}, "All countries"))
    return out


def found_words(records: object, countries: object, meetings: object) -> str:
    """"24 statements from 19 countries and groups at 12 meetings", in the singular where a count is one; the page's
    script says the same (app.js, foundWords)."""
    def one(n: object, single: str, many: str) -> str:
        return f"{n} {single if n == 1 else many}"
    return (f"{one(records, 'statement', 'statements')} from {one(countries, 'country or group', 'countries and groups')}"
            f" at {one(meetings, 'meeting', 'meetings')}")


def evidence_words(evidence: Mapping[str, Any] | None) -> str:
    """What was found, as the page's script sums it up under the filters ("" when nothing is to be said)."""
    e = evidence or {}
    if not e.get("records"):
        return ""
    parts = [found_words(e['records'], e.get('countries'), e.get('meetings'))]
    if e.get("date_from"):
        parts.append(f", {long_date(e['date_from'])} to {long_date(e.get('date_to'))}")
    if e.get("votes"):
        parts.append(f", and {e['votes']} recorded vote{'s' if e['votes'] > 1 else ''}")
    parts.append(".")
    named = [f"{x['name']} ({x['records']} statement{'s' if x['records'] > 1 else ''})" if x.get("records")
             else f"{x['name']} (no relevant statement found)" for x in e.get("named") or []]
    if named:
        parts.append(f" Named in the question: {', '.join(named)}.")
    parts.append(" The site counts these from the record itself.")
    return "".join(parts)
