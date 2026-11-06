# Keeping the data current

`wcs update` brings everything up to date in one run, and is meant to run on a schedule (twice a day). This page
explains what it does, how it is built to keep working as the UN's websites change, how to schedule it, and what to do
when it reports a problem.

## What a run does

1. **General Assembly votes.** Reads the voting data record in the UN Digital Library (its file list carries the
   file's date). If the UN has published a new file, downloads it and converts it (about 370 MB, 5 seconds), and
   deletes the files it supersedes (the newest is chosen by the date in its name, not by the name's order).
2. **Security Council records** (from `[live] sc_from`, 1 January 2024; the corpus covers the years before):
   * finds meeting records newer than the last one held, in the Digital Library's catalogue, then by probing the
     UN's document system (ODS) for the next meeting numbers;
   * downloads each record's English PDF, parses it into speeches (`wcs.sources.verbatim`) and checks the result;
   * looks again for numbers still missing (for 60 days), and fetches provisional records again for up to a year,
     at most monthly, so corrected versions replace them;
   * parses every cached PDF again when the parser changes (no downloads);
   * stores each year that changed.
3. **General Debate** (from `[live] gd_from_session`, the 79th session of 2024; the corpus covers the sessions
   before): see [The General Debate](#the-general-debate) below.
4. **Index.** Re-indexes the years that changed, then embeds new passages for up to `--embed-seconds`, and merges
   what was written into the search indices (see [The search index on disk](#the-search-index-on-disk)). A run
   that changed nothing leaves the index alone.
5. **Report.** Writes `data/logs/update-<time>.json` (the last 60, a month's, are kept) and prints a summary. Exits
   1 if something needs attention.

A first run after loading the corpus backfills everything since the corpus ended (S/PV.9525 onwards: about 300
meetings a year). It takes about 10 seconds a meeting, mostly reading the PDF, and saves its progress after every
meeting, so it can be stopped and started. `--max-new 50` limits a run to 50 records.

## Built to keep working

| Risk | What happens |
|---|---|
| The Digital Library's search changes or is down | ODS is probed for the following meeting numbers instead (one request each, stopping after 12 numbers in a row are not found). The report warns. |
| ODS changes or is down | Records come from the Digital Library's file links. |
| Both are unreachable | The run fails with an error. It never reports "no new records" when it could not look. |
| A response is not a PDF | It is rejected (ODS answers a missing symbol with an HTML page and status 200). |
| A closed meeting (only a communiqué) | Recognised by its catalogue title or by its first page ("in place of a verbatim record") and recorded as a communiqué, not a failure. |
| A record appears late | Its number is tried again on each run for 60 days. |
| A provisional record is corrected | Fetched again (at most monthly) for a year, or whenever the Digital Library's record changes. |
| The record template changes | Speakers are found by typography (a bold name, then a colon); if a record has no bold type, by wording, and the report says so. Running headers are dropped by position and by wording. |
| The parser improves | `PARSER_VERSION` is stored with every parsed record; a higher version parses the cached PDFs again on the next run. |
| A record will not parse | Reported, tried up to 5 times, then left alone. The rest of the run carries on. |
| A run is interrupted | The manifest is saved after every meeting, and years with parsed but unstored meetings are stored on the next run. |
| No new meetings for weeks | The report warns after 21 days without a newer record. |
| A record prints a speaker's name without bold type | Accepted by its wording if it comes straight after the President and names a country or body in brackets. |
| Body text runs into the page's footer area | Only short lines there are taken as the footer; a line of more than six words is kept as text. |
| The parser drifts from the corpus | `scripts/validate_undocs.py` compares the parser with the corpus on meetings both cover (results in `docs/VALIDATION.md`). Re-run it after changing the parser. |

Everything fetched is kept in `data/raw/undocs/`: `pdf/` (the records), `parsed/` (the speeches parsed from each),
and `manifest.json` (for each record: where and when it was fetched, its checksum, whether it was provisional, the
parser version, the speeches found and any problems).

## Scheduling

On the server (`docs/DEPLOY.md`), `wcs-update.timer` runs it twice a day, each run in a one-off container;
`deploy/update.sh` installs it. Every site's scheduled update on the shared server takes the same lock
(`flock /var/lock/wheretheystand-update.lock`), so a run that starts while another site's update is going waits for
it to finish rather than competing with it; `systemctl status wcs-update` shows it waiting. To see it:

```bash
systemctl list-timers wcs-update.timer        # when it runs next
journalctl -u wcs-update.service -n 50        # the last run's summary
systemctl start wcs-update.service            # run one now
```

If a run, or the nightly backup, fails (exits 1 when something needs attention, or stops with an error), the server
emails the operations address with the result and the run's last log lines, at most once every six hours. That is
set up once for every site on the server (Where MPs Stand's
[`docs/HOSTING.md`](https://github.com/rj-mitchell/wherempsstand/blob/main/docs/HOSTING.md), "Email from the
server", which names the address).

## The search index on disk

The search index (`data/index/`, LanceDB) never overwrites anything: every write makes a new version of a table
and leaves the old files on disk. So an update writes as little as it can, and cleans up after itself:

* **Only what changed is written.** A run with no new records writes nothing to the index. Re-indexing a year keeps
  the vectors of passages whose text is unchanged, so a new meeting costs the embedding of its own passages, not
  the whole year's.
* **New rows are merged, not rebuilt.** After writing, the update merges the new rows into the existing keyword,
  filter and meaning-search indices, compacts small files and deletes versions over an hour old (as Where MPs
  Stand does after every write). The report says `"index": "merged"`.
* **Full rebuilds are rare.** The indices are built from scratch on the first build, by `wcs index` and
  `wcs regroup`, and the meaning-search index is trained again once the vectors have grown by a fifth since it was
  last trained (recorded in `data/index/state.json`, under `_indices`). The report then says `"index": "built"`.
  The first update after this was introduced trains it once, since no size was recorded before.

`wcs optimize` does the clean-up on its own, at any time, while the site serves (for example after a run that was
stopped). `wcs index --re-embed` drops the vectors as well, so that every passage is embedded again: only needed
after changing the embedding model.

## When a run reports a problem

The last report's time, warnings and errors are also public at `/api/status` (they are all about the UN's
sources), so the site's freshness can be checked without logging in to the server.

The report also covers the site: spending against the caps and briefings that failed, from `data/state.db`
(`wcs usage` shows the same, with spend by day, and exits 1 when something needs a look). And it checks the
backups (`backups` in the report, kept out of `/api/status`): a nightly backup or off-site copy more than two days
old, or a failed off-site copy, makes the run exit 1, so the server emails, even when the backup timer itself has
stopped and so cannot fail.

| Report says | Likely cause | What to do |
|---|---|---|
| `could not reach the Digital Library or ODS` | Network or UN outage | Nothing, if it clears on the next run; check the server's network if it persists. |
| `Digital Library search failed; probing ODS only` | Library down or its search changed | Still working. If it persists, check the search URL in `wcs.sources.undocs.UNDocs.search`. |
| `records parsed by wording, not typography` | The PDF template changed | Look at one of the records named in the report; adjust `wcs.sources.verbatim`, raise `PARSER_VERSION`, and run `scripts/validate_undocs.py`. |
| `records could not be parsed` | An unusual record | The manifest entry has the error. Usually one record; the rest are unaffected. |
| `labels that look like countries but matched none` | A country written in a new way | Add it to `data_static/aliases.csv`. |
| `no Security Council record newer than ...` | Records not appearing, or discovery broken | Check https://digitallibrary.un.org for recent `S/PV` records; if they are there, discovery needs fixing. |
| `no General Debate records yet for the Nth session` | Over 15 months since the debate with no records found | Check the Digital Library for `A/<session>/PV` records. If the debate's records are there, look at the manifest entries in `manifest-ga.json`. |
| `statements not matched to a country` | A post written in a way the reference data lacks ("President of the Togolese Republic") | Add the country's form to `data_static/aliases.csv`. |
| `the General Debate website refused access` | The site's browser check now turns this client away (likely from a data-centre address: the live server's updates had no website statements by November 2026, while the same code fetched them from elsewhere) | Run `wcs gd-website` on the server: it reads the sitemap and one statement page, and prints what came back. Until it works, the corpus's text fills in where it has the session (labelled "corpus"; the corpus ends in 2024), and statements arrive with the official records, about a year later. To restore the faster route, see whether a newer browser identity (`BROWSER_USER_AGENT` in `wcs.sources.gadebate`) is accepted, or ask the UN for access. Once it works, `wcs gd-website --now` makes the next `wcs update` read the website at once. Meanwhile, statements read from a network the website doesn't refuse are kept in `data_static/gd_website/` (2025 and 2026, read 4 November 2026) and imported by the update once each; others can be loaded with `wcs import-gd-website <records-N.json …>` (paths or web addresses), then `wcs update --no-votes --no-sc` stores and indexes them. The official record still replaces each one when it is published. The warning repeats on every update until a reading succeeds, and `/api/status` shows the last reading (`general_debate_website`). |
| `website statement not matched to a country` | The site names a country in a new way ("Naoero" for Nauru) | Add it to `data_static/aliases.csv`. |
| `General Debate speeches found but no statements introduced` | The President's introductions are worded in a new way | Look at the record; adjust `INVITATION` or `ADDRESS` in `wcs.sources.unga`. |
| `statements shown as recorded videos ... could not be read from the annexes` | A session held virtually, with annexes laid out differently from 2020's | Look at the record's annexes; adjust `verbatim.read_annexes`. The other statements are unaffected. |
| `introductions name another country than the speaker who follows` | The record's pages come out of order (seen in 2020) | Those speeches are left out rather than misattributed; check the record if many are. |
| `spending: today's cap ... 80% used` (or this month's) | Many briefings asked for | Nothing, if it is a busy day: at the cap the site shows sources only until midnight UTC. `wcs usage` shows spend by day; raise `daily_spend_cap_usd` or `monthly_spend_cap_usd` in `config/settings.toml` if the budget allows. |
| `spending: ... cap reached: new briefings are paused` | The cap is spent | As above. Visitors still get the sources, and briefings already written still load. |
| `error: backup: the last backup was made over two days ago` | The backup timer has stopped, or its runs fail | `systemctl status wcs-backup.timer wcs-backup.service`; `journalctl -u wcs-backup.service -n 50`. Run one now with `systemctl start wcs-backup.service`. |
| `error: backup: the last off-site copy failed` (or `was over two days ago`) | The B2 key was changed or revoked, or B2 was down | `docker compose run --rm -T app wcs offsite` says whether the key works; see `docs/DEPLOY.md`, "Backups". |
| `warning: backup: backups are kept on this server only` | No off-site copy is set up | Set it up (`docs/DEPLOY.md`, "Backups"). |
| `briefing(s) failed in the last 24 hours` | The model API refused or timed out, or a bug | `wcs usage` lists each failure with its question and error. An API outage clears itself; the same error again and again is a bug. |

## Problems, corrections and withdrawn briefings

Every written briefing is saved at its own address (`/b/<id>`), shown under it with a link to report a problem with
it. Reports sent through the corrections page (`/corrections.html`) are kept in `data/state.db`:

```bash
wcs reports                       # open reports, newest first (--all for closed ones too)
wcs close-report 12               # dealt with: its contact address is deleted (the privacy notice says so)
wcs withdraw AbC123xy --reason "It attributed a statement to the wrong country."
```

A withdrawn briefing's address says it was withdrawn, and why, and the corrections page lists its question and the
reason. The same question can then be asked again. The alpha notice on every page links to the report form, and
gives the email address as well.

## The General Debate

**Before the 79th session (2023 and earlier)** the General Debate comes from the corpus, which its authors update
once a year: `wcs fetch-gd` downloads a new release (making the guestbook entry Dataverse asks for) and `wcs ingest-gd`
loads it, only the sessions before `[live] gd_from_session`. A new release changes nothing the site uses unless it
corrects an older session, so this is occasional. The corpus's 2024 and 2025 texts are not the record (the
delegations' texts as prepared, and AI transcripts), which is why the records take over from 2024.

**From the 79th session (September 2024)** `wcs update` reads the debate from the General Assembly's own verbatim
records (`A/<session>/PV.<n>`), with the parser used for the Security Council (`wcs.sources.unga`):

* **Finding the meetings.** The debate falls within each session's first 30 plenary meetings (4 to 17 in 2023 and
  2024). The Digital Library's catalogue names each meeting's agenda items, so meetings on other business are not
  fetched; numbers it has not catalogued are looked for on ODS. Numbers not yet published are looked for again
  every week, for up to three years, until a record says the debate has ended ("We have heard the last speaker in
  the general debate").
* **What counts as a statement.** The President introduces each speaker, with a heading ("Address by Mr. Petr Pavel,
  President of the Czech Republic") or an invitation ("I now give the floor to His Excellency Mr. Bassam Sabbagh,
  Minister for Foreign Affairs and Expatriates of the Syrian Arab Republic."). The speech that follows is the
  country's statement. The introduction gives the speaker's name and post, and the post gives the country when the
  attribution does not ("President Pavel:").
* **Rights of reply** are records of their own (agenda "General debate: right of reply"): the speeches after the
  President calls those who asked to reply, or that open by saying they are a reply. Points of order are left out.
  For sessions before the 79th, where the corpus has the statements but no replies, `wcs backfill-replies --since 48`
  adds them from the records (from 1993: the records of 1991-1993 are scans that can't be read reliably). Reloading
  the corpus keeps them.
* **Dates.** Each statement has its day, where the corpus has only the session.

**When the records appear.** About a year after the debate, and not in order: on 24 October 2026 the 80th session's
debate (September 2025) was not yet published, and the 79th's (September 2024) was.

**Until then, the General Debate website** (`wcs.sources.gadebate`, decision 12). The UN's website has a page for
each statement within days, listed in its sitemap: the speaker's name, post and date, the statement as the
delegation issued it (a PDF, in English for most), and an AI transcript of the UN's English interpretation. The
update reads the sitemap at most once a day and fetches new or changed pages. It takes the delegation's English text
if there is one, else the transcript, and labels the record with where its text came from (`text_source`
"delegation" or "transcript"); briefings say the text is not yet the official record. When the official record of a
statement is published it replaces the website's, statement by statement. The site's browser check turns away
automated clients, so this client presents itself as a browser and keeps to what robots.txt allows. If the site
blocks it anyway, the report warns (`the General Debate website refused access`) and the official records still
arrive in time. `[live] gd_website = false` turns it off. Raw files and state are in `data/raw/gadebate/`.

**Checked on the 79th session (2024).** Run end to end on its records: all 11 debate meetings found and parsed,
192 statements, one for each Member State that spoke, plus the State of Palestine, the Holy See and the European
Union. The four Member States without a statement (Afghanistan, Brunei Darussalam, Myanmar and the Democratic
People's Republic of Korea, which spoke only in reply) have no introduction anywhere in the records. 116 statements
by heads of State or Government, 68 by ministers, 6 by heads of delegation.
