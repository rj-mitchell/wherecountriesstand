# Parser validation

How well the UN-record parser (`wcs.sources.undocs`, used for meetings after the Security Council corpus ends)
reproduces the corpus on meetings both cover (docs/DECISIONS.md, 4). Each run fetches the UN's English PDF for a
sample of meetings spread through a year, parses it, and compares it speech by speech with the corpus. Re-run with:

    uv run python scripts/validate_undocs.py --year 2023 --sample 25

Run on 24 October 2026, parser version 1.

**Summary.** Every sampled meeting parsed. The parser recovers all the text (median word count 1.00 of the
corpus's) and the same speakers in the same order (median 1.00), with near-identical wording (median 0.98 for 2023,
0.96 for 2016). The remaining differences are by design or minor:

* **The presiding chair.** The corpus files everything the President says under the President's country. This
  parser keeps the chair's remarks apart from the President's statement "in my capacity as the representative of"
  their country (decision 7), so the comparison counts all of the President's words as one speaker on both sides.
* **Briefers.** Speakers without a bracketed label (most briefers) are classed from the President's rule 39
  invitation ("Ms. Rosemary DiCarlo, Under-Secretary-General for Political and Peacebuilding Affairs"). A few the
  corpus calls UN officials come out as organisations, and the reverse.
* **Order.** Where two speakers are swapped, the corpus and the printed record sometimes differ; the parser follows
  the printed record.

## 2023

The UN-record parser (`wcs.sources.undocs`, parser version 1) compared with the Security Council corpus on 25 meetings of 2023, chosen evenly through the year.

* **Speakers**: speeches after merging consecutive speeches by the same speaker, ours / the corpus's.
* **Order**: how closely the sequence of speakers matches (1.00 = identical).
* **Text**: average word-for-word similarity of the first 400 words of each matched speech.
* **Words**: total words, ours as a share of the corpus's.

Parsed: 25 of 25. Median order 1.00, median text 0.98, median words 1.00.

| Meeting | Speakers | Order | Text | Words | Notes |
|---|---|---|---|---|---|
| S/PV.9235 | 21 / 21 | 1.00 | 0.89 | 0.99 |  |
| S/PV.9247 | 22 / 22 | 1.00 | 0.92 | 1.00 |  |
| S/PV.9260 | 37 / 38 | 0.93 | 0.95 | 1.00 | first differences: organisation≠un, organisation≠un, KOR≠chair, chair≠KOR |
| S/PV.9271 | 4 / 4 | 0.75 | 0.99 | 0.98 | first differences: organisation≠un |
| S/PV.9283 | 14 / 14 | 1.00 | 1.00 | 1.00 |  |
| S/PV.9296 | 24 / 24 | 0.92 | 0.98 | 1.00 | first differences: organisation≠CHE, organisation≠GHA |
| S/PV.9306 | 19 / 19 | 0.95 | 0.97 | 1.00 | first differences: JPN≠un |
| S/PV.9315 | 36 / 33 | 0.96 | 0.98 | 1.00 | labels that look like countries but matched none: Vice-President |
| S/PV.9325 | 19 / 19 | 0.95 | 0.95 | 1.00 | first differences: GAB≠JPN, JPN≠GAB |
| S/PV.9338 | 19 / 20 | 0.97 | 0.93 | 1.00 | first differences: USA≠ALB, MLT≠USA, CHN≠MLT, BRA≠CHN |
| S/PV.9350 | 17 / 17 | 1.00 | 0.98 | 1.00 |  |
| S/PV.9361 | 24 / 24 | 1.00 | 0.92 | 1.00 |  |
| S/PV.9373 | 21 / 21 | 1.00 | 1.00 | 1.00 |  |
| S/PV.9384 | 17 / 18 | 0.97 | 0.98 | 1.00 | first differences: RUS≠FRA, BRA≠RUS, chair≠BRA |
| S/PV.9393 | 21 / 21 | 1.00 | 0.98 | 1.00 |  |
| S/PV.9406 | 31 / 31 | 1.00 | 0.99 | 1.00 |  |
| S/PV.9417 | 20 / 20 | 1.00 | 0.97 | 1.00 |  |
| S/PV.9425 | 18 / 18 | 1.00 | 0.98 | 1.00 |  |
| S/PV.9438 | 20 / 20 | 0.95 | 0.98 | 1.00 | 13 members listed (expected 14 besides the President) |
| S/PV.9451 | 32 / 32 | 0.97 | 0.97 | 1.12 | first differences: organisation≠un |
| S/PV.9462 | 35 / 35 | 0.91 | 0.99 | 1.00 | 13 members listed (expected 14 besides the President) |
| S/PV.9475 | 36 / 36 | 1.00 | 0.95 | 1.00 |  |
| S/PV.9485 | 30 / 30 | 1.00 | 0.96 | 1.00 |  |
| S/PV.9499 | 20 / 20 | 1.00 | 0.99 | 1.05 |  |
| S/PV.9509 (Resumption 1) | 79 / 79 | 1.00 | 0.99 | 1.02 |  |

## 2016

The UN-record parser (`wcs.sources.undocs`, parser version 1) compared with the Security Council corpus on 10 meetings of 2016, chosen evenly through the year.

* **Speakers**: speeches after merging consecutive speeches by the same speaker, ours / the corpus's.
* **Order**: how closely the sequence of speakers matches (1.00 = identical).
* **Text**: average word-for-word similarity of the first 400 words of each matched speech.
* **Words**: total words, ours as a share of the corpus's.

Parsed: 10 of 10. Median order 1.00, median text 0.96, median words 1.00.

| Meeting | Speakers | Order | Text | Words | Notes |
|---|---|---|---|---|---|
| S/PV.7600 | 5 / 5 | 1.00 | 0.92 | 0.99 |  |
| S/PV.7625 | 5 / 5 | 1.00 | 0.94 | 1.00 |  |
| S/PV.7651 | 39 / 39 | 1.00 | 0.99 | 1.00 |  |
| S/PV.7678 | 5 / 5 | 1.00 | 0.95 | 1.00 |  |
| S/PV.7703 | 16 / 16 | 1.00 | 0.97 | 1.00 |  |
| S/PV.7731 | 4 / 4 | 1.00 | 0.98 | 0.98 |  |
| S/PV.7758 | 113 / 114 | 0.97 | 0.94 | 1.00 | first differences: un≠organisation, organisation≠KOR, KOR≠CUB, CUB≠ZAF |
| S/PV.7781 | 7 / 7 | 1.00 | 0.96 | 0.99 |  |
| S/PV.7806 | 19 / 19 | 0.95 | 0.99 | 1.00 | first differences: organisation≠un |
| S/PV.7827 | 9 / 9 | 1.00 | 0.96 | 1.00 |  |
