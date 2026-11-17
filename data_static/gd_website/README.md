# General Debate statements read from the UN's website elsewhere

Statements from the UN's General Debate website (gadebate.un.org) for sessions whose official records are not yet
published. The website refuses the live server, so these were read on 4 November 2026 from a network it accepts, by
the same code (`wcs.sources.gadebate`, one request at a time with a pause). `wcs update` imports each file once
(`[live] gd_bundled` in `config/settings.toml`) and stores the statements like the server's own readings: labelled
as not yet the official record, and replaced by the official record, statement by statement, when it is published.

| File | Session | Statements | Delegation's text | AI transcript |
|---|---|---|---|---|
| records-80.json | 80th (September 2025) | 191 | 101 | 90 |
| records-81.json | 81st (September 2026) | 193 | 124 | 69 |

To refresh them, run `wcs.sources.gadebate.update` from a network the website accepts, with a scratch data folder
(`WCS_DATA_DIR`), and copy the `records-<session>.json` files it writes in `raw/gadebate/` over these. A changed
file is imported again.
