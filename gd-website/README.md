# General Debate statements read from the UN's website, for import

The UN's General Debate website (gadebate.un.org) refuses the live server, so these were read on 4 November 2026
from a network it doesn't refuse, by the same code (`wcs.sources.gadebate`, one request at a time with a pause).
On the server:

    docker compose run --rm -T app wcs import-gd-website \
      https://raw.githubusercontent.com/rj-mitchell/wherecountriesstand/data/gd-website/gd-website/records-80.json \
      https://raw.githubusercontent.com/rj-mitchell/wherecountriesstand/data/gd-website/gd-website/records-81.json
    docker compose run --rm -T app wcs update --no-votes --no-sc

| Session | Statements | Delegation's text | AI transcript |
|---|---|---|---|
| 80th (September 2025) | 191 | 101 | 90 |
| 81st (September 2026) | 193 | 124 | 69 |

Each is labelled as not yet the official record, and the official record replaces it when published.
