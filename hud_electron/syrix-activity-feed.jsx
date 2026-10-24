/* SYRIX Research Activity Feed (universal)
 * ------------------------------------------------------------------
 * One feed implementation, theme-agnostic. Reads activity events from
 * the bridge snapshot (hud_state.json -> activity_feed) and exposes:
 *
 *   - window.SyrixActivityFeedHook()      — React hook returning {events, latestKind, hasUnread, markRead, brainState}
 *   - window.SyrixActivityFeedPanel(props)— React component rendering the feed (sources clickable)
 *   - window.SyrixBrainStatusPill(props)  — React component for the live brain badge + fallback notice
 *
 * Themes only style; this file owns the logic. */

(function() {
  if (window.SyrixActivityFeedHook) return; // load once
  const { useState, useEffect, useRef, useMemo } = React;

  // ── Event kind metadata ──────────────────────────────────────────
  const KIND_META = {
    info:            { icon: '◇',  color: '#9bb1c9', label: 'Info' },
    intent:          { icon: '⊙',  color: '#d8b06a', label: 'Intent' },
    plan:            { icon: '◆',  color: '#8e7cff', label: 'Plan' },
    brain_route:     { icon: '⊛',  color: '#16d37a', label: 'Brain' },
    brain_fallback:  { icon: '⚠',  color: '#f5c243', label: 'Fallback' },
    brain_restored:  { icon: '✓',  color: '#16d37a', label: 'Restored' },
    tool_call:       { icon: '⚙',  color: '#74e7d9', label: 'Tool' },
    connector:       { icon: '🔌', color: 'var(--eg-gold-bright)', label: 'Connector' },
    search:          { icon: '⌕',  color: '#74e7d9', label: 'Searching' },
    source:          { icon: '↗',  color: 'var(--eg-gold-bright)', label: 'Source' },
    memory:          { icon: '✦',  color: '#c89bff', label: 'Memory' },
    synthesis:       { icon: '◐',  color: '#fdb863', label: 'Synthesising' },
    speak:           { icon: '◗',  color: '#16d37a', label: 'Speaking' },
    done:            { icon: '●',  color: '#16d37a', label: 'Done' },
    error:           { icon: '✕',  color: '#ff6b8b', label: 'Error' },
    mode:            { icon: '✦',  color: '#eef2f8', label: 'Mode' },
  };
  function metaFor(kind) {
    return KIND_META[String(kind || '').toLowerCase()] || KIND_META.info;
  }
  function fmtTimeAgo(ts) {
    const t = Number(ts || 0);
    if (!t) return '';
    const delta = (Date.now() / 1000) - t;
    if (delta < 1) return 'now';
    if (delta < 60) return Math.floor(delta) + 's';
    if (delta < 3600) return Math.floor(delta / 60) + 'm';
    return Math.floor(delta / 3600) + 'h';
  }

  // Clean-timeline mode (2026-07-10): the thinking panel renders friendly,
  // human-readable pills instead of raw backend labels. The backend publishes
  // hud_flags.clean_timeline from config "hud_clean_timeline_enabled"
  // (absent key = ON). Dev escape hatch: localStorage syrix_dev_trace = '1'
  // restores the raw ladder + raw inspect instantly, no restart needed.
  function _cleanModeOn(raw) {
    try {
      if (window.localStorage && localStorage.getItem('syrix_dev_trace') === '1') return false;
    } catch (_) {}
    const f = raw && raw.hud_flags;
    return !(f && f.clean_timeline === false);
  }

  // ── Hook — pulls events + brain state out of the bridge ──────────
  function SyrixActivityFeedHook() {
    const [events, setEvents] = useState([]);
    const [brainState, setBrainState] = useState({
      active: '',         // current provider name (eg 'groq', 'qwen', 'llama')
      label: '',          // friendly label (eg 'Groq · llama-3.3-70b')
      fallbackFrom: '',   // if a fallback fired, the original provider
      fallbackTs: 0,      // timestamp of latest fallback (so UI can flash a notice)
      restoredTs: 0,
    });
    const [hasUnread, setUnread] = useState(false);
    const [cleanMode, setCleanMode] = useState(_cleanModeOn(null));
    const lastSeenTsRef = useRef(0);

    useEffect(() => {
      const B = window.SyrixBridge;
      if (!B || !B.subscribe) return;
      const unsub = B.subscribe((s) => {
        const raw = (s && s.raw) || {};
        const list = Array.isArray(raw.activity_feed) ? raw.activity_feed : [];
        setEvents(list);
        setCleanMode(_cleanModeOn(raw));
        // Surface unread events relative to last open.
        const top = list.length ? Number(list[list.length - 1].ts || 0) : 0;
        if (top > lastSeenTsRef.current) setUnread(true);

        // Compute brain state from latest brain_route / fallback / restored events.
        let active = '', label = '', fallbackFrom = '', fallbackTs = 0, restoredTs = 0;
        for (let i = list.length - 1; i >= 0 && i >= list.length - 30; i--) {
          const ev = list[i];
          if (!ev) continue;
          if (!active && ev.kind === 'brain_route') {
            active = String(ev.provider || '').toLowerCase();
            label = String(ev.label || '').replace(/^Routing to /i, '');
          }
          if (!fallbackTs && ev.kind === 'brain_fallback') {
            fallbackFrom = String(ev.provider || '').toLowerCase();
            fallbackTs = Number(ev.ts || 0);
          }
          if (!restoredTs && ev.kind === 'brain_restored') {
            restoredTs = Number(ev.ts || 0);
          }
        }
        // Prefer raw current_brain when present.
        const cb = String(raw.current_brain || raw.active_brain || raw.syrix_last_brain_label || '').trim();
        if (cb) label = cb;
        if (!active) active = String(raw.brain_current_provider || '').toLowerCase();
        setBrainState({ active, label, fallbackFrom, fallbackTs, restoredTs });
      });
      return unsub;
    }, []);

    function markRead() {
      setUnread(false);
      lastSeenTsRef.current = Date.now() / 1000;
    }

    return { events, brainState, hasUnread, markRead, cleanMode };
  }

  // ── Feed panel — Dynamic Island expansion section ────────────────
  function SyrixActivityFeedPanel({ events, max = 20, onOpenSource }) {
    const safe = Array.isArray(events) ? events.slice(-max).slice().reverse() : [];
    if (!safe.length) {
      return React.createElement('div', { className: 'p-island-feed-empty', style: panelStyles.empty },
        'No recent activity. Send a message to see SYRIX work.'
      );
    }
    return React.createElement('div', { className: 'p-island-feed', style: panelStyles.feed },
      safe.map((ev) => {
        const m = metaFor(ev.kind);
        const ts = fmtTimeAgo(ev.ts);
        const isSource = ev.kind === 'source' && ev.source_url;
        const onClick = isSource
          ? (e) => {
              e.stopPropagation();
              if (typeof onOpenSource === 'function') {
                onOpenSource(ev.source_url, ev.source_title || ev.detail || ev.label);
              } else {
                try { window.open(ev.source_url, '_blank', 'noopener'); } catch (_) {}
              }
            }
          : null;
        return React.createElement(
          'div',
          {
            key: ev.id || (ev.ts + '_' + ev.label),
            className: 'p-island-feed-row' + (isSource ? ' source' : ''),
            onClick: onClick,
            title: isSource ? 'Open source' : (ev.detail || ''),
            style: { ...panelStyles.row, cursor: isSource ? 'pointer' : 'default' },
          },
          React.createElement('span', {
            className: 'p-island-feed-icon',
            style: { ...panelStyles.icon, color: m.color }
          }, m.icon),
          React.createElement('div', { className: 'p-island-feed-body', style: panelStyles.body },
            React.createElement('div', { className: 'p-island-feed-label', style: panelStyles.label }, ev.label),
            (ev.detail || isSource) && React.createElement('div', {
              className: 'p-island-feed-detail',
              style: { ...panelStyles.detail, color: isSource ? 'var(--eg-gold-bright)' : 'rgba(180,195,215,0.7)' }
            }, isSource ? (ev.source_title || ev.source_url) : ev.detail),
          ),
          React.createElement('span', { className: 'p-island-feed-ts', style: panelStyles.ts }, ts),
        );
      })
    );
  }

  // ── Brain status pill — appears on the topbar ───────────────────
  function SyrixBrainStatusPill({ brainState, compact }) {
    const { active, label, fallbackFrom, fallbackTs, restoredTs } = brainState || {};
    const fresh = (ts) => ts && (Date.now() / 1000 - ts < 12); // 12s notice window
    const inFallback = fresh(fallbackTs) && !fresh(restoredTs);
    const justRestored = fresh(restoredTs);
    const dotColor = inFallback ? '#f5c243' : (active ? '#16d37a' : 'rgba(120,145,175,0.6)');
    const txt = label || (active ? active : 'auto');
    return React.createElement('div', {
      className: 'syrix-brain-pill' + (inFallback ? ' fallback' : '') + (justRestored ? ' restored' : ''),
      title: inFallback
        ? `Brain fallback active — ${fallbackFrom || 'primary'} unavailable, routing to ${active || 'next'}`
        : justRestored
          ? `Primary brain restored: ${active || 'auto'}`
          : `Active brain: ${txt}`,
      style: pillStyles.pill,
    },
      React.createElement('span', {
        style: { ...pillStyles.dot, background: dotColor, boxShadow: dotColor === 'rgba(120,145,175,0.6)' ? 'none' : `0 0 5px ${dotColor}` }
      }),
      !compact && React.createElement('span', { style: pillStyles.text }, txt.toUpperCase()),
      inFallback && React.createElement('span', { style: pillStyles.notice }, 'FALLBACK'),
      justRestored && React.createElement('span', { style: { ...pillStyles.notice, background: 'rgba(22,211,122,0.18)', borderColor: 'rgba(22,211,122,0.55)', color: '#16d37a' } }, 'RESTORED'),
    );
  }

  // ── Inline styles (themes can override via class) ──────────────
  const panelStyles = {
    feed: {
      display: 'flex', flexDirection: 'column', gap: '4px',
      maxHeight: '220px', overflowY: 'auto',
      padding: '4px 0',
    },
    empty: {
      padding: '14px 8px', textAlign: 'center',
      color: 'rgba(180,195,215,0.55)',
      font: '600 10px JetBrains Mono', letterSpacing: '0.08em',
    },
    row: {
      display: 'flex', alignItems: 'flex-start', gap: '8px',
      padding: '5px 8px', borderRadius: '5px',
      background: 'rgba(255,255,255,0.025)',
      borderLeft: '2px solid rgba(255,255,255,0.06)',
      transition: 'background 0.15s',
    },
    icon: {
      flexShrink: 0, width: '14px', textAlign: 'center',
      font: '700 12px JetBrains Mono',
      paddingTop: '1px',
    },
    body: { flex: 1, minWidth: 0 },
    label: {
      font: '600 10px JetBrains Mono', letterSpacing: '0.04em',
      color: 'rgba(225,235,250,0.92)', whiteSpace: 'nowrap',
      overflow: 'hidden', textOverflow: 'ellipsis',
    },
    detail: {
      font: '400 9px JetBrains Mono', letterSpacing: '0.02em',
      marginTop: '1px', whiteSpace: 'nowrap',
      overflow: 'hidden', textOverflow: 'ellipsis',
    },
    ts: {
      flexShrink: 0,
      font: '500 9px JetBrains Mono',
      color: 'rgba(120,145,175,0.6)', paddingTop: '2px',
    },
  };

  const pillStyles = {
    pill: {
      display: 'inline-flex', alignItems: 'center', gap: '6px',
      padding: '4px 10px', borderRadius: '99px',
      background: 'rgba(255,255,255,0.04)',
      border: '1px solid rgba(255,255,255,0.08)',
      font: '600 9px JetBrains Mono', letterSpacing: '0.16em',
      color: 'rgba(225,235,250,0.92)', textTransform: 'uppercase',
    },
    dot: {
      width: '6px', height: '6px', borderRadius: '50%', flexShrink: 0,
    },
    text: { whiteSpace: 'nowrap' },
    notice: {
      marginLeft: '4px', padding: '1px 6px', borderRadius: '99px',
      background: 'rgba(245,194,67,0.18)', border: '1px solid rgba(245,194,67,0.55)',
      color: '#f5c243', font: '700 8px JetBrains Mono', letterSpacing: '0.16em',
    },
  };

  // ════════════════════════════════════════════════════════════════
  // SHARED LIVE TRACE (2026-06-13) — Grok-style "thinking" ladder +
  // per-answer inspector, driven by the SAME activity_feed stream the
  // classic chat uses. Self-contained + inline-styled so EVERY theme
  // shell (Aurora Glass + future shells) renders an identical, animated
  // trace with clickable sources — no per-theme CSS required. Restores
  // the live "what SYRIX is searching / which sources / then answer"
  // panel into the shell themes (it had only ever rendered in the
  // classic chat; the shells read a state key the backend never set).
  // ════════════════════════════════════════════════════════════════
  const h = React.createElement;

  // Kinds that belong on the live ladder (skip pure info / speak noise).
  // 2026-07-03 (Atlas rebuild): the pipeline stages are first-class rungs.
  const LADDER_KINDS = new Set([
    'intent', 'plan', 'brain_route', 'brain_fallback', 'brain_restored',
    'tool_call', 'search', 'source', 'memory', 'synthesis', 'done',
    'context_init', 'retrieval', 'filter_rank', 'verify', 'pack_built', 'pack_enriched',
    'spark_run', 'zenith_run', 'merge_done', 'mode',
    'connector',
  ]);

  // Inject motion keyframes once (GPU-only transforms; reduced-motion safe).
  let _traceStylesInjected = false;
  function _ensureTraceStyles() {
    if (_traceStylesInjected) return;
    _traceStylesInjected = true;
    try {
      const el = document.createElement('style');
      el.id = 'syrix-live-trace-styles';
      el.textContent = [
        '@keyframes syxTraceRise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}',
        '@keyframes syxTracePulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.5);opacity:.5}}',
        '.syx-trace{animation:syxTraceRise .28s cubic-bezier(.2,.9,.3,1.1) both}',
        '.syx-trace-rung{animation:syxTraceRise .3s cubic-bezier(.2,.9,.3,1.1) both}',
        '.syx-trace-rung.is-head .syx-trace-bullet{animation:syxTracePulse 1.1s ease-in-out infinite}',
        '.syx-trace-src{transition:filter .15s,transform .15s}',
        '.syx-trace-src:hover{filter:brightness(1.3);transform:translateX(1px)}',
        '@media (prefers-reduced-motion: reduce){.syx-trace,.syx-trace-rung,.syx-trace-rung.is-head .syx-trace-bullet{animation:none!important}}',
        // Clean intelligence timeline (2026-07-10) — calm glass pills on the
        // aurora tokens (with literal fallbacks so any shell can host them).
        '@keyframes syxPillIn{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:none}}',
        '@keyframes syxPillBreath{0%,100%{opacity:1}50%{opacity:.6}}',
        '.syx-tl{display:flex;flex-direction:column;align-items:flex-start;gap:7px;margin:6px 0 2px}',
        '.syx-tl-pill{display:inline-flex;align-items:center;flex-wrap:wrap;gap:7px;max-width:100%;padding:6px 12px;border-radius:14px;background:var(--eg-glass-2,rgba(255,255,255,.055));border:1px solid var(--eg-stroke,rgba(255,255,255,.1));transition:opacity var(--eg-dur,.26s) ease;animation:syxPillIn var(--eg-dur,.26s) var(--eg-spring,cubic-bezier(.32,.72,0,1)) both}',
        '.syx-tl-ico{font-size:12.5px;line-height:1}',
        '.syx-tl-pill.is-active .syx-tl-ico{animation:syxPillBreath 2.4s ease-in-out infinite}',
        '.syx-tl-txt{color:var(--eg-text-1,rgba(246,239,226,.95));font:600 11.5px Inter,system-ui,sans-serif;letter-spacing:.01em;transition:color var(--eg-dur-fast,.14s) ease}',
        '.syx-tl-pill.is-settled{opacity:.78}',
        '.syx-tl-pill.is-settled .syx-tl-txt{color:var(--eg-text-2,rgba(205,210,220,.75))}',
        '.syx-tl-sub{color:var(--eg-text-3,rgba(175,180,195,.6));font:400 10.5px Inter,system-ui,sans-serif}',
        '.syx-tl-chips{display:inline-flex;align-items:center;flex-wrap:wrap;gap:4px}',
        '.syx-tl-chip{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;background:rgba(255,255,255,.05);border:1px solid var(--eg-stroke,rgba(255,255,255,.09));color:var(--eg-text-2,rgba(215,225,240,.82));font:500 10px Inter,system-ui,sans-serif;animation:syxPillIn var(--eg-dur,.26s) var(--eg-spring,cubic-bezier(.32,.72,0,1)) both}',
        '.syx-tl-chip.is-link{cursor:pointer;transition:filter var(--eg-dur-fast,.14s) ease}',
        '.syx-tl-chip.is-link:hover{filter:brightness(1.25)}',
        // Connector pill (2026-07-12) — visually distinct from every other
        // stage: amber tint + soft glow sweep while active, so the user can
        // always SEE when SYRIX is touching a connected service, and which.
        '@keyframes syxConnGlow{0%,100%{box-shadow:0 0 0 0 rgba(var(--eg-accent-deep-rgb),0)}50%{box-shadow:0 0 16px 1px rgba(var(--eg-accent-deep-rgb),.4)}}',
        '@keyframes syxConnPlug{0%,100%{transform:rotate(0deg) scale(1)}50%{transform:rotate(-12deg) scale(1.15)}}',
        '.syx-tl-pill.is-connector{background:rgba(var(--eg-accent-surface-deep-rgb),.10);border-color:rgba(var(--eg-accent-deep-rgb),.42)}',
        '.syx-tl-pill.is-connector .syx-tl-txt{color:var(--eg-gold-bright)}',
        '.syx-tl-pill.is-connector.is-active{animation:syxPillIn var(--eg-dur,.26s) var(--eg-spring,cubic-bezier(.32,.72,0,1)) both,syxConnGlow 1.6s ease-in-out infinite}',
        '.syx-tl-pill.is-connector.is-active .syx-tl-ico{animation:syxConnPlug 1.3s ease-in-out infinite}',
        '.syx-tl-pill.is-connector.is-settled{opacity:.9}',
        // Brand badge — small rounded service logo tile inside the pill.
        '.syx-tl-brand{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;border-radius:4px;color:#fff;font-weight:800;font-family:Inter,system-ui,sans-serif;line-height:1;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18);flex-shrink:0}',
        '.syx-tl-pill.is-connector.is-active .syx-tl-brand{animation:syxConnPlug 1.3s ease-in-out infinite}',
        '@media (prefers-reduced-motion: reduce){.syx-tl-pill.is-connector.is-active,.syx-tl-pill.is-connector.is-active .syx-tl-ico{animation:none!important}}',
        '@media (prefers-reduced-motion: reduce){.syx-tl-pill,.syx-tl-chip,.syx-tl-pill.is-active .syx-tl-ico{animation:none!important;transition:none!important}}',
      ].join('\n');
      document.head.appendChild(el);
    } catch (_) {}
  }

  function _openSource(url, title) {
    if (!url) return;
    try {
      if (window.SyrixBridge && typeof window.SyrixBridge.open === 'function') {
        window.SyrixBridge.open(url, { title: title || url });
      } else if (window.SyrixBridge && window.SyrixBridge.send) {
        window.SyrixBridge.send('open_url', { data: { url: url } });
      } else {
        window.open(url, '_blank', 'noopener');
      }
    } catch (_) { try { window.open(url, '_blank', 'noopener'); } catch (__) {} }
  }

  // Build the ladder rows from raw feed events (this turn only, deduped).
  // 2026-07-03 (Atlas rebuild) — STRICT rid scoping: every backend emitter
  // tags its rid now, so untagged events are dropped too. They were the
  // cross-turn bleed vector (a concurrent voice/proactive turn's rid-less
  // events used to render into this turn's trace).
  function _ladderRows(events, sinceTs, activeRid) {
    const rows = [];
    for (const ev of (events || [])) {
      const ts = Number(ev.ts || 0);
      if (sinceTs && ts < sinceTs) continue;
      const rid = String(ev.request_id || '');
      if (!rid) continue;
      if (activeRid && rid !== activeRid) continue;
      const kind = String(ev.kind || '').toLowerCase();
      if (!LADDER_KINDS.has(kind)) continue;
      const label = String(ev.label || '').trim();
      if (!label && kind !== 'source') continue;
      const url = String(ev.source_url || '').trim();
      const detail = kind === 'source'
        ? String(ev.source_title || ev.detail || url)
        : String(ev.detail || '');
      const tail = rows[rows.length - 1];
      if (tail && tail.kind === kind && tail.label === label && tail.url === url) continue;
      rows.push({ kind: kind, label: label || 'Read source', detail: detail, url: url, ts: ts,
                  provider: String(ev.provider || '') });
    }
    // No trailing slice. These rows are already scoped to one request, so a
    // cap here could only drop steps the turn genuinely took — and anything
    // dropped before the ladder sees it can never be recovered, because the
    // feed is a rolling window that will not carry it again.
    return rows;
  }

  // Scope the trace to the CURRENT request. The most recent event that carries a
  // request id identifies the active turn; we take that turn's start time and
  // build rows from there, dropping any event tagged with a different request.
  // This is what makes the panel "show what SYRIX is actually doing" (Grok-style)
  // and never random sources from an earlier or proactive turn. Falls back to an
  // 8s-gap cluster when nothing is tagged so the trace still works.
  function _currentTurnRows(events) {
    const evs = events || [];
    if (!evs.length) return { rows: [], newestTs: 0 };
    let activeRid = '';
    for (let i = evs.length - 1; i >= 0; i--) {
      const r = String(evs[i].request_id || '');
      if (r) { activeRid = r; break; }
    }
    let startTs = 0;
    if (activeRid) {
      for (const e of evs) {
        if (String(e.request_id || '') === activeRid) {
          const ts = Number(e.ts || 0);
          if (!startTs || ts < startTs) startTs = ts;
        }
      }
    }
    if (!startTs) {
      // No tagged events — gap-cluster: only group events within a tight 3s window
      // so background/proactive events from prior turns don't bleed into the trace.
      const meaningful = evs.filter(function (e) { return LADDER_KINDS.has(String(e.kind || '').toLowerCase()); });
      if (!meaningful.length) return { rows: [], newestTs: 0 };
      startTs = Number(meaningful[meaningful.length - 1].ts || 0);
      for (let i = meaningful.length - 1; i > 0; i--) {
        const cur = Number(meaningful[i].ts || 0);
        const prev = Number(meaningful[i - 1].ts || 0);
        if (cur - prev > 3) break;
        startTs = prev;
      }
    }
    const rows = _ladderRows(evs, startTs - 0.001, activeRid);
    const newestTs = rows.length ? Number(rows[rows.length - 1].ts || 0) : 0;
    // The rid comes back with the rows: the thinking panel keys its
    // accumulated step list on it, so it can tell "more of this turn" from
    // "a different turn" without guessing from timestamps.
    return { rows: rows, newestTs: newestTs, rid: activeRid };
  }

  // ════════════════════════════════════════════════════════════════
  // CLEAN INTELLIGENCE TIMELINE (2026-07-10) — premium user-facing
  // rendering of the SAME rid-scoped event stream. Raw backend labels
  // are never shown for known kinds; unknown kinds render as "Working…".
  // TRUTH RULE: every pill is driven by a real backend event — no
  // synthetic progress. The raw ladder below stays intact and returns
  // via localStorage syrix_dev_trace='1' or hud_clean_timeline_enabled=false.
  // ════════════════════════════════════════════════════════════════

  // Ported from syrix-primary-chat.jsx _INTENT_FRIENDLY — sub-line under
  // the "Understanding request" pill.
  const _SYX_INTENT_FRIENDLY = {
    general_knowledge: 'Consulting Syrix Brain',
    wellbeing: 'Personality reply',
    chitchat: 'Casual chat',
    greeting: 'Greeting',
    farewell: 'Farewell',
    live_research: 'Web research',
    deep_research: 'Deep research',
    live_news: 'Pulling news',
    weather: 'Checking weather',
    trade_analysis: 'Trade analysis',
    stock_price: 'Stock quote',
    map: 'Map lookup',
    chart: 'Chart data',
    identity: 'Identity check',
    memory_recall: 'Memory recall',
    memory_remember: 'Saving to memory',
    memory_forget: 'Clearing memory',
    memory_review: 'Reviewing memory',
    system_action: 'Running system action',
    pc_status: 'System metrics',
    brain_status: 'Brain status',
    reminder: 'Setting reminder',
    continuation: 'Following up',
    clarify: 'Clarifying',
  };

  // tool_call labels → friendly verbs, matched by substring (first hit wins).
  const _SYX_TOOL_HINTS = [
    ['weather', 'Checking the weather'],
    ['news', 'Pulling the news'],
    ['screenshot', 'Taking a screenshot'],
    ['reminder', 'Setting a reminder'],
    ['timer', 'Setting a timer'],
    ['stock', 'Checking the market'],
    ['trade', 'Checking the market'],
    ['browser', 'Browsing a page'],
    ['sandbox', 'Browsing a page'],
    ['system', 'Running a system action'],
  ];

  // provider/domain → source-chip identity. Domain wins over provider so a
  // Wikipedia page found via a meta-engine still reads "Wikipedia".
  function _syxSourceMeta(provider, url) {
    let domain = '';
    try { domain = new URL(url).hostname.replace(/^www\./, '').toLowerCase(); } catch (_) {}
    if (domain) {
      if (domain.endsWith('wikipedia.org')) return { name: 'Wikipedia', icon: '📚' };
      if (domain.endsWith('arxiv.org')) return { name: 'arXiv', icon: '📄' };
      if (domain.endsWith('github.com')) return { name: 'GitHub', icon: '💻' };
      if (domain.endsWith('reddit.com')) return { name: 'Reddit', icon: '🗣' };
      if (domain.indexOf('stackexchange') >= 0 || domain.endsWith('stackoverflow.com')) return { name: 'Stack Exchange', icon: '💬' };
    }
    const p = String(provider || '').toLowerCase();
    const byProvider = {
      arxiv: ['arXiv', '📄'], stackexchange: ['Stack Exchange', '💬'],
      dictionary: ['Dictionary', '📖'], github: ['GitHub', '💻'], reddit: ['Reddit', '🗣'],
    };
    if (byProvider[p]) return { name: byProvider[p][0], icon: byProvider[p][1] };
    if (domain) {
      const stem = domain.split('.')[0];
      // Short stems are almost always initialisms — bbc, cnn, nyt, ft, bcc —
      // and "Bbc" reads as a typo. Longer ones are words and title-case.
      const name = stem.length <= 4 ? stem.toUpperCase()
        : stem.charAt(0).toUpperCase() + stem.slice(1);
      return { name: name, icon: '🌐' };
    }
    return { name: 'Web', icon: '🌐' };
  }

  // Meta-engines collapse into a single "Web" chip — engine names are
  // plumbing, not sources the user cares about.
  const _SYX_META_ENGINES = { free_web: 1, ddgs: 1, mojeek: 1, searxng: 1, live_resolver: 1 };
  function _chipForProvider(provider) {
    const p = String(provider || '').toLowerCase();
    if (!p) return null;
    if (_SYX_META_ENGINES[p]) return { name: 'Web', icon: '🌐' };
    const meta = _syxSourceMeta(p, '');
    return { name: meta.name, icon: meta.icon };
  }

  // Connector brand badges (2026-07-13) — small colored logo marks so the
  // thinking pill visibly shows WHICH service SYRIX is touching. Matches the
  // Connectors page tiles. Keyed by the name inside the backend label.
  const _SYX_CONN_BRANDS = {
    gmail:           { bg: 'linear-gradient(135deg,#ea4335,#c5221f)', mark: 'M' },
    'google calendar': { bg: 'linear-gradient(135deg,#4285f4,#1a73e8)', mark: '31' },
    'google drive':  { bg: 'linear-gradient(135deg,#1da462,#12805c)', mark: '▲' },
    github:          { bg: 'linear-gradient(135deg,#2d333b,#1c2128)', mark: 'GH' },
    notion:          { bg: 'linear-gradient(135deg,#2f3437,#191b1d)', mark: 'N' },
    slack:           { bg: 'linear-gradient(135deg,#611f69,#4a154b)', mark: '#' },
    spotify:         { bg: 'linear-gradient(135deg,#1db954,#128a40)', mark: '♪' },
    outlook:         { bg: 'linear-gradient(135deg,#0f6cbd,#0b5394)', mark: 'O' },
    filesystem:      { bg: 'linear-gradient(135deg,#d4aa5a,#a97e2f)', mark: '🗀' },
  };
  function _syxConnectorBrand(label) {
    const l = String(label || '').toLowerCase();
    for (const name in _SYX_CONN_BRANDS) {
      if (l.indexOf(name) >= 0) return Object.assign({ name: name }, _SYX_CONN_BRANDS[name]);
    }
    return null;
  }
  function _syxBrandBadge(brand) {
    // Small rounded brand tile (the same mark the Connectors page shows).
    return h('span', {
      className: 'syx-tl-brand',
      style: { background: brand.bg,
               fontSize: (String(brand.mark).length > 1 ? '8px' : '10px') },
    }, brand.mark);
  }

  // kind → friendly pill descriptor. null = drop the row from the timeline.
  // `collect` marks a search-like pill that gathers source chips.
  function _syxFriendly(r) {
    const label = String(r.label || '');
    switch (r.kind) {
      case 'intent': {
        const tok = (label.match(/^Intent:\s*(.+)$/i) || [])[1];
        const sub = tok ? (_SYX_INTENT_FRIENDLY[String(tok).toLowerCase().trim()] || '') : '';
        return { icon: '🧠', text: 'Understanding request', sub: sub };
      }
      case 'plan': return { icon: '🧭', text: 'Choosing the best approach' };
      case 'brain_route': return { icon: '✨', text: 'Reasoning' };
      case 'brain_fallback': return { icon: '🔁', text: 'Switching to a backup engine' };
      case 'brain_restored': return { icon: '✓', text: 'Primary engine restored' };
      case 'tool_call': {
        const l = label.toLowerCase();
        for (const pair of _SYX_TOOL_HINTS) { if (l.indexOf(pair[0]) >= 0) return { icon: '⚙', text: pair[1] }; }
        return { icon: '⚙', text: 'Using a tool' };
      }
      case 'connector': {
        // Connectors (2026-07-12): distinct, NAMED pill with the service's
        // brand badge. Backend label carries the connector name ("Using Gmail
        // connector", "Gmail connector · Read Emails ✓"); detail carries the
        // action ("Read Emails"). Parse the brand so we can render its logo.
        const brand = _syxConnectorBrand(label);
        return { icon: brand ? '' : '🔌', brand: brand,
                 text: label || 'Using a connector',
                 sub: String(r.detail || ''), connector: true };
      }
      case 'search': return { icon: '🔎', text: 'Searching the web', collect: true, chipFromProvider: r.provider };
      case 'retrieval': return { icon: '🔎', text: 'Searching the web', collect: true, chipFromProvider: r.provider };
      case 'filter_rank': return { icon: '🧠', text: 'Evaluating sources' };
      case 'verify': return { icon: '✅', text: 'Cross-checking facts' };
      case 'pack_built': case 'pack_enriched': return { icon: '✓', text: 'Information found' };
      case 'context_init': return { icon: '🌐', text: 'Gathering context' };
      case 'spark_run': return { icon: '⚡', text: 'Composing answer' };
      case 'zenith_run': return { icon: '◈', text: 'Deep research underway' };
      case 'synthesis': return { icon: '✍', text: 'Preparing answer' };
      case 'memory': {
        if (/recalled/i.test(label)) return { icon: '💾', text: 'Recalled from memory' };
        if (/^saving/i.test(label)) return { icon: '💾', text: 'Saving to memory' };
        if (/declined/i.test(label)) return { icon: '💾', text: 'Chose not to store that' };
        return { icon: '💾', text: 'Checking memory' };
      }
      case 'done': return { icon: '✓', text: 'Done', terminal: true };
      case 'error': return { icon: '⚠', text: 'Something went wrong' };
      case 'mode': return { icon: /fast/i.test(label) ? '⚡' : '🧠', text: label || 'Choosing reasoning mode' };
      case 'info': case 'merge_done': case 'speak': return null;
      default: return { icon: '⋯', text: 'Working…' };
    }
  }

  // Pill-worthy kinds (2026-07-13) — routing mechanics (intent/plan/
  // brain_route/synthesis) fire on EVERY turn, including a one-word "hi", so
  // gating pill visibility on their presence made the pulse flip
  // Thinking→pills→Thinking→pills on trivial replies (the user: "no point of
  // them, only use those pills for web search and hard questions"). These
  // kinds mean SYRIX is doing real, visible-worthy work; only THEIR presence
  // flips the pulse out of the idle "Thinking" state. Routing-mechanic rows
  // still render as pills once a worthy kind unlocks the timeline — they're
  // useful context alongside real steps, just not a reason to show it alone.
  const _SYX_WORTHY_KINDS = new Set([
    'tool_call', 'connector', 'search', 'retrieval', 'source',
    'verify', 'filter_rank', 'memory', 'context_init',
    'pack_built', 'pack_enriched', 'spark_run', 'zenith_run',
    'brain_fallback', 'error',
  ]);
  function _hasWorthyActivity(rows) {
    for (const r of (rows || [])) { if (_SYX_WORTHY_KINDS.has(r.kind)) return true; }
    return false;
  }

  const _SYX_MAX_CHIPS = 6;

  function _addChip(pill, chip) {
    if (!chip) return;
    for (const c of pill.chips) {
      if (c.name === chip.name && String(c.url || '') === String(chip.url || '')) return;
    }
    if (pill.chips.length >= _SYX_MAX_CHIPS) { pill.moreChips = (pill.moreChips || 0) + 1; return; }
    pill.chips.push(chip);
  }

  // Transform rid-scoped ladder rows into friendly pills: map each kind,
  // merge consecutive identical stages (stable ts = stable animation key),
  // fold sources into chip strips, cap the ladder at the newest pills.
  function _friendlyRows(rows) {
    const pills = [];
    for (const r of (rows || [])) {
      if (r.kind === 'source') {
        const meta = _syxSourceMeta(r.provider, r.url);
        let host = null;
        for (let i = pills.length - 1; i >= 0; i--) { if (pills[i].chips) { host = pills[i]; break; } }
        if (!host) {
          // A source with no preceding search stage still reflects a real
          // backend event — synthesize the search pill from ITS timestamp.
          host = { key: 'search_' + r.ts, kind: 'search', icon: '🔎', text: 'Searching the web', chips: [], ts: r.ts };
          pills.push(host);
        }
        _addChip(host, { name: meta.name, icon: meta.icon, url: r.url, title: r.detail || r.url });
        continue;
      }
      const d = _syxFriendly(r);
      if (!d) continue;
      const last = pills[pills.length - 1];
      if (last && last.text === d.text) {
        if (last.chips && d.chipFromProvider) _addChip(last, _chipForProvider(d.chipFromProvider));
        continue;
      }
      const pill = {
        key: r.kind + '_' + r.ts, kind: r.kind, icon: d.icon, text: d.text,
        // The subject is the query behind "Searching: …", the action behind a
        // connector row — the specific thing this step is working on, which is
        // what makes the ladder readable instead of a list of verbs.
        sub: d.sub || _stepSubject(r, d), ts: r.ts, terminal: !!d.terminal,
        connector: !!d.connector, brand: d.brand || null,
      };
      if (d.collect) {
        pill.chips = [];
        if (d.chipFromProvider) _addChip(pill, _chipForProvider(d.chipFromProvider));
      }
      pills.push(pill);
    }
    // Every stage the turn actually took, in order. The caller decides what
    // to show; this is the record.
    return pills;
  }

  // ── Clean timeline view — <SyrixIntelTimeline rows={rows} working={bool} />
  const _SYX_TIMELINE_MAX = 6;
  function SyrixIntelTimeline(props) {
    const rows = (props && props.rows) || [];
    const working = !!(props && props.working);
    // Stagger only the batch present on first paint; pills that stream in
    // later animate immediately (no dead delay before they appear).
    const initialCountRef = useRef(-1);
    _ensureTraceStyles();
    // This one is a horizontal strip, not a ladder, so it shows the newest few
    // rather than everything.
    const pills = _friendlyRows(rows).slice(-_SYX_TIMELINE_MAX);
    if (initialCountRef.current < 0 && pills.length) initialCountRef.current = pills.length;
    if (!pills.length) return null;
    return h('div', { className: 'syx-tl syrix-intel-timeline' },
      pills.map(function (p, i) {
        const isActive = working && i === pills.length - 1 && !p.terminal;
        return h('div', {
          key: p.key,
          className: 'syx-tl-pill' + (isActive ? ' is-active' : ' is-settled')
            + (p.connector ? ' is-connector' : ''),
          style: (initialCountRef.current > 0 && i < initialCountRef.current)
            ? { animationDelay: (i * 0.06) + 's' } : undefined,
        },
          p.brand ? _syxBrandBadge(p.brand)
                  : h('span', { className: 'syx-tl-ico' }, p.icon),
          h('span', { className: 'syx-tl-txt' }, p.text),
          p.sub ? h('span', { className: 'syx-tl-sub' }, p.sub) : null,
          (p.chips && p.chips.length) ? h('span', { className: 'syx-tl-chips' },
            p.chips.map(function (c, j) {
              const link = !!c.url;
              return h('span', {
                key: c.name + '_' + j,
                className: 'syx-tl-chip' + (link ? ' is-link' : ''),
                title: c.title || c.name,
                onClick: link ? function (e) { e.stopPropagation(); _openSource(c.url, c.name); } : undefined,
              }, c.icon + ' ' + c.name);
            }),
            p.moreChips ? h('span', { className: 'syx-tl-chip', key: 'more' }, '+' + p.moreChips) : null
          ) : null
        );
      })
    );
  }

  // ── Live trace ladder — drop <SyrixLiveTrace working={bool} /> in any shell.
  function SyrixLiveTrace(props) {
    const working = !!(props && props.working);
    const hook = SyrixActivityFeedHook();
    const events = hook.events || [];
    const [, _force] = useState(0);
    _ensureTraceStyles();

    // Track when the current working session started so we never show events
    // from a previous question at the start of a new one. Updated synchronously
    // on the first render where working transitions false→true.
    const prevWorkingRef = useRef(false);
    const workingStartRef = useRef(0);
    if (working && !prevWorkingRef.current) {
      workingStartRef.current = Date.now() / 1000;
    }
    prevWorkingRef.current = working;

    const turn = _currentTurnRows(events);
    const rows = turn.rows;

    // Only treat events as belonging to this turn if they arrived at or after
    // the moment working started (1s grace window covers clock skew and the
    // ~1.4s bridge poll delay). This prevents stale events from the previous
    // question showing while we wait for the first new activity_feed entry.
    const sessionStart = workingStartRef.current;
    const eventsAreFresh = !working || sessionStart === 0 || turn.newestTs >= sessionStart - 1.0;

    const recent = !!(turn.newestTs && (Date.now() / 1000 - turn.newestTs) < 1.5);
    // The logo is the immediate acknowledgement that a turn was accepted.
    // Activity rows arrive on a slower mailbox/poll path, so they must not be
    // allowed to delay or randomly hide the existing Thinking animation.
    const visible = working || (!working && recent);
    useEffect(function () {
      if (!visible) return undefined;
      const id = setTimeout(function () { _force(function (x) { return (x + 1) % 1000000; }); }, 800);
      return function () { clearTimeout(id); };
    }, [visible, turn.newestTs]);
    if (!visible) return null;
    if (!rows.length) return null;
    // Clean mode → premium friendly timeline; dev/legacy mode → raw ladder.
    if (hook.cleanMode) {
      return h(SyrixIntelTimeline, { rows: rows, working: working });
    }
    return h('div', { className: 'syx-trace syrix-live-trace', style: liveStyles.wrap },
      h('div', { style: liveStyles.rail }),
      rows.map(function (r, i) {
        const meta = metaFor(r.kind);
        const isHead = i === rows.length - 1;
        const isSource = r.kind === 'source' && r.url;
        return h('div', {
          key: r.ts + '_' + i,
          className: 'syx-trace-rung' + (isHead ? ' is-head' : '') + (isSource ? ' syx-trace-src' : ''),
          onClick: isSource ? function (e) { e.stopPropagation(); _openSource(r.url, r.detail); } : undefined,
          title: isSource ? r.url : (r.detail || r.label),
          style: Object.assign({}, liveStyles.rung, { cursor: isSource ? 'pointer' : 'default', animationDelay: (i * 0.04) + 's' }),
        },
          h('span', { className: 'syx-trace-bullet', style: Object.assign({}, liveStyles.bullet, { background: meta.color, boxShadow: isHead ? ('0 0 8px ' + meta.color) : ('0 0 4px ' + meta.color + '88') }) }),
          h('span', { style: Object.assign({}, liveStyles.label, isHead ? { color: meta.color } : {}) }, meta.icon + ' ' + r.label),
          r.detail ? h('span', { style: liveStyles.detail }, String(r.detail).slice(0, 72)) : null,
          isSource ? h('span', { style: liveStyles.openIco }, '↗') : null
        );
      })
    );
  }

  // ════════════════════════════════════════════════════════════════
  // LIQUID-GLASS ICON SET (2026-07-11) — minimal single-stroke glyphs built
  // from primitives (circle/line/polyline/polygon), never emoji. One shared
  // icon language for SPARK + ZENITH thinking panels. Exposed on window so
  // syrix-zenith-panel.jsx (a separate file, loaded after this one) can draw
  // from the exact same set.
  // ════════════════════════════════════════════════════════════════
  function SyrixLiquidIcon(props) {
    const kind = String((props && props.kind) || '').toLowerCase();
    const size = (props && props.size) || 12.5;
    const common = {
      width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
      stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round',
    };
    switch (kind) {
      case 'intent':
        return h('svg', common, h('circle', { cx: 12, cy: 12, r: 7 }), h('circle', { cx: 12, cy: 12, r: 2, fill: 'currentColor', stroke: 'none' }));
      case 'plan':
        return h('svg', common,
          h('circle', { cx: 6, cy: 17, r: 1.6, fill: 'currentColor', stroke: 'none' }),
          h('circle', { cx: 18, cy: 7, r: 1.6, fill: 'currentColor', stroke: 'none' }),
          h('line', { x1: 7.5, y1: 15.7, x2: 16.5, y2: 8.3, strokeDasharray: '2.2 2.4' }));
      case 'brain':
        // Two lobes and a stem — a mind, not a sparkle. The old four-line star
        // read as "magic" next to steps that are anything but.
        return h('svg', common,
          h('path', { d: 'M12 5.4a3 3 0 0 0-5.4 1.5A2.7 2.7 0 0 0 5.4 11a2.8 2.8 0 0 0 1.1 4.3A2.7 2.7 0 0 0 12 16.3z' }),
          h('path', { d: 'M12 5.4a3 3 0 0 1 5.4 1.5A2.7 2.7 0 0 1 18.6 11a2.8 2.8 0 0 1-1.1 4.3A2.7 2.7 0 0 1 12 16.3z' }),
          h('line', { x1: 12, y1: 5.4, x2: 12, y2: 19 }));
      case 'tool':
        return h('svg', common, h('polygon', { points: '12,3.3 18.8,7.15 18.8,16.85 12,20.7 5.2,16.85 5.2,7.15' }), h('circle', { cx: 12, cy: 12, r: 3 }));
      case 'search':
        return h('svg', common, h('circle', { cx: 10, cy: 10, r: 6 }), h('line', { x1: 14.5, y1: 14.5, x2: 20, y2: 20 }));
      case 'filter':
        return h('svg', common, h('polyline', { points: '4,4.5 20,4.5 13.5,12 13.5,19 10.5,19 10.5,12' }));
      case 'check':
        return h('svg', common, h('circle', { cx: 12, cy: 12, r: 7.8 }), h('polyline', { points: '8.2,12.3 11,15.2 15.8,9.2' }));
      case 'write':
        return h('svg', common, h('line', { x1: 5, y1: 19, x2: 14.5, y2: 9.5 }), h('polygon', { points: '14.5,9.5 17,7 19,9 16.5,11.5', fill: 'currentColor', stroke: 'none' }));
      case 'save':
        return h('svg', common, h('path', { d: 'M7 3.6h10a1 1 0 0 1 1 1V20l-6-4-6 4V4.6a1 1 0 0 1 1-1z' }));
      case 'done':
        return h('svg', common, h('polyline', { points: '4,12.6 9,17.6 20,6.4' }));
      case 'warn':
        return h('svg', common, h('path', { d: 'M12 3.6l8.6 15H3.4z' }), h('line', { x1: 12, y1: 10, x2: 12, y2: 13.6 }), h('circle', { cx: 12, cy: 16.6, r: 0.85, fill: 'currentColor', stroke: 'none' }));
      case 'bolt':
        return h('svg', common, h('polygon', { points: '13,2.2 5.2,13.6 11,13.6 9.2,21.8 18.8,9 12,9' }));
      case 'layers':
        return h('svg', common, h('rect', { x: 4, y: 8, width: 11, height: 11, rx: 2.4, opacity: 0.5 }), h('rect', { x: 9, y: 4, width: 11, height: 11, rx: 2.4 }));
      case 'loop':
        return h('svg', common, h('path', { d: 'M5 12a7 7 0 0 1 12-4.9M19 12a7 7 0 0 1-12 4.9' }),
          h('polyline', { points: '17,4.6 17,7.6 14,7.6' }), h('polyline', { points: '7,19.4 7,16.4 10,16.4' }));
      case 'globe':
        return h('svg', common, h('circle', { cx: 12, cy: 12, r: 7.8 }), h('line', { x1: 4.2, y1: 12, x2: 19.8, y2: 12 }),
          h('path', { d: 'M12 4.2c2.5 2.2 2.5 13.4 0 15.6M12 4.2c-2.5 2.2-2.5 13.4 0 15.6' }));
      case 'page':
        return h('svg', common, h('rect', { x: 6, y: 3, width: 12, height: 18, rx: 1.6 }),
          h('line', { x1: 9, y1: 8, x2: 15, y2: 8 }), h('line', { x1: 9, y1: 12, x2: 15, y2: 12 }), h('line', { x1: 9, y1: 16, x2: 13, y2: 16 }));
      default:
        return h('svg', common,
          h('circle', { cx: 6.5, cy: 12, r: 1.35, fill: 'currentColor', stroke: 'none' }),
          h('circle', { cx: 12, cy: 12, r: 1.35, fill: 'currentColor', stroke: 'none' }),
          h('circle', { cx: 17.5, cy: 12, r: 1.35, fill: 'currentColor', stroke: 'none' }));
    }
  }
  window.SyrixLiquidIcon = SyrixLiquidIcon;

  // Pill kind → liquid icon glyph (separate from _syxFriendly's emoji, which
  // the Inspect panel still uses — this mapping is only for the frameless
  // thinking panels below).
  const _LIQUID_ICON_KIND = {
    intent: 'intent', plan: 'plan', brain_route: 'brain', mode: 'brain',
    brain_fallback: 'loop', brain_restored: 'check',
    tool_call: 'tool', search: 'search', retrieval: 'search', context_init: 'globe',
    filter_rank: 'brain', verify: 'check', pack_built: 'check', pack_enriched: 'check',
    spark_run: 'bolt', zenith_run: 'layers', synthesis: 'write',
    memory: 'save', done: 'done', error: 'warn',
  };
  function _liquidIconFor(kind) { return _LIQUID_ICON_KIND[kind] || 'dots'; }

  // ════════════════════════════════════════════════════════════════
  // SPARK THINKING PANEL — frameless, no card, no rectangle. A "Thinking"
  // headline under the animated SYRIX mark, and beneath it the real work as
  // a quiet grey ladder: one line per step, each with a minimal line icon
  // (a lens for a search, the mind glyph for evaluation, a globe for a page
  // being read, a tick for a verification) and the thing it is working on
  // beside it.
  //
  // Every line is a rid-scoped activity_feed event. There is no synthetic
  // progress and no timer-driven filler: if the backend has not said it is
  // doing something, nothing claims that it is.
  //
  // Why a ladder and not a disclosure arrow: the steps are the answer to
  // "what is it doing", which is the question being asked during the only
  // moment this panel exists. Hiding them behind a control made the default
  // state a bare word.
  // ════════════════════════════════════════════════════════════════
  // No cap. The ladder is append-only and every line is a real stage the turn
  // went through, so trimming it means a step that happened stops being shown
  // while the turn is still running — the same "it vanished" the accumulator
  // exists to prevent, just arriving later.

  // The thinking ladder's accumulated steps for the turn in flight. Reset only
  // when the request id changes; see SyrixSparkPulse for why it is append-only.
  const _LADDER = { rid: null, steps: [], seen: {}, live: '' };

  let _sparkStylesInjected = false;
  function _ensureSparkStyles() {
    if (_sparkStylesInjected) return;
    _sparkStylesInjected = true;
    try {
      const el = document.createElement('style');
      el.id = 'syrix-spark-pulse-styles';
      el.textContent = [
        '@keyframes syxSparkFade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}',
        '@keyframes syxSparkOut{from{opacity:1;transform:none}to{opacity:0;transform:translateY(-3px)}}',
        '@keyframes syxSparkSheen{0%{background-position:165% 0}100%{background-position:-65% 0}}',
        '@keyframes syxSparkDrift{0%,100%{transform:scale(1);filter:drop-shadow(0 0 4px rgba(var(--eg-accent-rgb),.25))}50%{transform:scale(1.05);filter:drop-shadow(0 0 10px rgba(var(--eg-accent-rgb),.45))}}',
        /* Soft outward "ping" behind the mark, on its own clock (2.6s) so it
           drifts in and out of phase with the sheen/drift instead of repeating
           one beat — it reads as alive rather than as a metronome. It lives on
           the wrapper, not the mark, because the mark's own mask would clip a
           ping drawn on itself. */
        '@keyframes syxSparkPing{0%{opacity:0;transform:translateY(-50%) scale(.55)}18%{opacity:.5}65%{opacity:0;transform:translateY(-50%) scale(1.4)}100%{opacity:0;transform:translateY(-50%) scale(1.4)}}',
        '@keyframes syxStepIn{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}',
        '@keyframes syxStepBreath{0%,100%{opacity:.62}50%{opacity:1}}',
        /* The active line's text carries a slow highlight sweep — the one
           signal that says "this is the step happening right now" without
           adding a spinner or a second colour. */
        '@keyframes syxStepSweep{0%{background-position:210% 0}100%{background-position:-60% 0}}',

        '.syx-think{display:flex;flex-direction:column;gap:2px;margin:6px 0 2px;animation:syxSparkFade .3s ease}',
        '.syx-think.is-closing{animation:syxSparkOut .22s ease forwards}',

        /* Headline — the mark is a CSS mask, so the silhouette comes from the
           artwork's alpha while the fill is an animated gold sheen. */
        '.syx-think-head{position:relative;display:inline-flex;align-items:center;gap:10px}',
        '.syx-think-head::before{content:"";position:absolute;left:0;top:50%;width:23px;height:23px;border-radius:50%;background:radial-gradient(circle,rgba(var(--eg-accent-surface-rgb),.4),transparent 72%);opacity:0;animation:syxSparkPing 2.6s ease-out infinite;pointer-events:none;z-index:0}',
        '.syx-think-mark{position:relative;z-index:1;display:inline-block;width:23px;height:23px;flex-shrink:0;-webkit-mask:url(../assets/icons/thinking-syrix.png) center/contain no-repeat;mask:url(../assets/icons/thinking-syrix.png) center/contain no-repeat;background:linear-gradient(112deg,rgba(var(--eg-accent-surface-deep-rgb),.55) 0%,rgba(var(--eg-accent-surface-rgb),.92) 32%,var(--eg-accent-hi, #fff8e8) 48%,rgba(var(--eg-accent-surface-rgb),.92) 64%,rgba(var(--eg-accent-surface-deep-rgb),.55) 100%);background-size:260% 100%;animation:syxSparkSheen 2.6s linear infinite,syxSparkDrift 3.4s ease-in-out infinite}',
        '.syx-think-title{font:500 12.5px Inter,system-ui,sans-serif;letter-spacing:.01em;color:rgba(244,237,226,.82)}',

        /* Step ladder — indented to the headline's text column so it reads as
           detail under "Thinking", not as a second status. */
        '.syx-think-steps{display:flex;flex-direction:column;gap:1px;margin:4px 0 2px 33px;max-width:560px}',
        '.syx-think-step{display:flex;align-items:center;gap:8px;min-height:19px;padding:1px 0;animation:syxStepIn .3s cubic-bezier(.32,.72,0,1) both}',
        '.syx-think-ico{width:14px;height:14px;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:rgba(var(--eg-accent-rgb),.42)}',
        '.syx-think-txt{font:400 11.5px Inter,system-ui,sans-serif;letter-spacing:.005em;color:rgba(244,237,226,.40);white-space:nowrap}',
        '.syx-think-sub{font:400 11.5px Inter,system-ui,sans-serif;color:rgba(244,237,226,.26);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}',
        '.syx-think-sub::before{content:"·";margin-right:6px;opacity:.7}',
        /* Finished steps fade back; the oldest visible one fades furthest so
           the ladder has a sense of depth instead of a hard cut at the top. */
        '.syx-think-step.is-faded{opacity:.5}',
        '.syx-think-step.is-active .syx-think-ico{color:var(--eg-gold-bright);animation:syxStepBreath 1.9s ease-in-out infinite}',
        '.syx-think-step.is-active .syx-think-txt{color:transparent;background:linear-gradient(100deg,rgba(244,237,226,.52) 0%,rgba(244,237,226,.52) 36%,var(--eg-accent-hi, #fff6e2) 50%,rgba(244,237,226,.52) 64%,rgba(244,237,226,.52) 100%);background-size:240% 100%;-webkit-background-clip:text;background-clip:text;animation:syxStepSweep 2.4s linear infinite}',
        '.syx-think-step.is-active .syx-think-sub{color:rgba(244,237,226,.42)}',
        /* Connector steps keep their brand tile so it is always visible WHICH
           service is being touched. */
        '.syx-think-step.is-connector .syx-think-ico{color:inherit}',
        '.syx-think-brand{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;border-radius:4px;color:#fff;font-weight:800;font-family:Inter,system-ui,sans-serif;line-height:1;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18);flex-shrink:0}',
        /* Source names on a search line — clickable, still grey. */
        '.syx-think-srcs{display:inline-flex;align-items:center;gap:6px;min-width:0;overflow:hidden}',
        '.syx-think-src{font:400 11px Inter,system-ui,sans-serif;color:rgba(244,237,226,.28);white-space:nowrap;background:none;border:none;padding:0;cursor:pointer;transition:color .14s ease}',
        '.syx-think-src.is-link:hover{color:var(--eg-gold-bright)}',

        '@media (prefers-reduced-motion: reduce){.syx-think-head::before,.syx-think-mark,.syx-think-step,.syx-think-step.is-active .syx-think-ico,.syx-think-step.is-active .syx-think-txt{animation:none!important}.syx-think-step.is-active .syx-think-txt{color:rgba(244,237,226,.72);background:none;-webkit-text-fill-color:currentColor}}',
      ].join('\n');
      document.head.appendChild(el);
    } catch (_) {}
  }

  /* The page SYRIX is fetching RIGHT NOW, or '' when it is not fetching.

     Only a live fetch counts. The backend emits `web_domain` immediately
     before it opens a page (syrix_web/audit.py::searching), so this tracks
     what is happening rather than what already happened; a source event that
     arrives after the read is the past and is deliberately ignored. The 6s
     window means a finished fetch stops showing even if nothing else has
     landed in the feed since. */
  function _searchingDomain(events) {
    const now = Date.now() / 1000;
    for (let i = (events || []).length - 1; i >= 0; i--) {
      const ev = events[i] || {};
      const domain = String(ev.web_domain || '');
      if (!domain) continue;
      if (now - Number(ev.ts || 0) > 6) return '';
      return domain;
    }
    return '';
  }

  /* Pull the thing a step is working on out of its backend label/detail —
     the query behind "Searching: nvidia earnings", the action behind a
     connector row. Returns '' rather than echoing the whole label back,
     which would print the friendly text twice on the same line. */
  function _stepSubject(row, friendly) {
    const detail = String((row && row.detail) || '').trim();
    const label = String((row && row.label) || '').trim();
    let out = friendly && friendly.sub ? String(friendly.sub) : '';
    if (!out && detail && detail !== label) out = detail;
    if (!out) {
      const m = label.match(/^[^:]{1,28}:\s*(.+)$/);
      if (m) out = m[1];
    }
    out = out.trim();
    if (!out) return '';
    if (friendly && String(friendly.text || '').toLowerCase() === out.toLowerCase()) return '';
    return out.length > 64 ? out.slice(0, 63) + '…' : out;
  }

  function _thinkStep(props) {
    const p = props.pill;
    const cls = 'syx-think-step'
      + (props.active ? ' is-active' : '')
      + (props.faded ? ' is-faded' : '')
      + (p.connector ? ' is-connector' : '');
    const srcs = (p.chips || []).slice(0, 3);
    return h('div', { className: cls, style: { animationDelay: props.delay + 's' } },
      h('span', { className: 'syx-think-ico' },
        p.brand ? h('span', {
          className: 'syx-think-brand',
          style: { background: p.brand.bg, fontSize: (String(p.brand.mark).length > 1 ? '7px' : '9px') },
        }, p.brand.mark)
          : h(SyrixLiquidIcon, { kind: _liquidIconFor(p.kind), size: 13 })),
      h('span', { className: 'syx-think-txt' }, p.text),
      p.sub ? h('span', { className: 'syx-think-sub' }, p.sub) : null,
      srcs.length ? h('span', { className: 'syx-think-srcs' },
        srcs.map(function (c, j) {
          const link = !!c.url;
          return h('button', {
            key: c.name + '_' + j,
            type: 'button',
            className: 'syx-think-src' + (link ? ' is-link' : ''),
            title: c.title || c.name,
            onClick: link ? function (e) { e.stopPropagation(); _openSource(c.url, c.name); } : undefined,
          }, c.name);
        }),
        p.moreChips ? h('span', { className: 'syx-think-src' }, '+' + p.moreChips) : null
      ) : null
    );
  }

  function SyrixSparkPulse(props) {
    const working = !!(props && props.working);
    const closing = !!(props && props.closing);
    const hook = SyrixActivityFeedHook();
    const events = hook.events || [];
    const [, _force] = useState(0);
    _ensureSparkStyles();

    const prevWorkingRef = useRef(false);
    const workingStartRef = useRef(0);
    if (working && !prevWorkingRef.current) {
      workingStartRef.current = Date.now() / 1000;
    }
    prevWorkingRef.current = working;

    const turn = _currentTurnRows(events);
    const rows = turn.rows;
    const sessionStart = workingStartRef.current;

    /* ── The ladder ACCUMULATES; it never re-derives itself ────────────────
       The activity feed is a rolling window, so reading the panel straight
       off it meant a step could be present on one render and gone on the
       next: the ladder appeared, emptied, refilled, and replayed the same
       few lines in a loop, sometimes showing a stage from the previous
       question. That is a display artefact, not something SYRIX was doing.

       So this keeps its own ordered list, keyed on the request id: a step is
       appended the first time it is seen and then stays put for the rest of
       the turn. A new question resets it — that is the ONLY thing that
       removes a line. No re-ordering, no disappearing, no repeats.

       `working` is deliberately not part of the reset condition. It flickers
       false for a frame between the last token and the answer landing, and
       treating that as a new turn is what made the panel blink. */
    // Module-level, not a ref: the shell unmounts this panel for a beat when a
    // token stream takes over and remounts it afterwards, and a ref would take
    // the accumulated ladder with it — the exact "it vanished and started
    // again" the accumulator exists to stop. Only one thinking panel is ever
    // on screen, so a single cache is the whole of the state.
    const acc = _LADDER;

    /* A turn boundary is a DIFFERENT, NON-EMPTY request id — nothing else.

       The shell passes the id it sent the turn with (`turnId`), which is the
       authoritative answer; the feed's own id is the fallback for untagged
       sends. Deriving it from the feed alone was a second source of blinking:
       between rounds the feed can hold no tagged event for a moment, the id
       read as empty, and an empty id compared unequal to the real one — so the
       ladder wiped itself mid-turn and built up again from nothing.

       An absent id now means "no new information", never "new turn". */
    const turnKey = String((props && props.turnId) || turn.rid || '');
    if (turnKey && turnKey !== acc.rid) {
      acc.rid = turnKey;
      acc.steps = [];
      acc.seen = {};
      acc.live = '';
    }

    // Routing mechanics (intent / plan / brain_route / synthesis) fire on
    // EVERY turn, including a one-word "hi". They are shown once real work is
    // under way, but they are not on their own a reason to show a ladder.
    const eventsAreFresh = !working || sessionStart === 0 || rows.length === 0 || turn.newestTs >= sessionStart - 1.0;
    if (eventsAreFresh && _hasWorthyActivity(rows)) {
      const pills = _friendlyRows(rows);
      for (let i = 0; i < pills.length; i++) {
        const p = pills[i];
        const at = acc.seen[p.key];
        if (at !== undefined) {
          // Same step, richer detail (another source chip landed) — update it
          // in place so the line grows instead of the list churning.
          acc.steps[at] = p;
          continue;
        }
        /* Different backend stage, same thing to say. `search` and `retrieval`
           both read "Searching the web", and across frames they arrive as two
           events with two keys — which stacked as two identical lines and read
           as the panel repeating itself. _friendlyRows already merges these
           when they land in one frame; the ladder has to do the same across
           frames, because the feed hands them over one frame at a time.

           Only the LAST line is merged into: a second search after other work
           is a real second round and earns its own row. */
        const tail = acc.steps.length - 1;
        if (tail >= 0 && acc.steps[tail].text === p.text) {
          const merged = acc.steps[tail];
          if (merged.chips && p.chips) {
            for (let c = 0; c < p.chips.length; c++) _addChip(merged, p.chips[c]);
          } else if (p.chips) {
            merged.chips = p.chips;
          }
          if (p.sub && !merged.sub) merged.sub = p.sub;
          acc.seen[p.key] = tail;
          continue;
        }
        acc.seen[p.key] = acc.steps.length;
        acc.steps.push(p);
      }
    }

    // The page being fetched right now. It is ONE trailing line whose text
    // changes as the fetch moves on, never a new line per page.
    if (!closing) {
      const d = _searchingDomain(events);
      if (d) acc.live = d;
    } else {
      acc.live = '';
    }

    /* Visibility is the SHELL's decision, not this component's.

       The shell mounts this panel for the length of a turn and unmounts it
       once, with a fade. Second-guessing that from the feed's timestamps gave
       the panel an independent opinion about when it should exist — a second
       thing that could blink, fighting the first. It now renders whenever it
       is mounted and leaves the lifetime to its parent. */
    // The live-fetch line has a 6s life of its own, so the panel re-renders on
    // a slow beat to retire it even when no new event has arrived.
    useEffect(function () {
      const id = setTimeout(function () { _force(function (x) { return (x + 1) % 1000000; }); }, 800);
      return function () { clearTimeout(id); };
    }, [turn.newestTs]);

    // Every step, in the order it happened. Append-only, so a row that is on
    // screen keeps its identity and never re-animates.
    const steps = acc.steps;
    const liveDomain = acc.live && !closing && _searchingDomain(events) ? acc.live : '';
    const rendered = steps.map(function (p, i) {
      return { pill: p, key: p.key, active: working && i === steps.length - 1 && !p.terminal && !liveDomain };
    });
    if (liveDomain) {
      rendered.push({
        key: 'live_fetch',
        pill: { kind: 'context_init', text: 'Reading', sub: liveDomain, chips: [] },
        active: working,
      });
    }
    const shown = rendered;

    return h('div', { className: 'syx-think' + (closing ? ' is-closing' : ''), 'aria-live': 'polite' },
      h('div', { className: 'syx-think-head' },
        h('span', { className: 'syx-think-mark', 'aria-hidden': true }),
        h('span', { className: 'syx-think-title' }, 'Thinking')
      ),
      shown.length ? h('div', { className: 'syx-think-steps' },
        shown.map(function (s, i) {
          return h(_thinkStep, {
            key: s.key,
            pill: s.pill,
            active: s.active,
            faded: false,
            delay: Math.min(i, 4) * 0.04,
          });
        })
      ) : null
    );
  }

  // ── Per-answer inspector — drop <SyrixAnswerTrace msg={m} /> on click.
  const _WHERE = { web: 'searched the web', memory: 'used your memory', tool: 'ran a tool', brain: 'reasoned with the model' };
  const _KIND_WHERE = { search: 'web', source: 'web', tool_call: 'tool', memory: 'memory', brain_route: 'brain', synthesis: 'brain' };
  function _cleanBrain(p) {
    let s = String(p || '').trim();
    if (!s) return '';
    s = s.replace(/^Routing to /i, '');
    return s;
  }
  // Display names for the Inspect panel's Provider row (2026-07-08) — the
  // backend logs lowercase provider keys (groq/ollama/cerebras/...).
  const _PROVIDER_DISPLAY = {
    groq: 'Groq', ollama: 'Ollama', cerebras: 'Cerebras',
    openrouter: 'OpenRouter', github: 'GitHub Models', nim: 'NVIDIA NIM',
    brain_pool: 'Auto (pool)', syrix_host_brain: 'Auto', cache: 'Cache',
  };
  function _providerDisplay(p) {
    const clean = _cleanBrain(p);
    if (!clean) return '';
    return _PROVIDER_DISPLAY[clean.toLowerCase()] || clean;
  }
  // Friendly model names for the Inspect panel (2026-07-08):
  // "openai/gpt-oss-20b" → "GPT-OSS 20B", "llama-3.3-70b-versatile" →
  // "Llama 3.3 70B", "qwen2.5:7b-instruct" → "Qwen 2.5 7B". Raw id stays in
  // the row's title tooltip. Unknown ids pass through mostly untouched.
  function _modelDisplay(m) {
    let s = String(m || '').trim();
    if (!s) return '';
    const raw = s;
    s = s.split('/').pop().toLowerCase();
    s = s.replace(':', ' ');
    s = s.replace(/-(versatile|instant|instruct|chat|preview)\b/g, '');
    s = s.replace(/\bgpt-oss[- ]?/, 'GPT-OSS ');
    s = s.replace(/\bllama[- ]?/, 'Llama ');
    s = s.replace(/\bqwen[- ]?/, 'Qwen ');
    s = s.replace(/\bmixtral[- ]?/, 'Mixtral ');
    s = s.replace(/\bgemma[- ]?/, 'Gemma ');
    s = s.replace(/\bturbo\b/g, 'Turbo');
    s = s.replace(/(\d+(?:\.\d+)?)[- ]?b\b/g, '$1B');
    s = s.replace(/[-_]+/g, ' ').replace(/\s{2,}/g, ' ').trim();
    return s || raw;
  }
  function SyrixAnswerTrace(props) {
    const msg = (props && props.msg) || {};
    const hook = SyrixActivityFeedHook();
    const events = hook.events || [];
    const [logOpen, setLogOpen] = useState(false);
    _ensureTraceStyles();
    const rid = String(msg.request_id || '').trim();
    // Live activity-feed events for this request; for a REOPENED old message the
    // live ring no longer holds them, so fall back to the steps the backend
    // persisted onto the message (msg.steps).
    let steps = rid ? events.filter(function (e) { return e && String(e.request_id || '') === rid; }) : [];
    if (!steps.length && Array.isArray(msg.steps)) steps = msg.steps;
    // EXACT brains used (Groq / Ollama / NIM · model), incl. fallbacks, as a
    // chain — not just a single "auto" label.
    const brainsUsed = Array.isArray(msg.brains_used) ? msg.brains_used : [];
    const brainChain = brainsUsed.map(function (b) {
      // Prefer the exact brain identity ("Groq · GPT-OSS 20B") over the
      // event label whenever the model is known (2026-07-08).
      const bProv = _providerDisplay(b.provider);
      const bModel = _modelDisplay(b.model);
      let lbl = bModel ? ((bProv ? bProv + ' · ' : '') + bModel)
        : (_cleanBrain(b.label || b.provider) || String(b.provider || '').trim());
      return (b.fallback ? '↳ ' : '') + (lbl || 'brain');
    }).filter(Boolean).join('  →  ');
    let brain = brainChain || _cleanBrain(msg.provider || msg.brain);
    if (!brain) brain = _cleanBrain(steps.reduce(function (a, e) { return a || (String(e.kind) === 'brain_route' ? String(e.provider || e.label || '') : ''); }, ''));
    brain = brain || 'auto';
    // Provider/model/fallback breakdown (2026-07-08) — the real backend brain
    // that generated THIS reply, not just the Auto/Spark/Zenith view toggle.
    // failedEntries = attempts that actually errored out (brain_fallback
    // events); wonEntry = the one that ultimately produced the answer (last
    // entry in the chain — brain_route events, success only).
    const failedEntries = brainsUsed.filter(function (b) { return b.fallback && !b.model; });
    const routeEntries = brainsUsed.filter(function (b) { return !!b.model; });
    const wonEntry = routeEntries.length ? routeEntries[routeEntries.length - 1]
      : (brainsUsed.length ? brainsUsed[brainsUsed.length - 1] : null);
    const hadFallback = failedEntries.length > 0 || !!(wonEntry && wonEntry.fallback);
    const finalProvider = _providerDisplay((wonEntry && wonEntry.provider) || msg.provider || '') || brain;
    const finalModel = String((wonEntry && wonEntry.model) || msg.model || '').trim();
    const originalEntry = failedEntries[0] || null;
    const originalProvider = originalEntry ? _providerDisplay(originalEntry.provider || originalEntry.label) : '';
    const failReason = originalEntry ? String(originalEntry.reason || originalEntry.detail || '').trim() : '';
    const wheres = new Set();
    steps.forEach(function (e) { const w = _KIND_WHERE[String(e.kind || '').toLowerCase()]; if (w) wheres.add(w); });
    const whereSummary = Array.from(wheres).map(function (w) { return _WHERE[w] || w; }).join(' · ') || 'answered from the model';
    const srcMap = new Map();
    steps.forEach(function (e) { if (String(e.kind) === 'source' && e.source_url) srcMap.set(e.source_url, e.source_title || e.detail || e.source_url); });
    // Presentation V2 (2026-07-05): inspect is the ONLY place evidence renders —
    // include the Zenith deep-layer sources alongside the message's own.
    const deepSrcs = (msg.deep && typeof msg.deep === 'object' && Array.isArray(msg.deep.sources)) ? msg.deep.sources : [];
    (Array.isArray(msg.sources) ? msg.sources : []).concat(deepSrcs)
      .forEach(function (s) { const u = s && (s.url || s.href); if (u) srcMap.set(u, s.name || s.title || u); });
    const sources = Array.from(srcMap.entries()).slice(0, 12);
    const stepRows = steps.filter(function (e) { return String(e.kind) !== 'source' && String(e.label || '').trim(); });
    const route = String(msg.route_label || msg.intent || '').trim();
    const conf = (typeof msg.confidence === 'number') ? Math.round(msg.confidence * 100) : null;
    const logText = typeof msg.log_excerpt === 'string' ? msg.log_excerpt : '';
    // Clean mode (2026-07-10): inspect shows only friendly information — what
    // SYRIX did, its steps as timeline pills, sources, confidence as words.
    // The raw panel below (Provider/Model/Route/Backend log) stays intact and
    // returns with localStorage syrix_dev_trace='1'.
    if (hook.cleanMode) {
      const confWord = (conf == null) ? '' : (conf >= 80 ? 'High' : (conf >= 55 ? 'Medium' : 'Low'));
      const friendlySteps = _friendlyRows(steps.map(function (e) {
        return {
          kind: String(e.kind || '').toLowerCase(), label: String(e.label || ''),
          detail: String(e.detail || ''), url: String(e.source_url || ''),
          ts: Number(e.ts || 0), provider: String(e.provider || ''),
        };
      }));
      return h('div', { className: 'syx-inspect syrix-answer-trace', style: inspStyles.wrap, onClick: function (e) { e.stopPropagation(); } },
        h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'How'), h('span', { style: inspStyles.val }, whereSummary)),
        confWord ? h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'Confidence'), h('span', { style: inspStyles.val }, confWord)) : null,
        friendlySteps.length ? h('div', { style: inspStyles.steps },
          h('div', { style: Object.assign({}, inspStyles.key, { marginBottom: 5 }) }, 'What SYRIX did'),
          friendlySteps.map(function (p, i) {
            return h('div', { key: i, style: inspStyles.step },
              h('span', { style: { flexShrink: 0 } }, p.icon),
              h('span', { style: inspStyles.slabel }, p.text + (p.sub ? ' — ' + p.sub : '')));
          })) : null,
        sources.length ? h('div', { style: { marginTop: 8 } },
          h('div', { style: Object.assign({}, inspStyles.key, { marginBottom: 4 }) }, 'Sources (click to open)'),
          sources.map(function (pair, i) {
            const url = pair[0]; const name = pair[1];
            return h('a', {
              key: i, href: url, target: '_blank', rel: 'noopener noreferrer', style: inspStyles.chip, title: url,
              onClick: function (ev) { ev.preventDefault(); ev.stopPropagation(); _openSource(url, name); },
            }, String(name).slice(0, 36));
          })) : null,
        (!friendlySteps.length && !sources.length) ? h('div', { style: Object.assign({}, inspStyles.val, { opacity: 0.6, marginTop: 4 }) }, 'Trace not retained for this message.') : null
      );
    }
    return h('div', { className: 'syx-inspect syrix-answer-trace', style: inspStyles.wrap, onClick: function (e) { e.stopPropagation(); } },
      h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'Provider'), h('span', { style: Object.assign({}, inspStyles.val, { fontWeight: 600 }) }, finalProvider || brain || 'auto')),
      finalModel ? h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'Model'), h('span', { style: inspStyles.val, title: finalModel }, _modelDisplay(finalModel))) : null,
      h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'Fallback'), h('span', { style: inspStyles.val }, hadFallback ? 'Yes' : 'No')),
      hadFallback ? h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'Original'), h('span', { style: inspStyles.val }, originalProvider || 'unknown')) : null,
      (hadFallback && failReason) ? h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'Reason'), h('span', { style: inspStyles.val }, failReason)) : null,
      hadFallback ? h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'Final'), h('span', { style: inspStyles.val, title: finalModel }, finalProvider + (finalModel ? ' · ' + _modelDisplay(finalModel) : ''))) : null,
      route ? h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'Route'), h('span', { style: inspStyles.val }, route)) : null,
      h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'Did'), h('span', { style: inspStyles.val }, whereSummary)),
      (conf != null) ? h('div', { style: inspStyles.row }, h('span', { style: inspStyles.key }, 'Conf'), h('span', { style: inspStyles.val }, conf + '%')) : null,
      stepRows.length ? h('div', { style: inspStyles.steps },
        h('div', { style: Object.assign({}, inspStyles.key, { marginBottom: 5 }) }, 'Steps SYRIX took'),
        stepRows.map(function (e, i) {
          const meta = metaFor(e.kind);
          return h('div', { key: i, style: inspStyles.step },
            h('span', { style: Object.assign({}, inspStyles.sdot, { background: meta.color }) }),
            h('span', null,
              h('span', { style: inspStyles.slabel }, e.label || e.kind),
              e.detail ? h('span', { style: inspStyles.sdetail }, String(e.detail).slice(0, 80)) : null));
        })) : null,
      sources.length ? h('div', { style: { marginTop: 8 } },
        h('div', { style: Object.assign({}, inspStyles.key, { marginBottom: 4 }) }, 'Sources (click to open)'),
        sources.map(function (pair, i) {
          const url = pair[0]; const name = pair[1];
          return h('a', {
            key: i, href: url, target: '_blank', rel: 'noopener noreferrer', style: inspStyles.chip, title: url,
            onClick: function (ev) { ev.preventDefault(); ev.stopPropagation(); _openSource(url, name); },
          }, String(name).slice(0, 36));
        })) : null,
      // Backend log for THIS message — collapsed by default; makes diagnosing
      // errors easy without leaving the chat.
      logText ? h('div', { style: { marginTop: 8, borderTop: '1px solid rgba(255,255,255,.08)', paddingTop: 8 } },
        h('div', {
          style: Object.assign({}, inspStyles.key, { cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6 }),
          onClick: function (e) { e.stopPropagation(); setLogOpen(function (o) { return !o; }); },
          title: 'Backend log lines for this message',
        }, (logOpen ? '▾ ' : '▸ ') + 'Backend log'),
        logOpen ? h('pre', {
          style: { marginTop: 6, maxHeight: 170, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: 'rgba(0,0,0,.34)', borderRadius: 8, border: '1px solid rgba(255,255,255,.08)', padding: '8px 10px', font: "400 10px/1.5 'JetBrains Mono',monospace", color: 'rgba(180,210,180,.92)' },
        }, logText) : null
      ) : null,
      (!stepRows.length && !sources.length && !logText) ? h('div', { style: Object.assign({}, inspStyles.val, { opacity: 0.6, marginTop: 4 }) }, 'Trace not retained for this message.') : null
    );
  }

  // Inline styles (themes may override via .syrix-live-trace / .syrix-answer-trace).
  const liveStyles = {
    wrap: { position: 'relative', margin: '6px 0 2px', paddingLeft: 16, display: 'flex', flexDirection: 'column', gap: 6 },
    rail: { position: 'absolute', left: 4, top: 4, bottom: 4, width: 2, background: 'linear-gradient(180deg, rgba(var(--eg-accent-rgb),.65), rgba(var(--eg-accent-rgb),.04))', borderRadius: 2 },
    rung: { position: 'relative', display: 'flex', alignItems: 'center', gap: 8, padding: '2px 0' },
    bullet: { position: 'absolute', left: -14, top: '50%', transform: 'translateY(-50%)', width: 8, height: 8, borderRadius: '50%', flexShrink: 0 },
    label: { color: 'rgba(246,239,226,.95)', font: "600 11.5px 'Inter',system-ui,sans-serif", whiteSpace: 'nowrap', letterSpacing: '0.01em' },
    detail: { color: 'rgba(200,185,160,.65)', font: "400 10px 'JetBrains Mono',monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 230 },
    openIco: { color: 'var(--eg-gold-bright)', font: '700 11px monospace', marginLeft: 2, opacity: 0.85 },
  };
  const inspStyles = {
    wrap: { marginTop: 8, padding: '10px 12px', borderRadius: 12, background: 'rgba(255,255,255,.05)', border: '1px solid rgba(255,255,255,.12)', fontSize: 11, lineHeight: 1.5 },
    row: { display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 3 },
    key: { color: 'rgba(180,195,215,.62)', font: "600 9px 'JetBrains Mono',monospace", letterSpacing: '.08em', textTransform: 'uppercase', minWidth: 52, flexShrink: 0 },
    val: { color: 'rgba(232,240,252,.92)' },
    steps: { marginTop: 8, borderTop: '1px solid rgba(255,255,255,.08)', paddingTop: 8 },
    step: { display: 'flex', alignItems: 'flex-start', gap: 8, padding: '2px 0' },
    sdot: { width: 7, height: 7, borderRadius: '50%', marginTop: 5, flexShrink: 0 },
    slabel: { color: 'rgba(232,240,252,.9)', font: "500 11px 'Inter',sans-serif" },
    sdetail: { color: 'rgba(180,195,215,.6)', font: "400 10px 'JetBrains Mono',monospace", marginLeft: 6 },
    chip: { display: 'inline-flex', alignItems: 'center', gap: 4, maxWidth: 240, padding: '2px 8px', margin: '3px 4px 0 0', borderRadius: 999, background: 'rgba(var(--eg-accent-rgb),.14)', border: '1px solid rgba(var(--eg-accent-rgb),.4)', color: 'var(--eg-gold-bright)', font: "500 10px 'JetBrains Mono',monospace", cursor: 'pointer', textDecoration: 'none', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  };

  // Snapshot-based clean-mode check for non-hook call sites (island header,
  // zenith panel). Same rules as the hook: localStorage dev flag wins, then
  // hud_flags.clean_timeline (absent = ON).
  function SyrixCleanMode() {
    try {
      const s = window.SyrixBridge && window.SyrixBridge.getSnapshot && window.SyrixBridge.getSnapshot();
      return _cleanModeOn(s && s.raw);
    } catch (_) { return _cleanModeOn(null); }
  }

  window.SyrixActivityFeedHook = SyrixActivityFeedHook;
  window.SyrixActivityFeedPanel = SyrixActivityFeedPanel;
  window.SyrixBrainStatusPill = SyrixBrainStatusPill;
  window.SyrixLiveTrace = SyrixLiveTrace;
  window.SyrixSparkPulse = SyrixSparkPulse;
  window.SyrixAnswerTrace = SyrixAnswerTrace;
  window.SyrixIntelTimeline = SyrixIntelTimeline;
  window.SyrixCleanMode = SyrixCleanMode;
  window.SyrixFriendlyProvider = _providerDisplay;
})();
