"""Write small, made-up votes files into a data folder, so that a site started with no data has vote pages to
check (CI's accessibility check, decisions 26 and 30). Usage: python scripts/sample_votes.py /tmp/wcs-data

Two General Assembly votes, each with every Member State of 2025 voting one way or another, in the voting data's own
form (``wcs.store.parquet.VOTE_SCHEMA``); and three Security Council votes by the Council's members of 2025 (a
resolution, a draft not adopted owing to a permanent member's negative vote and one that did not get the votes it
needed), in ``COUNCIL_VOTE_SCHEMA``; and two made-up statements of the General Assembly's plenary record in explanation
of the first Assembly vote (decision 31), so that the vote's page lists them and Kenya's page has its explanations of
vote. They are labelled as samples, and never go near the live site's data.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from wcs.reference.countries import countries
from wcs.store.parquet import write_by_year, write_council_votes, write_votes

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


COUNCIL = ("DZA", "CHN", "DNK", "FRA", "GRC", "GUY", "PAK", "PAN", "KOR", "RUS", "SLE", "SVN", "SOM", "GBR", "USA")


def council_rows() -> list[dict]:
    c = countries()
    out = []
    samples = (("990101", "S/RES/2901(2025)", None, "adopted", None, date(2025, 12, 5), "YYYYYYYYYYYYYAY", []),
               ("draft:S/2025/901", None, "S/2025/901", "not adopted", "veto", date(2025, 12, 12), "YNYYYAYYYYYYYYY",
                ["CHN"]),
               ("draft:S/2025/902", None, "S/2025/902", "not adopted", "votes", date(2025, 12, 19), "AYAAAYAAAYAAYAA",
                []))
    for n, (vote_id, res, draft, outcome, reason, on, how, vetoed) in enumerate(samples, 1):
        totals = {k: how.count(k) for k in "YNAX"}
        words = None if outcome == "adopted" else (
            f"The draft resolution received {totals['Y']} votes in favour, {totals['N']} against and {totals['A']} "
            "abstentions. The draft resolution has not been adopted, " + (
                "owing to the negative vote of a permanent member of the Council." if reason == "veto" else
                "having failed to obtain the required number of votes."))
        for code, v in zip(COUNCIL, how, strict=True):
            out.append({"vote_id": vote_id, "body": "Security Council", "outcome": outcome, "reason": reason,
                        "what": "resolution" if res else "draft resolution", "country_code": code,
                        "country_name": c.name_on(code, on).upper(), "permanent_member": code in (
                            "CHN", "FRA", "RUS", "GBR", "USA"), "vote": v, "date": on, "resolution": res,
                        "draft": draft or "S/2025/900", "amends": None, "meeting": f"S/PV.999{n}",
                        "title": f"Security Council resolution {res} [on a sample for the accessibility check]"
                        if res else None, "agenda": "A sample agenda item.", "subjects": "SAMPLES",
                        "vote_note": None, "result": words, "total_yes": totals["Y"], "total_no": totals["N"],
                        "total_abstentions": totals["A"], "total_non_voting": totals["X"], "total_ms": 15,
                        "modality": "Vote", "vetoed_by": vetoed, "source": "voting data" if res else "meeting record",
                        "complete": True, "url": f"https://digitallibrary.un.org/record/{vote_id}" if res else
                        f"https://undocs.org/en/{draft}"})
    return out


def plenary_rows() -> list[dict]:
    on = ON[0]
    out = []
    for n, (code, eov, text) in enumerate((
            ("KEN", "before", "Kenya will vote in favour of draft resolution A/80/L.91, a sample for the accessibility "
                              "check, because it speaks to the concerns of developing countries."),
            ("FRA", "after", "France voted in favour of resolution 80/901, a sample for the accessibility check. " * 8)), 1):
        out.append({"record_id": f"ga:A/80/PV.91:{n:04d}", "source": "ga", "body": "General Assembly", "date": on,
                    "date_precision": "day", "year": on.year, "session": 80,
                    "meeting": "80th session, 91st plenary meeting", "symbol": "A/80/PV.91", "speech_number": n,
                    "agenda": "Agenda item 999: A sample agenda item", "agenda_area": None, "speaker_name": "Sample",
                    "speaker_post": None, "speaker_role": "", "participant": "", "country_code": code,
                    "country_label": code, "entity_kind": "member_state", "on_behalf_of": None, "language": None,
                    "ocr": False, "text": text.strip(), "url": "https://undocs.org/en/A/80/PV.91",
                    "text_source": "record", "eov": eov, "explains": "A/RES/80/901"})
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python scripts/sample_votes.py <data folder>")
    path = write_votes(Path(sys.argv[1]) / "parquet", rows())
    print(f"wrote {path}")
    print(f"wrote {write_council_votes(Path(sys.argv[1]) / 'parquet', council_rows())}")
    print(f"wrote plenary records {write_by_year(Path(sys.argv[1]) / 'parquet', 'ga', plenary_rows())}")
