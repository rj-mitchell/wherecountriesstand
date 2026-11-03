"""The General Debate from General Assembly records, tested on synthetic lines (no PDFs, no network)."""

from datetime import date

import pytest

from wcs.reference.countries import countries
from wcs.sources import undocs, unga
from wcs.sources.pdftext import Line, merge_fragments
from wcs.sources.verbatim import join_headings, parse
from wcs.store.parquet import read_year


def ln(page, y, text, x=123.0, bold="", italic=0.0, box=1, zone="body"):
    b = 1.0 if bold and bold == text else (0.3 if bold else 0.0)
    return Line(page, x, y - 10, y, 10.0, text, bold, italic, b, box, zone, x + 6 * len(text))


def ga_lines(symbol="A/81/PV.8", d="Tuesday, 22 September 2026, 3 p.m.", debate_heading=True, last=False):
    rows = [
        ln(0, 746, "United Nations", zone="header"),
        ln(0, 722, "General Assembly", bold="General Assembly"),
        ln(0, 702, "Eighty-first session"),
        ln(0, 676, "8th plenary meeting"),
        ln(0, 657, d),
        ln(0, 749, symbol, x=499, zone="header"),
        ln(0, 579, "President: Ms. Baerbock . . . . . . . . . . . . . . . . . . . . . . (Germany)", x=60),
        ln(0, 521, "The meeting was called to order at 3 p.m.", x=141, italic=1.0, box=2),
    ]
    if debate_heading:
        rows += [ln(0, 500, "Agenda item 8 (continued)", bold="Agenda item 8 ", italic=0.4, box=3),
                 ln(0, 488, "General debate", bold="General debate", box=4)]
    rows += [
        # a head of State: a heading of their own, wrapped over two lines, and an attribution without a country
        ln(0, 470, "Address by Mr. Petr Pavel, President of the Czech", bold="Address by Mr. Petr Pavel, President "
           "of the Czech", box=5),
        ln(0, 458, "Republic", bold="Republic", box=6, x=123),
        ln(0, 440, "The President: The Assembly will now hear an address by the President of", x=141,
           bold="The President", box=7),
        ln(0, 428, "the Czech Republic.", box=7),
        ln(0, 410, "Mr. Petr Pavel, President of the Czech Republic, was escorted into the", x=141, italic=1.0,
           box=8),
        ln(0, 398, "General Assembly Hall.", italic=1.0, box=8),
        ln(0, 380, "President Pavel: The Czech Republic stands by the Charter of the United", x=141,
           bold="President Pavel", box=9),
        ln(0, 368, "Nations and its principles.", box=9),
        ln(0, 350, "The President: On behalf of the General Assembly, I wish to thank the", x=141,
           bold="The President", box=10),
        ln(0, 338, "President of the Czech Republic for the statement he has just made.", box=10),
        ln(0, 326, "Mr. Petr Pavel, President of the Czech Republic, was escorted from the", x=141, italic=1.0,
           box=11),
        ln(0, 314, "General Assembly Hall.", italic=1.0, box=11),
        # a minister: invited by the President, named with their country
        ln(0, 296, "The Acting President (spoke in Spanish): I now give the floor to His Excellency", x=141,
           bold="The Acting President", box=12),
        ln(0, 284, "Mr. Bakary Yaou Sangaré, Minister for Foreign Affairs, Cooperation and", box=12),
        ln(0, 272, "Nigeriens Abroad of the Republic of the Niger.", box=12),
        ln(0, 254, "Mr. Sangaré (Niger) (spoke in French): The Niger calls for a just international", x=141,
           bold="Mr. Sangaré", box=13),
        ln(0, 242, "order.", box=13),
        # the President's invitation to the European Union
        ln(0, 224, "The Acting President: I now call on His Excellency Mr. António Costa, President", x=141,
           bold="The Acting President", box=14),
        ln(0, 212, "of the European Council of the European Union.", box=14),
        ln(0, 194, "Mr. Costa (European Union): The Union stands with the United Nations.", x=141,
           bold="Mr. Costa", box=15),
        # rights of reply: left out
        ln(1, 700, "The Acting President: Several representatives have asked to speak in exercise", x=141,
           bold="The Acting President", box=20),
        ln(1, 688, "of the right of reply. I remind members that statements are limited to ten minutes.", box=20),
        ln(1, 670, "Mr. Hilale (Morocco) (spoke in French): I exercise my right of reply to the statement made", x=141,
           bold="Mr. Hilale", box=21),
        ln(1, 658, "this morning by a neighbouring delegation, which contained claims that my delegation rejects in", box=21),
        ln(1, 646, "their entirety, and I ask that this reply be recorded in full by the Assembly.", box=21),
        # a point of order is not a reply
        ln(1, 640, "The Acting President: I give the floor to the representative of Algeria on a point of order.",
           x=141, bold="The Acting President", box=24),
        ln(1, 636, "Mr. Bendjama (Algeria) (spoke in French): May I ask the representative of Morocco to address the", x=141,
           bold="Mr. Bendjama", box=25),
        ln(1, 634, "President and not my delegation, as the rules of procedure require of every delegation taking the", box=25),
        ln(1, 632, "floor in this Hall during the exercise of the right of reply by any delegation?", box=25),
    ]
    if last:
        rows += [ln(1, 650, "The Acting President: We have heard the last speaker in the general debate.", x=141,
                    bold="The Acting President", box=22)]
    rows += [ln(1, 630, "The meeting rose at 6 p.m.", x=141, italic=1.0, box=23)]
    return merge_fragments(rows)


def test_join_headings():
    assert join_headings(["Agenda item 8 (continued)", "General debate", "Address by Mr. X, President of the",
                          "Republic of Chile"]) == ["Agenda item 8 (continued)", "General debate",
                                                    "Address by Mr. X, President of the Republic of Chile"]
    assert join_headings(["The situation in the Middle East", "Letter dated 1 May"]) == [
        "The situation in the Middle East", "Letter dated 1 May"]


def test_general_debate_statements(settings):
    m = parse(ga_lines(), "A/81/PV.8")
    assert m.meeting_date == date(2026, 9, 22)
    assert {sp.agenda for sp in m.speeches} == {"General debate"}
    pavel = next(sp for sp in m.speeches if sp.speaker == "President Pavel")
    assert pavel.heading == "Address by Mr. Petr Pavel, President of the Czech Republic"
    recs = unga.records_for(m, {}, countries(), settings)
    assert [r["record_id"] for r in recs] == ["gd:81:CZE", "gd:81:NER", "gd:81:EU", "gd:81:MAR:reply:8.009"]
    cze, ner, eu, mar = recs
    assert mar["agenda"] == unga.REPLY_AGENDA and mar["speaker_name"] == "Mr. Hilale" and unga.is_reply(mar)
    assert mar["text"].startswith("I exercise my right of reply") and mar["country_code"] == "MAR"
    assert cze["speaker_name"] == "Petr Pavel" and cze["speaker_post"] == "President of the Czech Republic"
    assert cze["speaker_role"] == "head" and cze["entity_kind"] == "member_state"
    assert cze["text"] == "The Czech Republic stands by the Charter of the United Nations and its principles."
    assert cze["date_precision"] == "day" and cze["session"] == 81 and cze["source"] == "gd"
    assert cze["meeting"] == "General Debate, 81st session" and cze["url"] == "https://undocs.org/en/A/81/PV.8"
    assert ner["speaker_name"] == "Bakary Yaou Sangaré" and ner["speaker_role"] == "minister"
    assert ner["language"] == "French"
    assert eu["entity_kind"] == "organisation" and eu["country_label"] == "European Union"
    assert unga.check(m, recs) == [] and not unga.concludes_debate(m)


def test_unheaded_meeting_uses_catalogue_or_president(settings):
    m = parse(ga_lines(debate_heading=False, last=True), "A/81/PV.9")
    assert unga.concludes_debate(m)  # and the President's words make it the debate
    assert len(unga.records_for(m, {}, countries(), settings)) == 4
    m2 = parse(ga_lines(debate_heading=False), "A/81/PV.9")
    assert unga.records_for(m2, {}, countries(), settings) == []
    assert len(unga.records_for(m2, {"agenda": ["General debate"]}, countries(), settings)) == 4


def test_country_in_posts():
    c = countries()
    assert unga.country_in("President of the Republic of Kenya and Commander-in-Chief of the Defence Forces", c) \
        == "KEN"
    assert unga.country_in("President of the Republic of GuineaBissau", c) == "GNB"
    assert unga.country_in("President of the French Republic", c) == "FRA"  # an alias
    assert unga.split_who("Mr. Joseph R. Biden, Jr., President of the United States of America") == (
        "Joseph R. Biden, Jr.", "President of the United States of America")
    assert unga.current_session(date(2026, 10, 24)) == 81 and unga.current_session(date(2027, 3, 1)) == 81


# ---- the update job, with a fake UN ------------------------------------------------------------------------------

class FakeDocs:
    def __init__(self, catalogue, pdfs):
        self.catalogue, self.pdfs, self.calls = catalogue, set(pdfs), []

    def search(self, query, rows=200):
        return [m for m in self.catalogue if m["symbol"].startswith(query.split(":", 1)[1].rstrip("*"))]

    def pdf(self, symbol, library_url=None):
        self.calls.append(symbol)
        return (b"%PDF-1.4 " + symbol.encode(), f"https://documents.un.org/doc/undoc/gen/{symbol}.pdf") \
            if symbol in self.pdfs else None


@pytest.fixture
def ga(settings, monkeypatch):
    settings.raw["live"] = {"sc_from": "2024-01-01", "gd_from_session": 81, "gd_website": False}

    def lines_for(path):
        symbol = path.read_bytes()[9:].decode()
        return ga_lines(symbol, last=symbol.endswith(".9"))
    monkeypatch.setattr(undocs, "read_lines", lines_for)
    return settings


def test_update_general_debate(ga):
    from wcs.update import Report, update_general_debate

    catalogue = [{"symbol": "A/81/PV.1", "verbatim": True, "agenda": ["Opening of the session"]},
                 {"symbol": "A/81/PV.8", "verbatim": True, "agenda": ["General debate"], "date": "2026-09-22"}]
    docs = FakeDocs(catalogue, {"A/81/PV.8"})
    report = Report(started="now")
    changed = update_general_debate(ga, countries(), docs, report, today=date(2026, 10, 24))
    assert changed == {2026} and report.fetched == ["A/81/PV.8"] and not report.failed
    assert "A/81/PV.1" not in docs.calls             # catalogued on other business: not fetched
    assert "A/81/PV.30" in docs.calls                # numbers not yet published are looked for on ODS
    assert report.gd[81] == {"meetings": 1, "statements": 3, "replies": 1, "concluded": False, "awaiting": 28,
                             "website": 0}
    assert sorted(r["record_id"] for r in read_year(ga.parquet_dir, "gd", 2026)) == [
        "gd:81:CZE", "gd:81:EU", "gd:81:MAR:reply:8.009", "gd:81:NER"]

    # a week later, the rest of the debate is out; the record that ends it stops the search
    man = undocs.Manifest.load(ga, "ga")
    for e in man.entries.values():
        if e.get("status") == "missing":
            e["checked_at"] = "2020-01-01T00:00:00+00:00"
    man.save()
    docs2 = FakeDocs(catalogue, {"A/81/PV.8", "A/81/PV.9"})
    report2 = Report(started="now")
    update_general_debate(ga, countries(), docs2, report2, today=date(2026, 10, 31))
    assert report2.fetched == ["A/81/PV.9"] and report2.gd[81]["concluded"]
    assert len(read_year(ga.parquet_dir, "gd", 2026)) == 8
    assert "gd:81:CZE:2" in {r["record_id"] for r in read_year(ga.parquet_dir, "gd", 2026)}

    # once concluded, nothing is fetched again
    docs3 = FakeDocs(catalogue, docs2.pdfs)
    update_general_debate(ga, countries(), docs3, Report(started="now"), today=date(2026, 11, 7))
    assert docs3.calls == []

    # but a record that failed is tried again, from the cache
    man = undocs.Manifest.load(ga, "ga")
    man.entries["A/81/PV.8"].update({"status": "failed", "attempts": 1})
    man.save()
    report4 = Report(started="now")
    update_general_debate(ga, countries(), FakeDocs(catalogue, docs2.pdfs), report4, today=date(2026, 11, 7))
    assert report4.fetched == ["A/81/PV.8"]


def test_missing_numbers_are_checked_weekly(ga):
    from wcs.update import Report, update_general_debate

    docs = FakeDocs([], set())
    update_general_debate(ga, countries(), docs, Report(started="now"), today=date(2026, 10, 24))
    assert len(docs.calls) == unga.GD_WINDOW
    docs2 = FakeDocs([], set())
    update_general_debate(ga, countries(), docs2, Report(started="now"), today=date(2026, 10, 24))
    assert docs2.calls == []                          # checked less than a week ago


def test_health_flags_overdue_debate():
    from wcs.update import Report, health

    r = Report(started="now", gd={81: {"meetings": 0, "statements": 0, "concluded": False, "awaiting": 30}})
    health(r, today=date(2027, 10, 1))
    assert not r.warnings
    health(r, today=date(2028, 1, 15))
    assert any("81th session" in w or "81st session" in w for w in r.warnings)


def test_backfilled_replies_sit_beside_the_corpus(ga):
    """Before gd_from_session the corpus has the statements: only replies are stored, and a corpus reload keeps them."""
    from wcs.store.parquet import write_by_year
    from wcs.update import Report, update_general_debate

    corpus = {"record_id": "gd:79:CZE", "source": "gd", "body": "General Assembly", "date": date(2024, 9, 1),
              "date_precision": "session", "year": 2024, "session": 79, "text": "The corpus statement."}
    write_by_year(ga.parquet_dir, "gd", [corpus])
    catalogue = [{"symbol": "A/79/PV.8", "verbatim": True, "agenda": ["General debate"], "date": "2024-09-24"}]
    report = Report(started="now")
    update_general_debate(ga, countries(), FakeDocs(catalogue, {"A/79/PV.8"}), report, sessions=range(79, 80))
    ids = sorted(r["record_id"] for r in read_year(ga.parquet_dir, "gd", 2024))
    assert ids == ["gd:79:CZE", "gd:79:MAR:reply:8.009"] and report.gd[79]["replies"] == 1
    write_by_year(ga.parquet_dir, "gd", [corpus], keep=unga.is_reply)   # `wcs ingest-gd` again
    assert len(read_year(ga.parquet_dir, "gd", 2024)) == 2


def test_recorded_statements_are_not_stored_as_their_introductions(settings):
    """Found in the records of 2020: the statement is printed in an annex; the record has only its introduction."""
    lines = ga_lines()
    for line in lines:
        if line.text.startswith("President Pavel:"):
            line.text = "President Pavel: I have the honour to introduce the pre-recorded statement by His Excellency"
    m = parse(merge_fragments(lines), "A/81/PV.8")
    recs = unga.records_for(m, {}, countries(), settings)
    assert "gd:81:CZE" not in [r["record_id"] for r in recs] and "gd:81:NER" in [r["record_id"] for r in recs]
    assert any("printed in annexes" in p for p in unga.check(m, recs))


def test_statements_printed_in_annexes(settings):
    """Found in the records of 2020: recorded statements are printed after the meeting, each in an annex."""
    lines = ga_lines()
    for line in lines:
        if line.text.startswith("President Pavel:"):
            line.text = "President Pavel: I have the honour to introduce the pre-recorded statement by His Excellency"
    rose = next(ln for ln in lines if "meeting rose" in ln.text)
    lines += [ln(2, 730, "2/9", zone="body"), ln(2, 705, "Annex I", x=60, bold="Annex I"),
              ln(2, 685, "Address by Mr. Petr Pavel, President of the Czech", bold="Address by Mr. Petr Pavel, "
                 "President of the Czech"),
              ln(2, 671, "Republic", bold="Republic"),
              ln(2, 655, "[Original: Czech; English translation provided by the", x=198),
              ln(2, 643, "delegation]", x=198),
              ln(2, 625, "Mr. President,", x=152), ln(2, 610, "The Czech Republic stands by the", x=152),
              ln(2, 598, "Charter of the United Nations.", x=123), ln(2, 585, "26-12345", x=500)]
    assert rose.page == 1
    m = parse(merge_fragments(lines), "A/81/PV.8")
    assert [a.number for a in m.annexes] == ["I"] and m.annexes[0].original.endswith("delegation]")
    recs = {r["record_id"]: r for r in unga.records_for(m, {}, countries(), settings)}
    cze = recs["gd:81:CZE"]
    assert cze["text_source"] == "annex" and cze["speaker_name"] == "Petr Pavel" and cze["language"] == "Czech"
    assert cze["text"] == "Mr. President,\n\nThe Czech Republic stands by the Charter of the United Nations."
    assert not any("printed in annexes" in w for w in m.warnings)


def test_a_heading_for_another_country_is_not_trusted(settings):
    """Found in the records of 2020: pages out of order put India's reply under Tonga's Prime Minister's heading."""
    lines = ga_lines()
    for line in lines:
        if line.text.startswith("President Pavel:"):
            line.text = "Mr. Kumar (India): I am exercising India's right of reply to the statement made earlier."
            line.bold_prefix = "Mr. Kumar"
    m = parse(merge_fragments(lines), "A/81/PV.8")
    recs = unga.records_for(m, {}, countries(), settings)
    assert "gd:81:CZE" not in {r["record_id"] for r in recs} and "gd:81:IND" not in {r["record_id"] for r in recs}
    assert any("name another country" in w for w in m.warnings)
