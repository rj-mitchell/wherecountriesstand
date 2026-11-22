/* Where They Stand family kit: measure.js, the consent banner and the Google tag loader (family/MEASUREMENT.md).
   Copied into each site by sync.mjs. Edit family/kit/measure.js, not the copies.

   It does nothing unless a page includes it, and a site's pages include it only when its measurement switch is on
   ([ads] measurement = true):

     <script src="/family/measure.js" data-tag="AW-XXXXXXXXX" data-privacy="/privacy"
             data-measures="download donate_click" defer></script>

   data-tag      the Google tag's ID. Without a valid one the script does nothing at all.
   data-privacy  the privacy notice's address (default /privacy), linked from the banner.
   data-measures the conversions this site has, from the five below (default all five); the banner names only these.

   Until the reader chooses, nothing is loaded from Google and nothing is stored. Google Consent Mode v2 starts with
   everything denied, and the Google tag itself is loaded only after "Accept" (basic consent mode). The choice is kept
   in the reader's own browser, in localStorage ("wts-measure-choice": "accepted 2026-11-21" or "rejected ..."),
   never in a cookie. "Measurement choices" (any element with data-wts-measure-open, in every footer) opens the banner
   again; rejecting after accepting deletes Google's cookies on this site and reloads the page, with nothing loaded.

   The conversions, sent as gtag("event", name) only after "Accept", and nothing else (no page views):
     briefing_read  an element with data-wts-conversion="briefing_read" (the end of a finished briefing) comes into
                    view: once for each element, and once for each data-wts-briefing value it carries
     source_opened  a click on an element with data-wts-conversion="source_opened" (a source chip, a record link)
     download       a click on an element with data-wts-conversion="download" (a Word, PDF, CSV or data file)
     donate_click   a click on an element with data-wts-conversion="donate_click" (the Donate link)
     report_sent    window.wtsMeasure.conversion("report_sent"), called by the site after a report or statement is sent;
                    or, for a form that works without script, a page carrying data-wts-conversion="report_sent" (the
                    thank-you page the form leads to), counted once when the page loads

   Plain script, no dependencies, nothing inline: it works under a content security policy that allows this file
   and Google's hosts (measure-csp.json beside it). Its logic runs against whatever window it is given, so the
   kit's tests run it in plain Node with stand-ins (family/tools/tests/measure.test.mjs). */
(function (win) {
  "use strict";

  var KEY = "wts-measure-choice";
  var REASK_DAYS = 365;
  var ORDER = ["briefing_read", "source_opened", "download", "donate_click", "report_sent"];
  var WORDS = {
    briefing_read: "finish reading a briefing",
    source_opened: "open a source",
    download: "download a file",
    donate_click: "follow the Donate link",
    report_sent: "send a report or a statement",
  };
  var CLICKED = { source_opened: true, download: true, donate_click: true };
  var MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December"];
  // The first-party cookies the Google tag may set on this site; deleted when a reader withdraws consent
  var GOOGLE_COOKIES = /^(_gcl_|_ga$|_ga_|_gid$|_gac_|_gat)/;

  if (!win || !win.document || win.wtsMeasure) return;
  var doc = win.document;
  var script = doc.currentScript || doc.querySelector("script[data-tag]");
  if (!script) return;
  var tag = (script.getAttribute("data-tag") || "").trim();
  if (!/^(AW|G|GT)-[A-Za-z0-9]+$/.test(tag)) return;
  var privacy = script.getAttribute("data-privacy") || "/privacy";
  var asked = (script.getAttribute("data-measures") || ORDER.join(" ")).split(/\s+/);
  var measures = ORDER.filter(function (name) { return asked.indexOf(name) !== -1; });
  if (!measures.length) measures = ORDER.slice();

  // Google Consent Mode v2: everything denied before anything else happens. This only fills an array in the page's
  // memory; the Google tag that would read it is not loaded until the reader accepts.
  win.dataLayer = win.dataLayer || [];
  function gtag() { win.dataLayer.push(arguments); }
  gtag("consent", "default", {
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
    analytics_storage: "denied",
    wait_for_update: 500,
  });

  var loaded = false;
  var sent = {};
  var seen = [];
  var observer = null;
  var region = null;

  function today() { return new Date().toISOString().slice(0, 10); }

  function stored() {
    var value = null;
    try { value = win.localStorage.getItem(KEY); } catch (e) { return null; }
    var m = /^(accepted|rejected) (\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
    if (!m) return null;
    var date = m[2] + "-" + m[3] + "-" + m[4];
    var age = (Date.now() - Date.parse(date + "T00:00:00Z")) / 86400000;
    if (!(age < REASK_DAYS)) return null;   // a year on, the reader is asked again
    return { choice: m[1], date: date };
  }

  function save(choice) {
    try { win.localStorage.setItem(KEY, choice + " " + today()); } catch (e) { /* kept for this page only */ }
  }

  function inWords(date) {
    var p = date.split("-");
    return Number(p[2]) + " " + MONTHS[Number(p[1]) - 1] + " " + p[0];
  }

  function list(items) {
    if (items.length < 2) return items.join("");
    return items.slice(0, -1).join(", ") + " or " + items[items.length - 1];
  }

  // The Google tag, with consent granted for measuring (never for personalised ads), and no page views sent
  function load() {
    if (loaded) return;
    loaded = true;
    gtag("consent", "update", {
      ad_storage: "granted",
      ad_user_data: "granted",
      analytics_storage: "granted",
      ad_personalization: "denied",
    });
    var tagScript = doc.createElement("script");
    tagScript.async = true;
    tagScript.src = "https://www.googletagmanager.com/gtag/js?id=" + encodeURIComponent(tag);
    (doc.head || doc.body).appendChild(tagScript);
    gtag("js", new Date());
    gtag("config", tag, { send_page_view: false, allow_ad_personalization_signals: false });
    watch();
  }

  function conversion(name) {
    if (!loaded || measures.indexOf(name) === -1) return false;
    gtag("event", name);
    return true;
  }

  function closest(node, selector) {
    while (node && node.nodeType === 1) {
      if (node.matches && node.matches(selector)) return node;
      node = node.parentNode;
    }
    return null;
  }

  function clicked(event) {
    var target = closest(event.target, "[data-wts-conversion]");
    if (!target) return;
    var name = target.getAttribute("data-wts-conversion");
    if (CLICKED[name]) conversion(name);
  }

  // The end of a briefing: counted once when it comes into view
  function ended(entries) {
    for (var i = 0; i < entries.length; i++) {
      var entry = entries[i];
      if (!entry.isIntersecting) continue;
      var element = entry.target;
      if (observer) observer.unobserve(element);
      var id = element.getAttribute("data-wts-briefing");
      if (id) {
        if (sent[id]) continue;
        sent[id] = true;
      }
      conversion("briefing_read");
    }
  }

  var ends = null;
  function lookForEnds() {
    if (!ends) return;
    var found = doc.querySelectorAll('[data-wts-conversion="briefing_read"]');
    for (var i = 0; i < found.length; i++) {
      if (seen.indexOf(found[i]) !== -1) continue;
      seen.push(found[i]);
      ends.observe(found[i]);
    }
  }

  function watch() {
    if (doc.querySelector('[data-wts-conversion="report_sent"]')) conversion("report_sent");
    if (typeof win.IntersectionObserver === "function" && measures.indexOf("briefing_read") !== -1) {
      ends = new win.IntersectionObserver(ended);
      observer = ends;
      lookForEnds();
    }
  }

  // Google's cookies on this site, deleted when a reader withdraws: each is expired at once, at this host and at
  // every parent domain it could have been set on. Nothing is ever set.
  function forget() {
    var names = [];
    var all = "";
    try { all = doc.cookie || ""; } catch (e) { return; }
    all.split(";").forEach(function (pair) {
      var name = pair.split("=")[0].trim();
      if (GOOGLE_COOKIES.test(name)) names.push(name);
    });
    var host = (win.location && win.location.hostname) || "";
    var parts = host.split(".");
    var domains = [""];
    for (var i = 0; i < parts.length - 1; i++) domains.push("; domain=." + parts.slice(i).join("."));
    names.forEach(function (name) {
      domains.forEach(function (domain) {
        doc.cookie = name + "=; Max-Age=0; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/" + domain;
      });
    });
  }

  function el(name, attributes, text) {
    var node = doc.createElement(name);
    for (var key in attributes) {
      if (Object.prototype.hasOwnProperty.call(attributes, key)) node.setAttribute(key, attributes[key]);
    }
    if (text) node.textContent = text;
    return node;
  }

  // The banner goes at the top of the main content, or into the last element marked data-wts-measure-slot (a site
  // marks one at the top of a dialog that makes the rest of the page inert, so the banner can still be reached).
  // It moves whenever the page changes: into a slot that appears, back when it goes, and back into the main content
  // when the page's own script draws it again.
  function place() {
    if (!region) return;
    var slots = doc.querySelectorAll("[data-wts-measure-slot]");
    var home = slots.length ? slots[slots.length - 1] : doc.querySelector("main") || doc.body;
    if (home && region.parentNode !== home) home.insertBefore(region, home.firstChild);
  }

  function choose(choice) {
    var before = stored();
    save(choice);
    if (choice === "accepted") {
      load();
      done("Thank you. Your choice is saved in this browser. You can change it from “Measurement choices” " +
        "at the foot of any page.");
      return;
    }
    if (loaded || (before && before.choice === "accepted")) {
      // withdrawn: tell Google, delete its cookies, and start the page again without the tag
      gtag("consent", "update", { ad_storage: "denied", ad_user_data: "denied", analytics_storage: "denied" });
      forget();
      if (win.location && typeof win.location.reload === "function") win.location.reload();
      return;
    }
    done("Thank you. Nothing is measured. You can change your choice from “Measurement choices” at the foot " +
      "of any page.");
  }

  function done(message) {
    if (!region) return;
    var hadFocus = region.contains && doc.activeElement && region.contains(doc.activeElement);
    while (region.firstChild) region.removeChild(region.firstChild);
    region.appendChild(el("p", { class: "wts-measure__text" }, message));
    if (hadFocus && region.focus) region.focus();
  }

  function open(focus) {
    var now = stored();
    if (region && region.parentNode) region.parentNode.removeChild(region);
    region = el("div", { class: "wts-measure", role: "region", "aria-label": "Measuring visits", tabindex: "-1",
      id: "wts-measure" });
    region.appendChild(el("p", { class: "wts-measure__text" },
      "If you agree, this site will use Google’s tag, which sets cookies, to tell Google when you " +
      list(measures.map(function (name) { return WORDS[name]; })) + ", so that Google can see whether the " +
      "free advertising it gives Where They Stand brings people here."));
    if (now) {
      region.appendChild(el("p", { class: "wts-measure__now" },
        "You chose to " + (now.choice === "accepted" ? "accept" : "reject") + " on " + inWords(now.date) + "."));
    }
    var actions = el("div", { class: "wts-measure__actions" });
    var accept = el("button", { type: "button", class: "wts-measure__button", "data-choice": "accepted" }, "Accept");
    var reject = el("button", { type: "button", class: "wts-measure__button", "data-choice": "rejected" }, "Reject");
    accept.addEventListener("click", function () { choose("accepted"); });
    reject.addEventListener("click", function () { choose("rejected"); });
    actions.appendChild(accept);
    actions.appendChild(reject);
    actions.appendChild(el("a", { class: "wts-measure__link", href: privacy + "#measurement" },
      "Read the privacy notice"));
    region.appendChild(actions);
    place();
    if (focus && region.focus) region.focus();
  }

  function reopen(event) {
    if (!closest(event.target, "[data-wts-measure-open]")) return;
    event.preventDefault();
    open(true);
  }

  doc.addEventListener("click", clicked, true);
  doc.addEventListener("auxclick", clicked, true);
  doc.addEventListener("click", reopen, false);
  if (typeof win.MutationObserver === "function") {
    new win.MutationObserver(function () { place(); lookForEnds(); })
      .observe(doc.documentElement || doc.body, { childList: true, subtree: true });
  }

  win.wtsMeasure = {
    conversion: conversion,
    open: function () { open(true); },
    choice: function () { var now = stored(); return now ? now.choice : null; },
  };

  var now = stored();
  if (now && now.choice === "accepted") load();
  else if (!now) open(false);
})(typeof window !== "undefined" ? window : null);
