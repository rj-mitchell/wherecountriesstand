"""Write a small, made-up votes file into a data folder, so that a site started with no data has vote pages to
check (CI's accessibility check, decision 26). Usage: python scripts/sample_votes.py /tmp/wcs-data

Two votes, each with every Member State of 2025 voting one way or another, in the voting data's own form
(``wcs.store.parquet.VOTE_SCHEMA``). They are labelled as samples, and never go near the live site's data.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from wcs.reference.countries import countries
from wcs.store.parquet import write_votes

ON = (date(2025, 12, 3), date(2025, 12, 10))


def rows() -> list[dict]:
    c = countries()
    members = sorted(x.code for x in c.by_code.values() if x.kind == "member_state")
    out = []
    for n, (on, symbol) in enumerate(zip(ON, ("A/RES/80/901", "A/RES/80/902"), strict=True), 1):
        how = {code: "YNAX"[(i * (n + 2)) % 7 % 4] for i, code in enumerate(members)}
        totals = {k: sum(1 for v in how.values() if v == k) for k in "YNAX"}
        for code in members:
            out.append({"undl_id": f"990000{n}", "country_code": code, "country_name": c.name_on(code, on).upper(),
                        "vote": how[code], "date": on, "session": 80, "resolution": symbol,
                        "draft": f"A/80/L.9{n}", "meeting": f"A/80/PV.9{n}",
                        "title": f"Sample resolution {n} for the accessibility check : resolution / adopted by the "
                                 "General Assembly",
                        "agenda_title": "A sample agenda item.", "subjects": "SAMPLES", "vote_note": None,
                        "total_yes": totals["Y"], "total_no": totals["N"], "total_abstentions": totals["A"],
                        "total_non_voting": totals["X"], "total_ms": len(members),
                        "url": f"https://digitallibrary.un.org/record/990000{n}"})
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/sample_votes.py <data folder>")
    path = write_votes(Path(sys.argv[1]) / "parquet", rows())
    print(f"wrote {path}")
