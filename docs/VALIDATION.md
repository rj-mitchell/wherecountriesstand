# Parser validation

How well the UN-record parser (`wcs.sources.undocs`, used for meetings after the Security Council corpus ends)
reproduces the corpus on meetings both cover (docs/DECISIONS.md, 4). Each run fetches the UN's English PDF for a
sample of meetings spread through a year, parses it, and compares it speech by speech with the corpus. Re-run with:

    uv run python scripts/validate_undocs.py --year 2023 --sample 25

Run on 24 October 2026, parser version 2. Version 2 keeps body text that version 1 took for the page footer (about
1.4% of the words, in a sample of 25 records of 2024-26: 5,009 words recovered, 5 lost) and reads attributions the
record forgot to print in bold. The medians below are unchanged from version 1: the recovered text sat in a few
meetings rather than in the typical one.

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

The UN-record parser (`wcs.sources.undocs`, parser version 2) compared with the Security Council corpus on 25 meetings of 2023, chosen evenly through the year.

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

The UN-record parser (`wcs.sources.undocs`, parser version 2) compared with the Security Council corpus on 10 meetings of 2016, chosen evenly through the year.

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

## The General Debate (General Assembly records)

The same parser reads the General Assembly's records for the General Debate (`wcs.sources.unga`). There is no corpus
of its rights of reply to compare against, and the corpus's statements are the delegations' texts rather than the
records, so these are checks of coverage and of samples:

* **Statements, 79th session (2024).** All 11 debate meetings found through the catalogue and parsed: 192
  statements, one for every Member State that spoke, plus the State of Palestine, the Holy See and the European
  Union. The four Member States without one (Afghanistan, Brunei Darussalam, Myanmar, and the Democratic People's
  Republic of Korea, which spoke only in reply) are introduced nowhere in the records. 116 statements by heads of
  State or Government, 68 by ministers, 6 by heads of delegation.
* **Rights of reply, 2000-2024 (sessions 55-79).** 466 replies from 355 debate meetings, 8 to 29 a session. Twelve
  drawn at random were all replies, each opening by saying so or answering a named delegation. Points of order and
  one-line exchanges with the chair are left out.
* **Older records.** Records from before 2000 are not read: before 1996 they are scans without text, and in the late
  1990s their two columns come out of the PDF in the wrong order.

* **Virtual sessions.** In 2020 (and partly 2021) statements were shown as recorded videos: the record holds a
  delegate's few words introducing each one, and prints the statement itself in an annex after the meeting
  ("Annex I" or "Annex 1", a heading such as "Address by His Holiness Pope Francis", a note of the original language
  and who translated it, then the text). Since November 2026 the annexes are read (`verbatim.read_annexes`): each
  becomes the country's statement, its country taken from the heading's post (or the Pope's name, for the Holy See),
  labelled as the delegation's text. Checked on 2020: 187 statements, one per country (154 from annexes), against
  33 before. The pages of some of these records come out of order, which had put headings on the wrong speeches
  (India's right of reply under Tonga's Prime Minister's heading): a heading that names another country than the
  speaker who follows is no longer trusted (19 in 2020, one in 2021, none since). The corpus still supplies the
  statements for 2020 and 2021; this matters if a future session is held virtually.

## Real briefings (October 2026)

Twenty-six briefings written by the model on the real record (eight in a first round, eighteen in a second),
across the test set's categories (topics, countries, groups, the General Debate, votes, Comprehensive length), cost
about $3.70 in all, from $0.04 to $0.33 each. None had an
invalid citation or an unverified quotation that mattered; the grey "unsupported" sentences were mostly paraphrase
running on past a citation. What they found, and what was changed:

| Found | Changed |
|---|---|
| Questions about votes got no voting data: resolution titles were matched on the model's search words, ten of them ("territorial integrity, sovereignty ...") needing five hits, so "Aggression against Ukraine" never qualified | Titles are matched on the question's own subject words first, as whole words (substrings let "rise" match "enterprises"); up to six resolutions when votes are asked about, and a vote shown unasked must contain every subject word |
| A country's vote misread from a list of 140 names (Nigeria said not to support ES-11/2; it voted in favour) | Each vote source also lists how the countries quoted in the briefing voted |
| "How has the Holy See voted?" answered only "No records found" | Source titles give an observer State's UN status, and that it does not vote (decision 6) |
| Recorded-video introductions from 2020 stored as statements, one under the wrong country | Left out, with a warning (above) |
| Source titles now name the Group of 77, the Non-Aligned Movement and the OIC, but the instructions forbade groupings beyond the original list | The instructions list them, and say that membership of the G77 or the NAM shows no view: only a statement on the group's behalf does |
| "No recorded-vote data were supplied" and "No statements from permanent members are supplied" greyed as claims | Read as notes about the evidence |
| A Comprehensive briefing ran to about 1,550 words against a target of 700-1,300 | Not changed: it used its length well; watch it |
