// Where Countries Stand: the ask page. Streams a briefing from /api/ask (server-sent events) and lays it out like
// the record: the question, the filters read from it, the briefing with numbered citations, then the sources.
// Every piece of text is set with textContent: nothing from the record or the model is parsed as HTML.
"use strict";

const $ = (id) => document.getElementById(id);
const BODY = { sc: "Security Council", gd: "General Assembly" };
let names = { countries: {}, groups: {} };

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

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00Z" : ""));
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

async function loadNames() {
  try {
    const [countries, groups] = await Promise.all([fetch("api/countries").then((r) => r.json()),
                                                   fetch("api/groups").then((r) => r.json())]);
    for (const c of countries) names.countries[c.code] = c.name;
    for (const g of groups) names.groups[g.key] = g.label;
  } catch (e) { /* the codes are shown instead */ }
}

async function loadCoverage() {
  const list = $("coverage");
  try {
    const meta = await fetch("api/meta").then((r) => r.json());
    const c = meta.coverage || {};
    const span = (x) => (x ? `${x.from.slice(0, 4)}–${fmtDate(x.to)}` : "not loaded");
    list.replaceChildren(
      ...[["General Debate", c.gd], ["Security Council debates", c.sc], ["General Assembly votes", c.votes]].map(
        ([label, x]) => el("li", {}, el("span", {}, label), el("span", { class: "dots" }), el("span", {}, span(x)))));
    if (!meta.briefings) {
      $("go").textContent = "Find sources";
    }
  } catch (e) {
    list.replaceChildren(el("li", {}, "The record's coverage could not be loaded."));
  }
}

// ---- the filters, as chips ------------------------------------------------------------------------------------

function chips(plan) {
  const f = plan.filters || {};
  const out = [];
  for (const s of f.sources || []) out.push(el("li", { class: `chip chip--${s === "sc" ? "sc" : "ga"}` }, BODY[s] || s));
  if (!(f.sources || []).length) out.push(el("li", { class: "chip" }, "Security Council and General Assembly"));
  if (f.date_from || f.date_to) {
    out.push(el("li", { class: "chip" }, `${f.date_from ? f.date_from.slice(0, 4) : "1946"}–${f.date_to ? f.date_to.slice(0, 4) : "today"}`));
  }
  for (const c of f.countries || []) out.push(el("li", { class: "chip" }, names.countries[c] || c));
  for (const g of f.groups || []) out.push(el("li", { class: "chip" }, names.groups[g] || g));
  if (!(f.countries || []).length && !(f.groups || []).length) out.push(el("li", { class: "chip" }, "All countries"));
  $("chips").replaceChildren(...out);
}

function summary(evidence, sources) {
  const p = $("summary");
  if (!evidence || !evidence.records) { p.hidden = true; return; }
  const parts = [el("strong", {}, `${evidence.records} statements`), " from ",
                 el("strong", {}, `${evidence.countries} countries and groups`), " at ",
                 el("strong", {}, `${evidence.meetings} meetings`)];
  if (evidence.date_from) parts.push(`, ${fmtDate(evidence.date_from)} to ${fmtDate(evidence.date_to)}`);
  if (evidence.votes) parts.push(" · ", el("strong", {}, `${evidence.votes} recorded vote${evidence.votes > 1 ? "s" : ""}`));
  parts.push(" · Counted from the record, not written by the model.");
  p.replaceChildren(...parts);
  p.hidden = false;
}

// ---- sources ----------------------------------------------------------------------------------------------------

function sourceCard(s) {
  if (s.kind === "vote") {
    const t = s.totals || {};
    return el("li", { class: "source source--vote", id: `s${s.n}` },
      el("p", { class: "source__meta" }, `General Assembly · recorded vote · ${fmtDate(s.date)}`),
      el("p", { class: "source__speaker" }, el("b", {}, `Resolution ${s.resolution}`), ` ${s.title || ""}`),
      el("p", { class: "procedural" }, `${t.yes} in favour, ${t.no} against, ${t.abstain} abstaining`),
      el("p", { class: "source__links" }, el("a", { href: s.url, rel: "noopener" }, "UN Digital Library record")));
  }
  const meta = [s.body, s.meeting, s.date_precision === "session" ? null : fmtDate(s.date), s.symbol]
    .filter(Boolean).join(" · ");
  const who = [el("b", {}, s.speaker_name || "Speaker")];
  if (s.country_name) who.push(` (${s.country_name})`);
  if (s.on_behalf_of) who.push(`, on behalf of the ${s.on_behalf_of}`);
  if (s.language) who.push(" ", el("i", {}, `(spoke in ${s.language})`));
  who.push(":");
  const words = (s.text || "").split(/\s+/);
  const short = words.slice(0, 90).join(" ") + (words.length > 90 ? " …" : "");
  const links = [];
  if (s.language) links.push(el("span", { class: "translated" }, "English text: UN translation."), " · ");
  if (s.text_source === "delegation") links.push(el("span", { class: "translated" }, "Text issued by the delegation, as prepared; not yet the official record."), " · ");
  if (s.text_source === "transcript") links.push(el("span", { class: "translated" }, "AI transcript of the UN's English interpretation; not yet the official record."), " · ");
  if (s.ocr) links.push(el("span", { class: "translated" }, "Text recognised from a scanned record."), " · ");
  links.push(el("a", { href: s.url, rel: "noopener" }, s.symbol ? `Read ${s.symbol}` : "Read the source"));
  const card = el("li", { class: `source${s.body === "General Assembly" ? " source--ga" : ""}`, id: `s${s.n}` },
    el("p", { class: "source__meta" }, meta + (s.agenda ? ` · ${s.agenda}` : "")),
    el("p", { class: "source__speaker" }, who),
    el("blockquote", {}, short));
  if (words.length > 90) {
    card.append(el("details", { class: "source__more" }, el("summary", {}, "Full statement"), el("p", {}, s.text)));
  }
  card.append(el("p", { class: "source__links" }, links));
  return card;
}

function showSources(list) {
  $("sources").replaceChildren(...list.map(sourceCard));
  $("sources-section").hidden = !list.length;
}

// ---- the briefing ----------------------------------------------------------------------------------------------

// "**bold**" in the model's text becomes <strong>; a line starting "#" becomes a heading. Nothing else is markup.
function inline(text) {
  const out = [];
  text.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    if (part.startsWith("**") && part.endsWith("**")) out.push(el("strong", {}, part.slice(2, -2)));
    else if (part) out.push(part);
  });
  return out;
}

function renderAnswer(answer) {
  const box = $("briefing");
  box.replaceChildren();
  let p = el("p");
  box.append(p);
  for (const seg of answer.segments || []) {
    const pieces = seg.text.split(/\n\s*\n/);
    pieces.forEach((piece, i) => {
      if (i > 0) { p = el("p"); box.append(p); }
      const heading = piece.match(/^\s*#+\s*(.+)$/m);
      if (heading && piece.trim().startsWith("#")) {
        const h = el("h3", {}, heading[1]);
        box.insertBefore(h, p);
        piece = piece.replace(/^\s*#+\s*.+$/m, "").trimStart();
      }
      if (!piece) return;
      const cls = [seg.status === "unsupported" ? "unsupported" : "", (seg.loaded || []).length ? "loaded" : "",
                   (seg.misquotes || []).length ? "misquote" : ""].filter(Boolean).join(" ");
      p.append(cls ? el("span", { class: cls }, inline(piece)) : el("span", {}, inline(piece)));
    });
    for (const n of seg.sources || []) {
      p.append("⁠", el("a", { class: "cite", href: `#s${n}`, "aria-label": `Source ${n}` }, `[${n}]`));
    }
  }
  for (const node of [...box.children]) if (!node.textContent.trim()) node.remove();
  const marks = [];
  if (answer.unsupported_segments) marks.push("Sentences in grey have no source and are the model's own connecting words.");
  if (answer.loaded_words) marks.push("Words underlined with dots are judgemental terms in the model's voice.");
  if (answer.quotes_unverified) marks.push("Quotations underlined with a wave were not found word for word in their source.");
  $("marks").textContent = marks.join(" ");
  $("marks").hidden = !marks.length;
}

function note(text, error = false) {
  $("notes").append(el("p", { class: `note${error ? " note--error" : ""}` }, text));
}

// ---- asking -----------------------------------------------------------------------------------------------------

let controller = null;

async function ask(question, mode, sourcesOnly) {
  if (controller) controller.abort();
  controller = new AbortController();
  $("home").hidden = true;
  $("result").hidden = false;
  $("question").textContent = question;
  $("interpretation").textContent = "";
  $("today").textContent = new Date().toLocaleDateString("en-GB");
  document.title = `${question} · Where Countries Stand`;
  for (const id of ["briefing", "notes", "sources", "chips"]) $(id).replaceChildren();
  $("summary").hidden = true;
  $("marks").hidden = true;
  $("sources-section").hidden = true;
  $("status").textContent = "Reading the question…";
  $("go").disabled = true;
  let drafting = null;
  try {
    const res = await fetch("api/ask", {
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
        const ev = JSON.parse(data);
        if (ev.type === "plan") {
          $("interpretation").textContent = ev.plan.interpretation || "";
          chips(ev.plan);
          $("status").textContent = "Searching the record…";
        } else if (ev.type === "sources") {
          showSources(ev.sources || []);
          summary(ev.evidence, ev.sources);
          $("status").textContent = (ev.sources || []).length ? "Writing the briefing from these sources…" : "";
        } else if (ev.type === "text") {
          if (!drafting) { drafting = el("p", { class: "drafting" }); $("briefing").append(drafting); }
          drafting.append(ev.text);
        } else if (ev.type === "final") {
          $("status").textContent = "";
          if (ev.answer) renderAnswer(ev.answer);
          else if (drafting) drafting.remove();
          if (ev.note) note(ev.note);
          if (ev.cached) note("This briefing was written earlier for the same question and the same data.");
        } else if (ev.type === "error") {
          $("status").textContent = "";
          note(ev.message, true);
        }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") { $("status").textContent = ""; note(e.message || String(e), true); }
  } finally {
    $("go").disabled = false;
  }
}

function fromUrl() {
  const params = new URLSearchParams(location.search);
  const q = params.get("q");
  if (!q) { $("home").hidden = false; $("result").hidden = true; return; }
  $("q").value = q;
  const mode = params.get("mode") === "comprehensive" ? "comprehensive" : "summary";
  document.querySelector(`input[name=mode][value=${mode}]`).checked = true;
  const sourcesOnly = params.get("sources") === "1";
  document.querySelector("input[name=sources_only]").checked = sourcesOnly;
  ask(q, mode, sourcesOnly);
}

document.addEventListener("DOMContentLoaded", () => {
  loadCoverage();
  loadNames().then(fromUrl);
  $("ask").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("q").value.trim();
    if (q.length < 3) return;
    const mode = document.querySelector("input[name=mode]:checked").value;
    const sourcesOnly = document.querySelector("input[name=sources_only]").checked;
    const params = new URLSearchParams({ q });
    if (mode !== "summary") params.set("mode", mode);
    if (sourcesOnly) params.set("sources", "1");
    history.pushState(null, "", `?${params}`);
    ask(q, mode, sourcesOnly);
  });
  window.addEventListener("popstate", fromUrl);
});
