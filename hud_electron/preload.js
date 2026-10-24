const { contextBridge, ipcRenderer } = require("electron");

let _scEnsurePromise = null;
function _ensureScBrowser() {
  if (!_scEnsurePromise) {
    _scEnsurePromise = ipcRenderer.invoke("sc-browser:ensure").finally(() => {
      _scEnsurePromise = null;
    });
  }
  return _scEnsurePromise;
}

async function _scInvoke(channel, payload) {
  const ready = await _ensureScBrowser();
  if (!ready || ready.ok === false) {
    return ready || { ok: false, reason: "browser_network_guard_unavailable" };
  }
  return ipcRenderer.invoke(channel, payload);
}

const _groqModel      = (() => { try { return String(process.env.GROQ_MODEL               || "llama-3.3-70b-versatile").trim(); } catch (_) { return "llama-3.3-70b-versatile"; } })();
const _spotifyClient  = (() => { try { return String(process.env.SPOTIFY_CLIENT_ID        || "").trim(); } catch (_) { return ""; } })();

contextBridge.exposeInMainWorld("syrixHud", {
  togglePin:         () => ipcRenderer.invoke("hud:toggle-pin"),
  minimize:          () => ipcRenderer.invoke("hud:minimize"),
  close:             () => ipcRenderer.invoke("hud:close"),
  toggleFullscreen:  () => ipcRenderer.invoke("hud:toggle-fullscreen"),
  toggleMaximize:    () => ipcRenderer.invoke("hud:toggle-maximize"),
  focusMainWindow:   () => ipcRenderer.invoke("hud:focus-main"),
  // One-purpose, per-boot loopback capability. Main validates this preload's
  // webContents before returning it; it is never persisted or logged.
  getHudWsAuthToken: () => ipcRenderer.invoke("hud:ws-auth-token"),
  getGroqModel:      () => Promise.resolve(_groqModel),
  // Back-compat provider status only. API tokens/client secrets intentionally
  // never cross into the renderer; use spotify.status()/spotify.call() below.
  getSpotifyConfig:  () => ipcRenderer.invoke("hud:spotify-status"),
  runtimeLogs: {
    recent: (limit) => ipcRenderer.invoke("hud:runtime-logs", {
      limit: Math.max(50, Math.min(500, Number(limit) || 400)),
    }),
  },
  // Phase 0: runtime mailboxes live in %LOCALAPPDATA%\SYRIX\state — the bridge
  // polls them through this IPC instead of relative file:// fetches.
  readRuntimeJson: (name) => ipcRenderer.invoke("hud:runtime-json", { name: String(name || "") }),
  // 2026-07-02 — file-based command fallback for when the WS is down: appends
  // to the hud_control.json queue via the main process (renderer has no fs).
  sendControl: (payload) => ipcRenderer.invoke("hud:send-control", payload || {}),
  spotify: {
    status: () => ipcRenderer.invoke("hud:spotify-status"),
    call: (action, data) => ipcRenderer.invoke("hud:spotify-api", {
      action: String(action || ""),
      data: data || {},
    }),
    clientConfigured: () => Promise.resolve(!!_spotifyClient),
    close: () => ipcRenderer.invoke("hud:spotify-close"),
  },
  onHotkey: (callback) => {
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on("hud:hotkey", listener);
    return () => ipcRenderer.removeListener("hud:hotkey", listener);
  },
  onScreenCaptureError: (callback) => {
    const listener = (_event, payload) => { try { callback(payload || {}); } catch (_) {} };
    ipcRenderer.on("hud:screen-capture-error", listener);
    return () => ipcRenderer.removeListener("hud:screen-capture-error", listener);
  },
  // Core Stability Pass: main process fires this when the window is about
  // to close. The renderer/bridge uses it to send `app_close` to the
  // Python backend over WebSocket so mic/camera/STT/voice all stop.
  onAppClosing: (callback) => {
    const listener = (_event, payload) => { try { callback(payload || {}); } catch (_) {} };
    ipcRenderer.on("hud:app-closing", listener);
    return () => ipcRenderer.removeListener("hud:app-closing", listener);
  },
  // SYRIX HUD App Dock — embedded BrowserView controls. Every helper is
  // a Promise that resolves to { ok, ... } so the renderer can ack.
  hudApp: {
    open:   (opts)  => ipcRenderer.invoke("hud-app:open",   opts || {}),
    close:  (id)    => ipcRenderer.invoke("hud-app:close",  { id }),
    focus:  (id)    => ipcRenderer.invoke("hud-app:focus",  { id }),
    mode:   (id, mode, bounds) => ipcRenderer.invoke("hud-app:mode", { id, mode, bounds }),
    nav:    (id, action, url) => ipcRenderer.invoke("hud-app:nav", { id, action, url: url || null }),
    popout: (id)    => ipcRenderer.invoke("hud-app:popout", { id }),
    vision: (id, enabled) => ipcRenderer.invoke("hud-app:vision", { id, enabled: !!enabled }),
    dragStart: (id) => ipcRenderer.invoke("hud-app:drag-start", { id }),
    dragEnd: (id, bounds) => ipcRenderer.invoke("hud-app:drag-end", { id, bounds }),
    ack:    (ack)   => ipcRenderer.invoke("hud-app:ack", ack || {}),
    chromeAck: (id) => ipcRenderer.invoke("hud-app:chrome-ack", { id }),
    emergencyCloseAll: () => ipcRenderer.invoke("hud-app:emergency-close-all"),
    list:   ()      => ipcRenderer.invoke("hud-app:list"),
    sessionInfo:  () => ipcRenderer.invoke("hud-app:session-info"),
    clearSession: () => ipcRenderer.invoke("hud-app:clear-session"),
    onState: (callback) => {
      const listener = (_event, payload) => { try { callback(payload || {}); } catch (_) {} };
      ipcRenderer.on("hud-app:state", listener);
      return () => ipcRenderer.removeListener("hud-app:state", listener);
    },
    // Stage mode — browser as full-window backdrop + overlay freeze.
    stage:   (opts) => ipcRenderer.invoke("hud-app:stage", opts || {}),
    overlay: (opts) => ipcRenderer.invoke("hud-app:overlay", opts || {}),
    onFreeze: (callback) => {
      const listener = (_event, payload) => { try { callback(payload || {}); } catch (_) {} };
      ipcRenderer.on("hud-app:freeze", listener);
      return () => ipcRenderer.removeListener("hud-app:freeze", listener);
    },
    // Phase 5 — HUD-sandbox automation primitives (embedded BrowserViews only).
    domDigest: (id, mode) => ipcRenderer.invoke("hud-app:dom-digest", { id, mode: mode || "digest" }),
    input: (id, op, opts) => ipcRenderer.invoke("hud-app:input", Object.assign({ id, op }, opts || {})),
    capture: (id) => ipcRenderer.invoke("hud-app:capture", { id }),
  },
  // Supercomputer browser — a SEPARATE namespace from hudApp on purpose. It
  // targets only the views the Supercomputer module itself created, in its own
  // ephemeral partition; nothing here can reach an app-dock view.
  superComputer: {
    ensure:    ()            => _ensureScBrowser(),
    open:      (opts)        => _scInvoke("sc-browser:open", opts || {}),
    tabs:      ()            => _scInvoke("sc-browser:tabs"),
    newTab:    ()            => _scInvoke("sc-browser:new-tab"),
    focus:     (id)          => _scInvoke("sc-browser:focus", { id }),
    nav:       (id, action, url) => _scInvoke("sc-browser:nav", { id, action, url: url || "" }),
    read:      (id, mode)    => _scInvoke("sc-browser:read", { id, mode: mode || "digest" }),
    input:     (id, op, opts) => _scInvoke("sc-browser:input", Object.assign({ id, op }, opts || {})),
    annotate:  (id)          => _scInvoke("sc-browser:annotate", { id }),
    capture:   (id)          => _scInvoke("sc-browser:capture", { id }),
    close:     (id)          => _scInvoke("sc-browser:close", { id }),
    printPdf:  (id, opts)    => _scInvoke("sc-browser:print-pdf", Object.assign({ id }, opts || {})),
    previewOrigin: (origin)  => _scInvoke("sc-browser:preview-origin", { origin: origin || "" }),
    chooseFolder: ()         => ipcRenderer.invoke("sc-browser:choose-folder"),
    openFile:  (p)           => ipcRenderer.invoke("sc-browser:open-file", { path: p }),
    armDownload: (folder, ttl_s) => _scInvoke("sc-browser:arm-download", { folder, ttl_s }),
    downloadStatus: ()       => _scInvoke("sc-browser:download-status"),
    setBounds: (bounds, hidden) => _scInvoke("sc-browser:set-bounds", { bounds, hidden: !!hidden }),
    dispose:   ()            => ipcRenderer.invoke("sc-browser:dispose"),
    onState: (callback) => {
      const listener = (_event, payload) => { try { callback(payload || {}); } catch (_) {} };
      ipcRenderer.on("sc-browser:state", listener);
      return () => ipcRenderer.removeListener("sc-browser:state", listener);
    },
  },
  islandOverlay: (open) => ipcRenderer.invoke("hud:island-overlay", { open: !!open }),
  sysinfo: () => ipcRenderer.invoke("hud:sysinfo"),
  // Deck: list the ecosystem apps and launch one by id (never by path).
  deck: {
    list:   ()   => ipcRenderer.invoke("hud:deck-list"),
    launch: (id) => ipcRenderer.invoke("hud:deck-launch", { id: String(id || "") }),
  },
  // Settings → SYRIX: SYRIX.md and the owner profile, by key (never by path).
  syrixFiles: {
    info: () => ipcRenderer.invoke("hud:syrix-files"),
    open: (key, reveal) => ipcRenderer.invoke("hud:syrix-file-open", { key: String(key || ""), reveal: !!reveal }),
  },
});
