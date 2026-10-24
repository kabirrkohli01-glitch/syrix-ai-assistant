/* SYRIX HUD App Dock — embedded BrowserView frame controls.
 *
 *  Renders the visual shell (glass chrome, controls, loading skeleton)
 *  for HUD apps that live in native Electron
 *  BrowserViews. The BrowserView itself is sized/positioned by main.js;
 *  this layer never paints over it — instead it leaves a transparent
 *  hole so the BrowserView shows through.
 *
 *  Architecture:
 *    syrix-bridge.js publishes:
 *      snap.syrix_hud_apps           → all open apps
 *      snap.syrix_active_hud_app     → focused id
 *      snap.syrix_hud_app_mode       → normal | minimized | fullscreen | custom
 *      snap.syrix_hud_app_vision_active
 *    + helper functions: openAppInHud / closeHudApp / fullscreenHudApp / …
 *
 *  Layout zone (must match main.js HUD_APP_LAYOUT):
 *    top: 64px, bottom: 64px, left: 146px, right: 16px + 420px chat rail
 *
 *  When an app is opened:
 *    - PrimaryHUD shrinks the orb to bottom-left compact mode.
 *    - The animated p-grid background keeps rendering behind the app.
 *    - HUD top/bottom bars + settings/widget icons remain reachable.
 *    - Per-panel widget-style close lives on the glass chrome titlebar.
 */
const { useState: uHAS, useEffect: uHAE, useRef: uHAR, useCallback: uHAC } = React;

/* Must mirror HUD_APP_LAYOUT / HUD_APP_CHROME_H in main.js (.p-chat width = 420). */
const HUD_CHAT_RAIL_W = 420;
const HUD_DOCK_MARGIN_RIGHT = 16;
const HUD_DOCK_INSETS = {
  top: 64,
  bottom: 64,
  left: 146,
  right: HUD_DOCK_MARGIN_RIGHT + HUD_CHAT_RAIL_W,
};
const HUD_APP_CHROME_H = 38;

const hudDockCSS = `
.syrix-hud-dock-root {
  position: fixed; inset: 0; pointer-events: none; z-index: 360;
}
.syrix-hud-dock-root.has-app { pointer-events: none; }

/* The frame draws as an overlay around the native BrowserView — borders
   and chrome only; the body is transparent. */
@keyframes hudAppOpen {
  from { opacity: 0; transform: scale(0.94) translateY(12px); }
  to   { opacity: 1; transform: none; }
}
.syrix-hud-app-frame {
  position: absolute;
  pointer-events: none;
  border: 1px solid rgba(var(--eg-accent-rgb),0.28);
  border-radius: 12px;
  box-shadow:
    0 24px 60px rgba(0,0,0,0.55),
    0 0 40px rgba(var(--eg-accent-rgb),0.08),
    inset 0 0 18px rgba(var(--eg-accent-rgb),0.04);
  overflow: hidden;
  animation: hudAppOpen 380ms cubic-bezier(0.16,1,0.3,1) both;
  transition: left 220ms cubic-bezier(0.32,0.72,0,1),
              top 220ms cubic-bezier(0.32,0.72,0,1),
              width 220ms cubic-bezier(0.32,0.72,0,1),
              height 220ms cubic-bezier(0.32,0.72,0,1),
              opacity 360ms cubic-bezier(0.16,1,0.3,1),
              transform 360ms cubic-bezier(0.16,1,0.3,1),
              border-radius 260ms cubic-bezier(0.32,0.72,0,1);
}
.syrix-hud-app-frame.minimized { opacity: 0; transform: scale(0.90) translateY(8px); pointer-events: none; }
.syrix-hud-app-frame.dragging { transition: none !important; animation: none !important; cursor: grabbing; }

/* Stage mode — the BrowserView is a full-window backdrop; the frame becomes an
   invisible positioning shell for status chips only (nav lives in the island). */
.syrix-hud-app-frame.stage {
  border: none;
  border-radius: 0;
  box-shadow: none;
  animation: none;
  pointer-events: none;
  background: transparent;
}
.syrix-hud-app-frame.stage .syrix-hud-app-control-status { top: 10px; }

/* Freeze backdrop — capturePage snapshot shown while the staged view is parked
   so HTML overlays (folder, widgets, menus) can paint above web content. */
@keyframes syrixStageFreezeIn { from { opacity: 0; } to { opacity: 1; } }
.syrix-stage-freeze {
  position: fixed;
  z-index: 350;
  background: #03080f;
  overflow: hidden;
  pointer-events: none;
  animation: syrixStageFreezeIn 150ms ease both;
}
.syrix-stage-freeze img {
  width: 100%; height: 100%;
  object-fit: fill;
  display: block;
}

/* Stage: the control-problem toast must clear the nav strip (view starts at
   y=68) — lift it into the strip and above the island. */
body.stage-browser .syrix-hud-app-toast { top: 14px; z-index: 9910; }
.syrix-hud-drag-overlay {
  position: absolute;
  left: 0; right: 0;
  top: ${HUD_APP_CHROME_H}px; bottom: 0;
  background: rgba(4,11,22,0.72);
  backdrop-filter: blur(14px);
  border-bottom-left-radius: 8px;
  border-bottom-right-radius: 8px;
  pointer-events: none;
  overflow: hidden;
}
.syrix-hud-drag-snapshot {
  position: absolute;
  inset: 0;
  width: 100%; height: 100%;
  object-fit: fill;
  opacity: 0.92;
  border-bottom-left-radius: 8px;
  border-bottom-right-radius: 8px;
}

/* Chrome wraps the BrowserView with a glass titlebar + controls strip.
   pointer-events:auto only on the chrome itself so clicks on the body
   pass through to the BrowserView. */
.syrix-hud-app-chrome {
  position: absolute;
  left: 0; right: 0; top: -1px;
  height: ${HUD_APP_CHROME_H}px;
  display: flex; align-items: center; gap: 7px;
  padding: 0 8px 0 10px;
  background: rgba(4,11,22,0.94);
  backdrop-filter: blur(20px);
  border-bottom: 1px solid rgba(var(--eg-accent-rgb),0.18);
  border-top-left-radius: 8px;
  border-top-right-radius: 8px;
  pointer-events: auto;
  user-select: none;
}
.syrix-hud-app-chrome.fullscreen { border-radius: 0; }
.syrix-hud-app-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: rgba(var(--eg-accent-surface-rgb),0.90);
  box-shadow: 0 0 7px rgba(var(--eg-accent-rgb),0.55);
  flex-shrink: 0;
}
.syrix-hud-app-title {
  font: 600 10px 'JetBrains Mono'; letter-spacing: 0.10em;
  color: rgba(var(--eg-accent-rgb),0.92);
  text-transform: uppercase;
  text-overflow: ellipsis; white-space: nowrap; overflow: hidden;
  max-width: 120px; flex-shrink: 0;
}
/* 2026-05-30 — The old per-tab chip strip has been replaced by a compact
   "Tabs (N) ▾" pill (see .syrix-hud-tabs-pill below).  Keep these classes
   alive but hidden so legacy callers still type-check; styling is neutralised
   so they never paint a red/disconnected strip across the top of the HUD. */
.syrix-hud-browser-tabs {
  display: none !important;
}
.syrix-hud-browser-tab { display: none !important; }
.syrix-hud-browser-tab-x { display: none !important; }

/* Compact Tabs pill — sits in the dock chrome's top control row.
   Acts as both a count indicator and a popover that lists open tabs with
   focus + close controls. */
.syrix-hud-tabs-pill {
  display: inline-flex; align-items: center; gap: 6px;
  height: 22px;
  padding: 0 9px;
  background: rgba(var(--eg-accent-surface-rgb),0.10);
  border: 1px solid rgba(var(--eg-accent-rgb),0.28);
  border-radius: 999px;
  font: 700 9px 'JetBrains Mono'; letter-spacing: 0.18em; text-transform: uppercase;
  color: rgba(var(--eg-accent-rgb),0.92);
  cursor: pointer;
  flex-shrink: 0;
  pointer-events: auto;
  transition: background .15s, border-color .15s;
}
.syrix-hud-tabs-pill:hover { background: rgba(var(--eg-accent-surface-rgb),0.18); border-color: rgba(var(--eg-accent-rgb),0.45); }
.syrix-hud-tabs-pill .syrix-hud-tabs-count {
  font: 700 9px 'JetBrains Mono'; color: rgb(var(--eg-accent-rgb));
}
.syrix-hud-tabs-pill .syrix-hud-tabs-caret {
  font-size: 9px; line-height: 1; opacity: 0.85;
}
.syrix-hud-tabs-pop {
  position: absolute; top: ${HUD_APP_CHROME_H + 4}px;
  background: rgba(4,11,22,0.96); backdrop-filter: blur(18px);
  border: 1px solid rgba(var(--eg-accent-rgb),0.30);
  border-radius: 10px;
  padding: 6px;
  min-width: 240px; max-width: 320px;
  max-height: 280px; overflow-y: auto;
  box-shadow: 0 18px 42px rgba(0,0,0,0.55);
  z-index: 5;
  /* Smooth dropdown: hidden by default, revealed on .open */
  opacity: 0;
  transform: translateY(-8px) scaleY(0.88);
  transform-origin: top center;
  pointer-events: none;
  visibility: hidden;
  transition: opacity 0.18s cubic-bezier(0.23,1,0.32,1),
              transform 0.18s cubic-bezier(0.23,1,0.32,1),
              visibility 0s linear 0.18s;
}
.syrix-hud-tabs-pop.open {
  opacity: 1;
  transform: translateY(0) scaleY(1);
  pointer-events: auto;
  visibility: visible;
  transition: opacity 0.18s cubic-bezier(0.23,1,0.32,1),
              transform 0.18s cubic-bezier(0.23,1,0.32,1),
              visibility 0s linear 0s;
}
.syrix-hud-tabs-pop-row {
  display: flex; align-items: center; gap: 6px;
  padding: 6px 8px;
  border-radius: 7px;
  cursor: pointer;
  color: rgba(220,235,255,0.88);
  font: 500 11px 'Inter';
  transition: background .12s;
}
.syrix-hud-tabs-pop-row:hover { background: rgba(var(--eg-accent-surface-rgb),0.10); }
.syrix-hud-tabs-pop-row.active {
  background: rgba(var(--eg-accent-surface-rgb),0.14);
  color: rgba(var(--eg-accent-rgb),0.98);
}
.syrix-hud-tabs-pop-label {
  flex: 1; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.syrix-hud-tabs-pop-close {
  width: 18px; height: 18px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 4px;
  color: rgba(180,210,235,0.72);
  font: 700 11px 'JetBrains Mono';
  cursor: pointer;
}
.syrix-hud-tabs-pop-close:hover {
  background: rgba(var(--eg-accent-surface-rgb),0.18);
  color: rgb(var(--eg-accent-rgb));
}
/* URL address bar — pill-shaped omnibox, Google-style. */
.syrix-hud-app-urlbar {
  flex: 1; min-width: 0;
  display: flex; align-items: center; gap: 5px;
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(var(--eg-accent-rgb),0.16);
  border-radius: 999px;
  padding: 0 12px;
  height: 24px;
  overflow: hidden;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.syrix-hud-app-urlbar:focus-within {
  border-color: rgba(var(--eg-accent-rgb),0.42);
  box-shadow: 0 0 0 2px rgba(var(--eg-accent-rgb),0.12);
}
.syrix-hud-app-urlbar-proto {
  font: 600 9px 'JetBrains Mono'; letter-spacing: 0.04em;
  color: rgba(var(--eg-accent-rgb),0.55);
  flex-shrink: 0; user-select: none;
}
.syrix-hud-app-urlbar-input {
  flex: 1; min-width: 0;
  background: transparent; border: none; outline: none;
  color: rgba(220,240,255,0.92);
  font: 400 11px 'Inter', system-ui;
  caret-color: rgb(var(--eg-accent-rgb));
  text-overflow: ellipsis;
}
.syrix-hud-app-urlbar-input::placeholder { color: rgba(140,180,220,0.38); }
.syrix-hud-app-host {
  font: 500 10px 'Inter'; letter-spacing: 0.02em;
  color: rgba(200,230,255,0.82);
  text-overflow: ellipsis; overflow: hidden; max-width: 180px; white-space: nowrap;
  flex-shrink: 1;
}
.syrix-hud-nav-sep {
  width: 1px; height: 14px;
  background: rgba(var(--eg-accent-surface-rgb),0.14);
  flex-shrink: 0;
}
.syrix-hud-app-ctrls { display: flex; align-items: center; gap: 3px; flex-shrink: 0; }
.syrix-hud-app-btn {
  width: 26px; height: 24px; padding: 0;
  background: rgba(var(--eg-accent-surface-rgb),0.055);
  border: 1px solid rgba(var(--eg-accent-rgb),0.18);
  color: rgba(var(--eg-accent-rgb),0.88);
  border-radius: 5px;
  font: 600 11px JetBrains Mono;
  cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center;
  transition: background 100ms, color 100ms, transform 100ms, border-color 140ms, box-shadow 160ms;
}
.syrix-hud-app-btn:hover {
  background: rgba(var(--eg-accent-surface-rgb),0.14);
  color: rgb(var(--eg-accent-rgb));
  border-color: rgba(var(--eg-accent-rgb),0.52);
  box-shadow: 0 0 0 1px rgba(var(--eg-accent-rgb),0.15), 0 0 16px rgba(var(--eg-accent-rgb),0.16);
}
.syrix-hud-app-btn:active { transform: scale(0.93); }
.syrix-hud-app-btn:disabled {
  opacity: 0.28;
  cursor: default;
  transform: none;
}
.syrix-hud-app-btn.vision-on {
  background: rgba(var(--eg-accent-surface-rgb),0.18); color: rgb(var(--eg-accent-rgb)); border-color: rgba(var(--eg-accent-rgb),0.4);
}
/* Widget-style panel close. */
.syrix-hud-app-close {
  flex-shrink: 0;
  background: none; border: none; cursor: pointer;
  color: rgba(140,170,200,0.55);
  font-size: 14px; padding: 3px 5px;
  transition: color .15s, transform .15s; line-height: 1;
}
.syrix-hud-app-close:hover { color: #ff4f6e; }
.syrix-hud-app-close:active { transform: scale(0.94); }

/* Resize handle — bottom-right corner grab zone. */
.syrix-hud-app-resize-handle {
  position: absolute;
  right: 0; bottom: 0;
  width: 18px; height: 18px;
  cursor: se-resize;
  pointer-events: auto;
  z-index: 2;
}
.syrix-hud-app-resize-handle::after {
  content: '';
  position: absolute;
  right: 4px; bottom: 4px;
  width: 8px; height: 8px;
  border-right: 2px solid rgba(var(--eg-accent-rgb),0.45);
  border-bottom: 2px solid rgba(var(--eg-accent-rgb),0.45);
  border-radius: 1px;
}
.syrix-hud-app-resize-handle:hover::after {
  border-color: rgba(var(--eg-accent-rgb),0.85);
}

/* Loading skeleton — dark gradient so we never flash white over the grid. */
.syrix-hud-app-skeleton {
  position: absolute; left: 0; right: 0; top: ${HUD_APP_CHROME_H}px; bottom: 0;
  pointer-events: none;
  background: linear-gradient(135deg, rgba(3,9,18,0.82) 0%, rgba(8,18,32,0.92) 50%, rgba(3,9,18,0.82) 100%);
  background-size: 200% 100%;
  animation: syrixHudSkel 3.2s linear infinite;
  border-bottom-left-radius: 8px;
  border-bottom-right-radius: 8px;
  display: flex; align-items: center; justify-content: center;
}
@keyframes syrixHudSkel { from { background-position: 200% 0; } to { background-position: -200% 0; } }
.syrix-hud-app-skeleton-text {
  font: 600 10px 'JetBrains Mono'; letter-spacing: 0.12em;
  color: rgba(var(--eg-accent-rgb),0.65);
  text-transform: uppercase;
}
.syrix-hud-app-control-status {
  position: absolute; top: ${HUD_APP_CHROME_H + 8}px; right: 10px;
  z-index: 4;
  pointer-events: none;
  padding: 5px 9px;
  background: rgba(4,11,22,0.88);
  backdrop-filter: blur(14px);
  border: 1px solid rgba(var(--eg-accent-rgb),0.34);
  border-radius: 6px;
  color: rgba(var(--eg-accent-bright-rgb),0.94);
  font: 700 9px 'JetBrains Mono';
  letter-spacing: 0.04em;
  text-transform: none;
  box-shadow: 0 10px 28px rgba(0,0,0,0.38);
}
.syrix-hud-app-control-status.failed {
  border-color: rgba(255,79,110,0.4);
  color: rgba(255,184,197,0.95);
}
.syrix-hud-app-toast {
  position: fixed; top: 72px; left: 50%; transform: translateX(-50%);
  z-index: 9500;
  pointer-events: none;
  padding: 7px 11px;
  background: rgba(4,11,22,0.92);
  backdrop-filter: blur(16px);
  border: 1px solid rgba(var(--eg-accent-rgb),0.34);
  border-radius: 7px;
  color: rgba(var(--eg-accent-bright-rgb),0.96);
  font: 700 10px 'JetBrains Mono';
  letter-spacing: 0.04em;
  text-transform: none;
  box-shadow: 0 18px 42px rgba(0,0,0,0.42);
}
.syrix-hud-app-toast.failed {
  border-color: rgba(255,79,110,0.44);
  color: rgba(255,184,197,0.96);
}

/* Status overlay — vision indicator + minimize chip. */
.syrix-hud-app-vision-indicator {
  position: absolute; left: 12px; bottom: 10px;
  pointer-events: none;
  padding: 4px 10px;
  background: rgba(var(--eg-accent-surface-rgb),0.18);
  border: 1px solid rgba(var(--eg-accent-rgb),0.45);
  border-radius: 999px;
  font: 600 9px 'JetBrains Mono'; letter-spacing: 0.22em;
  color: rgb(var(--eg-accent-rgb));
  text-transform: uppercase;
  display: flex; align-items: center; gap: 6px;
  box-shadow: 0 0 18px rgba(var(--eg-accent-rgb),0.25);
  animation: syrixHudPulse 1.6s ease-in-out infinite;
}
@keyframes syrixHudPulse { 0%,100% { opacity: 0.85; } 50% { opacity: 1; } }
.syrix-hud-app-vision-indicator .dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: rgb(var(--eg-accent-surface-rgb)); box-shadow: 0 0 8px rgb(var(--eg-accent-rgb));
}

/* Minimized app chips appear in the bottom dock strip. */
.syrix-hud-dock-strip {
  position: fixed;
  left: 50%; bottom: 76px; transform: translateX(-50%);
  display: flex; gap: 6px;
  z-index: 365;
  pointer-events: none;
}
.syrix-hud-dock-chip {
  pointer-events: auto;
  padding: 5px 12px;
  background: rgba(3,9,18,0.85);
  backdrop-filter: blur(16px);
  border: 1px solid rgba(var(--eg-accent-rgb),0.25);
  border-radius: 999px;
  display: flex; align-items: center; gap: 6px;
  cursor: pointer;
  transition: background 120ms, border-color 120ms;
}
.syrix-hud-dock-chip:hover {
  background: rgba(var(--eg-accent-surface-rgb),0.08);
  border-color: rgba(var(--eg-accent-rgb),0.45);
}
.syrix-hud-dock-chip .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: rgba(var(--eg-accent-surface-rgb),0.80);
}
.syrix-hud-dock-chip .label {
  font: 600 9px 'JetBrains Mono'; letter-spacing: 0.18em;
  color: rgba(220,235,255,0.85); text-transform: uppercase;
  max-width: 160px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap;
}
.syrix-hud-dock-chip .x {
  font: 600 11px JetBrains Mono;
  color: rgba(180,210,255,0.55);
  background: none; border: none; cursor: pointer;
  padding: 0; margin-left: 4px;
}
.syrix-hud-dock-chip .x:hover { color: #ff4f6e; }

/* Picker — small menu shown when user clicks the "open app" launcher. */
.syrix-hud-app-picker {
  position: fixed; right: 18px; top: 70px;
  z-index: 800;
  width: 240px;
  background: rgba(3,9,18,0.96);
  backdrop-filter: blur(22px);
  border: 1px solid rgba(var(--eg-accent-rgb),0.28);
  border-radius: 12px;
  padding: 8px;
  box-shadow: 0 24px 60px rgba(0,0,0,0.6);
}
.syrix-hud-app-picker-row {
  display: flex; align-items: center; gap: 8px;
  padding: 7px 9px;
  border-radius: 7px;
  cursor: pointer;
  transition: background 100ms;
}
.syrix-hud-app-picker-row:hover { background: rgba(var(--eg-accent-surface-rgb),0.08); }
.syrix-hud-app-picker-row .icon { font-size: 14px; width: 18px; text-align: center; }
.syrix-hud-app-picker-row .label {
  font: 500 11px Inter; color: rgba(220,235,255,0.85);
}
.syrix-hud-app-picker-input {
  width: 100%; margin-top: 6px; padding: 6px 8px;
  background: rgba(4,10,22,0.6);
  border: 1px solid rgba(var(--eg-accent-rgb),0.18);
  border-radius: 6px;
  color: #e9f3ff; outline: none;
  font: 500 11px Inter;
}
`;

/* App icons / built-ins shown in the launcher. The picker also accepts
   any URL or known alias via the bottom input — the bridge resolves it. */
const HUD_APP_BUILTINS = [
  { id: 'youtube',     icon: '▶',  label: 'YouTube',       target: 'youtube' },
  { id: 'maps',        icon: '🗺',  label: 'Google Maps',   target: 'maps' },
  { id: 'tradingview', icon: '📈', label: 'TradingView',   target: 'tradingview' },
  { id: 'news',        icon: '📰', label: 'News',          target: 'news' },
  { id: 'spotify',     icon: '♫',  label: 'Spotify Web',   target: 'spotify' },
  { id: 'github',      icon: '⌨',  label: 'GitHub',        target: 'github' },
  { id: 'chatgpt',     icon: '✦',  label: 'ChatGPT',       target: 'chatgpt' },
  { id: 'wikipedia',   icon: 'W',  label: 'Wikipedia',     target: 'wikipedia' },
];

function HudAppPickerMenu({ open, onClose, onPick }) {
  const [text, setText] = uHAS('');
  if (!open) return null;
  function submit() {
    const t = String(text || '').trim();
    if (!t) return;
    onPick(t);
    setText('');
  }
  return (
    <div className="syrix-hud-app-picker" onClick={(e) => e.stopPropagation()}>
      {HUD_APP_BUILTINS.map((row) => (
        <div key={row.id} className="syrix-hud-app-picker-row"
             onClick={() => { onPick(row.target); onClose(); }}>
          <div className="icon">{row.icon}</div>
          <div className="label">{row.label}</div>
        </div>
      ))}
      <input
        type="text"
        className="syrix-hud-app-picker-input"
        placeholder="Type a site or alias…"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') { submit(); onClose(); } }}
        autoFocus
      />
    </div>
  );
}

/* 2026-05-30 — Compact Tabs pill.  Replaces the old per-tab strip across the
   top of the HUD with a single "Tabs (N) ▾" chip in the dock chrome that
   pops a list of open tabs with focus + close controls.  All previous
   behaviour stays (switch tab, close tab, see open tabs); just compact. */
function HudTabsPill({ tabs, activeId, onCmd, chromeH }) {
  const [open, setOpen] = uHAS(false);
  const rootRef = uHAR(null);
  uHAE(() => {
    if (!open) return;
    function onDoc(ev) {
      if (rootRef.current && rootRef.current.contains(ev.target)) return;
      setOpen(false);
    }
    function onEsc(ev) { if (ev.key === 'Escape') setOpen(false); }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onEsc);
    };
  }, [open]);
  const list = Array.isArray(tabs) ? tabs : [];
  if (list.length === 0) return null;
  function shortLabel(tab) {
    const raw = String((tab && (tab.title || tab.domain || tab.url)) || 'Tab').trim();
    try {
      if (/^https?:\/\//i.test(raw)) return new URL(raw).hostname.replace(/^www\./, '').slice(0, 30);
    } catch (_) {}
    return raw.replace(/^www\./, '').slice(0, 30);
  }
  function focusTab(id) {
    try { console.info('[HUD_BROWSER_TAB_FOCUS] id=' + id); } catch (_) {}
    try { onCmd('focus', id); } catch (_) {}
    setOpen(false);
  }
  function closeTab(id, ev) {
    if (ev) { try { ev.stopPropagation(); } catch (_) {} }
    try { console.info('[HUD_BROWSER_TAB_CLOSE] id=' + id); } catch (_) {}
    try { onCmd('close', id); } catch (_) {}
  }
  try { console.info('[HUD_BROWSER_TABS_RENDER] count=' + list.length + ' active=' + (activeId || '')); } catch (_) {}
  return (
    <div ref={rootRef} style={{ position: 'relative' }} onMouseDown={(e) => e.stopPropagation()}>
      <div className="syrix-hud-tabs-pill"
           title={list.length + ' open tab' + (list.length === 1 ? '' : 's')}
           onClick={(e) => { e.stopPropagation(); setOpen(v => !v); }}>
        <span>Tabs</span>
        <span className="syrix-hud-tabs-count">{list.length}</span>
        <span className="syrix-hud-tabs-caret" style={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.18s', display: 'inline-block' }}>▾</span>
      </div>
      {/* Always in DOM so CSS transition can animate open/close */}
      <div className={"syrix-hud-tabs-pop" + (open ? ' open' : '')} role="menu">
        {list.map((tab) => (
          <div
            key={tab.id}
            className={"syrix-hud-tabs-pop-row" + (tab.id === activeId ? ' active' : '')}
            title={tab.url || tab.title || 'HUD tab'}
            onClick={() => focusTab(tab.id)}>
            <span className="syrix-hud-tabs-pop-label">{shortLabel(tab)}</span>
            <span
              className="syrix-hud-tabs-pop-close"
              title="Close tab"
              onClick={(e) => closeTab(tab.id, e)}>×</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function HudAppFrame({ app, tabs, isActive, focusedFullscreen, stage, onCmd }) {
  const mode = app.mode || 'normal';
  const [urlInput, setUrlInput] = uHAS(app.url || '');
  const [isDragging, setIsDragging] = uHAS(false);
  const controlStatus = String(app.controlsStatus || '');
  const showControlStatus = controlStatus === 'retrying' || controlStatus === 'failed';
  const controlStatusText = String(
    app.controlsDetail || (controlStatus === 'failed' ? 'controls unavailable' : 'controls retrying')
  );
  const host = (() => {
    try { return new URL(app.url || '').hostname.replace(/^www\./, ''); } catch { return ''; }
  })();
  // dragRef: { type, startX, startY, origBounds }
  const dragRef       = uHAR(null);
  const rafRef        = uHAR(null);
  const lastMove      = uHAR(null);
  const [localBounds, setLocalBounds] = uHAS(null);
  const localBoundsRef = uHAR(null);
  const [dragSnapshot, setDragSnapshot] = uHAS(null);

  // Sync URL bar when the app navigates.
  uHAE(() => { setUrlInput(app.url || ''); }, [app.url]);

  // Chrome-mount ack — tells main.js the per-app nav header rendered. Retry
  // because the bridge/preload can arrive a few ticks after the BrowserView.
  uHAE(() => {
    let cancelled = false;
    let timer = null;
    const delays = [0, 120, 300, 700, 1200, 2200, 4000, 7000, 10000, 15000];
    function schedule(idx) {
      if (cancelled || idx >= delays.length) return;
      timer = setTimeout(() => attempt(idx), delays[idx]);
    }
    function attempt(idx) {
      if (cancelled) return;
      try {
        const B = window.SyrixBridge;
        let p = null;
        if (B && typeof B.ackHudAppChrome === 'function') {
          p = B.ackHudAppChrome(app.id);
        } else if (window.syrixHud && window.syrixHud.hudApp && window.syrixHud.hudApp.chromeAck) {
          p = window.syrixHud.hudApp.chromeAck(app.id);
        } else if (window.syrixHud && window.syrixHud.ackHudAppChrome) {
          p = window.syrixHud.ackHudAppChrome(app.id);
        }
        if (!p || typeof p.then !== 'function') {
          schedule(idx + 1);
          return;
        }
        p.then((res) => {
          if (cancelled) return;
          if (res && res.ok) return;
          schedule(idx + 1);
        }).catch(() => schedule(idx + 1));
      } catch (_) {
        schedule(idx + 1);
      }
    }
    schedule(0);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [app.id]);

  // Global mouse handlers for drag-move and resize.
  uHAE(() => {
    function onMove(e) {
      const d = dragRef.current;
      if (!d) return;
      lastMove.current = { clientX: e.clientX, clientY: e.clientY };
      if (rafRef.current) return; // already a frame pending
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        const d2 = dragRef.current;
        const m = lastMove.current;
        if (!d2 || !m) return;
        const dx = m.clientX - d2.startX;
        const dy = m.clientY - d2.startY;
        const ob = d2.origBounds;
        const nb = d2.type === 'move'
          ? { x: ob.x + dx, y: ob.y + dy, width: ob.width, height: ob.height }
          : { x: ob.x, y: ob.y, width: Math.max(320, ob.width + dx), height: Math.max(240, ob.height + dy) };
        localBoundsRef.current = nb;
        setLocalBounds({ ...nb });
      });
    }
    function onUp() {
      if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
      const finalBounds = localBoundsRef.current;
      dragRef.current = null;
      lastMove.current = null;
      localBoundsRef.current = null;
      setIsDragging(false);
      setLocalBounds(null);
      setDragSnapshot(null);
      // Restore BrowserView at the dropped position
      try { window.syrixHud?.hudApp?.dragEnd?.(app.id, finalBounds || null); } catch (_) {}
    }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
    };
  }, [app.id]);

  if (mode === 'minimized') {
    return <div className="syrix-hud-app-frame minimized" />;
  }

  // Stage mode: the BrowserView is a full-window backdrop and the island nav
  // bar owns navigation, so the floating chrome disappears entirely. This
  // render still keeps the component (and its chrome-ack effect above)
  // mounted, so the main-side watchdog stays satisfied.
  if (stage) {
    const sb = app.bounds && typeof app.bounds === 'object' && Number.isFinite(app.bounds.x) ? app.bounds : null;
    const stageStyle = sb
      ? { left: sb.x, top: sb.y, width: sb.width, height: sb.height }
      : { left: 0, top: 68, right: 0, bottom: 0 };
    return (
      <div className="syrix-hud-app-frame stage" style={stageStyle}>
        {showControlStatus && (
          <div className={"syrix-hud-app-control-status " + controlStatus} aria-live="polite">
            {controlStatusText}
          </div>
        )}
        {app.vision && (
          <div className="syrix-hud-app-vision-indicator">
            <div className="dot"></div>
            <span>HUD VISION · LIVE</span>
          </div>
        )}
      </div>
    );
  }

  // Bounds: prefer boundsApplied (always up-to-date pixel coords from main.js),
  // fall back to app.bounds, then CSS-calc for first render before first IPC reply.
  const bounds = (() => {
    if (mode === 'fullscreen') {
      return {
        left: HUD_DOCK_INSETS.left,
        top: HUD_DOCK_INSETS.top,
        width: 'calc(100vw - ' + (HUD_DOCK_INSETS.left + HUD_DOCK_INSETS.right) + 'px)',
        height: 'calc(100vh - ' + (HUD_DOCK_INSETS.top + HUD_DOCK_INSETS.bottom) + 'px)',
      };
    }
    const b = app.bounds && typeof app.bounds === 'object' && Number.isFinite(app.bounds.x)
      ? app.bounds : null;
    if (b) return { left: b.x, top: b.y, width: b.width, height: b.height };
    // First-render fallback (before first broadcast arrives) — CSS-calc mirrors main.js.
    const zw = 'calc(100vw - ' + (HUD_DOCK_INSETS.left + HUD_DOCK_INSETS.right) + 'px)';
    const zh = 'calc(100vh - ' + (HUD_DOCK_INSETS.top + HUD_DOCK_INSETS.bottom) + 'px)';
    return {
      left: 'calc((100vw - ' + (HUD_DOCK_INSETS.left + HUD_DOCK_INSETS.right) + 'px) * 0.14 + ' + HUD_DOCK_INSETS.left + 'px)',
      top:  'calc((100vh - ' + (HUD_DOCK_INSETS.top + HUD_DOCK_INSETS.bottom) + 'px) * 0.11 + ' + HUD_DOCK_INSETS.top + 'px)',
      width:  'calc(' + zw + ' * 0.72)',
      height: 'calc(' + zh + ' * 0.78)',
    };
  })();

  function startDrag(type, e) {
    if (e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    const b = app.bounds && typeof app.bounds === 'object' && Number.isFinite(app.bounds.x)
      ? { ...app.bounds }
      : { x: HUD_DOCK_INSETS.left + 40, y: HUD_DOCK_INSETS.top + 40, width: 720, height: 480 };
    dragRef.current = { type, startX: e.clientX, startY: e.clientY, origBounds: b };
    localBoundsRef.current = { ...b };
    setLocalBounds({ ...b });
    setIsDragging(true);
    // Capture a screenshot of the browser content, then hide the BrowserView so
    // mousemove events reach this HTML layer. The snapshot keeps the drag visually full.
    try {
      const p = window.syrixHud?.hudApp?.dragStart?.(app.id);
      if (p && typeof p.then === 'function') {
        p.then(res => { if (res && res.snapshot) setDragSnapshot(res.snapshot); }).catch(() => {});
      }
    } catch (_) {}
  }

  function navigateTo(raw) {
    let url = String(raw || '').trim();
    if (!url) return;
    if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
    const B = window.SyrixBridge;
    if (B && B.navigateHudApp) {
      B.navigateHudApp(app.id, url);
    } else if (B && B.openAppInHud) {
      B.openAppInHud(url, { source: 'user_click' });
    }
  }

  function tabLabel(tab) {
    const raw = String((tab && (tab.title || tab.domain || tab.url)) || 'Tab').trim();
    if (!raw) return 'Tab';
    try {
      if (/^https?:\/\//i.test(raw)) return new URL(raw).hostname.replace(/^www\./, '').slice(0, 28);
    } catch (_) {}
    return raw.replace(/^www\./, '').slice(0, 28);
  }

  const effectiveBounds = (isDragging && localBounds)
    ? { left: localBounds.x, top: localBounds.y, width: localBounds.width, height: localBounds.height }
    : bounds;

  return (
    <div className={"syrix-hud-app-frame" + (isActive ? ' active' : '') + (isDragging ? ' dragging' : '')} style={effectiveBounds}>
      <div className={"syrix-hud-app-chrome" + (mode === 'fullscreen' ? ' fullscreen' : '')}
           onMouseDown={(e) => {
             // Only drag from non-interactive parts of the chrome.
             if (e.target.closest('button,input,a')) return;
             if (mode !== 'fullscreen') startDrag('move', e);
           }}
           style={{ cursor: mode === 'fullscreen' ? 'default' : 'grab' }}>
        {/* Left: back / forward / refresh */}
        <div className="syrix-hud-app-ctrls">
          <button className="syrix-hud-app-btn" title="Back" onClick={() => onCmd('back', app.id)}
                  disabled={!app.canGoBack}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M7 2L4 5.5L7 9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </button>
          <button className="syrix-hud-app-btn" title="Forward" onClick={() => onCmd('forward', app.id)}
                  disabled={!app.canGoForward}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M4 2L7 5.5L4 9" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </button>
          <button className="syrix-hud-app-btn" title="Refresh" onClick={() => onCmd('refresh', app.id)}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M9 5.5A3.5 3.5 0 1 1 7.5 2.5M9 2v3H6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </button>
        </div>
        <div className="syrix-hud-nav-sep"></div>
        {/* Center: pill-shaped omnibox */}
        <div className="syrix-hud-app-urlbar">
          <span className="syrix-hud-app-urlbar-proto">
            {/^https:\/\//i.test(urlInput) ? '🔒' : /^http:\/\//i.test(urlInput) ? '🌐' : '🔍'}
          </span>
          <input
            type="text"
            className="syrix-hud-app-urlbar-input"
            value={urlInput}
            placeholder="Search or enter URL…"
            onChange={(e) => setUrlInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); navigateTo(urlInput); }
              if (e.key === 'Escape') { e.target.blur(); setUrlInput(app.url || ''); }
            }}
            onFocus={(e) => e.target.select()}
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <div className="syrix-hud-nav-sep"></div>
        {/* Right: tabs + window controls */}
        <HudTabsPill tabs={tabs} activeId={app.id} onCmd={onCmd} chromeH={HUD_APP_CHROME_H} />
        <div className="syrix-hud-app-ctrls">
          <button className="syrix-hud-app-btn" title="Stage — fill the HUD background"
                  onClick={() => { try { window.SyrixStage && window.SyrixStage.enable(0); } catch (_) {} }}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none"><rect x="1.5" y="1.5" width="8" height="8" rx="1.2" stroke="currentColor" strokeWidth="1.4"/><path d="M1.5 4h8" stroke="currentColor" strokeWidth="1.2"/></svg>
          </button>
          {mode === 'fullscreen' ? (
            <button className="syrix-hud-app-btn" title="Restore" onClick={() => onCmd('restore', app.id)}>
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none"><rect x="3.5" y="2" width="5.5" height="5.5" rx="1" stroke="currentColor" strokeWidth="1.4"/><path d="M2 4v4.5A.5.5 0 0 0 2.5 9H7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>
            </button>
          ) : (
            <button className="syrix-hud-app-btn" title="Fullscreen" onClick={() => onCmd('fullscreen', app.id)}>
              <svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M1.5 4V2h2M7.5 2h2v2M9.5 7v2h-2M3.5 9h-2V7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>
            </button>
          )}
          <button className="syrix-hud-app-btn" title="Minimize" onClick={() => onCmd('minimize', app.id)}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M2.5 8.5h6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/></svg>
          </button>
          <button className={"syrix-hud-app-btn" + (app.vision ? ' vision-on' : '')}
                  title={app.vision ? 'Vision ON' : 'Vision OFF'}
                  onClick={() => onCmd('vision_toggle', app.id)}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none"><ellipse cx="5.5" cy="5.5" rx="4" ry="2.6" stroke="currentColor" strokeWidth="1.4"/><circle cx="5.5" cy="5.5" r="1.3" fill="currentColor"/></svg>
          </button>
          <button className="syrix-hud-app-btn" title="Open externally" onClick={() => onCmd('popout', app.id)}>
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none"><path d="M6 2h3v3M9 2L5.5 5.5M4 3H2.5A.5.5 0 0 0 2 3.5v5A.5.5 0 0 0 2.5 9h5A.5.5 0 0 0 8 8.5V7" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/></svg>
          </button>
        </div>
        <button type="button" className="syrix-hud-app-close" title="Close app" aria-label="Close app"
                onClick={(e) => { e.stopPropagation(); onCmd('close', app.id); }}>✕</button>
      </div>
      {isDragging && (
        <div className="syrix-hud-drag-overlay">
          {dragSnapshot && <img className="syrix-hud-drag-snapshot" src={dragSnapshot} alt="" />}
        </div>
      )}
      {!isDragging && app.loading && (
        <div className="syrix-hud-app-skeleton">
          <div className="syrix-hud-app-skeleton-text">Loading {host || 'app'}…</div>
        </div>
      )}
      {showControlStatus && (
        <div className={"syrix-hud-app-control-status " + controlStatus} aria-live="polite">
          {controlStatusText}
        </div>
      )}
      {app.vision && (
        <div className="syrix-hud-app-vision-indicator">
          <div className="dot"></div>
          <span>HUD VISION · LIVE</span>
        </div>
      )}
      {mode !== 'fullscreen' && (
        <div className="syrix-hud-app-resize-handle" onMouseDown={(e) => startDrag('resize', e)} />
      )}
    </div>
  );
}

/* Public component. Renders nothing when no apps are open. */
function SyrixHudAppDock({ onAnyOpenChange }) {
  const [apps, setApps] = uHAS([]);
  const [active, setActive] = uHAS(null);
  const [pickerOpen, setPickerOpen] = uHAS(false);
  const [stageState, setStageState] = uHAS(() => (window.SyrixStage ? window.SyrixStage.get() : { active: false, stage: { navH: 68 }, freeze: { frozen: false } }));

  uHAE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((s) => {
      setApps(Array.isArray(s.syrix_hud_apps) ? s.syrix_hud_apps : []);
      setActive(s.syrix_active_hud_app || null);
    });
  }, []);

  // Stage mode — browser fills the window as a backdrop; frames go chromeless
  // and the freeze snapshot renders here while overlays are open above it.
  uHAE(() => {
    if (!window.SyrixStage) return;
    return window.SyrixStage.subscribe((s) => setStageState(s));
  }, []);
  const stageActive = !!(stageState && stageState.active);

  // The launcher picker must paint above the staged browser → freeze tag.
  uHAE(() => {
    const S = window.SyrixStage;
    if (!S || !stageActive || !pickerOpen) return;
    S.acquireOverlay('app-picker');
    return () => { try { S.releaseOverlay('app-picker'); } catch (_) {} };
  }, [stageActive, pickerOpen]);

  // Notify parent (PrimaryHUD) whenever the open-app count crosses zero so
  // it can drive the compact-orb / clock-hide transitions.
  const hasApp = apps.length > 0;
  uHAE(() => {
    try { onAnyOpenChange && onAnyOpenChange(hasApp); } catch (_) {}
  }, [hasApp, onAnyOpenChange]);

  // Esc closes the picker, F11-like behavior on Esc when fullscreen.
  uHAE(() => {
    function onKey(e) {
      if (e.key === 'Escape') {
        if (pickerOpen) { setPickerOpen(false); return; }
        // Esc in fullscreen restores rather than closes the app.
        const cur = apps.find((a) => a.id === active);
        if (cur && cur.mode === 'fullscreen') {
          dispatch('restore', cur.id);
        }
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [pickerOpen, apps, active]);

  // Click-outside closes the picker.
  uHAE(() => {
    if (!pickerOpen) return;
    function handler(e) {
      if (!e.target.closest || (!e.target.closest('.syrix-hud-app-picker') && !e.target.closest('[data-hud-app-launcher]'))) {
        setPickerOpen(false);
      }
    }
    setTimeout(() => document.addEventListener('click', handler), 10);
    return () => document.removeEventListener('click', handler);
  }, [pickerOpen]);

  function dispatch(action, id) {
    const B = window.SyrixBridge;
    if (!B) return;
    const targetId = id || active || (apps[0] && apps[0].id) || '';
    switch (action) {
      case 'close':      return B.closeHudApp(targetId);
      case 'close_all':  return B.closeAllHudApps && B.closeAllHudApps();
      case 'focus':      return B.focusHudApp ? B.focusHudApp(targetId) : B.restoreHudApp(targetId);
      case 'minimize':   return B.minimizeHudApp(targetId);
      case 'restore':    return B.restoreHudApp(targetId);
      case 'fullscreen': return B.fullscreenHudApp(targetId);
      case 'refresh':    return B.refreshHudApp(targetId);
      case 'back':       return B.hudAppBack(targetId);
      case 'forward':    return B.hudAppForward(targetId);
      case 'popout':     return B.popoutHudApp(targetId);
      case 'bigger': {
        const cur = apps.find((a) => a.id === targetId);
        const b = (cur && cur.bounds) || null;
        const w = Math.round(((b && b.width)  || 720) * 1.15);
        const h = Math.round(((b && b.height) || 480) * 1.15);
        return B.resizeHudApp(targetId, { width: w, height: h, x: b && b.x, y: b && b.y });
      }
      case 'smaller': {
        const cur = apps.find((a) => a.id === targetId);
        const b = (cur && cur.bounds) || null;
        const w = Math.round(((b && b.width)  || 720) * 0.85);
        const h = Math.round(((b && b.height) || 480) * 0.85);
        return B.resizeHudApp(targetId, { width: w, height: h, x: b && b.x, y: b && b.y });
      }
      case 'vision_toggle': {
        const cur = apps.find((a) => a.id === targetId);
        return B.setHudAppVision(targetId, !(cur && cur.vision));
      }
      default: return;
    }
  }

  function pick(target) {
    const B = window.SyrixBridge;
    if (B && B.openAppInHud) B.openAppInHud(target, { source: 'user_click' });
    setPickerOpen(false);
  }

  // Voice / Python dispatch — extra public hooks so PrimaryHUD voice
  // routes (and Python's hud_state.json messages) can drive the dock.
  uHAE(() => {
    window.SyrixHudAppDispatch = (action, target) => {
      if (action === 'open' && target) {
        const B = window.SyrixBridge;
        return B && B.openAppInHud && B.openAppInHud(target, { source: 'voice_command' });
      }
      return dispatch(action, null);
    };
    return () => { try { delete window.SyrixHudAppDispatch; } catch (_) {} };
  }, [apps, active]);

  const activeApp = apps.find((a) => a.id === active) || null;
  const minimized = apps.filter((a) => a.mode === 'minimized');
  const visible = activeApp && activeApp.mode !== 'minimized' ? [activeApp] : [];
  const fullscreenApp = activeApp && activeApp.mode === 'fullscreen' ? activeApp : null;
  const controlProblem = apps.find((a) => {
    const st = String((a && a.controlsStatus) || '');
    return st === 'retrying' || st === 'failed';
  }) || null;
  const controlProblemStatus = String((controlProblem && controlProblem.controlsStatus) || '');
  const controlProblemText = String(
    (controlProblem && controlProblem.controlsDetail)
    || (controlProblemStatus === 'failed' ? 'controls unavailable' : 'controls retrying')
  );

  return (
    <>
      <style>{hudDockCSS}</style>
      {controlProblem && (
        <div className={"syrix-hud-app-toast " + controlProblemStatus} aria-live="polite">
          {controlProblemText}
        </div>
      )}
      {/* Freeze backdrop — pixel-identical snapshot swapped in while an HTML
          overlay (folder / widget / menu) needs to paint above the staged
          browser. The native view is parked offscreen for the duration. */}
      {stageActive && stageState.freeze && stageState.freeze.frozen && (() => {
        const fb = stageState.freeze.bounds;
        const navH = (stageState.stage && stageState.stage.navH) || 68;
        const style = fb && Number.isFinite(fb.x)
          ? { left: fb.x, top: fb.y, width: fb.width, height: fb.height }
          : { left: 0, top: navH, right: 0, bottom: 0 };
        return (
          <div className="syrix-stage-freeze" style={style}>
            {stageState.freeze.snapshot && <img src={stageState.freeze.snapshot} alt="" />}
          </div>
        );
      })()}
      <div className={"syrix-hud-dock-root" + (hasApp ? ' has-app' : '')}>
        {visible.map((app) => (
          <HudAppFrame
            key={app.id}
            app={app}
            tabs={apps}
            isActive={app.id === active}
            focusedFullscreen={!!fullscreenApp && fullscreenApp.id === app.id}
            stage={stageActive}
            onCmd={dispatch}
          />
        ))}
        {/* Minimized apps live in the Dynamic Island tab controller — no bottom chips needed. */}
      </div>
      <HudAppPickerMenu open={pickerOpen} onClose={() => setPickerOpen(false)} onPick={pick} />
      {/* Launcher mount-point exposed for PrimaryHUD top bar button. */}
      <button
        data-hud-app-launcher="1"
        style={{ display: 'none' }}
        onClick={() => setPickerOpen((v) => !v)}
        ref={(el) => { if (el) { window.SyrixHudAppLauncher = () => el.click(); } }}
      />
    </>
  );
}

window.SyrixHudAppDock = SyrixHudAppDock;
