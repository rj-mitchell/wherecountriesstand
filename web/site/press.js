// Where Countries Stand: the press page (press.html). Fills in the record's coverage, as of today, from /api/meta;
// without JavaScript the page says where each source starts.
"use strict";

function fmtDate(iso) {
  const d = new Date(`${iso.slice(0, 10)}T00:00:00Z`);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const meta = await fetch("/api/meta").then((r) => r.json());
    const c = meta.coverage || {};
    const rows = document.querySelectorAll("#coverage li");
    [c.gd, c.sc, c.votes].forEach((x, i) => {
      if (x && x.from && x.to && rows[i]) rows[i].lastElementChild.textContent = `${x.from.slice(0, 4)}–${fmtDate(x.to)}`;
    });
  } catch (e) { /* the starting years stay */ }
});
