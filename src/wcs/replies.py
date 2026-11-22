"""Readers' reports and the right of reply (the charity's impartiality policy, section 7; decision 16), with this
site's own addresses, limits and words.

A country's delegation or permanent mission, its government, a person named in the UN's records, or someone acting
for them, can add a short statement in their own words beside a saved briefing (``/b/<id>``) or a General Assembly
vote's page (``/votes/<symbol>.html``, ``wcs.api.votes``; decision 26). The site has no country pages yet. A statement can be given the country it speaks for (``wcs statements publish 3 --checked "…"
--country KEN``), so a country's page can gather its statements once there is one
(``GET /api/statements?country=KEN``).

The rules and the data are the Where They Stand sites' shared ones (wts-core, ``wts_core.replies``; its
docs/adr/0008): what a statement may be, its life (received, then published or declined; a published one withdrawn
at its author's request or removed by the site for breaking the rules), the log of every step, the confirming
address deleted three months after a statement ends, reports and their closing, the corrections log, and hiding an
item while a concern about it is decided. They are kept in the state database's ``wts_reports``,
``wts_statements`` and the tables beside them. What is here is what is this site's own: where a statement may sit,
its limits where the sites differ, and every word a sender is shown.

A statement is shown beside its briefing or vote, headed "Statement from [name], [date]" and followed by "In their own
words. Not checked or endorsed by Where Countries Stand." It is never part of the record: nothing in the search
index, the briefing engine or the AI model's instructions reads it (``wcs.rag``, ``wcs.engine``, ``wcs.index``), it
is not in a briefing's saved or cached events, and tests/test_statements.py checks that it never reaches the model.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import unquote, urlsplit

from wts_core import replies
from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.replies import Item, NotDone, Refused, Replies, ReplyMessages, ReplyRules
from wts_core.state import StateDB
from wts_core.usage import SavedBriefings

from wcs.config import SITE

MAX_WORDS = 150
# the longest a 150-word statement could reasonably be, so a wall of text without spaces is refused too
MAX_CHARS = 1500
NOT_ENDORSED = f"In their own words. Not checked or endorsed by {SITE.name}."
# A saved briefing's address, as app.shared_briefing accepts it, and a vote page's (its symbol in lower case, with
# hyphens: wcs.api.votes.slug_of), with or without ".html". A session's list of votes is not an item.
_BRIEFING = re.compile(r"^/b/([A-Za-z0-9_-]{6,20})$")
_VOTE_PAGE = re.compile(r"^/votes/((?!session-)[a-z0-9]+(?:-[a-z0-9]+)*)(?:\.html)?$")
VOTE_PAGES = "/votes/"
_COUNTRY = re.compile(r"^[A-Z]{3}$")
# A record of the archive (a speech), named by the id on its source card: what `wcs hide` takes to keep one out of
# searches and of new briefings' sources while a concern about it is decided.
RECORD = "record:"

REPLY_RULES = ReplyRules(
    min_words=1,
    max_words=MAX_WORDS,
    max_chars=MAX_CHARS,
    name_max=200,
    role_max=200,
    link_max=500,
    email_max=200,
    # For each visitor, each day. Not in force: the forms pass no visitor's code,
    # so nothing new is kept about who sent what. The per-minute limit applies to both forms as it always has.
    daily_statements=5,
    daily_reports=10,
    about_required=False,  # a statement sits beside a briefing; the country it speaks for may be added later
    # The words the form or a link may send, each kept as it is. The first two are the family's routes (the
    # impartiality policy, section 7.1): "Something is wrong" and "You've been characterised unfairly". "problem" is
    # what the form sent before the routes were named, and is still taken from links and pages kept from then;
    # "takedown" is a request to remove something about a person, listed first for the owner.
    report_kinds={"error": "error", "unfair": "unfair", "problem": "problem", "takedown": replies.TAKEDOWN,
                  "other": "other"},
    report_kind_required=True,
    report_min=5,
    report_max=4000,
    page_max=300,
    contact_max=200,
)

REPLY_MESSAGES = ReplyMessages(
    too_few_words="Write the statement.",
    too_many_words="A statement can be up to {max} words; this one is {n}.",
    too_many_chars="A statement can be up to {words} words ({max:,} characters).",
    name_missing="Give your name, as it should be shown.",
    name_too_long="Your name can be up to {max} characters.",
    role_missing="Give your role, such as Permanent Representative of Kenya to the United Nations.",
    role_too_long="Your role can be up to {max} characters.",
    bad_link="The link to the record must be a web address starting https:// (or leave it empty).",
    link_too_long="The link to the record can be up to {max} characters.",
    bad_email="Give an email address we can use to confirm who you are.",
    item_not_shown="There is no briefing or vote page at that address. Check the address of the page the statement "
                   "is about.",
    daily_statements="You have sent several statements today. Please send any more tomorrow, or email us.",
    daily_reports="You have sent several reports today. Please send any more tomorrow, or email us.",
    # the family's words for these two, the same on every Where They Stand site
    report_kind="Please choose what the report is about.",
    report_too_short="Please say what is wrong, in a few words.",
    report_too_long="A report can be up to {max:,} characters; this one is {n:,}.",
    contact_too_long="The contact address can be up to {max} characters.",
    lapsed="No decision was made in time; please send it again.",
    # the family glossary's heading and disclaimer
    heading="Statement from {name}, {date}",
    disclaimer="In their own words. Not checked or endorsed by {site}.",
    record_link="The record it points to",
    site=SITE.name,
)


def check_country(code: str | None) -> str | None:
    """An ISO 3166 alpha-3 code, as the site uses for countries, or None."""
    if not code:
        return None
    c = code.strip().upper()
    if not _COUNTRY.match(c):
        raise ValueError("A country is given by its three-letter code, such as KEN.")
    return c


def briefing_id(address: str | None) -> str | None:
    """The id of the saved briefing at an address of this site (a path or a full address), or None if the address
    is not a briefing's. For the record of each briefing: which one a report is about."""
    try:
        found = SiteItems.address(address or "")
    except Refused:
        return None
    return found.removeprefix("/b/") if found.startswith("/b/") else None


def vote_slug(address: str) -> str | None:
    """The vote a vote page's address (``/votes/<slug>.html``, as ``SiteItems.address`` gives it) is for."""
    return address.removeprefix(VOTE_PAGES).removesuffix(".html") if address.startswith(VOTE_PAGES) else None


class SiteItems:
    """Where a statement may sit on this site (``replies.Items``): beside a saved briefing, at ``/b/<id>``, or a
    General Assembly vote's page, at ``/votes/<symbol>.html`` (decision 26). ``vote_exists`` says whether a vote has
    a page; without it no vote page is an item that is there."""

    def __init__(self, briefings: SavedBriefings, vote_exists: Callable[[str], bool] | None = None) -> None:
        self._briefings = briefings
        self._vote_exists = vote_exists or (lambda _slug: False)
        # the items hidden now (``Replies.all_hidden``, given by ``open_replies`` once there is a ``Replies``)
        self.hidden: Callable[[], list[dict[str, Any]]] = list

    @staticmethod
    def address(pasted: str, hosts: tuple[str, ...] = SITE.hosts) -> str:
        """The briefing or vote a statement is about, as its path on this site (``/b/<id>``, or
        ``/votes/<symbol>.html``). Accepts a full address or a path; raises ``Refused``, with words for the reader,
        otherwise. ``hosts`` are the names the site answers to (a route adds the address the request itself came
        to)."""
        a = (pasted or "").strip()
        if not a:
            raise Refused("Give the address of the briefing or vote page the statement is about.", "bad_item")
        parts = urlsplit(a if "://" in a or a.startswith("/") else "https://" + a)
        if parts.scheme not in ("http", "https", "") or (parts.hostname and parts.hostname.lower() not in hosts):
            raise Refused("The address must be a briefing or a vote page on wherecountriesstand.com.", "bad_item")
        path = unquote(parts.path).rstrip("/")
        m = _BRIEFING.match(path)
        if m:
            return f"/b/{m[1]}"
        m = _VOTE_PAGE.match(path.lower())
        if m:
            return f"{VOTE_PAGES}{m[1]}.html"
        raise Refused("The address must be a briefing or a vote page on this site, such as "
                      "https://wherecountriesstand.com/b/… or https://wherecountriesstand.com/votes/…", "bad_item")

    def resolve(self, pasted: str) -> Item:
        return Item(address=self.address(pasted))

    def there(self, item: Item) -> bool:
        """Whether the briefing is there and has not been withdrawn, or the vote has a page."""
        slug = vote_slug(item.address)
        if slug is not None:
            return self._vote_exists(slug)
        saved = self._briefings.get(item.address.removeprefix("/b/"))
        return saved is not None and saved.withdrawn_at is None

    def shown(self, item: Item) -> bool:
        """Whether the briefing is there, has not been withdrawn, and is not hidden with a record among its
        sources (``note_in_place``): no statement is taken or shown beside a briefing its address does not show.
        A vote's page is shown while the vote is in the record. (An item hidden by its own address is the
        package's to know.)"""
        slug = vote_slug(item.address)
        if slug is not None:
            return self._vote_exists(slug)
        saved = self._briefings.get(item.address.removeprefix("/b/"))
        if saved is None or saved.withdrawn_at is not None:
            return False
        return note_in_place(None, saved.events, self.hidden()) is None


def hidden_records(hidden: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Of the items hidden now, the records of the archive: each record's id and the note shown in its place."""
    return {h["item"].removeprefix(RECORD): h["note"] for h in hidden if h["item"].startswith(RECORD)}


def note_in_place(briefing_id: str | None, events: Iterable[Mapping[str, Any]],
                  hidden: Iterable[Mapping[str, Any]]) -> str | None:
    """The note to show in place of a saved or cached briefing, or None when it can be shown: the briefing is
    hidden itself, or one of its sources is a record that is. A briefing written from a record carries the
    record's words (its source card quotes them, and the briefing's text was written from them), so while the
    record is hidden every briefing that had it among its sources is hidden with it, under the record's note."""
    hidden = list(hidden)
    for h in hidden:
        if briefing_id and h["item"] == f"/b/{briefing_id}":
            return str(h["note"])
    records = hidden_records(hidden)
    if not records:
        return None
    for event in events:
        if event.get("type") == "sources":
            for card in event.get("sources") or []:
                if card.get("record_id") in records:
                    return records[card["record_id"]]
    return None


def hideable(items: SiteItems, record_exists: Callable[[str], bool]) -> Callable[[str], str]:
    """This site's one spelling for each thing that can be hidden while a concern about it is decided, whatever
    the owner typed (``wts_core.replies.Replies(hideable=…)``): a saved briefing, filed as ``/b/<id>`` (its full
    address, with or without a trailing slash, will do); a vote's page, filed as ``/votes/<symbol>.html``; or a record of the archive, filed as ``record:<id>``,
    the id being the ``record_id`` on its source card. Anything else, and a briefing or record that is not there,
    is refused in words for the owner: a command that takes an address must not say "hidden" for nothing."""

    def filed(typed: str) -> str:
        typed = (typed or "").strip()
        if typed.lower().startswith(RECORD):
            record_id = typed[len(RECORD):].strip()
            if not record_id or not record_exists(record_id):
                raise NotDone(f"there is no record {record_id!r} in the archive: give record: and then the "
                              "record_id on its source card, such as record:sc:S/PV.9000:0005")
            return f"{RECORD}{record_id}"
        try:
            item = items.resolve(typed)
        except Refused:
            raise NotDone("only a saved briefing, a vote's page or a record of the archive can be hidden: give a "
                          "briefing's address (/b/ and then its id), a vote page's (/votes/ and then its name), or "
                          "record: and then the record_id on a source card") from None
        if not items.there(item):
            if vote_slug(item.address) is not None:
                raise NotDone(f"there is no vote's page at {item.address}")
            raise NotDone(f"there is no briefing at {item.address}, or it has been withdrawn")
        return item.address

    return filed


def open_replies(db: StateDB, briefings: SavedBriefings, clock: Clock = SYSTEM_CLOCK, *,
                 record_exists: Callable[[str], bool], vote_exists: Callable[[str], bool] | None = None) -> Replies:
    """The site's reports and statements, over its state database and its saved briefings. ``record_exists`` says
    whether the archive holds a record of an id: a record can be hidden only if it is there. ``vote_exists`` says
    whether a vote has a page (``VotePages.exists``): a statement can sit beside it, and it can be hidden."""
    items = SiteItems(briefings, vote_exists)

    def hidden_with(item: Item) -> bool:
        """Whether a statement beside this briefing is hidden with it though the briefing's own address is not
        hidden (``Replies(hidden_with=…)``, wts-core 0.3.0): while a record of the archive is hidden, a briefing
        that has it among its sources is hidden with it, and its statements with the briefing. Exactly what the
        statements route's own filter asked until the package took it over: nothing while no record is hidden,
        and then whether the briefing is shown at its address (``SiteItems.shown``)."""
        return bool(hidden_records(items.hidden())) and not items.shown(item)

    desk = Replies(db, items=items, rules=REPLY_RULES, messages=REPLY_MESSAGES,
                   hideable=hideable(items, record_exists), hidden_with=hidden_with, clock=clock)
    items.hidden = desk.all_hidden
    return desk


def as_shown(statement: dict) -> dict:
    """A published statement under the names this site's pages have always read: what the public sees of it, and
    nothing else (never its author's email address, the reason it ended or the owner's log)."""
    return {"id": statement["id"], "page": statement["item"], "country": statement["about"],
            "name": statement["name"], "role": statement["role"], "text": statement["text"],
            "link": statement["link"] or "", "shown_on": statement["date"]}
