# Design

How the site looks and behaves, and the rules it follows on the UN's name and emblem. Where Countries Stand uses the
Where They Stand family design (family kit 2.2): white pages, light grey panels, a band in the site's cloth colour
across the top of every page, Source Serif 4 for headings and Source Sans 3 for everything else. The family's rules
are in the charity's glossary (`family/GLOSSARY.md`, "Design", in the charity's private repository,
wheretheystand-charity); this document records what is particular to this site.

The pages are in [`web/site/`](../web/site/) and the stylesheet is
[`web/site/styles.css`](../web/site/styles.css). The header, the alpha notice and the footer are written once in
[`web/partials/`](../web/partials/) and included in each page by the server. The family kit is copied into
`web/site/family/` by the kit's `sync.mjs`; don't edit those copies (see [`DEPLOY.md`](DEPLOY.md), "Pages and the
family kit").

## The United Nations' name and emblem

The UN's name, its abbreviation and its emblem are reserved for the UN's official purposes under General Assembly
resolution 92(I) (1946) and protected internationally under Article 6ter of the Paris Convention. Using them needs
the Secretary-General's authorisation. The site therefore follows these rules:

- There is no UN emblem and nothing that resembles it: no world map seen from above the North Pole and no olive
  branches.
- There is no "United Nations" or "UN" wordmark or logo, and neither appears in the site's name or domain. The words
  appear only in ordinary sentences ("at the United Nations").
- UN document symbols (`S/PV.7565`) appear only as citations of real UN documents. Briefings carry no reference
  numbers that could pass for UN ones.
- Every page says, in the footer, that the site is independent and not affiliated with or endorsed by the United
  Nations, and that it does not use the UN's name or emblem.
- A screenshot of the site must never pass for a UN document.

Where MPs Stand follows the same approach with Parliament: it does not use Parliament's logo or the crowned
portcullis.

## Colour

All colours come from the family kit's tokens (`web/site/family/family.css`), mapped to the site's own names at the
top of `styles.css`. Each token holds a light and a dark value through `light-dark()`, and dark mode follows the
system setting.

| Token in `styles.css` | Kit token | Use |
|---|---|---|
| `--page` | `--wts-paper-light` | The page: white in light mode |
| `--panel` | `--wts-paper` | Panels, source cards, notes and the footer: light grey |
| `--ink`, `--ink-muted` | `--wts-ink`, `--wts-ink-muted` | Text, and secondary text such as dates and notes |
| `--rule`, `--rule-faint` | `--wts-rule`, `--wts-rule-faint` | Hairlines between rows of lists and tables |
| `--cloth` | `--wts-cloth-countries` | The header band, buttons, and the radio and checkbox accent (`#1F5F8B`, `#22699A` in dark mode) |
| `--link` | `--wts-cloth-countries-text` | Links (the cloth colour in light mode, a lighter blue in dark mode) |
| `--warning` | site's own | The border of an error note |
| `--wts-warn`, `--wts-warn-bg`, `--wts-mark` | the kit's `briefing.css` | A briefing's marks: points to check (warning) and the record's highlight in the site's cloth |

Colour is used only where it carries meaning. Votes are given as counts in words, never coloured green or red, since
that would imply one way of voting is right. When more contrast is requested (`prefers-contrast: more`) muted text and
faint rules take the full ink colour, and in forced-colours mode panels, notes and source cards get a visible
border.

## Type

Source Serif 4 sets the headings (`h1` to `h3`) and the site's name in the header band. Source Sans 3 sets
everything else, including the briefing text. Quotations from the record (the text of each source card and the full
statement under it, a briefing's record quotations, and the passages in the record beside it) are set in the serif, so
they read as the record rather than the site's own words. Both fonts
are bundled with the family kit in `web/site/family/fonts/`; nothing is loaded from Google.

Body text is 1.125rem with a line height of 1.55. Headings use `clamp()` sizes so they scale with the window.
Headings and labels are in sentence case at normal letter spacing.

Quotation marks and apostrophes are curly throughout. The model writes straight ones, which are curled once a
briefing has been checked (`wcs.rag.checks`, with the shared package's `curl_quotes`); those inside a web address,
`code` or an HTML tag are left as they are.

## Layout

Every block lines up to the same 72rem column as the header and footer, with the kit's gutter. Running text is held
to a reading width of about 46rem (`--column`); panels, grids and tables use the full column.

- The header is a band in the site's cloth. It holds the horseshoe mark on a white tile, the site's name, the
  family endorsement ("A Where They Stand project", with the site's spine) and the navigation (Ask, About,
  Methodology, Glossary).
- Under the band, the alpha notice is one line with an "Alpha" tag and a link to the report form.
- On the home page, the question box comes first, with its two equal choices ("Prepare briefing" and "Sources only
  (no AI)"), then the family's line and the "How the briefings are checked" disclosure under it, then the proof of
  the checking ([below](#the-home-pages-proof)), then the example questions beside a short list of what the record
  covers. Below 56rem the grids become a single column.
- A briefing puts the reader's question in a wide column with the filters used in a panel beside it, then the check
  strip across both, then the briefing with the record beside it on wide screens
  ([below](#checked-briefings)). The sources follow as numbered cards on grey panels, two to a row on wide screens
  and one on narrow ones.
- Text pages (About, Methodology, Glossary, Privacy and the others) use the reading width; the glossary sets each
  term beside its meaning across the full column.
- The footer is the family shelf, then the site's own links (About, Methodology, Corrections, Accessibility, Privacy,
  Source code, in the family's order) and one paragraph on independence, translations and licences.

Panels have square corners and no shadow. Buttons and inputs have a 3px radius. There are no coloured stripes down
the side of boxes, double rules, small tracked capitals or gold text; gold appears only in the bands on the book
spines.

## The mark

The mark is fifteen seats around a horseshoe table, open towards the viewer (`web/site/icon.svg`). It suggests where
countries sit and stand, and the fifteen seats are the Security Council's. It looks nothing like the UN emblem. It
sits on a white tile in the header band, and the same tile is used for the favicon and the link card so that it
stays clear on a dark browser tab. At 16 and 32 pixels, and in the SVG favicon, it is drawn with eleven larger
seats.

## Statements under the right of reply

A statement in someone's own words ([decision 16](DECISIONS.md#16-corrections-and-the-right-of-reply)) is the one
text on the site that is neither the record nor the site's own. It follows the briefing's notes in a box with a
dashed border, headed "Statement from [name], [date]" with the author's role beneath, and ends "In their own words.
Not checked or endorsed by Where Countries Stand." The "If you're named" page (`/named.html`) shows an example.

## Pages for each recorded vote

The index of recorded votes (`/votes.html`), each session's list and each vote's page
([decision 26](DECISIONS.md#26-a-page-for-each-recorded-vote-in-the-general-assembly)) are written on the server
into `web/partials/record-page.html`, which carries the same head, header and footer as every other page, by
[`src/wcs/api/votes.py`](../src/wcs/api/votes.py). They need no script.

- Above the heading, a link back to the index ("Recorded votes in the General Assembly"), which is also the page's
  breadcrumb trail. The heading is the vote's symbol and title; the line under it gives the date, the session and the
  result in words.
- On screens 64rem wide and over, the facts (a label beside each value, on one rule each) and the lists sit in the
  main column with the "If you're named" panel (light grey) beside them; on narrower screens the panel follows the
  facts. Running text keeps to the reading width.
- The totals are a small table. Every Member State is listed under how it voted, in columns about 10rem wide (two
  on a phone, four on a computer), each name on its own hairline. Nothing is coloured by how a country voted.
- Statements beside a vote use the same dashed box as beside a briefing, after the table.
- Lists of votes give the vote's heading as the link and its date and result under it in muted text; the sessions
  are listed in columns, each with its number of votes and its dates.

Screenshots of a vote's page, a session's list and the index at 390 and 1,280 pixels wide were looked at on
21 November 2026.

## Topic briefings

The topics' index (`/topics.html`) and each topic's page (`/topics/<slug>.html`,
[decision 27](DECISIONS.md#27-topic-briefings)) are written on the server into the same frame as the vote pages
(`web/partials/record-page.html`), by [`src/wcs/api/topic_pages.py`](../src/wcs/api/topic_pages.py).

- A topic's page is a briefing laid out as on the ask page: the link back to "Topic briefings", the topic's name as
  the page's heading, the question asked every week, and "Briefing of [day], written by AI from the sources below,
  from the UN's records up to [day]", with the filters used beside them; then the check strip across the column, the
  briefing with its chips and marks, "How this briefing was checked", the address of this version with the link to
  report a problem, any statements beside it, and the sources. Other topics with a briefing follow.
- The briefing's markup is the family's ([Checked briefings](#checked-briefings)), written by
  [`src/wcs/api/briefing_html.py`](../src/wcs/api/briefing_html.py), the page script's renderer written again in
  Python, so the page reads whole with no script. `web/tests/briefing.test.js` checks that the script makes exactly
  the server's HTML for two briefings (`web/tests/fixtures/server-briefings.json`, written by
  `tests/test_topics.py`). Where scripts run, `app.js` opens the same briefing from `/api/briefings/<id>` and draws it
  again, adding the record beside it on wide screens and the sheet a chip opens on a phone. If that fetch fails (a
  search engine, which robots.txt keeps out of `/api/briefings/`), the server's page stays.
- The index lists the topics under their areas, each with its one-line description, the day of its briefing (or
  "Not written yet", with no link) and its question. The home page lists the topics with a briefing, with their
  days, above the example questions, and links to the index. "Topics" is in the menu after Ask; on a phone the menu
  takes two lines.
- A topic's share card (`/topics/<slug>/card.png`) is drawn as a briefing's is ([Share cards](#share-cards)), with
  the topic's question as its title.

Screenshots of the index, a topic's page (with its script and without) and the home page at 390 and 1,280 pixels
wide were looked at on 21 November 2026, with a made-up briefing that has each kind of mark.

## Links to sister sites

After a briefing, with its address and the link to report a problem, links to sister sites each show that site's
spine, copied from the family shelf in the footer: "Ask the same question on Where MPs Stand"
(`https://wherempsstand.com/?q=…`) and, for each country the question is about, "Quality of life in … on Where Lives
Stand" (`https://wherelivesstand.com/country/<ISO alpha-3>`). They are offered only once a briefing has been written:
not for "No records found", a briefing that failed, or sources shown without a briefing (decision 24).

## Accessibility and behaviour

- Targets are at least 24px, and 44px on a touch screen (`pointer: coarse`).
- Focus rings are always visible; hover styles apply only with a fine pointer.
- Nothing scrolls sideways on a phone. Pages are checked at 390px wide by `scripts/a11y.mjs`, light and dark.
- The question box grows with the question, from one line to four (`field-sizing: content`); Enter asks and
  Shift+Enter starts a new line. Where a browser can't size a box to its text, it is two lines tall (three on the
  narrowest phones) and can be pulled taller.
- Printing keeps the site's name, the check strip (in outline), the briefing with each chip printed as its number
  ("[3]"), and its sources with their addresses as the printed reference list, and drops the navigation, forms,
  the wait, the record panel, the sheet and the footer links. The site has no Word export.

## Checked briefings

A briefing is shown as the family shows one (`family/BRIEFINGS.md` in the charity's repository, and the kit's
`web/site/family/briefing.css`, linked by the home page, which is also the page of every briefing), in the site's
cloth (`wts-cloth--countries` on the result). The parts are built by
[`web/site/briefing.js`](../web/site/briefing.js) from the answer as the server shows it, and laid out by
`styles.css`; [decision 25](DECISIONS.md#25-checked-briefings) records the choices. Screenshots taken on 21 November
2026, at phone and computer widths, light and dark, are in
[`screenshots/2026-11-checked-briefings/`](screenshots/2026-11-checked-briefings/).

- The check strip, at the top of the briefing, under the "Briefing of …, written by AI" line: "Checked against
  the UN record", with three counts in words: citations matched to the record, quotations found word for word, and
  sentences without a source. A count with a point to check says "To check" and links to the first one. A note in
  the strip says how many judgemental words in the AI's own voice are underlined, when there are any. It links to
  "How this briefing was checked" at the end, which gives the verifier's own counts and what each mark means. A
  briefing with no records found has no strip.
- Record quotations: a quotation found word for word is set in the serif on the record's highlight, with "✓ word
  for word"; one not found has a wavy underline and "Not found word for word". A quotation in a lead-in to its
  citation is the cited source's, as the verifier checked it.
- Source chips: after each cited passage, the source's number and the country or group (a statement on behalf
  of a group is the group's), the body and the date: "Italy, Security Council, 14 November 2025"; a vote's chip says
  "Recorded vote, General Assembly" and the date. Two runs from the same source side by side share one chip.
- Text with no source is always shown, with a dotted underline in the warning colour, "No source" on the first,
  and "(no source)", or "(its citation did not match the source and was removed)", for screen readers. It is never
  grey (decision 25 replaces decision 20's grey).
- The record beside the briefing, on screens 64rem wide and over: a sticky panel in the right-hand column, open
  from the start on the first source. A chip pressed, focused or hovered shows its passage, a UN document's slip
  (the symbol and date, who spoke and for which country or group, the meeting and agenda item, the passage with
  every quoted run highlighted and the chosen one outlined, and a link to the document). As the reader scrolls, the
  chip nearest the upper third of the window leads. A line in the gutter joins the chip's line of text to its
  passage. "Previous source" and "Next source" step through the sources cited.
- The bottom sheet, on narrower screens: a chip opens the same slip in a dialog from the bottom of the screen,
  with focus on its heading, kept inside it, and back on the chip when it closes (Close, Escape or a tap outside).
- Every mark has its words for screen readers, and nothing relies on colour alone.

## The wait

While a briefing is written, the page shows its real events in order (the stream's `plan`, `sources`, `text` and
`citation`, `progress` when the checks begin, then `final`), in four stages listed across the column, one "Now",
with a pulsing dot beside it so that something always moves while the site is working:

1. Reading the question: the family's five books on a shelf, each taken down and put back in turn, until the
   plan arrives, then the question as read as large chips ("Security Council", "2022 to today", "all countries")
   with the plan's own reading.
2. Searching the record: each record found arrives as a card of its UN document (the symbol, the date, the country
   or group and the speaker, the first words), into a pile down the right-hand side on wide screens or along a
   shelf on narrower ones, with a running count and then the summary ("24 statements from 19 countries and groups
   at 12 meetings").
3. Writing from these records only: "Writing from these 24 records only. The AI may use these and nothing else."
   The text appears as it is written, laid out as it will be; as each citation arrives its card is marked "Cited"
   and brought into view in the pile (the page itself does not move).
4. Checking every citation and quotation: once the model's text is complete, the server sends a `progress` event
   with `step: "checking"` before it runs the checks (`wcs.rag.pipeline`). From then until the final event, the
   checking line stands under the text, "Checking every citation and quotation against the UN record. The briefing
   is finished when this is done.", with a bar that moves while the checks run; the stage list shows "Checking" as
   Now, and "Writing from these records only" goes from above the text. On the final event the line goes, the
   finished briefing is shown at once, and the strip's counts run up passage by passage through the real results,
   each quotation taking its highlight in turn and its card's words highlighted, with sentences without a source
   "counted last". Then the stages go and the pile gives way to the record beside the briefing, open on the first
   source.

"Sources only (no AI)" shows the first two stages, then the list of sources. One `role="status"` region announces
each stage once, and the counts once, in words, at the end; the moving numbers are hidden from screen readers. With
reduced motion the stages are the same, without movement. No stage waits for an animation: each starts when its
event arrives, and the cards still being dealt are laid down at once when the next event comes. A saved briefing
(`/b/<id>`) opens finished, with no wait.

## The home page's proof

Under the question box, the line and the disclosure, a static block in the page's HTML (the kit's `wts-proof`), seen
without a script: "Every briefing, checked against the UN record", three steps ("Searches only the UN's records",
"AI writes only from what was found", "Every citation and quotation is checked before you see it"), then a real
sentence from saved briefing kGfKBI3cRLw (18 November 2026) with its record quotation and chip, beside the passage
from S/PV.10043 (Resumption 1) it came from, the same words highlighted, and a link to the methodology page. The
first step says "the UN's records" rather than the family's "the official record", as the site's other wording does
(decision 17). Since kit 2.3.0 (22 November 2026) the sentence and the passage are two cards of the same height,
labelled "In a briefing written by AI" and "In the record it cites", side by side from 48rem and one above the other
on phones, joined by a rule across the gutter at their middle; the sentence's card ends with the day the briefing
was prepared. The earlier layout pushed the sentence down to the level of the passage's highlight, which left a tall
empty space above it at computer widths; it needed a script to measure it, and has gone with it.

## Share cards

A saved briefing's link preview (`og:image` on `/b/<id>`) is its own card, `/b/<id>/card.png`, drawn once by
wts-core's `briefing_card` ([`src/wcs/cards.py`](../src/wcs/cards.py)): the band in the site's cloth with the mark
on its white tile, "Briefing written by AI, checked against the UN record", the site's neutral title for the
briefing ("Briefing from UN records, 18 November 2026", never the reader's question: decision 7), the strip's three
counts in words, the day it was prepared and the site's address. It is set in Source Serif 4 Semibold and Source
Sans 3, from `data_static/fonts/` (SIL Open Font License). A topic's page has a card of its own, with the topic's
question as its title ([Topic briefings](#topic-briefings)). Every other page keeps the family's card.

## Measurement for Google Ad Grants

Built on 22 November 2026 and off ([decision 28](DECISIONS.md#28-measurement-for-google-ad-grants-built-and-switched-off)):
while `[ads] measurement` is false, as it ships, no page loads anything for it and `/family/measure.js` is not
served. The kit's `measure.js` and `measure-csp.json`, in `web/site/family/`, are the family's consent banner and
Google tag loader, and the hosts the front door's policy adds for it
([`src/wcs/api/measurement.py`](../src/wcs/api/measurement.py)). The banner's styles are the kit's, in `family.css`
(`.wts-measure`).

While it is on, every page loads `measure.js` last in its head, and the banner, drawn by the script, sits at the top
of the page's main content until the reader chooses: one sentence saying what is measured, by whom and why, then
"Accept" and "Reject", the same button in the same style, and "Read the privacy notice". It is not a dialog, and it
moves nothing the page's script draws (the briefing, the wait and the record beside it are all drawn inside the main
content, below it). The footer's line "Measurement choices", after the notices, opens it again. The privacy page is
the measurement version, `web/partials/privacy-measurement.html`.

The places a conversion happens carry the kit's marks whether the switch is on or off: the check details at the end
of a finished briefing (`briefing_read`, with the briefing's id), the source chips and every link to the record
(`source_opened`), the shelf's Donate link (`donate_click`), and the corrections page's script once a report or a
statement is sent (`report_sent`). The site offers no downloads, so it has no `download`.

