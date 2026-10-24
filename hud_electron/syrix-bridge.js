/*
 * SYRIX BRIDGE — Connects the new SYRIX redesign UI to the existing
 * SYRIX host backend over the same contracts the legacy HUD uses:
 *
 *   - WebSocket: ws://127.0.0.1:5111  (state, chat messages)
 *   - hud_state.json (../hud_state.json) — full backend state snapshot
 *   - hud_chat.json  (../hud_chat.json)  — appended chat lines
 *   - hud_brain_state.json (../hud_brain_state.json) — NVIDIA NIM brains
 *
 * The legacy HUD's app.js is NOT loaded when the redesigned HUD is the shell.
 * This bridge provides equivalent contracts so the React design can:
 *   - Send commands (typed_text, set_brain_priority, toggle_*, etc.)
 *   - Subscribe to live state (connection, brain, model, mic_level, etc.)
 *   - Send chat messages and await replies
 *
 * Performance:
 *   - One WebSocket, single source of truth
 *   - Polls JSON files at 1.4s intervals (matches perf_hud_state_min_interval_seconds=1.4)
 *   - Polls hud_brain_state.json at 2.0s
 *   - Throttles chat-poll fallback to 1.2s when WS not available
 *
 * Nothing here is required by the legacy HUD entry point (index_legacy_v4.html),
 * which still uses app.js + hud-enhancements.js directly.
 */

(function () {
  'use strict';

  if (window.__SyrixBridgeStarted) return;
  window.__SyrixBridgeStarted = true;

  /* 2026-05-30 — HUD Layer/Focus Manager.
     A single ascending z-counter shared across all React HUD surfaces so the
     newest opened (or clicked-to-focus) surface lands on top.  Surfaces tell
     the manager when they open, focus, or close; the manager returns a
     monotonically increasing z value to apply via inline style.
     Layers in the HUD baseline (do NOT change):
       p-chat                     = 2600
       app-dock frame             = 360 (BrowserView overlay)
       long-report overlay        = 1900-ish
     Manager assignments are added ON TOP of the baseline by adding to a
     `layerBase` per-class; e.g. a panel asks for layer with base 2900 and
     the manager returns 2900+n.  This keeps surfaces above the baseline
     but never under each other unintentionally. */
  const __SYRIX_LAYER = {
    _counter: 0,
    _surfaces: new Map(),
    _open(id, base) {
      this._counter += 1;
      const z = (base | 0) + this._counter;
      this._surfaces.set(String(id), { z, ts: Date.now(), base: base | 0 });
      try { console.info('[HUD_LAYER_OPEN] id=' + id + ' z=' + z + ' base=' + base); } catch (_) {}
      return z;
    },
    _focus(id, base) {
      this._counter += 1;
      const z = (base | 0) + this._counter;
      this._surfaces.set(String(id), { z, ts: Date.now(), base: base | 0 });
      try { console.info('[HUD_LAYER_FOCUS] id=' + id + ' z=' + z); } catch (_) {}
      return z;
    },
    _close(id) {
      this._surfaces.delete(String(id));
      try { console.info('[HUD_LAYER_CLOSE] id=' + id); } catch (_) {}
    },
    _z(id) {
      const s = this._surfaces.get(String(id));
      return s ? s.z : 0;
    },
  };
  window.SyrixLayerManager = {
    open: (id, base) => __SYRIX_LAYER._open(id, base),
    focus: (id, base) => __SYRIX_LAYER._focus(id, base),
    close: (id) => __SYRIX_LAYER._close(id),
    z: (id) => __SYRIX_LAYER._z(id),
  };
  // Tiny log helper to keep call sites concise.
  function _layerLog(stage, detail) {
    try { console.info('[HUD_LAYER_Z] stage=' + stage + ' ' + JSON.stringify(detail || {})); } catch (_) {}
  }

  const HUD_WS_URL  = 'ws://127.0.0.1:5111';
  const STATE_URL   = '../hud_state.json';
  const CHAT_URL    = '../hud_chat.json';
  const BRAIN_URL   = '../hud_brain_state.json';
  const FREEWEB_URL = '../hud_free_web_state.json';
  const BRAIN_RESULT_URL = '../hud_brain_control_result.json';
  // Tiny mailbox file written by syrix_host.py:_publish_hud_hand_control_action
  // when a hand-control voice phrase is handled by Python. Polled like the brain
  // result file; the bridge re-fires the action on the React overlay.
  const HAND_CTL_URL = '../hud_hand_control_event.json';
  const PRIVATE_URL_RE = /\b(?:https?|wss?|file):\/\/[^\s"'<>]+/ig;
  const PRIVATE_FIELD_RE = /(?:url|uri|target|cookie|token|password|passwd|pwd|secret|credential|authorization)/i;
  const HUD_NOTIFICATION_HISTORY_KEY = 'syrix_hud_notification_history_v1';
  const HUD_NOTIFICATION_HISTORY_LIMIT = 160;
  const ACTION_TRACE_LIMIT = 80;
  let _actionTraceSeq = 0;
  const _actionTrace = [];
  const _pendingActions = new Map();

  function safeLogValue(value, depth) {
    if (value == null) return value;
    if (typeof value === 'string') return value.replace(PRIVATE_URL_RE, '[URL_REDACTED]');
    if (typeof value === 'number' || typeof value === 'boolean') return value;
    if (Array.isArray(value)) {
      if ((depth || 0) > 1) return '[ARRAY]';
      return value.slice(0, 8).map((v) => safeLogValue(v, (depth || 0) + 1));
    }
    if (typeof value === 'object') {
      if ((depth || 0) > 1) return '[OBJECT]';
      const out = {};
      for (const [k, v] of Object.entries(value)) {
        out[k] = PRIVATE_FIELD_RE.test(k) ? '[REDACTED]' : safeLogValue(v, (depth || 0) + 1);
      }
      return out;
    }
    return String(value);
  }

  function _traceAction(stage, detail) {
    const d = (detail && typeof detail === 'object') ? detail : {};
    const entry = Object.assign({
      seq: ++_actionTraceSeq,
      stage: String(stage || 'event'),
      ts: Date.now() / 1000,
    }, safeLogValue(d) || {});
    try {
      _actionTrace.push(entry);
      if (_actionTrace.length > ACTION_TRACE_LIMIT) {
        _actionTrace.splice(0, _actionTrace.length - ACTION_TRACE_LIMIT);
      }
      snap.syrix_action_trace = entry;
      snap.syrix_action_trace_recent = _actionTrace.slice(-20);
    } catch (_) {}
    try {
      // eslint-disable-next-line no-console
      console.info('[SYRIX_ACTION]', 'stage=' + entry.stage, safeLogValue(entry));
    } catch (_) {}
    try { window.dispatchEvent(new CustomEvent('syrix-action-trace', { detail: entry })); } catch (_) {}
    return entry;
  }

  function _newActionId(command) {
    return 'hud-' + Date.now().toString(36) + '-' + (++_actionTraceSeq).toString(36) + '-' + String(command || 'action').replace(/[^a-z0-9_-]+/gi, '_').slice(0, 32);
  }

  function _clearPendingAction(requestId, reason) {
    const id = String(requestId || '');
    if (!id) return;
    const p = _pendingActions.get(id);
    if (p && p.timer) {
      try { clearTimeout(p.timer); } catch (_) {}
    }
    if (p) _pendingActions.delete(id);
    _traceAction('pending_cleared', {
      request_id: id,
      command: p && p.command ? p.command : '',
      reason: String(reason || 'complete'),
    });
  }

  function _markActionFailed(requestId, command, code, reason) {
    _traceAction('action_failed_reason', {
      request_id: String(requestId || ''),
      command: String(command || ''),
      code: String(code || 'ERROR'),
      reason: String(reason || code || 'ERROR').slice(0, 220),
    });
  }

  let _notificationHistoryPruned = false;
  function _readHudNotificationHistory() {
    try {
      let rows = JSON.parse(localStorage.getItem(HUD_NOTIFICATION_HISTORY_KEY) || '[]');
      rows = Array.isArray(rows) ? rows.slice(-HUD_NOTIFICATION_HISTORY_LIMIT) : [];
      // 2026-07-10 (product review H5) — one-time prune of the transient
      // progress/infra entries that accumulated before alert triage existed
      // (the "160 unread alerts" state). Runs once per bridge lifetime.
      if (!_notificationHistoryPruned) {
        _notificationHistoryPruned = true;
        const kept = rows.filter((r) => _isAlertWorthy(
          String((r && r.module) || (r && r.source) || '').toLowerCase(),
          String((r && r.severity) || 'info').toLowerCase()
        ));
        if (kept.length !== rows.length) {
          rows = kept;
          try { localStorage.setItem(HUD_NOTIFICATION_HISTORY_KEY, JSON.stringify(rows)); } catch (_) {}
        }
      }
      return rows;
    } catch (_) {
      return [];
    }
  }

  function _writeHudNotificationHistory(rows) {
    try {
      localStorage.setItem(HUD_NOTIFICATION_HISTORY_KEY, JSON.stringify((rows || []).slice(-HUD_NOTIFICATION_HISTORY_LIMIT)));
    } catch (_) {}
  }

  function _humanizeKey(value) {
    const s = String(value || '').trim();
    if (!s) return '';
    return s.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
  }

  function _humanizeHudDetail(raw, command, ok) {
    const s = String(raw == null ? '' : raw).trim();
    const low = s.toLowerCase();
    const cmd = _humanizeKey(command || 'status');
    if (!s) return ok ? `${cmd} completed.` : `${cmd} failed.`;
    if (low === 'hud_ack_timeout') return 'The HUD did not confirm that request before the timeout.';
    if (low.includes('hud_ack_timeout')) return 'The HUD did not confirm that request before the timeout.';
    if (low.includes('electron_bridge_missing')) return 'The Electron HUD bridge was not ready for that action.';
    if (low.includes('permission_required') || low.includes('permission_error') || low.includes('access_denied')) {
      return `${cmd} needs permission before it can continue.`;
    }
    if (low.includes('revoked=true')) return `${cmd} is now turned off and permission has been revoked.`;
    if (low.includes('revoked=false')) return `${cmd} is now enabled.`;
    if (low === 'disabled') return `${cmd} is disabled.`;
    if (low === 'null' || low === 'none' || low === 'undefined') return `${cmd} did not provide a usable status.`;
    if (/^(true|false)$/i.test(s)) return `${cmd} is ${low === 'true' ? 'on' : 'off'}.`;
    if (/^[a-z0-9_:-]+$/i.test(s) && /[_-]/.test(s)) return _humanizeKey(s) + '.';
    return s.replace(PRIVATE_URL_RE, '[URL_REDACTED]');
  }

  // 2026-07-10 (product review H5) — alert triage. The Alerts panel had 160
  // "unread" entries that were transient progress ticks ("Working on it…",
  // "Thinking") and brain-pool plumbing ("MODEL FALLBACK — gpt-oss-20b out of
  // tokens"). Those still flash on the dynamic island, but they are not
  // alerts a user acts on, and a badge that always shows a number teaches
  // the user to ignore it. History keeps: kinds a user acts on (guardian /
  // resource warnings / reminders), plus real errors of unknown kinds.
  const TRANSIENT_NOTICE_KINDS = new Set([
    'processing', 'system', 'thinking', 'searching', 'model_fallback',
    'brain_fallback', 'brain_restored', 'brain_route', 'spark_run',
    'semantic_prefetch', 'semantic_recall', 'screen_watch', 'speak',
    'done', 'sources', 'info', 'status',
  ]);
  const ACTIONABLE_NOTICE_KINDS = new Set([
    'guardian', 'cpu', 'vram', 'storage', 'memory_pressure', 'reminder',
    'timer', 'morning_brief', 'technical_issue', 'backend_offline',
    'mic_unavailable', 'voice', 'vision',
  ]);
  function _isAlertWorthy(kindLow, severity) {
    if (ACTIONABLE_NOTICE_KINDS.has(kindLow)) return true;
    if (TRANSIENT_NOTICE_KINDS.has(kindLow)) return false;
    return severity !== 'info';
  }

  function recordHudNotification(raw, source) {
    if (!raw || typeof raw !== 'object') return null;
    const seq = Number(raw.seq || 0);
    const command = String(raw.command || raw.kind || raw.action || 'status');
    const toneRaw = String(raw.tone || '').toLowerCase();
    const ok = ('ok' in raw) ? !!raw.ok : toneRaw !== 'error';
    const severity = toneRaw === 'warn' ? 'warn' : (ok ? 'info' : 'error');
    const kindLow = String(raw.kind || raw.command || raw.module || source || '').toLowerCase();
    if (!_isAlertWorthy(kindLow, severity)) return null;
    const detailRaw = raw.text || raw.reason || raw.detail || '';
    const entry = {
      id: `${source || 'hud'}-${seq || Date.now()}-${Math.floor(Math.random() * 10000)}`,
      seq,
      ts: Number(raw.ts || Date.now() / 1000),
      time: new Date((Number(raw.ts || Date.now() / 1000)) * 1000).toISOString(),
      source: String(source || raw.source || raw.kind || 'hud'),
      module: String(raw.module || raw.kind || raw.command || source || 'hud'),
      severity,
      message: _humanizeHudDetail(detailRaw, command, ok),
      technical: safeLogValue(raw),
    };
    // FLAW-401 (2026-06-04): null-safety. _readHudNotificationHistory
    // can return null/undefined if the persisted blob is corrupt, and the
    // bridge would crash with "Cannot read properties of undefined (length)".
    const rows = Array.isArray(_readHudNotificationHistory())
      ? _readHudNotificationHistory()
      : [];
    const sig = `${entry.seq}|${entry.source}|${entry.module}|${entry.message}`;
    const last = rows.length > 0 ? rows[rows.length - 1] : null;
    const lastSig = last ? `${last.seq}|${last.source}|${last.module}|${last.message}` : '';
    if (sig !== lastSig) rows.push(entry);
    const clipped = rows.slice(-HUD_NOTIFICATION_HISTORY_LIMIT);
    _writeHudNotificationHistory(clipped);
    snap.hud_notification_history = clipped;
    try { window.dispatchEvent(new CustomEvent('syrix-hud-notification-history', { detail: { entry, history: clipped } })); } catch (_) {}
    return entry;
  }

  function getHudNotificationHistory() {
    return _readHudNotificationHistory();
  }

  function clearHudNotificationHistory() {
    _writeHudNotificationHistory([]);
    snap.hud_notification_history = [];
    try { window.dispatchEvent(new CustomEvent('syrix-hud-notification-history', { detail: { history: [] } })); } catch (_) {}
    publish();
    return true;
  }

  let _technicalNoticeSeq = 0;
  function notifyTechnicalIssue(kind, human, technical, extra) {
    _technicalNoticeSeq += 1;
    const note = Object.assign({
      seq: Date.now() + _technicalNoticeSeq,
      kind: String(kind || 'technical_issue'),
      command: String(kind || 'technical_issue'),
      tone: 'warn',
      ok: false,
      text: String(human || "That didn't go through.").slice(0, 180),
      detail: String(technical || human || '').slice(0, 1200),
      ts: Date.now() / 1000,
    }, (extra && typeof extra === 'object') ? extra : {});
    snap.last_island_notice = {
      seq: note.seq,
      kind: note.kind,
      tone: 'warn',
      text: note.text,
      detail: note.text,
      ts: note.ts,
    };
    snap.syrix_latest_hud_notification = note;
    recordHudNotification(note, note.kind);
    try {
      console.warn('[HUD_NOTICE] kind=' + note.kind + ' user_text=' + note.text + ' technical=' + String(note.detail || '').replace(/\s+/g, '_').slice(0, 220));
    } catch (_) {}
    publish();
    try { window.dispatchEvent(new CustomEvent('syrix-technical-notification', { detail: note })); } catch (_) {}
    return note;
  }

  let socket = null;
  let _wsAuthenticated = false;
  let _reconnAttempt = 0;
  let _reconnTimer = null;
  let _pendingChat = null;          // { id, resolve, sentTs, lineCount, completed, voice }
  // 2026-07-04 — streaming buffers are per request id. One global buffer let
  // overlapping turns (spark then zenith, typed over voice) interleave into a
  // single string that rendered under whichever turn was visible — the
  // "models leak into each other" bug. Keyed by rid; '' collects rid-less
  // legacy frames. syrix-chat-token events now carry `rid` so shells can
  // scope what they render.
  const _streamBuffers = new Map(); // rid ('' for legacy) → accumulated text

  function _endStream(rid) {
    const key = String(rid || '').trim();
    if (!_streamBuffers.has(key)) return;
    _streamBuffers.delete(key);
    try {
      window.dispatchEvent(new CustomEvent('syrix-chat-token', {
        detail: { token: '', text: '', rid: key, request_id: key, done: true }
      }));
    } catch (_) {}
  }
  let _chatRequestSeq = 0;
  // Request ids used to restart at "1" whenever the Electron renderer was
  // reloaded. hud_chat.json intentionally survives renderer/app restarts, so
  // the bridge then mistook every later assistant reply carrying "1" for an
  // older turn and suppressed it. Keep ids short and log-friendly, but scope
  // them to this renderer session so they remain unique across restarts.
  const _chatRequestSession = Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
  const _completedChatRequests = new Map();
  // Cancelled ownership must outlive the longest backend worker. A tiny array
  // let sufficiently old work become eligible for delivery again after a burst
  // of newer cancels. Expire deterministically by age, never by arbitrary pop.
  const _chatCancelledTurns = new Map(); // request id → cancellation metadata
  const _CHAT_CANCEL_TTL_MS = 2 * 60 * 60 * 1000;
  const _CHAT_CANCEL_MAX = 256;
  let _lastChatDispatch = { key: '', ts: 0 };
  let _lastChatPollTs = 0;
  let _chatResetTs = 0;
  let _lastChatResetNonce = '';
  let _lastBrainPoll  = 0;
  let _lastStatePoll  = 0;
  let _lastHudAppReqPoll = 0;
  let _lastWsStateAt  = 0;          // timestamp of last state pushed over WS
  let _started         = false;
  let _chatLines       = [];
  let _stateThrottleMs = 1400;
  let _brainThrottleMs = 2000;
  let _chatPollMs      = 1200;
  // When WS is delivering state, skip duplicate JSON polls for this window (ms).
  // Falls back to JSON polling automatically when WS drops or stops pushing.
  const _WS_FRESH_MS    = 3000;
  const _HUD_APP_REQ_POLL_MS = 250;
  let _lastHudNotificationHistorySeq = 0;
  let _hudAppNativeStateSeen = false;
  let _loggedIgnoredPersistedHudApps = false;
  let _lastWidgetAckHistorySeq = 0;
  let _lastIslandNoticeHistorySeq = 0;
  // Monotonic state_version of the freshest brain-related broadcast the bridge
  // has accepted. Stale brain_list events (older version) are dropped before
  // dispatch so an out-of-order WS message cannot clobber the user's
  // selection. Backend bumps the counter on every brain switch attempt.
  let _lastBrainStateVersion = 0;

  // Latest known snapshot — React subscribers receive a copy each tick.
  const snap = {
    status: 'connecting',     // 'connecting' | 'online' | 'offline'
    brain: '',                // friendly brain name (current_brain)
    model: '',                // current model id (groq_model | gemini_model | ollama_model)
    mic_level: 0,
    state: 'IDLE',
    voice_mode: false,        // backend-confirmed VOICE mode session flag
    tts_muted: false,         // backend-confirmed TTS mute flag
    safety_status: '',
    webcam_present: false,
    webcam_emotion: '',
    webcam_posture: '',
    screen_vision_revoked: false,
    trading_analyst_armed: false,
    cursor_mode_active: false,
    watchlist: [],
    cpu_percent: null,
    mem_percent: null,
    gpu_util_percent: null,
    gpu_temp_c: null,
    vram_used_mb: null,
    vram_total_mb: null,
    nvidia: null,             // hud_brain_state.json snapshot (full)
    free_web: null,           // hud_free_web_state.json snapshot
    last_chat: null,          // most recent chat line from backend
    chat_lines: [],           // deduped backend chat transcript
    chat_status: null,        // quiet HUD-only request progress; never a spoken fallback
    last_brain_test: null,    // most recent nvidia_brain_test result
    last_hand_control_event: null,  // {seq, action, ts} pushed by Python hand-control voice
    raw: null,                // full hud_state.json raw object
    // Phase-2B Pass-3 additions: live widget feeds pulled from hud_state.json
    // augmentation. Backend writes to these fields; the bridge re-publishes.
    timers: [],               // [{id,text,due_ts,remaining_s}]
    music: { provider: '', state: 'idle', track: '', artist: '' },
    weather: null,            // {city, temp_c, condition, wind, humidity, ts}
    memory_web: null,         // {nodes:[], edges:[], status, recent:[]}
    memory_review: null,      // {pending_count, approved_count, rejected_count, last_saved, save_errors}
    obsidian_status: null,    // {enabled, vault_path, total, by_kind:{}, latest:[], last_write_ts, last_write_path, last_error, ts}
    recent_memories: null,    // {recent:[{kind,title,ts,filename}], ts}
    memory_facts: null,       // {facts:[{text,timestamp}], ts} — REAL saved memory (memory.json)
    last_image: null,         // {prompt, url, path, provider, ts}
    last_diagnostic: null,    // {summary, providers:{...}, ts}
    feature_status: null,     // backend feature flags snapshot
    syrix_api_status: null,   // {apis:{...}, ts}; key presence only, never values
    syrix_brain_status: null, // {providers:{id:{available,reason,label}}, active, manual}
    // Phase-2B Pass-4 (runtime contract verification): per-command ack so the
    // React side can log success/error for every helper call. Backend writes
    // these via _send_widget_ack in syrix_host_hud_wiring.py.
    last_widget_ack: null,    // {seq, command, ok, detail, ts}
    last_island_notice: null, // {seq, text, tone, kind, ts}  — system warnings routed to dynamic island instead of chat
    island_card: null,        // {seq, kind:'sports'|'wiki', ttl_ms, ...} — rich Dynamic Island card pushed by backend
    island_card_history: [],  // rolling list of past island_card payloads — reopenable ONLY from the island panel
    widget_acks_recent: [],   // ring buffer of last 10 acks
    last_map: null,           // {query|origin|destination, url, kind, ok, ts}
    // SYRIX HUD App Dock state — published by Electron main via
    // window.syrixHud.hudApp.onState + by Python via hud_state.json so the
    // React layer always has a single source of truth.
    syrix_hud_apps: [],            // [{id,url,title,mode,bounds,canGoBack,canGoForward,loading,vision}]
    syrix_active_hud_app: null,    // active app id (orb compact mode key)
    syrix_hud_app_ack: null,       // {seq, command, ok, detail, ts}
    syrix_active_hud_context: null,// HUD-only active app/tab/page context for voice follow-ups
    syrix_latest_hud_notification: null,
    hud_notification_history: _readHudNotificationHistory(),
    syrix_hud_app_mode: null,      // mirror of active app's mode
    syrix_hud_app_vision_active: false,
    syrix_hud_app_request: null,   // {seq, action, ...} fired by Python voice for the bridge
    // Phase-2C: Blender / Contactless control feeds.
    syrix_blender: null,           // {ok, action, subject, output_path, blend_path, detail, ts}
    syrix_contactless_control: null, // {enabled, mode, allowed_actions[], source, ts}
    // Phase-2D: vision workspace + SYRIX 3D sandbox state mirrored from
    // hud_state.json (Python authoritative) + local renderer mirror.
    syrix_vision_workspace: null,  // {open, surface, mode, indicator_on, ts}
    syrix_sandbox_request: null,   // {seq, action, subject?, direction?, value?, ts}
    syrix_pass2: null,             // Pass-2 live widgets: market data, paper trading, memory graph
    // Connectors page (2026-07-12): full subsystem snapshot pushed by
    // syrix_connectors.hud_api — {connectors[], activity[], pending_confirm[], auth_flow, ...}.
    syrix_connectors: null,
    // On-demand widget open requests — Python writes {type, seq} to trigger HUD widget open.
    syrix_open_widget: null,       // {type: 'setup'|string, seq: int}
    // Short brain label for HUD chip display: "Groq" | "Ollama" | "NIM Vision" | etc.
    brain_label: '',
    // 2026-05-27 — Long-form report panel state. Backend writes full body
    // here when a reply exceeds the chat-line budget (research, code,
    // analysis). null when no report is pending. The chat-line stays as
    // the short summary; this is for the scrollable side panel.
    syrix_long_report: null,
    // 2026-05-31 — Real-time activity trace steps (Deep Research).
    syrix_trace_steps: [],
    syrix_latest_trace_step: null,
    // CAP-17 (2026-06-04): morning brief structured payload for the visual card.
    syrix_morning_brief_card: null,
  };
  const subscribers = new Set();

  function publish() {
    // Shallow copy to avoid subscriber mutation of bridge internal state.
    const out = Object.assign({}, snap);
    subscribers.forEach((fn) => { try { fn(out); } catch (_) {} });
  }

  function subscribe(fn) {
    if (typeof fn !== 'function') return () => {};
    subscribers.add(fn);
    // Push current snapshot immediately so the subscriber primes its state.
    try { fn(Object.assign({}, snap)); } catch (_) {}
    return () => subscribers.delete(fn);
  }

  function setStatus(s) {
    if (snap.status === s) return;
    snap.status = s;
    publish();
  }

  /* ── WebSocket ────────────────────────────────────────────────── */

  function _authenticateHudSocket(ws) {
    const native = window.syrixHud;
    if (!native || typeof native.getHudWsAuthToken !== 'function') {
      try { ws.close(1008, 'HUD authentication unavailable'); } catch (_) {}
      return;
    }
    Promise.resolve(native.getHudWsAuthToken()).then((tokenValue) => {
      if (socket !== ws || ws.readyState !== WebSocket.OPEN) return;
      const token = String(tokenValue || '');
      if (!/^[0-9a-fA-F]{64}$/.test(token)) {
        try { ws.close(1008, 'HUD authentication unavailable'); } catch (_) {}
        return;
      }
      // Authentication is deliberately the first application frame. Do not
      // use wsSend(): normal command traffic stays blocked until the backend's
      // post-auth hello proves this handshake completed.
      ws.send(JSON.stringify({ type: 'auth', token }));
    }).catch(() => {
      try { ws.close(1008, 'HUD authentication unavailable'); } catch (_) {}
    });
  }

  function connect() {
    // Only emit 'connecting' on first attempt or after being online.
    // During reconnect retries (status already 'reconnecting'), stay 'reconnecting'
    // so the island doesn't flash "CONNECTING"/"RECONNECTING" on every retry.
    if (snap.status !== 'reconnecting' && snap.status !== 'offline') setStatus('connecting');
    try {
      if (socket && socket.readyState !== WebSocket.CLOSED) {
        try { socket.close(); } catch (_) {}
      }
    } catch (_) {}
    let ws;
    try {
      ws = new WebSocket(HUD_WS_URL);
    } catch (_) {
      setStatus('offline');
      scheduleReconnect();
      return;
    }
    socket = ws;
    ws.addEventListener('open', () => {
      _wsAuthenticated = false;
      _authenticateHudSocket(ws);
    });
    ws.addEventListener('close', () => {
      if (socket !== ws) return;
      _wsAuthenticated = false;
      setStatus('reconnecting');
      scheduleReconnect();
    });
    ws.addEventListener('error', () => {
      // close handler will run anyway
    });
    ws.addEventListener('message', (event) => {
      if (socket !== ws) return;
      try {
        const msg = JSON.parse(event.data);
        if (!msg || typeof msg !== 'object') return;
        if (msg.type === 'hello') {
          if (!_wsAuthenticated) {
            if (msg.authenticated !== true) {
              try { ws.close(1008, 'HUD authentication required'); } catch (_) {}
              return;
            }
            _wsAuthenticated = true;
            _reconnAttempt = 0;
            setStatus('online');
            // The list request is the first normal command and therefore only
            // runs after the server accepted the nonce and sent its hello.
            try {
              ws.send(JSON.stringify({ type: 'command', command: 'list_brains' }));
            } catch (_) { /* listBrains polling remains the retry path */ }
            // Private mode lives in the backend; a HUD that reloads or
            // reconnects asks for it rather than assuming it is off.
            try {
              ws.send(JSON.stringify({ type: 'command', command: 'private_mode_get' }));
            } catch (_) {}
          }
          if (Array.isArray(msg.chat_history) && msg.chat_history.length) {
            snap.chat_history = msg.chat_history.slice(-20);
            publish();
          }
        } else if (msg.type === 'state') {
          _lastWsStateAt = Date.now();
          applyState(msg.data || {});
        } else if (msg.type === 'chat') {
          // Full chat lines arrived — end the stream for exactly the rids
          // they cover (a rid-less line ends the legacy '' stream only).
          (msg.lines || []).forEach((ln) => {
            if (ln && typeof ln === 'object' && lineRole(ln) === 'assistant') {
              _endStream(lineRequestId(ln));
            }
          });
          applyChatLines(msg.lines || []);
        } else if (msg.type === 'chat_sessions') {
          // A deleted active session clears the live transcript as well as the
          // history store. Apply the reset before publishing the new list so
          // the renderer cannot briefly show the deleted chat again.
          const resetNonce = String(msg.chat_reset_nonce || '');
          if (resetNonce && resetNonce !== _lastChatResetNonce) {
            _lastChatResetNonce = resetNonce;
            clearChatSnapshot('ws_session_delete');
          }
          snap.raw = Object.assign({}, snap.raw || {}, {
            syrix_chat_sessions: msg.sessions || [],
            syrix_chat_active_session: msg.active_id || '',
          });
          publish();
        } else if (msg.type === 'chat_token') {
          // Incremental token from streaming brain response — rid-scoped.
          // Stragglers from a superseded turn keep their own buffer; the
          // event carries the rid, so no shell renders them under another turn.
          const tokRid = String(msg.request_id || '').trim();
          const tokBuf = (_streamBuffers.get(tokRid) || '') + (msg.token || '');
          _streamBuffers.set(tokRid, tokBuf);
          while (_streamBuffers.size > 8) {
            _streamBuffers.delete(_streamBuffers.keys().next().value);
          }
          // 2026-07-06 — remember which request ids carried a REAL token
          // stream so a shell can key its "already streamed, skip the
          // fallback typewriter" check off the exact rid instead of a fuzzy
          // text-prefix match. Post-stream formatting/cleanup on the saved
          // line can shift the first ~48 chars just enough to break a prefix
          // match, which replays the fake typewriter on an answer the user
          // already watched stream in (visible as a flash-then-restart).
          if (tokRid) {
            try {
              if (!window.__syrixStreamedRidSeen) window.__syrixStreamedRidSeen = new Set();
              window.__syrixStreamedRidSeen.add(tokRid);
              while (window.__syrixStreamedRidSeen.size > 24) {
                window.__syrixStreamedRidSeen.delete(window.__syrixStreamedRidSeen.values().next().value);
              }
            } catch (_) {}
          }
          try {
            window.dispatchEvent(new CustomEvent('syrix-chat-token', {
              detail: { token: msg.token || '', text: tokBuf, rid: tokRid, request_id: tokRid, seq: msg.seq || 0, done: false }
            }));
          } catch (_) {}
        } else if (msg.type === 'chat_media') {
          // Pictures for the answer being written, ahead of the finished line.
          try { window.dispatchEvent(new CustomEvent('syrix-chat-media', { detail: msg })); } catch (_) {}
        } else if (msg.type === 'chat_done') {
          // Explicit stream-end signal — clears the typing cursor immediately,
          // before the full hud_chat.json state write propagates. Scoped: only
          // the finishing rid's stream ends; other in-flight streams live on.
          _endStream(String(msg.request_id || '').trim());
        } else if (msg.type === 'hud_app_request') {
          // Phase 2 — ack protocol v2: dock requests arrive instantly over WS
          // instead of waiting for the next state poll. _maybeRunHudAppRequest
          // dedupes by request_id/seq, so the state-poll fallback delivering
          // the same request again is harmless.
          _maybeRunHudAppRequest({ syrix_hud_app_request: msg.data || null });
        } else if (msg.type === 'sandbox_op') {
          // Phase 5 — HUD-sandbox automation: run a primitive against the
          // embedded BrowserView and reply with the result over WS. Policy is
          // already enforced on the Python side before this arrives.
          _runSandboxOp(msg.data || {});
        } else if (msg.type === 'sc_browser_op') {
          // Supercomputer browser op — separate module, separate partition,
          // separate view registry from the app-dock sandbox above.
          _runScBrowserOp(msg.data || {});
        } else if (msg.type === 'brain_list') {
          const ver = Number(msg.state_version || 0);
          if (ver > 0 && ver < _lastBrainStateVersion) {
            // Stale broadcast — older than the freshest brain change we have
            // already applied. Dropping prevents snap-back from late WS frames.
            return;
          }
          if (ver > 0) _lastBrainStateVersion = ver;
          window.dispatchEvent(new CustomEvent('syrix-brain-list', {
            detail: {
              brains: msg.brains || [],
              manual: String(msg.manual || 'auto'),
              manual_brain_id: String(msg.manual_brain_id || ''),
              manual_brain_model: String(msg.manual_brain_model || ''),
              manual_brain_role: String(msg.manual_brain_role || ''),
              manual_brain_display: String(msg.manual_brain_display || ''),
              nim_preferred_model_id: String(msg.nim_preferred_model_id || ''),
              state_version: ver,
            }
          }));
        } else if (msg.type === 'brain_select_ack') {
          // 2026-05-27 — Per-request brain switch ack from backend. Renderer
          // uses this to clear the pending spinner immediately rather than
          // relying on the 8s timeout. Two stages fire: "received" right after
          // the backend gets the command, "done" after _request_brain_switch
          // resolves with ok/reason/status (OK | AUTH_REQUIRED | COOLDOWN |
          // UNAVAILABLE).
          const rid = String(msg.request_id || '');
          const stage = String(msg.stage || 'done');
          const ok = !!msg.ok;
          const status = String(msg.status || (ok ? 'OK' : 'UNAVAILABLE'));
          const reason = String(msg.reason || '');
          if (rid) {
            if (stage === 'done') {
              _clearPendingAction(rid, ok ? 'brain_ack_ok' : ('brain_ack_' + status.toLowerCase()));
              if (!ok) _markActionFailed(rid, 'set_brain_priority', status, reason);
            }
          }
          _traceAction('brain_select_ack', { request_id: rid, stage, ok, status, priority: String(msg.priority || ''), reason });
          try {
            window.dispatchEvent(new CustomEvent('syrix-brain-select-ack', {
              detail: { request_id: rid, stage, ok, status, reason, priority: String(msg.priority || '') }
            }));
          } catch (_) {}
        } else if (msg.type === 'chat_status') {
          // 2026-05-27 — Fast-ack signal from backend: a typed/voice frame
          // was accepted and the brain call is starting. Renderer shows a
          // "Thinking..." chip within ~300ms so input never feels dropped.
          const state = String(msg.state || '');
          const stateNorm = state.toLowerCase();
          const rid = String(msg.request_id || '');
          snap.chat_status = { state, request_id: rid, source: String(msg.source || ''), ts: Number(msg.ts || Date.now() / 1000) };
          publish();
          try {
            window.dispatchEvent(new CustomEvent('syrix-chat-status', {
              detail: { state, request_id: rid, source: String(msg.source || '') }
            }));
          } catch (_) {}
          if (stateNorm === 'thinking' || stateNorm === 'processing') {
            _chatLifecycleLog('thinking_shown', { request_id: rid, source: String(msg.source || 'backend') });
          } else if (['completed', 'complete', 'failed', 'timeout', 'cancelled', 'canceled'].includes(stateNorm)) {
            const p = _pendingChat;
            if (p && rid && String(p.id) === rid && !isChatRequestComplete(p)) {
              const reason = stateNorm === 'complete' ? 'completed' : (stateNorm === 'canceled' ? 'cancelled' : stateNorm);
              settlePendingChat(p, '', reason);
            }
          }
        } else if (msg.type === 'chat_session_loaded') {
          // 2026-07-05 — instant chat-open push, same poll-lag fix as above.
          // Shape matches the hud_state.json fallback exactly (nonce-keyed)
          // so the existing React effect that watches
          // snap.raw.syrix_loaded_session.nonce needs no changes.
          snap.raw = Object.assign({}, snap.raw || {}, {
            syrix_loaded_session: {
              nonce: String(msg.nonce || ''),
              id: msg.id,
              title: msg.title,
              messages: msg.messages || [],
              ts: msg.ts,
            },
          });
          publish();
        } else if (msg.type === 'chat_session_peeked') {
          // 2026-07-05 — read-only hover-preload response. Deliberately NOT
          // written into snap.raw: it's keyed/ephemeral (one per hovered
          // session), unlike syrix_loaded_session which represents THE
          // single active/open chat. A stray late peek must never clobber
          // whichever chat is actually open.
          try {
            window.dispatchEvent(new CustomEvent('syrix:session-peeked', {
              detail: { id: msg.id, title: msg.title, messages: msg.messages || [] }
            }));
          } catch (_) {}
        } else if (msg.type === 'dictation_result') {
          // 2026-07-11 — composer dictation reply: backend ran the recorded
          // clip through the real faster-whisper model and sends the
          // transcript back here. Ephemeral, keyed by request_id — not part
          // of snap.raw.
          try {
            window.dispatchEvent(new CustomEvent('syrix:dictation-result', {
              detail: {
                request_id: String(msg.request_id || ''),
                ok: !!msg.ok,
                text: String(msg.text || ''),
                reason: String(msg.reason || ''),
              }
            }));
          } catch (_) {}
        } else if (msg.type === 'voice_transcript' || msg.type === 'voice_audio') {
          // Voice orb: an utterance's transcript, and the spoken reply one
          // sentence of audio at a time. Ephemeral — never written into snap.
          try {
            window.dispatchEvent(new CustomEvent(
              msg.type === 'voice_audio' ? 'syrix:voice-audio' : 'syrix:voice-transcript',
              { detail: msg }));
          } catch (_) {}
        } else if (msg.type === 'private_mode') {
          snap.private_mode = !!msg.active;
          try { window.dispatchEvent(new CustomEvent('syrix:private-mode', { detail: { active: !!msg.active } })); } catch (_) {}
          publish();
        } else if (msg.type === 'voice_caption') {
          // 2026-07-11 — Voice Mode word-sync captions. The backend publishes
          // begin/segment/end as each Kokoro audio segment starts playing so
          // the voice overlay can reveal the words in time with the speech.
          // Ephemeral by design: never written into snap, never persisted.
          try {
            window.dispatchEvent(new CustomEvent('syrix-voice-caption', {
              detail: {
                event: String(msg.event || ''),
                utterance_id: String(msg.utterance_id || ''),
                seq: Number(msg.seq || 0),
                text: String(msg.text || ''),
                duration_s: Number(msg.duration_s || 0),
                full_text: String(msg.full_text || ''),
                played: !!msg.played,
                aborted: !!msg.aborted,
                ts: Number(msg.ts || Date.now() / 1000),
              }
            }));
          } catch (_) {}
        } else if (msg.type === 'voice_turn') {
          // 2026-07-17 — Voice Session V2 canonical turn stream. The backend
          // owns one authoritative lifecycle per assistant turn and publishes
          // session_state / user_partial / user_final / assistant_created /
          // assistant_sentence / assistant_done / tool_activity / turn_error.
          // Ephemeral by design: never written into snap.chat_lines, so the
          // normal chat panel can never see any voice-turn text.
          try {
            window.dispatchEvent(new CustomEvent('syrix-voice-turn', {
              detail: {
                event: String(msg.event || ''),
                session_id: String(msg.session_id || ''),
                generation: Number(msg.generation || 0),
                turn_id: String(msg.turn_id || ''),
                seq: Number(msg.seq || 0),
                text: String(msg.text || ''),
                state: String(msg.state || ''),
                reason: String(msg.reason || ''),
                label: String(msg.label || ''),
                sentence_index: Number(msg.sentence_index || 0),
                duration_s: Number(msg.duration_s || 0),
                spoken_sentences: Number(msg.spoken_sentences || 0),
                interrupted: !!msg.interrupted,
                ts: Number(msg.ts || Date.now() / 1000),
              }
            }));
          } catch (_) {}
        } else if (msg.type === 'app_close_request') {
          // "Close yourself" typed or spoken in chat. Closing through the
          // window's own close path lets the main process stop every SYRIX
          // process, exactly as the title-bar close button does.
          try { window.syrixHud && window.syrixHud.close && window.syrixHud.close(); } catch (_) {}
        } else if (msg.type === 'long_report') {
          // 2026-05-27 — Long-form report payload. Backend writes the full
          // report here; the chat already has the short summary. Renderer
          // pops a scrollable panel.
          const payload = {
            request_id: String(msg.request_id || ''),
            title: String(msg.title || ''),
            summary: String(msg.summary || ''),
            body: String(msg.body || ''),
            kind: String(msg.kind || 'report'),
            report_kind: String(msg.report_kind || ''),
            ts: Number(msg.ts || Date.now() / 1000),
          };
          snap.syrix_long_report = payload;
          publish();
          try {
            console.info('[HUD_BROWSER_TAB_HIDE_OLD_STRIP] syrix_long_report updated request_id=' + payload.request_id);
          } catch (_) {}
          try {
            window.dispatchEvent(new CustomEvent('syrix-long-report', { detail: payload }));
          } catch (_) {}
        } else if (msg.type === 'morning_brief_card') {
          // CAP-17 (2026-06-04): structured morning-brief payload (SPY/QQQ/
          // VIX/watchlist/positions). The chat line already shows the prose;
          // this payload is the visual card. Push it as a custom event so the
          // primary app shell can render it however it likes.
          const card = {
            title: String(msg.title || 'Morning brief'),
            summary: String(msg.summary || ''),
            payload: (msg.payload && typeof msg.payload === 'object') ? msg.payload : {},
            ts: Number(msg.ts || Date.now() / 1000),
          };
          snap.syrix_morning_brief_card = card;
          publish();
          try {
            window.dispatchEvent(new CustomEvent('syrix-morning-brief-card', { detail: card }));
          } catch (_) {}
          try { console.info('[MORNING_BRIEF_CARD] ts=' + card.ts); } catch (_) {}
        } else if (msg.type === 'chat_deep') {
          // ATLAS (2026-07-03) — the background Zenith deep pass finished for
          // an already-delivered reply: patch the matching assistant chat
          // line in place (same rid) and notify the renderers. Kept in
          // _deepByRid so a deep frame that races its chat line still merges
          // when the line arrives via mergeChatLines.
          const deepRid = String(msg.request_id || '');
          const deep = (msg.deep && typeof msg.deep === 'object') ? msg.deep : null;
          if (deepRid && deep) {
            _deepByRid.set(deepRid, deep);
            while (_deepByRid.size > 40) {
              _deepByRid.delete(_deepByRid.keys().next().value);
            }
            let deepPatched = false;
            for (let i = _chatLines.length - 1; i >= 0; i--) {
              const ln = _chatLines[i];
              if (ln && typeof ln === 'object' && lineRole(ln) === 'assistant' && lineRequestId(ln) === deepRid) {
                _chatLines[i] = { ...ln, deep };
                deepPatched = true;
                break;
              }
            }
            if (deepPatched) snap.chat_lines = _chatLines.slice();
            publish();
            try {
              window.dispatchEvent(new CustomEvent('syrix-chat-deep', {
                detail: { request_id: deepRid, deep },
              }));
            } catch (_) {}
            try { console.info('[CHAT_DEEP] rid=' + deepRid + ' status=' + String(deep.status || '') + ' patched=' + deepPatched); } catch (_) {}
          }
        } else if (msg.type === 'zenith_agent_event') {
          // ZENITH (2026-07-02) — real sub-agent deployment events feeding the
          // agent panel. rid-scoped: a new mission resets the ring so agent
          // cards never bleed across turns. The same events also land in
          // hud_state.json via the activity feed (kind 'zenith'), so the panel
          // still updates over the file poll when the WS is down; `zid` is the
          // shared dedupe key across both transports.
          const zev = { ...msg };
          zev.request_id = String(msg.request_id || msg.rid || '');
          zev.ts = Number(msg.ts || Date.now() / 1000);
          if (!Array.isArray(snap.zenith_events) || snap.zenith_rid !== zev.request_id) {
            snap.zenith_events = [];
            snap.zenith_rid = zev.request_id;
          }
          snap.zenith_events = [...snap.zenith_events.slice(-79), zev];
          publish();
          try { console.info('[ZENITH_AGENT_EVENT] rid=' + zev.request_id + ' agent=' + String(msg.agent_id || '') + ' status=' + String(msg.agent_status || '')); } catch (_) {}
        }
      } catch (_) {}
    });
  }

  function scheduleReconnect() {
    if (_reconnTimer) return;
    _reconnAttempt = Math.min(_reconnAttempt + 1, 10);
    const delay = Math.min(250 * Math.pow(1.5, _reconnAttempt - 1), 5000);
    _reconnTimer = setTimeout(() => {
      _reconnTimer = null;
      connect();
    }, delay);
  }

  function wsSend(payload) {
    if (!_wsAuthenticated || !socket || socket.readyState !== WebSocket.OPEN) return false;
    try {
      socket.send(JSON.stringify(payload));
      return true;
    } catch (_) {
      return false;
    }
  }

  /* ── State application ─────────────────────────────────────────── */

  function normalizeBrainLabel(value) {
    let s = String(value || '').replace(/\s+/g, ' ').trim();
    if (!s) return '';
    s = s.replace(/^brain\s*:\s*/i, '').trim();
    s = s.replace(/\s+brain\s*$/i, '').trim();
    const low = s.toLowerCase();
    // 2026-05-30 — Core V2 mode/route labels. Show them in a more readable form.
    if (low.startsWith('core_v2:')) {
      const tail = s.split(':').slice(1).join(':').trim();
      const t = tail.toLowerCase();
      if (t === 'deep_research') return 'Deep Research';
      if (t === 'live_research') return 'Live Research';
      if (t === 'brain') return 'Brain';
      if (t === 'website') return 'Website';
      if (t === 'pc_status') return 'PC Status';
      if (t === 'weather') return 'Weather';
      if (t === 'news') return 'News';
      if (t === 'map') return 'Map';
      if (t === 'chart') return 'Chart';
      if (t === 'memory') return 'Memory';
      if (t === 'identity') return 'Identity';
      if (t === 'thinking') return 'Thinking';
      return tail || 'Core V2';
    }
    const parts = s.split('·').map(x => x.trim()).filter(Boolean);
    if ((low.includes('nvidia') || low.includes('nim')) && parts.length > 1) return parts[parts.length - 1];
    if (low === 'nim' || low === 'nvidia nim' || low === 'nvidia_nim') return 'NIM';
    if (low === 'groq') return 'Groq';
    if (low === 'auto' || low.includes('automatic')) return 'AUTO';
    if (low === 'ollama') return 'Ollama';
    if (low === 'local') return 'Local';
    return s;
  }

  function pickBrainLabel(raw) {
    // 1. Prefer the short clean label written by _brain_ui_write_snapshot (e.g. "Groq", "NIM Vision").
    if (raw && raw.syrix_last_brain_label && String(raw.syrix_last_brain_label).trim()) {
      return normalizeBrainLabel(raw.syrix_last_brain_label);
    }
    // 2. Fall back to active NIM brain if available.
    const nvActive = raw && raw.__nvidia_active;
    if (nvActive && (nvActive.last_provider || nvActive.active_brain)) {
      return normalizeBrainLabel(nvActive.active_brain || nvActive.last_provider);
    }
    // 3. Legacy current_brain field.
    return raw && raw.current_brain ? normalizeBrainLabel(raw.current_brain) : '';
  }

  function pickModel(raw) {
    if (!raw) return '';
    if (raw.brain_current_model && !/^(?:unknown|none)$/i.test(String(raw.brain_current_model))) return String(raw.brain_current_model);
    if (raw.brain_manual_model && !/^(?:unknown|none)$/i.test(String(raw.brain_manual_model))) return String(raw.brain_manual_model);
    if (raw.thinking_provider && raw.thinking_provider.includes(':')) return raw.thinking_provider;
    // Best-effort guess based on current brain
    const b = (raw.current_brain || '').toLowerCase();
    if (b.includes('groq')) return raw.groq_model || '';
    if (b.includes('gemini')) return raw.gemini_model || '';
    if (b.includes('ollama')) return raw.ollama_model || '';
    return raw.groq_model || '';
  }

  function applyState(data) {
    if (!data || typeof data !== 'object') return;
    snap.raw = data;
    const resetNonce = String(data.syrix_chat_reset_nonce || '');
    if (resetNonce && resetNonce !== _lastChatResetNonce) {
      _lastChatResetNonce = resetNonce;
      clearChatSnapshot('state_session_delete');
    }
    snap.state = data.state || data.voice_state || snap.state;
    snap.voice_state = data.voice_state || data.state || snap.voice_state;
    _logTtsStateIfChanged(snap.state);
    snap.safety_status = data.safety_status || '';
    snap.mic_level = typeof data.mic_level === 'number' ? data.mic_level : snap.mic_level;
    // VOICE MODE (2026-07-08): backend-confirmed voice-mode + TTS-mute flags so
    // the voice overlay reflects the real session state, not just the click.
    if (typeof data.syrix_voice_mode === 'boolean') snap.voice_mode = data.syrix_voice_mode;
    if (typeof data.syrix_tts_muted === 'boolean') snap.tts_muted = data.syrix_tts_muted;
    snap.webcam_present = !!data.webcam_present;
    snap.webcam_emotion = data.webcam_emotion || '';
    snap.webcam_posture = data.webcam_posture || '';
    snap.screen_vision_revoked = !!data.screen_vision_revoked;
    snap.trading_analyst_armed = !!data.trading_analyst_armed;
    snap.cursor_mode_active = !!data.cursor_mode_active;
    snap.watchlist = Array.isArray(data.watchlist) ? data.watchlist : [];
    snap.cpu_percent = typeof data.cpu_percent === 'number' ? data.cpu_percent : null;
    snap.mem_percent = typeof data.mem_percent === 'number' ? data.mem_percent
                     : typeof data.ram_percent === 'number' ? data.ram_percent : null;
    snap.gpu_util_percent = data.gpu_util_percent;
    snap.gpu_temp_c = data.gpu_temp_c;
    snap.vram_used_mb = data.vram_used_mb;
    snap.vram_total_mb = data.vram_total_mb;
    snap.brain = pickBrainLabel(data) || snap.brain;
    snap.model = pickModel(data) || snap.model;
    // Keep a dedicated clean label field separate from the legacy brain+model combo.
    if (data.syrix_last_brain_label && String(data.syrix_last_brain_label).trim()) {
      snap.brain_label = normalizeBrainLabel(data.syrix_last_brain_label);
    }
    // Phase-2B Pass-3: pull widget feed fields if backend wrote them.
    if (Array.isArray(data.syrix_timers)) snap.timers = data.syrix_timers;
    if (data.syrix_music && typeof data.syrix_music === 'object') snap.music = data.syrix_music;
    if (data.syrix_weather && typeof data.syrix_weather === 'object') snap.weather = data.syrix_weather;
    if (data.syrix_memory_web && typeof data.syrix_memory_web === 'object') snap.memory_web = data.syrix_memory_web;
    if (data.syrix_memory_review && typeof data.syrix_memory_review === 'object') {
      snap.memory_review = data.syrix_memory_review;
      if (snap.memory_web && typeof snap.memory_web === 'object') {
        snap.memory_web = Object.assign({}, snap.memory_web, { review: snap.memory_review });
      }
    }
    if (data.syrix_obsidian_status && typeof data.syrix_obsidian_status === 'object') snap.obsidian_status = data.syrix_obsidian_status;
    if (data.syrix_recent_memories && typeof data.syrix_recent_memories === 'object') snap.recent_memories = data.syrix_recent_memories;
    if (data.syrix_memory_facts && typeof data.syrix_memory_facts === 'object') snap.memory_facts = data.syrix_memory_facts;
    if (data.syrix_last_image && typeof data.syrix_last_image === 'object') snap.last_image = data.syrix_last_image;
    if (data.syrix_last_diagnostic && typeof data.syrix_last_diagnostic === 'object') snap.last_diagnostic = data.syrix_last_diagnostic;
    if (data.feature_status && typeof data.feature_status === 'object') snap.feature_status = data.feature_status;
    if (data.syrix_api_status && typeof data.syrix_api_status === 'object') snap.syrix_api_status = data.syrix_api_status;
    if (data.syrix_brain_status && typeof data.syrix_brain_status === 'object') snap.syrix_brain_status = data.syrix_brain_status;
    if (data.syrix_last_map && typeof data.syrix_last_map === 'object') snap.last_map = data.syrix_last_map;
    if (Array.isArray(data.syrix_widget_acks_recent)) snap.widget_acks_recent = data.syrix_widget_acks_recent;
    // Phase-2C: Blender / Contactless control state.
    if (data.syrix_blender && typeof data.syrix_blender === 'object') snap.syrix_blender = data.syrix_blender;
    if (data.syrix_contactless_control && typeof data.syrix_contactless_control === 'object') {
      snap.syrix_contactless_control = data.syrix_contactless_control;
    }
    // Phase-2D mirrors. Renderer subscribers receive deltas; new seq triggers
    // the sandbox to apply the queued action exactly once.
    if (data.syrix_vision_workspace && typeof data.syrix_vision_workspace === 'object') {
      snap.syrix_vision_workspace = data.syrix_vision_workspace;
    }
    if (data.syrix_sandbox_request && typeof data.syrix_sandbox_request === 'object') {
      snap.syrix_sandbox_request = data.syrix_sandbox_request;
    }
    if (data.syrix_pass2 && typeof data.syrix_pass2 === 'object') {
      snap.syrix_pass2 = data.syrix_pass2;
    }
    // Connectors page snapshot (2026-07-12).
    if (data.syrix_connectors && typeof data.syrix_connectors === 'object') {
      snap.syrix_connectors = data.syrix_connectors;
    }
    // On-demand widget open (e.g. setup image from Python voice).
    if (data.syrix_open_widget && typeof data.syrix_open_widget === 'object') {
      snap.syrix_open_widget = data.syrix_open_widget;
    }
    // SYRIX HUD App Dock — Python voice commands write
    // `syrix_hud_app_request` here; we re-fire them through the bridge
    // helpers exactly once per new seq.
    _maybeRunHudAppRequest(data);
    if (Array.isArray(data.syrix_hud_apps) && !_hudAppNativeStateSeen) {
      if (data.syrix_hud_apps.length && !_loggedIgnoredPersistedHudApps) {
        _loggedIgnoredPersistedHudApps = true;
        try {
          const hadHeatmap = data.syrix_hud_apps.some((a) => /tradingview\.com\/heatmap\/stock/i.test(String((a && a.url) || '')));
          console.info('[HUD_APP] ignored persisted app-dock state on startup count=' + data.syrix_hud_apps.length + ' heatmap=' + hadHeatmap);
        } catch (_) {}
      }
      snap.syrix_hud_apps = [];
      snap.syrix_active_hud_app = null;
      snap.syrix_active_hud_context = null;
      snap.syrix_hud_app_mode = null;
      snap.syrix_hud_app_vision_active = false;
    }
    if (data.syrix_latest_hud_notification && typeof data.syrix_latest_hud_notification === 'object') {
      snap.syrix_latest_hud_notification = data.syrix_latest_hud_notification;
      const nseq = Number(data.syrix_latest_hud_notification.seq || 0);
      if (nseq && nseq !== _lastHudNotificationHistorySeq) {
        _lastHudNotificationHistorySeq = nseq;
        recordHudNotification(data.syrix_latest_hud_notification, 'hud-app');
      }
    }
    // Foundation upgrades (2026-05-23): live status for new backend modules.
    // Python publishes this from syrix_upgrades_publisher every ~3s.
    if (data.syrix_upgrades && typeof data.syrix_upgrades === 'object') {
      snap.syrix_upgrades = data.syrix_upgrades;
    }
    if (data.syrix_widget_ack && typeof data.syrix_widget_ack === 'object') {
      const ack = data.syrix_widget_ack;
      const seq = Number(ack.seq || 0);
      const lastSeq = (snap.last_widget_ack && Number(snap.last_widget_ack.seq)) || 0;
      if (seq && seq !== lastSeq) {
        snap.last_widget_ack = ack;
        _traceAction('backend_result', {
          request_id: String(ack.request_id || ack.hud_action_request_id || ''),
          command: String(ack.command || ''),
          ok: !!ack.ok,
          reason: String(ack.detail || ack.reason || '').slice(0, 220),
        });
        try {
          const rid = String(ack.request_id || ack.hud_action_request_id || '');
          const pending = rid ? _pendingActions.get(rid) : null;
          if (pending && pending.ts) {
            const ackMs = Math.max(0, Date.now() - Number(pending.ts || 0));
            console.info('[HUD_LATENCY] subsystem=bridge event=action_ack status=' + (ack.ok ? 'ok' : 'failed') + ' request_id=' + rid + ' latency_ms=' + ackMs + ' command=' + String(ack.command || ''));
          }
        } catch (_) {}
        if (seq !== _lastWidgetAckHistorySeq) {
          _lastWidgetAckHistorySeq = seq;
          recordHudNotification(ack, 'widget');
        }
        try {
          const tag = ack.ok ? '%c[SYRIX ✓]' : '%c[SYRIX ✗]';
          const color = ack.ok ? 'color:#16d37a' : 'color:#ff4f6e';
          // eslint-disable-next-line no-console
          console.info(tag, color, ack.command, '·', ack.detail || '(no detail)');
        } catch (_) {}
        // Emit a CustomEvent so widgets can show inline toasts if they want to.
        try {
          window.dispatchEvent(new CustomEvent('syrix-widget-ack', { detail: ack }));
        } catch (_) {}
        _traceAction('hud_updated', {
          request_id: String(ack.request_id || ack.hud_action_request_id || ''),
          command: String(ack.command || ''),
          ok: !!ack.ok,
          source: 'widget_ack',
        });
        if (!ack.ok) {
          _markActionFailed(
            ack.request_id || ack.hud_action_request_id || '',
            ack.command || '',
            'ERROR',
            ack.detail || ack.reason || 'backend_result_error',
          );
        }
        _clearPendingAction(ack.request_id || ack.hud_action_request_id || '', ack.ok ? 'backend_result_ok' : 'backend_result_error');
      }
    }
    // 2026-05-18: system warnings (CPU, memory, guardian alerts, etc.)
    // surface on the dynamic island, NOT in the chat panel.
    // Python writes `syrix_island_notice` to hud_state.json; we surface
    // only NEW notices (by seq) so a repeated tick does not re-fire the
    // same notice. The subscriber consumes `last_island_notice`.
    if (data.syrix_island_notice && typeof data.syrix_island_notice === 'object') {
      const nt = data.syrix_island_notice;
      const seq = Number(nt.seq || 0);
      const lastSeq = (snap.last_island_notice && Number(snap.last_island_notice.seq)) || 0;
      if (seq && seq !== lastSeq) {
        snap.last_island_notice = nt;
        if (seq !== _lastIslandNoticeHistorySeq) {
          _lastIslandNoticeHistorySeq = seq;
          recordHudNotification(nt, nt.kind || 'system');
        }
      }
    }
    // 2026-07-02 — Dynamic Island rich cards (scoreboard / wiki summary).
    // Backend writes `syrix_island_card`; only a NEW seq replaces the slot so
    // repeated state ticks never re-trigger the island's card animation.
    if (data.syrix_island_card && typeof data.syrix_island_card === 'object') {
      const card = data.syrix_island_card;
      const cardSeq = Number(card.seq || 0);
      const lastCardSeq = (snap.island_card && Number(snap.island_card.seq)) || 0;
      if (cardSeq && cardSeq !== lastCardSeq) {
        snap.island_card = card;
      }
    }
    // Rolling card history — reopenable from the island's own expanded panel.
    if (Array.isArray(data.syrix_island_card_history)) {
      snap.island_card_history = data.syrix_island_card_history;
    }
    // Persistent agent watches/tasks are published as a normal state key;
    // keep the explicit bridge projection so a full state tick never drops it.
    if (Array.isArray(data.syrix_tasks)) {
      snap.syrix_tasks = data.syrix_tasks;
    }
    // Fall-back to legacy fields when present.
    if (typeof data.memory_nodes === 'number' && !snap.memory_web) {
      snap.memory_web = {
        nodes: data.memory_nodes,
        edges: data.memory_edges || 0,
        recent: Array.isArray(data.memory_recent_nodes) ? data.memory_recent_nodes : [],
        status: data.memory_status || 'live',
      };
    }
    publish();
  }

  function lineTsSeconds(line) {
    if (!line || typeof line !== 'object') return 0;
    const direct = line.ts_epoch ?? line.epoch ?? line.time_epoch;
    if (typeof direct === 'number' && Number.isFinite(direct)) return direct > 1e12 ? direct / 1000 : direct;
    const raw = line.ts ?? line.timestamp ?? line.time;
    if (typeof raw === 'number' && Number.isFinite(raw)) return raw > 1e12 ? raw / 1000 : raw;
    if (typeof raw === 'string' && raw.trim()) {
      const n = Number(raw);
      if (Number.isFinite(n)) return n > 1e12 ? n / 1000 : n;
      const ms = Date.parse(raw);
      if (Number.isFinite(ms)) return ms / 1000;
    }
    return 0;
  }

  function lineText(line) {
    if (!line || typeof line !== 'object') return '';
    return String(line.text || line.body || line.message || '').trim();
  }

  function lineRole(line) {
    // Core Stability Pass (2026-05-17): strict role mapping. Anything that
    // is not unambiguously a real user message lands on 'system' so it can
    // never be reflected back to Python as input. Mapping table:
    //   user                      → 'user'
    //   assistant|syrix|ai → 'assistant'
    //   everything else           → 'system'
    const role = String((line && line.role) || '').toLowerCase();
    if (role === 'user') return 'user';
    if (role === 'assistant' || role === 'syrix' || role === 'ai') return 'assistant';
    return 'system';
  }

  function lineRequestId(line) {
    if (!line || typeof line !== 'object') return '';
    return String(line.request_id || line.hud_chat_request_id || line.chat_request_id || '').trim();
  }

  function lineKey(line) {
    const role = lineRole(line);
    const text = lineText(line).replace(/\s+/g, ' ').toLowerCase();
    const ts = lineTsSeconds(line);
    return role + '|' + text + '|' + (ts ? Math.round(ts * 1000) : 'no-ts');
  }

  function sameChatLine(a, b) {
    const role = lineRole(a);
    if (!role || role !== lineRole(b)) return false;
    const arid = lineRequestId(a);
    const brid = lineRequestId(b);
    if ((arid || brid) && arid !== brid) return false;
    const at = lineText(a).replace(/\s+/g, ' ').trim().toLowerCase();
    const bt = lineText(b).replace(/\s+/g, ' ').trim().toLowerCase();
    if (!at || at !== bt) return false;
    const ats = lineTsSeconds(a);
    const bts = lineTsSeconds(b);
    if (!ats || !bts) return false; // unknown timestamp — never false-dedup
    const win = role === 'user' ? 15 : 60;
    return Math.abs(ats - bts) <= win;
  }

  function registerCancelledTurn(p) {
    if (!p) return;
    const id = String(p.id || '').trim();
    if (!id) return;
    pruneCancelledTurns();
    _chatCancelledTurns.delete(id);
    _chatCancelledTurns.set(id, {
      id,
      sentTs: p.sentTs,
      lineCount: p.lineCount,
      cancelledAtMs: Date.now(),
    });
    while (_chatCancelledTurns.size > _CHAT_CANCEL_MAX) {
      const oldest = _chatCancelledTurns.keys().next().value;
      if (!oldest) break;
      _chatCancelledTurns.delete(oldest);
    }
  }

  function pruneCancelledTurns(nowMs) {
    const cutoff = Number(nowMs || Date.now()) - _CHAT_CANCEL_TTL_MS;
    for (const [id, turn] of _chatCancelledTurns.entries()) {
      if (Number(turn && turn.cancelledAtMs || 0) >= cutoff) break;
      _chatCancelledTurns.delete(id);
    }
  }

  function isLineFromCancelledTurn(line) {
    if (!line || lineRole(line) !== 'assistant' || !_chatCancelledTurns.size) return false;
    pruneCancelledTurns();
    const rid = lineRequestId(line);
    const ts = lineTsSeconds(line);
    if (rid) return _chatCancelledTurns.has(rid);
    for (const g of Array.from(_chatCancelledTurns.values()).reverse()) {
      // A stamped reply has an immutable owner. Match the opaque request id
      // directly instead of using a timestamp window: the previous 12-second
      // heuristic could suppress a perfectly valid answer for the NEW turn,
      // while an old research result arriving after 12 seconds escaped it.
      // Rid-less replies are a degraded legacy path. Keep the conservative
      // time fallback only for those lines because there is no stronger
      // identity available.
      if (!ts) continue;
      if (!g.sentTs) continue;
      // suppress replies within 12 s after the cancel — anything later is a fresh turn
      if (ts >= g.sentTs - 0.5 && ts <= g.sentTs + 12) return true;
    }
    return false;
  }

  function clearPendingChatTimers(pending) {
    if (!pending) return;
    if (pending.timeout12) {
      try { clearTimeout(pending.timeout12); } catch (_) {}
      pending.timeout12 = null;
    }
    if (pending.timeoutOffline) {
      try { clearTimeout(pending.timeoutOffline); } catch (_) {}
      pending.timeoutOffline = null;
    }
    if (pending.timeoutHard) {
      try { clearTimeout(pending.timeoutHard); } catch (_) {}
      pending.timeoutHard = null;
    }
  }

  let _lastLoggedTtsActive = null;

  function _logTtsStateIfChanged(state) {
    const active = String(state || '').toUpperCase() === 'SPEAKING';
    if (_lastLoggedTtsActive === active) return;
    _lastLoggedTtsActive = active;
    try { console.info('[TTS_STATE] active=' + active); } catch (_) {}
  }

  function _chatTerminalState(reason) {
    const r = String(reason || '').toLowerCase();
    if (r.includes('cancel')) return 'cancelled';
    if (r.includes('timeout')) return 'timeout';
    if (r.includes('offline') || r.includes('failed') || r.includes('error')) return 'failed';
    return 'completed';
  }

  function _chatLifecycleLog(event, detail) {
    const d = (detail && typeof detail === 'object') ? detail : {};
    const rid = String(d.request_id || d.requestId || '');
    const reason = String(d.reason || '');
    try {
      let line = '[CHAT_LIFECYCLE] ' + String(event || 'event') + ' request_id=' + (rid || 'none');
      if (reason) line += ' reason=' + reason.replace(/\s+/g, '_').slice(0, 80);
      console.info(line);
    } catch (_) {}
    try {
      window.dispatchEvent(new CustomEvent('syrix-chat-lifecycle', {
        detail: Object.assign({ event: String(event || 'event'), request_id: rid }, d)
      }));
    } catch (_) {}
  }

  function isVoiceSpeaking() {
    return String(snap.state || '').toUpperCase() === 'SPEAKING';
  }

  function cancelSpeech() {
    const ttsOk = send('cancel_speech');
    try { console.info('[TTS_CANCEL] stopped=' + !!ttsOk); } catch (_) {}
    return !!ttsOk;
  }

  function cancelPendingChat(requestId) {
    const rid = requestId != null && requestId !== '' ? String(requestId) : (_pendingChat && String(_pendingChat.id));
    const p = _pendingChat;
    if (!p || !rid || String(p.id) !== rid || isChatRequestComplete(p)) return false;
    clearPendingChatTimers(p);
    registerCancelledTurn(p);
    settlePendingChat(p, '', 'cancelled');
    try { console.info('[CHAT_CANCEL] request_id=' + p.id + ' source=hud'); } catch (_) {}
    const regOk = send('cancel_hud_chat', { data: { hud_chat_request_id: String(p.id) } });
    cancelSpeech();
    try {
      console.info('[BACKEND_CANCEL] request_id=' + p.id + ' registered=' + !!regOk);
    } catch (_) {}
    return true;
  }

  /** Stop in-flight model reply and/or TTS without treating cancel as user text. */
  function cancelActiveTurn() {
    const modelPending = isChatPending();
    const phase = modelPending ? 'model' : 'tts';
    try { console.info('[CHAT_STOP] source=hud phase=' + phase); } catch (_) {}
    if (modelPending) {
      cancelPendingChat();
    } else {
      cancelSpeech();
    }
    return true;
  }

  function isChatPending() {
    return !!(_pendingChat && !isChatRequestComplete(_pendingChat));
  }

  function isTurnActive() {
    return isChatPending() || isVoiceSpeaking();
  }

  function isProgressAssistantMessage(lineOrText, meta) {
    const m = meta && typeof meta === 'object' ? meta : (lineOrText && typeof lineOrText === 'object' ? lineOrText : null);
    const kind = m && String(m.kind || m.message_kind || m.line_kind || m.chat_kind || '').toLowerCase();
    if (kind && /progress|status|delay|filler|ack/.test(kind)) return true;
    const text = typeof lineOrText === 'string' ? lineOrText : lineText(lineOrText);
    const norm = String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
    if (!norm) return false;
    if (norm.length > 220) return false;
    if (/\b(?:working on it|getting things ready|still working|one sec|1 sec|hold on a moment|hold on|on it,? boss|checking that now|pulling live data|getting ready)\b/.test(norm)) {
      return true;
    }
    if (/\b(?:bit heavier|heavier)\b/.test(norm) && norm.length < 180) return true;
    if (/^one sec\b/.test(norm) && norm.length < 100) return true;
    return false;
  }

  function isExplicitInterimAssistantMessage(line) {
    if (!line || typeof line !== 'object') return false;
    const kind = String(line.kind || line.message_kind || line.line_kind || line.chat_kind || '').trim().toLowerCase();
    return kind === 'interim' || kind === 'partial';
  }

  const _TECHNICAL_CHAT_SPAM_RE = /\b(?:BRAIN:\s*(?:PUBLIC SOURCES|LOCAL_ACTION_LAYER)|LIMITEDACCESS|freshness\s+unknown|confidence\s+zero|fetched[_\s-]?at|browserfallbackfailed|nosearchresults|Source errors|Traceback \(most recent call last\)|stack trace|provider debug|raw timeout|SYRIX timed out before a reply reached the HUD)\b/i;
  function isTechnicalChatSpam(lineOrText) {
    const text = typeof lineOrText === 'string' ? lineOrText : lineText(lineOrText);
    return _TECHNICAL_CHAT_SPAM_RE.test(String(text || ''));
  }

  // ATLAS (2026-07-03) — deep payloads by rid (last 40): a `chat_deep` frame
  // can arrive before or after its chat line on either transport; this ring
  // lets mergeChatLines hydrate late-arriving lines.
  const _deepByRid = new Map();

  function mergeChatLines(lines) {
    if (!Array.isArray(lines) || !lines.length) return false;
    let changed = false;
    lines.forEach((incoming) => {
      let line = incoming;
      if (!line || typeof line !== 'object') return;
      if (!lineText(line)) return;
      // Hydrate the deep layer from the ring when the line doesn't carry it.
      const deepRid = lineRequestId(line);
      if (deepRid && lineRole(line) === 'assistant' && !line.deep && _deepByRid.has(deepRid)) {
        line = { ...line, deep: _deepByRid.get(deepRid) };
      }
      if (lineRole(line) === 'assistant' && isTechnicalChatSpam(line)) {
        notifyTechnicalIssue('chat_technical_suppressed', "That didn't go through.", lineText(line), {
          request_id: lineRequestId(line),
        });
        try { console.warn('[CHAT_FILTER] technical_assistant_line_suppressed=true'); } catch (_) {}
        return;
      }
      // Progress/filler messages ("one sec", "working on it") must NOT land in
      // _chatLines. They are filtered by React anyway, but crucially they MUST
      // NOT consume a request_id slot — if they do, the real reply (same rid)
      // is rejected by the dedup below and the answer never appears in chat.
      if (lineRole(line) === 'assistant' && isProgressAssistantMessage(line)) {
        try { console.info('[CHAT_FILTER] progress_filler_skipped rid=' + (lineRequestId(line) || 'none')); } catch (_) {}
        return;
      }
      if (isLineFromCancelledTurn(line)) {
        try { console.info('[CHAT_CANCEL] late_reply_suppressed=true'); } catch (_) {}
        return;
      }
      const rid = lineRequestId(line);
      if (rid && lineRole(line) === 'assistant') {
        const incomingTs = lineTsSeconds(line);
        // Anchor dedupe to the newest matching USER turn. Older HUD builds
        // restarted numeric request ids at 1, sometimes several times within
        // a few minutes during an app restart; a time window alone therefore
        // still moved a new answer into an older turn. An interim and its final
        // answer are duplicates only when both follow the same user anchor.
        let userTurnTs = 0;
        for (let i = _chatLines.length - 1; i >= 0; i--) {
          const candidate = _chatLines[i];
          const candidateTs = lineTsSeconds(candidate);
          if (
            lineRole(candidate) === 'user'
            && lineRequestId(candidate) === rid
            && (!incomingTs || !candidateTs || candidateTs <= incomingTs + 0.5)
          ) {
            userTurnTs = candidateTs;
            break;
          }
        }
        if (!userTurnTs && _pendingChat && String(_pendingChat.id) === rid) {
          userTurnTs = Number(_pendingChat.sentTs || 0);
        }
        const existingIdx = _chatLines.findIndex((ex) => {
          if (lineRole(ex) !== 'assistant' || lineRequestId(ex) !== rid) return false;
          const existingTs = lineTsSeconds(ex);
          if (userTurnTs && existingTs) return existingTs >= userTurnTs - 0.5;
          if (!incomingTs || !existingTs) return true;
          return Math.abs(incomingTs - existingTs) <= 15 * 60;
        });
        if (existingIdx >= 0) {
          // Same-rid assistant line already present. If the NEW line is a
          // meaningfully fuller answer (e.g. a short interim/placeholder landed
          // first during a long research turn), UPGRADE it in place instead of
          // dropping the real answer; otherwise it's a true duplicate → suppress.
          const exText = String(lineText(_chatLines[existingIdx]) || '');
          const newText = String(lineText(line) || '');
          if (
            isExplicitInterimAssistantMessage(_chatLines[existingIdx])
            && newText.length > exText.length + 12
            && !sameChatLine(_chatLines[existingIdx], line)
          ) {
            // Preserve a deep layer the stored line already carries.
            if (_chatLines[existingIdx].deep && !line.deep) {
              line = { ...line, deep: _chatLines[existingIdx].deep };
            }
            _chatLines[existingIdx] = line;
            changed = true;
            try { console.info('chat_line_upgraded_to_full_answer request_id=' + rid + ' source=bridge'); } catch (_) {}
          } else {
            // True duplicate — but adopt a deep layer the stored line lacks
            // (file-poll replays the entry WITH deep after the WS was down).
            if (line.deep && !_chatLines[existingIdx].deep) {
              _chatLines[existingIdx] = { ..._chatLines[existingIdx], deep: line.deep };
              snap.chat_lines = _chatLines.slice();
              changed = true;
              try {
                window.dispatchEvent(new CustomEvent('syrix-chat-deep', {
                  detail: { request_id: rid, deep: line.deep },
                }));
              } catch (_) {}
            }
            try { console.info('duplicate_response_suppressed request_id=' + rid + ' source=bridge'); } catch (_) {}
          }
          return;
        }
      }
      if (_chatLines.some((existing) => sameChatLine(existing, line))) return;
      _chatLines.push(line);
      changed = true;
    });
    if (_chatLines.length > 160) _chatLines = _chatLines.slice(-160);
    snap.chat_lines = _chatLines.slice();
    const latest = _chatLines[_chatLines.length - 1];
    if (latest) snap.last_chat = latest;
    return changed;
  }

  function clearChatSnapshot(reason) {
    _chatResetTs = Date.now() / 1000;
    _chatLines = [];
    snap.chat_lines = [];
    snap.last_chat = null;
    _streamBuffers.clear();
    try {
      if (window.__syrixStreamedRidSeen && typeof window.__syrixStreamedRidSeen.clear === 'function') {
        window.__syrixStreamedRidSeen.clear();
      }
    } catch (_) {}
    if (_pendingChat && !isChatRequestComplete(_pendingChat)) {
      settlePendingChat(_pendingChat, '', 'session_deleted');
    }
    publish();
    try {
      window.dispatchEvent(new CustomEvent('syrix-chat-cleared', {
        detail: { reason: String(reason || 'session_deleted'), ts: _chatResetTs },
      }));
    } catch (_) {}
  }

  function rememberChatRequestComplete(id, reason) {
    if (!id) return;
    _completedChatRequests.set(id, { ts: Date.now(), reason: String(reason || '') });
    if (_completedChatRequests.size > 40) {
      const extra = _completedChatRequests.size - 40;
      const keys = Array.from(_completedChatRequests.keys()).slice(0, extra);
      keys.forEach((k) => _completedChatRequests.delete(k));
    }
  }

  // The finished answer for one chat turn, and how the turn ended. A turn's
  // promise can settle with no text — the backend's "completed" status may
  // arrive before the answer line, and the 3 s check settles a turn whose
  // reply is already on screen — so a caller that needs the words (the voice
  // orb) reads them from the transcript instead.
  function assistantReplyText(requestId) {
    const rid = String(requestId || '');
    if (!rid) return '';
    for (let i = _chatLines.length - 1; i >= 0; i--) {
      const l = _chatLines[i];
      if (!l || typeof l !== 'object' || lineRole(l) !== 'assistant') continue;
      if (lineRequestId(l) !== rid || isProgressAssistantMessage(l)) continue;
      return lineText(l);
    }
    return '';
  }
  function chatTurnOutcome(requestId) {
    const entry = _completedChatRequests.get(String(requestId || ''));
    return entry ? entry.reason : '';
  }

  function isChatRequestComplete(pending) {
    return !pending || !!pending.completed || _completedChatRequests.has(pending.id);
  }

  function findAssistantReplyForPending(pending) {
    if (!pending) return null;
    if (_chatCancelledTurns.has(String(pending.id || ''))) return null;
    const startIdx = Math.max(0, Number(pending.lineCount || 0));
    for (let i = _chatLines.length - 1; i >= startIdx; i--) {
      const l = _chatLines[i];
      if (!l || typeof l !== 'object') continue;
      const role = lineRole(l);
      if (role !== 'assistant') continue;
      const rid = lineRequestId(l);
      if (!rid || String(rid) !== String(pending.id)) continue;
      if (isProgressAssistantMessage(l)) continue;
      return l;
    }
    return null;
  }

  function settlePendingChat(pending, text, reason) {
    if (!pending || isChatRequestComplete(pending)) return false;
    clearPendingChatTimers(pending);
    pending.completed = true;
    const terminalState = _chatTerminalState(reason || 'settled');
    rememberChatRequestComplete(pending.id, reason || terminalState);
    if (_pendingChat && _pendingChat.id === pending.id) _pendingChat = null;
    if (String(text || '').trim()) {
      _chatLifecycleLog('assistant_reply_received', {
        request_id: String(pending.id || ''),
        reason: String(reason || 'assistant_reply'),
      });
    }
    try {
      const latencyMs = Math.max(0, Math.round((Date.now() / 1000 - Number(pending.sentTs || 0)) * 1000));
      console.info('[HUD_DELIVERY] request_id=' + pending.id + ' reason=' + (reason || 'settled') + ' latency_ms=' + latencyMs);
    } catch (_) {}
    if (snap.chat_status && String(snap.chat_status.request_id || '') === String(pending.id)) {
      snap.chat_status = null;
      publish();
    }
    try { pending.resolve(String(text || '')); } catch (_) {}
    _chatLifecycleLog(
      terminalState === 'cancelled' ? 'request_cancelled' : 'request_' + terminalState,
      { request_id: String(pending.id || ''), reason: String(reason || terminalState) }
    );
    _chatLifecycleLog('thinking_cleared', {
      request_id: String(pending.id || ''),
      reason: String(reason || terminalState),
    });
    _traceAction('pending_cleared', {
      request_id: String(pending.id || ''),
      command: 'typed_text',
      reason: String(reason || 'settled'),
    });
    _traceAction('hud_updated', {
      request_id: String(pending.id || ''),
      command: 'typed_text',
      source: 'chat_settled',
      has_text: !!String(text || '').trim(),
    });
    return true;
  }

  // Auto-save hook: after each new non-trivial assistant message, save the
  // exchange to Obsidian. Runs only when there is a preceding user message.
  // Skips trivial acks, errors, jokes, and single-word responses.
  let _lastAutoSaveKey = '';
  const _AUTO_SAVE_SKIP_RE = /^(?:ok|yes|no|sure|thanks|hi|hello|hey|bye|goodbye|got it|understood|on it|standing by|working on it|one sec|done|sorry|noted)\b/i;
  const _AUTO_MEMORY_DURABLE_RE = /\b(?:remember|save\s+(?:this|that)|keep\s+(?:a\s+)?note|call\s+me|my\s+(?:name|preference|timezone|birthday|goal|project|job|role|city|home)\s+is|i\s+(?:prefer|like|dislike|hate|live|work|study|use|need|always|never)\b)/i;
  const _AUTO_MEMORY_SENSITIVE_RE = /\b(?:kill\s+myself|hurt\s+myself|self[-\s]?harm|password|passcode|api\s*key|secret|bank\s+account|credit\s+card|routing\s+number|social\s+security)\b/i;
  const _AUTO_MEMORY_FAILURE_RE = /^(?:i\s+can'?t\s+verify|i\s+couldn'?t\s+verify|no\s+reliable\s+information|the\s+brain\s+returned\s+an\s+empty|an\s+unexpected\s+error|backend\s+is\s+taking\s+too\s+long)/i;
  function _tryAutoSaveExchange() {
    try {
      const raw = snap.raw && typeof snap.raw === 'object' ? snap.raw : {};
      const hudSettings = raw.hud_settings && typeof raw.hud_settings === 'object' ? raw.hud_settings : {};
      const enabled = raw.syrix_auto_save_conversation_memory_enabled === true
        || hudSettings.auto_save_conversation_memory === true;
      if (!enabled) return;
      const allLines = _chatLines;
      if (allLines.length < 2) return;
      const lastAsst = [...allLines].reverse().find((l) => lineRole(l) === 'assistant');
      if (!lastAsst) return;
      const asstText = lineText(lastAsst);
      // Skip trivial / too-short / error responses.
      if (!asstText || asstText.length < 80) return;
      if (_AUTO_SAVE_SKIP_RE.test(asstText)) return;
      if (_AUTO_MEMORY_FAILURE_RE.test(asstText)) return;
      if (/^\[SYRIX\]|^\[STT\]|^\[TTS\]|^backend is taking too long/i.test(asstText)) return;
      const lastUser = [...allLines].reverse().find((l) => lineRole(l) === 'user');
      if (!lastUser) return;
      const userText = lineText(lastUser);
      if (!userText) return;
      const requestId = lineRequestId(lastAsst);
      if (/^(?:audit|test|probe|eval)[-_]/i.test(requestId)) return;
      if (/\b(?:synthetic|benchmark|evaluation|audit)\s+(?:test|prompt|turn)\b/i.test(userText)) return;
      // Automatic memory is for durable user facts/preferences, not a shadow
      // transcript of every joke, lookup, code question, failure or safety turn.
      if (!_AUTO_MEMORY_DURABLE_RE.test(userText)) return;
      if (_AUTO_MEMORY_SENSITIVE_RE.test(userText) || _AUTO_MEMORY_SENSITIVE_RE.test(asstText)) return;
      const asstTs = lineTsSeconds(lastAsst);
      const userTs = lineTsSeconds(lastUser);
      // Only save when user message preceded this assistant reply.
      if (asstTs && userTs && asstTs < userTs) return;
      const saveKey = String(userTs || '') + '|' + String(asstTs || '') + '|' + asstText.slice(0, 32);
      if (saveKey === _lastAutoSaveKey) return;
      _lastAutoSaveKey = saveKey;
      send('auto_save_memory', {
        data: {
          user_text: userText.slice(0, 400),
          assistant_text: asstText.slice(0, 1500),
          request_id: requestId,
          route: String(lastAsst.route_label || lastAsst.route || ''),
        },
      });
    } catch (_) {}
  }

  function applyChatLines(lines) {
    if (!Array.isArray(lines) || !lines.length) return;
    // A delayed WS frame or a poll of the old mailbox must not resurrect a
    // transcript after the active session was deleted.
    const usable = _chatResetTs
      ? lines.filter((line) => {
          const ts = lineTsSeconds(line);
          return ts > 0 && ts >= (_chatResetTs - 1.0);
        })
      : lines;
    if (!usable.length) return;
    const changed = mergeChatLines(usable);
    if (changed) publish();

    if (changed) _tryAutoSaveExchange();

    if (_pendingChat) {
      // Resolve the awaiting chat() promise with the first assistant reply
      // we see that arrived after the user sent the message.
      const aiLine = findAssistantReplyForPending(_pendingChat);
      if (aiLine) {
        if (isLineFromCancelledTurn(aiLine)) {
          try { console.info('[CHAT_CANCEL] late_reply_suppressed=true'); } catch (_) {}
        } else if (isProgressAssistantMessage(aiLine)) {
          try { console.info('[CHAT_PENDING] progress_line_ignored=true'); } catch (_) {}
        } else {
          const text = lineText(aiLine);
          const p = _pendingChat;
          settlePendingChat(p, text, 'assistant_reply');
        }
      }
    }
  }

  /* ── JSON polling (fallback / supplemental) ─────────────────────── */

  async function pollJson(url) {
    const t0 = Date.now();
    try {
      let parsed = null;
      // Phase 0: runtime mailboxes moved to %LOCALAPPDATA%\SYRIX\state — read
      // via main-process IPC. The relative fetch stays as a fallback for any
      // environment without the preload API (e.g. plain-browser debugging).
      const name = String(url || '').split('/').pop();
      if (window.syrixHud && typeof window.syrixHud.readRuntimeJson === 'function') {
        const res = await window.syrixHud.readRuntimeJson(name);
        if (!res || !res.ok) return null;
        parsed = res.data;
      } else {
        const resp = await fetch(url, { cache: 'no-cache' });
        if (!resp.ok) return null;
        const txt = await resp.text();
        if (!txt) return null;
        parsed = JSON.parse(txt);
      }
      const ms = Date.now() - t0;
      if (ms > 200) {
        try { console.info('[HUD_LATENCY] subsystem=bridge event=poll_json_slow status=ok latency_ms=' + ms + ' url=' + String(url || '')); } catch (_) {}
      }
      return parsed;
    } catch (_) {
      const ms = Date.now() - t0;
      try { console.info('[HUD_LATENCY] subsystem=bridge event=poll_json status=failed latency_ms=' + ms + ' url=' + String(url || '')); } catch (_) {}
      return null;
    }
  }

  async function tickState() {
    const now = Date.now();
    const wsFresh = snap.status === 'online' && (now - _lastWsStateAt) < _WS_FRESH_MS;
    if (now - _lastStatePoll < _stateThrottleMs) return;
    // Skip duplicate full-state JSON poll while WebSocket is actively pushing state.
    if (wsFresh) return;
    _lastStatePoll = now;
    const data = await pollJson(STATE_URL);
    if (data) applyState(data);
  }

  let _hudAppReqInFlight = false;
  async function tickHudAppRequest() {
    if (_hudAppReqInFlight) return;
    const now = Date.now();
    if (now - _lastHudAppReqPoll < _HUD_APP_REQ_POLL_MS) return;
    _lastHudAppReqPoll = now;
    _hudAppReqInFlight = true;
    try {
      const data = await pollJson(STATE_URL);
      if (data) _maybeRunHudAppRequest(data);
    } finally {
      _hudAppReqInFlight = false;
    }
  }

  async function tickBrain() {
    const now = Date.now();
    if (now - _lastBrainPoll < _brainThrottleMs) return;
    _lastBrainPoll = now;
    const data = await pollJson(BRAIN_URL);
    if (data) {
      snap.nvidia = data;
      if (data.active) snap.raw = Object.assign({}, snap.raw || {}, { __nvidia_active: data.active });
      snap.brain = pickBrainLabel(snap.raw || {}) || snap.brain;
      publish();
    }
  }

  async function tickChat() {
    if (!_pendingChat && snap.status === 'online') return;
    const now = Date.now();
    if (now - _lastChatPollTs < _chatPollMs) return;
    _lastChatPollTs = now;
    const data = await pollJson(CHAT_URL);
    if (!data) return;
    let lines = [];
    if (Array.isArray(data)) lines = data;
    else if (Array.isArray(data.lines)) lines = data.lines;
    else if (Array.isArray(data.history)) lines = data.history;
    if (lines.length) applyChatLines(lines);
  }

  async function tickFreeWeb() {
    const data = await pollJson(FREEWEB_URL);
    if (data) {
      snap.free_web = data;
      publish();
    }
  }

  // Brain test result — only changes when the user explicitly triggers a probe.
  // Slow tick to avoid wasted reads.
  let _lastBrainResultSeq = 0;
  async function tickBrainResult() {
    const data = await pollJson(BRAIN_RESULT_URL);
    if (!data || typeof data !== 'object') return;
    const seq = Number(data.seq || 0);
    if (!seq || seq <= _lastBrainResultSeq) return;
    _lastBrainResultSeq = seq;
    snap.last_brain_test = data;
    publish();
  }

  // Hand-control action mailbox. Written by syrix_host.py when Python's
  // voice pipeline hears "enable hand control" / "hologram mode" / "start air
  // drawing" / etc. The first read after boot records the baseline seq so
  // stale events from a previous host session do NOT auto-fire the HUD.
  let _lastHandCtlSeq = -1;
  async function tickHandControl() {
    const data = await pollJson(HAND_CTL_URL);
    if (!data || typeof data !== 'object') return;
    const seq = Number(data.seq || 0);
    if (!seq) return;
    if (_lastHandCtlSeq < 0) {
      // First poll after HUD boot — record baseline only, do NOT fire so
      // that stale events from a prior session don't auto-toggle the overlay.
      _lastHandCtlSeq = seq;
      return;
    }
    if (seq <= _lastHandCtlSeq) return;
    _lastHandCtlSeq = seq;
    snap.last_hand_control_event = data;
    publish();
  }

  /* ── Public command surface ────────────────────────────────────── */

  function _writeControl(obj) {
    // 2026-07-02 — real file fallback via preload IPC (hud:send-control):
    // the main process appends the command to the hud_control.json queue,
    // which the backend poller drains. Fire-and-forget; returns true when
    // the IPC surface exists (the write itself is async).
    try {
      const api = (typeof window !== 'undefined' && window.syrixHud && window.syrixHud.sendControl)
        ? window.syrixHud.sendControl : null;
      if (!api) return false;
      api(obj || {}).then((res) => {
        try {
          // eslint-disable-next-line no-console
          console.info('[HUD_EVENT] file_fallback command=' + String((obj && obj.command) || '')
            + ' ok=' + String(!!(res && res.ok)) + (res && res.reason ? ' reason=' + res.reason : ''));
        } catch (_) {}
      }).catch(() => {});
      return true;
    } catch (_) {
      return false;
    }
  }

  // Phase-2 Runtime-Repair (2026-05-16): rolling ring of the last commands the
  // HUD has SENT (regardless of whether they were ack'd by the backend). The
  // backend's _send_widget_ack already populates widget_acks_recent — pairing
  // the two surfaces the full "clicked → sent → backend received → result"
  // story for each command.
  const _CMD_RING_LIMIT = 10;
  const _commandRing = [];
  function _ringPush(entry) {
    try {
      _commandRing.push(entry);
      if (_commandRing.length > _CMD_RING_LIMIT) {
        _commandRing.splice(0, _commandRing.length - _CMD_RING_LIMIT);
      }
    } catch (_) {}
  }
  function recentCommands(limit) {
    const n = Math.max(1, Math.min(_CMD_RING_LIMIT, Number(limit || _CMD_RING_LIMIT)));
    return _commandRing.slice(-n);
  }

  function send(command, extra) {
    if (!command) return false;
    const payload = { type: 'command', command };
    const requestId = (extra && extra.data && (extra.data.hud_action_request_id || extra.data.request_id))
      ? String(extra.data.hud_action_request_id || extra.data.request_id)
      : _newActionId(command);
    if (extra && typeof extra === 'object') {
      if (typeof extra.text === 'string' && extra.text.length) payload.text = extra.text;
      if (extra.data && typeof extra.data === 'object') payload.data = extra.data;
    }
    if (!payload.data || typeof payload.data !== 'object') payload.data = {};
    payload.data.hud_action_request_id = requestId;
    payload.data.request_id = payload.data.request_id || requestId;
    _traceAction('action_requested', {
      request_id: requestId,
      command: String(command || ''),
      transport: 'websocket',
    });
    const ok = wsSend(payload);
    _traceAction('ipc_sent', {
      request_id: requestId,
      command: String(command || ''),
      transport: 'websocket',
      ok: !!ok,
    });
    // Phase-2B Pass-4: log every outbound command so the audit trail is
    // symmetrical (sent → backend ack). Failures surface immediately because
    // wsSend returns false when the socket is not OPEN.
    try {
      const tag = ok ? '%c[SYRIX →]' : '%c[SYRIX ↛]';
      const color = ok ? 'color:#22d3ee' : 'color:#f5c243';
      // eslint-disable-next-line no-console
      console.info(tag, color, command, extra && extra.data ? safeLogValue(extra.data) : '');
      // eslint-disable-next-line no-console
      console.info('[HUD_EVENT] ipc_sent command=' + String(command || '') + ' ok=' + String(!!ok));
    } catch (_) {}
    _ringPush({
      ts: Date.now(),
      command: String(command || ''),
      sent: !!ok,
      request_id: requestId,
      payload_keys: extra && extra.data ? Object.keys(extra.data).slice(0, 6) : [],
    });
    if (ok) {
      try {
        console.info('[HUD_LATENCY] subsystem=bridge event=action_send_start status=ok request_id=' + requestId + ' command=' + String(command || ''));
      } catch (_) {}
      const timer = setTimeout(() => {
        if (!_pendingActions.has(requestId)) return;
        _markActionFailed(requestId, command, 'WARNING', 'backend_ack_timeout');
        _clearPendingAction(requestId, 'backend_ack_timeout');
      }, 9000);
      _pendingActions.set(requestId, { command: String(command || ''), timer, ts: Date.now() });
    } else {
      // 2026-07-02 — WS down: fall back to the hud_control.json queue via
      // preload IPC instead of silently dropping the click. WS acks can't
      // arrive without the socket, so the pending-action entry is cleared
      // with an explicit fallback reason rather than a fake failure.
      const fileOk = _writeControl(payload);
      if (fileOk) {
        _traceAction('file_fallback_sent', {
          request_id: requestId,
          command: String(command || ''),
          transport: 'file',
        });
        _clearPendingAction(requestId, 'file_fallback');
        return true;
      }
      _markActionFailed(requestId, command, 'UNAVAILABLE', 'backend_websocket_not_open');
      _clearPendingAction(requestId, 'send_failed');
    }
    return ok;
  }

  function noteUserActivity(kind) {
    return send('hud_user_activity', {
      data: {
        kind: String(kind || 'typing'),
      },
    });
  }

  // Explicit "open in my normal Chrome / default browser" path. Bypasses the
  // isolated HUD profile only when the user asked for an external browser.
  function openInExternalBrowser(target, mode) {
    const t = String(target || '').trim();
    if (!t) return false;
    return send('open_app_in_hud', {
      data: {
        target: t,
        open_mode: 'external',
        mode: mode || 'normal',
      },
    });
  }

  // Explicit "open inside the HUD app dock" path. This matches the default
  // website rule while still letting callers be clear about intent.
  function openInsideHud(target, mode) {
    const t = String(target || '').trim();
    if (!t) return false;
    return send('open_app_in_hud', {
      data: {
        target: t,
        open_mode: 'hud',
        mode: mode || 'normal',
      },
    });
  }

  function setBrain(id) {
    return setBrainProvider(id);
  }

  function togglePrivacy()  { return send('toggle_screen_vision_privacy'); }
  function toggleWebcam()   { return send('toggle_webcam_presence'); }
  function tradingStart()   { return send('trading_analyst_start'); }
  function tradingStop()    { return send('trading_analyst_stop'); }

  // Phase-2A Pass 2: real backend exists at syrix_host.py:34591
  // ('set_music_provider') — wire the HUD dropdown so it actually changes
  // CONFIG['music_provider'] instead of being a decorative element.
  function setMusicProvider(value) {
    if (!value) return false;
    return send('set_music_provider', { data: { provider: String(value) } });
  }

  // Phase-2A Pass 2: voice memory bridge helpers — call the existing
  // typed_text path so the routing layer (handle_obsidian_memory_query)
  // picks them up. These keep all secret/PII filtering in Python.
  function obsidianStatus()  { return send('typed_text', { text: 'show memory web' }); }
  // Direct snapshot refresh — bypasses the routing/voice layer and pulls the
  // latest vault summary from syrix_host_modules.obsidian_memory via the
  // `get_obsidian_status` handler. Result lands on `snap.obsidian_status`.
  function obsidianRefresh() { return send('get_obsidian_status'); }
  function obsidianRemember(text) {
    const t = String(text || '').trim();
    return send('typed_text', { text: t ? ('remember this ' + t) : 'remember this' });
  }
  function obsidianLogBug(text) {
    const t = String(text || '').trim();
    return send('typed_text', { text: t ? ('log this bug ' + t) : 'log this bug' });
  }
  function obsidianLogFix(text) {
    const t = String(text || '').trim();
    return send('typed_text', { text: t ? ('log this fix ' + t) : 'log this fix' });
  }
  function getRecentMemories() { return send('get_recent_memories'); }
  // Real saved-memory facts (memory.json → conversation_notes) for the HUD
  // Memory widget — the notes "what do you remember" lists back. 2026-07-24.
  function memoryFactsList() { return send('memory_facts_list'); }
  function memoryFactAdd(text) { return send('memory_fact_add', { data: { text: String(text || '') } }); }
  function memoryFactDelete(text) { return send('memory_fact_delete', { data: { text: String(text || '') } }); }
  function memoryFactEdit(oldText, newText) { return send('memory_fact_edit', { data: { old: String(oldText || ''), new: String(newText || '') } }); }
  function memoryClearAll() { return send('memory_clear_all'); }
  // Forget one remembered note (HUD memory widget). Deletes it from the vault.
  function forgetMemory(filename, kind) {
    return send('forget_memory', { data: { filename: String(filename || ''), kind: String(kind || 'memory') } });
  }
  // Open the Obsidian app pointed at SYRIX's memory vault. Dedicated command so
  // it surfaces in the Dynamic Island (not the chat) and opens the exact vault.
  function openObsidian() { return send('open_obsidian'); }

  // Spotify process close — kills the Spotify app (not just the widget).
  function closeSpotify() {
    try {
      const api = window.syrixHud && window.syrixHud.spotify;
      if (api && api.close) return api.close();
    } catch (_) {}
    return Promise.resolve({ ok: false, reason: 'bridge_missing' });
  }

  /* ── Phase-2B Pass-3: Timer / Music / Weather / Map / Memory / Image-gen /
        Diagnostic — bridge helpers that forward to backend commands handled
        by syrix_host_hud_wiring.py:_dispatch. ──────────────────────────── */

  function setTimer(seconds, label) {
    const s = Math.max(1, Math.min(86400 * 30, Number(seconds) || 0));
    if (!s) return false;
    return send('set_timer', {
      data: { seconds: s, label: String(label || '').slice(0, 200) },
    });
  }
  function cancelTimers()  { return send('cancel_timers'); }
  function listTimers()    { return send('list_timers'); }
  function pauseTimer()    { return send('pause_timer'); }
  function resumeTimer()   { return send('resume_timer'); }

  function musicPlay(query) {
    const q = String(query || '').trim();
    return send('music_play', q ? { data: { query: q } } : undefined);
  }
  function musicPause()  { return send('music_pause'); }
  function musicResume() { return send('music_resume'); }
  function musicNext()   { return send('music_next'); }
  function musicPrev()   { return send('music_prev'); }
  function musicStop()   { return send('music_stop'); }
  function volumeAction(action) {
    const a = String(action || '').trim().toLowerCase();
    if (!['up', 'down', 'mute', 'unmute'].includes(a)) return false;
    return send('volume_action', { data: { action: a } });
  }
  // Absolute master-volume set (0-100) — backs the HUD Settings volume slider.
  function setVolume(percent) {
    let p = Number(percent);
    if (!Number.isFinite(p)) return false;
    p = Math.max(0, Math.min(100, Math.round(p)));
    return send('set_volume', { data: { percent: p } });
  }

  function weatherFetch(city) {
    const c = String(city || '').trim();
    return send('weather_fetch', { data: { city: c } });
  }

  function openMap(query) {
    const q = String(query || '').trim();
    return send('map_open_query', q ? { data: { query: q } } : undefined);
  }
  function openMapDirections(from, to) {
    const f = String(from || '').trim();
    const t = String(to || '').trim();
    if (!t) return false;
    return send('map_open_directions', { data: { origin: f, destination: t } });
  }

  function memoryWebRefresh() { return send('memory_web_refresh'); }
  function memoryWebSearch(query) {
    const q = String(query || '').trim();
    if (!q) return false;
    return send('memory_search', { data: { query: q, text: q } });
  }
  function memoryReviewApprove(id) {
    const itemId = String(id || '').trim();
    if (!itemId) return false;
    return send('memory_review_approve', { data: { item_id: itemId } });
  }
  function memoryReviewDeny(id) {
    const itemId = String(id || '').trim();
    if (!itemId) return false;
    return send('memory_review_deny', { data: { item_id: itemId } });
  }
  function memoryReviewEdit(id, text) {
    const itemId = String(id || '').trim();
    const body = String(text || '').trim();
    if (!itemId || !body) return false;
    return send('memory_review_edit', { data: { item_id: itemId, text: body } });
  }
  function memoryReviewDelete(id) {
    const itemId = String(id || '').trim();
    if (!itemId) return false;
    return send('memory_review_delete', { data: { item_id: itemId } });
  }
  function memoryCleanupJunk() { return send('memory_cleanup_junk'); }

  function imageGenerate(prompt) {
    const p = String(prompt || '').trim();
    if (!p) return false;
    return send('image_generate', { data: { prompt: p } });
  }

  function runDiagnostic() { return send('run_diagnostic'); }
  function mcpRefresh()    { return send('mcp_refresh'); }

  function setUserIdleTimeoutMinutes(minutes) {
    const n = Math.max(1, Math.min(240, Number(minutes) || 15));
    return send('set_config', { data: { key: 'syrix_user_idle_timeout_minutes', value: n } });
  }

  function screenVisionToggle() { return send('screen_vision_toggle'); }
  function screenVisionStop()   { return send('screen_vision_stop'); }

  function voiceCommand(text) {
    const t = String(text || '').trim();
    if (!t) return false;
    return send('typed_text', { text: t, data: { voice: true } });
  }

  /* Phase-2C: Blender bridge helpers ----------------------------------- */
  function blenderOpen()                  { return send('blender_open'); }
  function blenderCreate(subject) {
    const s = String(subject || '').trim();
    return send('blender_create', { data: { subject: s } });
  }
  function blenderRotate(axis, degrees) {
    return send('blender_rotate', { data: {
      axis: String(axis || 'Z'),
      degrees: Number(degrees != null ? degrees : 30),
    } });
  }
  function blenderWireframe(on) {
    return send('blender_wireframe', { data: { on: !!on } });
  }
  function blenderExplode(distance) {
    return send('blender_explode', { data: {
      distance: Number(distance != null ? distance : 1.5),
    } });
  }
  function blenderExport(fmt) {
    return send('blender_export', { data: { format: String(fmt || 'glb') } });
  }
  function blenderOpenViewer()            { return send('blender_open_viewer'); }

  /* Phase-2D: Vision workspace + SYRIX 3D sandbox ----------------------- */
  function visionWorkspaceSet(surface, action, mode) {
    const surf = String(surface || 'screen').toLowerCase();
    const act  = String(action  || 'open').toLowerCase();
    const md   = String(mode    || 'normal').toLowerCase();
    return send('vision_workspace_set', { data: {
      surface: surf, action: act, mode: md,
    } });
  }
  function sandboxCreate(subject) {
    const s = String(subject || '').toLowerCase().replace(/\s+/g, '_');
    if (!s) return false;
    return send('sandbox_create', { data: { subject: s } });
  }
  function sandboxAction(action, opts) {
    const a = String(action || '').toLowerCase();
    if (!a) return false;
    const o = opts && typeof opts === 'object' ? opts : {};
    const data = { action: a };
    if (o.direction) data.direction = String(o.direction).toLowerCase();
    if (o.value != null) data.value = Number(o.value);
    return send('sandbox_action', { data });
  }
  function sandboxClear() { return send('sandbox_clear'); }
  function sandboxExportBlender(subject) {
    return send('sandbox_export_blender', { data: { subject: String(subject || '') } });
  }

  /* Phase-2B Pass-4: lightweight ack subscriber for widgets that want toast
   * notifications. The CustomEvent 'syrix-widget-ack' fires every time the
   * bridge sees a new ack from the backend. Returns an unsubscribe fn. */
  function onAck(fn) {
    if (typeof fn !== 'function') return () => {};
    const handler = (ev) => { try { fn(ev.detail); } catch (_) {} };
    window.addEventListener('syrix-widget-ack', handler);
    return () => window.removeEventListener('syrix-widget-ack', handler);
  }

  /* HUD diagnostic ping. Lets the React audit panel verify the bridge is
   * actually round-tripping. Backend handles 'run_diagnostic' which writes a
   * syrix_last_diagnostic field AND a syrix_widget_ack — so if we see neither
   * within ~6s we know the bridge or wiring is broken. */
  function pingContract() {
    return send('run_diagnostic');
  }

  function chat(opts) {
    opts = opts || {};
    // Core Stability Pass: chat() is the *only* path the renderer should use
    // to put a user message on the wire. We accept either a plain string or
    // an options object — anything else is treated as "no message", which
    // resolves to an empty string instead of being dispatched. This is what
    // stops accidental assistant-text → user-input round-trips: callers
    // would have to actively pass the assistant text as `{text: ...}`, and
    // even then the role on the backend side stays 'user' (it is the user
    // typing the assistant's prior reply back into the box).
    let text = typeof opts === 'string' ? opts : (opts && typeof opts.text === 'string' ? opts.text : '');
    const images = Array.isArray(opts.images) ? opts.images : [];
    // 2026-05-30 — UX mode (Deep Research) for THIS turn.
    // We forward the mode two ways for robustness:
    //   1. Prepend a [[SYRIX_MODE:...]] marker to text so the Core V2 spine
    //      strips it and routes through the matching adapter even when the
    //      `data.mode` envelope doesn't survive an older Python receiver.
    //   2. Set data.mode / data.syrix_mode so newer wiring can read it
    //      directly without parsing text.
    const SUPPORTED_MODES = ['deep_research'];
    const modeRaw = typeof opts === 'object' && opts && typeof opts.mode === 'string' ? opts.mode.trim() : '';
    const mode = SUPPORTED_MODES.includes(modeRaw) ? modeRaw : '';
    if (mode) {
      text = '[[SYRIX_MODE:' + mode + ']] ' + text;
    }
    // ZENITH (2026-07-02) — sticky chat model for this turn. 'spark' is the
    // baseline and sends NOTHING (wire format byte-identical to pre-ZENITH,
    // which is the isolation guarantee); 'zenith' prepends its marker AFTER
    // the mode prepend so the model marker sits outermost — the spine strips
    // model first, then mode. The renderer toggle is only a request signal;
    // the backend-extracted marker is the source of truth.
    // AUTO (2026-07-07) — third switcher position. The marker is the request
    // signal; the spine resolves auto→spark/zenith server-side. 'spark' still
    // sends NOTHING (wire format unchanged — the isolation guarantee holds).
    // SUPERCOMPUTER (2026-07-24) — fourth value, sent only by the Supercomputer
    // workspace's own composer. It is never a persisted switcher position, so
    // no normal chat turn can carry it. 'spark' still sends NOTHING.
    const SUPPORTED_MODELS = ['spark', 'zenith', 'auto', 'supercomputer'];
    const modelRaw = typeof opts === 'object' && opts && typeof opts.model === 'string' ? opts.model.trim().toLowerCase() : '';
    const model = SUPPORTED_MODELS.includes(modelRaw) ? modelRaw : 'spark';
    if (model === 'zenith' || model === 'auto' || model === 'supercomputer') {
      text = '[[SYRIX_MODEL:' + model + ']] ' + text;
    }
    const imgKey = images.map((im) => [im && im.mime, String((im && im.b64) || '').slice(0, 48)].join(':')).join('|');
    // Document Intelligence: ids of already-parsed attachments to ground this turn on.
    const docIds = Array.isArray(opts.docIds) ? opts.docIds.map(String).filter(Boolean) : [];
    const docKey = docIds.join(',');
    const chatKey = (text.replace(/\s+/g, ' ').trim().toLowerCase() + '|' + imgKey + '|' + docKey).slice(0, 700);
    const nowMs = Date.now();
    if (
      chatKey
      && ((_pendingChat && _pendingChat.key === chatKey)
        || (_lastChatDispatch.key === chatKey && (nowMs - _lastChatDispatch.ts) < 1500))
    ) {
      return Promise.resolve('');
    }
    let createdRequestId = '';
    const promise = new Promise((resolve) => {
      if (!text && !images.length && !docIds.length) { resolve(''); return; }
      const sentTs = Date.now() / 1000;
      const previous = _pendingChat;
      const requestId = _chatRequestSession + '-' + (++_chatRequestSeq).toString(36);
      createdRequestId = String(requestId);
      const pending = {
        id: requestId,
        resolve,
        sentTs,
        voice: !!opts.voice,
        model,
        key: chatKey,
        lineCount: _chatLines.length,
        completed: false,
        slowNotified: false,
        timeout12: null,
        timeoutOffline: null,
        timeoutHard: null,
      };
      _pendingChat = pending;
      _lastChatDispatch = { key: chatKey, ts: nowMs };
      try { console.info('[CHAT_LATENCY] stage=request_start request_id=' + requestId); } catch (_) {}
      _chatLifecycleLog('request_started', { request_id: String(requestId), source: 'hud' });
      _chatLifecycleLog('thinking_shown', { request_id: String(requestId), source: 'hud' });
      snap.chat_status = { state: 'processing', request_id: String(requestId), model, ts: Date.now() / 1000 };
      publish();
      _traceAction('hud_updated', {
        request_id: String(requestId),
        command: 'typed_text',
        source: 'chat_ack',
        state: 'processing',
      });
      if (previous) {
        // A new user turn supersedes the renderer's one in-flight normal-chat
        // turn. Register + propagate cancellation BEFORE settling the local
        // promise so the old backend worker cannot append a late answer under
        // the new topic. The backend chokepoint also rejects a worker that
        // races with this message after the cancellation is registered.
        try { registerCancelledTurn(previous); } catch (_) {}
        try {
          send('cancel_hud_chat', {
            data: {
              hud_chat_request_id: String(previous.id || ''),
              reason: 'superseded_by_new_turn',
            },
          });
        } catch (_) {}
        settlePendingChat(previous, '', 'superseded');
      }
      // Try WS first.
      let extra = { text };
      extra.data = { hud_chat_request_id: String(requestId) };
      if (mode) {
        extra.data.syrix_mode = mode;
        extra.data.mode = mode;
      }
      if (model === 'zenith' || model === 'supercomputer') {
        extra.data.syrix_model = model;
      }
      if (images.length) {
        // Wrap as data.images for backend; the typed_text command will see it.
        extra.data.images = images.map((im) => ({
          b64: im.b64,
          mime: im.mime,
          url: im.url || null,
          frame_id: im.frame_id || im.frameId || null,
          captured_ts: im.captured_ts || im.capturedTs || null,
          captured_ts_ms: im.captured_ts_ms || im.capturedTsMs || null,
          width: im.width || null,
          height: im.height || null,
        }));
      }
      // Attachments to ground on — additive; wire format is byte-identical when
      // no docs are attached (isolation guarantee, like the zenith marker).
      if (docIds.length) {
        extra.data.doc_ids = docIds;
      }
      // A turn spoken to the voice orb: answered in this chat, written to be
      // heard. Absent on every typed turn, so their wire format is unchanged.
      if (opts.voiceReply) {
        extra.data.voice_reply = true;
      }
      const ok = send('typed_text', extra);
      if (!ok) {
        // Fallback: try to surface that backend is offline so the user knows
        // the message wasn't sent. Don't reject — let the chat poll resolve
        // if backend wakes up.
        pending.timeoutOffline = setTimeout(() => {
          const p = _pendingChat;
          if (p && p.id === requestId && !isChatRequestComplete(p)) {
            notifyTechnicalIssue('backend_offline', "That didn't go through.", 'Backend offline - message not delivered.', { request_id: String(requestId) });
            settlePendingChat(p, '', 'offline');
          }
        }, 4500);
      } else {
        // Slow-response guard: escalate to 'processing_slow' after 3 s so the
        // island / chat panel can show a visible "still working" indicator.
        // Keep the request alive — a real backend answer may still arrive.
        pending.timeout12 = setTimeout(() => {
          const p = _pendingChat;
          if (p && p.id === requestId && !isChatRequestComplete(p)) {
            let _lateReply = null;
            try { _lateReply = findAssistantReplyForPending(p); } catch (_) {}
            if (_lateReply) {
              settlePendingChat(p, '', 'late_reply');
            } else {
              p.slowNotified = true;
              const elapsedMs = Math.max(0, Math.round((Date.now() / 1000 - Number(p.sentTs || 0)) * 1000));
              snap.chat_status = { state: 'processing_slow', request_id: String(requestId), elapsed_ms: elapsedMs, ts: Date.now() / 1000 };
              publish();
              try { console.info('[CHAT_PENDING] still_processing=true request_id=' + requestId + ' elapsed_ms=' + elapsedMs); } catch (_) {}
            }
          }
        }, 3000);
        // ZENITH missions legitimately run past the standard 120s renderer
        // ceiling (110s backend wall + dispatch overhead) — give them 180s.
        // SUPERCOMPUTER turns are agentic (browse → read → act → verify) and
        // can use the backend's one-hour task budget. Keep the renderer alive
        // five minutes beyond that budget so it cannot cancel and suppress a
        // valid completion while the backend is still working. Stop remains
        // available throughout.
        const hardTimeoutMs = model === 'zenith'
          ? 180000
          : model === 'supercomputer' ? 3900000 : 120000;
        pending.timeoutHard = setTimeout(() => {
          // Use the closed-over `pending` directly — more reliable than the mutable
          // `_pendingChat` global, which may have moved to a new request by now.
          if (isChatRequestComplete(pending)) return;
          try { console.warn('[CHAT_PENDING] expired_silent=true request_id=' + requestId + ' timeout_ms=' + hardTimeoutMs); } catch (_) {}
          try { registerCancelledTurn(pending); } catch (_) {}
          try { send('cancel_hud_chat', { data: { hud_chat_request_id: String(requestId), reason: 'hud_hard_timeout' } }); } catch (_) {}
          notifyTechnicalIssue('chat_timeout', "That didn't go through.", 'HUD hard timeout before a reply reached the renderer.', { request_id: String(requestId), timeout_ms: hardTimeoutMs });
          settlePendingChat(pending, '', 'timeout');
        }, hardTimeoutMs);
      }
    });
    try { promise.requestId = createdRequestId; } catch (_) {}
    return promise;
  }

  /* ── SYRIX HUD App Dock helpers ─────────────────────────────────
   * Thin wrappers over window.syrixHud.hudApp.* that emit the same
   * [SYRIX →] / [SYRIX ✓] / [SYRIX ✗] ack pattern used by every other
   * bridge helper, so the audit trail in DevTools stays symmetrical.
   *
   * Each helper returns a Promise resolving to { ok, ... } so calling
   * code (voice command dispatch, widget controls, Python-side
   * routing) can chain follow-up state updates.
   */

  let _hudAppAckSeq = 0;
  function _hudAppAck(command, ok, detail, extra) {
    _hudAppAckSeq += 1;
    const ack = {
      seq: _hudAppAckSeq,
      command: String(command || ''),
      ok: !!ok,
      detail: String(detail || '').slice(0, 240),
      ts: Date.now() / 1000,
    };
    if (extra && typeof extra === 'object') {
      for (const k of ['id', 'mode', 'action', 'latency_ms', 'request_id', 'target_type', 'domain', 'reused', 'focused', 'new_tab', 'source']) {
        if (k in extra) ack[k] = extra[k];
      }
    }
    snap.syrix_hud_app_ack = ack;
    snap.syrix_latest_hud_notification = ack;
    recordHudNotification(ack, 'hud-app');
    try {
      const native = _hudAppNative();
      if (native && native.ack) native.ack(ack).catch(() => {});
    } catch (_) {}
    // Phase 2 — ack protocol v2: deliver the ack straight to the backend over
    // WS so wait_for_hud_app_ack resolves in milliseconds; the hud_state.json
    // merge above remains the fallback transport.
    try { send('hud_app_ack', { data: ack }); } catch (_) {}
    try { _hudAppPublishStateToBackend(); } catch (_) {}
    try {
      const tag = ok ? '%c[SYRIX ✓]' : '%c[SYRIX ✗]';
      const color = ok ? 'color:#16d37a' : 'color:#ff4f6e';
      // eslint-disable-next-line no-console
      console.info(tag, color, 'hud-app:' + ack.command, '·', ack.detail || '(no detail)');
    } catch (_) {}
    try { window.dispatchEvent(new CustomEvent('syrix-hud-app-ack', { detail: ack })); } catch (_) {}
    publish();
    return ack;
  }

  function _hudAppLog(command, extra) {
    try {
      // eslint-disable-next-line no-console
      console.info('%c[SYRIX →]', 'color:#22d3ee', 'hud-app:' + command, safeLogValue(extra || ''));
    } catch (_) {}
  }

  function _hudAppDomainFromUrl(rawUrl) {
    try {
      const u = new URL(String(rawUrl || ''));
      return String(u.hostname || '').slice(0, 160);
    } catch (_) {
      return '';
    }
  }

  function _hudAppOpenSource(raw, fallback) {
    const source = String(raw || fallback || '').trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_');
    if (['user_click', 'voice_command', 'route', 'restore', 'startup'].includes(source)) return source;
    return 'user_click';
  }

  function _hudAppContextFrom(activeApp, active) {
    if (!activeApp) return null;
    const nav = {
      can_go_back: !!activeApp.canGoBack,
      can_go_forward: !!activeApp.canGoForward,
      loading: !!activeApp.loading,
      error: String(activeApp.lastError || activeApp.error || ''),
    };
    return {
      active_app_id: active || activeApp.id || null,
      active_tab: active || activeApp.id || null,
      current_url: String(activeApp.url || ''),
      current_domain: String(activeApp.domain || ''),
      page_title: String(activeApp.title || ''),
      app_mode: String(activeApp.mode || 'normal'),
      active_widget_page_name: String(activeApp.pageName || activeApp.title || activeApp.domain || 'HUD app'),
      navigation: nav,
      latest_notification: snap.syrix_latest_hud_notification || null,
      ts: Date.now() / 1000,
    };
  }

  let _lastHudAppStateKey = '';
  let _lastHudAppStateSentAt = 0;
  function _hudAppPublishStateToBackend() {
    const ctx = snap.syrix_active_hud_context || null;
    const key = JSON.stringify({
      active: snap.syrix_active_hud_app || '',
      apps: (snap.syrix_hud_apps || []).map((a) => ({
        id: a && a.id,
        url: a && a.url,
        title: a && a.title,
        mode: a && a.mode,
        back: !!(a && a.canGoBack),
        fwd: !!(a && a.canGoForward),
        loading: !!(a && a.loading),
        err: a && (a.lastError || a.error || ''),
      })),
      ack: snap.syrix_latest_hud_notification && snap.syrix_latest_hud_notification.seq,
    });
    const now = Date.now();
    if (key === _lastHudAppStateKey && (now - _lastHudAppStateSentAt) < 1200) return;
    _lastHudAppStateKey = key;
    _lastHudAppStateSentAt = now;
    send('hud_app_state_update', {
      data: {
        apps: snap.syrix_hud_apps || [],
        active: snap.syrix_active_hud_app || null,
        active_context: ctx,
        latest_notification: snap.syrix_latest_hud_notification || null,
      },
    });
  }

  function _hudAppRefreshSnapshot(list, active, stage) {
    _hudAppNativeStateSeen = true;
    snap.syrix_hud_apps = Array.isArray(list) ? list : [];
    snap.syrix_active_hud_app = active || null;
    snap.syrix_hud_stage = (stage && typeof stage === 'object') ? stage : { enabled: false, chatInset: 0, navH: 68, frozen: false };
    try { if (window.SyrixStage && window.SyrixStage.setStageState) window.SyrixStage.setStageState(snap.syrix_hud_stage, snap.syrix_hud_apps.length); } catch (_) {}
    const activeApp = snap.syrix_hud_apps.find((a) => a && a.id === active) || null;
    snap.syrix_hud_app_mode = activeApp ? activeApp.mode : null;
    snap.syrix_hud_app_vision_active = !!(activeApp && activeApp.vision);
    snap.syrix_active_hud_context = _hudAppContextFrom(activeApp, active);
    _hudAppPublishStateToBackend();
    publish();
  }

  function _hudAppNative() {
    try {
      return (window.syrixHud && window.syrixHud.hudApp) || null;
    } catch (_) {
      return null;
    }
  }

  // Phase 5 — execute a sandbox primitive against the embedded BrowserView and
  // return the result to the backend over the WS, keyed by op_id.
  async function _runSandboxOp(req) {
    const opId = String((req && req.op_id) || '');
    const verb = String((req && req.verb) || '');
    const id = (req && req.tab_id) || '';
    let result = { ok: false, reason: 'unknown_verb' };
    try {
      const native = _hudAppNative();
      if (!native) {
        result = { ok: false, reason: 'hud_app_native_unavailable' };
      } else if (verb === 'read') {
        result = await native.domDigest(id, String(req.mode || 'digest'));
      } else if (verb === 'click') {
        result = await native.input(id, 'click', { element_id: String(req.element_id || '') });
      } else if (verb === 'type') {
        result = await native.input(id, 'type', { element_id: String(req.element_id || ''), text: String(req.text || ''), submit: !!req.submit });
      } else if (verb === 'scroll') {
        result = await native.input(id, 'scroll', { dy: Number(req.dy || 600) });
      } else if (verb === 'screenshot') {
        result = await native.capture(id);
      } else if (verb === 'open') {
        result = await native.open({ url: String(req.url || ''), source: 'voice_command' });
      } else if (verb === 'close') {
        result = await native.close(id);
      } else if (verb === 'tabs') {
        result = await native.list();
      }
    } catch (exc) {
      result = { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
    }
    try {
      send('sandbox_result', { data: Object.assign({ op_id: opId, verb }, result || {}) });
    } catch (_) {}
  }

  // Supercomputer browser relay (2026-07-24). Structurally separate from the
  // app-dock relay above: a different WS message type, a different preload
  // namespace, and a different Electron module, so a Supercomputer op can never
  // resolve to an app-dock view. Policy is enforced on the Python side before
  // the op arrives, and again in the Electron module.
  function _scNative() {
    try {
      return (window.syrixHud && window.syrixHud.superComputer) || null;
    } catch (_) {
      return null;
    }
  }

  async function _runScBrowserOp(req) {
    const opId = String((req && req.op_id) || '');
    const verb = String((req && req.verb) || '');
    const id = (req && req.tab_id) || '';
    let result = { ok: false, reason: 'unknown_verb' };
    try {
      const native = _scNative();
      if (!native) {
        result = { ok: false, reason: 'sc_browser_native_unavailable' };
      } else {
        // Bootstrap the module on first use; main.js keeps it unloaded until
        // this fires, so a session that never enters the mode never loads it.
        await native.ensure();
        if (verb === 'open') {
          result = await native.open({ url: String(req.url || ''), id, new_tab: !!req.new_tab });
        } else if (verb === 'tabs') {
          result = await native.tabs();
        } else if (verb === 'new_tab') {
          result = await native.newTab();
        } else if (verb === 'focus') {
          result = await native.focus(id);
        } else if (verb === 'nav') {
          result = await native.nav(id, String(req.action || ''), String(req.url || ''));
        } else if (verb === 'annotate') {
          result = await native.annotate(id);
        } else if (verb === 'read') {
          result = await native.read(id, String(req.mode || 'digest'));
        } else if (verb === 'click') {
          result = await native.input(id, 'click', { element_id: String(req.element_id || '') });
        } else if (verb === 'type') {
          result = await native.input(id, 'type', {
            element_id: String(req.element_id || ''),
            text: String(req.text || ''),
            submit: !!req.submit,
          });
        } else if (verb === 'scroll') {
          result = await native.input(id, 'scroll', { dy: Number(req.dy || 600) });
        } else if (verb === 'back') {
          result = await native.input(id, 'back', {});
        } else if (verb === 'forward') {
          result = await native.input(id, 'forward', {});
        } else if (verb === 'reload') {
          result = await native.input(id, 'reload', {});
        } else if (verb === 'press_key') {
          result = await native.input(id, 'press_key', { keys: String(req.keys || '') });
        } else if (verb === 'hover') {
          result = await native.input(id, 'hover', { element_id: String(req.element_id || '') });
        } else if (verb === 'select_option') {
          result = await native.input(id, 'select_option', {
            element_id: String(req.element_id || ''),
            value: String(req.value || ''),
          });
        } else if (verb === 'drag') {
          result = await native.input(id, 'drag', {
            from_element: String(req.from_element || ''),
            to_element: String(req.to_element || ''),
            from_xy: req.from_xy || {},
            to_xy: req.to_xy || {},
          });
        } else if (verb === 'wait_for') {
          result = await native.input(id, 'wait_for', {
            text: String(req.text || ''),
            element_id: String(req.element_id || ''),
            wait_s: Number(req.wait_s || 15),
          });
        } else if (verb === 'attach_file') {
          result = await native.input(id, 'attach_file', {
            element_id: String(req.element_id || ''),
            path: String(req.path || ''),
          });
        } else if (verb === 'screenshot') {
          result = await native.capture(id);
        } else if (verb === 'close') {
          result = await native.close(id);
        } else if (verb === 'shutdown') {
          result = await native.dispose();
        } else if (verb === 'set_bounds') {
          result = await native.setBounds(req.bounds || {});
        } else if (verb === 'print_pdf') {
          result = await native.printPdf(id, {
            page_size: String(req.page_size || 'A4'),
            landscape: !!req.landscape,
          });
        } else if (verb === 'preview_origin') {
          result = await native.previewOrigin(String(req.origin || ''));
        } else if (verb === 'arm_download') {
          result = await native.armDownload(String(req.folder || ''), Number(req.ttl_s || 60));
        } else if (verb === 'download_status') {
          result = await native.downloadStatus();
        }
      }
    } catch (exc) {
      result = { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
    }
    try {
      send('sandbox_result', { data: Object.assign({ op_id: opId, verb }, result || {}) });
    } catch (_) {}
  }

  async function _hudAppCall(command, fn, extra) {
    const t0 = performance.now();
    const requestId = String((extra && (extra.request_id || extra.hud_action_request_id)) || _newActionId('hud_app_' + command));
    const traceExtra = Object.assign({}, extra || {}, { request_id: requestId, command: 'hud-app:' + command });
    _traceAction('action_requested', traceExtra);
    const native = _hudAppNative();
    if (!native) {
      _traceAction('ipc_sent', Object.assign({}, traceExtra, { ok: false, transport: 'electron_ipc' }));
      _markActionFailed(requestId, 'hud-app:' + command, 'UNAVAILABLE', 'electron_bridge_missing');
      _clearPendingAction(requestId, 'electron_bridge_missing');
      _hudAppAck(command, false, 'electron_bridge_missing', Object.assign({}, traceExtra, { latency_ms: Math.round(performance.now() - t0) }));
      return { ok: false, reason: 'electron_bridge_missing' };
    }
    _hudAppLog(command, extra);
    _traceAction('ipc_sent', Object.assign({}, traceExtra, { ok: true, transport: 'electron_ipc' }));
    try {
      const res = await fn(native);
      const latency = Math.round(performance.now() - t0);
      try { console.info('[HUD_CMD] command=' + command + ' ok=' + !!(res && res.ok) + ' latency_ms=' + latency); } catch (_) {}
      if (res && res.ok) {
        _hudAppAck(command, true, res.detail || (res.id ? 'id=' + res.id : 'done'),
                   Object.assign({}, traceExtra, res, { latency_ms: latency }));
        _traceAction('hud_updated', Object.assign({}, traceExtra, { ok: true, latency_ms: latency }));
        _clearPendingAction(requestId, 'hud_app_ok');
        return res;
      }
      _hudAppAck(command, false, (res && (res.reason || res.detail)) || 'failed',
                 Object.assign({}, traceExtra, { latency_ms: latency }));
      _markActionFailed(requestId, 'hud-app:' + command, 'ERROR', (res && (res.reason || res.detail)) || 'failed');
      _clearPendingAction(requestId, 'hud_app_error');
      return res || { ok: false, reason: 'unknown' };
    } catch (exc) {
      const latency = Math.round(performance.now() - t0);
      const msg = String((exc && exc.message) || exc).slice(0, 200);
      try { console.info('[HUD_CMD] command=' + command + ' ok=false latency_ms=' + latency + ' reason=exception'); } catch (_) {}
      _hudAppAck(command, false, msg, Object.assign({}, traceExtra, { latency_ms: latency }));
      _markActionFailed(requestId, 'hud-app:' + command, 'ERROR', msg || 'exception');
      _clearPendingAction(requestId, 'hud_app_exception');
      return { ok: false, reason: msg };
    }
  }

  // App resolver — friendly name -> safe URL. Always prefer dark/HUD-friendly
  // routes when the site supports them. Unknown inputs fall through to a
  // sanitized URL guess; everything ultimately funnels through main process
  // _hudAppUrlAllowed for the real safety check.
  const _APP_RESOLVER = [
    [/^(?:youtube|yt|y\s*t)$/i, 'https://www.youtube.com/?theme=dark'],
    [/^(?:google\s*maps|maps|map)$/i, 'https://www.google.com/maps'],
    [/^(?:tradingview|trading\s*view|tv|charts?)$/i, 'https://www.tradingview.com/chart/'],
    [/^(?:news|world\s*news|world\s*pulse)$/i, 'https://news.google.com/?hl=en-US&gl=US&ceid=US:en'],
    [/^(?:spotify|spotify\s*web|music\s*web)$/i, 'https://open.spotify.com/'],
    [/^(?:github|gh)$/i, 'https://github.com/'],
    [/^(?:gmail|mail|email|inbox)$/i, 'https://mail.google.com/'],
    [/^(?:calendar|gcal)$/i, 'https://calendar.google.com/'],
    [/^(?:drive|gdrive|google\s*drive)$/i, 'https://drive.google.com/'],
    [/^(?:chatgpt|openai|chat\s*gpt)$/i, 'https://chatgpt.com/'],
    [/^(?:claude(?:\.ai)?)$/i, 'https://claude.ai/'],
    [/^(?:wikipedia|wiki)$/i, 'https://en.wikipedia.org/'],
    [/^(?:weather)$/i, 'https://www.google.com/search?q=weather'],
    [/^(?:reddit)$/i, 'https://www.reddit.com/'],
    [/^(?:twitter|x|x\.com)$/i, 'https://x.com/'],
  ];

  function resolveHudAppUrl(targetRaw) {
    const t = String(targetRaw || '').trim();
    if (!t) return '';
    // Already a URL?
    if (/^https?:\/\//i.test(t)) return t;
    // Try the resolver table.
    for (const [pat, url] of _APP_RESOLVER) {
      if (pat.test(t)) return url;
    }
    // Looks like a bare domain ("nytimes.com", "github.io/foo")
    if (/^[a-z0-9.-]+\.[a-z]{2,}(?:\/.*)?$/i.test(t)) return 'https://' + t;
    // Otherwise: treat as a Google search keyword (last resort, behind safe URL guard).
    return 'https://www.google.com/search?q=' + encodeURIComponent(t);
  }

  function openAppInHud(target, opts) {
    const requestedUrl = opts && opts.url ? String(opts.url || '').trim() : '';
    const url = resolveHudAppUrl(requestedUrl || target);
    const requestId = String((opts && (opts.request_id || opts.requestId)) || '');
    const source = _hudAppOpenSource(opts && (opts.source || opts.open_source || opts.reason), requestId ? 'voice_command' : 'user_click');
    if (!url) {
      _hudAppAck('open', false, 'no_target', { request_id: requestId, target_type: opts && opts.target_type, source });
      return Promise.resolve({ ok: false, reason: 'no_target' });
    }
    const mode = (opts && opts.mode) || 'normal';
    const bounds = (opts && opts.bounds) || null;
    const newTab = !!(opts && (opts.new_tab || opts.newTab));
    // Stage preference: explicit opt wins, else the SyrixStage localStorage
    // pref (default ON — browser opens as a full-window backdrop).
    const stage = (opts && typeof opts.stage === 'boolean')
      ? opts.stage
      : (window.SyrixStage && typeof window.SyrixStage.prefOn === 'function' ? !!window.SyrixStage.prefOn() : true);
    return _hudAppCall('open', (n) => n.open({ url, mode, bounds, source, newTab, stage }), {
      target,
      url,
      mode,
      new_tab: newTab,
      source,
      request_id: requestId,
      target_type: (opts && opts.target_type) || (requestedUrl ? 'url' : 'app'),
      domain: (opts && opts.domain) || _hudAppDomainFromUrl(url),
    });
  }

  function navigateHudApp(id, rawUrl) {
    const url = resolveHudAppUrl(String(rawUrl || '').trim());
    if (!url) return Promise.resolve({ ok: false, reason: 'no_url' });
    return _hudAppCall('navigate', (n) => n.nav(id, 'url', url), { id, action: 'url', url });
  }
  function closeHudApp(id)        { return _hudAppCall('close',      (n) => n.close(id),              { id }); }
  function focusHudApp(id)        { return _hudAppCall('focus',      (n) => n.focus(id),              { id }); }
  function fullscreenHudApp(id)   { return _hudAppCall('fullscreen', (n) => n.mode(id, 'fullscreen'), { id, mode: 'fullscreen' }); }
  function minimizeHudApp(id)     { return _hudAppCall('minimize',   (n) => n.mode(id, 'minimized'),  { id, mode: 'minimized' }); }
  function restoreHudApp(id)      { return _hudAppCall('restore',    (n) => n.mode(id, 'normal'),     { id, mode: 'normal' }); }
  function resizeHudApp(id, bounds) {
    return _hudAppCall('resize', (n) => n.mode(id, 'custom', bounds || {}), { id, mode: 'custom', bounds });
  }
  function refreshHudApp(id)      { return _hudAppCall('refresh',    (n) => n.nav(id, 'refresh'),     { id, action: 'refresh' }); }
  function hudAppBack(id)         { return _hudAppCall('back',       (n) => n.nav(id, 'back'),        { id, action: 'back' }); }
  function hudAppForward(id)      { return _hudAppCall('forward',    (n) => n.nav(id, 'forward'),     { id, action: 'forward' }); }
  function popoutHudApp(id)       { return _hudAppCall('popout',     (n) => n.popout(id),             { id }); }
  function setHudAppVision(id, enabled) {
    return _hudAppCall('vision', (n) => n.vision(id, !!enabled), { id, enabled: !!enabled });
  }
  // Stage mode — browser as full-window backdrop.
  function setHudStage(enabled, chatInset) {
    const opts = { enabled: !!enabled };
    if (chatInset !== undefined) opts.chatInset = Number(chatInset) || 0;
    return _hudAppCall('stage', (n) => n.stage(opts), { enabled: !!enabled, chat_inset: opts.chatInset });
  }
  function setHudStageChatInset(px) {
    return _hudAppCall('stage', (n) => n.stage({ chatInset: Number(px) || 0 }), { chat_inset: Number(px) || 0 });
  }

  function closeAllHudApps() {
    const list = (snap.syrix_hud_apps || []).slice();
    return Promise.all(list.map((a) => a && a.id ? closeHudApp(a.id) : null));
  }

  // Ack-back to main.js telling it the per-app React chrome has rendered.
  // Without this, the main-side watchdog keeps the HUD controls in a
  // non-blocking retry/status state.
  function ackHudAppChrome(id) {
    try {
      const native = _hudAppNative();
      if (native && typeof native.chromeAck === 'function') {
        return native.chromeAck(String(id || ''));
      }
    } catch (_) {}
    return Promise.resolve({ ok: false, reason: 'native_unavailable' });
  }

  // Emergency close-all — bypasses snap state in case React's state is stale.
  function emergencyCloseAllHudApps() {
    try {
      const native = _hudAppNative();
      if (native && typeof native.emergencyCloseAll === 'function') {
        return native.emergencyCloseAll();
      }
    } catch (_) {}
    return closeAllHudApps();
  }

  function activeHudAppId() {
    return snap.syrix_active_hud_app || (snap.syrix_hud_apps && snap.syrix_hud_apps[0] && snap.syrix_hud_apps[0].id) || '';
  }

  // Subscribe to the native Electron state stream. Retries with backoff if
  // the preload hasn't exposed window.syrixHud.hudApp yet at start() time.
  let _hudAppNativeAttached = false;
  function _attachHudAppNativeState() {
    if (_hudAppNativeAttached) return;
    const native = _hudAppNative();
    if (!native || !native.onState) {
      // Preload not ready yet — schedule a retry series (100→200→400→800→1600→3200ms).
      let _retryDelay = 100;
      const _maxRetries = 7;
      let _retries = 0;
      function _retry() {
        if (_hudAppNativeAttached || _retries >= _maxRetries) return;
        _retries++;
        const n = _hudAppNative();
        if (n && n.onState) {
          _attachHudAppNativeState();
        } else {
          _retryDelay = Math.min(_retryDelay * 2, 3200);
          setTimeout(_retry, _retryDelay);
        }
      }
      setTimeout(_retry, _retryDelay);
      return;
    }
    _hudAppNativeAttached = true;
    try {
      native.onState((payload) => {
        if (!payload || typeof payload !== 'object') return;
        _hudAppRefreshSnapshot(payload.apps || [], payload.active || null, payload.stage || null);
      });
    } catch (_) {}
    // Initial pull so React primes immediately.
    try {
      native.list().then((p) => {
        if (p && typeof p === 'object') _hudAppRefreshSnapshot(p.apps || [], p.active || null, p.stage || null);
      }).catch(() => {});
    } catch (_) {}
  }

  /* Python → HUD bridge: voice commands like "open YouTube in HUD" land in
   * syrix_host_hud_wiring._handle_open_app_in_hud, which writes a
   * `syrix_hud_app_request` field with a monotonic seq. We replay it here
   * by calling the matching helper. */
  // -1 = not yet initialised. On the very first hud_state.json read we usually
  // baseline the seq and skip — that prevents replaying stale startup requests.
  // BUT: if the request is FRESH (within the last few seconds), the user issued
  // it just as the HUD was loading; we MUST fire it instead of silently dropping
  // the open. Otherwise SYRIX speaks "opened" while nothing actually opens.
  let _lastHudAppRequestSeq = -1;
  let _lastHudAppRequestId = '';
  const _HUD_APP_REQ_FRESH_WINDOW_S = 2.0;
  function _maybeRunHudAppRequest(data) {
    if (!data || typeof data !== 'object') return;
    const req = data.syrix_hud_app_request;
    if (!req || typeof req !== 'object') return;
    const seq = Number(req.seq || 0);
    if (!seq) return;
    const requestId = String(req.request_id || '');
    const requestKey = requestId || String(seq);
    if (_lastHudAppRequestSeq < 0) {
      // First read: only honour the request if it was published in the last
      // few seconds (user-initiated, not a stale file on disk).
      const reqTs = Number(req.ts || 0);
      const now = Date.now() / 1000;
      const fresh = reqTs > 0 && (now - reqTs) <= _HUD_APP_REQ_FRESH_WINDOW_S;
      _lastHudAppRequestSeq = seq;
      _lastHudAppRequestId = requestKey;
      if (!fresh) return;
      // fall through — fire the fresh request
    } else {
      if (requestId) {
        if (requestId === _lastHudAppRequestId) return;
        _lastHudAppRequestId = requestId;
        _lastHudAppRequestSeq = Math.max(_lastHudAppRequestSeq, seq);
      } else {
        if (seq <= _lastHudAppRequestSeq) return;
        _lastHudAppRequestSeq = seq;
        _lastHudAppRequestId = requestKey;
      }
    }
    const action = String(req.action || '').toLowerCase();
    const target = String(req.url || req.target || req.app || '');
    const id = String(req.id || '') || activeHudAppId();
    const source = _hudAppOpenSource(req.source || req.open_source || req.reason, 'voice_command');
    const requestExtra = {
      request_id: requestId,
      target_type: String(req.target_type || (req.url ? 'url' : 'app')),
      domain: String(req.domain || (req.url ? _hudAppDomainFromUrl(req.url) : '')),
      source,
    };
    try {
      if (action === 'open' || action === 'open_in_hud') {
        openAppInHud(target, Object.assign({}, requestExtra, { url: req.url || '', mode: req.mode || 'normal', bounds: req.bounds || null, source, new_tab: !!(req.new_tab || req.newTab) }));
      } else if (action === 'close') {
        _hudAppCall('close', (n) => n.close(id), Object.assign({ id }, requestExtra));
      } else if (action === 'close_all') {
        Promise.all((snap.syrix_hud_apps || []).map((a) => a && a.id ? _hudAppCall('close', (n) => n.close(a.id), Object.assign({ id: a.id }, requestExtra)) : null))
          .then(() => _hudAppAck('close_all', true, 'closed', requestExtra))
          .catch((err) => _hudAppAck('close_all', false, String((err && err.message) || err || 'failed'), requestExtra));
      } else if (action === 'fullscreen' || action === 'maximize') {
        _hudAppCall('fullscreen', (n) => n.mode(id, 'fullscreen'), Object.assign({ id, mode: 'fullscreen' }, requestExtra));
      } else if (action === 'minimize') {
        _hudAppCall('minimize', (n) => n.mode(id, 'minimized'), Object.assign({ id, mode: 'minimized' }, requestExtra));
      } else if (action === 'restore' || action === 'normalize') {
        _hudAppCall('restore', (n) => n.mode(id, 'normal'), Object.assign({ id, mode: 'normal' }, requestExtra));
      } else if (action === 'refresh' || action === 'reload') {
        _hudAppCall('refresh', (n) => n.nav(id, 'refresh'), Object.assign({ id, action: 'refresh' }, requestExtra));
      } else if (action === 'back') {
        _hudAppCall('back', (n) => n.nav(id, 'back'), Object.assign({ id, action: 'back' }, requestExtra));
      } else if (action === 'forward') {
        _hudAppCall('forward', (n) => n.nav(id, 'forward'), Object.assign({ id, action: 'forward' }, requestExtra));
      } else if (action === 'popout' || action === 'open_external') {
        _hudAppCall('popout', (n) => n.popout(id), Object.assign({ id }, requestExtra));
      } else if (action === 'vision_on') {
        _hudAppCall('vision', (n) => n.vision(id, true), Object.assign({ id, enabled: true }, requestExtra));
      } else if (action === 'vision_off') {
        _hudAppCall('vision', (n) => n.vision(id, false), Object.assign({ id, enabled: false }, requestExtra));
      }
      else if (action === 'bigger' || action === 'resize_up') {
        const app = (snap.syrix_hud_apps || []).find((a) => a && a.id === id);
        const cur = (app && app.bounds) || null;
        const w = Math.round(((cur && cur.width)  || 720) * 1.15);
        const h = Math.round(((cur && cur.height) || 480) * 1.15);
        _hudAppCall('resize', (n) => n.mode(id, 'custom', { width: w, height: h, x: cur && cur.x, y: cur && cur.y }),
          Object.assign({ id, mode: 'custom' }, requestExtra));
      } else if (action === 'smaller' || action === 'resize_down') {
        const app = (snap.syrix_hud_apps || []).find((a) => a && a.id === id);
        const cur = (app && app.bounds) || null;
        const w = Math.round(((cur && cur.width)  || 720) * 0.85);
        const h = Math.round(((cur && cur.height) || 480) * 0.85);
        _hudAppCall('resize', (n) => n.mode(id, 'custom', { width: w, height: h, x: cur && cur.x, y: cur && cur.y }),
          Object.assign({ id, mode: 'custom' }, requestExtra));
      } else {
        _hudAppAck(action || 'hud_app_request', false, 'unknown_action', requestExtra);
      }
    } catch (exc) {
      _hudAppAck(action || 'hud_app_request', false, String((exc && exc.message) || exc || 'exception'), requestExtra);
    }
  }

  function onHudAppAck(fn) {
    if (typeof fn !== 'function') return () => {};
    const handler = (ev) => { try { fn(ev.detail); } catch (_) {} };
    window.addEventListener('syrix-hud-app-ack', handler);
    return () => window.removeEventListener('syrix-hud-app-ack', handler);
  }

  /* ── NVIDIA NIM helpers ───────────────────────────────────────── */

  function nvidiaSnapshot() {
    return snap.nvidia ? Object.assign({}, snap.nvidia) : null;
  }
  function nvidiaTest(modelId)         { return send('nvidia_brain_test',      { data: { model_id: modelId } }); }
  function nvidiaPreferred(role, mid)  { return send('nvidia_brain_preferred', { data: { role, model_id: mid } }); }
  function nvidiaEnable(modelId, on)   { return send('nvidia_brain_enable',    { data: { model_id: modelId, enabled: !!on } }); }

  /* ── Brain Switcher helpers ────────────────────────────────────── */
  function listBrains() { return send('list_brains'); }
  function setBrainProvider(id) {
    const obj = id && typeof id === 'object' ? id : null;
    const raw = String(obj ? (obj.id || obj.brain_id || obj.model || 'auto') : (id || 'auto')).trim();
    const pid = raw.includes('/') ? raw : raw.toLowerCase();
    const payload = { priority: pid, brain_id: pid };
    if (obj) {
      payload.model = String(obj.model || obj.modelName || '');
      payload.role = String(obj.role || obj.type || '');
      payload.provider = String(obj.provider_key || obj.provider || '');
      payload.display_name = String(obj.displayName || obj.display || '');
    }
    return send('set_brain_priority', { data: payload });
  }
  function onBrainList(fn) {
    if (typeof fn !== 'function') return () => {};
    const h = (ev) => { try { fn(ev.detail); } catch (_) {} };
    window.addEventListener('syrix-brain-list', h);
    return () => window.removeEventListener('syrix-brain-list', h);
  }

  /* ── Boot ─────────────────────────────────────────────────────── */

  // Core Stability Pass (2026-05-17): notify the Python backend that the
  // HUD window is closing so it can run its full graceful-shutdown path
  // (mic / camera / STT / wake / clap / bridges / TTS / workers). We fire
  // both over WS (preferred) and as a one-shot synchronous file marker so
  // the message can never be lost to a half-closed socket.
  let _appCloseSent = false;
  function _releaseRendererMediaTracks() {
    try {
      if (window.SyrixHandControl && typeof window.SyrixHandControl.disable === 'function') {
        window.SyrixHandControl.disable();
      }
    } catch (_) {}
    try {
      document.querySelectorAll('video').forEach((v) => {
        const so = v && v.srcObject;
        if (so && typeof so.getTracks === 'function') {
          so.getTracks().forEach((t) => { try { t.stop(); } catch (_) {} });
        }
        try { v.srcObject = null; } catch (_) {}
      });
    } catch (_) {}
  }

  function notifyAppClosing(shutdownId) {
    if (_appCloseSent) return true;
    _appCloseSent = true;
    _releaseRendererMediaTracks();
    let ok = false;
    try {
      const data = {};
      if (shutdownId) data.shutdown_id = String(shutdownId);
      ok = !!wsSend({ type: 'command', command: 'app_close', source: 'hud_window_close', data, ts: Date.now() / 1000 });
    } catch (_) {}
    try {
      // eslint-disable-next-line no-console
      console.info('%c[SYRIX ⏹]', 'color:#f59e0b', 'app_close ws_sent=' + ok + ' (main also writes hud_control.json)');
    } catch (_) {}
    return ok;
  }

  function _attachAppClosingHook() {
    try {
      const av = window.syrixHud;
      if (av && typeof av.onAppClosing === 'function') {
        av.onAppClosing((payload) => { notifyAppClosing(payload && payload.shutdown_id); });
      }
    } catch (_) {}
  }

  // 2026-05-19 optimization: wrap polling callbacks so they skip the JSON
  // fetch entirely when the HUD window is hidden (minimized / alt-tab /
  // background). The setInterval keeps firing at its native cadence (so we
  // resume immediately on re-show), but the fetch + JSON.parse + publish
  // chain is skipped — meaningful CPU + disk I/O saving when SYRIX is idle.
  function _pollIfVisible(fn) {
    return function () {
      try {
        if (typeof document !== 'undefined' && document.hidden) return;
      } catch (_) {}
      return fn.apply(null, arguments);
    };
  }

  function _attachGlobalControlTrace() {
    if (window.__SyrixGlobalControlTraceAttached) return;
    window.__SyrixGlobalControlTraceAttached = true;
    const describe = (el) => {
      if (!el) return {};
      const text = String((el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title'))) || el.innerText || el.value || el.id || el.className || '').replace(/\s+/g, ' ').trim();
      return {
        tag: String(el.tagName || '').toLowerCase(),
        text: text.slice(0, 96),
        disabled: !!(
          el.disabled ||
          el.getAttribute?.('aria-disabled') === 'true' ||
          el.hasAttribute?.('data-disabled-reason') ||
          el.classList?.contains('disabled')
        ),
        reason: String((el.getAttribute && (el.getAttribute('title') || el.getAttribute('data-disabled-reason'))) || '').slice(0, 160),
      };
    };
    document.addEventListener('click', (ev) => {
      const el = ev && ev.target && ev.target.closest
        ? ev.target.closest('button,[role="button"],a,.switch,.syrix-hud-dock-chip,.p-wbtn,.p-icon-btn,.p-mini-btn')
        : null;
      if (!el) return;
      const d = describe(el);
      _traceAction('button_clicked', d);
      if (d.disabled) {
        _traceAction('action_failed_reason', Object.assign({}, d, {
          code: 'UNAVAILABLE',
          reason: d.reason || 'control_disabled',
        }));
      }
    }, true);
    document.addEventListener('change', (ev) => {
      const el = ev && ev.target && ev.target.closest ? ev.target.closest('input,select,textarea') : null;
      if (!el) return;
      _traceAction('button_clicked', Object.assign(describe(el), {
        event: 'change',
        input_type: String(el.type || ''),
      }));
    }, true);
  }

  function start() {
    if (_started) return;
    _started = true;
    _attachGlobalControlTrace();
    connect();
    _attachHudAppNativeState();
    _attachAppClosingHook();
    // First-touch polls so the UI primes even before WS is open.
    tickState(); tickHudAppRequest(); tickBrain(); tickFreeWeb();
    // Prime the hand-control baseline once so existing stale files don't
    // auto-fire on boot — the function records the seq and returns without
    // publishing.
    tickHandControl();
    setInterval(_pollIfVisible(tickState),       _stateThrottleMs);
    setInterval(_pollIfVisible(tickHudAppRequest), _HUD_APP_REQ_POLL_MS);
    setInterval(_pollIfVisible(tickBrain),       _brainThrottleMs);
    setInterval(_pollIfVisible(tickFreeWeb),     6000);
    setInterval(_pollIfVisible(tickBrainResult), 2500);
    setInterval(_pollIfVisible(tickHandControl),  800);
    setInterval(_pollIfVisible(tickChat),        _chatPollMs / 2);
    // Re-attach native HUD-app state listener if preload becomes ready late.
    setInterval(() => { if (!_hudAppNativeAttached) _attachHudAppNativeState(); }, 2000);
  }

  /* ── Public surface ───────────────────────────────────────────── */

  window.SyrixBridge = {
    subscribe,
    send,
    noteUserActivity,
    setBrain,
    togglePrivacy,
    toggleWebcam,
    tradingStart,
    tradingStop,
    chat,
    assistantReplyText,
    chatTurnOutcome,
    cancelPendingChat,
    cancelChat: cancelPendingChat,
    cancelActiveTurn,
    cancelSpeech,
    isChatPending,
    isVoiceSpeaking,
    isTurnActive,
    isProgressAssistantMessage,
    isTechnicalChatSpam,
    nvidiaSnapshot,
    nvidiaTest,
    nvidiaPreferred,
    nvidiaEnable,
    listBrains,
    setBrainProvider,
    onBrainList,
    // Phase-2A Pass 2 additions
    setMusicProvider,
    obsidianStatus,
    obsidianRefresh,
    obsidianRemember,
    obsidianLogBug,
    obsidianLogFix,
    getRecentMemories,
    memoryFactsList, memoryFactAdd, memoryFactDelete, memoryFactEdit, memoryClearAll,
    forgetMemory,
    openObsidian,
    closeSpotify,
    // Phase-2B Pass 3 additions
    setTimer, cancelTimers, listTimers, pauseTimer, resumeTimer,
    musicPlay, musicPause, musicResume, musicNext, musicPrev, musicStop,
    volumeAction,
    setVolume,
    weatherFetch,
    openMap, openMapDirections,
    memoryWebRefresh, memoryWebSearch,
    memoryReviewApprove, memoryReviewDeny, memoryReviewEdit, memoryReviewDelete, memoryCleanupJunk,
    imageGenerate,
    runDiagnostic,
    mcpRefresh,
    setUserIdleTimeoutMinutes,
    screenVisionToggle, screenVisionStop,
    voiceCommand,
    onAck, pingContract,
    // SYRIX HUD App Dock additions
    openAppInHud, navigateHudApp, closeHudApp, focusHudApp, closeAllHudApps,
    ackHudAppChrome, emergencyCloseAllHudApps,
    fullscreenHudApp, minimizeHudApp, restoreHudApp, resizeHudApp,
    refreshHudApp, hudAppBack, hudAppForward, popoutHudApp,
    setHudAppVision, resolveHudAppUrl, activeHudAppId, onHudAppAck,
    setHudStage, setHudStageChatInset,
    // Phase-2C: Blender bridge
    blenderOpen, blenderCreate, blenderRotate, blenderWireframe,
    blenderExplode, blenderExport, blenderOpenViewer,
    // Phase-2D: Vision workspace + SYRIX 3D sandbox
    visionWorkspaceSet, sandboxCreate, sandboxAction, sandboxClear,
    sandboxExportBlender,
    // Phase-2 Runtime-Repair (2026-05-16): command inspector + external-browser
    // open. `recentCommands` returns the last 10 outbound commands so the
    // diagnostic panel can render "clicked → sent → received → result".
    recentCommands,
    getApiStatus: () => send('get_api_status', {}),
    openInExternalBrowser,
    openInsideHud,
    getHudNotificationHistory,
    clearHudNotificationHistory,
    recordHudNotification,
    // 2026-05-30 — Layer/Focus manager.
    layer: window.SyrixLayerManager,
    traceAction: _traceAction,
    recentActionTrace: () => _actionTrace.slice(-ACTION_TRACE_LIMIT),
    // Core Stability Pass: lets the React layer trigger the same backend
    // shutdown the X button does (used by the Quit menu / debug paths).
    notifyAppClosing,
    // On-demand widget helpers
    showSetupImage: () => send('show_setup_image'),
    getSnapshot: () => Object.assign({}, snap),
    __syrixBridgeStarted: true,
    _internal: { connect, send: wsSend },
  };

  // Shim: legacy code paths that still expect `syrixHudSendCommand`.
  if (typeof window.syrixHudSendCommand !== 'function') {
    window.syrixHudSendCommand = send;
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
