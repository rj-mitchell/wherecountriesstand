"""Spend tracking, caps, rate limits and the answer cache (every model-written briefing costs money).

The site is public, so limits apply per visitor as well as overall (docs/DECISIONS.md, 9: a separate AI spending
cap). A visitor is identified by a salted hash of their IP address, with a salt that changes every day; the address
itself is never stored. Adapted from Where MPs Stand's ``api/usage.py``.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, visitor TEXT, at TEXT, kind TEXT, model TEXT,
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cost_usd REAL,
    cache_write_tokens INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS usage_at ON usage (at);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS answer_cache (key TEXT PRIMARY KEY, created_at TEXT, events TEXT);
CREATE TABLE IF NOT EXISTS failures (id INTEGER PRIMARY KEY, at TEXT, kind TEXT, question TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS briefings (id TEXT PRIMARY KEY, at TEXT NOT NULL, question TEXT NOT NULL,
    options TEXT NOT NULL, events TEXT NOT NULL, withdrawn_at TEXT, withdrawn_reason TEXT);
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, kind TEXT NOT NULL,
    page TEXT, message TEXT NOT NULL, contact TEXT, closed_at TEXT);
"""

FAILURE_DAYS = 90  # how long notes of failed briefings are kept


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class StateDB:
    """The site's own small database (data/state.db): spend, the answer cache, failures."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)
            # databases made before cache writes were recorded
            if "cache_write_tokens" not in {r[1] for r in con.execute("PRAGMA table_info(usage)")}:
                con.execute("ALTER TABLE usage ADD COLUMN cache_write_tokens INTEGER DEFAULT 0")

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def get(self, key: str) -> str | None:
        with self.connect() as con:
            row = con.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


# ---- cost ---------------------------------------------------------------------------------------------------

def prices_for(prices: dict[str, Any], model: str | None) -> dict[str, Any]:
    """The [usage] prices, with any per-model prices (``[usage.models."<model>"]``) on top."""
    for name, own in (prices.get("models") or {}).items():
        if model and (model == name or model.startswith(name)):
            return {**prices, **own}
    return prices


def token_counts(usage: dict[str, int] | None) -> dict[str, int]:
    """Token counts in the site's own form, whichever names they come in. ``rag.answer.usage_dict`` gives
    ``input_tokens`` including cache writes, with ``cache_write_tokens`` and ``cache_read_tokens``. The SDK's own
    names (``cache_creation_input_tokens``, ``cache_read_input_tokens``) come with ``input_tokens`` that leave the
    cache writes out, so they are added in. (Where Councils Stand's ``api/usage.py`` copes with both, too.)"""
    u = usage or {}
    if "cache_write_tokens" in u:
        writes = int(u.get("cache_write_tokens") or 0)
        given = int(u.get("input_tokens") or 0)
    else:
        writes = int(u.get("cache_creation_input_tokens") or 0)
        given = int(u.get("input_tokens") or 0) + writes
    reads = u.get("cache_read_tokens")
    if reads is None:
        reads = u.get("cache_read_input_tokens")
    return {"input_tokens": given, "output_tokens": int(u.get("output_tokens") or 0),
            "cache_read_tokens": int(reads or 0), "cache_write_tokens": writes}


def cost_usd(usage: dict[str, int] | None, prices: dict[str, Any], model: str | None = None) -> float:
    """Cost of one call. Cache writes cost 1.25x the input price; cache reads are priced separately."""
    if not usage:
        return 0.0
    u = token_counts(usage)
    prices = prices_for(prices, model)
    price_in = float(prices.get("price_input_per_mtok", 4.0))
    writes = u["cache_write_tokens"]
    return round(
        (u["input_tokens"] - writes) / 1e6 * price_in
        + writes / 1e6 * price_in * float(prices.get("cache_write_multiplier", 1.25))
        + u["output_tokens"] / 1e6 * float(prices.get("price_output_per_mtok", 20.0))
        + u["cache_read_tokens"] / 1e6 * float(prices.get("price_cache_read_per_mtok", 0.2)),
        6,
    )


def network_of(address: str | None) -> str:
    """What the limits count by: an IPv4 address, or an IPv6 /64. One home or phone connection is given a whole /64,
    so counting single IPv6 addresses would let one person appear as countless visitors (from Where MPs Stand's
    ``api/usage.py``)."""
    if not address:
        return "-"
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 4:
        return str(ip)
    if ip.ipv4_mapped:
        return str(ip.ipv4_mapped)
    return str(ip) if ip.is_loopback else str(ipaddress.ip_network(f"{ip}/64", strict=False))


def visitor_key(state: StateDB, address: str | None, day: str | None = None) -> str:
    """A code for a visitor's IP address (an IPv6 address by its /64: ``network_of``), for daily limits; the address
    itself is not kept. The salt changes every day (UTC) and old ones are deleted, so codes cannot be linked across
    days or turned back into an address."""
    name = f"visitor_salt:{day or datetime.now(UTC).strftime('%Y-%m-%d')}"
    salt = state.get(name)
    if salt is None:
        with state.connect() as con:
            con.execute("INSERT OR IGNORE INTO kv (key, value) VALUES (?, ?)", (name, secrets.token_hex(16)))
            con.execute("DELETE FROM kv WHERE key LIKE 'visitor_salt:%' AND key != ?", (name,))
        salt = state.get(name)
    return hashlib.sha256(f"{salt}|{network_of(address)}".encode()).hexdigest()[:24]


def record_usage(state: StateDB, kind: str, model: str | None, usage: dict[str, int] | None,
                 prices: dict[str, Any], visitor: str | None = None) -> float:
    cost = cost_usd(usage, prices, model)
    u = token_counts(usage)
    with state.connect() as con:
        con.execute("INSERT INTO usage (visitor, at, kind, model, input_tokens, output_tokens, cache_read_tokens, "
                    "cache_write_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (visitor, utcnow(), kind, model, u["input_tokens"], u["output_tokens"], u["cache_read_tokens"],
                     u["cache_write_tokens"], cost))
    return cost


def spent(state: StateDB, since: str) -> float:
    with state.connect() as con:
        return float(con.execute("SELECT coalesce(sum(cost_usd), 0) FROM usage WHERE at >= ?", (since,)).fetchone()[0])


def check_caps(state: StateDB, limits: dict[str, Any], visitor: str | None = None,
               pending_usd: float = 0.0) -> str | None:
    """A plain-English reason if another briefing may not be written now, else None. ``pending_usd`` is the
    estimated cost of briefings being written at this moment, whose cost is not recorded yet."""
    now = datetime.now(UTC)
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    if visitor is not None:
        with state.connect() as con:
            asked = con.execute("SELECT count(*) FROM usage WHERE visitor = ? AND kind = 'answer' AND at >= ?",
                                (visitor, day)).fetchone()[0]
        if asked >= int(limits.get("per_visitor_daily_questions", 40)):
            return ("You have reached today's limit for new briefings. It resets at midnight UTC; briefings others "
                    "have already asked for still load.")
    if spent(state, day) + pending_usd >= float(limits.get("daily_spend_cap_usd", 3.0)):
        return "Today's budget for new briefings has been used up. It resets at midnight UTC."
    if spent(state, month) + pending_usd >= float(limits.get("monthly_spend_cap_usd", 50.0)):
        return "This month's budget for new briefings has been used up."
    return None


class InFlight:
    """Briefings being written right now (from Where MPs Stand's ``api/usage.py``). Their cost is only known when
    they finish, so without this, many questions at once would all pass the spending caps before any was counted:
    each is reserved at an estimated cost while it runs, and each visitor (and the site as a whole) may have only a
    few at once."""

    def __init__(self, per_key: int = 2, total: int = 8, estimate_usd: float = 0.10):
        self.per_key, self.total, self.estimate_usd = per_key, total, estimate_usd
        self.running: dict[str, int] = defaultdict(int)
        self.lock = threading.Lock()

    def start(self, key: str) -> str | None:
        """Reserve a place, or say why not."""
        with self.lock:
            if self.running.get(key, 0) >= self.per_key:  # .get: a refused visitor leaves nothing behind
                return "A briefing is already being written for you; please wait for it to finish."
            if sum(self.running.values()) >= self.total:
                return "Many briefings are being written at the moment; please try again in a minute."
            self.running[key] += 1
            return None

    def finish(self, key: str) -> None:
        with self.lock:
            self.running[key] -= 1
            if self.running[key] <= 0:
                del self.running[key]

    def pending_usd(self, besides: int = 0) -> float:
        """Estimated cost of the briefings in progress, not counting ``besides`` of them (the caller's own)."""
        with self.lock:
            return max(sum(self.running.values()) - besides, 0) * self.estimate_usd


class RateLimiter:
    """At most ``per_minute`` requests per key in any rolling minute (in memory, per process)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return False
            q.append(now)
            if len(self.hits) > 50_000:  # forget idle visitors
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 60]:
                    del self.hits[k]
            return True


# ---- answer cache ---------------------------------------------------------------------------------------------

def cache_key(parts: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def cached_events(state: StateDB, key: str, hours: float) -> list[dict[str, Any]] | None:
    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with state.connect() as con:
        row = con.execute("SELECT events FROM answer_cache WHERE key = ? AND created_at >= ?", (key, since)).fetchone()
    return json.loads(row[0]) if row else None


def store_events(state: StateDB, key: str, events: list[dict[str, Any]], hours: float) -> None:
    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with state.connect() as con:
        con.execute("DELETE FROM answer_cache WHERE created_at < ?", (since,))
        con.execute("INSERT OR REPLACE INTO answer_cache (key, created_at, events) VALUES (?, ?, ?)",
                    (key, utcnow(), json.dumps(events, default=str)))


def record_failure(state: StateDB, kind: str, question: str | None, error: str) -> None:
    with state.connect() as con:
        con.execute("INSERT INTO failures (at, kind, question, error) VALUES (?, ?, ?, ?)",
                    (utcnow(), kind, (question or "")[:500], error[:500]))
        # the privacy notice (web/site/privacy.html) says these are deleted after 90 days
        con.execute("DELETE FROM failures WHERE at < ? OR id NOT IN (SELECT id FROM failures ORDER BY id DESC "
                    "LIMIT 500)", ((datetime.now(UTC) - timedelta(days=FAILURE_DAYS)).isoformat(timespec="seconds"),))


# ---- reports --------------------------------------------------------------------------------------------------

WARN_AT = 0.8  # spending at this share of a cap is worth knowing about before it stops new briefings


def summary(state: StateDB, limits: dict[str, Any], now: datetime | None = None, days: int = 7) -> dict[str, Any]:
    """Spend today and this month against the caps, briefings written, and recent failures (``wcs usage``)."""
    now = now or datetime.now(UTC)
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    out: dict[str, Any] = {"at": now.isoformat(timespec="seconds")}
    with state.connect() as con:
        for name, start, cap in (("today", day, "daily_spend_cap_usd"), ("month", month, "monthly_spend_cap_usd")):
            cost, briefings, visitors = con.execute(
                "SELECT coalesce(sum(cost_usd), 0), count(*) FILTER (WHERE kind = 'answer'), count(DISTINCT visitor) "
                "FROM usage WHERE at >= ?", (start,)).fetchone()
            out[name] = {"spent_usd": round(cost, 4), "cap_usd": float(limits.get(cap, 0) or 0),
                         "briefings": briefings, "visitors": visitors}
        out["by_day"] = [{"day": d, "spent_usd": round(c, 4), "briefings": n} for d, c, n in con.execute(
            "SELECT substr(at, 1, 10), sum(cost_usd), count(*) FILTER (WHERE kind = 'answer') FROM usage "
            "WHERE at >= ? GROUP BY 1 ORDER BY 1", (since,))]
        out["failures"] = [{"at": a, "kind": k, "question": q, "error": e} for a, k, q, e in con.execute(
            "SELECT at, kind, question, error FROM failures WHERE at >= ? ORDER BY id DESC", (since,))]
    out["days"] = days
    return out


def warnings(report: dict[str, Any], hours: float = 24) -> list[str]:
    """Plain-English warnings for the update report: spending near or at a cap, and recent failed briefings."""
    out = []
    for name, label in (("today", "today's"), ("month", "this month's")):
        r = report[name]
        if r["cap_usd"] and r["spent_usd"] >= WARN_AT * r["cap_usd"]:
            state = "reached: new briefings are paused" if r["spent_usd"] >= r["cap_usd"] else \
                f"{r['spent_usd'] / r['cap_usd']:.0%} used"
            out.append(f"spending: {label} cap of ${r['cap_usd']:.2f} {state} (${r['spent_usd']:.2f} spent)")
    since = (datetime.fromisoformat(report["at"]) - timedelta(hours=hours)).isoformat(timespec="seconds")
    recent = [f for f in report["failures"] if f["at"] >= since]
    if recent:
        out.append(f"{len(recent)} briefing(s) failed in the last {hours:g} hours; the latest: {recent[0]['error']} "
                   "(wcs usage lists them)")
    return out


# ---- saved briefings and problem reports (adapted from Where Councils Stand's api/state.py) --------------------

def new_briefing_id() -> str:
    return secrets.token_urlsafe(8)


def save_briefing(state: StateDB, bid: str, question: str, options: dict[str, Any],
                  events: list[dict[str, Any]]) -> str:
    """A written briefing, kept at its own address (/b/<id>) while the site runs, unless withdrawn."""
    with state.connect() as con:
        con.execute("INSERT INTO briefings (id, at, question, options, events) VALUES (?, ?, ?, ?, ?)",
                    (bid, utcnow(), question, json.dumps(options), json.dumps(events, default=str)))
    return bid


def saved_briefing(state: StateDB, bid: str) -> dict[str, Any] | None:
    with state.connect() as con:
        row = con.execute("SELECT id, at, question, options, events, withdrawn_at, withdrawn_reason FROM briefings "
                          "WHERE id = ?", (bid,)).fetchone()
    if not row:
        return None
    keys = ("id", "at", "question", "options", "events", "withdrawn_at", "withdrawn_reason")
    b = dict(zip(keys, row, strict=True))
    return {**b, "options": json.loads(b["options"]), "events": json.loads(b["events"])}


def withdraw_briefing(state: StateDB, bid: str, reason: str) -> bool:
    with state.connect() as con:
        return bool(con.execute("UPDATE briefings SET withdrawn_at = ?, withdrawn_reason = ? WHERE id = ?",
                                (utcnow(), reason, bid)).rowcount)


def withdrawn_briefings(state: StateDB) -> list[dict[str, Any]]:
    """For the corrections page: what was withdrawn, when and why (the question, not the briefing)."""
    with state.connect() as con:
        rows = con.execute("SELECT id, at, question, withdrawn_at, withdrawn_reason FROM briefings "
                           "WHERE withdrawn_at IS NOT NULL ORDER BY withdrawn_at DESC").fetchall()
    return [dict(zip(("id", "at", "question", "withdrawn_at", "reason"), r, strict=True)) for r in rows]


def add_report(state: StateDB, kind: str, page: str, message: str, contact: str) -> int:
    with state.connect() as con:
        cur = con.execute("INSERT INTO reports (at, kind, page, message, contact) VALUES (?, ?, ?, ?, ?)",
                          (utcnow(), kind, page, message, contact))
        return int(cur.lastrowid)


def reports(state: StateDB, open_only: bool = True) -> list[dict[str, Any]]:
    with state.connect() as con:
        rows = con.execute("SELECT id, at, kind, page, message, contact, closed_at FROM reports" +
                           (" WHERE closed_at IS NULL" if open_only else "") + " ORDER BY id DESC").fetchall()
    return [dict(zip(("id", "at", "kind", "page", "message", "contact", "closed_at"), r, strict=True)) for r in rows]


def close_report(state: StateDB, report_id: int) -> bool:
    """The contact address is used only to reply, and deleted when the report is dealt with (the privacy notice)."""
    with state.connect() as con:
        return bool(con.execute("UPDATE reports SET closed_at = ?, contact = '' WHERE id = ? AND closed_at IS NULL",
                                (utcnow(), report_id)).rowcount)
