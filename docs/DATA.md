# Data sources

Where the site's data comes from, what each source covers, its licence, and what was found in it. Facts are as
checked on 24 October 2026 unless another date is given.

## Sources

| Source | Coverage | Size | Licence | Access |
|---|---|---|---|---|
| [UN General Debate Corpus](https://doi.org/10.7910/DVN/0TJX8Y) (Jankin, Baturo, Dasandi) | 1946 to 2025; used to the 78th session (2023) | 11,141 statements in version 14 (March 2026); 71 MB compressed | CC0 | Harvard Dataverse: `wcs fetch-gd`, then `wcs ingest-gd` (below) |
| [UN Security Council Debates](https://doi.org/10.7910/DVN/KGVSYH) (Schoenfeld, Eckhard, Patz, van Meegdenburg and others) | 6 January 1992 to 30 December 2023 | 106,088 speeches at 6,595 meetings; 474 MB | CC0 | Harvard Dataverse: `wcs fetch-sc`, then `wcs ingest-sc` |
| UN verbatim records (`S/PV.<n>`, `A/<session>/PV.<n>`) | Security Council from 2024; General Debate from the 79th session (2024); rights of reply from 1993 | About 300 Security Council meetings a year | © United Nations: non-commercial use, with attribution | UN Digital Library and Official Document System, read by `wcs update` |
| [General Assembly voting data](https://digitallibrary.un.org/record/4060887) (UN Dag Hammarskjöld Library) | 1946 to 17 September 2026 (resolution 81/1) | 954,382 rows (one country's vote on one resolution), 5,730 resolutions | © United Nations: non-commercial use, with attribution | CSV in the UN Digital Library, updated regularly: `wcs fetch-votes`, then `wcs ingest-votes` |
| [Security Council voting data](https://digitallibrary.un.org/record/4055387) (UN Dag Hammarskjöld Library) | Resolution 1 (24 January 1946) to resolution 2829 (29 September 2026), version 7 | 41,559 rows (one member's vote on one resolution), 2,829 resolutions; 16 MB | © United Nations: non-commercial use, with attribution | CSV in the UN Digital Library: `wcs council-votes`, and each `wcs update` ([decision 30](DECISIONS.md#30-votes-in-the-security-council-including-vetoes)) |
| [List of vetoes](https://research.un.org/en/docs/sc/quick/veto) (UN Dag Hammarskjöld Library) | 1946 to 30 October 2026 | 251 lines (date, draft, meeting record, agenda item, permanent members voting against) | © United Nations | Read from the Library's research guide once a day by `wcs update` |
| [UN General Debate website](https://gadebate.un.org) | Each session's statements until its official records are published | About 190 statements a session | © United Nations | Read by `wcs update` ([OPERATIONS.md](OPERATIONS.md#before-the-records-are-published)) |
| [UN M49 standard](https://unstats.un.org/unsd/methodology/m49/overview/) (UN Statistics Division) | Every country or area | 248 rows | United Nations | `scripts/fetch_m49.py` writes `data_static/m49.csv`: official English short names, regions, and the current lists of LDCs, LLDCs and SIDS |

Group memberships are compiled in `data_static/` ([Group memberships](#group-memberships)).

### The General Debate corpus

Harvard Dataverse asks for a short guestbook entry (name, email, institution, position, purpose) before the corpus
can be downloaded. `wcs fetch-gd` makes the entry through Dataverse's API, with the name and email from
`WCS_GUESTBOOK_NAME` and `WCS_GUESTBOOK_EMAIL`, takes the latest version's files and checks their checksums. To
download it by hand instead, fetch `UNGDC_<years>.tar.gz` and `Speakers_by_session.xlsx` from
https://doi.org/10.7910/DVN/0TJX8Y into `data/raw/ungd/` (on the server, `docker compose cp` them into
`/data/raw/ungd/`), then run `wcs ingest-gd`.

The site uses the corpus to the 78th session (2023). Its 2024 texts are the delegations' own, as prepared, and its
2025 texts are AI transcripts of the interpretation (its README says so), so those sessions come from the UN's
records instead. The corpus drops "(spoke in French)"; languages are filled in from the UN's records of the same
statements, which are read for the rights of reply from 1993. Printed paragraph numbers are removed. Every country
code in the real download is recognised.

## What the data showed

* Security Council file names. Meeting records are `UNSC_<year>_SPV.<n>_spch<k>.txt`. The videoconferences of 2020
  and 2021, recorded in letters rather than meeting records, are `UNSC_<year>_S_<year>_<n>_spch<k>.txt` (symbol
  `S/2020/751`); 5,072 of the corpus's meetings are of this kind. Resumed meetings are written
  `S/PV.9220 (Resumption 1)`, as the UN writes them. The Dataverse metadata says 1995 to 2020, but the files run
  from 1992 to 2023.
* Who spoke. The speaker table gives a name, a country or organisation (630 different labels) and whether the
  speaker was presiding ("The President"), a member ("Mentioned") or invited ("Guest"). 190 labels match M49 names
  directly; `data_static/aliases.csv` covers the rest of the countries ("Cote D'ivoire", "Slovak Republic"), and
  everything else is an organisation or UN office. Of the speeches, 56,000 are by countries, 38,000 by the presiding
  chair, 4,400 by UN officials and 2,300 by other organisations.
* Text. The speeches keep the printed records' line wraps, which break sentences and names across lines; loading
  joins the lines back into paragraphs. Records from 1992 to 1994 are text recognised from scans, with recognition
  errors ("distinguishad"), and early records write "(interpretation from French)" rather than "(spoke in French)".
* Group statements. About 3,300 speeches are made on behalf of a group, led by the Security Council itself
  (presidential statements and remarks for the members, 1,300), the European Union (1,000), the Non-Aligned
  Movement, the Nordic countries, the Arab Group and the African members of the Council.
* Country codes in the voting data. There is one code per seat across name changes: CHN from 1946 (the Republic of
  China's seat until 25 October 1971), RUS from 1992 (SUN before), and GER for the Federal Republic of Germany before
  1990 (stored as DEU). 193 codes have votes in 2026, matching today's membership.
* The voting data leaves out votes on separate paragraphs and on drafts that were not adopted.
* In the file of 15 October 2026 (looked at on 21 November 2026): 5,730 votes, each with its own symbol and UN Digital
  Library record, and each with a vote listed for each Member State it records; the record's totals agree with the votes
  listed on every one. 76 have no session: the special and emergency special sessions (`A/RES/ES-11/1`,
  `A/RES/997(ES-I)`, `A/RES/S-8/2`) and `A/RES/80/288`, so the session is read from the symbol. One is a decision
  (`A/DEC/80/506`). 585 drafts and 103 agenda items join two with `|`. Titles end with the catalogue's
  "resolution / adopted by the General Assembly"; 76 are worded otherwise or in capitals. The votes run from
  26 January 1946 to 17 September 2026, in 99 sessions: 80 regular sessions (every one from the 1st to the 81st
  but the 19th, which has no recorded vote in the data), 10 emergency special and 9 special.

### The Security Council's votes

Looked at on 22 November 2026 (version 7 of the voting data, 16 October 2026):

* 2,829 resolutions, every one with a member list; 78 adopted "without vote", where the data gives each member "X"
  (its description says such members are given "Y", which the file does not do), and notes such as "Adopted
  without a vote". "X" is otherwise a member that did not take part: the notes say "did not participate in the
  voting", or for the USSR in 1950, "was not represented". The draft symbol is given from 1998, and is sometimes a
  meeting record before then. Codes are as in the Assembly's data (SUN for the USSR, CHN for China's seat throughout,
  GER stored as DEU). A few resolution symbols carry a space before the year ("S/RES/2826 (2026)"), which is taken
  out.
* The list of vetoes has 251 lines from 16 February 1946 to 30 October 2026, 69 of them since 1992. Some lines are
  starred ("most are veto of paragraphs or amendments"), one draft can be listed several times (votes on its parts,
  1985), a proposal made at a meeting is listed by the meeting's symbol (1946), two drafts can share a line
  (`S/2025/115 | S/2025/116`), one line is an oral amendment (`S/2023/1029 | oral amendment`, 22 December 2023), and
  the vote taken by letter in 2020 gives two letters for the record. The private meeting of 19 November 1996 is
  "Not identified in private meeting communiqué, but attributed to United States". The list is shown in a frame from
  the Library's own service, whose address the research guide's page gives.
* The meeting records print a vote in several ways over the years: "The result of the voting is as follows" (to
  about 2005), "There were 9 votes in favour" (about 2010 to 2015), "The draft resolution received …" (since), "A
  vote was taken by (a) show of hands", "Abstaining" or "Abstentions", and in 2001 "One Council member did not
  participate in the voting". In 2020 a vote was taken by letter, and the President's letter gives the result, naming
  in brackets only the members against or abstaining; each member's vote is in the letter's annexes, which are not
  read. Read from the UN's PDFs, a few lists lose lines at the foot of a page (S/PV.9786, where the names in favour
  are carried into the next speech), and the 1993 and 1994 records are scans.
* Checked here against the UN's own PDFs of the meetings from 25 February 2022 to 30 October 2026 that the list
  names (23 lines, 24 vetoes, the oral amendment of 2023 among them): every one found, with the same permanent
  members against; one list incomplete (S/PV.9786, above). Twelve drafts and amendments that failed for want of
  nine votes were found at the same meetings. The server holds the records from
  1992, and its first build reports how many of the 69 vetoes since then it finds.

## Keeping current

`wcs update` runs twice a day ([OPERATIONS.md](OPERATIONS.md)).

- Security Council, from 2024: the UN's own verbatim records (`S/PV.<n>`, English PDF), found through the Digital
  Library's catalogue and by probing ODS for the following meeting numbers, and parsed by `wcs.sources.verbatim`.
  On meetings both cover, the parser matches the corpus closely ([VALIDATION.md](VALIDATION.md)). Closed meetings,
  which have only a communiqué, are recorded as such. Provisional records are fetched again once corrected.
- Votes: the voting data record's file list is read on each run, and a new file is downloaded and converted. The
  vote pages read the new file the first time they are asked for after it is written ([decision 26](DECISIONS.md#26-a-page-for-each-recorded-vote-in-the-general-assembly)).
- Security Council votes: the same for the Council's voting data; the list of vetoes is read once a day; and the
  drafts not adopted are read again from the stored Council records whenever any of the three changes, then checked
  against the list ([decision 30](DECISIONS.md#30-votes-in-the-security-council-including-vetoes)).
- General Debate, from the 79th session (2024): the General Assembly's own verbatim records (`A/<session>/PV.<n>`),
  parsed by `wcs.sources.unga`. Until a session's records are published, its statements come from the General Debate
  website, labelled as not yet the official record ([decision 12](DECISIONS.md#12-the-general-debate-website)).
  Rights of reply are kept as records of their own, from 1993 ([decision 13](DECISIONS.md#13-rights-of-reply)).

### The UN's services

| Service | Use | Behaviour |
|---|---|---|
| Digital Library search, `digitallibrary.un.org/search?p=191__a:S/PV.102*&of=xm&rg=200` | Discovery and metadata | MARC XML. Field 191 is the symbol; 992 the meeting date; 091 `PRO` while provisional; 245 the title ("Official communiqué ..." for a closed meeting); 991 agenda items; 856 a link to each language's PDF; 005 last modified. |
| ODS symbol service, `documents.un.org/api/symbol/access?s=S/PV.10235&l=en&t=pdf` | Discovery by probing, and download | Redirects to the PDF (`/pro/` provisional, `/gen/` final). A missing symbol redirects to an HTML error page with status 200. |
| `undocs.org/en/<symbol>` | Links for readers | Redirects to a viewer page on docs.un.org. |
| The same two services for `A/<session>/PV.<n>` | General Assembly plenary records | Catalogued with agenda items ("General debate"). Published about a year late and out of order. |
| `gadebate.un.org` | The General Debate before its records are published | A page per statement (`/en/<session>/<country>`), listed with a last-modified date in `/sitemap.xml` (sessions 64 to 81). Each has the speaker's name, post and date, the delegation's text as a PDF (English for most), and an AI transcript of the English interpretation (`/en/node/<id>/transcript/en/prepare-download`, POST, returns a link to a text file). Behind an AWS WAF browser check: a non-browser user agent gets "Human Verification" (405); a browser's is let through, but not from the live server (November 2026). robots.txt does not disallow the statement pages. |
| `press.un.org` | Not used | Same-day summaries of each meeting, behind a JavaScript challenge that a plain client cannot pass. |

## Group memberships

Memberships are dated, so a country is grouped by what it belonged to on the date of each statement
([decision 5](DECISIONS.md#5-groups)).

* `data_static/memberships.csv` holds the EU, NATO, the Group of 77, the Non-Aligned Movement and the OIC, and
  three sets built by `scripts/build_groups.py`: the UN's five regional groups, the World Bank's income groups, and
  African Union suspensions.
* The regions, African Union membership and the UN's LDC, LLDC and SIDS lists are worked out in
  `wcs.reference.countries`.
* UN regional groups are as the UN lists them, with Israel in the Western European and other States from May 2000,
  Türkiye in two groups, and the United States counted in that group for elections. The UN publishes no joining
  dates, so each member is dated from its first recorded vote.
* World Bank income groups are by the Bank's fiscal year since 1988, from 1 July
  (`data_static/sources/OGHIST_2025_07_01.xlsx`).
* African Union suspensions run from 2002. A suspended state remains a member, and source titles say it was
  suspended.
* Non-Aligned Movement (121 members) and OIC (57): dates of admission are from worldstatesmen.org's lists of
  international organisations, checked against Wikipedia, and every member agrees. For seven of the OIC's founding
  members the two differ (formal admission on 29 February 1972, or attendance from 1970; Uzbekistan 1995 or 1996):
  the formal dates are used, and the other is noted in the file. Departures and suspensions are dated: Argentina
  (left 1991), Yugoslavia (suspended 1992), and Cyprus and Malta (left on joining the EU in 2004) from the NAM;
  Egypt (1979 to 1984), Afghanistan (1980 to 1989) and Syria (August 2012 to March 2025) suspended from the OIC.
* Group of 77: members as the Group lists them (g77.org, 134, including the State of Palestine), less China, which
  says it is not a member ([decision 15](DECISIONS.md#15-china-and-the-group-of-77)). The Group publishes no joining
  dates. Founders date from 15 June 1964; the later joiners that matter for the record since 1992 are dated from
  reports (Azerbaijan 27 September 2019, South Sudan by October 2014); Kiribati and Nauru are approximate (2014);
  the rest are dated from their first recorded UN vote. Former members are Mexico (to 1994) and the Republic of
  Korea (to 1996), which left on joining the OECD; Cyprus, Malta and Romania, which left on joining the EU;
  Yugoslavia; Palau (2002 to 2004); and Bosnia and Herzegovina (removed February 2020). Mexico's return was approved
  in 2023 but it is not yet on the Group's list, so it is not counted.

After changing `memberships.csv` or `groups.csv`, `wcs regroup` updates the groups of every indexed passage in place
(about a minute; nothing is embedded again).

## Licence of briefings

Briefings built on UN material are licensed CC BY-NC 4.0 ([decision 8](DECISIONS.md#8-licence)), because the UN's
own terms are non-commercial with attribution. The two corpora are CC0, so they add no conditions. Quotations from
the records are credited to the UN, with the document symbol and page.

## Possible future sources

| Source | Why | Notes |
|---|---|---|
| General Assembly plenary verbatim records (`A/…/PV.…`) beyond the General Debate | The rest of the Assembly's debates, including explanations of vote | The same loader as for the Security Council. From 1992. |
| UNFCCC COP high-level segment statements | The climate equivalent of the General Debate | PDFs on unfccc.int |
| ICJ written and oral statements | Countries' formal legal positions. The climate change advisory opinion drew 91 written statements. | PDFs on icj-cij.org |
| UPR recommendations and responses ([UPR Info](https://upr-info-database.uwazi.io/)) | 79,000 to 90,000 or more recommendations since 2008, with whether each was accepted or only "noted" | Bulk export to be confirmed |
| Treaty status, reservations and declarations (UN Treaty Collection) | Formal commitments, and exactly what countries won't accept | No bulk download: needs scraping |
| Climate pledges (NDCs) and laws ([Climate Policy Radar](https://github.com/climatepolicyradar/open-data)) | Climate commitments, full text | CC BY 4.0 |
| EU sanctions alignment declarations | Which non-EU countries align with each EU sanctions decision | Compile from Council of the EU press releases |
| OCHA Financial Tracking Service, IATI, Kiel Ukraine Support Tracker | What countries fund, beside what they say | Open APIs and downloads |
| Earth Negotiations Bulletin (IISD) | Who argued what in environmental negotiations since 1992 | Reuse terms unknown: ask IISD |

## Open questions

- The General Debate website's browser check. The update gets past it by presenting itself as a browser, but not
  from the live server, so recent statements come from files read elsewhere (`data_static/gd_website/`). Access or
  a feed from the UN would put this on a firmer footing.
