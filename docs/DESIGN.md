# Design: the look of the UN's Official Records

Where They Stand takes its look from Hansard's printed Official Report. Where Countries Stand takes
its look from the UN's printed **Official Records**: the verbatim records of General Assembly and
Security Council meetings (document symbols `A/…/PV.…` and `S/PV.…`). It uses the UN's web palette
and typeface for the controls.

A working prototype is in [`web/prototype/`](../web/prototype/). Open `index.html` or
`briefing.html` in a browser.

## The rule: like the record, never like the UN

The UN's name, its abbreviation and its emblem are reserved for the UN's official purposes under
General Assembly resolution 92(I) (1946), and protected internationally under Article 6ter of the
Paris Convention. Using them needs the Secretary-General's authorisation. So:

- **No UN emblem**, and nothing that resembles it. That means no world map seen from above the
  North Pole, and no olive branches.
- **No "United Nations" or "UN" wordmark or logo**, and neither in the site's name or domain. The
  words appear only in ordinary sentences ("…at the United Nations").
- **No UN-style document symbols for our own pages.** UN symbols (`S/PV.7565`) appear only as
  citations of real UN documents. Our briefings carry no reference numbers that could pass for
  UN ones.
- **Independence is stated on every page**: in the masthead ("Independent. Not affiliated with the
  United Nations.") and in the notice box at the foot.
- **A screenshot must never pass for a UN document.** The record's typography and conventions are
  borrowed, but the masthead is our own name and mark.

This matches Where They Stand, which takes Hansard's look but doesn't use Parliament's logo or the
crowned portcullis.

## What is borrowed from the record

Based on S/PV.7565 (20 November 2015):

| In the record | In the site |
|---|---|
| Set in **Times New Roman**, two justified columns | Body in **Tinos**, which is metrically identical to Times New Roman (Apache 2.0). Everything is a **single column**, ragged right, at a comfortable reading width (about 46rem). The record's two columns are not copied. |
| Masthead: body name large and bold, session line in italic beneath, status ("Provisional") in italic on the right | Site name large and bold, a description in italic beneath, "Independent. Not affiliated with the United Nations." in italic on the right |
| Running header between rules: symbol · agenda item in italic · date | "Briefing" · topic in italic · date |
| Speakers as **Mr. Delattre** (France) *(spoke in French)*: | Exactly the same in every source. The italic language note doubles as the translation flag. |
| Procedural lines in italics: *A vote was taken by show of hands.* | The same, for procedural notes and vote labels (*In favour:*) |
| List of members with dotted leaders: *President:* Mr. Rycroft …… (United Kingdom) | "The Council on this date" panel, and lists of coverage |
| Agenda items as a numbered list | Example questions numbered the same way |
| Boxed notice at the foot of the first page | Boxed notice at the foot of every page: independence, and that translated speeches are the UN's translations |

## Colour and type

| Token | Use | Value |
|---|---|---|
| UN Blue | Links, focus rings, primary buttons, the **General Assembly** | `#009EDB` (Pantone 2925), from the UN's 2020 brand quick guide |
| Council navy | The **Security Council** | Deep navy, `oklch(0.31 0.06 262)` |
| Ink and paper | Text and background | Near-black on white. The records are printed on white, unlike Hansard's ivory. |

- **The body determines the colour,** the way Where They Stand uses green for the Commons and red
  for the Lords. Sources and filter chips are edged in UN Blue for the General Assembly and Council
  navy for the Security Council. Further bodies (the Human Rights Council, for example) get their
  own colour from the same family.
- **Votes use neutral marks:** filled for yes, outlined for no, hatched for abstain. Never green
  for yes and red for no, which would imply that one way is right.
- **Roboto**, the UN's primary web typeface (Apache 2.0), is used for controls only: navigation,
  buttons, chips, metadata lines and counts. The record is always in Tinos.
- Both fonts are bundled with the site, not loaded from Google, as Where They Stand bundles
  Source Serif 4. The prototype loads them from Google Fonts for convenience.

## The mark

Fifteen seats around a horseshoe table, open towards the viewer (`web/prototype/icon.svg`). It
suggests where countries sit and stand, and the fifteen seats are the Security Council's. It looks
nothing like the UN emblem.

## Shared with Where They Stand

- Square corners, ruled lists, and colour only where it carries meaning.
- OKLCH colour tokens holding both light and dark values, through `light-dark()`. Dark mode follows
  the system setting.
- One fluid type scale, logical properties, visible focus rings, hover styles only for a mouse,
  and animation only when reduced motion isn't requested.
- No horizontal scrolling on phones. The prototype is checked at 390 px wide.
