"""The topic briefings' pages (decision 27), written on the server so that a reader without scripts and a search
engine see the whole briefing: its check strip, its text with record quotations, source chips and every mark, the
check details and the sources it used.

    /topics.html                 the index: every topic, by area, with the day its briefing was prepared
    /topics/<slug>.html          a topic: its newest standing briefing, in the family's design
    /topics/<slug>/card.png      its share card, with the topic's own question and the strip's counts (``wcs.cards``)

The page's script (``web/site/app.js``) then opens the same briefing from ``/api/briefings/<id>`` and adds what needs
a script: the record beside the briefing on a wide screen, and the sheet a chip opens on a phone. A topic's pages are
indexed, unlike a reader's briefing at ``/b/<id>`` (decision 7; the charity's answer 1 of 21 November 2026), because
their words are the site's own: the topic's title and its neutral question, which no reader wrote.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from wts_core.dates import long_date

from wcs.api import briefing_html as B
from wcs.config import Settings
from wcs.rag.checks import rechecked, shown
from wcs.reference.countries import Countries, countries
from wcs.topics import Current, Topic, current, load_topics, topics_file, versions

SITE_NAME = "Where Countries Stand"
SITE_MARK = "__SITE__"
INDEX_PATH = "/topics.html"
INDEX_NAME = "Topic briefings"
EARLIER = 5  # earlier versions linked from a topic's page
STATEMENTS = "<!--statements-->"
DESCRIPTION_MAX = 160
FAMILY_CARD = re.compile(r'(<meta property="og:image" content=")[^"]*(">)')
FAMILY_CARD_ALT = re.compile(r'(<meta property="og:image:alt" content=")[^"]*(">)')


def e(text: Any) -> str:
    return html.escape(str(text), quote=True)


def shorten(text: str, most: int) -> str:
    if len(text) <= most:
        return text
    return text[:most - 1].rsplit(" ", 1)[0].rstrip(" ,;:.(") + "…"


def breadcrumbs(trail: list[tuple[str, str]]) -> dict[str, Any]:
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": n, "name": name, "item": SITE_MARK + path}
                                for n, (name, path) in enumerate(trail, 1)]}


def final_and_sources(events: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any],
                                                             dict[str, Any]]:
    """A saved briefing as shown today (its marks judged again, then the display rule for quotations): its answer,
    its sources, its plan and the evidence's summary."""
    events = shown(rechecked(events))
    final = next((x for x in events if x.get("type") == "final"), {})
    found = next((x for x in events if x.get("type") == "sources"), {})
    plan = next((x for x in events if x.get("type") == "plan"), {})
    return (final.get("answer") or {}, list(found.get("sources") or []), plan.get("plan") or {},
            found.get("evidence") or {})


class TopicPages:
    """The topic pages of one site. ``frame`` is the page they are written into (``web/partials/record-page.html``),
    given once the site's pages are put together; ``hidden`` gives what is hidden now while a concern about each is
    decided."""

    def __init__(self, settings: Settings, db: Any, archive: Any, hidden: Callable[[], list[dict[str, Any]]],
                 reference: Countries | None = None):
        self.topics: list[Topic] = load_topics(topics_file(settings))
        self.db, self.archive, self.hidden = db, archive, hidden
        self._countries = reference
        self.frame = ""

    @property
    def countries(self) -> Countries:
        if self._countries is None:
            self._countries = countries()
        return self._countries

    def topic(self, slug: str) -> Topic | None:
        return next((t for t in self.topics if t.slug == slug), None)

    def current(self, slug: str) -> Current | None:
        return current(self.db, self.archive, slug, self.hidden())

    def listing(self) -> list[tuple[Topic, Current | None]]:
        hidden = self.hidden()
        return [(t, current(self.db, self.archive, t.slug, hidden)) for t in self.topics]

    # The frame

    def page(self, *, title: str, description: str, path: str, trail: list[tuple[str, str]], main: str,
             noindex: bool = False, card: tuple[str, str] | None = None, briefing: str | None = None) -> str:
        data = json.dumps(breadcrumbs(trail), ensure_ascii=False, indent=2).replace("</", "<\\/")
        data = "\n".join("    " + line for line in data.split("\n"))
        head = (f"  <title>{e(title)}</title>\n"
                f'  <meta name="description" content="{e(description)}">\n'
                f'  <link rel="canonical" href="{SITE_MARK}{path}">\n'
                f'  <meta property="og:title" content="{e(title)}">\n'
                f'  <meta property="og:description" content="{e(description)}">\n'
                f'  <meta property="og:url" content="{SITE_MARK}{path}">\n'
                + ('  <meta name="robots" content="noindex">\n' if noindex else "")
                + ('  <link rel="stylesheet" href="/family/briefing.css">\n'
                   '  <script src="/briefing.js" defer></script>\n'
                   '  <script src="/app.js" defer></script>\n' if briefing else "")
                + f'  <script type="application/ld+json">\n{data}\n  </script>\n')
        text = self.frame.replace("<!--#head-->", head.rstrip("\n"), 1).replace("<!--#main-->", main, 1)
        if card is not None:
            text = FAMILY_CARD.sub(lambda m: f"{m[1]}{SITE_MARK}{card[0]}{m[2]}", text, count=1)
            text = FAMILY_CARD_ALT.sub(lambda m: f"{m[1]}{e(card[1])}{m[2]}", text, count=1)
        if briefing:
            # the page's script opens this briefing to add the record beside it and the sheet (app.js)
            text = text.replace('<body class="wts-headings">',
                                f'<body class="wts-headings" data-briefing="{e(briefing)}">', 1)
        return text

    # The index, and the list on the home page

    def index(self) -> str:
        listing = self.listing()
        parts = [f"<h1>{INDEX_NAME}</h1>",
                 '<p class="lede">Briefings on subjects the United Nations keeps returning to, written afresh every '
                 "week by AI from the UN’s records and checked as every briefing is.</p>",
                 "<p>Each topic asks the same neutral question every week, about what countries and groups have "
                 "said and, where the record holds them, how they voted. Its page always shows the newest "
                 'briefing, with the date it was prepared and the records it used. <a href="/methodology.html'
                 '#checked">How every briefing is checked</a>, on the methodology page.</p>']
        areas: dict[str, list[tuple[Topic, Current | None]]] = {}
        for t, cur in listing:
            areas.setdefault(t.area, []).append((t, cur))
        for n, (area, rows) in enumerate(areas.items(), 1):
            parts.append(f'<h2 id="area-{n}">{e(area)}</h2><ul class="topic-index">')
            for t, cur in rows:
                when = (f"Briefing of {e(long_date(cur.prepared_at))}" if cur is not None
                        else "Not written yet")
                name = f'<a href="{t.path}">{e(t.title)}</a>' if cur is not None else e(t.title)
                parts.append(f"<li>{name}<span class=\"topic-index__meta\">{e(t.description)} {when}.</span>"
                             f'<span class="topic-index__question">{e(t.question)}</span></li>')
            parts.append("</ul>")
        parts.append('<p class="vote-named">Named in a briefing? See <a href="/named.html">If you’re named on this '
                     "site</a> to tell us something is wrong or unfair, or to add a statement in your own "
                     "words.</p>")
        description = ("Briefings on the war in Ukraine, Gaza, Sudan, climate change, nuclear weapons and other "
                       "subjects the UN keeps returning to, from the UN’s records, checked and written every week.")
        return self.page(title=f"{INDEX_NAME} | {SITE_NAME}", description=shorten(description, DESCRIPTION_MAX),
                         path=INDEX_PATH, trail=[(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH)], main="".join(parts))

    def home_section(self) -> str:
        """The home page's list of topics with a briefing, or nothing while none has one."""
        rows = [(t, cur) for t, cur in self.listing() if cur is not None]
        if not rows:
            return ""
        items = "".join(f'<li><a href="{t.path}">{e(t.title)}</a> <span class="topic-list__when">'
                        f"{e(long_date(cur.prepared_at))}</span></li>" for t, cur in rows)
        return ('<section aria-labelledby="topics-head" id="topics">'
                '<h2 id="topics-head">Topic briefings</h2>'
                "<p>Checked briefings on subjects the UN keeps returning to, written afresh every week.</p>"
                f'<ul class="topic-list">{items}</ul>'
                f'<p class="record-more"><a href="{INDEX_PATH}">All topic briefings</a></p></section>')

    # A topic's page

    def card_alt(self, t: Topic, counts_words: str) -> str:
        return (f"{t.question} A briefing written by AI, checked against the UN record. {counts_words}. "
                f"{SITE_NAME}.")

    def description(self, t: Topic, cur: Current | None) -> str:
        if cur is None:
            return shorten(f"{t.title}: {t.question}", DESCRIPTION_MAX)
        return shorten(f"{t.title}: what countries and groups have said at the UN, from the UN’s records. A "
                       f"briefing written by AI and checked, prepared on {long_date(cur.prepared_at)}.",
                       DESCRIPTION_MAX)

    def topic_page(self, slug: str, *, counts_words: str | None = None, statements: str = "") -> str | None:
        """A topic's page, or None if there is no such topic. ``counts_words``: the strip's counts in words, for
        the share card's text alternative; ``statements``: the statements published beside its briefing."""
        t = self.topic(slug)
        if t is None:
            return None
        cur = self.current(slug)
        trail = [(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH), (t.title, t.path)]
        title = f"{t.title}: briefing from UN records | {SITE_NAME}"
        crumbs = f'<p class="crumbs"><a href="{INDEX_PATH}">{INDEX_NAME}</a></p>'
        if cur is None:
            main = (crumbs + f"<h1>{e(t.title)}</h1>"
                    f'<p class="lede">The question asked every week: {e(t.question)}</p>'
                    "<p>Its briefing has not been written yet, or is not available just now. "
                    f'<a href="{INDEX_PATH}">Every topic briefing</a>.</p>')
            return self.page(title=title, description=self.description(t, None), path=t.path, trail=trail,
                             main=main, noindex=True)
        b = cur.briefing
        answer, sources, plan, evidence = final_and_sources(b.events)
        by_n = {s.get("n"): s for s in sources}
        f = plan.get("filters") or {}
        names = {code: c.name for code, c in self.countries.by_code.items()} if f.get("countries") else {}
        groups = self.countries.group_labels if f.get("groups") else {}
        prepared = long_date(cur.prepared_at)
        up_to = cur.version.get("records_up_to")
        reading = (f"Briefing of {prepared}, written by AI from the sources below"
                   + (f", from the UN’s records up to {long_date(up_to)}." if up_to else "."))
        summary = B.evidence_words(evidence)
        earlier = [v for v in versions(self.db, slug) if v["briefing_id"] != b.id][:EARLIER]
        earlier = [v for v in earlier if (x := self.archive.get(v["briefing_id"])) is not None and not x.withdrawn_at]
        notes = [f'<p class="note share">This version’s own address: <a href="/b/{e(b.id)}">{SITE_MARK}/b/{e(b.id)}'
                 f'</a><br><a href="/corrections.html?page={quote("/b/" + b.id, safe="/")}">Report a problem with '
                 "it</a></p>"]
        if earlier:
            notes.append('<p class="note">Earlier versions: ' + ", ".join(
                f'<a href="/b/{e(v["briefing_id"])}">{e(long_date(v["prepared_at"]))}</a>' for v in earlier) + ".</p>")
        main = "".join([
            crumbs,
            '<section id="result" class="topic" aria-labelledby="question">',
            '<div class="result__grid wts-cloth--countries" id="result-grid">',
            '<div class="result__head">',
            f'<h1 id="question">{e(t.title)}</h1>',
            f'<p class="topic__question">The question asked every week: {e(t.question)}</p>',
            f'<p class="reading"><span id="today">{e(reading)}</span> '
            f'<span id="interpretation">{e(plan.get("interpretation") or "")}</span></p>',
            "</div>",
            '<aside class="result__side panel" aria-labelledby="filters-head">',
            '<h2 id="filters-head">Filters used</h2>',
            '<ul class="chips" id="chips">' + B.html_of(B.plan_chips(plan, names, groups))
            + "</ul>",
            f'<p class="summary" id="summary">{e(summary)}</p>' if summary
            else '<p class="summary" id="summary" hidden></p>',
            "</aside>",
            '<div class="result__strip" id="strip">' + B.to_html(B.strip(answer)) + "</div>",
            '<div class="result__main">',
            '<p class="status visually-hidden" id="status" role="status" aria-live="polite"></p>',
            '<div class="columns briefing-text" id="briefing">' + B.html_of(B.briefing_blocks(answer, by_n))
            + "</div>",
            '<div id="details">' + B.to_html(B.details(answer, len(sources), b.id)) + "</div>",
            '<div id="notes">' + "".join(notes) + "</div>",
            '<div class="statements" id="statements">' + statements + "</div>",
            "</div>",
            '<aside class="record-col" id="record-col" aria-label="The record beside the briefing" hidden></aside>',
            '<svg class="wts-connector" id="connector" aria-hidden="true" focusable="false"></svg>',
            "</div>",
            '<section id="sources-section" aria-labelledby="sources-head">'
            '<h2 id="sources-head">Sources</h2><ol class="sources" id="sources">'
            + B.html_of([B.source_card(s) for s in sources]) + "</ol></section>",
            "</section>",
            '<div id="sheet-root"></div>',
            self._others(t),
        ])
        card = None
        if counts_words is not None:
            card = (f"/topics/{t.slug}/card.png", self.card_alt(t, counts_words))
        return self.page(title=title, description=self.description(t, cur), path=t.path, trail=trail, main=main,
                         card=card, briefing=b.id)

    def _others(self, t: Topic) -> str:
        rows = [(o, cur) for o, cur in self.listing() if o.slug != t.slug and cur is not None]
        if not rows:
            return ""
        return ('<section class="topic-others" aria-labelledby="others-head"><h2 id="others-head">Other topic '
                'briefings</h2><ul class="topic-list">'
                + "".join(f'<li><a href="{o.path}">{e(o.title)}</a></li>' for o, _ in rows) + "</ul></section>")

    # Addresses

    def moved(self, rest: str) -> str | None:
        """Where another form of a topic's address is sent for good: no ``.html``, a trailing slash, any case."""
        asked = re.sub(r"(?:\.html)?/*$", "", rest.strip().strip("/"), flags=re.I).lower()
        if not asked:
            return INDEX_PATH
        t = self.topic(asked)
        return t.path if t is not None else None

    def sitemap(self) -> list[tuple[str, str]]:
        """The index, dated by the newest briefing, and each topic with a briefing, dated by when it was
        prepared. A topic with none is left out."""
        rows = [(t.path, cur.prepared_at[:10]) for t, cur in self.listing() if cur is not None]
        newest = max((when for _, when in rows), default="")
        return [(INDEX_PATH, newest), *rows]

