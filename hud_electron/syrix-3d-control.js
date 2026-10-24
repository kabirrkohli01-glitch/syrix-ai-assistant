/*
 * SYRIX 3D CONTROL REGISTRY — Generic surface for controlling any 3D model
 * in the HUD via hand-control / contactless / voice / HUD buttons.
 *
 * Phase-3 Runtime-Repair (2026-05-16). Solves the limitation where every
 * gesture-to-3D pipeline was hardcoded for the arc-reactor / sandbox singleton.
 * Now any 3D widget (arc reactor, particle field, imported sphere, future
 * GLTF, etc.) can REGISTER itself and become controllable.
 *
 *   register(id, descriptor) -> token
 *   unregister(id)
 *   list() -> [{id, name, type, enabled_controls}]
 *   setActive(id)
 *   active() -> id | ""
 *   dispatch(action, value) -> { ok, applied_to, reason }
 *   onChange(fn) -> unsubscribe
 *
 * descriptor: {
 *   name: "Arc Reactor" | "Sandbox: glow_sphere" | "Imported model X",
 *   type: "arc_reactor" | "particles" | "sphere" | "imported" | ...,
 *   enabled_controls: ["rotate","zoom","pan","select","reset","toggle_particles"],
 *   apply: function(action, value) -> bool   // implements the action
 *   getTransform: function() -> { rotate:{x,y,z}, scale, position:{x,y,z} } (optional)
 *   resetTransform: function() -> bool (optional)
 *   onActivate: function() -> void (optional — called when made active)
 *   onDeactivate: function() -> void (optional)
 * }
 *
 * dispatch() rejects actions that aren't in the active object's
 * enabled_controls list and logs the reason — so a future "delete model"
 * gesture cannot fire without explicit allowlist enablement.
 *
 * The registry is mirror-aware: contactless-control feeds it gesture deltas
 * in the user's mental coordinate space (right/left of frame), and each
 * model decides how to apply them via the apply() callback. This means a
 * mirror-toggle change in the camera preview NEVER inverts gesture direction
 * unless the descriptor explicitly opts in.
 *
 * Public surface: window.SyrixModel3DRegistry
 */
(function () {
  'use strict';
  if (window.SyrixModel3DRegistry) return;

  const ALLOWED_DEFAULTS = Object.freeze([
    'rotate', 'zoom', 'pan', 'select', 'reset',
    'toggle_particles', 'switch', 'pause', 'resume',
  ]);

  // Internal state.
  const _models = new Map();    // id -> descriptor
  let _activeId = '';
  let _changeListeners = new Set();

  function _safeLog(level, msg, extra) {
    try {
      const fn = (console[level] || console.log);
      if (extra !== undefined) fn(`[3D_CONTROL] ${msg}`, extra);
      else fn(`[3D_CONTROL] ${msg}`);
    } catch (_) {}
  }

  function _emitChange() {
    const snap = {
      active: _activeId,
      count: _models.size,
      ids: Array.from(_models.keys()),
    };
    for (const fn of _changeListeners) {
      try { fn(snap); } catch (_) {}
    }
    try {
      window.dispatchEvent(new CustomEvent('syrix-3d-registry-change', { detail: snap }));
    } catch (_) {}
  }

  /**
   * Register a controllable 3D model.
   * If no model is currently active, the newly-registered one becomes active.
   */
  function register(id, descriptor) {
    const idStr = String(id || '').trim();
    if (!idStr) return { ok: false, reason: 'empty_id' };
    if (!descriptor || typeof descriptor !== 'object') {
      return { ok: false, reason: 'missing_descriptor' };
    }
    if (typeof descriptor.apply !== 'function') {
      return { ok: false, reason: 'missing_apply_fn' };
    }
    const enabled = Array.isArray(descriptor.enabled_controls) && descriptor.enabled_controls.length
      ? descriptor.enabled_controls.map(String)
      : ALLOWED_DEFAULTS.slice();
    const safe = {
      id: idStr,
      name: String(descriptor.name || idStr),
      type: String(descriptor.type || 'unknown'),
      enabled_controls: enabled,
      apply: descriptor.apply,
      getTransform: typeof descriptor.getTransform === 'function' ? descriptor.getTransform : null,
      resetTransform: typeof descriptor.resetTransform === 'function' ? descriptor.resetTransform : null,
      onActivate: typeof descriptor.onActivate === 'function' ? descriptor.onActivate : null,
      onDeactivate: typeof descriptor.onDeactivate === 'function' ? descriptor.onDeactivate : null,
      registered_at: Date.now(),
    };
    _models.set(idStr, safe);
    _safeLog('log', `registered object_id=${idStr} name=${safe.name} type=${safe.type}`);
    if (!_activeId) setActive(idStr);
    else _emitChange();
    return { ok: true, id: idStr };
  }

  function unregister(id) {
    const idStr = String(id || '').trim();
    if (!idStr || !_models.has(idStr)) return { ok: false, reason: 'unknown_id' };
    const ent = _models.get(idStr);
    try { ent && ent.onDeactivate && ent.onDeactivate(); } catch (_) {}
    _models.delete(idStr);
    if (_activeId === idStr) {
      // Promote the most-recently registered remaining one as active.
      let next = '';
      let bestTs = -Infinity;
      for (const [k, v] of _models.entries()) {
        if ((v.registered_at || 0) > bestTs) { bestTs = v.registered_at; next = k; }
      }
      _activeId = '';
      if (next) setActive(next);
      else _emitChange();
    }
    _safeLog('log', `unregistered object_id=${idStr}`);
    return { ok: true };
  }

  function list() {
    const out = [];
    for (const [k, v] of _models.entries()) {
      out.push({
        id: k,
        name: v.name,
        type: v.type,
        enabled_controls: v.enabled_controls.slice(),
        active: k === _activeId,
        transform: v.getTransform ? safeCall(v.getTransform) : null,
      });
    }
    return out;
  }

  function setActive(id) {
    const idStr = String(id || '').trim();
    if (!idStr) return { ok: false, reason: 'empty_id' };
    if (!_models.has(idStr)) return { ok: false, reason: 'unknown_id' };
    if (_activeId === idStr) return { ok: true, reason: 'already_active' };
    if (_activeId && _models.has(_activeId)) {
      const prev = _models.get(_activeId);
      try { prev && prev.onDeactivate && prev.onDeactivate(); } catch (_) {}
    }
    _activeId = idStr;
    const ent = _models.get(idStr);
    try { ent && ent.onActivate && ent.onActivate(); } catch (_) {}
    _safeLog('log', `active_object=${idStr} name=${ent.name}`);
    _emitChange();
    return { ok: true };
  }

  function active() { return _activeId; }

  function safeCall(fn) {
    try { return fn(); } catch (_) { return null; }
  }

  /**
   * Dispatch a generic 3D action to the active model.
   * Returns { ok, applied_to, reason } so the caller can update HUD state.
   */
  function dispatch(action, value) {
    const act = String(action || '').toLowerCase();
    if (!act) return { ok: false, reason: 'empty_action' };
    if (!_activeId) return { ok: false, reason: 'no_active_model' };
    const ent = _models.get(_activeId);
    if (!ent) return { ok: false, reason: 'active_missing' };
    if (ent.enabled_controls.indexOf(act) === -1) {
      _safeLog('warn', `blocked reason=unsupported_action action=${act} model=${_activeId}`);
      return { ok: false, applied_to: _activeId, reason: 'unsupported_action' };
    }
    // Built-in actions handled by the registry directly.
    if (act === 'reset') {
      if (ent.resetTransform) {
        const ok = !!safeCall(ent.resetTransform);
        _safeLog('log', `reset model=${_activeId} ok=${ok}`);
        return { ok, applied_to: _activeId, reason: ok ? 'reset_ok' : 'reset_failed' };
      }
      // Fall through to apply().
    }
    if (act === 'switch') {
      // value: { id } or { direction: "next"|"prev" }
      let nextId = '';
      const v = value || {};
      if (v.id) nextId = String(v.id);
      else {
        const ids = Array.from(_models.keys());
        const i = ids.indexOf(_activeId);
        if (i >= 0) {
          if (String(v.direction || 'next').toLowerCase() === 'prev') {
            nextId = ids[(i - 1 + ids.length) % ids.length];
          } else {
            nextId = ids[(i + 1) % ids.length];
          }
        }
      }
      if (nextId) {
        const r = setActive(nextId);
        return { ok: !!(r && r.ok), applied_to: nextId, reason: 'switched' };
      }
      return { ok: false, applied_to: _activeId, reason: 'switch_target_missing' };
    }
    try {
      const applied = !!ent.apply(act, value);
      // Build a structured log entry that matches the runtime audit spec.
      if (act === 'rotate' || act === 'zoom' || act === 'pan') {
        const ts = (value && value.value) ? value.value : (value || {});
        _safeLog('log',
          `gesture=${act} action=${act} model=${_activeId} ` +
          `rotate=${(ts.degrees != null ? ts.degrees : '')} ` +
          `zoom=${(ts.delta != null ? ts.delta : '')} ` +
          `pan=${(ts.dx != null ? `dx${ts.dx},dy${ts.dy || 0}` : '')}`);
      } else {
        _safeLog('log', `action=${act} model=${_activeId} ok=${applied}`);
      }
      return { ok: applied, applied_to: _activeId, reason: applied ? 'ok' : 'apply_returned_false' };
    } catch (err) {
      _safeLog('warn', `apply threw model=${_activeId} action=${act}`, err);
      return { ok: false, applied_to: _activeId, reason: 'apply_threw' };
    }
  }

  function onChange(fn) {
    if (typeof fn !== 'function') return () => {};
    _changeListeners.add(fn);
    return () => { try { _changeListeners.delete(fn); } catch (_) {} };
  }

  window.SyrixModel3DRegistry = {
    register, unregister, list, setActive, active, dispatch, onChange,
    ALLOWED_DEFAULTS,
  };
})();
