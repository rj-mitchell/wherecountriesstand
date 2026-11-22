"""Find recorded votes on a question's subject, and how countries voted: the General Assembly's, and the Security
Council's (decision 30).

A vote is found by the words of its resolution's title, agenda item and subjects (a Council draft that was not
adopted, by its agenda item, and for a veto also the words "veto" and "vetoed"). Each vote found lists every
country's vote under the country's UN name on the day (China's seat to 25 October 1971 as the Republic of
China), so a briefing can set how countries voted beside what they said.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from wts_core.adapters.duckdb import shared

from wcs.config import Settings
from wcs.engine import Filters
from wcs.reference.countries import Countries, countries
from wcs.store.parquet import council_votes_path, votes_path

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
        self.council_path = council_votes_path(settings.parquet_dir)
        # one per site, used by every request's thread: each thread gets its own cursor, so two questions at once
        # never read each other's results
        self.con = shared()

    def available(self) -> bool:
        return self.path.exists()

    def council_available(self) -> bool:
        return self.council_path.exists()

    def find(self, text: str, filters: Filters, limit: int = 3, every_word: bool = False,
             body: str = "General Assembly") -> list[dict[str, Any]]:
        """Resolutions whose title, agenda item or subjects contain the words of ``text`` (whole words, or their
        start: "sanction" finds "sanctions"; found in real briefings: "rise" was matching "enterprises").
        ``every_word``: all of them, for a vote shown alongside statements when the question did not ask about
        votes."""
        words = terms(text)
        if body == "Security Council":
            return self.find_council(words, filters, limit, every_word)
        if not words or not self.available():
            return []
        need = len(words) if every_word or len(words) <= 2 else max(2, (len(words) + 1) // 2)
        hits = " + ".join(f"(CASE WHEN regexp_matches(haystack, '\\b{re.escape(w).replace(chr(39), chr(39) * 2)}') "
                          f"THEN 1 ELSE 0 END)" for w in words)
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

    @staticmethod
    def _hits(words: list[str]) -> str:
        return " + ".join(f"(CASE WHEN regexp_matches(haystack, '\\b{re.escape(w).replace(chr(39), chr(39) * 2)}') "
                          f"THEN 1 ELSE 0 END)" for w in words)

    def find_council(self, words: list[str], filters: Filters, limit: int, every_word: bool) -> list[dict[str, Any]]:
        """Security Council votes (resolutions and drafts not adopted) whose description, agenda item or subjects
        contain the words, as ``find`` does for the General Assembly's."""
        if not words or not self.council_available():
            return []
        need = len(words) if every_word or len(words) <= 2 else max(2, (len(words) + 1) // 2)
        # a place the question names ("the war in Ukraine") is enough with it: the Council's agenda items name the
        # place ("Maintenance of peace and security of Ukraine"), not the question's other words
        places = [w for w in words if self.countries.resolve(w)]
        if places and not every_word:
            need = min(need, max(1, need - 1))
        clauses, params = [], []
        if filters.date_from:
            clauses.append("date >= ?")
            params.append(filters.date_from)
        if filters.date_to:
            clauses.append("date <= ?")
            params.append(filters.date_to)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        path = str(self.council_path).replace("'", "''")
        hits = self._hits(words)
        place_clause = f" AND ({self._hits(places)}) >= 1" if places and not every_word else ""
        rows = self.con.execute(f"""
            WITH res AS (
              SELECT vote_id, any_value(outcome) outcome, any_value(reason) reason, any_value(what) what,
                     any_value(coalesce(resolution, draft)) resolution, any_value(date) date, any_value(title) title,
                     any_value(agenda) agenda_title, any_value(meeting) meeting, any_value(total_yes) total_yes,
                     any_value(total_no) total_no, any_value(total_abstentions) total_abstentions,
                     any_value(total_non_voting) total_non_voting, any_value(result) result,
                     any_value(vetoed_by) vetoed_by, any_value(modality) modality, any_value(url) url,
                     lower(concat_ws(' ', any_value(title), any_value(agenda), any_value(subjects),
                                     CASE WHEN any_value(reason) = 'veto' THEN 'veto vetoed draft not adopted'
                                          WHEN any_value(outcome) = 'not adopted' THEN 'draft not adopted' END)) haystack
              FROM read_parquet('{path}') {where} GROUP BY vote_id)
            SELECT *, {hits} AS hits FROM res WHERE {hits} >= {need}{place_clause}
            ORDER BY hits DESC, date DESC LIMIT {int(limit)}""", params).fetchall()
        cols = [d[0] for d in self.con.description]
        out = []
        for row in rows:
            v = dict(zip(cols, row, strict=True))
            v.pop("haystack", None)
            from wcs.api.council_votes import resolution_title

            v["body"] = "Security Council"
            v["title"] = resolution_title(v["title"], v["agenda_title"]) if v["outcome"] == "adopted" else \
                (v["agenda_title"] or "")
            v["vetoed_by"] = [self.countries.name_on(c, v["date"]) for c in v["vetoed_by"] or []]
            v["votes"] = self.council_votes_on(v["vote_id"], v["date"])
            out.append(v)
        return out

    def council_votes_on(self, vote_id: str, on: date) -> dict[str, list[str]]:
        path = str(self.council_path).replace("'", "''")
        rows = self.con.execute(f"SELECT country_code, vote FROM read_parquet('{path}') WHERE vote_id = ? AND "
                                "country_code IS NOT NULL AND coalesce(modality, '') <> 'Without Vote'",
                                [vote_id]).fetchall()
        out: dict[str, list[str]] = {}
        for code, vote in rows:
            out.setdefault(vote, []).append(self.countries.name_on(code, on))
        return {k: sorted(v) for k, v in out.items()}

    def votes_on(self, undl_id: str, on: date) -> dict[str, list[str]]:
        rows = self.con.execute(f"SELECT country_code, vote FROM read_parquet('{self.path}') WHERE undl_id = ?",
                                [undl_id]).fetchall()
        out: dict[str, list[str]] = {}
        for code, vote in rows:
            out.setdefault(vote, []).append(self.countries.name_on(code, on))
        return {k: sorted(v) for k, v in out.items()}
