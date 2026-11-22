"""The measurement switch, [ads] in config/settings.toml (decision 28; the charity's family/MEASUREMENT.md).

Off, as it ships: no page names the family kit's measure.js, Google or "Measurement choices", /family/measure.js is
not served, and the privacy page is the one the site always had. On, with a tag: every page loads measure.js with the
tag and the site's conversions, the footer has "Measurement choices", the privacy page is the measurement version
(refused while unfinished), and nothing on any page loads Google itself. Off again, every page is as it was. The
conversions are marked where they happen, with the switch off as on. The site sets no content security policy of its
own: the shared front door does (docs/DEPLOY.md, "Measurement")."""

from __future__ import annotations

import dataclasses
import re
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wcs.api.app import PARTIALS_DIR, SITE_DIR, create_app
from wcs.api.measurement import MEASURES, measure_tag
from wcs.config import load_settings
from wcs.store.parquet import write_votes

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sample_topic  # noqa: E402
import sample_votes  # noqa: E402

TAG = "AW-123456789"
GOOGLE = re.compile(r"google|doubleclick|gtag", re.I)
KIT = (SITE_DIR / "family" / "measure.js").read_bytes()


def switched(settings, measurement: bool, tag: str = TAG, **site):
    raw = {**settings.raw, "ads": {"measurement": measurement, "tag": tag}}
    if site:
        raw["site"] = {**settings.raw.get("site", {}), **site}
    return dataclasses.replace(settings, raw=raw)


@pytest.fixture
def data(settings):
    """The data folder CI's accessibility check starts the site with: two made-up votes and a made-up topic."""
    write_votes(settings.parquet_dir, sample_votes.rows())
    sample_topic.write(settings.data_dir)
    return settings


@pytest.fixture
def finished(tmp_path):
    """The site's partials, with the measurement notice finished as the owner finishes it before turning it on."""
    folder = tmp_path / "partials"
    shutil.copytree(PARTIALS_DIR, folder)
    draft = folder / "privacy-measurement.html"
    text = draft.read_text().replace("NNNNNNN", "1234567")
    draft.write_text(re.sub(r"Last updated [^<]*\(draft[^<]*\)\.", "Last updated 1 March 2027.", text))
    return folder


def every_page(api: TestClient) -> dict[str, str]:
    """Every page the site serves: its own pages, the 404 page, a saved briefing's, the vote pages and the topics'."""
    paths = ["/", "/?q=climate+finance", "/no-such-page", f"/b/{sample_topic.ID}", "/votes.html", "/topics.html"]
    paths += [f"/{p.stem}.html" for p in sorted(SITE_DIR.glob("*.html")) if p.stem not in ("index", "404")]
    paths += sorted(set(re.findall(r'href="(/votes/[a-z0-9-]+\.html)"', api.get("/votes.html").text)))
    paths += sorted(set(re.findall(r'href="(/topics/[a-z0-9-]+\.html)"', api.get("/topics.html").text)))
    pages = {path: api.get(path).text for path in paths}
    assert len(pages) >= 20 and any(p.startswith("/votes/a-res") for p in pages) and "/topics/war-in-ukraine.html" in pages
    return pages


def test_the_switch_ships_off_with_no_tag():
    s = load_settings(env={})
    assert s.section("ads") == {"measurement": False, "tag": ""}
    assert measure_tag(s) == ""
    assert measure_tag(switched(s, True, "")) == "", "on without a tag is still off"
    assert measure_tag(switched(s, True, 'AW-1"><script>')) == ""
    assert measure_tag(switched(s, False)) == ""
    assert measure_tag(switched(s, True)) == TAG
    # the server sets it in deploy/.env
    assert measure_tag(load_settings(env={"WCS_ADS_MEASUREMENT": "true", "WCS_ADS_TAG": TAG})) == TAG
    assert measure_tag(load_settings(env={"WCS_ADS_MEASUREMENT": "false", "WCS_ADS_TAG": TAG})) == ""


def test_off_no_page_names_measure_js_or_google_and_the_script_is_not_served(data):
    api = TestClient(create_app(data))
    for path, page in every_page(api).items():
        assert not re.search(r"measure\.js|data-wts-measure-open|Measurement choices", page), path
        assert not GOOGLE.search(page), path
    for path in ("/family/measure.js", "/family/measure-csp.json"):
        assert api.get(path).status_code == 404, path
    privacy = api.get("/privacy.html").text
    assert "Where Countries Stand does not set cookies, show advertising or track visitors" in privacy
    assert 'id="measurement"' not in privacy and "NNNNNNN" not in privacy
    assert "content-security-policy" not in api.get("/").headers   # the front door sets it


def test_on_every_page_loads_measure_js_and_has_measurement_choices(data, finished):
    on = switched(data, True, partials=str(finished))
    api = TestClient(create_app(on))
    script = re.compile(r'  <script src="/family/measure\.js\?v=[0-9a-f]{12}" data-tag="AW-123456789" '
                        r'data-privacy="/privacy\.html" data-measures="briefing_read source_opened donate_click '
                        r'report_sent" defer></script>\n')
    for path, page in every_page(api).items():
        assert len(script.findall(page.split("</head>")[0])) == 1 and page.count("measure.js") == 1, path
        footer = page.split("<footer>")[1]
        assert footer.count('<p class="measure-choices"><a href="/privacy.html#measurement" data-wts-measure-open>'
                            "Measurement choices</a></p>") == 1, path
        # after the footer's notices, and still the family's six footer links: it is a control, not a page
        assert re.search(r"© United Nations\.</p>\n +<p class=\"measure-choices\">", footer), path
        assert len(re.findall(r"<a href=", footer.split('<nav class="foot__links"')[1].split("</nav>")[0])) == 6
        # only measure.js loads Google's tag, and only after "Accept"
        assert "googletagmanager" not in page, path
    js = api.get(re.search(r'src="(/family/measure\.js\?v=[0-9a-f]+)"', api.get("/").text)[1])
    assert js.content == KIT and js.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert api.get("/family/measure-csp.json").status_code == 404   # for the front door's policy, not the pages


def test_on_the_privacy_page_is_the_measurement_version(data, finished):
    api = TestClient(create_app(switched(data, True, partials=str(finished))))
    page = api.get("/privacy.html").text
    text = " ".join(page.split())
    for words in ("Google Ad Grants", "finish reading a briefing, open a source, follow the Donate link or send a "
                  "report or a statement", "independent controller", "The legal basis is your consent", "90 days",
                  'href="https://policies.google.com/technologies/retention"',
                  'href="https://business.safety.google/privacy/"', "“Measurement choices”",
                  "Nothing is loaded from Google until you choose", "No ads are ever shown",
                  "nothing on them is personalised", "registered in England and Wales (charity number 1234567)",
                  "the words of the question", "Last updated 1 March 2027."):
        assert words in text, words
    assert '<h2 id="measurement">' in page
    assert re.search(r'<link rel="canonical" href="[^"]*/privacy\.html">', page)
    assert '<a href="/privacy.html" aria-current="page">Privacy</a>' in page
    # never at an address of its own
    for path in ("/privacy-measurement.html", "/privacy-measurement"):
        assert api.get(path).status_code == 404
    assert "privacy-measurement" not in api.get("/sitemap-pages.xml").text


def test_on_the_site_refuses_to_start_with_the_notice_unfinished(data):
    for draft in ((PARTIALS_DIR / "privacy-measurement.html").read_text(),):
        assert "NNNNNNN" in draft and "(draft" in draft
    with pytest.raises(RuntimeError, match="NNNNNNN"):
        create_app(switched(data, True))
    create_app(switched(data, False))   # off, the draft is never read


def test_on_then_off_every_page_is_as_it_was(data, finished):
    before = every_page(TestClient(create_app(data)))
    on = every_page(TestClient(create_app(switched(data, True, partials=str(finished)))))
    assert on != before
    after = every_page(TestClient(create_app(switched(data, False, partials=str(finished)))))
    assert after == before


def test_the_conversions_are_marked_where_they_happen(data):
    """The inert attributes the kit's measure.js reads, the same with the switch off."""
    api = TestClient(create_app(data))
    pages = every_page(api)
    for path, page in pages.items():   # the Donate link in the kit's shelf, on every page
        assert page.count('<a href="https://wheretheystand.uk/donate" data-wts-conversion="donate_click">Donate</a>') \
            == 1, path
    vote = next(page for path, page in pages.items() if path.startswith("/votes/a-res"))
    record = re.findall(r'<a href="https://(?:undocs\.org|digitallibrary\.un\.org)/[^"]*"([^>]*)>', vote)
    assert record and all(attrs == ' data-wts-conversion="source_opened"' for attrs in record)
    topic = pages["/topics/war-in-ukraine.html"]
    assert (f'data-wts-conversion="briefing_read" data-wts-briefing="{sample_topic.ID}">') in topic
    assert re.search(r'<button type="button" class="wts-chip" [^>]*data-wts-conversion="source_opened">', topic)
    assert re.search(r'<a href="https://undocs\.org/en/S/PV\.9999" rel="noopener" '
                     r'data-wts-conversion="source_opened">Read S/PV\.9999</a>', topic)
    assert 'rel="noopener" data-wts-conversion="source_opened">Read S/PV.10043' in pages["/"]
    # the report form is sent by its script, which tells measure.js once the site has taken it
    script = (SITE_DIR / "corrections.js").read_text()
    sent = script.index('window.wtsMeasure?.conversion("report_sent");')
    assert script.index('await send("/api/report"') < sent < script.index("Thank you: your report has been sent.")
    # the site offers no downloads, so its banner never says it counts one
    assert "download" not in MEASURES.split()
    assert all(f"{name}:" in KIT.decode() for name in MEASURES.split())
