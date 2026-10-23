// Google Sign-In for Ask DIA, plus the visitor quota badge.
//
// Visitors get a few free questions a day (the server enforces it). Signing in
// with Google sends the ID token once to /api/auth/session; the server checks
// it and sets an HttpOnly session cookie, so no token is kept in this page.
// Owner emails (server-side OWNER_EMAILS) are unlimited; anyone else who signs
// in still gets the visitor cap. Without GOOGLE_CLIENT_ID nothing is shown.
(function () {
  "use strict";

  const GIS_SRC = "https://accounts.google.com/gsi/client";

  // --- quota badge next to the Ask DIA status ------------------------------

  const agentStatus = document.querySelector("#agentStatus");
  const quotaBadge = document.createElement("small");
  quotaBadge.id = "agentQuota";
  quotaBadge.className = "agent-quota";
  quotaBadge.setAttribute("aria-live", "polite");
  quotaBadge.style.marginLeft = "0.5em";
  quotaBadge.style.opacity = "0.75";
  quotaBadge.style.fontWeight = "normal";
  if (agentStatus) agentStatus.insertAdjacentElement("afterend", quotaBadge);

  function describe(status) {
    if (!status) return "";
    if (status.enabled === false) return ""; // kill switch: app.js shows the notice
    if (status.owner) return "Owner: unlimited";
    const quota = status.quota;
    if (!quota) return "";
    if (quota.remaining <= 0) return `Today's ${quota.limit} questions used`;
    return `${quota.remaining} of ${quota.limit} questions left today`;
  }

  let lastStatus = null;

  async function refreshQuota() {
    try {
      const response = await fetch("/api/agent/status", { credentials: "same-origin" });
      lastStatus = await response.json();
    } catch (error) {
      lastStatus = null;
    }
    quotaBadge.textContent = describe(lastStatus);
    renderAccount();
    return lastStatus;
  }

  // Refresh after every agent reply (app.js writes replies and errors,
  // including the daily-limit message, into #agentOutput).
  const agentOutput = document.querySelector("#agentOutput");
  if (agentOutput && "MutationObserver" in window) {
    let timer = null;
    new MutationObserver(() => {
      clearTimeout(timer);
      timer = setTimeout(refreshQuota, 300);
    }).observe(agentOutput, { childList: true, characterData: true, subtree: true });
  }

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
    } else if (agentStatus) {
      agentStatus.insertAdjacentElement("afterend", account);
    } else {
      document.body.prepend(account);
    }
    if (settingsSection) settingsSection.hidden = !settingsSection.contains(account);
  }

  account.append(gisSlot, signOut);
  place();
  if (narrow.addEventListener) narrow.addEventListener("change", place);

  let config = null;

  // Inline display (not just [hidden]) so band classes can't force it visible.
  function show(element, visible, display) {
    element.hidden = !visible;
    element.style.display = visible ? display : "none";
  }

  function renderAccount() {
    if (!config || !config.enabled) {
      show(account, false);
      if (settingsSection) settingsSection.hidden = true;
      return;
    }
    show(account, true, "inline-flex");
    if (settingsSection) settingsSection.hidden = !settingsSection.contains(account);
    const status = lastStatus || config;
    const signedIn = Boolean(status && status.signed_in);
    show(gisSlot, !signedIn, "inline-block");
    show(signOut, signedIn, "");
    if (signedIn) {
      signOut.textContent = status.owner ? "Owner · Sign out" : "Sign out";
      signOut.title = `Signed in as ${status.email}`;
      signOut.setAttribute("aria-label", `Sign out (${status.email})`);
    }
  }

  async function onCredential(response) {
    try {
      const reply = await fetch("/api/auth/session", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ credential: response && response.credential }),
      });
      if (!reply.ok) {
        const payload = await reply.json().catch(() => ({}));
        quotaBadge.textContent = payload.error || "Sign-in failed.";
      }
    } catch (error) {
      quotaBadge.textContent = "Sign-in failed.";
    }
    await refreshQuota();
  }

  signOut.addEventListener("click", async () => {
    try {
      await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
    } catch (error) {
      /* cookie expires on its own */
    }
    const gis = window.google && window.google.accounts && window.google.accounts.id;
    if (gis) gis.disableAutoSelect();
    await refreshQuota();
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
      gis.renderButton(gisSlot, {
        type: "standard",
        theme: "outline",
        size: "small",
        text: "signin",
        shape: "rectangular",
      });
    };
    document.head.append(script);
  }

  async function init() {
    try {
      const response = await fetch("/api/auth/config", { credentials: "same-origin" });
      config = await response.json();
    } catch (error) {
      config = null;
    }
    if (config && config.enabled && config.google_client_id) startGis(config.google_client_id);
    renderAccount();
    refreshQuota();
  }

  window.DIA_ACCOUNT = { refreshQuota, describe };
  init();
})();
