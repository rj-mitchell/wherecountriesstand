// Accessibility check for CI (adapted from Where MPs Stand's scripts/a11y.mjs): loads every page of the site in
// Chromium, light and dark, at desktop and phone widths, and fails on any serious or critical WCAG 2.2 AA problem
// that axe finds, or a page that scrolls sideways. Usage: node scripts/a11y.mjs http://localhost:8000
// (with playwright and axe-core installed where Node can find them; see .github/workflows/ci.yml).
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { join } from "node:path";
import { chromium } from "playwright";

// resolved from the folder it is run in, where CI installs the two packages
const require = createRequire(join(process.cwd(), "noop.js"));
const axe = readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
const base = process.argv[2] ?? "http://localhost:8000";
// the corrections page twice: with the report's fields, and with the statement's (the right of reply, decision 16)
// and a saved briefing, made up here (the site runs with no data), with each kind of point to check: a sentence with
// no source, one whose only citation failed the check against its source (so was removed), and a quotation not found
// word for word. Its grey and underlines are checked for contrast like everything else, and that each is marked.
// It offers the same question on Where MPs Stand, with that site's spine; a saved "No records found" and a saved
// briefing that failed offer nothing (decision 24).
const SAMPLE = "a11ysample";
const NO_RECORDS = "a11ynorecords";
const FAILED = "a11yfailed";
const pages = ["/", "/about.html", "/methodology.html", "/glossary.html", "/corrections.html",
  "/corrections.html?kind=statement", "/named.html", "/accessibility.html", "/privacy.html", "/terms.html",
  "/press.html", "/status.html", "/no-such-page", `/b/${SAMPLE}`, `/b/${NO_RECORDS}`, `/b/${FAILED}`];
const UNSUPPORTED = "Most delegations agreed.";
const INVALID = "Kenya called for a fund in 2019.";
const MISQUOTE = "we will never accept this";
const sample = {
  question: "What has Kenya said about climate finance?", at: "2026-11-13T12:00:00Z", options: { mode: "summary" },
  events: [{ type: "final", briefing_id: SAMPLE, answer: {
    segments: [
      { text: "Kenya spoke of adaptation finance", sources: [1], cited: [], status: "cited" },
      { text: ". ", sources: [], cited: [], status: "connective" },
      { text: UNSUPPORTED, sources: [], cited: [], status: "unsupported" },
      { text: " ", sources: [], cited: [], status: "connective" },
      { text: INVALID, sources: [], cited: [], status: "invalid" },
      { text: ` It said "${MISQUOTE}" of the proposal`, sources: [1], cited: [], status: "cited",
        misquotes: [{ start: 9, end: 36, text: MISQUOTE }] },
      { text: ".", sources: [], cited: [], status: "connective" },
    ],
    no_records: false, citations_total: 3, citations_invalid: 1, grounded_ratio: 0.6, unsupported_segments: 1,
    loaded_words: 0, quotes_total: 1, quotes_unverified: 1 } }],
};
const MPS_LINK = `https://wherempsstand.com/?q=${encodeURIComponent(sample.question)}`;
const noRecords = {
  question: "What has Kenya said about cricket?", at: "2026-11-13T12:00:00Z", options: { mode: "summary" },
  events: [{ type: "final", briefing_id: NO_RECORDS, answer: {
    segments: [{ text: "No records found. The archive contains no records matching this question and these filters.",
      sources: [], cited: [], status: "connective" }],
    markdown: "No records found.", no_records: true, citations_total: 0, citations_invalid: 0, grounded_ratio: 1.0,
    unsupported_segments: 0 } }],
};
const failed = {
  question: "What has Kenya said about debt relief?", at: "2026-11-13T12:00:00Z", options: { mode: "summary" },
  events: [{ type: "error", message: "The briefing could not be written. Please try again later." }],
};
const saved = { [SAMPLE]: sample, [NO_RECORDS]: noRecords, [FAILED]: failed };

const browser = await chromium.launch();
let failures = 0;
for (const scheme of ["light", "dark"]) {
  for (const width of [1280, 390]) {
    // the site's content security policy is set by the front door, not the app, so there is none to bypass here
    const ctx = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: scheme });
    for (const [id, body] of Object.entries(saved)) {
      await ctx.route(`**/api/briefings/${id}`, (route) =>
        route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) }));
    }
    const page = await ctx.newPage();
    for (const path of pages) {
      await page.goto(base + path, { waitUntil: "networkidle" }).catch(() => {});
      await page.addScriptTag({ content: axe });
      const result = await page.evaluate(async () =>
        window.axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } }));
      for (const v of result.violations.filter((v) => v.impact === "serious" || v.impact === "critical")) {
        failures++;
        console.log(`${scheme} ${width}px ${path}: ${v.id} (${v.impact}) ${v.help}`);
        for (const n of v.nodes.slice(0, 3)) console.log(`    ${n.target.join(" ")}`);
      }
      if (path === `/b/${SAMPLE}`) {
        // a sentence whose citations all failed is set in grey, as one with no source is, and the note says why
        const marked = await page.evaluate(() => ({
          grey: [...document.querySelectorAll("#briefing .unsupported")].map((e) => e.textContent),
          wave: [...document.querySelectorAll("#briefing .misquote")].map((e) => e.textContent),
          note: document.getElementById("marks").hidden ? "" : document.getElementById("marks").textContent,
        }));
        const missing = [
          !marked.grey.includes(UNSUPPORTED) && "the sentence with no source is not in grey",
          !marked.grey.includes(INVALID) && "the sentence whose citation failed is not in grey",
          !marked.wave.some((t) => t.includes(MISQUOTE)) && "the quotation not found is not underlined",
          !/did not bear it out/.test(marked.note) && "the note does not explain the sentence whose citation failed",
          !/not found word for word/.test(marked.note) && "the note does not explain the quotation",
        ].filter(Boolean);
        for (const m of missing) {
          failures++;
          console.log(`${scheme} ${width}px ${path}: ${m}`);
        }
      }
      if (path.startsWith("/b/")) {
        // the same question on Where MPs Stand, after the briefing, named and with its spine: on a written briefing only
        const offer = await page.evaluate(() => ({
          shown: !document.getElementById("sisters").hidden,
          links: [...document.querySelectorAll("#sister-links a")].map((a) => ({
            href: a.getAttribute("href"), text: a.textContent.trim(), spine: !!a.querySelector("svg.wts-spine.wts-cloth--mps") })),
        }));
        const mps = offer.links.find((l) => l.href.startsWith("https://wherempsstand.com/"));
        const problems = path === `/b/${SAMPLE}` ? [
          !offer.shown && "the links to sister sites are hidden",
          !mps && "there is no link to Where MPs Stand",
          mps && mps.href !== MPS_LINK && `the link to Where MPs Stand is ${mps.href}, not ${MPS_LINK}`,
          mps && mps.text !== "Ask the same question on Where MPs Stand" && `the link to Where MPs Stand reads "${mps.text}"`,
          mps && !mps.spine && "the link to Where MPs Stand has no spine",
        ] : [(offer.shown || offer.links.length) && "a briefing that was not written offers the question to a sister site"];
        for (const m of problems.filter(Boolean)) {
          failures++;
          console.log(`${scheme} ${width}px ${path}: ${m}`);
        }
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      if (overflow > 1) {
        failures++;
        console.log(`${scheme} ${width}px ${path}: the page scrolls sideways by ${overflow}px`);
      }
    }
    await ctx.close();
  }
}
await browser.close();
console.log(failures ? `${failures} accessibility problem(s) found`
  : `No serious accessibility problems on ${pages.length} pages, the sample briefing's points to check are marked, ` +
    "and only a written briefing offers the same question on Where MPs Stand");
process.exit(failures ? 1 : 0);
