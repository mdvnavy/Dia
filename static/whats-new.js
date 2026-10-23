// What's new indicator + release-notes panel (spec proto/whats-new-indicator.md v0.3, layout A).
// - Notes: static/whats-new.json, fetched once AFTER first paint (low priority, no-cache); never blocks intake or chat.
// - Seen: localStorage["dia-whats-new-seen"] = latest live note id, written the moment the panel opens.
// - No glow on a first visit, on any load error, when storage throws, or when the stored id is no longer listed.
// - The glow is CSS only (static/whats-new.css, --wn-* tokens); this file only flips data-state / data-fade-once.
(() => {
  "use strict";
  const KEY = "dia-whats-new-seen";
  const APP = "dia";
  const URL = "/static/whats-new.json";
  const MAX_NOTES = 10;
  const MOBILE = window.matchMedia("(max-width: 600px)");

  const wrap = document.querySelector("#whatsNewWrap");
  const button = document.querySelector("#whatsNew");
  const tip = document.querySelector("#whatsNewTip");
  const countText = document.querySelector("#whatsNewCount");
  const panel = document.querySelector("#whatsNewPanel");
  const body = document.querySelector("#whatsNewBody");
  const title = document.querySelector("#whatsNewTitle");
  const closeBtn = document.querySelector("#whatsNewClose");
  const backdrop = document.querySelector("#whatsNewBackdrop");
  if (!wrap || !button || !panel || !body) return;

  const state = { status: "idle", notes: [], latestId: null, newCount: 0, pills: 0, storageOk: true, fetching: null };

  // --- storage (never throws, never logs) ---
  function readSeen() {
    try { return window.localStorage.getItem(KEY); } catch (error) { state.storageOk = false; return null; }
  }
  function writeSeen(id) {
    if (!id) return;
    try { window.localStorage.setItem(KEY, id); } catch (error) { state.storageOk = false; }
  }

  // --- data ---
  const isNote = (n) => n && typeof n === "object" && typeof n.id === "string" && n.id &&
    typeof n.title === "string" && typeof n.body === "string" && /^\d{4}-\d{2}-\d{2}$/.test(String(n.date || ""));
  function liveNotes(data) {
    if (!data || typeof data !== "object" || !Array.isArray(data.notes)) throw new Error("invalid");
    return data.notes
      .map((note, index) => ({ note, index }))
      .filter(({ note }) => isNote(note) && note.live_flag === true && (note.app || APP) === APP)
      .sort((a, b) => (a.note.date < b.note.date ? 1 : a.note.date > b.note.date ? -1 : a.index - b.index))
      .map(({ note }) => note);
  }

  function load() {
    state.status = "loading";
    const request = fetch(URL, { cache: "no-cache", priority: "low" })
      .then((response) => { if (!response.ok) throw new Error("http"); return response.json(); })
      .then((data) => {
        state.notes = liveNotes(data);
        state.latestId = state.notes.length ? state.notes[0].id : null;
        state.status = state.notes.length ? "ready" : "empty";
      })
      .catch(() => { state.notes = []; state.latestId = null; state.status = "error"; })
      .finally(() => { state.fetching = null; wrap.dataset.status = state.status; });
    state.fetching = request;
    return request;
  }

  // --- indicator ---
  const plural = (n) => `${n} new update${n === 1 ? "" : "s"}`;
  function setIndicator(count, { fade = false } = {}) {
    state.newCount = count;
    if (count > 0) {
      button.dataset.state = "new";
      if (fade) button.setAttribute("data-fade-once", "");
      button.setAttribute("aria-label", `What's new, ${plural(count)}`);
      if (countText) countText.textContent = plural(count);
      if (tip) tip.textContent = `What's new · ${count} update${count === 1 ? "" : "s"}`;
    } else {
      button.dataset.state = "none";
      button.removeAttribute("data-fade-once");
      button.setAttribute("aria-label", "What's new");
      if (countText) countText.textContent = "";
      if (tip) tip.textContent = "What's new";
    }
  }

  // Decide the glow from the loaded notes and the stored id (spec §5).
  function evaluate({ fade }) {
    if (state.status !== "ready" || !state.latestId) { setIndicator(0); return; }
    const seen = readSeen();
    if (!state.storageOk) { setIndicator(0); return; }
    if (seen === null) { writeSeen(state.latestId); setIndicator(0); return; }       // first visit: no glow
    if (seen === state.latestId) { setIndicator(0); return; }
    const index = state.notes.findIndex((note) => note.id === seen);
    if (index < 0) { writeSeen(state.latestId); setIndicator(0); return; }            // retracted/pruned id
    setIndicator(index, { fade });
  }

  // --- panel rendering ---
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  const DATE_FMT = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  function render() {
    panel.removeAttribute("aria-busy");
    if (state.status === "loading" || state.status === "idle") {
      panel.setAttribute("aria-busy", "true");
      const skel = el("ul", "wn-skel");
      skel.setAttribute("aria-hidden", "true");
      for (let i = 0; i < 2; i += 1) { const li = el("li"); li.append(el("span"), el("span")); skel.append(li); }
      body.replaceChildren(skel);
      return;
    }
    if (state.status === "error") {
      const box = el("div", "wn-state");
      box.append(el("p", "", "Release notes couldn't load. Try again."));
      const retry = el("button", "wn-retry", "Retry");
      retry.type = "button";
      retry.addEventListener("click", () => {
        load().then(() => { if (isOpen()) { markSeen(state.status === "ready" ? pendingPills() : 0); render(); } });
        render();
        title.focus();
      });
      box.append(retry);
      body.replaceChildren(box);
      return;
    }
    if (state.status === "empty") {
      const box = el("div", "wn-state");
      box.append(el("p", "", "Nothing new yet."));
      body.replaceChildren(box);
      return;
    }
    const list = el("ol", "wn-list");
    state.notes.slice(0, MAX_NOTES).forEach((note, index) => {
      const item = el("li", "wn-item");
      const meta = el("div", "wn-meta");
      const time = el("time", "", DATE_FMT.format(new Date(`${note.date}T00:00:00Z`)));
      time.dateTime = note.date;
      meta.append(time);
      if (index < state.pills) meta.append(el("span", "wn-new", "New"));
      item.append(meta, el("h3", "", note.title), el("p", "", note.body));
      if (note.link && typeof note.link === "object" && /^\/[a-z0-9/#-]*$/i.test(note.link.href || "") && note.link.text) {
        const a = el("a", "", note.link.text);
        a.href = note.link.href;
        item.append(a);
      }
      list.append(item);
    });
    body.replaceChildren(list);
  }

  // Opening = seen: clear the glow immediately, write the latest id, keep this open's "New" pills.
  function markSeen(pills) {
    state.pills = pills;
    setIndicator(0);
    if (state.status === "ready") { readSeen(); if (state.storageOk) writeSeen(state.latestId); }
  }

  // --- open / close ---
  const isOpen = () => !panel.hidden;
  const focusables = () => [...panel.querySelectorAll("button, a[href], [tabindex='0']")].filter((n) => !n.hidden && n.offsetParent !== null);
  let modal = false;

  function place() {
    if (MOBILE.matches) { panel.style.left = ""; panel.style.top = ""; return; }
    const band = document.querySelector(".top-nav");
    const b = button.getBoundingClientRect();
    const w = Math.min(380, window.innerWidth - 32);
    panel.style.width = `${w}px`;
    panel.style.top = `${Math.round((band ? band.getBoundingClientRect().bottom : b.bottom) + 8)}px`;
    panel.style.left = `${Math.round(Math.max(16, Math.min(window.innerWidth - w - 16, b.left + b.width / 2 - w / 2)))}px`;
  }

  function open() {
    if (isOpen()) { title.focus(); return; }
    hideTip();
    markSeen(state.newCount);
    modal = MOBILE.matches;
    if (modal) panel.setAttribute("aria-modal", "true"); else panel.removeAttribute("aria-modal");
    if (backdrop) backdrop.hidden = !modal;
    panel.hidden = false;
    button.setAttribute("aria-expanded", "true");
    place();
    if (state.status === "idle" || state.status === "error") load();
    if (state.fetching) state.fetching.then(() => { if (isOpen()) { markSeen(state.status === "ready" ? pendingPills() : 0); render(); } });
    render();
    title.focus();
  }
  // Notes newer than the stored id, when the panel was opened before the fetch finished.
  function pendingPills() {
    const seen = readSeen();
    if (!state.storageOk || seen === null) return 0;
    const index = state.notes.findIndex((note) => note.id === seen);
    return index < 0 ? 0 : index;
  }

  function close({ restoreFocus = true } = {}) {
    if (!isOpen()) return;
    panel.hidden = true;
    if (backdrop) backdrop.hidden = true;
    button.setAttribute("aria-expanded", "false");
    state.pills = 0;
    if (restoreFocus) button.focus({ preventScroll: true });
  }

  button.addEventListener("click", () => (isOpen() ? close() : open()));
  closeBtn?.addEventListener("click", () => close());
  backdrop?.addEventListener("click", () => close());
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (event.key !== "Tab") return;
    const items = focusables();
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    if (modal) {                                   // mobile sheet: trap focus
      if (event.shiftKey && (active === first || active === title)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && active === last) { event.preventDefault(); (title || first).focus(); }
      return;
    }
    // Desktop popover: Tab past the end closes and continues to Search actions; Shift+Tab off the top returns.
    if (!event.shiftKey && (active === last || !items.length)) {
      event.preventDefault(); close({ restoreFocus: false });
      (document.querySelector("#paletteTrigger") || button).focus();
    } else if (event.shiftKey && active === title) {
      event.preventDefault(); close();
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (!isOpen() || modal) return;
    if (panel.contains(event.target) || wrap.contains(event.target)) return;
    close({ restoreFocus: false });
  });
  window.addEventListener("resize", () => { if (isOpen()) place(); });

  // --- tooltip: 400ms hover delay, instant on focus, Escape hides ---
  let tipTimer = 0;
  function showTip() { if (!isOpen()) wrap.classList.add("show-tip"); }
  function hideTip() { clearTimeout(tipTimer); wrap.classList.remove("show-tip"); }
  button.setAttribute("aria-describedby", "whatsNewTip");
  button.addEventListener("pointerenter", () => { clearTimeout(tipTimer); tipTimer = setTimeout(showTip, 400); });
  button.addEventListener("pointerleave", hideTip);
  button.addEventListener("focus", () => { if (button.matches(":focus-visible")) showTip(); });
  button.addEventListener("blur", hideTip);
  button.addEventListener("keydown", (event) => { if (event.key === "Escape" && wrap.classList.contains("show-tip")) { event.preventDefault(); hideTip(); } });

  // Another tab opened the panel: clear the glow here too (nice-to-have, spec §5).
  window.addEventListener("storage", (event) => {
    if (event.key === KEY && event.newValue && event.newValue === state.latestId) setIndicator(0);
  });

  // --- boot: after first paint, at low priority ---
  function boot() { load().then(() => { evaluate({ fade: true }); wrap.dataset.ready = "1"; }); }
  const afterPaint = () => requestAnimationFrame(() => setTimeout(() => {
    if ("requestIdleCallback" in window) window.requestIdleCallback(boot, { timeout: 1500 }); else boot();
  }, 0));
  if (document.readyState === "complete") afterPaint(); else window.addEventListener("load", afterPaint, { once: true });

  window.DiaWhatsNew = { open, close, isOpen };
})();
