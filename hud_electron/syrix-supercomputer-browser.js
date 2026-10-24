/* SYRIX Supercomputer browser — isolated Electron module (2026-07-24).
 *
 * Owns the ENTIRE Supercomputer browser surface: its own tab registry, its own
 * ephemeral session partition, its own `sc-browser:*` IPC namespace, its own
 * hardening, and its own teardown. main.js does nothing but `require` this
 * lazily on first use and call dispose() on quit.
 *
 * ── The control boundary is structural, not advisory ──────────────────────
 * Every handler resolves its target from `_views`, a Map this module alone
 * writes. There is no expressible operation against the main HUD window, the
 * existing HUD app dock, another application, or the desktop, because no other
 * webContents is reachable from here. That is the enforcement §4 asks for.
 *
 * Deliberately NOT shared with the older app-dock automation in main.js: the
 * digest and trusted-input techniques are re-implemented here rather than
 * refactored out, so the dock's proven code path is not modified at all and
 * cannot regress.
 *
 * ── Session: the SAME profile as the HUD browser ──────────────────────────
 * Supercomputer runs in the HUD's persistent browser partition (the profile
 * behind the HUD's own browser section), NOT a blank ephemeral one. That is a
 * deliberate reversal of the original design: an agent that is signed out of
 * every account cannot read your mail, open your docs or see your subscriptions,
 * which is most of what this mode is for. `install({ sharedPartition })` carries
 * the id in from main.js; when it is absent we fall back to an ephemeral one.
 *
 * Consequence, and it is the whole point: pages here are logged in as you. Every
 * side-effecting action is therefore gated on the Python side by a permission
 * card (syrix_supercomputer/gate.py) — the browser being able to reach your
 * account is not the same as the agent being allowed to act in it.
 *
 * ── Hardening (all default-deny) ──────────────────────────────────────────
 *   · http/https only; localhost, RFC1918 and link-local refused
 *   · every Chromium permission (mic, camera, geo, clipboard, …) denied
 *   · downloads blocked outright
 *   · window.open and cross-policy navigation refused
 *   · no preload, no Node, sandboxed renderer, webSecurity on
 *   · password fields refuse programmatic typing
 *   · a shared partition is NEVER storage-cleared on dispose (that would sign
 *     the user out of the HUD browser too); only an ephemeral one is
 */
'use strict';

const MAX_VIEWS = 12;
const DIGEST_MAX_ELEMENTS = 120;
// Keep a larger private browser-side buffer so Python can request subsequent
// slices of a long public page without another navigation.  Each tool
// observation remains small; this is only the source window for those slices.
const DIGEST_MAX_TEXT = 18000;

let _electron = null;          // { ipcMain, session, BrowserView }
let _getMainWindow = null;
let _log = () => {};
let _installed = false;
let _partition = '';
let _sharedPartition = '';     // set by main.js — the HUD browser's own profile
let _userAgent = '';           // Electron-free UA; see resolveUserAgent()
let _views = new Map();        // id -> { view, url, title, favicon, loading, lastError }
let _order = [];               // tab order, so the strip is stable
let _activeId = '';
let _seq = 0;
let _bounds = null;            // { x, y, width, height } in main-window coords
let _hidden = false;           // chat expanded over the browser, or drawer full
let _sessionHardened = false;
let _annotating = false;

/* ── Downloads ──────────────────────────────────────────────────────────────
 * Blocked by default. The Python side arms exactly one download after the user has
 * answered a permission card, naming the folder it may land in; the next
 * will-download consumes that arming and every later one is blocked again.
 * Single-use and time-limited on purpose — otherwise a page could sit and wait
 * for an approval meant for something else, then fire its own download into it.
 */
let _downloadGrant = null;     // { folder, expires }
let _lastDownload = null;      // outcome of the most recent attempt

// Refused whatever was approved. Approving "save that PDF" is not consent to
// save an installer, and the real filename is only known when it starts.
const UNSAFE_DOWNLOAD_RE =
  /\.(exe|msi|bat|cmd|com|scr|cpl|ps1|psm1|vbs|vbe|js|jse|wsf|wsh|hta|dll|sys|reg|inf|lnk|jar|apk|app|dmg|pkg|deb|rpm)$/i;

function takeDownloadGrant() {
  const g = _downloadGrant;
  _downloadGrant = null;                       // single use, always
  if (!g) return null;
  if (Date.now() > g.expires) return null;
  return g;
}

/* ── URL policy ─────────────────────────────────────────────────────────── */
/* The single exception to the private-host block: one SYRIX-owned preview
 * origin, registered as an exact host:port by the Python side when it starts a
 * workspace preview server. Mirrors policy.set_preview_origin — a single exact
 * authority, never a range, so this opens one server and not the loopback
 * interface. */
let _previewOrigin = '';

function urlAllowed(rawUrl) {
  const s = String(rawUrl || '').trim();
  if (!s) return { ok: false, reason: 'empty_url' };
  let u;
  try { u = new URL(s); } catch (_) { return { ok: false, reason: 'invalid_url' }; }
  const proto = (u.protocol || '').toLowerCase();
  if (proto !== 'http:' && proto !== 'https:') {
    return { ok: false, reason: 'scheme_blocked:' + proto };
  }
  if (_previewOrigin && (u.host || '').toLowerCase() === _previewOrigin) {
    return { ok: true, url: u.toString() };
  }
  // hostname (not host) strips userinfo and port, so
  // https://github.com@evil.example resolves to evil.example. Note WHATWG
  // keeps IPv6 brackets in hostname ("[::1]") where Python's urlparse strips
  // them — debracket here so both resolvers agree on what a loopback is.
  const host = (u.hostname || '').toLowerCase().replace(/\.$/, '').replace(/^\[|\]$/g, '');
  if (!host) return { ok: false, reason: 'no_host' };
  if (host === 'localhost' || host === '127.0.0.1' || host === '0.0.0.0'
      || host.endsWith('.localhost')) {
    return { ok: false, reason: 'private_host' };
  }
  // IPv6 loopback in any of its spellings (::1, 0:0:…:1).
  if (/^(?:0*:)+0*1$/.test(host) || host === '::1') {
    return { ok: false, reason: 'private_host' };
  }
  // IPv4-mapped IPv6 (::ffff:127.0.0.1) resolves to a loopback too.
  if (/^::ffff:/.test(host)) {
    const mapped = host.slice(7);
    if (/^127\./.test(mapped) || mapped === '0.0.0.0') {
      return { ok: false, reason: 'private_host' };
    }
    if (/^10\./.test(mapped) || /^192\.168\./.test(mapped)
        || /^172\.(1[6-9]|2\d|3[0-1])\./.test(mapped) || /^169\.254\./.test(mapped)) {
      return { ok: false, reason: 'private_lan' };
    }
  }
  if (/^10\./.test(host) || /^192\.168\./.test(host)
      || /^172\.(1[6-9]|2\d|3[0-1])\./.test(host)) {
    return { ok: false, reason: 'private_lan' };
  }
  if (/^169\.254\./.test(host)) return { ok: false, reason: 'link_local' };
  if (/^127\./.test(host)) return { ok: false, reason: 'private_host' };
  return { ok: true, url: u.toString() };
}

/* ── Session hardening ──────────────────────────────────────────────────── */
const BLOCKED_PERMISSIONS = new Set([
  'background-sync', 'bluetooth', 'camera', 'clipboard-read',
  'clipboard-sanitized-write', 'clipboard-write', 'display-capture',
  'file-system', 'filesystem', 'geolocation', 'hid', 'idle-detection',
  'local-fonts', 'media', 'mediaKeySystem', 'microphone', 'midi', 'midiSysex',
  'notifications', 'openExternal', 'payment-handler', 'pointerLock', 'serial',
  'usb', 'videoCapture', 'window-management',
]);

function partitionId() {
  if (!_partition) {
    // Prefer the HUD browser's own persistent profile so the user is already signed
    // in to Google, YouTube, Gmail and everything else. Falling back to an
    // ephemeral id keeps tests and any main.js that predates sharedPartition
    // working, but that path is signed out of everything.
    _partition = _sharedPartition
      || ('syrix-supercomputer-' + Date.now().toString(36) + '-'
          + Math.random().toString(36).slice(2, 8));
  }
  return _partition;
}

/* True when the active partition belongs to the HUD browser rather than to us.
 * A shared profile must never be storage-cleared on dispose — that would sign
 * the user out of the HUD browser as a side effect of leaving Supercomputer. */
function usingSharedPartition() {
  return !!_sharedPartition && _partition === _sharedPartition;
}

/* Google refuses its sign-in flow to any user-agent containing "Electron", and
 * several sites serve a degraded page to it. Strip the Electron token (and our
 * own product token) so pages see a plain Chrome UA. This changes only how we
 * identify ourselves; it defeats no security control on our side. */
function resolveUserAgent() {
  if (_userAgent) return _userAgent;
  let ua = '';
  try { ua = String(_electron.session.defaultSession.getUserAgent() || ''); } catch (_) { ua = ''; }
  if (!ua) return '';
  _userAgent = ua
    .replace(/\s*Electron\/[^\s]+/gi, '')
    .replace(/\s*SYRIX[^\s]*/gi, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
  return _userAgent;
}

function scSession() {
  return _electron.session.fromPartition(partitionId());
}

function hardenSession() {
  if (_sessionHardened) return;
  const ses = scSession();
  try {
    ses.setPermissionRequestHandler((_wc, permission, callback) => {
      const deny = BLOCKED_PERMISSIONS.has(String(permission || ''));
      if (deny) _log(`permission_denied permission=${permission}`);
      try { callback(!deny); } catch (_) {}
    });
  } catch (exc) { _log(`permission_handler_failed err=${exc && exc.message}`); }
  try {
    ses.setPermissionCheckHandler((_wc, permission) =>
      !BLOCKED_PERMISSIONS.has(String(permission || '')));
  } catch (exc) { _log(`permission_check_failed err=${exc && exc.message}`); }
  try { ses.setDevicePermissionHandler(() => false); } catch (_) {}
  try { ses.setDisplayMediaRequestHandler((_r, cb) => { try { cb({}); } catch (_) {} }); } catch (_) {}
  // Downloads are blocked by default and stay blocked unless the Python side
  // has armed exactly one, which it only does after the user answered a permission
  // card. The arming is single-use and expires, so a page cannot wait around
  // for a later approval and fire its own download into it.
  try {
    ses.on('will-download', (event, item) => {
      const armed = takeDownloadGrant();
      if (!armed) {
        try { event.preventDefault(); } catch (_) {}
        _log('download_blocked (not approved)');
        return;
      }
      let name = 'download';
      try { name = item.getFilename() || name; } catch (_) {}
      // Executables are refused whatever was approved: "yes, save that PDF" is
      // not consent to run an installer, and the file's real name is only known
      // here, at the moment it starts.
      if (UNSAFE_DOWNLOAD_RE.test(name)) {
        try { event.preventDefault(); } catch (_) {}
        _log(`download_refused_unsafe name=${name.slice(0, 60)}`);
        _lastDownload = { ok: false, reason: 'unsafe_file_type', name };
        return;
      }
      try { item.setSavePath(require('path').join(armed.folder, name)); } catch (_) {}
      _log(`download_started name=${name.slice(0, 60)}`);
      try {
        item.once('done', (_e, state) => {
          let savePath = '';
          try { savePath = item.getSavePath(); } catch (_) {}
          _lastDownload = { ok: state === 'completed', reason: String(state), name, path: savePath };
          _log(`download_done name=${name.slice(0, 60)} state=${state}`);
        });
      } catch (_) {}
    });
  } catch (_) {}
  for (const ev of ['select-usb-device', 'select-serial-port',
                    'select-bluetooth-device', 'select-hid-device']) {
    try {
      ses.on(ev, (event, _d, callback) => {
        try { event.preventDefault(); } catch (_) {}
        try { callback(''); } catch (_) {}
      });
    } catch (_) {}
  }
  _sessionHardened = true;
  _log(`session_hardened partition=${partitionId()}`);
}

/* ── The in-page digest ─────────────────────────────────────────────────────
 * Returns DATA about the page: a compact list of actionable elements, each
 * tagged with a stable id, plus readable text. The agent addresses elements by
 * that id, never by anything the page authored, and the returned text is fed to
 * the model inside an untrusted-content envelope on the Python side.
 */
const DIGEST_JS = `(() => {
  const out = {
    title: document.title || "", url: location.href, elements: [],
    links: [], headings: [], meta: {},
  };
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return null;
    if (r.bottom < 0 || r.top > (window.innerHeight + 900)) return null;
    const st = window.getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || st.opacity === "0") return null;
    return r;
  };
  const clean = (s) => String(s || "").replace(/\\s+/g, " ").trim();
  const abs = (href) => { try { return new URL(href, location.href).toString(); } catch (_) { return ""; } };

  /* ── actionable elements ── */
  let n = 0;
  const sel = 'a,button,input,textarea,select,[role=button],[role=link],[role=tab],[role=searchbox],[role=menuitem],[role=option],[role=checkbox],[contenteditable=true],[onclick]';
  const seen = new Set();
  document.querySelectorAll(sel).forEach((el) => {
    if (n >= ${DIGEST_MAX_ELEMENTS}) return;
    const r = vis(el); if (!r) return;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    let label = clean(el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.getAttribute("title") || el.getAttribute("name") || el.value || el.innerText || el.textContent || "").slice(0, 110);
    if (!label && tag === "input") label = type || "input";
    const key = tag + "|" + label + "|" + Math.round(r.top);
    if (seen.has(key)) return; seen.add(key);
    let id = el.getAttribute("data-sc-id");
    if (!id) { id = "e" + (++n); el.setAttribute("data-sc-id", id); }
    out.elements.push({
      id, tag, type, label,
      role: el.getAttribute("role") || "",
      href: tag === "a" ? abs(el.getAttribute("href") || "").slice(0, 400) : "",
      editable: (tag === "input" && !["button","submit","checkbox","radio","hidden"].includes(type)) || tag === "textarea" || el.isContentEditable === true,
      isPassword: type === "password",
      // Where it sits, so the agent can tell "above the fold" from "far down".
      y: Math.round(r.top + (window.scrollY || 0)),
    });
  });

  /* ── links, deduped by destination ──
   * Navigation is the agent's main move on a results/listing page, and hunting
   * for hrefs inside the element list is unreliable, so they get their own
   * list with readable anchor text. */
  const linkSeen = new Set();
  const chromeLinks = [];
  document.querySelectorAll('a[href]').forEach((a) => {
    if (out.links.length + chromeLinks.length >= 90) return;
    const href = abs(a.getAttribute('href'));
    if (!href || !/^https?:/i.test(href)) return;
    if (linkSeen.has(href)) return;
    const text = clean(a.innerText || a.getAttribute('aria-label') || a.getAttribute('title'));
    if (!text || text.length < 2) return;
    linkSeen.add(href);
    /* Site furniture vs the content itself. Links come out in DOM order, and on
     * every news/listing site the first two dozen are the masthead menu — so a
     * budget of 18 handed the agent "News, Opinion, Sport, Culture…" and not one
     * headline, and it then reported it could not find the article. Chrome links
     * are kept, but after the content, where the budget can no longer starve it. */
    let chrome = false;
    try {
      chrome = !!a.closest('nav,header,footer,[role=navigation],[role=banner],[role=contentinfo],[class*="nav" i],[class*="menu" i],[id*="nav" i]');
    } catch (_) {}
    const row = { text: text.slice(0, 140), href: href.slice(0, 400), id: a.getAttribute('data-sc-id') || "" };
    if (chrome) chromeLinks.push(row); else out.links.push(row);
  });
  out.links = out.links.concat(chromeLinks).slice(0, 60);

  document.querySelectorAll('h1,h2,h3').forEach((h) => {
    if (out.headings.length >= 40) return;
    const t = clean(h.innerText);
    if (t) out.headings.push({ level: Number(h.tagName.slice(1)), text: t.slice(0, 160) });
  });

  /* ── readable text ──
   * Prefer the semantic content root and drop chrome, so the model reads the
   * article instead of the navigation menu repeated four times. */
  const root = document.querySelector('main,article,[role=main]') || document.body;
  let text = "";
  try {
    const clone = root.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,nav,header,footer,aside,svg,[aria-hidden=true]').forEach((x) => x.remove());
    // A hidden BrowserView can make innerText silently omit collapsed or
    // off-screen rows while still returning a plausible-looking opening
    // paragraph.  That is worse than an empty read: the agent believes it has
    // the whole schedule/report and may stop early.  The detached semantic
    // clone has no layout requirement, so retain its textContent whenever it
    // materially contains more of the same cleaned page.
    const rendered = clone.innerText || "";
    const complete = clone.textContent || "";
    text = clean(complete).length > clean(rendered).length * 1.15
      ? complete : rendered;
  } catch (_) { text = (document.body && document.body.innerText) || ""; }
  /* The last-resort fallback must NOT use innerText on the live body.
   * innerText is layout-dependent and this BrowserView is usually HIDDEN (the
   * workspace shows one tab at a time), and Chromium does not lay out a hidden
   * view — measured on a real Gmail tab, body.innerText returned 313 characters
   * where body.textContent returned 225,228. So the one path that exists to
   * rescue a page the clone could not read was returning nothing, and the agent
   * would honestly report "the page was truncated" or "nothing was found".
   * The clone above is a detached node, where innerText already falls back to
   * textContent by spec, which is why that path works and this one did not. */
  if (document.body) {
    const rendered = document.body.innerText || "";
    const complete = document.body.textContent || "";
    if (clean(text).length < 200 || clean(complete).length > clean(text).length * 1.15) {
      text = clean(complete).length > clean(rendered).length * 1.15 ? complete : rendered;
    }
  }
  out.text = text.replace(/[ \\t]+/g, " ").replace(/\\n{3,}/g, "\\n\\n").trim().slice(0, ${DIGEST_MAX_TEXT});

  /* ── page state, so the agent knows whether there is more below ── */
  const de = document.documentElement || {};
  out.meta = {
    scrollY: Math.round(window.scrollY || 0),
    scrollHeight: Math.round(de.scrollHeight || 0),
    viewportHeight: Math.round(window.innerHeight || 0),
    atBottom: (window.scrollY || 0) + (window.innerHeight || 0) >= (de.scrollHeight || 0) - 80,
    truncated: text.length > ${DIGEST_MAX_TEXT},
    description: clean((document.querySelector('meta[name=description]') || {}).content || ""),
    readyState: document.readyState || "",
  };
  return out;
})()`;

/* ── Cookie / consent banners ───────────────────────────────────────────────
 * A consent wall covers the content on most UK and EU sites. Left alone the
 * agent reads the banner instead of the page, concludes the page has nothing,
 * and — worst of all — falls back on what it remembers. That is how "I opened
 * nvidia.com/rtx-5090" turned into "that GPU does not exist".
 *
 * So consent is handled as browser chrome, automatically, once per page load,
 * and it never costs the agent a step or a permission card. It is not the agent
 * "agreeing to terms on the user's behalf": the preference order is REJECT first —
 * reject-all, then necessary-only — and accept is the last resort for the sites
 * that offer no way past otherwise.
 *
 * Deliberately conservative about what it will click: the button must live
 * inside something that looks like a consent dialog (a known CMP container, or
 * a fixed/high-z-index overlay mentioning cookies), and its label must match a
 * short consent phrase. A stray "Accept" elsewhere on the page is not touched.
 */
const consentJs = (subframe) => `(() => {
  /* Sourcepoint, Didomi and several other CMPs render the whole dialog inside
   * an iframe, so a script that only ever ran in the main frame found no
   * buttons and left the wall standing — the agent then clicked at the page
   * behind it for two minutes and reported it could not read the article. In a
   * subframe the document IS the dialog, so the body itself is a candidate
   * root; the label patterns below still decide what may be clicked. */
  const SUBFRAME = ${subframe ? 'true' : 'false'};
  const CMP = [
    '#onetrust-banner-sdk', '#onetrust-consent-sdk', '#CybotCookiebotDialog',
    '.qc-cmp2-container', '#truste-consent-track', '#didomi-popup',
    '.didomi-popup-container', '.sp_message_container', '[id^="sp_message_container"]',
    '#usercentrics-root', '.cmp-container', '#cookie-law-info-bar',
    '[aria-label*="cookie" i]', '[aria-describedby*="cookie" i]',
    '[class*="cookie-banner" i]', '[class*="cookie-consent" i]',
    '[id*="cookie-banner" i]', '[id*="cookie-consent" i]', '[class*="gdpr" i]',
  ];
  const REJECT = [
    /^reject all$/i, /^reject non-essential/i, /^decline all$/i, /^refuse all$/i,
    /^only necessary/i, /^necessary only/i, /^essential only/i,
    /^use necessary cookies only$/i, /^continue without accepting/i,
    /^reject$/i, /^decline$/i, /^deny$/i,
  ];
  const ACCEPT = [
    /^accept all$/i, /^allow all$/i, /^accept all cookies$/i, /^i agree$/i,
    /^agree$/i, /^got it$/i, /^ok$/i, /^accept$/i, /^allow cookies$/i,
    /^yes, i agree$/i, /^understood$/i,
  ];
  const clean = (s) => String(s || '').replace(/\\s+/g, ' ').trim();

  /* Roots that plausibly ARE a consent dialog. */
  const roots = [];
  for (const sel of CMP) {
    try { document.querySelectorAll(sel).forEach((el) => roots.push(el)); } catch (_) {}
  }
  // Plus any fixed/sticky overlay that actually mentions cookies or consent.
  try {
    document.querySelectorAll('div,section,aside,dialog').forEach((el) => {
      if (roots.length > 40) return;
      const st = window.getComputedStyle(el);
      if (st.position !== 'fixed' && st.position !== 'sticky') return;
      const r = el.getBoundingClientRect();
      if (r.width < 180 || r.height < 40) return;
      const t = clean(el.innerText).slice(0, 400).toLowerCase();
      if (/cookie|consent|gdpr|privacy choices|your privacy/.test(t)) roots.push(el);
    });
  } catch (_) {}
  // In a CMP iframe the dialog is the whole document — there is no fixed
  // overlay to find, because the overlay is the frame itself.
  if (SUBFRAME) {
    try {
      const t = clean(document.body && document.body.innerText).slice(0, 600).toLowerCase();
      if (/cookie|consent|gdpr|privacy|personalised advertising|personalized advertising/.test(t)) {
        roots.push(document.body);
      }
    } catch (_) {}
  }
  if (!roots.length) return { found: false };

  const visible = (el) => {
    try {
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) return false;
      const st = window.getComputedStyle(el);
      return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
    } catch (_) { return false; }
  };

  const pick = (patterns) => {
    for (const root of roots) {
      let controls = [];
      try {
        controls = Array.from(root.querySelectorAll(
          'button,a[role=button],input[type=button],input[type=submit],[role=button]'));
      } catch (_) { continue; }
      for (const c of controls) {
        if (!visible(c)) continue;
        const label = clean(c.innerText || c.value || c.getAttribute('aria-label'));
        if (!label || label.length > 40) continue;
        if (patterns.some((p) => p.test(label))) return { el: c, label };
      }
    }
    return null;
  };

  // Reject first, always. Accept only when there is no way to decline.
  const hit = pick(REJECT) || pick(ACCEPT);
  if (!hit) return { found: true, dismissed: false };
  try { hit.el.click(); } catch (_) { return { found: true, dismissed: false }; }
  return { found: true, dismissed: true, label: hit.label };
})()`;

/* Annotate picker: the user clicks an element, we return a description of it.
 * Resolves the executeJavaScript promise on click, so nothing needs a preload
 * or an IPC channel inside the page. Escape cancels. */
const PICK_JS = `new Promise((resolve) => {
  const prev = document.getElementById('__sc_pick_style__');
  if (prev) prev.remove();
  const style = document.createElement('style');
  style.id = '__sc_pick_style__';
  style.textContent = '.__sc_pick_hi__{outline:2px solid #f0a726 !important;outline-offset:1px !important;background:rgba(240,167,38,.12) !important;cursor:crosshair !important}';
  document.documentElement.appendChild(style);
  let current = null;
  const clear = () => {
    if (current) current.classList.remove('__sc_pick_hi__');
    current = null;
  };
  const teardown = () => {
    clear();
    style.remove();
    document.removeEventListener('mousemove', onMove, true);
    document.removeEventListener('click', onClick, true);
    document.removeEventListener('keydown', onKey, true);
  };
  const onMove = (e) => {
    const el = e.target;
    if (!el || el === current || el === document.documentElement) return;
    clear();
    current = el;
    el.classList.add('__sc_pick_hi__');
  };
  const onClick = (e) => {
    e.preventDefault(); e.stopPropagation();
    const el = e.target;
    let id = el.getAttribute && el.getAttribute('data-sc-id');
    if (!id && el.setAttribute) {
      id = 'p' + Math.random().toString(36).slice(2, 8);
      el.setAttribute('data-sc-id', id);
    }
    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : { width: 0, height: 0 };
    const text = (el.innerText || el.textContent || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 200);
    const out = {
      ok: true,
      element_id: id || '',
      tag: (el.tagName || '').toLowerCase(),
      label: (el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('placeholder'))) || text.slice(0, 80),
      text: text,
      href: (el.getAttribute && el.getAttribute('href')) || '',
      rect: { w: Math.round(r.width), h: Math.round(r.height) },
      url: location.href,
    };
    teardown();
    resolve(out);
  };
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); teardown(); resolve({ ok: false, reason: 'cancelled' }); }
  };
  document.addEventListener('mousemove', onMove, true);
  document.addEventListener('click', onClick, true);
  document.addEventListener('keydown', onKey, true);
})`;

/* ── Navigation history, across Electron versions ───────────────────────────
 * `webContents.navigationHistory` arrived in Electron 32. This app runs 31,
 * where the methods live directly on webContents — so `wc.navigationHistory
 * .canGoBack()` threw a TypeError, which propagated out of tabState(), which
 * made broadcast() throw, which its catch swallowed. The whole
 * `sc-browser:state` broadcast was therefore dead: the tab strip never updated
 * and Back/Forward were permanently greyed out because canGoBack never arrived.
 *
 * Prefer the new API, fall back to the old, and never throw — a navigation
 * query failing must not be able to take the browser's entire UI state with it.
 */
function navHistory(wc) {
  if (!wc || wc.isDestroyed()) return null;
  const nh = wc.navigationHistory;
  if (nh && typeof nh.canGoBack === 'function') return nh;
  return {
    canGoBack: () => (typeof wc.canGoBack === 'function' ? wc.canGoBack() : false),
    canGoForward: () => (typeof wc.canGoForward === 'function' ? wc.canGoForward() : false),
    goBack: () => { if (typeof wc.goBack === 'function') wc.goBack(); },
    goForward: () => { if (typeof wc.goForward === 'function') wc.goForward(); },
  };
}

function canGoBack(wc) {
  try { const h = navHistory(wc); return h ? !!h.canGoBack() : false; } catch (_) { return false; }
}

function canGoForward(wc) {
  try { const h = navHistory(wc); return h ? !!h.canGoForward() : false; } catch (_) { return false; }
}

/* ── Tab state broadcast ────────────────────────────────────────────────── */
function tabState() {
  const tabs = [];
  for (const id of _order) {
    if (!_views.has(id)) continue;
    // Per-tab guard: one destroyed or misbehaving view must not be able to
    // stop every other tab's state reaching the UI.
    try {
      const e = _views.get(id);
      const wc = e.view && !e.view.webContents.isDestroyed() ? e.view.webContents : null;
      tabs.push({
        id,
        url: e.url,
        title: e.title || hostOf(e.url) || 'New tab',
        favicon: e.favicon || '',
        loading: !!e.loading,
        lastError: e.lastError || '',
        canGoBack: canGoBack(wc),
        canGoForward: canGoForward(wc),
      });
    } catch (_) {
      tabs.push({ id, url: '', title: 'Tab', favicon: '', loading: false,
                  lastError: 'state_unavailable', canGoBack: false, canGoForward: false });
    }
  }
  const active = tabs.find((t) => t.id === _activeId) || null;
  return {
    tabs,
    active: _activeId,
    // The Electron module owns the session; report it so the Python side can
    // state the truth about which profile is in use rather than inventing one.
    // partitionId() rather than the raw _partition: the id is allocated lazily
    // on first view, so reading the raw field before a tab exists reports an
    // empty profile for a browser that is in fact about to use the shared one.
    partition: partitionId(),
    sharedProfile: usingSharedPartition(),
    annotating: _annotating,
    hidden: _hidden,
    url: active ? active.url : '',
    canGoBack: active ? active.canGoBack : false,
    canGoForward: active ? active.canGoForward : false,
    loading: active ? active.loading : false,
  };
}

function hostOf(u) {
  try { return new URL(String(u || '')).hostname.replace(/^www\./, ''); } catch (_) { return ''; }
}

function broadcast() {
  const win = _getMainWindow && _getMainWindow();
  if (!win || win.isDestroyed()) return;
  try { win.webContents.send('sc-browser:state', tabState()); } catch (_) {}
}

/* ── View management ────────────────────────────────────────────────────── */
function applyBounds(entry, isActive) {
  if (!entry) return;
  try {
    if (!isActive || !_bounds || _hidden) {
      // Park inactive/hidden views off-layout rather than destroying them, so
      // switching tabs is instant and no page state is lost.
      entry.view.setBounds({ x: 0, y: 0, width: 0, height: 0 });
      return;
    }
    entry.view.setBounds({
      x: Math.round(_bounds.x), y: Math.round(_bounds.y),
      width: Math.max(1, Math.round(_bounds.width)),
      height: Math.max(1, Math.round(_bounds.height)),
    });
  } catch (_) {}
}

function applyAllBounds() {
  for (const [id, entry] of _views.entries()) applyBounds(entry, id === _activeId);
  const win = _getMainWindow && _getMainWindow();
  const active = _views.get(_activeId);
  if (win && !win.isDestroyed() && active) {
    try { win.setTopBrowserView(active.view); } catch (_) {}
  }
}

function createView() {
  const win = _getMainWindow && _getMainWindow();
  if (!win || win.isDestroyed()) return { ok: false, reason: 'no_window' };
  if (_views.size >= MAX_VIEWS) return { ok: false, reason: 'too_many_tabs' };

  hardenSession();
  const view = new _electron.BrowserView({
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      backgroundThrottling: false,
      spellcheck: false,
      webviewTag: false,
      plugins: false,
      navigateOnDragDrop: false,
      partition: partitionId(),
      // No preload: page content has no Node access whatsoever.
    },
  });

  const id = 'sc' + (++_seq);
  const entry = { view, url: '', title: '', favicon: '', loading: false, lastError: '' };
  _views.set(id, entry);
  _order.push(id);

  const wc = view.webContents;
  const ua = resolveUserAgent();
  if (ua) { try { wc.setUserAgent(ua); } catch (_) {} }
  try { wc.setWindowOpenHandler(({ url }) => {
    // Never spawn an OS window. A same-policy target opens as a new tab here,
    // which keeps every page inside the sandbox.
    const verdict = urlAllowed(url);
    if (verdict.ok) {
      const made = createView();
      if (made.ok) {
        _views.get(made.id).view.webContents.loadURL(verdict.url).catch(() => {});
        _activeId = made.id;
        applyAllBounds();
        broadcast();
      }
    }
    return { action: 'deny' };
  }); } catch (_) {}
  try {
    wc.on('will-navigate', (event, navUrl) => {
      const verdict = urlAllowed(navUrl);
      if (!verdict.ok) {
        try { event.preventDefault(); } catch (_) {}
        entry.lastError = 'navigation_blocked:' + verdict.reason;
        _log(`navigation_blocked id=${id} reason=${verdict.reason}`);
        broadcast();
      }
    });
  } catch (_) {}
  try { wc.on('page-title-updated', (_e, title) => { entry.title = String(title || '').slice(0, 200); broadcast(); }); } catch (_) {}
  try { wc.on('page-favicon-updated', (_e, icons) => { entry.favicon = String((icons && icons[0]) || ''); broadcast(); }); } catch (_) {}
  try { wc.on('did-start-loading', () => { entry.loading = true; broadcast(); }); } catch (_) {}
  try { wc.on('did-stop-loading', () => { entry.loading = false; broadcast(); }); } catch (_) {}
  try { wc.on('did-navigate', (_e, navUrl) => { entry.url = String(navUrl || ''); entry.lastError = ''; broadcast(); }); } catch (_) {}
  /* Clear the consent wall as soon as the page is up. Two passes: many CMPs
   * mount after first paint, and one that arrives late would otherwise sit over
   * the content for the whole task. Failures are ignored on purpose — a page
   * without a banner is the normal case, not an error. */
  try {
    wc.on('did-finish-load', () => {
      const run = () => {
        if (wc.isDestroyed()) return;
        wc.executeJavaScript(consentJs(false), true).then((r) => {
          if (r && r.dismissed) _log(`consent_dismissed id=${id} label=${String(r.label).slice(0, 30)}`);
        }).catch(() => {});
        // …and in every subframe, where most of the big CMPs actually live.
        let frames = [];
        try { frames = (wc.mainFrame && wc.mainFrame.framesInSubtree) || []; } catch (_) { frames = []; }
        for (const f of frames) {
          if (!f || f === wc.mainFrame) continue;
          try {
            f.executeJavaScript(consentJs(true), true).then((r) => {
              if (r && r.dismissed) {
                _log(`consent_dismissed id=${id} frame label=${String(r.label).slice(0, 30)}`);
              }
            }).catch(() => {});
          } catch (_) {}
        }
      };
      setTimeout(run, 350);
      setTimeout(run, 1600);
      // A third, later pass: Sourcepoint and friends often mount their iframe
      // well after did-finish-load, and a wall that arrives at 2.5s used to
      // stand for the whole task.
      setTimeout(run, 4000);
    });
  } catch (_) {}
  try { wc.on('did-navigate-in-page', (_e, navUrl) => { entry.url = String(navUrl || ''); broadcast(); }); } catch (_) {}
  try {
    wc.on('did-fail-load', (_e, code, desc, failedUrl, isMainFrame) => {
      if (!isMainFrame || code === -3) return;  // -3 = aborted, normal
      entry.lastError = `load_failed:${code}:${String(desc || '').slice(0, 80)}`;
      entry.loading = false;
      _log(`load_failed id=${id} code=${code} url=${String(failedUrl).slice(0, 80)}`);
      broadcast();
    });
  } catch (_) {}
  try {
    wc.on('render-process-gone', (_e, details) => {
      entry.lastError = 'render_process_gone:' + ((details && details.reason) || 'unknown');
      _log(`render_process_gone id=${id} reason=${(details && details.reason) || 'unknown'}`);
      broadcast();
    });
  } catch (_) {}

  try { win.addBrowserView(view); } catch (_) {}
  _activeId = id;
  applyAllBounds();
  return { ok: true, id, entry };
}

function destroyView(id) {
  const entry = _views.get(id);
  if (!entry) return false;
  const win = _getMainWindow && _getMainWindow();
  try { if (win && !win.isDestroyed()) win.removeBrowserView(entry.view); } catch (_) {}
  try {
    const wc = entry.view.webContents;
    if (wc && !wc.isDestroyed()) wc.close();
  } catch (_) {}
  _views.delete(id);
  _order = _order.filter((x) => x !== id);
  if (_activeId === id) _activeId = _order.length ? _order[_order.length - 1] : '';
  applyAllBounds();
  return true;
}

function resolve(id) {
  const entry = _views.get(String(id || '') || _activeId || '');
  if (!entry) return null;
  if (!entry.view || entry.view.webContents.isDestroyed()) return null;
  return entry;
}

/* ── Element location (live re-read, never cached coordinates) ───────────── */
async function locate(wc, elementId) {
  const js = `(() => {
    const el = document.querySelector('[data-sc-id=' + JSON.stringify(${JSON.stringify(String(elementId || ''))}) + ']');
    if (!el) return null;
    const r0 = el.getBoundingClientRect();
    if (r0.width < 1 || r0.height < 1) return null;
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const r = el.getBoundingClientRect();
    const t = (el.getAttribute('type') || '').toLowerCase();
    return {
      cx: Math.round(r.left + r.width / 2),
      cy: Math.round(r.top + r.height / 2),
      isPassword: t === 'password',
    };
  })()`;
  return wc.executeJavaScript(js, true);
}

/* ── IPC surface ────────────────────────────────────────────────────────── */
function registerHandlers(ipcMain) {
  ipcMain.handle('sc-browser:open', async (_e, payload) => {
    const raw = (payload && payload.url) || '';
    const verdict = urlAllowed(raw);
    if (!verdict.ok) return { ok: false, reason: verdict.reason };

    let entry = null;
    let id = String((payload && payload.id) || '');
    if (id && _views.has(id)) {
      entry = _views.get(id);
    } else if ((payload && payload.new_tab) || _views.size === 0) {
      const made = createView();
      if (!made.ok) return made;
      id = made.id; entry = made.entry;
    } else {
      id = _activeId; entry = _views.get(id);
    }
    if (!entry) return { ok: false, reason: 'no_tab' };
    try {
      _activeId = id;
      applyAllBounds();
      await entry.view.webContents.loadURL(verdict.url);
      entry.url = verdict.url;
      entry.lastError = '';
      broadcast();
      // Report the session on every open, not only from `tabs`. The Python side
      // learns which profile is live from whichever reply arrives first, and
      // `open` is the one that always happens.
      return { ok: true, data: {
        id, url: verdict.url, title: entry.title,
        partition: partitionId(), sharedProfile: usingSharedPartition(),
      } };
    } catch (exc) {
      entry.lastError = String((exc && exc.message) || exc).slice(0, 160);
      broadcast();
      return { ok: false, reason: entry.lastError };
    }
  });

  ipcMain.handle('sc-browser:new-tab', async () => {
    const made = createView();
    if (!made.ok) return made;
    broadcast();
    return { ok: true, data: { id: made.id } };
  });

  ipcMain.handle('sc-browser:focus', (_e, payload) => {
    const id = String((payload && payload.id) || '');
    if (!_views.has(id)) return { ok: false, reason: 'no_tab' };
    _activeId = id;
    applyAllBounds();
    broadcast();
    return { ok: true, data: { id } };
  });

  ipcMain.handle('sc-browser:nav', async (_e, payload) => {
    const entry = resolve(payload && payload.id);
    if (!entry) return { ok: false, reason: 'no_tab' };
    const action = String((payload && payload.action) || '');
    const wc = entry.view.webContents;
    try {
      if (action === 'back') {
        if (!canGoBack(wc)) return { ok: false, reason: 'no_history' };
        navHistory(wc).goBack();
      } else if (action === 'forward') {
        if (!canGoForward(wc)) return { ok: false, reason: 'no_history' };
        navHistory(wc).goForward();
      } else if (action === 'reload') {
        wc.reload();
      } else if (action === 'stop') {
        wc.stop();
      } else if (action === 'goto') {
        const verdict = urlAllowed((payload && payload.url) || '');
        if (!verdict.ok) return { ok: false, reason: verdict.reason };
        await wc.loadURL(verdict.url);
      } else {
        return { ok: false, reason: 'unknown_action' };
      }
      broadcast();
      return { ok: true };
    } catch (exc) {
      return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
    }
  });

  ipcMain.handle('sc-browser:tabs', () => ({ ok: true, data: tabState() }));

  /* Register (or clear) the one local preview origin this browser may reach.
   * Driven by Python's preview server lifecycle so the two policy layers agree. */
  ipcMain.handle('sc-browser:preview-origin', (_e, payload) => {
    _previewOrigin = String((payload && payload.origin) || '').trim().toLowerCase();
    _log(`preview_origin=${_previewOrigin || '(cleared)'}`);
    return { ok: true, data: { origin: _previewOrigin } };
  });

  ipcMain.handle('sc-browser:read', async (_e, payload) => {
    const entry = resolve(payload && payload.id);
    if (!entry) return { ok: false, reason: 'no_tab' };
    try {
      const data = await entry.view.webContents.executeJavaScript(DIGEST_JS, true);
      // A long schedule, table, or report often has its relevant later rows
      // below the first observation.  Returning only the leading snippet made
      // `scroll` ineffective because the digest always cloned the whole page
      // then re-truncated it from character zero.  Slice here by an explicit,
      // bounded offset so the agent can read the next portion without bloating
      // every normal observation or losing page-level metadata.
      const rawOffset = Number(payload && payload.offset);
      const offset = Number.isFinite(rawOffset)
        ? Math.max(0, Math.floor(rawOffset)) : 0;
      const windowChars = 2600;
      const fullText = String(data.text || '');
      data.meta = data.meta || {};
      data.meta.textTotalChars = fullText.length;
      data.meta.textOffset = Math.min(offset, fullText.length);
      data.meta.textWindowChars = windowChars;
      data.meta.truncated = (offset + windowChars) < fullText.length;
      data.text = fullText.slice(offset, offset + windowChars);
      if (String((payload && payload.mode) || 'digest') === 'text') {
        return { ok: true, data: { title: data.title, url: data.url, text: data.text } };
      }
      return { ok: true, data };
    } catch (exc) {
      return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
    }
  });

  ipcMain.handle('sc-browser:input', async (_e, payload) => {
    const entry = resolve(payload && payload.id);
    if (!entry) return { ok: false, reason: 'no_tab' };
    const op = String((payload && payload.op) || '');
    const wc = entry.view.webContents;
    try {
      if (op === 'click') {
        const loc = await locate(wc, payload.element_id);
        if (!loc) return { ok: false, reason: 'element_not_found' };
        wc.sendInputEvent({ type: 'mouseMove', x: loc.cx, y: loc.cy });
        wc.sendInputEvent({ type: 'mouseDown', x: loc.cx, y: loc.cy, button: 'left', clickCount: 1 });
        wc.sendInputEvent({ type: 'mouseUp', x: loc.cx, y: loc.cy, button: 'left', clickCount: 1 });
        return { ok: true, data: { x: loc.cx, y: loc.cy } };
      }
      if (op === 'type') {
        const loc = await locate(wc, payload.element_id);
        if (!loc) return { ok: false, reason: 'element_not_found' };
        // Refused on the Python side too; this is defence in depth.
        if (loc.isPassword) return { ok: false, reason: 'refused_password_field' };
        wc.sendInputEvent({ type: 'mouseDown', x: loc.cx, y: loc.cy, button: 'left', clickCount: 1 });
        wc.sendInputEvent({ type: 'mouseUp', x: loc.cx, y: loc.cy, button: 'left', clickCount: 1 });
        const text = String((payload && payload.text) || '');
        for (const ch of text) wc.sendInputEvent({ type: 'char', keyCode: ch });
        if (payload && payload.submit) {
          wc.sendInputEvent({ type: 'keyDown', keyCode: 'Return' });
          wc.sendInputEvent({ type: 'char', keyCode: '\r' });
          wc.sendInputEvent({ type: 'keyUp', keyCode: 'Return' });
        }
        return { ok: true, data: { typed: text.length } };
      }
      if (op === 'scroll') {
        const dy = Number((payload && payload.dy) || 600);
        wc.sendInputEvent({ type: 'mouseWheel', x: 10, y: 10, deltaX: 0, deltaY: -dy, canScroll: true });
        return { ok: true };
      }
      if (op === 'back') {
        if (!canGoBack(wc)) return { ok: false, reason: 'no_history' };
        navHistory(wc).goBack();
        return { ok: true };
      }
      if (op === 'forward') {
        const nh = navHistory(wc);
        if (!nh.canGoForward()) return { ok: false, reason: 'no_history' };
        nh.goForward();
        return { ok: true };
      }
      if (op === 'reload') {
        wc.reload();
        return { ok: true };
      }

      /* A real web app is not driven by click/type/scroll alone. A chart
       * indicator is a keyboard shortcut, a timeframe is a <select>, a menu
       * needs a hover to open, and a fib retracement is a DRAG across a canvas
       * with no element to aim at. Without these, "open TradingView and add a
       * fib line" was not a hard task — it was an unexpressible one. */
      if (op === 'press_key') {
        const combo = String((payload && payload.keys) || '').trim();
        if (!combo) return { ok: false, reason: 'no_keys' };
        const parts = combo.split('+').map((p) => p.trim()).filter(Boolean);
        const key = parts.pop() || '';
        const modifiers = parts
          .map((m) => ({ ctrl: 'control', control: 'control', alt: 'alt',
                         shift: 'shift', meta: 'meta', cmd: 'meta',
                         win: 'meta' }[m.toLowerCase()]))
          .filter(Boolean);
        // Single printable characters need a `char` event or the page sees the
        // keydown but no input; named keys (Enter, Escape, F5…) do not.
        const printable = key.length === 1 && !modifiers.length;
        wc.sendInputEvent({ type: 'keyDown', keyCode: key, modifiers });
        if (printable) wc.sendInputEvent({ type: 'char', keyCode: key });
        wc.sendInputEvent({ type: 'keyUp', keyCode: key, modifiers });
        return { ok: true, data: { keys: combo } };
      }
      if (op === 'hover') {
        const loc = await locate(wc, payload.element_id);
        if (!loc) return { ok: false, reason: 'element_not_found' };
        // Two moves: some menus only open on a genuine movement, not a jump.
        wc.sendInputEvent({ type: 'mouseMove', x: loc.cx - 2, y: loc.cy - 2 });
        wc.sendInputEvent({ type: 'mouseMove', x: loc.cx, y: loc.cy });
        return { ok: true, data: { x: loc.cx, y: loc.cy } };
      }
      if (op === 'select_option') {
        // Deliberately NOT synthetic clicks: Chromium paints a <select>'s
        // dropdown as native chrome outside the page's coordinate space, so
        // there is nothing at those pixels to click. Setting the value and
        // firing the events the page listens for is the only thing that works.
        const eid = String((payload && payload.element_id) || '');
        const value = String((payload && payload.value) || '');
        const js = `(() => {
          const el = document.querySelector('[data-sc-id=' + JSON.stringify(${JSON.stringify(eid)}) + ']');
          if (!el) return { ok: false, reason: 'element_not_found' };
          const want = ${JSON.stringify(value)}.trim().toLowerCase();
          const opts = Array.from(el.options || []);
          if (!opts.length) return { ok: false, reason: 'not_a_dropdown' };
          let hit = opts.find((o) => (o.value || '').trim().toLowerCase() === want)
                 || opts.find((o) => (o.textContent || '').trim().toLowerCase() === want)
                 || opts.find((o) => (o.textContent || '').trim().toLowerCase().includes(want));
          if (!hit) return { ok: false, reason: 'no_such_option',
                             options: opts.slice(0, 24).map((o) => (o.textContent || '').trim()) };
          el.value = hit.value;
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
          return { ok: true, chose: (hit.textContent || hit.value || '').trim() };
        })()`;
        const res = await wc.executeJavaScript(js, true);
        if (!res || !res.ok) {
          return { ok: false, reason: (res && res.reason) || 'select_failed',
                   data: { options: (res && res.options) || [] } };
        }
        return { ok: true, data: { chose: res.chose } };
      }
      if (op === 'drag') {
        const at = async (elementId, xy) => {
          if (elementId) {
            const loc = await locate(wc, String(elementId));
            return loc ? { x: loc.cx, y: loc.cy } : null;
          }
          if (xy && Number.isFinite(Number(xy.x)) && Number.isFinite(Number(xy.y))) {
            return { x: Math.round(Number(xy.x)), y: Math.round(Number(xy.y)) };
          }
          return null;
        };
        const from = await at(payload && payload.from_element, payload && payload.from_xy);
        const to = await at(payload && payload.to_element, payload && payload.to_xy);
        if (!from || !to) return { ok: false, reason: 'element_not_found' };
        wc.sendInputEvent({ type: 'mouseMove', x: from.x, y: from.y });
        wc.sendInputEvent({ type: 'mouseDown', x: from.x, y: from.y, button: 'left', clickCount: 1 });
        // Interpolated, not teleported: canvas apps track mousemove to draw the
        // line, and a single jump leaves them with nothing between the ends.
        const steps = 12;
        for (let i = 1; i <= steps; i += 1) {
          wc.sendInputEvent({
            type: 'mouseMove',
            x: Math.round(from.x + ((to.x - from.x) * i) / steps),
            y: Math.round(from.y + ((to.y - from.y) * i) / steps),
          });
        }
        wc.sendInputEvent({ type: 'mouseUp', x: to.x, y: to.y, button: 'left', clickCount: 1 });
        return { ok: true, data: { from, to } };
      }
      if (op === 'wait_for') {
        const wantText = String((payload && payload.text) || '');
        const wantEl = String((payload && payload.element_id) || '');
        const budgetMs = Math.max(1000, Math.min(60000, Number((payload && payload.wait_s) || 15) * 1000));
        const started = Date.now();
        while (Date.now() - started < budgetMs) {
          /* eslint-disable no-await-in-loop */
          const js = `(() => {
            const t = ${JSON.stringify(wantText)};
            const e = ${JSON.stringify(wantEl)};
            if (e) return !!document.querySelector('[data-sc-id=' + JSON.stringify(e) + ']');
            if (t) return (document.body ? document.body.innerText : '').toLowerCase()
                          .includes(t.toLowerCase());
            return document.readyState === 'complete';
          })()`;
          const hit = await wc.executeJavaScript(js, true).catch(() => false);
          if (hit) return { ok: true, data: { waited_ms: Date.now() - started } };
          await new Promise((r) => setTimeout(r, 250));
          /* eslint-enable no-await-in-loop */
        }
        return { ok: false, reason: 'timed_out', data: { waited_ms: Date.now() - started } };
      }
      if (op === 'attach_file') {
        /* A file input cannot be driven with synthetic events — clicking one
         * opens a NATIVE OS dialog that lives outside the page and outside
         * anything this module is allowed to touch. The debugger protocol sets
         * the input's files directly, which is the only in-page route.
         *
         * The path arrived here having already passed the local-files
         * allowlist AND a permission card naming this exact file. */
        const eid = String((payload && payload.element_id) || '');
        const filePath = String((payload && payload.path) || '');
        if (!filePath) return { ok: false, reason: 'no_path' };
        let attached = false;
        try {
          if (!wc.debugger.isAttached()) wc.debugger.attach('1.3');
          const doc = await wc.debugger.sendCommand('DOM.getDocument', { depth: -1 });
          const node = await wc.debugger.sendCommand('DOM.querySelector', {
            nodeId: doc.root.nodeId,
            selector: '[data-sc-id=' + JSON.stringify(eid) + ']',
          });
          if (!node || !node.nodeId) return { ok: false, reason: 'element_not_found' };
          await wc.debugger.sendCommand('DOM.setFileInputFiles', {
            nodeId: node.nodeId, files: [filePath],
          });
          attached = true;
        } finally {
          try { if (wc.debugger.isAttached()) wc.debugger.detach(); } catch (_) {}
        }
        return attached
          ? { ok: true, data: { path: filePath } }
          : { ok: false, reason: 'attach_failed' };
      }
      return { ok: false, reason: 'unknown_op' };
    } catch (exc) {
      return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
    }
  });

  /* Annotate: the user points at something and we describe it back. Pure
   * read: picking an element never activates it (the click is swallowed). */
  ipcMain.handle('sc-browser:annotate', async (_e, payload) => {
    const entry = resolve(payload && payload.id);
    if (!entry) return { ok: false, reason: 'no_tab' };
    if (_annotating) return { ok: false, reason: 'already_annotating' };
    _annotating = true;
    broadcast();
    try {
      const picked = await entry.view.webContents.executeJavaScript(PICK_JS, true);
      return picked && picked.ok
        ? { ok: true, data: picked }
        : { ok: false, reason: (picked && picked.reason) || 'cancelled' };
    } catch (exc) {
      return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
    } finally {
      _annotating = false;
      broadcast();
    }
  });

  ipcMain.handle('sc-browser:capture', async (_e, payload) => {
    const entry = resolve(payload && payload.id);
    if (!entry) return { ok: false, reason: 'no_tab' };
    try {
      const img = await entry.view.webContents.capturePage();
      return { ok: true, data: { png_base64: img.toPNG().toString('base64') } };
    } catch (exc) {
      return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
    }
  });

  ipcMain.handle('sc-browser:close', (_e, payload) => {
    const id = String((payload && payload.id) || _activeId || '');
    const ok = destroyView(id);
    broadcast();
    return { ok, data: { id } };
  });

  ipcMain.handle('sc-browser:set-bounds', (_e, payload) => {
    const b = payload && payload.bounds;
    _hidden = !!(payload && payload.hidden);
    if (b) {
      _bounds = {
        x: Number(b.x || 0), y: Number(b.y || 0),
        width: Number(b.width || 0), height: Number(b.height || 0),
      };
    }
    applyAllBounds();
    return { ok: true };
  });

  /* Render the active tab to PDF. This is how Supercomputer produces a real PDF
   * (Chromium's own print engine) without a Python PDF dependency: the agent
   * writes HTML into the task workspace, opens it through the local preview
   * origin, and prints that. Returns base64 so the Python side owns where it
   * lands — nothing is written to disk from here. */
  ipcMain.handle('sc-browser:print-pdf', async (_e, payload) => {
    const entry = resolve(payload && payload.id);
    if (!entry) return { ok: false, reason: 'no_tab' };
    try {
      const buf = await entry.view.webContents.printToPDF({
        printBackground: true,
        pageSize: String((payload && payload.page_size) || 'A4'),
        landscape: !!(payload && payload.landscape),
        margins: { marginType: 'default' },
      });
      return { ok: true, data: { pdf_base64: Buffer.from(buf).toString('base64') } };
    } catch (exc) {
      return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
    }
  });

  /* Arm exactly one download into a named folder. Called by Python only after a
   * permission card was answered Yes — this handler grants nothing on its own,
   * it records that the next download may land. */
  ipcMain.handle('sc-browser:arm-download', (_e, payload) => {
    const folder = String((payload && payload.folder) || '').trim();
    if (!folder) return { ok: false, reason: 'no_folder' };
    const ttl = Math.max(5, Math.min(180, Number((payload && payload.ttl_s) || 60)));
    _downloadGrant = { folder, expires: Date.now() + ttl * 1000 };
    _lastDownload = null;
    _log(`download_armed folder=${folder.slice(0, 60)} ttl=${ttl}s`);
    return { ok: true, data: { folder, ttl_s: ttl } };
  });

  /* What happened to the most recent download. Polled by the Python side so it
   * can report the real outcome rather than assuming it worked. */
  ipcMain.handle('sc-browser:download-status', () => ({ ok: true, data: _lastDownload || {} }));

  ipcMain.handle('sc-browser:dispose', () => dispose());
}

/* ── Lifecycle ──────────────────────────────────────────────────────────── */
function install(deps) {
  if (_installed) return { ok: true, reason: 'already_installed' };
  _electron = { ipcMain: deps.ipcMain, session: deps.session, BrowserView: deps.BrowserView };
  _getMainWindow = deps.getMainWindow;
  _sharedPartition = String((deps && deps.sharedPartition) || '');
  if (typeof deps.log === 'function') _log = deps.log;
  registerHandlers(deps.ipcMain);
  _installed = true;
  _log(`installed partition=${_sharedPartition || 'ephemeral'}`);
  return { ok: true };
}

function dispose() {
  const ids = Array.from(_views.keys());
  for (const id of ids) destroyView(id);
  _views.clear();
  _order = [];
  _activeId = '';
  _bounds = null;
  _hidden = false;
  _annotating = false;
  _previewOrigin = '';
  // Drop the partition's data ONLY when it is ours. Clearing a shared profile
  // would sign the user out of the HUD browser as a side effect of leaving this
  // mode — the tabs are gone either way, which is the part that matters.
  const wasShared = usingSharedPartition();
  if (_partition && !wasShared && _electron && _electron.session) {
    try {
      const ses = _electron.session.fromPartition(_partition);
      ses.clearStorageData().catch(() => {});
      ses.clearCache().catch(() => {});
    } catch (_) {}
  }
  _partition = '';
  _sessionHardened = false;
  _log(`disposed tabs=${ids.length} shared=${wasShared}`);
  broadcast();
  return { ok: true, closed: ids.length };
}

function snapshot() {
  return {
    installed: _installed,
    partition: _partition,
    active: _activeId,
    tabs: _views.size,
  };
}

module.exports = { install, dispose, snapshot, urlAllowed, _internals: { partitionId, tabState } };
