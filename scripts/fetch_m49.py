"""Download the UN Statistics Division's M49 standard and save it as data_static/m49.csv.

M49 gives each country or area its official English short name, ISO codes, geographic region and sub-region, and
whether it is a least developed country, a landlocked developing country or a small island developing state. The
page publishes the table as HTML only; this script reads the English table.

    uv run python scripts/fetch_m49.py
"""

from __future__ import annotations

import csv
import html
import re
import sys
from pathlib import Path

import httpx

URL = "https://unstats.un.org/unsd/methodology/m49/overview/"
OUT = Path(__file__).resolve().parents[1] / "data_static" / "m49.csv"
COLUMNS = {
    "Region Name": "region",
    "Sub-region Name": "subregion",
    "Intermediate Region Name": "intermediate_region",
    "Country or Area": "name",
    "M49 Code": "m49",
    "ISO-alpha2 Code": "iso2",
    "ISO-alpha3 Code": "iso3",
    "Least Developed Countries (LDC)": "ldc",
    "Land Locked Developing Countries (LLDC)": "lldc",
    "Small Island Developing States (SIDS)": "sids",
}


def cells(row_html: str) -> list[str]:
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", "", c)).split())
            for c in re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)]


def parse(page: str) -> list[dict[str, str]]:
    table = re.search(r'<table id\s*=\s*"downloadTableEN".*?</table>', page, re.S)
    if not table:
        raise SystemExit("English M49 table not found: the page layout may have changed")
    rows = [c for c in (cells(tr) for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0), re.S)) if c]
    header, body = rows[0], rows[1:]
    out = []
    for r in body:
        rec = dict(zip(header, r, strict=False))
        item = {COLUMNS[k]: rec.get(k, "") for k in COLUMNS}
        for flag in ("ldc", "lldc", "sids"):
            item[flag] = "1" if item[flag].strip().lower() == "x" else ""
        out.append(item)
    return out


def main() -> None:
    page = httpx.get(URL, timeout=60, follow_redirects=True).text
    rows = parse(page)
    if len(rows) < 240:
        raise SystemExit(f"only {len(rows)} rows: refusing to overwrite {OUT}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNS.values()))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["iso3"] or r["name"]))
    print(f"wrote {len(rows)} rows to {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
