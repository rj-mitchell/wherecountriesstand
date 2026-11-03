// Where Countries Stand: the problem-report form and the list of withdrawn briefings (corrections.html).
"use strict";

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  node.append(...children);
  return node;
}

function fmtDate(iso) {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

async function loadCorrections() {
  const list = await fetch("/api/corrections").then((r) => r.json()).catch(() => null);
  if (!list || !list.length) return;
  $("corrections").replaceChildren(el("ul", {}, ...list.map((c) =>
    el("li", {}, el("strong", {}, c.question), ` (written ${fmtDate(c.at)}; withdrawn ${fmtDate(c.withdrawn_at)}): `,
       c.reason || "withdrawn"))));
}

document.addEventListener("DOMContentLoaded", () => {
  loadCorrections();
  const page = new URLSearchParams(location.search).get("page");
  if (page) $("page").value = page.startsWith("/") ? `${location.origin}${page}` : page;
  $("report").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = new FormData(e.target);
    $("send").disabled = true;
    $("report-status").textContent = "Sending…";
    try {
      const res = await fetch("/api/report", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: form.get("kind"), page: form.get("page") || "", message: form.get("message"),
                               contact: form.get("contact") || "" }),
      });
      if (!res.ok) throw new Error(`The report could not be sent (${res.status}). Please email us instead.`);
      e.target.reset();
      $("report-status").textContent = "Thank you: your report has been sent.";
    } catch (err) {
      $("report-status").textContent = err.message;
    } finally {
      $("send").disabled = false;
    }
  });
});
