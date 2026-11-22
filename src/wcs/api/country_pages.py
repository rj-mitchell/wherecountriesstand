"""A page for each country in the UN's record, and their index (decision 29).

    /countries.html                  the index: the Member States and observer States from A to Z, then by region,
                                     then the former Member States and the others named in the records
    /countries/ita.html              one country: its ISO 3166 alpha-3 code in lower case (a former Member State's
                                     code from data_static/states.csv: ``/countries/sun.html``)
    /countries/ita/votes.html        every recorded vote of the General Assembly it is listed in, by session

Which places have a page follows the UN's own lists and record (decision 29): the 193 Member States, the two
non-member observer States, the former Member States in ``states.csv``, and Kosovo, which speaks in the Security
Council's record. Territories have none. Names and status are the UN's (decision 6).

Every page is written on the server from the site's data, with no AI and no script: the reference data
(``wcs.reference.countries``), the speeches the index is built from (the Parquet records), the votes file (through
the vote pages' own catalogue, ``wcs.api.votes``), the topic briefings and the statements published under the right
of reply. Nothing here compares one country's votes with another's: counts and lists only (decision 29).

What is read from the records and the votes file is kept until the files change (their times and sizes), as the vote
pages keep theirs, so the update that reads in new records or a new votes file is all it takes for the pages to
follow: there is nothing to build on the server.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from wts_core.checks import curl_quotes
from wts_core.dates import long_date

from wcs.api.briefing_html import TEXT_NOTES
from wcs.api.council_votes import LABEL as COUNCIL_LABEL
from wcs.api.council_votes import CouncilVote, CouncilVotePages
from wcs.api.topic_pages import TopicPages, final_and_sources
from wcs.api.votes import (
    COUNTRY_PAGE_KINDS,
    OPENED,
    SITE_NAME,
    VOTES_IN_ORDER,
    Vote,
    VotePages,
    _plural,
    _sort_name,
    e,
    in_a_sentence,
    result_words,
    shorten,
    statements_html,
)
from wcs.rag.sources import VOTE_LABEL
from wcs.reference.countries import REGIONS, ROC_LAST_DAY, ROC_UNTIL, Countries, Country

INDEX_PATH = "/countries.html"
INDEX_NAME = "Countries in the record"
LIVES = "https://wherelivesstand.com/country/"
NUMBER_WORDS = {1: "one", 2: "two", 3: "three"}
LATEST_STATEMENTS = 10
LATEST_VOTES = 20
YEARS_COUNTED = 10
FIRST_WORDS = 40
VOTE_TITLE_MAX = 90  # characters of a resolution's title in a country's latest votes
LIST_TITLE_MAX = 60  # and in its full list of votes
DESCRIPTION_MAX = 160
KEEP = 400  # countries whose statements and votes are kept once read (there are 204 pages)
KINDS = COUNTRY_PAGE_KINDS
STATE_KINDS = KINDS  # a State speaking, as the search counts one (wcs.engine.STATE_KINDS)
CODE = re.compile(r"[a-z]{3}")
# the groups listed under "Other groups", in this order (the region, the UN regional group, the Security Council
# and the income group have rows of their own)
OTHER_GROUPS = ("eu", "nato", "au", "au_suspended", "g77", "nam", "oic", "ldc", "lldc", "sids")
INCOME = ("income:low", "income:lower_middle", "income:upper_middle", "income:high")
REGION_NAMES = {key: name for name, key in REGIONS.items()}
_SINCE = re.compile(r"\bsince (\d{1,2} [A-Z][a-z]+ \d{4})\b")
_LIVES_SPINE = re.compile(r'<svg class="wts-spine wts-cloth--lives".*?</svg>', re.S)


# Places the UN's records name that do not speak or vote in them as States, and so have no page (decision 29): one
# sentence each of their status, in the owner's words, and a search of the records about them, sources only.
_SAR = "A Special Administrative Region of China. It does not speak or vote separately in the United Nations’ records."
UNLISTED = (
    ("taiwan", "Taiwan", "China’s seat at the United Nations was held by the Republic of China, whose government "
                         "has been based in Taiwan since 1949, until 25 October 1971 (General Assembly resolution "
                         "2758 (XXVI)). Taiwan has not spoken or voted in the United Nations’ records since."),
    ("hong-kong", "Hong Kong", _SAR),
    ("macao", "Macao", _SAR),
    ("western-sahara", "Western Sahara", "A Non-Self-Governing Territory on the United Nations’ list. It is discussed "
                                         "in the records but does not speak or vote in them."),
)


def search_path(place: str) -> str:
    """The home page's address for a search of the records about a place, sources only (no AI): what its form sends
    with "Sources only (no AI)" pressed, with no script."""
    return "/?" + urlencode({"q": f"What has been said about {place}?", "sources": "1"})


def path_of(code: str) -> str:
    return f"/countries/{code.lower()}.html"


def votes_path_of(code: str) -> str:
    return f"/countries/{code.lower()}/votes.html"


def council_path_of(code: str) -> str:
    return f"/countries/{code.lower()}/security-council-votes.html"


def terms(years: list[int]) -> list[str]:
    """Years on the Security Council, joined into runs: [1995, 1996, 2007, 2008, 2017] is "1995 to 1996", "2007 to
    2008" and "2017"."""
    out: list[str] = []
    run: list[int] = []
    for y in sorted(set(years)):
        if run and y != run[-1] + 1:
            out.append(str(run[0]) if len(run) == 1 else f"{run[0]} to {run[-1]}")
            run = []
        run.append(y)
    if run:
        out.append(str(run[0]) if len(run) == 1 else f"{run[0]} to {run[-1]}")
    return out


def its_latest(n: int, which: str) -> str:
    """"Its 20 most recent recorded votes", or "Its most recent recorded vote" for one."""
    if n == 1:
        return f"Its {which} recorded vote"
    return f"Its last {n} recorded votes" if which == "last" else f"Its {n} {which} recorded votes"


def and_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


@dataclass(frozen=True)
class Period:
    """A seat held, under the country's name, by another government until a day (decision 5): its record before
    then is shown in a section of its own, and the page's other sections begin on that day."""
    until: date
    heading: str
    note: str
    main_note: str


PERIODS = {"CHN": Period(
    until=ROC_UNTIL,
    heading="China’s seat, 1945 to 1971: the Republic of China",
    note="China’s seat at the United Nations was held by the Republic of China, whose government has been based "
         "in Taiwan since 1949, until 25 October 1971 (General "
         "Assembly resolution 2758 (XXVI)). Its statements and recorded votes from those years are listed here, "
         "newest first, labelled “China (Republic of China)” as everywhere on this site.",
    main_note="Listed from 26 October 1971. The record to 25 October 1971, the Republic of China’s, is in its "
              "own section below.")}
PERIOD_STATEMENTS = 30


@dataclass(frozen=True)
class Entity:
    """A place with a page: a Member State, an observer State, a former Member State, or another named in the
    records (Kosovo)."""
    country: Country

    @property
    def code(self) -> str:
        return self.country.code

    @property
    def name(self) -> str:
        return curl_quotes(self.country.name)

    @property
    def kind(self) -> str:
        return self.country.kind

    @property
    def path(self) -> str:
        return path_of(self.code)

    @property
    def votes_path(self) -> str:
        return votes_path_of(self.code)

    @property
    def council_path(self) -> str:
        return council_path_of(self.code)

    @property
    def page_title(self) -> str:
        return f"{self.name} | {SITE_NAME}"

    @property
    def last_day(self) -> date | None:
        """A former Member State's last day as one (the day before ``valid_to`` in states.csv)."""
        to = self.country.valid_to
        return to - timedelta(days=1) if to else None

    @property
    def votes(self) -> bool:
        """Whether it has, or had, a vote in the General Assembly."""
        return self.kind in ("member_state", "former_member_state")

    @property
    def sister(self) -> bool:
        """Whether Where Lives Stand has a page for it: the Member States, the State of Palestine and Kosovo
        (decision 29), not the Holy See, for which it has no figures, nor the former Member States."""
        return self.kind in ("member_state", "other_state") or self.code == "PSE"


@dataclass(frozen=True)
class Statement:
    record_id: str
    source: str
    body: str
    on: date
    precision: str
    meeting: str
    symbol: str
    agenda: str
    url: str
    words: str
    more: bool
    notes: tuple[str, ...]
    as_name: str          # the name the record gives the country on the day ("China (Republic of China)")


def entities(reference: Countries) -> dict[str, Entity]:
    """Every place with a page, by its code."""
    return {c.code: Entity(c) for c in reference.by_code.values() if c.kind in KINDS}


class CountryPages:
    """The country pages of one site. They are written into the vote pages' frame (``votes.frame``, the site's
    head, header and footer), with "Countries" marked in the menu (``frame``, set as the site's pages are put
    together)."""

    def __init__(self, votes: VotePages, topics: TopicPages | None, parquet_dir: Path, *,
                 today: Callable[[], date] = date.today,
                 hidden_records: Callable[[], Any] = dict,
                 statements: Callable[[str], list[dict[str, Any]]] = lambda _code: [],
                 not_endorsed: str = "",
                 briefings: Callable[[], bool] = lambda: True,
                 council: CouncilVotePages | None = None):
        self.votes = votes
        self.council = council or CouncilVotePages(votes)
        self.topics = topics
        self.records_dir = Path(parquet_dir) / "records"
        self.today = today
        self.hidden_records = hidden_records
        self.statements = statements
        self.not_endorsed = not_endorsed
        self.briefings = briefings
        self.frame = ""
        self._lock = threading.Lock()
        self._entities: dict[str, Entity] | None = None
        self._records_stamp: str | None = None
        self._summary: dict[str, dict[str, Any]] = {}
        self._columns: set[str] = set()
        self._latest: dict[str, list[Statement]] = {}
        self._votes_stamp: str | None = None
        self._cast: dict[str, list[tuple[Vote, str]]] = {}
        self._vote_counts: dict[str, tuple[int, str]] = {}

    # The places

    @property
    def countries(self) -> Countries:
        return self.votes.countries

    @property
    def entities(self) -> dict[str, Entity]:
        if self._entities is None:
            self._entities = entities(self.countries)
        return self._entities

    def entity(self, slug: str) -> Entity | None:
        return self.entities.get(slug.upper()) if CODE.fullmatch(slug) else None

    # The records (the speeches the index is built from)

    def _files(self) -> list[Path]:
        return sorted(self.records_dir.glob("*/*.parquet")) if self.records_dir.is_dir() else []

    def _stamp(self, files: list[Path]) -> str:
        parts = []
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            parts.append(f"{p.parent.name}/{p.name}:{st.st_mtime_ns}:{st.st_size}")
        return "|".join(parts)

    def _records(self) -> tuple[list[Path], dict[str, dict[str, Any]]]:
        """The record files, and for each country the date of its newest statement of its own and the years the
        Security Council's records show it at the table as an elected member, read again when a file changes."""
        files = self._files()
        stamp = self._stamp(files)
        if stamp == self._records_stamp:
            return files, self._summary
        summary: dict[str, dict[str, Any]] = {}
        columns: set[str] = set()
        if files:
            src = self._source(files)
            columns = {r[0] for r in self.votes._con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
            kinds = ", ".join(f"'{k}'" for k in STATE_KINDS)
            rows = self.votes._con.execute(f"""
                SELECT country_code,
                       max(date) FILTER (WHERE coalesce(on_behalf_of, '') = ''),
                       list(DISTINCT year) FILTER (WHERE source = 'sc' AND participant IN ('member', 'president'))
                FROM {src}
                WHERE country_code IS NOT NULL AND entity_kind IN ({kinds})
                GROUP BY country_code""").fetchall()
            summary = {code: {"newest": newest, "council": sorted(years or [])} for code, newest, years in rows}
        with self._lock:
            self._summary, self._columns, self._latest = summary, columns, {}
            self._records_stamp = stamp
        return files, summary

    @staticmethod
    def _source(files: list[Path]) -> str:
        listed = ", ".join("'" + str(p).replace("'", "''") + "'" for p in files)
        return f"read_parquet([{listed}], union_by_name=true)"

    def latest_statements(self, code: str, *, since: date | None = None, before: date | None = None,
                          most: int = LATEST_STATEMENTS) -> list[Statement]:
        """Its newest statements of its own in the Security Council and the General Debate (from ``since``, before
        ``before``), newest first: never one made on behalf of a group, which is the group's (decision 7), nor a
        record hidden while a concern about it is decided."""
        files, _ = self._records()
        if not files:
            return []
        hidden = set(self.hidden_records() or ())
        key = f"{code}:{since}:{before}:{most}"
        with self._lock:
            kept = self._latest.get(key)
        if kept is None:
            kept = self._read_latest(files, code, most + 20, since, before)
            with self._lock:
                self._latest[key] = kept
                while len(self._latest) > KEEP:
                    self._latest.pop(next(iter(self._latest)))
        return [s for s in kept if s.record_id not in hidden][:most]

    def _read_latest(self, files: list[Path], code: str, n: int, since: date | None = None,
                     before: date | None = None) -> list[Statement]:
        cols = self._columns
        extra = ", ".join(c if c in cols else f"NULL AS {c}" for c in ("text_source", "language", "ocr",
                                                                         "date_precision"))
        kinds = ", ".join(f"'{k}'" for k in STATE_KINDS)
        dates = "".join(f" AND date {op} ?" for op, d in ((">=", since), ("<", before)) if d)
        rows = self.votes._con.execute(f"""
            SELECT record_id, source, body, date, meeting, symbol, agenda, url, text, {extra}
            FROM {self._source(files)}
            WHERE country_code = ? AND entity_kind IN ({kinds}) AND coalesce(on_behalf_of, '') = ''
                  AND trim(coalesce(text, '')) <> ''{dates}
            ORDER BY date DESC, source, record_id DESC
            LIMIT {int(n)}""", [code, *(d for d in (since, before) if d)]).fetchall()
        out = []
        for (record_id, source, body, on, meeting, symbol, agenda, url, text, text_source, language, ocr,
             precision) in rows:
            words = (text or "").split()
            notes = tuple(x for x in ("English text: UN translation." if language else None,
                                      TEXT_NOTES.get(text_source or ""),
                                      "Text recognised from a scanned record." if ocr else None) if x)
            out.append(Statement(record_id=record_id, source=source, body=body or "", on=on,
                                 precision=precision or ("session" if source == "gd" else "day"),
                                 meeting=(meeting or "").strip(), symbol=(symbol or "").strip(),
                                 agenda=curl_quotes((agenda or "").strip()), url=(url or "").strip(),
                                 words=curl_quotes(" ".join(words[:FIRST_WORDS])), more=len(words) > FIRST_WORDS,
                                 notes=notes, as_name=curl_quotes(self.countries.name_on(code, on))))
        return out

    def council_years(self, code: str) -> list[int]:
        _, summary = self._records()
        return list(summary.get(code, {}).get("council") or [])

    # The votes (the vote pages' catalogue)

    def _votes_fresh(self) -> None:
        """How many recorded votes with a page each country is listed in, and the newest, read again when the votes
        file changes (for the sitemap, and whether a country has a list of votes)."""
        stamp = self.votes.stamp()
        if stamp == self._votes_stamp:
            return
        counts: dict[str, tuple[int, str]] = {}
        if stamp:
            path = str(self.votes.path).replace("'", "''")
            # as the catalogue counts a vote with a page (wcs.api.votes.catalog_of): a symbol, a date, and how
            # countries voted
            rows = self.votes._con.execute(f"""
                SELECT country_code, count(DISTINCT undl_id), max(date) FROM read_parquet('{path}')
                WHERE vote IN ('Y', 'N', 'A', 'X') AND coalesce(resolution, '') <> '' AND date IS NOT NULL
                      AND country_code IS NOT NULL
                GROUP BY country_code""").fetchall()
            counts = {code: (int(n), newest.isoformat()) for code, n, newest in rows}
        with self._lock:
            self._vote_counts, self._cast, self._votes_stamp = counts, {}, stamp

    def cast(self, code: str) -> list[tuple[Vote, str]]:
        """Every recorded vote it is listed in, with how it voted (Y, N, A or X), newest first."""
        self._votes_fresh()
        with self._lock:
            kept = self._cast.get(code)
        if kept is not None:
            return kept
        cat = self.votes.catalog()
        out: list[tuple[Vote, str]] = []
        if self.votes.stamp():
            path = str(self.votes.path).replace("'", "''")
            rows = self.votes._con.execute(f"SELECT undl_id, vote FROM read_parquet('{path}') WHERE country_code = ? "
                                           "AND vote IS NOT NULL", [code]).fetchall()
            for undl_id, how in rows:
                slug = cat.by_undl.get(str(undl_id))
                if slug is not None and how in VOTES_IN_ORDER:
                    out.append((cat.votes[slug], how))
            out.sort(key=lambda x: (x[0].on, x[0].symbol), reverse=True)
        with self._lock:
            self._cast[code] = out
            while len(self._cast) > KEEP:
                self._cast.pop(next(iter(self._cast)))
        return out

    def vote_count(self, code: str) -> tuple[int, str]:
        """How many recorded votes it is listed in, and the date of the newest."""
        self._votes_fresh()
        return self._vote_counts.get(code, (0, ""))

    # The frame

    def page(self, **kw: Any) -> str:
        return self.votes.page(frame=self.frame or None, **kw)

    def _lives_spine(self) -> str:
        m = _LIVES_SPINE.search(self.frame or self.votes.frame)
        return m[0] if m else ""

    # The index

    def index(self) -> str:
        listed = sorted(self.entities.values(), key=lambda x: _sort_name(x.name))
        current = [x for x in listed if x.kind in ("member_state", "observer_state")]
        former = [x for x in listed if x.kind == "former_member_state"]
        others = [x for x in listed if x.kind == "other_state"]
        members = sum(1 for x in current if x.kind == "member_state")
        observers = len(current) - members

        def item(x: Entity) -> str:
            status = ""
            if x.kind == "observer_state":
                status = '<span class="country-list__status">, non-member observer State</span>'
            elif x.kind == "former_member_state" and x.country.valid_from and x.country.valid_to:
                status = (f'<span class="country-list__status">, {x.country.valid_from.year} to '
                          f"{x.country.valid_to.year}</span>")
            return f'<li><a href="{x.path}">{e(x.name)}</a>{status}</li>'

        regions = [(key, name) for name, key in REGIONS.items()]
        jumps = [("a-z", "A to Z"), *((f"region-{key.split(':')[1]}", name) for key, name in regions),
                 ("former", "Former Member States"), ("others", "Others named in the records"),
                 ("no-page", "Places with no page of their own")]
        parts = [f"<h1>{INDEX_NAME}</h1>",
                 f'<p class="lede">A page for each of the {members} Member States of the United Nations and its '
                 f"{NUMBER_WORDS.get(observers, observers)} non-member observer States, for the former Member "
                 "States, and for Kosovo, which speaks in the Security Council’s record: each with its groups, its "
                 "latest statements and its recorded votes in the General Assembly, from the UN’s records.</p>",
                 "<p>Which places have a page follows the UN’s own lists and records, under the UN’s names, and "
                 "implies no view on any place’s status. Each page is written from the record, with no AI.</p>",
                 '<nav class="country-jump" aria-label="On this page"><ul>'
                 + "".join(f'<li><a href="#{anchor}">{e(name)}</a></li>' for anchor, name in jumps) + "</ul></nav>",
                 '<h2 id="a-z">Member States and observer States, A to Z</h2>',
                 '<ul class="country-list">' + "".join(item(x) for x in current) + "</ul>"]
        parts.append('<h2 id="regions">By region</h2>'
                     '<p>The same countries by their region in the UN’s M49 standard of country names and areas.</p>')
        for key, name in regions:
            here = [x for x in current if x.country.region and REGIONS.get(x.country.region) == key]
            parts.append(f'<h3 id="region-{key.split(":")[1]}">{e(name)}</h3>'
                         f'<ul class="country-list">' + "".join(item(x) for x in here) + "</ul>")
        parts.append('<h2 id="former">Former Member States</h2>'
                     "<p>States that were Member States of the United Nations, with the years of their membership "
                     "under that name. Each page gives its record up to its last day.</p>"
                     '<ul class="country-list">' + "".join(item(x) for x in former) + "</ul>")
        parts.append('<h2 id="others">Others named in the records</h2>'
                     "<p>Kosovo is not a member of the United Nations. It has a page because it speaks in the "
                     "Security Council’s record.</p>"
                     '<ul class="country-list">' + "".join(item(x) for x in others) + "</ul>")
        parts.append('<h2 id="no-page">Places with no page of their own</h2>'
                     "<p>These places have no page of their own, as the UN’s records do not treat them as "
                     "speakers. This follows the UN’s own lists and records, and implies no view on any place’s "
                     "status. A search of the records about each shows the sources only, with no AI.</p>"
                     '<ul class="country-unlisted">' + "".join(
                         f'<li><h3 id="no-page-{slug}">{e(name)}</h3><p>{e(sentence)}</p>'
                         f'<p><a href="{e(search_path(name))}">Search the records about {e(name)}</a> '
                         "(sources only, no AI)</p></li>" for slug, name, sentence in UNLISTED) + "</ul>")
        parts.append('<p class="vote-named">Named on these pages? See <a href="/named.html">If you’re named on this '
                     "site</a> to tell us something is wrong or unfair, or to add a statement in your own words.</p>")
        description = ("A page for each Member State and observer State of the United Nations, and the former "
                       "Member States: their groups, latest statements and recorded votes, from the UN’s records.")
        return self.page(title=f"{INDEX_NAME} | {SITE_NAME}", description=shorten(description, DESCRIPTION_MAX),
                         path=INDEX_PATH, trail=[(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH)], main="".join(parts))

    # A country's page

    def status_line(self, x: Entity) -> str:
        """Its status, in the UN's words: never a date the data does not hold."""
        c = x.country
        if x.kind == "member_state":
            admitted = c.valid_from  # the site holds no date of admission for today's members
            return ("Member State of the United Nations"
                    + (f" since {long_date(admitted)}" if admitted else "") + ".")
        if x.kind == "observer_state":
            m = _SINCE.search(c.note or "")
            return "Non-member observer State of the United Nations" + (f" since {m[1]}" if m else "") + "."
        if x.kind == "former_member_state":
            span = f", {c.valid_from.year} to {c.valid_to.year}" if c.valid_from and c.valid_to else ""
            return f"Former Member State{span}."
        return "Not a member of the United Nations."

    def groups_rows(self, x: Entity, on: date) -> list[tuple[str, str]]:
        """Its groups on a day, from ``Countries.groups_on`` and its labels: a label beside each value."""
        groups = set(self.countries.groups_on(x.code, on))
        labels = self.countries.group_labels
        rows: list[tuple[str, str]] = []
        region = next((g for g in sorted(groups) if g.startswith("region:")), None)
        if region:
            sub = x.country.subregion
            rows.append(("Region", e(REGION_NAMES.get(region, labels.get(region, region))
                                     + (f", {sub}" if sub else ""))))
        ungroups = [labels[g].removesuffix(" (UN regional group)") for g in sorted(groups)
                    if g.startswith("ungroup:") and g in labels]
        if ungroups:
            rows.append(("UN regional group" if len(ungroups) == 1 else "UN regional groups",
                         e(" and ".join(ungroups))))
        if "p5" in groups:
            rows.append(("Security Council", "Permanent member"))
        else:
            # its years on the Council from the Council's voting data, from 1946 (decision 30); the meeting records
            # since 1992 where that is not loaded
            seat = [y for y in self.council.seat_years(x.code) if date(y, 1, 1) <= on]
            years = seat or [y for y in self.council_years(x.code) if date(y, 1, 1) <= on]
            if years:
                shown = "the Council’s voting data shows" if seat else "the Council’s meeting records since 1992 show"
                rows.append(("Security Council", e(f"Elected member in {and_list(terms(years))}, as {shown} it")))
        others = [labels.get(g, g) for g in OTHER_GROUPS if g in groups]
        if others:
            rows.append(("Other groups", "<br>".join(e(g) for g in others)))
        income = next((labels.get(g, g) for g in INCOME if g in groups), None)
        if income:
            rows.append(("Income group", e(income)))
        return rows

    def _form(self, x: Entity) -> str:
        """A question about this country, sent to the home page as a plain form, with no script needed: the
        country is named in the question, so its statements on the subject are heard (decision 21)."""
        who = in_a_sentence(x.name)
        if x.kind == "former_member_state" and x.country.valid_from and x.country.valid_to:
            start = f"Between {x.country.valid_from.year} and {x.country.valid_to.year}, what did {who} say about "
        else:
            start = f"What has {who} said about "
        start = start[0].upper() + start[1:]
        if self.briefings():
            buttons = ('<button type="submit">Prepare briefing</button>'
                       '<button type="submit" class="button--quiet" name="sources" value="1">Sources only (no AI)'
                       "</button>")
        else:
            buttons = '<button type="submit" name="sources" value="1">Find sources</button>'
        return ('<form class="ask panel country-ask" action="/" method="get" aria-labelledby="ask-head">'
                f'<h2 id="ask-head">Ask about {e(in_a_sentence(x.name))}</h2>'
                '<label for="q">Your question</label>'
                '<div class="ask__row">'
                '<textarea id="q" name="q" rows="2" required minlength="3" maxlength="500" autocomplete="off" '
                f'enterkeyhint="go">{e(start)}</textarea>'
                f'<div class="ask__go">{buttons}</div></div>'
                '<p class="country-ask__note">Finish the question, or write your own. A briefing includes the '
                "statements of the countries a question names, where they have spoken on the subject.</p>"
                "</form>")

    INTRO = ("<p>Its most recent statements in the Security Council and the General Assembly’s General Debate, "
             "newest first, with rights of reply as records of their own. A statement made on behalf of a group is "
             "the group’s, and is not listed here.</p>")

    def _statement_list(self, x: Entity, found: list[Statement]) -> str:
        items = []
        for s in found:
            when = str(s.on.year) if s.precision == "session" else long_date(s.on)
            where = ", ".join(x for x in (s.body, s.meeting) if x)
            symbol = f', <a href="{e(s.url)}"{OPENED}>{e(s.symbol)}</a>' if s.symbol and s.url else \
                (f", {e(s.symbol)}" if s.symbol else "")
            named = f'<p class="country-statement__as">Recorded as {e(s.as_name)}.</p>' \
                if s.as_name != x.name else ""
            link = (f'<p class="country-statement__link"><a href="{e(s.url)}"{OPENED}>'
                    f"{e('Read ' + s.symbol if s.symbol else 'Read the statement')}</a></p>") if s.url else ""
            notes = f'<p class="country-statement__notes">{e(" ".join(s.notes))}</p>' if s.notes else ""
            items.append('<li class="country-statement">'
                         f'<p class="country-statement__meta">{e(when)}, {e(where)}{symbol}</p>'
                         + (f'<p class="country-statement__agenda">{e(s.agenda)}</p>' if s.agenda else "")
                         + named
                         + f"<blockquote><p>{e(s.words)}{' …' if s.more else ''}</p></blockquote>"
                         + notes + link + "</li>")
        return '<ol class="country-statements">' + "".join(items) + "</ol>"

    def _statements(self, x: Entity) -> str:
        period = PERIODS.get(x.code)
        found = self.latest_statements(x.code, since=period.until if period else None)
        head = '<h2 id="statements">Latest statements</h2>' + self.INTRO
        if period:
            head += f"<p>{e(period.main_note)}</p>"
        return head + (self._statement_list(x, found) if found else "<p>No statements in the record.</p>")

    def _counts_table(self, cast: list[tuple[Vote, str]], start: date, end: date) -> str:
        counts = {k: 0 for k in VOTES_IN_ORDER}
        for v, how in cast:
            if start <= v.on <= end:
                counts[how] += 1
        rows = "".join(f'<tr><th scope="row">{VOTE_LABEL[k]}</th><td>{counts[k]:,}</td></tr>' for k in VOTES_IN_ORDER)
        return ('<table class="vote-totals country-totals"><caption>Its recorded votes from '
                f"{e(long_date(start))} to {e(long_date(end))}</caption>"
                '<thead><tr><th scope="col">Vote</th><th scope="col">Recorded votes</th></tr></thead>'
                f"<tbody>{rows}</tbody></table>")

    @staticmethod
    def _cast_line(v: Vote, how: str, recorded_as: str | None = None) -> str:
        as_name = f" {e(recorded_as)}." if recorded_as else ""
        return (f'<li><a href="{v.path}">{e(v.name)}: {e(shorten(v.title, VOTE_TITLE_MAX))}</a>'
                f'<span class="vote-index__meta">{e(long_date(v.on))}. Its vote: {VOTE_LABEL[how]}.{as_name} '
                f"Result: {e(result_words(v.counts))}.</span></li>")

    @staticmethod
    def _list_line(v: Vote, how: str, recorded_as: str | None = None) -> str:
        """A vote in a country's full list, kept short (a Member State since 1946 is listed in over 5,000): the vote,
        its date and how the country voted. The result is on the vote's own page."""
        as_name = f" {e(recorded_as)}." if recorded_as else ""
        return (f'<li><a href="{v.path}">{e(v.name)}: {e(shorten(v.title, LIST_TITLE_MAX))}</a> '
                f'<span class="vote-index__meta">{e(long_date(v.on))}: {VOTE_LABEL[how]}.{as_name}</span></li>')

    def _recorded_as(self, x: Entity, on: date) -> str | None:
        """How the record names it on a day, where that is not today's name: "Recorded as China (Republic of
        China), to 25 October 1971" (decision 5)."""
        name = curl_quotes(self.countries.name_on(x.code, on))
        if name == x.name:
            return None
        period = PERIODS.get(x.code)
        when = f", to {long_date(ROC_LAST_DAY)}" if period and on < period.until else ""
        return f"Recorded as {name}{when}"

    def _period(self, x: Entity) -> str:
        """China's page: its seat to 25 October 1971, the Republic of China's, in a section of its own, with
        that period's statements and recorded votes, labelled as everywhere on the site (decision 5)."""
        period = PERIODS.get(x.code)
        if period is None:
            return ""
        found = self.latest_statements(x.code, before=period.until, most=PERIOD_STATEMENTS)
        cast = [(v, how) for v, how in self.cast(x.code) if v.on < period.until]
        parts = [f'<section class="country-period" aria-labelledby="period-head">'
                 f'<h2 id="period-head">{e(period.heading)}</h2>', f"<p>{e(period.note)}</p>",
                 '<h3 id="period-statements">Statements</h3>',
                 self._statement_list(x, found) if found else "<p>No statements in the record.</p>",
                 '<h3 id="period-votes">Recorded votes in the General Assembly</h3>']
        if cast:
            latest = cast[:LATEST_VOTES]
            parts.append(f"<p>{e(its_latest(len(latest), 'last'))} to {e(long_date(ROC_LAST_DAY))}, newest "
                         f"first. It is listed in {_plural(len(cast), 'recorded vote', 'recorded votes')} from "
                         f"{e(long_date(cast[-1][0].on))} until then.</p>"
                         '<ul class="vote-index">'
                         + "".join(self._cast_line(v, how, self._recorded_as(x, v.on)) for v, how in latest) + "</ul>"
                         f'<p class="record-more"><a href="{x.votes_path}">Every recorded vote of '
                         f"{e(in_a_sentence(x.name))}, by session</a>, with those to "
                         f"{e(long_date(ROC_LAST_DAY))} marked.</p>")
        else:
            parts.append("<p>No recorded votes in the record.</p>")
        return "".join(parts) + "</section>"

    def _votes(self, x: Entity) -> str:
        head = '<h2 id="votes">Recorded votes in the General Assembly</h2>'
        if x.kind == "observer_state":
            return head + "<p>As a non-member observer State, it does not vote in the General Assembly.</p>"
        if x.kind == "other_state":
            return head + ("<p>Kosovo has no vote in the General Assembly: it is not a member of the United "
                           "Nations.</p>")
        cast = self.cast(x.code)
        end = x.last_day or self.today()
        cast = [(v, how) for v, how in cast if v.on <= end]
        if not cast:
            return head + "<p>No recorded votes in the record.</p>"
        start = years_before(end, YEARS_COUNTED)
        intro = ("<p>How it voted in the General Assembly’s recorded votes, from the UN’s voting data: counts and "
                 "the list of votes only. "
                 + ("Over its last 10 years as a Member State:" if x.last_day else "Over the last 10 years:")
                 + "</p>")
        period = PERIODS.get(x.code)
        latest = [(v, how) for v, how in cast if period is None or v.on >= period.until][:LATEST_VOTES]
        if period:
            intro += f"<p>{e(period.main_note)}</p>"
        which = "last" if x.last_day else "most recent"
        return (head + intro + self._counts_table(cast, start, end)
                + f'<h3 id="latest-votes">{e(its_latest(len(latest), which))}</h3>'
                + '<ul class="vote-index">'
                + "".join(self._cast_line(v, how, self._recorded_as(x, v.on)) for v, how in latest) + "</ul>"
                + f'<p class="record-more"><a href="{x.votes_path}">Every recorded vote of {e(in_a_sentence(x.name))}, '
                f"by session</a>: {_plural(len(cast), 'vote', 'votes')}, from {e(long_date(cast[-1][0].on))}.</p>")

    # The Security Council's votes (decision 30)

    def council_cast(self, x: Entity) -> list[tuple[CouncilVote, str]]:
        end = x.last_day or self.today()
        return [(v, how) for v, how in self.council.cast(x.code) if v.on <= end]

    def sat(self, x: Entity) -> bool:
        """Whether it has sat on the Council (by the voting data), or defeated a draft as a permanent member."""
        return bool(self.council.seat_years(x.code) or self.council_cast(x) or self.council.vetoed(x.code))

    @staticmethod
    def _council_line(v: CouncilVote, how: str, result: str, recorded_as: str | None = None) -> str:
        as_name = f" {e(recorded_as)}." if recorded_as else ""
        return (f'<li><a href="{v.path}">{e(shorten(v.heading, VOTE_TITLE_MAX + 12))}</a>'
                f'<span class="vote-index__meta">{e(long_date(v.on))}. Its vote: {COUNCIL_LABEL[how]}.{as_name} '
                f"Result: {e(result)}.</span></li>")

    def _council(self, x: Entity) -> str:
        if not x.votes or not self.sat(x):
            return ""
        head = '<h2 id="council-votes">Votes in the Security Council</h2>'
        period = PERIODS.get(x.code)
        cast = self.council_cast(x)
        if period:
            cast = [(v, how) for v, how in cast if v.on >= period.until]
        years = self.council.seat_years(x.code)
        permanent = "p5" in self.countries.groups_on(x.code, x.last_day or self.today())
        if permanent:
            first = x.country.valid_from or (date(1991, 12, 24) if x.code == "RUS" else None)
            terms_words = "A permanent member of the Council" + (
                f", {first.year} to {x.last_day.year}" if x.last_day and first else
                (" since 24 December 1991, when it continued the USSR’s seat" if x.code == "RUS" else ""))
        elif years:
            terms_words = f"Its terms on the Council, as its voting data shows them: {and_list(terms(years))}"
        else:
            terms_words = ""
        parts = [head, "<p>How it voted in the Security Council’s votes: the resolutions adopted, from the UN’s voting "
                       "data, and the drafts not adopted, from the Council’s meeting records. Counts and the list of "
                       "votes only.</p>"]
        if terms_words:
            parts.append(f"<p>{e(terms_words)}.</p>")
        if period:
            parts.append(f"<p>{e(period.main_note)}</p>")
        if cast:
            last_years = sorted({v.on.year for v, _ in cast})[-YEARS_COUNTED:]
            counts = {k: 0 for k in VOTES_IN_ORDER}
            for v, how in cast:
                if v.on.year in last_years:
                    counts[how] += 1
            rows = "".join(f'<tr><th scope="row">{COUNCIL_LABEL[k]}</th><td>{counts[k]:,}</td></tr>'
                           for k in VOTES_IN_ORDER if counts[k] or k != "X")
            span = and_list(terms(last_years))
            parts.append('<table class="vote-totals country-totals"><caption>Its votes in its last '
                         f"{_plural(len(last_years), 'year', 'years')} on the Council ({e(span)})</caption>"
                         '<thead><tr><th scope="col">Vote</th><th scope="col">Votes</th></tr></thead>'
                         f"<tbody>{rows}</tbody></table>")
            latest = cast[:LATEST_VOTES]
            which = "last" if x.last_day else "most recent"
            parts.append(f'<h3 id="latest-council-votes">{e(its_latest(len(latest), which).replace("recorded vote", "Council vote"))}</h3>'
                         '<ul class="vote-index">' + "".join(
                             self._council_line(v, how, self.council.result_line(v), self._recorded_as(x, v.on))
                             for v, how in latest) + "</ul>")
        else:
            parts.append("<p>The site holds no vote of its on the Council since the voting data begins.</p>")
        if permanent:
            vetoed = [v for v in self.council.vetoed(x.code) if not period or v.on >= period.until]
            title = "Drafts not adopted owing to its negative vote"
            intro = ("<p>Drafts that were not adopted, owing to the negative vote of a permanent member of the Council "
                     "(commonly called a veto), where it voted against, newest first. Those from before the site’s "
                     "Council records begin are as the UN Dag Hammarskjöld Library’s list of vetoes gives them.</p>")
        else:
            vetoed = [v for v, how in cast if not v.adopted and how == "N"]
            title = "Drafts not adopted that it voted against"
            intro = "<p>Drafts the Council did not adopt where it voted against, newest first.</p>"
        if vetoed:
            parts.append(f'<h3 id="council-not-adopted">{title}</h3>{intro}<ul class="vote-index">' + "".join(
                f'<li><a href="{v.path}">{e(v.heading)}</a><span class="vote-index__meta">{e(long_date(v.on))}. '
                f"{e(v.agenda)}{'.' if v.agenda else ''} {e(self.council.result_line(v))}.</span></li>"
                for v in vetoed) + "</ul>")
        if cast:
            parts.append(f'<p class="record-more"><a href="{x.council_path}">Every Security Council vote of '
                         f"{e(in_a_sentence(x.name))}, by year</a>: {_plural(len(self.council_cast(x)), 'vote', 'votes')}"
                         f", from {e(long_date(self.council_cast(x)[-1][0].on))}.</p>")
        parts.append('<p class="vote-source">All the Council’s votes: <a href="/security-council-votes.html">Votes in '
                     "the Security Council</a>.</p>")
        return "".join(parts)

    def council_page(self, slug: str) -> str | None:
        """Every Security Council vote it is listed in, by year, newest first."""
        x = self.entity(slug)
        if x is None or not x.votes:
            return None
        cast = self.council_cast(x)
        if not cast:
            return None
        name = f"{x.name}: votes in the Security Council"
        trail = [(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH), (x.name, x.path), (name, x.council_path)]
        by_year: dict[int, list[tuple[CouncilVote, str]]] = {}
        for v, how in cast:
            by_year.setdefault(v.on.year, []).append((v, how))
        first, last = cast[-1][0].on, cast[0][0].on
        parts = [f'<p class="crumbs"><a href="{INDEX_PATH}">{INDEX_NAME}</a>: <a href="{x.path}">{e(x.name)}</a></p>',
                 f"<h1>{e(name)}</h1>",
                 f'<p class="lede">Every Security Council vote that lists {e(in_a_sentence(x.name))}: '
                 f"{_plural(len(cast), 'vote', 'votes')}, from {e(long_date(first))} to {e(long_date(last))}, by "
                 "year, newest first. The resolutions adopted are from the UN’s voting data, and the drafts not "
                 "adopted from the Council’s meeting records.</p>",
                 "<p>Each vote’s page gives how every member voted on it.</p>",
                 *([f"<p>Votes to {e(long_date(ROC_LAST_DAY))} are the Republic of China’s, which held China’s seat "
                    f"until then, and are marked “Recorded as China (Republic of China), to "
                    f"{e(long_date(ROC_LAST_DAY))}”.</p>"] if x.code in PERIODS else []),
                 '<nav class="country-jump" aria-label="Years"><ul class="session-index">'
                 + "".join(f'<li><a href="#year-{y}">{y}</a>, <span class="session-index__meta">'
                           f"{_plural(len(rows), 'vote', 'votes')}</span></li>" for y, rows in by_year.items())
                 + "</ul></nav>"]
        for y, rows in by_year.items():
            parts.append(f'<h2 id="year-{y}">{y}</h2><ul class="vote-index">' + "".join(
                f'<li><a href="{v.path}">{e(shorten(v.heading, LIST_TITLE_MAX + 12))}</a> <span class="vote-index__meta">'
                f"{e(long_date(v.on))}: {COUNCIL_LABEL[how]}."
                + (f" {e(r)}." if (r := self._recorded_as(x, v.on)) else "") + "</span></li>" for v, how in rows)
                + "</ul>")
        parts.append(self.council._source())
        description = shorten(f"How {in_a_sentence(x.name)} voted in each of the Security Council’s {len(cast):,} "
                              "votes that list it, by year, from the UN’s voting data and meeting records.",
                              DESCRIPTION_MAX)
        return self.page(title=f"{name} | {SITE_NAME}", description=description, path=x.council_path, trail=trail,
                         main="".join(parts))

    def _topics(self, x: Entity) -> str:
        if self.topics is None:
            return ""
        rows = []
        for t, cur in self.topics.listing():
            if cur is None:
                continue
            answer, sources, _, _ = final_and_sources(cur.briefing.events)
            cited = {n for seg in answer.get("segments") or [] if seg.get("status") == "cited"
                     for n in seg.get("sources") or []}
            if any(s.get("n") in cited and s.get("country_code") == x.code and not s.get("on_behalf_of")
                   for s in sources):
                rows.append(f'<li><a href="{t.path}">{e(t.title)}</a> <span class="topic-list__when">Briefing of '
                            f"{e(long_date(cur.prepared_at))}</span></li>")
        if not rows:
            return ""
        return ('<h2 id="topics">Topic briefings that cite it</h2>'
                "<p>The topic briefings whose current version cites a statement of its own.</p>"
                f'<ul class="topic-list">{"".join(rows)}</ul>')

    def _published(self, x: Entity) -> str:
        found = self.statements(x.code)
        if not found:
            return ""
        out = []
        for s in found:
            page = str(s.get("page") or "")
            what = "the vote’s page" if page.startswith("/votes/") else "the briefing"
            out.append(statements_html([s], self.not_endorsed).replace(
                '<p class="statement__note">',
                f'<p class="statement__link">Published beside <a href="{e(page)}">{what}</a>.</p>'
                '<p class="statement__note">', 1))
        return ('<h2 id="right-of-reply">Statements in their own words</h2>'
                f"<p>Statements published under the right of reply, speaking for {e(in_a_sentence(x.name))}, beside "
                "a briefing or a vote’s page on this site.</p>" + "".join(out))

    def _sister(self, x: Entity) -> str:
        if not x.sister:
            return ""
        return ('<nav class="sisters" aria-label="Sister sites"><ul>'
                f'<li><a href="{LIVES}{quote(x.code.upper())}">{self._lives_spine()}This country on Where Lives '
                "Stand</a></li></ul></nav>")

    @staticmethod
    def _named(path: str) -> str:
        page = quote(path, safe="/")
        return ('<aside class="vote-side panel" aria-labelledby="named-head">'
                '<h2 id="named-head">If you’re named</h2>'
                "<p>A country’s delegation, permanent mission or government can tell us that something on this page "
                "is wrong or unfair. We reply within 5 working days, and decide within 20 working days.</p>"
                '<ul class="plain-list">'
                '<li><a href="/named.html">If you’re named on this site</a></li>'
                f'<li><a href="/corrections.html?kind=error&amp;page={page}#report">Tell us what is wrong</a></li>'
                "</ul></aside>")

    def description(self, x: Entity) -> str:
        if x.votes:
            what = "its groups, its latest statements and its recorded votes in the General Assembly"
        else:
            what = "its groups and its latest statements"
        return shorten(f"{x.name} at the United Nations: {what}, from the UN’s records.", DESCRIPTION_MAX)

    def country_page(self, slug: str) -> str | None:
        x = self.entity(slug)
        if x is None:
            return None
        on = x.last_day or self.today()
        trail = [(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH), (x.name, x.path)]
        status = [f'<p class="lede">{e(self.status_line(x))}</p>']
        c = x.country
        if x.kind == "former_member_state":
            if c.note:
                status.append(f"<p>{e(curl_quotes(c.note))}</p>")
            heirs = [self.entities[s] for s in c.successors if s in self.entities]
            if heirs:
                status.append("<p>Successor: " + and_list([f'<a href="{h.path}">{e(h.name)}</a>' for h in heirs])
                              + ".</p>")
        elif x.kind == "other_state":
            status.append("<p>It has a page here because it speaks in the Security Council’s record, under the name "
                          "the record gives it.</p>")
        if x.code in PERIODS:
            status.append(f'<p>Its record to {long_date(ROC_LAST_DAY)} is that of the Republic of China, whose '
                          'government has been based in Taiwan since 1949, in <a href="#period-head">a section of its '
                          'own</a>.</p>')
        elif x.code == "DEU":
            status.append("<p>Its record before 3 October 1990 is that of the Federal Republic of Germany, and is "
                          "labelled so.</p>")
        rows = self.groups_rows(x, on)
        when = f"on its last day as a Member State, {long_date(on)}" if x.last_day else f"on {long_date(on)}"
        groups = (f'<h2 id="groups">Groups {e(when)}</h2>'
                  + ('<dl class="facts vote-facts">' + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in rows)
                     + "</dl>" if rows else "<p>The record holds no groups for it on this day.</p>")
                  + '<p class="vote-source">Groups as the site counts them on each date '
                  '(<a href="/methodology.html#record">the record</a>, on the methodology page).</p>')
        main = "".join([
            f'<p class="crumbs"><a href="{INDEX_PATH}">{INDEX_NAME}</a></p>',
            f"<h1>{e(x.name)}</h1>",
            *status,
            '<div class="vote-layout"><div class="vote-main">',
            groups,
            self._form(x),
            self._statements(x),
            self._votes(x),
            self._council(x),
            self._period(x),
            self._topics(x),
            self._published(x),
            self._sister(x),
            '<p class="vote-source">Written from the UN’s records and its voting data, with no AI. The UN’s names '
            "and status are used throughout.</p>",
            "</div>" + self._named(x.path) + "</div>",
        ])
        return self.page(title=x.page_title, description=self.description(x), path=x.path, trail=trail, main=main)

    def votes_page(self, slug: str) -> str | None:
        """Every recorded vote it is listed in, by session, newest first."""
        x = self.entity(slug)
        if x is None or not x.votes:
            return None
        end = x.last_day or self.today()
        cast = [(v, how) for v, how in self.cast(x.code) if v.on <= end]
        if not cast:
            return None
        name = f"{x.name}: recorded votes in the General Assembly"
        trail = [(SITE_NAME, "/"), (INDEX_NAME, INDEX_PATH), (x.name, x.path), (name, x.votes_path)]
        sessions: dict[str, list[tuple[Vote, str]]] = {}
        labels: dict[str, str] = {}
        for v, how in cast:
            sessions.setdefault(v.session.key, []).append((v, how))
            labels[v.session.key] = v.session.label[0].upper() + v.session.label[1:]
        first, last = cast[-1][0].on, cast[0][0].on
        parts = [f'<p class="crumbs"><a href="{INDEX_PATH}">{INDEX_NAME}</a>: <a href="{x.path}">{e(x.name)}</a></p>',
                 f"<h1>{e(name)}</h1>",
                 f'<p class="lede">Every recorded vote of the General Assembly that lists {e(in_a_sentence(x.name))}, '
                 f"from the UN’s voting data: {_plural(len(cast), 'vote', 'votes')}, from {e(long_date(first))} to "
                 f"{e(long_date(last))}, by session, newest first.</p>",
                 "<p>Each vote’s page gives how every Member State voted on it.</p>",
                 *([f"<p>Votes to {e(long_date(ROC_LAST_DAY))} are the Republic of China’s, which held "
                    "China’s seat until then, and are marked “Recorded as China (Republic of China), to "
                    f"{e(long_date(ROC_LAST_DAY))}”.</p>"] if x.code in PERIODS else []),
                 '<nav class="country-jump" aria-label="Sessions"><ul class="session-index">'
                 + "".join(f'<li><a href="#session-{key}">{e(labels[key])}</a>, '
                           f'<span class="session-index__meta">{_plural(len(rows), "vote", "votes")}</span></li>'
                           for key, rows in sessions.items()) + "</ul></nav>"]
        for key, rows in sessions.items():
            parts.append(f'<h2 id="session-{key}">{e(labels[key])}</h2><ul class="vote-index">'
                         + "".join(self._list_line(v, how, self._recorded_as(x, v.on)) for v, how in rows) + "</ul>")
        parts.append('<p class="vote-source">From the <a href="' + e(self.votes.record) + f'"{OPENED}>General Assembly '
                     "voting data</a> of the UN Dag Hammarskjöld Library, © United Nations, used for non-commercial "
                     "purposes with attribution.</p>")
        description = shorten(f"How {in_a_sentence(x.name)} voted in each of the General Assembly’s "
                              f"{len(cast):,} recorded votes that list it, by session, from the UN’s voting data.",
                              DESCRIPTION_MAX)
        return self.page(title=f"{name} | {SITE_NAME}", description=description, path=x.votes_path, trail=trail,
                         main="".join(parts))

    # Addresses

    def moved(self, rest: str) -> str | None:
        """Where another form of a country's address is sent for good: any case, no ``.html``, a trailing slash."""
        asked = re.sub(r"(?:\.html)?/*$", "", rest.strip().strip("/"), flags=re.I).lower()
        if not asked:
            return INDEX_PATH
        m = re.fullmatch(r"([a-z]{3})(?:/(votes|security-council-votes)(?:\.html)?)?", asked)
        if m is None:
            return None
        x = self.entity(m[1])
        if x is None:
            return None
        if m[2] == "security-council-votes":
            return x.council_path if x.votes and self.council_cast(x) else None
        if m[2] == "votes":
            return x.votes_path if x.votes and self.vote_count(x.code)[0] else None
        return x.path

    def sitemap(self) -> list[tuple[str, str]]:
        """The index, then each country's page dated by its newest statement or vote in the record, and each list of
        votes by its newest vote: dates from the record, never made up."""
        _, summary = self._records()
        out: list[tuple[str, str]] = []
        rows: list[tuple[str, str]] = []
        for x in sorted(self.entities.values(), key=lambda x: x.code):
            n, newest_vote = self.vote_count(x.code) if x.votes else (0, "")
            newest = summary.get(x.code, {}).get("newest")
            when = max(newest_vote, newest.isoformat() if newest else "")
            council = self.council_cast(x) if x.votes else []
            if council:
                when = max(when, council[0][0].on.isoformat())
            rows.append((x.path, when))
            if n:
                rows.append((x.votes_path, newest_vote))
            if council:
                rows.append((x.council_path, council[0][0].on.isoformat()))
        out.append((INDEX_PATH, max((when for _, when in rows if when), default="")))
        return out + rows
