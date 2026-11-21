// Where Countries Stand: the ask page. Streams a briefing from /api/ask (server-sent events: plan, sources, text and
// citation, final), or opens a saved one (/b/<id>), and shows it as the family shows a checked briefing
// (family/BRIEFINGS.md in the charity's repository, with /family/briefing.css): the check strip, record quotations,
// source chips, every passage with no source marked, and the record beside the briefing (a bottom sheet on phones).
// While a briefing is written, the wait shows its real events in order (docs/DESIGN.md, "The wait").
// The briefing's parts are built by briefing.js; every piece of text is set as text: nothing from the record or the
// model is parsed as HTML.
"use strict";

const $ = (id) => document.getElementById(id);
const B = window.WCSBriefing;
const BODY = { sc: "Security Council", gd: "General Assembly" };
let names = { countries: {}, kinds: {}, groups: {} };
// Sister sites a briefing links to (docs/DESIGN.md, "Links to sister sites"): Where MPs Stand takes the same
// question as ?q=, and Where Lives Stand has a page for each country at /country/<ISO 3166 alpha-3 code>.
const MPS = "https://wherempsstand.com/";
const LIVES = "https://wherelivesstand.com/country/";
// A statement under the right of reply is shown with these words, in the family's wording (decision 16).
const NOT_ENDORSED = "In their own words. Not checked or endorsed by Where Countries Stand.";
let shownBriefing = null;  // the briefing whose statements may be shown (a new question clears it)

const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const wide = () => matchMedia("(min-width: 64rem)").matches;  // the record beside the briefing, or a sheet
const canHover = () => matchMedia("(hover: hover)").matches;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}
const dom = (tree) => B.toDOM(tree);

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00Z" : ""));
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

async function loadNames() {
  try {
    const [countries, groups] = await Promise.all([fetch("/api/countries").then((r) => r.json()),
                                                   fetch("/api/groups").then((r) => r.json())]);
    for (const c of countries) { names.countries[c.code] = c.name; names.kinds[c.code] = c.kind; }
    for (const g of groups) names.groups[g.key] = g.label;
  } catch (e) { /* the codes are shown instead */ }
}

async function loadCoverage() {
  const list = $("coverage");
  try {
    const meta = await fetch("/api/meta").then((r) => r.json());
    const c = meta.coverage || {};
    const span = (x) => (x ? `${x.from.slice(0, 4)} to ${fmtDate(x.to)}` : "not loaded yet");
    list.replaceChildren(
      ...[["General Debate", c.gd], ["Security Council debates", c.sc], ["General Assembly votes", c.votes]].map(
        ([label, x]) => el("div", {}, el("dt", {}, label), el("dd", {}, span(x)))));
    if (!meta.briefings) {
      // no model: the main button finds the sources, so the second would only repeat it
      $("go").textContent = "Find sources";
      $("go-sources").hidden = true;
    }
  } catch (e) {
    // The server wrote the dates into the page; if they can't be refreshed, they stay as they are.
  }
}

function chips(plan) {
  const f = plan.filters || {};
  const out = [];
  for (const s of f.sources || []) out.push(el("li", { class: `chip chip--${s === "sc" ? "sc" : "ga"}` }, BODY[s] || s));
  if (!(f.sources || []).length) out.push(el("li", { class: "chip" }, "Security Council and General Assembly"));
  if (f.date_from || f.date_to) {
    const from = f.date_from ? f.date_from.slice(0, 4) : "1946";
    const to = f.date_to ? f.date_to.slice(0, 4) : "today";
    out.push(el("li", { class: "chip" }, from === to ? from : `${from} to ${to}`));
  }
  for (const c of f.countries || []) out.push(el("li", { class: "chip" }, names.countries[c] || c));
  for (const g of f.groups || []) out.push(el("li", { class: "chip" }, names.groups[g] || g));
  if (!(f.countries || []).length && !(f.groups || []).length) out.push(el("li", { class: "chip" }, "All countries"));
  $("chips").replaceChildren(...out);
}

function summary(evidence) {
  const p = $("summary");
  if (!evidence || !evidence.records) { p.hidden = true; return; }
  const parts = [`${evidence.records} statements from ${evidence.countries} countries and groups at ${evidence.meetings} meetings`];
  if (evidence.date_from) parts.push(`, ${fmtDate(evidence.date_from)} to ${fmtDate(evidence.date_to)}`);
  if (evidence.votes) parts.push(`, and ${evidence.votes} recorded vote${evidence.votes > 1 ? "s" : ""}`);
  parts.push(".");
  // countries the question names are always included when they have spoken on the subject
  const named = (evidence.named || []).map(n => n.records
    ? `${n.name} (${n.records} statement${n.records > 1 ? "s" : ""})`
    : `${n.name} (no relevant statement found)`);
  if (named.length) parts.push(` Named in the question: ${named.join(", ")}.`);
  parts.push(" The site counts these from the record itself.");
  p.replaceChildren(...parts);
  p.hidden = false;
}

// A sister site's spine, copied from the family shelf in the footer, so a link shows which site it goes to.
function spine(site) {
  const drawn = document.querySelector(`.wts-shelf .wts-cloth--${site}`);
  return drawn ? drawn.cloneNode(true) : null;
}

// Offered once a briefing has been written, after it with its address and report link: never for "No records found",
// a briefing that failed, or sources shown without one (decision 24).
function sisters(question, countries = []) {
  const links = [];
  if (question) {
    links.push(el("li", {}, el("a", { href: `${MPS}?q=${encodeURIComponent(question)}` },
      spine("mps"), "Ask the same question on Where MPs Stand")));
  }
  for (const code of countries) {
    if (!["member_state", "observer_state"].includes(names.kinds[code])) continue;
    links.push(el("li", {}, el("a", { href: LIVES + encodeURIComponent(code) },
      spine("lives"), `Quality of life in ${names.countries[code]} on Where Lives Stand`)));
  }
  $("sister-links").replaceChildren(...links);
  $("sisters").hidden = !links.length;
}

// The sources under the briefing: the printed reference list, with each source's address when printed.
function sourceCard(s) {
  if (s.kind === "vote") {
    const t = s.totals || {};
    return el("li", { class: "source source--vote", id: `s${s.n}` },
      el("p", { class: "source__meta" }, `General Assembly, recorded vote, ${fmtDate(s.date)}`),
      el("p", { class: "source__speaker" }, el("b", {}, `Resolution ${s.resolution}`), ` ${s.title || ""}`),
      el("p", { class: "procedural" }, `${t.yes} in favour, ${t.no} against, ${t.abstain} abstaining`),
      el("p", { class: "source__links" }, el("a", { href: s.url, rel: "noopener" }, "UN Digital Library record")));
  }
  // one line for where it was said, one for when and its document symbol, one for the agenda item
  const meta = [[s.body, s.meeting], [s.date_precision === "session" ? null : fmtDate(s.date), s.symbol], [s.agenda]]
    .map((line) => line.filter(Boolean).join(", ")).filter(Boolean)
    .flatMap((line, i) => (i ? [el("br"), line] : [line]));
  const who = [el("b", {}, s.speaker_name || "Speaker")];
  if (s.country_name) who.push(` (${s.country_name})`);
  if (s.on_behalf_of) who.push(`, on behalf of the ${s.on_behalf_of}`);
  if (s.language) who.push(" ", el("i", {}, `(spoke in ${s.language})`));
  who.push(":");
  const words = (s.text || "").split(/\s+/);
  const short = words.slice(0, 90).join(" ") + (words.length > 90 ? " …" : "");  // the ellipsis never alone on a line
  const notes = [];
  if (s.language) notes.push("English text: UN translation.");
  if (s.text_source === "delegation") notes.push("Text issued by the delegation, as prepared; not yet the official record.");
  if (s.text_source === "annex") notes.push("A statement shown as a recorded video: its text is printed in an annex to the record, as provided by the delegation.");
  if (s.text_source === "transcript") notes.push("AI transcript of the UN’s English interpretation; not yet the official record.");
  if (s.text_source === "corpus") notes.push("From the UN General Debate Corpus (the delegation’s text or a transcript); not yet the official record.");
  if (s.ocr) notes.push("Text recognised from a scanned record.");
  const card = el("li", { class: `source${s.body === "General Assembly" ? " source--ga" : ""}`, id: `s${s.n}` },
    el("p", { class: "source__meta" }, meta),
    el("p", { class: "source__speaker" }, who),
    el("blockquote", {}, short));
  if (words.length > 90) {
    card.append(el("details", { class: "source__more" }, el("summary", {}, "Full statement"), el("p", {}, s.text)));
  }
  if (notes.length) card.append(el("p", { class: "source__notes" }, notes.join(" ")));
  card.append(el("p", { class: "source__links" },
    el("a", { href: s.url, rel: "noopener" }, s.symbol ? `Read ${s.symbol}` : "Read the source")));
  return card;
}

function showSources(list) {
  $("sources").replaceChildren(...list.map(sourceCard));
  $("sources-section").hidden = !list.length;
}

// ---------- the finished briefing: the strip, the text, the record beside it ----------

// What the page shows of the briefing on screen now, for the record panel, the sheet and the chips.
const shown = { answer: null, sources: [], byN: {}, quoted: {}, cited: [], active: null, pinnedUntil: 0 };

function setBriefing(answer, sources) {
  shown.answer = answer;
  shown.sources = sources || [];
  shown.byN = Object.fromEntries(shown.sources.map((s) => [s.n, s]));
  shown.quoted = B.quotedFrom(answer);
  shown.cited = B.citedSources(answer).filter((n) => shown.byN[n]);
}

// The briefing as written and checked. A briefing with no records found has no strip: the site says so in its text.
function renderAnswer(answer, sources, { running = null } = {}) {
  setBriefing(answer, sources);
  const checked = !answer.no_records;
  $("strip").replaceChildren(...(checked ? [dom(B.strip(answer, running))] : []));
  $("briefing").replaceChildren(...B.briefingBlocks(answer, shown.byN).map(dom));
  $("details").replaceChildren(...(checked ? [dom(B.details(answer, shown.sources.length))] : []));
  bindChips();
}

// The strip's "Show the first" links: to the passage, scrolled into view and briefly shown.
function bindFirsts() {
  for (const a of document.querySelectorAll(".wts-check__first")) {
    a.addEventListener("click", (e) => {
      const target = document.querySelector(a.getAttribute("href"));
      if (!target) return;
      e.preventDefault();
      target.setAttribute("tabindex", "-1");
      target.scrollIntoView({ block: "center", behavior: reduced() ? "auto" : "smooth" });
      target.focus({ preventScroll: true });
      target.classList.add("is-shown");
      setTimeout(() => target.classList.remove("is-shown"), 1600);
      history.replaceState(null, "", location.pathname + location.search);
    });
  }
}

function bindChips() {
  for (const c of document.querySelectorAll("#briefing .wts-chip")) {
    const n = Number(c.dataset.source);
    c.addEventListener("click", () => {
      shown.pinnedUntil = Date.now() + 4000;
      if (wide()) { settleNow(); showRecord(n, c); } else openSheet(n, c);
    });
    c.addEventListener("focus", () => {
      if (!wide() || !$("record-panel")) return;
      shown.pinnedUntil = Date.now() + 4000;
      showRecord(n, c);
    });
    c.addEventListener("mouseenter", () => {
      if (!wide() || !canHover() || !$("record-panel")) return;
      shown.pinnedUntil = Date.now() + 2500;
      showRecord(n, c);
    });
  }
  bindFirsts();
}

// The record beside the briefing, on wide screens: open from the start on the first source, following the chips.
function openRecord(settling) {
  const col = $("record-col");
  if (!shown.cited.length) { col.replaceChildren(); col.hidden = true; return; }
  col.replaceChildren(el("div", { class: `wts-record${settling && !reduced() ? " is-settling" : ""}`, id: "record-panel" }));
  col.hidden = false;
  const first = document.querySelector("#briefing .wts-chip");
  showRecord(first ? Number(first.dataset.source) : shown.cited[0], null, { quiet: true });
}

function showRecord(n, chip, { quiet = false } = {}) {
  const panel = $("record-panel");
  const s = shown.byN[n];
  if (!panel || !s) return;
  shown.active = n;
  const seg = chip ? Number(chip.dataset.seg) : null;
  const i = shown.cited.indexOf(n);
  const prev = el("button", { type: "button" }, "Previous source");
  const next = el("button", { type: "button" }, "Next source");
  prev.addEventListener("click", () => step(-1));
  next.addEventListener("click", () => step(1));
  panel.replaceChildren(
    el("p", { class: "wts-record__title" }, "The record ", el("span", {}, `Source ${n}, ${i + 1} of ${shown.cited.length}`)),
    dom(B.slip(s, shown.quoted[n], seg === null ? firstSegOf(n) : seg, "record-passage")),
    el("div", { class: "wts-record__pager" }, prev, next));
  for (const c of document.querySelectorAll("#briefing .wts-chip")) {
    c.setAttribute("aria-current", String(Number(c.dataset.source) === n && (!chip || c === chip)));
  }
  const passage = $("record-passage");
  const mark = panel.querySelector("mark.wts-now") || panel.querySelector("mark");
  if (mark && passage) passage.scrollTop = Math.max(0, mark.offsetTop - passage.offsetTop - 24);
  if (!quiet || chip) drawConnector(chip || document.querySelector(`#briefing .wts-chip[data-source="${n}"]`));
  else drawConnector(null);
}

function firstSegOf(n) {
  const chip = document.querySelector(`#briefing .wts-chip[data-source="${n}"]`);
  return chip ? Number(chip.dataset.seg) : null;
}

function step(d) {
  if (!shown.cited.length) return;
  const i = shown.cited.indexOf(shown.active);
  const n = shown.cited[(i + d + shown.cited.length) % shown.cited.length];
  shown.pinnedUntil = Date.now() + 4000;
  const chip = document.querySelector(`#briefing .wts-chip[data-source="${n}"]`);
  showRecord(n, chip);
  if (chip) chip.scrollIntoView({ block: "center", behavior: reduced() ? "auto" : "smooth" });
}

// A line from the chosen chip's line of text to its passage, across the gutter.
function drawConnector(chip) {
  const svg = $("connector");
  svg.replaceChildren();
  const panel = $("record-panel");
  if (!chip || !panel || !wide()) return;
  const box = $("result-grid").getBoundingClientRect(), a = chip.getBoundingClientRect();
  if (a.bottom < 0 || a.top > innerHeight) return;
  const passage = $("record-passage");
  if (!passage) return;
  const m = panel.querySelector("mark.wts-now") || panel.querySelector("mark") || passage;
  const b = m.getBoundingClientRect(), pb = passage.getBoundingClientRect();
  const text = $("briefing").getBoundingClientRect();
  const x1 = Math.min(text.right, a.right) + 8 - box.left, y1 = a.top + a.height / 2 - box.top;
  const x2 = pb.left - 6 - box.left;
  const y2 = Math.min(Math.max(b.top + 10, pb.top + 8), pb.bottom - 8) - box.top;
  if (x2 - x1 < 12) return;
  const mid = (x1 + x2) / 2;
  const ns = "http://www.w3.org/2000/svg";
  svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
  svg.setAttribute("width", box.width);
  svg.setAttribute("height", box.height);
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", `M${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`);
  svg.append(path);
  for (const [cx, cy] of [[x1, y1], [x2, y2]]) {
    const dot = document.createElementNS(ns, "circle");
    dot.setAttribute("cx", cx); dot.setAttribute("cy", cy); dot.setAttribute("r", 3);
    svg.append(dot);
  }
}

// As the reader scrolls a wide screen, the chip nearest the upper third of the window leads, unless they have just
// chosen one.
let ticking = false;
addEventListener("scroll", () => {
  if (ticking || !$("record-panel")) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
    if (!wide()) return;
    const current = document.querySelector('#briefing .wts-chip[aria-current="true"]');
    if (Date.now() < shown.pinnedUntil) { drawConnector(current); return; }
    const line = innerHeight * 0.38;
    let best = null, bd = Infinity;
    for (const c of document.querySelectorAll("#briefing .wts-chip")) {
      const r = c.getBoundingClientRect();
      const d = Math.abs(r.top - line);
      if (r.bottom > 0 && r.top < innerHeight && d < bd) { bd = d; best = c; }
    }
    if (best && best !== current) showRecord(Number(best.dataset.source), best);
    else drawConnector(current);
  });
}, { passive: true });
addEventListener("resize", () => {
  if (wide() && $("sheet")) closeSheet();
  if (!$("record-panel") && wide() && shown.answer && !wait.active) openRecord(false);
  drawConnector(document.querySelector('#briefing .wts-chip[aria-current="true"]'));
});

// On narrower screens a chip opens the same slip in a sheet from the bottom of the screen: focus moves to its
// heading, stays inside it, and goes back to the chip when it closes (Close, Escape, or a tap outside).
let sheetChip = null;
function openSheet(n, chip) {
  const s = shown.byN[n];
  if (!s) return;
  closeSheet(false);
  sheetChip = chip;
  const close = el("button", { type: "button", class: "wts-sheet__close" }, "Close");
  const back = el("div", { class: "wts-sheet__back", id: "sheet-back" });
  const sheet = el("div", { class: "wts-sheet wts-cloth--countries", role: "dialog", "aria-modal": "true",
    "aria-labelledby": "sheet-head", id: "sheet" },
    el("div", { class: "wts-sheet__head" }, el("h2", { id: "sheet-head", tabindex: "-1" }, `Source ${n}: the record`), close),
    dom(B.slip(s, shown.quoted[n], Number(chip.dataset.seg), "sheet-passage", true)));
  close.addEventListener("click", () => closeSheet());
  back.addEventListener("click", () => closeSheet());
  $("sheet-root").replaceChildren(back, sheet);
  document.addEventListener("keydown", sheetKeys);
  document.body.classList.add("has-sheet");
  $("sheet-head").focus();
}
function sheetKeys(e) {
  const sheet = $("sheet");
  if (!sheet) return;
  if (e.key === "Escape") { e.preventDefault(); closeSheet(); return; }
  if (e.key !== "Tab") return;
  const f = [...sheet.querySelectorAll("button, a[href], [tabindex='0']")];
  const i = f.indexOf(document.activeElement);
  if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); }
  else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
  else if (i < 0) { e.preventDefault(); f[0].focus(); }
}
function closeSheet(refocus = true) {
  if (!$("sheet")) return;
  $("sheet-root").replaceChildren();
  document.removeEventListener("keydown", sheetKeys);
  document.body.classList.remove("has-sheet");
  if (refocus && sheetChip) sheetChip.focus();
  sheetChip = null;
}

// ---------- the wait: the stages, drawn from the stream's real events in order ----------

const STAGES = ["Reading the question", "Searching the record", "Writing from these records only",
  "Checking every citation and quotation"];
const CLOTHS = ["mps", "councils", "landlords", "countries", "lives"];
const wait = { active: false, timers: [], dealing: null, stage: -1, sourcesOnly: false, lit: new Set() };

function announce(text) { $("status").textContent = text; }

function setStage(k) {
  wait.stage = k;
  const names = wait.sourcesOnly ? STAGES.slice(0, 2) : STAGES;
  $("stages").replaceChildren(...names.map((n, i) => el("li", { class: "wts-stage", "data-state": i < k ? "done" : i === k ? "now" : "next" },
    el("span", { class: "wts-stage__state" }, i < k ? "✓ Done" : i === k ? "Now" : "Next"), el("span", {}, n))));
}

function startWait(sourcesOnly) {
  stopWait();
  wait.active = true;
  wait.sourcesOnly = sourcesOnly;
  wait.lit = new Set();
  const spines = el("div", { class: "wts-spines", "aria-hidden": "true", id: "spines" });
  CLOTHS.forEach((c, i) => {
    const s = el("span");
    s.style.setProperty("--c", `var(--wts-cloth-${c})`);
    s.style.setProperty("--i", String(i));
    s.style.setProperty("--h", `${[64, 56, 70, 60, 52][i]}px`);
    spines.append(s);
  });
  $("wait").replaceChildren(
    el("ol", { class: "wts-stages", id: "stages", "aria-label": "Preparing the briefing" }),
    spines,
    el("ul", { class: "wts-plan", id: "plan-chips", "aria-label": "The question as read", hidden: true }),
    el("p", { class: "wait__reading", id: "plan-reading", hidden: true }),
    el("p", { class: "wts-found", id: "found", hidden: true }),
    el("p", { class: "wait__only", id: "only", hidden: true }));
  $("wait").hidden = false;
  $("result").classList.add("is-waiting");
  setStage(0);
  announce("Reading the question.");
}

function stopWait() {
  wait.timers.forEach(clearTimeout);
  wait.timers = [];
  wait.dealing = null;
  wait.active = false;
  $("wait").hidden = true;
  $("wait").replaceChildren();
  if ($("pile") || !$("record-panel")) { $("record-col").replaceChildren(); $("record-col").hidden = true; }
  $("result").classList.remove("is-waiting");
}

// 1. The question as read, as large chips, with the plan's own reading under them.
function waitPlan(plan) {
  $("spines")?.remove();
  $("plan-chips").replaceChildren(...B.planChips(plan, names).map((c) => el("li", {}, c)));
  $("plan-chips").hidden = false;
  if (plan.interpretation) { $("plan-reading").textContent = plan.interpretation; $("plan-reading").hidden = false; }
  setStage(1);
  announce("Searching the record.");
}

// 2. Each record found arrives as a card in the record's own form, into a pile: down the side on wide screens, along
// a shelf on phones. A running count while they arrive, then the summary. Finished at once by the next event.
function waitSources(ev) {
  $("spines")?.remove();
  if (wait.stage < 1) setStage(1);
  const list = ev.sources || [];
  if (!list.length) return;
  const deck = el("ul", {});
  const pile = el("div", { class: "wts-pile", tabindex: "0", "aria-label": "The records found", id: "pile" }, deck);
  if (wide()) {
    $("record-col").replaceChildren(el("p", { class: "wts-record__title" }, "The records found ",
      el("span", { id: "pile-count", "aria-hidden": "true" }, "")), pile);
    $("record-col").hidden = false;
  } else {
    $("wait").append(pile);
  }
  const found = $("found");
  found.hidden = false;
  let k = 0;
  const one = () => {
    const s = list[k++];
    const card = dom(B.card(s));
    card.style.setProperty("--wts-r", `${((k % 5) - 2) * 1.2}deg`);
    deck.append(card);
    found.replaceChildren(...B.foundWords(ev.evidence, k, list.length).map(dom));
    if ($("pile-count")) $("pile-count").textContent = String(k);
    pile.scrollTop = pile.scrollHeight;
    pile.scrollLeft = pile.scrollWidth;
  };
  const finish = () => {
    wait.dealing = null;
    while (k < list.length) one();
    pile.scrollTop = 0;
    pile.scrollLeft = 0;
  };
  if (reduced()) { finish(); return; }
  wait.dealing = finish;
  const tick = () => {
    if (wait.dealing !== finish) return;
    one();
    if (k < list.length) wait.timers.push(setTimeout(tick, 45)); else finish();
  };
  tick();
}

// 3. Writing from these records only: the text as it streams, and each card cited as its citation arrives.
function waitWriting(count) {
  if (wait.dealing) wait.dealing();
  setStage(2);
  $("only").textContent = `Writing from these ${count} records only. The AI may use these and nothing else.`;
  $("only").hidden = false;
  announce(`Writing the briefing from these ${count} records only.`);
}

function waitCited(n) {
  if (wait.lit.has(n)) return;
  wait.lit.add(n);
  const card = document.querySelector(`#pile .wts-card[data-source="${n}"]`);
  const pile = $("pile");
  if (!card || !pile) return;
  card.classList.add("is-cited");
  // brought into view inside the pile only: the page itself does not move
  pile.scrollTop = Math.max(0, card.offsetTop - pile.offsetTop - 40);
  pile.scrollLeft = Math.max(0, card.offsetLeft - pile.offsetLeft - 40);
}

// 4. Checking: the finished briefing is shown at once; the strip's counts run up through the real results, passage
// by passage, each quotation taking its highlight in turn. 5. The pile settles into the record beside the briefing.
function waitChecking(answer, sources) {
  if (wait.dealing) wait.dealing();
  setStage(3);
  announce("Checking every citation and quotation.");
  const segs = answer.segments || [];
  const c = B.counts(answer);
  renderAnswer(answer, sources, { running: { citations: 0, quotes: 0 } });
  const quotes = [...document.querySelectorAll("#briefing .wts-quote--checked")];
  if (reduced() || !segs.length) { wait.timers.push(setTimeout(finishWait, reduced() ? 400 : 0)); return; }
  for (const q of quotes) {
    q.classList.remove("wts-quote--checked");
    q.classList.add("is-pending");
  }
  const cited = segs.map((s, i) => [s, i]).filter(([s]) => s.status === "cited");
  const stepMs = Math.min(110, 2400 / Math.max(1, cited.length));
  let cit = 0, qt = 0;
  const nums = () => [...document.querySelectorAll("#strip .wts-check__num")];
  cited.forEach(([s, i], j) => wait.timers.push(setTimeout(() => {
    cit = Math.min(c.citations_matched, cit + (s.sources || []).length);
    for (const q of quotes.filter((x) => Number(x.dataset.seg) === i)) {
      q.classList.add("wts-quote--checked", "is-flash");
      q.classList.remove("is-pending");
      setTimeout(() => q.classList.remove("is-flash"), 380);
      qt = Math.min(c.quotes_found, qt + 1);
      for (const n of s.sources || []) {
        const card = document.querySelector(`#pile .wts-card[data-source="${n}"]`);
        if (card) card.classList.add("is-checked");
      }
    }
    const [a, b] = nums();
    if (a) a.textContent = String(cit);
    if (b) b.textContent = String(qt);
  }, 250 + j * stepMs)));
  wait.timers.push(setTimeout(finishWait, 250 + cited.length * stepMs + 500));
}

function finishWait() {
  const answer = shown.answer;
  stopWait();  // the stages above go; where the browser anchors scrolling, the text being read stays in place
  if (!answer) return;
  // the results all shown: the strip as it stays, every quotation highlighted (the text itself is not redrawn)
  for (const q of document.querySelectorAll("#briefing .wts-quote.is-pending")) {
    q.classList.remove("is-pending", "is-flash");
    q.classList.add("wts-quote--checked");
  }
  $("strip").replaceChildren(dom(B.strip(answer)));
  bindFirsts();
  announce(B.countsSentence(answer));
  if (wide()) openRecord(true);
  else { $("record-col").replaceChildren(); $("record-col").hidden = true; }
}

// A chip pressed while the checks' results are still running up: the results are shown at once.
function settleNow() {
  if (wait.active && wait.stage === 3) finishWait();
}

// ---------- the events ----------

// Published statements beside a briefing, from /api/statements: in the author's own words, set apart from the site's
// text, and never part of the briefing (they are not in its saved events, and the model never sees them).
async function showStatements(id) {
  shownBriefing = id;
  const res = await fetch(`/api/statements?page=${encodeURIComponent(`/b/${id}`)}`).catch(() => null);
  const data = res && res.ok ? await res.json().catch(() => null) : null;
  if (shownBriefing !== id) return;  // another question was asked meanwhile
  $("statements").replaceChildren(...((data && data.statements) || []).map(statementCard));
}

function statementCard(s) {
  const head = `statement-${s.id}`;
  const paras = s.text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  return el("section", { class: "statement", "aria-labelledby": head },
    el("h2", { class: "statement__head", id: head }, `Statement from ${s.name}, ${fmtDate(s.shown_on)}`),
    el("p", { class: "statement__role" }, s.role),
    el("div", { class: "statement__text" }, paras.map((p) => el("p", {}, p))),
    /^https?:\/\//i.test(s.link || "") ? el("p", { class: "statement__link" }, "The record it points to: ",
                el("a", { href: s.link, rel: "ugc nofollow noopener" }, s.link)) : null,
    el("p", { class: "statement__note" }, NOT_ENDORSED));
}

function note(text, error = false) {
  $("notes").append(el("p", { class: `note${error ? " note--error" : ""}` }, text));
}

let controller = null;

// One event of a briefing, as it is written (POST /api/ask, with the wait) or when a saved one is opened (/b/<id>,
// shown finished at once).
function handle(ev, st) {
  const live = wait.active;
  if (ev.type === "plan") {
    $("interpretation").textContent = ev.plan.interpretation || "";
    chips(ev.plan);
    st.countries = (ev.plan.filters || {}).countries || [];
    if (live) waitPlan(ev.plan);
  } else if (ev.type === "sources") {
    st.sources = ev.sources || [];
    showSources(st.sources);
    summary(ev.evidence);
    if (live) waitSources(ev);
  } else if (ev.type === "text") {
    if (live && wait.stage < 2) waitWriting(st.sources.length);
    if (!st.drafting) {
      st.cursor = el("span", { class: "wts-cursor", "aria-hidden": "true" });
      st.drafting = el("p", { class: "drafting" }, st.cursor);
      $("briefing").append(st.drafting);
    }
    st.cursor.before(ev.text);
  } else if (ev.type === "citation") {
    if (live && wait.stage < 2) waitWriting(st.sources.length);
    const s = st.sources[Number(ev.search_result_index)];
    if (live && s) waitCited(s.n);
  } else if (ev.type === "final") {
    if (ev.answer && !ev.answer.no_records) {
      if (live) waitChecking(ev.answer, st.sources);
      else { renderAnswer(ev.answer, st.sources); if (wide()) openRecord(false); }
    } else {
      if (live && !ev.answer && st.sources.length) {
        // sources only (or no briefing could be written): the records dealt in full, then the list of sources
        setStage(2);
        announce(`${st.sources.length} records found. No briefing was written.`);
        const end = () => wait.timers.push(setTimeout(wait.dealing ? end : stopWait, wait.dealing ? 100 : 900));
        end();
      } else if (live) {
        stopWait();
        announce("No records found.");
      }
      if (ev.answer) renderAnswer(ev.answer, st.sources);
      else if (st.drafting) st.drafting.remove();
    }
    if (ev.answer && !ev.answer.no_records) sisters($("question").textContent, st.countries);
    if (ev.note) note(ev.note);
    if (ev.cached && !st.saved) note("This briefing was written earlier for the same question and the same data.");
    if (ev.briefing_id) { saved(ev.briefing_id, st); showStatements(ev.briefing_id); }
  } else if (ev.type === "error") {
    if (live) stopWait();
    if (st.drafting) st.drafting.remove();
    announce("");
    note(ev.message, true);
  }
}

// A written briefing has its own address: shown in the address bar, with a link to report a problem with it.
function saved(id, st) {
  const path = `/b/${id}`;
  if (!st.saved && location.pathname !== path) history.replaceState(null, "", path);
  const address = `${location.origin}${path}`;
  $("notes").append(el("p", { class: "note share" },
    "This briefing’s address: ",
    el("a", { href: path }, address), el("br"),
    el("a", { href: `/corrections.html?page=${encodeURIComponent(path)}` }, "Report a problem with it")));
}

// The page's title is the site's own words and the date, never the reader's question, which the page shows as theirs.
function written(iso) {
  $("today").textContent = `Briefing of ${fmtDate(iso)}, written by AI from the sources below.`;
  document.title = `Briefing from UN records, ${fmtDate(iso)} | Where Countries Stand`;
}

function reset(question) {
  stopWait();
  closeSheet(false);
  $("home").hidden = true;
  document.body.classList.add("showing-result");
  $("result").hidden = false;
  $("question").textContent = question;
  $("asked").hidden = !question;
  $("interpretation").textContent = "";
  written(new Date().toISOString().slice(0, 10));
  for (const id of ["briefing", "strip", "details", "notes", "statements", "sources", "chips", "connector", "record-col"]) {
    $(id).replaceChildren();
  }
  $("record-col").hidden = true;
  shownBriefing = null;
  shown.answer = null;
  $("summary").hidden = true;
  $("sources-section").hidden = true;
  announce("");
  sisters("");
}

async function openSaved(id) {
  reset("");
  announce("Opening the saved briefing.");
  const res = await fetch(`/api/briefings/${encodeURIComponent(id)}`).catch(() => null);
  announce("");
  if (!res || !res.ok) {
    const detail = res ? await res.json().catch(() => ({})) : {};
    // 410: withdrawn, with the reason; 423: hidden while a concern about it is decided, with the note
    $("question").textContent = res && res.status === 410 ? "Briefing withdrawn"
      : res && res.status === 423 ? "Briefing hidden for now" : "Briefing not found";
    note(typeof detail.detail === "string" ? detail.detail : "This briefing could not be opened.", true);
    return;
  }
  const b = await res.json();
  reset(b.question);
  $("q").value = b.question;
  const mode = b.options && b.options.mode === "comprehensive" ? "comprehensive" : "summary";
  document.querySelector(`input[name=mode][value=${mode}]`).checked = true;
  written(b.at.slice(0, 10));
  const st = { drafting: null, saved: b.at.slice(0, 10), sources: [] };
  for (const ev of b.events) handle(ev, st);
}

async function ask(question, mode, sourcesOnly) {
  if (controller) controller.abort();
  controller = new AbortController();
  reset(question);
  startWait(sourcesOnly);
  if (sourcesOnly) $("today").textContent = `Sources found on ${fmtDate(new Date().toISOString().slice(0, 10))}, with no briefing written.`;
  // on a phone the question box fills the screen: the wait is brought into view, so it is seen
  const top = $("result").getBoundingClientRect().top;
  if (top > innerHeight * 0.6) $("result").scrollIntoView({ block: "start", behavior: reduced() ? "auto" : "smooth" });
  $("go").disabled = true;
  $("go-sources").disabled = true;
  const st = { drafting: null, sources: [] };
  try {
    const res = await fetch("/api/ask", {
      method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
      body: JSON.stringify({ question, mode, sources_only: sourcesOnly }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(typeof detail.detail === "string" ? detail.detail : `The request failed (${res.status}).`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        const data = chunk.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
        if (!data) continue;
        handle(JSON.parse(data), st);
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") { stopWait(); announce(""); note(e.message || String(e), true); }
  } finally {
    $("go").disabled = false;
    $("go-sources").disabled = false;
  }
}

function fromUrl() {
  const shared = location.pathname.match(/^\/b\/([A-Za-z0-9_-]+)$/);
  if (shared) { openSaved(shared[1]); return; }
  const params = new URLSearchParams(location.search);
  const q = params.get("q");
  if (!q) {
    stopWait();
    $("home").hidden = false;
    $("result").hidden = true;
    document.body.classList.remove("showing-result");
    return;
  }
  $("q").value = q;
  const mode = params.get("mode") === "comprehensive" ? "comprehensive" : "summary";
  document.querySelector(`input[name=mode][value=${mode}]`).checked = true;
  ask(q, mode, params.get("sources") === "1");
}

// The home page's proof is drawn without a script, its sentence set level with the passage it came from at the
// usual widths (styles.css). Where the script runs, the two highlights are made exactly level, and the rule between
// them drawn at their first line, whatever the width.
function alignProof() {
  const pair = document.querySelector("#proof .wts-proof__pair");
  if (!pair || pair.offsetParent === null) return;
  const left = pair.firstElementChild, quote = pair.querySelector(".wts-proof__said .wts-quote");
  const mark = pair.querySelector(".wts-passage mark");
  if (!left || !quote || !mark) return;
  if (!matchMedia("(min-width: 48rem)").matches) { left.style.removeProperty("padding-block-start"); return; }
  left.style.setProperty("padding-block-start", "0px");
  const q = quote.getClientRects()[0], m = mark.getClientRects()[0], box = pair.getBoundingClientRect();
  if (!q || !m) return;
  left.style.setProperty("padding-block-start", `${Math.max(0, m.top - q.top)}px`);
  pair.style.setProperty("--wts-join", `${m.top - box.top + m.height / 2}px`);
}

document.addEventListener("DOMContentLoaded", () => {
  alignProof();
  addEventListener("resize", alignProof);
  document.fonts?.ready.then(alignProof);
  loadCoverage();
  loadNames().then(fromUrl);
  // The question box is a textarea, so a long question wraps: Enter asks, Shift+Enter starts a new line.
  $("q").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    if (!$("go").disabled) $("ask").requestSubmit($("go"));  // as Enter in a one-line box: not while a briefing is written
  });
  // Two equal choices beside the question box: a briefing written by AI, or the sources alone, with no AI.
  $("ask").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("q").value.replace(/\s+/g, " ").trim();
    if (q.length < 3) return;
    const mode = document.querySelector("input[name=mode]:checked").value;
    const sourcesOnly = !!e.submitter && e.submitter.value === "sources";
    const params = new URLSearchParams({ q });
    if (mode !== "summary") params.set("mode", mode);
    if (sourcesOnly) params.set("sources", "1");
    history.pushState(null, "", `/?${params}`);
    ask(q, mode, sourcesOnly);
  });
  window.addEventListener("popstate", fromUrl);
});
