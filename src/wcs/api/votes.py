"""A page for each General Assembly recorded vote, a page for each session's votes, and their index (decision 26).

    /votes.html                      the index: the latest votes, then every session, newest first
    /votes/session-80.html           a session's votes, newest first (``session-es-11``, ``session-s-8`` for the
                                     emergency special and special sessions)
    /votes/a-res-79-123.html         one vote: the resolution's symbol, in lower case, with every run of other
                                     characters made one hyphen

Every page is written on the server from the votes file (``votes_path``, from the UN Dag Hammarskjöld Library's
voting data, ``wcs.sources.votes``), so a reader without scripts and a search engine see all of it. No AI writes
any of it. A vote has a page only if the record lists how countries voted on it; a vote the record gives no table
for gets none.

The list of votes is read once (one query, under a fifth of a second over the whole file), and each vote's page
is put together when it is first asked for and kept, the most recent ``KEEP`` of them. Both are read again when the
votes file changes (its stamp: the time it was written and its size), which happens only when the update reads in a
new file from the UN, so the update itself does no more work than it did.

Titles: each page's title and heading begin with the vote's name, the resolution's symbol ("A/RES/79/123"), and
then its title as the record gives it. If two votes in the record ever share a symbol, each name adds the date, and
then the record's number in the UN Digital Library, so that no two pages share a title (the lesson of Where Councils
Stand's decision 28).
"""

from __future__ import annotations

import html
import json
import re
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from wts_core.adapters.duckdb import shared
from wts_core.checks import curl_quotes
from wts_core.dates import long_date

from wcs.config import Settings
from wcs.rag.sources import VOTE_LABEL
from wcs.reference.countries import Countries, countries
from wcs.store.parquet import votes_path

SITE_NAME = "Where Countries Stand"
SITE_MARK = "__SITE__"  # as in wcs.api.app: filled in with the site's address as a page is served
INDEX_PATH = "/votes.html"
INDEX_NAME = "Recorded votes in the General Assembly"
KEEP = 1000  # vote pages kept once written (about 30 KB each)
LATEST = 20  # votes listed first on the index
TITLE_SUBJECT_MAX = 70  # characters of a resolution's title in a page's <title>
DESCRIPTION_MAX = 160
VOTES_IN_ORDER = ("Y", "N", "A", "X")
UNDOCS = "https://undocs.org/en/"
# the family kit's mark for a source opened, on each link to the record (decision 28)
OPENED = ' data-wts-conversion="source_opened"'
# the kinds of place with a page of their own at /countries/<code>.html (decision 29; wcs.api.country_pages)
COUNTRY_PAGE_KINDS = ("member_state", "observer_state", "former_member_state", "other_state")

_SUFFIX = re.compile(r"\s*:\s*(?:resolutions?|decisions?)\s*/?\s*adopted by the General Assembly\.?\s*$", re.I)
_ES = re.compile(r"^A/(?:RES|DEC)/ES-(\d+)/", re.I)
_S = re.compile(r"^A/(?:RES|DEC)/S-(\d+)/", re.I)
_OLD_SPECIAL = re.compile(r"\((ES|S)-([IVXLC]+|\d+)\)\s*$", re.I)
_REGULAR = re.compile(r"^A/(?:RES|DEC)/(\d+)/", re.I)
_ROMAN = re.compile(r"\(([IVXLC]+)\)\s*$", re.I)
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Names the UN writes with "the" in a sentence ("What has the Russian Federation said about …")
THE = {"Bahamas", "Comoros", "Congo", "Gambia", "Holy See", "Maldives", "Marshall Islands",
       "Netherlands (Kingdom of the)", "Niger", "Philippines", "Solomon Islands", "Sudan"}
THE_STARTS = ("Central African Republic", "Democratic People", "Democratic Republic", "Dominican Republic",
              "German Democratic", "Lao People", "Republic of", "Russian Federation", "State of", "Syrian",
              "Union of", "United ")


def in_a_sentence(name: str) -> str:
    """The name as the UN writes it inside a sentence: "the Russian Federation", "Italy"."""
    return f"the {name}" if name in THE or name.startswith(THE_STARTS) else name
SESSION_KEY = re.compile(r"(?:\d{1,3}|es-\d{1,2}|s-\d{1,2}|other)")


def slug_of(symbol: str) -> str:
    """A symbol as it is written in a vote page's address: "A/RES/ES-11/1" is "a-res-es-11-1"."""
    return re.sub(r"[^a-z0-9]+", "-", (symbol or "").lower()).strip("-")


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _roman(text: str) -> int | None:
    if text.isdigit():
        return int(text)
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total, last = 0, 0
    for ch in reversed(text.upper()):
        v = values.get(ch)
        if v is None:
            return None
        total += -v if v < last else v
        last = max(last, v)
    return total or None


@dataclass(frozen=True)
class SessionOf:
    key: str      # "80", "es-11", "s-8", "other"
    kind: str     # "regular", "emergency", "special", "other"
    number: int
    label: str    # "80th session", "11th emergency special session"


def session_of(symbol: str, session: int | None) -> SessionOf:
    """The session a vote was held at, from its symbol (an emergency special or special session's symbol says so,
    "A/RES/ES-11/1", "A/RES/997(ES-I)") or, for a regular session, the record's session ("80"), or else the
    symbol's ("A/RES/80/288", "A/RES/3379(XXX)")."""
    s = (symbol or "").strip()
    m = _ES.match(s) or _S.match(s)
    if m:
        kind = "emergency" if m.re is _ES else "special"
        n = int(m[1])
    elif (m := _OLD_SPECIAL.search(s)) and (n := _roman(m[2]) or 0):
        kind = "emergency" if m[1].upper() == "ES" else "special"
    else:
        n = session or 0
        if not n and (m := _REGULAR.match(s)):
            n = int(m[1])
        if not n and (m := _ROMAN.search(s)):
            n = _roman(m[1]) or 0
        if not n:
            return SessionOf("other", "other", 0, "Session not recorded")
        return SessionOf(str(n), "regular", n, f"{ordinal(n)} session")
    word = "emergency special session" if kind == "emergency" else "special session"
    return SessionOf(f"{'es' if kind == 'emergency' else 's'}-{n}", kind, n, f"{ordinal(n)} {word}")


def clean_title(title: str | None, agenda: str | None = None) -> str:
    """A resolution's title as the record gives it, without the catalogue's "resolution / adopted by the General
    Assembly" and with its parts joined by a colon as in English ("… Campaign: actions and activities")."""
    t = _SUFFIX.sub("", title or "").strip()
    t = re.sub(r"\s+:\s+", ": ", re.sub(r"\s+", " ", t)).strip(" .:")
    return curl_quotes(t or (agenda or "").strip(" .") or "Untitled in the record")


def shorten(text: str, most: int) -> str:
    """At most ``most`` characters, cut at a word and marked with an ellipsis."""
    if len(text) <= most:
        return text
    cut = text[:most - 1].rsplit(" ", 1)[0].rstrip(" ,;:.(")
    return f"{cut}…"


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def result_words(counts: dict[str, int]) -> str:
    """"adopted by 157 votes to 8, with 7 abstentions", as the UN's records put it."""
    y, n, a = counts.get("Y", 0), counts.get("N", 0), counts.get("A", 0)
    abstentions = "no abstentions" if not a else _plural(a, "abstention", "abstentions")
    if y > n:
        return f"adopted by {_plural(y, 'vote', 'votes')} to {n:,}, with {abstentions}"
    return f"{y:,} in favour and {n:,} against, with {abstentions}"


def not_voting_words(counts: dict[str, int]) -> str:
    x = counts.get("X", 0)
    return f"{_plural(x, 'Member State', 'Member States')} did not vote." if x else "Every Member State voted."


def _sort_name(name: str) -> str:
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()


def e(text: Any) -> str:
    return html.escape(str(text), quote=True)


@dataclass(frozen=True)
class Vote:
    undl_id: str
    slug: str
    name: str                 # "A/RES/79/123", or with the date where two votes share a symbol
    symbol: str
    on: date
    session: SessionOf
    meeting: str
    draft: str
    title: str
    agenda: str
    note: str
    counts: dict[str, int]    # how many of the countries listed voted each way
    totals: dict[str, int | None]  # the record's own totals, and its number of Member States ("M")
    url: str

    @property
    def path(self) -> str:
        return f"/votes/{self.slug}.html"

    @property
    def heading(self) -> str:
        return f"{self.name}: {self.title}"

    @property
    def page_title(self) -> str:
        return f"{self.name}: {shorten(self.title, TITLE_SUBJECT_MAX)} | {SITE_NAME}"


@dataclass
class Session:
    of: SessionOf
    votes: list[Vote] = field(default_factory=list)   # newest first

    @property
    def path(self) -> str:
        return f"/votes/session-{self.of.key}.html"

    @property
    def name(self) -> str:
        return f"Recorded votes, {self.of.label}"


@dataclass
class Catalog:
    """Every vote in one votes file, with the sessions they were held at."""
    votes: dict[str, Vote]            # by slug
    by_undl: dict[str, str]           # UN Digital Library record -> slug
    sessions: dict[str, Session]      # by key

    @property
    def newest_first(self) -> list[Vote]:
        return sorted(self.votes.values(), key=lambda v: (v.on, v.symbol), reverse=True)

    def sessions_of(self, kind: str) -> list[Session]:
        return sorted((s for s in self.sessions.values() if s.of.kind == kind), key=lambda s: s.of.number,
                      reverse=True)


def catalog_of(rows: list[tuple]) -> Catalog:
    """The catalogue from one row per vote: (undl_id, symbol, date, session, meeting, draft, title, agenda, note,
    total_yes, total_no, total_abstentions, total_non_voting, total_ms, url, yes, no, abstaining, not voting)."""
    rows = sorted(rows, key=lambda r: (r[2] or date.min, str(r[0])))  # the earliest keeps the plain address
    by_symbol: dict[str, int] = {}
    by_symbol_day: dict[tuple[str, date], int] = {}
    for r in rows:
        by_symbol[r[1]] = by_symbol.get(r[1], 0) + 1
        by_symbol_day[(r[1], r[2])] = by_symbol_day.get((r[1], r[2]), 0) + 1
    votes: dict[str, Vote] = {}
    by_undl: dict[str, str] = {}
    sessions: dict[str, Session] = {}
    for r in rows:
        (undl_id, symbol, on, session, meeting, draft, title, agenda, note, ty, tn, ta, tx, tm, url,
         y, n, a, x) = r
        if not symbol or on is None or not (y or n or a or x):
            continue  # no table of votes: no page
        undl_id = str(undl_id)
        name = symbol
        if by_symbol[symbol] > 1:
            name = f"{symbol}, {long_date(on)}"
            if by_symbol_day[(symbol, on)] > 1:
                name += f", UN Digital Library record {undl_id}"
        slug = slug_of(symbol)
        if slug in votes or not SLUG.fullmatch(slug):
            slug = f"{slug}-{slug_of(undl_id)}".strip("-")
        of = session_of(symbol, session)
        v = Vote(undl_id=undl_id, slug=slug, name=name, symbol=symbol, on=on, session=of,
                 meeting=(meeting or "").strip(), draft=(draft or "").strip(), title=clean_title(title, agenda),
                 agenda=curl_quotes((agenda or "").strip()), note=curl_quotes((note or "").strip()),
                 counts={"Y": y or 0, "N": n or 0, "A": a or 0, "X": x or 0},
                 totals={"Y": ty, "N": tn, "A": ta, "X": tx, "M": tm}, url=(url or "").strip())
        votes[slug] = v
        by_undl[undl_id] = slug
        sessions.setdefault(of.key, Session(of)).votes.append(v)
    for s in sessions.values():
        s.votes.sort(key=lambda v: (v.on, v.symbol), reverse=True)
    return Catalog(votes, by_undl, sessions)


def undocs(symbol: str) -> str:
    return UNDOCS + quote(symbol.replace(" ", ""), safe="/()-.")


def breadcrumbs(trail: list[tuple[str, str]]) -> dict[str, Any]:
    """schema.org: where a page sits (Where Countries Stand, Recorded votes, this vote). The only structured data
    the pages carry."""
    return {"@context": "https://schema.org", "@type": "BreadcrumbList",
            "itemListElement": [{"@type": "ListItem", "position": n, "name": name, "item": SITE_MARK + path}
                                for n, (name, path) in enumerate(trail, 1)]}


def _vote_line(v: Vote) -> str:
    return (f'<li><a href="{v.path}">{e(v.heading)}</a><span class="vote-index__meta">{e(long_date(v.on))}, '
            f"{e(result_words(v.counts))}</span></li>")


def _named_aside(path: str | None) -> str:
    """The family's words for people and bodies a page names (GLOSSARY, "Corrections and the right of reply")."""
    if path is None:
        return ('<p class="vote-named">Named on these pages? See <a href="/named.html">If you’re named on this '
                "site</a> to tell us something is wrong or unfair, or to add a statement in your own words.</p>")
    page = quote(path, safe="/")
    return ('<aside class="vote-side panel" aria-labelledby="named-head">'
            '<h2 id="named-head">If you’re named</h2>'
            "<p>A country’s delegation, permanent mission or government can tell us that something on this page is "
            "wrong or unfair, or add a short statement in its own words, shown beside this vote. We reply within 5 "
            "working days, and decide within 20 working days.</p>"
            '<ul class="plain-list">'
            '<li><a href="/named.html">If you’re named on this site</a></li>'
            f'<li><a href="/corrections.html?kind=error&amp;page={page}#report">Tell us what is wrong</a></li>'
            f'<li><a href="/corrections.html?kind=statement&amp;page={page}#report">Add a statement</a></li>'
            "</ul></aside>")


def statements_html(rows: list[dict[str, Any]], not_endorsed: str) -> str:
    """Statements under the right of reply beside a vote (decision 16), in the markup the briefing's page uses:
    headed "Statement from [name], [date]" and ending with the words that say they are the author's own.
    ``data-nosnippet``: a search engine may show the page, but never quotes a statement as the site's words."""
    out = []
    for s in rows:
        head = f"statement-{int(s['id'])}"
        paras = [p.strip() for p in re.split(r"\n\s*\n", s.get("text") or "") if p.strip()]
        link = s.get("link") or ""
        out.append(f'<section class="statement" aria-labelledby="{head}" data-nosnippet>'
                   f'<h2 class="statement__head" id="{head}">Statement from {e(s["name"])}, '
                   f"{e(long_date(s['shown_on']))}</h2>"
                   f'<p class="statement__role">{e(s["role"])}</p>'
                   f'<div class="statement__text">{"".join(f"<p>{e(p)}</p>" for p in paras)}</div>'
                   + (f'<p class="statement__link">The record it points to: <a href="{e(link)}" '
                      f'rel="ugc nofollow noopener">{e(link)}</a></p>' if re.match(r"https?://", link, re.I) else "")
                   + f'<p class="statement__note">{e(not_endorsed)}</p></section>')
    return f'<div class="statements">{"".join(out)}</div>' if out else ""


STATEMENTS = "<!--statements-->"


class VotePages:
    """The vote pages of one site, over its votes file. ``frame`` is the page they are written into (the site's
    header, footer and head, from ``web/partials/record-page.html``), given once the site's pages are put
    together."""

    def __init__(self, settings: Settings, reference: Countries | None = None, keep: int = KEEP):
        self.path: Path = votes_path(settings.parquet_dir)
        self.record = str(settings.sources.get("votes_record") or "https://digitallibrary.un.org/record/4060887")
        self._countries = reference
        self.keep = keep
        self.frame = ""
        self._shared: Any = None
        self._lock = threading.Lock()
        self._stamp: str | None = None
        self._catalog = Catalog({}, {}, {})
        self._pages: OrderedDict[str, str] = OrderedDict()

    # The record

    @property
    def countries(self) -> Countries:
        if self._countries is None:
            self._countries = countries()
        return self._countries

    @property
    def _con(self) -> Any:
        if self._shared is None:
            self._shared = shared()  # one connection, a cursor for each thread
        return self._shared

    def stamp(self) -> str | None:
        """Changes whenever the votes file is written again."""
        try:
            st = self.path.stat()
        except OSError:
            return None
        return f"{st.st_mtime_ns}:{st.st_size}"

    def catalog(self) -> Catalog:
        stamp = self.stamp()
        if stamp == self._stamp:
            return self._catalog
        with self._lock:
            if stamp != self._stamp:
                self._catalog = catalog_of(self._rows()) if stamp else Catalog({}, {}, {})
                self._pages.clear()
                self._stamp = stamp
        return self._catalog

    def _rows(self) -> list[tuple]:
        path = str(self.path).replace("'", "''")
        counted = ", ".join(f"count(*) FILTER (WHERE vote = '{v}')" for v in VOTES_IN_ORDER)
        return self._con.execute(f"""
            SELECT undl_id, any_value(resolution), any_value(date), any_value(session), any_value(meeting),
                   any_value(draft), any_value(title), any_value(agenda_title), any_value(vote_note),
                   any_value(total_yes), any_value(total_no), any_value(total_abstentions),
                   any_value(total_non_voting), any_value(total_ms), any_value(url), {counted}
            FROM read_parquet('{path}') GROUP BY undl_id""").fetchall()

    def exists(self, slug: str) -> bool:
        """Whether a vote has a page at /votes/<slug>.html (for the right of reply, ``wcs.replies``)."""
        return slug in self.catalog().votes

    def _members(self, v: Vote) -> dict[str, list[tuple[str, str]]]:
        """Each way of voting, with the countries that voted so: each country's name on the day and its code."""
        path = str(self.path).replace("'", "''")
        rows = self._con.execute(f"SELECT country_code, vote FROM read_parquet('{path}') WHERE date = ? AND "
                                 "undl_id = ?", [v.on, v.undl_id]).fetchall()
        out: dict[str, list[tuple[str, str]]] = {}
        for code, how in rows:
            out.setdefault(how, []).append((curl_quotes(self.countries.name_on(code, v.on)), code or ""))
        return {k: sorted(names, key=lambda x: _sort_name(x[0])) for k, names in out.items()}

    def _country(self, name: str, code: str) -> str:
        """A country in a vote's lists, linked to its page (decision 29) where it has one."""
        if self.countries.kind(code) in COUNTRY_PAGE_KINDS:
            return f'<li><a href="/countries/{code.lower()}.html">{e(name)}</a></li>'
        return f"<li>{e(name)}</li>"

    # The pages

    def page(self, *, title: str, description: str, path: str, trail: list[tuple[str, str]], main: str,
             noindex: bool = False, frame: str | None = None) -> str:
        """A page in the frame (``frame``, or the vote pages' own): its head (title, description, canonical link,
        link preview and breadcrumbs) and its main content."""
        data = json.dumps(breadcrumbs(trail), ensure_ascii=False, indent=2).replace("</", "<\\/")
        data = "\n".join("    " + line for line in data.split("\n"))
        head = (f"  <title>{e(title)}</title>\n"
                f'  <meta name="description" content="{e(description)}">\n'
                f'  <link rel="canonical" href="{SITE_MARK}{path}">\n'
                f'  <meta property="og:title" content="{e(title)}">\n'
                f'  <meta property="og:description" content="{e(description)}">\n'
                f'  <meta property="og:url" content="{SITE_MARK}{path}">\n'
                + ('  <meta name="robots" content="noindex">\n' if noindex else "")
                + f'  <script type="application/ld+json">\n{data}\n  </script>\n')
        return (frame or self.frame).replace("<!--#head-->", head.rstrip("\n"), 1).replace("<!--#main-->", main, 1)

    def index(self) -> str:
        cat = self.catalog()
        trail = [(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH)]
        votes = cat.newest_first
        parts = [f"<h1>{INDEX_NAME}</h1>"]
        if not votes:
            parts.append('<p class="lede">No recorded votes have been read in yet.</p>')
        else:
            first, last = votes[-1], votes[0]
            parts.append(f'<p class="lede">Every resolution and decision the General Assembly has adopted by recorded '
                         f"vote, with how each Member State voted, from the UN’s own voting data: "
                         f"{len(votes):,} votes, from {e(long_date(first.on))} to {e(long_date(last.on))}.</p>")
            parts.append("<p>Votes on separate paragraphs of a draft, and drafts that were not adopted, are not in the "
                         "UN’s voting data, so they are not listed here. Each vote’s page links to the UN’s own "
                         "record of it.</p>")
            parts.append('<p>The Security Council’s votes, with the drafts it did not adopt, are in <a '
                         'href="/security-council-votes.html">Votes in the Security Council</a>.</p>')
            parts.append('<h2 id="latest">Latest votes</h2><ul class="vote-index">'
                         + "".join(_vote_line(v) for v in votes[:LATEST]) + "</ul>")
            for kind, heading in (("regular", "Regular sessions"), ("emergency", "Emergency special sessions"),
                                  ("special", "Special sessions"), ("other", "Session not recorded")):
                listed = cat.sessions_of(kind)
                if not listed:
                    continue
                parts.append(f'<h2 id="{kind}">{heading}</h2><ul class="session-index">' + "".join(
                    f'<li><a href="{s.path}">{e(s.of.label[0].upper() + s.of.label[1:])}</a>, '
                    f'<span class="session-index__meta">{_plural(len(s.votes), "recorded vote", "recorded votes")}, '
                    f"{e(self._span(s))}</span></li>" for s in listed) + "</ul>")
        parts.append(f'<p class="vote-source">From the <a href="{e(self.record)}"{OPENED}>General Assembly voting '
                     "data</a> of the UN Dag Hammarskjöld Library, © United Nations, used for non-commercial purposes "
                     'with attribution. How the record is read in: <a href="/methodology.html#record">The record</a>, '
                     "on the methodology page.</p>")
        parts.append(_named_aside(None))
        description = ("Every General Assembly resolution adopted by recorded vote since 1946, with how each Member "
                       "State voted, by session, from the UN’s own voting data.")
        return self.page(title=f"{INDEX_NAME} | {SITE_NAME}", description=description, path=INDEX_PATH, trail=trail,
                         main="".join(parts))

    @staticmethod
    def _span(s: Session) -> str:
        first, last = s.votes[-1].on, s.votes[0].on
        return long_date(first) if first == last else f"{long_date(first)} to {long_date(last)}"

    def session_page(self, key: str) -> str | None:
        s = self.catalog().sessions.get(key)
        if s is None:
            return None
        trail = [(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH), (s.name, s.path)]
        n = len(s.votes)
        what = "vote" if n == 1 else "votes"
        main = (f'<p class="crumbs"><a href="{INDEX_PATH}">{INDEX_NAME}</a></p>'
                f"<h1>{e(s.name)}</h1>"
                f'<p class="lede">The General Assembly’s {_plural(n, "recorded vote", "recorded votes")} at its '
                f"{e(s.of.label)}, {e(self._span(s))}, newest first, as the UN’s voting data records "
                f"{'it' if n == 1 else 'them'}. Each vote’s page gives how each Member State voted.</p>"
                '<ul class="vote-index">' + "".join(_vote_line(v) for v in s.votes) + "</ul>"
                + _named_aside(None))
        description = shorten(f"How each Member State voted in the General Assembly’s {n:,} recorded {what} at its "
                              f"{s.of.label}, {self._span(s)}, from the UN’s own voting data.", DESCRIPTION_MAX)
        return self.page(title=f"{s.name} | {SITE_NAME}", description=description, path=s.path, trail=trail,
                         main=main)

    def vote_page(self, slug: str) -> str | None:
        """A vote's page, with ``STATEMENTS`` where its statements go (they are filled in as it is served)."""
        cat = self.catalog()
        v = cat.votes.get(slug)
        if v is None:
            return None
        with self._lock:
            kept = self._pages.get(slug)
            if kept is not None:
                self._pages.move_to_end(slug)
                return kept
        text = self._vote_page(v)
        with self._lock:
            if self._stamp == self.stamp():
                self._pages[slug] = text
                while len(self._pages) > self.keep:
                    self._pages.popitem(last=False)
        return text

    def description(self, v: Vote) -> str:
        return shorten(f"How each Member State voted on General Assembly {self._kind(v)} {v.name}, on "
                       f"{long_date(v.on)}: {result_words(v.counts)}. {v.title}.", DESCRIPTION_MAX)

    @staticmethod
    def _kind(v: Vote) -> str:
        return "decision" if "/DEC/" in v.symbol.upper() else "resolution"

    def _vote_page(self, v: Vote) -> str:
        members = self._members(v)
        counts = {k: len(members.get(k, [])) for k in VOTES_IN_ORDER}
        trail = [(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH), (v.heading, v.path)]
        kind = self._kind(v)
        # each link to the record carries the family kit's mark for a source opened, inert unless measurement is on
        # (decision 28)
        facts = [(kind.capitalize(), f'<a href="{e(undocs(v.symbol))}"{OPENED}>{e(v.symbol)}</a>'),
                 ("Title", e(v.title)),
                 ("Date", e(long_date(v.on))),
                 ("Session", f'<a href="/votes/session-{v.session.key}.html">{e(v.session.label)}</a>')]
        if v.meeting:
            facts.append(("Meeting record", f'<a href="{e(undocs(v.meeting))}"{OPENED}>{e(v.meeting)}</a>'))
        # the voting data joins two symbols or agenda items with "|" ("A/64/L.22|A/64/L.22/Add.1")
        drafts = [d.strip() for d in v.draft.split("|") if d.strip()]
        if drafts:
            facts.append(("Draft" if len(drafts) == 1 else "Drafts", ", ".join(
                f'<a href="{e(undocs(d))}"{OPENED}>{e(d)}</a>' for d in drafts)))
        items = [a.strip().rstrip(".") for a in v.agenda.split("|") if a.strip()]
        if items:
            facts.append(("Agenda item" if len(items) == 1 else "Agenda items", "<br>".join(e(a) for a in items)))
        result = f"{result_words(counts)[0].upper()}{result_words(counts)[1:]}. {not_voting_words(counts)}"
        facts.append(("Result", e(result)))
        links = [f'<li><a href="{e(undocs(v.symbol))}"{OPENED}>The {kind}, {e(v.symbol)}, on undocs.org</a></li>']
        if v.url:
            links.append(f'<li><a href="{e(v.url)}"{OPENED}>The voting record in the UN Digital Library</a></li>')
        if v.meeting:
            links.append(f'<li><a href="{e(undocs(v.meeting))}"{OPENED}>The record of the meeting, {e(v.meeting)}, on '
                         "undocs.org</a></li>")
        totals = "".join(f'<tr><th scope="row">{VOTE_LABEL[k]}</th><td>{counts[k]:,}</td></tr>'
                         for k in VOTES_IN_ORDER)
        listed = sum(counts.values())
        parts = [f'<p class="crumbs"><a href="{INDEX_PATH}">{INDEX_NAME}</a></p>',
                 f"<h1>{e(v.heading)}</h1>",
                 f'<p class="lede">A recorded vote of the General Assembly on {e(long_date(v.on))}, at its '
                 f"{e(v.session.label)}: {e(result_words(counts))}. {e(not_voting_words(counts))}</p>",
                 '<div class="vote-layout"><div class="vote-main">',
                 '<dl class="facts vote-facts">' + "".join(f"<div><dt>{k}</dt><dd>{val}</dd></div>"
                                                           for k, val in facts) + "</dl>",
                 '<h2 id="record">In the UN’s records</h2><ul class="plain-list">' + "".join(links) + "</ul>"]
        if v.note:
            parts.append(f"<p>The UN’s voting data notes: “{e(v.note)}”</p>")
        recorded = {k: v.totals.get(k) for k in VOTES_IN_ORDER}
        if all(recorded[k] is not None for k in VOTES_IN_ORDER) and any(recorded[k] != counts[k]
                                                                       for k in VOTES_IN_ORDER):
            parts.append("<p>The totals the UN’s voting data gives for this vote ("
                         + e(", ".join(f"{recorded[k]} {VOTE_LABEL[k].lower()}" for k in VOTES_IN_ORDER))
                         + ") differ from the votes it lists for each Member State, which are given here.</p>")
        parts.append('<h2 id="how">How each country voted</h2>'
                     f'<table class="vote-totals"><caption>Totals, of {_plural(listed, "Member State", "Member States")}'
                     f" listed</caption>"
                     '<thead><tr><th scope="col">Vote</th><th scope="col">Countries</th></tr></thead>'
                     f"<tbody>{totals}</tbody></table>")
        for k in VOTES_IN_ORDER:
            names = members.get(k) or []
            if not names:
                continue
            anchor = slug_of(VOTE_LABEL[k])
            parts.append(f'<h3 id="{anchor}">{VOTE_LABEL[k]} ({len(names):,})</h3>'
                         f'<ul class="vote-list" aria-labelledby="{anchor}">'
                         + "".join(self._country(name, code) for name, code in names) + "</ul>")
        parts.append('<p class="vote-source">Each country is given the UN’s short name, as everywhere on this site; '
                     "China’s seat to 25 October 1971 is the Republic of China’s, and Germany before 3 October "
                     "1990 is the Federal Republic of Germany. The voting data itself gives each Member State’s "
                     f'official name at the time. From the <a href="{e(self.record)}"{OPENED}>General Assembly voting '
                     "data</a> of the UN Dag Hammarskjöld Library, © United Nations.</p>")
        parts.append(STATEMENTS)
        parts.append("</div>" + _named_aside(v.path) + "</div>")
        return self.page(title=v.page_title, description=self.description(v), path=v.path, trail=trail,
                         main="".join(parts))

    def hidden_page(self, slug: str, note: str) -> str | None:
        """What stands in place of a vote's page while a concern about it is decided (the impartiality policy,
        7.3): its name and the note, and nothing of the vote."""
        v = self.catalog().votes.get(slug)
        if v is None:
            return None
        main = (f'<p class="crumbs"><a href="{INDEX_PATH}">{INDEX_NAME}</a></p><h1>{e(v.name)}</h1>'
                f'<p class="note">{e(note)}</p>' + _named_aside(None))
        return self.page(title=f"{v.name} | {SITE_NAME}", description=f"General Assembly {self._kind(v)} {v.name}.",
                         path=v.path, trail=[(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH), (v.name, v.path)],
                         main=main, noindex=True)

    # Addresses

    def moved(self, rest: str) -> str | None:
        """The address a page asked for at another form of its address is sent to for good: a vote by its symbol
        in any case or spelling ("/votes/A/RES/79/123", "/votes/A-RES-79-123", ".../a-res-79-123/"), or by its UN
        Digital Library number ("/votes/4126622"), and a session without ".html"."""
        asked = re.sub(r"(?:\.html)?/*$", "", rest.strip().strip("/"), flags=re.I)
        if not asked:
            return INDEX_PATH
        cat = self.catalog()
        if asked.isdigit() and asked in cat.by_undl:
            return cat.votes[cat.by_undl[asked]].path
        slug = slug_of(asked)
        if slug.startswith("session-") and slug.removeprefix("session-") in cat.sessions:
            return cat.sessions[slug.removeprefix("session-")].path
        if slug in cat.votes:
            return cat.votes[slug].path
        return None

    def sitemap(self, hidden: Callable[[str], bool] = lambda _: False) -> list[tuple[str, str]]:
        """Every page here, with the date of its newest vote (a date from the record, never a made-up one): the
        index, each session, each vote that is not hidden."""
        cat = self.catalog()
        out: list[tuple[str, str]] = []
        votes = cat.newest_first
        out.append((INDEX_PATH, votes[0].on.isoformat() if votes else ""))
        for kind in ("regular", "emergency", "special", "other"):
            out += [(s.path, s.votes[0].on.isoformat()) for s in cat.sessions_of(kind)]
        out += [(v.path, v.on.isoformat()) for v in votes if not hidden(v.path)]
        return out
