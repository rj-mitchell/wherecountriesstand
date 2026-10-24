"""Derive data_static/un_members.csv from the UN's General Assembly voting data.

For each country code in the voting data: the first and last dates on which it cast (or was recorded as not
casting) a vote, and every official name it was recorded under. These are dates of recorded votes, not dates of
admission to the UN; they are used to tell current members from former states, never shown as admission dates.

    uv run python scripts/build_members.py path/to/votes_ga.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import duckdb

OUT = Path(__file__).resolve().parents[1] / "data_static" / "un_members.csv"


def main(path: str) -> None:
    con = duckdb.connect()
    rows = con.execute(
        """SELECT ms_code, min(date)::DATE, max(date)::DATE, string_agg(DISTINCT ms_name, '|' ORDER BY ms_name)
           FROM read_csv(?, header = true, all_varchar = true) GROUP BY 1 ORDER BY 1""", [path]).fetchall()
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "first_vote", "last_vote", "recorded_names"])
        w.writerows(rows)
    print(f"wrote {len(rows)} codes to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
