// Google Sign-In for DIA (lockdown v2: no use without signing in, same as Tia).
//
// Signed out, the page is read-only: the composers are replaced by a quiet
// sign-in prompt and every functional route answers 401 on the server.
// Signing in sends the Google ID token once to /api/auth/session; the server
// verifies it and sets a short-lived HttpOnly session cookie, so no token is
// kept in this page. The usage counter (app.js, window.DIA_USAGE) shows only
// when signed in and only ever displays the server's numbers.
(function () {
  "use strict";

  const GIS_SRC = "https://accounts.google.com/gsi/client";
  const usage = window.DIA_USAGE || { refresh: async () => null, setSignedOut() {}, render() {} };

  // --- account control in the top bar's right corner -------------------------

  const account = document.createElement("span");
  account.id = "diaAccount";
  account.className = "dia-account";
  account.hidden = true;
  account.style.alignItems = "center";
  account.style.gap = "0.5em";

  const gisSlot = document.createElement("span");
  gisSlot.id = "diaSignIn";

  const signOut = document.createElement("button");
  signOut.type = "button";
  signOut.id = "diaSignOut";
  signOut.hidden = true;
  signOut.style.whiteSpace = "nowrap";
  account.style.display = "none";

  const themeToggle = document.querySelector("#themeToggle");
  const band = themeToggle ? themeToggle.closest("header") : null;
  const letterhead = Boolean(band && band.classList.contains("lh"));
  const helpDialog = document.querySelector("#diaHelpDialog");
  const gateSlot = document.querySelector("#signInGateButton");
  const statusText = document.querySelector("#status");
  // Letterhead mobile breakpoint: the band has no room for another control,
  // so on narrow screens the account moves into Help & settings.
  const narrow = window.matchMedia ? window.matchMedia("(max-width: 600px)") : { matches: false };

  let settingsSection = null;
  function settings() {
    if (!settingsSection) {
      settingsSection = document.createElement("section");
      settingsSection.className = "help-settings dia-account-settings";
      const title = document.createElement("h3");
      title.textContent = "Account";
      settingsSection.append(title);
      const closeForm = helpDialog.querySelector('form[method="dialog"]');
      helpDialog.insertBefore(settingsSection, closeForm || null);
    }
    return settingsSection;
  }

  function place() {
    if (letterhead && narrow.matches && helpDialog) {
      signOut.className = "";
      settings().append(account);
    } else if (themeToggle) {
      // Top bar's right corner, just before the theme toggle.
      signOut.className = letterhead ? "lh-control" : "ghost";
      themeToggle.insertAdjacentElement("beforebegin", account);
    } else {
      document.body.prepend(account);
    }
    if (settingsSection) settingsSection.hidden = !settingsSection.contains(account);
  }

  account.append(gisSlot, signOut);
  place();
  if (narrow.addEventListener) narrow.addEventListener("change", place);

  let config = null;
  let state = null;

  // Inline display (not just [hidden]) so band classes can't force it visible.
  function show(element, visible, display) {
    element.hidden = !visible;
    element.style.display = visible ? display : "none";
  }

  function renderAccount() {
    const signedIn = Boolean(state && state.signed_in);
    usage.setSignedOut(!signedIn, { configured: Boolean(config && config.enabled) });
    if (gateSlot) gateSlot.hidden = !(config && config.enabled);
    if (!config || !config.enabled) {
      show(account, false);
      if (settingsSection) settingsSection.hidden = true;
      return;
    }
    show(account, true, "inline-flex");
    if (settingsSection) settingsSection.hidden = !settingsSection.contains(account);
    show(gisSlot, !signedIn, "inline-block");
    show(signOut, signedIn, "");
    if (signedIn) {
      signOut.textContent = "Sign out";
      signOut.title = state.email ? `Signed in as ${state.email}` : "Signed in";
      signOut.setAttribute("aria-label", state.email ? `Sign out (${state.email})` : "Sign out");
    }
  }

  async function refreshState() {
    try {
      const response = await fetch("/api/auth/config", { credentials: "same-origin" });
      const payload = await response.json();
      config = config || payload;
      state = payload;
    } catch (error) {
      state = null;
    }
    renderAccount();
    if (state && state.signed_in) await usage.refresh();
    return state;
  }

  async function onCredential(response) {
    try {
      const reply = await fetch("/api/auth/session", {
        method: "POST",
        credentials: "same-origin",
        // As in Tia: the GIS credential goes in "Authorization: Bearer". It is
        // exchanged once for an HttpOnly session cookie and not kept here.
        headers: {
          "Content-Type": "application/json",
          Authorization: "Bearer " + ((response && response.credential) || ""),
        },
        body: "{}",
      });
      if (!reply.ok && statusText) {
        const payload = await reply.json().catch(() => ({}));
        statusText.textContent = payload.error || "Sign-in didn't go through. Try again.";
      }
    } catch (error) {
      if (statusText) statusText.textContent = "Sign-in didn't go through. Try again.";
    }
    await refreshState();
  }

  signOut.addEventListener("click", async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    } catch (error) {
      /* cookie expires on its own */
    }
    const gis = window.google && window.google.accounts && window.google.accounts.id;
    if (gis) gis.disableAutoSelect();
    await refreshState();
  });

  function startGis(clientId) {
    const script = document.createElement("script");
    script.src = GIS_SRC;
    script.async = true;
    script.defer = true;
    script.onload = () => {
      const gis = window.google && window.google.accounts && window.google.accounts.id;
      if (!gis) return;
      gis.initialize({ client_id: clientId, callback: onCredential, auto_select: false });
      gis.renderButton(gisSlot, { type: "standard", theme: "outline", size: "small", text: "signin", shape: "rectangular" });
      if (gateSlot) {
        // Tia's sign-in card button: outline, large, "Sign in with", pill, fitted width.
        let renderedWidth = 0;
        const fit = () => {
          const width = Math.min(400, Math.floor(gateSlot.getBoundingClientRect().width));
          if (width <= 0 || width === renderedWidth) return;
          renderedWidth = width;
          gateSlot.replaceChildren();
          gis.renderButton(gateSlot, { theme: "outline", size: "large", text: "signin_with", shape: "pill", width: String(width) });
        };
        fit();
        if (typeof ResizeObserver !== "undefined") new ResizeObserver(fit).observe(gateSlot);
      }
    };
    document.head.append(script);
  }

  async function init() {
    await refreshState();
    if (config && config.enabled && config.google_client_id) startGis(config.google_client_id);
  }

  window.DIA_ACCOUNT = { refresh: refreshState };
  init();
})();
