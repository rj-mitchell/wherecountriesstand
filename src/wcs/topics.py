"""Topic briefings (decision 27): standing questions on what the UN keeps returning to, each written afresh every
week and shown at a fixed address, ``/topics/<slug>.html``, for first-time visitors and search engines.

The topics are listed in ``config/topics.toml`` (``[topics] file``). ``refresh`` writes each one's briefing through
the site's own pipeline, with every check a reader's briefing has: the question is read, the topic's own filters
(its dates and bodies) are laid over that reading, the sources are found and the model writes from them alone. The
briefing is saved as any other (it also has an address of its own, ``/b/<id>``), its cost is recorded against the
site's caps as kind ``topic``, and a failure is noted for the owner. The previous version stands whenever a run
fails, finds no records or is stopped at a cap.

Each topic's versions are kept in the state database's key-value table (``topic:<slug>``: the briefing's id, when
it was prepared and the latest record of the UN's it could use), newest first, so they are backed up with the
briefings themselves. A version that has been withdrawn, or is hidden while a concern is decided, is passed over,
and the page shows the newest one standing.
"""

from __future__ import annotations

import json
import logging
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.state import StateDB
from wts_core.usage import SavedBriefing

from wcs.config import Settings
from wcs.replies import note_in_place

log = logging.getLogger(__name__)

SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
KEY = "topic:{}"
KIND = "topic"  # the spending record's kind for a topic's calls: counted against the caps as every other
KEPT = 52  # versions kept in a topic's list (a year of weeks); the briefings themselves are never deleted
BODIES = ("sc", "gd", "ga")


@dataclass(frozen=True)
class Topic:
    slug: str
    title: str
    question: str
    area: str = "Topics"
    description: str = ""
    date_from: str | None = None
    date_to: str | None = None
    sources: tuple[str, ...] = ()

    @property
    def path(self) -> str:
        return f"/topics/{self.slug}.html"

    @property
    def filters(self) -> dict[str, Any]:
        """The filters the topic lays over the reading of its question: those it sets, and no others."""
        out: dict[str, Any] = {"date_from": self.date_from, "date_to": self.date_to, "sources": list(self.sources)}
        return {k: v for k, v in out.items() if v}


def topics_file(settings: Settings) -> Path:
    return settings.config_path.parent / str(settings.section("topics").get("file", "topics.toml"))


def _date(value: Any, slug: str, name: str) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"topic {slug!r}: {name} must be a date written YYYY-MM-DD, not {text!r}") from None
    return text


def load_topics(path: Path) -> list[Topic]:
    """The topics in ``path``, in its order. A topic that would give two pages one address, or an address that is
    not lower-case words joined by hyphens, stops the site starting, so a mistake shows at once."""
    if not Path(path).is_file():
        return []
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    out: list[Topic] = []
    seen: set[str] = set()
    for t in raw.get("topic", []):
        slug = str(t.get("slug", ""))
        if not SLUG.fullmatch(slug) or slug in seen:
            raise ValueError(f"topic slugs must be unique, lower-case words joined by hyphens: {slug!r}")
        seen.add(slug)
        for name in ("title", "question"):
            if not str(t.get(name, "")).strip():
                raise ValueError(f"topic {slug!r} has no {name}")
        sources = tuple(str(s) for s in t.get("sources") or ())
        if any(s not in BODIES for s in sources):
            raise ValueError(f"topic {slug!r}: sources are {', '.join(BODIES)}, not {list(sources)}")
        out.append(Topic(slug=slug, title=str(t["title"]).strip(), question=str(t["question"]).strip(),
                         area=str(t.get("area") or "Topics"), description=str(t.get("description") or ""),
                         date_from=_date(t.get("date_from"), slug, "date_from"),
                         date_to=_date(t.get("date_to"), slug, "date_to"), sources=sources))
    return out


# Versions


def _entry(db: StateDB, slug: str) -> dict[str, Any]:
    raw = db.get(KEY.format(slug))
    try:
        value = json.loads(raw) if raw else {}
    except ValueError:
        log.warning("topic %s: its list of versions could not be read, and is started again", slug)
        value = {}
    return value if isinstance(value, dict) else {}


def versions(db: StateDB, slug: str) -> list[dict[str, Any]]:
    """Every version of a topic's briefing, newest first: its briefing's id, when it was prepared and the latest
    record it could use."""
    return [v for v in _entry(db, slug).get("versions") or [] if isinstance(v, dict) and v.get("briefing_id")]


def last_run(db: StateDB, slug: str) -> dict[str, Any] | None:
    """When the topic was last refreshed, and what happened."""
    run = _entry(db, slug).get("last_run")
    return run if isinstance(run, dict) else None


def _write(db: StateDB, slug: str, *, version: Mapping[str, Any] | None = None,
           run: Mapping[str, Any] | None = None) -> None:
    entry = _entry(db, slug)
    if version is not None:
        entry["versions"] = [dict(version), *versions(db, slug)][:KEPT]
    if run is not None:
        entry["last_run"] = dict(run)
    db.set(KEY.format(slug), json.dumps(entry, ensure_ascii=False))


def keep_version(db: StateDB, slug: str, briefing_id: str, prepared_at: str, records_up_to: str | None) -> None:
    """Make a saved briefing the newest version of a topic."""
    _write(db, slug, version={"briefing_id": briefing_id, "prepared_at": prepared_at, "records_up_to": records_up_to})


@dataclass(frozen=True)
class Current:
    """The version a topic's page shows: the newest that is neither withdrawn nor hidden."""
    version: dict[str, Any]
    briefing: SavedBriefing

    @property
    def prepared_at(self) -> str:
        return str(self.version.get("prepared_at") or self.briefing.created_at)


def current(db: StateDB, archive: Any, slug: str, hidden: Iterable[Mapping[str, Any]] = ()) -> Current | None:
    hidden = list(hidden)
    for v in versions(db, slug):
        b = archive.get(v["briefing_id"])
        if b is None or b.withdrawn_at or note_in_place(b.id, b.events, hidden) is not None:
            continue
        return Current(v, b)
    return None


# Refreshing


@dataclass
class Outcome:
    slug: str
    outcome: str
    briefing_id: str | None = None
    cost_usd: float = 0.0
    ok: bool = True


@dataclass
class Refreshed:
    """What a run did, topic by topic, and what it spent."""
    outcomes: list[Outcome] = field(default_factory=list)
    spent_usd: float = 0.0
    stopped: str | None = None  # why the run stopped before the end: a cap, its own limit, a failure

    @property
    def ok(self) -> bool:
        return self.stopped is None and all(o.ok for o in self.outcomes)


def _without_usage(event: Mapping[str, Any]) -> dict[str, Any]:
    return {**event, "usage": None} if event.get("type") in ("plan", "final") and event.get("usage") else dict(event)


def refresh(settings: Settings, db: StateDB, pipeline: Any, parts: Any, *, only: Iterable[str] | None = None,
            force: bool = False, records_up_to: Callable[[], str | None] = lambda: None,
            clock: Clock = SYSTEM_CLOCK, progress: Callable[[str], None] = log.info) -> Refreshed:
    """Write each topic's briefing afresh. ``parts`` are the site's desk parts (``wcs.api.app.site_parts``): the
    spending record, the caps, the notes of failures and the saved briefings, so a topic's briefing is paid for,
    counted against the caps and kept like any other. ``records_up_to`` gives the latest date in the record, kept
    with each version. ``pipeline`` is the site's (``wcs.rag.pipeline.Pipeline``): its ``make_plan`` and ``run``.

    Before each topic the site's caps are asked, counting the briefing about to be written at its estimate, and so
    is the run's own limit (``[topics] max_cost_usd``): either reached stops the run, and every topic not reached
    keeps its version. A run that fails (the model or the service) stops too, as the same cause would stop the
    others, and keeps the previous version; so does one that finds no records."""
    from wcs.rag.pipeline import AskRequest
    from wcs.rag.query_parse import parse_model

    cfg = settings.section("topics")
    min_days = float(cfg.get("min_days", 6))
    limit = float(cfg.get("max_cost_usd", 3.0))
    mode = str(cfg.get("mode", "summary"))
    estimate = float(parts.in_progress.estimate_usd)
    topics = load_topics(topics_file(settings))
    if only:
        wanted = set(only)
        unknown = wanted - {t.slug for t in topics}
        if unknown:
            raise ValueError(f"no such topic: {', '.join(sorted(unknown))}")
        topics = [t for t in topics if t.slug in wanted]
    done = Refreshed()

    def spend(kind_model: str | None, usage: Mapping[str, Any] | None) -> float:
        if not usage:
            return 0.0
        cost = parts.spending.add(KIND, kind_model, usage)
        done.spent_usd += cost
        return cost

    def note(t: Topic, outcome: str, *, ok: bool = True, bid: str | None = None, cost: float = 0.0,
             record: bool = True) -> None:
        done.outcomes.append(Outcome(t.slug, outcome, bid, round(cost, 6), ok))
        if record:
            _write(db, t.slug, run={"at": clock.now().isoformat(timespec="seconds"), "outcome": outcome})
        progress(f"  {t.slug}: {outcome}")

    def failed(t: Topic, error: str) -> None:
        if parts.failures is not None:
            parts.failures.note(KIND, t.question, error)

    for t in topics:
        if done.stopped:
            note(t, f"not refreshed: {done.stopped}", ok=False, record=False)
            continue
        cur = versions(db, t.slug)
        if cur and not force:
            age = clock.now() - datetime.fromisoformat(str(cur[0]["prepared_at"]))
            if age < timedelta(days=min_days):
                note(t, f"written {age.days} days ago: not written again", record=False)
                continue
        if not pipeline.has_model():
            note(t, "skipped: no API key", record=False)
            continue
        paused = parts.caps.site_paused(pending_usd=estimate)
        if paused is not None:
            done.stopped = f"a spending cap would be reached ({paused.message})"
            note(t, f"not refreshed: {done.stopped}", ok=False)
            continue
        if done.spent_usd + estimate > limit:
            done.stopped = f"this run's limit of ${limit:.2f} would be reached"
            note(t, f"not refreshed: {done.stopped}", ok=False)
            continue
        cost = 0.0
        req = AskRequest(question=t.question, mode=mode)
        try:
            plan, usage = pipeline.make_plan(req)
        except Exception as exc:  # noqa: BLE001 - the previous version stands, and the owner is told
            failed(t, f"{type(exc).__name__}: {exc}")
            done.stopped = "the question could not be read"
            note(t, "failed: kept the previous version (see the failures in wcs usage)", ok=False)
            continue
        cost += spend(parse_model(settings), usage)
        if plan.parse_error and parts.failures is not None:
            # the model could not read the question (it gave nothing that reads as a plan): the built-in rules
            # read it, as for a reader's question, the owner is told, and the topic's own filters are laid over
            # either reading
            parts.failures.note("question reading", t.question, plan.parse_error)
        if plan.off_topic:
            note(t, "no records found: kept the previous version", cost=cost)
            continue
        read = plan.to_dict()
        read["filters"] = {**read["filters"], **t.filters}
        req.plan = {k: v for k, v in read.items() if k != "question"}
        kept: list[dict[str, Any]] = []
        text: list[str] = []
        final: dict[str, Any] | None = None
        error: str | None = None
        try:
            for ev in pipeline.run(req):
                kind = ev.get("type")
                if kind == "text":
                    text.append(str(ev.get("text") or ""))
                elif kind == "citation":
                    continue
                elif kind == "final":
                    final = dict(ev)
                elif kind == "error":
                    error = str(ev.get("message") or "the model declined")
                    cost += spend(ev.get("model"), ev.get("usage"))
                else:
                    kept.append(dict(ev))
        except Exception as exc:  # noqa: BLE001 - an API failure: the previous version stands
            log.exception("topic %s: the briefing could not be written", t.slug)
            error = f"{type(exc).__name__}: {exc}"
        if final is not None:
            cost += spend(final.get("model"), final.get("usage"))
        answer = (final or {}).get("answer")
        if error or not isinstance(answer, dict):
            failed(t, error or str((final or {}).get("note") or "no briefing was written"))
            done.stopped = "a briefing could not be written"
            note(t, "failed: kept the previous version (see the failures in wcs usage)", ok=False, cost=cost)
            continue
        if answer.get("no_records"):
            note(t, "no records found: kept the previous version", cost=cost)
            continue
        # kept as a reader's briefing is (the desk's form): the plan, the sources, the text and the final event,
        # without token counts, at an address of its own
        bid = parts.archive.new_id()
        final = {**_without_usage(final or {}), "briefing_id": bid}
        events = [_without_usage(e) for e in kept if e.get("type") in ("plan", "sources")]
        events += [{"type": "text", "text": "".join(text)}, final]
        saved = parts.archive.save(t.question, {"mode": mode, "topic": t.slug}, events, id=bid,
                                   extra={"topic": t.slug})
        keep_version(db, t.slug, saved.id, saved.created_at, records_up_to())
        note(t, f"written ({saved.id})", bid=saved.id, cost=cost)
    return done
