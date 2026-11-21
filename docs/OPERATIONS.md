# Operations

How the maintainer keeps Where Countries Stand running: the scheduled update and the problems it reports, the
General Debate, the search index, corrections and the right of reply, the record of briefings, test runs and the
state database. Setting up the server and deploying new code are in [DEPLOY.md](DEPLOY.md).

On the server, run each `wcs` command in `/opt/wherecountriesstand/deploy` as
`docker compose run --rm -T app wcs <command>`. Locally, run `uv run wcs <command>`.

## The scheduled update

`wcs update` brings everything up to date in one run. The server runs it twice a day.

### What a run does

1. General Assembly votes. It reads the voting data record in the UN Digital Library. If the UN has published a new
   file, it downloads and converts it (about 370 MB, 5 seconds) and deletes the files it replaces.
2. Security Council records, from `[live] sc_from` (1 January 2024; the corpus covers the years before). It:
   * finds meeting records newer than the last one held, in the Digital Library's catalogue, then by probing the
     UN's document system (ODS) for the next meeting numbers;
   * downloads each record's English PDF, parses it into speeches (`wcs.sources.verbatim`) and checks the result;
   * looks again for missing numbers for 60 days, and fetches provisional records again, at most monthly, for up
     to a year, so corrected versions replace them;
   * parses every cached PDF again when the parser changes (no downloads);
   * stores each year that changed.
3. The General Debate, from `[live] gd_from_session` (the 79th session, 2024): see
   [The General Debate](#the-general-debate).
4. The index. It re-indexes the years that changed, embeds new passages for up to `--embed-seconds`, and merges
   them into the search indices ([The search index on disk](#the-search-index-on-disk)).
5. The report. It writes `data/logs/update-<time>.json` (the last 60 are kept) and prints a summary. It exits 1 if
   something needs attention.

A first run after loading the corpus fills in everything since the corpus ended (S/PV.9525 onwards, about 300
meetings a year). It takes about 10 seconds a meeting and saves its progress after every meeting, so it can be
stopped and started. `--max-new 50` limits a run to 50 records.

### Built to keep working

| Risk | What happens |
|---|---|
| The Digital Library's search changes or is down | ODS is probed for the following meeting numbers instead (one request each, stopping after 12 numbers in a row are not found). The report warns. |
| ODS changes or is down | Records come from the Digital Library's file links. |
| Both are unreachable | The run fails with an error. It never reports "no new records" when it could not look. |
| A response is not a PDF | It is rejected (ODS answers a missing symbol with an HTML page and status 200). |
| A closed meeting (only a communiqué) | Recognised by its catalogue title or its first page, and recorded as a communiqué. |
| A record appears late | Its number is tried again on each run for 60 days. |
| A provisional record is corrected | Fetched again (at most monthly) for a year, or whenever the Digital Library's record changes. |
| The record template changes | Speakers are found by typography (a bold name, then a colon). If a record has no bold type, they are found by wording, and the report says so. Running headers are dropped by position and wording. |
| A speaker's name is printed without bold type | Accepted by its wording if it comes straight after the President and names a country or body in brackets. |
| Body text runs into the page's footer area | Only short lines there are taken as the footer; a line of more than six words is kept as text. |
| The parser improves | `PARSER_VERSION` is stored with every parsed record, and a higher version parses the cached PDFs again on the next run. |
| A record will not parse | Reported, tried up to 5 times, then left alone. The rest of the run carries on. |
| A run is interrupted | The manifest is saved after every meeting, and years with unstored meetings are stored on the next run. |
| No new meetings for weeks | The report warns after 21 days without a newer record. |
| The parser drifts from the corpus | `scripts/validate_undocs.py` compares the two on meetings both cover ([VALIDATION.md](VALIDATION.md)). Run it again after changing the parser. |

Everything fetched is kept in `data/raw/undocs/`: `pdf/` (the records), `parsed/` (the speeches parsed from each)
and `manifest.json` (for each record: where and when it was fetched, its checksum, whether it was provisional, the
parser version, the speeches found and any problems).

### Scheduling

`wcs-update.timer` runs the update at 5.41am and 5.41pm UTC, each run in a one-off container, and
`deploy/update.sh` installs it. Every site's scheduled update on the shared server takes the same lock
(`/var/lock/wheretheystand-update.lock`), so a run that starts during another site's update waits for it to finish.

```bash
systemctl list-timers wcs-update.timer        # when it runs next
journalctl -u wcs-update.service -n 50        # the last run's summary
systemctl start wcs-update.service            # run one now
```

If a run or the nightly backup fails (exits 1, or stops with an error), the server emails the operations address
with the result and the last log lines, at most once every six hours. This is set up once for every site on the
server: see "Email from the server" in Where MPs Stand's
[`docs/HOSTING.md`](https://github.com/rj-mitchell/wherempsstand/blob/main/docs/HOSTING.md).

## The General Debate

The sessions before the 79th (2023 and earlier) come from the General Debate corpus. Its authors add each new
session once a year: `wcs fetch-gd` downloads a new release and `wcs ingest-gd` loads the sessions before
`[live] gd_from_session`. A new release matters only if it corrects an older session. The corpus's 2024 and 2025
texts are the delegations' prepared texts and AI transcripts, which is why the UN's records take over from 2024.

From the 79th session (September 2024), `wcs update` reads the debate from the General Assembly's verbatim records
(`A/<session>/PV.<n>`), with the parser used for the Security Council (`wcs.sources.unga`).

* Finding the meetings. The debate falls within each session's first 30 plenary meetings. The Digital Library's
  catalogue names each meeting's agenda items, so meetings on other business are not fetched; numbers it has not
  catalogued are looked for on ODS. Numbers not yet published are looked for every week, for up to three years,
  until a record says the debate has ended ("We have heard the last speaker in the general debate").
* Statements. The President introduces each speaker, with a heading ("Address by Mr. Petr Pavel, President of the
  Czech Republic") or an invitation ("I now give the floor to …"). The speech that follows is the country's
  statement. The introduction gives the speaker's name and post, and the post gives the country when the
  attribution does not ("President Pavel:").
* Rights of reply are records of their own ([decision 13](DECISIONS.md#13-rights-of-reply)). For sessions before
  the 79th, where the corpus has no replies, `wcs backfill-replies --since 48` adds them from the records (from
  1993). Reloading the corpus keeps them.
* Dates. Each statement has its day; the corpus has only the session.

How well this works is in [VALIDATION.md](VALIDATION.md#the-general-debate-general-assembly-records).

### Before the records are published

The records are published about a year after the debate, and not in order. Until then, statements come from the
UN's General Debate website, gadebate.un.org (`wcs.sources.gadebate`;
[decision 12](DECISIONS.md#12-the-general-debate-website)). The website has a page for each statement within days,
listed in its sitemap, with the statement as the delegation issued it (a PDF, in English for most) and an AI
transcript of the UN's English interpretation. The update reads the sitemap at most once a day and fetches new or
changed pages. It takes the delegation's English text if there is one, otherwise the transcript, and records which
in `text_source` ("delegation" or "transcript"). Briefings say the text is not yet the official record. When the
official record of a statement is published it replaces the website's. `[live] gd_website = false` turns this off.
Raw files and state are in `data/raw/gadebate/`.

The website's browser check turns away the live server. Until that changes:

* `data_static/gd_website/` holds the 80th and 81st sessions' statements, read on 4 November 2026 from a network
  the website accepts. The update imports each file once (`[live] gd_bundled`); see that folder's README.
* Other files read elsewhere can be loaded with `wcs import-gd-website <records-N.json …>` (paths or web
  addresses), then stored and indexed with `wcs update --no-votes --no-sc`.
* `wcs gd-website` checks the website from the server: it reads the sitemap and one statement page, and prints
  what came back. Once it works, `wcs gd-website --now` makes the next update read the website at once. A newer
  browser identity (`BROWSER_USER_AGENT` in `wcs.sources.gadebate`) or access granted by the UN may get past the
  check.

## The search index on disk

The search index (`data/index/`, LanceDB) never overwrites anything: every write makes a new version of a table and
leaves the old files on disk. So the update writes as little as it can and cleans up after itself.

* Only what changed is written. Re-indexing a year keeps the vectors of passages whose text is unchanged, so a new
  meeting costs the embedding of its own passages only.
* New rows are merged into the existing keyword, filter and meaning-search indices; small files are compacted and
  versions over an hour old deleted. The report says `"index": "merged"`.
* The indices are built from scratch by the first build, `wcs index` and `wcs regroup`, and the meaning-search
  index is trained again once the vectors have grown by a fifth (recorded in `data/index/state.json`, under
  `_indices`). The report then says `"index": "built"`.

`wcs optimize` does the clean-up on its own, at any time, while the site serves. `wcs index --re-embed` embeds every
passage again, which is needed only after changing the embedding model.

## When a run reports a problem

The last report's time, warnings and errors are public at `/api/status` (they are all about the UN's sources), so
the site's freshness can be checked without logging in to the server.

The report also covers the site: spending against the caps and failed briefings, from `data/state.db` (`wcs usage`
shows the same, with spending by day). It also checks the backups (`backups`, kept out of `/api/status`): a nightly
backup or off-site copy more than two days old, or a failed off-site copy, makes the run exit 1 and so sends the
alert email, even when the backup timer itself has stopped.

| Report says | Likely cause | What to do |
|---|---|---|
| `could not reach the Digital Library or ODS` | A network or UN outage | Nothing, if it clears on the next run. If it persists, check the server's network. |
| `Digital Library search failed; probing ODS only` | The Library is down or its search changed | Nothing while it works. If it persists, check the search URL in `wcs.sources.undocs.UNDocs.search`. |
| `records parsed by wording, not typography` | The PDF template changed | Look at one of the records named; adjust `wcs.sources.verbatim`, raise `PARSER_VERSION`, and run `scripts/validate_undocs.py`. |
| `records could not be parsed` | An unusual record | The manifest entry has the error. Usually one record; the rest are unaffected. |
| `labels that look like countries but matched none` | A country written in a new way | Add it to `data_static/aliases.csv`. |
| `no Security Council record newer than ...` | Records not appearing, or discovery broken | Check https://digitallibrary.un.org for recent `S/PV` records. If they are there, discovery needs fixing. |
| `no General Debate records yet for the Nth session` | Over 15 months since the debate with no records found | Check the Digital Library for `A/<session>/PV` records. If they are there, look at the entries in `manifest-ga.json`. |
| `statements not matched to a country` | A post written in a way the reference data lacks ("President of the Togolese Republic") | Add the country's form to `data_static/aliases.csv`. |
| `the General Debate website refused access` | The website's browser check turns this client away | See [Before the records are published](#before-the-records-are-published). The warning repeats until a reading succeeds; `/api/status` shows the last one (`general_debate_website`). |
| `website statement not matched to a country` | The website names a country in a new way ("Naoero" for Nauru) | Add it to `data_static/aliases.csv`. |
| `General Debate speeches found but no statements introduced` | The President's introductions are worded in a new way | Look at the record; adjust `INVITATION` or `ADDRESS` in `wcs.sources.unga`. |
| `statements shown as recorded videos ... could not be read from the annexes` | A virtual session with annexes laid out differently from 2020's | Look at the record's annexes; adjust `verbatim.read_annexes`. The other statements are unaffected. |
| `introductions name another country than the speaker who follows` | The record's pages come out of order (seen in 2020) | Those speeches are left out rather than misattributed. Check the record if there are many. |
| `spending: today's cap ... 80% used` (or this month's) | Many briefings asked for | Nothing, if it is a busy day: at the cap the site shows sources only until midnight UTC. Raise `daily_spend_cap_usd` or `monthly_spend_cap_usd` in `config/settings.toml` if the budget allows. |
| `spending: ... cap reached: new briefings are paused` | The cap is spent | As above. Visitors still get the sources, and saved briefings still load. |
| `error: backup: the last backup was made over two days ago` | The backup timer has stopped, or its runs fail | `systemctl status wcs-backup.timer wcs-backup.service` and `journalctl -u wcs-backup.service -n 50`. Run one now with `systemctl start wcs-backup.service`. |
| `error: backup: the last off-site copy failed` (or `was over two days ago`) | The B2 key was changed or revoked, or B2 was down | `wcs offsite` says whether the key works; see [DEPLOY.md](DEPLOY.md#backups). |
| `warning: backup: backups are kept on this server only` | No off-site copy is set up | Set it up ([DEPLOY.md](DEPLOY.md#backups)). |
| `briefing(s) failed in the last 24 hours` | The model API refused or timed out, the model declined to answer, or a bug | `wcs usage` lists each failure with its question and error. An outage clears itself; the same error again and again is a bug. "The model declined to answer this question." needs nothing: the reader was told and shown the sources. |

## Problems, corrections and withdrawn briefings

Every written briefing is saved at its own address (`/b/<id>`), with a link to report a problem with it. Reports
sent through the corrections page (`/corrections.html`) are kept in `data/state.db`.

```bash
wcs waiting                       # how many reports and statements are waiting, and how long the oldest has waited
wcs reports                       # open reports: takedown requests first, then newest first (--all for closed ones too)
wcs reports show 12
wcs reports close 12              # dealt with: its contact address is deleted (the privacy notice says so)
wcs reports close 12 --outcome dismissed --note "Checked against S/PV.9000: the briefing is right."
wcs reports close 12 --public-note "A date in one briefing was wrong; it was written again."   # shown on the corrections page
wcs withdraw AbC123xy --reason "It attributed a statement to the wrong country."
```

`--outcome` is `resolved` (the default) or `dismissed`. `--note` is kept privately. `--public-note` is what the
corrections page says of the report, and must name no one who reported. The page (and `/api/corrections`) shows a
closed report as its date, its kind and that note only, without naming the briefing, so the note should say
where the mistake was. A command that cannot do what was asked (no open report with that number, no such briefing)
says why and exits 1. Some briefing ids begin with a hyphen; write `--` before such an id, after any options
(`wcs withdraw --reason "…" -- -AbC123xy4z`).

A withdrawn briefing's address says it was withdrawn and why, and the corrections page lists its question and the
reason. `wcs withdraw` also drops the briefing's copy in the answer cache, so the same question asked again is
written afresh.

Each saved briefing's share card (`/b/<id>/card.png`, its link preview) is drawn the first time it is asked for and
kept in `data/cards/` (`<id>.v<card version>.r<rules version>.png`). A withdrawn or hidden briefing's card is no
longer served, though its file stays. The folder holds nothing that cannot be drawn again: it can be emptied at any
time, and is not in the backups.

The form offers the charity's three routes (impartiality policy, section 7.1;
[decision 16](DECISIONS.md#16-corrections-and-the-right-of-reply)). Reports come in with the kind `error`
("Something is wrong"), `unfair` ("You've been characterised unfairly"), `takedown` or `other`; older reports may say
`problem`, which means `error`. Reply within 5 working days and decide within 20, as the corrections page and the
"If you're named" page (`/named.html`) promise. A takedown request, or anything that may be unlawful or harmful,
comes first. Log each decision and its reason, since the trustees review the log at each meeting. A letter claiming
legal liability goes straight to the trustees.

### Hiding something while a concern is decided

Under policy 7.3, while a concern is decided the thing it is about can be hidden, with a note shown in its place.
This is not a withdrawal: nothing is listed on the corrections page, and `wcs unhide` puts it back as it was.

```bash
wcs hide /b/AbC123xy --note "Hidden while a concern about it is looked into."
wcs hide record:sc:S/PV.9000:0005 --note "Hidden while a concern about this record is looked into."
wcs hidden                        # what is hidden now, and the note shown in its place
wcs unhide /b/AbC123xy
```

A briefing's address can be pasted as it appears in the browser. If there is no such briefing or record, or it
has been withdrawn, the command says so, hides nothing and exits 1.

* A saved briefing, by its address. Its page says "Briefing hidden for now" and shows the note. A statement beside
  it is hidden with it, and its copy in the answer cache is dropped.
* A record of the archive (one speech), by `record:` and the `record_id` on its source card (in the JSON of
  `/api/search` or of a briefing). It is left out of every search and every new briefing. Saved briefings that cite
  it are hidden with it, under its note, and the same question asked again is written afresh without it.

### Statements under the right of reply

The third route, "Add a statement in your own words", sends a statement (up to 150 words, a name and role, the
briefing it is about, an optional link to the record, and an email address) to `data/state.db`, where it waits. It
is shown only once published, beside its briefing.

```bash
wcs statements                                  # waiting and published, newest first (--all: every one)
wcs statements show 3                           # one statement, and everything done to it
wcs statements publish 3 --checked "Reply from the mission's published address, 14 November."
wcs statements publish 3 --checked "…" --country KEN   # ... and recorded as Kenya's
wcs statements decline 3 --reason "It is about another delegation's conduct."
wcs statements update 3 --text-file new.txt     # the author's new words (or --text, --name, --role, --link, --country)
wcs statements withdraw 3                       # at the author's request
wcs statements remove 3 --reason "It appeals for support, which statements can't."   # taken down by the site
wcs statements log 3                            # every step, with its time and note
wcs would-lapse                                 # what has waited three months and lapses next (changes nothing)
```

1. Confirm who sent it. Reply to the address it came with and wait for an answer. An address at the permanent
   mission or the foreign ministry is best; for any other, ask for confirmation from an address the mission or
   government publishes. Do the same before an update or withdrawal the author asks for. When publishing, say how
   the sender was confirmed (`--checked`, required). The note is kept in the statement's log and never shown. Leave
   the sender's email address out of it: a note containing the address is refused.
2. Check it against the rules (policy 7.2; `/named.html` gives them in plain words): up to 150 words, about the
   briefing it sits beside, nothing defamatory or abusive, nothing about a private individual or another person's
   conduct, no personal data about anyone else, and no appeals for votes, money or support.
3. Publish it as written, or decline it with the reason and tell the author. Never edit it. If something in it breaks
   the rules, ask the author to send it again without it, or agree the change with them and use `statements update`
   with their words (the old words are kept in the log).
4. An author can withdraw a statement at any time (`statements withdraw`). The site can take down one that breaks
   the rules (`statements remove`, with the reason) and tells the author why.

The confirming address is deleted three months after a statement is withdrawn, declined or removed. A statement
nobody has decided in three months lapses: it is declined with the reason "No decision was made in time; please send
it again.", and its address is deleted. A report still open after three months loses its contact address. This
happens as each report or statement arrives, at every listing, each night (`wcs tidy`) and at each update.

## How briefings went

For each question asked the site keeps one record: how long each step took, what it cost, how many sources went in
and from which groups, what the checks found and how it ended. It holds the question and nothing about the reader.
A report about a briefing, and its withdrawal, are noted on that briefing's record.

```bash
wcs briefing-stats --since 7d     # speed, cost, check failures, errors, beside the four weeks before (--json; --themes)
wcs briefing-stats --check        # only what is over its threshold; exits 1 if anything is
wcs weekly-report                 # the week's figures, saved in data/reports/
wcs propose-questions --since 7d  # questions worth adding to the test set
wcs tidy                          # the nightly housekeeping
```

`wcs stats` is a different command: it shows what is loaded and indexed.

* The weekly report is written every Monday at 7.07am UTC (`deploy/wcs-weekly-report.timer`) to
  `/data/reports/<year>-W<week>.md` and `.json`, readable by the site's user only. It lists the questions that found
  no records and the commonest themes, grouped by meaning with the site's own embedding model.
* Alerts are thresholds under `[alerts]` in `config/settings.toml` (or `data/settings.local.toml`):
  `p95_full_briefing_ms`, `error_rate`, `check_failure_rate`, `not_useful_rate` and `daily_cost_usd`. None is set
  until the owner chooses one; the weekly report proposes values from the week's figures. Once one is set, the
  weekly report and each update exit 1 when a figure is over it, which sends the alert email.
* The structured log, `/data/logs/briefings.jsonl`, has one line for each stage a briefing reached and one for how
  it ended, kept for 14 days or 50 MB. It never holds a question, an address or a visitor's code.
* The record keeps only the hour a question was asked, so `briefing-stats --since 24h` counts whole
  hours.
* `wcs propose-questions` writes the questions that found no records, or whose briefings the checks or a reader
  marked down, to a file in `data/proposed-questions/` (readable by the site's user only). A briefing that is
  hidden, withdrawn or reported is never proposed. Nothing is added to the test set: copy the ones worth keeping
  into `eval/questions.toml` by hand.

`wcs tidy` runs each night after the backup (`deploy/wcs-backup.service`). In order, it:

1. deletes a briefing's record after 13 months, the question of a failed briefing after 90 days, weekly reports
   and proposed questions after 13 months, and notes of failed briefings after 90 days;
2. takes the visitor's code and time of day off spending rows of past days (the day, model, token counts and cost
   stay), and deletes every visitor secret but the day's and cached answers older than a week
   (`answer_cache_hours`);
3. does the right of reply's housekeeping (above);
4. keeps the old tables within the same promises, until they are dropped
   ([The move onto wts-core](#the-move-onto-wts-core));
5. writes the database file out afresh, so nothing deleted is left readable in it. If the site was using the file
   at that moment it says "The database was NOT written out afresh tonight…" and tries again the next night. On the
   second night running it ends with status 1, so the alert email is sent.

## Test runs with briefings written

`wcs evaluate` scores, at no cost, how each question in `eval/questions.toml` is read and what evidence is found.
`wcs eval run` also has each briefing written and checked, and reports what the checks found, the spread of
sources, the time and the cost. It spends up to `[eval] max_cost_usd` ($10) on the model, so it is run by hand on
the server, where the record is. What the runs found, and the first baseline to compare a run with, are in
[EVALUATION.md](EVALUATION.md):

```bash
cd /opt/wherecountriesstand/deploy
docker compose run --rm -T app wcs eval run --record-fixtures /data/eval-fixtures   # saved in /data/eval/runs/
docker compose run --rm -T app wcs eval compare /data/eval/runs/<run A>.json /data/eval/runs/<run B>.json
docker compose run --rm -T app wcs eval replay /data/eval-fixtures                  # the checks again, free
docker compose run --rm -T --no-deps app ls /data/eval/runs                         # the runs kept
docker compose cp app:/data/eval/runs/<run>.json .                                  # a copy of one, out of the volume
```

Run it before and after a change to the model, the instructions, the ranking or the checks. A run that asks the
whole set and meets `[eval.thresholds]` is noted in `passed.jsonl` in the same folder.

* Everything a command writes on the server must be under `/data`: the code's folder (`/app`) is read-only in the
  container, and the container is removed when the command ends. Give `--record-fixtures` and `--out` paths under
  `/data`. Runs are not in the nightly backup, which holds only the state database.
* A run's spending is not in `wcs usage` and counts towards neither cap: its questions go straight to the
  pipeline. The run has its own limit (`[eval] max_cost_usd`, or `--max-cost`), stops cleanly when its spending
  reaches it, and prints what it spent (`cost_usd` in the saved run). Add that by hand when checking the
  provider's bill against `wcs usage`. The whole set cost $8.40 on 19 November 2026.

## The state database

`data/state.db` is the one thing that cannot be rebuilt from the UN's records. Its tables belong to wts-core and are
named `wts_…`: spending, the answer cache, saved briefings, notes of failed briefings, the record of each briefing,
reports, statements and their log, hidden items, and the day's visitor secret. It is backed up every night
([DEPLOY.md](DEPLOY.md#backups)).

### The move onto wts-core

Before wts-core, the site kept the same things in tables of its own: `usage`, `kv`, `answer_cache`, `failures`,
`briefings`, `reports` and `statements` (the old tables). The first time the site started on wts-core it copied
their rows into the `wts_` tables, once, in one transaction. It first kept a copy of the file as
`/data/state.pre-wts-core.<date>.db`, which `wcs tidy` deletes after 60 days. A backup from before the move can
still be restored and is brought across the same way.

The move is all or nothing. Only one kind of row can stop it: a statement whose log cannot be read as a list of
entries. With such a row the site does not start, nothing is half copied, and `docker compose logs app` shows the
error. Mend the row, and the site makes the move at its next start:

```bash
cd /opt/wherecountriesstand/deploy
docker compose run --rm --no-deps -T app python -c "import sqlite3, sys; con = sqlite3.connect('/data/state.db'); con.execute(sys.argv[1]); con.commit()" \
  "UPDATE statements SET log = '[]' WHERE id = 7"
```

The old tables are kept until 1 February 2027 so that the move can be undone, and nothing else reads them. Each
night `wcs tidy` holds them to the privacy notice: it blanks a statement's confirming address and a report's
contact address once the site has deleted its own copy, deletes old notes of failed briefings after 90 days and old
cached answers after 8, and deletes old visitor secrets and the visitor's code and time of day on old spending rows
of past days. It says how many rows it changed and the day the tables go. The first backup after the move still
holds the old spending rows as the old code left them, until that night's tidy.

On 1 February 2027 the first `wcs tidy` or start of the site drops the old tables. From then on, a backup from
before the move is no longer accepted by `wcs restore`, and there is no way back to the old code.

### Going back, and coming forward again

Until 1 February 2027 the site can go back to its code from before wts-core: the last version before the move,
kept in the private archived repository `rj-mitchell/wherecountriesstand_old`. This has not been rehearsed on the server.

1. Stop the site and run `deploy/undo-wts-core.sql` (the commands are at the top of the file). It writes the old
   tables again from the `wts_` tables, so the old code finds everything asked, sent and decided since the move,
   and then forgets the move.
2. Deploy the old code.

What the old tables cannot hold is folded into what they can (a removed statement becomes withdrawn, a hidden item
is shown again), and what wts-core has already forgotten stays forgotten; the script's heading lists both. Coming
forward again, the new code makes the move afresh from the old tables at its first start, with whatever the old
code took in meanwhile.

If the old code is started without running the undo script first, what it writes goes to the old tables only. The
new code, deployed again, then refuses to start: its log says "the site's old tables hold rows that are newer than
the import that copied them" and names two scripts, each of which discards something.

* `deploy/redo-wts-core.sql` keeps what the old code wrote and discards what the new code wrote between the move
  and going back. Use it when the old code has been running.
* `deploy/undo-wts-core.sql` keeps what the new code wrote and discards what the old code wrote since the move.
  Use it only for the false alarm below.

Either way: stop the site, run the script, start the site. Each script runs only on a database that has been moved,
and only once; run wrongly, it stops at its first step and changes nothing. After 1 February 2027 both refuse ("no
such table").

The check that stops the new code cannot see a row the old code wrote in the very second of the move, a row with no
time, or a change to a row that was already there (a report closed, a statement decided, a briefing withdrawn). It
can also raise a false alarm: if the server's clock was put back between the old code's last write and the move, the
site stops though nothing was lost. The mend loses nothing: run the undo script, then start the site again.
