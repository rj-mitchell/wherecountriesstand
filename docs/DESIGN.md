# Design

How the site looks and behaves, and the rules it follows on the UN's name and emblem. Where Countries Stand uses the
Where They Stand family design (family kit 2.0): white pages, light grey panels, a band in the site's cloth colour
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
| `--warning` | site's own | The underlines that mark loaded words and quotations not found in the source |

Colour is used only where it carries meaning. Votes are given as counts in words, never coloured green or red, since
that would imply one way of voting is right. When more contrast is requested (`prefers-contrast: more`) muted text and
faint rules take the full ink colour, and in forced-colours mode panels, notes and source cards get a visible
border.

## Type

Source Serif 4 sets the headings (`h1` to `h3`) and the site's name in the header band. Source Sans 3 sets
everything else, including the briefing text. Quotations from the record (the text of each source card and the full
statement under it) are set in the serif, so they read as the record rather than the site's own words. Both fonts
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
- On the home page, the question box and a panel on how briefings are checked sit side by side, then the example
  questions beside a short list of what the record covers. Below 56rem the grid becomes a single column.
- A briefing puts the question and the briefing in a wide column, with the filters used in a panel beside it. The
  sources follow as numbered cards on grey panels, two to a row on wide screens and one on narrow ones.
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
- Printing keeps the site's name, the briefing and its sources with their addresses, and drops the navigation,
  forms and footer links.
