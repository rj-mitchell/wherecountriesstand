"""The UN-record pipeline, tested on synthetic catalogue entries and positioned lines (no PDFs, no network)."""

from datetime import date

import pytest

from wcs.reference.countries import countries
from wcs.sources import undocs
from wcs.sources.pdftext import Line, merge_fragments
from wcs.sources.verbatim import briefers_in, paragraphs, parse, split_chair
from wcs.store.parquet import read_year, record_path

MARC = """<?xml version="1.0" encoding="UTF-8"?>
<collection xmlns="http://www.loc.gov/MARC21/slim">
<record>
  <controlfield tag="001">4130925</controlfield>
  <controlfield tag="005">20261023120000.0</controlfield>
  <datafield tag="091" ind1=" " ind2=" "><subfield code="a">PRO</subfield></datafield>
  <datafield tag="191" ind1=" " ind2=" "><subfield code="a">S/PV.10247</subfield></datafield>
  <datafield tag="245" ind1=" " ind2=" "><subfield code="a">Security Council, 81st year :</subfield>
    <subfield code="b">10247th meeting, Monday, 19 October 2026, New York</subfield></datafield>
  <datafield tag="269" ind1=" " ind2=" "><subfield code="a">2026</subfield></datafield>
  <datafield tag="856" ind1=" " ind2=" "><subfield code="u">https://digitallibrary.un.org/record/4130925/files/S_PV.10247-EN.pdf</subfield><subfield code="y">English</subfield></datafield>
  <datafield tag="989" ind1=" " ind2=" "><subfield code="a">Documents and Publications</subfield><subfield code="b">Meeting Records</subfield><subfield code="c">Verbatim Records</subfield></datafield>
  <datafield tag="991" ind1=" " ind2=" "><subfield code="c">Children and armed conflict.</subfield></datafield>
  <datafield tag="992" ind1=" " ind2=" "><subfield code="a">2026-10-19</subfield></datafield>
</record>
<record>
  <controlfield tag="001">4129900</controlfield>
  <datafield tag="191" ind1=" " ind2=" "><subfield code="a">S/PV.10234</subfield></datafield>
  <datafield tag="245" ind1=" " ind2=" "><subfield code="a">Official communiqu&#233; of the 10234th (closed) meeting</subfield></datafield>
  <datafield tag="989" ind1=" " ind2=" "><subfield code="c">Verbatim Records</subfield></datafield>
  <datafield tag="992" ind1=" " ind2=" "><subfield code="a">2026-10-07</subfield></datafield>
</record>
<record>
  <datafield tag="191" ind1=" " ind2=" "><subfield code="a">S/PV.9220(Resumption1)</subfield></datafield>
  <datafield tag="245" ind1=" " ind2=" "><subfield code="a">Security Council, 77th year :</subfield></datafield>
</record>
</collection>"""


def test_catalogue_entries():
    items = {i["symbol"]: i for i in undocs.parse_marc(MARC.replace("&#233;", "é"))}
    rec = items["S/PV.10247"]
    assert rec["date"] == "2026-10-19" and rec["provisional"] and rec["verbatim"]
    assert rec["pdf_en"].endswith("S_PV.10247-EN.pdf") and rec["agenda"] == ["Children and armed conflict"]
    assert rec["modified"] == "20261023120000.0"
    assert not items["S/PV.10234"]["verbatim"]          # a closed meeting's communiqué
    assert "S/PV.9220 (Resumption 1)" in items
    assert undocs.meeting_number("S/PV.9220 (Resumption 1)") == 9220


# ---- positioned lines ---------------------------------------------------------------------------------------------

def ln(page, y, text, x=125.0, bold="", size=10.0, box=1, italic=0.0, zone="body"):
    b = 1.0 if bold and bold == text else (0.3 if bold else 0.0)
    return Line(page, x, y - 10, y, size, text, bold, italic, b, box, zone, x + 6 * len(text))


def record_lines(symbol="S/PV.10300", d="Monday, 19 October 2026, 3 p.m.", president="(Greece)",
                 body=None) -> list[Line]:
    front = [
        ln(0, 750, symbol, x=496, size=20, zone="header"),
        ln(0, 720, "Security Council", bold="Security Council", size=20),
        ln(0, 657, d),
        ln(0, 578, "President:", x=62, italic=1.0),
        ln(0, 577, "Mrs. Balta ......................................"),
        ln(0, 577, president, x=406),
        ln(0, 548, "Members:", x=62, italic=1.0),
        *[ln(0, 547 - 15 * i, f"{c} .......................................... Mx. Rep{i}")
          for i, c in enumerate(["Bahrain", "China", "Colombia", "Denmark", "France", "Latvia", "Liberia",
                                 "Pakistan", "Panama", "Russian Federation", "Somalia", "United States of America",
                                 "Democratic Republic of the Congo"])],
        ln(0, 342, "United Kingdom of Great Britain and"),
        ln(0, 331, "Northern Ireland . . Ms. Jambert-Gray"),
        ln(0, 325, "Agenda", x=60, bold="Agenda", size=12),
        ln(0, 311, "Children and armed conflict"),
        ln(0, 194, "This record contains the text of speeches delivered in English", size=8.5),
    ]
    return front + (body or default_body())


def default_body() -> list[Line]:
    p = 1
    rows = [
        ln(p, 740, "S/PV.10300", x=61, zone="header"),
        ln(p, 715, "The meeting was called to order at 3.05 p.m.", x=147, box=10),
        ln(p, 700, "Children and armed conflict", x=123, bold="Children and armed conflict", box=11),
        ln(p, 680, "The President: In accordance with rule 39 of the Council’s provisional", x=147,
           bold="The President", box=12),
        ln(p, 668, "rules of procedure, I invite the following briefers to participate in this", x=123, box=12),
        ln(p, 656, "meeting: Ms. Rosemary DiCarlo, Under-Secretary-General for Political and", x=123, box=12),
        ln(p, 644, "Peacebuilding Affairs; and Mr. Bankole Adeoye, Commissioner for Political", x=123, box=12),
        ln(p, 632, "Affairs, Peace and Security of the African Union.", x=123, box=12),
        ln(p, 620, "I give the floor to Ms. DiCarlo.", x=147, box=12),
        ln(p, 600, "Ms. DiCarlo: Children are paying the highest price in conflict today.", x=147,
           bold="Ms. DiCarlo", box=13),
        ln(p, 580, "Mr. Adeoye: The African Union is committed to protecting children.", x=147,
           bold="Mr. Adeoye", box=14),
        # an attribution split into fragments, out of order, as the layout analysis sometimes returns it
        ln(p, 560, "(France)", x=230, box=15),
        ln(p, 560, "Mrs. Ben Kraiem", x=147, bold="Mrs. Ben Kraiem", box=16),
        ln(p, 560, "(spoke in French): France supports", x=300, box=17, italic=0.3),
        ln(p, 548, "the resolution and calls for its full implementation.", x=123, box=17),
        ln(p, 528, "Mr. Klavinskis (Latvia): Latvia voted in favour.", x=147, bold="Mr.", box=18),
        ln(p, 508, "Mr. Banza (Democratic Republic of the Congo): We welcome the vote.", x=147,
           bold="Mr. Banza", box=19),
        ln(p, 488, "Mr. Kim (Republic of Korea): Korea speaks on behalf of the Group of Friends", x=147,
           bold="Mr. Kim", box=20),
        ln(p, 476, "of Children and Armed Conflict? No: I have the honour to speak for my country.", x=123,
           box=20),
        ln(p, 456, "The President: I shall now make a statement in my capacity as the", x=147,
           bold="The President", box=21),
        ln(p, 444, "representative of Greece. Greece attaches particular importance to this.", x=123, box=21),
        ln(p, 432, "I now resume my functions as President of the Council.", x=147, box=21),
        ln(p, 420, "There are no more names inscribed on the list of speakers.", x=147, box=21),
        ln(p, 400, "The meeting rose at 4.40 p.m.", x=147, box=22),
        ln(p, 380, "This line comes after the end and is ignored.", x=147, box=23),
        ln(p, 40, "2/9", x=60, zone="footer"),
    ]
    return rows


def test_front_matter_and_speeches():
    m = parse(merge_fragments(record_lines()), "S/PV.10300")
    assert m.meeting_date == date(2026, 10, 19)
    assert m.president == "Mrs. Balta" and m.president_country == "Greece"
    assert len(m.members) == 14 and "United Kingdom of Great Britain and Northern Ireland" in m.members
    assert m.agenda == ["Children and armed conflict"] and m.detection == "bold"
    speakers = [(s.speaker, s.label, s.language_note) for s in m.speeches]
    assert speakers == [
        ("The President", None, None), ("Ms. DiCarlo", None, None), ("Mr. Adeoye", None, None),
        ("Mrs. Ben Kraiem", "France", "(spoke in French)"), ("Mr. Klavinskis", "Latvia", None),
        ("Mr. Banza", "Democratic Republic of the Congo", None), ("Mr. Kim", "Republic of Korea", None),
        ("The President", None, None)]
    assert m.speeches[3].text.startswith("France supports the resolution")
    assert "ignored" not in " ".join(s.text for s in m.speeches)
    assert m.briefers["DiCarlo"].startswith("Under-Secretary-General")


def test_records_for_a_meeting(settings):
    m = parse(merge_fragments(record_lines()), "S/PV.10300")
    recs = undocs.records_for(m, {"symbol": "S/PV.10300"}, countries(), settings)
    by = [(r["entity_kind"], r["country_code"], r["participant"]) for r in recs]
    assert by[:3] == [("chair", None, "president"), ("un", None, "guest"), ("organisation", None, "guest")]
    assert recs[1]["country_label"] == "United Nations" and recs[1]["speaker_post"].startswith("Under-Secretary")
    assert recs[2]["country_label"] == "African Union"
    fra = next(r for r in recs if r["country_code"] == "FRA")
    assert fra["participant"] == "member" and fra["language"] == "French" and fra["entity_kind"] == "member_state"
    kor = next(r for r in recs if r["country_code"] == "KOR")
    assert kor["participant"] == "guest" and kor["on_behalf_of"] is None
    # the President's national statement is Greece's; the chair's remarks after it are the chair's
    assert [(r["entity_kind"], r["country_code"]) for r in recs[-3:]] == [
        ("member_state", "KOR"), ("member_state", "GRC"), ("chair", None)]
    grc = next(r for r in recs if r["country_code"] == "GRC")
    assert grc["text"].startswith("I shall now make a statement") and "resume" not in grc["text"]
    assert all(r["record_id"] == f"sc:S/PV.10300:{i:04d}" for i, r in enumerate(recs, 1))
    assert recs[0]["url"] == "https://undocs.org/en/S/PV.10300" and recs[0]["meeting"] == "10300th meeting"


def test_split_chair():
    parts = split_chair("I thank him. I shall now make a statement in my capacity as the representative of Chile. "
                        "Chile agrees. I now resume my functions as President of the Council. I call on Peru.")
    assert [k for k, _ in parts] == ["chair", "national", "chair"]
    assert parts[1][1] == "I shall now make a statement in my capacity as the representative of Chile. Chile agrees."
    assert split_chair("I give the floor to Japan.") == [("chair", "I give the floor to Japan.")]


def test_wording_fallback_without_bold_type():
    body = [ln(1, 715, "The meeting was called to order at 10 a.m.", x=147, box=1),
            ln(1, 700, "The President: I welcome the Ministers.", x=147, box=2),
            ln(1, 680, "Mr. Nebenzia (Russian Federation) (spoke in Russian): We object.", x=147, box=3)]
    m = parse(record_lines(body=body), "S/PV.10301")
    assert m.detection == "wording" and [s.label for s in m.speeches] == [None, "Russian Federation"]


def test_paragraphs_carry_sentences_over_columns():
    lines = [ln(1, 700, "First paragraph starts here and", x=147, box=1),
             ln(1, 688, "continues on this line.", x=123, box=1),
             ln(1, 676, "Second paragraph opens indented and", x=147, box=1),
             ln(1, 300, "it carries on in a new box after a column break.", x=312, box=2)]
    paras = paragraphs(lines)
    assert [p.text for p in paras] == ["First paragraph starts here and continues on this line.",
                                       "Second paragraph opens indented and it carries on in a new box after a column break."]


def test_briefers_in_invitations():
    assert briefers_in("In accordance with rule 39 of the Council’s provisional rules of procedure, I invite Mr. Hans "
                       "Grundberg, Special Envoy of the Secretary-General for Yemen, to participate in this meeting.") \
        == {"Grundberg": "Special Envoy of the Secretary-General for Yemen"}
    assert undocs.briefer("Ms. Tadesse", None, "Executive Director of Amani Africa") == ("organisation",
                                                                                        "Amani Africa")
    assert undocs.briefer("The Secretary-General", None, None) == ("un", "United Nations")


def test_looks_like_country():
    assert undocs.looks_like_country("Democratic People's Republic of Korea")
    assert not undocs.looks_like_country("civil society representative")
    assert not undocs.looks_like_country("Amani Africa Network")


# ---- the update job, with a fake UN ------------------------------------------------------------------------------

class FakeDocs:
    """The Digital Library lists some meetings; ODS has PDFs for the numbers in ``pdfs``."""

    def __init__(self, listed, pdfs):
        self.listed, self.pdfs, self.calls = listed, set(pdfs), []

    def library_meetings(self, after):
        return [m for m in self.listed if undocs.meeting_number(m["symbol"]) > after]

    def pdf(self, symbol, library_url=None):
        self.calls.append(symbol)
        return (b"%PDF-1.4 " + symbol.encode(), f"https://documents.un.org/doc/undoc/pro/{symbol}.pdf") \
            if symbol in self.pdfs else None


@pytest.fixture
def live(settings):
    """Discovery starts after meeting 10000 (no corpus in the test data folder)."""
    settings.raw["live"] = {"sc_from": "2024-01-01", "sc_after_meeting": 10000}
    return settings


@pytest.fixture
def fake_pdfs(monkeypatch):
    """Parsing a cached 'PDF' returns synthetic lines for its symbol."""
    def lines_for(path):
        symbol = path.read_bytes()[9:].decode()
        if symbol == "S/PV.10002":
            return [ln(0, 700, "Official communiqué of the 10002nd (closed) meeting")]
        n = int(symbol.split(".")[1])
        when = "Monday, 5 February 2024, 3 p.m." if n < 10003 else "Monday, 1 December 2025, 3 p.m."
        return merge_fragments(record_lines(symbol, when))
    monkeypatch.setattr(undocs, "read_lines", lines_for)


def test_update_fetches_parses_and_stores(live, fake_pdfs):
    settings = live
    from wcs.update import Report, update_security_council

    listed = [{"symbol": "S/PV.10001", "verbatim": True, "provisional": True, "date": "2024-02-05"},
              {"symbol": "S/PV.10002", "verbatim": True}]
    docs = FakeDocs(listed, {"S/PV.10001", "S/PV.10002", "S/PV.10003", "S/PV.10004"})
    report = Report(started="now")
    changed = update_security_council(settings, countries(), docs, report)
    assert report.fetched == ["S/PV.10001", "S/PV.10003", "S/PV.10004"]
    assert report.communiques == ["S/PV.10002"] and not report.failed
    assert changed == {2024, 2025}
    rows = read_year(settings.parquet_dir, "sc", 2024)
    assert {r["symbol"] for r in rows} == {"S/PV.10001"} and rows[0]["source"] == "sc"
    assert record_path(settings.parquet_dir, "sc", 2025).exists()
    man = undocs.Manifest.load(settings)
    assert man.entries["S/PV.10001"]["stored"] is True and man.entries["S/PV.10001"]["provisional"]
    # the ODS probe stopped after a run of numbers not found
    assert "S/PV.10016" in docs.calls and "S/PV.10017" not in docs.calls

    # a second run finds nothing new and fetches nothing again
    docs2 = FakeDocs(listed, docs.pdfs)
    report2 = Report(started="now")
    update_security_council(settings, countries(), docs2, report2)
    assert report2.fetched == [] and report2.refetched == []
    assert "S/PV.10001" not in docs2.calls


def test_update_keeps_looking_for_missing_numbers(live, fake_pdfs):
    settings = live
    from wcs.update import Report, update_security_council

    listed = [{"symbol": "S/PV.10001", "verbatim": True}, {"symbol": "S/PV.10003", "verbatim": True}]
    docs = FakeDocs(listed, {"S/PV.10001", "S/PV.10003"})
    report = Report(started="now")
    update_security_council(settings, countries(), docs, report)
    assert report.missing == ["S/PV.10002"]          # between two known meetings, not yet available
    docs.pdfs.add("S/PV.10002")
    report2 = Report(started="now")
    update_security_council(settings, countries(), docs, report2)
    assert "S/PV.10002" in report2.missing + report2.communiques  # tried again: now found (a communiqué)


def test_update_leaves_corpus_years_alone(live, fake_pdfs):
    settings = live
    from wcs.update import Report, update_security_council

    listed = [{"symbol": "S/PV.10001", "verbatim": True}]
    settings.raw["live"] = {"sc_from": "2025-01-01", "sc_after_meeting": 10000}
    report = Report(started="now")
    changed = update_security_council(settings, countries(), FakeDocs(listed, {"S/PV.10001"}), report)
    assert report.fetched == ["S/PV.10001"] and changed == set()   # a 2024 meeting: before sc_from
    assert not record_path(settings.parquet_dir, "sc", 2024).exists()


def test_refetch_rules():
    today = date.today().isoformat()
    fresh = {"status": "parsed", "provisional": True, "date": today, "fetched_at": "2020-01-01T00:00:00+00:00"}
    assert undocs.due_for_refetch(fresh, None)
    assert not undocs.due_for_refetch({**fresh, "provisional": False}, None)
    assert not undocs.due_for_refetch({**fresh, "date": "2020-01-01"}, None)
    changed = {"status": "parsed", "modified": "20260101", "fetched_at": "2020-01-01T00:00:00+00:00", "date": today}
    assert undocs.due_for_refetch(changed, {"modified": "20260201"})
    assert undocs.still_looking({"status": "missing", "missing_since": "2999-01-01T00:00:00+00:00"}) is True
    assert not undocs.still_looking({"status": "missing", "missing_since": "2000-01-01T00:00:00+00:00"})


def test_health_flags_stale_data():
    from wcs.update import Report, health

    r = Report(started="now", latest_meeting="2026-01-01", failed=["S/PV.1"])
    health(r, today=date(2026, 3, 1))
    assert any("no Security Council record newer" in w for w in r.warnings) and not r.ok
