// The checked briefing's parts (web/site/briefing.js), with no browser: node --test web/tests/
// The example is saved briefing kGfKBI3cRLw (the veto, 18 November 2026) as the server shows it, through the display
// rule for quotations: fixtures/kGfKBI3cRLw-shown.json, which tests/test_checked_briefings.py checks is exactly what
// the server sends, with wts-core's check_counts for it. Its sources are the fixed copy in tests/fixtures/.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const B = require("../site/briefing.js");

const shown = require("./fixtures/kGfKBI3cRLw-shown.json");
const saved = require(path.join("..", "..", "tests", "fixtures", "countries-kGfKBI3cRLw-2026-11-21.json"));
const sources = saved.events.find((e) => e.type === "sources").sources;
const byN = Object.fromEntries(sources.map((s) => [s.n, s]));
const savedAnswer = saved.events.find((e) => e.type === "final").answer;
const ITALY = "“supports all initiatives that seek to impose self-limitations on the use of veto”";

const html = (nodes) => [].concat(nodes).map(B.toHTML).join("");
const text = (nodes) => [].concat(nodes).map(B.textOf).join("");

// The spans of a class in some HTML, each with its text (nested spans are balanced by counting).
function spans(markup, cls) {
  const out = [];
  const open = new RegExp(`<span class="${cls}(?: [^"]*)?"[^>]*>`, "g");
  for (const m of markup.matchAll(open)) {
    let depth = 1, i = m.index + m[0].length;
    const tags = /<(\/?)span\b[^>]*>/g;
    tags.lastIndex = i;
    let t;
    while (depth && (t = tags.exec(markup))) depth += t[1] ? -1 : 1;
    out.push(markup.slice(m.index, t.index + t[0].length));
  }
  return out;
}
const plain = (markup) => markup.replace(/<[^>]+>/g, "").replace(/&quot;/g, '"').replace(/&amp;/g, "&");

test("a quotation found word for word is never marked as having no source", () => {
  const markup = html(B.briefingBlocks(shown.answer, byN));
  const nosource = spans(markup, "wts-nosource");
  assert.equal(nosource.length, shown.counts.no_source);
  for (const n of nosource) assert.ok(!n.includes("wts-quote--checked"), n);
  const checked = spans(markup, "wts-quote wts-quote--checked").map(plain);
  assert.equal(checked.length, 15);
  assert.ok(checked.some((q) => q.startsWith(ITALY) && q.includes("✓ word for word") &&
    q.includes("(quotation, found word for word in source 1)")));
  assert.ok(!nosource.map(plain).some((n) => n.includes("supports all initiatives")));
  // the words before Italy's quotation keep their mark, with "No source" on the first passage with none
  assert.match(plain(nosource[0]), /^Several European States.*as when Italy said it No source \(no source\)$/s);
});

test("as it was saved, before the display rule, the same quotation sat inside a passage with no source", () => {
  const markup = html(B.briefingBlocks(savedAnswer, byN));
  assert.ok(spans(markup, "wts-nosource").map(plain).some((n) => n.includes("supports all initiatives")));
});

test("the strip's counts are wts-core's check_counts for the answer as shown", () => {
  assert.deepEqual(B.counts(shown.answer), shown.counts);
  const strip = B.strip(shown.answer);
  const words = text(strip).replace(/\s+/g, " ");
  assert.match(words, /^Checked against the UN record/);
  assert.match(words, /✓16of 16citations matched to the record/);
  assert.match(words, /✓15of 15quotations found word for word/);
  assert.match(words, /!2To checksentences without a source\. Show the first/);
  assert.match(B.toHTML(strip), /href="#nosource-0">Show the first</);
  assert.match(B.toHTML(strip), /href="#check-details">How it was checked</);
  assert.equal(B.countsSentence(shown.answer), "Checked against the UN record: 16 of 16 citations matched to the record, " +
    "15 of 15 quotations found word for word, 2 sentences without a source.");
});

test("while the results run up, the counts are hidden from screen readers and the count with no source comes last", () => {
  const strip = B.toHTML(B.strip(shown.answer, { citations: 3, quotes: 2 }));
  assert.match(strip, /<ul class="wts-check__list" aria-hidden="true">/);
  assert.match(strip, /sentences without a source: counted last/);
  assert.ok(!strip.includes("Show the first"));
});

test("each source is a chip naming the country or group, the body and the date, and two runs share one", () => {
  const markup = html(B.briefingBlocks(shown.answer, byN));
  // a chip is named by its own words (no aria-label that differs from what it shows)
  const chips = [...markup.matchAll(/<button type="button" class="wts-chip" data-source="(\d+)"[^>]*>(.*?)<\/button>/g)]
    .map((m) => [m[0], m[1], plain(m[2])]);
  assert.ok(chips.every((c) => !c[0].includes("aria-label")));
  assert.equal(chips[0][2], "Source 1 Italy, Security Council, 14 November 2025. Show the passage");
  // Italy's quotation and "in November 2025" are both source 1's: one chip, after the second
  assert.equal(chips.filter((c) => c[1] === "1").length, 2);  // here, and for Italy's second point later
  assert.match(plain(markup), /use of veto”✓ word for word \(quotation, found word for word in source 1\) in November 2025⁠Source 1 Italy, Security Council, 14 November 2025\. Show the passage\./);
  // a statement on behalf of a group is the group's (decision 7)
  assert.ok(chips.some((c) => c[2] === "Source 7 Benelux countries, Security Council, 14 November 2025. Show the passage"));
  assert.ok(!/·/.test(markup));
});

test("a quotation not found, a passage whose citation was removed, and a judgemental word each say so in words", () => {
  const answer = {
    segments: [
      { text: "Kenya said it \"we will never accept this\" of the proposal", sources: [1], status: "cited",
        misquotes: [{ start: 14, end: 39, text: "we will never accept this" }] },
      { text: ". ", sources: [], status: "connective" },
      { text: "Kenya called for a fund in 2019.", sources: [], status: "invalid" },
      { text: " The regime spoke.", sources: [], status: "unsupported", loaded: [{ start: 5, end: 11, word: "regime" }] },
    ],
    citations_total: 2, citations_invalid: 1, quotes_total: 1, quotes_unverified: 1, unsupported_segments: 1, loaded_words: 1,
  };
  const markup = html(B.briefingBlocks(answer, byN));
  const missing = spans(markup, "wts-quote wts-quote--missing").map(plain);
  assert.deepEqual(missing, ["\"we will never accept this\"Not found word for word (quotation not found word for word in source 1)"]);
  const nosource = spans(markup, "wts-nosource").map(plain);
  assert.deepEqual(nosource, ["Kenya called for a fund in 2019.No source (its citation did not match the source and was removed)",
    " The regime (a judgemental word in the AI’s own voice) spoke. (no source)"]);
  const strip = text(B.strip(answer)).replace(/\s+/g, " ");
  assert.match(strip, /!1of 2To checkcitations matched to the record\. Show the first/);
  assert.match(strip, /!0of 1To checkquotation found word for word\. Show the first/);
  assert.match(strip, /1 judgemental word in the AI’s own voice is underlined/);
  assert.match(B.toHTML(B.strip(answer)), /href="#misquote-0-14"/);
  assert.match(markup, /id="misquote-0-14"/);
  assert.deepEqual(B.counts(answer), { citations_matched: 1, citations_total: 2, quotes_found: 0, quotes_total: 1, no_source: 2 });
});

test("the record's slip is a UN document, with the quoted words highlighted and the chosen ones outlined", () => {
  const quoted = B.quotedFrom(shown.answer);
  // the chip for Italy's quotation is after "in November 2025", the passage next to it from the same source
  const chipSeg = B.chipsShown(shown.answer.segments).findIndex((shows, i) => shows && i > shown.answer.segments.findIndex((s) => s.text === ITALY));
  assert.equal(shown.answer.segments[chipSeg].text, "in November 2025");
  const slip = B.toHTML(B.slip(byN[1], quoted[1], chipSeg, "p1"));
  assert.match(slip, /<span class="wts-slip__sym">S\/PV\.10043 \(Resumption 1\)<\/span><span>14 November 2025<\/span>/);
  assert.match(slip, /<strong>Mr\. Greco<\/strong>, for Italy/);
  assert.match(slip, /Security Council, 10043rd meeting \(resumption 1\)\. Implementation of the note/);
  assert.match(slip, /<mark class="wts-now">supports all initiatives that seek to impose self-limitations on the use of veto<\/mark>/);
  assert.match(slip, /<mark>would inevitably generate additional vetoes<\/mark>/);
  assert.match(slip, /✓ Highlighted words found word for word/);
  // the link to the record carries the family kit's mark for a source opened (inert unless measurement is on)
  assert.match(slip, /<a href="https:\/\/undocs\.org\/en\/S\/PV\.10043\(Resumption1\)" rel="noopener" data-wts-conversion="source_opened">Read S\/PV\.10043 \(Resumption 1\)<\/a>/);
  // a quotation with an ellipsis is highlighted in its pieces
  const nz = B.toHTML(B.slip(byN[24], quoted[24], null, "p24"));
  assert.match(nz, /<mark>to prevent Council action<\/mark>/);
  assert.match(nz, /<mark>impacts every Member State<\/mark>/);
});

test("every source cited has its quotations found in its own text", () => {
  const quoted = B.quotedFrom(shown.answer);
  for (const n of B.citedSources(shown.answer)) {
    const parts = (quoted[n] || []).flatMap((q) => q.parts);
    const marks = B.highlights(byN[n].text, quoted[n], null);
    assert.ok(marks.length >= Math.min(1, parts.length), `source ${n}`);
  }
});

test("the wait's cards and the question as read are the record's own", () => {
  const card = B.toHTML(B.card(byN[7]));
  assert.match(card, /<span class="wts-card__sym">S\/PV\.10043<\/span><span>14 November 2025<\/span>/);
  assert.match(card, /<div class="wts-card__who">Benelux countries, Mr\. Maes<span class="wts-card__cited"> Cited<\/span><\/div>/);
  assert.ok(B.textOf(B.card(byN[7])).includes(byN[7].text.slice(0, 40)));
  const plan = saved.events.find((e) => e.type === "plan").plan;
  assert.deepEqual(B.planChips(plan), ["Security Council", "2022 to today", "all countries"]);
  const evidence = saved.events.find((e) => e.type === "sources").evidence;
  assert.equal(text(B.foundWords(evidence, 5, 24)), "5 records found so far");
  assert.equal(text(B.foundWords(evidence, 24, 24)), "24 statements from 19 countries and groups at 12 meetings");
});

test("the check details keep the verifier's own counts and say what each mark means", () => {
  const words = text(B.details(shown.answer, 24));
  assert.match(words, /^How this briefing was checked/);
  assert.match(words, /only the 24 records found/);
  assert.match(words, /Each of the 16 citations was checked against the text of its source: all matched\./);
  assert.match(words, /Each of the 15 quotations was looked for word for word in its source: all were found/);
  assert.match(words, /2 sentences that no source supports are marked with a dotted underline, and the first with “No source”/);
  assert.match(words, /A quotation found word for word in the source cited straight after it is shown as that source’s quotation/);
});

test("no records found has no chips and no marks", () => {
  const answer = { segments: [{ text: "No records found. The archive contains no records matching this question.", sources: [], status: "connective" }],
    no_records: true, citations_total: 0 };
  const markup = html(B.briefingBlocks(answer, {}));
  assert.equal(markup, "<p>No records found. The archive contains no records matching this question.</p>");
});

test("text is always text: nothing from the record or the model becomes markup", () => {
  const answer = { segments: [{ text: "<img src=x onerror=alert(1)> **bold** said \"<b>three words here</b>\"", sources: [1], status: "cited" }],
    citations_total: 1, quotes_total: 1 };
  const markup = html(B.briefingBlocks(answer, byN));
  assert.ok(!markup.includes("<img") && !markup.includes("<b>"));
  assert.match(markup, /&lt;img src=x onerror=alert\(1\)&gt; <strong>bold<\/strong>/);
});

test("a quotation in a lead-in to its citation is the cited source's, as the verifier checked it", () => {
  const answer = { segments: [
    { text: "As when Kenya said \u201cclimate finance for adaptation is too slow\u201d", sources: [], status: "connective" },
    { text: " in 2024", sources: [3], status: "cited" }, { text: ".", sources: [], status: "connective" }],
    citations_total: 1, quotes_total: 1 };
  const markup = html(B.briefingBlocks(answer, byN));
  assert.deepEqual(spans(markup, "wts-quote wts-quote--checked").map(plain),
    ["\u201cclimate finance for adaptation is too slow\u201d\u2713 word for word (quotation, found word for word in source 3)"]);
  assert.deepEqual(B.quotedFrom(answer)[3], [{ seg: 1, parts: ["climate finance for adaptation is too slow"] }]);
});

test("a quotation no source is given for is left as the model's words, and every block is an element or text", () => {
  const answer = { segments: [{ text: "As Italy said it \u201csupports all initiatives that seek to impose limits\u201d in 2025.", sources: [], status: "connective" }] };
  const blocks = B.briefingBlocks(answer, {});
  const walk = (n) => typeof n === "string" || (n && typeof n.tag === "string" && n.children.every(walk));
  assert.ok(blocks.every(walk));
  assert.equal(html(blocks), "<p>As Italy said it \u201csupports all initiatives that seek to impose limits\u201d in 2025.</p>");
});

// A topic briefing's page is written by the server (src/wcs/api/briefing_html.py, decision 27), so that a reader
// without scripts and a search engine see the whole briefing; the page's script then shows it again. The two must
// make the same markup: tests/test_topics.py writes the server's for two briefings (kGfKBI3cRLw as shown, and a
// made-up one with every kind of mark) into fixtures/server-briefings.json, and this script must give exactly it.
test("the server writes the strip, the text and the check details exactly as this script does", () => {
  const server = require("./fixtures/server-briefings.json");
  assert.equal(server.cases.length, 2);
  for (const c of server.cases) {
    const byN = Object.fromEntries(c.sources.map((s) => [s.n, s]));
    assert.equal(B.toHTML(B.strip(c.answer)), c.strip, `${c.name}: the strip`);
    assert.equal(html(B.briefingBlocks(c.answer, byN)), c.blocks, `${c.name}: the text`);
    assert.equal(B.toHTML(B.details(c.answer, c.found, c.briefing_id)), c.details, `${c.name}: the check details`);
  }
});

// The family kit's measure.js counts what these marks name, and only while the site's measurement switch is on and
// the reader has accepted (decision 28); with it off they are inert attributes, on the page whatever the switch.
test("the end of a finished briefing, its chips and its links to the record carry the family kit's marks", () => {
  const ended = B.toHTML(B.details(shown.answer, 24, "kGfKBI3cRLw"));
  assert.match(ended, /^<section class="check-details" id="check-details" aria-labelledby="check-details-head" data-wts-conversion="briefing_read" data-wts-briefing="kGfKBI3cRLw">/);
  // a briefing without an id (none is known yet) is still marked, and counted once for the element
  assert.match(B.toHTML(B.details(shown.answer, 24)), /^<section [^>]*data-wts-conversion="briefing_read">/);
  const chips = [...html(B.briefingBlocks(shown.answer, byN)).matchAll(/<button type="button" class="wts-chip"[^>]*>/g)];
  assert.ok(chips.length > 10 && chips.every((m) => m[0].endsWith(' data-wts-conversion="source_opened">')));
  const quoted = B.quotedFrom(shown.answer);
  for (const s of [byN[1], { n: 9, kind: "vote", url: "https://digitallibrary.un.org/record/1", totals: {} }]) {
    assert.match(B.toHTML(B.slip(s, quoted[s.n] || [], null, "p")), /<a href="[^"]+" rel="noopener" data-wts-conversion="source_opened">Read /);
  }
  assert.match(B.toHTML(B.card(byN[1])), /^<li class="wts-card" data-source="1">/, "a card of the wait is not a source opened");
});
