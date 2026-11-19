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
