// Where Countries Stand: the status page (status.html), filled in from /api/status. Every piece of text is set with
// textContent. Without JavaScript the page says where the same information is.
"use strict";

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.append(...children);
  return node;
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

function fmtTime(iso) {
  const d = new Date(iso);
  return `${fmtDate(iso)} at ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC`;
}

const BRIEFINGS = {
  available: "Available",
  paused: "Paused",
  unavailable: "Not available: sources only",
};

// A line in the style of the home page's list of the record: the label, dotted leaders, the value.
function line(label, value) {
  return el("li", {}, el("span", {}, label), el("span", { class: "dots" }), el("span", {}, value));
}

function show(s) {
  const c = s.coverage || {};
  const latest = (x) => (x && x.to ? fmtDate(x.to) : "not loaded yet");
  const lines = [
    line("New briefings", BRIEFINGS[s.briefings] || s.briefings),
    line("Finding sources", "Available"),
    line("Latest Security Council record", latest(c.sc)),
    line("Latest General Debate record", latest(c.gd)),
    line("Latest General Assembly vote", latest(c.votes)),
  ];
  const u = s.last_update;
  if (u && (u.finished || u.started)) {
    lines.push(line("Last update", `${fmtTime(u.finished || u.started)}${u.ok ? "" : " (needs attention)"}`));
  } else {
    lines.push(line("Last update", "none yet"));
  }
  const out = [el("h2", {}, "Now"), el("ul", { class: "leaders status-list" }, ...lines)];
  if (s.briefings_note) out.push(el("p", { class: "note" }, s.briefings_note));
  const notes = u ? [...(u.errors || []), ...(u.warnings || [])] : [];
  out.push(el("h2", {}, "Warnings from the last update"));
  out.push(notes.length ? el("ul", {}, ...notes.map((n) => el("li", {}, n.charAt(0).toUpperCase() + n.slice(1)))) : el("p", {}, "None."));
  if (s.checked_at) out.push(el("p", { class: "status" }, `Checked ${fmtTime(s.checked_at)}.`));
  $("status-live").replaceChildren(...out);
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const res = await fetch("/api/status");
    if (!res.ok) throw new Error(String(res.status));
    show(await res.json());
  } catch (e) {
    $("status-live").replaceChildren(el("p", { class: "note" },
      "The status could not be loaded just now. If this page loads but briefings do not, the site may be " +
      "restarting: please try again in a few minutes."));
  }
});
