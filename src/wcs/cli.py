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
            typer.echo(f"General Debate, session {session}: {info['statements']} statements from {info['meetings']} "
                       f"meetings{' (complete)' if info['concluded'] else ''}; {info['awaiting']} meetings not yet "
                       f"published")
        for w in report.warnings:
            typer.echo(f"warning: {w}")
        for e in report.errors:
            typer.echo(f"error: {e}")
    if not report.ok:
        raise typer.Exit(1)


@app.command("ingest-gd")
def ingest_gd(years: str = typer.Option(None)) -> None:
    """Load General Debate statements (from data/raw/ungd/) into the Parquet store (the sessions before [live]
    gd_from_session; later sessions come from `wcs update`)."""
    from wcs.reference.countries import countries
    from wcs.sources import ungd
    from wcs.store.parquet import write_by_year
    from wcs.update import gd_from_session

    s = load_settings()
    first = gd_from_session(s)
    counts = write_by_year(s.parquet_dir, "gd", (r for r in ungd.iter_records(s, countries(), _years(years))
                                                 if r["session"] < first))
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
