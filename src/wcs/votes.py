"""Find General Assembly recorded votes on a question's subject, and how countries voted.

A vote is found by the words of its resolution's title, agenda item and subjects. Each vote found lists every
country's vote under the country's UN name on the day (China's seat before 25 October 1971 as the Republic of
China), so a briefing can set how countries voted beside what they said.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import duckdb

from wcs.config import Settings
from wcs.engine import Filters
from wcs.reference.countries import Countries, countries
from wcs.store.parquet import votes_path

STOP = set("""a an and are as at be been by did do does for from had has have how in into is it its of on or over
said say says the their them they this those to was were what when where which who why will with would about
countries country states state nations united general assembly security council vote votes voted voting resolution
resolutions position positions stand stance since between""".split())  # noqa: SIM905


def terms(text: str) -> list[str]:
    seen: list[str] = []
    for w in re.findall(r"[a-z][a-z'-]{2,}", (text or "").lower()):
        w = w.strip("'-")
        if w not in STOP and w not in seen:
            seen.append(w)
    return seen[:12]


class VoteFinder:
    def __init__(self, settings: Settings, reference: Countries | None = None):
        self.settings = settings
        self.countries = reference or countries()
        self.path = votes_path(settings.parquet_dir)
        self.con = duckdb.connect()

    def available(self) -> bool:
        return self.path.exists()

    def find(self, text: str, filters: Filters, limit: int = 3) -> list[dict[str, Any]]:
        words = terms(text)
        if not words or not self.available():
            return []
        need = len(words) if len(words) <= 2 else max(2, (len(words) + 1) // 2)
        hits = " + ".join(f"(CASE WHEN haystack LIKE '%{w.replace(chr(39), chr(39) * 2)}%' THEN 1 ELSE 0 END)"
                          for w in words)
        clauses, params = [], []
        if filters.date_from:
            clauses.append("date >= ?")
            params.append(filters.date_from)
        if filters.date_to:
            clauses.append("date <= ?")
            params.append(filters.date_to)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.con.execute(f"""
            WITH res AS (
              SELECT undl_id, any_value(resolution) resolution, any_value(date) date, any_value(title) title,
                     any_value(agenda_title) agenda_title, any_value(meeting) meeting, any_value(total_yes) total_yes,
                     any_value(total_no) total_no, any_value(total_abstentions) total_abstentions,
                     any_value(total_non_voting) total_non_voting, any_value(url) url,
                     lower(concat_ws(' ', any_value(title), any_value(agenda_title), any_value(subjects))) haystack
              FROM read_parquet('{self.path}') {where} GROUP BY undl_id)
            SELECT *, {hits} AS hits FROM res WHERE {hits} >= {need}
            ORDER BY hits DESC, date DESC LIMIT {int(limit)}""", params).fetchall()
        cols = [d[0] for d in self.con.description]
        out = []
        for row in rows:
            v = dict(zip(cols, row, strict=True))
            v.pop("haystack", None)
            v["title"] = re.sub(r"\s*:\s*resolution\s*/\s*adopted by the General Assembly\.?$", "", v["title"] or "")
            v["votes"] = self.votes_on(v["undl_id"], v["date"])
            out.append(v)
        return out

    def votes_on(self, undl_id: str, on: date) -> dict[str, list[str]]:
        rows = self.con.execute(f"SELECT country_code, vote FROM read_parquet('{self.path}') WHERE undl_id = ?",
                                [undl_id]).fetchall()
        out: dict[str, list[str]] = {}
        for code, vote in rows:
            out.setdefault(vote, []).append(self.countries.name_on(code, on))
        return {k: sorted(v) for k, v in out.items()}
