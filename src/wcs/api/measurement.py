"""Measurement for Google Ad Grants (docs/DECISIONS.md, 28; the charity's family/MEASUREMENT.md): the switch, and
what it changes on the pages.

The switch is ``[ads] measurement`` and ``tag`` in config/settings.toml (``WCS_ADS_MEASUREMENT`` and ``WCS_ADS_TAG``
in deploy/.env), off as it ships. Off, nothing here changes a page: the pages carry nothing of it but the family
kit's inert ``data-wts-conversion`` marks, and ``/family/measure.js`` is not served. On, with a valid tag, every
page loads the kit's ``measure.js`` in its head (``with_measurement``), which asks the reader first and loads the
Google tag only if they accept; the footer gains "Measurement choices"; and the privacy page is the measurement
version, ``web/partials/privacy-measurement.html`` (``privacy_draft``), refused while it is unfinished.

The site sets no content security policy of its own: on the shared server the front door sets it, and must add the
kit's hosts (``web/site/family/measure-csp.json``) for this site before the switch is turned on (docs/DEPLOY.md,
"Measurement").
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from wcs.config import Settings

# The conversions this site has, for the kit's measure.js (its banner names only these). The site offers no
# downloads, so "download" is not one of them (decision 28).
MEASURES = "briefing_read source_opened donate_click report_sent"
SCRIPT = "family/measure.js"
# Kept for the front door's policy and never served: the pages need nothing from it
NEVER_SERVED = frozenset({"family/measure-csp.json"})
PRIVACY = "/privacy.html"
DRAFT = "privacy-measurement.html"   # in web/partials/, never served at an address of its own
_TAG = re.compile(r"(?:AW|G|GT)-[A-Za-z0-9]+")
_UNFINISHED = re.compile(r"NNNNNNN|\(draft\b")


def measure_tag(settings: Settings) -> str:
    """The Google tag's ID when [ads] measurement is on with a valid tag, else "" (off)."""
    ads = settings.section("ads")
    tag = str(ads.get("tag") or "").strip()
    return tag if ads.get("measurement") is True and _TAG.fullmatch(tag) else ""


def with_measurement(page: str, tag: str) -> str:
    """The page as it is served while measuring: the kit's measure.js last in its head, and "Measurement choices" on
    a line of its own after the footer's notices (a control, not one of the footer's six links). Unchanged when
    ``tag`` is "" (off)."""
    if not tag:
        return page
    script = (f'  <script src="/{SCRIPT}" data-tag="{html.escape(tag, quote=True)}" data-privacy="{PRIVACY}" '
              f'data-measures="{MEASURES}" defer></script>\n')
    page = page.replace("</head>", script + "</head>", 1)
    end = page.rfind("</footer>")
    after = page.rfind("</p>", 0, end) + len("</p>") if end >= 0 else -1
    if end < 0 or after < len("</p>"):
        return page
    opened = page.rfind("<p", 0, after)
    indent = page[page.rfind("\n", 0, opened) + 1:opened]
    choices = (f'\n{indent}<p class="measure-choices"><a href="{PRIVACY}#measurement" data-wts-measure-open>'
               "Measurement choices</a></p>")
    return page[:after] + choices + page[after:]


def privacy_draft(partials: Path) -> Path:
    """The measurement version of the privacy page, served at /privacy.html while measuring. A draft until the
    charity is registered: the site refuses to start with it while it still says NNNNNNN for the charity number or
    "(draft" in its date."""
    path = partials / DRAFT
    if _UNFINISHED.search(path.read_text(encoding="utf-8")):
        raise RuntimeError(f"[ads] measurement is on, but {path} isn't finished: put in the charity number for "
                           "NNNNNNN and the date it was last updated")
    return path
