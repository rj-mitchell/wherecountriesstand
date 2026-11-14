"""Command line: fetch and load the data, build the index, search and ask.

    wcs fetch-sc && wcs ingest-sc          # Security Council debates, 1992-2023
    wcs ingest-gd                          # General Debate (download by hand first: docs/DATA.md)
    wcs fetch-votes && wcs ingest-votes    # General Assembly votes
    wcs index && wcs embed                 # keyword index, then meaning search (slow; resumable)
    wcs update                             # new votes, Security Council and General Debate records (scheduled)
    wcs backup --to /backups               # copy the state database (scheduled)
    wcs restore /backups/wcs-state-<day>.sqlite.gz   # put a backup back (with the site stopped)
    wcs reports && wcs statements          # problem reports, and statements under the right of reply
    wcs waiting                            # what is waiting for a decision, and for how long
    wcs search "climate finance" --group sids
    wcs ask "How have small island States spoken about climate change since 2015?"
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import date
from typing import Any

import typer
from wts_core.cli import (
    BackupWords,
    OpsWording,
    RepliesCommands,
    RepliesWording,
    RestoreWords,
    ops_commands,
    replies_commands,
)
from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.replies import Replies, word_count
from wts_core.state import StateDB
from wts_core.usage import BriefingArchive, token_id

from wcs.config import SITE, Settings, load_settings
from wcs.state import BACKUPS

app = typer.Typer(add_completion=False, no_args_is_help=True)
# the clock the owner's commands read: a test stands its own in here
CLOCK: Clock = SYSTEM_CLOCK


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

    from wcs.api.app import create_app, start_logging

    settings = load_settings()
    # Requests are not logged: the privacy notice (web/site/privacy.html) says so. What is logged is the site's own
    # work on each briefing (the stages it reached and how it ended), with nothing of the reader's in it.
    start_logging(settings)
    uvicorn.run(create_app(settings, behind_proxy=behind_proxy), host=host, port=port, access_log=False,
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


# ---- backups (wts-core, ``wts_core.cli.ops_commands``): `wcs backup`, `wcs restore`, `wcs offsite` ----------------
#
# The copy, the checks before a restore and the off-site store are the Where They Stand sites' shared ones
# (``wts_core.ops``); what the backups are called and hold is ``wcs.state.BACKUPS``, and every line printed is this
# site's, as it was.

OPS_WORDING = OpsWording(
    backup=BackupWords(
        folder=None, keep=BACKUPS.keep,
        nothing_to_back_up="nothing to back up yet (no state database)", backed_up="backup: {archive}", failed=None,
        offsite_stored="Off-site copy: stored in {bucket} ({stored} kept there)",
        offsite_not_set_up="Off-site copy: not set up", offsite_failed="The off-site copy failed: {error}",
        help="Copy the state database (spending, saved briefings, reports): the one thing that cannot be rebuilt. "
             "The copy also goes to a Backblaze B2 bucket when one is set up (WCS_B2_* in deploy/.env).",
        to_help="folder for the backups", keep_help="how many to keep"),
    restore=RestoreWords(
        archive_must_exist=False, says_aborted=False,
        confirm="Replace {state_db} with {archive}? Stop the site first.",
        restored="restored {state_db} from {archive}", replaced="; the database it replaced is {kept}",
        not_restored="Not restored: {error}",
        help="Put a backup of the state database back (spending, saved briefings, reports). Stop the site first "
             "(docs/DEPLOY.md, \"Backups\"). The backup is checked before anything is replaced, and the database it "
             "replaces is kept as data/state.db.before-restore. A kept copy from an earlier restore is never "
             "written over: move or delete it first.",
        archive_help="a backup: /backups/wcs-state-YYYY-MM-DD.sqlite.gz", yes_help="don't ask first"),
    backup_check=None,  # a stopped backup is noticed by `wcs update`, which runs every day and is emailed
    errors_to_stderr=True,
    offsite_help="Check the off-site backup bucket: whether the key works, and how many of this site's backups are "
                 "there.")

app.add_typer(ops_commands(SITE, load_settings, BACKUPS, OPS_WORDING, clock=lambda: CLOCK))


@app.command()
def usage(days: int = typer.Option(7, help="list daily spend and failures for this many days"),
          as_json: bool = typer.Option(False, "--json")) -> None:
    """Spending on briefings today and this month against the caps, and briefings that failed (from data/state.db).
    Exits 1 when spending is at 80% of a cap or more, or a briefing failed in the last day."""
    from wcs.spending import summary, warnings

    s = load_settings()
    if not s.state_db.exists():
        typer.echo(f"no spending recorded yet ({s.state_db} does not exist)")
        return
    report = summary(s, _state(s), _clock(), days=days)
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


# ---- reports, statements under the right of reply, and hidden items (decision 16) ------------------------------
#
# The commands are the Where They Stand sites' shared ones (wts-core, ``wts_core.cli.replies_commands``), in the one
# shape every site has: `wcs reports list|show|close`, `wcs statements list|show|publish|decline|update|withdraw|
# remove|log`, `wcs hide`, `wcs unhide`, `wcs hidden`, `wcs waiting`. Every line they print is this site's, below.
# The names the site had before (`wcs reports`, `wcs close-report 3`, `wcs statements`, `wcs publish-statement 3`…)
# still work, for this release: the two listings as each group's own action when no command is given, the rest as
# further names for the same commands.

def _clock() -> Clock:
    return CLOCK


def _state(settings: Settings | None = None) -> StateDB:
    from wcs.state import open_state

    return open_state(settings or load_settings(), _clock())


def _briefings(db: StateDB) -> BriefingArchive:
    return BriefingArchive(db, new_id=lambda: token_id(8), clock=_clock())


def _replies() -> Replies:
    from wcs.replies import open_replies

    db = _state()
    return open_replies(db, _briefings(db), _clock())


def _report_lines(r: Mapping[str, Any]) -> list[str]:
    lines = [f"#{r['id']}  {r['at']}  {r['kind']}  {r['page'] or '-'}{'  (closed)' if r['closed_at'] else ''}",
             f"    {r['message']}"]
    if r["contact"]:
        lines.append(f"    reply to: {r['contact']}")
    if r["closed_at"] and (r["resolution"] or r["public_note"]):
        lines.append(f"    {r['status']}" + (f": {r['resolution']}" if r["resolution"] else "")
                     + (f" (said publicly: {r['public_note']})" if r["public_note"] else ""))
    return lines


def _statement_lines(st: Mapping[str, Any]) -> list[str]:
    shown_from = max(st["published_at"] or "", st["updated_at"] or "")[:10]  # the date of the words now shown
    lines = [f"#{st['id']}  {st['at']}  {st['status']}  {st['item']}"
             + (f"  (speaks for {st['about']})" if st["about"] else ""),
             f"    from: {st['name']}, {st['role']}"]
    if st["email"]:
        lines.append(f"    confirm with: {st['email']}")
    if st["link"]:
        lines.append(f"    record: {st['link']}")
    lines += [f"    {para}" for para in st["text"].splitlines() if para.strip()]
    # the owner's note of how the sender was confirmed, from the log's entry for the publishing
    lines += [f"    sender confirmed: {e['note']}" for e in st["log"] if e["action"] == "published" and e["note"]]
    lines.append(f"    ({st['words']} words"
                 + (f"; shown from {shown_from}" if st["status"] == "published" else "")
                 + (f"; {st['status']}: {st['reason']}" if st["reason"] else "") + ")")
    return lines


REPLIES_COMMANDS = RepliesCommands(
    listed=("pending", "published"),
    about_option="--country",
    withdraw_note="Withdrawn by its author.",
    outcome="resolved",  # how `reports close` records a report when --outcome is not given
    tidy=None,  # the nightly housekeeping is a step of `wcs tidy`
    # the names these commands had before, kept out of the help
    also={"close-report": "reports_close", "list-statements": "statements_list",
          "publish-statement": "statements_publish", "decline-statement": "statements_decline",
          "update-statement": "statements_update", "withdraw-statement": "statements_withdraw"},
    help={
        "reports": "Problems and takedown requests sent through the site's form. With no command, those still open.",
        "statements": "Statements under the right of reply sent through the site. With no command, those waiting "
                      "for a decision and those shown.",
        "reports_list": "Problems and takedown requests sent through the site's form: takedown requests first, "
                        "then newest first.",
        "reports_close": "Mark a report dealt with. Its contact address is deleted (the privacy notice says so).",
        "statements_list": "Statements under the right of reply sent through the site, newest first: those waiting "
                           "for a decision and those shown. Confirm who sent one (reply to its address, ideally a "
                           "mission's or government's) before publishing.",
        "statements_publish": "Show a waiting statement beside its briefing, dated today, as written (never "
                              "edited), once you have confirmed who sent it: --checked says how.",
        "statements_publish.checked": "how you confirmed who sent it, for the log (never shown on the site), e.g. "
                                      "'Reply from the mission's published address, 14 November.' Do not put the "
                                      "sender's email address in it: the privacy notice promises that address is "
                                      "deleted",
        "statements_publish.about": "the country it speaks for (ISO alpha-3, e.g. KEN), for that country's page "
                                    "once there is one",
        "statements_decline": "Decline a waiting statement, with the reason (tell its author). Its address is "
                              "deleted three months later.",
        "statements_decline.reason": "for the author and the log, e.g. 'It is about another delegation's conduct.'",
        "statements_update": "Change a statement at its author's request (confirmed from their address). A "
                             "published one with new words is dated afresh.",
        "statements_update.text": "the author's new wording, as sent",
        "statements_update.text_file": "the new wording, from a file",
        "statements_update.link": "the link to the record ('' for none)",
        "statements_update.about": "the country it speaks for (ISO alpha-3)",
        "statements_withdraw": "Take a statement down at its author's request. Its address is deleted three months "
                               "later.",
        "statements_remove": "Take a published statement down because it breaks the rules (policy 7.2: say why). "
                             "Its address is deleted three months later.",
        "hide": "Hide an item while a concern about it is decided: a saved briefing (/b/<id>), or a record of the "
                "archive (record:<its id, from its source card>). The note is shown in its place.",
    },
)
REPLIES_WORDING = RepliesWording(
    report=_report_lines,
    statement=_statement_lines,
    log_entry=lambda e: [f"    {e['at']}  {e['action']}" + (f": {e['note']}" if e["note"] else "")],
    hidden_item=lambda h: [f"{h['item']}  hidden since {h['at'][:10]}", f"    shown in its place: {h['note']}"],
    waiting="waiting for a decision: {reports} reports, {statements} statements; hidden for now: {hidden} items; "
            "the oldest has waited {oldest_working_days} working days",
    published="published", declined="declined", updated="updated", withdrawn="withdrawn", removed="removed",
    closed="closed", hid="hidden: {item}", unhid="shown again: {item}",
    tidied="right of reply: {lapsed} statements lapsed undecided, {emails} confirming addresses deleted, "
           "{contacts} old reports' contact details deleted, {counts} old daily counts deleted",
    not_done="error: {message}", refused="error: {message}", errors_to_stderr=True,
)


def _country(code: str) -> str:
    from wcs.replies import check_country

    return check_country(code) or ""


reports_app = typer.Typer(help=REPLIES_COMMANDS.help["reports"])
statements_app = typer.Typer(help=REPLIES_COMMANDS.help["statements"])


@reports_app.callback(invoke_without_command=True)
def list_reports(ctx: typer.Context,
                 all_: bool = typer.Option(False, "--all", help="include reports already dealt with")) -> None:
    if ctx.invoked_subcommand is not None:
        return  # one of the commands was asked for: nothing to do here
    for r in _replies().reports(open_only=not all_):  # `wcs reports`, as it always was
        typer.echo("\n".join(_report_lines(r)))


@statements_app.callback(invoke_without_command=True)
def list_statements(ctx: typer.Context, all_: bool = typer.Option(
        False, "--all", help="include declined, withdrawn and removed statements")) -> None:
    if ctx.invoked_subcommand is not None:
        return
    desk = _replies()  # `wcs statements`, as it always was
    for st in desk.statements():
        if all_ or st["status"] in REPLIES_COMMANDS.listed:
            typer.echo("\n".join(_statement_lines({**st, "words": word_count(st["text"]), "log": desk.log(st["id"])})))


app.add_typer(replies_commands(_replies, REPLIES_COMMANDS, REPLIES_WORDING, about=_country,
                               groups={"reports": reports_app, "statements": statements_app}))


@app.command()
def withdraw(briefing_id: str, reason: str = typer.Option(..., help="shown at its address and on the corrections "
                                                                   "page, e.g. 'It misattributed a statement.'")) -> None:
    """Withdraw a saved briefing (/b/<id>): its address then says why, and the corrections page lists it."""
    from wts_core.observe import SqliteBriefingLog

    db = _state()
    ok = _briefings(db).withdraw(briefing_id, reason)
    if ok:
        SqliteBriefingLog(db, clock=_clock()).mark(briefing_id, corrected=True)  # on that briefing's record
    typer.echo("withdrawn" if ok else "no briefing with that id")
    if not ok:
        raise typer.Exit(1)


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
    index = None
    if re_embed:
        # The index records which embedding model made its vectors, and is not opened for another: the vectors are
        # thrown away and the model now in the settings recorded, before anything is written.
        from wcs.adapters import get_embedder
        from wcs.index.lance import open_index

        index = open_index(s.index_dir)
        index.start_again(get_embedder(s))
    n = Indexer(s, countries(), index, keep_vectors=not re_embed).index_all(tuple(source) if source else SOURCES,
                                                                             _years(years))
    typer.echo(f"{n} passages indexed")


@app.command()
def optimize() -> None:
    """Merge rows waiting outside the search indices, compact small files, and delete versions of the index over an
    hour old (each update does this itself; this is for a one-off clean-up)."""
    from wcs.index.lance import open_index

    idx = open_index(load_settings().index_dir)
    before = idx.versions()
    idx.tidy()
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
    from wcs.adapters import get_embedder
    from wcs.index.build import State, embed_pending, refresh_indices
    from wcs.index.lance import open_index

    s = load_settings()
    embedder = get_embedder(s)
    idx = open_index(s.index_dir, embedder)
    state = State(s.index_dir / "state.json")
    n = embed_pending(s, idx, state, embedder, max_seconds=max_seconds)
    if n:
        refresh_indices(idx, state)
    typer.echo(f"{n} passages embedded")


@app.command()
def stats() -> None:
    """What is loaded and indexed."""
    from wcs.index.lance import open_index
    from wcs.store.parquet import votes_path, years

    s = load_settings()
    for src in ("sc", "gd"):
        ys = years(s.parquet_dir, src)
        typer.echo(f"{src}: {len(ys)} years" + (f" ({ys[0]}-{ys[-1]})" if ys else ""))
    typer.echo(f"votes: {'loaded' if votes_path(s.parquet_dir).exists() else 'not loaded'}")
    typer.echo(f"index: {open_index(s.index_dir).stats()}")


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
