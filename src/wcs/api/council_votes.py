"""A page for each Security Council vote, a page for each year's votes, and their index (decision 30).

    /security-council-votes.html         the index: the latest votes, the drafts not adopted, and every year
    /security-council-votes/2024.html    a year's votes, newest first
    /votes/s-res-2728-2024.html          a resolution: its symbol, by decision 26's rule
    /votes/s-2024-239.html               a draft not adopted: its draft symbol

The vote pages share ``/votes/`` with the General Assembly's (``wcs.api.votes``): every Assembly symbol begins "A/"
and every Council symbol "S/", so no address can be both (a test checks it). Every page is written on the server
from ``council_votes.parquet`` (``wcs.sources.council``), with no AI and no script, into the vote pages' frame.

Names and words: each member under the UN's short name on the day (decision 6; China's seat to 25 October 1971 is
the Republic of China's, decision 5), the permanent members marked in words. The result is the record's own
announcement where the site has it ("The draft resolution has not been adopted, owing to the negative vote of a
permanent member of the Council"); "veto" is used only as the plain common name beside it. Votes are counts and
lists, never coloured, and never compared with another member's (decision 29).
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from wts_core.checks import curl_quotes
from wts_core.dates import long_date

from wcs.api.votes import (
    COUNTRY_PAGE_KINDS,
    OPENED,
    SITE_NAME,
    SLUG,
    STATEMENTS,
    VotePages,
    _named_aside,
    _plural,
    _sort_name,
    e,
    in_a_sentence,
    ordinal,
    shorten,
    slug_of,
    undocs,
)
from wcs.reference.countries import Countries
from wcs.store.parquet import council_votes_path

INDEX_PATH = "/security-council-votes.html"
INDEX_NAME = "Votes in the Security Council"
YEAR_PATH = "/security-council-votes/{year}.html"
LATEST = 20
KEEP = 600
TITLE_SUBJECT_MAX = 70
DESCRIPTION_MAX = 160
ORDER = ("Y", "N", "A", "X")
# the record's own words for a member that took no part ("did not participate in the voting", in the voting data's
# notes and the meeting records)
LABEL = {"Y": "In favour", "N": "Against", "A": "Abstaining", "X": "Did not participate in the voting"}
P5_NAMES_ORDER = ("CHN", "FRA", "RUS", "SUN", "GBR", "USA")
VETO_WORDS = "not adopted, owing to the negative vote of a permanent member of the Council"
VOTES_WORDS = "not adopted, having failed to obtain the required number of votes"
_BRACKET = re.compile(r"\[(?:on\s+)?(.+?)\]\s*$", re.S)


def resolution_title(title: str | None, agenda: str | None) -> str:
    """The Library's short description of a resolution, without its number: "Security Council resolution 2728 (2024)
    [on ceasefire in the Gaza Strip during Ramadan]" is "Ceasefire in the Gaza Strip during Ramadan"."""
    t = " ".join((title or "").split())
    m = _BRACKET.search(t)
    text = m[1].strip(" .") if m else ""
    if not text:
        text = " ".join((agenda or "").split()).strip(" .") or "Untitled in the record"
    return curl_quotes(text[:1].upper() + text[1:])


def and_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


@dataclass(frozen=True)
class CouncilVote:
    vote_id: str
    slug: str
    name: str                     # the symbol, and where two votes share one, which of them
    symbol: str                   # the resolution's symbol, or the draft's
    on: date
    adopted: bool
    what: str                     # "resolution", "draft resolution", "proposed amendment", "draft decision", "proposal"
    reason: str | None            # "veto", "votes"
    meeting: str
    draft: str
    amends: str
    title: str
    agenda: str
    subjects: str
    note: str
    result: str                   # the record's announcement
    counts: dict[str, int]
    totals: dict[str, int | None]
    without_vote: bool
    vetoed_by: tuple[str, ...]
    source: str
    complete: bool
    url: str

    @property
    def path(self) -> str:
        return f"/votes/{self.slug}.html"

    @property
    def listed(self) -> bool:
        """Whether how members voted is given (not for a veto known only from the list of vetoes)."""
        return sum(self.counts.values()) > 0

    @property
    def heading(self) -> str:
        if self.adopted:
            return f"{self.name}: {self.title}"
        return f"{self.name}: {self.what} not adopted"

    @property
    def page_title(self) -> str:
        if self.adopted:
            return f"{self.name}: {shorten(self.title, TITLE_SUBJECT_MAX)} | {SITE_NAME}"
        return f"{self.heading} | {SITE_NAME}"


@dataclass
class Catalog:
    votes: dict[str, CouncilVote] = field(default_factory=dict)   # by slug
    by_id: dict[str, str] = field(default_factory=dict)            # vote_id -> slug
    years: dict[int, list[CouncilVote]] = field(default_factory=dict)

    @property
    def newest_first(self) -> list[CouncilVote]:
        return sorted(self.votes.values(), key=lambda v: (v.on, v.symbol), reverse=True)


class CouncilVotePages:
    """The Security Council's vote pages, written into the General Assembly vote pages' frame (``votes.frame``)."""

    def __init__(self, votes: VotePages, keep: int = KEEP):
        self.votes = votes
        self.path = council_votes_path(votes.path.parent)
        self.keep = keep
        self._lock = threading.Lock()
        self._stamp: str | None = None
        self._catalog = Catalog()
        self._pages: OrderedDict[str, str] = OrderedDict()
        self._members: dict[str, list[tuple[str, str]]] = {}
        self.first_record: Callable[[], date | None] = lambda: None  # the day the site's Council records begin

    @property
    def countries(self) -> Countries:
        return self.votes.countries

    @property
    def record(self) -> str:
        return "https://digitallibrary.un.org/record/4055387"

    def stamp(self) -> str | None:
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
                self._catalog = self._read() if stamp else Catalog()
                self._pages.clear()
                self._members.clear()
                self._stamp = stamp
        return self._catalog

    def _read(self) -> Catalog:
        path = str(self.path).replace("'", "''")
        counted = ", ".join(f"count(*) FILTER (WHERE vote = '{v}' AND country_code IS NOT NULL)" for v in ORDER)
        rows = self.votes._con.execute(f"""
            SELECT vote_id, any_value(outcome), any_value(what), any_value(reason), any_value(date),
                   any_value(resolution), any_value(draft), any_value(amends), any_value(meeting), any_value(title),
                   any_value(agenda), any_value(subjects), any_value(vote_note), any_value(result),
                   any_value(total_yes), any_value(total_no), any_value(total_abstentions),
                   any_value(total_non_voting), any_value(modality), any_value(vetoed_by), any_value(source),
                   bool_and(coalesce(complete, false)), any_value(url), {counted}
            FROM read_parquet('{path}') GROUP BY vote_id""").fetchall()
        rows.sort(key=lambda r: (r[4] or date.min, str(r[0])))
        by_symbol: dict[str, int] = {}
        for r in rows:
            sym = r[5] or r[6] or ""
            by_symbol[sym] = by_symbol.get(sym, 0) + 1
        cat = Catalog()
        seen: dict[str, int] = {}
        for (vote_id, outcome, what, reason, on, resolution, draft, amends, meeting, title, agenda, subjects, note,
             result, ty, tn, ta, tx, modality, vetoed_by, source, complete, url, y, n, a, x) in rows:
            symbol = (resolution or draft or "").strip()
            if not symbol or on is None:
                continue
            seen[symbol] = seen.get(symbol, 0) + 1
            nth = seen[symbol]
            name = symbol if by_symbol[symbol] == 1 else f"{symbol}, vote {nth} of {by_symbol[symbol]}"
            slug = slug_of(symbol) + ("" if nth == 1 else f"-{nth}")
            if not SLUG.fullmatch(slug) or slug in cat.votes:
                continue
            adopted = outcome == "adopted"
            agenda_text = curl_quotes(" ".join((agenda or "").split()).strip(" ."))
            v = CouncilVote(
                vote_id=str(vote_id), slug=slug, name=name, symbol=symbol, on=on, adopted=adopted,
                what=what or ("resolution" if adopted else "draft resolution"), reason=reason,
                meeting=(meeting or "").strip(), draft=(draft or "").strip() if adopted else "",
                amends=(amends or "").strip(), title=resolution_title(title, agenda) if adopted else "",
                agenda=agenda_text, subjects=(subjects or "").strip(), note=curl_quotes((note or "").strip()),
                result=curl_quotes((result or "").strip()), counts={"Y": y, "N": n, "A": a, "X": x},
                totals={"Y": ty, "N": tn, "A": ta, "X": tx}, without_vote=(modality or "") == "Without Vote",
                vetoed_by=tuple(vetoed_by or ()), source=source or "", complete=bool(complete),
                url=(url or "").strip())
            cat.votes[slug] = v
            cat.by_id[v.vote_id] = slug
            cat.years.setdefault(on.year, []).append(v)
        for vs in cat.years.values():
            vs.sort(key=lambda v: (v.on, v.symbol), reverse=True)
        return cat

    def exists(self, slug: str) -> bool:
        return slug in self.catalog().votes

    def members(self, v: CouncilVote) -> list[tuple[str, str, str, bool]]:
        """(name on the day, code, how it voted, permanent member) for each member listed, alphabetically."""
        with self._lock:
            kept = self._members.get(v.vote_id)
        if kept is not None:
            return kept
        path = str(self.path).replace("'", "''")
        rows = self.votes._con.execute(f"SELECT country_code, vote FROM read_parquet('{path}') WHERE vote_id = ? AND "
                                       "country_code IS NOT NULL", [v.vote_id]).fetchall()
        out = [(curl_quotes(self.countries.name_on(code, v.on)), code, how,
                "p5" in self.countries.groups_on(code, v.on)) for code, how in rows]
        out.sort(key=lambda x: _sort_name(x[0]))
        with self._lock:
            self._members[v.vote_id] = out
            while len(self._members) > self.keep:
                self._members.pop(next(iter(self._members)))
        return out

    def name(self, code: str, on: date) -> str:
        return curl_quotes(self.countries.name_on(code, on))

    def linked(self, code: str, on: date) -> str:
        name = e(self.name(code, on))
        if self.countries.kind(code) in COUNTRY_PAGE_KINDS:
            return f'<a href="/countries/{code.lower()}.html">{name}</a>'
        return name

    def vetoed_names(self, v: CouncilVote, link: bool = True, sentence: bool = False) -> str:
        """The permanent members voting against, as the UN names them ("China and the Russian Federation")."""
        codes = sorted(v.vetoed_by, key=lambda c: _sort_name(self.name(c, v.on)))
        out = []
        for c in codes:
            name = self.name(c, v.on)
            the = "the " if sentence and in_a_sentence(name) != name else ""
            out.append(the + (self.linked(c, v.on) if link else e(name)))
        return and_names(out) if out else ""

    # Words

    def result_line(self, v: CouncilVote) -> str:
        """The result, short, for lists: "Adopted as resolution 2728 (2024): 14 votes in favour, none against and 1
        abstention", or "Not adopted, owing to the negative vote of a permanent member of the Council (the Russian
        Federation voted against)"."""
        if v.adopted:
            if v.without_vote:
                return "Adopted without a vote"
            y, n, a = (v.totals.get(k) or 0 for k in ("Y", "N", "A"))
            ab = "no abstentions" if not a else _plural(a, "abstention", "abstentions")
            number = re.sub(r"^S/RES/(\d+)\s*\((\d{4})\)$", r"\1 (\2)", v.symbol)
            how = "unanimously " if not n and not a and y and not v.totals.get("X") else ""
            return (f"Adopted {how}as resolution {number}: {_plural(y, 'vote', 'votes')} in favour, "
                    f"{n if n else 'none'} against and {ab}")
        if v.reason == "veto":
            names = self.vetoed_names(v, link=False, sentence=True)
            return "Not adopted, owing to the negative vote of a permanent member of the Council" + \
                (f" ({names} voted against)" if names else "")
        if v.reason == "votes":
            return VOTES_WORDS[0].upper() + VOTES_WORDS[1:]
        return "Not adopted"

    def lede(self, v: CouncilVote) -> str:
        where = f", at its {meeting_ordinal(v.meeting)} meeting" if meeting_ordinal(v.meeting) else ""
        if v.meeting and not meeting_ordinal(v.meeting):
            where = f", in the letter {v.meeting}"
        kind = "resolution" if v.adopted else v.what
        result = self.result_line(v)
        return f"A vote of the Security Council on {long_date(v.on)}{where}, on a {kind}: {result[:1].lower()}{result[1:]}."

    # Pages

    def _line(self, v: CouncilVote) -> str:
        return (f'<li><a href="{v.path}">{e(v.heading)}</a><span class="vote-index__meta">{e(long_date(v.on))}. '
                f"{e(self.result_line(v))}.</span></li>")

    def index(self) -> str:
        cat = self.catalog()
        votes = cat.newest_first
        parts = [f"<h1>{INDEX_NAME}</h1>"]
        if not votes:
            parts.append('<p class="lede">No Security Council votes have been read in yet.</p>')
        else:
            adopted = [v for v in votes if v.adopted]
            failed = [v for v in votes if not v.adopted]
            first = self.first_record()
            parts.append(f'<p class="lede">Every resolution the Security Council has adopted, with how each member '
                         f"voted, from the UN’s own voting data: {_plural(len(adopted), 'resolution', 'resolutions')}"
                         + (f", from {e(long_date(adopted[-1].on))} to {e(long_date(adopted[0].on))}" if adopted
                            else "")
                         + ". And the drafts it did not adopt: "
                         + _plural(len(failed), "draft", "drafts") + ".</p>")
            parts.append("<p>A draft is not adopted when it does not get nine votes in favour, or when a permanent "
                         "member of the Council votes against it (commonly called a veto). Drafts not adopted are not "
                         "in the UN’s voting data: they are read from the Council’s meeting records"
                         + (f", which the site holds from {e(long_date(first))}" if first else "")
                         + ", with how each member voted. Vetoes from before then are listed as the UN Dag "
                         "Hammarskjöld Library’s list of vetoes gives them: the draft, the meeting and the permanent "
                         "members voting against.</p>")
            parts.append('<p>The General Assembly’s votes are in <a href="/votes.html">Recorded votes in the General '
                         "Assembly</a>.</p>")
            parts.append('<h2 id="latest">Latest votes</h2><ul class="vote-index">'
                         + "".join(self._line(v) for v in votes[:LATEST]) + "</ul>")
            parts.append('<h2 id="not-adopted">Drafts not adopted</h2>'
                         "<p>Newest first. Where a permanent member’s negative vote defeated a draft, the permanent "
                         "members voting against are named.</p>"
                         '<ul class="vote-index">' + "".join(self._line(v) for v in failed) + "</ul>")
            parts.append('<h2 id="years">By year</h2><ul class="session-index">' + "".join(
                f'<li><a href="{YEAR_PATH.format(year=y)}">{y}</a>, <span class="session-index__meta">'
                f"{_plural(len(vs), 'vote', 'votes')}</span></li>"
                for y, vs in sorted(cat.years.items(), reverse=True)) + "</ul>")
        parts.append(self._source())
        parts.append(_named_aside(None))
        description = ("Every Security Council resolution since 1946 with how each member voted, and the drafts not "
                       "adopted, including vetoes, from the UN’s voting data and meeting records.")
        return self.votes.page(title=f"{INDEX_NAME} | {SITE_NAME}", description=description, path=INDEX_PATH,
                               trail=[(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH)], main="".join(parts))

    def _source(self) -> str:
        return ('<p class="vote-source">Adopted resolutions are from the <a href="' + e(self.record) + f'"{OPENED}>'
                "Security Council voting data</a> of the UN Dag Hammarskjöld Library, © United Nations, used for "
                "non-commercial purposes with attribution; drafts not adopted from the Council’s meeting records, "
                'checked against the Library’s <a href="https://research.un.org/en/docs/sc/quick/veto"'
                f"{OPENED}>list of vetoes</a>. How the record is read in: "
                '<a href="/methodology.html#record">The record</a>, on the methodology page.</p>')

    def year_page(self, year: int) -> str | None:
        vs = self.catalog().years.get(year)
        if not vs:
            return None
        name = f"{INDEX_NAME}, {year}"
        path = YEAR_PATH.format(year=year)
        failed = sum(1 for v in vs if not v.adopted)
        main = (f'<p class="crumbs"><a href="{INDEX_PATH}">{INDEX_NAME}</a></p><h1>{e(name)}</h1>'
                f'<p class="lede">The Security Council’s {_plural(len(vs), "vote", "votes")} in {year}, newest first: '
                f"{_plural(len(vs) - failed, 'resolution', 'resolutions')} adopted and "
                f"{_plural(failed, 'draft', 'drafts')} not adopted. Each vote’s page gives how each member voted.</p>"
                '<ul class="vote-index">' + "".join(self._line(v) for v in vs) + "</ul>" + _named_aside(None))
        description = shorten(f"How each member voted in the Security Council’s {len(vs):,} votes in {year}: the "
                              "resolutions adopted and the drafts not adopted.", DESCRIPTION_MAX)
        return self.votes.page(title=f"{name} | {SITE_NAME}", description=description, path=path,
                               trail=[(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH), (name, path)], main=main)

    def vote_page(self, slug: str) -> str | None:
        v = self.catalog().votes.get(slug)
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

    def description(self, v: CouncilVote) -> str:
        what = f"resolution {v.name}" if v.adopted else f"{v.what} {v.name}"
        subject = v.title if v.adopted else v.agenda
        return shorten(f"How each member voted on Security Council {what}, on {long_date(v.on)}: "
                       f"{self.result_line(v)}. {subject}.", DESCRIPTION_MAX)

    def _vote_page(self, v: CouncilVote) -> str:
        members = self.members(v)
        year_path = YEAR_PATH.format(year=v.on.year)
        trail = [(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH), (v.heading, v.path)]
        doc = "Resolution" if v.adopted else v.what[0].upper() + v.what[1:]
        oral = v.symbol.startswith("S/PV.")  # a proposal made at the meeting: the list gives its record
        facts = [(doc, "Made at the meeting, with no document of its own" if oral else
                  f'<a href="{e(undocs(v.symbol))}"{OPENED}>{e(v.symbol)}</a>')]
        if v.adopted:
            facts.append(("Title", e(v.title) + ' <span class="vote-facts__note">(the UN Library’s description)'
                                                 "</span>"))
        facts.append(("Date", f'{e(long_date(v.on))} (<a href="{year_path}">votes in {v.on.year}</a>)'))
        if v.meeting:
            label = "Meeting record" if "/PV." in v.meeting else "Letter recording the vote"
            facts.append((label, f'<a href="{e(undocs(v.meeting))}"{OPENED}>{e(v.meeting)}</a>'))
        if v.draft and "/PV." not in v.draft:
            facts.append(("Draft", f'<a href="{e(undocs(v.draft))}"{OPENED}>{e(v.draft)}</a>'))
        if v.amends:
            facts.append(("To the draft", f'<a href="{e(undocs(v.amends))}"{OPENED}>{e(v.amends)}</a>'))
        items = [a.strip().rstrip(".") for a in v.agenda.split("|") if a.strip()]
        if items:
            facts.append(("Agenda item" if len(items) == 1 else "Agenda items", "<br>".join(e(a) for a in items)))
        subjects = [s.strip() for s in v.subjects.split("|") if s.strip()]
        if subjects:
            facts.append(("Subjects", e("; ".join(subjects))))
        if v.result:
            facts.append(("Result", f"“{e(v.result)}” <span class=\"vote-facts__note\">(the meeting record)</span>"))
        else:
            facts.append(("Result", e(self.result_line(v)) + "." + (
                ' <span class="vote-facts__note">(the UN Library’s list of vetoes)</span>'
                if v.source == "veto list" else "")))
        if v.vetoed_by:
            facts.append(("Permanent members voting against" if len(v.vetoed_by) > 1 else
                          "Permanent member voting against", self.vetoed_names(v, sentence=False)))
        parts = [f'<p class="crumbs"><a href="{INDEX_PATH}">{INDEX_NAME}</a>: <a href="{year_path}">{v.on.year}</a></p>',
                 f"<h1>{e(v.heading)}</h1>", f'<p class="lede">{e(self.lede(v))}</p>',
                 '<div class="vote-layout"><div class="vote-main">',
                 '<dl class="facts vote-facts">' + "".join(f"<div><dt>{k}</dt><dd>{val}</dd></div>"
                                                           for k, val in facts) + "</dl>"]
        if v.reason == "veto":
            parts.append("<p>Under Article 27 of the Charter of the United Nations, a decision of the Council on a matter "
                         "that is not procedural needs nine votes in favour, including the concurring votes of the "
                         "permanent members, so a draft that a permanent member votes against is not adopted. This is "
                         "commonly called a veto.</p>")
        links = [] if oral else [f'<li><a href="{e(undocs(v.symbol))}"{OPENED}>The '
                                 f'{e(v.what if not v.adopted else "resolution")}, {e(v.symbol)}, on the UN’s Official '
                                 "Document System</a></li>"]
        if v.meeting:
            links.append(f'<li><a href="{e(undocs(v.meeting))}"{OPENED}>The record of the vote, {e(v.meeting)}</a>'
                         "</li>")
        if v.adopted and v.url:
            links.append(f'<li><a href="{e(v.url)}"{OPENED}>The voting record in the UN Digital Library</a></li>')
        parts.append('<h2 id="record">In the UN’s records</h2><ul class="plain-list">' + "".join(links) + "</ul>")
        if v.note:
            parts.append(f"<p>The UN’s voting data notes: “{e(v.note)}”</p>")
        parts.append(self._how(v, members))
        parts.append('<p class="vote-source">Each member is given the UN’s short name, as everywhere on this site; '
                     "China’s seat to 25 October 1971 is the Republic of China’s. "
                     + ("From the " + f'<a href="{e(self.record)}"{OPENED}>Security Council voting data</a> of the UN '
                        "Dag Hammarskjöld Library, © United Nations." if v.adopted else
                        "From the UN Dag Hammarskjöld Library’s list of vetoes, © United Nations." if v.source ==
                        "veto list" else "Read from the Council’s meeting record, and checked against the UN Dag "
                        "Hammarskjöld Library’s list of vetoes, © United Nations.") + "</p>")
        parts.append(STATEMENTS)
        parts.append("</div>" + _named_aside(v.path) + "</div>")
        return self.votes.page(title=v.page_title, description=self.description(v), path=v.path, trail=trail,
                               main="".join(parts))

    def _how(self, v: CouncilVote, members: list[tuple[str, str, str, bool]]) -> str:
        """The totals and each member under how it voted."""
        head = '<h2 id="how">How each member voted</h2>'
        if v.without_vote:
            names = "".join(f"<li>{self.linked(code, v.on)}{' <span class="vote-list__p5">(permanent member)</span>' if p5 else ''}</li>"
                            for _, code, _, p5 in members)
            return (head + "<p>The resolution was adopted without a vote, so no member’s vote is recorded."
                    + (" The members of the Council at the time:</p>"
                       f'<ul class="vote-list" aria-label="Members of the Council">{names}</ul>' if names else "</p>"))
        if not members and v.source != "veto list":
            # read from a meeting record whose printed lists the site could not read with certainty (``draft_rows``):
            # the totals it announces, and never a member's vote that might be wrong
            totals = [(LABEL[k], v.totals.get(k)) for k in ORDER if v.totals.get(k) is not None]
            table = ('<table class="vote-totals"><caption>Totals, as the record gives them</caption><thead><tr>'
                     '<th scope="col">Vote</th><th scope="col">Members</th></tr></thead><tbody>'
                     + "".join(f'<tr><th scope="row">{label}</th><td>{n:,}</td></tr>' for label, n in totals)
                     + "</tbody></table>") if totals else ""
            return (head + table + "<p>How each member voted is not listed here: the site could not read the lists in "
                    "the meeting record with certainty, so it gives only what the record announces. The meeting record "
                    "has how each member voted.</p>")
        if not members:
            first = self.first_record()
            when = f" The site’s records of the Council’s meetings begin on {long_date(first)}." if first and \
                v.on < first else ""
            return (head + "<p>How each member voted is not given here: this draft is known from the UN Dag Hammarskjöld "
                    f"Library’s list of vetoes, which gives the permanent members voting against.{when} The "
                    "meeting record has the vote.</p>")
        by: dict[str, list[tuple[str, str, bool]]] = {}
        for name, code, how, p5 in members:
            by.setdefault(how, []).append((name, code, p5))
        counts = {k: len(by.get(k, [])) for k in ORDER}
        totals = {k: v.totals.get(k) for k in ORDER}
        shown = [k for k in ORDER if counts[k] or totals.get(k)]
        rows = "".join(f'<tr><th scope="row">{LABEL[k]}</th><td>{(totals[k] if totals[k] is not None else counts[k]):,}'
                       f"</td></tr>" for k in shown)
        out = [head, '<table class="vote-totals"><caption>Totals, as the record gives them</caption>'
                     '<thead><tr><th scope="col">Vote</th><th scope="col">Members</th></tr></thead>'
                     f"<tbody>{rows}</tbody></table>"]
        if not v.complete and any(totals[k] is not None and counts[k] != totals[k] for k in ("Y", "N", "A")):
            out.append("<p>The record as the site reads it does not name every member who voted: only those named are "
                       "listed below. The meeting record has the full vote.</p>")
        for k in ORDER:
            names = by.get(k) or []
            if not names:
                continue
            anchor = slug_of(LABEL[k])
            out.append(f'<h3 id="{anchor}">{LABEL[k]} ({len(names):,})</h3>'
                       f'<ul class="vote-list" aria-labelledby="{anchor}">'
                       + "".join(f"<li>{self.linked(code, v.on)}{' <span class="vote-list__p5">(permanent member)</span>' if p5 else ''}</li>"
                                 for _, code, p5 in names) + "</ul>")
        return "".join(out)

    def hidden_page(self, slug: str, note: str) -> str | None:
        v = self.catalog().votes.get(slug)
        if v is None:
            return None
        main = (f'<p class="crumbs"><a href="{INDEX_PATH}">{INDEX_NAME}</a></p><h1>{e(v.name)}</h1>'
                f'<p class="note">{e(note)}</p>' + _named_aside(None))
        return self.votes.page(title=f"{v.name} | {SITE_NAME}", description=f"Security Council vote {v.name}.",
                               path=v.path, trail=[(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH), (v.name, v.path)],
                               main=main, noindex=True)

    # Addresses

    def moved(self, rest: str) -> str | None:
        """Another form of a vote's address (any case, its symbol with slashes, a trailing slash, its UN Digital
        Library number), sent to its page for good. ``rest`` is what follows "/votes/"."""
        asked = re.sub(r"(?:\.html)?/*$", "", rest.strip().strip("/"), flags=re.I)
        cat = self.catalog()
        if asked.isdigit() and asked in cat.by_id:
            return cat.votes[cat.by_id[asked]].path
        slug = slug_of(asked)
        return cat.votes[slug].path if slug in cat.votes else None

    def year_moved(self, rest: str) -> str | None:
        asked = re.sub(r"(?:\.html)?/*$", "", rest.strip().strip("/"), flags=re.I)
        if not asked:
            return INDEX_PATH
        if asked.isdigit() and int(asked) in self.catalog().years:
            return YEAR_PATH.format(year=int(asked))
        return None

    def sitemap(self, hidden: Callable[[str], bool] = lambda _: False) -> list[tuple[str, str]]:
        cat = self.catalog()
        votes = cat.newest_first
        out = [(INDEX_PATH, votes[0].on.isoformat() if votes else "")]
        out += [(YEAR_PATH.format(year=y), vs[0].on.isoformat()) for y, vs in sorted(cat.years.items(), reverse=True)]
        out += [(v.path, v.on.isoformat()) for v in votes if not hidden(v.path)]
        return out

    # For the country pages (decision 29)

    def cast(self, code: str) -> list[tuple[CouncilVote, str]]:
        """Every Council vote that lists this member, with how it voted, newest first (votes taken without a vote are
        left out: no member's vote is recorded)."""
        cat = self.catalog()
        if not self.stamp():
            return []
        path = str(self.path).replace("'", "''")
        rows = self.votes._con.execute(f"SELECT vote_id, vote FROM read_parquet('{path}') WHERE country_code = ? AND "
                                       "vote IS NOT NULL AND coalesce(modality, '') <> 'Without Vote'",
                                       [code]).fetchall()
        out = [(cat.votes[cat.by_id[str(i)]], how) for i, how in rows if str(i) in cat.by_id and how in ORDER]
        out.sort(key=lambda x: (x[0].on, x[0].symbol), reverse=True)
        return out

    def seat_years(self, code: str) -> list[int]:
        """The years the voting data lists this member on the Council."""
        if not self.stamp():
            return []
        path = str(self.path).replace("'", "''")
        rows = self.votes._con.execute(f"SELECT DISTINCT year(date) FROM read_parquet('{path}') WHERE country_code = ? "
                                       "AND source = 'voting data'", [code]).fetchall()
        return sorted(int(y) for (y,) in rows if y)

    def vetoed(self, code: str) -> list[CouncilVote]:
        """Drafts not adopted owing to this permanent member's negative vote, newest first."""
        return [v for v in self.catalog().newest_first if code in v.vetoed_by]


def meeting_ordinal(meeting: str) -> str:
    m = re.match(r"S/PV\.(\d+)", meeting or "")
    return ordinal(int(m[1])) if m else ""
