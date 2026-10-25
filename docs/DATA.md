# Data sources

Checked on 24 October 2026. "Licence" means the terms for republishing, not just for reading.

## First version

| Source | Coverage | Size | Licence | Access |
|---|---|---|---|---|
| [UN General Debate Corpus](https://doi.org/10.7910/DVN/0TJX8Y) (Jankin, Baturo, Dasandi) | 1946–2025, every country's annual statement, English | 10,000+ statements; 71 MB compressed | **CC0** | Harvard Dataverse. The download asks for a short "guestbook" form (name, email, institution, position, purpose), which `wcs fetch-gd` fills in through Dataverse's API (name and email from `WCS_GUESTBOOK_NAME` and `WCS_GUESTBOOK_EMAIL`), taking the latest version's files and checking their checksums; `wcs ingest-gd` loads them. Version 14, released March 2026: the authors add each new session. Used to the 78th session (2023): its 2024 texts are the delegations' own, as prepared, and its 2025 texts AI transcripts of the interpretation (its README says so), so those sessions come from the UN's records instead. The corpus drops "(spoke in French)"; languages are filled in from the UN's records of the same statements, from 2000 (1,387 statements). Printed paragraph numbers ("81.<tab>") are removed. Tested on the real download (October 2026): 11,141 statements, every country code recognised. |
| [UN Security Council Debates](https://doi.org/10.7910/DVN/KGVSYH) (Schoenfeld, Eckhard, Patz, van Meegdenburg and others) | 6 January 1992 to 30 December 2023 | 106,088 speeches at 6,595 meetings (including 5,072 from the 2020–21 videoconferences); speeches 474 MB | **CC0** | Harvard Dataverse, direct download: `wcs fetch-sc`. The Dataverse metadata says 1995–2020, but the files run from 1992 to 2023. |
| [General Assembly voting data](https://digitallibrary.un.org/record/4060887) (UN Dag Hammarskjöld Library) | 1946 to 17 September 2026 (resolution 81/1) | 954,382 rows: one country's vote on one resolution, 5,730 resolutions | © United Nations: **non-commercial use, with attribution** | CSV in the UN Digital Library, updated regularly: `wcs fetch-votes`. It excludes votes on separate paragraphs and on drafts that were not adopted. |
| [UN M49 standard](https://unstats.un.org/unsd/methodology/m49/overview/) (UN Statistics Division) | Every country or area | 248 rows | UN | `scripts/fetch_m49.py` writes `data_static/m49.csv`: official English short names, regions, and the current lists of LDCs, LLDCs and SIDS. |

### What the data showed

* **Security Council file names.** Meeting records are `UNSC_<year>_SPV.<n>_spch<k>.txt`; the videoconferences of
  2020 and 2021, recorded in letters rather than meeting records, are `UNSC_<year>_S_<year>_<n>_spch<k>.txt`
  (symbol `S/2020/751`). Resumed meetings are written `S/PV.9220 (Resumption 1)`, as the UN writes them.
* **Who spoke.** The speaker table gives a name, a country or organisation (630 different labels) and whether the
  speaker was presiding ("The President"), a member ("Mentioned") or invited ("Guest"). 190 labels match M49 names
  directly; `data_static/aliases.csv` covers the rest of the countries ("Cote D'ivoire", "Slovak Republic"), and
  everything else is an organisation or UN office. Of the speeches: 56,000 by countries, 38,000 by the presiding
  chair, 4,400 by UN officials and 2,300 by other organisations.
* **Text.** The speeches keep the printed records' line wraps (with Windows line endings), which break sentences and
  names across lines; loading joins the lines back into paragraphs. Records from 1992 to 1994 are text recognised
  from scans, with recognition errors ("distinguishad"), and early records write "(interpretation from French)" rather
  than "(spoke in French)".
* **Group statements.** About 3,300 speeches are made on behalf of a group, led by the Security Council itself
  (presidential statements and remarks for the members, 1,300), the European Union (1,000), the Non-Aligned Movement,
  the Nordic countries, the Arab Group and the African members of the Council.
* **Country codes in the voting data.** One code per seat across name changes: CHN from 1946 (the Republic of China's
  seat until 25 October 1971), RUS from 1992 (SUN before), GER for the Federal Republic of Germany before 1990 (stored
  as DEU). 193 codes have votes in 2026, matching today's membership.

### Keeping current

`wcs update` (scheduled twice a day; see [`OPERATIONS.md`](OPERATIONS.md)):

- **Security Council, 2024 onwards:** the UN's own verbatim records (`S/PV.nnnn`, English PDF), found through the
  Digital Library's catalogue and by probing ODS for the following meeting numbers, downloaded from either, and parsed
  by `wcs.sources.verbatim`. Checked against the corpus on meetings both cover: median speaker order 1.00, wording
  0.98, all the text (see [`VALIDATION.md`](VALIDATION.md)). Closed meetings, which have only a communiqué, are
  recorded as such. Provisional records are fetched again once corrected.
- **Votes:** the voting data record's file list is read on each run, and a new file is downloaded and converted.
- **General Debate, 79th session (2024) onwards:** the General Assembly's own verbatim records (`A/<session>/PV.<n>`),
  parsed by the same parser (`wcs.sources.unga`); each statement is the speech the President introduces, with the
  speaker's name and post from the introduction. The records are published about a year after the debate; until
  then each statement comes from the UN's General Debate website (the delegation's English text, or an AI transcript
  of the English interpretation), labelled as not yet the official record and replaced by it when it is published
  (decision 12). Rights of reply are kept as records of their own, from 2000 (decision 13). Checked on the 79th session (2024): all 11 debate meetings, 192
  statements, every Member State that spoke (see [`OPERATIONS.md`](OPERATIONS.md#the-general-debate)). Sessions
  before the 79th come from the corpus.

What the UN's services do, as found on 24 October 2026:

| Service | Use | Behaviour |
|---|---|---|
| Digital Library search, `digitallibrary.un.org/search?p=191__a:S/PV.102*&of=xm&rg=200` | Discovery and metadata | MARC XML. Field 191 the symbol; 992 the meeting date; 091 `PRO` while provisional; 245 the title ("Official communiqué ..." for a closed meeting); 991 agenda items; 856 a link to each language's PDF; 005 last modified. |
| ODS symbol service, `documents.un.org/api/symbol/access?s=S/PV.10235&l=en&t=pdf` | Discovery by probing, and download | Redirects to the PDF (`/pro/` provisional, `/gen/` final). A missing symbol redirects to an HTML error page with status 200. |
| `undocs.org/en/<symbol>` | Links for readers | Redirects to a viewer page on docs.un.org. |
| The same two services for `A/<session>/PV.<n>` | General Assembly plenary records | Catalogued with agenda items ("General debate"). Published about a year late and out of order: on 24 October 2026, `A/80/PV.1`–`4` and `PV.18` were out, the 80th session's debate (`PV.5` onwards) was not. |
| `gadebate.un.org` | The General Debate before its records are out | A page per statement (`/en/<session>/<country>`), listed with a last-modified date in `/sitemap.xml` (sessions 64 to 81). Each has the speaker's name, post and date, the delegation's text as a PDF (English for most; others in their language), and an AI transcript of the English interpretation (`/en/node/<id>/transcript/en/prepare-download`, POST, returns a link to a text file). Behind an AWS WAF browser check: a non-browser user agent gets "Human Verification" (405); a browser's is let through. robots.txt does not disallow the statement pages. |
| `press.un.org` | Not used | Same-day summaries of each meeting. A JavaScript challenge, which a plain client cannot pass. |

### Licence of briefings

Briefings built on UN material are licensed **CC BY-NC 4.0** (decision 8 in
[`DECISIONS.md`](DECISIONS.md)), because the UN's own terms are non-commercial with attribution.
The two corpora are CC0, so they impose no conditions of their own. Quotations from the records are
credited to the UN, with the document symbol and page.

## Later

| Source | Why | Notes |
|---|---|---|
| General Assembly plenary verbatim records (`A/…/PV.…`) | The rest of the Assembly's debates, including explanations of vote | The same loader as for the Security Council. From 1992. |
| UNFCCC COP high-level segment statements | The climate equivalent of the General Debate | PDFs on unfccc.int |
| ICJ written and oral statements | Countries' formal legal positions. The climate change advisory opinion drew 91 written statements. | PDFs on icj-cij.org |
| UPR recommendations and responses ([UPR Info](https://upr-info-database.uwazi.io/)) | 79,000–90,000+ recommendations since 2008, with whether each was accepted or only "noted" | Bulk export to be confirmed |
| Treaty status, reservations and declarations (UN Treaty Collection) | Formal commitments, and exactly what countries won't accept | No bulk download: needs scraping |
| Climate pledges (NDCs) and laws ([Climate Policy Radar](https://github.com/climatepolicyradar/open-data)) | Climate commitments, full text | CC BY 4.0 |
| EU sanctions alignment declarations | Which non-EU countries align with each EU sanctions decision | Compile from Council of the EU press releases |
| OCHA Financial Tracking Service, IATI, Kiel Ukraine Support Tracker | What countries fund, beside what they say | Open APIs and downloads |
| Earth Negotiations Bulletin (IISD) | Who argued what in environmental negotiations since 1992 | Reuse terms unknown: ask IISD |

## Open questions

- **The General Debate website's browser check.** The update gets past it by presenting itself as a browser
  (decision 12). If the UN tightens it, the debate falls back to arriving a year late. Asking the UN for access or a
  feed would put this on a firmer footing.
- **Rights of reply before 2000.** The 1990s General Assembly records are printed in two columns that come out of the
  PDF in the wrong order (and before 1996 are scans without text), so their replies are not read yet.

- **Group memberships on a date.** In `data_static/memberships.csv` (EU, NATO, and since October 2026 the Group of
  77, the Non-Aligned Movement and the OIC), with the regions, African Union and the UN's LDC, LLDC and SIDS lists
  worked out in `wcs.reference.countries`. Still not covered: UN regional groups as used for elections, African
  Union suspensions, World Bank income groups by year.
  * **Non-Aligned Movement** (121 members) and **OIC** (57): dates of admission from worldstatesmen.org's lists of
    international organisations, checked against Wikipedia; every member agrees. The OIC's founding-era dates differ
    for seven members between the two (formal admission on 29 February 1972 against attendance from 1970, and
    Uzbekistan 1995 or 1996); the formal dates are used, and the other noted in the file. Departures and suspensions
    are dated: Argentina (left 1991), Yugoslavia (suspended 1992), Cyprus and Malta (left on joining the EU in 2004)
    from the NAM; Egypt (1979-84), Afghanistan (1980-89) and Syria (August 2012 to March 2025) suspended from the OIC.
  * **Group of 77**: members as the Group itself lists them (g77.org, 134, including the State of Palestine), less
    China (133). The Group lists China, but China says it is not a member, only a supporter, so it is left out
    (decision 15); the Group's joint statements, made "on behalf of the Group of 77 and China", are attributed to
    the Group as always. The Group publishes no dates of joining. Founders date from 15 June 1964; the later joiners that matter
    for the record since 1992 are dated from reports (Azerbaijan 27 September 2019, South Sudan by October 2014);
    Kiribati and Nauru are approximate (2014); the rest are dated from their first recorded UN vote. Former members:
    Mexico (to 1994) and the Republic of Korea (to 1996), which left on joining the OECD; Cyprus, Malta and Romania,
    which left on joining the EU; Yugoslavia; Palau (2002-04); Bosnia and Herzegovina (removed February 2020).
    Mexico's return was approved in 2023 but it is not yet on the Group's list, so it is not counted.
  * After changing the file, `wcs regroup` updates the groups carried by every indexed passage in place (about a
    minute; nothing is re-embedded).
- **Bulk access to UN verbatim records.** Check whether the UN Digital Library allows harvesting
  (for example through an OAI-PMH interface or search exports), or whether documents must be
  fetched one at a time. Fetch politely in either case.
- **Statements on behalf of groups** ("on behalf of the Group of 77 and China") need detecting and
  storing as the group's statement, listing the members covered.
- **Country codes over time**, with successor states (the USSR and the Russian Federation,
  Yugoslavia, Czechoslovakia, Germany, Sudan and South Sudan). Before 1971, China's seat was held by
  the Republic of China.
