"""Keep the data current: one command for a scheduled job (``wcs update``).

Each run:

1. **Votes.** Reads the voting data record's file list; when the UN has published a new file, downloads and converts
   it.
2. **Security Council.** Finds meeting records newer than the last one held (the Digital Library's catalogue, then
   probing ODS for the following numbers), downloads and parses them, looks again for numbers still missing, fetches
   provisional records again when corrected versions may be out, and parses cached records again if the parser has
   changed. Records are stored for the years from ``[live] sc_from`` (the corpus keeps the years before).
3. **Index.** Re-indexes the years that changed, then embeds new passages for as long as allowed.
4. **Report.** Writes data/logs/update-<time>.json, and returns problems so a scheduler can alert on them.

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
from wcs.sources import undocs
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
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": self.ok}


def live_from(settings: Settings) -> date:
    return date.fromisoformat(settings.section("live").get("sc_from", "2024-01-01"))


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
    for symbol, entry in manifest.entries.items():
        if entry.get("status") == "parsed" and entry.get("parser_version", 0) < PARSER_VERSION:
            records = undocs.parse_cached(settings, ref, manifest, symbol)
            changed.update(r["year"] for r in records)
            report.reparsed += 1
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


def health(report: Report, today: date | None = None) -> None:
    today = today or date.today()
    if report.latest_meeting:
        age = (today - date.fromisoformat(report.latest_meeting)).days
        if age > STALE_DAYS:
            report.warnings.append(f"no Security Council record newer than {report.latest_meeting} ({age} days)")
    wording = [s for s, ps in report.problems.items() if any("typography" in p for p in ps)]
    if wording:
        report.warnings.append(f"{len(wording)} records parsed by wording, not typography: check the template")
    if report.failed:
        report.errors.append(f"{len(report.failed)} records could not be parsed: {', '.join(report.failed[:5])}")


def run(settings: Settings, *, do_votes: bool = True, do_sc: bool = True, do_index: bool = True,
        embed_seconds: float | None = None, max_new: int | None = None, docs: undocs.UNDocs | None = None,
        reference: Countries | None = None) -> Report:
    report = Report(started=datetime.now(UTC).isoformat(timespec="seconds"))
    ref = reference or countries()
    changed: set[int] = set()
    if do_votes:
        try:
            update_votes(settings, report)
        except Exception as exc:
            log.exception("votes update failed")
            report.errors.append(f"voting data: {type(exc).__name__}: {exc}")
    if do_sc:
        try:
            changed = update_security_council(settings, ref, docs or undocs.UNDocs(settings), report, max_new)
        except Exception as exc:
            log.exception("Security Council update failed")
            report.errors.append(f"Security Council: {type(exc).__name__}: {exc}")
    if do_index and changed:
        from wcs.index.build import Indexer

        idx = Indexer(settings, ref)
        for year in sorted(changed):
            report.indexed += idx.index_year("sc", year)
        idx.index.build_indices()
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
