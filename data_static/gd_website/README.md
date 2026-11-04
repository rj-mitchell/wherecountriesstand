# General Debate statements read from the UN's website elsewhere

The UN's General Debate website (gadebate.un.org) refuses the live server, so these statements were read on
4 November 2026 from a network it doesn't refuse, by the same code (`wcs.sources.gadebate`, one request at a time
with a pause). `wcs update` imports each file once (`[live] gd_bundled` in config/settings.toml) and stores them like
the server's own readings: labelled as not yet the official record, and replaced by the official record, statement
by statement, when it is published.

| File | Session | Statements | Delegation's text | AI transcript |
|---|---|---|---|---|
| records-80.json | 80th (September 2025) | 191 | 101 | 90 |
| records-81.json | 81st (September 2026) | 193 | 124 | 69 |

To refresh them, read the website from such a network (`gadebate.update` into a scratch data folder) and replace the
files; a changed file is imported again.
