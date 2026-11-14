# Where Countries Stand

**Sourced briefings on what countries have said, and how they have voted, at the United Nations.**

[wherecountriesstand.com](https://wherecountriesstand.com) · Status: Alpha

Ask a question such as "How have small island States spoken about climate and security since 2018?" and get a short,
balanced briefing that groups the positions taken, for anyone who wants to know where countries stand: students,
journalists, researchers and the curious. Every point cites the verbatim UN record, with its document symbol, and
speeches given in other languages are marked as UN translations. If the record holds nothing relevant, the site
says "No records found."

A Where They Stand project. Where They Stand is applying to register as a charity in England and Wales
([wheretheystand.uk](https://wheretheystand.uk)).

> Where Countries Stand is independent. It is not affiliated with or endorsed by the United Nations, and it does not
> use the UN's name or emblem.

## Status

6 November 2026. **Live in alpha** at [wherecountriesstand.com](https://wherecountriesstand.com), on the charity's
shared server, with written briefings switched on and spending caps of $3 a day and $50 a month. The record:

- the Security Council from 1992: the corpus to 2023, then the UN's own verbatim records, kept current twice a day
  by `wcs update`;
- the General Debate from 1946: the corpus to 2023, then the General Assembly's own records, and until those are
  out (about a year after each debate) the UN's General Debate website; rights of reply from 1993;
- General Assembly votes from 1946.

The home page shows each source's latest date, and the status page (`/status.html`, from `/api/status`) the last update and its warnings, and whether briefings are paused. Next: a page
for each country (which Where Lives Stand would link to), and testing with people who use assistive technology.

## In the family

- **Code:** adapted from Where MPs Stand's code (same licence). Parts of the API (saved briefings and reports) were
  adapted from Where Councils Stand's, which in turn adapted this site's engine.
- **Where it runs:** the charity's shared server, beside Where MPs Stand, Where Councils Stand and Where Landlords
  Stand; its front door, Cloudflare set-up, email alerts and backup bucket are described in Where MPs Stand's
  [`docs/HOSTING.md`](https://github.com/rj-mitchell/wherempsstand/blob/main/docs/HOSTING.md).
- **The binding:** the masthead's endorsement, the footer shelf linking every sister site, the horseshoe mark as the
  favicon and on the link card, `theme-color` in Record blue, and Source Serif 4 for headings, from the family kit in
  `web/site/family/`. The UN look and its
  UN blue stay for the content ([`docs/DESIGN.md`](docs/DESIGN.md)).
- **Links to sister sites:** under each briefing, "Ask Where MPs Stand the same question" (Where MPs Stand reads
  `?q=`), and for each country the question names, "Quality of life in … on Where Lives Stand", at
  `wherelivesstand.com/country/<ISO alpha-3>` (the address Where Lives Stand's redesign gives each country).
- **Links from sister sites:** the family shelf on every sister site's footer, and the hub. Where Lives Stand could
  link a country's dossier here once this site has country pages; until then, to a question about that country.

## Corrections and the right of reply

The charity's corrections and right-of-reply policy (decision 16): "If you're named" (`/named.html`, in every footer)
sets out the three routes for countries' delegations, missions and governments, and people named in the record, with
the response times ("We reply within 5 working days, and decide within 20 working days."). A statement in someone's
own words is held until the owner confirms who sent it (`wcs statements`, `docs/OPERATIONS.md`), then shown beside
its briefing, set apart from the site's text; it never reaches the model or the index. A briefing, or a record
of the archive, can be hidden with a note while a concern about it is decided (`wcs hide`).

## The shared engine

What this site does in the same way as its sister sites is not written here but taken from **wts-core**, the Where
They Stand sites' shared package ([rj-mitchell/wts-core](https://github.com/rj-mitchell/wts-core), AGPL-3.0; the
wheel of a release, named in `pyproject.toml`): settings, the state database, the limits and the spending record,
the checks on every briefing (citations, quotations, unsupported sentences, judgemental words, spelling), the flow
of a question from its arrival to its last event, the model client, the embedder and the search index's tables,
reports and the right of reply, backups, the record of each briefing with its statistics and weekly report, the
evaluation harness, and the shared CI. What is here is what follows the United Nations' record: the loaders and
parsers, who is speaking and for whom, the countries and groups on each date, the index's columns, how a question
is read, retrieval and ranking, the instructions to the model, the pages, and every word a reader is shown.
Decision 18 in [`docs/DECISIONS.md`](docs/DECISIONS.md) says why; `tests/test_parity.py` compares what the site
gives now with what its own code gave before the move.

## Documents

| Document | What it is |
|---|---|
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Product decisions made by the owner. They override everything else. |
| [`docs/DATA.md`](docs/DATA.md) | Data sources: coverage, size, licence, how to get them, and what was found in them |
| [`docs/DESIGN.md`](docs/DESIGN.md) | The look: the UN's printed Official Records, without the UN's name or emblem, and the family binding |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Keeping the data current: the scheduled `wcs update`, how it survives changes on the UN's side, what its warnings mean |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | This site's steps on the shared server: settings, the first data load, running it, backups |
| [`docs/VALIDATION.md`](docs/VALIDATION.md) | How well the parser for the UN's own records matches the corpus |
| [`outreach/`](outreach/) | Letters sent to the UN on the site's behalf, kept as a record |

## How it works

```
 Security Council debates (CC0), to 2023 ──┐
 UN verbatim records (S/PV), from 2024 ────┤  (wcs update: Digital Library + ODS, parsed from PDF)
 General Debate corpus (CC0), to 2025 ─────┤
 UN verbatim records (A/PV), from 2024 ────┤  (wcs update: the General Debate meetings of each session,
 UN General Debate website, stop-gap ──────┤   and the website's statements until the records are out)
                                           ├─► loaders ─► Parquet (zstd, by source and year) ─► DuckDB
 General Assembly votes (UN) ──────────────┘      │
                                          ▼
 UN M49 + data_static/ ─► countries: UN names on the date, groups on the date (region, P5, EU, NATO, AU, G77,
                          NAM, OIC, LDC, LLDC, SIDS, place at a Council meeting)
                                          │
                                          ▼
                        LanceDB: BM25 passages + vectors (local model), filtered by body, date, country,
                        group, kind of speaker and group statements
                                          │
 question ─► filter plan (offline rules, or Claude) ─► deterministic retrieval, ranking, balance by group
                                          │
                     Claude answers ONLY from the sources, with search-result citations
                                          ▼
                     verification: citations re-checked, quotations checked, loaded words flagged
```

* **Who is speaking.** Each speech is attributed to a country (its UN name on the date), an organisation, a UN
  official, or the presiding chair. The President of the Security Council presiding is never treated as their own
  country; "in my capacity as the representative of …" is. Statements made on behalf of a group ("I have the honour to
  speak on behalf of the Group of 77 and China") are kept as the group's, never as each member's (decision 7).
* **Groups on the date.** A passage carries the groups its country belonged to on the day: the United Kingdom is in
  the EU until 31 January 2020, Morocco in the African Union from 30 January 2017, the USSR a permanent member until
  24 December 1991. China before 25 October 1971 is labelled the Republic of China.
* **Balanced evidence.** The best-ranked candidates are grouped (by region by default; or by place at the Council; or
  by bloc) and drawn round-robin across the groups, with at most two contributions per country, so a briefing can
  compare groups instead of echoing one delegation.
* **Translations and scans.** Speeches given in another language are marked "(spoke in French)", with the English
  text noted as the UN's translation. Records before 1994 are text recognised from scans and are marked as such.
* **Ranking.** Relevance (keyword and meaning) × recency × role, as in Where MPs Stand. Role means head of state or
  government, minister or representative, where the source gives it.

## Try it

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras
uv run wcs fetch-sc && uv run wcs ingest-sc          # Security Council debates (about 510 MB download)
uv run wcs fetch-votes && uv run wcs ingest-votes    # General Assembly votes (about 370 MB)
uv run wcs update --no-index                           # Security Council records since 2024 (first run: about 10 s a
                                                       # meeting) and General Debate records from 2024
uv run wcs evaluate                                    # score against the test set (eval/questions.toml)
uv run wcs briefing-stats --since 7d                   # how briefings went: speed, cost, what the checks found
uv run wcs index                                       # keyword index (about 40 seconds)
uv run wcs embed --max-seconds 3600                    # meaning search, newest years first (resumable; see below)
uv run wcs optimize                                    # merge, compact and delete old index versions (updates do this)
uv run wcs search "use of the veto" --since 2015
uv run wcs search "climate change security" --group sids
uv run wcs ask "How have small island States spoken about climate and security since 2018?"
```

Embedding runs at about 26 passages a second on 4 CPU cores, so all 346,000 Security Council passages take about
3.5–4 hours. It goes newest years first and can be stopped and resumed; keyword search works throughout, and meaning
search covers whatever has been embedded.

`wcs ask` shows the plan and the sources without an API key, and writes the cited briefing with one
(`WCS_ANTHROPIC_API_KEY`, or `ANTHROPIC_API_KEY`). The General Debate corpus is downloaded with `uv run wcs fetch-gd`
(it asks for a guestbook entry: set `WCS_GUESTBOOK_NAME` and `WCS_GUESTBOOK_EMAIL`), then loaded with
`uv run wcs ingest-gd`.

```bash
uv run pytest        # tests (offline: the model is faked)
uv run ruff check src tests scripts
```

The pages are in `web/site/`; their masthead, notice and footer are written once in `web/partials/` and put into
each page by the server. `web/site/family/` is the Where They Stand family kit, copied in by its sync script (see
[`docs/DEPLOY.md`](docs/DEPLOY.md), "Pages and the family kit"); don't edit it. CI (`.github/workflows/ci.yml`)
runs ruff and the tests on every push and pull request, and checks every page for accessibility problems
with axe (`scripts/a11y.mjs`).

## Known limitations

* **Group statements are found by their wording**, in the opening of each speech, and each group is given one name
  however it is written ("Arab Group" and "Group of Arab States"; "Pacific" and "Pacific Islands Forum member
  States"). A statement whose opening does not say whom it speaks for is treated as national.
* **Speakers' roles** (head of state, minister, representative) are rarely given in the Security Council record, so
  role weighting mostly applies to the General Debate.
* **Languages in the General Debate before 1993** are not known: the corpus leaves out "(spoke in French)", and the
  UN's records, from which it is filled in, can be read from late 1993 (earlier ones are poor scans). Those speeches
  are not marked as translations.
* **Security Council records since January 2024** come from the UN's own verbatim records (`wcs update`), parsed
  from the PDFs. Checked against the corpus on 35 meetings, they match closely ([`docs/VALIDATION.md`](docs/VALIDATION.md)),
  but a briefer is now and then classed as an organisation rather than a UN official, or the reverse.
* **The General Debate from 2024** comes first from the UN's General Debate website: the delegation's English
  text (as prepared, not as delivered) or an AI transcript of the interpretation, labelled as such, until the
  official record replaces it about a year later. The website's browser check could one day shut this route.
* **Group memberships** cover the regions, the UN's regional groups, the permanent five, the EU, NATO, the African
  Union (with its suspensions), the Group of 77, the Non-Aligned Movement, the Organisation of Islamic Cooperation,
  the World Bank's income groups by year and the UN's lists of least developed, landlocked developing and small
  island developing States. The Group of 77 and the UN regional groups publish no dates of joining, so some are
  approximate ([`docs/DATA.md`](docs/DATA.md)).

## Licence

Code: AGPL-3.0 ([LICENSE](LICENSE)). Text written by the site (briefings): CC BY-NC 4.0 (decision 8), because the
UN's own terms for its documents are non-commercial, with attribution. Data: the Security Council and General
Debate corpora are CC0; the General Assembly voting data and the UN's records are © United Nations, non-commercial
use with attribution. The name and mark, and the Where They Stand name and spines, are not covered.
