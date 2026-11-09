"""The General Debate website as a stop-gap source, tested with a fake site (no network)."""

import json
from datetime import date

import pytest

from wcs.reference.countries import countries
from wcs.sources import gadebate, undocs
from wcs.store.parquet import read_year

SITE = gadebate.SITE

PAGE = """<html><head><meta property="og:title" content="Kenya | 81st session" /></head><body>
<div class="group-speaker-country-details"><div class="field field--name-field-speaker-list field--type-entity-reference
 field--label-hidden"><div class='field__items'><div class="field__item">Kenya</div></div></div>
<div class="field field--name-field-speaker-title field--label-hidden"><div class='field__items'><div class="field__item">His
 Excellency</div></div></div><div class="field field--name-field-speaker-name field--type-string field--label-hidden
 field__item">William Samoei Ruto</div><div class="field field--name-field-speaker-function-2 field--label-hidden">
<div class='field__items'><div class="field__item">President</div></div></div></div>
<div class="field field--name-field-release-date"><div class='field__items'><div class="field__item"><time
 datetime="2026-09-23T00:00:00-04:00" class="datetime">23 September 2026</time></div></div></div>
<div class="field field--name-field-english-statement field--type-file field--label-inline"><div
 class="custom-statement-file-label">Statement in English</div><div class="field__item"><span class="file"> <a
 href="/sites/default/files/gastatements/81/ke_en.pdf" type="application/pdf">ke_en.pdf</a></span></div></div></div>
<button class="download-transcript" data-prepare-url="/en/node/81100/transcript/en/prepare-download">Transcript</button>
</body></html>"""

PAGE_FR = PAGE.replace("Kenya", "Cameroon").replace("english-statement", "french-statement") \
    .replace("ke_en.pdf", "cm_fr.pdf").replace("William Samoei Ruto", "Lejeune Mbella Mbella") \
    .replace(">President<", ">Minister for Foreign Affairs<").replace("81100", "81101")

TRANSCRIPT = """Meeting/Event: Cameroon - General Debate, 81st Session
Date: 26 September 2026

[Auto-generated transcript: may contain errors]

Kazakhstan.
I now give the floor to His Excellency Mr. Lejeune Mbella Mbella, Minister for Foreign Affairs of Cameroon.
Madam President, Excellencies, distinguished delegates.
Cameroon calls for a reformed Security Council.
On behalf of the General Assembly, I wish to thank the Minister for Foreign Affairs of Cameroon.
"""

SITEMAP_INDEX = f"<sitemapindex><sitemap><loc>{SITE}/sitemap.xml?page=1</loc></sitemap></sitemapindex>"
SITEMAP = f"""<urlset><url><loc>{SITE}/en/81/kenya</loc><lastmod>2026-09-24T10:00:00-04:00</lastmod></url>
<url><loc>{SITE}/en/81/cameroon</loc><lastmod>2026-09-27T10:00:00-04:00</lastmod></url>
<url><loc>{SITE}/en/80/france</loc><lastmod>2025-09-27T10:00:00-04:00</lastmod></url>
<url><loc>{SITE}/fr/81/kenya</loc></url></urlset>"""


class Resp:
    def __init__(self, text="", content=b"", ctype="text/html"):
        self.text, self.content, self.headers = text, content or text.encode(), {"content-type": ctype}


class FakeSite:
    def __init__(self, blocked=False):
        self.calls, self.blocked = [], blocked

    def get(self, url):
        self.calls.append(url)
        if self.blocked:
            raise gadebate.Blocked("405")
        pages = {f"{SITE}/sitemap.xml": SITEMAP_INDEX, f"{SITE}/sitemap.xml?page=1": SITEMAP,
                 f"{SITE}/en/81/kenya": PAGE, f"{SITE}/en/81/cameroon": PAGE_FR}
        if url.endswith(".pdf"):
            return Resp(content=b"%PDF-1.4 fake", ctype="application/pdf")
        return Resp(pages[url])

    def transcript(self, prepare_url):
        self.calls.append(prepare_url)
        return TRANSCRIPT


@pytest.fixture
def site_settings(settings, monkeypatch):
    settings.raw["live"] = {"sc_from": "2024-01-01", "gd_from_session": 81, "gd_website": True}
    import pdfminer.high_level
    monkeypatch.setattr(pdfminer.high_level, "extract_text", lambda path: (
        "REPUBLIC OF KENYA\n\nNATIONAL STATEMENT\n\nCheck against delivery\n\n23 SEPTEMBER 2026\n\n"
        "Mr President of the General Assembly,\n\nKenya stands for a fair\nglobal financial system.\n\n"
        + "Kenya will work with every partner for reform of the international financial architecture.\n\n" * 30
        + "2\n\nhttps://www.example.org/statement\n"))
    return settings


def test_parse_page_and_cleaning():
    info = gadebate.parse_page(PAGE, f"{SITE}/en/81/kenya")
    assert info["label"] == "Kenya" and info["name"] == "William Samoei Ruto" and info["post"] == "President"
    assert info["date"] == "2026-09-23" and info["session"] == 81 and info["slug"] == "kenya"
    assert info["files"] == {"english": f"{SITE}/sites/default/files/gastatements/81/ke_en.pdf"}
    assert info["transcript"].endswith("/en/node/81100/transcript/en/prepare-download")
    assert gadebate.clean_transcript(TRANSCRIPT) == ("Madam President, Excellencies, distinguished delegates. "
                                                     "Cameroon calls for a reformed Security Council.")
    assert gadebate.clean_statement("REPUBLIC OF KENYA\n\nCheck against delivery\n\nMr President,\n\nKenya speaks."
                                    "\n\n2\n") == "Mr President,\n\nKenya speaks."
    assert gadebate.clean_statement("Mr President,\n\nWe stand ready.\n\nThank you.\n\nLast review: 24 September 2026"
                                    ) == "Mr President,\n\nWe stand ready.\n\nThank you."
    assert not gadebate.readable("(cid:3)(cid:2)5 " * 400) and gadebate.readable("the record of the house " * 80)
    urls = gadebate.sitemap_statements(SITEMAP, {81})
    assert sorted(urls) == [f"{SITE}/en/81/cameroon", f"{SITE}/en/81/kenya"]


def test_website_fills_in_until_the_record(site_settings):
    from wcs.update import Report, update_general_debate

    class NoDocs:
        def search(self, query, rows=200):
            return []

        def pdf(self, symbol, library_url=None):
            return None

    report = Report(started="now")
    changed = update_general_debate(site_settings, countries(), NoDocs(), report, today=date(2026, 10, 24),
                                    website=FakeSite())
    assert changed == {2026} and report.gd[81]["website"] == 2
    rows = {r["record_id"]: r for r in read_year(site_settings.parquet_dir, "gd", 2026)}
    ken, cmr = rows["gd:81:KEN"], rows["gd:81:CMR"]
    assert ken["text_source"] == "delegation" and ken["text"].startswith("Mr President of the General Assembly")
    assert "Check against delivery" not in ken["text"] and ken["speaker_role"] == "head"
    assert not ken["text"].rstrip().endswith(("2", "statement"))   # page number and URL dropped
    assert ken["url"] == f"{SITE}/en/81/kenya" and ken["date"] == date(2026, 9, 23)
    assert cmr["text_source"] == "transcript" and cmr["language"] == "French"
    assert cmr["text"].startswith("Madam President") and "wish to thank" not in cmr["text"]
    assert gadebate.load_state(site_settings).get("unstored") == []   # stored, so not returned again

    # within the day, the sitemap is not read again
    site2 = FakeSite()
    update_general_debate(site_settings, countries(), NoDocs(), Report(started="now"), today=date(2026, 10, 24),
                          website=site2)
    assert site2.calls == []


def test_official_record_replaces_website_text(site_settings, monkeypatch):
    from test_unga import FakeDocs, ga_lines

    from wcs.update import Report, update_general_debate

    monkeypatch.setattr(undocs, "read_lines", lambda path: ga_lines(path.read_bytes()[9:].decode()))
    # the website has Kenya and Cameroon; the record (meeting 8) has the Czech Republic, the Niger and the EU
    catalogue = [{"symbol": "A/81/PV.8", "verbatim": True, "agenda": ["General debate"], "date": "2026-09-22"}]
    update_general_debate(site_settings, countries(), FakeDocs(catalogue, {"A/81/PV.8"}), Report(started="now"),
                          today=date(2026, 10, 24), website=FakeSite())
    rows = {r["record_id"]: r for r in read_year(site_settings.parquet_dir, "gd", 2026)}
    assert rows["gd:81:CZE"]["text_source"] == "record" and rows["gd:81:KEN"]["text_source"] == "delegation"


def test_blocked_website_is_a_warning(site_settings):
    from wcs.update import Report, update_general_debate

    class NoDocs:
        def search(self, query, rows=200):
            return []

        def pdf(self, symbol, library_url=None):
            return None

    report = Report(started="now")
    update_general_debate(site_settings, countries(), NoDocs(), report, today=date(2026, 10, 24),
                          website=FakeSite(blocked=True))
    assert any("refused access" in w for w in report.warnings) and report.ok


def test_a_challenge_page_served_as_200_is_a_refusal_and_stays_visible(site_settings):
    """The live server found no statements and no warning (November 2026): a page that isn't a sitemap is now a
    refusal, and the last reading is kept for the status page and for every update's warnings until one succeeds."""
    from wcs.update import Report, update_general_debate

    class Challenge(FakeSite):
        def get(self, url):
            self.calls.append(url)
            return Resp("<html><title>Just a moment...</title></html>", ctype="text/html")

    class NoDocs:
        def search(self, query, rows=200):
            return []

        def pdf(self, symbol, library_url=None):
            return None

    report = Report(started="now")
    update_general_debate(site_settings, countries(), NoDocs(), report, today=date(2026, 10, 24), website=Challenge())
    assert any("refused access" in w and "not a sitemap" in w for w in report.warnings)
    check = gadebate.last_check(site_settings)
    assert check["ok"] is False and "not a sitemap" in check["error"]
    # the next update doesn't read the website again (20 hours), but still warns
    report = Report(started="later")
    update_general_debate(site_settings, countries(), NoDocs(), report, today=date(2026, 10, 24), website=Challenge())
    assert any("refused access" in w for w in report.warnings)


def test_a_good_reading_is_recorded(site_settings):
    gadebate.update(site_settings, countries(), {81}, site=FakeSite())
    check = gadebate.last_check(site_settings, {81})
    assert check["ok"] and check["pages_listed"] == {"81": 2} and check["statements_kept"]["81"] >= 1


def test_statements_read_elsewhere_are_imported_and_stored(site_settings, tmp_path):
    """For a server the website refuses: records read elsewhere are imported, then stored by the next update."""
    from wcs.update import Report, update_general_debate

    gadebate.update(site_settings, countries(), {81}, site=FakeSite())
    exported = tmp_path / "records-81.json"
    exported.write_text(gadebate.records_path(site_settings, 81).read_text())
    for f in gadebate.cache_dir(site_settings).glob("*.json"):
        f.unlink()                                       # a server that never read the website

    class NoDocs:
        def search(self, query, rows=200):
            return []

        def pdf(self, symbol, library_url=None):
            return None

    assert gadebate.import_records(site_settings, [str(exported)]) == {81: len(json.loads(exported.read_text()))}
    update_general_debate(site_settings, countries(), NoDocs(), Report(started="now"), today=date(2026, 10, 24),
                          website=FakeSite(blocked=True))
    rows = {r["record_id"]: r for r in read_year(site_settings.parquet_dir, "gd", 2026)}
    assert "gd:81:KEN" in rows and rows["gd:81:KEN"]["text_source"] == "delegation"


def test_statements_kept_in_the_repository_are_imported_by_the_update(site_settings, tmp_path):
    """data_static/gd_website: imported once, and stored even though the website refuses this server."""
    from wcs.update import Report, update_general_debate

    gadebate.update(site_settings, countries(), {81}, site=FakeSite())
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "records-81.json").write_text(gadebate.records_path(site_settings, 81).read_text())
    for f in gadebate.cache_dir(site_settings).glob("*.json"):
        f.unlink()
    site_settings.raw["live"]["gd_bundled"] = str(bundled)

    class NoDocs:
        def search(self, query, rows=200):
            return []

        def pdf(self, symbol, library_url=None):
            return None

    update_general_debate(site_settings, countries(), NoDocs(), Report(started="now"), today=date(2026, 10, 24),
                          website=FakeSite(blocked=True))
    assert "gd:81:KEN" in {r["record_id"] for r in read_year(site_settings.parquet_dir, "gd", 2026)}
    assert gadebate.import_bundled(site_settings, {81}) == {}           # once only


def test_health_skips_sessions_stored_again_without_a_count():
    # November 2026: a new parser version re-read every cached General Debate record, so older sessions were stored
    # again and reached the report with only their website count; the health check then failed on KeyError
    from wcs.update import Report, health

    report = Report(started="now")
    report.gd = {75: {"website": 0}, 79: {"website": 0, "meetings": 0, "statements": 0, "replies": 0,
                                         "concluded": False, "awaiting": 11}}
    health(report, today=date(2026, 11, 9))
    assert any("79th session" in w for w in report.warnings)
    assert not any("75th session" in w for w in report.warnings)
