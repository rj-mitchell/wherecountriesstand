"""Countries, their UN names and status, and the groups they belonged to on a date.

Sources, all in data_static/:

* ``m49.csv``: the UN Statistics Division's M49 standard: official English short names, ISO codes, regions, and
  the current lists of least developed, landlocked developing and small island developing States
  (``scripts/fetch_m49.py``);
* ``un_members.csv``: every code in the UN's General Assembly voting data, with its first and last recorded vote
  (``scripts/build_members.py``). A code with a vote in the latest year is a current member;
* ``states.csv``: former member States, observer States and others, with dates and successors;
* ``aliases.csv``: other labels the records use for a country ("Cote D'ivoire", "Slovak Republic");
* ``memberships.csv``: dated membership of the permanent five, the European Union and NATO.

Names follow the UN's own usage (docs/DECISIONS.md, 6). China's seat was held by the Republic of China until
25 October 1971 (General Assembly resolution 2758 (XXVI)); records of "China" before that date are labelled so.
Germany before unification on 3 October 1990 is the Federal Republic of Germany (the GDR sat separately).
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from wcs.config import STATIC_DIR

ROC_UNTIL = date(1971, 10, 25)
GERMAN_UNIFICATION = date(1990, 10, 3)
AU_MOROCCO_FROM = date(2017, 1, 30)
REGIONS = {"Africa": "region:africa", "Americas": "region:americas", "Asia": "region:asia",
           "Europe": "region:europe", "Oceania": "region:oceania"}
FAR_FUTURE = date(9999, 12, 31)


def norm(label: str | None) -> str:
    """Lower-case ASCII words only, without "the": for matching labels written in different ways."""
    s = unicodedata.normalize("NFKD", label or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"\bthe\b", " ", s)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


@dataclass
class Country:
    code: str
    name: str                       # UN short name today (or the former state's name)
    kind: str                       # member_state, former_member_state, observer_state, other_state, territory
    region: str | None = None
    subregion: str | None = None
    ldc: bool = False
    lldc: bool = False
    sids: bool = False
    valid_from: date | None = None
    valid_to: date | None = None
    successors: list[str] = field(default_factory=list)
    note: str = ""


class Countries:
    def __init__(self, static_dir: Path = STATIC_DIR):
        self.static_dir = Path(static_dir)
        self.by_code: dict[str, Country] = {}
        self._labels: dict[str, str] = {}
        self._memberships: dict[str, list[tuple[str, date, date]]] = {}
        self.group_labels: dict[str, str] = {}
        self._load()

    def _rows(self, name: str) -> list[dict[str, str]]:
        with open(self.static_dir / name, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def _load(self) -> None:
        members = {r["code"]: r for r in self._rows("un_members.csv")}
        latest = max(r["last_vote"] for r in members.values())[:4]
        for r in self._rows("m49.csv"):
            if not r["iso3"]:
                continue
            m = members.get(r["iso3"])
            kind = "member_state" if m and m["last_vote"][:4] == latest else "territory"
            self.by_code[r["iso3"]] = Country(
                r["iso3"], r["name"], kind, r["region"] or None, r["subregion"] or None,
                r["ldc"] == "1", r["lldc"] == "1", r["sids"] == "1")
        for r in self._rows("states.csv"):
            base = self.by_code.get(r["code"])
            self.by_code[r["code"]] = Country(
                r["code"], r["name"], r["kind"], base.region if base else None, base.subregion if base else None,
                base.ldc if base else False, base.lldc if base else False, base.sids if base else False,
                _date(r["valid_from"]), _date(r["valid_to"]),
                [s for s in r["successor"].split(";") if s], r["note"])
        # former states take the region of their (first) successor
        for c in self.by_code.values():
            if c.region is None and c.successors and c.successors[0] in self.by_code:
                c.region = self.by_code[c.successors[0]].region
                c.subregion = self.by_code[c.successors[0]].subregion
        for c in self.by_code.values():
            self._labels.setdefault(norm(c.name), c.code)
            # "Venezuela (Bolivarian Republic of)" is also written "Bolivarian Republic of Venezuela"
            m = re.fullmatch(r"(.+?) \((.+)\)", c.name)
            if m:
                self._labels.setdefault(norm(f"{m.group(2)} {m.group(1)}"), c.code)
                self._labels.setdefault(norm(m.group(1)), c.code)
        for code, m in members.items():
            for name in m["recorded_names"].split("|"):
                self._labels.setdefault(norm(name), "DEU" if code == "GER" else code)
        for r in self._rows("aliases.csv"):
            self._labels[norm(r["label"])] = r["code"]
        for r in self._rows("memberships.csv"):
            self._memberships.setdefault(r["code"], []).append(
                (r["group"], _date(r["valid_from"]) or date.min, _date(r["valid_to"]) or FAR_FUTURE))
        self.group_labels = {r["group"]: r["label"] for r in self._rows("groups.csv")}

    # ---- lookups ----------------------------------------------------------------------------------------------
    def resolve(self, label: str | None) -> str | None:
        """The code for a country label ("France", "Cote D'ivoire", "RUSSIAN FEDERATION"), or None if the label
        is not a state (an organisation, a UN office, a civil society group)."""
        if not label:
            return None
        if label in self.by_code:
            return label
        return self._labels.get(norm(label))

    def get(self, code: str | None) -> Country | None:
        return self.by_code.get(code or "")

    def name_on(self, code: str, on: date | None = None) -> str:
        """The UN name for a country on a date: "China (Republic of China)" for China's seat before 25 October
        1971, otherwise today's UN short name (or the former state's name)."""
        c = self.by_code.get(code)
        if c is None:
            return code
        if code == "CHN" and on is not None and on < ROC_UNTIL:
            return "China (Republic of China)"
        if code == "DEU" and on is not None and on < GERMAN_UNIFICATION:
            return "Federal Republic of Germany"
        return c.name

    def kind(self, code: str | None) -> str:
        c = self.by_code.get(code or "")
        return c.kind if c else "organisation"

    def groups_on(self, code: str | None, on: date) -> list[str]:
        """Groups a country belonged to on a date (region, the permanent five, EU, NATO, AU, LDC, LLDC, SIDS).
        Security Council membership comes from each meeting record instead (``participant``)."""
        c = self.by_code.get(code or "")
        if c is None or (c.valid_from and on < c.valid_from) or (c.valid_to and on >= c.valid_to):
            return []
        out: list[str] = []
        if c.region in REGIONS:
            out.append(REGIONS[c.region])
        for group, start, end in self._memberships.get(c.code, []):
            if start <= on < end:
                out.append(group)
        if c.region == "Africa" and c.kind in ("member_state", "former_member_state") and \
                (c.code != "MAR" or on >= AU_MOROCCO_FROM):
            out.append("au")
        out += [g for g, flag in (("ldc", c.ldc), ("lldc", c.lldc), ("sids", c.sids)) if flag]
        return sorted(set(out))

    def members_of(self, group: str, on: date | None = None) -> list[str]:
        """Codes in a group (on a date, if given)."""
        on = on or date.today()
        states = ("member_state", "former_member_state", "observer_state")
        return sorted(c.code for c in self.by_code.values() if c.kind in states and group in self.groups_on(c.code, on))

    def label(self, group: str) -> str:
        return self.group_labels.get(group, group)


@lru_cache(maxsize=4)
def countries(static_dir: Path = STATIC_DIR) -> Countries:
    return Countries(static_dir)
