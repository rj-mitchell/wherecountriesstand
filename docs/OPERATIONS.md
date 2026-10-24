# Keeping the data current

`wcs update` brings everything up to date in one run, and is meant to run on a schedule (twice a day). This page
explains what it does, how it is built to keep working as the UN's websites change, how to schedule it, and what to do
when it reports a problem.

## What a run does

1. **General Assembly votes.** Reads the voting data record in the UN Digital Library (its file list carries the
   file's date). If the UN has published a new file, downloads it and converts it (about 370 MB, 5 seconds).
2. **Security Council records** (from `[live] sc_from`, 1 January 2024; the corpus covers the years before):
   * finds meeting records newer than the last one held, in the Digital Library's catalogue, then by probing the
     UN's document system (ODS) for the next meeting numbers;
   * downloads each record's English PDF, parses it into speeches (`wcs.sources.verbatim`) and checks the result;
   * looks again for numbers still missing (for 60 days), and fetches provisional records again for up to a year,
     at most monthly, so corrected versions replace them;
   * parses every cached PDF again when the parser changes (no downloads);
   * stores each year that changed.
3. **General Debate** (from `[live] gd_from_session`, the 81st session of 2026; the corpus covers the sessions
   before): see [The General Debate](#the-general-debate) below.
4. **Index.** Re-indexes the years that changed, then embeds new passages for up to `--embed-seconds`.
5. **Report.** Writes `data/logs/update-<time>.json` and prints a summary. Exits 1 if something needs attention.

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

With systemd (the units are in `deploy/`):

```bash
sudo cp deploy/wcs-update.service deploy/wcs-update.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wcs-update.timer
systemctl list-timers wcs-update.timer        # when it runs next
journalctl -u wcs-update.service -n 50        # the last run's summary
```

Or with cron, twice a day:

```
41 5,17 * * * cd /opt/wherecountriesstand && WCS_DATA_DIR=/opt/wherecountriesstand/data .venv/bin/wcs update --embed-seconds 1800 >> data/logs/cron.log 2>&1
```

## When a run reports a problem

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
| `General Debate speeches found but no statements introduced` | The President's introductions are worded in a new way | Look at the record; adjust `INVITATION` or `ADDRESS` in `wcs.sources.unga`. |

## The General Debate

**Before the 81st session (2025 and earlier)** the General Debate comes from the corpus, which its authors update once
a year. Its download asks for a guestbook entry, so a new release is fetched by hand into `data/raw/ungd/` and loaded
with `wcs ingest-gd`, which loads only the sessions before `[live] gd_from_session`.

**From the 81st session (September 2026)** `wcs update` reads the debate from the General Assembly's own verbatim
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
  attribution does not ("President Pavel:"). Rights of reply and points of order are left out, as the corpus leaves
  them out.
* **Dates.** Each statement has its day, where the corpus has only the session.

**When the records appear.** About a year after the debate, and not in order: on 24 October 2026 the 80th session's
debate (September 2025) was not yet published, and the 79th's (September 2024) was. So each September's debate
reaches the site about a year later. Two sources publish sooner, the UN's General Debate website (statements as
delivered, with an AI transcript of the English interpretation) and its press releases (summaries), but both put
automated visitors through a browser check, so they are not used. Asking the UN for permission or a feed would close
the gap; see the open questions in `docs/DATA.md`.

**Checked on the 79th session (2024).** Run end to end on its records: all 11 debate meetings found and parsed,
192 statements, one for each Member State that spoke, plus the State of Palestine, the Holy See and the European
Union. The four Member States without a statement (Afghanistan, Brunei Darussalam, Myanmar and the Democratic
People's Republic of Korea, which spoke only in reply) have no introduction anywhere in the records. 116 statements
by heads of State or Government, 68 by ministers, 6 by heads of delegation.
