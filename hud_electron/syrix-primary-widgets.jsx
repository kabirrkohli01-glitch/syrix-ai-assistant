/* SYRIX WIDGETS — Draggable floating widgets */
const { useState: uWS, useEffect: uWE, useRef: uWR } = React;

const widgetCSS = `
/* z-index must clear .eg-shell (aurora-glass.css, z-index:900) — since
   2026-07-09 chat is permanently mounted 'open' as a full-area background
   surface inside .eg-shell, so anything below 900 here is unreachable:
   the chat panel silently eats every click/drag meant for a widget. */
.p-wlayer { position: fixed; inset: 0; pointer-events: none; z-index: 950; }
.p-wlayer.focused { pointer-events: auto; }
.p-widget {
  position: absolute; pointer-events: auto;
  background: rgba(6,10,20,0.86);
  -webkit-backdrop-filter: blur(32px) saturate(1.7) brightness(0.82);
  backdrop-filter: blur(32px) saturate(1.7) brightness(0.82);
  border: 0.5px solid rgba(var(--eg-accent-rgb),0.28);
  border-radius: var(--au-r-lg, 22px);
  box-shadow: 0 20px 64px rgba(0,0,0,0.78),
              0 0 0 0.5px rgba(var(--eg-accent-rgb),0.10),
              inset 0 1px 0 rgba(255,248,220,0.06);
  overflow: hidden; isolation: isolate;
  animation: au-unfurl 0.46s var(--au-ease-ios, cubic-bezier(0.23,1,0.32,1)) both;
  transition: width 0.38s cubic-bezier(0.23,1,0.32,1),
              height 0.38s cubic-bezier(0.23,1,0.32,1),
              border-radius 0.26s ease,
              box-shadow 0.26s ease;
}
.p-widget.is-dragging {
  transition: none !important;
  box-shadow: 0 28px 80px rgba(0,0,0,0.88), 0 0 0 1px rgba(var(--eg-accent-rgb),0.32), inset 0 1px 0 rgba(255,248,220,0.07) !important;
  cursor: grabbing !important;
}
.p-widget.is-closing {
  animation: au-furl 0.22s cubic-bezier(0.55,0,1,0.45) both !important;
  pointer-events: none;
  transition: none;
}
@keyframes au-unfurl {
  from { opacity: 0; transform: scale(0.88) translateY(18px) translateZ(0); filter: blur(4px); }
  to   { opacity: 1; transform: scale(1) translateY(0) translateZ(0); filter: blur(0); }
}
@keyframes au-furl {
  0%   { opacity: 1; transform: scale(1) translateY(0) translateZ(0); filter: blur(0); }
  40%  { opacity: 0.6; }
  100% { opacity: 0; transform: scale(0.06) translateY(52px) translateZ(0); filter: blur(6px); }
}
/* catch-light */
.p-widget::before {
  content:''; position:absolute; inset:0; border-radius:inherit; pointer-events:none; z-index:0;
  background:
    radial-gradient(ellipse 70% 24% at 30% 0%, rgba(var(--eg-accent-rgb),0.08), transparent 100%),
    linear-gradient(to bottom, rgba(255,245,210,0.07) 0px, transparent 1px);
}
/* specular band */
.p-widget::after {
  content:''; position:absolute; inset:0; border-radius:inherit; pointer-events:none; z-index:0;
  background: linear-gradient(132deg, transparent 28%, rgba(var(--eg-accent-surface-rgb),0.035) 46%, rgba(255,255,255,0.055) 50%, rgba(var(--eg-accent-surface-rgb),0.035) 54%, transparent 72%);
  opacity: 0.7; transition: opacity 0.26s;
}
.p-widget:hover { border-color: rgba(var(--eg-accent-rgb),0.42) !important; }
.p-widget:hover::after { opacity: 1; }
.p-widget.focused {
  position: fixed;
  left: 258px !important; top: 70px !important;
  width: calc(100vw - 310px) !important; height: calc(100vh - 128px) !important;
  border-radius: var(--au-r-xl, 28px);
  border-color: rgba(var(--eg-accent-rgb),0.48) !important;
  box-shadow:
    0 36px 90px rgba(0,0,0,0.88),
    0 0 0 1px rgba(var(--eg-accent-rgb),0.24),
    0 0 52px -8px rgba(var(--eg-accent-rgb),0.22),
    inset 0 1px 0 rgba(255,248,230,0.07);
  animation: none;
}
.p-w-bar {
  display: flex; align-items: center; gap: 8px; padding: 10px 14px;
  background: rgba(0, 4, 12, 0.72);
  -webkit-backdrop-filter: var(--au-blur-1, blur(10px));
  backdrop-filter: var(--au-blur-1, blur(10px));
  border-bottom: 0.5px solid rgba(var(--eg-accent-rgb),0.12);
  cursor: grab; user-select: none; position: relative; z-index: 2;
}
.p-w-bar:active { cursor: grabbing; }
.p-widget.focused .p-w-bar { cursor: default; }
.p-w-dot { width: 8px; height: 8px; border-radius: 50%; background: rgba(var(--eg-accent-surface-rgb),0.90); box-shadow: 0 0 6px rgba(var(--eg-accent-rgb),0.55); flex-shrink: 0; }
.p-w-title { font: 600 9px/1 'JetBrains Mono'; letter-spacing: 0.32em; color: rgba(var(--eg-accent-rgb),0.88); flex: 1; text-transform: uppercase; }
.p-w-x { background: none; border: none; cursor: pointer; color: rgba(110,140,170,0.5); font-size: 14px; padding: 3px 5px; transition: color .15s, transform .15s; line-height: 1; }
.p-w-x:hover { color: #ff4f6e; }
.p-w-x:active { transform: scale(0.94); }
.p-w-focus:hover { color: rgb(var(--eg-accent-rgb)); }
.p-w-body { padding: 14px; }
.p-widget.focused .p-w-body { height: calc(100% - 39px); overflow: auto; padding: 22px; }
.p-widget.focused .p-w-body:has(iframe[title="Ventusky live weather map"]) {
  display: flex; flex-direction: column; min-height: 0; overflow: hidden;
}
.p-widget.focused .p-w-body:has(iframe[title="Ventusky live weather map"]) .pw-weather-top,
.p-widget.focused .p-w-body:has(iframe[title="Ventusky live weather map"]) .pw-w-grid,
.p-widget.focused .p-w-body:has(iframe[title="Ventusky live weather map"]) .pw-meta-row {
  flex: 0 0 auto;
}
.p-widget.focused .p-w-body:has(iframe[title="Ventusky live weather map"]) > div:last-child {
  flex: 1 1 auto; min-height: 0; display: flex; flex-direction: column;
}
.p-widget.focused .p-w-body:has(iframe[title="Ventusky live weather map"]) > div:last-child > div { flex: 0 0 auto; }
.p-widget.focused .p-w-body:has(iframe[title="Ventusky live weather map"]) iframe[title="Ventusky live weather map"] { flex: 1 1 auto; min-height: 320px; height: auto !important; width: 100% !important; }

/* ── Leaflet map canvas ── */
.pw-map-canvas { width: 100%; background: #03050b; display: block; }
.pw-map-fallback {
  padding: 6px 10px;
  font: 500 11px Inter; color: rgb(var(--eg-accent-rgb));
  background: rgba(15,10,2,0.85);
  border-top: 1px solid rgba(var(--eg-accent-rgb),0.14);
}
/* Dark amber Leaflet theme */
.leaflet-container { background: #03050b !important; }
.leaflet-tile-pane { filter: sepia(0.55) brightness(0.82) hue-rotate(6deg) saturate(0.78); }
.leaflet-control-zoom a {
  background: rgba(3,9,20,0.92) !important; color: rgb(var(--eg-accent-rgb)) !important;
  border-color: rgba(var(--eg-accent-rgb),0.22) !important; font-weight: 600;
}
.leaflet-control-zoom a:hover { background: rgba(var(--eg-accent-surface-rgb),0.12) !important; }
.leaflet-control-attribution { background: rgba(3,9,20,0.7) !important; color: rgba(110,140,170,0.5) !important; font-size: 9px !important; }
.leaflet-control-attribution a { color: rgba(var(--eg-accent-rgb),0.5) !important; }
.leaflet-popup-content-wrapper {
  background: rgba(4,11,22,0.93) !important;
  border: 1px solid rgba(var(--eg-accent-rgb),0.30) !important;
  border-radius: 8px !important;
  box-shadow: 0 10px 32px rgba(0,0,0,0.65) !important;
  color: #e9f3ff !important;
  font: 500 11px Inter !important;
}
.leaflet-popup-tip { display: none !important; }

/* ── Music widget ── */
.pw-disc {
  width: 100%; aspect-ratio: 1/1; background: radial-gradient(circle at 40% 35%, #0a1c38, #050e1c);
  border-radius: 10px; display: flex; align-items: center; justify-content: center; margin-bottom: 12px;
  border: 1px solid rgba(var(--eg-accent-rgb),0.07);
}
.pw-music-name { font: 600 13px/1.2 'Inter'; color: #e9f3ff; text-align: center; }
.pw-music-artist { font: 500 10px/1 'JetBrains Mono'; letter-spacing: 0.18em; color: rgba(var(--eg-accent-rgb),0.60); margin-top: 4px; text-align: center; }
.pw-prog { height: 3px; background: rgba(255,255,255,0.07); border-radius: 2px; margin: 14px 0; overflow: hidden; position: relative; }
.pw-prog-fill { height: 100%; background: linear-gradient(90deg,rgb(var(--eg-accent-surface-rgb)),rgb(var(--eg-accent-surface-rgb))); border-radius: 2px; transition: width .5s linear, opacity .3s linear; }
/* Indeterminate playing-indicator: backend does not publish a real song
   position, so we never claim one. The bar shows an animated sweep only
   when state==='playing'; otherwise it stays empty / dimmed. */
.pw-prog.idle .pw-prog-fill { width: 0; opacity: 0.25; }
.pw-prog.paused .pw-prog-fill { width: 100%; opacity: 0.25; animation: none; background: rgba(var(--eg-accent-surface-rgb),0.35); }
.pw-prog.playing .pw-prog-fill {
  width: 38%;
  background: linear-gradient(90deg, transparent, rgb(var(--eg-accent-surface-rgb)) 50%, transparent);
  animation: pwSweep 1.8s cubic-bezier(0.4,0,0.2,1) infinite;
}
@keyframes pwSweep { 0% { transform: translateX(-110%); } 100% { transform: translateX(280%); } }
.pw-meta-row { display: flex; gap: 6px; align-items: center; justify-content: center; margin-top: 6px; flex-wrap: wrap; }
.pw-chip {
  font: 600 8px 'JetBrains Mono'; letter-spacing: 0.18em; text-transform: uppercase;
  padding: 2px 6px; border-radius: 4px;
  background: rgba(var(--eg-accent-surface-rgb),0.07); border: 1px solid rgba(var(--eg-accent-rgb),0.22);
  color: rgba(var(--eg-accent-rgb),0.78);
}
.pw-chip.live   { color: #16d37a; border-color: rgba(22,211,122,0.32); background: rgba(22,211,122,0.07); }
.pw-chip.stale  { color: rgb(var(--eg-accent-rgb)); border-color: rgba(var(--eg-accent-rgb),0.4); background: rgba(var(--eg-accent-surface-rgb),0.06); }
.pw-chip.error  { color: #ff7a90; border-color: rgba(255,79,110,0.4); background: rgba(255,79,110,0.06); }
.pw-chip.muted  { color: rgba(var(--eg-accent-rgb),0.50); border-color: rgba(var(--eg-accent-rgb),0.12); background: rgba(255,255,255,0.02); }
.pw-ctrls { display: flex; align-items: center; justify-content: center; gap: 16px; }
.pw-btn { background: none; border: none; cursor: pointer; color: rgba(var(--eg-accent-rgb),0.80); display: flex; padding: 6px; border-radius: 50%; transition: all .15s; }
.pw-btn:hover { color: rgb(var(--eg-accent-rgb)); background: rgba(var(--eg-accent-surface-rgb),0.10); box-shadow: 0 0 0 1px rgba(var(--eg-accent-rgb),0.15), 0 0 14px rgba(var(--eg-accent-rgb),0.14); }
.pw-play {
  width: 44px; height: 44px; background: rgba(var(--eg-accent-surface-rgb),0.12);
  border: 1px solid rgba(var(--eg-accent-rgb),0.30); border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; color: rgb(var(--eg-accent-rgb)); transition: all .15s;
}
.pw-play:hover { background: rgba(var(--eg-accent-surface-rgb),0.24); border-color: rgba(var(--eg-accent-rgb),0.54); box-shadow: 0 0 0 1px rgba(var(--eg-accent-rgb),0.15), 0 0 18px rgba(var(--eg-accent-rgb),0.20); }

/* ── Weather widget ── */
.pw-weather-top { display: flex; align-items: center; gap: 14px; margin-bottom: 14px; }
.pw-temp { font: 700 42px/1 'JetBrains Mono'; color: #e9f3ff; }
.pw-temp-unit { font-size: 20px; color: rgba(var(--eg-accent-rgb),0.60); }
.pw-w-desc { font: 500 12px/1.3 'Inter'; color: rgba(180,210,255,0.8); }
.pw-w-city { font: 600 9px 'JetBrains Mono'; letter-spacing: 0.28em; color: rgba(var(--eg-accent-rgb),0.55); margin-top: 3px; text-transform: uppercase; }
.pw-w-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 7px; }
.pw-w-stat { background: rgba(2,6,16,0.55); border: 1px solid rgba(var(--eg-accent-rgb),0.12); border-radius: 8px; padding: 8px; text-align: center; }
.pw-w-val { font: 600 12px 'JetBrains Mono'; color: #e9f3ff; }
.pw-w-lbl { font: 500 8px 'JetBrains Mono'; letter-spacing: 0.15em; color: rgba(110,140,170,0.7); margin-top: 3px; text-transform: uppercase; }

/* ── Map widget ── */
.pw-map { width: 100%; height: 300px; border-radius: 8px; overflow: hidden; }
.p-widget.focused .pw-map { height: 100%; min-height: 420px; }
.pw-map-frame { width: 100%; height: 300px; border: 0; border-radius: 8px; background: #050e1c; display: block; }
.p-widget.focused .pw-map-frame { height: min(64vh, calc(100% - 128px)); min-height: 420px; }
.pw-map-fallback { padding: 6px 10px; font: 500 10px/1.35 Inter; color: rgba(var(--eg-accent-rgb),0.86); }
/* ── Drawing widget ── */
.pw-drawing-wrap { padding: 10px; display: flex; flex-direction: column; gap: 8px; }
.pw-drawing-tools { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pw-drawing-canvas {
  width: 100%; height: 280px; display: block;
  background: rgba(245,248,255,0.96);
  border: 1px solid rgba(var(--eg-accent-rgb),0.16);
  border-radius: 8px;
  cursor: crosshair;
  touch-action: none;
}
.p-widget.focused .pw-drawing-canvas { height: calc(100vh - 238px); min-height: 420px; }
.pw-brush-color {
  width: 30px; height: 30px; padding: 0; border: 1px solid rgba(var(--eg-accent-rgb),0.22);
  border-radius: 8px; background: rgba(4,10,22,0.6); cursor: pointer;
}
.pw-brush-size { width: 110px; accent-color: rgb(var(--eg-accent-rgb)); }
.pw-drawing-hint { font: 500 9px/1.35 'JetBrains Mono'; letter-spacing: 0.08em; color: rgba(180,210,255,0.58); text-transform: uppercase; }

.pw-search-results { display: grid; gap: 6px; margin-top: 8px; max-height: 150px; overflow: auto; }
.pw-search-item {
  display: grid; grid-template-columns: 38px 1fr auto; gap: 8px; align-items: center;
  padding: 6px; border-radius: 8px; border: 1px solid rgba(var(--eg-accent-rgb),0.12);
  background: rgba(var(--eg-accent-surface-rgb),0.045);
}
.pw-search-art { width: 38px; height: 38px; border-radius: 6px; object-fit: cover; background: rgba(255,255,255,0.08); }
.pw-search-title { font: 600 10px/1.2 Inter; color: #e9f3ff; }
.pw-search-sub { font: 500 8px/1.2 'JetBrains Mono'; letter-spacing: 0.08em; color: rgba(180,210,255,0.62); text-transform: uppercase; margin-top: 2px; }

/* ── Timer widget ── */
.pw-timer-disp {
  font: 200 46px/1 'JetBrains Mono'; color: rgb(var(--eg-accent-rgb)); text-align: center;
  text-shadow: 0 0 28px rgba(var(--eg-accent-rgb),0.40); margin: 8px 0 14px; letter-spacing: 0.025em; white-space: nowrap;
}
.pw-timer-ctrls { display: flex; gap: 8px; justify-content: center; align-items: center; flex-wrap: wrap; }
.p-widget:not(.focused) .pw-timer-ctrls .p-mini-btn { padding-left: 8px; padding-right: 8px; }
.p-widget.focused .pw-timer-disp { font-size: clamp(58px, 9vw, 132px); margin-top: 12vh; letter-spacing: 0.04em; }
.p-widget.focused .pw-timer-ctrls { gap: 12px; }

/* ── Notes widget ── */
.pw-notes-ta {
  width: 100%; height: 150px;
  background: rgba(2,6,16,0.70); border: 1px solid rgba(var(--eg-accent-rgb),0.14); border-radius: 8px;
  color: #e9f3ff; font: 400 12px/1.55 'Inter'; padding: 10px; resize: none; outline: none;
  caret-color: rgb(var(--eg-accent-rgb));
}
.p-widget.focused .pw-notes-ta { height: calc(100% - 44px); min-height: 360px; }
.pw-notes-ta:focus { border-color: rgba(var(--eg-accent-rgb),0.28); }
.pw-notes-ta::placeholder { color: rgba(80,110,150,0.42); }
.pw-notes-btns { display: flex; gap: 6px; margin-top: 8px; }
@media (max-width: 820px) {
  .p-widget.focused {
    left: 18px !important; top: 70px !important;
    width: calc(100vw - 36px) !important; height: calc(100vh - 128px) !important;
  }
}
`;

function playWidgetSound(_name) {
  // Widget button sounds disabled — boot/notify sounds still handled by syrix-audio.js
}

// "5s ago" / "2m ago" / "1h ago" — accepts unix seconds. Returns '' for
// missing/invalid input so widgets can skip the chip entirely.
function timeAgo(tsSeconds) {
  const t = Number(tsSeconds);
  if (!t || !Number.isFinite(t)) return '';
  const delta = Math.max(0, Date.now() / 1000 - t);
  if (delta < 5) return 'just now';
  if (delta < 60) return Math.floor(delta) + 's ago';
  if (delta < 3600) return Math.floor(delta / 60) + 'm ago';
  if (delta < 86400) return Math.floor(delta / 3600) + 'h ago';
  return Math.floor(delta / 86400) + 'd ago';
}

// Returns 'live' / 'stale' / 'error' for a ts older than the threshold.
function freshness(tsSeconds, staleAfterSeconds) {
  const t = Number(tsSeconds);
  if (!t || !Number.isFinite(t)) return 'muted';
  const delta = Date.now() / 1000 - t;
  if (delta < 0) return 'live';
  if (delta < (staleAfterSeconds || 600)) return 'live';
  return 'stale';
}

/* Draggable wrapper — uses direct DOM style updates during drag to avoid
   re-renders and CSS transition lag. Position commits to state only on mouseup. */
function Draggable({ id, title, x, y, onClose, style, focused, onFocus, onBlur, headerExtra, children }) {
  const ref = uWR(null);
  const [pos, setPos] = uWS({ x, y });
  const [closing, setClosing] = uWS(false);
  const drag = uWR(null);

  uWE(() => {
    function mv(e) {
      if (!drag.current || !ref.current) return;
      const nx = e.clientX - drag.current.ox;
      const ny = e.clientY - drag.current.oy;
      ref.current.style.left = nx + 'px';
      ref.current.style.top  = ny + 'px';
      drag.current.nx = nx;
      drag.current.ny = ny;
    }
    function up() {
      if (!drag.current) return;
      const { nx, ny } = drag.current;
      drag.current = null;
      if (ref.current) ref.current.classList.remove('is-dragging');
      if (nx !== undefined) setPos({ x: nx, y: ny });
    }
    window.addEventListener('mousemove', mv, { passive: true });
    window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up); };
  }, []);

  function closeWidget() {
    if (closing) return;
    if (focused && onBlur) onBlur();
    setClosing(true);
    setTimeout(() => onClose(id), 260);
  }

  function toggleFocus(e) {
    e.stopPropagation();
    if (focused) onBlur && onBlur();
    else onFocus && onFocus(id);
  }

  return (
    <div ref={ref} className={"p-widget" + (focused ? ' focused' : '') + (closing ? ' is-closing' : '')} style={{ left: pos.x, top: pos.y, ...style }}>
      <div className="p-w-bar" onMouseDown={e => {
        if (focused) return;
        if (e.target.closest && e.target.closest('.p-w-x')) return;
        const r = ref.current.getBoundingClientRect();
        drag.current = { ox: e.clientX - r.left, oy: e.clientY - r.top };
        ref.current.classList.add('is-dragging');
      }}>
        <div className="p-w-dot"></div>
        <span className="p-w-title">{title}</span>
        {headerExtra || null}
        <button className="p-w-x p-w-focus" title={focused ? 'Exit focus' : 'Focus'} onClick={toggleFocus}>
          {focused ? '↙' : '⛶'}
        </button>
        <button className="p-w-x" onClick={closeWidget}>✕</button>
      </div>
      {children}
    </div>
  );
}

/* Music Widget — Spotify-first controls through main-process IPC.
 * Tokens/client secrets remain in Electron main/preload and are never exposed
 * to the renderer. If Spotify is not configured, the widget falls back to the
 * existing backend music bridge. */
function MusicWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [music, setMusic] = uWS({ provider: '', state: 'idle', track: '', artist: '' });
  const [query, setQuery] = uWS('');
  const [sp, setSp] = uWS(null);
  const [spErr, setSpErr] = uWS('');
  const [spConfig, setSpConfig] = uWS(null);
  const [volume, setVolume] = uWS(null);
  const [results, setResults] = uWS([]);
  const [searching, setSearching] = uWS(false);
  const [devices, setDevices] = uWS([]);
  const [showDevices, setShowDevices] = uWS(false);
  const [loadingDevices, setLoadingDevices] = uWS(false);
  const [spReason, setSpReason] = uWS('');
  const spPollRef = uWR(null);

  uWE(() => {
    try {
      const api = window.syrixHud && window.syrixHud.spotify;
      if (api && api.status) {
        api.status().then((cfg) => setSpConfig(cfg || { configured: false })).catch(() => setSpConfig({ configured: false }));
      }
    } catch (_) {}
  }, []);

  uWE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((s) => {
      if (s && s.music && typeof s.music === 'object') setMusic(s.music);
    });
  }, []);

  function spotifyMessage(res) {
    if (!res) return 'Spotify bridge unavailable.';
    if (res.reason === 'missing_auth') return 'Spotify auth missing. Configure Spotify tokens/client settings, then reopen the HUD.';
    if (res.reason === 'no_active_device') return 'No active Spotify device. Click "DEVICES" below or open Spotify on a phone/desktop once.';
    if (res.reason === 'premium_required') return 'Spotify Premium or active playback permission is required.';
    if (res.reason === 'not_found') return 'No Spotify result found.';
    if (res.reason === 'fetch_unavailable') return 'Spotify proxy unavailable in this Electron build.';
    if (res.reason === 'missing_device') return 'Pick a device first.';
    if (res.status) return 'Spotify error ' + res.status;
    return res.error || 'Spotify request failed.';
  }

  async function loadDevices() {
    setLoadingDevices(true);
    const res = await spCall('devices');
    setLoadingDevices(false);
    if (res && res.ok && Array.isArray(res.devices)) {
      setDevices(res.devices);
      setShowDevices(true);
    } else {
      setDevices([]);
      setShowDevices(true);
    }
  }
  async function transferTo(deviceId, startPlay) {
    const res = await spCall('transfer', { device_id: deviceId, play: !!startPlay });
    if (res && res.ok) {
      setShowDevices(false);
      setSpErr('');
    }
  }

  async function spCall(action, payload) {
    try {
      const api = window.syrixHud && window.syrixHud.spotify;
      if (!api || !api.call) return { ok: false, reason: 'bridge_missing' };
      const res = await api.call(action, payload || {});
      if (!res || !res.ok) {
        setSpReason(String((res && res.reason) || 'spotify_error'));
        setSpErr(spotifyMessage(res));
      } else {
        setSpReason('');
        setSpErr('');
      }
      return res;
    } catch (e) {
      const msg = String(e && e.message ? e.message : e).slice(0, 120);
      setSpErr(msg);
      return { ok: false, error: msg };
    }
  }

  uWE(() => {
    if (!spConfig || !spConfig.configured) return;
    async function poll() {
      const res = await spCall('current');
      if (res && res.ok) {
        const data = res.data || null;
        setSp(data);
        if (data && data.device) setVolume(data.device.volume_percent);
      } else if (res && res.status === 204) {
        setSp(null);
      }
    }
    poll();
    spPollRef.current = setInterval(poll, 2000);
    return () => { clearInterval(spPollRef.current); };
  }, [spConfig]);

  async function spPlay(q) {
    if (!(spConfig && spConfig.configured)) {
      const B = window.SyrixBridge;
      if (!B || !B.musicPlay) { setSpErr('Spotify not connected; media bridge unavailable.'); return; }
      setSpErr('');
      B.musicPlay(q || '');
      return;
    }
    await spCall('play', q ? { query: q } : {});
    playWidgetSound('click');
  }
  async function spPause() {
    if (!(spConfig && spConfig.configured)) {
      const B = window.SyrixBridge;
      if (!B || !B.musicPause) { setSpErr('Spotify not connected; media bridge unavailable.'); return; }
      setSpErr('');
      B.musicPause();
      return;
    }
    await spCall('pause');
    playWidgetSound('click');
  }
  async function spNext() {
    if (!(spConfig && spConfig.configured)) {
      const B = window.SyrixBridge;
      if (!B || !B.musicNext) { setSpErr('Spotify not connected; media bridge unavailable.'); return; }
      setSpErr('');
      B.musicNext();
      return;
    }
    await spCall('next');
    playWidgetSound('click');
  }
  async function spPrev() {
    if (!(spConfig && spConfig.configured)) {
      const B = window.SyrixBridge;
      if (!B || !B.musicPrev) { setSpErr('Spotify not connected; media bridge unavailable.'); return; }
      setSpErr('');
      B.musicPrev();
      return;
    }
    await spCall('previous');
    playWidgetSound('click');
  }
  async function spSeek(posMs) {
    if (!(spConfig && spConfig.configured)) return;
    await spCall('seek', { position_ms: Math.round(posMs) });
  }
  async function spVolume(pct) {
    if (!(spConfig && spConfig.configured)) return;
    setVolume(pct);
    await spCall('volume', { volume_percent: Math.round(pct) });
  }
  async function runSearch() {
    const q = String(query || '').trim();
    if (!q) { setResults([]); return; }
    if (!(spConfig && spConfig.configured)) {
      const B = window.SyrixBridge;
      if (B && B.musicPlay) B.musicPlay(q);
      setSpErr('Spotify API not configured; sent query to existing music bridge.');
      return;
    }
    setSearching(true);
    const res = await spCall('search', { query: q });
    setSearching(false);
    if (res && res.ok) setResults(Array.isArray(res.items) ? res.items : []);
  }

  function fmtMs(ms) {
    const t = Math.floor(ms / 1000);
    return Math.floor(t / 60) + ':' + String(t % 60).padStart(2, '0');
  }

  const spTrack = sp && sp.item;
  const spPlaying = !!(sp && sp.is_playing);
  const spTrackName = spTrack ? spTrack.name : '';
  const spArtist = spTrack ? (spTrack.artists || []).map(a => a.name).join(', ') : '';
  const spAlbum = spTrack ? (spTrack.album || {}).name : '';
  const spArtUrl = spTrack ? ((spTrack.album || {}).images || []).map(i => i.url)[0] || '' : '';
  const spProgress = sp ? sp.progress_ms || 0 : 0;
  const spDuration = spTrack ? spTrack.duration_ms || 0 : 0;
  const spPct = spDuration ? Math.min(100, (spProgress / spDuration) * 100) : 0;
  const spDevice = sp && sp.device ? sp.device.name : '';

  const hasSpotify = !!(spConfig && spConfig.configured);
  const authRequired = !!(spConfig && !spConfig.configured && spConfig.reason === 'missing_auth');
  const noActiveDevice = !!(hasSpotify && !spTrack && (spReason === 'no_active_device' || spReason === '' || (sp && sp.status === 204) || !sp));
  const mediaUnavailable = !hasSpotify && !music.track && (!music.state || music.state === 'idle');
  const playing = hasSpotify ? spPlaying : music.state === 'playing';
  const paused  = hasSpotify ? (!spPlaying && !!spTrack) : music.state === 'paused';
  const trackName = authRequired ? 'AUTH_REQUIRED' : noActiveDevice ? 'NO_ACTIVE_DEVICE' : hasSpotify ? (spTrackName || '—') : (mediaUnavailable ? 'AUTH_REQUIRED' : (music.track || (music.provider ? music.provider.toUpperCase() : '—')));
  const artistLine = authRequired ? 'Spotify credentials are missing' : noActiveDevice ? 'Open Spotify, start playback once, then refresh devices' : hasSpotify ? (spArtist || spDevice || '') : (mediaUnavailable ? 'Spotify auth required or media bridge unavailable' : (music.provider ? `${music.provider.toUpperCase()} · ${(music.state || 'idle').toUpperCase()}` : 'NO PROVIDER · IDLE'));
  const progClass = playing ? 'pw-prog playing' : paused ? 'pw-prog paused' : 'pw-prog idle';

  function openSpotifyFallback() {
    const url = 'https://open.spotify.com';
    try {
      const B = window.SyrixBridge;
      if (B && B.openAppInHud) { B.openAppInHud(url, { source: 'user_click' }).catch(() => {}); return; }
      if (B && B.openInsideHud && B.openInsideHud(url)) return;
    } catch (_) {}
    try { window.open(url, '_blank', 'noopener,noreferrer'); } catch (_) {}
  }

  function doPlay() {
    const q = (query || '').trim();
    spPlay(q).catch(() => {});
    playWidgetSound('click');
  }
  function doPause()  { spPause().catch(() => {}); }
  function doNext()   { spNext().catch(() => {}); }
  function doPrev()   { spPrev().catch(() => {}); }

  async function doCloseSpotify() {
    try {
      const api = window.syrixHud && window.syrixHud.spotify;
      if (api && api.close) {
        const res = await api.close();
        setSp(null);
        if (res && !res.ok) setSpErr('Could not quit Spotify: ' + (res.reason || 'unknown'));
        else setSpErr('Spotify closed.');
        return;
      }
      const B = window.SyrixBridge;
      if (B && B.closeSpotify) {
        const res = await B.closeSpotify();
        setSp(null);
        if (res && !res.ok) setSpErr('Could not quit Spotify: ' + (res.reason || 'unknown'));
        else setSpErr('Spotify closed.');
      }
    } catch (e) {
      setSpErr(String(e && e.message ? e.message : e).slice(0, 120));
    }
  }

  const closeSpotifyBtn = (
    <button
      className="p-w-x"
      title="Quit Spotify background player"
      onClick={(e) => { e.stopPropagation(); doCloseSpotify(); }}
      style={{ fontSize: 11, letterSpacing: '0.04em', color: 'rgba(255,120,90,0.7)', padding: '2px 6px' }}
    >
      ⏹SP
    </button>
  );

  return (
    <Draggable id={id} title={hasSpotify ? 'Music · Spotify' : 'Music · Live'} x={60} y={100} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} headerExtra={closeSpotifyBtn} style={{ width: 320 }}>
      <div className="p-w-body">
        {hasSpotify && spArtUrl ? (
          <div className="pw-album-art">
            <img src={spArtUrl} alt={spAlbum} onError={e => { e.currentTarget.style.display='none'; }} />
          </div>
        ) : (
          <div className="pw-disc">
            <svg width="68" height="68" viewBox="0 0 68 68" fill="none">
              <circle cx="34" cy="34" r="32" stroke="rgba(var(--eg-accent-rgb),0.22)" strokeWidth="1"/>
              <circle cx="34" cy="34" r="22" stroke="rgba(var(--eg-accent-rgb),0.40)" strokeWidth="1.5"/>
              <circle cx="34" cy="34" r="8" fill={playing ? 'rgb(var(--eg-accent-rgb))' : 'rgba(var(--eg-accent-rgb),0.55)'}/>
              {[0,1,2,3,4,5,6,7].map(i => {
                const a = (i/8)*Math.PI*2;
                return <line key={i} x1={34+Math.cos(a)*24} y1={34+Math.sin(a)*24} x2={34+Math.cos(a)*28} y2={34+Math.sin(a)*28} stroke="rgba(var(--eg-accent-rgb),0.32)" strokeWidth="2"/>;
              })}
            </svg>
          </div>
        )}
        <div className="pw-music-name">{trackName.slice(0, 44)}</div>
        <div className="pw-music-artist">{artistLine.slice(0, 54)}</div>
        {hasSpotify && spDuration > 0 ? (
          <>
            <div className="pw-seek-wrap" onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              const frac = (e.clientX - rect.left) / rect.width;
              spSeek(frac * spDuration).catch(() => {});
            }}>
              <div className="pw-seek-fill" style={{ width: spPct + '%' }}></div>
              <div className="pw-seek-dot" style={{ left: spPct + '%' }}></div>
            </div>
            <div className="pw-time-row">
              <span>{fmtMs(spProgress)}</span>
              <span>{fmtMs(spDuration)}</span>
            </div>
          </>
        ) : (
          <div className={progClass} title={playing ? 'Playing' : paused ? 'Paused' : 'Idle'}>
            <div className="pw-prog-fill"></div>
          </div>
        )}
        <div className="pw-ctrls">
          <button className="pw-btn" onClick={doPrev} title="Previous track">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg>
          </button>
          <div className="pw-play" onClick={playing ? doPause : doPlay} title={playing ? 'Pause' : 'Play'}>
            {playing
              ? <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
              : <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>}
          </div>
          <button className="pw-btn" onClick={doNext} title="Next track">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6h2v12h-2z"/></svg>
          </button>
        </div>
        {hasSpotify && volume !== null && (
          <div className="pw-vol-row">
            <span className="pw-vol-icon">VOL</span>
            <input type="range" className="pw-vol-slider" min="0" max="100" value={volume}
              onChange={(e) => spVolume(Number(e.target.value)).catch(() => {})} />
            <span style={{ font: '500 8px JetBrains Mono', color: 'rgba(110,140,170,0.6)', minWidth: 26 }}>{Math.round(volume)}%</span>
          </div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 6, marginTop: 8 }}>
          <input type="text" value={query} onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') runSearch(); }}
            placeholder="Search songs, artists, playlists..."
            style={{ width:'100%', padding:'6px 8px', background:'rgba(4,10,22,0.6)', border:'1px solid rgba(var(--eg-accent-rgb),0.18)', borderRadius:6, color:'#e9f3ff', font:'500 11px Inter', outline:'none' }} />
          <button className="p-mini-btn" onClick={runSearch} disabled={searching} title={searching ? 'COOLDOWN: Spotify search already in progress' : undefined} data-disabled-reason={searching ? 'COOLDOWN: Spotify search already in progress' : undefined}>{searching ? '...' : 'SEARCH'}</button>
        </div>
        {results.length > 0 && (
          <div className="pw-search-results">
            {results.map((r) => (
              <div className="pw-search-item" key={r.uri || r.id || r.name}>
                {r.image ? <img className="pw-search-art" src={r.image} alt="" /> : <div className="pw-search-art" />}
                <div>
                  <div className="pw-search-title">{String(r.name || 'Untitled').slice(0, 42)}</div>
                  <div className="pw-search-sub">{String(r.artist || r.type || 'spotify').slice(0, 48)}</div>
                </div>
                <button className="p-mini-btn" onClick={() => spCall('play', { uri: r.uri }).then(() => playWidgetSound('click'))}>PLAY</button>
              </div>
            ))}
          </div>
        )}
        <div className="pw-meta-row" style={{ marginTop: 8 }}>
          {hasSpotify ? (
            <>
              <span className={"pw-chip " + (spErr ? 'error' : playing ? 'live' : 'stale')}>
                {spErr ? 'ERR' : playing ? 'LIVE' : 'PAUSED'}
              </span>
              <span className="pw-chip">SPOTIFY</span>
              {spDevice && <span className="pw-chip">{spDevice.slice(0, 16).toUpperCase()}</span>}
              <button className="p-mini-btn" style={{ marginLeft: 'auto' }} onClick={loadDevices} disabled={loadingDevices} title={loadingDevices ? 'COOLDOWN: device refresh already in progress' : undefined} data-disabled-reason={loadingDevices ? 'COOLDOWN: device refresh already in progress' : undefined}>
                {loadingDevices ? '…' : 'DEVICES'}
              </button>
            </>
          ) : (
            <>
              <span className="pw-chip stale">AUTH_REQUIRED</span>
              <span className="pw-chip muted">{String((spConfig && spConfig.reason) || music.provider || 'SPOTIFY APP').toUpperCase()}</span>
            </>
          )}
        </div>
        {noActiveDevice && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8 }}>
            <span className="pw-chip stale">NO_ACTIVE_DEVICE</span>
            <button className="p-mini-btn" onClick={openSpotifyFallback}>OPEN SPOTIFY</button>
            <button className="p-mini-btn" onClick={loadDevices} disabled={loadingDevices} title={loadingDevices ? 'COOLDOWN: device refresh already in progress' : undefined} data-disabled-reason={loadingDevices ? 'COOLDOWN: device refresh already in progress' : undefined}>{loadingDevices ? '…' : 'DEVICES'}</button>
          </div>
        )}
        {hasSpotify && showDevices && (
          <div style={{ marginTop: 8, padding: 8, background: 'rgba(4,10,22,0.55)', border: '1px solid rgba(var(--eg-accent-rgb),0.16)', borderRadius: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
              <div style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.22em', color: 'rgba(180,210,255,0.7)' }}>SPOTIFY DEVICES</div>
              <div onClick={() => setShowDevices(false)} style={{ cursor: 'pointer', font: '500 9px JetBrains Mono', color: 'rgba(var(--eg-accent-rgb),0.60)' }}>CLOSE</div>
            </div>
            {devices.length === 0 ? (
              <div style={{ font: '500 10px/1.4 Inter', color: 'rgba(var(--eg-accent-rgb),0.86)' }}>
                No Spotify devices reachable. Open Spotify on a phone or desktop, play one song so it registers, then click DEVICES again.
              </div>
            ) : (
              devices.map((d) => (
                <div key={d.id || d.name} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0', borderBottom: '1px dashed rgba(var(--eg-accent-rgb),0.06)' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ font: '500 11px Inter', color: '#e9f3ff', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {d.name || '(unnamed)'} {d.is_active ? '·' : ''}
                    </div>
                    <div style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.14em', color: 'rgba(180,210,255,0.55)' }}>
                      {(d.type || '').toUpperCase()}{d.is_active ? ' · ACTIVE' : ''}{d.is_restricted ? ' · RESTRICTED' : ''}
                    </div>
                  </div>
                  <button className="p-mini-btn" disabled={!d.id || d.is_restricted} title={!d.id ? 'UNAVAILABLE: Spotify did not provide a device id' : d.is_restricted ? 'UNAVAILABLE: Spotify marks this device as restricted' : undefined} data-disabled-reason={!d.id ? 'UNAVAILABLE: Spotify did not provide a device id' : d.is_restricted ? 'UNAVAILABLE: Spotify marks this device as restricted' : undefined} onClick={() => transferTo(d.id, true)}>USE</button>
                </div>
              ))
            )}
          </div>
        )}
        {spErr && <div style={{ font:'400 9px Inter', color:'rgba(255,100,100,0.75)', marginTop:6, wordBreak:'break-word' }}>{spErr}</div>}
      </div>
    </Draggable>
  );
}

/* Weather Widget — fetches live weather via SyrixBridge.weatherFetch (Open-Meteo).
 * Remembers the user's last chosen city in localStorage so the widget primes
 * with the right place after a HUD reload instead of always defaulting to
 * London. */
const WEATHER_LS_KEY = 'syrix.weather.city.v1';
function _loadWeatherCity() {
  try {
    if (typeof localStorage === 'undefined') return '';
    return String(localStorage.getItem(WEATHER_LS_KEY) || '');
  } catch (_) { return ''; }
}
function _saveWeatherCity(c) {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(WEATHER_LS_KEY, String(c || ''));
  } catch (_) {}
}
// Ventusky overlay layers (matches https://www.ventusky.com URL fragment).
// "wind"=current winds at surface, "rain"=precipitation, "temperature"=temp.
const VENTUSKY_LAYERS = [
  { id: 'wind',        label: 'WIND'  },
  { id: 'rain',        label: 'RAIN'  },
  { id: 'temperature', label: 'TEMP'  },
  { id: 'clouds',      label: 'CLOUD' },
];
function _ventuskyUrl(layer) {
  // p=lat;lon;zoom — modest zoom so the world is visible by default.
  const safe = VENTUSKY_LAYERS.find(l => l.id === layer) ? layer : 'wind';
  return `https://www.ventusky.com/?p=20;0;2&l=${safe}`;
}
function WeatherWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [city, setCity] = uWS(() => _loadWeatherCity() || 'London');
  const [weather, setWeather] = uWS(null);
  const [loading, setLoading] = uWS(false);
  const [localErr, setLocalErr] = uWS('');
  // Ventusky live map — collapsed by default so the widget stays compact,
  // expand on demand. Persisted in localStorage so user preference sticks.
  const [showMap, setShowMap] = uWS(() => {
    try { return localStorage.getItem('syrix.weather.map.v1') === '1'; }
    catch (_) { return false; }
  });
  const [mapLayer, setMapLayer] = uWS('wind');
  const [mapErr, setMapErr] = uWS('');
  uWE(() => {
    try { localStorage.setItem('syrix.weather.map.v1', showMap ? '1' : '0'); } catch (_) {}
  }, [showMap]);

  uWE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    const unsub = window.SyrixBridge.subscribe((s) => {
      if (s && s.weather) {
        setWeather(s.weather);
        setLocalErr('');
        setLoading(false);
      }
    });
    return unsub;
  }, []);

  // Direct Open-Meteo fetch — runs in parallel with the bridge so the widget
  // works even if the backend handler is unavailable, slow, or returns an
  // error for an unfamiliar city.  Picks the first geocoding hit and feeds
  // the same `weather` schema the bridge would produce, so downstream
  // parsing is shared.
  async function _directOpenMeteo(cityName) {
    const c = String(cityName || '').trim();
    if (!c) return null;
    try {
      const gURL = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(c)}&count=1&language=en&format=json`;
      const gR = await fetch(gURL, { cache: 'no-store' });
      if (!gR.ok) return null;
      const gJ = await gR.json();
      const hit = gJ && Array.isArray(gJ.results) && gJ.results[0];
      if (!hit) return null;
      const lat = hit.latitude, lon = hit.longitude;
      const label = [hit.name, hit.admin1, hit.country_code].filter(Boolean).join(', ');
      const fURL = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&timezone=auto`;
      const fR = await fetch(fURL, { cache: 'no-store' });
      if (!fR.ok) return null;
      const fJ = await fR.json();
      const cur = fJ && fJ.current; if (!cur) return null;
      const WMO = {
        0:'clear skies', 1:'mostly clear', 2:'partly cloudy', 3:'overcast',
        45:'fog', 48:'rime fog', 51:'light drizzle', 53:'drizzle', 55:'heavy drizzle',
        61:'light rain', 63:'rain', 65:'heavy rain', 71:'light snow', 73:'snow', 75:'heavy snow',
        80:'rain showers', 81:'heavy showers', 82:'violent showers',
        95:'thunderstorm', 96:'thunderstorm w/ hail', 99:'severe thunderstorm',
      };
      const cond = WMO[cur.weather_code] || 'clear';
      const temp = Number(cur.temperature_2m).toFixed(1);
      const wind = Number(cur.wind_speed_10m).toFixed(1);
      const hum  = Math.round(Number(cur.relative_humidity_2m));
      const summary = `In ${label}: ${cond}, ${temp}°C, wind ${wind} km/h, humidity ${hum}%.`;
      return {
        city: label,
        summary,
        status: 'DATA',
        source: 'open_meteo_direct',
        ts: Date.now() / 1000,
      };
    } catch (_) { return null; }
  }

  function fetchCity(isAutoRetry) {
    const expectedCity = String(city || '').trim();
    setLocalErr('');
    setLoading(true);
    // Kick off the bridge request (if available) AND the direct Open-Meteo
    // call in parallel — first one to return wins.
    try {
      if (window.SyrixBridge && window.SyrixBridge.weatherFetch) {
        window.SyrixBridge.weatherFetch(city);
      }
    } catch (_) {}
    _saveWeatherCity(city);
    let resolvedByDirect = false;
    _directOpenMeteo(expectedCity || city).then((w) => {
      if (!w) return;
      resolvedByDirect = true;
      setWeather((cur) => {
        // Don't overwrite a fresher bridge response if it already arrived.
        if (cur && cur.summary && Number(cur.ts) > (Date.now() / 1000 - 5)) return cur;
        return w;
      });
      setLoading(false);
      setLocalErr('');
    });
    setTimeout(() => {
      let didTimeout = false;
      setWeather((cur) => {
        const curCity = String((cur && cur.city) || '').trim().toLowerCase();
        const okCity = !expectedCity || curCity.includes(expectedCity.toLowerCase()) || expectedCity.toLowerCase().includes(curCity);
        if (cur && okCity && (cur.summary || cur.error || cur.status === 'DATA' || cur.status === 'ERROR')) return cur;
        if (resolvedByDirect) return cur;
        didTimeout = true;
        return {
          city: expectedCity || city,
          summary: '',
          error: 'Could not reach weather service.  Check connectivity.',
          status: 'ERROR',
          source: 'open_meteo',
          ts: Date.now() / 1000,
        };
      });
      if (didTimeout) {
        setLocalErr(`Weather request timed out for ${expectedCity || 'this location'}.`);
        // Auto-retry once when the initial mount-fetch fails (network may not be ready yet)
        if (!isAutoRetry) {
          setTimeout(() => fetchCity(true), 4000);
        }
      }
      setLoading(false);
    }, 8000);
  }

  uWE(() => {
    // Slightly longer initial delay so the widget is fully mounted before fetching
    const t = setTimeout(() => fetchCity(), 400);
    return () => clearTimeout(t);
  }, []);

  // Auto-refresh every 10 minutes so weather stays current without user interaction.
  // Restarts when city changes so the new city gets refreshed on the same schedule.
  uWE(() => {
    const t = setInterval(() => fetchCity(true), 10 * 60 * 1000);
    return () => clearInterval(t);
  }, [city]);

  // Parse the backend's compact "In London, UK: clear skies, 12.0°C, wind 5.0 mph, humidity 72%." line.
  let display = { temp: '', icon: '🌤', desc: 'Fetching live weather', humidity: '', wind: '', cityLabel: city };
  // Freshness chip — weather is considered fresh for 30 min.
  const wTs = weather && Number(weather.ts);
  const wFresh = weather ? freshness(wTs, 30 * 60) : 'muted';
  const wAgo = weather ? timeAgo(wTs) : '';
  const weatherStatus = String((weather && weather.status) || '').toUpperCase();
  if (localErr) {
    display.desc = localErr;
    display.icon = '⚠';
  } else if (weatherStatus === 'LOADING') {
    display.desc = 'Loading live weather';
    display.icon = '…';
    display.cityLabel = weather.city || city;
  } else if (weather && weather.summary) {
    const sum = String(weather.summary);
    const mTemp = sum.match(/([\-]?\d+\.?\d*)°C/);
    const mWind = sum.match(/wind\s+(\d+\.?\d*)\s*([a-z]+)/i);
    const mHum  = sum.match(/humidity\s+(\d+)%/i);
    const mCond = sum.match(/:\s*([^,]+),/);
    display.temp = mTemp ? Math.round(parseFloat(mTemp[1])).toString() : '';
    display.wind = mWind ? `${mWind[1]} ${mWind[2]}` : '';
    display.humidity = mHum ? `${mHum[1]}%` : '';
    display.desc = (mCond ? mCond[1] : 'live').replace(/^\s+|\s+$/g, '');
    display.cityLabel = weather.city || city;
    // Pick an icon from condition text.
    const t = display.desc.toLowerCase();
    if (t.includes('clear')) display.icon = '☀️';
    else if (t.includes('thunder')) display.icon = '⛈';
    else if (t.includes('snow')) display.icon = '❄️';
    else if (t.includes('rain') || t.includes('shower') || t.includes('drizzle')) display.icon = '🌧';
    else if (t.includes('fog')) display.icon = '🌫';
    else if (t.includes('cloud') || t.includes('overcast')) display.icon = '☁️';
    else display.icon = '🌤';
  } else if (weather && weather.error) {
    display.desc = String(weather.error).slice(0, 80);
    display.icon = '⚠';
    display.cityLabel = weather.city || city;
  }

  return (
    <Draggable id={id} title="Weather · Live" x={80} y={190} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: showMap ? 360 : 280 }}>
      <div className="p-w-body">
        <div className="pw-weather-top">
          <div style={{ fontSize: 48, lineHeight: 1 }}>{display.icon}</div>
          <div>
            <div className="pw-temp">{display.temp || '—'}<span className="pw-temp-unit">°C</span></div>
            <div className="pw-w-desc">{display.desc}</div>
            <div className="pw-w-city">{display.cityLabel}</div>
          </div>
        </div>
        <div className="pw-w-grid">
          <div className="pw-w-stat">
            <div className="pw-w-val">{display.humidity || '—'}</div>
            <div className="pw-w-lbl">Humidity</div>
          </div>
          <div className="pw-w-stat">
            <div className="pw-w-val">{display.wind || '—'}</div>
            <div className="pw-w-lbl">Wind</div>
          </div>
          <div className="pw-w-stat" title={weather && weather.ts ? `Updated ${wAgo}` : 'Not yet fetched'}>
            <div className="pw-w-val">{loading || weatherStatus === 'LOADING' ? '…' : (weather && weather.summary ? (wFresh === 'stale' ? 'STALE' : 'LIVE') : (weather && weather.error ? 'ERROR' : '—'))}</div>
            <div className="pw-w-lbl">{weather && weather.ts ? wAgo : 'Source'}</div>
          </div>
        </div>
        {/* Real provenance chips — provider name + last update. Backend uses
            syrix_host_live_web.quick_weather (Open-Meteo + WMO codes). */}
        <div className="pw-meta-row" style={{ marginTop: 8 }}>
          <span className={"pw-chip " + (weather ? wFresh : 'muted')}>
            {loading || weatherStatus === 'LOADING' ? 'LOADING' : weather ? (weather.error || weatherStatus === 'ERROR' ? 'ERROR' : (wFresh === 'stale' ? 'STALE' : 'DATA')) : 'NO DATA'}
          </span>
          {weather && wAgo && <span className="pw-chip muted">UPDATED · {wAgo}</span>}
          <span className="pw-chip muted">SRC · OPEN-METEO</span>
        </div>
        <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
          <input
            type="text"
            value={city}
            onChange={(e) => setCity(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') fetchCity(); }}
            placeholder="City…"
            style={{
              flex: 1, padding: '5px 8px',
              background: 'rgba(4,10,22,0.6)',
              border: '1px solid rgba(var(--eg-accent-rgb),0.18)',
              borderRadius: 6, color: '#e9f3ff', font: '500 11px Inter', outline: 'none',
            }} />
          <button className="p-mini-btn" onClick={fetchCity} disabled={loading} title={loading ? 'COOLDOWN: weather refresh already in progress' : undefined} data-disabled-reason={loading ? 'COOLDOWN: weather refresh already in progress' : undefined}>
            {loading ? 'FETCHING' : 'FETCH'}
          </button>
        </div>
        {/* Ventusky live wind/rain/temp/cloud map. Collapsed by default — the
            iframe is heavy, so we only render it when the user expands. If
            embedding is refused (X-Frame-Options), the "OPEN VENTUSKY" link
            launches it in the system browser via SyrixBridge. */}
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px solid rgba(var(--eg-accent-rgb),0.08)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
            <span style={{ font: '600 9px/1 "JetBrains Mono", monospace', letterSpacing: '0.22em', color: 'rgba(180,210,255,0.62)' }}>
              LIVE MAP · VENTUSKY
            </span>
            <button className="p-mini-btn" onClick={() => setShowMap(v => !v)}>
              {showMap ? 'HIDE' : 'SHOW'}
            </button>
          </div>
          {showMap && (
            <>
              <div style={{ display: 'flex', gap: 4, marginBottom: 6, flexWrap: 'wrap' }}>
                {VENTUSKY_LAYERS.map(l => (
                  <button
                    key={l.id}
                    className={'p-mini-btn' + (mapLayer === l.id ? ' primary' : '')}
                    onClick={() => setMapLayer(l.id)}
                    style={{ padding: '3px 8px', fontSize: 10 }}
                  >{l.label}</button>
                ))}
                <button
                  className="p-mini-btn"
                  style={{ padding: '3px 8px', fontSize: 10 }}
                  onClick={() => {
                    const B = window.SyrixBridge;
                    const u = _ventuskyUrl(mapLayer);
                    if (B && B.openAppInHud)       B.openAppInHud(u, { source: 'user_click' });
                    else if (B && B.openInsideHud) B.openInsideHud(u);
                    else if (typeof window !== 'undefined' && window.open) window.open(u, '_blank');
                  }}
                >OPEN</button>
              </div>
              <iframe
                key={mapLayer}
                src={_ventuskyUrl(mapLayer)}
                title="Ventusky live weather map"
                style={{
                  width: '100%', height: 220, border: '1px solid rgba(var(--eg-accent-rgb),0.18)',
                  borderRadius: 6, background: '#000',
                }}
                loading="lazy"
                referrerPolicy="no-referrer-when-downgrade"
                onError={() => setMapErr('Ventusky embed blocked; use OPEN.')}
              />
              {mapErr && (
                <div style={{ fontSize: 10, color: '#ff7a90', marginTop: 4 }}>{mapErr}</div>
              )}
            </>
          )}
        </div>
      </div>
    </Draggable>
  );
}

/* Map Widget — Leaflet + CARTO dark raster tiles.
 * Leaflet is loaded locally (node_modules) before widget scripts so L is
 * always available on mount. CARTO dark_all tiles use {s}.basemaps.cartocdn.com
 * subdomains which are already covered by the CSP img-src wildcard. */
function MapWidget({ id, onClose, focused, onFocus, onBlur }) {
  const mapContainerRef = uWR(null);
  const mapRef          = uWR(null);
  const markerRef       = uWR(null);
  const [query,     setQuery]     = uWS('');
  const [from,      setFrom]      = uWS('');
  const [to,        setTo]        = uWS('');
  const [mapErr,    setMapErr]    = uWS('');
  const [searching, setSearching] = uWS(false);

  uWE(() => {
    const L = window.L;
    if (!L || !mapContainerRef.current || mapRef.current) return;
    const map = L.map(mapContainerRef.current, {
      center: [28.6139, 77.2090],
      zoom: 12,
      zoomControl: true,
      attributionControl: false,
      zoomSnap: 0.25,
      wheelPxPerZoomLevel: 80,
      maxBounds: [[-85, -180], [85, 180]],
      maxBoundsViscosity: 1.0,
    });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png', {
      subdomains: 'abcd',
      maxZoom: 19,
      minZoom: 2,
      noWrap: true,
    }).addTo(map);
    mapRef.current = map;
    return () => {
      try { map.remove(); } catch (_) {}
      mapRef.current = null;
      markerRef.current = null;
    };
  }, []);

  uWE(() => {
    const timer = setTimeout(() => {
      try { mapRef.current && mapRef.current.invalidateSize(); } catch (_) {}
    }, 380);
    return () => clearTimeout(timer);
  }, [focused]);

  async function geocodeAndPan() {
    const q = String(query || '').trim();
    if (!q || !mapRef.current) return;
    setSearching(true);
    setMapErr('');
    try {
      const r = await fetch(
        'https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(q),
        { headers: { 'Accept': 'application/json' } }
      );
      if (!r.ok) throw new Error('status_' + r.status);
      const arr = await r.json();
      if (!Array.isArray(arr) || !arr.length) {
        setMapErr('No result for "' + q + '"');
        setSearching(false);
        return;
      }
      const { lat, lon, display_name } = arr[0];
      const L = window.L;
      mapRef.current.flyTo([Number(lat), Number(lon)], 15, { duration: 1.4 });
      if (markerRef.current) { try { markerRef.current.remove(); } catch (_) {} }
      const icon = L.divIcon({
        className: '',
        html: '<div style="width:14px;height:14px;border-radius:50%;background:rgb(var(--eg-accent-surface-rgb));border:2px solid #fff8e0;box-shadow:0 0 14px rgba(var(--eg-accent-rgb),0.9);"></div>',
        iconSize: [14, 14],
        iconAnchor: [7, 7],
        popupAnchor: [0, -10],
      });
      markerRef.current = L.marker([Number(lat), Number(lon)], { icon })
        .addTo(mapRef.current)
        .bindPopup('<span style="font:500 11px Inter;color:#e9f3ff">' + String(display_name || q).slice(0, 90) + '</span>')
        .openPopup();
      setMapErr('');
    } catch (_) {
      setMapErr('Search failed — check connectivity.');
    }
    setSearching(false);
  }

  function openStreetView() {
    const map = mapRef.current;
    if (!map) { setMapErr('Map not loaded.'); return; }
    const c = map.getCenter();
    const url = 'https://www.google.com/maps/@?api=1&map_action=pano&viewpoint='
      + c.lat.toFixed(6) + ',' + c.lng.toFixed(6);
    const B = window.SyrixBridge;
    if (B && B.openAppInHud) B.openAppInHud(url, { source: 'user_click' });
    else if (B && B.openMap) B.openMap(url);
    else setMapErr('Bridge unavailable.');
  }

  function openOnGoogle() {
    const B = window.SyrixBridge;
    if (!B || !B.openMap) { setMapErr('Google Maps bridge unavailable.'); return; }
    setMapErr('');
    B.openMap(query.trim() || '');
  }

  function openDirections() {
    const B = window.SyrixBridge;
    if (!B || !B.openMapDirections) { setMapErr('Directions bridge unavailable.'); return; }
    if (!to) return;
    setMapErr('');
    B.openMapDirections(from, to);
  }

  const mapHeight = focused ? 'calc(100vh - 240px)' : '300px';
  const inputStyle = { padding: '5px 8px', background: 'rgba(2,6,16,0.7)', border: '1px solid rgba(var(--eg-accent-rgb),0.18)', borderRadius: 6, color: '#e9f3ff', font: '500 11px Inter', outline: 'none' };

  return (
    <Draggable id={id} title="Map · Live" x={100} y={80} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: 460 }}>
      <div ref={mapContainerRef} className="pw-map-canvas"
           style={{ height: mapHeight, transition: 'height 0.38s cubic-bezier(0.23,1,0.32,1)' }} />
      {mapErr && <div className="pw-map-fallback">{mapErr}</div>}
      <div style={{ padding: '8px 10px', borderTop: '1px solid rgba(var(--eg-accent-rgb),0.08)', display: 'grid', gridTemplateColumns: '1fr auto', gap: 6 }}>
        <input type="text" value={query} onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') geocodeAndPan(); }}
          placeholder="Search a place…" style={inputStyle} />
        <button className="p-mini-btn" onClick={geocodeAndPan}
          disabled={searching || !query.trim()}
          title={!query.trim() ? 'UNAVAILABLE: enter a search query first' : undefined}>
          {searching ? '…' : 'GO'}
        </button>
        <input type="text" value={from} onChange={(e) => setFrom(e.target.value)}
          placeholder="From (optional)" style={{ ...inputStyle, borderColor: 'rgba(var(--eg-accent-rgb),0.12)' }} />
        <button className="p-mini-btn" onClick={openStreetView}>🚶 VIEW</button>
        <input type="text" value={to} onChange={(e) => setTo(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') openDirections(); }}
          placeholder="To (for directions)" style={inputStyle} />
        <button className="p-mini-btn" onClick={openDirections}
          disabled={!to} title={!to ? 'UNAVAILABLE: enter a destination first' : undefined}>
          DIRECTIONS
        </button>
      </div>
    </Draggable>
  );
}

/* Timer Widget — countdown timers backed by SyrixBridge.setTimer.
 *
 * Shows all live HUD timers from snap.timers and lets you add new ones via
 * a duration picker. Stopwatch mode kept as a secondary local-only feature
 * since the backend's reminders system is countdown-oriented. */
function TimerWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [timers, setTimers] = uWS([]);
  const [mode, setMode] = uWS('countdown'); // 'countdown' | 'stopwatch'
  const [customMin, setCustomMin] = uWS(15); // custom-duration input (minutes)
  const [label, setLabel] = uWS('');
  const [now, setNow] = uWS(Date.now() / 1000);
  const [timerErr, setTimerErr] = uWS('');

  uWE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((s) => {
      if (Array.isArray(s.timers)) setTimers(s.timers);
    });
  }, []);

  // Local ticker for smooth countdown rendering between backend pushes.
  uWE(() => {
    const i = setInterval(() => setNow(Date.now() / 1000), 500);
    return () => clearInterval(i);
  }, []);

  // Stopwatch — backed by the global SyrixStopwatch store (island-hub) so a
  // running stopwatch survives widget close and migrates to the Dynamic
  // Island, exactly like countdown timers do.
  const [swSnap, setSwSnap] = uWS(() => (
    window.SyrixStopwatch ? window.SyrixStopwatch.get() : { running: false, elapsedMs: 0 }
  ));
  uWE(() => {
    if (!window.SyrixStopwatch) return;
    return window.SyrixStopwatch.subscribe(setSwSnap);
  }, []);
  uWE(() => {
    if (mode !== 'stopwatch' || !swSnap.running || !window.SyrixStopwatch) return;
    const id = setInterval(() => setSwSnap(window.SyrixStopwatch.get()), 250);
    return () => clearInterval(id);
  }, [swSnap.running, mode]);
  const elapsedMs = swSnap.elapsedMs;
  const run = swSnap.running;

  function fmt(secs) {
    const s = Math.max(0, Math.floor(secs));
    const h = String(Math.floor(s / 3600)).padStart(2, '0');
    const m = String(Math.floor((s % 3600) / 60)).padStart(2, '0');
    const ss = String(s % 60).padStart(2, '0');
    return `${h}:${m}:${ss}`;
  }

  // Start a timer in ONE click. Presets call this directly (no select-then-start
  // two-step), and the custom input + START use the same path.
  function startTimer(minutes) {
    const B = window.SyrixBridge;
    if (!B || !B.setTimer) { setTimerErr('Timer bridge unavailable.'); return; }
    const mins = Math.max(0.0167, Number(minutes) || 0); // >= 1 second
    setTimerErr('');
    const seconds = Math.max(1, Math.round(mins * 60));
    B.setTimer(seconds, label || (mins >= 1 ? `${Math.round(mins)}m` : `${seconds}s`));
    setLabel('');
    playWidgetSound('timerStart');
  }
  function cancelAll() {
    const B = window.SyrixBridge;
    if (!B || !B.cancelTimers) { setTimerErr('Timer cancel bridge unavailable.'); return; }
    setTimerErr('');
    B.cancelTimers();
    playWidgetSound('timerStop');
  }

  function stopwatchToggle() {
    if (!window.SyrixStopwatch) return;
    window.SyrixStopwatch.toggle();
    playWidgetSound(run ? 'timerStop' : 'timerStart');
  }
  function stopwatchReset() {
    if (!window.SyrixStopwatch) return;
    window.SyrixStopwatch.reset();
    playWidgetSound('timerReset');
  }

  // Active timer to show big — first non-paused with shortest remaining.
  const live = (timers || [])
    .map((t) => ({ ...t, remaining: Math.max(0, (t.due_ts || 0) - now) }))
    .sort((a, b) => a.remaining - b.remaining);
  const headline = live[0];

  return (
    <Draggable id={id} title="Timer · Live" x={310} y={320} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: 280 }}>
      <div className="p-w-body">
        <div style={{ display: 'flex', gap: 6, justifyContent: 'center', marginBottom: 8 }}>
          <button className={'p-mini-btn' + (mode === 'countdown' ? ' primary' : '')} onClick={() => setMode('countdown')}>COUNTDOWN</button>
          <button className={'p-mini-btn' + (mode === 'stopwatch' ? ' primary' : '')} onClick={() => setMode('stopwatch')}>STOPWATCH</button>
        </div>
        {mode === 'countdown' ? (
          <>
            <div className="pw-timer-disp eg-timer-disp">{headline ? fmt(headline.remaining) : fmt(customMin * 60)}</div>
            {headline && (
              <div className="eg-timer-headline">
                {headline.label} · {live.length > 1 ? `+${live.length - 1} more` : 'live'}
              </div>
            )}
            {/* One-click presets — clicking starts that timer immediately. */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 4, marginBottom: 8 }}>
              {[1, 5, 10, 30].map((m) => (
                <button key={m} className="p-mini-btn" onClick={() => startTimer(m)}>{m}m</button>
              ))}
            </div>
            {/* Custom duration — type minutes, press START or Enter. */}
            <div className="eg-timer-custom">
              <input
                type="number" min="1" max="600" step="1"
                className="eg-timer-input"
                value={customMin}
                onChange={(e) => setCustomMin(Math.max(1, Math.min(600, Number(e.target.value) || 0)))}
                onKeyDown={(e) => { if (e.key === 'Enter') startTimer(customMin); }}
                title="Custom minutes" />
              <span className="eg-timer-unit">min</span>
              <button className="p-mini-btn primary" onClick={() => startTimer(customMin)}>START</button>
            </div>
            <input
              type="text"
              className="eg-timer-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') startTimer(customMin); }}
              placeholder="Label (optional)" />
            <div className="pw-timer-ctrls">
              <button className="p-mini-btn danger" onClick={cancelAll} style={{ width: '100%' }}>CANCEL ALL</button>
            </div>
            {timerErr && <div style={{ marginTop: 6, font: '500 10px/1.35 Inter', color: 'rgb(var(--eg-accent-rgb))' }}>{timerErr}</div>}
          </>
        ) : (
          <>
            <div className="pw-timer-disp">{fmt(elapsedMs / 1000)}</div>
            <div className="pw-timer-ctrls">
              <button className="p-mini-btn" onClick={stopwatchToggle}>{run ? 'PAUSE' : 'START'}</button>
              <button className="p-mini-btn" onClick={stopwatchReset}>RESET</button>
            </div>
          </>
        )}
      </div>
    </Draggable>
  );
}

/* Memory Web Widget — live nodes/edges from snap.memory_web. */
function MemoryWebWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [mw, setMw] = uWS({ nodes: 0, edges: 0, recent: [], status: '' });
  const [review, setReview] = uWS(null);
  const [obs, setObs] = uWS(null);
  const [recentMem, setRecentMem] = uWS([]);
  const [query, setQuery] = uWS('');
  const [mwErr, setMwErr] = uWS('');
  // 2026-07-24 — the user's REAL saved memory (memory.json → conversation_notes),
  // the notes "what do you remember" lists back. This is the live, editable
  // store — separate from the Obsidian vault sections below.
  const [facts, setFacts] = uWS([]);
  const [factAdd, setFactAdd] = uWS('');
  const [factEditing, setFactEditing] = uWS(null); // original text being edited
  const [factEditVal, setFactEditVal] = uWS('');
  // The memory graph / Obsidian-vault sections show old chat nodes & auto-saved
  // notes — noise, not the user's curated saved memory. Hidden by default; the
  // SAVED MEMORY section above is the real, editable store.
  const [showDiag, setShowDiag] = uWS(false);
  const [showObs, setShowObs] = uWS(() => {
    try { return localStorage.getItem('syrix_memory_show_obs') === '1'; } catch (_) { return false; }
  });
  const [showRecent, setShowRecent] = uWS(() => {
    try { return localStorage.getItem('syrix_memory_show_recent') === '1'; } catch (_) { return true; }
  });

  uWE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((s) => {
      if (s && s.memory_web) {
        setMw(s.memory_web);
        if (s.memory_web.review) setReview(s.memory_web.review);
      }
      if (s && s.memory_review) setReview(s.memory_review);
      if (s && s.obsidian_status) {
        setObs(s.obsidian_status);
        // Use latest list from obsidian_status if recent_memories hasn't arrived yet.
        if (Array.isArray(s.obsidian_status.latest) && s.obsidian_status.latest.length) {
          setRecentMem(s.obsidian_status.latest);
        }
      }
      if (s && s.recent_memories && Array.isArray(s.recent_memories.recent)) {
        setRecentMem(s.recent_memories.recent);
      }
      if (s && s.memory_facts && Array.isArray(s.memory_facts.facts)) {
        setFacts(s.memory_facts.facts);
      }
    });
  }, []);

  // Full refresh on mount — pull memory web, Obsidian vault, and recent memories
  // in one pass so the widget isn't blank on first open.
  uWE(() => {
    const B = window.SyrixBridge;
    if (!B) return;
    try { if (B.memoryWebRefresh) B.memoryWebRefresh(); } catch (_) {}
    try { if (B.obsidianRefresh) B.obsidianRefresh(); } catch (_) {}
    try { if (B.getRecentMemories) B.getRecentMemories(); } catch (_) {}
    try { if (B.memoryFactsList) B.memoryFactsList(); } catch (_) {}
    // Keep all feeds current without needing the user to click ↻.
    const t = setInterval(() => {
      try { if (B.memoryWebRefresh) B.memoryWebRefresh(); } catch (_) {}
      try { if (B.obsidianRefresh) B.obsidianRefresh(); } catch (_) {}
      try { if (B.getRecentMemories) B.getRecentMemories(); } catch (_) {}
      try { if (B.memoryFactsList) B.memoryFactsList(); } catch (_) {}
    }, 2 * 60 * 1000);
    return () => clearInterval(t);
  }, []);

  // 2026-05-30 — Display-only dedupe of Recent Memories: collapse repeats by
  // lowercase title+kind so the user sees a clean list.  Raw memory data
  // (bridge state, vault notes) is never mutated.  Count of repeats is kept
  // on the surviving entry so the UI can label "· repeated Nx" when useful.
  const recentMemDeduped = (function () {
    const out = [];
    const seen = new Map();
    for (const m of (recentMem || [])) {
      if (!m || typeof m !== 'object') continue;
      const title = String(m.title || m.filename || '').trim().toLowerCase();
      const kind = String(m.kind || 'memory').trim().toLowerCase();
      if (!title) continue;
      const key = kind + '|' + title;
      if (seen.has(key)) {
        const ix = seen.get(key);
        out[ix]._repeats = (out[ix]._repeats || 1) + 1;
        continue;
      }
      seen.set(key, out.length);
      out.push(Object.assign({}, m));
    }
    return out;
  })();

  uWE(() => {
    try { localStorage.setItem('syrix_memory_show_obs', showObs ? '1' : '0'); } catch (_) {}
  }, [showObs]);

  uWE(() => {
    try { localStorage.setItem('syrix_memory_show_recent', showRecent ? '1' : '0'); } catch (_) {}
  }, [showRecent]);

  function refresh() {
    const B = window.SyrixBridge;
    if (!B || !B.memoryWebRefresh) { setMwErr('Memory web bridge unavailable.'); return; }
    setMwErr('');
    B.memoryWebRefresh();
    if (B.obsidianRefresh) { try { B.obsidianRefresh(); } catch (_) {} }
    if (B.getRecentMemories) { try { B.getRecentMemories(); } catch (_) {} }
    playWidgetSound('click');
  }
  function search() {
    const q = (query || '').trim();
    if (!q) return;
    const B = window.SyrixBridge;
    if (!B || !B.memoryWebSearch) { setMwErr('Memory search bridge unavailable.'); return; }
    setMwErr('');
    B.memoryWebSearch(q);
    playWidgetSound('click');
  }
  function rememberThis() {
    const t = (query || '').trim();
    if (!t) return;
    const B = window.SyrixBridge;
    if (!B || !B.obsidianRemember) { setMwErr('Obsidian bridge unavailable.'); return; }
    setMwErr('');
    B.obsidianRemember(t);
    setQuery('');
    if (B.obsidianRefresh) setTimeout(() => { try { B.obsidianRefresh(); } catch (_) {} }, 600);
    playWidgetSound('click');
  }
  // ── Real saved-memory CRUD (memory.json). Optimistic UI + re-fetch to
  //    reconcile with the authoritative backend list. ──
  function factReconcile(B) { setTimeout(() => { try { if (B.memoryFactsList) B.memoryFactsList(); } catch (_) {} }, 450); }
  function factSubmitAdd() {
    const t = (factAdd || '').trim();
    if (!t) return;
    const B = window.SyrixBridge;
    if (!B || !B.memoryFactAdd) { setMwErr('Memory bridge unavailable.'); return; }
    setMwErr('');
    setFacts(prev => ((prev || []).some(f => String(f.text || '').toLowerCase() === t.toLowerCase())
      ? prev : [...(prev || []), { text: t, timestamp: '' }]));
    B.memoryFactAdd(t);
    setFactAdd('');
    factReconcile(B);
    playWidgetSound('click');
  }
  function factDelete(text) {
    const t = String(text || '').trim();
    if (!t) return;
    const B = window.SyrixBridge;
    if (!B || !B.memoryFactDelete) { setMwErr('Memory bridge unavailable.'); return; }
    setMwErr('');
    setFacts(prev => (prev || []).filter(f => String(f.text || '').toLowerCase() !== t.toLowerCase()));
    B.memoryFactDelete(t);
    factReconcile(B);
    playWidgetSound('click');
  }
  function factStartEdit(text) { setFactEditing(text); setFactEditVal(text); }
  function factCancelEdit() { setFactEditing(null); setFactEditVal(''); }
  function factCommitEdit() {
    const oldT = String(factEditing || '').trim();
    const newT = (factEditVal || '').trim();
    if (!oldT || !newT) { factCancelEdit(); return; }
    const B = window.SyrixBridge;
    if (!B || !B.memoryFactEdit) { setMwErr('Memory bridge unavailable.'); return; }
    setMwErr('');
    if (oldT.toLowerCase() !== newT.toLowerCase()) {
      setFacts(prev => (prev || []).map(f => (String(f.text || '').toLowerCase() === oldT.toLowerCase()
        ? Object.assign({}, f, { text: newT }) : f)));
      B.memoryFactEdit(oldT, newT);
      factReconcile(B);
    }
    factCancelEdit();
    playWidgetSound('click');
  }
  function factClearAll() {
    const B = window.SyrixBridge;
    if (!B || !B.memoryClearAll) { setMwErr('Memory bridge unavailable.'); return; }
    if (!window.confirm('Clear ALL saved memory? SYRIX will forget everything it remembers about you. This cannot be undone.')) return;
    setMwErr('');
    setFacts([]);
    setFactEditing(null);
    B.memoryClearAll();
    factReconcile(B);
    playWidgetSound('click');
  }

  const mwTs = Number(mw && mw.ts);
  const rv = review || (mw && mw.review) || {};
  const rvPending = Number(rv.pending_count || 0);
  const rvApproved = Number(rv.approved_count || 0);
  const rvDenied = Number(rv.denied_count || 0);
  const rvJunk = Number(rv.rejected_junk_count || 0);
  const rvRejected = Number(rv.rejected_count || (rvDenied + rvJunk) || 0);
  const rvLast = (rv && rv.last_saved) || {};
  const rvErrors = Array.isArray(rv.save_errors) ? rv.save_errors : [];
  const rvPendingItems = Array.isArray(rv.pending) ? rv.pending.slice(0, 2) : [];
  const rvApprovedItems = Array.isArray(rv.approved) ? rv.approved.slice(0, 2) : [];
  const rvRejectedItems = Array.isArray(rv.rejected) ? rv.rejected.slice(0, 2) : [];
  const mwStatus = String((mw && mw.status) || '').toLowerCase();
  const mwFresh = mwTs ? freshness(mwTs, 5 * 60) : (mwStatus === 'live' ? 'live' : 'muted');
  const usefulRecent = (mw && Array.isArray(mw.recent) ? mw.recent : []).filter((n) => {
    const type = String((n && n.type) || '').toLowerCase();
    const text = String((n && (n.text || n.label || n.title || n.summary)) || '').trim();
    if (!text) return false;
    if (type.includes('weekly_summary')) return false;
    if (/weekly_summary|placeholder|sample memory|demo node/i.test(text)) return false;
    return true;
  });
  const mwHasData = !!(mw && (usefulRecent.length || (mw.nodes && mwStatus !== 'placeholder') || (mw.edges && mwStatus !== 'placeholder')));
  function memoryAction(name, item, text) {
    const B = window.SyrixBridge;
    const itemId = item && item.id;
    if (!B || !itemId) { setMwErr('Memory review bridge unavailable.'); return; }
    setMwErr('');
    if (name === 'approve' && B.memoryReviewApprove) B.memoryReviewApprove(itemId);
    else if (name === 'deny' && B.memoryReviewDeny) B.memoryReviewDeny(itemId);
    else if (name === 'delete' && B.memoryReviewDelete) B.memoryReviewDelete(itemId);
    else if (name === 'edit' && B.memoryReviewEdit) B.memoryReviewEdit(itemId, text);
    else setMwErr('Memory review action unavailable.');
    setTimeout(refresh, 500);
    playWidgetSound('click');
  }
  function editMemory(item) {
    const current = String((item && (item.source_text || item.summary)) || '').trim();
    const next = window.prompt ? window.prompt('Edit memory before approval', current) : '';
    if (next && next.trim()) memoryAction('edit', item, next.trim());
  }
  function cleanupJunk() {
    const B = window.SyrixBridge;
    if (!B || !B.memoryCleanupJunk) { setMwErr('Memory cleanup bridge unavailable.'); return; }
    setMwErr('');
    B.memoryCleanupJunk();
    setTimeout(refresh, 700);
    playWidgetSound('click');
  }
  return (
    <Draggable id={id} title="Memory · Web" x={140} y={250} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: 320 }}>
      <div className="p-w-body">
        {/* ── SAVED MEMORY (2026-07-24) — the user's REAL memory (memory.json →
             conversation_notes), the exact notes "what do you remember" lists.
             Fully editable: add / edit / delete, each persisted and kept in
             sync with recall. This is the live store — the Obsidian/graph
             sections below are secondary. ── */}
        <div style={{ marginBottom: 10, paddingBottom: 8, borderBottom: '1px dashed rgba(var(--eg-accent-rgb),0.16)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <div style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.22em', color: 'rgba(var(--eg-accent-rgb),0.9)' }}>
              SAVED MEMORY {facts.length > 0 ? `· ${facts.length}` : ''}
            </div>
            {facts.length > 0 && (
              <button className="p-mini-btn danger" onClick={factClearAll} title="Forget everything SYRIX remembers about you">CLEAR ALL</button>
            )}
          </div>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            <input
              type="text"
              value={factAdd}
              onChange={(e) => setFactAdd(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') factSubmitAdd(); }}
              placeholder="Remember something…"
              style={{ flex: 1, padding: '5px 8px', background: 'rgba(4,10,22,0.6)', border: '1px solid rgba(var(--eg-accent-rgb),0.18)', borderRadius: 6, color: '#e9f3ff', font: '500 11px Inter', outline: 'none' }} />
            <button className="p-mini-btn primary" onClick={factSubmitAdd} disabled={!factAdd.trim()} title={factAdd.trim() ? 'Save to SYRIX memory' : 'UNAVAILABLE: type something to remember first'} data-disabled-reason={!factAdd.trim() ? 'UNAVAILABLE: type something to remember first' : undefined}>ADD</button>
          </div>
          <div style={{ maxHeight: 176, overflow: 'auto' }}>
            {facts.length === 0 ? (
              <div style={{ font: '500 11px Inter', color: 'rgba(180,210,255,0.55)' }}>Nothing saved yet. Add one above, or tell SYRIX "remember …".</div>
            ) : (
              facts.slice().reverse().map((f, idx) => {
                const text = String(f.text || '');
                const isEditing = factEditing !== null && String(factEditing) === text;
                return (
                  <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 0', borderBottom: '1px dashed rgba(var(--eg-accent-rgb),0.06)' }}>
                    {isEditing ? (
                      <input
                        type="text"
                        value={factEditVal}
                        autoFocus
                        onChange={(e) => setFactEditVal(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') factCommitEdit(); else if (e.key === 'Escape') factCancelEdit(); }}
                        style={{ flex: 1, padding: '4px 7px', background: 'rgba(4,10,22,0.7)', border: '1px solid rgba(var(--eg-accent-rgb),0.3)', borderRadius: 6, color: '#e9f3ff', font: '500 11px Inter', outline: 'none' }} />
                    ) : (
                      <div style={{ flex: 1, font: '500 11px/1.4 Inter', color: 'rgba(230,240,255,0.9)' }}>{text}</div>
                    )}
                    {isEditing ? [
                      <button key="ok" className="p-mini-btn primary" onClick={factCommitEdit} title="Save">✓</button>,
                      <button key="no" className="p-mini-btn" onClick={factCancelEdit} title="Cancel">✕</button>,
                    ] : [
                      <button key="ed" className="p-mini-btn" onClick={() => factStartEdit(text)} title="Edit">✎</button>,
                      <button key="del" className="p-mini-btn danger" onClick={() => factDelete(text)} title="Delete">🗑</button>,
                    ]}
                  </div>
                );
              })
            )}
          </div>
        </div>
        {mwErr && <div style={{ margin: '2px 0 8px', font: '500 10px/1.35 Inter', color: 'rgba(var(--eg-accent-rgb),0.86)' }}>{mwErr}</div>}
        <div onClick={() => setShowDiag(v => !v)} title="Memory graph + Obsidian vault diagnostics — old chat nodes, not your saved facts"
          style={{ marginTop: 4, cursor: 'pointer', userSelect: 'none', font: '600 8px JetBrains Mono', letterSpacing: '0.18em', color: 'rgba(150,180,220,0.42)' }}>
          {showDiag ? '▾' : '▸'} MEMORY GRAPH · VAULT (diagnostic)
        </div>
        {showDiag && (<div style={{ marginTop: 8, opacity: 0.85 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
          <div style={{ font: '700 22px JetBrains Mono', color: mwHasData ? 'rgb(var(--eg-accent-rgb))' : 'rgba(180,210,255,0.55)' }}>{mwHasData ? (mw.nodes || 0) : '—'}</div>
          <div style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.22em', color: 'rgba(180,210,255,0.6)' }}>NODES</div>
          <div style={{ font: '700 22px JetBrains Mono', color: mwHasData ? 'rgb(var(--eg-accent-rgb))' : 'rgba(180,210,255,0.55)' }}>{mwHasData ? (mw.edges || 0) : '—'}</div>
          <div style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.22em', color: 'rgba(180,210,255,0.6)' }}>EDGES</div>
        </div>
        <div className="pw-meta-row" style={{ justifyContent: 'flex-start', marginTop: 0, marginBottom: 8 }}>
          <span className={"pw-chip " + (mwHasData ? mwFresh : 'muted')}>
            {mwHasData ? (mwFresh === 'stale' ? 'STALE' : (mwStatus || 'LIVE').toUpperCase()) : 'NO DATA'}
          </span>
          {mwTs ? <span className="pw-chip muted">UPDATED · {timeAgo(mwTs)}</span> : null}
        </div>
        <div style={{ marginBottom: 8, padding: '6px 0', borderTop: '1px dashed rgba(var(--eg-accent-rgb),0.08)', borderBottom: '1px dashed rgba(var(--eg-accent-rgb),0.08)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 4 }}>
            <div className="pw-chip muted" title="Memories waiting for approval">PENDING · {rvPending}</div>
            <div className="pw-chip live" title="Approved memories and lessons">APPROVED · {rvApproved}</div>
            <div className="pw-chip muted" title="Denied and rejected memory items">DENIED/JUNK · {rvDenied}/{rvJunk}</div>
          </div>
          {rvLast && rvLast.summary ? (
            <div style={{ marginTop: 6, font: '500 10px/1.35 Inter', color: 'rgba(220,235,255,0.72)' }}>
              LAST SAVED · {String(rvLast.category || 'memory').replace(/_/g, ' ').toUpperCase()} · {String(rvLast.summary || '').slice(0, 110)}
            </div>
          ) : null}
          {[['APPROVED', rvApprovedItems], ['REJECTED', rvRejectedItems]].map(([label, items]) => (
            items && items.length ? (
              <div key={label} style={{ marginTop: 5, font: '500 10px/1.35 Inter', color: label === 'PENDING' ? 'rgba(var(--eg-accent-rgb),0.86)' : 'rgba(180,210,255,0.68)' }}>
                {label} · {items.map((x) => String((x && x.summary) || '').slice(0, 58)).filter(Boolean).join(' | ')}
              </div>
            ) : null
          ))}
          {rvPendingItems.length ? (
            <div style={{ marginTop: 7, display: 'grid', gap: 6 }}>
              {rvPendingItems.map((x) => (
                <div key={x.id} style={{ padding: 7, border: '1px solid rgba(var(--eg-accent-rgb),0.18)', borderRadius: 8, background: 'rgba(var(--eg-accent-rgb),0.045)' }}>
                  <div style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.16em', color: 'rgba(var(--eg-accent-rgb),0.9)' }}>
                    PENDING · {String(x.category || 'memory').replace(/_/g, ' ').toUpperCase()} · {Math.round(Number(x.confidence || 0) * 100)}%
                  </div>
                  <div title={String(x.source_text || '')} style={{ marginTop: 3, font: '500 10px/1.35 Inter', color: 'rgba(220,235,255,0.78)' }}>
                    {String(x.summary || '').slice(0, 130)}
                  </div>
                  {x.reason ? <div style={{ marginTop: 2, font: '500 9px/1.25 Inter', color: 'rgba(180,210,255,0.58)' }}>{String(x.reason).slice(0, 120)}</div> : null}
                  <div style={{ display: 'flex', gap: 5, marginTop: 6, flexWrap: 'wrap' }}>
                    <button className="p-mini-btn primary" onClick={() => memoryAction('approve', x)}>APPROVE</button>
                    <button className="p-mini-btn" onClick={() => editMemory(x)}>EDIT</button>
                    <button className="p-mini-btn" onClick={() => memoryAction('deny', x)}>DENY</button>
                    <button className="p-mini-btn danger" onClick={() => memoryAction('delete', x)}>DELETE</button>
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          {rvErrors.length ? (
            <div style={{ marginTop: 4, font: '500 10px/1.35 Inter', color: 'rgba(var(--eg-accent-rgb),0.9)' }}>
              SAVE ERROR · {String((rvErrors[0] && rvErrors[0].reason) || 'unknown').slice(0, 120)}
            </div>
          ) : null}
        </div>
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') search(); }}
            placeholder="Search memory…"
            style={{ flex: 1, padding: '5px 8px', background: 'rgba(4,10,22,0.6)', border: '1px solid rgba(var(--eg-accent-rgb),0.18)', borderRadius: 6, color: '#e9f3ff', font: '500 11px Inter', outline: 'none' }} />
          <button className="p-mini-btn" onClick={search} disabled={!query.trim()} title={!query.trim() ? 'UNAVAILABLE: enter a memory search first' : undefined} data-disabled-reason={!query.trim() ? 'UNAVAILABLE: enter a memory search first' : undefined}>SEARCH</button>
          <button className="p-mini-btn" onClick={rememberThis} disabled={!query.trim()} title={query.trim() ? 'Save text as an Obsidian memory note' : 'UNAVAILABLE: enter text to save first'} data-disabled-reason={!query.trim() ? 'UNAVAILABLE: enter text to save first' : undefined}>SAVE</button>
          <button className="p-mini-btn" onClick={cleanupJunk}>CLEAN</button>
          <button className="p-mini-btn" onClick={refresh}>↻</button>
        </div>
        {mwErr && <div style={{ marginBottom: 8, font: '500 10px/1.35 Inter', color: 'rgba(var(--eg-accent-rgb),0.86)' }}>{mwErr}</div>}
        <div style={{ maxHeight: 180, overflow: 'auto', borderTop: '1px dashed rgba(var(--eg-accent-rgb),0.08)', paddingTop: 6 }}>
          {usefulRecent.length === 0 ? (
            <div style={{ font: '500 11px Inter', color: 'rgba(180,210,255,0.55)' }}>
              {mw.status === 'live' ? 'No useful recent entries.' : (mw.status || 'Loading…')}
            </div>
          ) : (
            usefulRecent.slice(0, 8).map((n, idx) => (
              <div key={idx} style={{ padding: '4px 0', borderBottom: '1px dashed rgba(var(--eg-accent-rgb),0.06)', font: '500 11px/1.4 Inter', color: 'rgba(220,235,255,0.78)' }}>
                <div style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.18em', color: 'rgba(var(--eg-accent-rgb),0.55)' }}>{(n.type || '').toUpperCase() || 'NODE'}</div>
                {(n.text || '').slice(0, 200)}
              </div>
            ))
          )}
        </div>
        {/* Recent Memories — from Obsidian vault latest notes */}
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px dashed rgba(var(--eg-accent-rgb),0.12)' }}>
          <div
            onClick={() => setShowRecent(v => !v)}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', userSelect: 'none', marginBottom: showRecent ? 6 : 0 }}>
            <div style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.22em', color: 'rgba(180,210,255,0.7)' }}>
              RECENT MEMORIES {recentMemDeduped.length > 0 ? `· ${recentMemDeduped.length}` : ''}
            </div>
            <div style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.18em', color: 'rgba(var(--eg-accent-rgb),0.60)' }}>
              {showRecent ? 'HIDE' : 'SHOW'}
            </div>
          </div>
          {showRecent && (
            <div style={{ maxHeight: 160, overflow: 'auto' }}>
              {recentMemDeduped.length === 0 ? (
                <div style={{ font: '500 10px Inter', color: 'rgba(180,210,255,0.55)' }}>
                  No saved memories yet. Conversations save automatically.
                </div>
              ) : (
                recentMemDeduped.slice(0, 8).map((m, idx) => (
                  <div key={idx} style={{ padding: '4px 0', borderBottom: '1px dashed rgba(var(--eg-accent-rgb),0.06)' }}>
                    <div style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.14em', color: 'rgba(var(--eg-accent-rgb),0.55)' }}>
                      {(m.kind || 'memory').toUpperCase()}
                      {m.ts ? ` · ${timeAgo(m.ts)}` : ''}
                      {m._repeats ? ` · repeated ${m._repeats}x` : ''}
                    </div>
                    <div style={{ font: '500 11px/1.35 Inter', color: 'rgba(220,235,255,0.78)', marginTop: 1 }}>
                      {String(m.title || m.filename || '').slice(0, 60)}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px dashed rgba(var(--eg-accent-rgb),0.12)' }}>
          <div
            onClick={() => setShowObs(v => !v)}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer', userSelect: 'none' }}>
            <div style={{ font: '600 9px JetBrains Mono', letterSpacing: '0.22em', color: 'rgba(180,210,255,0.7)' }}>
              OBSIDIAN VAULT
            </div>
            <div style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.18em', color: 'rgba(var(--eg-accent-rgb),0.60)' }}>
              {showObs ? 'HIDE' : 'SHOW'}
            </div>
          </div>
          {showObs && (
            <div style={{ marginTop: 8 }}>
              {!obs ? (
                <div style={{ font: '500 10px Inter', color: 'rgba(180,210,255,0.55)' }}>Querying vault…</div>
              ) : !obs.enabled ? (
                <div style={{ font: '500 10px/1.45 Inter', color: 'rgba(var(--eg-accent-rgb),0.86)' }}>
                  Disabled · {obs.last_error || obs.reason || 'set obsidian_memory_enabled + obsidian_vault_path in config.json'}
                </div>
              ) : (
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
                    <div style={{ font: '700 18px JetBrains Mono', color: 'rgb(var(--eg-accent-rgb))' }}>{obs.total || 0}</div>
                    <div style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.22em', color: 'rgba(180,210,255,0.6)' }}>NOTES</div>
                  </div>
                  {obs.vault_path && (
                    <div title={obs.vault_path} style={{ font: '500 10px Inter', color: 'rgba(180,210,255,0.55)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginBottom: 6 }}>
                      {obs.vault_path}
                    </div>
                  )}
                  {obs.by_kind && Object.keys(obs.by_kind).length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
                      {Object.entries(obs.by_kind).map(([k, v]) => (
                        <span key={k} className="pw-chip" style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.14em' }}>
                          {k.toUpperCase()} · {v}
                        </span>
                      ))}
                    </div>
                  )}
                  {obs.last_write_path && (
                    <div style={{ font: '500 10px/1.4 Inter', color: 'rgba(220,235,255,0.66)' }}>
                      <div style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.18em', color: 'rgba(var(--eg-accent-rgb),0.55)', marginBottom: 2 }}>LAST WRITE</div>
                      <div style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{obs.last_write_path}</div>
                      {obs.last_write_ts ? <div style={{ font: '500 9px JetBrains Mono', color: 'rgba(180,210,255,0.5)' }}>{timeAgo(obs.last_write_ts)}</div> : null}
                    </div>
                  )}
                  {obs.last_error && (
                    <div style={{ marginTop: 6, font: '500 10px/1.35 Inter', color: 'rgba(var(--eg-accent-rgb),0.86)' }}>
                      {obs.last_error}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
        </div>)}
      </div>
    </Draggable>
  );
}

/* Diagnostics Widget — live frontend health + optional backend deep scan.
   Section 1: connection + hardware from bridge state (no button needed).
   Section 2: SYRIX activity (last query, timers, music).
   Section 3: backend probe results after "Deep Scan". */
const _DIAG_NAMES = {
  syrix_wiring:   'HUD <-> Backend link',
  live_web:       'Live web search',
  free_web:       'Free web search',
  searxng:        'SearXNG',
  brave_search:   'Brave Search API',
  screen_vision:  'Screen vision',
  vision_adapter: 'Image understanding',
  gpu_cuda:       'GPU / CUDA',
  blender:        'Blender 3D',
};
function _diagPretty(name) {
  return String(name || '').replace(/[_\-.]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}
function _diagStatus(status) {
  const s = String(status || 'unknown').toLowerCase();
  if (s === 'healthy' || s === 'ok' || s === 'restarted') return { tone: 'ok', word: 'Working' };
  if (s === 'missing' || s === 'timed_out' || s === 'overheated') return { tone: 'bad', word: 'Not working' };
  if (s === 'disabled') return { tone: 'warn', word: 'Off (optional)' };
  if (s === 'degraded' || s === 'stale') return { tone: 'warn', word: 'Partial' };
  return { tone: 'warn', word: 'Unknown' };
}

function DiagnosticsWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [snap,    setSnap]    = uWS(null);
  const [diag,    setDiag]    = uWS(null);
  const [busy,    setBusy]    = uWS(false);
  const [err,     setErr]     = uWS('');
  const [lastAt,  setLastAt]  = uWS(0);
  const [now,     setNow]     = uWS(Date.now());
  const seenTsRef = uWR(0);

  // Tick every 2s so "X ago" and stale detection stay fresh
  uWE(() => {
    const t = setInterval(() => setNow(Date.now()), 2000);
    return () => clearInterval(t);
  }, []);

  uWE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((s) => {
      if (!s) return;
      setSnap(s);
      setLastAt(Date.now());
      if (s.last_diagnostic) {
        setDiag(s.last_diagnostic);
        const ts = Number(s.last_diagnostic.ts) || 0;
        if (ts > seenTsRef.current) { seenTsRef.current = ts; setBusy(false); }
      }
    });
  }, []);

  function runDeepScan() {
    const B = window.SyrixBridge;
    if (!B || !B.runDiagnostic) { setErr('Backend not connected — deep scan unavailable.'); return; }
    setErr(''); setBusy(true);
    B.runDiagnostic();
    setTimeout(() => setBusy(false), 14000);
  }

  // ── frontend-detectable facts ──────────────────────────────────────────
  const bridgeExists  = !!(window.SyrixBridge && window.SyrixBridge.subscribe);
  const backendAge    = lastAt ? (now - lastAt) : Infinity;
  const backendLive   = backendAge < 6000;
  const backendStale  = backendAge >= 6000 && backendAge < 30000;

  const rg  = (snap && snap.resource_governor) || {};
  const cpu = typeof rg.cpu_percent    === 'number' ? Math.round(rg.cpu_percent)    : null;
  const ram = typeof rg.memory_percent === 'number' ? Math.round(rg.memory_percent) : null;
  const gpu = (rg.gpu_metrics_status === 'live' && typeof rg.gpu_util_percent === 'number')
              ? Math.round(rg.gpu_util_percent) : null;
  const gpuTemp = typeof rg.gpu_temp_c === 'number' ? Math.round(rg.gpu_temp_c) : null;

  const musicState  = snap && snap.music  ? String(snap.music.state || 'idle')  : null;
  const musicTrack  = snap && snap.music  ? String(snap.music.track || '')       : '';
  const liveTimers  = Array.isArray(snap && snap.timers)
    ? snap.timers.filter(t => t && t.due_ts > now / 1000) : [];
  const lastQuery   = (snap && snap.last_query)    ? String(snap.last_query).slice(0, 72)    : '';
  const ttsSpeaking = !!(snap && snap.tts_speaking);
  const asrActive   = !!(snap && snap.asr_active);

  // ── backend probe rows ─────────────────────────────────────────────────
  const health = (diag && diag.health) || {};
  const probes = Array.isArray(health.probes) ? health.probes : [];
  let probeRows = [];
  if (probes.length) {
    probeRows = probes.map(p => {
      const info = _diagStatus(p.status);
      return { key: p.name, name: _DIAG_NAMES[p.name] || _diagPretty(p.name), tone: info.tone, word: info.word, detail: p.detail ? String(p.detail).slice(0, 80) : '' };
    });
  } else if (diag) {
    const providers = diag.providers || {};
    probeRows = Object.entries(providers)
      .filter(([k]) => !String(k).startsWith('sub.'))
      .map(([k, v]) => {
        let tone = 'warn', word = 'Unknown';
        if (typeof v === 'boolean') { tone = v ? 'ok' : 'bad'; word = v ? 'Working' : 'Off'; }
        else { const d = String(v); tone = /unavailable|missing|fail|error/i.test(d) ? 'bad' : /\bok\b|ready|live|loaded/i.test(d) ? 'ok' : 'warn'; word = tone === 'ok' ? 'Working' : tone === 'bad' ? 'Not working' : 'Partial'; }
        return { key: k, name: _diagPretty(String(k).replace(/^phase2\./, '')), tone, word, detail: '' };
      });
  }
  const diagTs = Number(diag && diag.ts);
  const overall = String(health.overall || '').toLowerCase();

  // ── render helpers ─────────────────────────────────────────────────────
  const DOT_OK   = '#16d37a';
  const DOT_WARN = 'rgb(var(--eg-accent-rgb))';
  const DOT_BAD  = '#ff4f6e';
  function dotColor(tone) { return tone === 'ok' ? DOT_OK : tone === 'bad' ? DOT_BAD : DOT_WARN; }

  function Row({ label, value, tone, sub }) {
    const c = dotColor(tone);
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px', borderRadius: 7, background: 'rgba(2,6,16,0.52)', marginBottom: 4 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: c, flexShrink: 0, boxShadow: `0 0 5px ${c}` }} />
        <span style={{ flex: 1, minWidth: 0 }}>
          <div style={{ font: '500 11px Inter', color: 'rgba(220,232,250,0.88)' }}>{label}</div>
          {sub && <div style={{ font: '500 9px JetBrains Mono', color: 'rgba(180,210,255,0.50)', marginTop: 1 }}>{sub}</div>}
        </span>
        <span style={{ font: '500 10px JetBrains Mono', letterSpacing: '0.04em', color: c, flexShrink: 0 }}>{value}</span>
      </div>
    );
  }

  function SectionHead({ label }) {
    return <div style={{ font: '600 8.5px JetBrains Mono', letterSpacing: '0.26em', color: 'rgba(180,210,255,0.5)', margin: '10px 0 5px', textTransform: 'uppercase' }}>{label}</div>;
  }

  return (
    <Draggable id={id} title="Diagnostics · Live" x={180} y={110} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: 370 }}>
      <div className="p-w-body" style={{ maxHeight: focused ? undefined : 480, overflowY: 'auto' }}>

        {/* ── 1. Connection ───────────────────────────────────────────── */}
        <SectionHead label="Connection" />
        <Row label="HUD Bridge"
          value={bridgeExists ? 'CONNECTED' : 'NOT FOUND'}
          tone={bridgeExists ? 'ok' : 'bad'} />
        <Row label="Backend (Python)"
          value={backendLive ? 'LIVE' : backendStale ? 'STALE' : (lastAt ? 'LOST' : 'OFFLINE')}
          tone={backendLive ? 'ok' : backendStale ? 'warn' : 'bad'}
          sub={lastAt ? 'last data ' + timeAgo(lastAt / 1000) : 'no data received yet'} />

        {/* ── 2. Hardware ─────────────────────────────────────────────── */}
        {(cpu !== null || ram !== null || gpu !== null) && <>
          <SectionHead label="Hardware" />
          {cpu !== null && <Row label="CPU" value={cpu + '%'} tone={cpu < 80 ? 'ok' : cpu < 93 ? 'warn' : 'bad'} />}
          {ram !== null && <Row label="RAM" value={ram + '%'} tone={ram < 80 ? 'ok' : ram < 92 ? 'warn' : 'bad'} />}
          {gpu !== null && <Row label="GPU" value={gpu + '%'} tone={gpu < 85 ? 'ok' : gpu < 95 ? 'warn' : 'bad'} sub={gpuTemp !== null ? gpuTemp + '°C' : undefined} />}
        </>}

        {/* ── 3. SYRIX Activity ───────────────────────────────────────── */}
        {snap && <>
          <SectionHead label="SYRIX Activity" />
          <Row label="Voice — listening" value={asrActive ? 'ACTIVE' : 'IDLE'} tone={asrActive ? 'ok' : 'warn'} />
          <Row label="Voice — speaking"  value={ttsSpeaking ? 'SPEAKING' : 'QUIET'} tone={ttsSpeaking ? 'ok' : 'warn'} />
          {liveTimers.length > 0 && (
            <Row label="Active timers" value={liveTimers.length + ' running'} tone="ok"
              sub={liveTimers.slice(0,2).map(t => (t.label || '') + ' ' + Math.max(0, Math.ceil(t.due_ts - now/1000)) + 's').join(' · ')} />
          )}
          {musicState && musicState !== 'idle' && (
            <Row label="Music" value={musicState.toUpperCase()} tone={musicState === 'playing' ? 'ok' : 'warn'} sub={musicTrack.slice(0, 40) || undefined} />
          )}
          {lastQuery && (
            <div style={{ padding: '6px 8px', borderRadius: 7, background: 'rgba(2,6,16,0.52)', marginBottom: 4 }}>
              <div style={{ font: '500 8.5px JetBrains Mono', letterSpacing: '0.12em', color: 'rgba(180,210,255,0.5)', marginBottom: 3 }}>LAST QUERY</div>
              <div style={{ font: '500 11px/1.38 Inter', color: 'rgba(220,232,250,0.80)' }}>{lastQuery}</div>
            </div>
          )}
        </>}

        {/* ── 4. Deep scan button ─────────────────────────────────────── */}
        <div style={{ marginTop: 12 }}>
          <button onClick={runDeepScan} disabled={busy} style={{
            width: '100%', padding: '8px 12px',
            background: busy ? 'rgba(var(--eg-accent-surface-rgb),0.06)' : 'rgba(var(--eg-accent-surface-rgb),0.10)',
            border: '1px solid rgba(var(--eg-accent-rgb),0.28)', borderRadius: 8,
            color: 'rgb(var(--eg-accent-rgb))', font: '600 10px JetBrains Mono',
            letterSpacing: '0.18em', cursor: busy ? 'wait' : 'pointer',
            transition: 'background 0.15s',
          }}>
            {busy ? 'SCANNING BACKEND…' : 'RUN DEEP BACKEND SCAN'}
          </button>
          {err && <div style={{ marginTop: 6, font: '500 10px/1.35 Inter', color: 'rgb(var(--eg-accent-rgb))' }}>{err}</div>}
        </div>

        {/* ── 5. Backend probe results ─────────────────────────────────── */}
        {probeRows.length > 0 && <>
          <SectionHead label={'Backend Systems · ' + (diagTs ? timeAgo(diagTs) : '—')} />
          {overall && (
            <Row
              label="Overall health"
              value={overall === 'healthy' ? 'ALL GOOD' : overall === 'degraded' ? 'DEGRADED' : overall.toUpperCase()}
              tone={overall === 'healthy' ? 'ok' : overall === 'degraded' ? 'warn' : 'bad'}
            />
          )}
          {probeRows.map((r, i) => (
            <Row key={r.key || i} label={r.name} value={r.word} tone={r.tone} sub={r.detail || undefined} />
          ))}
        </>}
        {diag && !probeRows.length && (
          <div style={{ marginTop: 8, font: '500 10px Inter', color: 'rgba(180,210,255,0.45)' }}>
            No probe detail in this scan result.
          </div>
        )}

      </div>
    </Draggable>
  );
}

/* API Status Widget — shows connected/missing status for all optional APIs. */
const API_LABELS = {
  openweather:  'OpenWeather',
  alpha_vantage:'Alpha Vantage',
  nasa:         'NASA',
  openrouter:   'OpenRouter',
  unsplash:     'Unsplash',
  huggingface:  'HuggingFace',
  open_meteo:   'Open-Meteo',
  jokeapi:      'JokeAPI',
};
function ApiStatusWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [apis, setApis]   = uWS(null);
  const [ts,   setTs]     = uWS(0);
  const [busy, setBusy]   = uWS(false);
  const [err,  setErr]    = uWS('');

  uWE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((s) => {
      if (s && s.syrix_api_status) {
        setApis(s.syrix_api_status.apis || null);
        setTs(Number(s.syrix_api_status.ts) || 0);
      }
    });
  }, []);

  function refresh() {
    const B = window.SyrixBridge;
    if (!B || !B.getApiStatus) { setErr('Bridge unavailable'); return; }
    setErr('');
    setBusy(true);
    B.getApiStatus();
    setTimeout(() => setBusy(false), 4000);
    playWidgetSound('click');
  }

  uWE(() => { refresh(); }, []);

  const connected = apis ? Object.values(apis).filter(Boolean).length : 0;
  const total     = apis ? Object.values(apis).length : 0;

  return (
    <Draggable id={id} title="API Status" x={220} y={140} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: 300 }}>
      <div className="p-w-body">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <span style={{ font: '600 11px Inter', color: 'rgba(180,210,255,0.7)', letterSpacing: '.04em' }}>
            {apis ? `${connected} / ${total} CONNECTED` : (busy ? 'LOADING…' : 'PRESS REFRESH')}
          </span>
          <button className="p-mini-btn" onClick={refresh} disabled={busy} title={busy ? 'COOLDOWN: API status refresh already running' : undefined} data-disabled-reason={busy ? 'COOLDOWN: API status refresh already running' : undefined} style={{ padding: '2px 10px', fontSize: 10 }}>
            {busy ? '…' : 'REFRESH'}
          </button>
        </div>
        {apis ? (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px 8px' }}>
            {Object.entries(apis).map(([key, ok]) => (
              <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 6, font: '500 10.5px Inter', color: ok ? 'rgba(100,220,140,0.9)' : 'rgba(255,100,100,0.75)' }}>
                <span style={{ fontSize: 13, lineHeight: 1 }}>{ok ? '●' : '○'}</span>
                {API_LABELS[key] || key}
              </div>
            ))}
          </div>
        ) : (
          <div style={{ font: '500 11px Inter', color: 'rgba(180,210,255,0.45)', textAlign: 'center', padding: 12 }}>
            {err || (busy ? 'Fetching status…' : 'Press REFRESH to probe API keys.')}
          </div>
        )}
        {ts > 0 && (
          <div style={{ marginTop: 10, font: '500 9.5px JetBrains Mono', color: 'rgba(180,210,255,0.35)', textAlign: 'right' }}>
            updated {timeAgo(ts)}
          </div>
        )}
      </div>
    </Draggable>
  );
}

/* Image Generation Widget — prompt + provider, shows last generated. */
function ImageGenWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [prompt, setPrompt] = uWS('');
  const [last, setLast] = uWS(null);
  const [err, setErr] = uWS('');

  uWE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((s) => {
      if (s && s.last_image) setLast(s.last_image);
    });
  }, []);

  function go() {
    const p = (prompt || '').trim();
    if (!p) return;
    const B = window.SyrixBridge;
    if (!B || !B.imageGenerate) { setErr('Image generation bridge unavailable.'); return; }
    setErr('');
    B.imageGenerate(p);
    playWidgetSound('click');
  }

  const url = last && last.path ? String(last.path) : '';
  const isHttp = /^https?:\/\//.test(url);
  const imgTs = Number(last && last.ts);

  function copyPath() {
    if (!url || !navigator.clipboard) return;
    navigator.clipboard.writeText(url).catch(() => {});
    playWidgetSound('click');
  }

  return (
    <Draggable id={id} title="Image · Gen" x={210} y={200} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: 320 }}>
      <div className="p-w-body">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe the image you want…"
          rows={3}
          style={{ width: '100%', padding: '6px 8px', background: 'rgba(4,10,22,0.6)', border: '1px solid rgba(var(--eg-accent-rgb),0.18)', borderRadius: 6, color: '#e9f3ff', font: '500 11px Inter', outline: 'none', resize: 'vertical' }} />
        <button className="p-mini-btn primary" onClick={go} disabled={!prompt.trim()} title={!prompt.trim() ? 'UNAVAILABLE: enter an image prompt first' : undefined} data-disabled-reason={!prompt.trim() ? 'UNAVAILABLE: enter an image prompt first' : undefined} style={{ width: '100%', marginTop: 8 }}>
          GENERATE
        </button>
        {err && <div style={{ marginTop: 8, font: '500 10px/1.35 Inter', color: 'rgba(var(--eg-accent-rgb),0.86)' }}>{err}</div>}
        {last ? (
          <div style={{ marginTop: 10 }}>
            <div className="pw-meta-row" style={{ justifyContent: 'flex-start', margin: '0 0 6px' }}>
              <span className="pw-chip live">PROVIDER · {(last.provider || 'UNKNOWN').toUpperCase()}</span>
              {imgTs ? <span className="pw-chip muted">{timeAgo(imgTs)}</span> : null}
              <span className="pw-chip muted">{isHttp ? 'REMOTE' : url ? 'LOCAL' : 'NO PATH'}</span>
            </div>
            <div style={{ font: '500 11px Inter', color: 'rgba(220,235,255,0.78)', wordBreak: 'break-all', marginTop: 4 }}>
              {(last.prompt || '(no prompt)').slice(0, 200)}
            </div>
            {url && (
              <div style={{ font: '500 10px/1.4 JetBrains Mono', color: 'rgba(180,210,255,0.55)', wordBreak: 'break-all', marginTop: 6 }}>
                {url.slice(0, 200)}
              </div>
            )}
            {isHttp ? (
              <a href={url} target="_blank" rel="noreferrer" style={{ color: 'rgb(var(--eg-accent-rgb))', font: '500 11px JetBrains Mono', display: 'inline-block', marginTop: 6 }}>
                Open ↗
              </a>
            ) : url ? (
              <button className="p-mini-btn" onClick={copyPath} style={{ marginTop: 6 }} title="Copy local file path to clipboard">
                COPY PATH
              </button>
            ) : null}
          </div>
        ) : (
          <div style={{ font: '500 11px Inter', color: 'rgba(180,210,255,0.55)', marginTop: 10 }}>
            No image generated yet.
          </div>
        )}
      </div>
    </Draggable>
  );
}

function DrawingWidget({ id, onClose, focused, onFocus, onBlur }) {
  const canvasRef = uWR(null);
  const wrapRef = uWR(null);
  const drawingRef = uWR(false);
  const lastRef = uWR(null);
  const undoRef = uWR([]);
  const redoRef = uWR([]);
  const [color, setColor] = uWS('#101827');
  const [size, setSize] = uWS(6);
  const [eraser, setEraser] = uWS(false);
  const [opacity, setOpacity] = uWS(1);

  function pushHistory() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    try {
      undoRef.current.push(canvas.toDataURL('image/png'));
      if (undoRef.current.length > 24) undoRef.current.shift();
      redoRef.current = [];
    } catch (_) {}
  }

  function restoreDataUrl(dataUrl) {
    const canvas = canvasRef.current;
    const ctx = canvas && canvas.getContext('2d');
    if (!canvas || !ctx || !dataUrl) return;
    const img = new Image();
    img.onload = () => {
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = 'source-over';
      ctx.fillStyle = '#f8fbff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    };
    img.src = dataUrl;
  }

  function fitCanvas() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const old = document.createElement('canvas');
    old.width = canvas.width || 1;
    old.height = canvas.height || 1;
    const oldCtx = old.getContext('2d');
    if (oldCtx) oldCtx.drawImage(canvas, 0, 0);
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.fillStyle = '#f8fbff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (old.width > 1 && old.height > 1) ctx.drawImage(old, 0, 0, canvas.width, canvas.height);
  }

  uWE(() => {
    fitCanvas();
    const onResize = () => fitCanvas();
    window.addEventListener('resize', onResize);
    let ro = null;
    try {
      ro = new ResizeObserver(onResize);
      if (canvasRef.current) ro.observe(canvasRef.current);
      if (wrapRef.current) ro.observe(wrapRef.current);
    } catch (_) {}
    return () => {
      window.removeEventListener('resize', onResize);
      try { ro && ro.disconnect(); } catch (_) {}
    };
  }, [focused]);

  function point(e) {
    const canvas = canvasRef.current;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    return { x: (e.clientX - rect.left) * dpr, y: (e.clientY - rect.top) * dpr };
  }
  function drawTo(p) {
    const canvas = canvasRef.current;
    const ctx = canvas && canvas.getContext('2d');
    const last = lastRef.current;
    if (!ctx || !last) return;
    const dpr = window.devicePixelRatio || 1;
    ctx.globalAlpha = eraser ? 1 : Math.max(0.05, Math.min(1, Number(opacity) || 1));
    ctx.globalCompositeOperation = eraser ? 'destination-out' : 'source-over';
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = color;
    ctx.lineWidth = Math.max(1, Number(size) || 1) * dpr;
    ctx.beginPath();
    ctx.moveTo(last.x, last.y);
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
    lastRef.current = p;
  }
  function start(e) {
    pushHistory();
    drawingRef.current = true;
    lastRef.current = point(e);
    e.preventDefault();
  }
  function move(e) {
    if (!drawingRef.current) return;
    drawTo(point(e));
    e.preventDefault();
  }
  function end() {
    drawingRef.current = false;
    lastRef.current = null;
  }
  function clear() {
    const canvas = canvasRef.current;
    const ctx = canvas && canvas.getContext('2d');
    if (!ctx) return;
    pushHistory();
    ctx.fillStyle = '#f8fbff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    playWidgetSound('click');
  }
  function undo() {
    const canvas = canvasRef.current;
    if (!canvas || !undoRef.current.length) return;
    try { redoRef.current.push(canvas.toDataURL('image/png')); } catch (_) {}
    restoreDataUrl(undoRef.current.pop());
    playWidgetSound('click');
  }
  function redo() {
    const canvas = canvasRef.current;
    if (!canvas || !redoRef.current.length) return;
    try { undoRef.current.push(canvas.toDataURL('image/png')); } catch (_) {}
    restoreDataUrl(redoRef.current.pop());
    playWidgetSound('click');
  }
  function save() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const a = document.createElement('a');
    a.href = canvas.toDataURL('image/png');
    a.download = 'syrix-drawing-' + new Date().toISOString().replace(/[:.]/g, '-') + '.png';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { try { a.remove(); } catch (_) {} }, 0);
    playWidgetSound('click');
  }

  return (
    <Draggable id={id} title="Drawing · Paint" x={130} y={120} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: 520 }}>
      <div className="pw-drawing-wrap" ref={wrapRef}>
        <div className="pw-drawing-tools">
          <input className="pw-brush-color" type="color" value={color} onChange={(e) => setColor(e.target.value)} title="Brush color" />
          <input className="pw-brush-size" type="range" min="1" max="32" value={size} onChange={(e) => setSize(Number(e.target.value))} title="Brush size" />
          <input className="pw-brush-size" type="range" min="0.1" max="1" step="0.05" value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} title="Opacity" />
          <button className={"p-mini-btn" + (eraser ? ' primary' : '')} onClick={() => setEraser(v => !v)}>{eraser ? 'ERASER' : 'BRUSH'}</button>
          {['#101827', 'rgb(var(--eg-accent-rgb))', '#16d37a', 'rgb(var(--eg-accent-rgb))', '#ff4f6e'].map((c) => (
            <button key={c} className="p-mini-btn" title={c} style={{ minWidth: 22, width: 22, padding: 0, background: c }} onClick={() => { setColor(c); setEraser(false); }} />
          ))}
          <button className="p-mini-btn" onClick={undo}>UNDO</button>
          <button className="p-mini-btn" onClick={redo}>REDO</button>
          <button className="p-mini-btn" onClick={clear}>CLEAR</button>
          <button className="p-mini-btn" onClick={save}>SAVE</button>
          <span className="pw-drawing-hint">Mouse or hand pinch draws</span>
        </div>
        <canvas
          ref={canvasRef}
          className="pw-drawing-canvas"
          data-hand-draw="1"
          onMouseDown={start}
          onMouseMove={move}
          onMouseUp={end}
          onMouseLeave={end}
        />
      </div>
    </Draggable>
  );
}

/* Notes Widget — persists via localStorage so notes survive HUD reload
 * and host restart. localStorage in Electron lives under userData; values
 * never leave the box. We hydrate once on mount, then save (debounced) on
 * every keystroke. */
const NOTES_LS_KEY = 'syrix.notes.v1';
function _loadNotesFromLS() {
  try {
    if (typeof localStorage === 'undefined') return '';
    const v = localStorage.getItem(NOTES_LS_KEY);
    return typeof v === 'string' ? v : '';
  } catch (_) { return ''; }
}
function _saveNotesToLS(v) {
  try {
    if (typeof localStorage === 'undefined') return false;
    localStorage.setItem(NOTES_LS_KEY, String(v || ''));
    return true;
  } catch (_) { return false; }
}
function NotesWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [txt, setTxt] = uWS(() => _loadNotesFromLS());
  const [saved, setSaved] = uWS('idle'); // 'idle' | 'saving' | 'saved' | 'error'
  const saveTimer = uWR(null);

  // Debounced save so we don't write to localStorage on every keypress.
  uWE(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    setSaved('saving');
    saveTimer.current = setTimeout(() => {
      const ok = _saveNotesToLS(txt);
      setSaved(ok ? 'saved' : 'error');
    }, 360);
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
  }, [txt]);

  const savedLabel = saved === 'saving' ? 'Saving…' : saved === 'saved' ? 'Saved' : saved === 'error' ? 'Save failed' : '';

  function clearAll() {
    setTxt('');
    _saveNotesToLS('');
  }

  return (
    <Draggable id={id} title="Notes" x={210} y={160} onClose={onClose} focused={focused} onFocus={onFocus} style={{ width: 284 }} onBlur={onBlur}>
      <div className="p-w-body">
        <textarea className="pw-notes-ta" value={txt} onChange={e => setTxt(e.target.value)} placeholder="Write anything..."></textarea>
        <div className="pw-notes-btns" style={{ alignItems: 'center' }}>
          <button className="p-mini-btn" onClick={() => navigator.clipboard && navigator.clipboard.writeText(txt)}>COPY</button>
          <button className="p-mini-btn" onClick={clearAll}>CLEAR</button>
          <span style={{
            marginLeft: 'auto',
            font: '500 9px JetBrains Mono',
            letterSpacing: '0.18em',
            color: saved === 'error' ? '#ff4f6e' : 'rgba(var(--eg-accent-rgb),0.70)',
            textTransform: 'uppercase'
          }}>{savedLabel}</span>
        </div>
      </div>
    </Draggable>
  );
}

/* Setup Image Widget — shows the owner's desk/PC setup photo on demand. */
function SetupImageWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [missing, setMissing] = uWS(false);
  return (
    <Draggable id={id} title="My Setup" x={80} y={80} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: 420 }}>
      <div className="p-w-body">
        {missing ? (
          <div style={{ font: '500 11px/1.5 Inter', color: 'rgba(var(--eg-accent-rgb),0.86)', padding: 12 }}>
            Setup image unavailable: ../my setup.png was not found.
          </div>
        ) : (
          <img
            src="../my setup.png"
            className="pw-setup-img"
            alt="Desk setup"
            onError={() => setMissing(true)}
          />
        )}
        <div className="pw-setup-caption">Setup Overview</div>
      </div>
    </Draggable>
  );
}

/* PowerShell Log Widget — read-only viewer for the user's recent PowerShell
   commands. Backend (syrix_host.py::_publish_powershell_recent) tails the
   PSReadLine history file every few seconds and writes the last N lines to
   hud_state.json under `syrix_powershell_recent`. We just render them. */
function PowerShellLogWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [lines, setLines] = uWS([]);
  const [src, setSrc] = uWS('');
  const [updated, setUpdated] = uWS(0);
  uWE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    const unsub = window.SyrixBridge.subscribe((snap) => {
      const raw = snap && snap.raw;
      const obj = raw && raw.syrix_powershell_recent;
      if (obj && Array.isArray(obj.lines)) {
        setLines(obj.lines.slice(-80));
        setSrc(String(obj.source || ''));
        setUpdated(Number(obj.ts || 0));
      }
    });
    return unsub;
  }, []);
  const isEmpty = !lines || lines.length === 0;
  return (
    <Draggable id={id} title="PowerShell · Recent" x={140} y={120} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: 460 }}>
      <div className="p-w-body">
        <div style={{
          maxHeight: 260, overflowY: 'auto',
          background: 'rgba(2,8,18,0.62)',
          border: '1px solid rgba(var(--eg-accent-rgb),0.18)',
          borderRadius: 8, padding: '8px 10px',
          font: '500 11px/1.5 JetBrains Mono, monospace',
          color: 'rgba(196,232,250,0.92)',
          whiteSpace: 'pre-wrap', wordBreak: 'break-all',
        }}>
          {isEmpty ? (
            <span style={{ color: 'rgba(196,232,250,0.55)' }}>
              No PowerShell history captured yet. Run a few commands in any PowerShell window — the HUD picks them up automatically.
            </span>
          ) : lines.map((l, i) => (
            <div key={i} style={{ borderBottom: i === lines.length - 1 ? 'none' : '1px dashed rgba(var(--eg-accent-rgb),0.10)', padding: '2px 0' }}>
              <span style={{ color: 'rgba(var(--eg-accent-rgb),0.65)' }}>{'>'} </span>{l}
            </div>
          ))}
        </div>
        <div style={{
          marginTop: 6,
          font: '500 9px JetBrains Mono', letterSpacing: '0.18em',
          color: 'rgba(180,210,255,0.55)', textTransform: 'uppercase',
          display: 'flex', justifyContent: 'space-between',
        }}>
          <span>{lines.length} lines</span>
          <span title={src || 'PSReadLine ConsoleHost_history.txt'}>
            {src ? src.replace(/^.*[\\/]/, '') : 'history'}
          </span>
        </div>
      </div>
    </Draggable>
  );
}

/* SYRIX Foundation Upgrades — backend module status panel.
   Reads snap.syrix_upgrades (published every ~3s by syrix_upgrades_publisher).
   Each row shows a backend module's enabled flag, status, and provider so
   the user can see at a glance which new systems are live.
   HUD-safe: never renders URLs, API keys, or file paths. */
function SyrixUpgradesWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [snap, setSnap] = uWS(null);
  const [filter, setFilter] = uWS('all'); // all | live | available | disabled

  uWE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((s) => {
      if (s && s.syrix_upgrades && typeof s.syrix_upgrades === 'object') {
        setSnap(s.syrix_upgrades);
      }
    });
  }, []);

  const modules = snap
    ? Object.entries(snap).filter(([k]) => !k.startsWith('_')).sort((a, b) => a[0].localeCompare(b[0]))
    : [];

  const counts = modules.reduce((acc, [, info]) => {
    const s = String((info && info.status) || '').toLowerCase();
    if (s === 'ready' || s === 'serving' || s === 'streaming' || s === 'reloaded' || s === 'watching') acc.live++;
    else if (s === 'available' || s === 'configured') acc.available++;
    else acc.disabled++;
    return acc;
  }, { live: 0, available: 0, disabled: 0 });

  function rowState(info) {
    const s = String((info && info.status) || '').toLowerCase();
    if (s === 'ready' || s === 'serving' || s === 'streaming' || s === 'reloaded' || s === 'watching') return 'live';
    if (s === 'available' || s === 'configured') return 'available';
    if (s === 'error') return 'error';
    return 'disabled';
  }

  function dotColor(state) {
    if (state === 'live')      return 'rgba(100,220,140,0.95)';
    if (state === 'available') return 'rgba(120,180,255,0.85)';
    if (state === 'error')     return 'rgba(255,120,120,0.9)';
    return 'rgba(180,180,180,0.55)';
  }

  const visible = modules.filter(([, info]) => {
    if (filter === 'all') return true;
    return rowState(info) === filter;
  });

  return (
    <Draggable id={id} title="SYRIX Systems" x={260} y={80} onClose={onClose} focused={focused} onFocus={onFocus} onBlur={onBlur} style={{ width: 360 }}>
      <div className="p-w-body">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10, gap: 6, flexWrap: 'wrap' }}>
          <span style={{ font: '600 11px Inter', color: 'rgba(180,210,255,0.7)', letterSpacing: '.04em' }}>
            {snap ? `${counts.live} LIVE / ${counts.available} READY / ${counts.disabled} OFF` : 'WAITING…'}
          </span>
          <div style={{ display: 'flex', gap: 4 }}>
            {['all', 'live', 'available', 'disabled'].map(f => (
              <button
                key={f}
                className="p-mini-btn"
                onClick={() => setFilter(f)}
                style={{
                  padding: '2px 8px',
                  fontSize: 10,
                  opacity: filter === f ? 1 : 0.5,
                  border: filter === f ? '1px solid rgba(120,180,255,0.5)' : '1px solid rgba(120,180,255,0.18)',
                }}
              >
                {f.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        {snap ? (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: 4, maxHeight: 360, overflowY: 'auto' }}>
            {visible.map(([name, info]) => {
              const state = rowState(info);
              const color = dotColor(state);
              const provider = String((info && info.provider) || '').slice(0, 22);
              const status   = String((info && info.status)   || '').slice(0, 18);
              const reason   = String((info && info.reason)   || '');
              return (
                <div
                  key={name}
                  title={reason || `${name} – ${status}`}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '14px 1fr auto',
                    alignItems: 'center',
                    gap: 8,
                    padding: '3px 6px',
                    borderRadius: 6,
                    background: state === 'live' ? 'rgba(100,220,140,0.06)'
                              : state === 'error' ? 'rgba(255,120,120,0.08)'
                              : 'rgba(120,180,255,0.04)',
                    font: '500 10.5px Inter',
                    color: 'rgba(220,232,250,0.92)',
                  }}
                >
                  <span style={{ fontSize: 13, lineHeight: 1, color }}>●</span>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    <b style={{ color: 'rgba(220,232,250,0.95)' }}>{name}</b>
                    <span style={{ color: 'rgba(180,210,255,0.5)', marginLeft: 6 }}>{provider || ''}</span>
                  </span>
                  <span style={{ font: '500 10px JetBrains Mono', color, letterSpacing: '.02em' }}>{status}</span>
                </div>
              );
            })}
            {visible.length === 0 && (
              <div style={{ font: '500 11px Inter', color: 'rgba(180,210,255,0.45)', textAlign: 'center', padding: 12 }}>
                No modules match the current filter.
              </div>
            )}
          </div>
        ) : (
          <div style={{ font: '500 11px Inter', color: 'rgba(180,210,255,0.45)', textAlign: 'center', padding: 12 }}>
            Waiting for backend status…
          </div>
        )}
      </div>
    </Draggable>
  );
}

/* Widget Layer */
function WidgetLayer({ widgets, onClose, focusedId, onFocusChange }) {
  uWE(() => {
    if (!focusedId) return;
    function onKey(e) {
      if (e.key === 'Escape') onFocusChange && onFocusChange(null);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [focusedId, onFocusChange]);

  return (
    <>
      <style>{widgetCSS}</style>
      <div className={"p-wlayer" + (focusedId ? ' focused' : '')}>
        {widgets.map(w => {
          const shared = {
            id: w.id,
            onClose,
            focused: focusedId === w.id,
            onFocus: onFocusChange,
            onBlur: () => onFocusChange && onFocusChange(null),
          };
          if (w.type === 'music')      return <MusicWidget       key={w.id} {...shared} />;
          if (w.type === 'weather')    return <WeatherWidget     key={w.id} {...shared} />;
          if (w.type === 'map')        return <MapWidget         key={w.id} {...shared} />;
          if (w.type === 'timer')      return <TimerWidget       key={w.id} {...shared} />;
          if (w.type === 'notes')      return <NotesWidget       key={w.id} {...shared} />;
          if (w.type === 'drawing')    return <DrawingWidget     key={w.id} {...shared} />;
          if (w.type === 'memory')     return <MemoryWebWidget   key={w.id} {...shared} />;
          if (w.type === 'diagnostic') return <DiagnosticsWidget key={w.id} {...shared} />;
          if (w.type === 'api_status') return <ApiStatusWidget   key={w.id} {...shared} />;
          if (w.type === 'image')      return <ImageGenWidget    key={w.id} {...shared} />;
          if (w.type === 'setup')      return <SetupImageWidget  key={w.id} {...shared} />;
          if (w.type === 'powershell') return <PowerShellLogWidget key={w.id} {...shared} />;
          if (w.type === 'syrix_upgrades') return <SyrixUpgradesWidget key={w.id} {...shared} />;
          // Pass-2 widgets (telemetry_chart, memory_graph) live in
          // syrix-pass2-widgets.jsx and self-register under window.SyrixPass2Widgets.
          if (window.SyrixPass2Widgets && window.SyrixPass2Widgets[w.type]) {
            const P2 = window.SyrixPass2Widgets[w.type];
            return <P2 key={w.id} {...shared} />;
          }
          return null;
        })}
      </div>
    </>
  );
}

window.WidgetLayer = WidgetLayer;
window.SyrixDraggable = Draggable;
