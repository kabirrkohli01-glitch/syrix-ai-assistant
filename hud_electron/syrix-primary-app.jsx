/* SYRIX PRIMARY APP — Main HUD state machine + voice */
const { useState, useEffect, useRef, useMemo, useCallback } = React;
const HUD_THEME_SETTINGS_KEY = 'syrix_hud_v4_settings';
const HUD_THEME_AURORA_GLASS = 'aurora-glass';

function normalizeHudTheme(_value) {
  return HUD_THEME_AURORA_GLASS;
}

function isGlassHudTheme(_value) {
  // Aurora Glass — always a glass theme.
  return true;
}

/* Stage-mode overlay hook — while `on`, holds a SyrixStage overlay tag so the
 * staged BrowserView is frozen (snapshot swap) and this panel can paint above
 * web content. Releases on unmount / when `on` drops. */
function useStageOverlay(tag, on) {
  useEffect(() => {
    const S = window.SyrixStage;
    if (!S || !on) return;
    S.acquireOverlay(tag);
    return () => { try { S.releaseOverlay(tag); } catch (_) {} };
  }, [tag, on]);
}

function readHudTheme() {
  return HUD_THEME_AURORA_GLASS;
}

function saveHudTheme(_theme) {}

function hudThemeLabel(_theme) {
  return 'Aurora Glass';
}

function islandNoticeTextLength(notice) {
  if (!notice) return 0;
  return [notice.text, notice.detail].filter(Boolean).join(' ').trim().length;
}

function islandNoticeReadMs(notice) {
  const len = islandNoticeTextLength(notice);
  return Math.max(3000, Math.min(5000, 2850 + len * 22));
}

function islandNoticeWidthPx(notice) {
  const len = islandNoticeTextLength(notice);
  return Math.max(318, Math.min(680, 286 + len * 3.1));
}

function readableHudStatusDetail(raw, command, ok) {
  const s = String(raw == null ? '' : raw).trim();
  const cmd = String(command || 'status').replace(/[_-]+/g, ' ').trim() || 'status';
  const low = s.toLowerCase();
  if (!s) return ok ? `${cmd} completed.` : `${cmd} failed.`;
  // 2026-06-08 FIX: backend debug payloads like "url=https://..." were
  // leaking into Dynamic Island toasts. Strip any url=/href=/href: prefix
  // and never display a raw URL — say what the action did, not where.
  if (/^(url|href|link|target)\s*[:=]/i.test(s) || /^https?:\/\//i.test(s)) {
    return ok ? `${cmd} opened.` : `${cmd} could not open.`;
  }
  if (low.includes('hud_ack_timeout')) return 'The HUD did not confirm that request before the timeout.';
  if (low.includes('permission_required') || low.includes('permission_error') || low.includes('access_denied')) {
    return `${cmd} needs permission before it can continue.`;
  }
  if (low.includes('revoked=true')) return `${cmd} is turned off and permission has been revoked.`;
  if (low.includes('revoked=false')) return `${cmd} is enabled.`;
  if (/^(true|false)$/i.test(s)) return `${cmd} is ${low === 'true' ? 'on' : 'off'}.`;
  if (low === 'null' || low === 'none' || low === 'undefined') return `${cmd} did not provide a usable status.`;
  if (/^[a-z0-9_:-]+$/i.test(s) && /[_-]/.test(s)) return s.replace(/[_-]+/g, ' ') + '.';
  return s;
}

function isGeminiProvider(_value) {
  return false;
}

const HUD_BALL_THEME_COLORS = {
  [HUD_THEME_AURORA_GLASS]: {
    /* Aurora Glass — Obsidian & Champagne. Warm gold corona around a dark body.
       `primary` was 0xfff6e4 (so pale it read as white); pulled toward a clear
       champagne gold and the hot core is now warm white, not pure 0xffffff. */
    primary: 0xffdc9a,
    secondary: 0xe6b873,
    body: 0x0b0907,
    emissive: 0x241a0d,
    hemiSky: 0xffe9c2,
    hemiGround: 0x0d0a06,
    rim: 0xf6e2b3,
    core: 0xfff1d6,
  },
};

function hudBallTheme(_theme) {
  return HUD_BALL_THEME_COLORS[HUD_THEME_AURORA_GLASS];
}

/* Boot screen — delegates to the Three.js cinematic defined in
   syrix-boot-cinematic.jsx (loaded before this file). */
function BootScreen({ onReveal, onDone }) {
  // Remove pre-boot layer on first render
  useEffect(() => {
    try {
      const pre = document.getElementById('syrix-pre-boot');
      if (pre && pre.parentNode) pre.parentNode.removeChild(pre);
    } catch (_) {}
  }, []);
  return React.createElement(SyrixBootCinematic, { onReveal, onDone });
}

/* ── Mic bars ────────────────────────────────────────────────────── */
function MicBars({ level }) {
  const [heights, setHeights] = useState(Array(12).fill(3));
  const levelRef = useRef(level);
  useEffect(() => {
    levelRef.current = level;
  }, [level]);
  useEffect(() => {
    const i = setInterval(() => {
      const liveLevel = levelRef.current;
      setHeights(Array.from({ length: 12 }, (_, j) => {
        const base = Math.sin(Date.now() * 0.006 + j * 0.75) * 0.5 + 0.5;
        return Math.max(3, base * liveLevel * 22);
      }));
    }, 80);
    return () => clearInterval(i);
  }, []);
  return (
    <div className="p-mic-bar">
      {heights.map((h, i) => (
        <div key={i} className="p-mic-seg" style={{ height: h + 'px', opacity: 0.4 + level * 0.6 }}></div>
      ))}
    </div>
  );
}

/* ── Long-form report overlay ───────────────────────────────────────
   2026-05-27 — When the backend emits a `long_report` message (research,
   stock analysis, code, step-by-step instructions), the chat keeps the
   short summary while this overlay shows the full body in a scrollable
   panel. Closes on Esc or backdrop click. Copy button copies the body. */
function SyrixLongReportOverlay() {
  const [rpt, setRpt] = useState(null);
  const [zLayer, setZLayer] = useState(4200);
  useEffect(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    let last = '';
    const unsub = window.SyrixBridge.subscribe((s) => {
      const r = s.syrix_long_report;
      const sig = r ? `${r.request_id}|${r.ts}` : '';
      if (sig === last) return;
      last = sig;
      if (r && (r.body || r.summary)) {
        setRpt(r);
        // 2026-05-30 — Newest opened report panel rises above whatever was
        // previously on top.  Layer manager keeps a monotonic counter so
        // open + click-to-focus both produce a fresh top z.
        try {
          const z = window.SyrixLayerManager && window.SyrixLayerManager.open('long_report', 4200);
          if (z) setZLayer(z);
        } catch (_) {}
      }
    });
    function onEsc(e) { if (e.key === 'Escape') doClose(); }
    window.addEventListener('keydown', onEsc);
    return () => { try { unsub && unsub(); } catch (_) {} window.removeEventListener('keydown', onEsc); };
  }, []);
  function doClose() {
    setRpt(null);
    try { window.SyrixLayerManager && window.SyrixLayerManager.close('long_report'); } catch (_) {}
  }
  if (!rpt) return null;
  function copyAll() {
    try { navigator.clipboard.writeText(String(rpt.body || rpt.summary || '')); } catch (_) {}
  }
  function bringToFront() {
    try {
      const z = window.SyrixLayerManager && window.SyrixLayerManager.focus('long_report', 4200);
      if (z) setZLayer(z);
    } catch (_) {}
  }
  return (
    <div onClick={doClose} style={{
      position: 'fixed', inset: 0, zIndex: zLayer,
      background: 'rgba(2,6,15,0.62)', backdropFilter: 'blur(6px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40,
    }}>
      <div onClick={(e) => { e.stopPropagation(); bringToFront(); }} style={{
        width: 'min(960px, 96vw)', maxHeight: '88vh',
        background: 'linear-gradient(180deg, rgba(8,14,26,0.96), rgba(3,8,16,0.96))',
        border: '1px solid rgba(34,211,238,0.32)', borderRadius: 14,
        boxShadow: '0 30px 80px rgba(0,0,0,0.7), 0 0 80px -20px rgba(34,211,238,0.42)',
        display: 'flex', flexDirection: 'column', overflow: 'hidden',
      }}>
        <div style={{
          padding: '14px 18px', borderBottom: '1px solid rgba(34,211,238,0.16)',
          display: 'flex', alignItems: 'center', gap: 12,
        }}>
          <span style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.26em', color: '#d4aa5a' }}>
            {String(rpt.kind || 'report').toUpperCase()}
          </span>
          <span style={{ font: '600 13px Inter', color: '#e9f3ff', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {rpt.title || 'Full Response'}
          </span>
          <button className="mini-btn" onClick={copyAll} style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.18em' }}>COPY</button>
          <button className="mini-btn" onClick={doClose} style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.18em' }}>CLOSE</button>
        </div>
        {rpt.summary && (
          <div style={{
            padding: '12px 18px', borderBottom: '1px solid rgba(34,211,238,0.08)',
            font: '500 12px/1.6 Inter', color: 'rgba(220,235,255,0.86)', flexShrink: 0,
          }}>
            {rpt.summary}
          </div>
        )}
        <div style={{
          padding: '14px 18px', overflowY: 'auto', flex: 1,
          font: '400 13px/1.62 Inter', color: 'rgba(220,235,255,0.92)',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
        }}>
          {rpt.body || rpt.summary || ''}
        </div>
      </div>
    </div>
  );
}

/* CAP-17 (2026-06-04) — Morning brief card.
   Auto-shows when the backend pushes a `morning_brief_card` ws message.
   The chat panel still has the prose; this is the visual quick-view of
   SPY/QQQ/VIX as colored chips, watchlist as a mini-table, and open
   positions as a P&L strip. Dismissed by the user (X) or after 5 minutes. */
function SyrixMorningBriefCard() {
  const [card, setCard] = useState(null);
  useEffect(() => {
    function onCard(ev) {
      const c = (ev && ev.detail) || null;
      if (!c) return;
      setCard(c);
      // Auto-dismiss after 5 minutes if user ignores it.
      setTimeout(() => setCard((cur) => (cur && cur.ts === c.ts ? null : cur)), 5 * 60 * 1000);
    }
    window.addEventListener('syrix-morning-brief-card', onCard);
    return () => window.removeEventListener('syrix-morning-brief-card', onCard);
  }, []);
  if (!card) return null;
  const p = card.payload || {};
  const fmtPct = (v) => {
    if (v == null || !Number.isFinite(Number(v))) return '';
    const n = Number(v);
    const sign = n >= 0 ? '+' : '';
    return sign + n.toFixed(2) + '%';
  };
  const fmtPrice = (v) => {
    if (v == null || !Number.isFinite(Number(v))) return '—';
    return '$' + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  const indexChip = (label, q) => {
    if (!q || (q.price == null && q.pre_price == null)) {
      return <span style={{ font: '600 10px JetBrains Mono', color: 'rgba(180,210,255,0.45)', padding: '4px 9px' }}>{label} —</span>;
    }
    const price = q.pre_price != null ? q.pre_price : q.price;
    const chg = q.pre_change_pct != null ? q.pre_change_pct : q.change_pct;
    const color = (chg == null) ? '#a3b8d6' : (chg >= 0 ? '#48c78e' : '#ff6b6b');
    return (
      <span style={{
        font: '600 11px JetBrains Mono', letterSpacing: '0.10em',
        padding: '4px 10px', borderRadius: 999,
        background: 'rgba(' + (chg >= 0 ? '72,199,142' : (chg == null ? '163,184,214' : '255,107,107')) + ',0.10)',
        border: '1px solid rgba(' + (chg >= 0 ? '72,199,142' : (chg == null ? '163,184,214' : '255,107,107')) + ',0.30)',
        color,
      }}>{label} {fmtPrice(price)} {chg != null && <span style={{ marginLeft: 6, opacity: 0.85 }}>{fmtPct(chg)}</span>}</span>
    );
  };
  const watch = Array.isArray(p.watchlist_quotes) ? p.watchlist_quotes : [];
  const positions = (p.positions && typeof p.positions === 'object') ? p.positions : {};
  const positionRows = Object.entries(positions);
  return (
    <div style={{
      position: 'fixed', top: 16, left: 16, width: 'min(380px, 92vw)', zIndex: 3800,
      background: 'linear-gradient(180deg, rgba(8,14,26,0.96), rgba(3,8,16,0.92))',
      border: '1px solid rgba(34,211,238,0.32)', borderRadius: 14,
      boxShadow: '0 24px 60px rgba(0,0,0,0.55), 0 0 60px -20px rgba(34,211,238,0.30)',
      overflow: 'hidden', backdropFilter: 'blur(16px)',
      animation: 'mbCardIn 0.4s cubic-bezier(0.4,0,0.2,1)',
    }}>
      <style>{`@keyframes mbCardIn { from { transform: translateX(-12px); opacity: 0; } to { transform: none; opacity: 1; } }`}</style>
      <div style={{
        padding: '10px 14px', borderBottom: '1px solid rgba(34,211,238,0.16)',
        display: 'flex', alignItems: 'center', gap: 10,
      }}>
        <span style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.28em', color: '#d4aa5a', flex: 1 }}>
          MORNING BRIEF
        </span>
        <button onClick={() => setCard(null)} style={{
          background: 'none', border: 'none', color: 'rgba(150,180,210,0.65)',
          fontSize: 14, cursor: 'pointer', padding: '2px 6px', lineHeight: 1,
        }}>✕</button>
      </div>
      <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {indexChip('SPY', p.spy)}
          {indexChip('QQQ', p.qqq)}
          {indexChip('VIX', p.vix)}
        </div>
        {watch.length > 0 && (
          <div>
            <div style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.22em', color: 'rgba(165,180,252,0.75)', marginBottom: 6 }}>
              WATCHLIST
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '4px 10px', font: '500 12px Inter', color: 'rgba(220,235,255,0.92)' }}>
              {watch.slice(0, 6).map((q) => {
                const chg = q.pre_change_pct != null ? q.pre_change_pct : q.change_pct;
                const color = (chg == null) ? '#a3b8d6' : (chg >= 0 ? '#48c78e' : '#ff6b6b');
                return (
                  <React.Fragment key={q.ticker}>
                    <span style={{ font: '600 12px JetBrains Mono' }}>{q.ticker}</span>
                    <span style={{ textAlign: 'right' }}>{fmtPrice(q.pre_price != null ? q.pre_price : q.price)}</span>
                    <span style={{ textAlign: 'right', color }}>{fmtPct(chg) || '—'}</span>
                  </React.Fragment>
                );
              })}
            </div>
          </div>
        )}
        {positionRows.length > 0 && (
          <div>
            <div style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.22em', color: 'rgba(245,194,67,0.85)', marginBottom: 6 }}>
              POSITIONS
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {positionRows.slice(0, 4).map(([tkr, ps]) => (
                <div key={tkr} style={{ font: '500 12px Inter', color: 'rgba(220,235,255,0.92)' }}>
                  <strong style={{ font: '600 12px JetBrains Mono', color: '#f5c243' }}>
                    {String((ps && ps.side) || 'long').toUpperCase()}
                  </strong>{' '}
                  {(ps && ps.shares) || 0} {tkr} @ {fmtPrice(ps && ps.avg_price)}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Reading Panel (A3) ─────────────────────────────────────────── */
function ReadingPanel() {
  const [panel, setPanel] = React.useState(null);
  const scrollRef = React.useRef(null);

  React.useEffect(() => {
    const B = window.SyrixBridge;
    if (!B || !B.subscribe) return;
    return B.subscribe((s) => {
      const rp = s && s.raw && s.raw.syrix_reading_panel;
      if (rp && rp.content) setPanel(rp);
    });
  }, []);

  React.useEffect(() => {
    if (!panel) return;
    function onKey(e) { if (e.key === 'Escape') setPanel(null); }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [panel]);

  if (!panel) return null;

  const sources = Array.isArray(panel.sources) ? panel.sources : [];

  return (
    <div className="rp-overlay" onClick={() => setPanel(null)}>
      <div className="rp-panel" onClick={(e) => e.stopPropagation()}>
        <div className="rp-header">
          <span className="rp-title">FULL RESPONSE</span>
          <button className="rp-close" onClick={() => setPanel(null)}>✕</button>
        </div>
        <div className="rp-scroll" ref={scrollRef}>
          <pre className="rp-content">{panel.content}</pre>
          {sources.length > 0 && (
            <div className="rp-sources">
              <div className="rp-sources-label">Sources</div>
              {sources.map((s, i) => (
                <a
                  key={i}
                  className="rp-source-link"
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    try { window.SyrixBridge && window.SyrixBridge.send && window.SyrixBridge.send('open_url', { data: { url: s.url } }); } catch (_) {}
                  }}
                >{s.name || s.url}</a>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Primary HUD ─────────────────────────────────────────────────── */
/* ── Orb Stats Ring — expands on right-click with live PC data ── */
function OrbStatsRing({ open, stats, ping, sysinfo }) {
  const si = sysinfo || {};
  // CPU: prefer native IPC (sampled every 2s), fall back to backend
  const cpuPct   = si.cpu_percent != null ? si.cpu_percent : stats.cpu;
  const ramPct   = si.ram_percent != null ? si.ram_percent : stats.ram;
  const ramLabel = si.ram_used_gb != null ? `${si.ram_used_gb}/${si.ram_total_gb}G` : (ramPct != null ? Math.round(ramPct) + '%' : '—');
  const diskVal  = si.disk_percent != null ? si.disk_percent + '%' : '—';
  const gpuVal   = stats.gpuUtil  != null ? Math.round(stats.gpuUtil)  + '%' : '—';
  const tempVal  = stats.gpuTemp  != null ? Math.round(stats.gpuTemp) + '°C' : (si.uptime_str || '—');
  const hostVal  = si.hostname ? si.hostname.slice(0, 8).toUpperCase() : (stats.voiceState || '—').slice(0, 8);
  const items = [
    { key: 'cpu',   label: 'CPU',   value: cpuPct  != null ? Math.round(cpuPct) + '%' : '—',  dx: -185, dy: -85 },
    { key: 'gpu',   label: 'GPU',   value: gpuVal,                                               dx: 0,    dy: -115 },
    { key: 'ram',   label: 'RAM',   value: ramLabel,                                             dx: 185,  dy: -85 },
    { key: 'net',   label: 'NET',   value: ping != null ? ping + 'ms' : '—',                   dx: -215, dy: 0 },
    { key: 'temp',  label: stats.gpuTemp != null ? 'GPU°C' : 'UP',   value: tempVal,           dx: 215,  dy: 0 },
    { key: 'disk',  label: 'DISK',  value: diskVal,                                             dx: -185, dy: 85 },
    { key: 'host',  label: 'HOST',  value: hostVal,                                             dx: 0,    dy: 115 },
    { key: 'tasks', label: 'TASKS', value: String(stats.taskCount),                             dx: 185,  dy: 85 },
  ];
  return (
    <div className="orb-sr" onClick={e => e.stopPropagation()} onContextMenu={e => { e.preventDefault(); e.stopPropagation(); }}>
      {items.map((it, i) => (
        <div
          key={it.key}
          className={'orb-sc' + (open ? ' open' : '')}
          style={{ '--dx': it.dx + 'px', '--dy': it.dy + 'px', transitionDelay: open ? (i * 30) + 'ms' : '0ms' }}
        >
          <span className="osc-lbl">{it.label}</span>
          <span className="osc-val">{it.value}</span>
        </div>
      ))}
    </div>
  );
}

function IslandStateIcon({ mode, isOffline, tone, isConnecting, isReconnecting, voiceOffPhase }) {
  // Docking phase: the orb itself occupies the pill's attention, so the icon
  // zone stays blank. Once parked ('text') or announcing the return
  // ('restore-fade'/'restore-move'), show the island's own standby dot next
  // to the label. 'settled' (muted, but the announcement window has passed)
  // and 'none' fall through to the normal icon logic below, so the island
  // looks like a regular idle pill again.
  if (voiceOffPhase === 'dock') return null;
  if (voiceOffPhase === 'text' || voiceOffPhase === 'restore-fade' || voiceOffPhase === 'restore-move') {
    return <div className="p-island-icon-dot standby voice-off-dot" />;
  }
  if (isOffline || tone === 'error')
    return <div className="p-island-icon-dot error" />;
  if (isConnecting || isReconnecting)
    return <div className="p-island-icon-dot standby" />;
  if (mode === 'listening')
    return (
      <svg className="p-island-icon-svg" width="9" height="12" viewBox="0 0 9 12" fill="none">
        <rect x="2.5" y="0" width="4" height="7" rx="2" fill="currentColor"/>
        <path d="M1 5.5 a3.5 3.5 0 0 0 7 0" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
        <line x1="4.5" y1="9" x2="4.5" y2="11.2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
        <line x1="2.5" y1="11.2" x2="6.5" y2="11.2" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
      </svg>
    );
  if (mode === 'speaking')
    return (
      <svg className="p-island-icon-svg" width="11" height="11" viewBox="0 0 11 11" fill="none">
        <path d="M1.5 3.5 L4.5 2 L4.5 9 L1.5 7.5 Z" fill="currentColor"/>
        <path d="M6 4 a2.3 2.3 0 0 1 0 3" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round"/>
        <path d="M7.5 2.5 a4 4 0 0 1 0 6" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round"/>
      </svg>
    );
  if (mode === 'processing')
    return (
      <div className="p-island-icon-dots">
        <span/><span/><span/>
      </div>
    );
  // Idle SYRIX is a clean wordmark state; the connection dot belongs to
  // active/exception states and should not compete with the label.
  if (mode === 'sleep' || mode === 'idle') return null;
  return <div className={"p-island-icon-dot " + (tone === 'standby' ? 'standby' : 'online')} />;
}

function IslandWaveformBars() {
  return (
    <div className="p-island-bars" aria-hidden="true">
      <div className="p-island-bar"/>
      <div className="p-island-bar"/>
      <div className="p-island-bar"/>
      <div className="p-island-bar"/>
      <div className="p-island-bar"/>
    </div>
  );
}

function PrimaryHUD() {
  // mode: sleep | idle | listening | processing | speaking | interrupted
  // Starts in sleep/standby — time shows, not listening. Activates on double-clap or wake word.
  const [mode, setMode] = useState('sleep');
  // Animation flag for the reverse logo/bracket reveal that plays when going
  // to sleep (sleepingAnim). Set true for a fixed duration then cleared so
  // the CSS anim fires once. (The wake-side logo reveal was removed —
  // 2026-07-03 — orb click now wakes silently, no logo popup.)
  const [sleepingAnim, setSleepingAnim] = useState(false);
  // Backend voice state mirror — 'IDLE' | 'LISTENING' | 'PROCESSING' | 'SPEAKING' | 'SLEEP'.
  // Drives the top-bar Stop button visibility while TTS is playing.
  const [backendVoiceState, setBackendVoiceState] = useState('IDLE');
  // Entry animation: HUD fades/scales in after boot completes.
  const [hudEntered, setHudEntered] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setHudEntered(true), 80);
    return () => clearTimeout(t);
  }, []);
  const [chatOpen, setChatOpen] = useState(false);
  const [settingsOpen, setSettingsOpenRaw] = useState(false);
  // Opening and closing Settings crossfades (window.SyrixSmooth, shell).
  const setSettingsOpen = useCallback((v) => {
    const apply = () => setSettingsOpenRaw(v);
    if (window.SyrixSmooth) window.SyrixSmooth(apply); else apply();
  }, []);
  const [hudTheme, setHudTheme] = useState(readHudTheme);
  const [widgets, setWidgets] = useState([]);
  const [focusedWidgetId, setFocusedWidgetId] = useState(null);
  const [corner, setCorner] = useState(false);
  const [time, setTime] = useState(new Date());
  const [transcript, setTranscript] = useState('');
  const [micLvl, setMicLvl] = useState(0);
  const [widgetMenu, setWidgetMenu] = useState(false);
  // HUD App Dock — compact-orb state driven by the dock (any open app).
  const [hudAppOpen, setHudAppOpen] = useState(false);
  const [hudApps, setHudApps] = useState([]);
  const [activeHudAppId, setActiveHudAppId] = useState(null);
  const prevHudAppCountRef = useRef(0);
  // Stage mode — the browser fills the window as a backdrop and the island
  // morphs into a nav bar. Driven by window.SyrixStage (fed by the bridge).
  const [hudStage, setHudStageState] = useState(() => (
    window.SyrixStage ? window.SyrixStage.get() : { active: false, stage: { navH: 68 } }
  ));
  useEffect(() => {
    if (!window.SyrixStage) return;
    return window.SyrixStage.subscribe(setHudStageState);
  }, []);
  const stageActive = !!(hudStage && hudStage.active);
  const [stageFolderOpen, setStageFolderOpen] = useState(false);
  // 2026-07-04 — widgets over the staged browser. BrowserViews are native
  // layers that always paint above HTML, so open widgets were invisible in
  // stage mode. Holding a stage-overlay tag while any widget is open swaps
  // the live view for its snapshot (rendered by the dock at z 350) and the
  // widget layer (z 400) paints + drags on top exactly like the normal HUD.
  useEffect(() => {
    const S = window.SyrixStage;
    if (!S) return;
    if (stageActive && widgets.length > 0) {
      S.acquireOverlay('p-widgets');
      return () => { try { S.releaseOverlay('p-widgets'); } catch (_) {} };
    }
    try { S.releaseOverlay('p-widgets'); } catch (_) {}
  }, [stageActive, widgets.length]);
  const [stageTabsOpen, setStageTabsOpen] = useState(false);
  const [stageUrl, setStageUrl] = useState('');
  // Screen Vision dock state — OFF by default; user must explicitly enable.
  const [screenVisionActive, setScreenVisionActive] = useState(false);
  const [screenVisionRevoked, setScreenVisionRevoked] = useState(false);
  const [svAck, setSvAck] = useState('');   // brief feedback text for screen-vision button
  const [muted, setMuted] = useState(false);
  const [micMuted, setMicMuted] = useState(false);
  const micMutedRef = useRef(false);
  const ttsMutedRef = useRef(false);
  const [ttsMuted, setTtsMuted] = useState(
    () => { try { return localStorage.getItem('syrix_tts_muted') === 'true'; } catch (_) { return false; } }
  );
  const toggleTtsMute = useCallback(() => {
    setTtsMuted(m => {
      const next = !m;
      try { localStorage.setItem('syrix_tts_muted', String(next)); } catch (_) {}
      try { window.SyrixBridge && window.SyrixBridge.send && window.SyrixBridge.send(next ? 'mute_tts' : 'unmute_tts'); } catch (_) {}
      return next;
    });
  }, []);
  // 2026-06-21 — The mic-mute control is the single "quiet SYRIX" switch: it
  // pauses mic input AND fully mutes TTS (SYRIX never speaks the answer), and
  // drops the ORB into a soft-red sleep glow. The backend keeps running — only
  // voice in/out is silenced (SYRIX is NOT put to sleep). Toggling off restores
  // normal listening + speaking.
  const applyMute = useCallback((next) => {
    setMicMuted(next);
    micMutedRef.current = next;
    setTtsMuted(next);
    try { localStorage.setItem('syrix_tts_muted', String(next)); } catch (_) {}
    try {
      const B = window.SyrixBridge;
      if (B && B.send) {
        B.send(next ? 'mute_mic' : 'unmute_mic');
        B.send(next ? 'mute_tts' : 'unmute_tts');
      }
    } catch (_) {}
  }, []);
  // VOICE MODE (2026-07-08): the voice session is renderer-driven and never
  // persisted — after any HUD (re)load there is no voice UI on screen, so
  // re-assert voice_mode_off once the bridge has had a moment to connect.
  // Idempotent when the backend is already in Chat Mode.
  useEffect(() => {
    const t = setTimeout(() => {
      try { window.SyrixBridge && window.SyrixBridge.send && window.SyrixBridge.send('voice_mode_off'); } catch (_) {}
    }, 1500);
    return () => clearTimeout(t);
  }, []);
  // Keep ref in sync so closures inside useEffect can read the latest value
  // without triggering re-renders or stale closure captures.
  useEffect(() => { micMutedRef.current = micMuted; }, [micMuted]);
  useEffect(() => { ttsMutedRef.current = ttsMuted; }, [ttsMuted]);
  // 2026-07-08 (voice-mode remodel) — the moment the mic mutes (e.g. exiting
  // Voice Mode), drop any live LISTENING/SPEAKING mode immediately instead of
  // waiting for the next backend state tick. Without this the Dynamic Island
  // kept saying "LISTENING" through the ~30s Whisper-unload grace window.
  useEffect(() => {
    if (micMuted) setMode(m => (m === 'listening' || m === 'speaking') ? 'idle' : m);
  }, [micMuted]);
  // 2026-07-02 — Voice-off dock sequence: whenever mic and/or TTS end up
  // muted (either one, or both), the orb travels into the Dynamic Island and
  // vanishes; the island announces the change for a few seconds, then settles
  // back to its normal look (still muted, orb still hidden). Unmuting mirrors
  // the same two-stage shape in reverse so both directions feel equally
  // smooth: mute is glide-to-the-island-then-fade-out (dock → text), so
  // unmute is fade-in-at-the-island-then-glide-back (restore-fade →
  // restore-move) instead of an instant reappear-then-glide. Purely visual —
  // the real mic-capture and TTS-playback gates are micMutedRef/ttsMutedRef
  // (above) and the backend's boss_mic_listen_muted / _SYRIX_TTS_MUTED flags,
  // which already stay in lockstep with this.
  const voiceOff = micMuted || ttsMuted;
  const [voiceOffPhase, setVoiceOffPhase] = useState('none'); // none|dock|text|settled|restore-fade|restore-move
  const voiceOffTimersRef = useRef([]);
  const wasVoiceOffRef = useRef(false);
  const voiceOffInitializedRef = useRef(false);
  const skipVoiceOffDockRef = useRef(false);
  useEffect(() => {
    voiceOffTimersRef.current.forEach(clearTimeout);
    voiceOffTimersRef.current = [];
    if (voiceOff) {
      wasVoiceOffRef.current = true;
      // A persisted mute preference is a steady state, not a fresh mute
      // action. Running the dock animation on first mount made the island
      // render as a wide blank capsule with only its standby dot for ~4s.
      if (!voiceOffInitializedRef.current || skipVoiceOffDockRef.current) {
        skipVoiceOffDockRef.current = false;
        voiceOffInitializedRef.current = true;
        setVoiceOffPhase('settled');
        return () => voiceOffTimersRef.current.forEach(clearTimeout);
      }
      setVoiceOffPhase('dock');
      voiceOffTimersRef.current = [
        setTimeout(() => setVoiceOffPhase('text'), 1000),
        setTimeout(() => setVoiceOffPhase('settled'), 1000 + 3000),
      ];
    } else if (wasVoiceOffRef.current) {
      wasVoiceOffRef.current = false;
      setVoiceOffPhase('restore-fade');
      voiceOffTimersRef.current = [
        setTimeout(() => setVoiceOffPhase('restore-move'), 550),
        setTimeout(() => setVoiceOffPhase('none'), 3000),
      ];
    } else {
      setVoiceOffPhase('none');
    }
    voiceOffInitializedRef.current = true;
    return () => voiceOffTimersRef.current.forEach(clearTimeout);
  }, [voiceOff]);
  // Phase-4 (2026-05-17): 3D model picker — selecting a model spawns it in
  // the sandbox via the existing bridge.sandboxCreate() flow. The picker
  // surfaces the educational summary inline so the user knows what they're
  // looking at while gesturing on it.
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [lastModelSpawned, setLastModelSpawned] = useState(null);
  // Right-side 3D sandbox side-panel is OFF by default; voice commands cover
  // rotate / scale / explode / clear / send-to-Blender, so opting in here
  // only brings back the verbose grid for users who prefer it.
  const [sandboxPanelOpen, setSandboxPanelOpen] = useState(false);

  const ballRef = useRef(null);
  const ballApi = useRef(null);
  const modeRef = useRef(mode);
  modeRef.current = mode;

  // Debounce guard: prevents IDLE→sleep flicker between consecutive listen captures.
  // Each capture loop iteration briefly emits IDLE before the next LISTENING call,
  // so we only switch to sleep if IDLE persists for >4s with no active backend
  // state arriving. ACTIVATED / LISTENING / PROCESSING / SPEAKING all count as
  // "session alive" and cancel the pending sleep.
  const sleepDebounceRef = useRef(null);
  // Sleep-lock guard: when the user explicitly clicks the Sleep button (or says
  // "sleep"), suppress backend → mode transitions for a short window so a
  // stale in-flight LISTENING tick can't immediately flip us back to listening.
  // Cleared automatically after SLEEP_LOCK_MS.
  const sleepLockUntilRef = useRef(0);
  const SLEEP_LOCK_MS = 12000;
  const SLEEP_DEBOUNCE_MS = 4000;
  const [userIdleTimeoutMinutes, setUserIdleTimeoutMinutes] = useState(15);

  /* Clock */
  useEffect(() => {
    const i = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(i);
  }, []);

  useEffect(() => {
    function note(kind) {
      // Don't record activity or wake from sleep on cursor movement/hover
      if (modeRef.current === 'sleep') return;

      lastUserActivityRef.current = Date.now();
      const now = Date.now();
      if (now - lastIdleActivityPingRef.current > 30000) {
        lastIdleActivityPingRef.current = now;
        try {
          const B = window.SyrixBridge;
          if (B && B.noteUserActivity) B.noteUserActivity(kind || 'hud_activity');
        } catch (_) {}
      }
    }
    const opts = { passive: true };
    window.addEventListener('pointerdown', note, opts);
    window.addEventListener('pointermove', note, opts);
    window.addEventListener('keydown', note);
    window.addEventListener('wheel', note, opts);
    window.addEventListener('touchstart', note, opts);
    note('hud_ready');
    const check = setInterval(() => {
      const timeoutMs = Math.max(60000, Math.min(240 * 60000, Number(userIdleTimeoutMinutes || 15) * 60000));
      const idleFor = Date.now() - Number(lastUserActivityRef.current || 0);
      const modeNow = String(modeRef.current || '').toLowerCase();
      if (idleFor >= timeoutMs && modeNow === 'idle') {
        goSleep();
        try {
          const B = window.SyrixBridge;
          if (B && B.send) B.send('sleep', { data: { reason: 'user_idle_timeout', timeout_minutes: Number(userIdleTimeoutMinutes || 15) } });
        } catch (_) {}
        lastUserActivityRef.current = Date.now();
      }
    }, 15000);
    return () => {
      window.removeEventListener('pointerdown', note, opts);
      window.removeEventListener('pointermove', note, opts);
      window.removeEventListener('keydown', note);
      window.removeEventListener('wheel', note, opts);
      window.removeEventListener('touchstart', note, opts);
      clearInterval(check);
    };
  }, [userIdleTimeoutMinutes]);

  /* Live backend connection + brain state from SyrixBridge */
  const [conn, setConn] = useState({ status: 'connecting', brain: '', model: '' });
  const [battery, setBattery] = useState({ percent: null, plugged: null });
  /* Orb right-click stats ring */
  const [orbStats, setOrbStats] = useState({ cpu: null, ram: null, gpuUtil: null, gpuTemp: null, voiceState: 'IDLE', memNodes: 0, taskCount: 0 });
  const [orbMenuOpen, setOrbMenuOpen] = useState(false);
  /* Native PC sysinfo — polled every 3s via Electron IPC; independent of Python backend */
  const [nativeSysinfo, setNativeSysinfo] = useState(null);
  const [netPing, setNetPing] = useState(null);
  const [nim, setNim] = useState({ online: 0, total: 0, models: [], active: null, lastTest: null });
  const [nimOpen, setNimOpen] = useState(false);
  const [brainList, setBrainList] = useState([]);
  const [brainManual, setBrainManual] = useState('auto');
  const [brainPending, setBrainPending] = useState('');
  const [brainSwitchError, setBrainSwitchError] = useState('');
  const brainManualRef = useRef('auto');
  const brainPendingRef = useRef('');
  const brainSwitchTimerRef = useRef(null);
  // Latest state_version we have applied for brain selection. Heartbeat state
  // ticks carrying an older version cannot clobber a fresher brain_list event.
  const lastBrainStateVersionRef = useRef(0);
  // Anchor ref + position state for the brain switcher dropdown so it can
  // appear smoothly tucked under the brain pill rather than floating in
  // the middle of the screen as a modal.
  const brainPillRef = useRef(null);
  const [brainPillRect, setBrainPillRect] = useState(null);
  const [islandOpen, setIslandOpen] = useState(false);
  const [islandNotice, setIslandNotice] = useState(null);
  /* ── Island mode ──────────────────────────────────────────────────────────
     Right-clicking the island chooses what it shows when nothing urgent is
     happening:
       status  — SYRIX's own state (the wordmark when idle)
       name    — whose machine this is
       widgets — an answer's live card takes the island over as it arrives
     Truth outranks the preference: a dropped connection, a voice state or a
     running timer still claim the pill in every mode. The mode only decides
     the QUIET state, so the island can never be configured into lying about
     what is happening right now. */
  const [islandMode, setIslandMode] = useState(() => {
    try {
      const v = String(localStorage.getItem('syrix_island_mode') || '');
      if (v === 'name' || v === 'widgets') return v;
    } catch (_) {}
    return 'status';
  });
  const [islandMenuOpen, setIslandMenuOpen] = useState(false);
  function chooseIslandMode(m) {
    setIslandMode(m);
    try { localStorage.setItem('syrix_island_mode', m); } catch (_) {}
    setIslandMenuOpen(false);
  }
  const ownerName = (() => {
    try { if (window.SyrixOwner && window.SyrixOwner.name) return window.SyrixOwner.name(); } catch (_) {}
    return 'SYRIX';
  })();
  useEffect(() => {
    if (!islandMenuOpen) return undefined;
    function onKey(e) { if (e.key === 'Escape') setIslandMenuOpen(false); }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [islandMenuOpen]);
  // ── Dynamic Island v4 — live activities + rich cards (2026-07-02) ──
  // Media session keeps polling even when the Music widget is closed, so the
  // island can take over the session (iOS-style). Stopwatch state lives in a
  // global store so it survives widget close. Cards are backend-pushed
  // (scoreboard / wiki) via snap.island_card.
  const mediaSession = (window.SyrixMediaSessionHook || (() => null))();
  // "End" the media activity fully — unlike pause, this drops it out of the
  // island entirely (like the timer's STOP) instead of leaving a paused card
  // sitting there. Cleared the moment a different track starts playing.
  const [mediaDismissed, setMediaDismissed] = useState(false);
  const mediaDismissTrackRef = useRef('');
  useEffect(() => {
    const track = (mediaSession && mediaSession.track) || '';
    if (track !== mediaDismissTrackRef.current) {
      mediaDismissTrackRef.current = track;
      setMediaDismissed(false);
    }
  }, [mediaSession && mediaSession.track]);
  const [swSnap, setSwSnap] = useState(() => (
    window.SyrixStopwatch ? window.SyrixStopwatch.get() : { running: false, elapsedMs: 0 }
  ));
  const [islandCard, setIslandCard] = useState(null);
  const [islandCardVisible, setIslandCardVisible] = useState(false);
  const [islandCardHistory, setIslandCardHistory] = useState([]);
  const [agentTasks, setAgentTasks] = useState([]);
  // Supercomputer, while its workspace is closed. See SyrixIslandSupercomputer.
  const [scState, setScState] = useState(null);
  const [, setIslandTick] = useState(0); // 1s heartbeat for time-based island content
  const lastIslandCardSeqRef = useRef(0);
  const islandCardTimerRef = useRef(null);
  useEffect(() => {
    if (!window.SyrixStopwatch) return;
    return window.SyrixStopwatch.subscribe(setSwSnap);
  }, []);
  useEffect(() => {
    const B = window.SyrixBridge;
    if (!B || !B.subscribe) return;
    const unsub = B.subscribe((s) => {
      if (Array.isArray(s && s.syrix_tasks)) setAgentTasks(s.syrix_tasks);
      if (s && typeof s.syrix_supercomputer === 'object') setScState(s.syrix_supercomputer);
      if (Array.isArray(s && s.island_card_history)) setIslandCardHistory(s.island_card_history);
      const card = s && s.island_card;
      if (!card || typeof card !== 'object') return;
      const seq = Number(card.seq || 0);
      if (!seq || seq === lastIslandCardSeqRef.current) return;
      lastIslandCardSeqRef.current = seq;
      setIslandCard(card);
      setIslandCardVisible(true);
      if (islandCardTimerRef.current) clearTimeout(islandCardTimerRef.current);
      const ttl = Math.max(8000, Math.min(180000, Number(card.ttl_ms) || 45000));
      islandCardTimerRef.current = setTimeout(() => setIslandCardVisible(false), ttl);
    });
    return () => {
      try { unsub && unsub(); } catch (_) {}
      if (islandCardTimerRef.current) clearTimeout(islandCardTimerRef.current);
    };
  }, []);
  // Heartbeat: re-render once per second ONLY while the island is showing
  // something time-based (running stopwatch, playing track, live card), so
  // the pill is accurate to the second without a permanent global ticker.
  const _needIslandTick = !!(
    (swSnap && swSnap.running)
    || (mediaSession && mediaSession.active && mediaSession.playing)
    || islandCardVisible
  );
  useEffect(() => {
    if (!_needIslandTick) return;
    const id = setInterval(() => setIslandTick((t) => (t + 1) % 86400), 1000);
    return () => clearInterval(id);
  }, [_needIslandTick]);
  // Watchdog: a THINKING/SPEAKING pill must never outlive the actual work.
  // The stale-state self-heal in the WS subscriber catches most cases; this
  // is the hard backstop — if the HUD has been in `processing` for over 90s
  // (no real request runs that long without streaming something), or in
  // `speaking` for over 3 minutes, force the island back to idle.
  useEffect(() => {
    if (mode !== 'processing' && mode !== 'speaking') return;
    const enteredAt = Date.now();
    const limitMs = mode === 'processing' ? 90000 : 180000;
    const id = setInterval(() => {
      if (Date.now() - enteredAt > limitMs) {
        setMode((m) => (m === 'processing' || m === 'speaking') ? 'idle' : m);
      }
    }, 5000);
    return () => clearInterval(id);
  }, [mode]);
  const [hudNotifications, setHudNotifications] = useState([]);
  const lastPushedNoticeTsRef = useRef(null);
  const [activeTimers, setActiveTimers] = useState([]);
  const [timerDisplay, setTimerDisplay] = useState('');
  const [brainStatus, setBrainStatus] = useState(null);
  const [brainError, setBrainError] = useState(false);
  const [backendLoading, setBackendLoading] = useState(false);
  // 2026-06-08 — Universal Research Activity Feed + Brain Status hook.
  // Pulls activity_feed events + computed brain state from the bridge.
  // Theme-agnostic; both primary and aurora shells consume the same data.
  const activityFeedHook = window.SyrixActivityFeedHook
    ? window.SyrixActivityFeedHook()
    : { events: [], brainState: {}, hasUnread: false, markRead: () => {} };
  const activityFeedEvents = activityFeedHook.events || [];
  const activityBrainState = activityFeedHook.brainState || {};
  // Flash a Dynamic Island notice when the brain falls back or recovers.
  const lastFallbackTsRef = useRef(0);
  const lastRestoredTsRef = useRef(0);
  useEffect(() => {
    const fts = Number(activityBrainState.fallbackTs || 0);
    if (fts && fts !== lastFallbackTsRef.current) {
      lastFallbackTsRef.current = fts;
      const from = String(activityBrainState.fallbackFrom || 'primary').toUpperCase();
      const to = String(activityBrainState.active || 'next').toUpperCase();
      if (islandNoticeTimerRef.current) clearTimeout(islandNoticeTimerRef.current);
      setIslandNotice({
        tone: 'warn',
        text: `BRAIN FALLBACK · ${from}`,
        detail: `Routing to ${to} — primary will retry`,
      });
      islandNoticeTimerRef.current = setTimeout(() => setIslandNotice(null), 5000);
    }
    const rts = Number(activityBrainState.restoredTs || 0);
    if (rts && rts !== lastRestoredTsRef.current) {
      lastRestoredTsRef.current = rts;
      const active = String(activityBrainState.active || 'primary').toUpperCase();
      if (islandNoticeTimerRef.current) clearTimeout(islandNoticeTimerRef.current);
      setIslandNotice({
        tone: 'success',
        text: `BRAIN RESTORED · ${active}`,
        detail: 'Primary back online',
      });
      islandNoticeTimerRef.current = setTimeout(() => setIslandNotice(null), 4000);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activityBrainState.fallbackTs, activityBrainState.restoredTs]);
  const prevBackendStatusRef = useRef('connecting');
  const backendLoadingTimerRef = useRef(null);
  const islandNoticeTimerRef = useRef(null);

  useEffect(() => {
    if (!islandNotice) return;
    const key = islandNotice.ts || islandNotice.label || islandNotice.text || Date.now();
    if (key === lastPushedNoticeTsRef.current) return;
    lastPushedNoticeTsRef.current = key;
    const entry = {
      id: String(Date.now()) + '-' + Math.random().toString(36).slice(2),
      message: [islandNotice.label || islandNotice.text, islandNotice.detail].filter(Boolean).join(' — '),
      severity: islandNotice.tone === 'error' ? 'error' : islandNotice.tone === 'warn' ? 'warn' : 'info',
      time: Date.now(),
      seq: Date.now(),
    };
    setHudNotifications(prev => [entry, ...prev].slice(0, 100));
  }, [islandNotice]);

  useEffect(() => {
    const active = (activeTimers || []).filter(t => t && (t.due_ts || 0) > Date.now() / 1000);
    if (!active.length) { setTimerDisplay(''); return; }
    const nearest = active.reduce((a, b) => (a.due_ts < b.due_ts ? a : b));
    function tick() {
      const rem = Math.max(0, nearest.due_ts - Date.now() / 1000);
      const m = Math.floor(rem / 60);
      const s = Math.floor(rem % 60);
      setTimerDisplay(String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0'));
    }
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [activeTimers]);
  const lastIslandAckSeqRef = useRef(0);
  const lastIslandErrorRef = useRef('');
  // Rate-limit + dedupe the island ERROR toast. Without this, every WS tick
  // (~1.4 s) that re-reads a still-present last_error line spawns a fresh
  // 6-second toast, which under STT cuBLAS-error spam pinned the HUD into
  // a continuous "ERROR" flash. We allow the same error text at most once
  // every ISLAND_ERROR_REFIRE_MS, regardless of dedupe state.
  const lastIslandErrorTsRef = useRef(0);
  const ISLAND_ERROR_REFIRE_MS = 30000;
  // 2026-05-18: track new system-side island notices (CPU, memory, guardian)
  // by sequence so a repeated WS tick of the same notice does not refire.
  const lastIslandNoticeSeqRef = useRef(0);
  // Issue-7: track processing-slow island notices so they auto-clear on reply.
  const islandProcessingRef = useRef(false);
  const lastUserActivityRef = useRef(Date.now());
  const lastIdleActivityPingRef = useRef(0);
  useEffect(() => { brainManualRef.current = brainManual; }, [brainManual]);
  useEffect(() => { brainPendingRef.current = brainPending; }, [brainPending]);
  function canonicalBrainSelection(manual, nimPinned, forced) {
    const forceRaw = String(forced || '').trim();
    const manualRaw = String(manual || '').trim();
    const force = forceRaw.toLowerCase();
    const man = manualRaw.toLowerCase();
    const pinned = String(nimPinned || '').trim();
    const selected = force || man || 'auto';
    if (manualRaw && manualRaw !== 'auto' && manualRaw !== 'none' && !['groq', 'ollama', 'nvidia_nim', 'nim', 'nvidia'].includes(manualRaw.toLowerCase())) return manualRaw;
    if (selected === 'nvidia_nim' || selected === 'nim' || selected === 'nvidia') {
      return pinned || 'nvidia_nim';
    }
    if (selected === '' || selected === 'none' || selected === 'gemini') return 'auto';
    if (!force && manualRaw.includes('/')) return manualRaw;
    if (forceRaw.includes('/')) return forceRaw;
    return selected;
  }
  function clearBrainPending() {
    try { console.info('[HUD_EVENT] pending_cleared action=set_brain_provider target=' + String(brainPendingRef.current || '')); } catch (_) {}
    brainPendingRef.current = '';
    setBrainPending('');
    if (brainSwitchTimerRef.current) {
      window.clearTimeout(brainSwitchTimerRef.current);
      brainSwitchTimerRef.current = null;
    }
  }
  useEffect(() => {
    return () => {
      if (brainSwitchTimerRef.current) window.clearTimeout(brainSwitchTimerRef.current);
    };
  }, []);
  function applyConfirmedBrainSelection(next) {
    const confirmed = canonicalBrainSelection(next, '', '');
    const pending = String(brainPendingRef.current || '');
    if (pending) {
      if (confirmed === pending) {
        try { console.info('[BRAIN_TRACE] brain_selected brain=' + confirmed); } catch (_) {}
        clearBrainPending();
        setBrainSwitchError('');
        setBrainManual(confirmed);
        brainManualRef.current = confirmed;
      }
      return;
    }
    setBrainManual(confirmed);
    brainManualRef.current = confirmed;
  }
  useEffect(() => {
    function onBrainListEvt(ev) {
      const detail = (ev && ev.detail) || {};
      const { brains, manual, manual_brain_id, nim_preferred_model_id } = detail;
      const ver = Number(detail.state_version || 0);
      if (ver > 0 && ver < lastBrainStateVersionRef.current) {
        // Stale brain_list event arrived out of order — drop it.
        return;
      }
      if (ver > 0) lastBrainStateVersionRef.current = ver;
      if (Array.isArray(brains)) setBrainList(brains);
      if (manual !== undefined) {
        applyConfirmedBrainSelection(canonicalBrainSelection(manual_brain_id || manual, nim_preferred_model_id, ''));
      }
    }
    // 2026-05-27 — Per-click brain ack from backend. Clears pending spinner
    // immediately instead of waiting for the brain_list broadcast or the
    // 8-second timeout. Shows AUTH_REQUIRED / COOLDOWN / UNAVAILABLE inline
    // when the backend reports the destination provider is not usable.
    function onBrainAckEvt(ev) {
      const detail = (ev && ev.detail) || {};
      if (detail.stage !== 'done') return;
      const ok = !!detail.ok;
      const status = String(detail.status || (ok ? 'OK' : 'UNAVAILABLE'));
      const reason = String(detail.reason || '');
      if (ok) {
        // Backend committed the selection. brain_list will arrive next with
        // the canonical manual value; the ack is already a backend-confirmed
        // terminal state, so it may update the visible active brain too.
        const confirmed = canonicalBrainSelection(detail.priority || brainPendingRef.current || 'auto', '', '');
        setBrainManual(confirmed);
        brainManualRef.current = confirmed;
        clearBrainPending();
        setBrainSwitchError('');
      } else {
        clearBrainPending();
        const label = status + (reason ? (': ' + reason) : '');
        setBrainSwitchError(label);
      }
    }
    window.addEventListener('syrix-brain-list', onBrainListEvt);
    window.addEventListener('syrix-brain-select-ack', onBrainAckEvt);
    return () => {
      window.removeEventListener('syrix-brain-list', onBrainListEvt);
      window.removeEventListener('syrix-brain-select-ack', onBrainAckEvt);
    };
  }, []);

  useEffect(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    const unsub = window.SyrixBridge.subscribe((snap) => {
      const newStatus = snap.status || 'connecting';
      const prevStatus = prevBackendStatusRef.current;
      prevBackendStatusRef.current = newStatus;
      // Backend loading: show when transitioning from not-online to any state,
      // hide smoothly when reaching 'online'
      if (newStatus !== 'online' && prevStatus === 'online') {
        // Went offline
        setBackendLoading(true);
        if (backendLoadingTimerRef.current) clearTimeout(backendLoadingTimerRef.current);
      } else if (newStatus === 'online' && prevStatus !== 'online') {
        // Just came online — fade out loading after a brief moment for smooth transition
        backendLoadingTimerRef.current = setTimeout(() => {
          setBackendLoading(false);
        }, 600);
      } else if (newStatus !== 'online') {
        // Still offline — ensure loading is visible
        setBackendLoading(true);
      }
      setConn({
        status: newStatus,
        brain: snap.brain || '',
        model: snap.model || '',
      });
      setBattery({
        percent: typeof snap.battery_percent === 'number' ? snap.battery_percent : null,
        plugged: typeof snap.battery_plugged === 'boolean' ? snap.battery_plugged : null,
      });
      const openHudApps = Array.isArray(snap.syrix_hud_apps) ? snap.syrix_hud_apps : [];
      setHudApps(openHudApps);
      setActiveHudAppId(snap.syrix_active_hud_app || (openHudApps[0] && openHudApps[0].id) || null);
      const raw = snap.raw || {};
      const hudSettings = raw && raw.hud_settings && typeof raw.hud_settings === 'object' ? raw.hud_settings : {};
      const idleMinRaw = raw.syrix_user_idle_timeout_minutes ?? hudSettings.user_idle_timeout_minutes;
      const idleMin = Number(idleMinRaw);
      if (Number.isFinite(idleMin) && idleMin > 0) {
        setUserIdleTimeoutMinutes(Math.max(1, Math.min(240, idleMin)));
      }
      // Brain pin reconciliation from the heartbeat state file. This is a
      // best-effort hydration path that catches manual edits to config.json
      // and the cold-start window before a brain_list event arrives.
      //
      // It MUST NOT clobber a fresher brain_list broadcast that already
      // updated `lastBrainStateVersionRef.current`. We compare against the
      // state_version stamped on the snapshot — anything older than the
      // last brain_list we accepted is treated as stale and ignored. The
      // pending-click guard inside applyConfirmedBrainSelection provides
      // the additional protection while a click is in flight.
      const rawVer = Number(raw.state_version || 0);
      if (rawVer >= lastBrainStateVersionRef.current) {
        const rawManualField = raw.brain_manual_provider;
        const rawForcedField = raw.brain_force_priority;
        const rawManualBrainId = String(raw.brain_manual_id || '').trim();
        // Only reapply when the backend actually published a value. Treating
        // empty/undefined as 'auto' caused the historical snap-back: a
        // half-written heartbeat would force the UI back to Auto even after
        // the user clicked a real provider.
        const hasManual = rawManualField !== undefined && rawManualField !== null && String(rawManualField).trim() !== '';
        const hasForced = rawForcedField !== undefined && rawForcedField !== null && String(rawForcedField).trim() !== '';
        if (hasManual || hasForced || rawManualBrainId) {
          const rawManual = String(rawManualField || '').trim().toLowerCase();
          const rawForced = String(rawForcedField || '').trim().toLowerCase();
          const rawPinnedNim = String(raw.nim_preferred_model_id || '').trim();
          applyConfirmedBrainSelection(canonicalBrainSelection(rawManualBrainId || rawManual, rawPinnedNim, rawForced));
          if (rawVer > 0) lastBrainStateVersionRef.current = rawVer;
        }
      }
      if (snap.syrix_brain_status && typeof snap.syrix_brain_status === 'object') {
        setBrainStatus(snap.syrix_brain_status);
      }
      setBrainError(!!raw.brain_error);
      const ack = snap.last_widget_ack;
      // Activity-ping acks (mouse move / presence notes) must never surface
      // in the island — they are internal heartbeats, not user-visible outcomes.
      const _silentAckCmds = new Set([
        'hud_user_activity', 'presence_activity', 'typing_activity', 'hud_activity',
        // 2026-06-21 — routine voice/control toggles are self-evident from the
        // UI (mic icon, orb glow); their acks were popping a toast every time
        // ("OK · mute tts") which read as the island "randomly popping up".
        'mute_mic', 'unmute_mic', 'mute_tts', 'unmute_tts',
        'cancel_speech', 'stop_tts', 'wake', 'sleep', 'standby', 'toggle_ptt_mode',
      ]);
      if (ack && Number(ack.seq || 0) && Number(ack.seq || 0) !== lastIslandAckSeqRef.current) {
        lastIslandAckSeqRef.current = Number(ack.seq || 0);
        if (!islandOpen && !_silentAckCmds.has(String(ack.command || ''))) {
          if (islandNoticeTimerRef.current) clearTimeout(islandNoticeTimerRef.current);
          const notice = {
            tone: ack.ok ? 'ok' : 'error',
            text: `${ack.ok ? 'OK' : 'ERROR'} · ${String(ack.command || 'status').replace(/[_-]+/g, ' ')}`,
            detail: readableHudStatusDetail(ack.detail || '', ack.command || 'status', ack.ok),
          };
          setIslandNotice(notice);
          islandNoticeTimerRef.current = setTimeout(() => setIslandNotice(null), islandNoticeReadMs(notice));
        }
      }
      // 2026-05-18: System warnings (CPU > 90 %, memory pressure, guardian
      // alerts) arrive here via the bridge's `last_island_notice` slot.
      // Render them as an island toast with tone='warn' (amber) and do NOT
      // forward to the chat panel — system notices stay out of the conversation.
      const nt = snap.last_island_notice;
      if (nt && Number(nt.seq || 0) && Number(nt.seq) !== lastIslandNoticeSeqRef.current) {
        lastIslandNoticeSeqRef.current = Number(nt.seq);
        if (!islandOpen) {
          if (islandNoticeTimerRef.current) clearTimeout(islandNoticeTimerRef.current);
          const tone = (nt.tone === 'info' || nt.tone === 'ok' || nt.tone === 'error') ? nt.tone : 'warn';
          const label = (nt.kind || 'system').toUpperCase();
          const notice = {
            tone,
            text: label.replace(/[_-]+/g, ' '),
            detail: readableHudStatusDetail(nt.text || '', nt.kind || 'system', tone !== 'error'),
          };
          setIslandNotice(notice);
          islandNoticeTimerRef.current = setTimeout(() => setIslandNotice(null), islandNoticeReadMs(notice));
        }
      }
      // Issue-7: show a subtle "Processing…" island pill when a typed or voice
      // command takes longer than 3 s (bridge escalates chat_status to
      // 'processing_slow'). Clear it automatically when the status resets.
      const cs = snap.chat_status;
      const csState = cs && typeof cs === 'object' ? String(cs.state || '') : '';
      if (csState === 'processing_slow' && !islandProcessingRef.current && !islandOpen) {
        islandProcessingRef.current = true;
        if (islandNoticeTimerRef.current) clearTimeout(islandNoticeTimerRef.current);
        setIslandNotice({ tone: 'info', text: 'PROCESSING', detail: 'Working on it…' });
      } else if (csState !== 'processing_slow' && csState !== 'processing' && islandProcessingRef.current) {
        islandProcessingRef.current = false;
        setIslandNotice(null);
        if (islandNoticeTimerRef.current) { clearTimeout(islandNoticeTimerRef.current); islandNoticeTimerRef.current = null; }
      }
      const rawLastError = String((snap.raw && snap.raw.last_error) || '').trim();
      // Drop transient STT-side warnings that already self-recover (cuBLAS
      // CPU rebuild, legacy-fallback notice, etc.). Backend also suppresses
      // these in syrix_host_hud_wiring._last_error_from_logs; this is a
      // defensive second layer so an older Python build can't flood us.
      const lowErr = rawLastError.toLowerCase();
      const _suppressed = lowErr.includes('[stt] whisper_error')
        || lowErr.includes('[stt] whisper_fallback')
        || lowErr.includes('[stt] cuda_recovery_retry_failed')
        || lowErr.includes('[stt_cooldown]')
        || lowErr.includes('[gpu_guard] nvidia_smi_failed')
        || lowErr.includes('[brain_guard] gemini_skipped');
      const nowTs = Date.now();
      const isDifferent = rawLastError !== lastIslandErrorRef.current;
      const refireOk = isDifferent
        || (nowTs - lastIslandErrorTsRef.current >= ISLAND_ERROR_REFIRE_MS);
      // BUG-3 FIX: Parse the ACTUAL log level from the raw log line before
      // deciding how to display it. The old code always used tone:'error' and
      // text:'ERROR' regardless of the true log level — so an INFO-level line
      // written to last_error would flash up as a red ERROR pill.
      // Parse loguru pipe format: "YYYY-MM-DD HH:MM:SS[.ms] | LEVEL | ..."
      // Only show as ERROR if the pipe-field really says ERROR/CRITICAL.
      // Suppress INFO-level lines entirely (they are not errors).
      function _parseLastErrSeverity(raw) {
        const pipeM = String(raw || '').match(
          /^\d{4}-\d{2}-\d{2}[\sT]\d{1,2}:\d{2}:\d{2}[^|]*\|\s*(\w+)\s*\|/
        );
        if (pipeM) {
          const lvl = pipeM[1].toUpperCase();
          if (lvl === 'ERROR' || lvl === 'CRITICAL') return 'ERROR';
          if (lvl === 'WARNING' || lvl === 'WARN') return 'WARN';
          return 'INFO'; // e.g. INFO — not an error, skip the pill
        }
        // No pipe format — treat as error only if the text contains 'error'/'exception'/'traceback'
        const lo = String(raw || '').toLowerCase();
        if (lo.includes('error') || lo.includes('exception') || lo.includes('traceback')) return 'ERROR';
        if (lo.includes('warn')) return 'WARN';
        return 'INFO';
      }
      const _errSev = _parseLastErrSeverity(rawLastError);
      if (rawLastError && !_suppressed && refireOk && !islandOpen && _errSev !== 'INFO') {
        lastIslandErrorRef.current = rawLastError;
        lastIslandErrorTsRef.current = nowTs;
        if (islandNoticeTimerRef.current) clearTimeout(islandNoticeTimerRef.current);
        const _errTone = _errSev === 'ERROR' ? 'error' : 'warn';
        const notice = { tone: _errTone, text: _errSev, detail: rawLastError };
        setIslandNotice(notice);
        islandNoticeTimerRef.current = setTimeout(() => setIslandNotice(null), islandNoticeReadMs(notice));
      }
      setScreenVisionRevoked(!!snap.screen_vision_revoked);
      // Sync backend activation state → HUD mode so wake-word/double-clap
      // activates visually. Canonical Python voice states (see
      // intent_modules/state_controller.py):
      //   IDLE / ACTIVATED / LISTENING / PROCESSING / SPEAKING
      // Active states (anything except IDLE / SLEEP / empty) keep the HUD in
      // "listening" visuals and cancel any pending sleep timeout. IDLE/SLEEP
      // start a debounced transition back to sleep so the brief IDLE flicker
      // between consecutive STT capture iterations never drops the session.
      const bState = String(snap.state || '').toUpperCase();
      if (bState) setBackendVoiceState(bState);
      const sleepLocked = Date.now() < sleepLockUntilRef.current;
      const ACTIVE_STATES = new Set([
        'LISTENING', 'LISTEN', 'ACTIVATED', 'ACTIVE',
        'PROCESSING', 'THINKING', 'SPEAKING', 'INTERRUPTED',
      ]);
      let isActiveState = ACTIVE_STATES.has(bState) || bState.indexOf('LISTEN') >= 0;
      let isIdleState   = (bState === 'IDLE' || bState === '' || bState === 'SLEEP');
      // 2026-06-16 — Self-heal a STALE active state. The voice loop sometimes
      // leaves `state` at PROCESSING/SPEAKING after it actually finished, which
      // left the Dynamic Island (and orb) showing "THINKING"/"SPEAKING" while
      // SYRIX was really idle. Trust the concrete signals: is_thinking for
      // thinking, voice_state for speaking. Only downgrade when those are
      // explicitly negative (is_thinking === false), so a route that never sets
      // is_thinking can't be falsely forced idle.
      const _reallySpeaking = String(snap.voice_state || snap.tts_state || '').toUpperCase().indexOf('SPEAK') >= 0;
      // Derive whether a user message is actually awaiting a reply — if not,
      // any PROCESSING/THINKING state from the backend is stale (startup noise,
      // leftover from a previous request, or a route that forgot to clear state).
      const _chatLines = Array.isArray(snap?.chat_lines) ? snap.chat_lines : [];
      let _hasUserPending = false;
      for (let _ci = _chatLines.length - 1; _ci >= 0; _ci--) {
        const _cr = String(_chatLines[_ci]?.role || _chatLines[_ci]?.speaker || '').toLowerCase();
        if (_cr === 'user' || _cr === 'human') { _hasUserPending = true; break; }
        if (_cr === 'assistant' || _cr === 'syrix' || _cr === 'ai') break;
      }
      const _staleActive = !_reallySpeaking && (snap.is_thinking === false || !_hasUserPending)
        && (bState === 'PROCESSING' || bState === 'THINKING' || bState === 'SPEAKING');
      if (_staleActive) { isActiveState = false; isIdleState = true; }
      if (isActiveState && !sleepLocked) {
        // Backend is alive — cancel any pending sleep transition.
        if (sleepDebounceRef.current) {
          clearTimeout(sleepDebounceRef.current);
          sleepDebounceRef.current = null;
        }
        let voiceMode = (bState === 'PROCESSING' || bState === 'THINKING') ? 'processing'
          : bState === 'SPEAKING' ? 'speaking'
          // 2026-07-09 (Voice Mode V2 foundation pass) — a beat that reads
          // "cut off" (barge-in just aborted TTS), distinct from a fresh
          // LISTENING turn-start.
          : bState === 'INTERRUPTED' ? 'interrupted'
          : 'listening';
        // 2026-06-21 — When SYRIX is muted it cannot speak, so a lingering
        // SPEAKING state from the backend is always stale: never show it.
        if (ttsMutedRef.current && voiceMode === 'speaking') voiceMode = 'idle';
        // 2026-07-08 (voice-mode remodel) — same rule for the mic: when the mic
        // is muted SYRIX cannot be listening, so a lingering LISTENING state
        // (the ~30s Whisper-unload grace after exiting Voice Mode) must never
        // reach the Dynamic Island as "LISTENING".
        if (micMutedRef.current && voiceMode === 'listening') voiceMode = 'idle';
        // 2026-07-24 — Voice-state PRESENCE (thinking/processing + the "cut off"
        // interrupted beat) is a Voice-Mode-only concept, exactly like listening
        // and speaking above. When the mic is muted — normal chat, or right after
        // exiting Voice Mode — a lingering PROCESSING/INTERRUPTED tick (a typed
        // turn on the still-warm session, or the last voice turn's state before
        // the backend resets it) must NOT light the Dynamic Island or the frame
        // glow; the chat panel owns "thinking" there. This completes the
        // mute-coercion set so NO voice animation bleeds into normal chat.
        if (micMutedRef.current && (voiceMode === 'processing' || voiceMode === 'interrupted')) voiceMode = 'idle';
        setMode(voiceMode);
      } else if (isIdleState && (modeRef.current === 'listening' || modeRef.current === 'processing' || modeRef.current === 'speaking')) {
        // IDLE from the voice loop is not a standby request. Only an explicit
        // SLEEP backend state or a user sleep action may put the HUD in sleep.
        if (sleepDebounceRef.current) clearTimeout(sleepDebounceRef.current);
        sleepDebounceRef.current = setTimeout(() => {
          sleepDebounceRef.current = null;
          if (modeRef.current === 'listening' || modeRef.current === 'processing' || modeRef.current === 'speaking') setMode(bState === 'SLEEP' ? 'sleep' : 'idle');
        }, SLEEP_DEBOUNCE_MS);
      }
      // Telemetry gate: when system_telemetry_enabled is explicitly false, hide
      // all CPU/RAM/GPU stats (Task Manager owns them). Absent flag = show
      // (backward compatible with older backends).
      const _teleOn = snap.system_telemetry_enabled !== false;
      setOrbStats({
        cpu:        _teleOn && typeof snap.cpu_percent   === 'number' ? snap.cpu_percent   : null,
        ram:        _teleOn && typeof snap.mem_percent   === 'number' ? snap.mem_percent   : null,
        gpuUtil:    _teleOn && snap.gpu_util_percent != null ? snap.gpu_util_percent       : null,
        gpuTemp:    _teleOn && snap.gpu_temp_c        != null ? snap.gpu_temp_c            : null,
        voiceState: String(snap.state || 'IDLE').toUpperCase(),
        memNodes:   snap.memory_web ? (snap.memory_web.nodes || 0) : 0,
        taskCount:  (Array.isArray(snap.timers) ? snap.timers.filter(t => t && (t.due_ts || 0) > Date.now() / 1000).length : 0)
          + (Array.isArray(snap.syrix_tasks) ? snap.syrix_tasks.filter(t => t && ['queued', 'running'].includes(String(t.status || '').toLowerCase())).length : 0)
          // A backgrounded Supercomputer task counts too, so the collapsed pill
          // shows that something is still running without opening the island.
          + ((snap.syrix_supercomputer && snap.syrix_supercomputer.active
              && snap.syrix_supercomputer.detached
              && !['done', 'stopped', 'failed'].includes(
                String((snap.syrix_supercomputer.task || {}).state || '').toLowerCase())) ? 1 : 0),
      });
      setActiveTimers(
        Array.isArray(snap.timers)
          ? snap.timers.filter(t => t && (t.due_ts || 0) > Date.now() / 1000)
          : []
      );
      const nv = snap.nvidia;
      if (nv && nv.models && typeof nv.models === 'object') {
        const entries = Object.entries(nv.models);
        const onlineCount = entries.filter(([, m]) => m && m.status === 'online').length;
        setNim({
          online: onlineCount,
          total: entries.length,
          active: (nv.active && typeof nv.active === 'object') ? nv.active : null,
          lastTest: snap.last_brain_test || null,
          models: entries.map(([id, m]) => ({
            id,
            role: (m && m.role) || '',
            status: (m && m.status) || 'idle',
            lat: (m && m.last_latency_ms) || 0,
            reason: (m && (m.safe_reason || m.last_error_msg || m.last_error_type)) || '',
          })),
        });
      }
    });
    return () => {
      try { unsub && unsub(); } catch (_) {}
      if (islandNoticeTimerRef.current) clearTimeout(islandNoticeTimerRef.current);
      if (backendLoadingTimerRef.current) clearTimeout(backendLoadingTimerRef.current);
    };
  }, [islandOpen]);

  // Periodic network latency estimate for the orb stats ring.
  useEffect(() => {
    function measurePing() {
      try {
        const rtt = navigator.connection && navigator.connection.rtt;
        if (typeof rtt === 'number' && rtt > 0) { setNetPing(rtt); return; }
      } catch (_) {}
      const t0 = performance.now();
      // /cdn-cgi/trace is served by 1.1.1.1 itself. The bare host redirects
      // to one.one.one.one, which connect-src does not allow, so each ping
      // was refused and logged as an error; redirect: 'manual' keeps any
      // future redirect from being followed either.
      fetch('https://1.1.1.1/cdn-cgi/trace', { method: 'HEAD', mode: 'no-cors', cache: 'no-store', redirect: 'manual' })
        .then(() => setNetPing(Math.round(performance.now() - t0)))
        .catch(() => {});
    }
    measurePing();
    const id = setInterval(measurePing, 10000);
    return () => clearInterval(id);
  }, []);

  // Native system info — polled every 3s; shows accurate CPU/RAM/disk/hostname
  // regardless of whether the Python backend is running.
  useEffect(() => {
    let id = null;
    async function pollSysinfo() {
      try {
        const api = window.syrixHud;
        if (!api || !api.sysinfo) return;
        const r = await api.sysinfo();
        // Telemetry disabled (Task Manager owns stats): stop polling entirely.
        if (r && r.reason === 'telemetry_disabled') { if (id) { clearInterval(id); id = null; } return; }
        if (r && r.ok) setNativeSysinfo(r);
      } catch (_) {}
    }
    pollSysinfo();
    id = setInterval(pollSysinfo, 3000);
    return () => { if (id) clearInterval(id); };
  }, []);

  // Push active BrowserView below island panel so the dropdown renders above it.
  // (main.js ignores this while stage mode is active.)
  useEffect(() => {
    try { window.syrixHud?.islandOverlay?.(islandOpen); } catch (_) {}
  }, [islandOpen]);

  useEffect(() => {
    const previous = prevHudAppCountRef.current;
    if (previous > 0 && hudApps.length === 0) {
      setIslandOpen(false);
    }
    prevHudAppCountRef.current = hudApps.length;
  }, [hudApps.length]);

  // ── Stage mode wiring ──────────────────────────────────────────────────
  // Entry: collapse the classic island expansion (the island IS the nav bar
  // now). Exit: drop the stage-only dropdowns so nothing lingers.
  useEffect(() => {
    if (stageActive) {
      setIslandOpen(false);
      setIslandNotice(null);
    } else {
      setStageFolderOpen(false);
      setStageTabsOpen(false);
    }
  }, [stageActive]);

  // Escape always leaves a page that has taken over the window. The
  // toolbar's close button depends on a tab being registered; this does
  // not, so there is one way out that cannot be reached by a page opening
  // in a state the toolbar does not know about.
  useEffect(() => {
    if (!stageActive) return undefined;
    const onKey = (e) => {
      if (e.key !== 'Escape') return;
      const B = window.SyrixBridge;
      try { B && B.closeAllHudApps && B.closeAllHudApps(); } catch (_) {}
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [stageActive]);

  // Keep the stage omnibox in sync with the active tab's real URL.
  const _stageApp = hudApps.find((a) => a && a.id === activeHudAppId) || hudApps[0] || null;
  const _stageAppUrl = (_stageApp && _stageApp.url) || '';
  useEffect(() => { setStageUrl(_stageAppUrl); }, [_stageAppUrl, activeHudAppId]);

  // Overlay tags — any HUD panel that must paint above the staged browser
  // freezes it (snapshot swap) for exactly as long as it is open.
  useStageOverlay('widgets', stageActive && widgets.length > 0);
  useStageOverlay('widget-menu', stageActive && widgetMenu);
  useStageOverlay('settings', stageActive && settingsOpen);
  useStageOverlay('brain-menu', stageActive && nimOpen);
  useStageOverlay('stage-folder', stageActive && stageFolderOpen);
  useStageOverlay('stage-tabs', stageActive && stageTabsOpen);

  // Click-outside + Escape dismiss the stage dropdowns (folder / tabs).
  useEffect(() => {
    if (!stageFolderOpen && !stageTabsOpen) return;
    function onDoc(e) {
      if (e.target && e.target.closest
        && (e.target.closest('.p-stage-folder-panel') || e.target.closest('.p-stage-drop') || e.target.closest('.p-island'))) return;
      setStageFolderOpen(false);
      setStageTabsOpen(false);
    }
    function onKey(e) {
      if (e.key === 'Escape') { setStageFolderOpen(false); setStageTabsOpen(false); }
    }
    const t = setTimeout(() => document.addEventListener('click', onDoc), 10);
    window.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(t);
      document.removeEventListener('click', onDoc);
      window.removeEventListener('keydown', onKey);
    };
  }, [stageFolderOpen, stageTabsOpen]);

  /* Init 3D ball */
  useEffect(() => {
    if (!ballRef.current || !window.SyrixPrimaryBall) return;
    ballApi.current = window.SyrixPrimaryBall.createSyrixBall(ballRef.current);
    if (ballApi.current && ballApi.current.setTheme) ballApi.current.setTheme(hudBallTheme(hudTheme));
    return () => ballApi.current && ballApi.current.stop();
  }, []);

  /* Sync ball state */
  useEffect(() => {
    if (!ballApi.current) return;
    const s = mode === 'listening' ? 'listening'
      : mode === 'processing' ? 'processing'
      : mode === 'speaking' ? 'speaking'
      : mode === 'sleep' ? 'sleep'
      : 'idle';
    ballApi.current.setState(s);
  }, [mode]);

  /* Mic / Audio visualization — Safety pass (2026-05-17):
     The renderer-side mic is used ONLY for the orb's pulse visualization.
     Python runs the authoritative STT pipeline. To honour "no auto-start
     mic on boot", we start in simulation mode and only request real mic
     access AFTER an explicit user gesture (click/keydown) inside the HUD,
     or when Python signals an active listening session via the bridge.
     On HUD close / sleep / mode→sleep, we release the stream cleanly. */
  useEffect(() => {
    let raf;
    let analyser, dataArr;
    let realStream = null;
    let lastStateAt = 0;
    let lastEmitted = 0;
    let usingRealMic = false;
    let stopped = false;
    let simLevel = 0;

    function simLoop() {
      if (stopped) return;
      raf = requestAnimationFrame(() => setTimeout(simLoop, 90));
      const target = modeRef.current === 'listening' ? 0.16 + Math.random() * 0.24 : Math.random() * 0.045;
      simLevel += (target - simLevel) * 0.18;
      const v = simLevel;
      setMicLvl(v);
      if (ballApi.current) ballApi.current.setMicLevel(v);
    }

    function realLoop() {
      if (stopped) return;
      // If the user toggled mic mute while listening, stop the stream and
      // fall back to simulation so the mic LED goes dark immediately.
      if (micMutedRef.current) {
        try { realStream && realStream.getTracks().forEach(t => t.stop()); } catch (_) {}
        realStream = null;
        usingRealMic = false;
        cancelAnimationFrame(raf);
        simLoop();
        return;
      }
      raf = requestAnimationFrame(realLoop);
      if (!analyser || !dataArr) return;
      analyser.getByteTimeDomainData(dataArr);
      let sum = 0;
      for (let i = 0; i < dataArr.length; i++) {
        const v = (dataArr[i] - 128) / 128;
        sum += v * v;
      }
      const rms = Math.min(1, Math.sqrt(sum / dataArr.length) * 5);
      if (ballApi.current) ballApi.current.setMicLevel(rms);
      const now = performance.now();
      if (now - lastStateAt > 66 && Math.abs(rms - lastEmitted) > 0.012) {
        lastStateAt = now;
        lastEmitted = rms;
        setMicLvl(rms);
      }
    }

    async function activateRealMic() {
      if (usingRealMic || stopped || micMutedRef.current) return;
      try {
        const savedMicId = localStorage.getItem('syrix_mic_device_id') || '';
        const audioConstraint = savedMicId
          ? { audio: { deviceId: { ideal: savedMicId } } }
          : { audio: true };
        const stream = await navigator.mediaDevices.getUserMedia(audioConstraint);
        if (stopped) {
          try { stream.getTracks().forEach(t => t.stop()); } catch (_) {}
          return;
        }
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        ctx.createMediaStreamSource(stream).connect(analyser);
        dataArr = new Uint8Array(analyser.frequencyBinCount);
        realStream = stream;
        usingRealMic = true;
        cancelAnimationFrame(raf);
        realLoop();
      } catch (_) {
        // Mic denied or unavailable — stay in simulation mode.
      }
    }

    // Boot in simulation mode (no mic open).
    simLoop();

    // Gesture-gate: first deliberate interaction inside the HUD elevates to
    // real mic. Browsers also require this for AudioContext resume on some
    // platforms, so it doubles as a UX-friendly autoplay handshake.
    function onFirstGesture() {
      try { window.removeEventListener('pointerdown', onFirstGesture); } catch (_) {}
      try { window.removeEventListener('keydown', onFirstGesture); } catch (_) {}
      try { activateRealMic(); } catch (_) {}
    }
    window.addEventListener('pointerdown', onFirstGesture, { once: true });
    window.addEventListener('keydown',    onFirstGesture, { once: true });

    // Python-driven hand-off: when the backend pushes a "listening" state,
    // also try to elevate (covers the "wake word triggers but user never
    // clicked the HUD window" case).
    function onState(snap) {
      try {
        const vs = (snap && (snap.voice_state || (snap.raw && snap.raw.voice_state))) || '';
        if (!usingRealMic && (vs === 'LISTENING' || vs === 'SPEAKING')) {
          activateRealMic();
        }
        if (usingRealMic && vs === 'SLEEP' && realStream) {
          // Optional: keep mic on across SLEEP — Python decides who listens.
          // We deliberately do NOT close the renderer stream here because the
          // user may un-sleep and expect instant visualization. Closing happens
          // on app close (preload.onAppClosing) and component unmount.
        }
      } catch (_) {}
    }
    let unsubBridge = null;
    try {
      if (window.SyrixBridge && window.SyrixBridge.subscribe) {
        unsubBridge = window.SyrixBridge.subscribe(onState);
      }
    } catch (_) {}

    // Hand-off from Electron main when the window is about to close.
    let unsubClosing = null;
    try {
      if (window.syrixHud && window.syrixHud.onAppClosing) {
        unsubClosing = window.syrixHud.onAppClosing((payload) => {
          stopped = true;
          try { realStream && realStream.getTracks().forEach(t => t.stop()); } catch (_) {}
          realStream = null;
          try {
            if (window.SyrixBridge && window.SyrixBridge.notifyAppClosing) {
              window.SyrixBridge.notifyAppClosing(payload && payload.shutdown_id);
            }
          } catch (_) {}
        });
      }
    } catch (_) {}

    return () => {
      stopped = true;
      try { cancelAnimationFrame(raf); } catch (_) {}
      try { realStream && realStream.getTracks().forEach(t => t.stop()); } catch (_) {}
      realStream = null;
      try { unsubBridge && unsubBridge(); } catch (_) {}
      try { unsubClosing && unsubClosing(); } catch (_) {}
    };
  }, []);

  /* Voice recognition — OFF by default.
   *
   * The host's primary voice pipeline lives in Python (faster_whisper / Google
   * STT in syrix_host.py → enhanced_handle_command). Running a second
   * browser-side recognizer at the same time:
   *   - opens the mic twice (Windows can refuse the second handle),
   *   - sends transcripts to Google's cloud SR even when the user expects
   *     fully-local STT,
   *   - lets the HUD and Python disagree about what the user said.
   *
   * To remain compliant with the "no second competing voice system"
   * directive we leave this disabled. Voice commands still work because
   * Python is the sole voice authority, and the HUD now receives state
   * deltas through the bridge (snap.raw / WS). Flip the constant below to
   * `true` in this file (and the const at the top is the only place to
   * change) if you specifically want the browser-side recognizer back. */
  const HUD_BROWSER_SR_ENABLED = false;
  useEffect(() => {
    if (!HUD_BROWSER_SR_ENABLED) return;
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;
    const r = new SR();
    r.continuous = true;
    r.interimResults = true;
    r.lang = 'en-US';

    let lastTranscriptAt = 0;
    r.onresult = e => {
      const res = e.results[e.results.length - 1];
      const text = res[0].transcript.toLowerCase().trim();
      if (modeRef.current === 'listening') {
        const now = performance.now();
        if (now - lastTranscriptAt > 200 || res.isFinal) {
          lastTranscriptAt = now;
          setTranscript(text);
        }
      }
      if (res.isFinal) processVoice(text);
    };
    r.onerror = () => { try { r.start(); } catch (_) {} };
    r.onend = () => { try { r.start(); } catch (_) {} };
    try { r.start(); } catch (_) {}
    return () => { try { r.stop(); } catch (_) {} };
  }, []);

  function processVoice(cmd) {
    // In sleep mode ignore ALL voice — the backend is in standby and the mic
    // should stay silent. Wake only via orb click, never via voice command.
    if (modeRef.current === 'sleep') return;
    const B = window.SyrixBridge;
    const text = String(cmd || '').trim();
    if (!text) return;
    const low = text.toLowerCase();

    // 1. SYRIX local UI state (no backend needed).
    if (/^(?:hey\s+)?(?:syrix|computer)\s*[,!]?\s*$/.test(low)) { activateListen(); return; }
    if (/\bhey\s+syrix\b|\bdouble\s+tap\b|\bsyrix\s+wake\b|\bactivate\b/.test(low)) { activateListen(); B && B.send && B.send('wake'); return; }
    if (/\b(go\s+)?sleep\b|\bsyrix\s+sleep\b|\bgoodnight\b/.test(low)) { goSleep(); B && B.send && B.send('sleep'); return; }
    if (/\b(?:idle|stop\s+listen|be\s+quiet|deactivate)\b/.test(low)) { setMode('idle'); setTranscript(''); return; }

    // 3. Screen-Vision.
    if (/(?:toggle\s+screen\s+vision|share\s+screen|can\s+you\s+see\s+my\s+screen|look\s+at\s+(?:my\s+)?screen|what(?:'s|s|\s+is)\s+on\s+(?:my|the)\s+screen)/.test(low)) {
      requestScreenVision(/what|can\s+you\s+see|look\s+at/.test(low) ? 'describe' : 'open');
      return;
    }
    if (/\bread\s+(?:my\s+)?screen\b|\bocr\s+(?:my\s+)?screen\b/.test(low)) {
      requestScreenVision('read');
      return;
    }
    if (/\bdescribe\s+(?:my\s+)?screen\b/.test(low)) {
      requestScreenVision('describe');
      return;
    }
    if (/\bstop\s+looking\b|\bclose\s+screen\s+vision\b|\bdisable\s+screen\s+vision\b/.test(low)) {
      stopScreenVisionLocal();
      // Phase-2D: also close any open vision workspace surface.
      if (B && B.visionWorkspaceSet) {
        const ws = B.getSnapshot && B.getSnapshot().syrix_vision_workspace;
        const surface = (ws && ws.surface) || 'screen';
        B.visionWorkspaceSet(surface, 'close', 'normal');
      }
      return;
    }
    if (/\b(?:what(?:'s|\s+is)\s+this|what\s+am\s+i\s+holding|what\s+do\s+you\s+see|can\s+you\s+see\s+(?:me|this|that|it)|look\s+at\s+(?:me|this|that|it|the\s+camera|my\s+desk)|scan\s+(?:camera|this|that|my\s+desk)|check\s+(?:my\s+desk|this|that)|identify\s+(?:this|that|the\s+object)|read\s+this|read\s+(?:the\s+)?camera|describe\s+this|describe\s+(?:the\s+)?camera)\b/.test(low)) {
      if (screenVisionActive && /\bscreen|read\s+this|describe\s+this/.test(low)) {
        requestScreenVision(/read/.test(low) ? 'read' : 'describe');
      }
      return;
    }

    // 4. Diagnostic / self-audit
    if (/\b(?:run\s+system\s+check|system\s+check|run\s+diagnostics?|audit\s+yourself|what(?:'s|\s+is)\s+broken|status\s+report|self[-\s]test)\b/.test(low)) {
      addWidget('diagnostic');
      B && B.runDiagnostic && B.runDiagnostic();
      return;
    }

    // 4b. API status panel
    if (/\b(?:api\s+status|show\s+api|check\s+api(?:s)?|which\s+api(?:s)?|api\s+keys?|connected\s+api(?:s)?)\b/.test(low)) {
      addWidget('api_status');
      B && B.getApiStatus && B.getApiStatus();
      return;
    }

    // 5. Map / Directions — open Google Maps in the real browser.
    {
      const dirMatch = low.match(/\b(?:route|directions?|navigate)\s+(?:from\s+(.+?)\s+)?(?:to\s+(.+?))(?:\s+(?:please|now|today))?$/);
      if (dirMatch) {
        const fromTxt = (dirMatch[1] || '').trim();
        const toTxt = (dirMatch[2] || '').trim();
        if (toTxt && B && B.openMapDirections) {
          B.openMapDirections(fromTxt, toTxt);
          addWidget('map'); setCorner(true);
          return;
        }
      }
      const altDirMatch = low.match(/\bhow\s+(?:do\s+i\s+)?(?:get|drive|walk)\s+to\s+(.+?)(?:\s+(?:please|now))?$/);
      if (altDirMatch) {
        const toTxt = (altDirMatch[1] || '').trim();
        if (toTxt && B && B.openMapDirections) {
          B.openMapDirections('', toTxt);
          addWidget('map'); setCorner(true);
          return;
        }
      }
      const mapMatch = low.match(/\b(?:open|show|pull\s+up|display)\s+(?:(?:a|the|google)\s+)?(?:map|maps)(?:\s+(?:of|for|in)\s+(.+))?(?:\s+please)?$/);
      if (mapMatch || /\bgoogle\s+maps\b/.test(low) || /\bopen\s+map\s+of\b/.test(low)) {
        const q = mapMatch ? (mapMatch[1] || '').trim() : '';
        if (B && B.openMap) B.openMap(q);
        addWidget('map'); setCorner(true);
        return;
      }
      const whereMatch = low.match(/\bwhere\s+is\s+(.+?)(?:\s+(?:please|now))?$/);
      if (whereMatch) {
        const q = (whereMatch[1] || '').trim();
        if (B && B.openMap) B.openMap(q);
        addWidget('map'); setCorner(true);
        return;
      }
    }

    // 6. Timers
    {
      const setMatch = /\b(?:set|start|create|begin|run|make)\s+(?:a\s+|an\s+|the\s+)?(?:[\w\-]+\s+){0,4}?(?:countdown|timer|alarm)\b|\btimer\s+(?:for|of)\b|\bremind\s+me\s+(?:in|to)\b|\bwake\s+me\s+in\b/.test(low);
      if (setMatch) {
        // Try to extract duration.
        const seconds = (function () {
          const m = low.match(/(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b/);
          if (m) {
            const n = parseFloat(m[1]);
            const u = m[2];
            if (u.startsWith('h')) return n * 3600;
            if (u.startsWith('m') && !u.startsWith('milli')) return n * 60;
            if (u.startsWith('s')) return n;
          }
          if (/\bhalf\s+(?:an\s+)?hour\b/.test(low)) return 1800;
          if (/\bquarter\s+(?:an\s+)?hour\b/.test(low)) return 900;
          return 0;
        })();
        if (seconds > 0 && B && B.setTimer) {
          B.setTimer(seconds, '');
          addWidget('timer');
          return;
        }
      }
      if (/\b(?:cancel|stop|clear)\s+(?:my\s+|the\s+|all\s+)?(?:timer|timers|reminder|reminders|countdown|countdowns)\b/.test(low)) {
        B && B.cancelTimers && B.cancelTimers();
        return;
      }
      if (/\b(?:show|list)\s+(?:my\s+|all\s+|the\s+)?(?:timer|timers|reminder|reminders)\b|\bhow\s+long\s+(?:left|until|remaining)\b/.test(low)) {
        B && B.listTimers && B.listTimers();
        addWidget('timer');
        return;
      }
      if (/\bpause\s+(?:the\s+|my\s+)?(?:timer|countdown)\b/.test(low)) { B && B.pauseTimer && B.pauseTimer(); return; }
      if (/\bresume\s+(?:the\s+|my\s+)?(?:timer|countdown)\b/.test(low)) { B && B.resumeTimer && B.resumeTimer(); return; }
    }

    // 7. Music
    {
      if (/\b(?:pause|stop|halt)\s+(?:the\s+)?(?:music|song|track|playback)\b/.test(low)) {
        B && B.musicPause && B.musicPause();
        return;
      }
      if (/\b(?:resume|continue|unpause)\s+(?:the\s+)?(?:music|song|track|playback)\b/.test(low)) {
        B && B.musicResume && B.musicResume();
        return;
      }
      if (/\b(?:next|skip)(?:\s+(?:track|song|tune))?\b/.test(low)) {
        B && B.musicNext && B.musicNext();
        return;
      }
      if (/\b(?:previous|prev|rewind|last)\s+(?:track|song|tune)\b/.test(low)) {
        B && B.musicPrev && B.musicPrev();
        return;
      }
      if (/\b(?:stop|kill|end)\s+(?:the\s+)?music\b/.test(low)) {
        B && B.musicStop && B.musicStop();
        return;
      }
      const playMatch = low.match(/\bplay\s+(?:some\s+|a\s+|the\s+)?(?:music|song|playlist|track|tunes?|audio)\b(?:\s+(.+))?$/)
                     || low.match(/\bplay\s+(.+?)(?:\s+(?:on\s+spotify|on\s+youtube))?$/);
      if (playMatch && (/\bmusic\b|\bplay\b/.test(low))) {
        const q = (playMatch[1] || '').trim();
        B && B.musicPlay && B.musicPlay(q);
        addWidget('music');
        return;
      }
    }

    // 8. Volume
    {
      if (/\b(?:volume\s+up|louder|turn\s+up\s+(?:the\s+)?(?:volume|sound|audio))\b/.test(low)) { B && B.volumeAction && B.volumeAction('up'); return; }
      if (/\b(?:volume\s+down|quieter|turn\s+down\s+(?:the\s+)?(?:volume|sound|audio))\b/.test(low)) { B && B.volumeAction && B.volumeAction('down'); return; }
      if (/\bmute\b/.test(low)) { B && B.volumeAction && B.volumeAction('mute'); return; }
      if (/\bunmute\b/.test(low)) { B && B.volumeAction && B.volumeAction('unmute'); return; }
    }

    // 9. Weather
    {
      const wMatch = low.match(/\bweather\s+(?:in|for|at)\s+(.+?)(?:\s+(?:right\s+now|today|now|please))?$/);
      if (wMatch) {
        const c = (wMatch[1] || '').trim();
        if (c && B && B.weatherFetch) { B.weatherFetch(c); addWidget('weather'); return; }
      }
      if (/\b(?:open|show)\s+weather\b|\bcurrent\s+weather\b|^weather$/.test(low)) {
        B && B.weatherFetch && B.weatherFetch('London');
        addWidget('weather');
        return;
      }
    }

    // 10. Memory web
    if (/\b(?:show|open|display|refresh)\s+(?:me\s+)?(?:the\s+|my\s+)?memory(?:\s+web|\s+graph|\s+map)?\b|\bwhat\s+do\s+you\s+remember\b/.test(low)) {
      addWidget('memory');
      B && B.memoryWebRefresh && B.memoryWebRefresh();
      return;
    }

    // 11. Image generation
    if (/\b(?:generate|create|make|render|design|produce|draw|paint|sketch)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+|some\s+)?(?:image|images|picture|pictures|photo|photos|drawing|illustration|render|art|artwork|painting|sketch)\b/.test(low)
        && !/\b(?:stock|ticker|chart|tradingview)\b/.test(low)) {
      const m = low.match(/\b(?:generate|create|make|render|design|produce)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+|some\s+)?(?:image|images|picture|pictures|photo|photos|drawing|illustration|render|art|artwork|painting|sketch)\s+(?:of\s+)?(.+?)$/)
              || low.match(/\b(?:image|picture|photo)\s+of\s+(.+?)$/)
              || low.match(/\b(?:draw|paint|sketch)\s+(.+?)$/);
      const prompt = m ? (m[1] || '').trim() : '';
      if (prompt) {
        B && B.imageGenerate && B.imageGenerate(prompt);
        addWidget('image');
        return;
      }
    }

    // 11b. HUD App Dock voice commands. These run BEFORE the generic widget
    // shortcuts so "open YouTube" / "open Google Maps" land in the dock.
    {
      const appAliasRe =
        /^(?:open|launch|show|bring\s+up|pull\s+up)\s+(?:(?:the\s+|a\s+)?(?:hud\s+)?)?(youtube|youtube\.com|yt|google\s*maps?|maps|tradingview|trading\s*view|charts?|news|world\s*news|world\s*pulse|spotify(?:\s*web)?|github|chatgpt|openai|claude|gmail|inbox|calendar|drive|wikipedia|reddit|twitter|x\.com)(?:\s+in\s+(?:the\s+)?hud)?$/i;
      const m = low.match(appAliasRe);
      if (m && B && B.openAppInHud) {
        const target = m[1].toLowerCase()
          .replace(/\s+/g, '')
          .replace('googlemaps', 'maps')
          .replace('tradingview', 'tradingview');
        B.openAppInHud(target, { source: 'voice_command' });
        return;
      }
      // "open this in HUD" → open the most recently mentioned URL (best-effort: use Google search)
      if (/\bopen\s+this\s+in\s+(?:the\s+)?hud\b|\bin\s+hud\s+please\b/.test(low)) {
        if (B && B.openAppInHud) B.openAppInHud('https://www.google.com/', { source: 'voice_command' });
        return;
      }
      // Bare URL in command: "open https://nytimes.com"
      const urlMatch = low.match(/\bopen\s+(https?:\/\/[\w.\-]+(?:\/[^\s]*)?)/i);
      if (urlMatch && B && B.openAppInHud) {
        B.openAppInHud(urlMatch[1], { source: 'voice_command' });
        return;
      }
      // Generic "open X in HUD" — let resolver handle the alias / URL guess.
      const inHudMatch = low.match(/\bopen\s+(.+?)\s+in\s+(?:the\s+)?hud\b/);
      if (inHudMatch && B && B.openAppInHud) {
        B.openAppInHud(inHudMatch[1], { source: 'voice_command' });
        return;
      }
      // Dock control commands (apply to active app).
      const activeId = B && B.activeHudAppId && B.activeHudAppId();
      const hasOpenApp = !!(activeId && activeId !== '');
      if (/(?:close|kill|end|exit)\s+(?:this|that|the)?\s*(?:hud\s+)?app\b|\bclose\s+(?:the\s+)?(?:hud\s+)?app\b|\bclose\s+all\s+(?:hud\s+)?apps?\b/.test(low)) {
        if (B && B.closeAllHudApps && /\ball\s+/.test(low)) B.closeAllHudApps();
        else if (B && B.closeHudApp && hasOpenApp) B.closeHudApp(activeId);
        return;
      }
      if (hasOpenApp) {
        const dock = window.SyrixHudAppDispatch;
        if (/\bfullscreen(?:\s+this|\s+(?:the\s+)?app)?\b|\bmaximi[sz]e(?:\s+this)?\b/.test(low))  { B.fullscreenHudApp(activeId); return; }
        if (/\brestore(?:\s+this)?\b|\bun[\s-]?fullscreen\b|\bexit\s+fullscreen\b/.test(low))      { B.restoreHudApp(activeId); return; }
        if (/\bminimi[sz]e(?:\s+this|\s+(?:the\s+)?app)?\b/.test(low))                              { B.minimizeHudApp(activeId); return; }
        if (/\b(?:make\s+(?:it|this)\s+)?(?:bigger|larger|grow)\b/.test(low))                       { dock ? dock('bigger', null)  : null; return; }
        if (/\b(?:make\s+(?:it|this)\s+)?(?:smaller|shrink)\b/.test(low))                           { dock ? dock('smaller', null) : null; return; }
        if (/\brefresh(?:\s+this)?\b|\breload(?:\s+this)?\b/.test(low))                              { B.refreshHudApp(activeId); return; }
        if (/\bgo\s+back\b|\b(?:nav\s+)?back\b/.test(low))                                            { B.hudAppBack(activeId); return; }
        if (/\bgo\s+forward\b|\bnav\s+forward\b|\bforward\b/.test(low))                              { B.hudAppForward(activeId); return; }
        if (/\bpop\s+(?:this\s+)?out(?:\s+to\s+browser)?\b|\bopen\s+in\s+browser\b/.test(low))       { B.popoutHudApp(activeId); return; }
        if (/\blook\s+at\s+(?:this|that)\s+(?:hud\s+)?app\b|\bturn\s+on\s+hud\s+vision\b/.test(low)) { B.setHudAppVision(activeId, true); return; }
        if (/\bstop\s+looking\b|\bturn\s+off\s+hud\s+vision\b|\bdisable\s+hud\s+vision\b/.test(low)) { B.setHudAppVision(activeId, false); return; }
      }
    }

    // 11c. Phase-2C: Blender / 3D-model voice intents.
    //      Routed before HUD widget shortcuts so "open blender" doesn't
    //      collide with the generic "open" patterns above.
    {
      if (/\b(?:open|launch|start|fire\s+up|boot)\s+blender\b/.test(low)) {
        B && B.blenderOpen && B.blenderOpen();
        return;
      }
      const create = low.match(/\b(?:make|create|generate|build|model|design)\s+(?:a|an|the\s+)?(?:simple\s+|low[\s-]?poly\s+|procedural\s+)?(?:3[\s-]?d\s+|three[\s-]?d\s+)?([^.?!]{1,120}?)(?:\s+(?:in|inside|with|using)\s+blender)?\s*$/);
      if (create && /\b(?:3[\s-]?d|three[\s-]?d|model|blender|mesh|helmet|reactor|cube|sphere|gear|robot|spaceship|drone|skull|figurine)\b/.test(low)) {
        const subject = (create[1] || '').replace(/^(?:3[\s-]?d\s+|three[\s-]?d\s+|model\s+of\s+(?:a\s+|an\s+)?)/i, '').trim();
        if (subject && B && B.blenderCreate) {
          B.blenderCreate(subject);
          return;
        }
      }
      if (/\brotate\s+(?:the\s+)?(?:model|mesh|object|scene|view|camera)\b|\bspin\s+(?:the\s+)?(?:model|mesh|object)\b/.test(low)) {
        const axisMatch = low.match(/\baxis\s+(x|y|z)\b/);
        const degMatch = low.match(/\bby\s+(\d{1,3})\s*(?:deg|degrees?)?\b/);
        const axis = axisMatch ? axisMatch[1].toUpperCase() : 'Z';
        const degrees = degMatch ? parseFloat(degMatch[1]) : 30;
        B && B.blenderRotate && B.blenderRotate(axis, degrees);
        return;
      }
      if (/\bshow\s+(?:the\s+)?wireframe\b|\b(?:enable|toggle)\s+wireframe\b|\bwireframe\s+mode\b/.test(low)) {
        B && B.blenderWireframe && B.blenderWireframe(true);
        return;
      }
      if (/\bhide\s+(?:the\s+)?wireframe\b|\bdisable\s+wireframe\b/.test(low)) {
        B && B.blenderWireframe && B.blenderWireframe(false);
        return;
      }
      if (/\bexplode(?:d)?\s+view\b|\bexploded\s+(?:view|diagram)\b/.test(low)) {
        B && B.blenderExplode && B.blenderExplode(1.5);
        return;
      }
      const exp = low.match(/\bexport(?:\s+(?:the\s+)?(?:model|scene|mesh))?\s+(?:as|to)\s+(glb|gltf|obj|fbx|stl)\b/);
      if (exp) {
        B && B.blenderExport && B.blenderExport(exp[1]);
        return;
      }
      if (/\bopen\s+(?:the\s+)?(?:model|scene|mesh)\s+in\s+(?:the\s+)?(?:syrix\s+)?3[\s-]?d\s+viewer\b|\b(?:show|open)\s+(?:syrix\s+)?3[\s-]?d\s+viewer\b/.test(low)) {
        B && B.blenderOpenViewer && B.blenderOpenViewer();
        return;
      }
    }

    // 11e1. Phase-2D: SYRIX 3D Sandbox voice intents.
    {
      // 3D Sandbox lifecycle
      const sbCreate = low.match(/\b(?:create|add|make|spawn|render|draw)\s+(?:a\s+|an\s+|some\s+|the\s+)?(?:3[\s-]?d\s+|three[\s-]?d\s+)?(particles?|particle\s+field|arc\s+reactor(?:\s+preview)?|reactor\s+preview|glowing\s+sphere|wireframe\s+cube|wire\s+cube|neon\s+ring|torus(?:\s+knot)?|pyramid|hud\s+(?:cube|sphere|ring|grid|torus))\b/);
      if (sbCreate) {
        let subj = (sbCreate[1] || '').trim();
        const norm = subj
          .replace(/^particles?$/i, 'particles')
          .replace(/^particle\s+field$/i, 'particles')
          .replace(/^arc\s+reactor(?:\s+preview)?$/i, 'arc_reactor')
          .replace(/^reactor\s+preview$/i, 'arc_reactor')
          .replace(/^glowing\s+sphere$/i, 'glow_sphere')
          .replace(/^(?:wireframe|wire)\s+cube$/i, 'wire_cube')
          .replace(/^neon\s+ring$/i, 'neon_ring')
          .replace(/^torus\s+knot$/i, 'torus_knot')
          .replace(/\s+/g, '_').toLowerCase();
        if (B && B.sandboxCreate) B.sandboxCreate(norm);
        return;
      }
      if (/\bclear\s+(?:all\s+)?3[\s-]?d\s+(?:objects?|particles?|things?|stuff)\b|\b(?:clear|reset)\s+(?:the\s+)?sandbox\b/.test(low)) {
        if (B && B.sandboxClear) B.sandboxClear();
        return;
      }
      if (/\bexport\s+(?:this|it)?\s*(?:to|into)\s+blender\b|\bsend\s+this\s+to\s+blender\b/.test(low)) {
        if (B && B.sandboxExportBlender) B.sandboxExportBlender('');
        return;
      }
      if (/\bexplode\s+(?:it|this|that|view|the\s+(?:object|model|scene))\b|\bexploded\s+view\b/.test(low)) {
        if (B && B.sandboxAction) B.sandboxAction('explode');
        return;
      }
      if (/\bmake\s+(?:it|this|that|the\s+(?:object|model|sphere|cube|ring|torus))\s+(?:bigger|larger|huge|grow)\b/.test(low)) {
        if (B && B.sandboxAction) B.sandboxAction('bigger');
        return;
      }
      if (/\bmake\s+(?:it|this|that|the\s+(?:object|model|sphere|cube|ring|torus))\s+(?:smaller|tiny|shrink)\b/.test(low)) {
        if (B && B.sandboxAction) B.sandboxAction('smaller');
        return;
      }
      const sbMove = low.match(/\bmove\s+(?:it|this|that|the\s+(?:object|model|sphere|cube|ring|torus))\s+(left|right|up|down|forward|back(?:ward)?)\b/);
      if (sbMove) {
        const dir = (sbMove[1] || '').toLowerCase().replace(/^backward$/, 'back');
        if (B && B.sandboxAction) B.sandboxAction('move', { direction: dir });
        return;
      }
      if (/\brotate\s+(?:it|this|that|the\s+(?:object|model|sphere|cube|ring|torus|particles?|reactor))\b/.test(low)
          // Avoid colliding with Blender's rotate; that pattern requires the word "model"/"mesh"/"scene".
          && !/\brotate\s+the\s+(?:model|mesh|scene|view|camera)\b/.test(low)) {
        if (B && B.sandboxAction) B.sandboxAction('rotate');
        return;
      }
      if (/\bopen\s+(?:the\s+)?(?:3[\s-]?d\s+)?(?:sandbox|hud\s+3d|3d\s+layer|hud\s+3d\s+sandbox)\b|\b(?:enable|start|show)\s+(?:the\s+)?(?:syrix\s+)?(?:3[\s-]?d\s+)?(?:sandbox|hud\s+3d\s+layer)\b/.test(low)) {
        return;
      }
      if (/\b(?:close|disable|stop|hide|end|exit)\s+(?:the\s+)?(?:3[\s-]?d\s+)?(?:sandbox|hud\s+3d\s+layer)\b/.test(low)) {
        if (B && B.sandboxClear) B.sandboxClear();
        return;
      }
      if (/\bscreen\s+vision\s+on\b|\bshow\s+(?:me\s+)?what\s+you\s+see\b|\bopen\s+screen\s+vision\b/.test(low)) {
        requestScreenVision('open');
        return;
      }
      if (/\bscreen\s+vision\s+off\b|\bclose\s+screen\s+vision(?:\s+workspace)?\b/.test(low)) {
        stopScreenVisionLocal();
        return;
      }
    }


    // 12. HUD widget shortcuts.
    if (/\b(?:open|show)\s+(?:the\s+)?map\b/.test(low)) { addWidget('map'); setCorner(true); return; }
    if (/\b(?:open|show|start)\s+(?:the\s+)?(?:drawing|paint|canvas|sketch)\s+(?:widget|pad|canvas)?\b|\bpaint\s+widget\b|\bdrawing\s+widget\b/.test(low)) { addWidget('drawing'); return; }
    if (/\b(?:open|show)\s+(?:the\s+)?notes\b|\bnotepad\b/.test(low)) { addWidget('notes'); return; }
    if (/\b(?:open|show)\s+(?:the\s+)?diagnostics?\b/.test(low)) { addWidget('diagnostic'); return; }
    if (/\b(?:open|show)\s+chat\b|\bchat\s+with\s+syrix\b/.test(low)) { setChatOpen(true); return; }
    if (/\b(?:open|show)\s+settings\b|\bsettings\s+hud\b/.test(low)) { setSettingsOpen(true); return; }
    // 2026-05-18: PowerShell history widget. Voice/typed shortcuts.
    if (/\b(?:open|show)\s+(?:the\s+)?(?:powershell|ps|shell)\s+(?:log|history|widget)?\b/.test(low)) { addWidget('powershell'); return; }
    if (/\bpowershell\s+history\b/.test(low)) { addWidget('powershell'); return; }

    // 2026-05-23: SYRIX foundation-upgrades systems panel.
    if (/\b(?:open|show)\s+(?:the\s+)?(?:syrix\s+)?(?:systems|upgrades|foundation|backend\s+status|modules)\b/.test(low)) {
      addWidget('syrix_upgrades');
      return;
    }

    // 2026-05-24 (Pass-2): telemetry chart, memory graph.
    if (/\b(?:open|show)\s+(?:the\s+)?telemetry\s+(?:chart|graph|widget)?\b|\b(?:show|open)\s+(?:system\s+)?metrics\s+chart\b/.test(low)) {
      addWidget('telemetry_chart'); return;
    }
    if (/\b(?:open|show)\s+(?:the\s+)?memory\s+(?:graph|map|flow)\b|\b(?:open|show)\s+react\s+flow\b/.test(low)) {
      addWidget('memory_graph'); return;
    }

    // 12b. Setup image — open on-demand setup widget (local PNG, not app dock).
    if (/\b(?:show|open|tell\s+me\s+about|display|view)\s+(?:my\s+)?(?:pc\s+)?setup\b|\bopen\s+(?:my\s+)?setup\s+(?:picture|image|photo)\b|\bshow\s+setup\s+(?:picture|image|photo)\b|\bkabirr\s*(?:kohli\s*)?(?:'s\s+)?setup\b|\bwhat\s+(?:is\s+)?(?:my\s+|kabirr'?s?\s+)?(?:pc\s+)?setup\b|\bshow\s+setup\s+image\b/.test(low)) {
      addWidget('setup');
      if (B && B.showSetupImage) B.showSetupImage();
      return;
    }

    // 13. Fallback — forward to backend as typed_text so the host chat handles it.
    if (text.length > 1 && B && B.chat) { B.chat({ text, voice: true }).catch(() => {}); }
  }

  // Debounce guard for orb-click activation. A rapid double-click should
  // NOT spawn a second backend session — the Python side has its own
  // session-state re-entry guard, but we also debounce here so the WS
  // doesn't get spammed with redundant activate_from_hud frames.
  const lastOrbActivateRef = useRef(0);
  const activationPendingRef = useRef(false);
  const ORB_ACTIVATE_DEBOUNCE_MS = 700;

  function activateListen() {
    // 2026-07-09 — the old "activating" flag drove a brightness-flash-to-
    // white animation on the home-screen orb (.p-ball-wrap.activating,
    // removed below in CSS) from before the orb moved into Voice Mode. The
    // flag itself was already dead (never read anywhere in this file) —
    // removed for good along with the CSS so it can't be re-wired by
    // accident later.
    activationPendingRef.current = true;
    setTimeout(() => {
      activationPendingRef.current = false;
    }, 900);
    setMode('listening');
    setTranscript('');
  }

  // Orb-click activation → wakes SYRIX from sleep/standby the same way
  // double-clap and wake word do. Sends `activate_from_hud` to the backend
  // (syrix_host.request_hud_activation → main loop activation queue →
  // session_worker thread). Debounced so rapid double-clicks land as a
  // single session. If a session is already running this is a visual no-op
  // because the backend's session-state guard refuses the duplicate.
  function activateFromOrbClick(sourceTag) {
    const now = Date.now();
    if (now - lastOrbActivateRef.current < ORB_ACTIVATE_DEBOUNCE_MS) return false;
    lastOrbActivateRef.current = now;
    // Drop any pending sleep transition — we're explicitly waking now.
    sleepLockUntilRef.current = 0;
    if (sleepDebounceRef.current) {
      clearTimeout(sleepDebounceRef.current);
      sleepDebounceRef.current = null;
    }
    activateListen();
    try {
      const B = window.SyrixBridge;
      if (B && B.send) {
        B.send('activate_from_hud', { data: { source: String(sourceTag || 'hud_orb') } });
      }
    } catch (_) { /* swallow — visual mode already flipped to listening */ }
    return true;
  }

  function goSleep() {
    // Engage sleep lock so any in-flight LISTENING bridge tick can't flip
    // the HUD back to listening immediately after the user asked for sleep.
    sleepLockUntilRef.current = Date.now() + SLEEP_LOCK_MS;
    if (sleepDebounceRef.current) {
      clearTimeout(sleepDebounceRef.current);
      sleepDebounceRef.current = null;
    }
    // Trigger the bracket/logo sleep animation for ~1.2s
    setSleepingAnim(true);
    setTimeout(() => setSleepingAnim(false), 1200);
    setMode('sleep');
    setTranscript('');
  }

  function requestScreenVision(action) {
    // The `screen_vision_revoked` flag is the BACKEND privacy gate — it stops
    // passive/guardian screen captures. The UI uses getDisplayMedia which
    // always shows a fresh OS permission prompt, so an explicit user click
    // must NEVER be silently blocked. If the backend flag is set, we ask the
    // backend to clear it (since the click is the user's explicit consent),
    // then proceed.
    const B = window.SyrixBridge;
    if (screenVisionRevoked && action !== 'stop') {
      try {
        if (B && B.send) B.send('screen_vision_toggle');
      } catch (_) {}
      setScreenVisionRevoked(false);
    }
    const mode = action === 'widget' ? 'minimized' : 'fullscreen';
    setScreenVisionActive(action !== 'stop');
    setSvAck(action === 'stop' ? 'stopped' : 'starting');
    try {
      if (B && B.visionWorkspaceSet) B.visionWorkspaceSet('screen', action === 'stop' ? 'close' : mode, mode);
    } catch (_) {}
    try {
      window.dispatchEvent(new CustomEvent('syrix-screen-vision-request', {
        detail: { action: action || 'open', mode },
      }));
    } catch (_) {}
    setTimeout(() => setSvAck(''), 1800);
  }

  function stopScreenVisionLocal() {
    const B = window.SyrixBridge;
    setScreenVisionActive(false);
    try { if (B && B.visionWorkspaceSet) B.visionWorkspaceSet('screen', 'close', 'normal'); } catch (_) {}
    try { window.dispatchEvent(new CustomEvent('syrix-screen-vision-stop')); } catch (_) {}
    setSvAck('stopped');
    setTimeout(() => setSvAck(''), 1600);
  }

  useEffect(() => {
    try { document.body.classList.toggle('body--screen-vision-active', !!screenVisionActive); } catch (_) {}
    return () => { try { document.body.classList.remove('body--screen-vision-active'); } catch (_) {} };
  }, [screenVisionActive]);

  useEffect(() => {
    function onScreenVisionState(ev) {
      const d = ev && ev.detail;
      if (!d || typeof d !== 'object') return;
      setScreenVisionActive(!!d.active);
      if (d.message) {
        setSvAck(String(d.message).slice(0, 22));
        setTimeout(() => setSvAck(''), 1800);
      }
    }
    window.addEventListener('syrix-screen-vision-state', onScreenVisionState);
    return () => window.removeEventListener('syrix-screen-vision-state', onScreenVisionState);
  }, []);

  function addWidget(type) {
    setWidgets(ws => ws.some(w => w.type === type) ? ws : [...ws, { id: 'w-' + Date.now(), type }]);
    setWidgetMenu(false);
  }

  // Listen for Python-triggered widget open requests (e.g. "show my setup" from voice).
  const lastOpenWidgetSeq = useRef(0);
  useEffect(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((snap) => {
      const ow = snap.syrix_open_widget;
      if (!ow || typeof ow !== 'object') return;
      const seq = Number(ow.seq || 0);
      if (!seq || seq === lastOpenWidgetSeq.current) return;
      lastOpenWidgetSeq.current = seq;
      const type = String(ow.type || '').trim();
      if (type) addWidget(type);
    });
  }, []);

  // Idle health probe — emits one line every 60s with the live picture of
  // what's still consuming resources. Lets us audit screen-vision leaks at a
  // glance from DevTools console without instrumenting every subsystem. The
  // probe itself is ~50µs per minute and never writes to disk.
  useEffect(() => {
    function snapshotHealth() {
      let ramMb = -1;
      try {
        if (window.performance && window.performance.memory) {
          ramMb = Math.round(window.performance.memory.usedJSHeapSize / (1024 * 1024));
        }
      } catch (_) {}
      let ws = null;
      try { ws = (window.SyrixBridge && window.SyrixBridge.getSnapshot && window.SyrixBridge.getSnapshot().syrix_vision_workspace) || null; } catch (_) {}
      const sandboxApi = window.SyrixSandbox3DApi || null;
      const sandboxAlive = !!(sandboxApi && sandboxApi.isRunning && sandboxApi.isRunning());
      const screenLive = !!(ws && ws.surface === 'screen' && ws.open);
      try {
        console.log('[IDLE_HEALTH] ram_mb=' + ramMb +
          ' screen=' + (screenLive ? 'on' : 'off') +
          ' sandbox=' + (sandboxAlive ? 'on' : 'off') +
          ' hidden=' + (document.hidden ? 'yes' : 'no'));
      } catch (_) {}
    }
    snapshotHealth();
    const id = setInterval(snapshotHealth, 60000);
    return () => clearInterval(id);
  }, []);

  function removeWidget(id) {
    setWidgets(ws => {
      const remaining = ws.filter(w => w.id !== id);
      // Only release the explicit corner flag if no other large-layout
      // condition still needs the orb docked. The render-time derivation in
      // p-ball-wrap is the real source of truth; this just keeps the
      // explicit flag from sticking when nothing else needs it. Closing one
      // widget must never recenter the orb while screen vision / hud-app /
      // a second widget is still active.
      const hasMap = remaining.some(w => w.type === 'map');
      const stillNeedsCorner =
        hasMap
        || remaining.length >= 2;
      if (!stillNeedsCorner) setCorner(false);
      return remaining;
    });
    setFocusedWidgetId(f => f === id ? null : f);
  }

  /* Close widget menu on outside click */
  useEffect(() => {
    if (!widgetMenu) return;
    function handler(e) {
      if (!e.target.closest('.p-widget-menu') && !e.target.closest('[data-wm-btn]')) setWidgetMenu(false);
    }
    setTimeout(() => document.addEventListener('click', handler), 10);
    return () => document.removeEventListener('click', handler);
  }, [widgetMenu]);

  useEffect(() => {
    const b = document.body;
    b.className = b.className.replace(/\btheme-\S+/g, '').trim();
    b.classList.add('theme-aurora-glass');
    if (ballApi.current && ballApi.current.setTheme) ballApi.current.setTheme(hudBallTheme());
  }, [hudTheme]);

  /* ── Palette ──────────────────────────────────────────────────────────────
     The shell is one theme; the palette is the colour it is painted in.
     'aurora' is the champagne default, 'dark' a plain neutral dark with no
     amber in it. Every accented surface reads --eg-accent-rgb, so this is a
     class on <body> and nothing else. */
  const [palette, setPalette] = useState(() => {
    try { return localStorage.getItem('syrix_palette') === 'dark' ? 'dark' : 'aurora'; }
    catch (_) { return 'aurora'; }
  });
  useEffect(() => {
    document.body.classList.toggle('pal-dark', palette === 'dark');
    document.documentElement.classList.toggle('pal-dark', palette === 'dark');
  }, [palette]);
  function selectPalette(p) {
    const next = p === 'dark' ? 'dark' : 'aurora';
    // The class is set inside the transition so the crossfade captures the
    // new palette; the effect above then finds it already applied.
    const apply = () => { document.body.classList.toggle('pal-dark', next === 'dark'); setPalette(next); };
    if (window.SyrixSmooth) window.SyrixSmooth(apply); else apply();
    try { localStorage.setItem('syrix_palette', next); } catch (_) {}
  }
  // The sidebar's account menu switches the palette without owning it.
  useEffect(() => {
    const onSet = (e) => selectPalette(e && e.detail && e.detail.palette);
    window.addEventListener('syrix-palette-set', onSet);
    return () => window.removeEventListener('syrix-palette-set', onSet);
  }, []);

  const timeStr = time
    .toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    })
    .replace(/\s?[AP]M$/i, '');
  const dateStr = time.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' }).toUpperCase();
  const focusedWidget = widgets.find(w => w.id === focusedWidgetId) || null;
  const focusType = focusedWidget ? focusedWidget.type : '';
  const focusContext = focusType === 'timer' ? 'timer'
    : focusType === 'notes' ? 'memory'
    : focusType === 'map' ? 'vision'
    : focusType || '';
  const rootClass = [
    hudEntered ? 'hud-entered' : 'hud-entering',
    mode === 'sleep'       ? 'is-sleeping'
      : mode === 'listening'  ? 'is-listening'
      : mode === 'processing' ? 'is-processing'
      : mode === 'speaking'   ? 'is-speaking'
      : mode === 'interrupted' ? 'is-interrupted'
      : '',
    focusContext ? 'is-widget-focus focus-' + focusContext : '',
  ].filter(Boolean).join(' ');

  const statusLabel = {
    sleep: 'SLEEP MODE',
    idle: 'IDLE · VOICE READY',
    listening: '● LISTENING',
    processing: '◈ PROCESSING',
    speaking: '◉ SPEAKING',
    interrupted: '◈ INTERRUPTED',
  }[mode] || 'IDLE';
  const isOffline = conn.status === 'offline';
  const isConnecting = conn.status === 'connecting';
  const isReconnecting = conn.status === 'reconnecting';
  const hudAppCount = hudApps.length;
  const activeHudApp = hudApps.find((app) => app && app.id === activeHudAppId) || hudApps[0] || null;
  // Stage mode replaces the tabs presentation entirely (island = nav bar).
  const islandTabMode = hudAppCount > 0 && !stageActive;
  const timerWidgetOpen = widgets.some(w => w.type === 'timer');
  const musicWidgetOpen = widgets.some(w => w.type === 'music');
  const islandLiveTimer = !!timerDisplay && !timerWidgetOpen;
  // ── Island activity sessions — any active-but-not-stopped task whose widget
  // is closed migrates into the island (iOS Live Activities model). Read the
  // stopwatch store fresh each render so the 1s heartbeat shows true elapsed.
  const stopwatchSnap = window.SyrixStopwatch
    ? window.SyrixStopwatch.get()
    : (swSnap || { running: false, elapsedMs: 0 });
  const stopwatchLive = !timerWidgetOpen && !!(stopwatchSnap.running || stopwatchSnap.elapsedMs > 0);
  const mediaLive = !musicWidgetOpen && !!(mediaSession && mediaSession.active) && !mediaDismissed;
  const activeAgentTasks = agentTasks.filter((task) => task && ['queued', 'running'].includes(String(task.status || '').toLowerCase()));
  const watchCount = activeAgentTasks.length;
  const classicActivityCount =
    (mediaLive ? 1 : 0) + (islandLiveTimer ? 1 : 0) + (stopwatchLive ? 1 : 0);
  const islandActivityCount =
    classicActivityCount + watchCount;
  // Cards + classic expansion are suppressed while the island is the stage
  // nav bar; everything returns the moment stage ends.
  const islandCardShowing = !stageActive && !!(islandCardVisible && islandCard);
  /* A rich card takes the island over ONLY in widgets mode. In status/name
     mode it still announces itself in the pill, but it does not open itself
     over the HUD — that auto-expansion is the thing widgets mode is for. */
  const islandExpanded = !stageActive && (islandOpen || (islandCardShowing && islandMode === 'widgets'));
  // Card history list minus whatever's currently showing live, so the panel
  // never lists the same card twice.
  const islandCardHistoryList = islandCardHistory.filter(
    (h) => !islandCardShowing || h.seq !== islandCard.seq
  );
  const islandTone = isOffline
    ? 'error'
    : (voiceOffPhase === 'dock' || voiceOffPhase === 'text' || voiceOffPhase === 'restore-fade' || voiceOffPhase === 'restore-move')
      ? 'standby'
    : isConnecting || isReconnecting
      ? 'standby'
      : (islandTabMode || islandActivityCount > 0 || islandCardShowing)
        ? 'online'
      : mode === 'sleep'
        ? 'standby'
        : 'online';
  const islandCardPillText = islandCardShowing
    ? (islandCard.kind === 'sports'
      ? (islandCard.live ? 'LIVE · ' : 'SCORES · ') + String(islandCard.league_label || islandCard.league || '').toUpperCase().slice(0, 18)
      : String(islandCard.title || 'INFO').toUpperCase().slice(0, 26))
    : '';
  const islandMediaPillText = mediaLive
    ? '♪ ' + (String(mediaSession.track || 'PLAYING').slice(0, 24) || 'PLAYING')
    : '';
  const islandStopwatchPillText = stopwatchLive
    ? '⏱ ' + (window.SyrixStopwatch && window.SyrixStopwatch.format
      ? window.SyrixStopwatch.format(stopwatchSnap.elapsedMs)
      : Math.floor(stopwatchSnap.elapsedMs / 1000) + 's')
    : '';
  const islandWatchPillText = watchCount > 0 ? `◐ ${watchCount} WATCHING` : '';
  const islandQuietText = islandMode === 'name' ? ownerName.toUpperCase() : 'SYRIX';
  /* Whether opening the island would reveal anything at all.

     Clicking it used to expand into an empty rectangle whenever there was no
     timer, no music, no card and no activity — which is most of the time. An
     expand that reveals nothing reads as a broken control, so when there is
     no live detail the panel shows the state in words instead: what SYRIX is
     doing, which engine is selected, and what the island is set to show. */
  const islandHasLiveDetail = !!(
    islandCardShowing
    || classicActivityCount > 0
    || agentTasks.length > 0
    || islandTabMode
    || (islandCardHistoryList && islandCardHistoryList.length > 0)
    || (activityFeedEvents && activityFeedEvents.length > 0)
  );
  /* The selected chat model. This file does not own that state — the shell
     does — so it is read from the key the shell persists it to rather than
     from a variable that does not exist here. */
  const islandEngine = (() => {
    try {
      const v = String(localStorage.getItem('syrix_chat_model_v1') || '').toLowerCase();
      if (v === 'zenith') return 'ZENITH 1.1';
      if (v === 'auto') return 'AUTO';
    } catch (_) {}
    return 'SPARK 1.1';
  })();
  const islandStateWords = isOffline ? 'Offline'
    : isReconnecting ? 'Reconnecting to the SYRIX backend'
    : isConnecting ? 'Connecting'
    : mode === 'processing' ? 'Working on your question'
    : mode === 'speaking' ? 'Speaking'
    : mode === 'listening' ? 'Listening'
    : mode === 'sleep' ? 'Asleep — say the wake word or type to wake'
    : 'Connected and idle';
  // Pill text priority: voice-off → connection truth → live voice states →
  // rich card → activities (timer is most urgent, then stopwatch, then music)
  // → tabs → sleep/idle. Connection + voice states always outrank activities
  // so the pill never lies about what SYRIX is doing right now.
  // When 2+ activities are live at once, the center text steps back to the
  // plain mode label — each activity gets its own readable chip in the
  // activity zone instead of one winning the text slot and the rest being
  // reduced to unlabeled dots.
  /* Name mode is absolute. Picking it should change the island there and
     then, not "once nothing else is happening" — with a reconnect in progress
     that meant it never changed at all, which reads as a broken setting.

     Nothing is hidden by this: the state icon on the left still carries the
     connection and voice state in every mode, so the island can say whose
     machine this is without ever misreporting what SYRIX is doing. */
  const islandText = islandMode === 'name' && !stageActive ? ownerName.toUpperCase()
    : voiceOffPhase === 'dock' ? ''
    // 2026-07-09 — the island no longer announces "SYRIX VOICE MODE IS
    // OFF/ON": Voice Mode enter/exit (and the legacy quiet-mic mute) should
    // be silent, not narrated.
    : voiceOffPhase === 'text' ? ''
    : (voiceOffPhase === 'restore-fade' || voiceOffPhase === 'restore-move') ? ''
    : isOffline ? 'OFFLINE'
    : isReconnecting ? 'RECONNECTING'
    : isConnecting ? 'CONNECTING'
    : mode === 'processing' ? 'THINKING'
    : mode === 'speaking' ? 'SPEAKING'
    : mode === 'listening' ? 'LISTENING'
    : islandCardShowing ? islandCardPillText
    : islandActivityCount > 1 ? (islandTabMode ? `TABS · ${hudAppCount}` : watchCount > 0 ? islandWatchPillText : 'ACTIVE')
    : islandLiveTimer ? timerDisplay
    : stopwatchLive && stopwatchSnap.running ? islandStopwatchPillText
    : mediaLive ? islandMediaPillText
    : watchCount > 0 ? islandWatchPillText
    : stopwatchLive ? islandStopwatchPillText
    : islandTabMode ? `TABS · ${hudAppCount}`
    // The quiet state — the only slot the island mode gets to decide.
    : mode === 'sleep' ? islandQuietText
    : mode === 'idle' ? islandQuietText
    : 'ACTIVE';
  // Stable key for the pill text span: ticking values (timer/stopwatch) keep
  // one key so the crossfade animation fires on label CHANGES, not every second.
  const islandTextKey = (mode === 'processing' || mode === 'speaking' || mode === 'listening') ? islandText
    : islandCardShowing ? 'card-' + String(islandCard.seq || islandCard.kind)
    : islandActivityCount > 1 ? islandText
    : islandLiveTimer ? 'live-timer'
    : stopwatchLive ? 'live-stopwatch'
    : mediaLive ? 'live-media-' + String(mediaSession.track || '')
    : islandText;
  const islandBarScale =
    mode === 'listening'  ? Math.max(0.18, Math.min(1, micLvl * 1.9))
    : mode === 'speaking'   ? 0.58
    : mode === 'processing' ? 0.22
    : 0.14;

  const islandModeClass = (voiceOffPhase === 'dock' || voiceOffPhase === 'text' || voiceOffPhase === 'restore-fade' || voiceOffPhase === 'restore-move') ? ' mode-voice-off'
    : mode === 'listening'  ? ' mode-listening'
    : mode === 'speaking'   ? ' mode-speaking'
    : mode === 'processing' ? ' mode-processing'
    : mode === 'sleep'      ? ' mode-sleep'
    : '';

  const islandShowTimer = islandLiveTimer; /* only when timer widget is closed */
  function dismissIslandCard() {
    setIslandCardVisible(false);
    if (islandCardTimerRef.current) { clearTimeout(islandCardTimerRef.current); islandCardTimerRef.current = null; }
  }
  // Reopen a past card from CARD HISTORY — history rows already ARE full
  // card payloads, so this just re-feeds one through the same live slot.
  function reopenIslandCard(h) {
    if (!h || !h.kind) return;
    lastIslandCardSeqRef.current = Number(h.seq || 0) - 1; // allow re-showing the same seq
    setIslandCard(h);
    setIslandCardVisible(true);
    if (islandCardTimerRef.current) clearTimeout(islandCardTimerRef.current);
    const ttl = Math.max(8000, Math.min(180000, Number(h.ttl_ms) || 45000));
    islandCardTimerRef.current = setTimeout(() => setIslandCardVisible(false), ttl);
  }

  /* Geometry. In stage mode the island IS the browser toolbar and keeps the
     top-centre bar. In the normal HUD it docks to the foot of the sidebar
     instead of floating over the middle of the top edge: the conversation now
     owns the centre of the window, and a status pill hovering above it was
     competing with the answer for the same space.

     Anchored by `bottom`, not `top` — a fixed box pinned at its bottom edge
     grows UPWARD as its content expands, which is what lets the expanded
     panel open over the sidebar instead of off the bottom of the screen. */
  const islandStyle = stageActive
    ? { position: 'fixed', top: 18, left: '50%', transform: 'translateX(-50%)',
        zIndex: 9000, '--island-bar-scale': islandBarScale }
    : { position: 'fixed', bottom: 12, left: 10, top: 'auto', transform: 'none',
        zIndex: 9000, '--island-bar-scale': islandBarScale };
  function normalizeBrainId(value) {
    const v = String(value || '').toLowerCase();
    if (v === 'nvidia_nim' || v === 'nvidia nim' || v === 'nim' || v === 'nvidia') return 'nvidia_nim';
    if (v === 'groq') return 'groq';
    if (v.includes('gemini')) return 'gemini';
    if (v === 'ollama') return 'ollama';
    if (v === 'openrouter') return 'openrouter';
    if (v === 'auto' || v === 'none') return 'auto';
    return '';
  }
  const activeBrainId = normalizeBrainId(conn.brain)
    || normalizeBrainId(brainStatus && (brainStatus.active_provider || brainStatus.manual_provider))
    || 'auto';
  const visibleActiveBrainId = activeBrainId === 'gemini' ? 'auto' : activeBrainId;
  const providerMap = (brainStatus && brainStatus.providers && typeof brainStatus.providers === 'object') ? brainStatus.providers : {};
  const brainChoices = [
    ['groq', 'Groq'],
    ['ollama', 'Ollama'],
    ['openrouter', 'OpenRouter'],
    ['nvidia_nim', 'NIM'],
  ];
  const exactRuntimeBrainLabel = conn.brain && !normalizeBrainId(conn.brain) ? String(conn.brain) : '';
  const currentBrainLabel = exactRuntimeBrainLabel || (visibleActiveBrainId === 'auto'
    ? 'Auto'
    : ((brainChoices.find(([bid]) => bid === visibleActiveBrainId) || [visibleActiveBrainId, visibleActiveBrainId])[1]));
  // ── Pin vs Runtime split ─────────────────────────────────────────────
  // brainManual is the user's PINNED brain (what they clicked in the panel).
  // visibleActiveBrainId is the RUNTIME brain (what the router actually used
  // on the last turn). When the router falls back — e.g. user pinned Groq but
  // Groq returned 429 so Ollama ran — these diverge and the legacy display
  // (which only showed runtime) looked like a snap-back. Now we surface the
  // pin in the headline and the runtime as a secondary "via X" hint.
  const _normalizedBrainManualId = normalizeBrainId(brainManual);
  const _pinnedBrainId = brainManual && brainManual !== 'auto'
    ? String(brainManual)
    : '';
  const _pinnedFallbackLabel = (rawId) => {
    const tail = String(rawId || '').split('/').pop();
    return tail.replace(/[-_]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  };
  const pinnedBrainLabel = _pinnedBrainId
    ? ((brainList || []).find(b => b && (String(b.id || '') === _pinnedBrainId || String(b.brain_id || '') === _pinnedBrainId || String(b.model || '') === _pinnedBrainId))?.displayName
      || (brainChoices.find(([bid]) => bid === _pinnedBrainId) || [_pinnedBrainId, _pinnedFallbackLabel(_pinnedBrainId)])[1])
    : 'Auto';
  const pinAndRuntimeDiffer = !!_pinnedBrainId && _pinnedBrainId !== visibleActiveBrainId && pinnedBrainLabel !== currentBrainLabel;
  // Clean mode (2026-07-10): the "· via <runtime provider>" suffix is routing
  // diagnostics — surface it only when the dev trace is enabled.
  const displayedBrainLabel = pinAndRuntimeDiffer && !(window.SyrixCleanMode && window.SyrixCleanMode())
    ? `${pinnedBrainLabel} · via ${currentBrainLabel}`
    : pinnedBrainLabel;
  function pickProviderStatus(id) {
    const p = providerMap[id] || {};
    const known = Object.prototype.hasOwnProperty.call(providerMap, id);
    const rawReason = known ? String(p.reason || (p.available ? 'ok' : 'unavailable')) : 'status_unknown';
    return {
      available: known ? !!p.available : false,
      reason: formatSyrixStatusLabel(rawReason, { available: known ? !!p.available : false }),
      rawReason,
      locked: visibleActiveBrainId === id || visibleActiveBrainId.indexOf(id) >= 0,
    };
  }
  function selectBrain(id) {
    const B = window.SyrixBridge;
    if (!B || !B.setBrain) return;
    B.setBrain(id);
    const label = id === 'auto' ? 'Auto routing' : (brainChoices.find(([k]) => k === id) || [id, id])[1];
    setIslandNotice({ tone: 'ok', text: id === 'auto' ? 'AUTO ROUTING' : `LOCKING · ${label}`, detail: '' });
    if (islandNoticeTimerRef.current) clearTimeout(islandNoticeTimerRef.current);
    islandNoticeTimerRef.current = setTimeout(() => setIslandNotice(null), 2400);
  }

  function hudAppHost(app) {
    const raw = String((app && (app.url || app.domain || app.title)) || '').trim();
    if (!raw) return '';
    try {
      const parsed = new URL(raw);
      return parsed.host.replace(/^www\./i, '');
    } catch (_) {}
    return raw.replace(/^https?:\/\//i, '').replace(/^www\./i, '').split(/[/?#]/)[0];
  }

  function hudAppLabel(app) {
    const raw = String((app && (app.title || app.domain || app.url)) || 'HUD Tab').trim();
    if (!raw) return 'HUD Tab';
    const label = raw.replace(/^https?:\/\//i, '').replace(/^www\./i, '').split(/[?#]/)[0];
    return label.length > 30 ? label.slice(0, 29) + '…' : label;
  }

  function runHudAppControl(action, id) {
    const B = window.SyrixBridge;
    if (!B) return;
    const targetId = id || activeHudAppId || (hudApps[0] && hudApps[0].id) || '';
    const targetApp = hudApps.find((app) => app && app.id === targetId) || null;
    try {
      if (action === 'focus' && targetId) {
        if (B.focusHudApp) B.focusHudApp(targetId);
        if (targetApp && targetApp.mode === 'minimized' && B.restoreHudApp) return B.restoreHudApp(targetId);
        return;
      }
      if (action === 'restore' && targetId && B.restoreHudApp) return B.restoreHudApp(targetId);
      // Close must always be able to close something. A page opened by
      // clicking a link in an answer does not always land in the hud-app
      // registry, so `targetId` comes back empty, the close silently does
      // nothing, and the browser fills the window with no way out —
      // reported as "I can't close that browser". Falling through to
      // close-all means the escape always works, and closing the last view
      // is what drops stage mode.
      if (action === 'close') {
        if (targetId && B.closeHudApp) return B.closeHudApp(targetId);
        if (B.closeAllHudApps) return B.closeAllHudApps();
        return;
      }
      if (action === 'close_all' && B.closeAllHudApps) return B.closeAllHudApps();
      if (action === 'refresh' && targetId && B.refreshHudApp) return B.refreshHudApp(targetId);
      if (action === 'back' && targetId && B.hudAppBack) return B.hudAppBack(targetId);
      if (action === 'forward' && targetId && B.hudAppForward) return B.hudAppForward(targetId);
      if (action === 'minimize' && targetId && B.minimizeHudApp) return B.minimizeHudApp(targetId);
      if (action === 'fullscreen' && targetId) {
        if (targetApp && targetApp.mode === 'fullscreen' && B.restoreHudApp) return B.restoreHudApp(targetId);
        if (B.fullscreenHudApp) return B.fullscreenHudApp(targetId);
      }
    } catch (_) {}
  }

  // Stage nav bar omnibox — navigate the active tab (bridge resolves bare
  // keywords to a Google search, same as the windowed URL bar).
  function stageNavigate(raw) {
    const text = String(raw || '').trim();
    if (!text) return;
    const B = window.SyrixBridge;
    const targetId = activeHudAppId || (hudApps[0] && hudApps[0].id) || '';
    if (B && B.navigateHudApp && targetId) B.navigateHudApp(targetId, text);
    else if (B && B.openAppInHud) B.openAppInHud(text, { source: 'user_click' });
  }

  const currentHudTheme = normalizeHudTheme(hudTheme);
  const nimPanelAurora = currentHudTheme === HUD_THEME_AURORA_GLASS;
  const nimPanelLiquid = isGlassHudTheme(currentHudTheme);
  // Anchor the brain dropdown to the pill (top-bar). If the first measurement
  // has not arrived yet, tuck it near the top-right brain-pill area.
  const _brainAnchorBase = brainPillRect && brainPillRect.width
    ? {
        position: 'fixed',
        top: Math.round(brainPillRect.bottom + 8),
        left: Math.max(12, Math.round(brainPillRect.left - 40)),
        width: 420,
        maxHeight: '72vh', overflowY: 'auto',
        transformOrigin: 'top left',
        animation: 'syrixBrainDropIn 180ms cubic-bezier(0.18, 0.9, 0.32, 1.16)',
      }
    : {
        position: 'fixed', top: 64, right: 18,
        width: 420, maxHeight: '72vh', overflowY: 'auto',
        transformOrigin: 'top right',
        animation: 'syrixBrainDropIn 180ms cubic-bezier(0.18, 0.9, 0.32, 1.16)',
      };
  const nimPanelStyle = nimPanelAurora ? {
    /* Aurora v3 — warm obsidian glass with a champagne rim. */
    ..._brainAnchorBase,
    background: 'linear-gradient(165deg, rgba(26,20,12,0.90), rgba(10,8,5,0.94))',
    backdropFilter: 'blur(30px) saturate(1.55)',
    WebkitBackdropFilter: 'blur(30px) saturate(1.55)',
    border: '1px solid rgba(255,245,230,0.17)', borderRadius: 16,
    padding: '14px 16px', zIndex: 1500,
    boxShadow: '0 30px 80px rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,250,240,0.17), 0 0 32px -10px rgba(233,201,140,0.3)'
  } : nimPanelLiquid ? {
    ..._brainAnchorBase,
    background: 'linear-gradient(180deg, rgba(248,251,255,0.22), rgba(216,226,236,0.12))',
    backdropFilter: 'blur(32px) saturate(1.5)',
    WebkitBackdropFilter: 'blur(32px) saturate(1.5)',
    border: '1px solid rgba(255,255,255,0.32)', borderRadius: 14,
    padding: '14px 16px', zIndex: 1500,
    boxShadow: '0 28px 70px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.24)'
  } : {
    ..._brainAnchorBase,
    background: 'linear-gradient(180deg, rgba(5,11,20,0.96), rgba(3,8,16,0.94))',
    backdropFilter: 'blur(26px)',
    border: '1px solid rgba(34,211,238,0.28)', borderRadius: 14,
    padding: '14px 16px', zIndex: 1500,
    boxShadow: '0 28px 70px rgba(0,0,0,0.7), 0 0 70px -20px rgba(34,211,238,0.45)'
  };
  // Friendly pill label: prefer active brain's display name from brainList,
  // never expose raw model IDs like "nvidia/llama-3.1-nemotron-70b" on the pill.
  function _friendlyBrainLabel() {
    try {
      if (brainPending) {
        const hit = (brainList || []).find(b => b && (b.id === brainPending || b.model === brainPending));
        const label = hit && hit.display
          ? String(hit.display)
          : String(brainPending).split('/').pop().replace(/[-_]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        return 'LOCKING · ' + label.slice(0, 12);
      }
      if (brainManual && brainManual !== 'auto') {
        const hit = (brainList || []).find(b => b && (b.id === brainManual || b.model === brainManual));
        if (hit && hit.display) return String(hit.display);
        // Fallback: tidy the id
        const tail = String(brainManual).split('/').pop();
        return tail.replace(/[-_]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase()).slice(0, 18);
      }
      // Auto: show the actually-active provider when known
      const cur = (conn && conn.brain) ? String(conn.brain) : '';
      if (cur) {
        const tidy = cur.replace(/^nvidia[_-]?/i, '').replace(/[-_]+/g, ' ').trim();
        return tidy.length > 0
          ? 'AUTO · ' + tidy.replace(/\b\w/g, c => c.toUpperCase()).slice(0, 14)
          : 'AUTO';
      }
      return 'AUTO';
    } catch (_) {
      return 'BRAIN';
    }
  }
  const friendlyPillLabel = _friendlyBrainLabel();
  // Keep brainPillRect in sync when the pill is rendered / window resized.
  useEffect(() => {
    function measure() {
      const el = brainPillRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setBrainPillRect({
        bottom: r.bottom, left: r.left, width: r.width, top: r.top, right: r.right,
      });
    }
    if (nimOpen) measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [nimOpen, friendlyPillLabel]);
  const nimAccent = nimPanelAurora ? '#f6e2b3' : (nimPanelLiquid ? 'rgba(248,251,255,0.92)' : '#d4aa5a');
  const nimMuted = nimPanelAurora ? 'rgba(244,237,226,0.68)' : (nimPanelLiquid ? 'rgba(245,248,252,0.62)' : 'rgba(180,210,255,0.62)');

  return (
    <div id="syrix-primary" className={rootClass}>
      {/* Long-form report overlay (2026-05-27): appears when backend emits a
          long_report payload. Top-level so it overlays every page/widget. */}
      <SyrixLongReportOverlay />
      {/* CAP-17 (2026-06-04): Morning brief visual card — appears when the
          backend pushes a `morning_brief_card` ws payload (~09:00 ET). */}
      <SyrixMorningBriefCard />
      {/* A3: Reading panel — auto-opens for answers > 500 chars */}
      <ReadingPanel />
      {/* Aurora Glass shell — always rendered. aurora-glass CSS hides legacy chrome. */}
      {window.SyrixAuroraShell
        ? React.createElement(window.SyrixAuroraShell, {
            listening:       mode === 'listening',
            micMuted:        micMuted,
            ttsMuted:        ttsMuted,
            onTtsMuteToggle: toggleTtsMute,
            brainLabel:      displayedBrainLabel || 'AUTO',
            // 2026-07-02 — honest core chip: bound to the real connection
            // state instead of a hardcoded "LIVE".
            coreLabel:       isOffline ? 'CORE V2:OFFLINE'
                             : (isConnecting || isReconnecting) ? 'CORE V2:CONNECTING'
                             : 'CORE V2:LIVE',
            versionLabel:    'SYRIX V4.2',
            chromeMuted: !!(
              widgetMenu || settingsOpen ||
              focusedWidget ||
              (Array.isArray(hudApps) && hudApps.length > 0)
            ),
            brains: [
              { id: 'auto', label: 'Auto', available: true, locked: !visibleActiveBrainId || visibleActiveBrainId === 'auto' },
              ...brainChoices.map(([bid, label]) => {
                const st = pickProviderStatus(bid);
                return { id: bid, label, available: !!st.available, locked: !!st.locked, reason: st.reason };
              }),
            ],
            activeBrainId:  visibleActiveBrainId || 'auto',
            onSelectBrain:  (id) => selectBrain(id),
            onOpenSettings: () => setSettingsOpen(true),
            onOpenWidgets:  () => setWidgetMenu((v) => !v),
            onOpenWidget:   addWidget,
            onMicToggle:    () => applyMute(!micMutedRef.current),
            // VOICE MODE (2026-07-08): the aurora shell owns the voice overlay
            // + the voice_mode_on/off wire commands; this callback keeps the
            // primary shell's mic/TTS mute visuals (orb dock, island notice)
            // in lockstep with the mode change.
            //
            // 2026-07-11 root-cause fix — "faint white glow on the HUD frame
            // right after leaving Voice Mode": the Voice Mode overlay sits on
            // top of (not instead of) the home screen, so while a session is
            // live the home screen's OWN `mode` state keeps tracking the same
            // backend voice_state underneath it (listening/processing/
            // speaking), silently painting #syrix-primary's frame classes
            // (is-speaking = cyan glow) the whole time it's hidden behind the
            // overlay. The overlay closes with an instant display:none (no
            // fade), so whatever frame glow was live at that exact moment
            // (almost always is-speaking, since you just finished talking)
            // is revealed immediately — previously only `applyMute(true)`
            // ran here, and the effect that clears mode on mute
            // (`if (micMuted) setMode(...)`) only fires a render cycle later
            // once that state actually commits, leaving a one-frame gap where
            // the stale glow is visible. Clearing mode in the SAME callback,
            // synchronously with the exit, closes that gap.
            onVoiceModeChange: (active) => {
              if (!active) {
                // Exiting Voice Mode intentionally parks voice I/O, but this
                // is not a user mute action. Skip the decorative dock phase
                // so the island returns directly to its normal idle pill.
                skipVoiceOffDockRef.current = true;
                applyMute(true);
                setMode(m => (m === 'listening' || m === 'speaking' || m === 'processing' || m === 'interrupted') ? 'idle' : m);
              } else {
                applyMute(false);
              }
            },
            sleepMode:      mode === 'sleep',
            onToggleSleep:  () => {
              if (mode === 'sleep') {
                setMode('idle');
                try { window.SyrixBridge && window.SyrixBridge.send && window.SyrixBridge.send('wake'); } catch (_) {}
              } else {
                goSleep();
                try { window.SyrixBridge && window.SyrixBridge.send && window.SyrixBridge.send('sleep'); } catch (_) {}
              }
            },
          })
        : null}
      {/* BG layers */}
      <div className="p-grid"></div>
      <div className="p-border"></div>
      <div className="p-corner p-tl"></div>
      <div className="p-corner p-tr"></div>
      <div className="p-corner p-bl"></div>
      <div className="p-corner p-br"></div>

      {/* The browser's navigation bar — back, forward, reload, omnibox, tabs
          and close — rendered only while a page fills the window.

          This element used to be present at all times as the Dynamic
          Island: a floating pill carrying a clock, a connection tone, cards
          and menus. Removed on request. The shell reports all of that in
          its own surfaces, and two components describing the same state is
          how an old clock ended up sitting over a new one. The stage nav
          stays, because a browser with no toolbar has no way out of
          itself. */}
      {stageActive && (
      <div
        className={"p-conn-badge p-island p-island-" + islandTone + islandModeClass
          + (islandExpanded ? ' open' : '')
          + (islandCardShowing && !islandOpen ? ' card-only' : '')
          + (islandCardShowing ? ' has-card card-kind-' + String(islandCard.kind || '') : '')
          + (islandTabMode ? ' tabs' : '')
          + (stageActive ? ' stage-nav' : '')
          + (isReconnecting ? ' reconnecting' : '')}
        style={islandStyle}
        role={stageActive ? 'toolbar' : 'button'}
        tabIndex={stageActive ? undefined : '0'}
        aria-expanded={stageActive ? undefined : islandExpanded}
        onClick={stageActive ? undefined : () => { setIslandNotice(null); setIslandMenuOpen(false); setIslandOpen(v => !v); }}
        onContextMenu={stageActive ? undefined : (e) => {
          e.preventDefault(); e.stopPropagation();
          setIslandNotice(null);
          setIslandMenuOpen(v => !v);
        }}
        onKeyDown={stageActive ? undefined : (e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setIslandNotice(null); setIslandOpen(v => !v); }
          if (e.key === 'Escape') { setIslandOpen(false); setIslandMenuOpen(false); dismissIslandCard(); }
        }}>
        {/* Stage nav row — Google-style toolbar replacing the pill while a
            browser fills the background. */}
        {stageActive && (
          <div className="p-stage-nav-row" onClick={(e) => e.stopPropagation()}>
            <div className="p-stage-nav-state" title={'SYRIX · ' + mode.toUpperCase()}>
              <IslandStateIcon
                key={'stage-' + mode}
                mode={mode}
                isOffline={isOffline}
                isConnecting={isConnecting}
                isReconnecting={isReconnecting}
                tone={islandTone}
                voiceOffPhase={voiceOffPhase}
              />
            </div>
            {/* Live activities — timer / stopwatch / music stay visible while
                the browser fills the screen (2026-07-04). Music pill is a
                real play/pause control; timer + stopwatch tick live. */}
            {(islandLiveTimer || stopwatchLive || (mediaSession && mediaSession.active && !mediaDismissed)) && (
              <div className="p-stage-nav-live">
                {islandLiveTimer && (
                  <span className="p-stage-live-pill is-timer" title="Timer running">
                    <i className="p-stage-live-dot" />{timerDisplay}
                  </span>
                )}
                {stopwatchLive && (
                  <button className="p-stage-live-pill is-sw"
                    title={stopwatchSnap.running ? 'Stopwatch — click to pause' : 'Stopwatch paused — click to resume'}
                    onClick={() => { try { window.SyrixStopwatch && window.SyrixStopwatch.toggle(); } catch (_) {} }}>
                    ⏱ {window.SyrixStopwatch && window.SyrixStopwatch.format
                        ? window.SyrixStopwatch.format(stopwatchSnap.elapsedMs)
                        : Math.floor(stopwatchSnap.elapsedMs / 1000) + 's'}
                  </button>
                )}
                {mediaSession && mediaSession.active && !mediaDismissed && (
                  <button className={"p-stage-live-pill is-media" + (mediaSession.playing ? ' on' : '')}
                    title={(mediaSession.track || 'Music') + (mediaSession.playing ? ' — click to pause' : ' — click to play')}
                    onClick={() => { try { mediaSession.playing ? mediaSession.pause() : mediaSession.play(); } catch (_) {} }}>
                    ♪ {(String(mediaSession.track || 'PLAYING')).slice(0, 16)} {mediaSession.playing ? '⏸' : '▶'}
                  </button>
                )}
              </div>
            )}
            <button className="p-stage-nav-btn" title="Back"
              disabled={!activeHudApp || !activeHudApp.canGoBack}
              onClick={() => runHudAppControl('back')}>‹</button>
            <button className="p-stage-nav-btn" title="Forward"
              disabled={!activeHudApp || !activeHudApp.canGoForward}
              onClick={() => runHudAppControl('forward')}>›</button>
            <button className="p-stage-nav-btn" title="Reload"
              disabled={!activeHudApp}
              onClick={() => runHudAppControl('refresh')}>↻</button>
            <div className="p-stage-nav-urlbar">
              <span className="p-stage-nav-proto">
                {/^https:\/\//i.test(stageUrl) ? '🔒' : /^http:\/\//i.test(stageUrl) ? '🌐' : '🔍'}
              </span>
              <input
                type="text"
                className="p-stage-nav-input"
                value={stageUrl}
                placeholder="Search or enter URL…"
                onChange={(e) => setStageUrl(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') { e.preventDefault(); stageNavigate(stageUrl); e.target.blur(); }
                  if (e.key === 'Escape') { e.target.blur(); setStageUrl(_stageAppUrl); }
                }}
                onFocus={(e) => e.target.select()}
                spellCheck={false}
                autoComplete="off"
              />
            </div>
            {/* Inline tab chips — one-click switching between open tabs
                without the dropdown (favicons, active ring). Shown when 2+
                tabs are open; the ⧉ dropdown stays for the full list. */}
            {hudAppCount > 1 && (
              <div className="p-stage-tab-chips">
                {hudApps.slice(0, 5).map((app) => {
                  const dom = hudAppHost(app);
                  return (
                    <button key={app.id}
                      className={"p-stage-tab-chip" + (app.id === activeHudAppId ? ' active' : '')}
                      title={hudAppLabel(app)}
                      onClick={() => runHudAppControl('focus', app.id)}>
                      {dom
                        ? <img src={'https://icons.duckduckgo.com/ip3/' + dom + '.ico'} alt=""
                            onError={(e) => { try { e.target.style.display = 'none'; e.target.parentNode.textContent = (hudAppLabel(app)[0] || '•').toUpperCase(); } catch (_) {} }} />
                        : <span>{(hudAppLabel(app)[0] || '•').toUpperCase()}</span>}
                    </button>
                  );
                })}
              </div>
            )}
            <button className="p-stage-nav-btn" title="New tab"
              onClick={() => {
                try {
                  const B = window.SyrixBridge;
                  if (B && B.openAppInHud) B.openAppInHud('https://www.google.com', { title: 'Browser', new_tab: true });
                } catch (_) {}
              }}>+</button>
            <button className={"p-stage-nav-btn wide" + (stageTabsOpen ? ' active' : '')}
              title="Tabs"
              onClick={() => { setStageTabsOpen(v => !v); setStageFolderOpen(false); }}>
              ⧉ {hudAppCount}
            </button>
            <button className="p-stage-nav-btn" title="Chat panel"
              onClick={() => { try { window.dispatchEvent(new CustomEvent('syrix:stage-chat-toggle')); } catch (_) {} }}>💬</button>
            <button className="p-stage-nav-btn danger" title="Close tab"
              disabled={!activeHudApp}
              onClick={() => runHudAppControl('close')}>✕</button>
          </div>
        )}

        {/* Pill row — 3-zone: icon | text | activity */}
        {!stageActive && (
        <div className="p-island-pill-row">
          <div className={"p-island-body" + (islandText === islandQuietText ? ' is-idle-wordmark' : '')}>
            <div className="p-island-icon-zone">
              <IslandStateIcon
                key={mode}
                mode={mode}
                isOffline={isOffline}
                isConnecting={isConnecting}
                isReconnecting={isReconnecting}
                tone={islandTone}
                voiceOffPhase={voiceOffPhase}
              />
            </div>
            <div className="p-island-text-zone">
              <span key={islandTextKey} className="p-island-text">
                {islandText}
              </span>
            </div>
            <div className="p-island-activity-zone">
              {(voiceOffPhase === 'dock' || voiceOffPhase === 'text' || voiceOffPhase === 'restore-fade' || voiceOffPhase === 'restore-move') ? null : brainError ? (
                <span className="p-island-brain-err-dot" title="Brain offline"/>
              ) : (mode === 'listening' || mode === 'speaking') ? (
                <IslandWaveformBars/>
              ) : mode === 'processing' ? (
                <div className="p-island-think-dots"><span/><span/><span/></div>
              ) : islandCardShowing && islandCard.kind === 'sports' && islandCard.live ? (
                <span className="p-island-card-live-dot" title="Live match"/>
              ) : islandActivityCount > 1 ? (
                /* More than one live activity — show each task as its own
                   readable chip (icon + real value), side by side, instead of
                   hiding all but one behind "+N" or a bare unlabeled dot. The
                   pill's own width/min-width transition (see .p-island) makes
                   this grow sideways smoothly, exactly as much as it needs,
                   as tasks are added or removed. */
                <div className="p-island-multi-row">
                  {islandLiveTimer && (
                    <span className="p-island-chip p-island-chip-timer" key="t" title="Timer running">
                      <span className="p-island-timer-live-dot"/>
                      {timerDisplay}
                    </span>
                  )}
                  {mediaLive && (
                    <span className="p-island-chip p-island-chip-media" key="m" title={mediaSession.track || 'Media'}>
                      {mediaSession.playing && window.SyrixIslandEq
                        ? <window.SyrixIslandEq playing={true}/>
                        : <span className="p-island-media-dot"/>}
                      {(String(mediaSession.track || 'PLAYING')).slice(0, 14)}
                    </span>
                  )}
                  {stopwatchLive && (
                    <span className="p-island-chip p-island-chip-sw" key="s" title={stopwatchSnap.running ? 'Stopwatch running' : 'Stopwatch paused'}>
                      <span className="p-island-sw-live-dot"/>
                      {window.SyrixStopwatch && window.SyrixStopwatch.format ? window.SyrixStopwatch.format(stopwatchSnap.elapsedMs) : ''}
                    </span>
                  )}
                </div>
              ) : islandLiveTimer ? (
                <span className="p-island-timer-live-dot"/>
              ) : mediaLive && mediaSession.playing && window.SyrixIslandEq ? (
                <window.SyrixIslandEq playing={true}/>
              ) : stopwatchLive && stopwatchSnap.running ? (
                <span className="p-island-sw-live-dot" title="Stopwatch running"/>
              ) : null}
            </div>
          </div>
        </div>
        )}

        {/* Expandable content — always in DOM, grid 0fr→1fr controls height.
            Unmounted in stage mode (the nav bar replaces it). */}
        {!stageActive && (
        <div className="p-island-expand-wrap">
          <div className="p-island-expand-inner">
            <div className="p-island-content" onClick={(e) => e.stopPropagation()}>
              {/* Rich card — scoreboard / wiki summary pushed by the backend.
                  In card-only mode this is ALL the island shows (iOS-style
                  auto-expand, sized to the card); with the panel open it
                  tops the stack. */}
              {islandCardShowing && window.SyrixIslandCard && (
                <window.SyrixIslandCard card={islandCard} onDismiss={dismissIslandCard}/>
              )}
              {islandOpen && islandCardShowing && <div className="p-island-divider"/>}
              {/* Live activity sessions — music / timers / stopwatch whose
                  widgets are closed, each with real inline controls. */}
              {islandOpen && classicActivityCount > 0 && window.SyrixIslandActivities && (
                <>
                  <window.SyrixIslandActivities
                    media={mediaSession}
                    showMedia={mediaLive}
                    onEndMedia={() => {
                      try { mediaSession && mediaSession.pause && mediaSession.pause(); } catch (_) {}
                      setMediaDismissed(true);
                    }}
                    stopwatch={stopwatchSnap}
                    showStopwatch={stopwatchLive}
                    timers={activeTimers}
                    timerDisplay={timerDisplay}
                    showTimers={activeTimers.length > 0 && !timerWidgetOpen}
                    onStopTimers={() => {
                      try {
                        const B = window.SyrixBridge;
                        if (B && B.cancelTimers) B.cancelTimers();
                      } catch (_) {}
                    }}
                  />
                  <div className="p-island-divider"/>
                </>
              )}
              {islandOpen && window.SyrixIslandSupercomputer && (
                <>
                  <window.SyrixIslandSupercomputer
                    state={scState}
                    onOpen={() => {
                      try {
                        window.dispatchEvent(new CustomEvent('syrix-open-supercomputer'));
                      } catch (_) {}
                    }}
                    onStop={() => {
                      try {
                        const B = window.SyrixBridge;
                        if (B && B.send) B.send('supercomputer_stop', { data: { reason: 'island_stop' } });
                      } catch (_) {}
                    }}
                  />
                </>
              )}
              {islandOpen && agentTasks.length > 0 && window.SyrixIslandWatches && (
                <>
                  <window.SyrixIslandWatches
                    tasks={agentTasks}
                    onCancel={(taskId) => {
                      try {
                        const B = window.SyrixBridge;
                        if (B && B.send) B.send('cancel_agent_task', { data: { task_id: taskId } });
                      } catch (_) {}
                    }}
                  />
                  <div className="p-island-divider"/>
                </>
              )}
              {!islandOpen ? null : islandTabMode ? (
                <div className="p-island-tabs">
                  <div className="p-island-current">
                    <span>TAB CONTROLLER</span>
                    <b>{hudAppCount} OPEN</b>
                  </div>
                  {activeHudApp && (
                    <div className="p-island-tab-active">
                      <span>{hudAppLabel(activeHudApp)}</span>
                      <em>{hudAppHost(activeHudApp) || activeHudApp.mode || 'HUD tab'}</em>
                    </div>
                  )}
                  <div className="p-island-tab-controls">
                    <button className="p-island-tab-btn" title="Back" disabled={!activeHudApp || !activeHudApp.canGoBack} onClick={() => runHudAppControl('back')}>‹</button>
                    <button className="p-island-tab-btn" title="Forward" disabled={!activeHudApp || !activeHudApp.canGoForward} onClick={() => runHudAppControl('forward')}>›</button>
                    <button className="p-island-tab-btn" title="Reload" disabled={!activeHudApp} onClick={() => runHudAppControl('refresh')}>↻</button>
                    <button className="p-island-tab-btn" title={activeHudApp && activeHudApp.mode === 'fullscreen' ? 'Restore' : 'Fullscreen'} disabled={!activeHudApp} onClick={() => runHudAppControl('fullscreen')}>⛶</button>
                    <button className="p-island-tab-btn" title={activeHudApp && activeHudApp.mode === 'minimized' ? 'Restore' : 'Minimize'} disabled={!activeHudApp} onClick={() => runHudAppControl(activeHudApp && activeHudApp.mode === 'minimized' ? 'restore' : 'minimize')}>−</button>
                    <button className="p-island-tab-btn danger" title="Close tab" disabled={!activeHudApp} onClick={() => runHudAppControl('close')}>×</button>
                  </div>
                  <div className="p-island-tab-list">
                    {hudApps.map((app) => (
                      <div key={app.id} className={"p-island-tab-row" + (app.id === activeHudAppId ? ' active' : '')}>
                        <button className="p-island-tab-focus" title={hudAppHost(app) || app.url || app.title || 'HUD tab'} onClick={() => runHudAppControl('focus', app.id)}>
                          <span>{hudAppLabel(app)}</span>
                          <em>{app.mode === 'fullscreen' ? 'full' : app.mode === 'minimized' ? 'min' : app.loading ? 'loading' : 'open'}</em>
                        </button>
                        <button className="p-island-tab-close" title="Close tab" onClick={() => runHudAppControl('close', app.id)}>×</button>
                      </div>
                    ))}
                  </div>
                  {hudAppCount > 1 && (
                    <button className="p-island-tab-all" onClick={() => runHudAppControl('close_all')}>Close all</button>
                  )}
                </div>
              ) : (
                <>
                  {/* Card history — past sports/wiki cards, reopenable ONLY
                      here (the island's own expanded panel). Excludes the
                      card currently showing live so it never lists itself. */}
                  {window.SyrixIslandCardHistory && islandCardHistoryList.length > 0 && (
                    <>
                      <div className="p-island-divider"/>
                      <window.SyrixIslandCardHistory
                        history={islandCardHistoryList}
                        onReopen={reopenIslandCard}
                      />
                    </>
                  )}

                  {/* Activity feed */}
                  {window.SyrixActivityFeedPanel && activityFeedEvents && activityFeedEvents.length > 0 && (
                    <div className="p-island-activity">
                      <div className="p-island-current p-island-activity-head">
                        <span>ACTIVITY</span>
                        <b>{activityFeedEvents.length} EVENTS</b>
                      </div>
                      {React.createElement(window.SyrixActivityFeedPanel, {
                        events: activityFeedEvents,
                        max: 30,
                        onOpenSource: (url, title) => {
                          try {
                            const B = window.SyrixBridge;
                            if (B && B.openHudApp) {
                              B.openHudApp({ url, title: title || url });
                            } else {
                              window.open(url, '_blank', 'noopener');
                            }
                          } catch (_) { window.open(url, '_blank', 'noopener'); }
                        },
                      })}
                    </div>
                  )}

                  {/* Nothing live to report — say so, rather than opening into
                      an empty box. */}
                  {!islandHasLiveDetail && (
                    <div className="p-island-quiet">
                      <div className="p-island-quiet-row">
                        <span>Status</span><b>{islandStateWords}</b>
                      </div>
                      <div className="p-island-quiet-row">
                        <span>Engine</span><b>{islandEngine}</b>
                      </div>
                      <div className="p-island-quiet-row">
                        <span>Showing</span>
                        <b>{islandMode === 'name' ? 'Your name'
                          : islandMode === 'widgets' ? 'Answer cards' : 'Status'}</b>
                      </div>
                      <div className="p-island-quiet-hint">Right-click to change what this shows.</div>
                    </div>
                  )}

                </>
              )}
            </div>
          </div>
        </div>
        )}
      </div>
      )}

      {/* Island mode menu — right-click the dock. Anchored to the dock rather
          than to the cursor so it can never open off the bottom of the screen
          or under the composer. */}
      {islandMenuOpen && !stageActive && (
        <>
          <div className="p-island-menu-veil" onClick={() => setIslandMenuOpen(false)}
               onContextMenu={(e) => { e.preventDefault(); setIslandMenuOpen(false); }} />
          <div className="p-island-menu" role="menu" onClick={(e) => e.stopPropagation()}>
            <div className="p-island-menu-title">DYNAMIC ISLAND</div>
            {[
              { id: 'status',  label: 'Status',  sub: 'SYRIX · listening · thinking' },
              { id: 'name',    label: 'Name',    sub: ownerName },
              { id: 'widgets', label: 'Widgets', sub: 'Answers open here as live cards' },
            ].map((o) => (
              <button key={o.id} type="button" role="menuitemradio"
                aria-checked={islandMode === o.id}
                className={"p-island-menu-item" + (islandMode === o.id ? ' is-active' : '')}
                onClick={() => chooseIslandMode(o.id)}>
                <span className="p-island-menu-copy">
                  <span className="p-island-menu-lbl">{o.label}</span>
                  <span className="p-island-menu-sub">{o.sub}</span>
                </span>
                <span className="p-island-menu-tick" aria-hidden="true">
                  {islandMode === o.id ? '✓' : ''}
                </span>
              </button>
            ))}
          </div>
        </>
      )}

      {/* Stage folder panel — the left rail collapsed into one iPhone-folder
          grid, dropping from the nav bar over the (frozen) browser. */}
      {stageActive && stageFolderOpen && (
        <div className="p-stage-folder-panel" onClick={(e) => e.stopPropagation()}>
          <div className="p-stage-folder-title">SYRIX CONTROLS</div>
          <div className="p-stage-folder-grid">
            {(window.SyrixStage && window.SyrixStage.registry ? window.SyrixStage.registry.list() : []).map((a) => (
              <button
                key={a.name}
                className="p-stage-folder-btn"
                title={a.label || a.name}
                onClick={() => {
                  setStageFolderOpen(false);
                  try { window.SyrixStage.registry.invoke(a.name); } catch (_) {}
                }}>
                <span className="p-stage-folder-ico">{a.icon || '•'}</span>
                <span className="p-stage-folder-lbl">{a.label || a.name}</span>
              </button>
            ))}
            {(!window.SyrixStage || !window.SyrixStage.registry || window.SyrixStage.registry.list().length === 0) && (
              <div className="p-stage-folder-empty">Controls loading…</div>
            )}
          </div>
        </div>
      )}

      {/* Stage tabs dropdown — switch / close open tabs, open a new one. */}
      {stageActive && stageTabsOpen && (
        <div className="p-stage-drop" onClick={(e) => e.stopPropagation()}>
          {hudApps.map((app) => (
            <div key={app.id} className={"p-island-tab-row" + (app.id === activeHudAppId ? ' active' : '')}>
              <button className="p-island-tab-focus"
                title={hudAppHost(app) || app.url || app.title || 'HUD tab'}
                onClick={() => { setStageTabsOpen(false); runHudAppControl('focus', app.id); }}>
                <span>{hudAppLabel(app)}</span>
                <em>{app.mode === 'minimized' ? 'min' : app.loading ? 'loading' : 'open'}</em>
              </button>
              <button className="p-island-tab-close" title="Close tab"
                onClick={() => runHudAppControl('close', app.id)}>×</button>
            </div>
          ))}
          <button className="p-island-tab-all"
            onClick={() => {
              setStageTabsOpen(false);
              try { window.SyrixHudAppLauncher && window.SyrixHudAppLauncher(); } catch (_) {}
            }}>+ New tab</button>
          {hudAppCount > 1 && (
            <button className="p-island-tab-all" onClick={() => runHudAppControl('close_all')}>Close all</button>
          )}
        </div>
      )}

      {/* Top bar */}
      <div className="p-topbar">
        <div className="p-topbar-l">
          <div className={"p-pulse" + (mode === 'sleep' ? ' sleep' : '')}></div>
          <span className="p-version">SYRIX v4.1</span>
          {(() => {
            const isManual = brainManual && brainManual !== 'auto';
            const isSwitching = !!brainPending;
            const pillClass = 'p-nim-pill' + (isSwitching ? ' pending' : isManual ? ' manual' : nim.online > 0 ? ' online' : nim.total > 0 ? ' degraded' : '');
            const pillTitle = isManual
              ? `Brain locked: ${friendlyPillLabel} — click to switch or release to Auto`
              : nim.total > 0 ? `Brain Switcher · ${nim.online}/${nim.total} NIM online — click to switch` : 'Brain Switcher — click to switch';
            return (
              <button
                ref={brainPillRef}
                className={pillClass}
                title={pillTitle}
                aria-expanded={nimOpen}
                aria-haspopup="menu"
                onClick={() => {
                  const willOpen = !nimOpen;
                  setNimOpen(o => !o);
                  if (willOpen) {
                    window.SyrixBridge?.listBrains?.();
                    // Measure pill so the dropdown anchors directly under it.
                    try {
                      const el = brainPillRef.current;
                      if (el) {
                        const r = el.getBoundingClientRect();
                        setBrainPillRect({ bottom: r.bottom, left: r.left, width: r.width, top: r.top, right: r.right });
                      }
                    } catch (_) {}
                  }
                }}
                onKeyDown={(e) => { if (e.key === 'Escape') setNimOpen(false); }}
                style={{ marginLeft: 14 }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: isSwitching ? '#d4aa5a' : isManual ? '#f5c243' : (nim.online > 0 ? '#16d37a' : 'rgba(120,145,175,0.6)'),
                    boxShadow: isSwitching ? '0 0 5px #d4aa5a' : isManual ? '0 0 5px #f5c243' : (nim.online > 0 ? '0 0 5px #16d37a' : 'none'),
                  }}></span>
                  <span style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.18em', textTransform: 'uppercase' }}>
                    {friendlyPillLabel}
                  </span>
                </span>
              </button>
            );
          })()}
          {/* 2026-06-08 — Universal Brain Status pill (fallback / restored
              notice). Only visible when the brain is in fallback or just
              recovered, so it doesn't clutter the topbar during steady state. */}
          {window.SyrixBrainStatusPill && (
            (activityBrainState.fallbackTs && (Date.now() / 1000 - activityBrainState.fallbackTs < 12)) ||
            (activityBrainState.restoredTs && (Date.now() / 1000 - activityBrainState.restoredTs < 8))
          ) ? (
            <span style={{ marginLeft: 8 }}>
              {React.createElement(window.SyrixBrainStatusPill, {
                brainState: activityBrainState, compact: true,
              })}
            </span>
          ) : null}
        </div>
        <div className="p-topbar-r">
          {/* Battery indicator — shows when unplugged and under 30% */}
          {(() => {
            const batPct = battery.percent;
            const plugged = battery.plugged;
            if (typeof batPct === 'number' && batPct >= 0 && !plugged && batPct < 30) {
              const level = Math.max(0, Math.min(100, batPct));
              const isLow = level < 15;
              const color = isLow ? '#ff6b6b' : level < 20 ? '#ffa500' : '#f5c243';
              const shadowColor = isLow ? 'rgba(255,107,107,0.5)' : level < 20 ? 'rgba(255,165,0,0.5)' : 'rgba(245,194,67,0.5)';
              return (
                <div className="p-battery-icon" title={`Battery: ${level.toFixed(0)}% (unplugged)`}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                    padding: '2px 6px', borderRadius: 4,
                    background: 'rgba(0,0,0,0.3)', border: `1px solid ${color}40`,
                    marginRight: 8
                  }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M5 18H3a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h18a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/>
                    <rect x="5" y="8" width="14" height="8"/>
                    <rect x="21" y="9" width="2" height="6"/>
                  </svg>
                  <span style={{
                    fontSize: '7px', fontWeight: '600', color: color,
                    textShadow: `0 0 4px ${shadowColor}`,
                    letterSpacing: '0.05em'
                  }}>
                    {level.toFixed(0)}%
                  </span>
                </div>
              );
            }
            return null;
          })()}
          <button className="p-icon-btn" title="Settings" onClick={() => setSettingsOpen(true)}>
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
          </button>
          <button className={"p-icon-btn" + (chatOpen ? ' active' : '')} title="Chat" onClick={() => setChatOpen(!chatOpen)}>
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
            </svg>
          </button>
          <button className="p-icon-btn" title="Widgets" data-wm-btn="1" onClick={() => setWidgetMenu(!widgetMenu)}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>
              <rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>
            </svg>
          </button>
          <button className={"p-icon-btn" + (hudAppOpen ? ' active' : '')} title="Open app in HUD"
            onClick={() => { try { window.SyrixHudAppLauncher && window.SyrixHudAppLauncher(); } catch (_) {} }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="4" width="18" height="14" rx="2"/>
              <path d="M3 8h18"/>
              <circle cx="6.5" cy="6" r="0.7" fill="currentColor"/>
              <circle cx="9"   cy="6" r="0.7" fill="currentColor"/>
              <circle cx="11.5" cy="6" r="0.7" fill="currentColor"/>
            </svg>
          </button>
          {/* Window controls — restored. HUD is frameless so these are the
              only way to minimize / maximize / close from the UI. */}
          <span style={{ width: 1, height: 18, background: 'rgba(34,211,238,0.18)', margin: '0 4px' }}></span>
          <button className="p-icon-btn p-win-min" title="Minimize"
            onClick={() => { try { window.syrixHud && window.syrixHud.minimize && window.syrixHud.minimize(); } catch (_) {} }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M5 18h14"/>
            </svg>
          </button>
          <button className="p-icon-btn p-win-max" title="Maximize / restore"
            onClick={() => { try { window.syrixHud && window.syrixHud.toggleMaximize && window.syrixHud.toggleMaximize(); } catch (_) {} }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <rect x="4" y="4" width="16" height="16" rx="1.5"/>
            </svg>
          </button>
          <button className="p-icon-btn p-win-close" title="Close"
            style={{ color: '#ff7a90' }}
            onClick={() => { try { window.syrixHud && window.syrixHud.close && window.syrixHud.close(); } catch (_) {} }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M6 6l12 12M18 6L6 18"/>
            </svg>
          </button>
        </div>
      </div>

      {/* Widget menu */}
      {widgetMenu && (
        <div className="p-widget-menu">
          {[
            ['map', '🗺  Map'],
            ['music', '♫  Music'],
            ['weather', '⛅  Weather'],
            ['timer', '⏱  Timer'],
            ['notes', '📝  Notes'],
            ['drawing', '✎  Drawing'],
            ['memory', '🧠  Memory Web'],
            ['diagnostic', '⚙  Diagnostics'],
            ['image', '🎨  Image Gen'],
          ].map(([type, label]) => (
            <button key={type} className="p-mini-btn" style={{ textAlign: 'left', width: '100%' }}
              onClick={() => { addWidget(type); if (type === 'map') setCorner(true); }}>
              {label}
            </button>
          ))}
        </div>
      )}

      {/* Orb stats ring close veil */}
      {orbMenuOpen && (
        <div
          style={{ position: 'fixed', inset: 0, zIndex: 99, cursor: 'default' }}
          onClick={() => setOrbMenuOpen(false)}
          onContextMenu={e => { e.preventDefault(); setOrbMenuOpen(false); }}
        />
      )}

      {/* 3D Ball — REMOVED from the home screen (2026-07-08 voice-mode
          remodel). The official SYRIX orb now lives inside the Voice Mode
          overlay (aurora shell) as the face of live voice conversation; the
          home HUD stays clean chat-first chrome, and the old dock-into-the-
          island up/down travel is gone with it. `ballRef` is intentionally
          never mounted, so the guarded ballApi effects all no-op. */}

      {/* Sleep animation — logo box appears, brackets expand outward, all fades */}
      {sleepingAnim && (
        <div className="p-sleep-anim">
          <span className="p-sleep-bk-l">[</span>
          <div className="p-sleep-logo-box">
            <img className="p-sleep-logo" src="../assets/icons/thinking-syrix.png" alt="SYRIX" draggable={false} />
          </div>
          <span className="p-sleep-bk-r">]</span>
        </div>
      )}

      {/* Transcript pill */}
      {mode === 'listening' && transcript && (
        <div className="p-transcript">{transcript}</div>
      )}

      {/* Corner clock */}
      <div className={"p-corner-clock" + ((corner || hudAppOpen) && !focusType ? ' show' : '')}>
        <div className="p-corner-time">{timeStr}</div>
        <div className={"p-corner-status" + (mode === 'listening' ? ' listen' : mode === 'processing' ? ' processing' : mode === 'speaking' ? ' speaking' : '')}>{statusLabel}</div>
      </div>

      {/* Bottom bar — hidden under aurora-glass shell (z 8500 > 200) */}
      <div className="p-bar" />

      {/* Widgets */}
      <WidgetLayer widgets={widgets} onClose={removeWidget} focusedId={focusedWidgetId} onFocusChange={setFocusedWidgetId} />


      {/* Chat */}
      <SyrixChat open={chatOpen} onClose={() => setChatOpen(false)} />

      {/* Brain switcher — rebuilt 2026-06-01 as a minimal one-click selector
          (Auto / Groq / Ollama / NIM rows) replacing a 428-line legacy panel
          with too many edge cases. */}
      {nimOpen && (() => {
        const autoActive = !brainManual || brainManual === 'auto';
        const FALLBACK_BRAINS = [
          { id: 'groq', brain_id: 'groq', display: 'Groq', displayName: 'Groq', provider: 'groq', providerLabel: 'Groq', provider_key: 'groq', model: 'llama-3.3-70b-versatile', best_for: 'Fast cloud reasoning', status: 'pending', available: true, caps: ['stream'], aliases: ['groq'] },
          { id: 'ollama', brain_id: 'ollama', display: 'Ollama', displayName: 'Ollama', provider: 'ollama', providerLabel: 'Ollama', provider_key: 'ollama', model: 'local', best_for: 'Private offline inference', status: 'pending', available: true, caps: ['stream','local'], aliases: ['ollama'] },
        ];
        const rows = Array.isArray(brainList) && brainList.length > 0 ? brainList : FALLBACK_BRAINS;
        const providerKey = (b) => String(b.provider_key || b.provider || '').trim().toLowerCase();
        const brainDisplay = (b) => String((b && (b.displayName || b.display)) || '').trim();
        const brainMatches = (b, id) => {
          const target = String(id || '').trim();
          if (!b || !target) return false;
          if (String(b.id || '') === target || String(b.brain_id || '') === target || String(b.model || '') === target) return true;
          return Array.isArray(b.aliases) && b.aliases.some(a => String(a || '') === target);
        };
        function tidyBrainId(id) {
          const tail = String(id || '').split('/').pop();
          return tail.replace(/[-_]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        }
        const groq    = rows.find(b => providerKey(b) === 'groq')   || null;
        const ollama  = rows.find(b => providerKey(b) === 'ollama') || null;
        const nimRows = rows.filter(b => providerKey(b) === 'nim');
        const activeBrain = rows.find(b => brainMatches(b, brainManual));
        const manualLabel = activeBrain ? brainDisplay(activeBrain) : (brainManual !== 'auto' ? tidyBrainId(brainManual) : '');
        const pendingBrain = rows.find(b => brainMatches(b, brainPending));
        const pendingLabel = pendingBrain ? brainDisplay(pendingBrain) : (brainPending ? tidyBrainId(brainPending) : '');

        function brainBlockedReason(b) {
          const st = String((b && b.status) || '').toLowerCase();
          const reason = String((b && b.reason) || (b && b.switch_reason) || '').trim();
          if (st === 'eol') return reason || 'model is no longer available';
          if (st === 'utility') return reason || 'internal utility model';
          return '';
        }
        function statusColor(b) {
          const s = String((b && b.status) || '').toLowerCase();
          if (s === 'online') return '#16d37a';
          if (s === 'ready')  return '#7be3c0';
          if (s === 'cooldown') return '#f5c243';
          if (s === 'offline' || s === 'error' || s === 'disabled') return '#ff4f6e';
          if (s === 'eol' || s === 'utility') return 'rgba(120,140,170,0.5)';
          return 'rgba(180,210,255,0.55)';
        }
        function statusLabel(b, isActive, isPending) {
          if (isPending) return 'SWITCHING';
          if (isActive) return 'ACTIVE';
          const st = String((b && b.status) || '').toLowerCase();
          const reason = String((b && b.reason) || '').toLowerCase();
          if (st === 'cooldown') return 'COOLDOWN';
          if (st === 'eol' || st === 'utility') return 'UNAVAILABLE';
          if (reason.includes('missing') || reason.includes('key') || reason.includes('config')) return 'NEEDS KEY';
          if (b && b.available === false) return 'UNAVAILABLE';
          if (st === 'error') return 'FAILED';
          if (st === 'offline' || st === 'disabled') return 'OFFLINE';
          return 'AVAILABLE';
        }

        function commitBrainSelection(selection) {
          if (brainPendingRef.current) return;
          const rawTarget = String((selection && typeof selection === 'object') ? (selection.id || selection.brain_id || selection.model || 'auto') : (selection || 'auto')).trim();
          const target = rawTarget.includes('/') ? rawTarget : rawTarget.toLowerCase();
          if (!target) return;
          const bridge = window.SyrixBridge;
          const bridgeSnap = bridge && typeof bridge.getSnapshot === 'function' ? bridge.getSnapshot() : null;
          if (!bridge || typeof bridge.setBrainProvider !== 'function' || (bridgeSnap && bridgeSnap.status && bridgeSnap.status !== 'online')) {
            setBrainSwitchError('Backend unavailable — WebSocket is not connected.');
            return;
          }
          try { console.info('[BRAIN_TRACE] brain_button_clicked brain=' + target); } catch (_) {}
          window.SyrixSound?.click?.();
          setBrainSwitchError('');
          brainPendingRef.current = target;
          setBrainPending(target);
          const ok = bridge.setBrainProvider((selection && typeof selection === 'object') ? selection : target);
          if (!ok) {
            clearBrainPending();
            setBrainSwitchError('Backend unavailable — WebSocket is not connected.');
            window.SyrixBridge?.listBrains?.();
            return;
          }
          window.setTimeout(() => window.SyrixBridge?.listBrains?.(), 250);
          window.setTimeout(() => window.SyrixBridge?.listBrains?.(), 1000);
          brainSwitchTimerRef.current = window.setTimeout(() => {
            if (brainPendingRef.current === target) {
              try { window.SyrixBridge?.listBrains?.(); } catch (_) {}
              window.setTimeout(() => {
                if (brainPendingRef.current === target) {
                  clearBrainPending();
                  setBrainSwitchError('Backend did not confirm ' + (pendingLabel || tidyBrainId(target)) + '.');
                }
              }, 750);
            }
          }, 8000);
        }

        function BrainToggle({ b, isActive, isPending }) {
          const st = String(b.status || '').toLowerCase();
          const isEol = st === 'eol';
          const isUtility = st === 'utility';
          const blockedReason = brainBlockedReason(b);
          const disabled = isEol || isUtility || (!!brainPending && !isPending);
          const title = isActive
            ? 'Active — all requests routed to ' + brainDisplay(b) + '. Click AUTO to release.'
            : isPending ? 'Switching to ' + brainDisplay(b) + '…'
            : blockedReason ? brainDisplay(b) + ' unavailable: ' + blockedReason
            : 'Switch to ' + brainDisplay(b);
          const sLbl = statusLabel(b, isActive, isPending);
          return (
            <button
              type="button"
              disabled={disabled}
              title={title}
              aria-pressed={isActive}
              aria-busy={isPending}
              onClick={(e) => {
                e.stopPropagation();
                if (disabled || brainPending) return;
                if (isActive) commitBrainSelection('auto');
                else commitBrainSelection(b);
              }}
              style={{
                width: '100%', textAlign: 'left',
                padding: '10px 12px', marginBottom: 6,
                background: isActive ? 'rgba(34,211,238,0.10)' : isPending ? 'rgba(245,194,67,0.10)' : 'rgba(255,255,255,0.03)',
                border: '1px solid ' + (isActive ? 'rgba(34,211,238,0.35)' : 'rgba(255,255,255,0.08)'),
                borderRadius: 10,
                color: '#e9f3ff',
                cursor: disabled ? 'not-allowed' : 'pointer',
                opacity: disabled ? 0.55 : 1,
                display: 'flex', alignItems: 'center', gap: 10,
              }}
            >
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: statusColor(b), flexShrink: 0 }} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <div style={{ font: '600 12px Inter', color: '#e9f3ff', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{brainDisplay(b)}</div>
                <div style={{ font: '400 10px Inter', color: 'rgba(180,200,225,0.6)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.best_for || b.model || ''}</div>
              </span>
              <span style={{ font: '700 8px JetBrains Mono', letterSpacing: '0.16em', color: statusColor(b), flexShrink: 0 }}>{sLbl}</span>
            </button>
          );
        }

        const headerSub = brainPending
          ? 'Switching · ' + (pendingLabel || tidyBrainId(brainPending))
          : autoActive ? 'Auto routing active'
          : 'Manual · ' + (manualLabel || brainManual);

        return (
          <div onClick={() => setNimOpen(false)} style={nimPanelStyle}>
            <div onClick={e => e.stopPropagation()} style={{
              minWidth: 340, maxWidth: 420, maxHeight: '78vh',
              background: 'linear-gradient(180deg, rgba(8,14,26,0.96), rgba(3,8,16,0.96))',
              border: '1px solid rgba(34,211,238,0.32)',
              borderRadius: 14,
              boxShadow: '0 30px 80px rgba(0,0,0,0.7), 0 0 80px -20px rgba(34,211,238,0.42)',
              padding: 16,
              display: 'flex', flexDirection: 'column', gap: 10,
              overflow: 'hidden',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div>
                  <div style={{ font: '600 11px JetBrains Mono', letterSpacing: '0.26em', color: nimAccent }}>BRAIN</div>
                  <div style={{ marginTop: 4, font: '500 9px JetBrains Mono', letterSpacing: '0.14em', color: nimMuted, textTransform: 'uppercase' }}>{headerSub}</div>
                </div>
                <button onClick={() => setNimOpen(false)} title="Close" style={{
                  background: 'transparent', border: '1px solid rgba(255,255,255,0.12)',
                  color: '#e9f3ff', borderRadius: 6, padding: '4px 8px',
                  font: '600 10px JetBrains Mono', cursor: 'pointer',
                }}>✕</button>
              </div>

              <button
                type="button"
                disabled={!!brainPending}
                onClick={(e) => { e.stopPropagation(); if (!brainPending) commitBrainSelection('auto'); }}
                style={{
                  width: '100%', textAlign: 'left',
                  padding: '10px 12px',
                  background: autoActive ? 'rgba(22,211,122,0.12)' : 'rgba(255,255,255,0.03)',
                  border: '1px solid ' + (autoActive ? 'rgba(22,211,122,0.35)' : 'rgba(255,255,255,0.08)'),
                  borderRadius: 10,
                  color: '#e9f3ff',
                  cursor: brainPending ? 'not-allowed' : 'pointer',
                  display: 'flex', alignItems: 'center', gap: 10,
                }}
              >
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: autoActive ? '#16d37a' : 'rgba(180,210,255,0.5)', flexShrink: 0 }} />
                <span style={{ flex: 1 }}>
                  <div style={{ font: '600 12px Inter', color: '#e9f3ff' }}>Auto</div>
                  <div style={{ font: '400 10px Inter', color: 'rgba(180,200,225,0.6)', marginTop: 2 }}>Smart routing — picks the best available brain per intent</div>
                </span>
                <span style={{ font: '700 8px JetBrains Mono', letterSpacing: '0.16em', color: autoActive ? '#16d37a' : 'rgba(180,210,255,0.55)' }}>{autoActive ? 'ACTIVE' : 'USE'}</span>
              </button>

              <div style={{ overflowY: 'auto', display: 'flex', flexDirection: 'column', paddingRight: 4 }}>
                {groq && (
                  <>
                    <div style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.2em', color: 'rgba(180,200,225,0.5)', textTransform: 'uppercase', marginTop: 6, marginBottom: 4 }}>Cloud</div>
                    <BrainToggle b={groq} isActive={brainMatches(groq, brainManual)} isPending={brainMatches(groq, brainPending)} />
                  </>
                )}
                {nimRows.length > 0 && (
                  <>
                    <div style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.2em', color: 'rgba(180,200,225,0.5)', textTransform: 'uppercase', marginTop: 6, marginBottom: 4 }}>NVIDIA NIM</div>
                    {nimRows.map(b => (
                      <BrainToggle key={b.id || b.brain_id || b.model} b={b} isActive={brainMatches(b, brainManual)} isPending={brainMatches(b, brainPending)} />
                    ))}
                  </>
                )}
                {ollama && (
                  <>
                    <div style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.2em', color: 'rgba(180,200,225,0.5)', textTransform: 'uppercase', marginTop: 6, marginBottom: 4 }}>Local</div>
                    <BrainToggle b={ollama} isActive={brainMatches(ollama, brainManual)} isPending={brainMatches(ollama, brainPending)} />
                  </>
                )}
              </div>

              {brainSwitchError && (
                <div style={{
                  padding: '8px 10px', borderRadius: 8,
                  background: 'rgba(255,79,110,0.08)', border: '1px solid rgba(255,79,110,0.28)',
                  font: '500 11px Inter', color: '#ff8fa3',
                }}>{brainSwitchError}</div>
              )}
            </div>
          </div>
        );
      })()}

      {/* Settings — full-window page (syrix-settings.jsx). */}
      {settingsOpen && window.SyrixSettings && (
        <window.SyrixSettings
          onClose={() => setSettingsOpen(false)}
          palette={palette}
          onPaletteChange={selectPalette}
          localNotifications={hudNotifications}
        />
      )}

      {/* HUD App Dock — embedded BrowserView frame controls. Renders nothing
          when no apps are open; transparent everywhere except chrome strips
          so the animated p-grid background and HUD top/bottom bars remain
          visible and reachable even when an app is "fullscreen in HUD". */}
      {window.SyrixHudAppDock && (
        <window.SyrixHudAppDock onAnyOpenChange={setHudAppOpen} />
      )}

      {/* Phase-2D: SYRIX 3D Sandbox.
          Sandbox3D mounts once and stays dormant until the user adds an object
          or asks for the sandbox via voice. */}
      {window.SyrixSandbox3D && (
        <window.SyrixSandbox3D
          workspaceOpen={(() => {
            const B = window.SyrixBridge;
            const s = B && B.getSnapshot && B.getSnapshot();
            const vw = s && s.syrix_vision_workspace;
            const inWorkspace = !!(vw && vw.open && vw.mode !== 'minimized');
            // Keep the sandbox stage visible whenever a model has been spawned
            // via the picker, even with no vision workspace open.
            const hasSpawned = !!lastModelSpawned;
            return inWorkspace || hasSpawned;
          })()}
          showSidePanel={sandboxPanelOpen}
        />
      )}

      {/* Phase-4 (2026-05-17): 3D model picker modal. Tapping a model spawns
          it via sandboxCreate() — the existing pipeline; gestures + voice
          still work on it via the SyrixModel3DRegistry. */}
      {modelPickerOpen && window.SyrixModels3D && (
        <div onClick={() => setModelPickerOpen(false)} style={{
          position: 'fixed', inset: 0, zIndex: 1900,
          background: 'rgba(2,5,12,0.55)', backdropFilter: 'blur(8px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            width: 'min(720px, 92vw)', maxHeight: '78vh', overflowY: 'auto',
            background: 'rgba(3,8,16,0.95)',
            border: '1px solid rgba(34,211,238,0.3)', borderRadius: 14,
            padding: '16px 18px',
            boxShadow: '0 30px 80px rgba(0,0,0,0.65), 0 0 80px -20px rgba(34,211,238,0.45)',
            color: '#e7f5ff',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
              <div>
                <div style={{ font: '600 12px JetBrains Mono', letterSpacing: '0.32em', color: '#d4aa5a', textTransform: 'uppercase' }}>
                  3D Model Library
                </div>
                <div style={{ marginTop: 6, font: '500 10px JetBrains Mono', letterSpacing: '0.18em', color: 'rgba(180,210,255,0.6)', textTransform: 'uppercase' }}>
                  Tap to spawn · gestures rotate / zoom / pan · click ✕ to close
                </div>
              </div>
              <button onClick={() => setModelPickerOpen(false)} style={{
                background: 'none', border: 'none', color: 'rgba(180,200,220,0.65)',
                cursor: 'pointer', fontSize: 16, padding: 4,
              }}>✕</button>
            </div>
            {(() => {
              const cats = window.SyrixModels3D.categories();
              const labels = {
                space: 'Space & planets',
                anatomy: 'Human anatomy',
                biology: 'Biology',
                animals: 'Animals',
                chemistry: 'Chemistry',
                engineering: 'Engineering',
                computing: 'Computing & AI',
              };
              return cats.map((c) => (
                <div key={c.category} style={{ marginBottom: 14 }}>
                  <div style={{
                    font: '600 10px JetBrains Mono', letterSpacing: '0.28em',
                    color: 'rgba(180,210,255,0.7)', textTransform: 'uppercase',
                    marginBottom: 8,
                  }}>{labels[c.category] || c.category}</div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 8 }}>
                    {c.ids.map((id) => {
                      const meta = window.SyrixModels3D.describe(id) || {};
                      return (
                        <button key={id}
                          title={meta.summary || ''}
                          onClick={() => {
                            // Spawn locally — backend echo would double-spawn,
                            // so don't mirror to the Python sandbox_create path.
                            try {
                              if (window.SyrixSandbox3DApi && window.SyrixSandbox3DApi.add) {
                                window.SyrixSandbox3DApi.add(id);
                              }
                            } catch (_) {}
                            try {
                              setLastModelSpawned({ id, name: meta.name || id, summary: meta.summary || '' });
                            } catch (_) {}
                          }}
                          style={{
                            background: 'rgba(34,211,238,0.06)',
                            border: '1px solid rgba(34,211,238,0.22)',
                            borderRadius: 8, color: '#d4aa5a',
                            padding: '9px 10px', textAlign: 'left',
                            font: "600 10px 'JetBrains Mono', monospace",
                            letterSpacing: '0.14em', textTransform: 'uppercase',
                            cursor: 'pointer',
                          }}>
                          {meta.name || id}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ));
            })()}
            {lastModelSpawned && (
              <div style={{
                marginTop: 6, padding: '10px 12px',
                background: 'rgba(34,211,238,0.06)',
                border: '1px solid rgba(34,211,238,0.22)',
                borderRadius: 8,
              }}>
                <div style={{
                  font: '600 10px JetBrains Mono', letterSpacing: '0.22em',
                  color: '#d4aa5a', textTransform: 'uppercase',
                  marginBottom: 6,
                }}>
                  Spawned · {lastModelSpawned.name}
                </div>
                <div style={{ font: '400 12px Inter', color: 'rgba(220,235,255,0.85)', lineHeight: 1.45 }}>
                  {lastModelSpawned.summary}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* Status words for provider health, shared with the island brain menu. */
function formatSyrixStatusLabel(value, opts = {}) {
  const raw = String(value || '').trim().toLowerCase();
  if (!raw) return opts.available ? 'Ready' : 'Idle';
  const direct = {
    ok: 'Ready',
    ready: 'Ready',
    online: 'Ready',
    active: 'Ready',
    live: 'Ready',
    idle: 'Idle',
    status_unknown: 'Idle',
    unavailable: 'Idle',
    disabled: 'Disabled',
    eol: 'Disabled',
    retired: 'Disabled',
    configured_no_endpoint: 'Missing endpoint',
    missing_endpoint: 'Missing endpoint',
    endpoint_missing: 'Missing endpoint',
    endpoint_url_pending: 'Missing endpoint',
    no_endpoint: 'Missing endpoint',
    not_configured: 'Not configured',
    auth_required: 'Auth required',
    missing_key: 'Missing key/config',
    key_missing: 'Missing key/config',
    auth_invalid: 'Missing key/config',
    missing_config: 'Missing key/config',
    configured_no_key: 'Missing key/config',
    unknown_provider: 'Missing config',
    not_a_manual_brain_route: 'Idle',
    cooldown: 'Cooling down',
    timeout: 'Failed test',
    network: 'Failed test',
    error: 'Failed test',
    failed: 'Failed test',
  };
  if (direct[raw]) return direct[raw];
  if (raw.includes('endpoint')) return 'Missing endpoint';
  if (raw.includes('key') || raw.includes('auth') || raw.includes('config')) return 'Missing key/config';
  if (raw.includes('unknown_provider') || (raw.includes('unknown') && raw.includes('provider'))) return 'Missing config';
  if (raw.includes('not_a_manual')) return 'Idle';
  if (raw.includes('cooldown') || raw.includes('cooling')) return 'Cooling down';
  if (raw.includes('fail') || raw.includes('error') || raw.includes('timeout') || raw.includes('network')) return 'Failed test';
  return raw.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/* ── App root ────────────────────────────────────────────────────── */
function App() {
  const [booted,   setBooted]   = useState(false);
  const [hudReady, setHudReady] = useState(false);
  return (
    <>
      {(hudReady || booted) && <PrimaryHUD />}
      {!booted && (
        <BootScreen
          onReveal={() => setHudReady(true)}
          onDone={()   => setBooted(true)}
        />
      )}
    </>
  );
}

/* 2026-07-08 — ROOT-CAUSE HARDENING for "HUD randomly goes blank". Any
   uncaught error thrown during render (e.g. the EgChatPanel
   `ourTurnAnswered` ReferenceError that fired whenever a streamed token
   bubble mounted) made React 18 unmount the ENTIRE root — a dead black
   window with no message and no way back except killing electron. This
   boundary catches the throw, logs it where main.js can see it
   ([HUD_RENDERER_CONSOLE] → syrix_startup.log), and remounts the tree after
   a short beat; all durable state rehydrates from the backend snapshot over
   WS so the user just sees a sub-second shimmer instead of a blank HUD.
   Three crashes inside a minute means a real repeating fault — then we stop
   looping and show a minimal recovery screen with the error + a reload
   button so it can be reported instead of silently flickering forever. */
class SyrixRootErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { crashed: false, lastError: '' };
    this.crashTimes = [];
    this.retryTimer = null;
  }
  static getDerivedStateFromError(error) {
    return { crashed: true, lastError: String((error && (error.stack || error.message)) || error || 'unknown').slice(0, 600) };
  }
  componentDidCatch(error, info) {
    try {
      console.error('[HUD_ROOT_BOUNDARY] render_crash', String(error && (error.stack || error.message) || error),
        String(info && info.componentStack || '').slice(0, 800));
    } catch (_) {}
    const now = Date.now();
    this.crashTimes = this.crashTimes.filter((t) => now - t < 60000);
    this.crashTimes.push(now);
    if (this.crashTimes.length <= 3) {
      this.retryTimer = setTimeout(() => this.setState({ crashed: false }), 800);
    }
  }
  componentWillUnmount() { if (this.retryTimer) clearTimeout(this.retryTimer); }
  render() {
    if (!this.state.crashed) return this.props.children;
    if (this.crashTimes.length <= 3) return null; // sub-second gap while the remount timer runs
    return (
      <div style={{ position: 'fixed', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#03050b', zIndex: 99999 }}>
        <div style={{ maxWidth: 520, padding: '28px 32px', borderRadius: 14, border: '1px solid rgba(34,211,238,0.25)', background: 'rgba(6,14,28,0.92)' }}>
          <div style={{ font: '600 12px JetBrains Mono', letterSpacing: '0.28em', color: 'rgba(34,211,238,0.85)', marginBottom: 10 }}>SYRIX INTERFACE FAULT</div>
          <div style={{ font: '500 12px/1.5 Inter', color: 'rgba(220,235,255,0.85)', marginBottom: 14 }}>
            The interface crashed repeatedly and auto-recovery was stopped to avoid a flicker loop.
          </div>
          <pre style={{ font: '500 10px/1.45 JetBrains Mono', color: 'rgba(255,160,160,0.85)', whiteSpace: 'pre-wrap', margin: '0 0 16px', maxHeight: 160, overflow: 'auto' }}>
            {this.state.lastError}
          </pre>
          <button
            onClick={() => { try { window.location.reload(); } catch (_) {} }}
            style={{ font: '600 11px JetBrains Mono', letterSpacing: '0.18em', color: '#03050b', background: 'rgba(34,211,238,0.9)', border: 'none', borderRadius: 8, padding: '9px 18px', cursor: 'pointer' }}>
            RELOAD HUD
          </button>
        </div>
      </div>
    );
  }
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<SyrixRootErrorBoundary><App /></SyrixRootErrorBoundary>);



