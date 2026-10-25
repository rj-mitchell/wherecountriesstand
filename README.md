# Where Countries Stand

**Sourced briefings on what countries have said, and how they have voted, at the United Nations.**

Where Countries Stand applies the approach of [Where They Stand](https://wheretheystand.uk) to the UN. Ask a
question such as "How have small island States spoken about climate and security since 2018?" and get a short,
balanced briefing that groups the positions taken. Every point cites the verbatim UN record, with its document symbol,
and speeches given in other languages are marked as UN translations. If the record holds nothing relevant, the site
says "No records found."

It is a project of Where They Stand, a charitable incorporated organisation being registered in England and Wales.
The engine is adapted from Where MPs Stand's, the charity's Parliament site (now live as Where They Stand; same
licence, same author).

> Where Countries Stand is independent. It is not affiliated with or endorsed by the United Nations, and it does not
> use the UN's name or emblem.

## Status

The research engine works from the command line on the real record: the Security Council from 1992 (the corpus to
2023, then the UN's own verbatim records, kept current by `wcs update`: 753 meetings and 21,000 speeches from January
2024 to 19 October 2026), the General Assembly voting data (1946 to September 2026), and the General Debate (the
corpus to 2025, then the General Assembly's own records; until those are out, about a year after each debate, the
UN's General Debate website), with rights of reply from 2000. `wcs serve` runs the site (`web/site/`) and its API, with spending caps and rate limits; see
[`docs/DEPLOY.md`](docs/DEPLOY.md) to put it on a server.

| File | What it is |
|---|---|
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Product decisions made so far. They override everything else. |
| [`docs/DATA.md`](docs/DATA.md) | Data sources: coverage, size, licence, how to get them, and what was found in them |
| [`docs/DESIGN.md`](docs/DESIGN.md) | The look: the UN's printed Official Records, without the UN's name or emblem |
| [`docs/OPERATIONS.md`](docs/OPERATIONS.md) | Keeping the data current: the scheduled `wcs update`, how it survives changes on the UN's side, what its warnings mean |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Putting the site on a server: Docker, HTTPS, the first data load |
| [`docs/VALIDATION.md`](docs/VALIDATION.md) | How well the parser for the UN's own records matches the corpus |
| [`web/prototype/`](web/prototype/) | Static design prototype. Screenshots are in [`docs/img/`](docs/img/). |

## How it works

```
 Security Council debates (CC0), to 2023 ──┐
 UN verbatim records (S/PV), from 2024 ────┤  (wcs update: Digital Library + ODS, parsed from PDF)
 General Debate corpus (CC0), to 2025 ─────┤
 UN verbatim records (A/PV), from 2026 ────┤  (wcs update: the General Debate meetings of each session,
 UN General Debate website, stop-gap ──────┤   and the website's statements until the records are out)
                                           ├─► loaders ─► Parquet (zstd, by source and year) ─► DuckDB
 General Assembly votes (UN) ──────────────┘      │
                                          ▼
 UN M49 + data_static/ ─► countries: UN names on the date, groups on the date (region, P5, EU, NATO, AU, LDC,
                          LLDC, SIDS, place at a Council meeting)
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
* **Ranking.** Relevance (keyword and meaning) × recency × role, as in Where They Stand. Role means head of state or
  government, minister or representative, where the source gives it.

## Try it

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras
uv run wcs fetch-sc && uv run wcs ingest-sc          # Security Council debates (about 510 MB download)
uv run wcs fetch-votes && uv run wcs ingest-votes    # General Assembly votes (about 370 MB)
uv run wcs update --no-index                           # Security Council records since 2024 (first run: about 10 s a
                                                       # meeting) and General Debate records from 2026
uv run wcs evaluate                                    # score against the test set (eval/questions.toml)
uv run wcs index                                      # keyword index (about 40 seconds)
uv run wcs embed --max-seconds 3600                    # meaning search, newest years first (resumable; see below)
uv run wcs search "use of the veto" --since 2015
uv run wcs search "climate change security" --group sids
uv run wcs ask "How have small island States spoken about climate and security since 2018?"
```

Embedding runs at about 26 passages a second on 4 CPU cores, so all 346,000 Security Council passages take about
3.5–4 hours. It goes newest years first and can be stopped and resumed; keyword search works throughout, and meaning
search covers whatever has been embedded.

`wcs ask` shows the plan and the sources without an API key, and writes the cited briefing with one
(`WCS_ANTHROPIC_API_KEY`, or `ANTHROPIC_API_KEY`). The General Debate corpus is downloaded by hand (see
[`docs/DATA.md`](docs/DATA.md)), then loaded with `uv run wcs ingest-gd`.

```bash
uv run pytest        # tests (offline: the model is faked)
uv run ruff check src tests scripts
```

## Known limitations

* **Group statements are found by their wording**, in the opening of each speech. Most are found cleanly (the EU,
  the Non-Aligned Movement, the Nordic countries, the Arab Group, the African members of the Council), but a few names
  are cut short ("Pacific") or run on ("Nordic countries Denmark"), and a statement whose opening does not say whom it
  speaks for is treated as national.
* **Speakers' roles** (head of state, minister, representative) are rarely given in the Security Council record, so
  role weighting mostly applies to the General Debate.
* **The General Debate loader** is tested on a sample archive built to the corpus's file naming, not yet on the real
  download.
* **Security Council records since January 2024** come from the UN's own verbatim records (`wcs update`), parsed
  from the PDFs. Checked against the corpus on 35 meetings, they match closely ([`docs/VALIDATION.md`](docs/VALIDATION.md)),
  but a briefer is now and then classed as an organisation rather than a UN official, or the reverse.
* **The General Debate from 2026** comes first from the UN's General Debate website: the delegation's English
  text (as prepared, not as delivered) or an AI transcript of the interpretation, labelled as such, until the
  official record replaces it about a year later. The website's browser check could one day shut this route.
* **Group memberships** cover the regions, the permanent five, the EU, NATO, the African Union and the UN's lists of
  least developed, landlocked developing and small island developing States. The Group of 77, the Non-Aligned Movement
  and the Organisation of Islamic Cooperation are still to add.
* **The briefing step** is tested with a fake model; it has not yet been run against the real API from this repository.

## Licence

Code: [AGPL-3.0](LICENSE). Briefing text: CC BY-NC 4.0 (decision 8). The Security Council and General Debate corpora
are CC0; the General Assembly voting data is © United Nations, non-commercial use with attribution.
