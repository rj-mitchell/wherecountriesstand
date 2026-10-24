"""General Assembly voting data from the UN Dag Hammarskjöld Library (non-commercial use, with attribution).

UN Digital Library record 4060887: one row per Member State per resolution adopted by recorded vote, from 1946.
Votes on separate paragraphs and on drafts that were not adopted are not included. The code GER (the Federal
Republic of Germany before 1990) is stored as DEU; China's seat before 25 October 1971 keeps CHN, as in the data,
and is labelled the Republic of China where it is shown (``reference.countries``).
"""

from __future__ import annotations

import logging
import re
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


def fetch(settings: Settings, force: bool = False) -> Path:
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
    return out


def latest_csv(settings: Settings) -> Path | None:
    found = sorted(raw_dir(settings).glob("*.csv"))
    return found[-1] if found else None


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
