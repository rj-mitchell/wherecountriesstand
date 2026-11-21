"""Share cards for saved briefings: the link preview (``og:image`` on ``/b/<id>``) that shows the checking.

Each card is drawn once, with no AI, by wts-core's ``adapters.cards.briefing_card`` in the site's cloth, with its
mark, and kept on disk under the data folder; after that the file is served. Its title is the site's neutral title for
the briefing ("Briefing from UN records, 18 November 2026"), never the reader's question (docs/DECISIONS.md, 7), and
its counts are the check strip's, from ``check_counts`` on the answer as it is shown (``wcs.rag.checks.shown``). The
family's account is ``family/BRIEFINGS.md`` in the charity's repository (the review plan of 21 November 2026, B5).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from wts_core.checks import RULES_VERSION, CheckCounts, check_counts
from wts_core.dates import long_date

from wcs.config import REPO_ROOT, STATIC_DIR
from wcs.rag.checks import rechecked, shown

FONTS = STATIC_DIR / "fonts"
# Raise when what a card shows changes, so that cards already drawn are drawn again. The version of the checking
# rules is in the name too: a briefing judged again under new rules may show different counts.
CARD_VERSION = 1
CLOTH = (0x1F, 0x5F, 0x8B)  # the site's cloth, as on its band (the family kit's --wts-cloth-countries)
RECORD = "the UN record"


def card_style() -> Any:
    from wts_core.adapters.cards import CardStyle

    return CardStyle(site_name="Where Countries Stand", domain="wherecountriesstand.com", cloth=CLOTH, record=RECORD,
                     serif=FONTS / "SourceSerif4-Semibold.ttf", sans=FONTS / "SourceSans3-Regular.ttf",
                     sans_bold=FONTS / "SourceSans3-Semibold.ttf",
                     # the mark on its white tile, as in the header band and the favicon
                     icon=REPO_ROOT / "web" / "site" / "family" / "icon-512.png")


def shown_counts(events: list[dict[str, Any]]) -> CheckCounts | None:
    """The check strip's counts for a saved briefing as it is shown today, or None if it has no written answer."""
    final = next((e for e in shown(rechecked(events)) if e.get("type") == "final"), None)
    answer = (final or {}).get("answer")
    if not isinstance(answer, dict) or answer.get("no_records"):
        return None
    return check_counts(answer)


def counts_in_words(counts: CheckCounts) -> str:
    """The counts as the strip says them, for the card's text alternative."""
    nosource = "sentence" if counts.no_source == 1 else "sentences"
    return (f"{counts.citations_matched} of {counts.citations_total} citations matched to the record, "
            f"{counts.quotes_found} of {counts.quotes_total} quotations found word for word, "
            f"{counts.no_source} {nosource} without a source")


def card_path(folder: Path, bid: str) -> Path:
    return folder / f"{bid}.v{CARD_VERSION}.r{RULES_VERSION}.png"


def card_png(folder: Path, bid: str, title: str, counts: CheckCounts, created_at: str) -> Path:
    """The card's file, drawn the first time it is asked for and kept."""
    path = card_path(folder, bid)
    if not path.exists():
        from wts_core.adapters.cards import briefing_card

        folder.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{id(path)}.tmp")
        tmp.write_bytes(briefing_card(card_style(), title=title, counts=counts, prepared=long_date(created_at)))
        tmp.replace(path)
    return path
