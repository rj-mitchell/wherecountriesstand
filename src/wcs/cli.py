"""Command line: fetch and load the data, build the index, search and ask.

    wcs fetch-sc && wcs ingest-sc          # Security Council debates, 1992-2023
    wcs ingest-gd                          # General Debate (download by hand first: docs/DATA.md)
    wcs fetch-votes && wcs ingest-votes    # General Assembly votes
    wcs index && wcs embed                 # keyword index, then meaning search (slow; resumable)
    wcs update                             # new votes, Security Council and General Debate records (scheduled)
    wcs search "climate finance" --group sids
    wcs ask "How have small island States spoken about climate change since 2015?"
"""

from __future__ import annotations

import json
import logging
from datetime import date

import typer

from wcs.config import load_settings

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _years(spec: str | None) -> set[int] | None:
    """"2015" or "2010-2015" or "2001,2005"."""
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out.update(range(int(a), int(b or a) + 1))
    return out


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000,
          behind_proxy: bool = typer.Option(False, help="trust X-Forwarded-For (behind Caddy or another proxy)")) -> None:
    """Run the site and its API."""
    import uvicorn

    from wcs.api.app import create_app

    # requests are not logged: the privacy notice (web/site/privacy.html) says so
    uvicorn.run(create_app(load_settings(), behind_proxy=behind_proxy), host=host, port=port, access_log=False,
                proxy_headers=behind_proxy, forwarded_allow_ips="*" if behind_proxy else None)


@app.command()
def bootstrap(embed_hours: float = typer.Option(0, help="embed for up to this long at the end (0: leave it to the "
                                                         "scheduled updates)"),
              replies_since: int = typer.Option(55, help="first General Debate session for rights of reply")) -> None:
    """First load on a new server, in order, skipping whatever is already there (so it can be run again): the
    voting data, the Security Council corpus, the General Debate corpus (if downloaded by hand), the UN's records
    since the corpora, rights of reply, the keyword index, and meaning search."""
    from wcs.index.build import SOURCES, Indexer
    from wcs.reference.countries import countries
    from wcs.sources import undocs, ungd, unsc, votes
    from wcs.store.parquet import votes_path, write_by_year, years
    from wcs.update import Report, gd_from_session, live_from, run, update_general_debate

    s = load_settings()
    ref = countries()
    if not votes_path(s.parquet_dir).exists():
        typer.echo("General Assembly votes: downloading and converting")
        votes.ingest(s, votes.fetch(s))
    start = live_from(s)
    if not [y for y in years(s.parquet_dir, "sc") if y < start.year]:
        typer.echo("Security Council corpus: downloading (about 510 MB) and loading")
        unsc.fetch(s)
        write_by_year(s.parquet_dir, "sc", (r for r in unsc.iter_records(s, ref) if r["date"] < start))
    if ungd.archive_path(s) and not [y for y in years(s.parquet_dir, "gd") if y < 1945 + gd_from_session(s)
                                     and y < 2000]:
        typer.echo("General Debate corpus: loading")
        from wcs.sources.unga import is_reply

        first = gd_from_session(s)
        write_by_year(s.parquet_dir, "gd", (r for r in ungd.iter_records(s, ref) if r["session"] < first),
                      keep=is_reply)
    elif not ungd.archive_path(s):
        typer.echo(f"General Debate corpus: not downloaded (put it in {ungd.raw_dir(s)}: see docs/DATA.md); "
                   "carrying on without it")
    typer.echo("The UN's records since the corpora (first time: several hours, resumable)")
    report = run(s, do_votes=False, do_index=False, reference=ref)
    typer.echo(f"  {len(report.fetched)} records fetched; latest meeting {report.latest_meeting}")
    typer.echo("Rights of reply in past General Debates")
    update_general_debate(s, ref, undocs.UNDocs(s), Report(started="bootstrap"),
                          sessions=range(replies_since, gd_from_session(s)))
    typer.echo("Keyword index")
    n = Indexer(s, ref).index_all(SOURCES)
    typer.echo(f"  {n} passages")
    if embed_hours:
        run(s, do_votes=False, do_sc=False, do_gd=False, embed_seconds=embed_hours * 3600, reference=ref)
    typer.echo("Done. Meaning search fills in over the next scheduled updates (`wcs update --embed-seconds`).")


@app.command()
def evaluate(with_model: bool = typer.Option(False, help="have the model read each question (needs an API key)"),
             only: list[str] = typer.Option(None, help="question ids"),
             save: bool = typer.Option(True, help="save the results under eval/runs/")) -> None:
    """Score the site against the test set (eval/questions.toml): how each question is read, and the evidence."""
    from wcs import evaluate as ev

    results = ev.run(load_settings(), with_model, set(only) if only else None)
    for r in results:
        mark = "ok " if r.plan_ok and r.evidence_ok is not False else "-- "
        typer.echo(f"{mark}{r.id:28} {r.records:3} sources {r.votes} votes  " + "; ".join(r.problems))
    s = ev.summary(results)
    typer.echo(f"\nquestions read right: {s['plans_right']}/{s['questions']} (filters {s['fields_right']}/"
               f"{s['fields']}); evidence right: {s['evidence_right']}/{s['evidence_checked']}")
    for cat, c in s["by_category"].items():
        typer.echo(f"  {cat:10} {c['plans_right']}/{c['questions']}")
    if save:
        typer.echo(f"saved {ev.save(results, with_model)}")


@app.command("fetch-sc")
def fetch_sc(force: bool = False) -> None:
    """Download the Security Council debates corpus (about 510 MB)."""
    from wcs.sources import unsc

    for p in unsc.fetch(load_settings(), force=force):
        typer.echo(p)


@app.command("ingest-sc")
def ingest_sc(years: str = typer.Option(None, help="e.g. 2015 or 2010-2015")) -> None:
    """Load Security Council speeches from the corpus into the Parquet store (the years before [live] sc_from;
    later years come from `wcs update`)."""
    from wcs.reference.countries import countries
    from wcs.sources import unsc
    from wcs.store.parquet import write_by_year
    from wcs.update import live_from

    s = load_settings()
    start = live_from(s)
    counts = write_by_year(s.parquet_dir, "sc", (r for r in unsc.iter_records(s, countries(), _years(years))
                                                 if r["date"] < start))
    typer.echo(f"{sum(counts.values())} speeches in {len(counts)} years")


@app.command()
def update(votes: bool = typer.Option(True, help="check the voting data"),
           sc: bool = typer.Option(True, help="check for new Security Council records"),
           gd: bool = typer.Option(True, help="check for new General Debate records"),
           index: bool = typer.Option(True, help="re-index the years that changed"),
           embed_seconds: float = typer.Option(600, help="embed new passages for up to this long (0: none)"),
           max_new: int = typer.Option(None, help="process at most this many records (for a first, partial run)"),
           as_json: bool = typer.Option(False, "--json")) -> None:
    """Bring the data up to date: new votes, new and corrected Security Council and General Debate records, the
    index. For a
    scheduled job: exits 1 when something needs attention (see the report in data/logs/)."""
    from wcs.update import run

    report = run(load_settings(), do_votes=votes, do_sc=sc, do_gd=gd, do_index=index, embed_seconds=embed_seconds or None,
                 max_new=max_new)
    if as_json:
        typer.echo(json.dumps(report.to_dict(), default=str, indent=1))
    else:
        typer.echo(f"new records: {len(report.fetched)}; corrected: {len(report.refetched)}; reparsed: "
                   f"{report.reparsed}; communiqués: {len(report.communiques)}; not yet available: "
                   f"{len(report.missing)}; failed: {len(report.failed)}")
        typer.echo(f"latest meeting: {report.latest_meeting}; years written: {report.years_written}; "
                   f"passages indexed: {report.indexed}; embedded: {report.embedded}")
        if report.votes:
            typer.echo(f"votes: {report.votes}")
        for session, info in report.gd.items():
            typer.echo(f"General Debate, session {session}: {info['statements']} statements and {info['replies']} "
                       f"rights of reply from {info['meetings']} meetings{' (complete)' if info['concluded'] else ''}; "
                       f"{info['awaiting']} meetings not yet published")
        for w in report.warnings:
            typer.echo(f"warning: {w}")
        for e in report.errors:
            typer.echo(f"error: {e}")
    if not report.ok:
        raise typer.Exit(1)


@app.command()
def usage(days: int = typer.Option(7, help="list daily spend and failures for this many days"),
          as_json: bool = typer.Option(False, "--json")) -> None:
    """Spending on briefings today and this month against the caps, and briefings that failed (from data/state.db).
    Exits 1 when spending is at 80% of a cap or more, or a briefing failed in the last day."""
    from wcs.api.usage import StateDB, summary, warnings

    s = load_settings()
    path = s.data_dir / "state.db"
    if not path.exists():
        typer.echo(f"no spending recorded yet ({path} does not exist)")
        return
    report = summary(StateDB(path), s.usage, days=days)
    alerts = warnings(report)
    if as_json:
        typer.echo(json.dumps({**report, "warnings": alerts}, indent=1))
    else:
        for name, label in (("today", "Today (UTC)"), ("month", "This month")):
            r = report[name]
            share = f" ({r['spent_usd'] / r['cap_usd']:.0%})" if r["cap_usd"] else ""
            typer.echo(f"{label}: ${r['spent_usd']:.2f} of ${r['cap_usd']:.2f}{share}; briefings written: "
                       f"{r['briefings']}; visitors who asked: {r['visitors']}")
        typer.echo(f"\nLast {days} days:")
        for d in report["by_day"]:
            typer.echo(f"  {d['day']}  ${d['spent_usd']:.2f}  briefings: {d['briefings']}")
        typer.echo(f"\nFailures in the last {days} days: {len(report['failures'])}")
        for f in report["failures"][:20]:
            typer.echo(f"  {f['at']}  {f['kind']}: {f['error']}\n      question: {f['question']}")
        for w in alerts:
            typer.echo(f"warning: {w}")
    if alerts:
        raise typer.Exit(1)


@app.command("backfill-replies")
def backfill_replies(since: int = typer.Option(..., help="first session, e.g. 55 (2000)"),
                     until: int = typer.Option(None, help="last session (default: the one before [live] "
                                                          "gd_from_session)")) -> None:
    """Add the General Debate's rights of reply for past sessions, from the UN's records (the corpus has only the
    statements). Resumable; records without a text layer (scanned, before the late 1990s) are skipped."""
    from wcs.reference.countries import countries
    from wcs.sources import undocs
    from wcs.update import Report, gd_from_session, update_general_debate

    s = load_settings()
    last = until or gd_from_session(s) - 1
    report = Report(started="backfill")
    changed = update_general_debate(s, countries(), undocs.UNDocs(s), report, sessions=range(since, last + 1))
    for session, info in report.gd.items():
        typer.echo(f"session {session}: {info['replies']} rights of reply from {info['meetings']} meetings")
    typer.echo(f"years written: {sorted(changed)}; run `wcs index --source gd` to index them")
    if report.failed:
        typer.echo(f"could not parse: {', '.join(report.failed)}")


@app.command("ingest-gd")
def ingest_gd(years: str = typer.Option(None)) -> None:
    """Load General Debate statements (from data/raw/ungd/) into the Parquet store (the sessions before [live]
    gd_from_session; later sessions come from `wcs update`)."""
    from wcs.reference.countries import countries
    from wcs.sources import ungd
    from wcs.sources.unga import is_reply
    from wcs.store.parquet import write_by_year
    from wcs.update import gd_from_session

    s = load_settings()
    first = gd_from_session(s)
    # rights of reply, added from the UN's records (`wcs backfill-replies`), are kept
    counts = write_by_year(s.parquet_dir, "gd", (r for r in ungd.iter_records(s, countries(), _years(years))
                                                 if r["session"] < first), keep=is_reply)
    typer.echo(f"{sum(counts.values())} statements in {len(counts)} years")


@app.command("fetch-votes")
def fetch_votes(force: bool = False) -> None:
    """Download the General Assembly voting data."""
    from wcs.sources import votes

    typer.echo(votes.fetch(load_settings(), force=force))


@app.command("ingest-votes")
def ingest_votes() -> None:
    """Convert the voting data to Parquet."""
    from wcs.sources import votes

    typer.echo(f"{votes.ingest(load_settings())} votes")


@app.command()
def index(years: str = typer.Option(None), source: list[str] = typer.Option(None)) -> None:
    """Build the keyword index from the Parquet store."""
    from wcs.index.build import SOURCES, Indexer
    from wcs.reference.countries import countries

    s = load_settings()
    n = Indexer(s, countries()).index_all(tuple(source) if source else SOURCES, _years(years))
    typer.echo(f"{n} passages indexed")


@app.command()
def regroup() -> None:
    """Update each passage's groups after the reference data changes (memberships.csv, groups.csv), without
    re-indexing or re-embedding."""
    from wcs.index.build import regroup as run_regroup
    from wcs.reference.countries import countries

    for table, n in run_regroup(load_settings(), countries()).items():
        typer.echo(f"{table}: {n} passages regrouped")


@app.command()
def embed(max_seconds: float = typer.Option(None, help="stop after about this long (resumable)")) -> None:
    """Embed indexed passages for meaning search, newest years first."""
    from wcs.embed.base import get_embedder
    from wcs.index.build import State, embed_pending
    from wcs.index.lance import SearchIndex

    s = load_settings()
    idx = SearchIndex(s.index_dir, int(s.embedding.get("dim", 384)))
    n = embed_pending(s, idx, State(s.index_dir / "state.json"), get_embedder(s), max_seconds=max_seconds)
    idx.build_indices()
    typer.echo(f"{n} passages embedded")


@app.command()
def stats() -> None:
    """What is loaded and indexed."""
    from wcs.index.lance import SearchIndex
    from wcs.store.parquet import votes_path, years

    s = load_settings()
    for src in ("sc", "gd"):
        ys = years(s.parquet_dir, src)
        typer.echo(f"{src}: {len(ys)} years" + (f" ({ys[0]}-{ys[-1]})" if ys else ""))
    typer.echo(f"votes: {'loaded' if votes_path(s.parquet_dir).exists() else 'not loaded'}")
    typer.echo(f"index: {SearchIndex(s.index_dir, int(s.embedding.get('dim', 384))).stats()}")


@app.command()
def search(query: str, country: list[str] = typer.Option(None), group: list[str] = typer.Option(None),
           source: list[str] = typer.Option(None), since: int = typer.Option(None), until: int = typer.Option(None),
           limit: int = 12, group_by: str = "region", as_json: bool = typer.Option(False, "--json")) -> None:
    """Search speeches (no model call): balanced evidence for a question."""
    from wcs.engine import Engine, Filters

    s = load_settings()
    eng = Engine(s)
    codes = [eng.countries.resolve(c) or c for c in country or []]
    f = Filters(date_from=date(since, 1, 1) if since else None, date_to=date(until, 12, 31) if until else None,
                sources=list(source or []), countries=codes, groups=list(group or []))
    res = eng.search(query, f, limit=limit, group_by=group_by)
    if as_json:
        typer.echo(json.dumps({"evidence": res.evidence, "records": res.records}, default=str, indent=1))
        return
    typer.echo(f"{res.candidates} candidates; meaning search {'on' if res.semantic_available else 'off'}")
    typer.echo(json.dumps(res.evidence, default=str))
    for r in res.records:
        who = r["country_name"] + (f" for the {r['on_behalf_of']}" if r.get("on_behalf_of") else "")
        typer.echo(f"\n[{r['ranking']['score']:.3f}] {r['date']} {r.get('symbol') or r['meeting']} — {who} "
                   f"({r.get('speaker_name') or 'unnamed'})")
        typer.echo("   " + " ".join(r["excerpt"].split())[:300])


@app.command()
def ask(question: str, mode: str = "summary", as_json: bool = typer.Option(False, "--json")) -> None:
    """Ask a question: plan, evidence and (with an API key) a cited briefing."""
    from wcs.rag.pipeline import AskRequest, Pipeline, answer_once

    out = answer_once(Pipeline(load_settings()), AskRequest(question, mode))
    if as_json:
        typer.echo(json.dumps(out, default=str, indent=1))
        return
    typer.echo(out["plan"]["plan"]["interpretation"])
    for src in out["sources"]["sources"]:
        typer.echo(f"[Source {src['n']}] {src['title']}")
    final = out.get("final") or {}
    if final.get("answer"):
        typer.echo("\n" + final["answer"]["markdown"])
    elif final.get("note"):
        typer.echo("\n" + final["note"])


if __name__ == "__main__":
    app()
