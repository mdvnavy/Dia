// What changed: the bell in the header and the /changes page (Ikenna, 2026-10-23 9:55 PM ET: "the new tag is literally
// like a fucking notification center ... with the change log. Look at the fucking family tree").
// Pattern from mdvnavy/nwakwesi-family-tree app/app.js (initBell, renderChangelog):
// - Resting on the bell (mouse, 150ms), focusing it, or one tap fades in the latest five changes; the bell itself,
//   a second tap, or "See all changes" opens /changes.
// - Data: /static/changelog.json (no-cache), built from git history by scripts/build-changelog.py. No approval flag.
// - A dot shows while the newest entry's key differs from localStorage[SEEN]. The key is new (v1), so no older
//   What's new state can hide tonight's changes. Opening the panel or the page marks everything seen.
(() => {
  "use strict";
  const SEEN = "dia-changes-seen-v1";
  const URL = "/static/changelog.json";
  const LATEST = 5;
  const $ = (s) => document.querySelector(s);

  let loading = null;
  const load = () => loading || (loading = fetch(URL, { cache: "no-cache" })
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then((log) => (Array.isArray(log && log.entries) ? log.entries : []))
    .catch((error) => { loading = null; throw error; }));

  const readSeen = () => { try { return localStorage.getItem(SEEN); } catch (e) { return null; } };
  const SEEN_AT_LOAD = readSeen();   // "New" marks stay for this visit even after the dot clears
  // How many entries are newer than what this reader saw. Never seen (or a key no longer listed): all of them.
  const newCount = (entries) => {
    if (!entries.length) return 0;
    const at = SEEN_AT_LOAD ? entries.findIndex((e) => e.key === SEEN_AT_LOAD) : -1;
    return at >= 0 ? at : entries.length;
  };
  const DATE = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const when = (iso) => (/^\d{4}-\d{2}-\d{2}$/.test(iso || "") ? DATE.format(new Date(iso + "T00:00:00Z")) : "");
  const el = (tag, attrs, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) n.setAttribute(k, v);
    for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) n.append(k);
    return n;
  };
  // Glyph's clay NEW tag (static/brand/clay-new-{light,dark}-22/44/66.png, the What's new v0.5 asset, byte-identical
  // to Glyph's exports): one image per theme, CSS picks; the word "New" is read by screen readers and shown in forced colours.
  const clayImg = (theme) => el("img", { class: "cl-new-img cl-new-img--" + theme, alt: "", width: "22", height: "8",
    src: `/static/brand/clay-new-${theme}-22.png`,
    srcset: `/static/brand/clay-new-${theme}-22.png 1x, /static/brand/clay-new-${theme}-44.png 2x, /static/brand/clay-new-${theme}-66.png 3x` });
  const newPill = () => el("span", { class: "cl-new" }, clayImg("light"), clayImg("dark"), el("span", { class: "cl-new-text" }, "New"));

  const btn = $("#bell-btn");
  function setDot(on, count = 0) {
    if (!btn) return;
    btn.classList.toggle("has-new", on);
    btn.setAttribute("aria-label", on ? `What changed, ${count} new` : "What changed");
  }
  function markSeen(entries) {
    if (!entries.length) return;
    try { localStorage.setItem(SEEN, entries[0].key); } catch (e) { /* storage may be blocked; the dot just clears */ }
    setDot(false);
  }

  // ---------------------------------------------------------------- the full page (/changes)
  function renderPage() {
    const body = $("#changes-list"); if (!body) return;
    load().then((entries) => {
      const fresh = newCount(entries);
      body.replaceChildren(...entries.map((e, i) => el("li", { class: "cl-entry", id: e.key, tabindex: "-1" },
        el("h2", null, e.title, i < fresh ? newPill() : null),
        el("p", { class: "cl-date" }, el("time", { datetime: e.date }, when(e.date))),
        el("p", { class: "cl-text" }, e.text))));
      if (!entries.length) body.replaceChildren(el("li", { class: "cl-entry" }, el("p", null, "Nothing has changed yet.")));
      body.setAttribute("aria-busy", "false");
      markSeen(entries);
      const t = location.hash && document.getElementById(location.hash.slice(1));
      if (t) { t.scrollIntoView(); t.focus({ preventScroll: true }); }
    }).catch(() => body.replaceChildren(el("li", { class: "cl-entry" }, el("p", null, "The list of changes did not load. Reload the page to try again."))));
  }

  // ---------------------------------------------------------------- the bell
  function initBell() {
    const wrap = $("#bell"), panel = $("#bell-panel");
    if (!wrap || !btn || !panel) return;
    // The panel lives on <body> so nothing in the page (the prompt bar, the band's caps) stacks over it or styles it.
    document.body.append(panel);
    const inside = (node) => !!node && (wrap.contains(node) || panel.contains(node));
    const hovered = () => wrap.matches(":hover") || panel.matches(":hover");
    const place = () => {
      if (window.matchMedia("(max-width: 600px)").matches) { panel.style.left = ""; panel.style.top = ""; return; }
      const band = document.querySelector(".top-nav"), b = btn.getBoundingClientRect();
      const w = Math.min(400, window.innerWidth - 32);
      panel.style.top = Math.round((band ? band.getBoundingClientRect().bottom : b.bottom) + 8) + "px";
      panel.style.left = Math.round(Math.max(16, Math.min(window.innerWidth - w - 16, b.left + b.width / 2 - w / 2))) + "px";
    };
    let closeTimer = 0, openTimer = 0, tapWasOpen = null, quiet = false, filled = false;
    const isOpen = () => wrap.classList.contains("open");
    const title = () => el("h2", { class: "dia-bell-title" }, "What changed");
    const fill = () => {
      if (filled) return; filled = true;
      panel.replaceChildren(title(), el("p", null, "Loading…"));
      load().then((entries) => {
        const fresh = newCount(entries);
        const list = el("ol", { class: "dia-bell-list" }, entries.slice(0, LATEST).map((e, i) => el("li", null,
          el("a", { href: "/changes#" + e.key },
            el("span", { class: "dia-bell-when" }, i < fresh ? newPill() : null, el("time", { datetime: e.date }, when(e.date))),
            el("b", null, e.title),
            el("span", { class: "dia-bell-text" }, e.text)))));
        const all = el("a", { class: "dia-bell-all", href: "/changes" }, "See all changes");
        panel.replaceChildren(title(), entries.length ? list : el("p", null, "Nothing has changed yet."), all);
        markSeen(entries);
      }).catch(() => { filled = false; panel.replaceChildren(title(), el("p", null, "The list of changes did not load. Try again in a moment.")); });
    };
    const open = () => {
      clearTimeout(closeTimer); clearTimeout(openTimer); fill();
      if (!isOpen()) { panel.scrollTop = 0; place(); }
      wrap.classList.add("open"); panel.classList.add("open"); btn.setAttribute("aria-expanded", "true");
    };
    const close = (refocus = false) => {
      clearTimeout(closeTimer); clearTimeout(openTimer); if (!isOpen()) return;
      if (refocus && inside(document.activeElement)) { quiet = true; btn.focus({ preventScroll: true }); quiet = false; }
      wrap.classList.remove("open"); panel.classList.remove("open"); btn.setAttribute("aria-expanded", "false");
    };
    // A mouse rests on the bell (a short wait, so a pointer passing by does not open it); a finger taps once for the
    // list and again (or "See all changes") for the page; a keyboard opens it on focus and Enter follows the link.
    wrap.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") { clearTimeout(closeTimer); clearTimeout(openTimer); openTimer = setTimeout(open, 150); } });
    panel.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") clearTimeout(closeTimer); });
    const leave = (e) => { if (e.pointerType === "mouse") { clearTimeout(openTimer); clearTimeout(closeTimer); closeTimer = setTimeout(() => { if (!hovered()) close(); }, 300); } };
    wrap.addEventListener("pointerleave", leave);
    panel.addEventListener("pointerleave", leave);
    btn.addEventListener("pointerdown", (e) => { tapWasOpen = e.pointerType === "mouse" ? null : isOpen(); });
    btn.addEventListener("click", (e) => { const was = tapWasOpen; tapWasOpen = null; if (was === false) { e.preventDefault(); open(); } });
    wrap.addEventListener("focusin", () => { if (!quiet && tapWasOpen === null) open(); });
    const blur = (e) => { if (!inside(e.relatedTarget) && !hovered()) close(); };
    wrap.addEventListener("focusout", blur);
    panel.addEventListener("focusout", blur);
    // Tab from the bell goes into the panel (it sits at the end of <body>); Tab past its last link returns to the band.
    btn.addEventListener("keydown", (e) => {
      if (e.key !== "Tab" || e.shiftKey || !isOpen()) return;
      const first = panel.querySelector("a"); if (first) { e.preventDefault(); first.focus(); }
    });
    panel.addEventListener("keydown", (e) => {
      if (e.key !== "Tab") return;
      const links = [...panel.querySelectorAll("a")];
      if (!e.shiftKey && document.activeElement === links[links.length - 1]) {
        e.preventDefault(); close(); const next = document.querySelector("#paletteTrigger"); (next || btn).focus();
      } else if (e.shiftKey && document.activeElement === links[0]) { e.preventDefault(); quiet = true; btn.focus(); quiet = false; }
    });
    document.addEventListener("pointerdown", (e) => { if (isOpen() && !inside(e.target)) close(); });
    window.addEventListener("resize", () => { if (isOpen()) place(); });
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || !isOpen()) return;
      e.preventDefault(); e.stopPropagation();
      const within = inside(document.activeElement); close(true);
      if (!within) { quiet = true; btn.focus({ preventScroll: true }); quiet = false; }   // Escape lands on the bell
    }, true);
    // The DIA menu's and the palette's "What changed" open the panel and put focus on its first change.
    window.DiaWhatsNew = {
      open: () => { open(); load().then(() => requestAnimationFrame(() => (panel.querySelector("a") || btn).focus())).catch(() => btn.focus()); },
      close: () => close(true),
      isOpen,
    };
    // The dot: checked once the page has painted, at low priority.
    const check = () => load().then((entries) => {
      const fresh = newCount(entries);
      if (entries.length && entries[0].key !== readSeen()) setDot(true, fresh); else setDot(false);
      wrap.dataset.ready = "1";
    }).catch(() => { wrap.dataset.ready = "1"; });
    const later = () => requestAnimationFrame(() => setTimeout(check, 0));
    if (document.readyState === "complete") later(); else window.addEventListener("load", later, { once: true });
  }

  initBell();
  renderPage();
})();
