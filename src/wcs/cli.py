"""Command line: fetch and load the data, build the index, search and ask.

    wcs fetch-sc && wcs ingest-sc          # Security Council debates, 1992-2023
    wcs ingest-gd                          # General Debate (download by hand first: docs/DATA.md)
    wcs fetch-votes && wcs ingest-votes    # General Assembly votes
    wcs index && wcs embed                 # keyword index, then meaning search (slow; resumable)
    wcs update                             # new votes, Security Council and General Debate records (scheduled)
    wcs backup --to /backups               # copy the state database (scheduled)
    wcs restore /backups/wcs-state-<day>.sqlite.gz   # put a backup back (with the site stopped)
    wcs reports && wcs statements          # problem reports, and statements under the right of reply
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
              replies_since: int = typer.Option(48, help="first General Debate session for rights of reply")) -> None:
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
    if not ungd.archive_path(s):
        try:
            typer.echo("General Debate corpus: downloading (about 71 MB)")
            ungd.fetch(s)
        except Exception as exc:
            typer.echo(f"  could not download it: {exc}")
    if ungd.archive_path(s) and not [y for y in years(s.parquet_dir, "gd") if y < 1945 + gd_from_session(s)
                                     and y < 1993]:
        typer.echo("General Debate corpus: loading")
        from wcs.sources.unga import is_reply

        first = gd_from_session(s)
        write_by_year(s.parquet_dir, "gd", (r for r in ungd.iter_records(s, ref) if r["session"] < first),
                      keep=is_reply)
    elif not ungd.archive_path(s):
        typer.echo(f"General Debate corpus: not there (put it in {ungd.raw_dir(s)}: see docs/DATA.md); "
                   "carrying on without it")
    typer.echo("The UN's records since the corpora (first time: several hours, resumable)")
    report = run(s, do_votes=False, do_index=False, reference=ref)
    typer.echo(f"  {len(report.fetched)} records fetched; latest meeting {report.latest_meeting}")
    typer.echo("Rights of reply in past General Debates")
    update_general_debate(s, ref, undocs.UNDocs(s), Report(started="bootstrap"),
                          sessions=range(replies_since, gd_from_session(s)))
    typer.echo(f"  languages of {ungd.add_languages(s, gd_from_session(s))} corpus statements, from the UN's records")
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
        for b in report.backups:
            typer.echo(f"{b['level']}: backup: {b['text']}")
    if report.needs_attention:
        raise typer.Exit(1)


@app.command()
def backup(to: str = typer.Option(..., help="folder for the backups"),
           keep: int = typer.Option(14, help="how many to keep")) -> None:
    """Copy the state database (spending, saved briefings, reports): the one thing that cannot be rebuilt. The copy
    also goes to a Backblaze B2 bucket when one is set up (WCS_B2_* in deploy/.env)."""
    from pathlib import Path

    from wcs.backup import make_backup
    from wcs.offsite import OffsiteError, send

    out = make_backup(load_settings().data_dir / "state.db", Path(to), keep)
    if not out:
        typer.echo("nothing to back up yet (no state database)")
        return
    typer.echo(f"backup: {out}")
    try:
        sent = send(out)
    except OffsiteError as exc:
        typer.echo(f"The off-site copy failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("Off-site copy: " + (f"stored in {sent['bucket']} ({sent['stored']} kept there)"
                                    if sent.get("ok") else sent["offsite"]))


@app.command()
def restore(archive: str = typer.Argument(..., help="a backup: /backups/wcs-state-YYYY-MM-DD.sqlite.gz"),
            yes: bool = typer.Option(False, "--yes", help="don't ask first")) -> None:
    """Put a backup of the state database back (spending, saved briefings, reports). Stop the site first
    (docs/DEPLOY.md, "Backups"). The backup is checked before anything is replaced, and the database it replaces is
    kept as data/state.db.before-restore. A kept copy from an earlier restore is never written over: move or delete
    it first."""
    from pathlib import Path

    from wcs.backup import restore_backup

    state_db = load_settings().data_dir / "state.db"
    if not yes and not typer.confirm(f"Replace {state_db} with {archive}? Stop the site first."):
        raise typer.Exit(1)
    try:
        kept = restore_backup(Path(archive), state_db)
    except (OSError, RuntimeError) as exc:
        typer.echo(f"Not restored: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"restored {state_db} from {archive}" + (f"; the database it replaced is {kept}" if kept else ""))


@app.command()
def offsite() -> None:
    """Check the off-site backup bucket: whether the key works, and how many of this site's backups are there."""
    from wcs.offsite import check

    typer.echo(json.dumps(check(), indent=2))


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


def _done(ok: bool, done: str, failed: str) -> None:
    """Say what an owner's command on reports, statements or briefings did. One that did nothing (no such number, or
    not in a state to be changed) exits 1, as the sister sites' commands do, so it cannot be taken for done."""
    typer.echo(done if ok else failed)
    if not ok:
        raise typer.Exit(1)


@app.command("reports")
def list_reports(all_: bool = typer.Option(False, "--all", help="include reports already dealt with")) -> None:
    """Problems and takedown requests sent through the site's form (newest first)."""
    from wcs.api.usage import StateDB, reports

    for r in reports(StateDB(load_settings().data_dir / "state.db"), open_only=not all_):
        typer.echo(f"#{r['id']}  {r['at']}  {r['kind']}  {r['page'] or '-'}{'  (closed)' if r['closed_at'] else ''}")
        typer.echo(f"    {r['message']}")
        if r["contact"]:
            typer.echo(f"    reply to: {r['contact']}")


@app.command("close-report")
def close_report_cmd(report_id: int) -> None:
    """Mark a report dealt with. Its contact address is deleted (the privacy notice says so)."""
    from wcs.api.usage import StateDB, close_report

    ok = close_report(StateDB(load_settings().data_dir / "state.db"), report_id)
    _done(ok, "closed", "no open report with that number")


def _statement_lines(st: dict) -> list[str]:
    from wcs.api.statements import word_count

    lines = [f"#{st['id']}  {st['at']}  {st['status']}  {st['page']}"
             + (f"  (speaks for {st['country']})" if st["country"] else ""),
             f"    from: {st['name']}, {st['role']}"]
    if st["email"]:
        lines.append(f"    confirm with: {st['email']}")
    if st["link"]:
        lines.append(f"    record: {st['link']}")
    lines += [f"    {para}" for para in st["text"].splitlines() if para.strip()]
    # the owner's note of how the sender was confirmed, from the log's entry for the publishing
    lines += [f"    sender confirmed: {e['note']}" for e in st["log"] if e["action"] == "published" and e.get("note")]
    lines.append(f"    ({word_count(st['text'])} words"
                 + (f"; shown from {st['shown_on']}" if st["status"] == "published" else "")
                 + (f"; {st['status']}: {st['reason']}" if st["reason"] else "") + ")")
    return lines


def _statements_state():
    """The state database, with the confirming addresses of statements that ended three months ago deleted first
    (the privacy notice)."""
    from wcs.api.statements import forget_emails
    from wcs.api.usage import StateDB

    state = StateDB(load_settings().data_dir / "state.db")
    forget_emails(state)
    return state


def _country(code: str | None) -> str | None:
    from wcs.api.statements import check_country

    try:
        return check_country(code)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command("statements")
def list_statements(all_: bool = typer.Option(False, "--all", help="include declined and withdrawn statements")) -> None:
    """Statements under the right of reply sent through the site, newest first: those waiting for a decision and
    those shown. Confirm who sent one (reply to its address, ideally a mission's or government's) before publishing."""
    from wcs.api.statements import statements

    for st in statements(_statements_state(), open_only=not all_):
        typer.echo("\n".join(_statement_lines(st)))


# the same command under Where Councils Stand's name for it, so the owner's habit works on either site
app.command("list-statements", hidden=True)(list_statements)


@app.command("publish-statement")
def publish_statement_cmd(statement_id: int, checked: str = typer.Option(
        ..., help="how you confirmed who sent it, for the log (never shown on the site), e.g. 'Reply from the "
                  "mission's published address, 14 November.' Do not put the sender's email address in it: the "
                  "privacy notice promises that address is deleted"),
        country: str = typer.Option(
        None, help="the country it speaks for (ISO alpha-3, e.g. KEN), for that country's page once there is one")) \
        -> None:
    """Show a waiting statement beside its briefing, dated today, as written (never edited), once you have confirmed
    who sent it: --checked says how."""
    from wcs.api.statements import publish_statement

    try:
        ok = publish_statement(_statements_state(), statement_id, _country(country), checked=checked)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _done(ok, "published", "no waiting statement with that number")


@app.command("decline-statement")
def decline_statement_cmd(statement_id: int, reason: str = typer.Option(
        ..., help="for the author and the log, e.g. 'It is about another delegation's conduct.'")) -> None:
    """Decline a waiting statement, with the reason (tell its author). Its address is deleted three months later."""
    from wcs.api.statements import decline_statement

    ok = decline_statement(_statements_state(), statement_id, reason)
    _done(ok, "declined", "no waiting statement with that number")


@app.command("update-statement")
def update_statement_cmd(statement_id: int, text: str = typer.Option(None, help="the author's new wording, as sent"),
                         text_file: str = typer.Option(None, help="the new wording, from a file"),
                         name: str = typer.Option(None), role: str = typer.Option(None),
                         link: str = typer.Option(None, help="the link to the record ('' for none)"),
                         country: str = typer.Option(None, help="the country it speaks for (ISO alpha-3)")) -> None:
    """Change a statement at its author's request (confirmed from their address). A published one with new words is
    dated afresh."""
    from pathlib import Path

    from wcs.api.statements import check_line, check_link, check_text, update_statement

    try:
        new_text = check_text(Path(text_file).read_text(encoding="utf-8") if text_file else text) \
            if (text or text_file) else None
        new_name = check_line(name, "a name") if name is not None else None
        new_role = check_line(role, "a role") if role is not None else None
        new_link = check_link(link) if link is not None else None
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    ok = update_statement(_statements_state(), statement_id, text=new_text, name=new_name, role=new_role,
                          link=new_link, country=_country(country))
    _done(ok, "updated", "nothing changed: no waiting or published statement with that number, or no change")


@app.command("withdraw-statement")
def withdraw_statement_cmd(statement_id: int, reason: str = typer.Option(
        "Withdrawn by its author.", help="if the site removes it, why (policy 7.2: say why)")) -> None:
    """Take a statement down, at its author's request or because it breaks the rules. Its address is deleted three
    months later."""
    from wcs.api.statements import withdraw_statement

    ok = withdraw_statement(_statements_state(), statement_id, reason)
    _done(ok, "withdrawn", "no waiting or published statement with that number")


@app.command()
def withdraw(briefing_id: str, reason: str = typer.Option(..., help="shown at its address and on the corrections "
                                                                   "page, e.g. 'It misattributed a statement.'")) -> None:
    """Withdraw a saved briefing (/b/<id>): its address then says why, and the corrections page lists it."""
    from wcs.api.usage import StateDB, withdraw_briefing

    ok = withdraw_briefing(StateDB(load_settings().data_dir / "state.db"), briefing_id, reason)
    _done(ok, "withdrawn", "no briefing with that id")


@app.command("gd-website")
def gd_website(now: bool = typer.Option(False, "--now", help="also let the next `wcs update` read the website "
                                                            "straight away, rather than after 20 hours")) -> None:
    """Check the UN's General Debate website from here: whether it answers, and how many statements its sitemap
    lists for the sessions whose official records aren't complete. Prints what came back if it refuses."""
    from datetime import date

    from wcs.sources import gadebate, unga
    from wcs.update import gd_from_session

    s = load_settings()
    sessions = set(range(gd_from_session(s), unga.current_session(date.today()) + 1))
    site = gadebate.Website(pause=1)
    try:
        r = site.get(f"{gadebate.SITE}/sitemap.xml")
        typer.echo(f"sitemap: {r.status_code} {r.headers.get('content-type')}, {len(r.text)} bytes")
        index = gadebate._sitemap(site, f"{gadebate.SITE}/sitemap.xml")
        pages: dict[str, str] = {}
        for sub in gadebate.sitemap_pages(index) or [f"{gadebate.SITE}/sitemap.xml"]:
            text = gadebate._sitemap(site, sub) if sub != f"{gadebate.SITE}/sitemap.xml" else index
            pages.update(gadebate.sitemap_statements(text, sessions))
        for session in sorted(sessions):
            listed = [u for u in pages if u.split("/")[4] == str(session)]
            typer.echo(f"session {session}: {len(listed)} statements listed")
        if pages:
            url = sorted(pages)[-1]
            info = gadebate.parse_page(site.get(url).text, url)
            typer.echo(f"one page ({url}): speaker {info.get('label')!r}, English text: "
                       f"{'yes' if 'english' in info['files'] else 'no'}, transcript: "
                       f"{'yes' if info.get('transcript') else 'no'}")
    except gadebate.Blocked as exc:
        typer.echo(f"REFUSED: {exc}")
    except Exception as exc:
        typer.echo(f"FAILED: {type(exc).__name__}: {exc}")
    typer.echo(f"last reading by the update: {json.dumps(gadebate.last_check(s, sessions), default=str)}")
    if now:
        state = gadebate.load_state(s)
        state.pop("sitemap_checked", None)
        gadebate.save_state(s, state)
        typer.echo("the next `wcs update` will read the website")


@app.command("import-gd-website")
def import_gd_website(sources: list[str] = typer.Argument(..., help="records-<session>.json files: paths or web "
                                                                    "addresses")) -> None:
    """Load General Debate website statements read elsewhere, for a server the website refuses (docs/OPERATIONS.md).
    Then run `wcs update --no-votes --no-sc` to store and index them."""
    from wcs.sources import gadebate

    for session, n in sorted(gadebate.import_records(load_settings(), sources).items()):
        typer.echo(f"session {session}: {n} statements imported")
    typer.echo("now run: wcs update --no-votes --no-sc")


@app.command("backfill-replies")
def backfill_replies(since: int = typer.Option(..., help="first session, e.g. 48 (1993)"),
                     until: int = typer.Option(None, help="last session (default: the one before [live] "
                                                          "gd_from_session)")) -> None:
    """Add the General Debate's rights of reply for past sessions, from the UN's records (the corpus has only the
    statements), and the languages of the corpus's statements. Resumable; records without a text layer (scanned,
    before the late 1990s) are skipped."""
    from wcs.reference.countries import countries
    from wcs.sources import undocs
    from wcs.update import Report, gd_from_session, update_general_debate

    s = load_settings()
    last = until or gd_from_session(s) - 1
    report = Report(started="backfill")
    changed = update_general_debate(s, countries(), undocs.UNDocs(s), report, sessions=range(since, last + 1))
    for session, info in report.gd.items():
        typer.echo(f"session {session}: {info['replies']} rights of reply from {info['meetings']} meetings")
    from wcs.sources import ungd

    typer.echo(f"languages of {ungd.add_languages(s, gd_from_session(s))} corpus statements, from the UN's records")
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
    typer.echo(f"{sum(counts.values())} statements in {len(counts)} years; languages from the UN's records: "
               f"{ungd.add_languages(s, first)}")


@app.command("fetch-gd")
def fetch_gd(force: bool = False) -> None:
    """Download the General Debate corpus (latest version) from Harvard Dataverse, making the guestbook entry it asks
    for (WCS_GUESTBOOK_NAME and WCS_GUESTBOOK_EMAIL)."""
    from wcs.sources import ungd

    for path in ungd.fetch(load_settings(), force=force):
        typer.echo(f"{path} ({path.stat().st_size // 1_000_000} MB)")


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
def index(years: str = typer.Option(None), source: list[str] = typer.Option(None),
          re_embed: bool = typer.Option(False, help="drop the vectors too, so every passage is embedded again (after "
                                                    "changing the embedding model)")) -> None:
    """Build the keyword index from the Parquet store, and the search indices afresh. Passages whose text is
    unchanged keep their vectors."""
    from wcs.index.build import SOURCES, Indexer
    from wcs.reference.countries import countries

    s = load_settings()
    n = Indexer(s, countries(), keep_vectors=not re_embed).index_all(tuple(source) if source else SOURCES,
                                                                      _years(years))
    typer.echo(f"{n} passages indexed")


@app.command()
def optimize() -> None:
    """Merge rows waiting outside the search indices, compact small files, and delete versions of the index over an
    hour old (each update does this itself; this is for a one-off clean-up)."""
    from wcs.index.lance import SearchIndex

    s = load_settings()
    idx = SearchIndex(s.index_dir, int(s.embedding.get("dim", 384)))
    before = idx.versions()
    idx.optimize()
    typer.echo(f"Done: table versions {before} -> {idx.versions()}")


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
    from wcs.index.build import State, embed_pending, refresh_indices
    from wcs.index.lance import SearchIndex

    s = load_settings()
    idx = SearchIndex(s.index_dir, int(s.embedding.get("dim", 384)))
    state = State(s.index_dir / "state.json")
    n = embed_pending(s, idx, state, get_embedder(s), max_seconds=max_seconds)
    if n:
        refresh_indices(idx, state)
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
    elif out.get("error"):
        typer.echo("\n" + out["error"]["message"])


if __name__ == "__main__":
    app()
