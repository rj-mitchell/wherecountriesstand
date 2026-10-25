"""Keep the data current: one command for a scheduled job (``wcs update``).

Each run:

1. **Votes.** Reads the voting data record's file list; when the UN has published a new file, downloads and converts
   it.
2. **Security Council.** Finds meeting records newer than the last one held (the Digital Library's catalogue, then
   probing ODS for the following numbers), downloads and parses them, looks again for numbers still missing, fetches
   provisional records again when corrected versions may be out, and parses cached records again if the parser has
   changed. Records are stored for the years from ``[live] sc_from`` (the corpus keeps the years before).
3. **General Debate.** For each session from ``[live] gd_from_session``, looks for the session's first plenary
   meetings (the Digital Library's catalogue, then ODS) until a record says the debate has ended, and stores the
   statements (``wcs.sources.unga``). The records appear about a year after the debate.
4. **Index.** Re-indexes the years that changed, then embeds new passages for as long as allowed.
5. **Report.** Writes data/logs/update-<time>.json, and returns problems so a scheduler can alert on them.

Nothing here depends on one website staying the same: discovery and download each have two routes, every parsed
meeting is checked, and a run that cannot reach the UN at all fails loudly rather than reporting "no new records".
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from wcs.config import Settings
from wcs.reference.countries import Countries, countries
from wcs.sources import gadebate, undocs, unga
from wcs.sources import votes as vote_source
from wcs.sources.verbatim import PARSER_VERSION
from wcs.store.parquet import record_path, write_year, years

log = logging.getLogger(__name__)

STALE_DAYS = 21  # the Council meets most weeks of the year; this long without a new record is worth a look
MAX_GAPS = 300   # meeting numbers missing from the catalogue tried on ODS in one run


@dataclass
class Report:
    started: str
    finished: str | None = None
    votes: dict[str, Any] = field(default_factory=dict)
    discovered: int = 0
    fetched: list[str] = field(default_factory=list)
    refetched: list[str] = field(default_factory=list)
    reparsed: int = 0
    communiques: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    problems: dict[str, list[str]] = field(default_factory=dict)
    years_written: dict[int, int] = field(default_factory=dict)
    indexed: int = 0
    embedded: int = 0
    latest_meeting: str | None = None
    gd: dict[int, dict[str, Any]] = field(default_factory=dict)  # session -> meetings, statements, concluded
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def live_from(settings: Settings) -> date:
    return date.fromisoformat(settings.section("live").get("sc_from", "2024-01-01"))


def gd_from_session(settings: Settings) -> int:
    return int(settings.section("live").get("gd_from_session", 81))


def corpus_last_meeting(settings: Settings) -> int:
    """The highest S/PV number in the stored years before ``sc_from`` (the corpus's last meeting)."""
    start = live_from(settings).year
    files = [str(record_path(settings.parquet_dir, "sc", y)) for y in years(settings.parquet_dir, "sc") if y < start]
    if not files:
        return 0
    row = duckdb.connect().execute(
        f"SELECT max(TRY_CAST(regexp_extract(symbol, 'S/PV\\.(\\d+)', 1) AS INTEGER)) FROM read_parquet({files!r})"
    ).fetchone()
    return int(row[0] or 0)


def update_votes(settings: Settings, report: Report) -> bool:
    try:
        url = vote_source.file_url(settings)
    except Exception as exc:
        report.warnings.append(f"voting data: could not read the record ({exc})")
        return False
    name = url.rsplit("/", 1)[-1]
    current = vote_source.latest_csv(settings)
    report.votes = {"file": name, "had": current.name if current else None}
    if current and current.name == name:
        return False
    path = vote_source.fetch(settings)
    report.votes["votes"] = vote_source.ingest(settings, path)
    return True


def update_security_council(settings: Settings, ref: Countries, docs: undocs.UNDocs, report: Report,
                            max_new: int | None = None) -> set[int]:
    """Fetch, parse and store new and corrected meeting records; returns the years that changed."""
    manifest = undocs.Manifest.load(settings)
    start = live_from(settings)
    after = max(manifest.last_number(), corpus_last_meeting(settings),
                int(settings.section("live").get("sc_after_meeting", 0)))
    changed: set[int] = set()

    # 1. discovery: the catalogue, then probing ODS beyond the highest number known
    catalogue: dict[str, dict[str, Any]] = {}
    library_ok = True
    try:
        for meta in docs.library_meetings(after):
            catalogue[meta["symbol"]] = meta
    except Exception as exc:
        library_ok = False
        report.warnings.append(f"Digital Library search failed ({exc}); probing ODS only")
    top = max([after] + [n for s in catalogue if (n := undocs.meeting_number(s))])
    probed_ok = False
    misses, n = 0, top
    while misses < undocs.PROBE_MISSES:
        n += 1
        symbol = f"S/PV.{n}"
        if symbol in catalogue:
            misses = 0
            continue
        try:
            found = docs.pdf(symbol)
            probed_ok = True
        except Exception as exc:
            report.warnings.append(f"ODS probe failed at {symbol} ({exc})")
            break
        if found is None:
            misses += 1
            continue
        misses = 0
        catalogue[symbol] = {"symbol": symbol, "verbatim": True, "provisional": True, "_pdf": found}
    if not library_ok and not probed_ok:
        report.errors.append("could not reach the Digital Library or ODS: no records checked")
        return changed
    # numbers the catalogue skipped (closed meetings, or records not catalogued yet) are tried on ODS too, a
    # bounded number per run
    gaps = [n for n in range(after + 1, top) if f"S/PV.{n}" not in catalogue and f"S/PV.{n}" not in manifest.entries]
    if len(gaps) > MAX_GAPS:
        report.warnings.append(f"{len(gaps)} meeting numbers not in the catalogue; trying the first {MAX_GAPS}")
    for n in gaps[:MAX_GAPS]:
        catalogue[f"S/PV.{n}"] = {"symbol": f"S/PV.{n}", "verbatim": True}
    report.discovered = len(catalogue)

    # 2. what to fetch: new records, numbers still missing, provisional records due for a corrected version
    todo: list[tuple[dict[str, Any], bool]] = []
    for symbol, meta in sorted(catalogue.items(), key=lambda kv: (undocs.meeting_number(kv[0]) or 0, kv[0])):
        entry = manifest.entries.get(symbol)
        if entry is None or entry.get("status") in (None, "missing") or \
                (entry.get("status") == "failed" and entry.get("attempts", 0) < undocs.MAX_ATTEMPTS):
            todo.append((meta, False))
        elif undocs.due_for_refetch(entry, meta):
            todo.append((meta, True))
    for symbol, entry in manifest.entries.items():
        if symbol in catalogue:
            continue
        if undocs.still_looking(entry) or undocs.due_for_refetch(entry, None):
            todo.append(({"symbol": symbol, "verbatim": True, **{k: entry[k] for k in ("provisional", "agenda")
                                                                  if k in entry}}, entry.get("status") == "parsed"))
    if max_new is not None:
        todo = todo[:max_new]

    for meta, refetch in todo:
        symbol = meta["symbol"]
        pre = meta.pop("_pdf", None)
        if pre is not None:  # downloaded while probing: keep it rather than fetch it twice
            path = undocs.cache_dir(settings) / "pdf" / f"{undocs.safe_name(symbol)}.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pre[0])
            manifest.entries.setdefault(symbol, {"first_seen": undocs._now()}).update(
                {"url": pre[1], "fetched_at": undocs._now(), "provisional": "/pro/" in pre[1]})
            records = undocs.parse_cached(settings, ref, manifest, symbol, meta)
        else:
            try:
                records = undocs.process(settings, ref, manifest, docs, meta, force=refetch)
            except Exception as exc:
                log.exception("failed on %s", symbol)
                manifest.entries.setdefault(symbol, {})["status"] = "failed"
                manifest.entries[symbol]["error"] = f"{type(exc).__name__}: {exc}"
                records = []
        entry = manifest.entries[symbol]
        status = entry.get("status")
        if status == "parsed":
            (report.refetched if refetch else report.fetched).append(symbol)
            changed.update(r["year"] for r in records)
        elif status == "communique":
            report.communiques.append(symbol)
        elif status == "missing":
            report.missing.append(symbol)
        else:
            report.failed.append(symbol)
        if entry.get("problems"):
            report.problems[symbol] = entry["problems"]
        manifest.save()  # after every meeting, so an interrupted run loses nothing

    # 3. records parsed by an older parser are parsed again from the cached PDFs (no downloads)
    for symbol, entry in list(manifest.entries.items()):
        if entry.get("status") == "parsed" and entry.get("parser_version", 0) < PARSER_VERSION:
            records = undocs.parse_cached(settings, ref, manifest, symbol)
            changed.update(r["year"] for r in records)
            report.reparsed += 1
            if report.reparsed % 25 == 0:
                manifest.save()  # an interrupted run resumes here; its years are stored on the next run
    manifest.save()

    # 4. store each changed year (from sc_from on) from every meeting parsed for it; a year with meetings parsed but
    # not yet stored (an interrupted run) is stored too
    changed |= {int(e["date"][:4]) for e in manifest.entries.values()
                if e.get("status") == "parsed" and e.get("stored") is False and e.get("date")}
    for year in sorted(y for y in changed if y >= start.year):
        rows, symbols = [], []
        for symbol, entry in manifest.entries.items():
            if entry.get("status") == "parsed" and (entry.get("date") or "")[:4] == str(year):
                rows += [r for r in undocs.load_parsed(settings, symbol) if r["date"] >= start]
                symbols.append(symbol)
        write_year(settings.parquet_dir, "sc", year, rows)
        for symbol in symbols:
            manifest.entries[symbol]["stored"] = True
        report.years_written[year] = len(rows)
    manifest.save()
    dates = [e["date"] for e in manifest.entries.values() if e.get("status") == "parsed" and e.get("date")]
    report.latest_meeting = max(dates, default=None)
    return {y for y in changed if y >= start.year}


def update_general_debate(settings: Settings, ref: Countries, docs: undocs.UNDocs, report: Report,
                          max_new: int | None = None, today: date | None = None,
                          sessions: range | None = None, website: gadebate.Website | None = None) -> set[int]:
    """Fetch, parse and store the General Debate meetings of each session from ``gd_from_session``; returns the years
    that changed. ``sessions``: earlier sessions too (``wcs backfill-replies``), for which only the rights of reply
    are stored, beside the corpus's statements."""
    manifest = undocs.Manifest.load(settings, "ga")
    first, last = gd_from_session(settings), unga.current_session(today)
    sessions = sessions or range(first, last + 1)
    todo: list[tuple[dict[str, Any], bool]] = []
    for session in sessions:
        entries = {s: e for s, e in manifest.entries.items() if unga.session_of(s) == session}
        concluded = any(e.get("concludes_debate") for e in entries.values())
        catalogue: dict[str, dict[str, Any]] = {}
        if not concluded:
            try:
                for meta in docs.search(f"191__a:A/{session}/PV.*"):
                    n = unga.meeting_number(meta["symbol"])
                    if n and n <= unga.GD_CATALOGUE_WINDOW and unga.session_of(meta["symbol"]) == session:
                        catalogue[meta["symbol"]] = meta
            except Exception as exc:
                report.warnings.append(f"Digital Library search failed for the General Assembly ({exc}); trying ODS")
        numbers = set(range(1, unga.GD_WINDOW + 1)) | {unga.meeting_number(s) or 0 for s in catalogue}
        for n in sorted(numbers):
            symbol = f"A/{session}/PV.{n}"
            meta, entry = catalogue.get(symbol), manifest.entries.get(symbol)
            if session < first and not catalogue and (entry or {}).get("status") is None:
                continue  # a past session the catalogue cannot list: not probed number by number
            if entry is not None and undocs.due_for_refetch(entry, meta):
                todo.append((meta or {"symbol": symbol, "verbatim": True}, True))
                continue
            if concluded:  # the debate is complete: only records that failed are tried again (from the cache)
                if entry is not None and entry.get("status") == "failed" and \
                        entry.get("attempts", 0) < undocs.MAX_ATTEMPTS:
                    todo.append((meta or {"symbol": symbol, "verbatim": True, "agenda": entry.get("agenda")}, False))
                continue
            if meta and meta.get("agenda") and not any("general debate" in a.lower() for a in meta["agenda"]):
                if entry is None:  # catalogued, on other business: not fetched
                    manifest.entries[symbol] = {"first_seen": undocs._now(), "status": "other", "agenda": meta["agenda"]}
                continue
            status = (entry or {}).get("status")
            if entry is None or status is None or \
                    (status == "missing" and undocs._days_since(entry.get("checked_at")) >= unga.CHECK_EVERY_DAYS and
                     undocs._days_since(entry.get("missing_since")) < unga.GIVE_UP_DAYS) or \
                    (status == "failed" and entry.get("attempts", 0) < undocs.MAX_ATTEMPTS):
                todo.append((meta or {"symbol": symbol, "verbatim": True}, False))
    if max_new is not None:
        todo = todo[:max_new]

    changed: set[int] = set()
    for meta, refetch in todo:
        symbol = meta["symbol"]
        try:
            records = undocs.process(settings, ref, manifest, docs, meta, force=refetch, body="ga")
        except Exception as exc:
            log.exception("failed on %s", symbol)
            manifest.entries.setdefault(symbol, {})["status"] = "failed"
            manifest.entries[symbol]["error"] = f"{type(exc).__name__}: {exc}"
            records = []
        entry = manifest.entries[symbol]
        entry["checked_at"] = undocs._now()
        if entry.get("status") == "parsed":
            (report.refetched if refetch else report.fetched).append(symbol)
            changed.update(r["year"] for r in records)
        elif entry.get("status") == "failed":
            report.failed.append(symbol)
        if entry.get("problems"):
            report.problems[symbol] = entry["problems"]
        manifest.save()

    for symbol, entry in list(manifest.entries.items()):
        if entry.get("status") == "parsed" and entry.get("parser_version", 0) < PARSER_VERSION:
            records = undocs.parse_cached(settings, ref, manifest, symbol, body="ga")
            changed.update(r["year"] for r in records)
            report.reparsed += 1
    changed |= {1945 + s for sym, e in manifest.entries.items() if e.get("status") == "parsed" and
                e.get("stored") is False and (s := unga.session_of(sym)) and s in sessions}

    # until a session's official records are complete, statements from the UN's General Debate website fill in
    def official(session: int) -> list[dict[str, Any]]:
        return [r for sym, e in manifest.entries.items() if e.get("status") == "parsed" and
                unga.session_of(sym) == session for r in undocs.load_parsed(settings, sym)]

    live = {s for s in sessions if s >= first and not any(
        e.get("concludes_debate") for sym, e in manifest.entries.items() if unga.session_of(sym) == s)}
    if live and settings.section("live").get("gd_website", True):
        try:
            got, problems = gadebate.update(settings, ref, live, site=website,
                                            skip={r["record_id"] for s in live for r in official(s)})
            changed |= {1945 + s for s in got}
            if problems:
                report.problems["gadebate.un.org"] = problems
        except gadebate.Blocked as exc:
            report.warnings.append(f"the General Debate website refused access ({exc}): statements will come from "
                                   "the official records, about a year after the debate")
        except Exception as exc:
            log.exception("General Debate website")
            report.warnings.append(f"General Debate website: {type(exc).__name__}: {exc}")

    # store each changed session's records (a session's debate falls in one year: September). Before
    # gd_from_session the corpus has the statements, so only the replies are stored, beside them.
    for year in sorted(changed):
        session = year - 1945
        rows, symbols, seen, from_website = [], [], set(), 0
        for symbol, entry in sorted(manifest.entries.items(), key=lambda kv: unga.meeting_number(kv[0]) or 0):
            if entry.get("status") == "parsed" and unga.session_of(symbol) == session:
                for r in undocs.load_parsed(settings, symbol):
                    if session < first and not unga.is_reply(r):
                        continue
                    rid, k = r["record_id"], 2
                    while r["record_id"] in seen:  # a country introduced twice keeps both statements
                        r["record_id"], k = f"{rid}:{k}", k + 1
                    seen.add(r["record_id"])
                    rows.append(r)
                symbols.append(symbol)
        if session < first:
            write_year(settings.parquet_dir, "gd", year, rows, keep=lambda r: not unga.is_reply(r))
        else:
            # the official record replaces the website's text, statement by statement
            for r in gadebate.load_records(settings, session):
                if r["record_id"] not in seen:
                    rows.append(r)
                    from_website += 1
            write_year(settings.parquet_dir, "gd", year, rows)
            gadebate.mark_stored(settings, {session})
        report.gd.setdefault(session, {})["website"] = from_website
        for symbol in symbols:
            manifest.entries[symbol]["stored"] = True
        report.years_written[year] = report.years_written.get(year, 0) + len(rows)
    manifest.save()
    for session in sessions:
        es = [e for s, e in manifest.entries.items() if unga.session_of(s) == session]
        report.gd[session] = {**report.gd.get(session, {}), "meetings": sum(e.get("status") == "parsed" for e in es),
                              "statements": sum(e.get("statements", e.get("speeches", 0)) for e in es
                                                if e.get("status") == "parsed"),
                              "replies": sum(e.get("replies", 0) for e in es if e.get("status") == "parsed"),
                              "concluded": any(e.get("concludes_debate") for e in es),
                              "awaiting": sum(e.get("status") == "missing" for e in es)}
    return changed


GD_OVERDUE_DAYS = 456  # the records are usually out within about a year of the debate


def health(report: Report, today: date | None = None) -> None:
    today = today or date.today()
    for session, info in report.gd.items():
        held = date(1945 + session, 9, 30)
        if not info["statements"] and (today - held).days > GD_OVERDUE_DAYS:
            report.warnings.append(f"no General Debate records yet for the {session}th session (September "
                                   f"{held.year}): check https://digitallibrary.un.org for A/{session}/PV records")
    if report.latest_meeting:
        age = (today - date.fromisoformat(report.latest_meeting)).days
        if age > STALE_DAYS:
            report.warnings.append(f"no Security Council record newer than {report.latest_meeting} ({age} days)")
    wording = [s for s, ps in report.problems.items() if any("typography" in p for p in ps)]
    if wording:
        report.warnings.append(f"{len(wording)} records parsed by wording, not typography: check the template")
    if report.failed:
        report.errors.append(f"{len(report.failed)} records could not be parsed: {', '.join(report.failed[:5])}")


def run(settings: Settings, *, do_votes: bool = True, do_sc: bool = True, do_gd: bool = True, do_index: bool = True,
        embed_seconds: float | None = None, max_new: int | None = None, docs: undocs.UNDocs | None = None,
        reference: Countries | None = None) -> Report:
    report = Report(started=datetime.now(UTC).isoformat(timespec="seconds"))
    ref = reference or countries()
    changed: dict[str, set[int]] = {"sc": set(), "gd": set()}
    if do_votes:
        try:
            update_votes(settings, report)
        except Exception as exc:
            log.exception("votes update failed")
            report.errors.append(f"voting data: {type(exc).__name__}: {exc}")
    if do_sc:
        try:
            docs = docs or undocs.UNDocs(settings)
            changed["sc"] = update_security_council(settings, ref, docs, report, max_new)
        except Exception as exc:
            log.exception("Security Council update failed")
            report.errors.append(f"Security Council: {type(exc).__name__}: {exc}")
    if do_gd:
        try:
            docs = docs or undocs.UNDocs(settings)
            changed["gd"] = update_general_debate(settings, ref, docs, report, max_new)
        except Exception as exc:
            log.exception("General Debate update failed")
            report.errors.append(f"General Debate: {type(exc).__name__}: {exc}")
    if do_index and (any(changed.values()) or embed_seconds):
        from wcs.index.build import Indexer

        idx = Indexer(settings, ref)
        for source, ys in changed.items():
            for year in sorted(ys):
                report.indexed += idx.index_year(source, year)
        if any(changed.values()):
            idx.index.build_indices()
        # passages still waiting for meaning search (a first load takes hours) are embedded on every run, changes
        # or not, within the time allowed
        if embed_seconds:
            from wcs.embed.base import get_embedder
            from wcs.index.build import embed_pending

            try:
                report.embedded = embed_pending(settings, idx.index, idx.state, get_embedder(settings),
                                                max_seconds=embed_seconds)
                idx.index.build_indices()
            except Exception as exc:  # keyword search still works; embedding resumes next run
                report.warnings.append(f"embedding: {type(exc).__name__}: {exc}")
    health(report)
    report.finished = datetime.now(UTC).isoformat(timespec="seconds")
    write_report(settings, report)
    return report


def write_report(settings: Settings, report: Report) -> Path:
    d = settings.data_dir / "logs"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"update-{re.sub(r'[^0-9T]', '', report.started)[:15]}.json"
    path.write_text(json.dumps(report.to_dict(), indent=1, default=str))
    return path
