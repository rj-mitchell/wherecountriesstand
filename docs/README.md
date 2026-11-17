# Documentation

An index of Where Countries Stand's documents, with what each covers and who it is for. Start with the
[README](../README.md) for what the site is and how to run it locally.

| Document | What it covers | For |
|---|---|---|
| [DECISIONS.md](DECISIONS.md) | The product decisions and their reasons, numbered so that code can refer to them | Everyone |
| [DATA.md](DATA.md) | Data sources, their coverage and licences, what was found in them, and group memberships | Developers and researchers |
| [VALIDATION.md](VALIDATION.md) | How closely the parser for the UN's records matches the corpora, and a review of real briefings | Developers and researchers |
| [DESIGN.md](DESIGN.md) | The site's look and behaviour within the Where They Stand family, and the rules on the UN's name and emblem | Anyone changing the pages |
| [DEPLOY.md](DEPLOY.md) | Setting up the site on the shared server, deploying new code, spending caps, backups and caching | The maintainer |
| [OPERATIONS.md](OPERATIONS.md) | The scheduled update and its warnings, corrections and the right of reply, the record of briefings, test runs and the state database | The maintainer |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Setting up, the checks to run and the style to follow | Contributors |
| [outreach/README.md](../outreach/README.md) | Letters to outside bodies, and how they are sent | The maintainer |
| [data_static/gd_website/README.md](../data_static/gd_website/README.md) | General Debate statements read from the UN's website on another network | The maintainer |

Terms used across the documents:

- The corpora: the two academic datasets of UN speeches, the UN Security Council Debates and the UN General Debate
  Corpus, which supply the older years.
- The UN's records, or verbatim records: the UN's own meeting records (`S/PV.<n>` for the Security Council,
  `A/<session>/PV.<n>` for the General Assembly).
- wts-core, or the shared package: the Python package the Where They Stand sites share.
- The state database: `data/state.db`, which holds spending, saved briefings, reports and statements.
- The owner makes the product decisions; the maintainer runs the site and the server.
