"""Rebuild three sets of dated memberships in data_static/memberships.csv (other groups' rows are kept):

* UN regional groups (``ungroup:*``), as the UN lists them (https://www.un.org/dgacm/en/content/regional-groups,
  read 3 November 2026), from each member's first recorded UN vote (approximate: the UN publishes no dates), with
  its special cases: Israel in the Western European and other States from May 2000; Türkiye in both that group and
  Asia-Pacific; the United States in that group for elections. Former members are in the group their successor is.
* World Bank income groups (``income:*``, and ``income:middle`` for either middle group), by the Bank's fiscal year:
  a classification applies from 1 July to 30 June (data_static/sources/OGHIST_2025_07_01.xlsx, the Bank's
  "Country Analytical History"; the newest edition is linked from its Country and Lending Groups page).
* African Union suspensions (``au_suspended``), from worldstatesmen.org's list of international organisations
  (read 3 November 2026). A suspended state is still a member; source titles say it was suspended on the date.

    uv run python scripts/build_groups.py
"""

from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

from openpyxl import load_workbook

from wcs.reference.countries import countries

ROOT = Path(__file__).resolve().parents[1]
MEMBERSHIPS = ROOT / "data_static" / "memberships.csv"
OGHIST = ROOT / "data_static" / "sources" / "OGHIST_2025_07_01.xlsx"
MINE = ("ungroup:", "income:", "au_suspended")

UN_GROUPS = {
    "ungroup:african": """Algeria; Angola; Benin; Botswana; Burkina Faso; Burundi; Cabo Verde; Cameroon; Central African Republic;
        Chad; Comoros; Congo; Côte d'Ivoire; Democratic Republic of the Congo; Djibouti; Egypt; Equatorial Guinea;
        Eritrea; Eswatini; Ethiopia; Gabon; Gambia; Ghana; Guinea; Guinea-Bissau; Kenya; Lesotho; Liberia; Libya;
        Madagascar; Malawi; Mali; Mauritania; Mauritius; Morocco; Mozambique; Namibia; Niger; Nigeria; Rwanda;
        Sao Tome and Principe; Senegal; Seychelles; Sierra Leone; Somalia; South Africa; South Sudan; Sudan; Togo;
        Tunisia; Uganda; United Republic of Tanzania; Zambia; Zimbabwe""",
    "ungroup:asia_pacific": """Afghanistan; Bahrain; Bangladesh; Bhutan; Brunei Darussalam; Cambodia; China; Cyprus;
        Democratic People's Republic of Korea; Fiji; India; Indonesia; Iran (Islamic Republic of); Iraq; Japan; Jordan;
        Kazakhstan; Kiribati; Kuwait; Kyrgyzstan; Lao People's Democratic Republic; Lebanon; Malaysia; Maldives;
        Marshall Islands; Micronesia (Federated States of); Mongolia; Myanmar; Naoero; Nepal; Oman; Pakistan; Palau;
        Papua New Guinea; Philippines; Qatar; Republic of Korea; Samoa; Saudi Arabia; Singapore; Solomon Islands;
        Sri Lanka; Syrian Arab Republic; Tajikistan; Thailand; Timor-Leste; Tonga; Türkiye; Turkmenistan; Tuvalu;
        United Arab Emirates; Uzbekistan; Vanuatu; Viet Nam; Yemen""",
    "ungroup:eastern_european": """Albania; Armenia; Azerbaijan; Belarus; Bosnia and Herzegovina; Bulgaria; Croatia;
        Czechia; Estonia; Georgia; Hungary; Latvia; Lithuania; Montenegro; North Macedonia; Poland;
        Republic of Moldova; Romania; Russian Federation; Serbia; Slovakia; Slovenia; Ukraine""",
    "ungroup:latin_american_caribbean": """Antigua and Barbuda; Argentina; Bahamas; Barbados; Belize;
        Bolivia (Plurinational State of); Brazil; Chile; Colombia; Costa Rica; Cuba; Dominica; Dominican Republic;
        Ecuador; El Salvador; Grenada; Guatemala; Guyana; Haiti; Honduras; Jamaica; Mexico; Nicaragua; Panama;
        Paraguay; Peru; Saint Kitts and Nevis; Saint Lucia; Saint Vincent and the Grenadines; Suriname;
        Trinidad and Tobago; Uruguay; Venezuela (Bolivarian Republic of)""",
    "ungroup:western_european_other": """Andorra; Australia; Austria; Belgium; Canada; Denmark; Finland; France;
        Germany; Greece; Iceland; Ireland; Israel; Italy; Liechtenstein; Luxembourg; Malta; Monaco; Netherlands;
        New Zealand; Norway; Portugal; San Marino; Spain; Sweden; Switzerland; Türkiye;
        United Kingdom of Great Britain and Northern Ireland; United States of America""",
}
SPECIAL = {("ungroup:western_european_other", "ISR"): ("2000-05-01", "full member from May 2000 (temporary, made "
                                                                      "permanent in 2004); day not known"),
           ("ungroup:western_european_other", "USA"): (None, "not a member of any group; an observer of this one, "
                                                             "and counted in it for elections"),
           ("ungroup:western_european_other", "TUR"): (None, "in both this group and Asia-Pacific; in this one for "
                                                             "elections"),
           ("ungroup:asia_pacific", "TUR"): (None, "in both this group and the Western European and other States; "
                                                   "in that one for elections")}
FORMER = {"SUN": "ungroup:eastern_european", "YUG": "ungroup:eastern_european", "SCG": "ungroup:eastern_european",
          "CSK": "ungroup:eastern_european", "DDR": "ungroup:eastern_european", "YMD": "ungroup:asia_pacific"}

INCOME = {"L": "income:low", "LM": "income:lower_middle", "UM": "income:upper_middle", "H": "income:high"}

AU_SUSPENDED = [  # (country, from, to); to "" while it lasts
    ("CAF", "2013-03-25", "2016-03-31"), ("EGY", "2013-07-05", "2014-06-17"), ("GAB", "2023-08-31", "2025-04-30"),
    ("GIN", "2008-12-29", "2010-12-09"), ("GIN", "2021-09-10", "2026-01-22"), ("CIV", "2010-12-09", "2011-04-21"),
    ("MDG", "2002-07-10", "2003-07-10"), ("MDG", "2009-03-20", "2014-01-27"), ("MDG", "2025-10-14", ""),
    ("MLI", "2012-03-23", "2012-10-24"), ("MLI", "2020-08-19", "2020-10-09"), ("MLI", "2021-06-01", ""),
    ("MRT", "2005-08-04", "2007-04-10"), ("MRT", "2008-08-09", "2009-07-01"), ("NER", "2010-02-19", "2011-03-16"),
    ("NER", "2023-08-22", ""), ("SDN", "2019-06-06", "2019-09-06"), ("SDN", "2021-10-27", ""),
    ("TGO", "2005-02-25", "2005-05-27"), ("BFA", "2015-09-18", "2015-09-26"), ("BFA", "2022-01-31", ""),
    ("GNB", "2012-04-17", "2014-06-17"), ("GNB", "2025-11-28", ""), ("SSD", "2020-06-16", "2020-10-14"),
]


def first_votes() -> dict[str, str]:
    with open(ROOT / "data_static" / "un_members.csv", encoding="utf-8") as fh:
        return {r["code"]: r["first_vote"] for r in csv.DictReader(fh)}


def un_group_rows() -> list[list[str]]:
    c, votes, rows = countries(), first_votes(), []
    for group, names in UN_GROUPS.items():
        for name in (n.strip() for n in " ".join(names.split()).split(";")):
            code = c.resolve(name) or {"Naoero": "NRU", "Türkiye": "TUR"}.get(name)
            if not code:
                sys.exit(f"unresolved: {name}")
            start, note = SPECIAL.get((group, code), (None, ""))
            note = note or "from its first recorded UN vote (the UN publishes no dates of joining a group)"
            rows.append([group, code, start or votes.get(code, "1945-10-24"), "", note])
    for code, group in FORMER.items():
        st = c.get(code)
        rows.append([group, code, str(st.valid_from or "1945-10-24"), str(st.valid_to or ""), "former member State"])
    return rows


def income_rows() -> list[list[str]]:
    ws = load_workbook(OGHIST, read_only=True, data_only=True)["Country Analytical History"]
    rows_in = list(ws.iter_rows(values_only=True))
    fiscal = [str(v) for v in rows_in[4][2:] if v]            # "FY89" ... : 1 July of the year before
    c, out = countries(), []
    for row in rows_in[11:]:
        code = row[0]
        if not code or not c.get(code):
            continue
        runs: list[list] = []
        for fy, value in zip(fiscal, row[2:2 + len(fiscal)], strict=False):
            key = INCOME.get(str(value or "").strip().rstrip("*"))
            year = 1900 + int(fy[2:]) if int(fy[2:]) > 50 else 2000 + int(fy[2:])
            start = date(year - 1, 7, 1)
            if runs and runs[-1][0] == key and runs[-1][2] == start:
                runs[-1][2] = date(year, 7, 1)
            else:
                runs.append([key, start, date(year, 7, 1)])
        last = fiscal[-1]
        for i, (key, start, end) in enumerate(runs):
            if not key:
                continue
            open_end = i == len(runs) - 1 and end == date(2000 + int(last[2:]), 7, 1)
            note = "World Bank classification for its fiscal years (1 July to 30 June)"
            out.append([key, code, start.isoformat(), "" if open_end else end.isoformat(), note])
            if key in ("income:lower_middle", "income:upper_middle"):
                out.append(["income:middle", code, start.isoformat(), "" if open_end else end.isoformat(), note])
    # one "income:middle" run per spell, not one per level
    merged: dict[str, list[list[str]]] = {}
    for r in sorted((r for r in out if r[0] == "income:middle"), key=lambda r: (r[1], r[2])):
        last_run = merged.setdefault(r[1], [])
        if last_run and last_run[-1][3] == r[2]:
            last_run[-1][3] = r[3]
        else:
            last_run.append(r)
    return [r for r in out if r[0] != "income:middle"] + [r for runs in merged.values() for r in runs]


def main() -> None:
    with open(MEMBERSHIPS, encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    header, kept = rows[0], [r for r in rows[1:] if not r[0].startswith(MINE)]
    new = un_group_rows() + income_rows() + [
        ["au_suspended", code, a, b, "suspended from the African Union's activities (still a member)"]
        for code, a, b in AU_SUSPENDED]
    with open(MEMBERSHIPS, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows([header, *kept, *new])
    print(f"{len(new)} rows: {sum(r[0].startswith('ungroup:') for r in new)} UN regional groups, "
          f"{sum(r[0].startswith('income:') for r in new)} income groups, {len(AU_SUSPENDED)} AU suspensions")


if __name__ == "__main__":
    main()
