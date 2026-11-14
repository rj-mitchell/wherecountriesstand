"""Readers' reports and the right of reply (the charity's impartiality policy, section 7; decision 16), with this
site's own addresses, limits and words.

A country's delegation or permanent mission, its government, a person named in the UN's records, or someone acting
for them, can add a short statement in their own words beside a saved briefing (``/b/<id>``: the site has no
country pages yet). A statement can be given the country it speaks for (``wcs statements publish 3 --checked "…"
--country KEN``), so a country's page can gather its statements once there is one
(``GET /api/statements?country=KEN``).

The rules and the data are the Where They Stand sites' shared ones (wts-core, ``wts_core.replies``; its
docs/adr/0008): what a statement may be, its life (received, then published or declined; a published one withdrawn
at its author's request or removed by the site for breaking the rules), the log of every step, the confirming
address deleted three months after a statement ends, reports and their closing, the corrections log, and hiding an
item while a concern about it is decided. They are kept in the state database's ``wts_reports``,
``wts_statements`` and the tables beside them. What is here is what is this site's own: where a statement may sit,
its limits where the sites differ, and every word a sender is shown.

A statement is shown beside its briefing, headed "Statement from [name], [date]" and followed by "In their own
words. Not checked or endorsed by Where Countries Stand." It is never part of the record: nothing in the search
index, the briefing engine or the AI model's instructions reads it (``wcs.rag``, ``wcs.engine``, ``wcs.index``), it
is not in a briefing's saved or cached events, and tests/test_statements.py checks that it never reaches the model.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from wts_core import replies
from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.replies import Item, Refused, Replies, ReplyMessages, ReplyRules
from wts_core.state import StateDB
from wts_core.usage import SavedBriefings

from wcs.config import SITE

MAX_WORDS = 150
# the longest a 150-word statement could reasonably be, so a wall of text without spaces is refused too
MAX_CHARS = 1500
NOT_ENDORSED = f"In their own words. Not checked or endorsed by {SITE.name}."
# A saved briefing's address, as app.shared_briefing accepts it.
_BRIEFING = re.compile(r"^/b/([A-Za-z0-9_-]{6,20})$")
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
    # For each visitor, each day (wts-core docs/MIGRATION.md 1.9). Not in force: the forms pass no visitor's code,
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
    item_not_shown="There is no briefing at that address. Check the address of the briefing the statement is about.",
    daily_statements="You have sent several statements today. Please send any more tomorrow, or email us.",
    daily_reports="You have sent several reports today. Please send any more tomorrow, or email us.",
    report_kind="Choose what the report is about.",
    report_too_short="Say what is wrong, in a few words at least.",
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
        return BriefingItems.address(address or "").removeprefix("/b/")
    except Refused:
        return None


class BriefingItems:
    """Where a statement may sit on this site (``replies.Items``): beside a saved briefing, at ``/b/<id>``."""

    def __init__(self, briefings: SavedBriefings) -> None:
        self._briefings = briefings

    @staticmethod
    def address(pasted: str, hosts: tuple[str, ...] = SITE.hosts) -> str:
        """The briefing a statement is about, as its path on this site (``/b/<id>``). Accepts a full address or a
        path; raises ``Refused``, with words for the reader, otherwise. ``hosts`` are the names the site answers
        to (a route adds the address the request itself came to)."""
        a = (pasted or "").strip()
        if not a:
            raise Refused("Give the address of the briefing the statement is about.", "bad_item")
        parts = urlsplit(a if "://" in a or a.startswith("/") else "https://" + a)
        if parts.scheme not in ("http", "https", "") or (parts.hostname and parts.hostname.lower() not in hosts):
            raise Refused("The address must be a briefing on wherecountriesstand.com.", "bad_item")
        m = _BRIEFING.match(unquote(parts.path).rstrip("/"))
        if not m:
            raise Refused("The address must be a briefing on this site, such as "
                          "https://wherecountriesstand.com/b/…", "bad_item")
        return f"/b/{m[1]}"

    def resolve(self, pasted: str) -> Item:
        return Item(address=self.address(pasted))

    def shown(self, item: Item) -> bool:
        """Whether the briefing is there and has not been withdrawn."""
        saved = self._briefings.get(item.address.removeprefix("/b/"))
        return saved is not None and saved.withdrawn_at is None


def open_replies(db: StateDB, briefings: SavedBriefings, clock: Clock = SYSTEM_CLOCK) -> Replies:
    """The site's reports and statements, over its state database and its saved briefings."""
    return Replies(db, items=BriefingItems(briefings), rules=REPLY_RULES, messages=REPLY_MESSAGES, clock=clock)


def as_shown(statement: dict) -> dict:
    """A published statement under the names this site's pages have always read: what the public sees of it, and
    nothing else (never its author's email address, the reason it ended or the owner's log)."""
    return {"id": statement["id"], "page": statement["item"], "country": statement["about"],
            "name": statement["name"], "role": statement["role"], "text": statement["text"],
            "link": statement["link"] or "", "shown_on": statement["date"]}
