"""Check the UN-record parser against the Security Council corpus on meetings both cover (docs/DECISIONS.md, 4).

For a sample of meetings in the corpus (already loaded with `wcs ingest-sc`), fetch the UN's record, parse it with
`wcs.sources.undocs`, and compare speech by speech: how many speeches, who spoke in what order, and how much of the
wording matches. Writes a markdown report.

    uv run python scripts/validate_undocs.py --year 2023 --sample 20 --out docs/VALIDATION.md
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from difflib import SequenceMatcher
from pathlib import Path

import duckdb

from wcs.config import load_settings
from wcs.reference.countries import countries
from wcs.sources import undocs
from wcs.store.parquet import record_path


def who(r: dict) -> str:
    """A speaker token for aligning the two sequences: the country, or the kind of speaker. Everything the presiding
    President says counts as the chair on both sides: the corpus files it under the President's country, this
    parser separates the chair's remarks from the President's national statement (by design)."""
    if r.get("entity_kind") == "chair" or r.get("participant") == "president":
        return "chair"
    return r.get("country_code") or r.get("entity_kind") or "?"


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def compare(ours: list[dict], theirs: list[dict]) -> dict:
    # consecutive speeches by the same speaker are merged on both sides before aligning
    def merged(rows: list[dict]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for r in rows:
            t = who(r) if who(r) != "chair" else "chair"
            if out and out[-1][0] == t:
                out[-1] = (t, out[-1][1] + " " + r["text"])
            else:
                out.append((t, r["text"]))
        return out

    a, b = merged(ours), merged(theirs)
    sm = SequenceMatcher(a=[x for x, _ in a], b=[x for x, _ in b], autojunk=False)
    pairs = [(a[i + k], b[j + k]) for i, j, n in sm.get_matching_blocks() for k in range(n)]
    sims = []
    for (_, ta), (_, tb) in pairs:
        wa, wb = words(ta), words(tb)
        if wa and wb:
            sims.append(SequenceMatcher(a=wa[:400], b=wb[:400], autojunk=False).ratio())
    total_a = sum(len(words(r["text"])) for r in ours)
    total_b = sum(len(words(r["text"])) for r in theirs)
    return {"speakers_ours": len(a), "speakers_corpus": len(b), "order": sm.ratio(),
            "text": statistics.mean(sims) if sims else 0.0, "words": total_a / total_b if total_b else 0.0,
            "diff": [(x, y) for x, y in zip([x for x, _ in a], [y for y, _ in b], strict=False) if x != y][:4]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2023)
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    settings = load_settings()
    ref = countries()
    path = record_path(settings.parquet_dir, "sc", args.year)
    con = duckdb.connect()
    symbols = [r[0] for r in con.execute(
        f"SELECT symbol FROM read_parquet('{path}') WHERE symbol LIKE 'S/PV.%' GROUP BY symbol "
        "HAVING count(*) >= 4 ORDER BY symbol").fetchall()]
    step = max(1, len(symbols) // args.sample)
    chosen = symbols[::step][:args.sample]
    manifest = undocs.Manifest.load(settings)
    docs = undocs.UNDocs(settings)
    rows = []
    for symbol in chosen:
        theirs = [dict(zip([d[0] for d in con.description], r, strict=True)) for r in con.execute(
            f"SELECT * FROM read_parquet('{path}') WHERE symbol = ? ORDER BY speech_number", [symbol]).fetchall()]
        meta = docs.library_record(symbol) or {"symbol": symbol, "verbatim": True}
        ours = undocs.process(settings, ref, manifest, docs, meta)
        entry = manifest.entries.get(symbol, {})
        c = compare(ours, theirs) if ours else {"speakers_ours": 0, "speakers_corpus": len(theirs), "order": 0.0,
                                                 "text": 0.0, "words": 0.0, "diff": []}
        rows.append((symbol, c, entry.get("problems") or ([entry.get("status")] if entry.get("status") != "parsed"
                                                          else [])))
        print(f"{symbol}: {c['speakers_ours']}/{c['speakers_corpus']} speakers, order {c['order']:.2f}, "
              f"text {c['text']:.2f}, words {c['words']:.2f} {entry.get('problems') or ''}", file=sys.stderr)
    manifest.save()
    ok = [c for _, c, _ in rows if c["speakers_ours"]]
    lines = [
        f"# Parser validation: {args.year}",
        "",
        f"The UN-record parser (`wcs.sources.undocs`, parser version {undocs.PARSER_VERSION}) compared with the "
        f"Security Council corpus on {len(rows)} meetings of {args.year}, chosen evenly through the year.",
        "",
        "* **Speakers**: speeches after merging consecutive speeches by the same speaker, ours / the corpus's.",
        "* **Order**: how closely the sequence of speakers matches (1.00 = identical).",
        "* **Text**: average word-for-word similarity of the first 400 words of each matched speech.",
        "* **Words**: total words, ours as a share of the corpus's.",
        "",
        f"Parsed: {len(ok)} of {len(rows)}. Median order {statistics.median(c['order'] for c in ok):.2f}, median "
        f"text {statistics.median(c['text'] for c in ok):.2f}, median words {statistics.median(c['words'] for c in ok):.2f}."
        if ok else "Nothing parsed.",
        "",
        "| Meeting | Speakers | Order | Text | Words | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for symbol, c, problems in rows:
        notes = "; ".join(problems) or ("first differences: " + ", ".join(f"{a}≠{b}" for a, b in c["diff"])
                                        if c["diff"] else "")
        lines.append(f"| {symbol} | {c['speakers_ours']} / {c['speakers_corpus']} | {c['order']:.2f} | "
                     f"{c['text']:.2f} | {c['words']:.2f} | {notes} |")
    report = "\n".join(lines) + "\n"
    if args.out:
        args.out.write_text(report)
    print(report)


if __name__ == "__main__":
    main()
