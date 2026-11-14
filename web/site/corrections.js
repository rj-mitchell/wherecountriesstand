// Where Countries Stand: the report form and the corrections log (corrections.html): withdrawn briefings, and what
// the site has said publicly of a correction made after a report.
// The form offers the family's three routes (the impartiality policy, section 7.1): "Something is wrong" and "You've
// been characterised unfairly" are sent as reports (/api/report), and "Add a statement in your own words" as a
// statement under the right of reply (/api/statements). ?kind= picks the route (error, unfair, statement, takedown,
// other; "problem", the old name for "error", still works), and ?page= fills in the address.
"use strict";

const $ = (id) => document.getElementById(id);
const KINDS = ["error", "unfair", "statement", "takedown", "other"];
const MAX_WORDS = 150;
const HINT = {
  error: "A fact, a quotation, a date, a label, a group or a connection is incorrect, or a briefing misstates the record.",
  unfair: "The record is right, but the site’s own words, labels or groupings are unfair: a loaded word in the site’s own voice, a connection without published evidence, or a grouping that suggests something the record doesn’t show.",
  statement: "The record is accurate and the site has followed its impartiality policy, but you see it differently: add a short statement, shown beside the briefing.",
  takedown: "Something about you is shown that should not be. These requests are dealt with first.",
  other: "Anything else about the site.",
};
const QUESTION = {
  error: "What is wrong?",
  unfair: "What is unfair, and why?",
  takedown: "What would you like removed, and why?",
  other: "Your message",
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.append(...children);
  return node;
}

function fmtDate(iso) {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

function words(text) {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

async function loadCorrections() {
  const list = await fetch("/api/corrections").then((r) => r.json()).catch(() => null);
  if (!list || !list.length) return;
  // A withdrawn briefing: its question, when it was written and withdrawn, and why. Anything else in the log is a
  // correction the site has made after a report, in the words the site chose to publish, with its date.
  $("corrections").replaceChildren(el("ul", {}, ...list.map((c) => c.kind && c.kind !== "withdrawn"
    ? el("li", {}, el("strong", {}, "Corrected"), ` (${fmtDate(c.date)}): `, c.note)
    : el("li", {}, el("strong", {}, c.question), ` (written ${fmtDate(c.at)}; withdrawn ${fmtDate(c.withdrawn_at)}): `,
       c.reason || "withdrawn"))));
}

function kind() {
  return document.querySelector("input[name=kind]:checked").value;
}

// One set of fields at a time: a hidden set is disabled too, so its required fields don't stop the other being sent.
function showKind() {
  const k = kind();
  const statement = k === "statement";
  $("report-fields").hidden = statement;
  $("report-fields").disabled = statement;
  $("statement-fields").hidden = !statement;
  $("statement-fields").disabled = !statement;
  $("kind-hint").textContent = HINT[k];
  if (!statement) $("message-label").textContent = QUESTION[k];
  $("send").textContent = statement ? "Send the statement" : "Send";
}

function countWords() {
  const n = words($("st-text").value);
  $("st-count").textContent = n > MAX_WORDS ? `${n} words: ${n - MAX_WORDS} more than the 150 a statement can have.`
                                            : `${n} word${n === 1 ? "" : "s"} of 150.`;
  return n;
}

async function send(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify(body) });
  if (res.ok) return;
  const detail = await res.json().catch(() => ({}));
  // the site's own checks say what to change; anything else (a field too long for the browser to have sent) is general
  if (typeof detail.detail === "string") throw new Error(detail.detail);
  throw new Error(`It could not be sent (${res.status}). Please email us instead.`);
}

document.addEventListener("DOMContentLoaded", () => {
  loadCorrections();
  const params = new URLSearchParams(location.search);
  const asked = params.get("kind") === "problem" ? "error" : params.get("kind");
  if (KINDS.includes(asked)) document.querySelector(`input[name=kind][value=${asked}]`).checked = true;
  const page = params.get("page");
  if (page) {
    const address = page.startsWith("/") ? `${location.origin}${page}` : page;
    $("page").value = address;
    $("st-page").value = address;
  }
  showKind();
  for (const radio of document.querySelectorAll("input[name=kind]")) radio.addEventListener("change", showKind);
  $("st-text").addEventListener("input", countWords);

  $("report").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = new FormData(e.target);
    const k = kind();
    if (k === "statement" && countWords() > MAX_WORDS) {
      $("report-status").textContent = `A statement can be up to ${MAX_WORDS} words. Please shorten it.`;
      $("st-text").focus();
      return;
    }
    $("send").disabled = true;
    $("report-status").textContent = "Sending…";
    try {
      if (k === "statement") {
        await send("/api/statements", {
          page: form.get("st-page"), name: form.get("st-name"), role: form.get("st-role"),
          text: form.get("st-text"), link: form.get("st-link") || "", email: form.get("st-email"),
        });
      } else {
        await send("/api/report", { kind: k, page: form.get("page") || "", message: form.get("message"),
                                    contact: form.get("contact") || "" });
      }
      e.target.reset();
      document.querySelector(`input[name=kind][value=${k}]`).checked = true;
      showKind();
      countWords();
      $("report-status").textContent = k === "statement"
        ? "Thank you: your statement has been sent. We will write to you to confirm who you are before it is shown."
        : "Thank you: your report has been sent.";
    } catch (err) {
      $("report-status").textContent = err.message;
    } finally {
      $("send").disabled = false;
    }
  });
});
