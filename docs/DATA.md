# Data sources

Checked on 24 October 2026. "Licence" means the terms for republishing, not just for reading.

## First version

| Source | Coverage | Size | Licence | Access |
|---|---|---|---|---|
| [UN General Debate Corpus](https://doi.org/10.7910/DVN/0TJX8Y) (Jankin, Baturo, Dasandi) | 1946–2025, every country's annual statement, English | 10,000+ statements; 71 MB compressed | **CC0** | Harvard Dataverse. The download asks for a short "guestbook" form (name, email), so it is fetched by hand: put `UNGDC_1946-2025.tar.gz` and `Speakers_by_session.xlsx` in `data/raw/ungd/`, then run `wcs ingest-gd`. Version 14, released March 2026: the authors add each new session. |
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

- **General Debate:** the corpus is updated each year. Load each new release.
- **Security Council, 2024 onwards:** our own loader for the verbatim records (`S/PV.nnnn`, English
  PDF) from the UN Digital Library. Check it against the corpus on the overlapping years
  (2020–2023) before trusting it.
- **Votes:** reload the CSV regularly.

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

- **Group memberships on a date.** The Correlates of War organisation-membership data is believed
  to stop in 2014. Memberships since then (BRICS expansion, EU candidates, African Union
  suspensions) may need adding by hand from official sources. Needed: UN regional groups, EU, AU,
  NAM, G77, OIC, NATO, permanent and elected Council members, World Bank income groups by year.
- **Bulk access to UN verbatim records.** Check whether the UN Digital Library allows harvesting
  (for example through an OAI-PMH interface or search exports), or whether documents must be
  fetched one at a time. Fetch politely in either case.
- **Statements on behalf of groups** ("on behalf of the Group of 77 and China") need detecting and
  storing as the group's statement, listing the members covered.
- **Country codes over time**, with successor states (the USSR and the Russian Federation,
  Yugoslavia, Czechoslovakia, Germany, Sudan and South Sudan). Before 1971, China's seat was held by
  the Republic of China.
