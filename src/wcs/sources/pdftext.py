"""PDF to positioned lines of text, with which characters are bold or italic.

A UN verbatim record prints each speaker's name in bold at the start of a paragraph ("**Mr. Delattre** (France)
(*spoke in French*):"), procedural notes in italics, and a running header and footer on every page. Keeping the font
of each character lets the parser find speakers by their typography, with their wording as a fallback, so a change of
font or a reworded heading does not silently break it.

Uses pdfminer.six (pure Python), which keeps the reading order of two-column pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Running headers sit above 90.5% of the page height and footers below 10.5% (S/PV records 1995-2026 and A/PV
# records); the parser also drops header-like lines by their wording, in case a layout moves.
HEADER_ZONE = 0.905
FOOTER_ZONE = 0.105


@dataclass
class Line:
    page: int                # 0-based
    x0: float
    y0: float
    y1: float
    size: float              # largest font size on the line
    text: str
    bold_prefix: str         # the bold characters at the start of the line ("Mr. Delattre ")
    italic: float            # share of characters in italics
    bold: float              # share of characters in bold
    box: int                 # the text box (paragraph-ish block) the line belongs to, numbered through the document
    zone: str                # "header", "footer" or "body"
    x1: float = 0.0


def _font_flags(fontname: str) -> tuple[bool, bool]:
    f = fontname.lower()
    return ("bold" in f or "black" in f or "heavy" in f or "semibold" in f), ("italic" in f or "oblique" in f)


def read_lines(path: Path) -> list[Line]:
    """Every line of text in reading order, page by page."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LAParams, LTChar, LTTextContainer, LTTextLine

    out: list[Line] = []
    box_no = 0
    for page_no, page in enumerate(extract_pages(str(path), laparams=LAParams())):
        height = float(page.height) or 792.0
        for el in page:
            if not isinstance(el, LTTextContainer):
                continue
            box_no += 1
            for line in el:
                if not isinstance(line, LTTextLine):
                    continue
                chars = [c for c in line if isinstance(c, LTChar)]
                text = " ".join(line.get_text().split())
                if not text or not chars:
                    continue
                flags = [_font_flags(c.fontname) for c in chars]
                prefix = []
                for c, (b, _) in zip(chars, flags, strict=True):
                    if not b:
                        break
                    prefix.append(c.get_text())
                zone = "header" if line.y0 > height * HEADER_ZONE else ("footer" if line.y1 < height * FOOTER_ZONE
                                                                        else "body")
                out.append(Line(page_no, float(line.x0), float(line.y0), float(line.y1),
                                max(float(c.size) for c in chars), text, " ".join("".join(prefix).split()),
                                sum(i for _, i in flags) / len(flags), sum(b for b, _ in flags) / len(flags),
                                box_no, zone, float(line.x1)))
    return merge_fragments(out)


def column_edges(lines: list[Line]) -> dict[int, list[int]]:
    """Left edges of the text columns on each page: frequent left positions, keeping the leftmost of each cluster
    (first-line indents are frequent too, about 18-24 points in)."""
    from collections import Counter

    edges: dict[int, list[int]] = {}
    for page in {ln.page for ln in lines}:
        counts = Counter(round(ln.x0) for ln in lines if ln.page == page)
        frequent = sorted(x for x, n in counts.items() if n >= 3) or sorted(counts)
        cols: list[int] = []
        for x in frequent:
            if not cols or x - cols[-1] > 40:
                cols.append(x)
        edges[page] = cols
    return edges


def merge_fragments(lines: list[Line]) -> list[Line]:
    """Join pieces of one printed line that the layout analysis put in separate boxes.

    A justified line with wide gaps (often a speaker's attribution: "Ms. Koumby Missambo (Gabon) (spoke in French):")
    can come back as several boxes, in the wrong order. Pieces on the same page, in the same column and at the same
    height are joined left to right, at the place of the first of them."""
    edges = column_edges(lines)

    def column(ln: Line) -> int:
        cols = edges.get(ln.page) or [0]
        return max((i for i, e in enumerate(cols) if ln.x0 >= e - 2), default=0)

    groups: dict[tuple[int, int, int], list[int]] = {}
    for i, ln in enumerate(lines):
        if ln.zone != "body":
            continue
        key = (ln.page, column(ln), round(ln.y0 / 2.5))
        groups.setdefault(key, []).append(i)
    replace: dict[int, Line] = {}
    drop: set[int] = set()
    for idx in groups.values():
        if len(idx) < 2:
            continue
        parts = sorted((lines[i] for i in idx), key=lambda ln: ln.x0)
        lengths = [max(len(p.text), 1) for p in parts]
        total = sum(lengths)
        prefix = ""
        for p in parts:
            prefix = f"{prefix} {p.bold_prefix}".strip() if p.bold_prefix else prefix
            if p.bold < 0.99:
                break
        merged = Line(parts[0].page, parts[0].x0, min(p.y0 for p in parts), max(p.y1 for p in parts),
                      max(p.size for p in parts), " ".join(p.text for p in parts), prefix,
                      sum(p.italic * n for p, n in zip(parts, lengths, strict=True)) / total,
                      sum(p.bold * n for p, n in zip(parts, lengths, strict=True)) / total,
                      parts[0].box, "body", max(p.x1 for p in parts))
        first = min(idx)
        replace[first] = merged
        drop.update(i for i in idx if i != first)
    return [replace.get(i, ln) for i, ln in enumerate(lines) if i not in drop]
