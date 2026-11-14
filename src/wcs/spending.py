"""What the site has spent on briefings, against its caps, and which briefings failed: for ``wcs usage`` and the
update's report. The figures are read from the Where They Stand sites' shared spending record and notes of
failures (wts-core, ``wts_core.usage``); the report's shape and its warnings' words are this site's own.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.state import StateDB
from wts_core.usage import Failures, Limits, PriceTable, Spending

from wcs.config import Settings

WARN_AT = 0.8  # spending at this share of a cap is worth knowing about before it stops new briefings


def spending_record(settings: Settings, db: StateDB, clock: Clock = SYSTEM_CLOCK) -> Spending:
    """The spending record, priced by ``[usage]``, for the owner's commands (the web app builds its own with the
    briefing desk)."""
    return Spending(db, PriceTable.from_settings(settings.usage, file=settings.config_path), clock=clock)


def summary(settings: Settings, db: StateDB, clock: Clock = SYSTEM_CLOCK, days: int = 7) -> dict[str, Any]:
    """Spend today and this month against the caps, briefings written, and recent failures (``wcs usage``)."""
    now = clock.now()
    spending = spending_record(settings, db, clock)
    limits = Limits.from_settings(settings.usage, file=settings.config_path)
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    out: dict[str, Any] = {"at": now.isoformat(timespec="seconds")}
    for name, start, cap in (("today", now.strftime("%Y-%m-%d"), limits.daily_cap_usd),
                             ("month", now.strftime("%Y-%m"), limits.monthly_cap_usd)):
        total = spending.totals(start)
        out[name] = {"spent_usd": round(total.spent_usd, 4), "cap_usd": float(cap or 0),
                     "briefings": total.briefings, "visitors": total.visitors}
    out["by_day"] = [{"day": d.day, "spent_usd": round(d.spent_usd, 4), "briefings": d.briefings}
                     for d in spending.by_day(since)]
    out["failures"] = Failures(db, clock=clock).recent(None, since=since)
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
