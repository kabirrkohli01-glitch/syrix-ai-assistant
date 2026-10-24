/* ── SYRIX Stage — shared store for browser stage mode ───────────────────────
 *
 * Stage mode: the embedded HUD browser (native BrowserView, main.js) fills the
 * whole window as a backdrop while the Dynamic Island morphs into a nav bar.
 * Because BrowserViews are native layers that always paint ABOVE HTML, any
 * panel that wants to appear on top of the staged browser must first acquire
 * an overlay tag here — main.js then swaps the live view for a capturePage
 * snapshot (rendered by the dock) until the last tag is released.
 *
 * window.SyrixStage is a plain-JS global (no React, no build step) shared by:
 *   syrix-bridge.js         — feeds setStageState() from hud-app:state
 *   syrix-hud-app-dock.jsx  — renders the freeze backdrop, stage frame
 *   syrix-primary-app.jsx   — island nav bar + folder panel
 *   syrix-aurora-shell.jsx  — rail/chat/body-class integration + registry
 */
(function () {
  "use strict";
  if (window.SyrixStage) return;

  var PREF_KEY = "syrix_stage_mode_v1";

  var _state = {
    stage: { enabled: false, chatInset: 0, navH: 68, frozen: false },
    appCount: 0,
    active: false, // enabled && appCount > 0 — what the UI keys off
    freeze: { frozen: false, snapshot: null, bounds: null },
  };
  var _subs = new Set();
  var _tags = new Set();
  var _sessionPref = null; // per-session override from the Windowed/Stage buttons
  var _insetTimer = null;

  function _native() {
    try { return (window.syrixHud && window.syrixHud.hudApp) || null; } catch (_) { return null; }
  }

  function _emit() {
    _subs.forEach(function (fn) { try { fn(_state); } catch (_) {} });
  }

  function get() { return _state; }

  function subscribe(fn) {
    if (typeof fn !== "function") return function () {};
    _subs.add(fn);
    try { fn(_state); } catch (_) {}
    return function () { _subs.delete(fn); };
  }

  /* Fed by syrix-bridge.js on every hud-app:state broadcast. */
  function setStageState(stage, appCount) {
    var s = stage && typeof stage === "object" ? stage : {};
    var prevActive = _state.active;
    _state = Object.assign({}, _state, {
      stage: {
        enabled: !!s.enabled,
        chatInset: Number(s.chatInset) || 0,
        navH: Number(s.navH) || 68,
        frozen: !!s.frozen,
      },
      appCount: Number.isFinite(Number(appCount)) ? Number(appCount) : _state.appCount,
    });
    _state.active = _state.stage.enabled && _state.appCount > 0;
    if (prevActive && !_state.active) {
      // Stage ended (last tab closed or Windowed pressed): main.js has already
      // reset its overlay refcount, so drop our tags and freeze snapshot too.
      _tags.clear();
      _state.freeze = { frozen: false, snapshot: null, bounds: null };
    }
    _emit();
  }

  /* Fed by the hud-app:freeze push (attached below). */
  function onFreezePayload(p) {
    var frozen = !!(p && p.frozen);
    _state = Object.assign({}, _state, {
      freeze: frozen
        ? { frozen: true, snapshot: (p && p.snapshot) || null, bounds: (p && p.bounds) || null }
        : { frozen: false, snapshot: null, bounds: null },
    });
    _emit();
  }

  /* Preference: default ON. The Windowed button only overrides the current
   * session — a fresh HUD launch goes back to stage-on-open. */
  function prefOn() {
    if (_sessionPref !== null) return _sessionPref;
    try { return localStorage.getItem(PREF_KEY) !== "0"; } catch (_) { return true; }
  }
  function setPref(on) {
    _sessionPref = null;
    try { localStorage.setItem(PREF_KEY, on ? "1" : "0"); } catch (_) {}
  }

  function enable(chatInset) {
    _sessionPref = true;
    var n = _native();
    if (n && n.stage) return n.stage({ enabled: true, chatInset: Number(chatInset) || 0 });
    return Promise.resolve({ ok: false, reason: "native_unavailable" });
  }

  function disable() {
    _sessionPref = false;
    _tags.clear(); // main.js clears its refcount when stage turns off
    var n = _native();
    if (n && n.stage) return n.stage({ enabled: false, chatInset: 0 });
    return Promise.resolve({ ok: false, reason: "native_unavailable" });
  }

  /* Live view inset for the left-docked stage chat (tweened in main.js).
   * Small debounce so a rapid open/close doesn't restart the tween mid-flight. */
  function setChatInset(px) {
    if (_insetTimer) clearTimeout(_insetTimer);
    _insetTimer = setTimeout(function () {
      _insetTimer = null;
      var n = _native();
      if (n && n.stage) { try { n.stage({ chatInset: Number(px) || 0 }); } catch (_) {} }
    }, 30);
  }

  /* Overlay freeze — tag-deduped refcount. Main.js only ever sees the 0↔1
   * crossings, so its own count stays in lockstep with ours. */
  function acquireOverlay(tag) {
    var t = String(tag || "anon");
    if (_tags.has(t)) return;
    var wasEmpty = _tags.size === 0;
    _tags.add(t);
    if (wasEmpty) {
      var n = _native();
      if (n && n.overlay) { try { n.overlay({ acquire: true }); } catch (_) {} }
    }
  }
  function releaseOverlay(tag) {
    var t = String(tag || "anon");
    if (!_tags.delete(t)) return;
    if (_tags.size === 0) {
      var n = _native();
      if (n && n.overlay) { try { n.overlay({ acquire: false }); } catch (_) {} }
    }
  }

  /* Rail-action registry — the aurora shell registers its rail handlers here
   * so the island's folder panel invokes the EXACT same functions the rail
   * buttons use (chat, widgets, browser, …). */
  var _registry = new Map();
  var registry = {
    register: function (name, fn, meta) {
      if (!name || typeof fn !== "function") return function () {};
      _registry.set(String(name), { fn: fn, meta: meta || {} });
      _emit();
      return function () {
        var cur = _registry.get(String(name));
        if (cur && cur.fn === fn) { _registry.delete(String(name)); _emit(); }
      };
    },
    invoke: function (name) {
      var entry = _registry.get(String(name));
      if (!entry) return false;
      try { entry.fn(); } catch (_) {}
      return true;
    },
    list: function () {
      var out = [];
      _registry.forEach(function (e, name) { out.push(Object.assign({ name: name }, e.meta)); });
      out.sort(function (a, b) { return (Number(a.order) || 99) - (Number(b.order) || 99); });
      return out;
    },
  };

  /* Attach the freeze push listener; the preload may not be ready yet at
   * script-eval time, so retry with backoff (same pattern as syrix-bridge). */
  var _freezeAttached = false;
  var _freezeTries = 0;
  function _attachFreeze() {
    if (_freezeAttached) return;
    var n = _native();
    if (n && n.onFreeze) {
      _freezeAttached = true;
      try { n.onFreeze(onFreezePayload); } catch (_) { _freezeAttached = false; }
      return;
    }
    _freezeTries += 1;
    if (_freezeTries <= 8) setTimeout(_attachFreeze, Math.min(100 * Math.pow(2, _freezeTries), 3200));
  }
  _attachFreeze();

  window.SyrixStage = {
    get: get,
    subscribe: subscribe,
    setStageState: setStageState,
    onFreezePayload: onFreezePayload,
    prefOn: prefOn,
    setPref: setPref,
    enable: enable,
    disable: disable,
    setChatInset: setChatInset,
    acquireOverlay: acquireOverlay,
    releaseOverlay: releaseOverlay,
    registry: registry,
  };
})();
