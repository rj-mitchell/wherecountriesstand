"""Spend tracking, caps, rate limits and the answer cache (every model-written briefing costs money).

The site is public, so limits apply per visitor as well as overall (docs/DECISIONS.md, 9: a separate AI spending
cap). A visitor is identified by a salted hash of their IP address, with a salt that changes every day; the address
itself is never stored. Adapted from Where MPs Stand's ``api/usage.py``.
"""

from __future__ import annotations

import hashlib
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
    input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cost_usd REAL);
CREATE INDEX IF NOT EXISTS usage_at ON usage (at);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS answer_cache (key TEXT PRIMARY KEY, created_at TEXT, events TEXT);
CREATE TABLE IF NOT EXISTS failures (id INTEGER PRIMARY KEY, at TEXT, kind TEXT, question TEXT, error TEXT);
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


def cost_usd(usage: dict[str, int] | None, prices: dict[str, Any], model: str | None = None) -> float:
    """Cost of one call. Cache writes cost 1.25x the input price; cache reads are priced separately."""
    if not usage:
        return 0.0
    prices = prices_for(prices, model)
    price_in = float(prices.get("price_input_per_mtok", 4.0))
    writes = usage.get("cache_write_tokens", 0)
    return round(
        (usage.get("input_tokens", 0) - writes) / 1e6 * price_in
        + writes / 1e6 * price_in * float(prices.get("cache_write_multiplier", 1.25))
        + usage.get("output_tokens", 0) / 1e6 * float(prices.get("price_output_per_mtok", 20.0))
        + usage.get("cache_read_tokens", 0) / 1e6 * float(prices.get("price_cache_read_per_mtok", 0.2)),
        6,
    )


def visitor_key(state: StateDB, address: str | None, day: str | None = None) -> str:
    """A code for a visitor's IP address, for daily limits; the address itself is not kept. The salt changes every
    day (UTC) and old ones are deleted, so codes cannot be linked across days or turned back into an address."""
    name = f"visitor_salt:{day or datetime.now(UTC).strftime('%Y-%m-%d')}"
    salt = state.get(name)
    if salt is None:
        with state.connect() as con:
            con.execute("INSERT OR IGNORE INTO kv (key, value) VALUES (?, ?)", (name, secrets.token_hex(16)))
            con.execute("DELETE FROM kv WHERE key LIKE 'visitor_salt:%' AND key != ?", (name,))
        salt = state.get(name)
    return hashlib.sha256(f"{salt}|{address or '-'}".encode()).hexdigest()[:24]


def record_usage(state: StateDB, kind: str, model: str | None, usage: dict[str, int] | None,
                 prices: dict[str, Any], visitor: str | None = None) -> float:
    cost = cost_usd(usage, prices, model)
    u = usage or {}
    with state.connect() as con:
        con.execute("INSERT INTO usage (visitor, at, kind, model, input_tokens, output_tokens, cache_read_tokens, "
                    "cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (visitor, utcnow(), kind, model, u.get("input_tokens", 0), u.get("output_tokens", 0),
                     u.get("cache_read_tokens", 0), cost))
    return cost


def spent(state: StateDB, since: str) -> float:
    with state.connect() as con:
        return float(con.execute("SELECT coalesce(sum(cost_usd), 0) FROM usage WHERE at >= ?", (since,)).fetchone()[0])


def check_caps(state: StateDB, limits: dict[str, Any], visitor: str | None = None) -> str | None:
    """A plain-English reason if another briefing may not be written now, else None."""
    now = datetime.now(UTC)
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    if visitor is not None:
        with state.connect() as con:
            asked = con.execute("SELECT count(*) FROM usage WHERE visitor = ? AND kind = 'answer' AND at >= ?",
                                (visitor, day)).fetchone()[0]
        if asked >= int(limits.get("per_visitor_daily_questions", 40)):
            return ("You have reached today's limit for new briefings. It resets at midnight UTC; briefings others "
                    "have already asked for still load.")
    if spent(state, day) >= float(limits.get("daily_spend_cap_usd", 3.0)):
        return "Today's budget for new briefings has been used up. It resets at midnight UTC."
    if spent(state, month) >= float(limits.get("monthly_spend_cap_usd", 50.0)):
        return "This month's budget for new briefings has been used up."
    return None


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
