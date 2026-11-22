# Decisions

The product decisions behind Where Countries Stand, each with its reason. They are the owner's, and they take
precedence over anything else in this repository. Code and other documents refer to them by number ("decision 7").
A new decision is added at the end with the next number.

## 1. Name and address

Where Countries Stand is a website of its own, at wherecountriesstand.com, and a project of Where They Stand. It sits
on the family's International shelf with Where Lives Stand.

## 2. Code

The site has its own repository. What it shares with the other Where They Stand sites comes from their common
package, wts-core (decision 18), so it is written and fixed once.

## 3. Coverage

The site covers the Security Council's debates, the General Assembly's General Debate and the Assembly's recorded
votes, with countries' group memberships, from 1992. From 1992 the Russian Federation holds the Soviet seat and most
of today's members are present. The General Debate and the votes go back to 1946 as an archive, searched when a
question asks for earlier years. Briefings cover the last 10 years unless the question gives dates, with an obvious
control to widen the range.

## 4. Keeping current

The academic corpora supply the back catalogue. The site's own loader reads the UN's verbatim records from 2024, and
is checked against the corpora on the years both cover ([VALIDATION.md](VALIDATION.md)). The Security Council
corpus ends in 2023, and the General Debate corpus's texts from 2024 are prepared texts and AI transcripts
rather than the record.

## 5. Groups

Countries are grouped by their memberships on the date of each statement (blocs, regions, income groups, place on
the Security Council), as Where MPs Stand uses an MP's party and role on the date. Before 25 October 1971, "China"
is the Republic of China, which held the seat until then.

## 6. Country names

The site uses the UN's own names and status throughout (Member State, observer State, non-member), and says so on
the site. This keeps the site neutral on disputed names.

## 7. Neutral wording

The charity's impartiality policy and Where MPs Stand's wording rules apply. The site's own voice also avoids
"regime" (it says "government"), "rogue state", "the West" and similar labels. "Condemned" is allowed when it
describes a formal statement. A statement made on behalf of a group is attributed to the group, never to each of its
members.

A reader's question is never presented in the site's voice. A briefing's page title and link preview say "Briefing
from UN records" and the date it was written, and the page shows the question as one a reader asked. The ask API
takes no search plan and no earlier answers from the browser, so the site always reads the question itself and the
model never sees text presented as a briefing that the site did not write.

## 8. Licence

The code is licensed under AGPL-3.0, as Where MPs Stand's is. Briefing text is licensed under CC BY-NC 4.0, because
the UN's terms for its documents are non-commercial.

## 9. Hosting and budget

The site runs on the charity's shared server, beside Where MPs Stand, which had room for it. Its AI spending has caps
of its own: $3 a day and $50 a month to start, and $25 a day and $100 a month from 19 November 2026, the owner's decision
([DEPLOY.md](DEPLOY.md#spending)).

## 10. Testing

A test set of about 50 questions (`eval/questions.toml`), including sparse ones where "No records found" is the
right answer, measures how questions are read and what evidence is found.

## 11. Look

The site uses the Where They Stand family design (family kit 2.0), and never the UN's name, emblem or wordmark as a
mark ([DESIGN.md](DESIGN.md)). The UN's name and emblem are protected and reserved for the UN's own use.

## 12. The General Debate website

Until a session's official records are published (about a year after the debate), its statements come from the
UN's General Debate website, gadebate.un.org. They are labelled as not yet the official record, and are replaced by
it when it is published. Without this the most recent debate would be missing for a year. The website's browser
check turns away automated clients, so the update presents itself as a browser and reads only what robots.txt
allows, one request at a time ([OPERATIONS.md](OPERATIONS.md#the-general-debate)).

## 13. Rights of reply

Rights of reply in the General Debate are records of their own ("General debate: right of reply"), never mixed with
the statements, so a reply is never mistaken for a country's statement. Points of order are left out. They come from
the UN's records from 1993: the corpus has none, and earlier records are scans that cannot be read reliably.

## 14. Briefing length

There are two kinds of briefing, as on Where MPs Stand. Summary is the default: 24 sources and about 300 to 450
words. Comprehensive has 40 sources and about 700 to 1,300 words, and adds how positions changed over time and the
main points of agreement and contention. The choice is shown beside the question box.

## 15. China and the Group of 77

China is not counted as a member of the Group of 77. China says it is only a supporter of the Group, although the
Group lists it. The site describes a country's memberships as the country itself states them. The Group's joint
statements, made "on behalf of the Group of 77 and China", are attributed to the Group (decision 7).

## 16. Corrections and the right of reply

The charity's corrections and right-of-reply policy (its impartiality policy, section 7) is built into the site, in
the family's wording. The "If you're named" page (`/named.html`, linked from the About, corrections, privacy and
terms pages) is for countries' delegations, missions and governments, people named in the UN's records, and anyone
acting for them. It sets out three routes, each starting on the corrections page's form: "Something is wrong",
"You've been characterised unfairly" and "Add a statement in your own words". The site replies within 5 working days
and decides within 20.

Corrections cover everything the site adds to the record: briefings, the groups a country is counted in on a date,
country names and status, speaker labels, the "not yet the official record" and scanned-record labels, and links to
sister sites.

A statement is up to 150 words, with its author's name and role, about one saved briefing. It is published as
written once the maintainer has confirmed who sent it, or declined with a reason, and its author can update or
withdraw it at any time. It is shown after the briefing, marked as the author's own words and not checked or
endorsed by the site. Statements are never given to the AI model, never indexed and never part of a briefing, so
the record stays the UN's alone (`tests/test_statements.py`). How the maintainer handles reports and statements is
in [OPERATIONS.md](OPERATIONS.md#problems-corrections-and-withdrawn-briefings).

## 17. Explaining the AI briefings

The site says plainly why its briefings can be relied on when the same question could be put to a general AI
chatbot, using the family's shared wording where it is true here. The home page has one line under the question box
and a disclosure, "How the briefings are checked", with five short points. The About page has a section, "AI
briefings", with the same points at more length.

Three of the family's phrases are changed because they would not be true here. The site says "the UN's records"
where the family says "the official record", because the newest General Debate speeches come from the UN's website
(decision 12). It says evidence is drawn "from across groups of countries (by region, by bloc or by place at the
Security Council)", because a briefing uses one grouping at a time. It claims document symbols only for statements
taken from the UN's verbatim records, because the General Debate corpus has none.

Each claim rests on the code: what the model is given (`wcs.rag.answer`, and rule 1 of `wcs.rag.prompts`), the
checks (`wcs.rag.checks`), "No records found" (`wcs.rag.pipeline`), the balance across groups (`wcs.rank.evidence`),
the twice-daily update (`deploy/wcs-update.timer`) and coverage (`config/settings.toml`, [DATA.md](DATA.md)). Check
the claims against these before changing either.

## 18. The shared engine

The site is built on wts-core, the Where They Stand sites' shared package (public, AGPL-3.0), installed as the
wheel of a release at a fixed version, with CI's shared workflows at the same tag. The package provides what the
sites do alike: settings, the state database, limits and spending caps, the checks on briefings, the flow of a
question, the model client, the embedder and the index's tables, readers' reports and the right of reply, backups,
the record of each briefing with its weekly report and alerts, and the evaluation harness. This repository keeps what
is particular to the UN's record (loaders, attribution, groups, the index's columns, reading a question, retrieval and
ranking, the instructions to the model and the pages) and every word a reader is shown.

Readers see the site behave as it did on its own code, apart from the differences listed in wts-core's design
documents. `tests/test_parity.py` checks this against recorded outputs in `tests/golden/`. The rule for flagging a
"note about the evidence" stays the wider of the family's two forms; narrowing it would be a change of its own, to
be measured first.

## 19. The move onto wts-core

The site moves onto wts-core 0.3.0 in a single deploy. The first time it starts on the shared package it copies its
old tables in the state database into the package's tables. The old tables are kept, and held to the privacy notice,
until 1 February 2027, so that the move can be undone until then. How the move works, and how to go back, is in
[OPERATIONS.md](OPERATIONS.md#the-move-onto-wts-core).

## 20. Every point to check is marked

A sentence whose citations all failed the check against their sources loses those citations and is set in grey,
as a sentence with no source is. The note under the briefing says so: "One sentence in grey gave a source that did
not bear it out when checked, so the citation was removed: check the record before relying on it." The About page's
"Method" section says the same. A reader should be able to see every point that needs checking. The accessibility
check (`scripts/a11y.mjs`) opens a made-up briefing with each kind of point to check, light and dark, and fails if
one is not marked.

Changed by [decision 25](#25-checked-briefings) (21 November 2026): such a sentence is marked as one with no source
is, with a dotted underline and words, never grey, and the note under the briefing moves into the check strip and
"How this briefing was checked".

## 21. Countries named in a question are heard

On 19 November 2026 the owner decided that a briefing includes the countries its question names, if they have spoken
on the subject. An audit found briefings on contested subjects that left out the very countries the question was
about: a question about Western countries shielding Israel drew 24 sources and none from Israel, and a question
about Russia and Ukraine had no statement by the Russian Federation. Evidence followed the question's wording and
was balanced only across groups, so a country whose statements were worded differently could go unheard.

- A country counts as named when the question names a State the site's country names resolve ("Russia's" is read
  as "Russia"), or writes "US", "UK" or "America" (not "Latin America"). A group ("the EU", "NATO", "Western
  countries") is not a State and is not expanded into its members. At most six are taken, in the order the question
  names them.
- Each is searched for on its own, with the same search words, dates, bodies and groups as the question. Its
  statements must clear the same tests as any other source: found by words, or close enough in meaning, and at
  least a quarter as relevant as the best source found (`min_relevance_ratio`).
- Its best statements are taken first, within the same number of sources: two each, as for any country, while the
  named countries together hold no more than a quarter of the places, and one each otherwise. The other places are
  filled by turns across groups as before, so the turns that would have come last make way. A statement made on
  behalf of a group is not the country's own.
- A country that has said nothing relevant is not brought in. The summary of the evidence lists each named country
  with the number of its statements, or says no relevant statement by it was found, and the model is told the same.
- A question that asks what particular countries said (they are filters) is unchanged: only they are searched.

## 22. The home page's dates need no script

On 21 November 2026 an outside review, and the charity's review plan (A1), found that search engines saw "The latest
dates in the record could not be loaded just now." on the home page. The page loaded its dates from `/api/meta`,
which robots.txt closed to crawlers, and Google's renderer obeys robots.txt for the fetches a page makes. Readers saw
the dates. The fix follows Where MPs Stand's decision 40 (19 November 2026):

- The server writes what the record covers into the home page ("1992 to 20 November 2026"), so no fetch is needed.
  With nothing read in, or if the record can't be read, the page keeps its own words ("from 1946").
- robots.txt allows the free, read-only answers the pages are drawn from (`/api/meta`, `/api/countries`,
  `/api/groups`, and the public log of corrections and the status the corrections and status pages show).
  Everything else under `/api/` stays closed: briefings, statements, reports and the check token's address.
- The page's script still refreshes the dates. If that fails, the server's text stays and no error is shown.

## 23. A methodology page, and the family's footer

On 21 November 2026 the charity's review plan (C4, "Same names for the same pages") asked for the same page names
across the family: Where Councils Stand, Where Landlords Stand, Where Lives Stand and Where MPs Stand each have a
methodology page, and this site kept its method inside About.

- The method moves to its own page, `/methodology.html` ("Methodology | Where Countries Stand"), linked in the menu
  (Ask, About, Methodology, Glossary) and in every footer. It holds the "AI briefings" section in full, with all
  seven of the family's points (the glossary, "AI briefings"), "The record", "From question to briefing" (the
  "Method" section, renamed so that it does not repeat the page's name) and "Impartiality". Decision 20's note on
  sentences whose citations failed is now on this page.
- About keeps a short "AI briefings" section, with its two short parts, as the glossary asks of About pages, and a
  "Methodology" section linking to each part of the new page. The anchors people may have linked to
  (`/about.html#ai-briefings`, `#given`, `#checked`, `#how`, `#record` and `#impartiality`) all still land on About,
  beside a link to the same part of the methodology page. About keeps its licences, its "More about the site" list
  and its "In the family" paragraph. The home page's "How the briefings are checked" now links to the methodology
  page, as the glossary says.
- The footer follows the glossary: About, Methodology, Corrections, Accessibility, Privacy, Source code, and no
  more. "If you're named" is reached from Corrections and About, not from the footer, as the plan's decisions record.
- The menu gained "Topics" after Ask on 21 November 2026 ([decision 27](#27-topic-briefings)).
- The page is found by the sitemap as every other page is. The charity's nightly check should add
  `/methodology.html` to this site's `crawlerPages` once the page is live.

## 24. The same question on Where MPs Stand

On 21 November 2026 the charity's review plan (C1, "Briefings to sister sites") asked each briefing site to offer
the same question on a sister site where it makes sense, naming the sister site and showing its spine. For this site
that is Where MPs Stand, which takes a question as `?q=`.

- Every briefing, as it is written and when it is opened at `/b/<id>`, ends with "Ask the same question on Where
  MPs Stand", linking to `https://wherempsstand.com/?q=` and the question, URL-encoded. It sits after the briefing,
  with the briefing's address and the link to report a problem, and before any statements under the right of reply.
- The spine beside it is the family kit's own drawing of Where MPs Stand's spine, copied from the shelf in the same
  page's footer, so it changes when the kit does and is never drawn by hand here.
- It is offered only once a briefing has been written: not for "No records found", not when the briefing failed,
  and not for sources shown without a briefing. The links to Where Lives Stand for the countries a question is
  about follow the same rule, so a page offers nothing it has not answered itself.
- `scripts/a11y.mjs` opens a saved briefing, a saved "No records found" and a saved failure in a browser, and fails
  if the link is missing, wrongly addressed, worded or without its spine, or offered where it should not be.

## 25. Checked briefings

On 21 November 2026 the charity's review plan (Part B, "make the checking the design") asked the three briefing sites
to show their checking as design rather than prose, in one family design chosen from a prototype (direction B,
"ledger"; the charity's `family/BRIEFINGS.md` and kit 2.1.0). On this site:

- B1 and B3, the briefing: The check strip ("Checked against the UN record") replaces the note under the
  briefing and the legend in `#marks`; record quotations, source chips and the record beside the briefing (a bottom
  sheet on phones) follow the family's markup ([DESIGN.md](DESIGN.md#checked-briefings)). Text with no source is
  always shown and marked with a dotted underline and words, never grey, as the owner decided (answer 9): grey read
  as "secondary", and colour alone fails WCAG 1.4.1. A sentence whose citation failed is marked the same way, which
  changes decision 20.
- The display rule: Every answer shown passes through wts-core's `quotes_beside` on the server: a new
  briefing's final event (after it is saved and cached as the verifier wrote it), a cached answer and a saved one.
  It was found in saved briefing kGfKBI3cRLw, where Italy's quotation, found word for word in its source, was shown
  grey because the model attached the citation to "in November 2025". The page also counts a quotation in a lead-in
  as the source cited straight after it, as the verifier checks it: version 2 of the checks reads such a lead-in as
  a join, not a claim, so in new briefings the case no longer comes as text with no source, but its quotation still
  needs its source named. The checks and their counts are unchanged; the strip counts sentences without a source on
  the segments as shown (wts-core's `check_counts`), and "How this briefing was checked" keeps the verifier's own.
- Chip wording: The country or group, the body and the date in full ("Italy, Security Council, 14 November
  2025"). A statement on behalf of a group names the group (decision 7). A General Assembly vote's chip says
  "Recorded vote, General Assembly" and its date. A General Debate statement whose record gives only the session
  shows the year. A chip's accessible name is its own words, "Source 1 Italy, Security Council, 14 November 2025.
  Show the passage", and not the kit's `aria-label` ("Source 1: …"), which axe fails under WCAG 2.5.3 (label in
  name) because the visible "1Italy, …" is not part of it. The charity's kit should take the same markup.
- Judgemental words in the AI's own voice are underlined in the warning colour on a light tint, word by word
  (they were dotted, a whole sentence at a time, which the mark for no source now uses), with their words for
  screen readers; the strip says how many there are.
- B6, the wait: The stages follow the stream's real events ([DESIGN.md](DESIGN.md#the-wait)); cards are marked
  "Cited" when the model's citation event arrives, and the counts run up through the final event's real results.
  The streaming text is laid out as it will be (headings, lists, bold) rather than as raw text.
- "Sources only (no AI)" is a second button beside "Prepare briefing", an equal choice, in place of a checkbox
  under the box ("Sources only (no written briefing)"). Addresses with `?sources=1` work as before. With no model
  configured the first button says "Find sources" and the second is not shown.
- B4, the home page: The grey side box gives way to the family's line and disclosure under the question box,
  and the static proof, built from a real sentence of kGfKBI3cRLw ("Italy said it does not support expanding the
  category of permanent members, which “would inevitably generate additional vetoes”", source 1, S/PV.10043
  (Resumption 1), 14 November 2025): the cleanest sentence in it, fully cited, with no mark for text with no source.
  A fixed copy of that briefing, as `/api/briefings/kGfKBI3cRLw` gave it on 21 November 2026, is kept in
  `tests/fixtures/`, and the tests check that the proof's words are its own. The first step says "Searches only the
  UN's records", as decision 17 has the site say "the UN's records" for "the official record".
- B5, share cards: A saved briefing's link preview is its own card ([DESIGN.md](DESIGN.md#share-cards)), with the
  site's neutral title, never the reader's question (decision 7). The site has no topic briefings yet; when it has,
  each topic's card will carry the topic's own question.
- The instructions: The model is told to attach each citation to the claim and its quotation, not to a date or
  other words beside them (instructions version 4). Answers cached under version 3 are not served again.
- Printing keeps the numbers ("[3]") and the sources as the printed reference list. The site has no Word export.

The prototype the design was chosen from is published at https://claude.ai/artifact/MrYFRm3pmhdi7n1uhZ2pKR and kept
in the charity's repository (`family/prototypes/checked-briefings/`).

## 26. A page for each recorded vote in the General Assembly

On 21 November 2026 the owner agreed (answer 6 of the charity's review plan, C3) that the site gives a page to each
General Assembly recorded vote, so that a first-time visitor or a search engine finds the record itself to read,
not only a question box. All of them, from 1946: 5,730 votes in the UN's voting data of 15 October 2026, in 99
sessions.

- Addresses: the index is `/votes.html` ("Recorded votes in the General Assembly"), each session's list is
  `/votes/session-80.html` (`session-es-11` for an emergency special session, `session-s-8` for a special session),
  and each vote is `/votes/<symbol>.html`, the resolution's symbol in lower case with every run of other characters
  made one hyphen: `A/RES/ES-11/1` is `/votes/a-res-es-11-1.html`. The symbol is what people cite, and no two votes
  in the data share one. Should two ever do, the earliest keeps the address and the other adds its UN Digital
  Library number. As for every page (decisions 22 and 23), the canonical link gives the one address, and the other
  forms are sent there for good (301): any case, no `.html`, a trailing slash, the symbol with its slashes
  (`/votes/A/RES/ES-11/1`) and the UN Digital Library number (`/votes/4126622`). An address that is no vote's is a
  404.
- Titles: every page's title and heading are its own (the lesson of Where Councils Stand's decision 28). A vote's
  is its symbol and its title as the record gives it ("A/RES/ES-11/1: Aggression against Ukraine | Where Countries
  Stand"), without the catalogue's "resolution / adopted by the General Assembly", with the title cut at a word
  after 70 characters in the page's title only. If two votes ever share a symbol, each adds its date, and then its
  UN Digital Library number. Titles that the record gives in capitals are left as they are: changing their case
  would get names wrong. A test checks that no two pages of the site share a title.
- Content: written on the server from the votes file, with no AI and no script. Each vote's page gives the date in
  full, the session (from the symbol for special and emergency special sessions, which the data leaves without
  one), the meeting record, the draft and the agenda item, the result in the UN's words ("adopted by 141 votes to
  5, with 35 abstentions"), the totals as a table, and every Member State listed under "In favour", "Against",
  "Abstaining" or "Not voting", in the UN's short names as everywhere on the site (decision 6), alphabetically.
  It links to the resolution, the meeting record and the draft through undocs.org, and to the vote's record in the
  UN Digital Library. Votes are counts in words, never coloured. A vote the record gives no table for gets no page.
- Index: the index lists the 20 latest votes, then every regular session (newest first), every emergency special
  session and every special session, each with its number of votes and dates. Each session's page lists its votes,
  newest first. The home page links to the index from "The record", and the methodology page from "The record".
  The menu is unchanged: it stays the family's Ask, About, Methodology and Glossary (decision 23).
- Structured data: only `BreadcrumbList` (Where Countries Stand, Recorded votes in the General Assembly, the vote or
  session), matching the link above each heading.
- Sitemap: `/sitemap.xml` is now a sitemap index of `/sitemap-pages.xml` (the pages, as before) and
  `/sitemap-votes.xml` (the index, every session and every vote). A vote's page is dated by the vote's own date,
  a session's and the index's by their newest vote: dates from the record, never made up. A hidden vote is left
  out. robots.txt is unchanged.
- Right of reply: a vote's page names countries, so it carries the family's "If you're named" panel beside the
  vote, linking "If you're named on this site", the report form and "Add a statement". A vote's page is an item the
  right of reply covers as a saved briefing is (decision 16): a statement can sit beside it (shown after the table,
  in the same box, with `data-nosnippet` so a search engine never quotes it as the site's words), and the
  maintainer can hide it while a concern is decided (`wcs hide /votes/<symbol>.html`), which shows only its symbol
  and the note, marked noindex and out of the sitemap. A session's list and the index are not items, as they
  name no country's vote; they link "If you're named on this site" all the same. The "If you're named" page,
  the corrections form and the messages a sender is shown say "briefing or vote page".
- Speed: the list of votes is read in one query (under a fifth of a second over the whole file) and kept, and each
  vote's page is put together the first time it is asked for (a few milliseconds) and kept, the latest 1,000.
  Both are read again only when the votes file changes (its time and size), which happens only when the update reads
  in a new file from the UN. The update does no more work than before.
- Accessibility: CI starts the site with two made-up votes (`scripts/sample_votes.py`), as it has no data, and
  `scripts/a11y.mjs` checks the index, the first session and the first vote it links to, and that the vote's page
  has its table, one heading of the first rank, the "If you're named" link and only breadcrumbs as structured data.

## 27. Topic briefings

On 21 November 2026 the owner agreed (answer 10 of the charity's review plan, C3) that the site has topic briefings,
as Where MPs Stand has: standing questions on subjects the UN keeps returning to, each with a briefing written afresh
every week at a fixed address, so that a first-time visitor or a search engine finds real, checked briefings to read
without asking anything. The plan left the choice of topics to the session carrying it out.

- The topics: ten, in `config/topics.toml`, chosen across what the record holds (the Security Council's debates, the
  General Debate and the General Assembly's recorded votes) and from every side. Under "Countries and conflicts": the
  war in Ukraine; Israel, Gaza and the question of Palestine; Sudan; Afghanistan; Haiti. Under "Themes": reform of
  the Security Council and the veto; climate change and climate finance; nuclear weapons and non-proliferation;
  women, peace and security; unilateral sanctions. Each has been on the Security Council's or the General Assembly's
  agenda throughout the years since 2022; together they cover conflicts in Europe, the Middle East, Africa, Asia and
  the Americas, and themes that divide groups of countries rather than parties to one conflict (Security Council
  reform, climate finance, nuclear weapons, unilateral sanctions).
  "The question of Palestine" is the UN's own name for its agenda item; unilateral sanctions are named under both
  names in use, "also called unilateral coercive measures", so that neither side's word is the site's alone.
- The questions: each asks "What have countries and groups said about … at the United Nations since 2022?", and
  adds "and how have they voted?" only where the General Assembly's recorded votes bear on it (Ukraine, Palestine,
  nuclear weapons, unilateral sanctions). Each names no side and presumes no answer (decision 7), and the record can
  answer it. 2022 gives each topic close to four years, long enough to cover the war in Ukraine from its start and
  short enough for 24 sources to say something of each year. Each topic's dates (`date_from`, and optionally
  `date_to` and the bodies, `sources`) are laid over the model's reading of its question, so every week's briefing
  covers the same ground whatever the reading.
- The briefing: written by `wcs topics refresh` through the site's own pipeline, with every check a reader's
  briefing has, in summary length (decision 14), and saved as any other briefing (it also has an address of its own,
  `/b/<id>`, which stays out of search engines). A run that fails, finds no records or is stopped at a cap leaves
  the last good version in place. A version that is withdrawn, or hidden while a concern is decided, gives way to the
  one before it. Earlier versions are linked from the page.
- The pages: `/topics.html`, the index by area, and `/topics/<slug>.html`, each written on the server whole (the
  question, the day it was prepared and the latest record it could use, the filters, the check strip, the text
  with record quotations, chips and every mark, the check details and the sources), in the family's design
  (decision 25), so a reader without scripts and a search engine see all of it. The page's script then shows the same
  briefing again with the record beside it, or a sheet on a phone. The server's markup and the script's are the
  same, and a test checks it. A topic's page is titled with the topic's name ("The war in Ukraine: briefing from UN
  records | Where Countries Stand") and carries only `BreadcrumbList` as structured data, as the vote pages do.
- Indexing: the topics' pages are open to search engines, and in the sitemap (`/sitemap-topics.xml`, dated by when
  each briefing was prepared), while every reader's briefing stays out (decision 7, and the owner's answer 1 of the
  review plan: only the record's own pages and curated topics are indexed). Their words are the site's own: the
  topic's title and its neutral question, which no reader wrote. A topic with no briefing standing has a page that
  says so, kept out of search engines and the sitemap.
- Share cards: a topic's link preview is its own card, `/topics/<slug>/card.png`, with the topic's own question as
  its title and the strip's counts (the review plan's B5, and the charity's `family/BRIEFINGS.md`). The same
  briefing at `/b/<id>` keeps the site's neutral card.
- Where it is linked: "Topics" joins the menu after Ask (Ask, Topics, About, Methodology, Glossary), as Where MPs
  Stand's menu has Topics; the glossary sets no limit on menus, only on footers. The home page lists the topics
  with a briefing, written by the server. A topic's page does not offer the same question on Where MPs Stand
  (decision 24): its question is about the United Nations.
- The cost: a briefing cost $0.1667 on average in the first full evaluation run (19 November 2026,
  [EVALUATION.md](EVALUATION.md)), and readers' briefings cost $0.11 to $0.21. Ten topics a week cost about $1.70
  (£1.34 at £1 = $1.27), and at most $2.10 (£1.65): about $7 to $9 a month, within the review plan's £1 to £2 a week
  and under a tenth of the monthly cap. Each rewrite is counted against the site's caps as kind `topic`; the run
  stops before a topic that would reach a cap, counting it at the estimate a briefing is reserved at ($0.25), and at
  its own limit of $3 (`[topics] max_cost_usd`). A topic written in the last six days is not written again unless
  asked (`--force`), so a run after the server was down, or one by hand the day before, does not pay twice.
- When: every Sunday at 11:23 UTC (`deploy/wcs-topics.timer`, installed by `deploy/update.sh`), between the morning
  and evening updates and clear of the other sites' jobs on the server. It takes no update lock: it reads the index as
  the site does and writes only to the state database.

Each topic's versions are kept in the state database's key-value table (`topic:<slug>`), so they are backed up with
the briefings. Where MPs Stand rewrites a topic only when its sources have changed; here each is written afresh
every week, as the review plan asks, which the cost allows.

## 28. Measurement for Google Ad Grants, built and switched off

The charity will apply for Google Ad Grants once it is registered, which needs the Google tag on the sites to count
conversions (the owner's answers 3 and 8 of the charity's review plan, Part D; its `family/MEASUREMENT.md` and
[Ad Grants checklist](https://github.com/rj-mitchell/wheretheystand-charity/blob/main/plans/2026-11-21-ad-grants-checklist.md)).
It is built now, with the family kit 2.2.0, and off, so the site is what it was until the owner turns it on
(21 November 2026; built here 22 November 2026, following Where Landlords Stand's decision 20):

- The switch is `[ads] measurement` and `tag` in `config/settings.toml`, false and empty; on the server
  `WCS_ADS_MEASUREMENT` and `WCS_ADS_TAG` in `deploy/.env`. A tag that isn't a Google tag's ID (`AW-`, `G-` or
  `GT-` and letters and digits) leaves it off. Off, the pages carry nothing of it but the kit's inert
  `data-wts-conversion` marks, and `/family/measure.js` is not served (the sync copies it into `web/site/family/`
  with the rest of the kit). The kit's `measure-csp.json` is never served: it is the list of hosts the front door's
  policy adds.
- On, every page (the site's own pages, the vote pages, the topic pages, a saved briefing's and the 404 page) loads
  the kit's `measure.js` last in its head, with the content's hash in its address like the site's other scripts. It
  shows the family's consent banner at the top of the page's main content and loads the Google tag only if the
  reader accepts. The footer gains "Measurement choices" on a line of its own after the notices: it is a control,
  not a page, so the footer keeps the family's six links. Nothing is added to `styles.css`, so that, off, no page's
  stylesheet address changes.
- The privacy page while measuring is `web/partials/privacy-measurement.html`, served at `/privacy.html` and never
  at an address of its own: a draft beside the current notice, in the glossary's "Who runs it, once registered"
  words. The site refuses to start with measurement on while it still says `NNNNNNN` for the charity number or
  "(draft" in its date.
- What is measured, and nothing else (no page views, never a question's words, nothing typed in a form):
  - `briefing_read`: the end of a finished briefing comes into view. The end is "How this briefing was checked"
    (`#check-details`), which a briefing has only once it is written and checked, never for "No records found" or
    sources alone. It carries the briefing's id (`data-wts-briefing`), so a topic's briefing, which the server writes
    and the page's script draws again, is counted once.
  - `source_opened`: a source chip in a briefing, or a link to the record: "Read S/PV.…" in the record beside the
    briefing, its sheet on a phone and the list of sources, the home page's proof, and a vote page's links to the
    resolution, the meeting, the drafts, the Digital Library record and the voting data.
  - `donate_click`: the Donate link in the kit's footer shelf.
  - `report_sent`: the corrections page's form (a report, or a statement under the right of reply) calls
    `window.wtsMeasure?.conversion("report_sent")` once the site has taken it. The form works only with its script,
    so there is no thank-you page to mark.
  - Not `download`: the site offers no downloads of its own (no Word, PDF or CSV; the UN's documents it links are
    the record, counted as a source opened, and the press page's logo files are not counted). So the banner never
    says it counts one (`data-measures="briefing_read source_opened donate_click report_sent"`).
- The site sets no content security policy of its own: the shared front door (Where MPs Stand's Caddy) sets every
  header, and would block the Google tag. So before the switch is turned on, this site's domain goes into the front
  door's `MEASURED_SITES` (docs/DEPLOY.md, "Measurement").
- A question never reaches Google: the address of the page a question was just asked on holds the question
  (`/?q=…`, until a written briefing takes its own address, `/b/<id>`; a search for sources alone keeps it), so the
  kit's `measure.js` (2.2.0) sends Google each page's address and the referrer cut to their path, without the query
  or fragment (the charity's `family/MEASUREMENT.md`, "What Google is sent"). The draft notice says so.
