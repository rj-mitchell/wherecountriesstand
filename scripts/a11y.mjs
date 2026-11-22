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
// and a saved briefing, made up here (the site runs with no data), as the server shows it (after the display rule for
// quotations), with each kind of mark: a record quotation found word for word, one shown as its source's by the display
// rule ("beside"), a sentence with no source, one whose only citation failed the check against its source (so was
// removed), a quotation not found word for word, and a judgemental word. Each is checked for contrast like everything
// else, and that it is marked, in words as well as by its look (family/BRIEFINGS.md). The record beside the briefing is
// checked on a computer, and the bottom sheet on a phone: open, focused, closed with Escape, focus back on its chip.
// It offers the same question on Where MPs Stand, with that site's spine; a saved "No records found" and a saved
// briefing that failed offer nothing (decision 24). The same briefing is checked again as a topic's (below).
const SAMPLE = "a11ysample";
const NO_RECORDS = "a11ynorecords";
const FAILED = "a11yfailed";
const pages = ["/", "/about.html", "/methodology.html", "/glossary.html", "/corrections.html",
  "/corrections.html?kind=statement", "/named.html", "/accessibility.html", "/privacy.html", "/terms.html",
  "/press.html", "/status.html", "/no-such-page", `/b/${SAMPLE}`, `/b/${NO_RECORDS}`, `/b/${FAILED}`, "/votes.html"];
// The pages written from the record's votes (decision 26): the index, and the first session and the first vote it
// links to. CI starts the site with a small made-up votes file (scripts/sample_votes.py); a site with real votes
// is checked on its own. A site with no votes fails here, as its vote pages would go unchecked.
const votesIndex = await fetch(`${base}/votes.html`).then((r) => r.text());
const sessionPage = votesIndex.match(/href="(\/votes\/session-[a-z0-9-]+\.html)"/)?.[1];
const votePage = votesIndex.match(/href="(\/votes\/(?!session-)[a-z0-9-]+\.html)"/)?.[1];
if (!sessionPage || !votePage) {
  console.log("The site has no vote pages to check: start it with a votes file (python scripts/sample_votes.py <data folder>)");
  process.exit(1);
}
pages.push(sessionPage, votePage);
// The Security Council's votes (decision 30): the index, its first year, a resolution's page and a vetoed draft's.
// CI writes three made-up Council votes with the Assembly's (scripts/sample_votes.py).
const councilIndex = await fetch(`${base}/security-council-votes.html`).then((r) => r.text());
const councilYear = councilIndex.match(/href="(\/security-council-votes\/\d{4}\.html)"/)?.[1];
const councilVote = councilIndex.match(/href="(\/votes\/s-res-[a-z0-9-]+\.html)"/)?.[1];
const notAdopted = councilIndex.slice(councilIndex.indexOf('id="not-adopted"'));
const councilDraft = notAdopted.match(/href="(\/votes\/s-(?!res-)[a-z0-9-]+\.html)"/)?.[1];
if (!councilYear || !councilVote || !councilDraft) {
  console.log("The site has no Security Council vote pages to check: start it with them (python scripts/sample_votes.py <data folder>)");
  process.exit(1);
}
pages.push("/security-council-votes.html", councilYear, councilVote, councilDraft);
// The topic briefings (decision 27): the index and the first topic it links to. CI writes the same made-up briefing
// as the sample below into the site's data as a topic's (scripts/sample_topic.py), so its page is written by the
// server, then shown again by its script: every mark is checked on it as on the saved briefing, and in the page's own
// HTML, with no script, as a search engine reads it.
const topicsIndex = await fetch(`${base}/topics.html`).then((r) => r.text());
const topicPage = topicsIndex.match(/href="(\/topics\/[a-z0-9-]+\.html)"/)?.[1];
if (!topicPage) {
  console.log("The site has no topic briefing to check: write one (python scripts/sample_topic.py <data folder>)");
  process.exit(1);
}
pages.push("/topics.html", topicPage);
// The country pages (decision 29): the index, a Member State with votes (and a topic that cites it), its list of
// votes, China (with its section for the Republic of China's seat), an observer State of each kind, a former Member
// State and Kosovo. Each is written on the server; the country's page is checked for one heading of the first rank,
// its form to the home page and only breadcrumbs as structured data.
const countryPage = "/countries/ken.html";
pages.push("/countries.html", countryPage, "/countries/ken/votes.html", "/countries/chn.html", "/countries/pse.html",
  "/countries/vat.html", "/countries/sun.html", "/countries/xkx.html",
  // with their votes in the Security Council (decision 30): a permanent member, and an elected member's full list
  "/countries/usa.html", "/countries/dza/security-council-votes.html");
const CHECKED = "adaptation finance must reach the most vulnerable";
const BESIDE = "climate finance is far too slow";
const UNSUPPORTED = "Most delegations agreed.";
const INVALID = "Kenya called for a fund in 2019.";
const MISQUOTE = "we will never accept this";
const LOADED = "regime";
const lead = `Several States spoke of slow finance, as when Kenya said it was "${BESIDE}"`;
const source = { n: 1, kind: "record", title: "Mr. Kimani (Kenya)", url: "https://undocs.org/en/S/PV.9999",
  record_id: "sc:S/PV.9999:0003", date: "2026-03-12", date_precision: "day", body: "Security Council",
  meeting: "9999th meeting", symbol: "S/PV.9999", agenda: "Maintenance of international peace and security",
  speaker_name: "Mr. Kimani", country_code: "KEN", country_name: "Kenya", entity_kind: "member_state",
  group: "African States", text: `I thank the President. In our view ${CHECKED}, and ${BESIDE} for the small island ` +
    "States and the least developed countries. We call for a fund that answers to them." };
const sample = {
  question: "What has Kenya said about climate finance?", at: "2026-11-13T12:00:00Z", options: { mode: "summary" },
  events: [
    { type: "plan", plan: { question: "What has Kenya said about climate finance?", interpretation: "Kenya on climate finance.",
      filters: { sources: ["sc"], date_from: "2016-01-01", countries: ["KEN"], groups: [] } }, usage: null },
    { type: "sources", sources: [source], candidates: 1, semantic: false,
      evidence: { records: 1, countries: 1, meetings: 1, date_from: "2026-03-12", date_to: "2026-03-12", votes: 0 } },
    { type: "final", briefing_id: SAMPLE, answer: {
    segments: [
      { text: `Kenya said "${CHECKED}"`, sources: [1], cited: [], status: "cited" },
      { text: ". ", sources: [], cited: [], status: "connective" },
      { text: lead.slice(0, lead.indexOf('"')), sources: [], cited: [], status: "unsupported" },
      { text: `"${BESIDE}"`, sources: [1], cited: [], status: "cited", beside: true },
      { text: " in March 2026", sources: [1], cited: [], status: "cited" },
      { text: ". ", sources: [], cited: [], status: "connective" },
      { text: UNSUPPORTED, sources: [], cited: [], status: "unsupported" },
      { text: " ", sources: [], cited: [], status: "connective" },
      { text: INVALID, sources: [], cited: [], status: "invalid" },
      { text: ` It said "${MISQUOTE}" of the proposal`, sources: [1], cited: [], status: "cited",
        misquotes: [{ start: 9, end: 36, text: MISQUOTE }] },
      { text: ".", sources: [], cited: [], status: "connective" },
      { text: ` The ${LOADED} said so.`, sources: [], cited: [], status: "unsupported",
        loaded: [{ start: 5, end: 5 + LOADED.length, word: LOADED }] },
    ],
    no_records: false, citations_total: 4, citations_invalid: 1, grounded_ratio: 0.6, unsupported_segments: 3,
    loaded_words: 1, quotes_total: 3, quotes_unverified: 1 } }],
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
{
  // the topic's page as the server wrote it, before any script: the whole briefing, indexed
  const raw = await fetch(base + topicPage).then((r) => r.text());
  const missing = [
    !raw.includes('<section class="wts-check"') && "no check strip",
    !raw.includes('<button type="button" class="wts-chip"') && "no source chips",
    !raw.includes('class="wts-quote wts-quote--checked"') && "no record quotation",
    !raw.includes('class="wts-nosource"') && "no mark for text with no source",
    !raw.includes('id="check-details"') && "no check details",
    !raw.includes('<li class="source"') && "no sources",
    raw.includes('name="robots" content="noindex"') && "it is kept out of search engines",
  ].filter(Boolean);
  for (const m of missing) {
    failures++;
    console.log(`${topicPage} as the server writes it: ${m}`);
  }
}
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
      if (path === `/b/${SAMPLE}` || path === topicPage) {
        // every mark is there, in words as well as by its look, and nothing found word for word is marked as no source
        const marked = await page.evaluate(() => {
          const texts = (sel) => [...document.querySelectorAll(sel)].map((e) => e.textContent);
          const strip = document.querySelector("#strip .wts-check");
          return {
            nosource: texts("#briefing .wts-nosource"),
            dotted: [...document.querySelectorAll("#briefing .wts-nosource")].every((e) => getComputedStyle(e).textDecorationStyle === "dotted"),
            tag: texts("#briefing .wts-nosource__tag"),
            checked: texts("#briefing .wts-quote--checked"),
            checkedInNoSource: document.querySelectorAll("#briefing .wts-nosource .wts-quote--checked").length,
            missing: texts("#briefing .wts-quote--missing"),
            loaded: texts("#briefing .loaded"),
            chips: [...document.querySelectorAll("#briefing .wts-chip")].map((c) => c.textContent),
            strip: strip ? strip.textContent.replace(/\s+/g, " ") : "",
            details: document.getElementById("check-details")?.textContent || "",
            record: !!document.querySelector("#record-panel .wts-slip mark"),
          };
        });
        const missing = [
          !marked.nosource.some((t) => t.startsWith(UNSUPPORTED) && t.includes("(no source)")) && "the sentence with no source is not marked",
          !marked.nosource.some((t) => t.startsWith(INVALID) && t.includes("(its citation did not match the source and was removed)"))
            && "the sentence whose citation failed is not marked",
          !marked.dotted && "a passage with no source is not underlined with dots",
          marked.tag.length !== 1 && "the first passage with no source does not say “No source”",
          !marked.checked.some((t) => t.includes(CHECKED) && t.includes("found word for word in source 1")) && "the quotation found is not marked",
          !marked.checked.some((t) => t.includes(BESIDE)) && "the quotation beside its citation is not shown as its source’s",
          marked.checkedInNoSource && "a quotation found word for word is marked as having no source",
          !marked.missing.some((t) => t.includes(MISQUOTE) && t.includes("not found word for word")) && "the quotation not found is not marked",
          !marked.loaded.some((t) => t.includes(LOADED) && t.includes("judgemental word")) && "the judgemental word is not marked",
          !marked.chips.length && "there are no source chips",
          marked.chips.some((c) => c !== "Source 1 Kenya, Security Council, 12 March 2026. Show the passage") && "a chip does not name its source",
          !/Checked against the UN record/.test(marked.strip) && "there is no check strip",
          !/!\s*3\s*of 4\s*To check\s*citations matched to the record/.test(marked.strip) && `the strip's citations count reads "${marked.strip}"`,
          !/!\s*2\s*of 3\s*To check\s*quotations found word for word/.test(marked.strip) && `the strip's quotations count reads "${marked.strip}"`,
          // as a topic's, the server shows it as it shows every saved briefing, judged again by today's checking
          // rules, which read the lead-in to Kenya's second quotation as a join: one passage with no source fewer
          !new RegExp(`!\\s*${path === topicPage ? 3 : 4}\\s*To check\\s*sentences without a source`).test(marked.strip)
            && `the strip's count with no source reads "${marked.strip}"`,
          !/did not match/.test(marked.details) && "the check details do not explain the citation that failed",
          width > 1000 && !marked.record && "the record is not beside the briefing",
        ].filter(Boolean);
        for (const m of missing) {
          failures++;
          console.log(`${scheme} ${width}px ${path}: ${m}`);
        }
        if (width < 1000) {
          // a chip opens the record in a sheet: focus moves in, Escape closes it, and focus goes back to the chip
          const chip = page.locator("#briefing .wts-chip").first();
          await chip.click();
          const open = await page.evaluate(() => ({
            dialog: !!document.querySelector('#sheet[role="dialog"][aria-modal="true"]'),
            focus: document.activeElement?.id,
            mark: !!document.querySelector("#sheet .wts-passage mark"),
          }));
          const opened = await page.evaluate(() => window.axe.run(document.getElementById("sheet"),
            { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] } }));
          for (const v of opened.violations.filter((v) => v.impact === "serious" || v.impact === "critical")) {
            failures++;
            console.log(`${scheme} ${width}px ${path} (the sheet open): ${v.id} (${v.impact}) ${v.help}`);
          }
          await page.keyboard.press("Escape");
          const closed = await page.evaluate(() => ({ gone: !document.getElementById("sheet"),
            back: document.activeElement?.classList.contains("wts-chip") }));
          const sheet = [
            !open.dialog && "a chip does not open the sheet as a dialog",
            open.focus !== "sheet-head" && "focus does not move into the sheet",
            !open.mark && "the sheet does not highlight the quoted words",
            !closed.gone && "Escape does not close the sheet",
            !closed.back && "focus does not go back to the chip",
          ].filter(Boolean);
          for (const m of sheet) {
            failures++;
            console.log(`${scheme} ${width}px ${path}: ${m}`);
          }
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
      if (path === votePage || path === councilVote || path === councilDraft) {
        // the vote's page is written on the server: its table, its totals and the family's words for anyone named
        const vote = await page.evaluate(() => ({
          h1: document.querySelectorAll("h1").length,
          lists: document.querySelectorAll("ul.vote-list li").length,
          totals: document.querySelectorAll("table.vote-totals tbody tr").length,
          named: !!document.querySelector('.vote-side a[href="/named.html"]')
            && document.querySelector('.vote-side a[href="/named.html"]').textContent === "If you’re named on this site",
          data: [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => JSON.parse(s.textContent)["@type"]),
        }));
        const problems = [
          vote.h1 !== 1 && "the vote's page does not have one heading of the first rank",
          !vote.lists && "the vote's page lists no countries",
          (path === votePage ? vote.totals !== 4 : !vote.totals) && "the vote's page has no table of totals",
          !vote.named && "the vote's page does not link to If you're named on this site",
          (vote.data.length !== 1 || vote.data[0] !== "BreadcrumbList") && "the vote's page carries structured data other than its breadcrumbs",
        ].filter(Boolean);
        for (const m of problems) {
          failures++;
          console.log(`${scheme} ${width}px ${path}: ${m}`);
        }
      }
      if (path === countryPage) {
        const country = await page.evaluate(() => ({
          h1: document.querySelectorAll("h1").length,
          form: document.querySelector("form.country-ask")?.getAttribute("action"),
          question: document.querySelector("form.country-ask textarea[name=q]")?.value,
          data: [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => JSON.parse(s.textContent)["@type"]),
        }));
        const problems = [
          country.h1 !== 1 && "the country's page does not have one heading of the first rank",
          country.form !== "/" && "the country's question is not a form to the home page",
          !(country.question || "").includes("Kenya") && "the country's question does not name it",
          (country.data.length !== 1 || country.data[0] !== "BreadcrumbList") && "the country's page carries structured data other than its breadcrumbs",
        ].filter(Boolean);
        for (const m of problems) {
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
  : `No serious accessibility problems on ${pages.length} pages, the sample briefing's marks are all there in words ` +
    "(on its saved page and as a topic's, in the server's HTML too), only a written briefing offers the same " +
    "question on Where MPs Stand, the vote pages carry their table, and the country pages their form");
process.exit(failures ? 1 : 0);
