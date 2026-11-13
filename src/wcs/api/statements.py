"""Statements under the right of reply (the charity's impartiality policy, section 7.2; decision 16): a country's
delegation or permanent mission, its government, a person named in the UN's records, or someone acting for them, adds
a short statement in their own words beside an item on the site.

The items are saved briefings (``/b/<id>``): the site has no country pages yet. A statement can be given the country
it speaks for (``wcs publish-statement 3 --country KEN``), so a country's page can gather its statements once there is
one (``GET /api/statements?country=KEN``).

A statement is kept in the site's own database (``data/state.db``, table ``statements``, as problem reports are) and
shown beside its briefing, headed "Statement from [name], [date]" and followed by "In their own words. Not checked or
endorsed by Where Countries Stand." It is never part of the record: nothing in the search index, the briefing engine
or the AI model's instructions reads it (``wcs.rag``, ``wcs.engine``, ``wcs.index``), it is not in a briefing's saved
or cached events, and ``tests/test_statements.py`` checks that it never reaches the model.

The email address used to confirm who sent a statement is kept while it is waiting or shown, and deleted three months
after it is withdrawn or declined (the privacy notice): as each statement arrives, by every owner's command on
statements, and twice a day by ``wcs update``.
"""

from __future__ import annotations

import calendar
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlsplit

from wcs.api.usage import StateDB, utcnow

MAX_WORDS = 150
# the longest a 150-word statement could reasonably be, so a wall of text without spaces is refused too
MAX_CHARS = 1500
SITE_HOSTS = ("wherecountriesstand.com", "www.wherecountriesstand.com")
SITE_NAME = "Where Countries Stand"
NOT_ENDORSED = f"In their own words. Not checked or endorsed by {SITE_NAME}."
# How long the confirming address is kept after a statement is withdrawn or declined (the privacy notice).
EMAIL_MONTHS = 3
# What the public sees of a statement: never its author's email address, the reason it ended or the owner's log.
PUBLIC = ("id", "page", "country", "name", "role", "text", "link", "shown_on")
# A saved briefing's address, as app.shared_briefing accepts it.
_ITEM = re.compile(r"^/b/([A-Za-z0-9_-]{6,20})$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COUNTRY = re.compile(r"^[A-Z]{3}$")


# ---- checking what is sent -------------------------------------------------------------------------------------

def word_count(text: str) -> int:
    return len(text.split())


def item_address(address: str, hosts: tuple[str, ...] = SITE_HOSTS) -> str:
    """The briefing a statement is about, as its path on this site (``/b/<id>``). Accepts a full address or a path;
    raises ValueError, with words for the reader, otherwise."""
    a = (address or "").strip()
    if not a:
        raise ValueError("Give the address of the briefing the statement is about.")
    parts = urlsplit(a if "://" in a or a.startswith("/") else "https://" + a)
    if parts.scheme not in ("http", "https", "") or (parts.hostname and parts.hostname.lower() not in hosts):
        raise ValueError("The address must be a briefing on wherecountriesstand.com.")
    m = _ITEM.match(unquote(parts.path).rstrip("/"))
    if not m:
        raise ValueError("The address must be a briefing on this site, such as https://wherecountriesstand.com/b/…")
    return f"/b/{m[1]}"


def check_text(text: str) -> str:
    """The statement's words, as written (never edited: policy 7.2), or ValueError if they are missing or too long."""
    t = (text or "").strip()
    if not t:
        raise ValueError("Write the statement.")
    if word_count(t) > MAX_WORDS:
        raise ValueError(f"A statement can be up to {MAX_WORDS} words; this one is {word_count(t)}.")
    if len(t) > MAX_CHARS:
        raise ValueError(f"A statement can be up to {MAX_WORDS} words ({MAX_CHARS:,} characters).")
    return t


def check_line(value: str, what: str, longest: int = 200) -> str:
    """A name or role, on one line."""
    v = " ".join((value or "").split())
    if len(v) < 2:
        raise ValueError(f"Give {what}.")
    if len(v) > longest:
        raise ValueError(f"{what[0].upper()}{what[1:]} can be up to {longest} characters.")
    return v


def check_link(link: str) -> str:
    """A link to the record: a web address, with no quotes, angle brackets or spaces (as Where MPs Stand)."""
    link = (link or "").strip()
    if link and (urlsplit(link).scheme not in ("http", "https") or not urlsplit(link).hostname
                 or any(c in link for c in "\"'<> ")):
        raise ValueError("The link to the record must be a web address starting https:// (or leave it empty).")
    return link


def check_email(email: str) -> str:
    email = (email or "").strip()
    if not _EMAIL.match(email):
        raise ValueError("Give an email address we can use to confirm who you are.")
    return email


def check_country(code: str | None) -> str | None:
    """An ISO 3166 alpha-3 code, as the site uses for countries, or None."""
    if not code:
        return None
    c = code.strip().upper()
    if not _COUNTRY.match(c):
        raise ValueError("A country is given by its three-letter code, such as KEN.")
    return c


# ---- the store -------------------------------------------------------------------------------------------------

def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def months_before(when: datetime, months: int) -> datetime:
    """The same day ``months`` calendar months earlier (the month's last day where that day doesn't exist)."""
    y, m = divmod(when.month - 1 - months, 12)
    year, month = when.year + y, m + 1
    return when.replace(year=year, month=month, day=min(when.day, calendar.monthrange(year, month)[1]))


def _row(keys: list[str], row: tuple) -> dict[str, Any]:
    out = dict(zip(keys, row, strict=True))
    if "log" in out:
        out["log"] = json.loads(out["log"])
    return out


def add_statement(state: StateDB, page: str, name: str, role: str, text: str, link: str, email: str) -> int:
    """A statement as sent, waiting for the owner to confirm who sent it, then publish or decline it."""
    forget_emails(state)
    with state.connect() as con:
        cur = con.execute("INSERT INTO statements (at, page, name, role, text, link, email, log) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (utcnow(), page, name, role, text, link, email,
                           json.dumps([{"at": utcnow(), "action": "received"}])))
        return int(cur.lastrowid)


def statement(state: StateDB, sid: int) -> dict[str, Any] | None:
    with state.connect() as con:
        cur = con.execute("SELECT * FROM statements WHERE id = ?", (sid,))
        row = cur.fetchone()
        keys = [d[0] for d in cur.description]
    return _row(keys, row) if row else None


def statements(state: StateDB, open_only: bool = True) -> list[dict[str, Any]]:
    """For the owner: waiting and published statements (every one with ``open_only`` off), newest first."""
    with state.connect() as con:
        cur = con.execute("SELECT * FROM statements" +
                          (" WHERE status IN ('pending', 'published')" if open_only else "") + " ORDER BY id DESC")
        keys = [d[0] for d in cur.description]
        return [_row(keys, r) for r in cur.fetchall()]


def published_statements(state: StateDB, page: str | None = None, country: str | None = None) -> list[dict[str, Any]]:
    """What the public sees: the published statements beside one briefing, or all of one country's, oldest first.
    Never the author's email address."""
    where, args = ["status = 'published'"], []
    if page:
        where.append("page = ?")
        args.append(page)
    if country:
        where.append("country = ?")
        args.append(country)
    with state.connect() as con:
        rows = con.execute(f"SELECT {', '.join(PUBLIC)} FROM statements WHERE {' AND '.join(where)} "
                           "ORDER BY shown_on, id", args).fetchall()
    return [dict(zip(PUBLIC, r, strict=True)) for r in rows]


def _change(state: StateDB, sid: int, from_status: tuple[str, ...], action: str, note: str = "",
            **fields: Any) -> bool:
    """Change a statement, if it is in one of ``from_status``, and log what was done and when (policy 7.4)."""
    with state.connect() as con:
        row = con.execute("SELECT status, log FROM statements WHERE id = ?", (sid,)).fetchone()
        if not row or row[0] not in from_status:
            return False
        fields["log"] = json.dumps(json.loads(row[1]) + [{"at": utcnow(), "action": action,
                                                          **({"note": note} if note else {})}])
        con.execute(f"UPDATE statements SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                    (*fields.values(), sid))
    return True


def publish_statement(state: StateDB, sid: int, country: str | None = None) -> bool:
    """Show a waiting statement beside its briefing, dated today, as written. ``country`` records the country it
    speaks for, for that country's page once there is one."""
    extra = {"country": country} if country else {}
    return _change(state, sid, ("pending",), "published", status="published", shown_on=today(), **extra)


def decline_statement(state: StateDB, sid: int, reason: str) -> bool:
    return _change(state, sid, ("pending",), "declined", reason, status="declined", ended_at=utcnow(), reason=reason)


def update_statement(state: StateDB, sid: int, **changes: str | None) -> bool:
    """The author's new wording, name, role or link, as they sent it, or the country it speaks for. A published
    statement with new words is dated afresh: its heading gives the date of the words shown."""
    changes = {k: v for k, v in changes.items() if k in ("text", "name", "role", "link", "country") and v is not None}
    if not changes:
        return False
    current = statement(state, sid)
    if current and current["status"] == "published" and changes.keys() & {"text", "name", "role", "link"}:
        changes["shown_on"] = today()
    return _change(state, sid, ("pending", "published"), "updated", ", ".join(sorted(changes)), **changes)


def withdraw_statement(state: StateDB, sid: int, reason: str) -> bool:
    """Take a statement down: at its author's request, or because it breaks the rules (policy 7.2: say why)."""
    return _change(state, sid, ("pending", "published"), "withdrawn", reason, status="withdrawn", ended_at=utcnow(),
                   reason=reason)


def forget_emails(state: StateDB, now: datetime | None = None) -> int:
    """Delete the confirming address of every statement withdrawn or declined more than three months ago (the
    privacy notice). Returns how many were deleted."""
    cutoff = months_before(now or datetime.now(UTC), EMAIL_MONTHS).isoformat(timespec="seconds")
    with state.connect() as con:
        return con.execute("UPDATE statements SET email = '' WHERE status IN ('withdrawn', 'declined') "
                           "AND email != '' AND ended_at < ?", (cutoff,)).rowcount
