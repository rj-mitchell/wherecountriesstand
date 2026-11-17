"""General Assembly voting data from the UN Dag Hammarskjöld Library (non-commercial use, with attribution).

UN Digital Library record 4060887: one row per Member State per resolution adopted by recorded vote, from 1946.
Votes on separate paragraphs and on drafts that were not adopted are not included. The code GER (the Federal
Republic of Germany before 1990) is stored as DEU; China's seat before 25 October 1971 keeps CHN, as in the data,
and is labelled the Republic of China where it is shown (``reference.countries``).
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import duckdb
import httpx

from wcs.config import Settings
from wcs.store.parquet import votes_path

log = logging.getLogger(__name__)


def raw_dir(settings: Settings) -> Path:
    return settings.raw_dir / "votes"


def file_url(settings: Settings) -> str:
    """The current CSV's address, read from the record's MARC export (the file name carries its date)."""
    record = settings.sources["votes_record"].rstrip("/")
    xml = httpx.get(f"{record}/export/xm", timeout=60, follow_redirects=True).text
    for url in re.findall(r"<subfield code=\"u\">([^<]+\.csv)</subfield>", xml):
        return url
    raise RuntimeError("no CSV file listed in the voting data record")


_FILE_DATE = re.compile(r"(?<!\d)(\d{4})[_-](\d{1,2})[_-](\d{1,2})(?!\d)")


def file_date(name: str) -> date | None:
    """The date in a file's name ("2026_10_15_votes_ga.csv"). The month and day are not always two digits, so the
    names do not sort by date."""
    m = _FILE_DATE.search(name)
    try:
        return date(*map(int, m.groups())) if m else None
    except ValueError:
        return None


def fetch(settings: Settings, force: bool = False) -> Path:
    """Download the current CSV (about 370 MB), then delete the ones it supersedes."""
    url = file_url(settings)
    out = raw_dir(settings) / url.rsplit("/", 1)[-1]
    if out.exists() and not force:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    log.info("downloading %s", url)
    with httpx.stream("GET", url, follow_redirects=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
    tmp.replace(out)
    for old in superseded(settings, out):
        log.info("deleting %s, superseded by %s", old.name, out.name)
        old.unlink()
    return out


def superseded(settings: Settings, current: Path) -> list[Path]:
    """The other CSVs with an earlier date in their name than ``current``'s (one with no date is left alone)."""
    new = file_date(current.name)
    if new is None:
        return []
    return [p for p in raw_dir(settings).glob("*.csv")
            if p != current and (d := file_date(p.name)) is not None and d < new]


def latest_csv(settings: Settings) -> Path | None:
    """The CSV with the latest date in its name (by date, not by name)."""
    found = list(raw_dir(settings).glob("*.csv"))
    return max(found, key=lambda p: (file_date(p.name) or date.min, p.name)) if found else None


def ingest(settings: Settings, csv_path: Path | None = None) -> int:
    """Convert the CSV to data/parquet/votes.parquet; returns the number of votes."""
    csv_path = csv_path or latest_csv(settings)
    if csv_path is None:
        raise FileNotFoundError(f"no voting data CSV in {raw_dir(settings)}: run `wcs fetch-votes`")
    out = votes_path(settings.parquet_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    con = duckdb.connect()

    def num(col: str) -> str:
        return f"TRY_CAST(TRY_CAST({col} AS DOUBLE) AS INTEGER)"

    con.execute(f"""
        COPY (
          SELECT undl_id,
                 CASE ms_code WHEN 'GER' THEN 'DEU' ELSE ms_code END AS country_code,
                 ms_name AS country_name, ms_vote AS vote, TRY_CAST(date AS DATE) AS date,
                 TRY_CAST(session AS INTEGER) AS session, resolution, draft, meeting, title, agenda_title,
                 subjects, vote_note, {num('total_yes')} AS total_yes, {num('total_no')} AS total_no,
                 {num('total_abstentions')} AS total_abstentions, {num('total_non_voting')} AS total_non_voting,
                 {num('total_ms')} AS total_ms, undl_link AS url
          FROM read_csv(?, header = true, all_varchar = true)
          ORDER BY date, resolution, country_code
        ) TO '{tmp}' (FORMAT parquet, COMPRESSION zstd)""", [str(csv_path)])
    tmp.replace(out)
    return con.execute(f"SELECT count(*) FROM read_parquet('{out}')").fetchone()[0]
