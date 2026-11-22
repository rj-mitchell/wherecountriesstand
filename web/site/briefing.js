// Where Countries Stand: a checked briefing, as the family shows one (family/BRIEFINGS.md in the charity's repository,
// with the kit's /family/briefing.css). Pure functions from a briefing's answer and sources to a tree of elements:
// the check strip, the text with record quotations, source chips and marks for text with no source, the record slip
// beside it, the cards of the wait, and the check details at the end. app.js turns the trees into the page; the tests
// in web/tests/ turn them into HTML. Every piece of text is a text node: nothing from the record or the model is ever
// parsed as HTML.
(function (root, make) {
  const api = make();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WCSBriefing = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------- elements as plain trees ----------

  // h("span", { class: "x" }, "text", h(...)): a tree node. Falsy children are left out.
  function h(tag, attrs, ...children) {
    return { tag, attrs: attrs || {}, children: children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false && c !== "") };
  }

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ESC[c]);

  // The tree as HTML (for the tests, and nowhere on the page).
  function toHTML(node) {
    if (typeof node === "string" || typeof node === "number") return esc(node);
    const attrs = Object.entries(node.attrs)
      .filter(([, v]) => v !== null && v !== undefined && v !== false)
      .map(([k, v]) => (v === true ? ` ${k}` : ` ${k}="${esc(v)}"`)).join("");
    return `<${node.tag}${attrs}>${node.children.map(toHTML).join("")}</${node.tag}>`;
  }

  // The tree as elements, every string a text node.
  function toDOM(node, doc) {
    doc = doc || document;
    if (typeof node === "string" || typeof node === "number") return doc.createTextNode(String(node));
    const el = doc.createElement(node.tag);
    for (const [k, v] of Object.entries(node.attrs)) {
      if (v === null || v === undefined || v === false) continue;
      el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of node.children) el.append(toDOM(c, doc));
    return el;
  }

  // The text a tree reads as (for the tests).
  function textOf(node) {
    return typeof node === "string" || typeof node === "number" ? String(node) : node.children.map(textOf).join("");
  }

  // ---------- words ----------

  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December"];
  function longDate(iso) {
    if (!iso) return "";
    const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
    return d && m ? `${d} ${MONTHS[m - 1]} ${y}` : String(y || "");
  }
  const plural = (n, one, many) => (n === 1 ? one : many);

  // A source's date as the site writes it: in full, or the year where the record gives only the session.
  function sourceDate(s) {
    return s.date_precision === "session" ? String(s.date || "").slice(0, 4) : longDate(s.date);
  }

  // The words in a chip: who spoke (a statement on behalf of a group is the group's, decision 7), the body, the date.
  // A vote's kind and body: a Security Council vote (decision 30) is not a "recorded vote" in its records.
  const voteWords = (s) => (s.body === "Security Council" ? ["Vote", "Security Council"] : ["Recorded vote", "General Assembly"]);

  function chipLabel(s) {
    if (!s) return "";
    if (s.kind === "vote") return [...voteWords(s), longDate(s.date)].join(", ");
    const who = s.on_behalf_of || s.country_name || s.speaker_name || "Speaker";
    return [who, s.body, sourceDate(s)].filter(Boolean).join(", ");
  }

  // ---------- quotations, as the verifier finds them (wts-core checks.verify) ----------

  // double quotes, straight or curly, of six characters or more; or single curly quotes not closing a word
  const QUOTE = /["“]([^"“”\n]{6,}?)["”]|‘([^‘’\n]{6,}?)’(?![a-z])/g;
  const ELLIPSIS = /\s*(?:\.\.\.|…|\[[^\]]{0,40}\])\s*/;
  const wordsIn = (s) => s.toLowerCase().replace(/[’']/g, "").split(/[^a-z0-9]+/).filter(Boolean);

  // Each segment's quotations, at their places in the segment: [{start, end, quote, missing, endsHere, sources}].
  // A quotation of a word or two is a term, not a quotation, as the verifier counts them. Its sources are the
  // verifier's: those of the passages it is part of, or of the cited passage straight after it (within three
  // characters), which is how a quotation in a lead-in is checked against the citation that follows.
  function quotations(segments) {
    const starts = [];
    let pos = 0;
    for (const s of segments) { starts.push(pos); pos += (s.text || "").length; }
    const full = segments.map((s) => s.text || "").join("");
    const out = segments.map(() => []);
    for (const m of full.matchAll(QUOTE)) {
      const quote = m[1] || m[2];
      if (wordsIn(quote).length < 3) continue;
      const a = m.index, b = a + m[0].length;
      const sources = [];
      segments.forEach((s, i) => {
        const s0 = starts[i], s1 = s0 + (s.text || "").length;
        if ((s0 < b && a < s1) || (s0 >= b && s0 - b <= 3 && (s.sources || []).length)) {
          for (const n of s.sources || []) if (!sources.includes(n)) sources.push(n);
        }
      });
      segments.forEach((s, i) => {
        const s0 = starts[i], s1 = s0 + (s.text || "").length;
        if (!(s0 < b && a < s1)) return;
        const start = Math.max(a, s0) - s0, end = Math.min(b, s1) - s0;
        const missing = (s.misquotes || []).some((x) => x.start < end && start < x.end);
        out[i].push({ start, end, quote, missing, endsHere: b <= s1, sources });
      });
    }
    return out;
  }

  const flagged = (seg) => seg.status === "unsupported" || seg.status === "invalid";
  const isBlank = (seg) => seg.status === "connective" && !(seg.text || "").trim();

  // The check strip's counts: the verifier's for citations and quotations, and the passages shown as having no
  // source, counted on the segments as shown (wts-core's check_counts, after quotes_beside, on the server).
  function counts(answer) {
    const total = Number(answer.citations_total || 0), quotes = Number(answer.quotes_total || 0);
    return {
      citations_matched: total - Number(answer.citations_invalid || 0), citations_total: total,
      quotes_found: quotes - Number(answer.quotes_unverified || 0), quotes_total: quotes,
      no_source: (answer.segments || []).filter(flagged).length,
    };
  }

  // Where the strip's "Show the first" links go: the first passage with no source, the first whose citation was
  // removed, and the first quotation not found.
  function firsts(answer) {
    const segs = answer.segments || [];
    const marks = quotations(segs);
    const nosource = segs.findIndex(flagged), invalid = segs.findIndex((s) => s.status === "invalid");
    let missing = null;
    marks.some((qs, i) => qs.some((q) => (q.missing ? ((missing = `misquote-${i}-${q.start}`), true) : false)));
    return { nosource: nosource < 0 ? null : `nosource-${nosource}`, invalid: invalid < 0 ? null : `nosource-${invalid}`, missing };
  }

  // ---------- the briefing's text ----------

  // Whether a cited passage shows its chips: two runs next to each other from the same sources show one chip,
  // after the second.
  function chipsShown(segments) {
    return segments.map((seg, i) => {
      if (seg.status !== "cited" || !(seg.sources || []).length) return false;
      let j = i + 1;
      while (j < segments.length && isBlank(segments[j])) j++;
      const next = segments[j];
      return !(next && next.status === "cited" && (next.sources || []).join() === seg.sources.join());
    });
  }

  // The briefing as lines of pieces: {seg, start, end} of a segment's text, and {chips, seg} where a cited passage's
  // chips go (after its last words, on the line they are on).
  function lines(segments) {
    const out = [[]];
    const shows = chipsShown(segments);
    segments.forEach((seg, i) => {
      let off = 0, last = null;
      (seg.text || "").split("\n").forEach((part, k) => {
        if (k > 0) out.push([]);
        if (part) { out[out.length - 1].push({ seg: i, start: off, end: off + part.length }); last = out[out.length - 1]; }
        off += part.length + 1;
      });
      if (shows[i]) (last || out[out.length - 1]).push({ chips: seg.sources, seg: i });
    });
    return out;
  }

  // A chip's name is its own words, read as "Source 3 Italy, Security Council, 14 November 2025. Show the passage":
  // what it shows is part of what it is called (WCAG 2.5.3), so it has no aria-label that would differ from it.
  // data-wts-conversion: the family kit's mark for a source opened, inert unless measurement is on (decision 28).
  function chip(n, source, seg) {
    const label = chipLabel(source) || `Source ${n}`;
    return h("button", { type: "button", class: "wts-chip", "data-source": n, "data-seg": seg, "data-wts-conversion": "source_opened" },
      h("span", { class: "wts-chip__in" }, h("span", { class: "wts-chip__n" }, h("span", { class: "wts-vh" }, "Source"), ` ${n}`),
        " ", h("span", {}, label)),
      h("span", { class: "wts-vh" }, ". Show the passage"));
  }

  const LOADED_NOTE = " (a judgemental word in the AI’s own voice)";

  // One piece of a segment's text, with its quotations, judgemental words and bold, as nodes.
  function pieceNodes(seg, i, start, end, quotes, ctx) {
    const text = seg.text.slice(start, end);
    const drop = new Set();
    const bold = [];
    for (const m of text.matchAll(/\*\*([^*]+)\*\*/g)) {
      bold.push([m.index + 2, m.index + 2 + m[1].length]);
      [m.index, m.index + 1, m.index + m[0].length - 2, m.index + m[0].length - 1].forEach((x) => drop.add(x));
    }
    const local = (a, b) => [Math.max(a, start) - start, Math.min(b, end) - start];
    const qs = quotes.filter((q) => q.start < end && start < q.end).map((q) => ({ ...q, range: local(q.start, q.end) }));
    const loaded = (seg.loaded || []).filter((l) => l.start < end && start < l.end).map((l) => local(l.start, l.end));
    const cuts = new Set([0, text.length]);
    for (const [a, b] of [...bold, ...loaded, ...qs.map((q) => q.range)]) { cuts.add(a); cuts.add(b); }
    const at = [...cuts].sort((x, y) => x - y);
    const runs = [];
    for (let k = 0; k + 1 < at.length; k++) {
      const a = at[k], b = at[k + 1];
      let t = "";
      for (let x = a; x < b; x++) if (!drop.has(x)) t += text[x];
      if (!t) continue;
      const q = qs.findIndex((r) => r.range[0] <= a && b <= r.range[1]);
      runs.push({ t, quote: q, bold: bold.some(([s, e]) => s <= a && b <= e), loaded: loaded.some(([s, e]) => s <= a && b <= e) });
    }
    const runNode = (r) => {
      let n = r.t;
      if (r.loaded) n = h("span", { class: "loaded" }, n, h("span", { class: "wts-vh" }, LOADED_NOTE));
      return r.bold ? h("strong", {}, n) : n;
    };
    const out = [];
    for (let k = 0; k < runs.length;) {
      const q = runs[k].quote;
      let j = k;
      while (j < runs.length && runs[j].quote === q) j++;
      const inner = runs.slice(k, j).map(runNode);
      if (q < 0) out.push(...inner);
      else out.push(...[].concat(quoteNode(qs[q], seg, i, inner, ctx)));
      k = j;
    }
    return out;
  }

  // A quotation: the record's own words where the verifier found them in the passage's source, or marked where it
  // did not. One found in text with no source is left as text: nothing says whose words they are.
  function quoteNode(q, seg, i, inner, ctx) {
    const n = q.sources[0];
    const where = n ? `source ${n}` : "its source";
    if (q.missing) {
      const id = `misquote-${i}-${q.start}`;
      const fresh = !ctx.missingIds.has(id);
      ctx.missingIds.add(id);
      return h("span", { class: "wts-quote wts-quote--missing", id: fresh ? id : null, "data-seg": i },
        inner, q.endsHere && h("span", { class: "wts-quote__tick", "aria-hidden": "true" }, "Not found word for word"),
        q.endsHere && h("span", { class: "wts-vh" }, ` (quotation not found word for word in ${where})`));
    }
    if (!n || flagged(seg)) return inner;
    return h("span", { class: "wts-quote wts-quote--checked", "data-seg": i, "data-source": n },
      inner, q.endsHere && h("span", { class: "wts-quote__tick", "aria-hidden": "true" }, "✓ word for word"),
      q.endsHere && h("span", { class: "wts-vh" }, ` (quotation, found word for word in ${where})`));
  }

  // The briefing's text as blocks (headings, paragraphs and lists): every cited passage followed by its chips,
  // every passage with no source marked, the first with "No source" in words.
  function briefingBlocks(answer, sourcesByN) {
    const segments = answer.segments || [];
    const quotes = quotations(segments);
    const ctx = { missingIds: new Set(), firstNoSource: segments.findIndex(flagged) };
    const lastPiece = new Map();
    const all = lines(segments);
    all.forEach((line, li) => line.forEach((p, pi) => { if (p.chips === undefined) lastPiece.set(p.seg, `${li}:${pi}`); }));
    const seen = new Set();
    const inlineOf = (line, li) => {
      const out = [];
      line.forEach((p, pi) => {
        if (p.chips) {
          out.push("⁠", ...p.chips.map((n) => chip(n, sourcesByN[n], p.seg)));
          return;
        }
        const seg = segments[p.seg];
        let start = p.start;
        if (pi === 0) {  // a list item's or heading's marker is not text
          const lead = seg.text.slice(p.start, p.end).match(/^\s*(?:[-*•]|#+)\s+/);
          if (lead) start += lead[0].length;
        }
        if (!flagged(seg)) { out.push(...pieceNodes(seg, p.seg, start, p.end, quotes[p.seg], ctx)); return; }
        // the space a passage ends with goes after its mark, so that the underline stops at its last word and the
        // "No source" tag never touches the next word
        const trail = /\S(\s+)$/.exec(seg.text.slice(start, p.end));
        const end = trail ? p.end - trail[1].length : p.end;
        const nodes = pieceNodes(seg, p.seg, start, end, quotes[p.seg], ctx);
        const first = !seen.has(p.seg);
        seen.add(p.seg);
        const last = lastPiece.get(p.seg) === `${li}:${pi}`;
        const why = seg.status === "invalid" ? "its citation did not match the source and was removed" : "no source";
        out.push(h("span", { class: "wts-nosource", id: first ? `nosource-${p.seg}` : null, "data-seg": p.seg },
          nodes,
          last && p.seg === ctx.firstNoSource && h("span", { class: "wts-nosource__tag", "aria-hidden": "true" }, "No source"),
          last && h("span", { class: "wts-vh" }, ` (${why})`)));
        if (trail) out.push(" ");
      });
      return out;
    };
    const blocks = [];
    let para = null, list = null;
    all.forEach((line, li) => {
      const plain = line.map((p) => (p.chips ? "" : segments[p.seg].text.slice(p.start, p.end))).join("").trim();
      const cites = line.some((p) => p.chips);
      if (!plain && !cites) { para = null; list = null; return; }
      if (!cites && (/^#+\s/.test(plain) || /^\*\*[^*]+\*\*:?$/.test(plain))) {
        blocks.push(h("h2", { class: "briefing-head" }, plain.replace(/^#+\s*/, "").replace(/^\*\*|\*\*:?$/g, "")));
        para = null; list = null;
        return;
      }
      if (/^[-*•]\s/.test(plain)) {
        if (!list) { list = h("ul", {}); blocks.push(list); }
        list.children.push(h("li", {}, inlineOf(line, li)));
        para = null;
        return;
      }
      list = null;
      if (!para) { para = h("p", {}); blocks.push(para); } else para.children.push(" ");
      para.children.push(...inlineOf(line, li));
    });
    return blocks;
  }

  // The sources the chips point to, in order of their numbers: what the record beside the briefing steps through.
  function citedSources(answer) {
    const ns = new Set();
    for (const s of answer.segments || []) if (s.status === "cited") for (const n of s.sources || []) ns.add(n);
    return [...ns].sort((a, b) => a - b);
  }

  // The words quoted from each source, in pieces split at an ellipsis, for highlighting in its passage.
  function quotedFrom(answer) {
    const segs = answer.segments || [];
    const shows = chipsShown(segs);
    const out = {};
    quotations(segs).forEach((qs, i) => {
      if (flagged(segs[i])) return;
      for (const q of qs) {
        if (q.missing || !q.endsHere) continue;
        // the chip a quotation is shown with: on its own passage, or the next that shows one
        let at = i;
        while (at < segs.length && !shows[at]) at++;
        for (const n of q.sources) (out[n] = out[n] || []).push({ seg: at, parts: quoteParts(q.quote) });
      }
    });
    return out;
  }
  const quoteParts = (q) => q.split(ELLIPSIS).map((p) => p.trim()).filter((p) => wordsIn(p).length >= 2);

  // ---------- the check strip, and the check details at the end ----------

  // One count of the strip. ``shown``: the number shown now (it runs up during the wait); null shows "counted last".
  function stripItem({ shown, total, words, problem, first, pending }) {
    return h("li", { class: "wts-check__item" },
      h("span", { class: "wts-check__count" },
        h("span", { class: "wts-check__glyph", "aria-hidden": "true" }, pending ? "…" : problem ? "!" : "✓"),
        !pending && h("span", { class: "wts-check__num" }, String(shown)),
        total !== null && h("span", {}, `of ${total}`),
        problem && !pending && h("span", { class: "wts-check__badge" }, "To check")),
      h("span", {}, pending ? `${words}: counted last` : words,
        problem && !pending && first && [". ", h("a", { class: "wts-check__first", href: `#${first}` }, "Show the first")]));
  }

  // The strip: the three counts in words, under the title that names the record. ``running``: the numbers to show
  // while the checks' results run up ({citations, quotes}, the count with no source "counted last"); the list is then
  // hidden from screen readers, which hear the counts once, at the end.
  function strip(answer, running) {
    const c = counts(answer), f = firsts(answer);
    const loaded = Number(answer.loaded_words || 0);
    return h("section", { class: "wts-check", "aria-labelledby": "check-title", id: "check" },
      h("div", { class: "wts-check__head" },
        h("h2", { class: "wts-check__title", id: "check-title" }, "Checked against the UN record"),
        h("a", { class: "wts-check__more", href: "#check-details" }, "How it was checked")),
      h("ul", { class: "wts-check__list", "aria-hidden": running ? "true" : null },
        stripItem({ shown: running ? running.citations : c.citations_matched, total: c.citations_total,
          words: plural(c.citations_total, "citation matched to the record", "citations matched to the record"),
          problem: !running && c.citations_matched < c.citations_total, first: f.invalid }),
        stripItem({ shown: running ? running.quotes : c.quotes_found, total: c.quotes_total,
          words: plural(c.quotes_total, "quotation found word for word", "quotations found word for word"),
          problem: !running && c.quotes_found < c.quotes_total, first: f.missing }),
        stripItem({ shown: c.no_source, total: null, pending: !!running,
          words: plural(c.no_source, "sentence without a source", "sentences without a source"),
          problem: c.no_source > 0, first: f.nosource })),
      loaded > 0 && !running && h("p", { class: "check-note" },
        `${loaded} ${plural(loaded, "judgemental word", "judgemental words")} in the AI’s own voice ${plural(loaded, "is", "are")} underlined: check ${plural(loaded, "it", "them")} against the record.`));
  }

  // The counts in one sentence, as screen readers hear them once the checks are shown.
  function countsSentence(answer) {
    const c = counts(answer);
    return `Checked against the UN record: ${c.citations_matched} of ${c.citations_total} ${plural(c.citations_total, "citation", "citations")} matched to the record, ` +
      `${c.quotes_found} of ${c.quotes_total} ${plural(c.quotes_total, "quotation", "quotations")} found word for word, ` +
      `${c.no_source ? `${c.no_source} ${plural(c.no_source, "sentence", "sentences")}` : "no sentences"} without a source.`;
  }

  // "How this briefing was checked", at the end: the verifier's own counts, and what each mark means. It is the end
  // of a finished briefing, so it carries the family kit's mark for a briefing read, with the briefing's id
  // (``briefingId``) so that the same briefing drawn again is counted once; inert unless measurement is on
  // (decision 28).
  function details(answer, found, briefingId) {
    const total = Number(answer.citations_total || 0), invalid = Number(answer.citations_invalid || 0);
    const quotes = Number(answer.quotes_total || 0), missing = Number(answer.quotes_unverified || 0);
    const unsupported = Number(answer.unsupported_segments || 0), loaded = Number(answer.loaded_words || 0);
    const beside = (answer.segments || []).filter((s) => s.beside).length;
    const points = [
      found ? `The AI was given only the ${found} ${plural(found, "record", "records")} found for the question, listed under Sources, and may use nothing else.` : null,
      `Each of the ${total} ${plural(total, "citation", "citations")} was checked against the text of its source: ` +
        (invalid ? `${invalid} did not match, so ${plural(invalid, "it was", "they were")} removed, and the words ${plural(invalid, "it was", "they were")} attached to are marked “No source”: check the record before relying on them.` : "all matched."),
      quotes ? `Each of the ${quotes} ${plural(quotes, "quotation", "quotations")} was looked for word for word in its source: ` +
        (missing ? `${missing} could not be found and ${plural(missing, "is", "are")} marked “Not found word for word” with a wavy underline.` : "all were found, and each is marked “✓ word for word”.") : null,
      unsupported ? `${unsupported} ${plural(unsupported, "sentence", "sentences")} that no source supports ${plural(unsupported, "is", "are")} marked with a dotted underline, and the first with “No source”. Short joining phrases are not counted.`
        : "No sentence was found without a source. Short joining phrases are not counted.",
      beside ? `${beside === 1 ? "A quotation" : `${beside} quotations`} found word for word in the source cited straight after ${beside === 1 ? "it is" : "them are"} shown as that source’s quotation; the words around ${beside === 1 ? "it keep their" : "them keep their"} marks.` : null,
      loaded ? `${loaded} ${plural(loaded, "judgemental word", "judgemental words")} in the AI’s own voice ${plural(loaded, "is", "are")} underlined.` : null,
      "Some errors can still get through: check the record before relying on a point.",
    ];
    return h("section", { class: "check-details", id: "check-details", "aria-labelledby": "check-details-head",
      "data-wts-conversion": "briefing_read", "data-wts-briefing": briefingId || null },
      h("h2", { id: "check-details-head" }, "How this briefing was checked"),
      h("ul", {}, points.filter(Boolean).map((p) => h("li", {}, p))),
      h("p", {}, h("a", { href: "/methodology.html#checked" }, "How every briefing is checked, on the methodology page")));
  }

  // ---------- the record: the slip beside the briefing, and the cards of the wait ----------

  // Lower-case words with a map back to the text, as the verifier compares a quotation with its source.
  function wordMap(text) {
    let out = "", at = [], space = true;
    for (let i = 0; i < text.length; i++) {
      const c = text[i].toLowerCase();
      if (c === "'" || c === "’" || c === "‘") continue;
      if (/[a-z0-9]/.test(c)) { out += c; at.push(i); space = false; }
      else if (!space) { out += " "; at.push(i); space = true; }
    }
    return { out, at };
  }

  // Where each quoted piece is in the source's text: [start, end, now], with ``now`` for those of the passage chosen.
  function highlights(text, quoted, nowSeg) {
    const map = wordMap(text);
    const spans = [];
    for (const q of quoted || []) {
      for (const part of q.parts) {
        const w = wordsIn(part).join(" ");
        if (!w) continue;
        let i = (" " + map.out + " ").indexOf(` ${w} `);
        if (i < 0) i = (" " + map.out).indexOf(` ${w}`);  // the last word may run on in the record
        if (i < 0) continue;
        const a = map.at[i], b = map.at[i + w.length - 1] + 1;
        if (spans.some(([x, y]) => a < y && x < b)) continue;
        spans.push([a, b, q.seg === nowSeg]);
      }
    }
    return spans.sort((x, y) => x[0] - y[0]);
  }

  // The part of a long statement around what is quoted from it, cut at sentence ends.
  function excerpt(text, spans, before = 420, after = 560) {
    if (!spans.length) {
      if (text.length <= 900) return { from: 0, to: text.length };
      const end = text.indexOf(". ", 700);
      return { from: 0, to: end > 0 && end < 1000 ? end + 1 : 900 };
    }
    let a = Math.max(0, spans[0][0] - before), b = Math.min(text.length, spans[spans.length - 1][1] + after);
    if (a > 0) {  // from the start of a sentence or a paragraph
      const stop = text.lastIndexOf(". ", a), para = text.lastIndexOf("\n", a);
      const cut = Math.max(stop >= 0 ? stop + 2 : -1, para >= 0 ? para + 1 : -1);
      if (cut >= 0 && a - cut < 240) a = cut;
    }
    if (b < text.length) {  // to the end of one
      const stop = text.indexOf(". ", b), para = text.indexOf("\n", b);
      const cut = Math.min(stop >= 0 ? stop + 1 : Infinity, para >= 0 ? para : Infinity);
      if (cut !== Infinity && cut - b < 240) b = cut;
    }
    if (a > 0 && /^\s/.test(text.slice(a))) a += text.slice(a).match(/^\s*/)[0].length;
    return { from: a, to: b };
  }

  function passageNodes(text, spans, short) {
    const { from, to } = short ? excerpt(text, spans, 160, 420) : excerpt(text, spans);
    const out = [];
    if (from > 0) out.push("… ");
    let at = from;
    for (const [a, b, now] of spans) {
      if (a < from || b > to) continue;
      out.push(text.slice(at, a), h("mark", { class: now ? "wts-now" : null }, text.slice(a, b)));
      at = b;
    }
    out.push(text.slice(at, to));
    if (to < text.length) out.push(" …");
    return out;
  }

  const TEXT_NOTES = {
    delegation: "Text issued by the delegation, as prepared; not yet the official record.",
    annex: "A statement shown as a recorded video: its text is printed in an annex to the record, as provided by the delegation.",
    transcript: "AI transcript of the UN’s English interpretation; not yet the official record.",
    corpus: "From the UN General Debate Corpus (the delegation’s text or a transcript); not yet the official record.",
  };

  // The record's slip for one source: a UN document (its symbol and date, who spoke, the meeting and agenda item),
  // the passage with every quoted run highlighted and the chosen one outlined, and the link to the document.
  // ``short``: a shorter passage, for the sheet on a phone, so the highlighted words are in view as it opens.
  function slip(s, quoted, nowSeg, passageId, short) {
    if (s.kind === "vote") {
      const t = s.totals || {};
      return h("div", { class: "wts-slip" },
        h("div", { class: "wts-slip__top" }, h("span", { class: "wts-slip__sym" }, s.resolution || "Recorded vote"), h("span", {}, longDate(s.date))),
        h("div", { class: "wts-slip__who" }, h("strong", {}, voteWords(s)[1]), `, ${voteWords(s)[0].toLowerCase()}`),
        s.title && h("div", { class: "wts-slip__heading" }, s.title),
        h("p", { class: "wts-passage", tabindex: "0", id: passageId, "aria-label": `The record of source ${s.n}` },
          s.body === "Security Council"
            ? `${s.vote_name || s.resolution}: ${t.yes ?? "–"} in favour, ${t.no ?? "–"} against, ${t.abstain ?? "–"} abstaining.`
            : `Adopted by a recorded vote: ${t.yes} in favour, ${t.no} against, ${t.abstain} abstaining.`),
        h("div", { class: "wts-slip__foot" }, h("span", {}), h("a", { href: s.url, rel: "noopener", "data-wts-conversion": "source_opened" }, "Read the UN Digital Library record")));
    }
    const text = s.text || "";
    const spans = highlights(text, quoted, nowSeg);
    const who = [h("strong", {}, s.speaker_name || "Speaker")];
    if (s.country_name) who.push(`, for ${s.country_name}`);
    if (s.on_behalf_of) who.push(`, on behalf of the ${s.on_behalf_of}`);
    if (s.language) who.push(` (spoke in ${s.language})`);
    const notes = [s.language ? "English text: UN translation." : null, TEXT_NOTES[s.text_source] || null,
      s.ocr ? "Text recognised from a scanned record." : null].filter(Boolean);
    return h("div", { class: "wts-slip" },
      h("div", { class: "wts-slip__top" },
        s.symbol ? h("span", { class: "wts-slip__sym" }, s.symbol) : h("b", {}, s.body || "The record"),
        h("span", {}, sourceDate(s))),
      h("div", { class: "wts-slip__who" }, who),
      h("div", { class: "wts-slip__heading" }, [[s.body, s.meeting].filter(Boolean).join(", "), s.agenda].filter(Boolean).join(". ")),
      notes.length > 0 && h("div", { class: "wts-slip__heading" }, notes.join(" ")),
      h("p", { class: "wts-passage", tabindex: "0", id: passageId, "aria-label": `The passage from source ${s.n}` }, passageNodes(text, spans, short)),
      h("div", { class: "wts-slip__foot" },
        spans.length ? h("span", { class: "wts-slip__found" }, "✓ Highlighted words found word for word") : h("span", {}),
        h("a", { href: s.url, rel: "noopener", "data-wts-conversion": "source_opened" }, s.symbol ? `Read ${s.symbol}` : "Read the source")));
  }

  // A record found, as a card in the pile of the wait: its document symbol and date, who spoke, its first words.
  function card(s) {
    const first = (s.kind === "vote" ? s.title || "" : (s.text || "")).replace(/\s+/g, " ").trim();
    const line = first.length > 150 ? first.slice(0, 150).replace(/\s+\S*$/, "") + "…" : first;
    const who = s.kind === "vote" ? `${voteWords(s)[1]}, ${voteWords(s)[0].toLowerCase()}`
      : [s.on_behalf_of || s.country_name, s.speaker_name].filter(Boolean).join(", ");
    return h("li", { class: "wts-card", "data-source": s.n },
      h("div", { class: "wts-card__top" },
        h("span", { class: "wts-card__sym" }, s.kind === "vote" ? s.resolution || "Recorded vote" : s.symbol || s.body || ""),
        h("span", {}, sourceDate(s))),
      h("div", { class: "wts-card__who" }, h("span", {}, who), h("span", { class: "wts-card__cited" }, " Cited")),
      h("div", { class: "wts-card__line" }, line));
  }

  // ---------- the wait ----------

  const BODY = { sc: "Security Council", gd: "General Assembly", ga: "General Assembly plenary meetings" };

  // The question as read, as large chips: the bodies, the dates, the countries and groups.
  function planChips(plan, names) {
    const f = (plan && plan.filters) || {};
    names = names || { countries: {}, groups: {} };
    const out = [];
    const bodies = (f.sources || []).map((s) => BODY[s] || s);
    out.push(bodies.length ? bodies.join(" and ") : "Security Council and General Assembly");
    if (f.date_from || f.date_to) {
      const from = f.date_from ? f.date_from.slice(0, 4) : "1946", to = f.date_to ? f.date_to.slice(0, 4) : "today";
      out.push(from === to ? from : `${from} to ${to}`);
    }
    for (const c of f.countries || []) out.push(names.countries[c] || c);
    for (const g of f.groups || []) out.push(names.groups[g] || g);
    if (!(f.countries || []).length && !(f.groups || []).length) out.push("all countries");
    return out;
  }

  // What was found, in words: the running count while the cards arrive, then the summary.
  function foundWords(evidence, shown, total) {
    if (shown < total) return [h("strong", {}, String(shown)), ` ${plural(shown, "record", "records")} found so far`];
    const e = evidence || {};
    const votes = Number(e.votes || 0);
    if (!e.records) return [h("strong", {}, String(total)), ` ${plural(total, "record", "records")} found`];
    return [h("strong", {}, String(e.records)), ` ${plural(e.records, "statement", "statements")} from ${e.countries} ${plural(e.countries, "country or group", "countries and groups")} at ${e.meetings} ${plural(e.meetings, "meeting", "meetings")}`,
      votes ? `, and ${votes} recorded ${plural(votes, "vote", "votes")}` : ""];
  }

  return { h, toHTML, toDOM, textOf, longDate, chipLabel, quotations, counts, countsSentence, firsts, chipsShown, briefingBlocks,
    citedSources, quotedFrom, strip, details, slip, card, highlights, planChips, foundWords, flagged };
});
