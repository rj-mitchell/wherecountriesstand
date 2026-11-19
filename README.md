# Where Countries Stand

[![CI](https://github.com/rj-mitchell/wherecountriesstand/actions/workflows/ci.yml/badge.svg)](https://github.com/rj-mitchell/wherecountriesstand/actions/workflows/ci.yml)

Where Countries Stand answers questions about what countries have said, and how they have voted, at the United
Nations. Each answer is a short briefing, written by AI from the UN's records only, that groups the positions taken
and cites the record for every point. It is for students, journalists, researchers and anyone else who wants to know
where countries stand. The site is live at [wherecountriesstand.com](https://wherecountriesstand.com) and is in
alpha (November 2026). It is a project of [Where They Stand](https://wheretheystand.uk), which is applying to
register as a charity in England and Wales.

Where Countries Stand is independent. It is not affiliated with or endorsed by the United Nations, and it does not
use the UN's name or emblem.

## What it does

- Searches Security Council debates from 1992, the General Assembly's General Debate from 1946 (with rights of reply
  from 1993) and General Assembly recorded votes from 1946.
- Reads a question into filters (body, dates, countries, groups) and shows them, so the reader can see how the
  question was understood.
- Draws evidence from across groups of countries (by region, by bloc or by place at the Security Council), using
  each country's memberships on the date of the statement.
- Writes a cited briefing from those sources only, then checks the citations and quotations against the sources and
  flags unsupported sentences and loaded words. If nothing relevant is found it says "No records found".
- Marks speeches given in another language as the UN's translations, records recognised from scans, and General
  Debate statements that are not yet in the official record.
- Lets countries' delegations and people named in the record report an error or add a statement in their own words,
  under the charity's right-of-reply policy.

## How it works

Loaders turn two academic corpora, the UN's own verbatim records and the UN's voting data into Parquet files,
queried with DuckDB. Each passage is tagged with its speaker, the country or group they spoke for, and that
country's memberships on the date. LanceDB holds a keyword index and vectors from a local embedding model ("meaning
search"). A question is parsed into filters, evidence is retrieved, ranked by relevance, recency and the speaker's
role, and balanced across groups. Claude, from Anthropic, writes the briefing from those sources, and the result is
checked before it is shown. A scheduled job, `wcs update`, reads in new records twice a day.

What the Where They Stand sites share (settings, the state database, spending limits, the checks on briefings, the
model client, backups and the right of reply) comes from their common package,
[wts-core](https://github.com/rj-mitchell/wts-core). This repository holds what is particular to the UN's record:
the loaders and parsers, attribution, groups, retrieval and ranking, the instructions to the model, and the pages in
`web/`.

## Quick start

It needs Python 3.12 or later and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-extras                                  # the embed extra adds meaning search
uv run wcs serve                                      # http://127.0.0.1:8000
```

The site starts with no data and shows its empty states. To load data and try it from the command line:

```bash
uv run wcs fetch-sc && uv run wcs ingest-sc           # Security Council debates to 2023 (about 510 MB)
uv run wcs fetch-votes && uv run wcs ingest-votes     # General Assembly votes (about 370 MB)
uv run wcs update --no-index                          # the UN's own records from 2024
uv run wcs index                                      # keyword index
uv run wcs embed --max-seconds 3600                   # meaning search, newest years first; resumable
uv run wcs search "use of the veto" --since 2015
uv run wcs ask "How have small island developing States spoken about climate and security since 2018?"
```

`wcs ask` shows the filters and sources without an API key, and writes the briefing when `WCS_ANTHROPIC_API_KEY`
(or `ANTHROPIC_API_KEY`) is set. The General Debate corpus needs a Harvard Dataverse guestbook entry: set
`WCS_GUESTBOOK_NAME` and `WCS_GUESTBOOK_EMAIL`, then run `uv run wcs fetch-gd` and `uv run wcs ingest-gd`.
`uv run wcs --help` lists every command.

To run the checks CI runs on the code:

```bash
uv run ruff check src tests scripts
uv run pytest                                         # offline: the model is faked
```

## Documentation

- [docs/README.md](docs/README.md): an index of the documents, with who each is for
- [CONTRIBUTING.md](CONTRIBUTING.md): setting up, checks and style for changes
- [docs/DATA.md](docs/DATA.md): data sources, coverage and licences
- [docs/DECISIONS.md](docs/DECISIONS.md): the product decisions and their reasons
- [docs/DEPLOY.md](docs/DEPLOY.md) and [docs/OPERATIONS.md](docs/OPERATIONS.md): running the live site
- [docs/EVALUATION.md](docs/EVALUATION.md): the test set's runs, and the baseline later runs are compared against

## Data sources and licences

| Source | Coverage | Licence |
|---|---|---|
| [UN Security Council Debates](https://doi.org/10.7910/DVN/KGVSYH) (Schoenfeld, Eckhard, Patz, van Meegdenburg and others) | 1992 to 2023 | CC0 |
| [UN General Debate Corpus](https://doi.org/10.7910/DVN/0TJX8Y) (Jankin, Baturo, Dasandi) | 1946 to 2023 as used here | CC0 |
| UN verbatim records (`S/PV`, `A/<session>/PV`) | From 2024, and rights of reply from 1993 | © United Nations, non-commercial use with attribution |
| [General Assembly voting data](https://digitallibrary.un.org/record/4060887) (UN Dag Hammarskjöld Library) | From 1946 | © United Nations, non-commercial use with attribution |
| [UN General Debate website](https://gadebate.un.org) | Recent statements until the official record is published | © United Nations |
| [UN M49 standard](https://unstats.un.org/unsd/methodology/m49/overview/) | Country names, regions and UN country lists | United Nations |

Group memberships (EU, NATO, African Union, Group of 77, Non-Aligned Movement, OIC and others) are compiled in
`data_static/` with their sources. [docs/DATA.md](docs/DATA.md) has the details.

## Known limitations

- Statements on behalf of a group are found by their wording near the start of a speech. A statement that does not
  say whom it speaks for is treated as national.
- Speakers' roles are rarely given in the Security Council record, so weighting by role mostly applies to the
  General Debate.
- The languages of General Debate speeches before 1993 are not known, so those speeches are not marked as
  translations.
- The parser for the UN's own records matches the corpus closely ([docs/VALIDATION.md](docs/VALIDATION.md)), but a
  briefer is occasionally classed as an organisation rather than a UN official, or the reverse.
- The UN publishes no joining dates for the Group of 77 or the regional groups, so some membership dates are
  approximate.
- There is no page for each country yet.

## Contributing

To report something wrong on the site, use its [corrections page](https://wherecountriesstand.com/corrections.html).
For code, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

The code is licensed under the GNU Affero General Public License v3.0 ([LICENSE](LICENSE)). Briefing text written by
the site is licensed under CC BY-NC 4.0, because the UN's terms for its documents are non-commercial. The data
sources keep their own licences, listed above. The Where Countries Stand name and mark, and the Where They Stand name
and spines, are not covered by these licences.
