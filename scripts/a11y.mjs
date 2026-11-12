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
const pages = ["/", "/about.html", "/glossary.html", "/corrections.html", "/corrections.html?kind=statement",
  "/named.html", "/accessibility.html", "/privacy.html", "/terms.html", "/press.html", "/status.html", "/no-such-page"];

const browser = await chromium.launch();
let failures = 0;
for (const scheme of ["light", "dark"]) {
  for (const width of [1280, 390]) {
    // the site's content security policy is set by the front door, not the app, so there is none to bypass here
    const ctx = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: scheme });
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
console.log(failures ? `${failures} accessibility problem(s) found` : `No serious accessibility problems on ${pages.length} pages`);
process.exit(failures ? 1 : 0);
