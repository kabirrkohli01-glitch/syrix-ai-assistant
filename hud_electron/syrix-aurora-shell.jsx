/* SYRIX · Aurora Glass Shell — v4 (2026-06-13)
 * The only active HUD shell. Exposes window.SyrixAuroraShell, mounted by primary-app.
 * Primary-app chrome (topbar, rail, chat, dock) is hidden via aurora-glass.css;
 * the Dynamic Island, orb, widget panels and modals show through. */
/* IIFE scope — prevents const hook names colliding with syrix-primary-app.jsx globals */
(() => {
const { useEffect, useState, useRef, useCallback, useMemo } = React;

/* ─── Smooth state changes ────────────────────────────────────────────────
   A change that swaps what fills the screen — a page opening or closing, a
   palette, a chat — runs inside a View Transition: Chromium snapshots the
   old frame and crossfades it into the new one, which also carries the
   gradients and custom properties CSS transitions cannot animate. flushSync
   makes React paint the new state inside the callback, where the transition
   expects it. Falls back to a plain update without the API or with reduced
   motion. */
function egSmooth(update) {
  const run = () => {
    try {
      if (typeof ReactDOM !== 'undefined' && ReactDOM.flushSync) ReactDOM.flushSync(update);
      else update();
    } catch (_) { try { update(); } catch (__) {} }
  };
  const reduce = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (!reduce && typeof document.startViewTransition === 'function') {
    try { document.startViewTransition(run); return; } catch (_) {}
  }
  run();
}
window.SyrixSmooth = egSmooth;

/* ─── Bridge ──────────────────────────────────────────────────────────────── */
function useEgBridge() {
  const [snap, setSnap] = useState(() => {
    try { return window.SyrixBridge?.getSnapshot?.() || {}; } catch (_) { return {}; }
  });
  useEffect(() => {
    if (!window.SyrixBridge?.subscribe) return;
    const unsub = window.SyrixBridge.subscribe(s => setSnap(s || {}));
    return () => { try { unsub?.(); } catch (_) {} };
  }, []);
  return snap;
}
function useEgNow() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  return now;
}
function egFmt(d) {
  let h = d.getHours(); const m = String(d.getMinutes()).padStart(2,'0');
  const ampm = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
  const days = ['SUN','MON','TUE','WED','THU','FRI','SAT'];
  const months = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  return { hm: `${h}:${m}`, ampm, date: `${days[d.getDay()]} · ${d.getDate()} ${months[d.getMonth()]}` };
}
// Enter sends unless Settings → General switched sending to Ctrl+Enter.
function egShouldSend(e) {
  if (window.SyrixPrefs && window.SyrixPrefs.shouldSend) return window.SyrixPrefs.shouldSend(e);
  return e.key === 'Enter' && !e.shiftKey && !(e.nativeEvent && e.nativeEvent.isComposing);
}

function egNav(text, model, docIds, voiceReply) {
  // model: 'spark' (default baseline — sends nothing extra) | 'zenith'
  // (flagship — the bridge prepends [[SYRIX_MODEL:zenith]] and extends the
  // hard timeout for the longer multi-agent mission) | 'auto' (2026-07-07 —
  // the spine picks spark/zenith per turn; the bridge sends its marker).
  // docIds: parsed-attachment ids to ground this turn on (Document Intelligence).
  // Returns the bridge promise (promise.requestId = this turn's rid) so the
  // shell can scope its thinking panel / token stream to exactly this turn.
  const opts = { text, mode: 'chat', model: (model === 'zenith' || model === 'auto') ? model : 'spark' };
  if (Array.isArray(docIds) && docIds.length) opts.docIds = docIds;
  if (voiceReply) opts.voiceReply = true;
  try { return window.SyrixBridge?.chat?.(opts); } catch (_) { return null; }
}
function egIsUser(msg) {
  const r = String(msg?.role || msg?.speaker || '').toLowerCase();
  return r === 'user' || r === 'human';
}

// Chat mailboxes have used both numeric seconds and numeric milliseconds, and
// some older lines also carry an ISO `ts` plus a numeric `ts_epoch`. Keep all
// turn/session comparisons on one stable seconds value.
function egLineTsSeconds(msg) {
  const raw = Number(msg?.ts || 0) || Number(msg?.ts_epoch || 0);
  return raw > 1e12 ? raw / 1000 : raw;
}

/* 2026-07-10 — does this chat line answer our pending typed turn?
 * True when the assistant line carries OUR rid, or when it carries NO rid at
 * all but landed after we sent. Some backend delivery paths (legacy router /
 * brain-fallback) append the final assistant line without stamping
 * request_id; before this fallback `working` stayed true after such an
 * answer, so the thinking panel kept rendering BELOW the delivered message
 * until the 90s hard timeout. Lines stamped with a DIFFERENT rid (voice
 * turns, another engine's answer) still never end our turn. */
function egLineAnswersTurn(m, turn) {
  if (!turn || !m || egIsUser(m)) return false;
  const mrid = String(m.request_id || m.hud_chat_request_id || '');
  if (turn.rid && mrid === turn.rid) return true;
  if (mrid) return false;
  const ts = egLineTsSeconds(m);
  return ts >= (Number(turn.sentTs || 0) - 1);
}

/* Small bubble timestamp (2026-07-09, Voice Mode full-fix pass) — reuses
 * egFmt's H:MM formatting rather than duplicating it; egFmt itself returns
 * an object shaped for the top clock, so this just picks the .hm field. */
function egBubbleTs(raw) {
  const n = Number(raw || 0);
  if (!n) return '';
  const ms = n > 1e12 ? n : n * 1000; // ts_epoch may be ms already, ts is seconds
  try { return egFmt(new Date(ms)).hm; } catch (_) { return ''; }
}

/* Stable per-message React key so list mutations (in-place upgrade/dedupe,
 * filler insert, deep patch) never remount rows — index keys replayed egMsgIn
 * and leaked per-row state (inspect open, vote, typewriter) across restores.
 * (rid, side) is unique because a turn's user + assistant share a request_id. */
function egMsgKey(m, i) {
  const rid = String((m && (m.request_id || m.hud_chat_request_id)) || '');
  if (rid) return rid + (egIsUser(m) ? ':u' : ':a');
  const ts = String((m && (m.ts || m.ts_epoch)) || '');
  return ts ? 't' + ts : 'i' + i;
}

/* Marker for "live lines that arrive AFTER a session is reopened". Timestamp-
 * based, not an absolute index, so it survives the bridge capping _chatLines to
 * the last 160 (which shifted every index and dropped a reopened chat's live
 * follow-ups). SYRIX backend + HUD share one clock (same machine). */
function egLiveSinceMarker(base) {
  let mx = 0;
  for (const m of (Array.isArray(base) ? base : [])) {
    const t = egLineTsSeconds(m);
    if (t > mx) mx = t;
  }
  return Math.max(mx, Date.now() / 1000);
}

/* Who the HUD belongs to. One source of truth for the launch greeting and for
   the Dynamic Island's name mode, overridable without a rebuild so the HUD can
   be handed to someone else. */
const EG_OWNER_DEFAULT = 'Kabirr Kohli';
function egOwnerName() {
  try {
    const v = String(localStorage.getItem('syrix_owner_name') || '').trim();
    if (v) return v;
  } catch (_) {}
  return EG_OWNER_DEFAULT;
}
function egOwnerFirstName() {
  return egOwnerName().split(/\s+/)[0] || '';
}
// Published so the Dynamic Island (a separate React root, loaded after this
// file) reads the same name the greeting does instead of keeping its own copy.
window.SyrixOwner = { name: egOwnerName, firstName: egOwnerFirstName };

/* Stage-mode overlay hook — while `on`, holds a SyrixStage overlay tag so the
 * staged BrowserView is frozen (snapshot swap) and this panel paints above
 * web content. No-op when stage mode is off. */
function egUseStageOverlay(tag, on) {
  useEffect(() => {
    const S = window.SyrixStage;
    if (!S || !on) return;
    S.acquireOverlay(tag);
    return () => { try { S.releaseOverlay(tag); } catch (_) {} };
  }, [tag, on]);
}

/* ─── Icons ───────────────────────────────────────────────────────────────── */
const EgI = {
  chat:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M4 5h16v11H8l-4 4z"/></svg>,
  history:   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3.5 12a8.5 8.5 0 1 1 2.6 6.1"/><polyline points="3,15 6,18 9.2,15.4"/><polyline points="12,7 12,12.5 16,14.5"/></svg>,
  browser:   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3.5 12h17M12 3.2c2.6 2.8 2.6 14.8 0 17.6M12 3.2c-2.6 2.8-2.6 14.8 0 17.6"/></svg>,
  charts:    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="3.5" y="4" width="17" height="16" rx="2"/><path d="M7.5 16V12M12 16V8M16.5 16v-6"/></svg>,
  notes:     <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="14 3 14 9 20 9"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="12" y2="17"/></svg>,
  memory:    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3a3 3 0 0 0-3 3v1a3 3 0 0 0-3 3v1a3 3 0 0 0 0 6v1a3 3 0 0 0 3 3 3 3 0 0 0 3-3v-3"/><path d="M12 3a3 3 0 0 1 3 3v1a3 3 0 0 1 3 3v1a3 3 0 0 1 0 6v1a3 3 0 0 1-3 3 3 3 0 0 1-3-3v-3"/></svg>,
  settings:  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 12a7.4 7.4 0 0 0-.1-1.2l2.1-1.6-2-3.5-2.5 1a7.5 7.5 0 0 0-2-1.2l-.4-2.6h-4l-.4 2.6a7.5 7.5 0 0 0-2 1.2l-2.5-1-2 3.5 2.1 1.6a7.4 7.4 0 0 0 0 2.4l-2.1 1.6 2 3.5 2.5-1c.6.5 1.3.9 2 1.2l.4 2.6h4l.4-2.6a7.5 7.5 0 0 0 2-1.2l2.5 1 2-3.5-2.1-1.6c.1-.4.1-.8.1-1.2z"/></svg>,
  shield:    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l8 3v6c0 4.6-3.2 8.4-8 9.6C7.2 20.4 4 16.6 4 12V6l8-3z"/></svg>,
  plug:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M9 7V3M15 7V3"/><path d="M6 7h12v4a6 6 0 0 1-6 6 6 6 0 0 1-6-6z"/><path d="M12 17v4"/></svg>,
  mic:       <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11A6.5 6.5 0 0 0 12 17.5 6.5 6.5 0 0 0 18.5 11M12 17.5V21"/></svg>,
  micOff:    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9.34V4a3 3 0 0 0-5.94-.6M17 16.95A7 7 0 0 1 5 12v-2m14 0v2a7 7 0 0 1-.11 1.23M12 19v3"/></svg>,
  volOff:    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>,
  vol:       <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>,
  send:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="19" x2="12" y2="6"/><polyline points="6 11 12 5 18 11"/></svg>,
  check:     <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><polyline points="4 12.5 9.5 18 20 6"/></svg>,
  plus:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>,
  minus:     <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M5 12h14"/></svg>,
  close:     <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M6 6l12 12M18 6L6 18"/></svg>,
  caret:     <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M7 10l5 5 5-5"/></svg>,
  brain:     <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5V20M12 4.5A2.5 2.5 0 0 1 14.5 2M12 20v-2m0 2a2.5 2.5 0 0 0 2.5 2.5M12 20a2.5 2.5 0 0 1-2.5 2.5M5 9a3 3 0 0 0 3 3M5 9a3 3 0 0 1 3-3M19 9a3 3 0 0 1-3 3M19 9a3 3 0 0 0-3-3"/></svg>,
  sparkle:   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/></svg>,
  clip:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M21 11.5l-9 9a5 5 0 0 1-7-7l9-9a3.5 3.5 0 0 1 5 5l-9 9a2 2 0 0 1-3-3l8-8"/></svg>,
  thumbUp:   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/><path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>,
  thumbDown: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z"/><path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg>,
  back:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M15 18l-6-6 6-6"/></svg>,
  forward:   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18l6-6-6-6"/></svg>,
  refresh:   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>,
  external:  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>,
  sun:       <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.2 5.2l1.4 1.4M17.4 17.4l1.4 1.4M5.2 18.8l1.4-1.4M17.4 6.6l1.4-1.4"/></svg>,
  stop:      <svg viewBox="0 0 24 24" fill="currentColor" width="11" height="11"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>,
  wave:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12h2M21 12h-2M7 7v10M11 4v16M15 7v10M19 9v6"/></svg>,
  moon:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>,
  map:       <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21"/><line x1="9" y1="3" x2="9" y2="18"/><line x1="15" y1="6" x2="15" y2="21"/></svg>,
  globe:     <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>,
  trading:   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>,
  news:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="3.5" y="4.5" width="17" height="15" rx="2"/><path d="M7 9h10M7 13h10M7 17h6"/></svg>,
  deck:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round"><rect x="2.5" y="5" width="19" height="14" rx="3"/><rect x="5.5" y="8" width="3.4" height="3.4" rx="0.9"/><rect x="10.3" y="8" width="3.4" height="3.4" rx="0.9"/><rect x="15.1" y="8" width="3.4" height="3.4" rx="0.9"/><rect x="5.5" y="12.6" width="3.4" height="3.4" rx="0.9"/><rect x="10.3" y="12.6" width="3.4" height="3.4" rx="0.9"/><rect x="15.1" y="12.6" width="3.4" height="3.4" rx="0.9"/></svg>,
  apps:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round"><rect x="4" y="4" width="6" height="6" rx="1.2"/><rect x="14" y="4" width="6" height="6" rx="1.2"/><rect x="4" y="14" width="6" height="6" rx="1.2"/><rect x="14" y="14" width="6" height="6" rx="1.2"/></svg>,
  diag:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>,
  terminal:  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/></svg>,
  weather:   <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9z"/></svg>,
  search:    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.2" y2="16.2"/></svg>,
  cloud:     <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M6.5 19a4.5 4.5 0 0 1-.5-8.97 5.5 5.5 0 0 1 10.6-1.9A4 4 0 0 1 17.5 16H6.5z"/></svg>,
  pin:       <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2.5c-2.8 0-5 2.2-5 5 0 3.4 3.4 6.4 4.3 8.9.2.6 1.2.6 1.4 0 .9-2.5 4.3-5.5 4.3-8.9 0-2.8-2.2-5-5-5z"/><circle cx="12" cy="7.5" r="1.8"/><line x1="12" y1="18.5" x2="12" y2="21.5"/></svg>,
  pinFill:   <svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2.5c-2.8 0-5 2.2-5 5 0 3.4 3.4 6.4 4.3 8.9.2.6 1.2.6 1.4 0 .9-2.5 4.3-5.5 4.3-8.9 0-2.8-2.2-5-5-5z"/><line x1="12" y1="18.5" x2="12" y2="21.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg>,
  more:      <svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></svg>,
  edit:      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M4 20h4l10.5-10.5a2.1 2.1 0 0 0 0-3L17.5 5.5a2.1 2.1 0 0 0-3 0L4 16v4z"/><line x1="14" y1="6.5" x2="17.5" y2="10"/></svg>,
};

/* ─── Typewriter hook ─────────────────────────────────────────────────────── */
function useTypewriter(text, speed) {
  // Reveals an answer that did not stream live: frame-paced, stepping to word
  // boundaries, and finished within about a second and a quarter however long
  // the answer is — a fixed characters-per-tick crawl made long answers slow
  // and short ones jittery.
  const [n, setN] = useState(text ? text.length : 0);
  const frame = useRef(0);
  useEffect(() => {
    if (frame.current) { cancelAnimationFrame(frame.current); frame.current = 0; }
    if (!text) { setN(0); return undefined; }
    setN(0);
    const per = Math.max(3, Math.ceil(text.length / 75));
    let i = 0;
    const step = () => {
      let next = i + per;
      if (next < text.length) {
        const space = text.indexOf(' ', next);
        if (space !== -1 && space - next < 10) next = space + 1;
      }
      i = Math.min(text.length, next);
      setN(i);
      if (i < text.length) frame.current = requestAnimationFrame(step);
      else frame.current = 0;
    };
    frame.current = requestAnimationFrame(step);
    return () => { if (frame.current) { cancelAnimationFrame(frame.current); frame.current = 0; } };
  }, [text]);
  return n;
}

/* ─── Mic wave bars ───────────────────────────────────────────────────────── */
function EgMicWave({ active }) {
  const h = active ? [5,12,8,16,10,6,14,11,7] : [3,5,4,6,4,3,5,4,3];
  return (
    <div className={"eg-mic-wave" + (active ? " is-active" : "")} aria-hidden="true">
      {h.map((v, i) => <span key={i} style={{ height: v + 'px' }} />)}
    </div>
  );
}

/* ─── Thinking dots ───────────────────────────────────────────────────────── */
function EgDots() {
  return null;
}

/* ─── Search trace banner ─────────────────────────────────────────────────── */
function EgTrace({ trace }) {
  if (!trace || trace.step === 'done') return null;
  const synth = trace.step === 'synthesizing';
  return (
    <div className={"eg-trace-bar" + (synth ? ' is-synth' : '')}>
      <span className="eg-trace-spin" />
      {trace.step === 'searching' && <span className="eg-trace-txt">Searching — <em>{trace.query || '…'}</em></span>}
      {trace.step === 'sources' && (
        <div className="eg-trace-srcs">
          <span className="eg-trace-txt">Sources</span>
          {(trace.titles || []).slice(0, 3).map((t, i) => (
            <span key={i} className="eg-trace-chip">{String(t).slice(0, 36)}</span>
          ))}
        </div>
      )}
      {synth && <span className="eg-trace-txt">Synthesising…</span>}
    </div>
  );
}

/* ─── Scroll rail ────────────────────────────────────────────────────────────
   A real scrollbar on the right rim of the conversation, drawn as a tick
   scale with a thumb riding over it.

   It is a scrollbar, not an ornament: the thumb can be dragged, the track can
   be clicked to page, and each accent mark is a measured user turn that
   scrolls to that question. The scale exists so the thumb has something to
   read against — a bare thumb on a bare track gives no sense of how much
   transcript there is.

   Position is written as two custom properties straight onto the rail inside
   the scroll handler. Putting it through React state instead would re-render
   the whole transcript on every scroll event, which is the sort of thing that
   makes a long chat feel cheap.

   An earlier version drew a second, brighter copy of the scale and clipped it
   to the viewport window. Do not go back to that: the clip lands mid-tick, so
   the band ends in half-drawn marks that read as a rendering fault. */
const EG_TICK_COUNT = 44;
const EG_THUMB_MIN_PCT = 7;

function EgScrollRail({ scrollRef, revision }) {
  const railRef = useRef(null);
  const trackRef = useRef(null);
  const [marks, setMarks] = useState([]);
  const dragRef = useRef(null);
  const idleTimer = useRef(null);

  const sync = useCallback(() => {
    const el = scrollRef && scrollRef.current;
    const rail = railRef.current;
    if (!el || !rail) return;
    const span = el.scrollHeight - el.clientHeight;
    if (span <= 24) {
      // Everything fits. The thumb covers the whole track, which is the
      // truth, and the rail dims rather than disappearing so its position
      // stays learnable before the transcript is long enough to need it.
      rail.classList.add('is-inert');
      rail.style.setProperty('--eg-thumb-top', '0%');
      rail.style.setProperty('--eg-thumb-h', '100%');
      return;
    }
    rail.classList.remove('is-inert');
    const h = Math.max(EG_THUMB_MIN_PCT, (el.clientHeight / el.scrollHeight) * 100);
    const top = (el.scrollTop / span) * (100 - h);
    rail.style.setProperty('--eg-thumb-top', top.toFixed(3) + '%');
    rail.style.setProperty('--eg-thumb-h', h.toFixed(3) + '%');
  }, [scrollRef]);

  // Turn markers — measured from the DOM, never estimated from message counts.
  const measure = useCallback(() => {
    const el = scrollRef && scrollRef.current;
    if (!el) { setMarks([]); return; }
    const total = el.scrollHeight;
    if (total <= el.clientHeight + 24) { setMarks([]); return; }
    const base = el.getBoundingClientRect().top - el.scrollTop;
    const out = [];
    try {
      el.querySelectorAll('.eg-msg-user').forEach((row, i) => {
        const top = row.getBoundingClientRect().top - base;
        out.push({ key: i, pct: Math.min(99, Math.max(0.5, (top / total) * 100)), top: top });
      });
    } catch (_) {}
    setMarks(out);
  }, [scrollRef]);

  function flashLive() {
    const rail = railRef.current;
    if (!rail) return;
    rail.classList.add('is-live');
    if (idleTimer.current) clearTimeout(idleTimer.current);
    idleTimer.current = setTimeout(() => {
      if (railRef.current && !dragRef.current) railRef.current.classList.remove('is-live');
    }, 1100);
  }

  useEffect(() => {
    const el = scrollRef && scrollRef.current;
    if (!el) return undefined;
    function onScroll() { sync(); flashLive(); }
    el.addEventListener('scroll', onScroll, { passive: true });
    let ro = null;
    try {
      ro = new ResizeObserver(() => { sync(); measure(); });
      ro.observe(el);
    } catch (_) {}
    sync();
    measure();
    return () => {
      el.removeEventListener('scroll', onScroll);
      try { ro && ro.disconnect(); } catch (_) {}
      if (idleTimer.current) clearTimeout(idleTimer.current);
    };
  }, [scrollRef, sync, measure]);

  // A settle delay lets an answer finish laying out before its mark is
  // measured; without it the mark lands at the pre-render height of its turn.
  useEffect(() => {
    const t = setTimeout(() => { sync(); measure(); }, 280);
    return () => clearTimeout(t);
  }, [revision, sync, measure]);

  const ticks = useMemo(() => Array.from({ length: EG_TICK_COUNT }, (_, i) => i), []);

  function scrollToFraction(f) {
    const el = scrollRef && scrollRef.current;
    if (!el) return;
    const span = el.scrollHeight - el.clientHeight;
    el.scrollTop = Math.max(0, Math.min(span, f * span));
  }

  // Dragging the thumb. Pointer capture keeps the gesture alive when the
  // cursor leaves the 16px rail, which it will on any real drag.
  function onThumbDown(e) {
    const el = scrollRef && scrollRef.current;
    const track = trackRef.current;
    if (!el || !track) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = track.getBoundingClientRect();
    const thumbH = (el.clientHeight / el.scrollHeight) * rect.height;
    const thumbTop = (el.scrollTop / (el.scrollHeight - el.clientHeight)) * (rect.height - thumbH);
    dragRef.current = { grabOffset: e.clientY - (rect.top + thumbTop), rect: rect, thumbH: thumbH };
    railRef.current && railRef.current.classList.add('is-live', 'is-dragging');
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch (_) {}
  }
  function onThumbMove(e) {
    const d = dragRef.current;
    if (!d) return;
    const travel = d.rect.height - d.thumbH;
    if (travel <= 0) return;
    const y = e.clientY - d.grabOffset - d.rect.top;
    scrollToFraction(Math.max(0, Math.min(1, y / travel)));
  }
  function onThumbUp(e) {
    if (!dragRef.current) return;
    dragRef.current = null;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch (_) {}
    if (railRef.current) railRef.current.classList.remove('is-dragging');
    flashLive();
  }

  // Clicking the track pages towards the click, the way a scrollbar does.
  function onTrackDown(e) {
    const el = scrollRef && scrollRef.current;
    const track = trackRef.current;
    if (!el || !track) return;
    const rect = track.getBoundingClientRect();
    const at = (e.clientY - rect.top) / rect.height;
    const cur = el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight);
    const page = el.clientHeight * 0.9 * (at > cur ? 1 : -1);
    try { el.scrollTo({ top: el.scrollTop + page, behavior: 'smooth' }); }
    catch (_) { el.scrollTop += page; }
  }

  function jumpTo(mark, e) {
    e.stopPropagation();
    const el = scrollRef && scrollRef.current;
    if (!el) return;
    try { el.scrollTo({ top: Math.max(0, mark.top - 28), behavior: 'smooth' }); }
    catch (_) { el.scrollTop = Math.max(0, mark.top - 28); }
  }

  return (
    <div className="eg-rail-scroll" ref={railRef} aria-hidden="true">
      <div className="eg-rail-track" ref={trackRef} onPointerDown={onTrackDown}>
        <div className="eg-rail-scale">
          {ticks.map(i => <i key={i} className="eg-rail-tick" />)}
        </div>
        {marks.map(m => (
          <button
            key={m.key}
            type="button"
            className="eg-rail-turn"
            style={{ top: m.pct + '%' }}
            tabIndex={-1}
            title="Jump to this question"
            onPointerDown={e => e.stopPropagation()}
            onClick={e => jumpTo(m, e)}
          />
        ))}
        <div
          className="eg-rail-thumb"
          onPointerDown={onThumbDown}
          onPointerMove={onThumbMove}
          onPointerUp={onThumbUp}
          onPointerCancel={onThumbUp}
        />
      </div>
    </div>
  );
}

/* ─── Cursor glow ─────────────────────────────────────────────────────────── */
function EgCursorGlow() {
  useEffect(() => {
    const move = e => {
      document.documentElement.style.setProperty('--eg-mx', e.clientX + 'px');
      document.documentElement.style.setProperty('--eg-my', e.clientY + 'px');
    };
    document.addEventListener('mousemove', move, { passive: true });
    return () => document.removeEventListener('mousemove', move);
  }, []);
  return <div className="eg-cursor-glow" aria-hidden="true" />;
}

/* 2026-07-02 — source-pill helpers: favicon, domain, "2 min ago" stamps. */
function egDomainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); }
  catch (_) { return url ? String(url).slice(0, 24) : ''; }
}
function egFaviconFor(url) {
  try { return 'https://icons.duckduckgo.com/ip3/' + new URL(url).hostname + '.ico'; }
  catch (_) { return ''; }
}
function egRelTime(iso) {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 45) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago';
  if (s < 86400 * 7) return Math.round(s / 86400) + ' d ago';
  try { return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }); }
  catch (_) { return ''; }
}
/* Perplexity-style pills under live answers — only when the message carries
   envelope sources, so plain chat stays clean. Detail lives in ⓘ inspect. */
function EgSourcePills({ sources }) {
  const list = (Array.isArray(sources) ? sources : []).filter(s => s && (s.url || s.name)).slice(0, 5);
  if (!list.length) return null;
  return (
    <div className="eg-src-pills">
      {list.map((s, i) => {
        const dom = egDomainOf(s.url);
        const fav = s.url ? egFaviconFor(s.url) : '';
        const stamp = egRelTime(s.published || s.retrieved_at);
        const tip = String(s.name || dom || 'source')
          + (s.published ? '\nPublished: ' + s.published : '')
          + (s.retrieved_at ? '\nRetrieved: ' + s.retrieved_at : '')
          + (s.provider ? '\nVia: ' + s.provider : '');
        return (
          <button key={i} className="eg-src-pill" title={tip}
            onClick={ev => { ev.stopPropagation(); try { window.SyrixBridge?.send?.('open_url', { data: { url: s.url || '' } }); } catch (_) {} }}>
            {fav && <img src={fav} alt=""
                         onError={(e) => { try { e.target.style.display = 'none'; } catch (_) {} }} />}
            {(dom || String(s.name || 'source').slice(0, 22)) + (stamp ? ' · ' + stamp : '')}
          </button>
        );
      })}
    </div>
  );
}

/* ─── Deep analysis section (ATLAS 2026-07-03) ───────────────────────────────
   The Zenith deep pass always runs in the background for informational turns
   and attaches to the SAME chat line (line.deep — the bridge patches it in
   place). The SPARK/ZENITH switcher is a VIEW preference: ZENITH position
   auto-expands this section; execution is identical either way. */
function EgDeepAnalysis({ deep }) {
  const status = String((deep && deep.status) || 'pending');
  const [open, setOpen] = useState(() => {
    try {
      if (String((deep && deep.view_hint) || '') === 'expand') return true;
      return String(localStorage.getItem('syrix_chat_model_v1') || '').toLowerCase() === 'zenith';
    } catch (_) { return false; }
  });
  if (!deep || typeof deep !== 'object') return null;
  // Presentation V2 (2026-07-05): the chat header carries NO metadata — no
  // source counts, no confidence. That evidence lives in the inspect panel.
  const headline = status === 'pending' ? 'Zenith · deep analysis running…'
    : status === 'ready' ? 'Zenith · Deep Analysis'
    : status === 'insufficient_sources' ? 'Zenith · not enough verified sources'
    : 'Zenith · deep analysis unavailable';
  return (
    <div className={'eg-deep-card status-' + status}
         style={{ marginTop: 8, borderRadius: 12, border: '1px solid rgba(233,201,140,0.22)',
                  background: 'rgba(233,201,140,0.05)', overflow: 'hidden' }}>
      <div onClick={ev => { ev.stopPropagation(); setOpen(v => !v); }}
           style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 11px',
                    cursor: 'pointer', userSelect: 'none', fontSize: 12, opacity: 0.95 }}
           title={open ? 'Collapse deep analysis' : 'Expand deep analysis'}>
        <span style={{ color: '#e9c98c' }}>◈</span>
        <span key={headline}>{headline}</span>
        {status === 'pending' && <span className="eg-think-dots" aria-hidden="true"><i /><i /><i /></span>}
        <span style={{ marginLeft: 'auto', opacity: 0.7 }}>{open ? '▾' : '▸'}</span>
      </div>
      {open && (
        <div style={{ padding: '2px 12px 10px', fontSize: 13 }} onClick={ev => ev.stopPropagation()}>
          {status === 'pending' && (
            /* The same ladder as every other mode. This was the second
               mount of the violet sub-agent panel; one thinking surface
               means one, including here. */
            typeof window !== 'undefined' && typeof window.SyrixSparkPulse === 'function'
              ? React.createElement(window.SyrixSparkPulse, {
                  working: true,
                  turnId: String(deep.request_id || deep.ts || 'deep'),
                })
              : <div style={{ opacity: 0.75 }}>Reading full articles and cross-checking claims…</div>
          )}
          {status === 'ready' && (
            /* Presentation V2: rich renderer, and NO source pills in chat —
               deep sources surface through the message's inspect panel. */
            typeof window !== 'undefined' && typeof window.SyrixAnswerRenderer === 'function'
              ? React.createElement(window.SyrixAnswerRenderer, { text: String(deep.text || '') })
              : <div style={{ whiteSpace: 'pre-wrap' }}>{String(deep.text || '')}</div>
          )}
          {(status === 'insufficient_sources' || status === 'failed') && (
            <div style={{ opacity: 0.8 }}>
              {String(deep.text || 'Deep analysis could not complete — the fast answer above stands.')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Single message with optional typewriter reveal ─────────────────────── */
function EgChatMessage({ msg, isLatestAI, zenith }) {
  const text = String(msg?.text || msg?.message || msg?.content || '');
  const isUser = egIsUser(msg);
  const rawBrainLabel = msg?.label || msg?.brain || msg?.route_label || '';
  const modelLabel = msg?.model || msg?.model_used || '';
  // Show the exact brain/model, not just the routing label.
  const brainDisplay = modelLabel
    ? (rawBrainLabel ? rawBrainLabel + ' · ' + modelLabel : modelLabel)
    : (rawBrainLabel || '');
  const brainLabel = brainDisplay; // keep for backward-compat with inspector fallback
  const sources = Array.isArray(msg?.sources) ? msg.sources : [];
  const [expanded, setExpanded] = useState(false);
  const [voted, setVoted] = useState(0);
  const [copied, setCopied] = useState(false);

  // 2026-07-02 — the typewriter is a FALLBACK only: replies the user already
  // watched stream in token-by-token (chat_token path) render instantly
  // instead of replaying a fake animation on the finished text.
  // 2026-07-06 — prefer an exact rid match (set by the bridge the moment a
  // real token arrives) over the old fuzzy text-prefix match. Post-stream
  // formatting/cleanup on the saved line can shift the first ~48 chars just
  // enough to break the prefix match, which replayed the typewriter on an
  // answer the user had already watched stream in.
  const liveStreamed = (() => {
    try {
      const rid = String(msg?.request_id || msg?.hud_chat_request_id || '').trim();
      if (rid && typeof window !== 'undefined' && window.__syrixStreamedRidSeen
          && window.__syrixStreamedRidSeen.has(rid)) return true;
      const p = String((typeof window !== 'undefined' && window.__syrixLastStreamText) || '');
      return !isUser && p.length > 24 && text.startsWith(p.slice(0, 48));
    } catch (_) { return false; }
  })();
  // 2026-07-06 — ZENITH turns render the deep analysis as THE answer, not a
  // preview: the fast draft is an internal detail the backend always
  // produces, not a second visible answer that gets swapped out. Hidden
  // while the deep pass is pending/ready; still shown as the fallback if the
  // deep pass comes back insufficient/failed, matching what EgDeepAnalysis's
  // own failure copy ("the fast answer above stands") already assumes.
  const deepStatus = msg?.deep ? String(msg.deep.status || 'pending') : '';
  // 2026-07-07 — hide the fast draft ONLY once the deep answer is actually
  // ready. While the deep pass is still pending the fast "quick take" stays
  // visible (with the deep "analysing…" card below it), so a Zenith turn never
  // blanks the answer and re-shows it — the reported disappear/reappear.
  const hideFastAnswer = !isUser && zenith && !!msg?.deep && deepStatus === 'ready';
  const shouldTypewriter = !hideFastAnswer && isLatestAI && !isUser && !liveStreamed;
  const typeLen = useTypewriter(shouldTypewriter ? text : null);
  // A streamed answer carries on from where the live stream had reached when
  // the finished line landed, instead of snapping to its full length — the
  // snap was the visible "text, then the answer changes" jolt.
  const [contFrom] = useState(() => {
    try {
      const c = window.__syrixStreamShown;
      const rid = String(msg?.request_id || msg?.hud_chat_request_id || '');
      return (!isUser && liveStreamed && isLatestAI && c && rid && c.rid === rid && c.len < text.length) ? c.len : -1;
    } catch (_) { return -1; }
  });
  const contText = useEgSmoothText(text, contFrom >= 0, contFrom);
  const display = shouldTypewriter ? text.slice(0, typeLen) : (contFrom >= 0 ? contText : text);
  const animating = (shouldTypewriter && typeLen < text.length)
    || (contFrom >= 0 && contText.length < text.length);

  if (!text) return null;

  function castVote(v) {
    if (voted) return;
    setVoted(v);
    try {
      window.SyrixBridge?.send?.('feedback_message', {
        data: { vote: v, response: text },
      });
    } catch (_) {}
  }

  // 2026-07-11 — answer copy moved here from the renderer's corner overlay:
  // one Copy button in the action row next to ⓘ inspect, hover-only like the
  // rest of the row. Copies the visible answer (the deep text when Zenith's
  // deep card IS the answer).
  function copyAnswer() {
    const body = hideFastAnswer ? String(msg?.deep?.text || text) : text;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(body);
      } else {
        const ta = document.createElement('textarea');
        ta.value = body; document.body.appendChild(ta);
        ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch (_) {}
  }

  // An answer that was streamed takes over from the live bubble in place: it
  // must not play the entrance animation again over text already on screen —
  // that replay was the "answer fades in a second time" at the end of a turn.
  return (
    <div className={"eg-msg" + (isUser ? " eg-msg-user" : " eg-msg-ai") + (!isUser && liveStreamed ? " is-continued" : "")}>
      <div className="eg-msg-content">
        {/* Attachments ride with the sent message (2026-07-16): the backend
            stamps [{name, kind, subtype}] on the user line; the optimistic
            pending bubble carries the same summaries so the chips appear the
            moment the message is sent. Chips are chrome — excluded from text
            selection via .eg-msg-atts. */}
        {isUser && Array.isArray(msg?.attachments) && msg.attachments.length > 0 && (
          <div className="eg-msg-atts" aria-label="Files attached to this message">
            {msg.attachments.slice(0, 6).map((a, i) => {
              const v = egAttachmentVisual(a);
              const ext = String(a.name || '').includes('.') ? String(a.name).split('.').pop().toUpperCase() : '';
              return (
                <span key={i} className={"eg-att is-file is-sent tone-" + v.tone} title={a.name}>
                  <span className="eg-att-mark"><EgFileIcon tone={v.tone} size={32} /></span>
                  <span className="eg-att-text">
                    <span className="eg-att-name">{a.name}</span>
                    <span className="eg-att-sub">{v.tone === 'image' ? 'Image' : (ext || v.label)}</span>
                  </span>
                </span>
              );
            })}
          </div>
        )}
        {/* A data card (a quote, the weather) leads the answer and is drawn
            at once. Held until the text reveal finished, a chart that had
            been ready for seconds appeared only after the last word. */}
        {!isUser && msg?.panel && msg.panel.type && msg.panel.type !== 'media'
          && typeof window !== 'undefined' && typeof window.SyrixAdaptiveCard === 'function'
          && React.createElement(window.SyrixAdaptiveCard, {
               panel: msg.panel,
               timeMs: Number(msg.ts_epoch ? msg.ts_epoch * 1000 : 0),
             })}
        {!isUser && !hideFastAnswer && msg?.panel && msg.panel.media
          && typeof window !== 'undefined' && window.SyrixAnswerMedia
          && React.createElement(window.SyrixAnswerMedia.Images, { images: msg.panel.media.images })}
        <div
          className={(isUser ? "eg-msg-bubble" : "eg-msg-canvas") + (animating ? ' is-animating' : '') + (zenith ? ' is-zenith' : '')}>
          {/* Presentation V2 (2026-07-05): AI answers render through the smart
              renderer (typography, tables, code cards, flows) — raw markdown
              syntax never reaches the user. 2026-07-20 — mid-reveal now uses
              that SAME renderer with streaming:true instead of a plain
              syntax-stripped partial, so structure builds up incrementally and
              the finished answer never visually rebuilds when the reveal ends.
              `stable` suppresses the block entrance animation once a message
              has streamed/typed in so layout-identical blocks don't re-pop. */}
          {/* 2026-07-06 — ZENITH: the fast draft is hidden while the deep card
              is the answer (see hideFastAnswer above), so only one answer
              body is ever visible for a Zenith turn. */}
          {!hideFastAnswer && (
            (!isUser && typeof window !== 'undefined' && typeof window.SyrixAnswerRenderer === 'function')
              ? React.createElement(window.SyrixAnswerRenderer,
                  { text: display, streaming: animating, stable: liveStreamed || shouldTypewriter,
                    sources: animating ? null : sources })
              : (animating && !isUser && typeof window !== 'undefined' && typeof window.SyrixStripStreamMd === 'function'
                  ? <span className="sxr-stream">{window.SyrixStripStreamMd(display)}</span>
                  : display)
          )}
          {!hideFastAnswer && animating && <span className="eg-cursor-blink" />}
        </div>
        {/* Adaptive response component (2026-07-11) — compact inline data card
            (stock / score / weather / products) the backend attaches ONLY when
            the semantic decider judged the user's intent earns it. Part of the
            answer, rendered after the reveal so it never flashes mid-typewriter.
            Unknown/legacy panel types render nothing inside the card component. */}
        {!isUser && !animating && typeof window !== 'undefined' && window.SyrixAnswerMedia && (
          <>
            {msg?.panel && msg.panel.media
              && React.createElement(window.SyrixAnswerMedia.Products, { products: msg.panel.media.products })}
            {React.createElement(window.SyrixAnswerMedia.Sources, { sources })}
            {isLatestAI && React.createElement(window.SyrixAnswerMedia.FollowUps, { questions: msg?.follow_ups })}
          </>
        )}
        {/* Presentation V2: source pills removed from the chat body — evidence
            lives exclusively in the inspect panel (SyrixAnswerTrace). */}
        {/* ATLAS (2026-07-03) — expandable Zenith deep-analysis layer; the
            bridge patches line.deep in place when the background pass lands.
            2026-07-06 — when the fast draft is hidden (hideFastAnswer) this
            IS the answer, so it must not wait on the (irrelevant, hidden)
            typewriter to finish before showing. */}
        {!isUser && msg?.deep && (hideFastAnswer || !animating) && <EgDeepAnalysis deep={msg.deep} />}
        {/* Action row — hover-only (2026-07-11): inspect toggle, vote and copy
            live together; the row stays visible while the inspect panel is
            open or after a vote so the UI never vanishes mid-use. */}
        {!isUser && (
          <div className={"eg-msg-brain-row"
                          + (expanded ? ' is-open' : '')
                          + (voted ? ' has-voted' : '')}
               onClick={() => setExpanded(v => !v)}
               title="Click to see exactly how SYRIX answered">
            <span className="eg-msg-brain-dot" />
            {/* Presentation V2: brain/model detail moved into inspect — the
                chat shows only the product name, not routing metadata. */}
            <span>SYRIX</span>
            <span style={{ opacity: 0.55 }}>·</span>
            <span style={{ opacity: 0.7 }}>{expanded ? '▾ hide' : 'ⓘ inspect'}</span>
            <button className={"eg-vote" + (voted === 1 ? ' is-liked' : '')}
              onClick={ev => { ev.stopPropagation(); castVote(1); }}
              disabled={!!voted} title="Good response">
              {EgI.thumbUp}
            </button>
            <button className={"eg-vote" + (voted === -1 ? ' is-disliked' : '')}
              onClick={ev => { ev.stopPropagation(); castVote(-1); }}
              disabled={!!voted} title="Not helpful">
              {EgI.thumbDown}
            </button>
            <button className={"eg-copy" + (copied ? ' is-copied' : '')}
              onClick={ev => { ev.stopPropagation(); copyAnswer(); }}
              title="Copy answer">
              {copied ? '✓ copied' : 'copy'}
            </button>
          </div>
        )}
        {expanded && !isUser && (
          /* Click-to-inspect: exact brain, route, where it looked, step trace, sources. */
          window.SyrixAnswerTrace
            ? React.createElement(window.SyrixAnswerTrace, { msg: msg })
            : (
              <div className="eg-msg-detail">
                {brainDisplay && (
                  <div className="eg-detail-row">
                    <span className="eg-detail-lbl">Brain</span>
                    <span className="eg-detail-val">{brainDisplay}</span>
                  </div>
                )}
                {sources.length > 0 && (
                  <div className="eg-detail-row eg-detail-srcs">
                    <span className="eg-detail-lbl">Sources</span>
                    {sources.slice(0, 4).map((s, i) => (
                      <button key={i} className="eg-src-link"
                        onClick={ev => { ev.stopPropagation(); try { window.SyrixBridge?.send?.('open_url', { data: { url: s.url || s.href } }); } catch (_) {} }}>
                        {String(s.name || s.title || s.url || 'source').slice(0, 40)}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )
        )}
        {/* 2026-07-11 — the standalone vote row merged into the action row
            above (inspect · vote · copy, hover-only). */}
      </div>
    </div>
  );
}

/* ─── Mode switcher — shared dropdown ────────────────────────────────────────
   One implementation mounted in TWO places (collapsed bottom bar + open panel
   composer) so both share identical behaviour. Each row is a name, a version
   and one line saying what the mode is for; the active chat model carries a
   check. Outside-click / Escape / picking a row closes it. Purely a UI wrapper
   over the model/onModelChange state owned by SyrixAuroraShell.
   Supercomputer is an operating mode rather than a chat model: picking it
   opens the agentic workspace and is never persisted (setModel intercepts it).
   Voice has its own button in the composer and is not listed here.
   The menu is portaled to document.body and positioned from the trigger's
   live bounding rect: the bottom-bar pill is `overflow:hidden`, so a normal
   position:absolute child would silently render invisible there. */
const EG_MODES = [
  { id: 'auto',          name: 'Auto',          ver: '',    desc: 'Picks Spark or Zenith for each question' },
  { id: 'spark',         name: 'Spark',         ver: '1.1', desc: 'Fast answers to everyday questions' },
  { id: 'zenith',        name: 'Zenith',        ver: '1.1', desc: 'Deeper thinking for complex questions' },
  { id: 'supercomputer', name: 'Supercomputer', ver: '',    desc: 'Agentic tasks and automation', mode: true },
];

function EgModelSwitcher({ model, onModelChange, menuAlign = 'left' }) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState(null);
  const btnRef = useRef(null);
  const menuRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    // Opens downward from the switcher. It flips above only when the space
    // below cannot hold it — with the composer docked at the foot of the
    // window — so the list never runs off-screen.
    function place() {
      const el = btnRef.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const menuH = (menuRef.current && menuRef.current.offsetHeight) || 250;
      const below = window.innerHeight - r.bottom - 12;
      const pos = below >= menuH
        ? { top: Math.round(r.bottom + 8), dir: 'down' }
        : { bottom: Math.round(window.innerHeight - r.top + 8), dir: 'up' };
      if (menuAlign === 'right') pos.right = Math.round(window.innerWidth - r.right);
      else pos.left = Math.round(r.left);
      setMenuPos(pos);
    }
    place();
    // The first placement guesses the height; measure the real menu once it
    // has painted and settle the direction from that.
    const raf = requestAnimationFrame(place);
    function onDoc(e) {
      if (btnRef.current && btnRef.current.contains(e.target)) return;
      if (menuRef.current && menuRef.current.contains(e.target)) return;
      setOpen(false);
    }
    function onKey(e) { if (e.key === 'Escape') setOpen(false); }
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', place);
      window.removeEventListener('scroll', place, true);
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, menuAlign]);
  // `model` should never hold 'supercomputer' (it is intercepted, not
  // persisted); anything that is not Zenith or Auto reads as Spark so a stale
  // localStorage value cannot render a blank label.
  const current = model === 'zenith' || model === 'auto' ? model : 'spark';
  const currentMode = EG_MODES.find(m => m.id === current);
  function pick(v) { onModelChange && onModelChange(v); setOpen(false); }
  const menu = (open && menuPos && typeof ReactDOM !== 'undefined' && ReactDOM.createPortal)
    ? ReactDOM.createPortal(
        <div className={"eg-model-menu " + (menuPos.dir === 'up' ? 'opens-up' : 'opens-down')} role="menu" ref={menuRef}
             style={{ position: 'fixed', top: menuPos.top, bottom: menuPos.bottom, left: menuPos.left, right: menuPos.right }}>
          {EG_MODES.map(m => (
            <React.Fragment key={m.id}>
              {m.mode && <div className="eg-model-menu-sep" />}
              <button type="button" className={"eg-model-menu-item" + (m.id === current ? ' is-sel' : '')}
                onClick={() => pick(m.id)} role="menuitemradio" aria-checked={m.id === current}>
                <span className="eg-mmi-body">
                  <span className="eg-mmi-name">{m.name}{m.ver && <span className="eg-mmi-ver">{m.ver}</span>}</span>
                  <span className="eg-mmi-desc">{m.desc}</span>
                </span>
                {m.id === current && <span className="eg-mmi-check">{EgI.check}</span>}
              </button>
            </React.Fragment>
          ))}
        </div>,
        document.body,
      )
    : null;
  return (
    <div className="eg-model-wrap">
      <button
        ref={btnRef}
        type="button"
        className={"eg-model-btn is-" + current + (open ? ' is-open' : '')}
        onClick={() => setOpen(v => !v)}
        aria-haspopup="menu" aria-expanded={open}
        title={currentMode.name + (currentMode.ver ? ' ' + currentMode.ver : '') + ' — ' + currentMode.desc + '. Click to switch.'}>
        <span className="eg-model-lbl" key={current}>
          {currentMode.name}{currentMode.ver && <span className="eg-model-ver">{currentMode.ver}</span>}
        </span>
        <span className={"eg-model-caret" + (open ? ' is-open' : '')}>{EgI.caret}</span>
      </button>
      {menu}
    </div>
  );
}

/* ─── Chat panel (slides up from bottom edge) ────────────────────────────── */
/* ─── Document Intelligence: attachment cards above the composer ─────────── */
const EG_EXT_VISUALS = {
  pdf: { label: 'PDF', tone: 'pdf' },
  doc: { label: 'WORD', tone: 'word' }, docx: { label: 'DOCX', tone: 'word' },
  odt: { label: 'ODT', tone: 'word' },
  txt: { label: 'NOTE', tone: 'notes' }, md: { label: 'NOTE', tone: 'notes' },
  rtf: { label: 'NOTE', tone: 'notes' },
  csv: { label: 'CSV', tone: 'sheet' }, tsv: { label: 'TSV', tone: 'sheet' },
  xls: { label: 'XLS', tone: 'sheet' }, xlsx: { label: 'XLSX', tone: 'sheet' }, ods: { label: 'ODS', tone: 'sheet' },
  ppt: { label: 'PPT', tone: 'slides' }, pptx: { label: 'PPTX', tone: 'slides' }, odp: { label: 'SLIDE', tone: 'slides' },
  jpg: { label: 'IMG', tone: 'image' }, jpeg: { label: 'IMG', tone: 'image' }, png: { label: 'IMG', tone: 'image' },
  gif: { label: 'GIF', tone: 'image' }, webp: { label: 'IMG', tone: 'image' }, heic: { label: 'IMG', tone: 'image' }, svg: { label: 'SVG', tone: 'image' },
  js: { label: 'CODE', tone: 'code' }, jsx: { label: 'CODE', tone: 'code' }, ts: { label: 'CODE', tone: 'code' }, tsx: { label: 'CODE', tone: 'code' },
  py: { label: 'CODE', tone: 'code' }, json: { label: 'DATA', tone: 'code' }, yaml: { label: 'DATA', tone: 'code' }, yml: { label: 'DATA', tone: 'code' },
  zip: { label: 'ZIP', tone: 'archive' }, '7z': { label: '7Z', tone: 'archive' }, rar: { label: 'RAR', tone: 'archive' },
};
const EG_KIND_VISUALS = {
  pdf: { label: 'PDF', tone: 'pdf' }, document: { label: 'DOC', tone: 'word' }, doc: { label: 'WORD', tone: 'word' }, word: { label: 'WORD', tone: 'word' },
  spreadsheet: { label: 'SHEET', tone: 'sheet' }, presentation: { label: 'SLIDE', tone: 'slides' },
  image: { label: 'IMG', tone: 'image' }, archive: { label: 'ARCH', tone: 'archive' },
  code: { label: 'CODE', tone: 'code' }, text: { label: 'NOTE', tone: 'notes' }, notes: { label: 'NOTE', tone: 'notes' }, unknown: { label: 'FILE', tone: 'unknown' },
};
/* ─── File-type icons ────────────────────────────────────────────────────────
   Flat tiles in each format's own colour with the mark people know — PDF,
   Word's W, Excel's X, PowerPoint's P — drawn as SVG so nothing is fetched.
   An image shows the picture itself. */
const EG_FILE_MARKS = {
  pdf:     { a: '#FF6B5E', b: '#D9291C', glyph: 'PDF' },
  word:    { a: '#5B9BFF', b: '#1F5BD8', glyph: 'W' },
  sheet:   { a: '#3DD08A', b: '#118A4B', glyph: 'X' },
  slides:  { a: '#FF9259', b: '#D6501C', glyph: 'P' },
  code:    { a: '#4CC9F0', b: '#0B7FA3', glyph: 'code' },
  notes:   { a: '#B7AEA3', b: '#72695E', glyph: 'lines' },
  archive: { a: '#F7C35C', b: '#BF7F16', glyph: 'zip' },
  image:   { a: '#B39DFF', b: '#7650F0', glyph: 'photo' },
  unknown: { a: '#A3A9B5', b: '#666D7A', glyph: 'lines' },
};

function EgFileIcon({ tone, size, thumb }) {
  const px = size || 40;
  if (tone === 'image' && thumb) {
    return (
      <span className="eg-fileico is-thumb" style={{ width: px, height: px }}>
        <img src={thumb} alt="" draggable="false" />
      </span>
    );
  }
  const m = EG_FILE_MARKS[tone] || EG_FILE_MARKS.unknown;
  const gid = 'egfi-' + (tone || 'unknown');
  const letter = m.glyph.length === 1;
  return (
    <span className="eg-fileico" style={{ width: px, height: px }} aria-hidden="true">
      <svg viewBox="0 0 40 40" width={px} height={px}>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={m.a} />
            <stop offset="1" stopColor={m.b} />
          </linearGradient>
        </defs>
        <rect x="0" y="0" width="40" height="40" rx="11" fill={`url(#${gid})`} />
        <rect x="0.5" y="0.5" width="39" height="39" rx="10.5" fill="none" stroke="rgba(255,255,255,.22)" />
        <path d="M0 11a11 11 0 0 1 11-11h18a11 11 0 0 1 11 11v4H0z" fill="rgba(255,255,255,.08)" />
        {letter && (
          <text x="20" y="26.5" textAnchor="middle" fontFamily="Segoe UI, Inter, sans-serif"
                fontWeight="700" fontSize="18" fill="#fff">{m.glyph}</text>
        )}
        {m.glyph === 'PDF' && (
          <text x="20" y="24.5" textAnchor="middle" fontFamily="Segoe UI, Inter, sans-serif"
                fontWeight="800" fontSize="11" letterSpacing=".6" fill="#fff">PDF</text>
        )}
        {m.glyph === 'code' && (
          <path d="M15 14l-6 6 6 6M25 14l6 6-6 6M22.5 12l-5 16" stroke="#fff" strokeWidth="2.4"
                fill="none" strokeLinecap="round" strokeLinejoin="round" />
        )}
        {m.glyph === 'lines' && (
          <g stroke="#fff" strokeWidth="2.4" strokeLinecap="round">
            <path d="M12 14h16M12 20h16M12 26h10" />
          </g>
        )}
        {m.glyph === 'zip' && (
          <g fill="#fff">
            <rect x="18.5" y="8" width="3" height="3" rx=".8" /><rect x="18.5" y="13" width="3" height="3" rx=".8" />
            <rect x="18.5" y="18" width="3" height="3" rx=".8" />
            <rect x="16" y="23" width="8" height="9" rx="2.2" />
          </g>
        )}
        {m.glyph === 'photo' && (
          <g fill="#fff">
            <circle cx="15" cy="15" r="3.2" />
            <path d="M8 30l8.5-9 5.5 5.5 3.5-3.5 6.5 7z" />
          </g>
        )}
      </svg>
    </span>
  );
}

/* A preview URL for an attached image's own file, released when it goes. */
function useEgObjectUrl(file) {
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!file || !(file instanceof Blob) || !String(file.type || '').startsWith('image/')) { setUrl(''); return undefined; }
    let made = '';
    try { made = URL.createObjectURL(file); setUrl(made); } catch (_) { setUrl(''); }
    return () => { if (made) { try { URL.revokeObjectURL(made); } catch (_) {} } };
  }, [file]);
  return url;
}

/* ─── Smooth text reveal ──────────────────────────────────────────────────────
   Tokens arrive in bursts, so painting them as they land reads as stutter.
   This reveals toward whatever has arrived at a pace set by how far behind it
   is — a small backlog trickles, a big one catches up within a few frames —
   and always stops on a word boundary, so text flows rather than jumps. */
function useEgSmoothText(target, active, start) {
  const full = target || '';
  const from = active ? Math.max(0, Math.min(full.length, Number(start) || 0)) : full.length;
  const [shown, setShown] = useState(from);
  const shownRef = useRef(from);
  const targetRef = useRef(full);
  const frame = useRef(0);
  targetRef.current = full;
  useEffect(() => {
    if (!active) { shownRef.current = full.length; setShown(full.length); return; }
    if (shownRef.current > full.length) { shownRef.current = 0; setShown(0); }
    if (frame.current) return;
    const step = () => {
      const text = targetRef.current;
      const behind = text.length - shownRef.current;
      if (behind <= 0) { frame.current = 0; return; }
      let next = shownRef.current + Math.max(2, Math.ceil(behind / 7));
      if (next < text.length) {
        const space = text.indexOf(' ', next);
        if (space !== -1 && space - next < 12) next = space + 1;
      }
      shownRef.current = Math.min(text.length, next);
      setShown(shownRef.current);
      frame.current = requestAnimationFrame(step);
    };
    frame.current = requestAnimationFrame(step);
  }, [full, active]);
  useEffect(() => () => { if (frame.current) cancelAnimationFrame(frame.current); }, []);
  return active ? full.slice(0, shown) : full;
}

/* What SYRIX is doing right now, from the shell's signals and the user's own
   actions, for the one-line status under the chat list. */
function useEgPresence(working, snap) {
  const [writing, setWriting] = useState(false);
  const [flash, setFlash] = useState({ kind: '', until: 0, detail: '' });
  const [dragging, setDragging] = useState(false);
  const [happyUntil, setHappyUntil] = useState(0);
  const [idleSince, setIdleSince] = useState(() => Date.now());
  const [, tick] = useState(0);
  const wasWorking = useRef(false);
  useEffect(() => {
    let tokT = 0, dragDepth = 0;
    const onTok = (ev) => {
      const d = ev && ev.detail;
      if (!d || d.done) return;
      setWriting(true);
      clearTimeout(tokT);
      tokT = setTimeout(() => setWriting(false), 1300);
    };
    const hasFiles = (ev) => {
      try { return Array.from((ev.dataTransfer && ev.dataTransfer.types) || []).includes('Files'); } catch (_) { return false; }
    };
    // State is set a tick later, never inside the event itself. Updating it
    // from a capture listener let React re-render mid-event, and a re-render
    // there resets a controlled input to its old value — measured, it made
    // the composer drop every keystroke.
    const later = (fn) => setTimeout(fn, 0);
    const onEnter = (ev) => { if (hasFiles(ev)) { dragDepth += 1; later(() => setDragging(true)); } };
    const onLeave = (ev) => { if (hasFiles(ev)) { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) later(() => setDragging(false)); } };
    const onDrop = (ev) => {
      dragDepth = 0;
      let name = '';
      try { name = (ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0] && ev.dataTransfer.files[0].name) || ''; } catch (_) {}
      const had = name || hasFiles(ev);
      later(() => {
        setDragging(false);
        if (had) setFlash({ kind: 'caught', until: Date.now() + 1800, detail: name });
      });
    };
    const onPet = (ev) => {
      const kind = ev && ev.detail && ev.detail.kind;
      if (kind === 'sent') setFlash({ kind: 'sent', until: Date.now() + 900, detail: '' });
      if (kind === 'attached') setFlash({ kind: 'caught', until: Date.now() + 1800, detail: (ev.detail && ev.detail.name) || '' });
    };
    window.addEventListener('syrix-chat-token', onTok);
    window.addEventListener('dragenter', onEnter, true);
    window.addEventListener('dragleave', onLeave, true);
    window.addEventListener('drop', onDrop, true);
    window.addEventListener('syrix-presence', onPet);
    return () => {
      window.removeEventListener('syrix-chat-token', onTok);
      window.removeEventListener('dragenter', onEnter, true);
      window.removeEventListener('dragleave', onLeave, true);
      window.removeEventListener('drop', onDrop, true);
      window.removeEventListener('syrix-presence', onPet);
      clearTimeout(tokT);
    };
  }, []);
  useEffect(() => {
    if (wasWorking.current && !working) setHappyUntil(Date.now() + 2600);
    if (working) setIdleSince(Date.now());
    wasWorking.current = !!working;
  }, [working]);
  useEffect(() => {
    if (!flash.until) return undefined;
    const t = setTimeout(() => tick(n => n + 1), Math.max(0, flash.until - Date.now()) + 20);
    return () => clearTimeout(t);
  }, [flash.until]);
  useEffect(() => {
    if (!happyUntil) return undefined;
    const t = setTimeout(() => tick(n => n + 1), Math.max(0, happyUntil - Date.now()) + 20);
    return () => clearTimeout(t);
  }, [happyUntil]);
  useEffect(() => {
    const id = setInterval(() => tick(n => n + 1), 15000);
    return () => clearInterval(id);
  }, []);
  const now = Date.now();
  const feed = (snap && snap.raw && Array.isArray(snap.raw.activity_feed)) ? snap.raw.activity_feed : [];
  const last = feed.length ? feed[feed.length - 1] : null;
  const kind = String((last && last.kind) || '');
  const label = String((last && last.label) || '');
  const domain = String((last && (last.web_domain || (last.extra && last.extra.web_domain))) || '');
  const fresh = last && last.ts ? (now / 1000 - Number(last.ts)) < 20 : false;
  if (fresh && kind === 'error' && !working) return { mood: 'error', detail: '' };
  if (flash.kind && now < flash.until) {
    return { mood: flash.kind, detail: flash.kind === 'caught' && flash.detail ? 'Got ' + flash.detail : '' };
  }
  if (dragging) return { mood: 'drag', detail: '' };
  if (working) {
    if (writing) return { mood: 'writing', detail: '' };
    if (/search|source|filter_rank|pack_|retriev/.test(kind) || /^Search/.test(label)) {
      const where = domain || (/^Searching\s+(\S+)/.exec(label) || [])[1] || '';
      return { mood: 'searching', detail: where ? 'Reading ' + where : '' };
    }
    return { mood: 'thinking', detail: '' };
  }
  if (now < happyUntil) return { mood: 'happy', detail: '' };
  if (now - idleSince > 5 * 60 * 1000) return { mood: 'sleepy', detail: '' };
  return { mood: 'idle', detail: '' };
}

/* ─── Profile row ───────────────────────────────────────────────────────────
   Under the chat list: who the HUD belongs to, and one quiet line saying what
   SYRIX is doing right now. */
const EG_PRESENCE_TEXT = {
  idle: 'Ready', typing: 'Ready', drag: 'Drop to attach', caught: 'Attached', sent: 'Sending\u2026',
  thinking: 'Thinking\u2026', searching: 'Searching the web', writing: 'Writing\u2026', happy: 'Ready',
  sleepy: 'Ready', error: 'Something went wrong',
};

function egPalette() {
  try { return document.body.classList.contains('pal-dark') ? 'dark' : 'aurora'; } catch (_) { return 'aurora'; }
}

/* The account row at the foot of the sidebar: who this is, what SYRIX is
   doing, and — one click away — the switches used most: Private mode, the
   palette, voice, Connectors and Settings. */
function EgProfileRow({ mood, detail, modelLabel, privateMode, onTogglePrivate, voiceActive,
                        onToggleVoice, onOpenConnectors, onOpenSettings, connectedCount }) {
  const name = egOwnerName();
  const initial = (name.trim()[0] || 'S').toUpperCase();
  const m = mood || 'idle';
  const busy = m === 'thinking' || m === 'searching' || m === 'writing' || m === 'sent';
  const text = (m === 'caught' && detail) ? detail.replace(/^Got /, 'Attached ')
    : (m === 'searching' && detail) ? detail
    : (EG_PRESENCE_TEXT[m] || 'Ready');
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const [palette, setPalette] = useState(egPalette);
  const btnRef = useRef(null);
  const menuRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    setPalette(egPalette());
    function place() {
      const r = btnRef.current && btnRef.current.getBoundingClientRect();
      if (!r) return;
      setPos({ left: Math.round(r.left), bottom: Math.round(window.innerHeight - r.top + 8),
               width: Math.max(236, Math.round(r.width)) });
    }
    place();
    function onDoc(e) {
      if (btnRef.current && btnRef.current.contains(e.target)) return;
      if (menuRef.current && menuRef.current.contains(e.target)) return;
      setOpen(false);
    }
    function onKey(e) { if (e.key === 'Escape') setOpen(false); }
    window.addEventListener('resize', place);
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('resize', place);
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  function pickPalette(p) {
    setPalette(p);
    try { window.dispatchEvent(new CustomEvent('syrix-palette-set', { detail: { palette: p } })); } catch (_) {}
  }
  function run(fn) { setOpen(false); try { fn && fn(); } catch (_) {} }
  const status = privateMode ? 'Private · ' + text : text;
  const menu = (open && pos && typeof ReactDOM !== 'undefined' && ReactDOM.createPortal) ? ReactDOM.createPortal(
    <div className="eg-acct-menu" role="menu" ref={menuRef}
      style={{ position: 'fixed', left: pos.left, bottom: pos.bottom, width: pos.width }}>
      <div className="eg-acct-head">
        <span className="eg-profile-avatar" aria-hidden="true">{initial}</span>
        <span className="eg-acct-head-text">
          <span className="eg-acct-name">{name}</span>
          <span className="eg-acct-sub">SYRIX · {modelLabel}</span>
        </span>
      </div>
      <button type="button" className="eg-acct-item" role="menuitemcheckbox" aria-checked={!!privateMode}
        onClick={() => onTogglePrivate && onTogglePrivate()}>
        <span className="eg-acct-ico" aria-hidden="true">{EgI.shield}</span>
        <span className="eg-acct-body">
          <span className="eg-acct-lbl">Private mode</span>
          <span className="eg-acct-desc">Not saved · local model · offline</span>
        </span>
        <span className={'eg-side-switch' + (privateMode ? ' is-on' : '')} aria-hidden="true"><span /></span>
      </button>
      <div className="eg-acct-item is-static">
        <span className="eg-acct-ico" aria-hidden="true">{palette === 'dark' ? EgI.moon : EgI.sun}</span>
        <span className="eg-acct-body"><span className="eg-acct-lbl">Theme</span></span>
        <span className="eg-acct-seg" role="radiogroup" aria-label="Theme">
          <button type="button" role="radio" aria-checked={palette === 'aurora'}
            className={palette === 'aurora' ? 'is-on' : ''} onClick={() => pickPalette('aurora')}>Aura</button>
          <button type="button" role="radio" aria-checked={palette === 'dark'}
            className={palette === 'dark' ? 'is-on' : ''} onClick={() => pickPalette('dark')}>Dark</button>
        </span>
      </div>
      <div className="eg-acct-sep" />
      <button type="button" className="eg-acct-item" role="menuitem" onClick={() => run(onToggleVoice)}>
        <span className="eg-acct-ico" aria-hidden="true">{EgI.wave}</span>
        <span className="eg-acct-body"><span className="eg-acct-lbl">{voiceActive ? 'End voice' : 'Talk to SYRIX'}</span></span>
      </button>
      <button type="button" className="eg-acct-item" role="menuitem" onClick={() => run(onOpenConnectors)}>
        <span className="eg-acct-ico" aria-hidden="true">{EgI.plug}</span>
        <span className="eg-acct-body"><span className="eg-acct-lbl">Connectors</span></span>
        {connectedCount > 0 && <span className="eg-acct-count">{connectedCount} connected</span>}
      </button>
      <button type="button" className="eg-acct-item" role="menuitem" onClick={() => run(onOpenSettings)}>
        <span className="eg-acct-ico" aria-hidden="true">{EgI.settings}</span>
        <span className="eg-acct-body"><span className="eg-acct-lbl">Settings</span></span>
      </button>
    </div>,
    document.body,
  ) : null;
  return (
    <React.Fragment>
      <button type="button" ref={btnRef}
        className={'eg-profile is-' + (m === 'error' ? 'error' : busy ? 'busy' : 'ready') + (open ? ' is-open' : '') + (privateMode ? ' is-private' : '')}
        onClick={() => setOpen(v => !v)} aria-haspopup="menu" aria-expanded={open}
        title={name + ' — account and quick settings'}>
        <span className="eg-profile-avatar" aria-hidden="true">{initial}<span className="eg-profile-dot" /></span>
        <span className="eg-profile-text">
          <span className="eg-profile-name">{name}</span>
          <span className="eg-profile-status" key={status}>{status}</span>
        </span>
        <span className="eg-profile-more" aria-hidden="true">{EgI.more}</span>
      </button>
      {menu}
    </React.Fragment>
  );
}

function egAttachmentVisual(card) {
  const name = String(card?.name || '');
  const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
  const subtype = String(card?.subtype || '').toLowerCase().replace(/^\./, '');
  return EG_EXT_VISUALS[ext] || EG_EXT_VISUALS[subtype] || EG_KIND_VISUALS[String(card?.kind || 'unknown').toLowerCase()] || EG_KIND_VISUALS.unknown;
}
function egHumanSize(n) {
  const b = Number(n || 0);
  if (!b) return '';
  if (b >= 1024 * 1024) return (b / (1024 * 1024)).toFixed(1) + ' MB';
  if (b >= 1024) return Math.round(b / 1024) + ' KB';
  return b + ' B';
}

/* An attached file, as mainstream chat apps show one in the composer: an image
   is a rounded thumbnail; any other file is a compact card with its type
   mark, name and "PDF · 1.2 MB". Progress is a ring round the mark (a real
   percentage while reading, a sweep while SYRIX analyses it); remove sits on
   the corner and shows on hover, or always when something went wrong. */
function EgAttachCard({ card, onRemove, onRetry }) {
  const visual = egAttachmentVisual(card);
  const thumb = useEgObjectUrl(card._file);
  const status = card.status || 'uploading';
  const busy = status === 'uploading' || status === 'analyzing';
  const failed = status === 'failed';
  const cancelled = status === 'cancelled';
  const isImage = visual.tone === 'image' && !!thumb;
  const size = egHumanSize(card.size);
  const sub = failed ? (card.error ? 'Could not read' : 'Failed')
    : cancelled ? 'Cancelled'
    : busy ? (status === 'uploading' ? 'Uploading…' : 'Reading…')
    : [visual.label, card.subtitle || size].filter(Boolean).join(' · ');
  const pct = status === 'uploading' ? Math.max(0, Math.min(100, Number(card.progress || 0))) : null;
  const ring = busy && (
    <svg className={"eg-att-ring" + (pct === null ? ' is-spin' : '')} viewBox="0 0 48 48" aria-hidden="true">
      <circle className="track" cx="24" cy="24" r="20" />
      <circle className="bar" cx="24" cy="24" r="20" strokeDasharray="125.7"
              strokeDashoffset={pct === null ? 88 : 125.7 * (1 - pct / 100)} transform="rotate(-90 24 24)" />
    </svg>
  );
  const remove = (
    <button type="button" className="eg-att-x" onClick={() => onRemove(card.upload_id)}
      title={busy ? 'Cancel' : 'Remove'} aria-label={(busy ? 'Cancel ' : 'Remove ') + card.name}>
      <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
    </button>
  );
  if (isImage) {
    return (
      <div className={"eg-att is-image status-" + status} role="listitem" title={card.error || card.name}>
        <img className="eg-att-thumb" src={thumb} alt={card.name} draggable="false" />
        {busy && <span className="eg-att-veil">{ring}</span>}
        {failed && <span className="eg-att-veil is-failed">!</span>}
        {remove}
      </div>
    );
  }
  return (
    <div className={"eg-att is-file status-" + status} role="listitem" title={card.error || card.name}>
      <span className="eg-att-mark">
        <EgFileIcon tone={visual.tone} size={36} />
        {ring}
      </span>
      <span className="eg-att-text">
        <span className="eg-att-name">{card.name}</span>
        <span className="eg-att-sub">{sub}</span>
      </span>
      {(failed || cancelled) && card._file && (
        <button type="button" className="eg-att-retry" onClick={() => onRetry(card.upload_id)}
          title="Try again" aria-label={"Try again: " + card.name}>Retry</button>
      )}
      {remove}
    </div>
  );
}

/* The files waiting to be sent. Under the bottom composer the tray is docked
   onto it — same surface, joined edge — so files and text read as one card;
   in the side/half/full panel it sits above that panel's composer. */
function EgAttachTray({ attachments, onRemove, onRetry, docked }) {
  const list = Array.isArray(attachments) ? attachments : [];
  if (!list.length) return null;
  return (
    <div className={"eg-attach-tray" + (docked ? ' is-docked' : '')} role="list" aria-label="Attached files">
      {list.map(c => (
        <EgAttachCard key={c.upload_id} card={c} onRemove={onRemove} onRetry={onRetry} />
      ))}
    </div>
  );
}

// Starting points under the composer. Each is a question SYRIX answers well
// with live data, phrased specifically enough that the answer is unambiguous
// (a results question names the competition rather than "football scores").
// Four are drawn per launch and per new chat, never the same four twice.
const EG_QUICK_ACTIONS = [
  { label: 'Markets today',        prompt: 'How did the stock markets do today? Cover the S&P 500, Nasdaq and FTSE 100, and what moved them.', icon: EgI.charts },
  { label: "Today's news",         prompt: 'What are the top news stories in the UK and around the world today?', icon: EgI.news },
  { label: 'Bitcoin price',        prompt: "What's the price of Bitcoin right now, and how has it moved this week?", icon: EgI.trading },
  { label: 'Weather in London',    prompt: "What's the weather in London today and tomorrow?", icon: EgI.weather },
  { label: 'Pound to dollar',      prompt: "What's the GBP to USD exchange rate right now?", icon: EgI.trading },
  { label: 'Nvidia stock',         prompt: 'How is Nvidia (NVDA) stock doing today, and why?', icon: EgI.charts },
  { label: 'AI news this week',    prompt: 'What is the biggest AI and tech news this week?', icon: EgI.brain },
  { label: 'Premier League table', prompt: 'Show me the current Premier League table.', icon: EgI.globe },
  { label: 'Gold price',           prompt: "What's the price of gold today, and why is it moving?", icon: EgI.charts },
  { label: 'UK interest rates',    prompt: "What's the Bank of England interest rate now, and when is the next decision?", icon: EgI.search },
];
// The apps shown in the "Connect an app" pill: the ones connected, or a
// familiar three when none are. Gmail and Drive use their full-colour marks
// (the same Gmail artwork as the Connectors page); the plain .svg files for
// those two are single-colour silhouettes and read as the wrong logo.
const EG_CONNECT_LOGOS = { gmail: 'gmail-reference.png', google_drive: 'googledrive-color.svg',
  google_calendar: 'googlecalendar.svg', notion: 'notion.svg', github: 'github.svg', slack: null,
  dropbox: 'dropbox.svg', todoist: 'todoist.svg', spotify: 'spotify.svg', discord: 'discord.svg',
  asana: 'asana.svg', trello: 'trello.svg', linear: 'linear.svg', jira: 'jira.svg', stripe: 'stripe.svg' };
function egConnectLogos(snap) {
  const list = ((snap && (snap.syrix_connectors || (snap.raw && snap.raw.syrix_connectors)) || {}).connectors) || [];
  const live = list.filter(c => c && (c.health === 'connected' || c.health === 'needs_reauth') && EG_CONNECT_LOGOS[c.id])
    .map(c => EG_CONNECT_LOGOS[c.id]);
  const fallback = [EG_CONNECT_LOGOS.gmail, EG_CONNECT_LOGOS.google_drive, EG_CONNECT_LOGOS.notion];
  return { logos: (live.length ? live : fallback).slice(0, 3), count: live.length };
}


function egLaunchQuickActions() {
  let previous = '';
  try { previous = String(localStorage.getItem('eg-quick-actions-v2') || ''); } catch (_) {}
  let next = [];
  let signature = '';
  for (let attempt = 0; attempt < 8; attempt++) {
    const pool = EG_QUICK_ACTIONS.slice();
    for (let i = pool.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    next = pool.slice(0, 4);
    signature = next.map(action => action.label).sort().join('|');
    if (signature !== previous || EG_QUICK_ACTIONS.length <= 4) break;
  }
  try { localStorage.setItem('eg-quick-actions-v2', signature); } catch (_) {}
  return next;
}

function EgChatPanel({
  open, lines, working, listening, micMuted,
  draft, setDraft, onSubmit, onMic, onClose, inputRef, searchTrace,
  deepResearch, onToggleDeepResearch, onUploadClick, fileInputRef, onUploadChosen,
  attachments, onRemoveAttachment, onRetryAttachment, onDropFiles,
  onComposerPaste,
  onCancelTurn, isSpeaking, onCancelSpeech, chatLayout, onLayoutChange,
  model, onModelChange, zenithTurn, pendingRid, ourTurnAnswered,
  isHome,
}) {
  const scrollRef = useRef(null);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [composerDragOver, setComposerDragOver] = useState(false);
  const didSnapRef = useRef(false);
  const attachList = Array.isArray(attachments) ? attachments : [];
  const hasReadyAttach = attachList.some(c => c.status === 'ready' || c.status === 'partial');
  // Whether the user is currently pinned to the bottom. While pinned we follow
  // new content; if they scroll up to read history we leave them alone.
  const stickRef = useRef(true);
  // Scrolled up far enough to lose the latest line: offer the way back, the
  // round chevron every chat app puts above its composer. `jumpingRef` holds
  // the button away while the smooth scroll it started is still travelling,
  // or it would reappear under the pointer mid-glide.
  const [showJump, setShowJump] = useState(false);
  const jumpingRef = useRef(false);

  function handleLayoutChange(newLayout) {
    if (newLayout === chatLayout) return;
    // 2026-07-04 — fluid morph: the CSS geometry transitions carry the frame
    // between compact/side/full like the Dynamic Island. Layout applies
    // immediately; the transition class only softens the inner content while
    // the frame glides, then everything settles crisp.
    onLayoutChange(newLayout);
    setIsTransitioning(true);
    setTimeout(() => setIsTransitioning(false), 420);
  }

  const visible = (Array.isArray(lines) ? lines : []);

  const latestAIIdx = useMemo(() => {
    for (let i = visible.length - 1; i >= 0; i--) {
      if (!egIsUser(visible[i])) return i;
    }
    return -1;
  }, [visible.length]);

  // 2026-07-02 — light purple hint on a ZENITH turn's Q + A. The backend
  // spine already stamps the real resolved model onto every AI chat line
  // (syrix_host.py entry["syrix_model"], sourced from runtime_spine.py's
  // diagnostics) — this just reads that fact; no guessing, no new state.
  const zenithIdx = useMemo(() => {
    const set = new Set();
    for (let i = 0; i < visible.length; i++) {
      const m = visible[i];
      if (!egIsUser(m) && String(m?.syrix_model || '').toLowerCase() === 'zenith') {
        set.add(i);
        if (i > 0 && egIsUser(visible[i - 1])) set.add(i - 1);
      }
    }
    return set;
  }, [visible]);

  // 2026-07-02 — REAL token streaming. The backend has streamed brain replies
  // over WS (chat_token) all along, but this shell — the active theme — never
  // listened, so users only saw the fake typewriter after completion. The
  // accumulated stream renders live and the thinking panel yields to it.
  const [streamText, setStreamText] = useState('');
  // Bursty tokens are revealed at a steady, catch-up pace (see useEgSmoothText).
  const smoothStream = useEgSmoothText(streamText, true);
  useEffect(() => {
    try { window.__syrixStreamShown = { rid: pendingRidRef.current, len: smoothStream.length }; } catch (_) {}
  }, [smoothStream]);
  // Pictures for the turn arrive ahead of the finished line: first how many
  // to make room for, then the images themselves.
  const [liveMedia, setLiveMedia] = useState(null);
  useEffect(() => {
    function onMedia(ev) {
      const d = ev && ev.detail;
      if (!d) return;
      const rid = String(d.request_id || '');
      const ourRid = pendingRidRef.current;
      if (!rid || (ourRid && rid !== ourRid)) return;
      setLiveMedia(prev => {
        const same = prev && prev.rid === rid;
        return {
          rid,
          pending: d.pending != null ? Number(d.pending) : (same ? prev.pending : 0),
          images: Array.isArray(d.images) ? d.images : (same ? prev.images : null),
          card: (d.card && typeof d.card === 'object') ? d.card : (same ? prev.card : null),
        };
      });
    }
    window.addEventListener('syrix-chat-media', onMedia);
    return () => window.removeEventListener('syrix-chat-media', onMedia);
  }, []);
  const liveImagesWanted = !!(liveMedia && liveMedia.rid === String(pendingRid || '')
    && ((liveMedia.images && liveMedia.images.length) || (!liveMedia.images && liveMedia.pending > 0)));
  const liveCard = (liveMedia && liveMedia.rid === String(pendingRid || '') && liveMedia.card) || null;
  // 2026-07-04 — rid-scoped stream: the bridge tags every token event with its
  // request id. While a typed turn is pending we render ONLY its stream;
  // another turn's tokens (a superseded send, an overlapping voice turn) no
  // longer bleed into this panel — that was the "models leak into each other"
  // visual. With no pending rid (voice/idle) behaviour is unchanged.
  const pendingRidRef = useRef('');
  useEffect(() => {
    // A new/changed turn drops any stale streamed text so the previous turn's
    // stream can never linger under the fresh thinking panel.
    const next = String(pendingRid || '');
    if (next !== pendingRidRef.current) { setStreamText(''); setLiveMedia(null); }
    pendingRidRef.current = next;
  }, [pendingRid]);
  useEffect(() => {
    function onTok(ev) {
      const d = ev && ev.detail;
      if (!d) return;
      const tokRid = String(d.rid || d.request_id || '');
      const ourRid = pendingRidRef.current;
      if (tokRid && ourRid && tokRid !== ourRid) return; // another turn's stream
      // 2026-07-07 — Keep the accumulated stream on `done`: clearing it here
      // re-opened a one-frame gap where `working` was still true but streamText
      // was empty, so the thinking panel flashed back between the last token and
      // the final line landing. The stream bubble (gated on !ourTurnAnswered)
      // now hands off directly to the finished message; the working→false effect
      // and the new-turn effect clear the buffer.
      if (d.done) return;
      setStreamText(d.text || '');
      try { window.__syrixLastStreamText = d.text || ''; } catch (_) {}
    }
    window.addEventListener('syrix-chat-token', onTok);
    return () => window.removeEventListener('syrix-chat-token', onTok);
  }, []);
  // Safety: when the turn fully ends (working false) any leftover stream
  // buffer is stale — the final line has landed in chat_lines.
  useEffect(() => {
    if (!working && streamText) setStreamText('');
    if (!working) setLiveMedia(null);
  }, [working]);

  /* ── The progress panel belongs to the TURN, not to the token stream ──────
     It used to be `working && !streamText`, i.e. "show it whenever nothing is
     streaming". A turn with tool use streams in rounds — search, read,
     synthesise — and the buffer resets between them, so the panel closed on
     the first token of every round and reopened in each gap. That is the
     blinking: it was reporting the state of the stream buffer, not the state
     of the work.

     It is now tied to `working` alone: it opens when the turn starts, stays
     for the whole turn while steps accumulate underneath it, and closes once
     when the turn ends. The streamed answer renders BELOW it, the way a
     research panel sits above the answer it produced.

     The close is also held briefly and cancelled if `working` comes back, so
     a single-frame dip between rounds can never be seen as a close.

     It steps aside once the answer itself starts to appear, rather than at
     the end of the turn. Held to the end, the panel sat above a finished
     answer for as long as the turn took to wrap up (media, follow-ups, the
     finished line), which read as the work still going on. The close is
     latched for the turn, so a gap between rounds cannot reopen it. */
  const [thinkDoneFor, setThinkDoneFor] = useState('');
  const answerBegun = smoothStream.length > 0 || !!ourTurnAnswered;
  useEffect(() => {
    if (working && answerBegun) setThinkDoneFor(String(pendingRid || 'turn'));
  }, [working, answerBegun, pendingRid]);
  useEffect(() => { if (!working) setThinkDoneFor(''); }, [working]);
  const thinkRawActive = working && thinkDoneFor !== String(pendingRid || 'turn');
  const [thinkVisible, setThinkVisible] = useState(false);
  const [thinkClosing, setThinkClosing] = useState(false);
  const thinkTimerRef = useRef(null);
  useEffect(() => {
    if (thinkRawActive) {
      if (thinkTimerRef.current) { clearTimeout(thinkTimerRef.current); thinkTimerRef.current = null; }
      setThinkClosing(false);
      setThinkVisible(true);
      return;
    }
    if (!thinkVisible) return;
    setThinkClosing(true);
    thinkTimerRef.current = setTimeout(() => {
      setThinkVisible(false);
      setThinkClosing(false);
      thinkTimerRef.current = null;
    }, 420);
  }, [thinkRawActive]);
  useEffect(() => () => { if (thinkTimerRef.current) clearTimeout(thinkTimerRef.current); }, []);

  // Track whether the user is pinned to the bottom so we never yank them up
  // while they read history.
  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const below = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (jumpingRef.current) {
      stickRef.current = true;
      if (below <= 4) jumpingRef.current = false;
      return;
    }
    stickRef.current = below <= 160;
    setShowJump(below > 240);
  }

  // Following the answer as it streams is an instant snap, never an animated
  // one. The transcript has `scroll-behavior: smooth` for the rail's page
  // jumps, and it applied to this too: every token restarted a glide toward
  // a bottom that had already moved, so the view trailed the text and a fast
  // burst could carry it past the pin tolerance and stop following.
  function snapToBottom(el) {
    try { el.scrollTo({ top: el.scrollHeight, behavior: 'instant' }); }
    catch (_) { el.scrollTop = el.scrollHeight; }
  }

  function jumpToLatest() {
    const el = scrollRef.current;
    if (!el) return;
    jumpingRef.current = true;
    stickRef.current = true;
    setShowJump(false);
    try { el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }); }
    catch (_) { el.scrollTop = el.scrollHeight; }
    // A glide the user interrupts with the wheel never reaches the bottom;
    // release the hold anyway so the button can return.
    setTimeout(() => { jumpingRef.current = false; }, 900);
  }

  // 2026-06-16 — ROOT-CAUSE FIX for "chat doesn't auto-scroll to the answer".
  // The old effect only fired on visible.length / working changes, so it missed
  // the typewriter reveal which grows the bubble height AFTER the message was
  // added — the answer typed out below the fold and the user had to scroll. A
  // MutationObserver on the scroll subtree catches every height change (new
  // nodes, char-by-char reveal, trace ladder) and keeps us pinned to the bottom
  // whenever the user hasn't scrolled away.
  useEffect(() => {
    if (!open) return;
    const el = scrollRef.current;
    if (!el) return;
    const pin = () => {
      if (stickRef.current) snapToBottom(el);
      else if (!jumpingRef.current) setShowJump(el.scrollHeight - el.scrollTop - el.clientHeight > 240);
    };
    let mo = null;
    try {
      mo = new MutationObserver(pin);
      mo.observe(el, { childList: true, subtree: true, characterData: true });
    } catch (_) {}
    return () => { try { mo && mo.disconnect(); } catch (_) {} };
  }, [open]);

  useEffect(() => {
    if (!open) { didSnapRef.current = false; stickRef.current = true; return; }
    const el = scrollRef.current;
    if (!el) return;
    // First open, or the user just sent a message (latest line is theirs):
    // always pin + snap so the reply is guaranteed visible as it streams in.
    const last = visible[visible.length - 1];
    const lastIsUser = last && egIsUser(last);
    if (!didSnapRef.current || lastIsUser) {
      stickRef.current = true;
      snapToBottom(el);
      setShowJump(false);
      didSnapRef.current = true;
      return;
    }
    if (stickRef.current) snapToBottom(el);
  }, [open, visible.length, working]);

  function handleKey(e) {
    if (egShouldSend(e)) { e.preventDefault(); onSubmit(); }
    if (e.key === 'Escape') onClose?.();
  }

  return (
    <div className={
      "eg-chat-panel" +
      (chatLayout === 'bg'      ? ' bg'      : '') +
      (chatLayout === 'compact' ? ' compact' : '') +
      (chatLayout === 'side'    ? ' side'    : '') +
      (chatLayout === 'half'    ? ' half'    : '') +
      (chatLayout === 'full'    ? ' full'    : '') +
      (chatLayout === 'stage-right' ? ' stage-right' : '') +
      (isTransitioning ? ' eg-layout-fade' : '') +
      (model === 'zenith' ? ' zenith-active' : '') +
      (model === 'auto' ? ' auto-active' : '') +
      (open ? ' is-open' : '')
    } aria-hidden={open ? 'false' : 'true'}
      /* Drop-anywhere upload (2026-07-16): in the 'bg' canvas layout the
         panel's own composer (the old drop target) is display:none, which
         left drag-and-drop dead in the default layout. Accept file drops on
         the whole conversation surface instead. */
      onDragOver={e => {
        try {
          if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) e.preventDefault();
        } catch (_) {}
      }}
      onDrop={e => {
        try {
          if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
            e.preventDefault();
            onDropFiles(e.dataTransfer.files);
          }
        } catch (_) {}
      }}>

      {/* Header */}
      <div className="eg-chat-head">
        <div className="eg-chat-title">
          <span className="eg-chat-dot" />
          CHAT
        </div>
        <div className="eg-chat-head-row">
          {(working || isSpeaking) && (
            <button className="eg-stop-speech" onClick={onCancelTurn} title="Stop Syrix">
              {EgI.stop} STOP
            </button>
          )}
          {/* Layout switchers hidden in stage mode — the panel is pinned to
              its left dock there; only minimize (close) remains. */}
          {chatLayout !== 'stage-right' && (<>
          {/* Background — fills the main area while the nav rail, top bar and
              Dynamic Island stay on top. This is the default surface whenever
              Voice Mode is off (chat-first, chat-app style). */}
          <button
            className={"eg-chat-layout-btn" + (chatLayout === 'bg' ? ' is-active' : '')}
            onClick={() => handleLayoutChange('bg')}
            title="Background — fill the space (nav stays)">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/>
              <line x1="4.2" y1="6" x2="11.8" y2="6"/>
              <line x1="4.2" y1="9" x2="9.5" y2="9"/>
            </svg>
          </button>
          {/* Compact — always goes to compact */}
          <button
            className={"eg-chat-layout-btn" + (chatLayout === 'compact' ? ' is-active' : '')}
            onClick={() => handleLayoutChange('compact')}
            title="Compact view">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <line x1="2" y1="12.5" x2="14" y2="12.5"/>
              <polyline points="5.5,9 8,12 10.5,9"/>
              <line x1="8" y1="3.5" x2="8" y2="12"/>
            </svg>
          </button>
          {/* Side — toggles side ↔ compact */}
          <button
            className={"eg-chat-layout-btn" + (chatLayout === 'side' ? ' is-active' : '')}
            onClick={() => handleLayoutChange(chatLayout === 'side' ? 'compact' : 'side')}
            title={chatLayout === 'side' ? 'Back to compact' : 'Side panel'}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/>
              <line x1="10.5" y1="2.5" x2="10.5" y2="13.5"/>
            </svg>
          </button>
          {/* Half — chat takes the right half of the screen */}
          <button
            className={"eg-chat-layout-btn" + (chatLayout === 'half' ? ' is-active' : '')}
            onClick={() => handleLayoutChange('half')}
            title="Half screen">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/>
              <line x1="8" y1="2.5" x2="8" y2="13.5"/>
              <rect x="8" y="2.5" width="6.5" height="11" fill="currentColor" fillOpacity="0.26" stroke="none"/>
            </svg>
          </button>
          {/* Full — toggles full ↔ compact */}
          <button
            className={"eg-chat-layout-btn" + (chatLayout === 'full' ? ' is-active' : '')}
            onClick={() => handleLayoutChange(chatLayout === 'full' ? 'compact' : 'full')}
            title={chatLayout === 'full' ? 'Back to compact' : 'Full screen'}>
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="3,6 3,3 6,3"/>
              <polyline points="10,3 13,3 13,6"/>
              <polyline points="13,10 13,13 10,13"/>
              <polyline points="6,13 3,13 3,10"/>
            </svg>
          </button>
          </>)}
          <button className="eg-chat-close" onClick={onClose}
            title={chatLayout === 'stage-right' ? 'Minimize to edge' : 'Collapse'}>{EgI.close}</button>
        </div>
      </div>

      {/* Scroll area */}
      <div className="eg-chat-scroll" ref={scrollRef} onScroll={handleScroll}>
        {/* On the launch surface the transcript is empty: the mark, the
            greeting, the composer and the starting points are one stack in the
            bottom bar, so their order cannot drift apart. Laying the greeting
            out here instead meant reserving the composer's travel twice, in
            two elements, and it drifted below the composer. */}
        {isHome ? null : visible.map((msg, i) => (
          <EgChatMessage
            key={egMsgKey(msg, i)}
            msg={msg}
            isLatestAI={i === latestAIIdx && !working}
            /* 2026-07-06 — OR in `zenithTurn` (this turn's selected model,
               known instantly from the composer) for the newest AI line so
               the "hide the fast draft" decision applies from its very first
               render, not only once the backend's `syrix_model` stamp lands
               on the line a moment later. */
            zenith={zenithIdx.has(i) || (i === latestAIIdx && zenithTurn)}
          />
        ))}
        {/* The progress panel, above the answer it is producing — it stays up
            for the whole turn and grows a step at a time. `turnId` is the
            shell's own request id for this turn, which is what tells the
            ladder "same turn, more steps" from "a different question". */}
        {thinkVisible && (
          /* One ladder for all three modes. ZENITH used to mount its own
             violet sub-agent panel here while SPARK and AUTO rendered the
             step ladder, so the same work looked like two different
             products depending on which switcher position it ran under —
             and AUTO already showed the ladder even when it resolved to
             ZENITH, because this branch reads the composer rather than the
             turn. Zenith's one extra fact, how many sub-agents are
             deployed, arrives as an ordinary ladder step instead. */
          <div aria-live="polite" style={{ display: 'contents' }}>
            {typeof window !== 'undefined' && typeof window.SyrixSparkPulse === 'function'
              ? React.createElement(window.SyrixSparkPulse, {
                  working: !thinkClosing, closing: thinkClosing, turnId: pendingRid })
              : (
                <div className={"eg-think eg-think-min" + (thinkClosing ? ' is-closing' : '')}>
                  <span className="eg-think-pulse" aria-hidden="true" />
                  <span className="eg-think-label">Thinking…</span>
                </div>
              )}
          </div>
        )}
        {/* Live streaming bubble — real tokens as they arrive, under the
            progress panel above. */}
        {(streamText || liveImagesWanted || liveCard) && !ourTurnAnswered && (
          <div className="eg-msg eg-msg-ai">
            <div className="eg-msg-content">
              {liveCard && typeof window !== 'undefined' && typeof window.SyrixAdaptiveCard === 'function'
                && React.createElement(window.SyrixAdaptiveCard, { panel: liveCard, timeMs: Date.now() })}
              {liveImagesWanted && typeof window !== 'undefined' && window.SyrixAnswerMedia
                && React.createElement(window.SyrixAnswerMedia.Images, {
                  images: liveMedia.images || [], pending: liveMedia.images ? 0 : liveMedia.pending })}
              {streamText && (<div className="eg-msg-canvas is-animating">
                {/* 2026-07-20 — render the live token stream through the SAME
                    rich renderer as the finished answer (streaming:true), so
                    headings/tables/lists/code build up incrementally and the
                    hand-off to the committed message is a no-op instead of the
                    old plain→rich rebuild. Falls back to the syntax stripper
                    only when marked/DOMPurify are unavailable. */}
                {typeof window !== 'undefined' && typeof window.SyrixAnswerRenderer === 'function'
                  ? React.createElement(window.SyrixAnswerRenderer, { text: smoothStream, streaming: true })
                  : (typeof window !== 'undefined' && typeof window.SyrixStripStreamMd === 'function'
                      ? <span className="sxr-stream">{window.SyrixStripStreamMd(smoothStream)}</span>
                      : smoothStream)}
                <span className="eg-cursor-blink" />
              </div>)}
            </div>
          </div>
        )}
        {/* Last child of the transcript and `position: sticky`, so it rides
            the bottom edge of whatever is in view without a wrapper that
            would change the scroll container's layout. */}
        {showJump && !isHome && (
          <button type="button" className="eg-jump-latest" onClick={jumpToLatest}
            aria-label="Jump to the latest message" title="Jump to latest">
            <svg viewBox="0 0 16 16" aria-hidden="true"><polyline points="4,6 8,10 12,6" /></svg>
          </button>
        )}
      </div>

      {/* Scroll position + turn markers, on the right rim of the transcript. */}
      <EgScrollRail scrollRef={scrollRef} revision={visible.length + (working ? 1 : 0)} />

      {/* Composer — one unified liquid-glass pill, same visual language as the
          collapsed bottom bar (2026-07-02 revamp: previously this was a boxy
          textarea + loose floating buttons that looked like an older design
          the moment the panel opened). */}
      <div className={"eg-composer" + (composerDragOver ? ' eg-drag-over' : '')}
        onDragOver={e => { e.preventDefault(); if (!composerDragOver) setComposerDragOver(true); }}
        onDragLeave={e => { e.preventDefault(); setComposerDragOver(false); }}
        onDrop={e => {
          e.preventDefault(); setComposerDragOver(false);
          if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) onDropFiles(e.dataTransfer.files);
        }}>
        <input ref={fileInputRef} type="file" multiple style={{ display: 'none' }}
          onChange={onUploadChosen}
          accept="image/*,.pdf,.txt,.md,.rtf,.csv,.tsv,.doc,.docx,.odt,.xls,.xlsx,.ods,.ppt,.pptx,.odp,.json,.yaml,.yml,.xml,.html,.css,.sql,.py,.js,.ts,.tsx,.jsx,.cpp,.c,.h,.java,.go,.rs,.rb,.php,.sh,.zip,.7z,.rar,.heic" />

        {(chatLayout === 'side' || chatLayout === 'half' || chatLayout === 'full') && (
          <EgAttachTray attachments={attachments} onRemove={onRemoveAttachment}
            onRetry={onRetryAttachment} />
        )}

        <div className={"eg-composer-pill" + (model === 'zenith' ? ' zenith' : '') + (model === 'auto' ? ' auto' : '')}>
          <button
            className="eg-plus-btn"
            onClick={onUploadClick}
            title="Attach / upload">
            {EgI.plus}
          </button>

          {/* SPARK / ZENITH model switcher — dropdown, sticky across shells. */}
          <EgModelSwitcher model={model} onModelChange={onModelChange} />

          <EgMicWave active={listening} />

          <textarea
            ref={inputRef}
            className="eg-chat-ta"
            rows={1}
            value={draft}
            onChange={e => {
              setDraft(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
            }}
            onPaste={onComposerPaste}
            onKeyDown={handleKey}
            placeholder={deepResearch ? 'Deep Research mode — ask anything…' : model === 'zenith' ? 'Ask ZENITH anything…' : 'Talk to SYRIX…'}
          />

          {/* 2026-07-09 — chat-bar mic-mute button removed: Voice Mode is now
              a fully separate dedicated overlay (model switcher), so the
              normal-HUD composer never listens to the mic or speaks TTS on
              its own and doesn't need its own mute control anymore. */}
          <button
            className={"eg-send-btn" + ((working || isSpeaking) ? ' is-stop' : '')}
            onClick={(working || isSpeaking) ? onCancelTurn : onSubmit}
            disabled={!(working || isSpeaking) && !draft.trim() && !hasReadyAttach}
            title={(working || isSpeaking) ? 'Stop Syrix' : 'Send'}>
            {(working || isSpeaking) ? EgI.stop : EgI.send}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Brain switcher panel ────────────────────────────────────────────────── */
function EgBrainPanel({ brains, activeBrainId, onSelectBrain, onClose }) {
  return (
    <div className="eg-brain-panel" onClick={e => e.stopPropagation()}>
      <div className="eg-brain-head">
        <span className="eg-brain-title">{EgI.brain} BRAIN ROUTER</span>
        <button className="eg-brain-close" onClick={onClose}>{EgI.close}</button>
      </div>
      <div className="eg-brain-list">
        {(brains || []).map(b => {
          const active = b.locked || b.id === activeBrainId;
          const disabled = b.id !== 'auto' && !b.available;
          return (
            <div key={b.id}
              className={"eg-brain-row" + (active ? ' is-active' : '') + (disabled ? ' is-disabled' : '')}
              onClick={() => { if (!disabled) onSelectBrain?.(b.id); }}>
              <div className="eg-brain-info">
                <div className="eg-brain-label">{b.label}</div>
                <div className="eg-brain-sub">
                  {b.id === 'auto' ? 'Smart routing across providers'
                    : disabled ? (b.reason || 'Unavailable')
                    : active ? 'Active — click to unpin'
                    : 'Pin this provider'}
                </div>
              </div>
              <button
                type="button"
                className={"eg-toggle" + (active ? ' is-on' : '')}
                disabled={disabled}
                onClick={e => { e.stopPropagation(); if (!disabled) onSelectBrain?.(b.id); }}
                aria-pressed={active}
                title={active ? 'Active' : (disabled ? (b.reason || 'Unavailable') : 'Switch')}>
                <span className="eg-toggle-knob" />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ─── Widget menu ─────────────────────────────────────────────────────────── */
const EG_WIDGET_LIST = [
  { type: 'map',           icon: 'map',     label: 'Map' },
  { type: 'weather',       icon: 'weather', label: 'Weather' },
  { type: 'notes',         icon: 'notes',   label: 'Notes' },
  { type: 'music',         icon: 'wave',    label: 'Music' },
  { type: 'memory_graph',  icon: 'memory',  label: 'Memory' },
  { type: 'diagnostic',    icon: 'diag',    label: 'Diagnostics' },
  { type: 'drawing',       icon: 'apps',    label: 'Drawing' },
  { type: 'timer',         icon: 'sun',     label: 'Timer' },
];

function EgWidgetMenu({ onOpenWidget, onClose, onMouseEnter, onMouseLeave, phase, anchorTop }) {
  return (
    <div className={"eg-rail-flyout eg-widget-menu" + (phase === 'open' ? ' is-open' : phase === 'closing' ? ' is-closing' : '')}
      style={anchorTop != null ? { top: anchorTop } : undefined}
      onClick={e => e.stopPropagation()}
      onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave}>
      <div className="eg-wm-head">
        <span>WIDGETS</span>
        <button className="eg-wm-close" onClick={onClose}>{EgI.close}</button>
      </div>
      <div className="eg-wm-grid">
        {EG_WIDGET_LIST.map(w => (
          <button key={w.type} className="eg-wm-btn"
            onClick={() => { onOpenWidget(w.type); onClose(); }}
            title={w.label}>
            <span className="eg-wm-ico">{EgI[w.icon] || EgI.apps}</span>
            <span className="eg-wm-lbl">{w.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/* ─── Deck — the SYRIX ecosystem launch pad ─────────────────────────────────
   Grows out of the sidebar beside its row, like Widgets. One key per app in
   deck.json; pressing a key asks the main process to launch that app by id
   (syrix-deck.js), so SYRIX stays open and in use while the app starts. A
   dot marks apps that are already running where that can be checked. The
   list is cached between opens so the second open paints at once, and is
   refreshed while the panel is open so the running dots stay true. */
let _egDeckCache = null;

function useEgDeck(open) {
  const [apps, setApps] = useState(_egDeckCache);
  useEffect(() => {
    if (!open) return undefined;
    const api = window.syrixHud && window.syrixHud.deck;
    if (!api || !api.list) return undefined;
    let alive = true;
    function load() {
      api.list().then((rows) => {
        if (!alive || !Array.isArray(rows)) return;
        _egDeckCache = rows;
        setApps(rows);
      }).catch(() => {});
    }
    load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, [open]);
  return apps;
}

const EG_DECK_FAIL = {
  missing: 'was not found where the Deck expects it',
  forbidden: 'can only be launched from the SYRIX window',
  unknown: 'is not in the Deck any more',
};

function EgDeckMenu({ onClose, onMouseEnter, onMouseLeave, phase, anchorTop }) {
  const apps = useEgDeck(phase !== 'closing');
  const [pressed, setPressed] = useState({});
  const ref = useRef(null);
  const [top, setTop] = useState(anchorTop);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (anchorTop == null || !el) { setTop(anchorTop); return; }
    setTop(Math.max(12, Math.min(anchorTop - 10, window.innerHeight - el.offsetHeight - 12)));
  }, [anchorTop, apps && apps.length]);

  function press(app) {
    if (!app || pressed[app.id] === 'busy') return;
    if (!app.available) {
      egToastNotice(app.name + ' ' + EG_DECK_FAIL.missing + '.');
      return;
    }
    const api = window.syrixHud && window.syrixHud.deck;
    if (!api || !api.launch) return;
    setPressed(p => ({ ...p, [app.id]: 'busy' }));
    api.launch(app.id).then((res) => {
      const ok = !!(res && res.ok);
      setPressed(p => ({ ...p, [app.id]: ok ? 'done' : '' }));
      egToastNotice(ok
        ? (res.how === 'reopened' ? app.name + ' is already running — bringing it up.' : 'Opening ' + app.name + '…')
        : app.name + ' ' + (EG_DECK_FAIL[res && res.reason] || 'could not be opened') + '.');
      setTimeout(() => setPressed(p => ({ ...p, [app.id]: '' })), 1600);
    }).catch(() => setPressed(p => ({ ...p, [app.id]: '' })));
  }

  const list = Array.isArray(apps) ? apps : [];
  return (
    <div ref={ref}
      className={"eg-rail-flyout eg-deck-menu" + (phase === 'open' ? ' is-open' : phase === 'closing' ? ' is-closing' : '')}
      style={top != null ? { top } : undefined}
      onClick={e => e.stopPropagation()}
      onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave}
      role="dialog" aria-label="Deck">
      <div className="eg-deck-aura" aria-hidden="true" />
      <div className="eg-deck-head">
        <div className="eg-deck-titles">
          <span className="eg-deck-title">Deck</span>
          <span className="eg-deck-sub">The SYRIX ecosystem</span>
        </div>
        <button type="button" className="eg-wm-close" onClick={onClose} aria-label="Close Deck">{EgI.close}</button>
      </div>
      <div className="eg-deck-grid">
        {apps === null && [0, 1, 2, 3, 4].map(i => (
          <div key={i} className="eg-deck-slot is-skel" style={{ animationDelay: (i * 40) + 'ms' }}>
            <span className="eg-deck-key" /><span className="eg-deck-name">&nbsp;</span>
          </div>
        ))}
        {list.map((a, i) => {
          const state = pressed[a.id] || '';
          return (
            <button key={a.id} type="button"
              className={"eg-deck-slot" + (state ? ' is-' + state : '') + (a.available ? '' : ' is-missing') + (a.running ? ' is-running' : '')}
              style={{ '--deck-aura': a.aura || 'rgb(var(--eg-accent-rgb))', animationDelay: (60 + i * 45) + 'ms' }}
              onClick={() => press(a)}
              title={a.name + (a.tagline ? ' — ' + a.tagline : '') + (a.running ? ' · running' : '') + (a.available ? '' : ' · not found')}>
              <span className="eg-deck-key">
                <span className="eg-deck-glow" aria-hidden="true" />
                <img className="eg-deck-icon" src={a.icon} alt="" draggable="false" />
                {a.running && <span className="eg-deck-live" aria-label="Running" />}
                {state === 'busy' && <span className="eg-deck-spin" aria-hidden="true" />}
              </span>
              <span className="eg-deck-name">{a.short || a.name}</span>
              <span className="eg-deck-tag">{a.tagline}</span>
            </button>
          );
        })}
      </div>
      <div className="eg-deck-foot">
        {list.length ? list.length + ' in the SYRIX ecosystem' : 'Loading the ecosystem…'}
      </div>
    </div>
  );
}

/* ─── Browser nav pill (appears when HUD apps are open) ──────────────────── */
function EgBrowserNav({ apps, activeId }) {
  const app = apps?.find(a => a.id === activeId) || apps?.[0];
  const [closing, setClosing] = useState(false);
  const [overlay, setOverlay] = useState(false);
  if (!app && !overlay) return null;
  const nav = action => { try { window.syrixHud?.hudApp?.nav?.(app?.id, action); } catch (_) {} };
  function handleClose() {
    if (closing) return;
    setClosing(true);
    setOverlay(true);
    setTimeout(() => { try { window.syrixHud?.hudApp?.close?.(app?.id); } catch (_) {} }, 280);
    setTimeout(() => setOverlay(false), 520);
  }
  return (
    <>
      {overlay && <div className="eg-browser-close-overlay" />}
      {app && (
        <div className={"eg-browser-nav" + (closing ? ' is-closing' : '')}>
          <button className="eg-nav-btn" onClick={() => nav('back')} title="Back">{EgI.back}</button>
          <button className="eg-nav-btn" onClick={() => nav('forward')} title="Forward">{EgI.forward}</button>
          <button className="eg-nav-btn" onClick={() => nav('reload')} title="Refresh">{EgI.refresh}</button>
          <div className="eg-nav-url">
            <span className="eg-nav-url-text">{app.title || app.url || 'Browser'}</span>
          </div>
          <button className="eg-nav-btn" title="Open in system browser"
            onClick={() => { try { window.open(app.url || '', '_blank', 'noopener'); } catch (_) {} }}>
            {EgI.external}
          </button>
          <button className="eg-nav-btn eg-nav-close-btn" title="Close" onClick={handleClose}>
            {EgI.close}
          </button>
        </div>
      )}
    </>
  );
}

/* ─── Overlay preset pill (Clean / Research) ─────────────────────────────── */
function EgPresets({ active, onSet }) {
  const opts = [
    { id: 'clean',    label: 'Clean',    tip: 'Focused view' },
    { id: 'research', label: 'Research', tip: 'Research mode' },
  ];
  return (
    <div className="eg-presets">
      {opts.map(o => (
        <button key={o.id}
          className={"eg-preset-btn" + (active === o.id ? ' is-active' : '')}
          onClick={() => onSet(o.id)}
          title={o.tip}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ─── Delayed unmount + enter/open/close phase tracking — keeps a panel
   mounted through its CSS open/close-out transition instead of snapping in
   or vanishing the instant its `open` flag flips. Returns [mounted, phase]
   where phase is 'entering' (just mounted, still collapsed — one frame
   before the transition target class is applied), 'open' (fully expanded),
   or 'closing' (collapsing back out). Used by the rail flyouts
   (Widgets/News/Finance) so they grow/shrink like the rail itself instead
   of fading in place. ─────────────────────────────────────────────────── */
function useMenuMount(open, exitMs = 220) {
  const [mounted, setMounted] = useState(open);
  const [phase, setPhase] = useState(open ? 'open' : 'entering');
  useEffect(() => {
    if (open) {
      setMounted(true);
      setPhase('entering');
      const raf = requestAnimationFrame(() => requestAnimationFrame(() => setPhase('open')));
      return () => cancelAnimationFrame(raf);
    }
    if (!mounted) return undefined;
    setPhase('closing');
    const t = setTimeout(() => setMounted(false), exitMs);
    return () => clearTimeout(t);
  }, [open]);
  return [mounted, phase];
}

/* ─── Toast stack ─────────────────────────────────────────────────────────── */
let _egToastSeq = 0;
function EgToastStack({ toasts }) {
  if (!toasts?.length) return null;
  return (
    <div className="eg-toast-stack" aria-live="polite" aria-atomic="false">
      {toasts.map(t => (
        <div key={t.id} className={"eg-toast" + (t.tone === 'error' ? ' is-error' : t.tone === 'ok' ? ' is-ok' : '')}>
          {t.text}
        </div>
      ))}
    </div>
  );
}

/* ─── Chat history (chat-app style) ─────────────────────────────────── */
function egSessTime(tsSeconds) {
  const ts = Number(tsSeconds);
  if (!isFinite(ts) || ts <= 0) return '';
  const ms = ts * 1000, diff = Math.max(0, Date.now() - ms), min = Math.floor(diff / 60000);
  if (min < 1) return 'Just now';
  if (min < 60) return min + 'm ago';
  const hr = Math.floor(min / 60);
  if (hr < 24) return hr + 'h ago';
  const d = new Date(ms);
  if (hr < 48) return 'Yesterday';
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

// Dependency-free fuzzy match: in-order subsequence with a contiguous-run
// bonus (so "syrx web" ranks a run of consecutive hits above scattered ones),
// plus prefix/substring bonuses. Returns -1 when query isn't a subsequence.
function egFuzzyScore(query, text) {
  const q = String(query || '').toLowerCase().trim();
  const t = String(text || '').toLowerCase();
  if (!q) return 0;
  if (!t) return -1;
  let qi = 0, run = 0, score = 0;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) { qi++; run++; score += run; }
    else { run = 0; }
  }
  if (qi < q.length) return -1;
  if (t.indexOf(q) === 0) score += 20;
  else if (t.indexOf(q) > -1) score += 10;
  return score;
}

function egCacheSet(map, key, val, cap) {
  map.set(key, val);
  if (map.size > (cap || 30)) map.delete(map.keys().next().value);
}

const EG_HIST_FILTERS = [
  { id: 'all',    label: 'All' },
  { id: 'today',  label: 'Today' },
  { id: 'week',   label: 'Week' },
  { id: 'pinned', label: 'Pinned' },
  // 2026-07-26 — Supercomputer chats are their own kind of thing (a browser
  // session and a task history, not just words), so they get their own filter
  // and their own section rather than being mixed in with text chats.
  { id: 'supercomputer', label: 'Supercomputer' },
];

/* Per-row action menu (Rename / Pin / Delete).

   Portaled to document.body and placed from the "more" button's live rect.
   Rendered inside the row it was clipped by the chat list's overflow, so on
   the first and last rows it opened behind the list edge and could not be
   reached. It opens below the button and flips above when the space below
   cannot hold it, and it is clamped inside the window either way. Scrolling,
   resizing or Escape closes it rather than leaving it floating off its row. */
function EgHistRowMenu({ open, anchorRef, menuRef, pinned, onRename, onTogglePin, onDelete, onClose }) {
  const [mounted, phase] = useMenuMount(open, 170);
  const [pos, setPos] = useState(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  React.useLayoutEffect(() => {
    if (!open) return undefined;
    function place() {
      const a = anchorRef && anchorRef.current;
      if (!a) return;
      const r = a.getBoundingClientRect();
      const el = menuRef && menuRef.current;
      const h = (el && el.offsetHeight) || 126;
      const w = (el && el.offsetWidth) || 168;
      const gap = 6, pad = 8;
      const below = window.innerHeight - r.bottom - pad;
      const up = below < h + gap && (r.top - pad) > below;
      const top = Math.max(pad, Math.min(up ? r.top - gap - h : r.bottom + gap, window.innerHeight - h - pad));
      const left = Math.max(pad, Math.min(r.right - w, window.innerWidth - w - pad));
      setPos({ top: Math.round(top), left: Math.round(left), up });
    }
    place();
    const raf = requestAnimationFrame(place);
    function onScroll(e) {
      if (menuRef && menuRef.current && menuRef.current.contains(e.target)) return;
      closeRef.current && closeRef.current();
    }
    function onKey(e) { if (e.key === 'Escape') closeRef.current && closeRef.current(); }
    window.addEventListener('resize', onScroll);
    window.addEventListener('scroll', onScroll, true);
    document.addEventListener('keydown', onKey);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', onScroll);
      window.removeEventListener('scroll', onScroll, true);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  if (!mounted || !pos || typeof ReactDOM === 'undefined' || !ReactDOM.createPortal) return null;
  return ReactDOM.createPortal(
    <div ref={menuRef} role="menu"
      className={"eg-hist-row-menu" + (pos.up ? ' opens-up' : '') + (phase === 'open' ? ' is-open' : phase === 'closing' ? ' is-closing' : '')}
      style={{ top: pos.top, left: pos.left }}
      onClick={e => e.stopPropagation()} onMouseDown={e => e.stopPropagation()}>
      <button type="button" role="menuitem" className="eg-hist-row-menu-item" onClick={onRename}>
        <span className="eg-hist-row-menu-ico">{EgI.edit}</span>Rename
      </button>
      <button type="button" role="menuitem" className="eg-hist-row-menu-item" onClick={onTogglePin}>
        <span className="eg-hist-row-menu-ico">{pinned ? EgI.pinFill : EgI.pin}</span>{pinned ? 'Unpin' : 'Pin'}
      </button>
      <div className="eg-hist-row-menu-sep" />
      <button type="button" role="menuitem" className="eg-hist-row-menu-item is-danger" onClick={onDelete}>
        <span className="eg-hist-row-menu-ico">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="2.5,4 13.5,4"/><path d="M5.5 4V2.7h5V4"/>
            <path d="M4 4l.7 9.3h6.6L12 4"/><line x1="6.6" y1="6.4" x2="6.6" y2="11"/><line x1="9.4" y1="6.4" x2="9.4" y2="11"/>
          </svg>
        </span>
        Delete
      </button>
    </div>,
    document.body,
  );
}

/* Hover preview — sits to the right of the history list, level with the row
   under the pointer, and glides between rows rather than jumping. Fetches the
   transcript on hover (not on click) through the shared session cache, so the
   next click opens the chat instantly. */
function EgHistoryPreview({ open, loading, data, anchorTop, onContinue, onMouseEnter, onMouseLeave }) {
  const [mounted, phase] = useMenuMount(open, 180);
  const ref = useRef(null);
  const placedRef = useRef(false);
  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) { placedRef.current = false; return; }
    if (anchorTop == null) return;
    const top = Math.round(Math.max(56, Math.min(anchorTop - 12, window.innerHeight - el.offsetHeight - 14)));
    if (!placedRef.current) {
      // First placement lands without gliding in from the stylesheet default.
      el.style.transition = 'none';
      el.style.top = top + 'px';
      void el.offsetHeight;
      el.style.transition = '';
      placedRef.current = true;
    } else {
      el.style.top = top + 'px';
    }
  });
  if (!mounted) return null;
  const msgs = (data && Array.isArray(data.messages)) ? data.messages.slice(-8) : [];
  return (
    <div ref={ref} className={"eg-hist-preview" + (phase === 'open' ? ' is-open' : phase === 'closing' ? ' is-closing' : '')}
      onClick={e => e.stopPropagation()}
      onMouseEnter={onMouseEnter} onMouseLeave={onMouseLeave}>
      <div className="eg-hist-preview-head">{(data && data.title) || 'Chat preview'}</div>
      <div className="eg-hist-preview-body" key={(data && data.id) || 'loading'}>
        {loading && !data && (
          <div className="eg-hist-preview-skel" aria-label="Loading">
            <span /><span /><span />
          </div>
        )}
        {!loading && data && !msgs.length && <div className="eg-hist-preview-empty">No messages yet.</div>}
        {msgs.map((m, i) => {
          const role = String(m.role || m.speaker || '').toLowerCase();
          const isUser = role === 'user';
          const text = String(m.text || m.message || m.content || '').trim();
          if (!text) return null;
          return (
            <div key={i} className={"eg-hist-preview-msg" + (isUser ? ' is-user' : '')}>
              <span className="eg-hist-preview-role">{isUser ? 'You' : 'SYRIX'}</span>
              <span className="eg-hist-preview-text">{text.length > 170 ? text.slice(0, 169) + '…' : text}</span>
            </div>
          );
        })}
      </div>
      <button type="button" className="eg-hist-preview-continue" disabled={!data} onClick={onContinue}>
        Continue chat
      </button>
    </div>
  );
}

/* Chat history — the lower half of the sidebar (search, filters, sections,
   rename/pin/delete, hover preview). It is a section of the nav, not a drawer
   over the HUD: the list is where you pick a conversation from, so it lives
   permanently beside the conversation you are in. */
function EgSidebarChats({ sessions, loading, onLoad, onDelete, onClearAll, collapsed, onToggleCollapsed, onPin, onRename, onPeek, sessionCacheRef, searchRef, footer }) {
  const list = Array.isArray(sessions) ? sessions : [];
  const [confirmClear, setConfirmClear] = React.useState(false);
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const [openMenuId, setOpenMenuId] = useState(null);
  const [renamingId, setRenamingId] = useState(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [deletingIds, setDeletingIds] = useState(() => new Set());
  const [hoveredId, setHoveredIdState] = useState(null);
  const [previewData, setPreviewData] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const hoveredIdRef = useRef(null);
  const hoverOpenTimer = useRef(null);
  const hoverCloseTimer = useRef(null);
  const menuBtnRefs = useRef({});
  const rowMenuRef = useRef(null);
  const [previewTop, setPreviewTop] = useState(null);
  const renameInputRef = useRef(null);

  // Debounce search input (150-250ms per spec).
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 220);
    return () => clearTimeout(t);
  }, [query]);

  function setHovered(id) { hoveredIdRef.current = id; setHoveredIdState(id); }

  // Hover-preload responses arrive as a window event from the bridge (keyed,
  // not part of shared app state — see syrix-bridge.js chat_session_peeked).
  useEffect(() => {
    function onPeeked(e) {
      const d = e && e.detail; if (!d || !d.id) return;
      const sid = String(d.id);
      const entry = { title: d.title, messages: d.messages || [] };
      if (sessionCacheRef && sessionCacheRef.current) egCacheSet(sessionCacheRef.current, sid, entry, 30);
      if (hoveredIdRef.current && String(hoveredIdRef.current) === sid) {
        setPreviewLoading(false);
        setPreviewData({ id: sid, title: entry.title, messages: entry.messages });
      }
    }
    window.addEventListener('syrix:session-peeked', onPeeked);
    return () => window.removeEventListener('syrix:session-peeked', onPeeked);
  }, [sessionCacheRef]);

  // Close an open row menu on outside click. The menu is portaled out of the
  // row, so a click inside it is checked separately from its button.
  useEffect(() => {
    if (!openMenuId) return;
    function onDocDown(e) {
      const btn = menuBtnRefs.current[openMenuId];
      if (btn && btn.contains(e.target)) return;
      if (rowMenuRef.current && rowMenuRef.current.contains(e.target)) return;
      setOpenMenuId(null);
    }
    document.addEventListener('mousedown', onDocDown, true);
    return () => document.removeEventListener('mousedown', onDocDown, true);
  }, [openMenuId]);

  useEffect(() => () => {
    if (hoverOpenTimer.current) clearTimeout(hoverOpenTimer.current);
    if (hoverCloseTimer.current) clearTimeout(hoverCloseTimer.current);
  }, []);

  function handleRowEnter(s, rowEl) {
    if (hoverCloseTimer.current) { clearTimeout(hoverCloseTimer.current); hoverCloseTimer.current = null; }
    if (hoverOpenTimer.current) clearTimeout(hoverOpenTimer.current);
    // Short enough to feel immediate, long enough that sweeping the pointer
    // down the list does not open a preview for every row it crosses. Once a
    // preview is showing, moving to the next row switches it sooner.
    const delay = hoveredIdRef.current ? 50 : 90;
    hoverOpenTimer.current = setTimeout(() => {
      try { if (rowEl) setPreviewTop(rowEl.getBoundingClientRect().top); } catch (_) {}
      setHovered(s.id);
      const cached = sessionCacheRef && sessionCacheRef.current && sessionCacheRef.current.get(String(s.id));
      if (cached) {
        setPreviewLoading(false);
        setPreviewData({ id: s.id, title: cached.title || s.title, messages: cached.messages });
      } else {
        setPreviewLoading(true);
        setPreviewData(null);
        onPeek && onPeek(s.id);
      }
    }, delay);
  }
  function handleRowLeave() {
    if (hoverOpenTimer.current) { clearTimeout(hoverOpenTimer.current); hoverOpenTimer.current = null; }
    if (hoverCloseTimer.current) clearTimeout(hoverCloseTimer.current);
    // The transcript stays in place while the panel fades out; the next
    // hover replaces it.
    hoverCloseTimer.current = setTimeout(() => {
      setHovered(null); setPreviewLoading(false);
    }, 160);
  }
  function handlePreviewEnter() {
    if (hoverCloseTimer.current) { clearTimeout(hoverCloseTimer.current); hoverCloseTimer.current = null; }
  }

  function handleLoad(s) {
    const cached = sessionCacheRef && sessionCacheRef.current && sessionCacheRef.current.get(String(s.id));
    // 2026-07-24 — carry the session kind through so a saved VOICE conversation
    // reopens in the voice-chat surface. Resolve it from the list even when the
    // caller only had an id (the preview "continue" button).
    const kind = (s && s.kind) || ((sessions || []).find(x => String(x && x.id) === String(s.id)) || {}).kind || '';
    onLoad(s.id, cached ? cached.messages : null, kind);
  }
  function handleDelete(id) {
    if (!id || deletingIds.has(id)) return;
    setDeletingIds(prev => { const n = new Set(prev); n.add(id); return n; });
    onDelete(id);
  }
  function startRename(s) {
    setOpenMenuId(null);
    setRenamingId(s.id);
    setRenameDraft(s.title || '');
    setTimeout(() => { try { renameInputRef.current?.focus(); renameInputRef.current?.select(); } catch (_) {} }, 30);
  }
  function commitRename(s) {
    const t = renameDraft.trim();
    if (t && t !== s.title) onRename(s.id, t);
    setRenamingId(null);
  }

  const filtered = useMemo(() => {
    const now = Date.now(), DAY = 86400000;
    let out = list;
    if (filter === 'pinned') out = out.filter(s => s.pinned);
    else if (filter === 'today') out = out.filter(s => (now - (Number(s.updated_ts) || 0) * 1000) < DAY);
    else if (filter === 'week') out = out.filter(s => (now - (Number(s.updated_ts) || 0) * 1000) < DAY * 7);
    else if (filter === 'supercomputer') out = out.filter(s => s.kind === 'supercomputer');
    const q = debouncedQuery.trim();
    if (q) {
      out = out
        .map(s => ({ s, score: Math.max(egFuzzyScore(q, s.title), egFuzzyScore(q, s.preview)) }))
        .filter(x => x.score >= 0)
        .sort((a, b) => b.score - a.score)
        .map(x => x.s);
    }
    return out;
  }, [list, filter, debouncedQuery]);

  // The backend opens a Supercomputer session on mode entry, so without this
  // an empty "Supercomputer" row appears the moment the workspace mounts,
  // before anything has been asked.
  const superSessions = useMemo(
    () => filtered.filter(s => s.kind === 'supercomputer' && (s.preview || (s.message_count || 0) > 1)),
    [filtered]);
  // NOTE the `!== 'supercomputer'` half: without it every Supercomputer chat
  // would render twice, once in its own section and once under Chats.
  // Chats saved by the retired voice screen (kind 'voice') are ordinary chats
  // now and list with the rest; empty ones it left behind stay hidden.
  const textSessions = useMemo(
    () => filtered.filter(s => s.kind !== 'supercomputer'
      && (s.kind !== 'voice' || s.preview || (s.message_count || 0) > 1)),
    [filtered]);

  function renderHistRow(s, i) {
    const isVoice = s.kind === 'voice';
    const isSuper = s.kind === 'supercomputer';
    return (
      <div key={s.id || i}
        className={"eg-hist-row" + (isVoice ? ' is-voice' : '') + (isSuper ? ' is-super' : '') + (s.active ? ' is-active' : '') + (deletingIds.has(s.id) ? ' is-deleting' : '') + (openMenuId === s.id ? ' has-menu-open' : '')}
        style={{ animationDelay: (Math.min(i, 14) * 24) + 'ms' }}
        onClick={() => (renamingId === s.id ? null : handleLoad(s))}
        onMouseEnter={e => handleRowEnter(s, e.currentTarget)}
        onMouseLeave={handleRowLeave}
        /* One line per chat. The preview text and the message count used to
           render as two more lines inside every row, which turned a list of
           thirty chats into a wall; they are in this tooltip and in the hover
           preview panel, which shows the real transcript. */
        title={[s.title || 'New chat',
                (s.preview && s.preview !== s.title) ? s.preview : '',
                [egSessTime(s.updated_ts), (s.message_count || 0) + ' messages'].filter(Boolean).join(' · ')]
               .filter(Boolean).join('\n')}>
        <div className="eg-hist-row-main">
          {renamingId === s.id ? (
            <input
              ref={renameInputRef}
              className="eg-hist-row-rename-input"
              value={renameDraft}
              onClick={e => e.stopPropagation()}
              onChange={e => setRenameDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') commitRename(s);
                else if (e.key === 'Escape') setRenamingId(null);
              }}
              onBlur={() => commitRename(s)}
            />
          ) : (
            <div className="eg-hist-row-title">
              {isVoice && <span className="eg-hist-row-voice-ico" style={{ pointerEvents: 'none' }}>{EgI.mic}</span>}
              {isSuper && <img className="eg-hist-row-super-ico" src="assets/mode-icons/supercomputer.png"
                               alt="" draggable="false" style={{ pointerEvents: 'none' }} />}
              {s.pinned && <span className="eg-hist-row-pin" style={{ pointerEvents: 'none' }}>{EgI.pinFill}</span>}
              {s.title || 'New chat'}
            </div>
          )}
        </div>
        <div className="eg-hist-row-actions">
          <button className="eg-hist-more" title="More" aria-haspopup="menu" aria-expanded={openMenuId === s.id}
            ref={el => { menuBtnRefs.current[s.id] = el; }}
            onClick={e => { e.stopPropagation(); setOpenMenuId(v => v === s.id ? null : s.id); }}>
            <span style={{ pointerEvents: 'none' }}>{EgI.more}</span>
          </button>
          <EgHistRowMenu
            open={openMenuId === s.id}
            anchorRef={{ current: menuBtnRefs.current[s.id] || null }}
            menuRef={rowMenuRef}
            onClose={() => setOpenMenuId(null)}
            pinned={!!s.pinned}
            onRename={e => { e.stopPropagation(); startRename(s); }}
            onTogglePin={e => { e.stopPropagation(); onPin(s.id, !s.pinned); setOpenMenuId(null); }}
            onDelete={e => { e.stopPropagation(); setOpenMenuId(null); handleDelete(s.id); }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className={"eg-chats" + (collapsed ? ' is-collapsed' : '')}>
      <div className="eg-chats-inner">
        <button
          type="button"
          className="eg-chats-toggle"
          aria-expanded={!collapsed}
          onClick={onToggleCollapsed}
          title={collapsed ? 'Show chats' : 'Hide chats'}>
          <span className="eg-chats-caret" aria-hidden="true">{EgI.caret}</span>
          <span className="eg-chats-label">Chats</span>
          <span className="eg-chats-count">{list.length || ''}</span>
        </button>
        <div className="eg-hist-search">
          <span className="eg-hist-search-ico" style={{ pointerEvents: 'none' }}>{EgI.search}</span>
          <input
            ref={searchRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search chats…"
            className="eg-hist-search-input"
          />
        </div>
        <div className="eg-hist-filters">
          {EG_HIST_FILTERS.map(f => (
            <button key={f.id}
              className={"eg-hist-filter" + (filter === f.id ? ' is-active' : '')}
              onClick={() => setFilter(f.id)}>
              {f.label}
            </button>
          ))}
        </div>
        <div className="eg-hist-list">
          {filtered.length === 0 && loading && list.length === 0 && (
            <div className="eg-hist-skeleton" aria-label="Loading chats">
              {[0, 1, 2, 3, 4].map(i => <span key={i} style={{ animationDelay: (i * 90) + 'ms', width: (88 - i * 9) + '%' }} />)}
            </div>
          )}
          {filtered.length === 0 && !(loading && list.length === 0) && (
            <div className="eg-hist-empty">
              {list.length === 0 ? 'No past chats yet.' : 'No chats match.'}
            </div>
          )}
          {superSessions.length > 0 && (
            <div className="eg-hist-section-label eg-hist-section-super">
              <img src="assets/mode-icons/supercomputer.png" alt="" draggable="false"
                   style={{ pointerEvents: 'none' }} /> Supercomputer
            </div>
          )}
          {superSessions.map((s, i) => renderHistRow(s, i))}
          {superSessions.length > 0 && textSessions.length > 0 && (
            <div className="eg-hist-section-label">Chats</div>
          )}
          {textSessions.map((s, i) => renderHistRow(s, i))}
        </div>
        {list.length > 0 && onClearAll && (
          confirmClear ? (
            <div className="eg-hist-clear-confirm">
              <span>Delete all chats?</span>
              <button
                className="eg-hist-clear-all is-danger"
                onClick={e => { e.stopPropagation(); onClearAll(); setConfirmClear(false); }}>
                Delete
              </button>
              <button
                className="eg-hist-clear-all"
                onClick={e => { e.stopPropagation(); setConfirmClear(false); }}>
                Cancel
              </button>
            </div>
          ) : (
            <button className="eg-hist-clear-all"
              onClick={e => { e.stopPropagation(); setConfirmClear(true); }}
              title="Delete all saved chats permanently">
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ pointerEvents: 'none' }}>
                <polyline points="2.5,4 13.5,4"/><path d="M5.5 4V2.7h5V4"/>
                <path d="M4 4l.7 9.3h6.6L12 4"/>
              </svg>
              Clear all chats
            </button>
          )
        )}
      </div>
      {footer ? <div className="eg-footer-slot">{footer}</div> : null}
      <EgHistoryPreview
        open={!!hoveredId && !openMenuId}
        loading={previewLoading}
        data={previewData}
        anchorTop={previewTop}
        onMouseEnter={handlePreviewEnter}
        onMouseLeave={handleRowLeave}
        onContinue={() => {
          if (!previewData) return;
          handleLoad({ id: previewData.id });
        }}
      />
    </div>
  );
}


/* ═══════════════════════════════════════════════════════════════════════════
   VOICE — an orb over the open chat.

   Voice is a way of talking in this chat, not a separate place. The orb sits
   above the composer, every spoken turn is written into the thread as an
   ordinary message (and saved with it), and typing still works while it is
   up. The conversation runs here in the renderer:

     echo-cancelled mic → end of speech after a pause → local Whisper
     (voice_transcribe) → an ordinary chat turn marked voice_reply, always
     Spark → the reply as it streams → local Kokoro (voice_speak), one
     sentence at a time, each played the moment it arrives.

   Talking over SYRIX interrupts it: playback stops, the turn is cancelled,
   and what was just said becomes the next turn. A pause mid-thought that is
   followed by more speech before the transcript is back is treated as one
   utterance, not two.
   ═══════════════════════════════════════════════════════════════════════════ */
const EG_VOICE = {
  endSilenceMs: 720,   // the pause that ends a turn
  minSpeechMs: 300,    // shorter than this is a cough or a click, not a turn
  maxUtterMs: 30000,
  preRollMs: 360,      // audio kept from just before speech was detected
  startFrames: 3,      // consecutive loud frames (~43 ms each) that start speech
  bargeFrames: 6,      // louder, longer speech needed to talk over a reply
  floor: 0.005,        // the quietest level ever treated as speech
  thinkTimeoutMs: 120000,
  replyWaitMs: 15000,  // how long to wait for an answer line after "completed"
};

// Whisper, fed near-silence, is known to produce these stock phrases.
const EG_VOICE_PHANTOM = /^(?:thank you[.!]?|thanks for watching[.!]?|you|bye[.!]?|\.+|so\.?|okay\.?)$/i;

function egVoicePcm16k(chunks, rate) {
  let total = 0;
  for (const c of chunks) total += c.length;
  const ratio = rate / 16000;
  const outLen = Math.max(0, Math.floor(total / ratio));
  const out = new Int16Array(outLen);
  const flat = new Float32Array(total);
  let off = 0;
  for (const c of chunks) { flat.set(c, off); off += c.length; }
  for (let i = 0; i < outLen; i++) {
    // Box-filter each output sample over its source span: a cheap low-pass
    // that keeps 48 kHz → 16 kHz from aliasing into Whisper's band.
    const a = Math.floor(i * ratio), b = Math.min(total, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = a; j < b; j++) sum += flat[j];
    const s = Math.max(-1, Math.min(1, b > a ? sum / (b - a) : 0));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function egVoiceB64(int16) {
  const bytes = new Uint8Array(int16.buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

// Index just past the last complete sentence in `text` (0 when none).
function egVoiceSentenceCut(text) {
  const re = /[.!?…]["')\]]?(?=\s)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) last = m.index + m[0].length;
  return last;
}

// A one-line notice in the shell's toast stack, for things the user tried
// that cannot happen right now.
function egToastNotice(text) {
  try { window.dispatchEvent(new CustomEvent('syrix-shell-toast', { detail: { text: String(text || '') } })); } catch (_) {}
}

function egVoiceNorm(t) { return String(t || '').replace(/\s+/g, ' ').trim(); }

function egVoiceSend(command, data) {
  try { return !!window.SyrixBridge?.send?.(command, { data: data || {} }); } catch (_) { return false; }
}

/* The engine. `submitRef.current(text)` sends a voice turn and returns
   { rid, promise }; `cancelRef.current()` cancels the chat turn in flight. */
function useEgVoice(submitRef, cancelRef) {
  const [active, setActive] = useState(false);
  const [phase, setPhaseState] = useState('off');
  const [muted, setMuted] = useState(false);
  const [note, setNote] = useState('');
  const levelRef = useRef(0);
  const st = useRef({ phase: 'off' }).current;

  const setPhase = useCallback((p) => { st.phase = p; setPhaseState(p); }, []);

  function stopPlayback() {
    try { st.playing && st.playing.stop(); } catch (_) {}
    st.playing = null;
    st.queue = [];
  }

  function endTurn() {
    clearTimeout(st.thinkTimer);
    st.turn = null;
    stopPlayback();
    if (st.phase !== 'off') setPhase(st.muted ? 'muted' : st.nomic ? 'nomic' : (st.capturing ? 'hearing' : 'listening'));
  }

  // Cut SYRIX off: whatever it was saying or about to say is dropped.
  // `keepChat` is for a new turn replacing this one — the bridge has already
  // superseded the old chat turn, and cancelling "the active turn" here would
  // cancel the new one instead.
  function interrupt(keepChat) {
    const turn = st.turn;
    if (!turn) return;
    st.turn = null;
    clearTimeout(st.thinkTimer);
    stopPlayback();
    if (turn.id) egVoiceSend('voice_stop', { turn_id: turn.id });
    if (!turn.final && !keepChat) { try { cancelRef.current && cancelRef.current(); } catch (_) {} }
  }

  function speakPiece(turn, text, final) {
    if (!turn.id) return;
    egVoiceSend('voice_speak', { turn_id: turn.id, chunk_seq: turn.seq++, text, final: !!final });
  }

  // Speak whole sentences out of the streamed answer as soon as they exist.
  function onStream(turn, text) {
    if (turn.final || turn.broken) return;
    if (!text.startsWith(turn.spoken)) { turn.broken = true; return; }
    const tail = text.slice(turn.spoken.length);
    const cut = egVoiceSentenceCut(tail);
    if (cut <= 0 || (!turn.spoken && cut < 12)) return;
    const piece = tail.slice(0, cut);
    turn.spoken += piece;
    speakPiece(turn, piece, false);
  }

  // A reply with nothing left to add: let what is already queued play to the
  // end rather than cutting it off, or go back to listening.
  function finishTurn(turn) {
    turn.final = true;
    if (turn.spoken) speakPiece(turn, '', true);
    else endTurn();
  }

  function onFinal(turn, reply) {
    if (st.turn !== turn) return;
    let finalText = String(reply || '');
    if (!finalText.trim()) {
      // The turn settled without its text. If it completed, the answer is in
      // (or about to reach) the transcript: wait for it there. Clearing the
      // playback here is what cut SYRIX off mid-sentence, and answering
      // nothing is what left some voice turns silent.
      const B = window.SyrixBridge;
      const outcome = String((B && B.chatTurnOutcome && B.chatTurnOutcome(turn.id)) || '');
      if (/^(cancelled|canceled|superseded|offline|session_deleted)$/.test(outcome)) { finishTurn(turn); return; }
      const fromChat = (B && B.assistantReplyText && B.assistantReplyText(turn.id)) || '';
      if (fromChat) finalText = fromChat;
      else {
        // Only a turn that completed has an answer still on its way; a failed
        // or timed-out one does not, so voice goes straight back to listening.
        const coming = !outcome || /^(completed|complete|late_reply|assistant_reply)$/.test(outcome);
        if (!coming) { finishTurn(turn); return; }
        if (!turn.waitUntil) turn.waitUntil = Date.now() + EG_VOICE.replyWaitMs;
        if (Date.now() < turn.waitUntil) { setTimeout(() => onFinal(turn, ''), 200); return; }
        finishTurn(turn);
        return;
      }
    }
    turn.final = true;
    let rest = finalText;
    if (turn.spoken) {
      const said = egVoiceNorm(turn.spoken), full = egVoiceNorm(finalText);
      if (full.startsWith(said)) rest = full.slice(said.length);
      else {
        // The finished answer was reworded after streaming began. Skip as
        // many sentences as were already spoken and say the rest.
        const spokenCount = (said.match(/[.!?…](?=\s|$)/g) || []).length;
        const parts = full.split(/(?<=[.!?…])\s+/);
        rest = parts.slice(spokenCount).join(' ');
      }
    }
    speakPiece(turn, rest, true);
  }

  function beginTurn(rid, promise) {
    interrupt(true);
    const turn = { id: String(rid || ''), seq: 0, spoken: '', final: false, broken: false, ended: false };
    st.turn = turn;
    setPhase('thinking');
    clearTimeout(st.thinkTimer);
    st.thinkTimer = setTimeout(() => { if (st.turn === turn) endTurn(); }, EG_VOICE.thinkTimeoutMs);
    if (!turn.id) { endTurn(); return; }
    Promise.resolve(promise).then(reply => onFinal(turn, reply), () => onFinal(turn, ''));
  }

  function sendUtterance(chunks, speechMs) {
    if (speechMs < EG_VOICE.minSpeechMs) {
      setPhase(st.muted ? 'muted' : (st.turn ? st.phase : 'listening'));
      return;
    }
    st.lastUtter = chunks;
    st.lastSpeechMs = speechMs;
    const pcm = egVoicePcm16k(chunks, st.rate || 48000);
    const rid = 'vt_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    st.transRid = rid;
    setPhase('transcribing');
    if (!egVoiceSend('voice_transcribe', { audio_b64: egVoiceB64(pcm), sample_rate: 16000, request_id: rid })) {
      st.transRid = '';
      setNote('SYRIX is offline');
      setPhase('listening');
    }
  }

  function onFrame(buf) {
    const n = buf.length;
    let sum = 0;
    for (let i = 0; i < n; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / n);
    const frameMs = n / (st.rate || 48000) * 1000;
    if (st.muted) { levelRef.current = 0; return; }
    const thr = Math.max(EG_VOICE.floor, st.noise * 2.8);
    const frame = new Float32Array(buf);
    if (st.phase !== 'speaking') levelRef.current = Math.min(1, rms / (thr * 5));

    if (!st.capturing) {
      if (rms < thr) st.noise = st.noise * 0.96 + rms * 0.04;
      st.pre.push(frame); st.preMs += frameMs;
      while (st.preMs > EG_VOICE.preRollMs && st.pre.length > 1) { st.pre.shift(); st.preMs -= frameMs; }
      // While SYRIX is busy, speech has to be clearly louder and longer to
      // count, so its own voice leaking past echo cancellation does not
      // interrupt it.
      const busy = !!st.turn || st.phase === 'transcribing';
      const bar = busy ? Math.max(thr * 2.2, 0.025) : thr;
      st.loudRun = rms > bar ? st.loudRun + 1 : 0;
      if (st.loudRun < (busy ? EG_VOICE.bargeFrames : EG_VOICE.startFrames)) return;
      let head = st.pre;
      if (st.phase === 'transcribing' && st.transRid && st.lastUtter) {
        // More speech before the transcript arrived: one utterance, not two.
        head = st.lastUtter.concat(st.pre);
        st.speechMs = st.lastSpeechMs || 0;
        st.transRid = '';
      } else {
        st.speechMs = 0;
      }
      if (st.turn) interrupt();
      st.capturing = true;
      st.cap = head.slice();
      st.capMs = st.preMs;
      st.speechMs += st.loudRun * frameMs;
      st.silentMs = 0;
      st.pre = []; st.preMs = 0; st.loudRun = 0;
      setPhase('hearing');
      return;
    }
    st.cap.push(frame); st.capMs += frameMs;
    if (rms > thr) { st.speechMs += frameMs; st.silentMs = 0; } else st.silentMs += frameMs;
    if (st.silentMs >= EG_VOICE.endSilenceMs || st.capMs >= EG_VOICE.maxUtterMs) {
      const chunks = st.cap, speechMs = st.speechMs;
      st.capturing = false; st.cap = []; st.capMs = 0; st.speechMs = 0; st.silentMs = 0;
      sendUtterance(chunks, speechMs);
    }
  }

  function playNext() {
    if (st.playing || !st.queue.length || !st.queue[0].buffer) {
      if (!st.playing && !st.queue.length && st.turn && st.turn.ended) endTurn();
      return;
    }
    const item = st.queue.shift();
    try {
      const node = st.out.createBufferSource();
      node.buffer = item.buffer;
      node.connect(st.analyser);
      node.onended = () => {
        if (st.playing !== node) return;
        st.playing = null;
        playNext();
      };
      st.playing = node;
      node.start();
      if (st.phase !== 'hearing') setPhase('speaking');
    } catch (_) { st.playing = null; playNext(); }
  }

  useEffect(() => {
    function onTranscript(e) {
      const d = e && e.detail;
      if (!d || !st.transRid || String(d.request_id || '') !== st.transRid) return;
      st.transRid = '';
      const text = String(d.text || '').trim();
      if (!d.ok || !text || (EG_VOICE_PHANTOM.test(text) && (st.lastSpeechMs || 0) < 1200)) {
        if (st.phase === 'transcribing') setPhase(st.muted ? 'muted' : 'listening');
        return;
      }
      setNote('');
      // submit adopts the turn (adoptTurn) once it has a request id; a
      // message held for a file still being read is adopted when it is sent.
      let res = null;
      try { res = submitRef.current && submitRef.current(text); } catch (_) { res = null; }
      if (!res || !res.rid) setPhase(res ? 'thinking' : (st.muted ? 'muted' : 'listening'));
    }
    function onToken(e) {
      const d = e && e.detail;
      const turn = st.turn;
      if (!d || d.done || !turn || !turn.id || String(d.rid || d.request_id || '') !== turn.id) return;
      onStream(turn, String(d.text || ''));
    }
    function onAudio(e) {
      const d = e && e.detail;
      const turn = st.turn;
      if (!d || !turn || String(d.turn_id || '') !== turn.id) return;
      if (d.end) { turn.ended = true; playNext(); return; }
      if (!d.ok || !d.audio_b64 || !st.out) return;
      const item = { buffer: null };
      st.queue.push(item);
      try {
        const raw = atob(d.audio_b64);
        const bytes = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
        st.out.decodeAudioData(bytes.buffer).then((buf) => {
          if (st.turn !== turn) return;
          item.buffer = buf;
          playNext();
        }, () => { st.queue = st.queue.filter(q => q !== item); playNext(); });
      } catch (_) { st.queue = st.queue.filter(q => q !== item); }
    }
    window.addEventListener('syrix:voice-transcript', onTranscript);
    window.addEventListener('syrix-chat-token', onToken);
    window.addEventListener('syrix:voice-audio', onAudio);
    return () => {
      window.removeEventListener('syrix:voice-transcript', onTranscript);
      window.removeEventListener('syrix-chat-token', onToken);
      window.removeEventListener('syrix:voice-audio', onAudio);
    };
  }, []);

  function teardown() {
    interrupt();
    try { st.proc && st.proc.disconnect(); } catch (_) {}
    try { st.src && st.src.disconnect(); } catch (_) {}
    try { st.stream && st.stream.getTracks().forEach(t => t.stop()); } catch (_) {}
    try { st.ctx && st.ctx.close(); } catch (_) {}
    try { st.out && st.out.close(); } catch (_) {}
    cancelAnimationFrame(st.raf);
    clearInterval(st.keepalive);
    clearTimeout(st.micWatch);
    Object.assign(st, { proc: null, src: null, stream: null, ctx: null, out: null, analyser: null,
      capturing: false, cap: [], pre: [], preMs: 0, transRid: '', lastUtter: null });
    levelRef.current = 0;
  }

  const start = useCallback(() => {
    if (st.phase !== 'off') return;
    setActive(true); setMuted(false); setNote('');
    st.muted = false; st.nomic = false; st.frames = 0;
    setPhase('starting');
    egVoiceSend('voice_prewarm');
    // Both audio contexts are created here, inside the click, and resumed at
    // once. Created after the await for the microphone, Electron could hand
    // them back suspended: the mic stream then delivered no frames, nothing
    // was ever heard, and voice looked dead.
    const AC = window.AudioContext || window.webkitAudioContext;
    let ctx, out;
    try {
      ctx = new AC(); out = new AC();
      try { ctx.resume(); out.resume(); } catch (_) {}
      const speakerId = (() => { try { return localStorage.getItem('syrix_speaker_device_id') || ''; } catch (_) { return ''; } })();
      if (speakerId && typeof out.setSinkId === 'function') { out.setSinkId(speakerId).catch(() => {}); }
      const analyser = out.createAnalyser();
      analyser.fftSize = 512;
      analyser.connect(out.destination);
      Object.assign(st, { ctx, out, analyser, rate: ctx.sampleRate, noise: 0.004, loudRun: 0,
        capturing: false, cap: [], pre: [], preMs: 0, queue: [], turn: null });
      // The orb follows SYRIX's voice while it speaks.
      const scratch = new Uint8Array(analyser.fftSize);
      const tick = () => {
        if (st.phase === 'speaking' && st.analyser) {
          st.analyser.getByteTimeDomainData(scratch);
          let sum = 0;
          for (let i = 0; i < scratch.length; i++) { const v = (scratch[i] - 128) / 128; sum += v * v; }
          levelRef.current = Math.min(1, Math.sqrt(sum / scratch.length) * 4);
        }
        st.raf = requestAnimationFrame(tick);
      };
      st.raf = requestAnimationFrame(tick);
      // Keeps the backend's speech models loaded while the orb is up; they
      // are released a warm period after voice_end (see stop()).
      st.keepalive = setInterval(() => egVoiceSend('voice_keepalive'), 30000);
    } catch (_) {
      setNote('Audio is unavailable');
      setPhase('error');
      return;
    }
    // Without a microphone voice still works: typed messages are answered
    // out loud, as in ChatGPT.
    const noMic = (why) => {
      st.nomic = true;
      setNote(why);
      if (!st.turn) setPhase('nomic');
    };
    const micId = (() => { try { return localStorage.getItem('syrix_mic_device_id') || ''; } catch (_) { return ''; } })();
    navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
        ...(micId ? { deviceId: { ideal: micId } } : {}),
      },
    }).then((stream) => {
      if (st.phase === 'off' || st.ctx !== ctx) { try { stream.getTracks().forEach(t => t.stop()); } catch (_) {} return; }
      try {
        const src = ctx.createMediaStreamSource(stream);
        const proc = ctx.createScriptProcessor(2048, 1, 1);
        const silent = ctx.createGain();
        silent.gain.value = 0;
        proc.onaudioprocess = (e) => { st.frames += 1; try { onFrame(e.inputBuffer.getChannelData(0)); } catch (_) {} };
        src.connect(proc); proc.connect(silent); silent.connect(ctx.destination);
        Object.assign(st, { stream, src, proc });
        try { ctx.resume(); } catch (_) {}
        if (!st.turn) setPhase('listening');
        // A mic that delivers nothing is said so, instead of listening
        // forever in silence.
        st.micWatch = setTimeout(() => {
          if (st.phase === 'off' || st.frames > 0) return;
          try { ctx.resume(); } catch (_) {}
          st.micWatch = setTimeout(() => {
            if (st.phase !== 'off' && st.frames === 0) noMic('The microphone is not sending audio — type instead');
          }, 1500);
        }, 1500);
      } catch (_) {
        try { stream.getTracks().forEach(t => t.stop()); } catch (_) {}
        noMic('Could not start the microphone — type instead');
      }
    }, () => noMic('No microphone — type and SYRIX answers out loud'));
  }, []);

  const stop = useCallback(() => {
    teardown();
    egVoiceSend('voice_end');
    setActive(false); setMuted(false); setNote('');
    st.muted = false;
    setPhase('off');
  }, []);

  const toggleMute = useCallback(() => {
    const next = !st.muted;
    st.muted = next;
    setMuted(next);
    try { st.stream && st.stream.getAudioTracks().forEach(t => { t.enabled = !next; }); } catch (_) {}
    if (next) { st.capturing = false; st.cap = []; }
    if (!st.turn && st.phase !== 'transcribing') setPhase(next ? 'muted' : 'listening');
  }, []);

  // Tapping the orb while SYRIX talks is the silent way to interrupt.
  const tap = useCallback(() => {
    if (st.turn) { interrupt(); setPhase(st.muted ? 'muted' : 'listening'); }
  }, []);

  // A message typed while the orb is up is answered out loud too.
  const adoptTurn = useCallback((rid, promise) => {
    if (st.phase === 'off' || st.phase === 'error' || !st.out) return;
    beginTurn(rid, promise);
  }, []);

  useEffect(() => () => teardown(), []);

  return { active, phase, muted, note, levelRef, start, stop, toggleMute, tap, adoptTurn };
}

const EG_VOICE_LABEL = {
  starting: 'Starting…', listening: 'Listening', hearing: 'Listening', nomic: 'Type to talk',
  transcribing: 'Listening', thinking: 'Thinking', speaking: 'Speaking',
  muted: 'Microphone off', error: '',
};

/* The orb, drawn on a canvas: a softly lit sphere in the palette's accent
   with slow light moving inside it. Every value it animates — size, glow,
   drift speed, colour — eases toward its target each frame, so a change of
   state or a burst of speech never jumps. Colours are read from the palette
   tokens and re-read when the palette changes. */
function egOrbPalette() {
  const css = getComputedStyle(document.body);
  const rgb = (name, fallback) => {
    const v = String(css.getPropertyValue(name) || '').trim();
    const parts = v.split(',').map(x => parseFloat(x)).filter(x => isFinite(x));
    return parts.length === 3 ? parts : fallback;
  };
  return {
    base: rgb('--eg-accent-rgb', [233, 201, 140]),
    bright: rgb('--eg-accent-bright-rgb', [246, 226, 179]),
    deep: rgb('--eg-accent-deep-rgb', [201, 164, 94]),
  };
}

/* How the orb moves in each state. `drift` is how fast the colour inside it
   flows, `breathe` how much it swells at rest, `react` how strongly it
   answers a voice (yours while listening, SYRIX's while speaking), `sat` how
   much colour it keeps, `alpha` how solid it is. */
const EG_ORB_MOTION = {
  starting:     { drift: 0.25, breathe: 0.015, react: 0.0, sat: 1,    alpha: 0.85 },
  listening:    { drift: 0.22, breathe: 0.012, react: 1.0, sat: 1,    alpha: 1 },
  hearing:      { drift: 0.40, breathe: 0.000, react: 1.0, sat: 1,    alpha: 1 },
  transcribing: { drift: 0.55, breathe: 0.030, react: 0.2, sat: 1,    alpha: 1 },
  thinking:     { drift: 0.65, breathe: 0.035, react: 0.0, sat: 1,    alpha: 1 },
  speaking:     { drift: 0.45, breathe: 0.000, react: 1.0, sat: 1,    alpha: 1 },
  muted:        { drift: 0.10, breathe: 0.006, react: 0.0, sat: 0.10, alpha: 0.55 },
  nomic:        { drift: 0.20, breathe: 0.012, react: 0.0, sat: 0.70, alpha: 0.9 },
  error:        { drift: 0.08, breathe: 0.000, react: 0.0, sat: 0.10, alpha: 0.45 },
  off:          { drift: 0.10, breathe: 0.000, react: 0.0, sat: 1,    alpha: 0.6 },
};

/* The orb: one clean, solid circle with soft colour slowly flowing inside it
   — no halo, no highlight, no rim. At rest it is almost still; while SYRIX
   thinks it breathes; when someone talks it swells with the voice and its
   edge ripples gently. Colours come from the palette (champagne in Aura,
   white and grey in Dark) and are re-read when the palette changes. Every
   value eases toward its target each frame, so nothing jumps. */
function EgVoiceOrb({ phase, levelRef, onTap, label }) {
  const canvasRef = useRef(null);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d');
    let pal = egOrbPalette();
    const obs = new MutationObserver(() => { pal = egOrbPalette(); });
    obs.observe(document.body, { attributes: true, attributeFilter: ['class'] });
    const reduce = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    const cur = { drift: 0.2, breathe: 0.01, react: 0, sat: 1, alpha: 0.85, lvl: 0, wob: 0 };
    let t = 0, tb = 0, last = performance.now(), raf = 0;
    const grey = (c, sat) => {
      const g = (c[0] + c[1] + c[2]) / 3;
      return [g + (c[0] - g) * sat, g + (c[1] - g) * sat, g + (c[2] - g) * sat];
    };
    const rgba = (c, k) => 'rgba(' + Math.round(c[0]) + ',' + Math.round(c[1]) + ',' + Math.round(c[2]) + ',' + k + ')';
    const lighten = (c, k) => [c[0] + (255 - c[0]) * k, c[1] + (255 - c[1]) * k, c[2] + (255 - c[2]) * k];
    function frame(now) {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const want = EG_ORB_MOTION[phaseRef.current] || EG_ORB_MOTION.listening;
      const follow = 1 - Math.pow(0.03, dt);
      for (const k of ['drift', 'breathe', 'react', 'sat', 'alpha']) cur[k] += (want[k] - cur[k]) * follow;
      const lvlIn = Math.max(0, Math.min(1, ((levelRef && levelRef.current) || 0) * cur.react));
      // Quick to rise with a voice, slower to settle, like a VU meter.
      cur.lvl += (lvlIn - cur.lvl) * (1 - Math.pow(lvlIn > cur.lvl ? 0.002 : 0.06, dt));
      cur.wob += (cur.lvl - cur.wob) * (1 - Math.pow(0.05, dt));
      if (!reduce) { t += dt * cur.drift; tb += dt; }

      const dpr = window.devicePixelRatio || 1;
      const size = canvas.clientWidth;
      if (canvas.width !== Math.round(size * dpr)) { canvas.width = canvas.height = Math.round(size * dpr); }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size, size);
      const c = size / 2;
      const breathe = reduce ? 0 : Math.sin(tb * 1.6) * cur.breathe;
      const R = size * 0.39 * (1 + breathe + cur.lvl * 0.13);

      // The outline: a circle whose edge ripples only while there is a voice.
      ctx.beginPath();
      const N = 120;
      for (let i = 0; i <= N; i++) {
        const a = (i / N) * Math.PI * 2;
        const ripple = reduce ? 0 : cur.wob * 0.045 * (Math.sin(3 * a + tb * 2.3) + 0.55 * Math.sin(5 * a - tb * 1.9));
        const r = R * (1 + ripple);
        const x = c + Math.cos(a) * r, y = c + Math.sin(a) * r;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.save();
      ctx.globalAlpha = cur.alpha;
      ctx.clip();
      const base = grey(pal.base, cur.sat), deep = grey(pal.deep, cur.sat), light = lighten(grey(pal.bright, cur.sat), 0.35);
      // Body: an even fill, lighter toward the top.
      const body = ctx.createLinearGradient(c, c - R, c, c + R);
      body.addColorStop(0, rgba(light, 1));
      body.addColorStop(1, rgba(base, 1));
      ctx.fillStyle = body;
      ctx.fillRect(c - R * 1.2, c - R * 1.2, R * 2.4, R * 2.4);
      // Colour that flows: two broad, soft clouds drifting on slow orbits.
      const clouds = [
        { col: deep,  k: 0.55, ox: 0.38, oy: 0.30, p: 0.0, s: 1.0, rr: 0.95 },
        { col: light, k: 0.70, ox: 0.34, oy: 0.40, p: 2.6, s: 0.8, rr: 0.80 },
      ];
      for (const cl of clouds) {
        const ang = t * cl.s * 2 + cl.p;
        const x = c + Math.cos(ang) * R * cl.ox;
        const y = c + Math.sin(ang * 1.2) * R * cl.oy;
        const g = ctx.createRadialGradient(x, y, 0, x, y, R * cl.rr);
        g.addColorStop(0, rgba(cl.col, cl.k));
        g.addColorStop(1, rgba(cl.col, 0));
        ctx.fillStyle = g;
        ctx.fillRect(c - R * 1.2, c - R * 1.2, R * 2.4, R * 2.4);
      }
      ctx.restore();
      raf = requestAnimationFrame(frame);
    }
    raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); obs.disconnect(); };
  }, []);
  return (
    <button type="button" className="eg-vorb" onClick={onTap} aria-label={label}>
      <canvas ref={canvasRef} className="eg-vorb-canvas" aria-hidden="true" />
    </button>
  );
}

/* Voice, laid out as mainstream chat apps do it: the orb in the middle, one
   line saying what is happening under it, and two round controls under that
   — microphone and end. */
function EgVoiceDock({ voice, leaving }) {
  const lastPhaseRef = useRef(voice.phase);
  if (!leaving && voice.phase !== 'off') lastPhaseRef.current = voice.phase;
  const p = leaving ? lastPhaseRef.current : voice.phase;
  const label = (leaving ? '' : voice.note) || EG_VOICE_LABEL[p] || '';
  const hint = p === 'speaking' ? 'Talk or tap to interrupt'
    : p === 'listening' ? 'Speak, or type below'
    : p === 'nomic' ? 'Type below — SYRIX answers out loud'
    : p === 'muted' ? 'Microphone is off'
    : '';
  return (
    <div className={'eg-voice-dock-wrap' + (leaving ? ' is-leaving' : '')}>
    <div className={'eg-voice-dock is-' + p + (voice.muted ? ' is-muted' : '') + (leaving ? ' is-leaving' : '')} role="region" aria-label="Voice conversation">
      <EgVoiceOrb phase={p} levelRef={voice.levelRef} onTap={voice.tap}
        label={p === 'speaking' ? 'Interrupt SYRIX' : 'SYRIX voice'} />
      <div className="eg-voice-status" aria-live="polite">
        <span className="eg-voice-label" key={label}>{label}</span>
        <span className={'eg-voice-hint' + (hint ? '' : ' is-empty')}>{hint || ' '}</span>
      </div>
      <div className="eg-voice-ctls">
        <button type="button" className={'eg-voice-ctl' + (voice.muted ? ' is-off' : '')} onClick={voice.toggleMute}
          title={voice.muted ? 'Turn microphone on' : 'Turn microphone off'}
          aria-pressed={voice.muted}>
          {voice.muted ? EgI.micOff : EgI.mic}
        </button>
        <button type="button" className="eg-voice-ctl eg-voice-end" onClick={voice.stop} title="End voice">
          {EgI.close}
        </button>
      </div>
    </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Main shell component
   ═══════════════════════════════════════════════════════════════════════════ */
function SyrixAuroraShell(props) {
  const {
    onOpenSettings,
    onOpenWidgets,
    onOpenWidget,
    onMicToggle,
    listening   = false,
    micMuted    = false,
    brainLabel  = 'AUTO',
    coreLabel   = 'CORE V2',
    versionLabel= 'SYRIX V4.2',
    onToggleSleep,
    chromeMuted = false,
    brains      = [],
    activeBrainId = 'auto',
    onSelectBrain,
    ttsMuted    = false,
    onTtsMuteToggle,
  } = props || {};

  const snap     = useEgBridge();
  // The top bar no longer carries a clock. `now` survives because the launch
  // greeting has to notice when it crosses noon or six.
  const now      = useEgNow();
  const { hm }   = egFmt(now);
  const isOnline = snap?.status === 'online';

  /* ── Metrics ── */
  const raw  = snap?.raw || {};
  const pick = (...keys) => {
    for (const k of keys) {
      const a = snap?.[k]; if (typeof a === 'number' && isFinite(a)) return a;
      const b = raw?.[k];  if (typeof b === 'number' && isFinite(b)) return b;
    }
    return null;
  };
  // Telemetry gate: hide CPU/RAM/GPU when system_telemetry_enabled is explicitly
  // false (Task Manager owns these). Absent flag = show (backward compatible).
  const _teleOn = (snap?.system_telemetry_enabled !== false) && (raw?.system_telemetry_enabled !== false);
  const cpu  = _teleOn ? pick('cpu_percent','cpu_pct') : null;
  const ram  = _teleOn ? pick('ram_percent','mem_percent','memory_percent') : null;
  const gpu  = _teleOn ? pick('gpu_util_percent','gpu_percent') : null;

  /* ── Voice / working state ── */
  const vsRaw    = String(snap?.voice_state || snap?.tts_state || '').toUpperCase();
  const isSpeaking = vsRaw === 'SPEAKING' || vsRaw === 'TTS_SPEAKING';
  // is_thinking can arrive at the top level OR nested under raw (the bridge
  // surfaces it inconsistently), so check both before falling back to state.
  const isThinking = !!(snap?.is_thinking) || !!(snap?.raw && snap.raw.is_thinking) || /think|process/i.test(snap?.state || '');
  const [localPending, setLocalPending] = useState(false);
  // 2026-07-04 — the in-flight typed turn, rid-scoped: { rid, model, sentTs }.
  // The rid comes back from SyrixBridge.chat() and matches the request_id the
  // backend now stamps on every event of the turn (tokens, thinking, deep
  // attach, final line). This is what lets the panel react to OUR turn only —
  // previously any assistant line (a voice reply, another model's answer)
  // dismissed the thinking panel, and none arriving left it stuck forever.
  const [pendingTurn, setPendingTurn] = useState(null);
  const [expireStamp, setExpireStamp] = useState(0); // epoch s when we force-idled a wedged turn
  // Backend terminal status and the transcript travel over separate frames.
  // Keep the existing Thinking line alive for a short bounded handoff so a
  // completed status cannot make it vanish before the answer is painted.
  const pendingTerminalGraceRef = useRef(null);
  function clearPendingTerminalGrace() {
    if (pendingTerminalGraceRef.current) {
      clearTimeout(pendingTerminalGraceRef.current);
      pendingTerminalGraceRef.current = null;
    }
  }
  useEffect(() => () => clearPendingTerminalGrace(), []);
  const lastAsstN = useRef(0);
  useEffect(() => {
    const lines = snap.chat_lines;
    if (!Array.isArray(lines)) return;
    const n = lines.filter(m => !egIsUser(m)).length;
    if (n > lastAsstN.current) {
      lastAsstN.current = n;
      // Rid-scoped clear: when we know our turn's rid, only ITS assistant
      // line ends the pending state. The count heuristic stays as the
      // fallback for rid-less sends (offline mailbox edge).
      const rid = pendingTurn && pendingTurn.rid;
      if (rid) {
        // egLineAnswersTurn also accepts a rid-LESS assistant line landing
        // after we sent — see its doc comment (thinking-panel-below-answer fix).
        const done = lines.some(m => egLineAnswersTurn(m, pendingTurn));
        if (done) {
          clearPendingTerminalGrace();
          setLocalPending(false);
          setPendingTurn(null);
        }
      } else {
        setLocalPending(false);
        setPendingTurn(null);
      }
    }
  }, [snap.chat_lines && snap.chat_lines.length, pendingTurn]);
  // Terminal backend signal for our rid. Failure paths end immediately; a
  // successful terminal status gets a short grace period because the answer
  // transcript can arrive in the next WS/mailbox frame.
  const pendingTurnRef = useRef(null);
  useEffect(() => { pendingTurnRef.current = pendingTurn; }, [pendingTurn]);
  useEffect(() => {
    function onStatus(ev) {
      const d = (ev && ev.detail) || {};
      const st = String(d.state || '').toLowerCase();
      if (!['completed', 'complete', 'failed', 'timeout', 'cancelled', 'canceled', 'offline', 'superseded'].includes(st)) return;
      const cur = pendingTurnRef.current;
      if (cur && cur.rid && d.request_id && String(d.request_id) !== cur.rid) return;
      clearPendingTerminalGrace();
      if (st === 'completed' || st === 'complete') {
        const terminalRid = String(d.request_id || (cur && cur.rid) || '');
        pendingTerminalGraceRef.current = setTimeout(() => {
          pendingTerminalGraceRef.current = null;
          const still = pendingTurnRef.current;
          if (!still) return;
          if (terminalRid && still.rid && still.rid !== terminalRid) return;
          setLocalPending(false);
          setPendingTurn(null);
        }, 3000);
        return;
      }
      setLocalPending(false);
      setPendingTurn(null);
    }
    window.addEventListener('syrix-chat-status', onStatus);
    return () => window.removeEventListener('syrix-chat-status', onStatus);
  }, []);
  // 2026-06-16 — ROOT-CAUSE FIX for "stuck on thinking after the answer was
  // delivered / spoken". Previously `working` trusted snap.is_thinking, which
  // the backend sometimes never clears (research worker, dropped WS update,
  // dispatch that hung past its own finally-clear). The DEFINITIVE done signal
  // is the transcript itself: if the last conversational line is the user's we
  // are still awaiting SYRIX; the moment an assistant line lands after it we are
  // done — regardless of any stale is_thinking flag. This guarantees the
  // spinner clears as soon as the reply appears in chat.
  const awaitingReply = useMemo(() => {
    const lines = Array.isArray(snap.chat_lines) ? snap.chat_lines : [];
    for (let i = lines.length - 1; i >= 0; i--) {
      const r = String(lines[i] && (lines[i].role || lines[i].speaker) || '').toLowerCase();
      if (r === 'user' || r === 'human') return true;
      if (r === 'assistant' || r === 'syrix' || r === 'ai') return false;
    }
    return false;
  }, [snap.chat_lines]);
  // 2026-06-19 — ROOT-CAUSE FIX: chat_status is an OBJECT ({state,request_id,ts}),
  // so String(snap.chat_status) was always "[object Object]" and never matched —
  // the backend's authoritative processing/thinking/processing_slow signal was
  // silently ignored. Combined with snap.is_thinking being undefined on this
  // shell, `working` collapsed to localPending only (typed path), so the thinking
  // indicator never showed for voice and looked like a hang on slow replies.
  // Read .state directly; this is the reliable backend signal for BOTH paths.
  const _csRaw = snap?.chat_status;
  const _csState = typeof _csRaw === 'string' ? _csRaw : String((_csRaw && _csRaw.state) || '');
  const chatStatusThinking = /think|process|pending/i.test(_csState);
  // Show the indicator whenever: we have an optimistic local send pending, OR the
  // backend reports thinking AND we are still awaiting a reply. Gating both
  // chatStatusThinking and isThinking on awaitingReply is critical: the backend
  // sometimes never clears chat_status.state after a voice response, so without
  // the gate the spinner stays forever after the assistant reply lands.
  // 2026-07-04 — `expired`: once the hard timeout force-idled a wedged turn,
  // stay idle until a NEWER user line starts a fresh turn. Previously the
  // timeout only cleared localPending, so a stuck is_thinking/chat_status +
  // awaitingReply combination kept the panel up forever anyway.
  const lastUserLineTs = useMemo(() => {
    const lines = Array.isArray(snap.chat_lines) ? snap.chat_lines : [];
    for (let i = lines.length - 1; i >= 0; i--) {
      if (egIsUser(lines[i])) return egLineTsSeconds(lines[i]);
    }
    return 0;
  }, [snap.chat_lines]);
  const expired = expireStamp > 0 && lastUserLineTs <= expireStamp;
  // 2026-07-06 — ROOT-CAUSE FIX for "the answer flashes fully, disappears,
  // then re-streams from scratch". `localPending` is only cleared by the
  // effect above, which runs ONE RENDER AFTER the final assistant line for
  // our rid lands in chat_lines. That stray render still had `working ===
  // true`, which made EgChatMessage treat the just-arrived, already-complete
  // answer as "not latest" (isLatestAI requires `!working`) and paint it in
  // full instantly; the very next render flipped `working` false, made it
  // "latest", and restarted its typewriter from an empty string — the
  // flash-then-restart. `ourTurnAnswered` reads chat_lines synchronously
  // (no effect delay) so `working` drops to false in the SAME render the
  // reply appears, before EgChatMessage ever paints it.
  const ourTurnAnswered = useMemo(() => {
    if (!pendingTurn) return false;
    const lines = Array.isArray(snap.chat_lines) ? snap.chat_lines : [];
    // 2026-07-10 — also matches a rid-less assistant line that landed after
    // we sent (egLineAnswersTurn), so `working` drops the moment ANY delivery
    // path lands our answer instead of leaving the thinking panel rendered
    // below the reply until the hard timeout.
    return lines.some(m => egLineAnswersTurn(m, pendingTurn));
  }, [snap.chat_lines, pendingTurn]);
  const working = !expired && !ourTurnAnswered && !!(localPending || ((chatStatusThinking || isThinking) && awaitingReply));
  const presence = useEgPresence(working, snap);
  useEffect(() => {
    if (!working) return;
    // Hard timeout: force-idle the WHOLE thinking state after 90s (185s for a
    // zenith turn) so a wedged dispatch never leaves the spinner up forever.
    const forModel = (pendingTurn && pendingTurn.model) || pendingModelRef.current;
    const hardTimeout = setTimeout(() => {
      setLocalPending(false);
      setPendingTurn(null);
      setExpireStamp(Date.now() / 1000);
      try { console.warn('[EG_THINK] hard_expired model=' + forModel); } catch (_) {}
    }, (forModel === 'zenith' || forModel === 'auto') ? 185000 : 90000);
    return () => clearTimeout(hardTimeout);
  }, [working]);

  /* ── Chat ── */
  // 2026-07-09 — chat is now the permanent home surface (chat-app
  // style): always mounted open, in the 'bg' layout that fills the main
  // area beside the nav rail. See the hardcoded `open={true}` below.
  const [chatOpen, setChatOpen]       = useState(true);
  const [chatLayout, setChatLayout]   = useState('bg');
  const [draft, setDraft]             = useState('');
  const [deepResearch, setDeepResearch] = useState(false);
  // Launch surface state. It lives here, not in the chat panel, because the
  // starting-point chips render under the composer in the bottom bar while the
  // greeting renders inside the transcript — two different subtrees driven by
  // one fact. (It was previously panel-local, which left newChat() calling
  // setters that did not exist in its scope and throwing part-way through.)
  // There is no separate "hidden" flag: the launch surface is shown exactly
  // while the transcript is empty, and a sent message fills it optimistically.
  const [quickActions, setQuickActions] = useState(egLaunchQuickActions);
  // Sidebar collapse + width — both persisted, so the HUD reopens the way it
  // was left. The width is published as --eg-side-w on <body> because the
  // Dynamic Island docks to the sidebar from a different React root and every
  // neighbouring surface offsets from that one number.
  const EG_SIDE_MIN = 208;
  const EG_SIDE_MAX = 440;
  const [sideCollapsed, setSideCollapsed] = useState(() => {
    try { return localStorage.getItem('eg-side-collapsed') === '1'; } catch (_) { return false; }
  });
  const [sideWidth, setSideWidth] = useState(() => {
    try {
      const v = parseInt(localStorage.getItem('eg-side-width') || '', 10);
      if (Number.isFinite(v)) return Math.max(EG_SIDE_MIN, Math.min(EG_SIDE_MAX, v));
    } catch (_) {}
    return 272;
  });
  const sideWidthRef = useRef(sideWidth);
  sideWidthRef.current = sideWidth;
  useEffect(() => {
    // Collapsed width belongs to the stylesheet, so the inline value is
    // removed rather than overwritten — otherwise expanding would restore the
    // rail at 60px and look broken.
    if (sideCollapsed) document.body.style.removeProperty('--eg-side-w');
    else document.body.style.setProperty('--eg-side-w', sideWidth + 'px');
    return () => document.body.style.removeProperty('--eg-side-w');
  }, [sideWidth, sideCollapsed]);
  function onSideGripDown(e) {
    if (sideCollapsed) return;
    e.preventDefault();
    const startX = e.clientX;
    const startW = sideWidthRef.current;
    // The width transition is switched off for the drag: animating towards a
    // value that changes every pointermove is what makes a resize feel like
    // it is fighting the cursor.
    document.body.classList.add('eg-side-resizing');
    function move(ev) {
      setSideWidth(Math.max(EG_SIDE_MIN, Math.min(EG_SIDE_MAX, startW + (ev.clientX - startX))));
    }
    function up() {
      document.body.classList.remove('eg-side-resizing');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      try { localStorage.setItem('eg-side-width', String(sideWidthRef.current)); } catch (_) {}
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }
  function resetSideWidth() {
    setSideWidth(272);
    try { localStorage.setItem('eg-side-width', '272'); } catch (_) {}
  }
  function toggleSidebar() {
    setSideCollapsed(v => {
      const next = !v;
      try { localStorage.setItem('eg-side-collapsed', next ? '1' : '0'); } catch (_) {}
      return next;
    });
  }
  // ── Composer dictation (speak-to-type) ──────────────────────────────────
  // 2026-07-11 — the bottom-bar mic button used to be a mic+TTS mute toggle;
  // it is now speak-to-type, as in mainstream chat apps,
  // scoped ONLY to the normal-HUD composer text box, with real Whisper-grade
  // accuracy. Recording (getUserMedia + ScriptProcessor, captured as raw
  // PCM16) happens entirely in the renderer; on stop the whole clip is sent
  // once over the WS as `dictation_transcribe`, decoded by the SAME
  // faster-whisper model Voice Mode uses (transcribe_audio_with_whisper in
  // syrix_host.py), and the transcript comes back as a `dictation_result`
  // push. The backend handler skips outright whenever Voice Mode owns the
  // model, so the two can never contend for the GPU — Voice Mode's own mic
  // (vmMicMuted/toggleVmMic) and the global mic/TTS mute (still in Settings)
  // are untouched by any of this.
  const [dictating, setDictating] = useState(false);
  const [dictationBusy, setDictationBusy] = useState(false);
  const [dictationNoMic, setDictationNoMic] = useState(false);
  const dictationNoMicTimerRef = useRef(null);
  const dictationStreamRef = useRef(null);
  const dictationCtxRef = useRef(null);
  const dictationProcRef = useRef(null);
  const dictationSrcRef = useRef(null);
  const dictationBufRef = useRef([]);
  const dictationBaseRef = useRef('');
  const dictationRidRef = useRef('');
  // One persistent AudioContext for the chime, created lazily and reused —
  // creating+closing a fresh context on every beep risked landing outside
  // Chromium's "user activation" window (esp. the ON chime, which used to
  // fire only after `await getUserMedia(...)` resolved) and playing silently.
  const dictationChimeCtxRef = useRef(null);
  function playDictationChime(turningOn) {
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      let ctx = dictationChimeCtxRef.current;
      if (!ctx || ctx.state === 'closed') {
        ctx = new AC();
        dictationChimeCtxRef.current = ctx;
      }
      if (ctx.state === 'suspended') { try { ctx.resume(); } catch (_) {} }
      const now = ctx.currentTime;
      const freqs = turningOn ? [660, 880] : [880, 620];
      freqs.forEach((f, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = f;
        const t0 = now + i * 0.09;
        gain.gain.setValueAtTime(0, t0);
        gain.gain.linearRampToValueAtTime(0.22, t0 + 0.012);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.12);
        osc.connect(gain); gain.connect(ctx.destination);
        osc.start(t0); osc.stop(t0 + 0.13);
      });
    } catch (_) {}
  }
  function teardownDictationAudio() {
    try { dictationProcRef.current && dictationProcRef.current.disconnect(); } catch (_) {}
    try { dictationSrcRef.current && dictationSrcRef.current.disconnect(); } catch (_) {}
    try { dictationStreamRef.current && dictationStreamRef.current.getTracks().forEach(t => t.stop()); } catch (_) {}
    try { dictationCtxRef.current && dictationCtxRef.current.close(); } catch (_) {}
    dictationProcRef.current = null;
    dictationSrcRef.current = null;
    dictationStreamRef.current = null;
    dictationCtxRef.current = null;
  }
  async function startDictation() {
    if (dictating || dictationBusy) return;
    let stream;
    try {
      // The microphone chosen in Settings → Voice, as the voice orb uses.
      const micId = (() => { try { return localStorage.getItem('syrix_mic_device_id') || ''; } catch (_) { return ''; } })();
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true,
                 ...(micId ? { deviceId: { ideal: micId } } : {}) },
      });
    } catch (_) {
      // No mic device / permission denied — chime already played on click;
      // nothing else to clean up since capture never started. Flash a
      // message in the composer placeholder for a beat so it's obvious why
      // dictation didn't start, then fall back to the normal placeholder.
      try { clearTimeout(dictationNoMicTimerRef.current); } catch (_) {}
      setDictationNoMic(true);
      dictationNoMicTimerRef.current = setTimeout(() => setDictationNoMic(false), 1000);
      return;
    }
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      const ctx = new AC();
      const src = ctx.createMediaStreamSource(stream);
      const proc = ctx.createScriptProcessor(4096, 1, 1);
      const silent = ctx.createGain();
      silent.gain.value = 0; // pull the graph without echoing the mic to speakers
      dictationBufRef.current = [];
      proc.onaudioprocess = (e) => {
        dictationBufRef.current.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };
      src.connect(proc);
      proc.connect(silent);
      silent.connect(ctx.destination);
      dictationStreamRef.current = stream;
      dictationCtxRef.current = ctx;
      dictationProcRef.current = proc;
      dictationSrcRef.current = src;
      dictationBaseRef.current = draft ? draft.replace(/\s+$/, '') + ' ' : '';
      setDictating(true);
    } catch (_) {
      try { stream.getTracks().forEach(t => t.stop()); } catch (_) {}
    }
  }
  function stopDictation() {
    if (!dictating) return;
    const chunks = dictationBufRef.current;
    const sampleRate = (dictationCtxRef.current && dictationCtxRef.current.sampleRate) || 48000;
    teardownDictationAudio();
    setDictating(false);
    let total = 0;
    for (const c of chunks) total += c.length;
    if (!total) return;
    const pcm16 = new Int16Array(total);
    let off = 0;
    for (const c of chunks) {
      for (let i = 0; i < c.length; i++) {
        const s = Math.max(-1, Math.min(1, c[i]));
        pcm16[off++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      }
    }
    const bytes = new Uint8Array(pcm16.buffer);
    let binary = '';
    const STEP = 0x8000;
    for (let i = 0; i < bytes.length; i += STEP) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + STEP));
    }
    const b64 = btoa(binary);
    const rid = 'dict_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
    dictationRidRef.current = rid;
    setDictationBusy(true);
    try {
      window.SyrixBridge?.send?.('dictation_transcribe', {
        data: { audio_b64: b64, sample_rate: sampleRate, request_id: rid },
      });
    } catch (_) { setDictationBusy(false); }
  }
  function toggleDictation() {
    // Chime fires FIRST, synchronously, inside the click handler itself —
    // Chromium's autoplay/user-activation window can lapse by the time an
    // async function returns from `await getUserMedia(...)`, which is why
    // the ON chime went silent when it was fired from inside startDictation.
    if (dictating) {
      playDictationChime(false);
      stopDictation();
    } else if (!dictationBusy) {
      playDictationChime(true);
      startDictation();
    }
  }
  useEffect(() => {
    function onDictationResult(e) {
      const d = e && e.detail; if (!d) return;
      if (dictationRidRef.current && d.request_id && d.request_id !== dictationRidRef.current) return;
      setDictationBusy(false);
      if (d.ok && d.text) setDraft(dictationBaseRef.current + d.text);
    }
    window.addEventListener('syrix:dictation-result', onDictationResult);
    return () => window.removeEventListener('syrix:dictation-result', onDictationResult);
  }, []);
  useEffect(() => () => {
    teardownDictationAudio();
    try { dictationChimeCtxRef.current && dictationChimeCtxRef.current.close(); } catch (_) {}
    try { clearTimeout(dictationNoMicTimerRef.current); } catch (_) {}
  }, []);
  // 2026-07-10 (product review C3) — offline send honesty. A message sent
  // while the backend is down used to disappear behind a fake progress panel;
  // now the composer says plainly that delivery waits for reconnection.
  const [offlineSendTs, setOfflineSendTs] = useState(0);
  useEffect(() => {
    if (offlineSendTs && String((snap && snap.status) || 'online') === 'online') {
      setOfflineSendTs(0);
    }
  }, [snap && snap.status, offlineSendTs]);
  // Document Intelligence: attachment cards shown above the composer. Each card
  // is { upload_id, name, size, kind, subtype, status, subtitle, doc_id, error,
  // progress }. status: uploading → analyzing → ready | partial | failed |
  // cancelled (cancelled is frontend-only: late backend status is ignored).
  const [attachments, setAttachments] = useState([]);
  const heldSendRef = useRef(null);
  // The composer grows with its text up to ~7 lines, then scrolls.
  const composerRef = useRef(null);
  const [composerTall, setComposerTall] = useState(false);
  const [heldSendName, setHeldSendName] = useState('');
  const cancelledUploadsRef = useRef(new Set());
  const [bottomDragOver, setBottomDragOver] = useState(false);
  // 2026-07-02 — SPARK / ZENITH sticky model, shared with the primary shell
  // via localStorage + the 'syrix-model-changed' event. Request signal only;
  // the backend-extracted marker is the source of truth per turn.
  const [model, setModelState] = useState(() => {
    try {
      const v = String(localStorage.getItem('syrix_chat_model_v1') || '').toLowerCase();
      return (v === 'zenith' || v === 'auto') ? v : 'spark';
    } catch (_) { return 'spark'; }
  });
  const pendingModelRef = useRef('spark');
  // Switching model used to bloom the whole composer for ~1.8s. The switcher
  // already says which model is selected; a light show on top of it was noise
  // over the thing being typed into. The resting per-model tint stays.
  // ── VOICE — the orb over this chat (see useEgVoice). A voice turn is an
  // ordinary chat turn marked voice_reply, so it lands in this thread and is
  // saved with it. submitRef/cancelRef always point at this render's submit
  // and cancel, which the engine calls from its own event handlers.
  const voiceSubmitRef = useRef(null);
  const voiceCancelRef = useRef(null);
  const voice = useEgVoice(voiceSubmitRef, voiceCancelRef);

  // ── PRIVATE MODE — owned by the backend (syrix_private): nothing from the
  // chat is saved, every answer comes from the local model, and nothing
  // leaves the PC. Off at every launch; the bridge asks for the real state
  // on connect, so this is never a guess.
  const [privateMode, setPrivateModeState] = useState(() => !!(window.SyrixBridge?.getSnapshot?.()?.private_mode));
  const privateModeRef = useRef(privateMode);
  useEffect(() => {
    const onPrivate = (e) => {
      const on = !!(e && e.detail && e.detail.active);
      privateModeRef.current = on;
      setPrivateModeState(on);
    };
    // Settings switches Private mode through the same path as the sidebar.
    const onSet = (e) => { try { setPrivateModeRef.current && setPrivateModeRef.current(!!(e && e.detail && e.detail.on)); } catch (_) {} };
    window.addEventListener('syrix:private-mode', onPrivate);
    window.addEventListener('syrix-private-set', onSet);
    return () => {
      window.removeEventListener('syrix:private-mode', onPrivate);
      window.removeEventListener('syrix-private-set', onSet);
    };
  }, []);
  useEffect(() => {
    document.body.classList.toggle('eg-private', privateMode);
    return () => document.body.classList.remove('eg-private');
  }, [privateMode]);
  function setPrivateMode(on) {
    try { window.SyrixBridge?.send?.('private_mode_set', { data: { on: !!on } }); } catch (_) {}
    // Switching either way starts an empty conversation — entering opens a
    // fresh private chat, leaving discards it — so the panel clears at once,
    // the same way New chat does, rather than waiting on the backend.
    egSmooth(() => {
      privateModeRef.current = !!on;
      setPrivateModeState(!!on);
      setSessionView({ lines: [], sinceTs: Date.now() / 1000 });
      setPendingUserMsgs([]); setLocalMessages([]);
      setLocalPending(false); setPendingTurn(null);
    });
  }
  useEffect(() => {
    document.body.classList.toggle('eg-voice-live', voice.active);
    return () => document.body.classList.remove('eg-voice-live');
  }, [voice.active]);
  // The dock stays mounted for its exit animation after voice ends.
  const [voiceDockShown, setVoiceDockShown] = useState(false);
  useEffect(() => {
    if (voice.active) { setVoiceDockShown(true); return undefined; }
    const t = setTimeout(() => setVoiceDockShown(false), 460);
    return () => clearTimeout(t);
  }, [voice.active]);
  // ── SUPERCOMPUTER (2026-07-24) ───────────────────────────────────────────
  // A separate agentic working environment, not a chat model. Selecting it
  // opens a full-window workspace (chat + embedded browser); leaving tears the
  // whole subsystem down. Never persisted, so a HUD reload always returns to
  // normal chat and no stale marker can leak into a normal turn.
  const [superMode, setSuperMode] = useState(false);
  // The chat to reopen, handed to the workspace so IT sends the enter.
  //
  // The shell used to send `supercomputer_enter` here and the workspace sent
  // one again on mount — two enters, in an order nothing guaranteed. If the
  // workspace's bare enter won the race it opened a fresh session and the
  // resume was discarded as "already active", which is one of the two reasons
  // reopening a saved chat produced a blank one. There is exactly one enter
  // now, and it carries the resume id.
  const [superResumeId, setSuperResumeId] = useState('');
  const enterSuperMode = (resumeSessionId) => {
    if (privateModeRef.current) {
      egToastNotice('Supercomputer browses the web, so it is off while Private mode is on.');
      return;
    }
    if (voice.active) voice.stop();
    setSuperResumeId(resumeSessionId ? String(resumeSessionId) : '');
    setSuperMode(true);
    setChatOpen(false);
    try { console.info('[SUPERCOMPUTER] enter shell=aurora'); } catch (_) {}
  };
  // The island's "open" arrow on a backgrounded task. Re-entering an already
  // active session is idempotent on the backend, so this just puts the
  // workspace back on screen around the run that is still going.
  useEffect(() => {
    const onOpen = () => { try { enterSuperMode(''); } catch (_) {} };
    window.addEventListener('syrix-open-supercomputer', onOpen);
    return () => window.removeEventListener('syrix-open-supercomputer', onOpen);
  }, []);
  // useCallback — a stable identity is required here: the workspace
  // depends on `onClose` in its mount effect, and `snap` re-renders this shell
  // on every state tick.
  const exitSuperMode = useCallback(() => {
    setSuperMode(false);
    try { window.SyrixBridge?.send?.('supercomputer_exit'); } catch (_) {}
    try { console.info('[SUPERCOMPUTER] exit shell=aurora'); } catch (_) {}
  }, []);
  const setModel = (next) => {
    if (next === 'voice') { voice.active ? voice.stop() : voice.start(); return; }
    if (next === 'supercomputer') { enterSuperMode(); return; }
    const v = (next === 'zenith' || next === 'auto') ? next : 'spark';
    setModelState(v);
    try { localStorage.setItem('syrix_chat_model_v1', v); } catch (_) {}
    try { window.dispatchEvent(new CustomEvent('syrix-model-changed', { detail: { model: v, source: 'aurora' } })); } catch (_) {}
    try { console.info('[SYRIX_MODEL] selected=' + v + ' shell=aurora'); } catch (_) {}
  };
  useEffect(() => {
    const onModelChanged = (e) => {
      if (e && e.detail && e.detail.source === 'aurora') return;
      const v = e && e.detail && e.detail.model;
      setModelState((v === 'zenith' || v === 'auto') ? v : 'spark');
    };
    window.addEventListener('syrix-model-changed', onModelChanged);
    return () => window.removeEventListener('syrix-model-changed', onModelChanged);
  }, []);
  // Optimistic echo: user messages shown INSTANTLY on send, before the backend
  // round-trips them back via the ~1.4s state poll (fixes the "my message
  // appears late / randomly" lag). Reconciled against chat_lines below.
  const [pendingUserMsgs, setPendingUserMsgs] = useState([]);
  const inputRef    = useRef(null);
  const fileInputRef= useRef(null);
  const mountedAt   = useRef(Date.now());
  const lineCountRef= useRef((snap.chat_lines || []).length);
  useEffect(() => {
    const n = (snap.chat_lines || []).length;
    if (n > lineCountRef.current && (Date.now() - mountedAt.current) > 12000) setChatOpen(true);
    lineCountRef.current = n;
  }, [snap.chat_lines && snap.chat_lines.length]);

  /* ── Sleep-mode local messages (never sent to backend) ── */
  const [localMessages, setLocalMessages] = useState([]);

  /* ── Chat list — a section of the sidebar, expanded by default. Named for
     what it is now: there is no History destination anymore, the list is
     simply always there and its own header caret opens and closes it. ── */
  const [chatsOpen, setChatsOpen] = useState(true);
  const histSearchRef = useRef(null);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  // The chat list is only known once the backend answers, which on a cold
  // start is several seconds after the window appears. The last list seen is
  // kept locally and shown at once; the backend's answer replaces it.
  const [sessions, setSessions]       = useState(() => {
    try {
      const cached = JSON.parse(localStorage.getItem('syrix_chat_sessions_cache_v1') || 'null');
      return Array.isArray(cached) ? cached : [];
    } catch (_) { return []; }
  });
  const [sessionsLive, setSessionsLive] = useState(false);
  const [sessionView, setSessionView] = useState(null); // { lines, sinceTs } when viewing a reopened chat
  // Tombstones keep a just-deleted row hidden while an older WS/state snapshot
  // is still in flight. They are cleared with a fresh HUD process, never by a
  // timer, so stale data cannot make the chat reappear.
  const [deletedSessionIds, setDeletedSessionIds] = useState(() => new Set());
  const deleteRetryTimersRef = useRef(new Map());
  const deleteRefreshTimersRef = useRef(new Map());
  const [newChatPending, setNewChatPending] = useState(false);
  const newChatBusyRef = useRef(false);
  const newChatRetryRef = useRef(null);
  const newChatRefreshRefs = useRef([]);
  const loadedNonceRef = useRef('');
  // Hover-preload cache: id -> { title, messages }. Populated by real loads
  // AND by hover-triggered peeks, so a chat opened once (or merely hovered)
  // reopens instantly with zero round-trip on the next click.
  const sessionCacheRef = useRef(new Map());
  // Which session the backend has actually delivered, and the one-shot retry
  // that covers a dropped chat_load_session.
  const loadedIdRef = useRef('');
  const loadRetryRef = useRef(null);
  function requestSessions() { try { window.SyrixBridge?.send?.('chat_list_sessions'); } catch (_) {} }
  function newChat() { egSmooth(newChatNow); }
  function newChatNow() {
    // The store reuses an empty active session, so a retry is idempotent. This
    // covers the brief WS reconnect window without creating duplicate blanks.
    if (newChatBusyRef.current) return;
    newChatBusyRef.current = true;
    setNewChatPending(true);
    let sent = false;
    try { sent = !!window.SyrixBridge?.send?.('chat_new_session'); } catch (_) {}
    if (!sent) {
      newChatRetryRef.current = setTimeout(() => {
        newChatRetryRef.current = null;
        try { window.SyrixBridge?.send?.('chat_new_session'); } catch (_) {}
      }, 350);
    }
    // Refresh after rotation so the just-finished chat is visible the next
    // time the drawer opens, even if the first list poll raced the switch.
    newChatRefreshRefs.current.forEach(clearTimeout);
    newChatRefreshRefs.current = [
      setTimeout(requestSessions, 450),
      setTimeout(requestSessions, 1400),
    ];
    // Hide the old mailbox transcript immediately while the backend rotates;
    // live lines newer than this marker can still appear in the fresh chat.
    setSessionView({ lines: [], sinceTs: Date.now() / 1000 });
    setPendingUserMsgs([]); setLocalMessages([]); setChatOpen(true);
    // End whatever turn was in flight. Without this the launch surface never
    // came back: `working`/`localPending` stayed true from the previous
    // question, so the shell still considered the chat busy and New Chat
    // looked like it had done nothing.
    clearPendingTerminalGrace();
    setLocalPending(false); setPendingTurn(null); setExpireStamp(0);
    setAttachments([]); setDraft('');
    cancelSpeech();
    // Voice stays on in the new chat; only what the old chat was saying stops.
    if (voice.active) voice.tap();
    // New Chat is another welcome surface: rotate the starting points so it
    // never offers the same four twice in a row.
    setQuickActions(egLaunchQuickActions());
    setTimeout(() => { try { inputRef.current?.focus(); } catch (_) {} }, 40);
    setTimeout(() => {
      newChatBusyRef.current = false;
      setNewChatPending(false);
    }, 1400);
  }
  useEffect(() => () => {
    if (newChatRetryRef.current) clearTimeout(newChatRetryRef.current);
    newChatRefreshRefs.current.forEach(clearTimeout);
    deleteRetryTimersRef.current.forEach(clearTimeout);
    deleteRefreshTimersRef.current.forEach(timers => {
      if (Array.isArray(timers)) timers.forEach(clearTimeout);
    });
  }, []);
  // Start a fresh chat session ONCE per HUD process (so old chats go to
  // history) but don't open the panel. 2026-07-04 — guarded: this used to run
  // on EVERY mount, so a shell remount (theme flip, stage toggle, error
  // recovery) silently reset the backend session mid-conversation and every
  // follow-up lost its context. A remount now resumes the live session; only
  // app launch and the explicit New Chat button start a fresh one.
  useEffect(() => {
    let freshBoot = false;
    if (!window.__syrixSessionInitialized) {
      window.__syrixSessionInitialized = true;
      freshBoot = true;
      try { window.SyrixBridge?.send?.('chat_new_session'); } catch (_) {}
    }
    // 2026-07-19 — a fresh app launch rotates the backend to a brand-new
    // session (above), but that alone left the OLD conversation on screen:
    // sessionView=null just shows the live chat_lines mailbox as-is, and
    // chat_new_session never truncates that rolling file (only the explicit
    // New Chat button did, via this same sessionView masking trick). Apply
    // the identical "hide everything before now" marker newChat() uses so a
    // relaunch always opens on a blank chat, same as clicking New Chat —
    // the old conversation still lives in history, just not on screen.
    setSessionView(freshBoot ? { lines: [], sinceTs: Date.now() / 1000 } : null);
    setPendingUserMsgs([]);
    setLocalMessages([]);
    // The chats section is NOT closed here. It was, back when history was a
    // drawer over the HUD that obviously had to start shut; as a permanent
    // sidebar section that line meant the list arrived collapsed on every
    // launch, however it had been left.
  }, []);
  function loadSession(id, cachedMessages, kind) {
    egSmooth(() => loadSessionNow(id, cachedMessages, kind));
  }
  function loadSessionNow(id, cachedMessages, kind) {
    if (!id) return;
    // Opening a saved chat leaves Private mode, as ChatGPT's temporary chat
    // does: what is said next belongs to that saved conversation.
    if (privateModeRef.current) setPrivateMode(false);
    // 2026-07-26 — a saved SUPERCOMPUTER conversation reopens in the
    // Supercomputer workspace, not the chat panel. Same shape as the voice
    // branch above: load the session (which flips active_id and rebuilds the
    // backend's live buffer), then open the mode bound to that session so it
    // continues the conversation instead of starting a blank one. The backend
    // also reopens the tabs the chat was last looking at — the browser profile
    // is the user's own, so those pages come back signed in.
    if (String(kind || '') === 'supercomputer') {
      try { window.SyrixBridge?.send?.('chat_load_session', { data: { session_id: String(id) } }); } catch (_) {}
      setChatOpen(false);
      // Clear the workspace watermark so the restored transcript is shown
      // rather than sliced away as "lines from before I mounted".
      try { if (window.__syrixScWatermark) window.__syrixScWatermark.id = null; } catch (_) {}
      enterSuperMode(String(id));
      try { console.info('[SUPERCOMPUTER] reopen-from-history shell=aurora id=' + String(id)); } catch (_) {}
      return;
    }
    // Instant open: if hover already warmed the cache, paint it NOW — the
    // authoritative chat_load_session call still fires below (flips the
    // backend's active_id + rebuilds its live memory buffer) but the UI
    // never waits on it since it already matches.
    if (Array.isArray(cachedMessages)) {
      setSessionView({ id: String(id), lines: cachedMessages, sinceTs: egLiveSinceMarker(snap.chat_lines) });
      setPendingUserMsgs([]);
    }
    try { window.SyrixBridge?.send?.('chat_load_session', { data: { session_id: String(id) } }); } catch (_) {}
    // The cached paint above is provisional — a hover peek can be a partial
    // transcript, which is what showed a reopened chat with half its messages
    // missing. The authoritative payload replaces it wholesale when it lands;
    // if it does not, ask once more rather than leaving the partial on screen.
    if (loadRetryRef.current) clearTimeout(loadRetryRef.current);
    loadRetryRef.current = setTimeout(() => {
      loadRetryRef.current = null;
      if (String(loadedIdRef.current || '') !== String(id)) {
        try { window.SyrixBridge?.send?.('chat_load_session', { data: { session_id: String(id) } }); } catch (_) {}
      }
    }, 2500);
    // Opening a chat leaves the list open: it is the sidebar now, and hiding
    // it the moment you pick something removes the context you picked from.
    setChatOpen(true);
  }
  function peekSession(id) {
    if (!id) return;
    try { window.SyrixBridge?.send?.('chat_peek_session', { data: { session_id: String(id) } }); } catch (_) {}
  }
  function pinSession(id, pinned) {
    if (!id) return;
    try { window.SyrixBridge?.send?.('chat_pin_session', { data: { session_id: String(id), pinned: !!pinned } }); } catch (_) {}
  }
  function renameSession(id, title) {
    const t = String(title || '').trim();
    if (!id || !t) return;
    try { window.SyrixBridge?.send?.('chat_rename_session', { data: { session_id: String(id), title: t } }); } catch (_) {}
  }
  function deleteSession(id) {
    const sid = String(id || '').trim();
    if (!sid) return;
    setDeletedSessionIds(prev => {
      if (prev.has(sid)) return prev;
      const next = new Set(prev);
      next.add(sid);
      return next;
    });
    sessionCacheRef.current.delete(sid);
    const activeId = String(snap?.raw?.syrix_chat_active_session || '');
    const viewedId = String((sessionView && sessionView.id) || '');
    if (activeId === sid || viewedId === sid) {
      // Remove the transcript from the visible HUD immediately; the backend
      // reset signal then clears the bridge/mailbox copy as confirmation.
      setSessionView({ id: '', lines: [], sinceTs: Date.now() / 1000 });
      setPendingUserMsgs([]);
      setLocalMessages([]);
    }
    const sendDelete = () => {
      try {
        return !!window.SyrixBridge?.send?.('chat_delete_session', {
          data: { session_id: sid },
        });
      } catch (_) {
        return false;
      }
    };
    if (!sendDelete()) {
      const oldRetry = deleteRetryTimersRef.current.get(sid);
      if (oldRetry) clearTimeout(oldRetry);
      deleteRetryTimersRef.current.set(sid, setTimeout(() => {
        deleteRetryTimersRef.current.delete(sid);
        sendDelete();
      }, 350));
    }
    const oldRefresh = deleteRefreshTimersRef.current.get(sid);
    if (Array.isArray(oldRefresh)) oldRefresh.forEach(clearTimeout);
    deleteRefreshTimersRef.current.set(sid, [
      setTimeout(requestSessions, 300),
      setTimeout(requestSessions, 1000),
      setTimeout(requestSessions, 2200),
    ]);
  }
  function clearAllSessions() {
    try { window.SyrixBridge?.send?.('chat_clear_all_sessions', {}); } catch (_) {}
    setSessions([]);
    setDeletedSessionIds(new Set());
    setSessionView({ id: '', lines: [], sinceTs: Date.now() / 1000 });
    setPendingUserMsgs([]); setLocalMessages([]);
  }
  // Settings → Privacy deletes every chat through the same path as the
  // sidebar's "Clear all chats".
  const clearAllRef = useRef(clearAllSessions);
  clearAllRef.current = clearAllSessions;
  useEffect(() => {
    const on = () => { try { clearAllRef.current(); } catch (_) {} };
    window.addEventListener('syrix-clear-all-chats', on);
    return () => window.removeEventListener('syrix-clear-all-chats', on);
  }, []);
  // The chats section is collapsed and expanded by its own header caret. It
  // used to have a History nav button as well, which toggled a section that is
  // always on screen — so the button went, and the event it dispatched with it.
  /* The chat list is a permanent part of the sidebar, so it is fetched on
     mount rather than when a drawer opens — and it keeps asking until the
     answer arrives.

     `chat_list_sessions` is a WebSocket send. At mount the socket is usually
     still connecting, and a send on a socket that is not open is simply
     dropped: no error, no retry, and the sidebar sits empty for the whole
     session looking like there is no history. So: ask on mount, ask again on
     every connection state change, and back off through a few attempts while
     the list is still empty. The backoff stops the moment anything lands. */
  const hasSessionsRef = useRef(false);
  useEffect(() => { hasSessionsRef.current = sessionsLive; }, [sessionsLive]);
  useEffect(() => {
    let tries = 0;
    let timer = null;
    function ask() {
      requestSessions();
      tries += 1;
      if (!hasSessionsRef.current && tries < 6) {
        timer = setTimeout(ask, 900 * tries);
      }
    }
    ask();
    return () => { if (timer) clearTimeout(timer); };
  }, [chatsOpen, snap.status]);
  // Session list + loaded-transcript payload arrive on snap.raw (reliable poll).
  useEffect(() => {
    const r = snap?.raw || {};
    const list = Array.isArray(r.syrix_chat_sessions) ? r.syrix_chat_sessions : null;
    if (list) {
      setSessions(list);
      setSessionsLive(true);
      try { localStorage.setItem('syrix_chat_sessions_cache_v1', JSON.stringify(list.slice(0, 100))); } catch (_) {}
    }
    const loaded = r.syrix_loaded_session;
    if (loaded && loaded.nonce && loaded.nonce !== loadedNonceRef.current) {
      loadedNonceRef.current = loaded.nonce;
      loadedIdRef.current = loaded.id ? String(loaded.id) : '';
      if (loadRetryRef.current) { clearTimeout(loadRetryRef.current); loadRetryRef.current = null; }
      const msgs = Array.isArray(loaded.messages) ? loaded.messages : [];
      setSessionView({ id: loaded.id ? String(loaded.id) : '', lines: msgs, sinceTs: egLiveSinceMarker(snap.chat_lines) });
      setPendingUserMsgs([]); setChatOpen(true);
      if (loaded.id) {
        const cache = sessionCacheRef.current;
        cache.set(String(loaded.id), { title: loaded.title, messages: msgs });
        if (cache.size > 30) cache.delete(cache.keys().next().value);
      }
    }
  }, [snap.raw && snap.raw.syrix_chat_sessions, snap.raw && snap.raw.syrix_loaded_session && snap.raw.syrix_loaded_session.nonce]);

  const visibleSessions = useMemo(
    () => sessions.filter(s => !deletedSessionIds.has(String(s && s.id || ''))),
    [sessions, deletedSessionIds]
  );

  function submit(textOverride, opts) {
    const spoken = !!(opts && opts.spoken);
    // A message sent while a file is still uploading or being read waits for
    // it and goes WITH it. It used to go at once, without the file: asked
    // about an image that was still analysing, SYRIX answered that it could
    // not see one.
    const stillReading = attachments.filter(c => c.status === 'uploading' || c.status === 'analyzing');
    if (stillReading.length) {
      const held = (typeof textOverride === 'string' ? textOverride : (draft || '')).trim();
      if (held) {
        heldSendRef.current = { text: held, opts };
        setHeldSendName(stillReading[0].name || 'your file');
        if (!spoken) setDraft('');
      }
      return { rid: '', promise: Promise.resolve('') };
    }
    // Ready attachments (Document Intelligence) to ground this turn on.
    const readyDocs = attachments.filter(c => c.status === 'ready' || c.status === 'partial');
    const readyDocIds = readyDocs.map(c => c.doc_id).filter(Boolean);
    let t = (typeof textOverride === 'string' ? textOverride : (draft || '')).trim();
    // Allow sending with attachments and no text — default to a sensible ask.
    if (!t && readyDocIds.length) {
      t = readyDocs.length > 1 ? 'Analyze these files.' : 'Analyze this file.';
    }
    if (!t) return;
    try { window.dispatchEvent(new CustomEvent('syrix-presence', { detail: { kind: 'sent' } })); } catch (_) {}
    // 2026-07-04 — chat always goes through to the backend and gets a real
    // reply, even while SYRIX is asleep/muted. Sleep/mute only affect voice
    // (mic capture + TTS output); typed chat is a separate channel and should
    // never be short-circuited with a canned "I'm sleeping" bubble.
    // Show the user's bubble immediately; reconciled when the backend echoes it.
    // Attachment summaries ride along so the chips render on the optimistic
    // bubble too (the backend stamps the durable copy on its echoed line).
    const attMeta = readyDocs.slice(0, 6).map(c => ({
      name: c.name, kind: c.kind || '', subtype: c.subtype || '',
    }));
    setPendingUserMsgs(p => [...p.slice(-8), {
      role: 'user', text: t, ts: Date.now() / 1000, _opt: true,
      attachments: attMeta.length ? attMeta : undefined,
    }]);
    // Snapshot the sticky model for THIS turn (mid-flight switching must not
    // repaint the pending turn's thinking panel).
    // Voice always answers on Spark: a spoken reply has to start in seconds.
    const voiceTurn = voice.active;
    const modelForTurn = voiceTurn ? 'spark' : ((model === 'zenith' || model === 'auto') ? model : 'spark');
    pendingModelRef.current = modelForTurn;
    // Offline honesty: the send is queued through the control channel and
    // answered on reconnect — say so instead of showing silent progress.
    const _connNow = String((snap && snap.status) || 'online');
    if (_connNow === 'offline' || _connNow === 'reconnecting') {
      setOfflineSendTs(Date.now());
    }
    const sendPromise = egNav((deepResearch && !voiceTurn) ? `[[SYRIX_MODE:deep_research]] ${t}` : t, modelForTurn, readyDocIds, voiceTurn);
    // Rid-scope this turn: the bridge exposes the request id synchronously on
    // the returned promise. Everything the panel shows for this turn (ladder
    // vs zenith panel, token stream, done detection) keys off this rid.
    const ridForTurn = sendPromise && sendPromise.requestId ? String(sendPromise.requestId) : '';
    // Every turn sent while the orb is up — spoken, typed, or held for a file
    // and sent now — is answered out loud.
    if (voiceTurn && ridForTurn) voice.adoptTurn(ridForTurn, sendPromise);
    clearPendingTerminalGrace();
    setPendingTurn({ rid: ridForTurn, model: modelForTurn, sentTs: Date.now() / 1000 });
    setExpireStamp(0);
    // A spoken turn leaves whatever is half-typed in the box alone.
    if (!spoken) setDraft('');
    setDeepResearch(false); setLocalPending(true); setChatOpen(true);
    // The attachments moved up with the message; clear the tray for the next turn.
    if (readyDocIds.length) setAttachments([]);
    if (!spoken) setTimeout(() => { try { inputRef.current?.focus(); } catch (_) {} }, 30);
    return { rid: ridForTurn, promise: sendPromise };
  }
  voiceSubmitRef.current = (text) => submit(text, { spoken: true });
  voiceCancelRef.current = () => cancelTurn();

  // A related question clicked under an answer is sent as the next message.
  // Re-subscribed each render so it always calls the current submit.
  useEffect(() => {
    const onAsk = (ev) => {
      const t = String((ev && ev.detail && ev.detail.text) || '').trim();
      if (t) submit(t);
    };
    window.addEventListener('syrix-ask', onAsk);
    return () => window.removeEventListener('syrix-ask', onAsk);
  });

  // Drop an optimistic echo once the backend has echoed the same text into
  // chat_lines (matched normalised), so there's never a duplicate bubble.
  useEffect(() => {
    if (!pendingUserMsgs.length) return;
    const base = Array.isArray(snap.chat_lines) ? snap.chat_lines : [];
    const seen = new Set(base.filter(egIsUser).map(m => String(m.text || m.message || m.content || '').trim().toLowerCase()));
    const now = Date.now() / 1000;
    setPendingUserMsgs(p => {
      // 2026-06-21 — keep the question bubble until the backend echoes it back
      // (the `seen` reconciliation), with only a long leak-guard backstop. The
      // old 25s clock dropped the user's question mid-flight on 30-60s research
      // turns, so the answer arrived (by voice) with no visible question. 300s
      // is comfortably past any real turn.
      const next = p.filter(pm => !seen.has(String(pm.text || '').trim().toLowerCase()) && (now - (pm.ts || 0)) < 300);
      return next.length === p.length ? p : next; // same ref when unchanged → no render loop
    });
  }, [snap.chat_lines && snap.chat_lines.length]);

  // Merge backend chat_lines with any still-pending optimistic user messages.
  // When a past session is reopened, show its stored transcript plus any NEW
  // live lines that arrive after it was loaded (so follow-ups keep working).
  const displayLines = useMemo(() => {
    const base = Array.isArray(snap.chat_lines) ? snap.chat_lines : [];
    let core;
    if (sessionView && Array.isArray(sessionView.lines)) {
      // Live follow-ups that arrived AFTER this chat was reopened, matched by
      // timestamp (not an absolute index that the 160-line cap would shift), so
      // the previous conversation's lingering live lines never bleed in.
      const since = Number(sessionView.sinceTs || 0);
      // A reopened chat is its saved transcript plus what is said in it from
      // now on. A live line that repeats a saved one — same request, same
      // role — is a copy, never a new message, whatever its timestamp says.
      const savedKeys = new Set(sessionView.lines.map(m => (
        String((m && m.request_id) || '') + '|' + (egIsUser(m) ? 'u' : 'a') + '|'
        + (m && m.request_id ? '' : String((m && (m.text || m.message || m.content)) || '').trim())
      )));
      const tail = since > 0
        ? base.filter(m => egLineTsSeconds(m) > since && !savedKeys.has(
            String((m && m.request_id) || '') + '|' + (egIsUser(m) ? 'u' : 'a') + '|'
            + (m && m.request_id ? '' : String((m && (m.text || m.message || m.content)) || '').trim())))
        : [];
      core = sessionView.lines.concat(tail);
    } else {
      core = base;
    }
    if (!pendingUserMsgs.length && !localMessages.length) return core;
    const seen = new Set(core.filter(egIsUser).map(m => String(m.text || m.message || m.content || '').trim().toLowerCase()));
    const extra = pendingUserMsgs.filter(pm => !seen.has(String(pm.text || '').trim().toLowerCase()));
    const withPending = extra.length ? core.concat(extra) : core;
    return localMessages.length ? withPending.concat(localMessages) : withPending;
  }, [snap.chat_lines, pendingUserMsgs, sessionView, localMessages]);
  // Supercomputer is its own thread, tagged channel:'supercomputer' by the
  // backend's chat-line chokepoint, and never shows in normal chat. Voice
  // turns are ordinary chat turns; lines tagged 'voice' by the retired voice
  // screen show here too, so those older conversations reopen intact.
  const chatOnlyLines = useMemo(
    () => displayLines.filter(m => String((m && m.channel) || '') !== 'supercomputer'),
    [displayLines]
  );
  /* Launch surface — true while this chat has nothing in it and nothing is in
     flight. It centres the composer, shows the greeting above it and the
     starting-point chips below it; the first message drops all three and the
     composer glides down to the foot of the window. */
  const isHome = chatOnlyLines.length === 0 && !working && !localPending;
  const greeting = useMemo(() => {
    const h = new Date().getHours();
    const part = h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
    const who = egOwnerFirstName();
    return who ? part + ', ' + who + '.' : part + '.';
  // `hm` ticks once a minute, which is often enough to cross a boundary.
  }, [hm]);
  function onUploadClick() { try { fileInputRef.current?.click(); } catch (_) {} }

  // Hard wire limit: the backend WS server rejects frames over 12 MB
  // (syrix_host_hud_bridge max_size), and base64 inflates by ~37%. Anything larger
  // than 8 MB would silently kill the socket and leave the card spinning on
  // "Analyzing…" forever — fail it honestly up front instead.
  const EG_MAX_UPLOAD_BYTES = 8 * 1024 * 1024;

  // Send one file to the backend document engine and track it as a card.
  function beginUpload(f) {
    if (!f) return;
    const uploadId = 'up-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 7);
    if (Number(f.size || 0) > EG_MAX_UPLOAD_BYTES) {
      setAttachments(a => [...a, {
        upload_id: uploadId, name: f.name, size: f.size, type: f.type || '',
        status: 'failed', subtitle: 'Too large — max 8 MB per file',
        error: 'file exceeds the 8 MB chat upload limit', doc_id: '', progress: 0,
      }]);
      return;
    }
    setAttachments(a => [...a, {
      upload_id: uploadId, name: f.name, size: f.size, type: f.type || '',
      status: 'uploading', subtitle: 'Uploading…', doc_id: '', error: '', _file: f,
      progress: 0,
    }]);
    const r = new FileReader();
    // Real read progress for the uploading phase (analysis stays indeterminate).
    r.onprogress = (ev) => {
      if (!ev || !ev.lengthComputable || !ev.total) return;
      const pct = Math.min(99, Math.round((ev.loaded / ev.total) * 100));
      setAttachments(a => a.map(c => (c.upload_id === uploadId && c.status === 'uploading')
        ? { ...c, progress: pct } : c));
    };
    r.onload = () => {
      if (cancelledUploadsRef.current.has(uploadId)) return;   // cancelled mid-read — don't send
      try {
        window.SyrixBridge?.send?.('upload_file', { data: {
          name: f.name, size: f.size, type: f.type || '', mime: f.type || '',
          upload_id: uploadId, payload_b64: String(r.result || ''),
        } });
      } catch (_) {}
      setAttachments(a => a.map(c => (c.upload_id === uploadId && c.status !== 'cancelled')
        ? { ...c, status: 'analyzing', subtitle: 'Analyzing…', progress: 100 } : c));
    };
    r.onerror = () => setAttachments(a => a.map(c => (c.upload_id === uploadId && c.status !== 'cancelled')
      ? { ...c, status: 'failed', subtitle: 'Read failed', error: 'could not read file' } : c));
    r.readAsDataURL(f);
  }

  function onUploadChosen(e) {
    const files = Array.from(e.target.files || []);
    files.forEach(beginUpload);
    if (files[0]) {
      try { window.dispatchEvent(new CustomEvent('syrix-presence', { detail: { kind: 'attached', name: files[0].name } })); } catch (_) {}
    }
    try { e.target.value = ''; } catch (_) {}
  }

  function onDropFiles(fileList) {
    const files = Array.from(fileList || []);
    if (files.length) { files.forEach(beginUpload); setChatOpen(true); }
  }

  // Paste-to-attach (2026-07-16): pasted images/files in either composer
  // become attachments, exactly like drag-and-drop or the + picker.
  function onComposerPaste(e) {
    try {
      const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
      if (files.length) {
        e.preventDefault();
        files.forEach(beginUpload);
        setChatOpen(true);
      }
    } catch (_) {}
  }

  function removeAttachment(uploadId) {
    // A busy card first becomes 'cancelled' (in-flight work is abandoned and
    // any late backend status for it is ignored); a second click — or X on a
    // settled card — removes it outright.
    const card = attachments.find(c => c.upload_id === uploadId);
    if (card && (card.status === 'uploading' || card.status === 'analyzing')) {
      cancelledUploadsRef.current.add(uploadId);
      setAttachments(a => a.map(c => c.upload_id === uploadId
        ? { ...c, status: 'cancelled', subtitle: 'Cancelled', progress: 0 } : c));
      return;
    }
    setAttachments(a => a.filter(c => c.upload_id !== uploadId));
  }

  function retryAttachment(uploadId) {
    const card = attachments.find(c => c.upload_id === uploadId);
    if (card && card._file) {
      cancelledUploadsRef.current.delete(uploadId);
      setAttachments(a => a.filter(c => c.upload_id !== uploadId));
      beginUpload(card._file);
    }
  }

  // Reconcile backend card status (hud_state.syrix_docs) into our attachments,
  // matched by upload_id. The backend fills kind/subtitle/doc_id + final status.
  useEffect(() => {
    const docs = (snap.raw && Array.isArray(snap.raw.syrix_docs)) ? snap.raw.syrix_docs : null;
    if (!docs || !docs.length) return;
    const byId = {};
    docs.forEach(d => { const k = String(d.upload_id || d.doc_id || ''); if (k) byId[k] = d; });
    setAttachments(a => {
      let changed = false;
      const next = a.map(c => {
        const b = byId[c.upload_id];
        if (!b) return c;
        if (c.status === 'cancelled') return c;   // user cancelled — ignore late status
        const merged = {
          ...c,
          doc_id: b.doc_id || c.doc_id,
          kind: b.kind || c.kind,
          subtype: b.subtype || c.subtype,
          status: b.status || c.status,
          subtitle: b.subtitle || c.subtitle,
          error: b.error || c.error,
          notes: b.notes || c.notes,
        };
        if (merged.status !== c.status || merged.subtitle !== c.subtitle || merged.doc_id !== c.doc_id) changed = true;
        return merged;
      });
      return changed ? next : a;
    });
  }, [snap.raw && snap.raw.syrix_docs]);
  useEffect(() => {
    if (!heldSendRef.current) return;
    if (attachments.some(c => c.status === 'uploading' || c.status === 'analyzing')) return;
    const held = heldSendRef.current;
    heldSendRef.current = null;
    setHeldSendName('');
    submit(held.text, held.opts);
  });
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    // Height goes through --composer-h: the shared input rules pin height
    // with !important, which an inline height cannot beat.
    el.style.setProperty('--composer-h', 'auto');
    const full = el.scrollHeight;
    el.style.setProperty('--composer-h', Math.min(full, 184) + 'px');
    el.style.overflowY = full > 184 ? 'auto' : 'hidden';
    const tall = full > 40;
    if (tall !== composerTall) setComposerTall(tall);
  }, [draft]);
  function cancelSpeech() {
    try {
      const B = window.SyrixBridge;
      if (B?.cancelActiveTurn) B.cancelActiveTurn();
      if (B?.cancelSpeech)     B.cancelSpeech();
      if (B?.send)             B.send('cancel_speech');
    } catch (_) {}
  }
  function cancelTurn() {
    setLocalPending(false); setPendingTurn(null); cancelSpeech();
    // Stop also silences a spoken reply.
    if (voice.active) voice.tap();
  }

  /* ── Panels ── */
  const [widgetMenuOpen, setWidgetMenuOpen] = useState(false);
  const [deckOpen, setDeckOpen] = useState(false);
  const [plusMenuOpen,  setPlusMenuOpen]  = useState(false);

  /* ── Rail menu hover-open/close (2026-07-04) — Widgets/News/Finance open on
     hover (in addition to click) once the rail itself is expanded; moving the
     mouse off both the rail button AND the flyout panel closes it again after
     a short grace delay so crossing the gap between the two doesn't flicker.
     Each flyout anchors its `top` to the button that opened it (captured from
     the button's own rect at hover/click time) so it grows out beside that
     exact row instead of floating centered on screen. */
  const railMenuCloseTimers = useRef({});
  const railBtnRefs = useRef({});
  const [railMenuAnchor, setRailMenuAnchor] = useState({});
  function railMenuEnter(name, setter) {
    const t = railMenuCloseTimers.current[name];
    if (t) { clearTimeout(t); railMenuCloseTimers.current[name] = null; }
    const el = railBtnRefs.current[name];
    if (el) setRailMenuAnchor(a => ({ ...a, [name]: el.getBoundingClientRect().top }));
    setter(true);
  }
  function railMenuLeave(name, setter) {
    const t = railMenuCloseTimers.current[name];
    if (t) clearTimeout(t);
    railMenuCloseTimers.current[name] = setTimeout(() => setter(false), 180);
  }
  const [widgetMenuMounted, widgetMenuPhase] = useMenuMount(widgetMenuOpen);
  // Keep the rail itself visually expanded the whole time one of its flyouts
  // is open/closing — otherwise :hover alone collapses the rail the instant
  // the mouse leaves it for the flyout (which sits outside the rail's own
  // DOM subtree), snapping the rail narrow while the flyout is still open.
  const [deckMounted, deckPhase] = useMenuMount(deckOpen);
  const railMenuOpen = widgetMenuMounted || deckMounted;

  /* ── Overlay preset ── */
  const [preset, setPreset] = useState(() => {
    try { return localStorage.getItem('eg-preset') || 'clean'; } catch (_) { return 'clean'; }
  });
  function applyPreset(p) {
    setPreset(p);
    try { localStorage.setItem('eg-preset', p); } catch (_) {}
  }


  /* ── Browser apps ── */
  const hudApps     = snap?.hud_apps || snap?.syrix_hud_apps || [];
  const activeAppId = snap?.syrix_hud_active_app_id || hudApps[0]?.id || null;
  const browserOpen = hudApps.length > 0;

  /* ── Privacy ── */
  // The rail's Private button and the island's copy of it call through a ref,
  // because the island registry is built once on mount.
  const setPrivateModeRef = useRef(null);
  setPrivateModeRef.current = setPrivateMode;
  function togglePrivateMode() {
    try { setPrivateModeRef.current && setPrivateModeRef.current(!privateModeRef.current); } catch (_) {}
  }

  /* ── Browser open helper ── */
  function openBrowser(url) {
    const target = url || 'https://www.google.com';
    try {
      if (window.SyrixBridge?.openAppInHud) { window.SyrixBridge.openAppInHud(target, { title: 'Browser' }); return; }
    } catch (_) {}
    try { window.open(target, '_blank', 'noopener'); } catch (_) {}
  }
  function openWidget(type) {
    if (typeof onOpenWidget === 'function') { onOpenWidget(type); return; }
    egNav('open ' + type);
  }

  /* ── Rail — living sidebar (2026-07-04). Compact icon rail by default;
     expands on hover (pure CSS) with labels animating in; nothing shifts
     because the rail floats above the canvas. ── */

  /* ── Connectors page (2026-07-12) — full-window view over the HUD, opened
     from the rail. Renders window.SyrixConnectorsPanel; Esc / "Back to HUD"
     always return here. ── */
  const [connectorsOpen, setConnectorsOpenRaw] = useState(false);
  const setConnectorsOpen = useCallback((v) => egSmooth(() => setConnectorsOpenRaw(v)), []);

  /* ── Stage mode — browser fills the window; rail folds into the island's
     folder button, chat docks left, bottom composer hides. Everything below
     derives from SyrixStage so it self-restores when the last tab closes. ── */
  const [stageState, setStageStateLocal] = useState(() => (
    window.SyrixStage ? window.SyrixStage.get() : { active: false }
  ));
  useEffect(() => {
    if (!window.SyrixStage) return;
    return window.SyrixStage.subscribe(setStageStateLocal);
  }, []);
  const stageActive = !!(stageState && stageState.active);

  useEffect(() => {
    document.body.classList.toggle('stage-browser', stageActive);
    return () => document.body.classList.remove('stage-browser');
  }, [stageActive]);

  // The sidebar width is published on <body>, not just on the shell: the
  // Dynamic Island docks to the sidebar's foot and lives in the primary-app
  // subtree, so it has to read the same --eg-side-w the shell lays out from.
  useEffect(() => {
    document.body.classList.toggle('eg-side-collapsed', sideCollapsed);
    return () => document.body.classList.remove('eg-side-collapsed');
  }, [sideCollapsed]);

  // Rail actions → SyrixStage registry, so the island's folder grid calls the
  // EXACT same handlers as the rail buttons. Registered once on mount; the
  // setState functions are stable and the helpers only touch stable setters.
  useEffect(() => {
    const S = window.SyrixStage;
    if (!S || !S.registry) return;
    const unsubs = [
      S.registry.register('chat', () => { setChatOpen(true); setTimeout(() => { try { inputRef.current?.focus(); } catch (_) {} }, 50); }, { label: 'Chat', icon: '💬', order: 1 }),
      S.registry.register('widgets', () => setWidgetMenuOpen(v => !v), { label: 'Widgets', icon: '🧩', order: 3 }),
      S.registry.register('deck', () => setDeckOpen(v => !v), { label: 'Deck', icon: '🎛️', order: 3.5 }),
      S.registry.register('browser', () => {
        const staged = !!(window.SyrixStage && window.SyrixStage.get().active);
        if (staged && window.SyrixBridge?.openAppInHud) {
          // In stage a "Browser" press means a NEW tab, not refocusing Google.
          window.SyrixBridge.openAppInHud('https://www.google.com', { title: 'Browser', new_tab: true });
          return;
        }
        openBrowser();
      }, { label: 'New Tab', icon: '🌐', order: 4 }),
      S.registry.register('connectors', () => setConnectorsOpen(true), { label: 'Connectors', icon: '🔌', order: 8.5 }),
      S.registry.register('privacy', () => togglePrivateMode(), { label: 'Private', icon: '🛡️', order: 9 }),
      S.registry.register('settings', () => { try { onOpenSettings && onOpenSettings(); } catch (_) {} }, { label: 'Settings', icon: '⚙️', order: 10 }),
    ];
    return () => unsubs.forEach((u) => { try { u(); } catch (_) {} });
  }, []);

  // Island nav bar chat button → toggle the docked chat panel open/closed
  // (while staged this is the only way to get a full-screen browser, since
  // the chat's own header close is a no-op in stage mode — see onClose below).
  useEffect(() => {
    function onToggle() {
      setChatOpen(v => {
        const next = !v;
        if (next) setTimeout(() => { try { inputRef.current?.focus(); } catch (_) {} }, 60);
        return next;
      });
    }
    window.addEventListener('syrix:stage-chat-toggle', onToggle);
    return () => window.removeEventListener('syrix:stage-chat-toggle', onToggle);
  }, []);

  // Force the chat into the left-docked stage layout while staged; restore the
  // user's previous layout verbatim on exit.
  const prevChatLayoutRef = useRef('compact');
  const prevStageActiveRef = useRef(false);
  useEffect(() => {
    if (stageActive && !prevStageActiveRef.current) {
      prevChatLayoutRef.current = chatLayout === 'stage-right' ? 'compact' : chatLayout;
      setChatLayout('stage-right');
    } else if (!stageActive && prevStageActiveRef.current) {
      setChatLayout(prevChatLayoutRef.current || 'compact');
    }
    prevStageActiveRef.current = stageActive;
  }, [stageActive]);

  // Live view inset — the browser tweens right to make room for the docked
  // chat column (no freeze needed; both stay fully interactive).
  useEffect(() => {
    const S = window.SyrixStage;
    if (!S || !stageActive) return;
    S.setChatInset(chatOpen ? 440 : 0);
  }, [stageActive, chatOpen]);

  // Note: chat used to close/shrink back to a compact bubble whenever mic
  // unmuted (voice came back on). Chat is now the permanent background
  // surface regardless of mic state, so that revert no longer happens —
  // the Voice Mode overlay (z-index 9600) fully covers it anyway whenever
  // Voice Mode itself is actually open.

  // Shell panels that must paint above the staged browser → freeze tags.
  egUseStageOverlay('eg-widget-menu', stageActive && widgetMenuOpen);
  egUseStageOverlay('eg-deck-menu', stageActive && deckOpen);

  /* ── Toasts from island notice queue ── */
  const [toasts, setToasts] = useState([]);
  const shownToastRef = useRef(new Set());
  useEffect(() => {
    const q = snap?.island_notice_queue;
    if (!Array.isArray(q) || !q.length) return;
    const latest = q[q.length - 1];
    if (!latest?.text) return;
    const key = latest.text + (latest.ts || '');
    if (shownToastRef.current.has(key)) return;
    shownToastRef.current.add(key);
    const id = ++_egToastSeq;
    setToasts(ts => [...ts.slice(-3), { id, text: latest.text, tone: latest.tone || '' }]);
    setTimeout(() => setToasts(ts => ts.filter(t => t.id !== id)), 4200);
  }, [snap?.island_notice_queue?.length]);
  useEffect(() => {
    const onToast = (e) => {
      const text = String((e && e.detail && e.detail.text) || '');
      if (!text) return;
      const id = ++_egToastSeq;
      setToasts(ts => [...ts.slice(-3), { id, text, tone: '' }]);
      setTimeout(() => setToasts(ts => ts.filter(t => t.id !== id)), 4200);
    };
    window.addEventListener('syrix-shell-toast', onToast);
    return () => window.removeEventListener('syrix-shell-toast', onToast);
  }, []);

  /* ── Body class side-effects ── */
  useEffect(() => {
    EG_PRESETS_IDS.forEach(id => document.body.classList.remove('eg-preset-' + id));
    document.body.classList.add('eg-preset-' + preset);
    return () => EG_PRESETS_IDS.forEach(id => document.body.classList.remove('eg-preset-' + id));
  }, [preset]);
  const shellCls = [
    'eg-shell',
    chatOpen     ? 'chat-is-open'    : '',
    mobileNavOpen ? 'mobile-nav-open' : '',
    chromeMuted  ? 'chrome-muted'    : '',
    sideCollapsed ? 'side-collapsed' : '',
    isHome       ? 'is-home'         : '',
    'preset-' + preset,
  ].filter(Boolean).join(' ');

  return (
    <div className={shellCls}>
      {/* Watermark — small credit in bottom left */}
      <div className="eg-watermark-credit">Made by Kabirr Kohli</div>

      {/* Cursor glow removed by design (2026-06-16). */}

      {/* Toast notification stack */}
      <EgToastStack toasts={toasts} />

      {/* Connectors page — full-window view above the HUD canvas. Mounted only
          while open; the panel handles Esc + "Back to HUD" itself. */}
      {connectorsOpen && typeof window !== 'undefined'
        && typeof window.SyrixConnectorsPanel === 'function' && (
        React.createElement(window.SyrixConnectorsPanel, {
          onClose: () => setConnectorsOpen(false),
        })
      )}

      {/* Supercomputer workspace — full-window agentic environment. Mounted
          only while the mode is active; the workspace handles Esc + "Leave"
          itself and calls onClose, which tears the backend session down. */}
      {superMode && typeof window !== 'undefined'
        && typeof window.SyrixSupercomputerWorkspace === 'function' && (
        React.createElement(window.SyrixSupercomputerWorkspace, {
          onClose: exitSuperMode,
          resumeSessionId: superResumeId,
          onOpenSettings: () => { try { onOpenSettings && onOpenSettings(); } catch (_) {} },
        })
      )}

      {/* Veils for click-outside dismissal */}
      {widgetMenuOpen && <div className="eg-veil" onClick={() => setWidgetMenuOpen(false)} aria-hidden="true" />}
      {deckOpen && <div className="eg-veil" onClick={() => setDeckOpen(false)} aria-hidden="true" />}

      {/* ── Top bar ── */}
      <div className="eg-topbar">
        {/* Left: clock */}
        <div className="eg-topbar-left">
          <button
            type="button"
            className="eg-mobile-nav-toggle"
            aria-label={mobileNavOpen ? 'Close SYRIX navigation' : 'Open SYRIX navigation'}
            aria-expanded={mobileNavOpen}
            onClick={() => setMobileNavOpen(v => !v)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <path d="M4 7h16M4 12h16M4 17h16" />
            </svg>
          </button>
        </div>

        {/* Center: island sits here via primary-app fixed z-index — no additional controls */}
        <div className="eg-topbar-center"></div>

        {/* Right: metrics + win controls */}
        <div className="eg-topbar-right">
          {cpu !== null && <div className="eg-metric"><span>CPU</span><b>{Math.round(cpu)}%</b></div>}
          {ram !== null && <div className="eg-metric"><span>RAM</span><b>{Math.round(ram)}%</b></div>}
          {gpu !== null && <div className="eg-metric"><span>GPU</span><b>{Math.round(gpu)}%</b></div>}
          <div className="eg-win-controls">
            <button className="eg-win-btn"          onClick={() => { try { window.syrixHud?.minimize?.();        } catch (_) {} }} title="Minimize">—</button>
            <button className="eg-win-btn"          onClick={() => { try { window.syrixHud?.toggleMaximize?.(); } catch (_) {} }} title="Maximize">▢</button>
            <button className="eg-win-btn eg-win-x" onClick={() => { try { window.syrixHud?.close?.();           } catch (_) {} }} title="Close">✕</button>
          </div>
        </div>

      </div>

      {/* ── Sidebar ──────────────────────────────────────────────────────────
          One permanent column: brand, New chat, the destinations, then the
          chat history itself. History is a section here rather than a drawer
          over the HUD — picking a past conversation is navigation, so it sits
          with the rest of the navigation. Collapsing shrinks it to an icon
          rail; every control keeps its title, so nothing becomes unreachable.
          ── */}
      {mobileNavOpen && (
        <button
          type="button"
          className="eg-mobile-nav-backdrop"
          aria-label="Close SYRIX navigation"
          onClick={() => setMobileNavOpen(false)}
        />
      )}
      <nav className={"eg-side" + (railMenuOpen ? ' is-menu-open' : '')} aria-label="SYRIX navigation">
        <div className="eg-side-top">
          <div className="eg-side-brand">
            <img className="eg-side-mark" src="../assets/icons/syrix_app_logo.png" alt="" draggable="false" />
            <span className="eg-side-wordmark">SYRIX</span>
          </div>
          <button
            type="button"
            className="eg-side-collapse"
            onClick={toggleSidebar}
            aria-label={sideCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={sideCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="4.5" width="18" height="15" rx="2.5"/>
              <line x1="9.5" y1="4.5" x2="9.5" y2="19.5"/>
            </svg>
          </button>
        </div>

        <button
          type="button"
          className={"eg-side-new" + (newChatPending ? ' is-busy' : '')}
          disabled={newChatPending}
          aria-busy={newChatPending}
          title="New chat"
          data-label="New chat"
          onClick={() => { newChat(); setMobileNavOpen(false); }}>
          <span className="eg-side-new-ico" aria-hidden="true">{EgI.plus}</span>
          <span className="eg-side-txt">New chat</span>
        </button>

        <div className="eg-side-nav">
          {[
            /* Exactly one row is ever "active": it answers "where am I", and
               two marked rows answer nothing. Chat is where you are unless a
               full-window view has taken over. The chats section's own caret
               already shows whether it is open, so History is not a place. */
            { lbl:'Chat',     ico:EgI.chat,      act:() => { setChatOpen(true); setTimeout(()=>{ try{inputRef.current?.focus();}catch(_){} },50); }, cls:(connectorsOpen || superMode) ? '' : 'is-active' },
            { lbl:'Widgets',  ico:EgI.apps,      act:() => setWidgetMenuOpen(v=>!v), cls:widgetMenuOpen?'is-active':'', menuName:'widgets',
              onEnter:() => railMenuEnter('widgets', setWidgetMenuOpen), onLeave:() => railMenuLeave('widgets', setWidgetMenuOpen) },
            { lbl:'Deck',     ico:EgI.deck,      act:() => setDeckOpen(v=>!v), cls:deckOpen?'is-active':'', menuName:'deck',
              tip:'Deck — launch the apps of the SYRIX ecosystem',
              onEnter:() => railMenuEnter('deck', setDeckOpen), onLeave:() => railMenuLeave('deck', setDeckOpen) },
            { lbl:'Browser',  ico:EgI.browser,   act:() => openBrowser() },
            { lbl:'Connectors', ico:EgI.plug,    act:() => setConnectorsOpen(true), cls:connectorsOpen?'is-active':'' },
            { lbl:'Private',  ico:EgI.shield,    act:() => togglePrivateMode(), toggled: privateMode,
              tip: privateMode ? 'Private mode is on — click to turn it off' : 'Private mode — nothing saved, nothing leaves this PC' },
            { lbl:'Settings', ico:EgI.settings,  act:() => onOpenSettings?.() },
          ].map(({ lbl, ico, act, cls, onEnter, onLeave, menuName, toggled, tip }) => (
            <button key={lbl} type="button" className={"eg-side-btn" + (cls ? ' ' + cls : '') + (toggled ? ' is-toggled' : '')}
              ref={menuName ? (el => { railBtnRefs.current[menuName] = el; }) : undefined}
              onClick={() => { act(); setMobileNavOpen(false); }} onMouseEnter={onEnter} onMouseLeave={onLeave}
              aria-pressed={toggled === undefined ? undefined : !!toggled}
              title={tip || lbl} data-label={lbl}>
              <span className="eg-side-ico" aria-hidden="true">{ico}</span>
              <span className="eg-side-txt">{lbl}</span>
              {toggled !== undefined && (
                <span className={"eg-side-switch" + (toggled ? ' is-on' : '')} aria-hidden="true"><span /></span>
              )}
            </button>
          ))}
        </div>

        {/* Drag the edge to resize; double-click restores the default. */}
        <div
          className="eg-side-grip"
          onPointerDown={onSideGripDown}
          onDoubleClick={resetSideWidth}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize sidebar"
          title="Drag to resize · double-click to reset"
        />

        <EgSidebarChats
          footer={<EgProfileRow mood={presence.mood} detail={presence.detail}
            modelLabel={voice.active ? 'Voice · Spark 1.1' : model === 'zenith' ? 'Zenith 1.1' : model === 'auto' ? 'Auto' : 'Spark 1.1'}
            privateMode={privateMode}
            onTogglePrivate={togglePrivateMode}
            voiceActive={voice.active}
            onToggleVoice={() => (voice.active ? voice.stop() : voice.start())}
            onOpenConnectors={() => setConnectorsOpen(true)}
            onOpenSettings={() => { try { onOpenSettings && onOpenSettings(); } catch (_) {} }}
            connectedCount={((snap && (snap.syrix_connectors || (snap.raw && snap.raw.syrix_connectors)) || {}).connectors || [])
              .filter(c => c && (c.health === 'connected' || c.health === 'needs_reauth')).length} />}
          sessions={visibleSessions}
          loading={!sessionsLive}
          onLoad={loadSession}
          onDelete={deleteSession}
          onClearAll={clearAllSessions}
          collapsed={!chatsOpen}
          onToggleCollapsed={() => setChatsOpen(v => !v)}
          onPin={pinSession}
          onRename={renameSession}
          onPeek={peekSession}
          sessionCacheRef={sessionCacheRef}
          searchRef={histSearchRef}
        />
      </nav>

      {/* Widget menu — grows out from the rail beside the Widgets row */}
      {widgetMenuMounted && (
        <EgWidgetMenu onOpenWidget={openWidget} onClose={() => setWidgetMenuOpen(false)}
          phase={widgetMenuPhase} anchorTop={railMenuAnchor.widgets}
          onMouseEnter={() => railMenuEnter('widgets', setWidgetMenuOpen)}
          onMouseLeave={() => railMenuLeave('widgets', setWidgetMenuOpen)} />
      )}

      {/* Deck — the ecosystem launch pad, beside the Deck row */}
      {deckMounted && (
        <EgDeckMenu onClose={() => setDeckOpen(false)}
          phase={deckPhase} anchorTop={railMenuAnchor.deck}
          onMouseEnter={() => railMenuEnter('deck', setDeckOpen)}
          onMouseLeave={() => railMenuLeave('deck', setDeckOpen)} />
      )}

      {/* News channel picker — grows out from the rail beside the News row */}

      {/* Finance site picker — grows out from the rail beside the Finance row */}

      {/* ── Main area (orb rendered by primary-app behind the shell) ──
          2026-06-16 — the user wants the orb completely clear: no "Thinking…" text
          or status bubble floating over it. The chat panel is the single place
          that shows the thinking state now. */}
      <div className="eg-main" />

      {/* ── Chat panel (full glass sheet) ──
          2026-07-09 — always mounted open: chat is the permanent home
          surface (chat-app style), not a panel that opens/closes.
          `open` is hardcoded true so no other state (voice mode, stage
          mode, mic mute) can ever hide it again. */}
      <EgChatPanel
        open={true}
        lines={chatOnlyLines}
        working={working}
        listening={listening}
        micMuted={micMuted}
        draft={draft}
        setDraft={setDraft}
        onSubmit={submit}
        onMic={onMicToggle}
        onClose={() => { stageActive ? setChatOpen(false) : setChatLayout('bg'); }}
        inputRef={inputRef}
        searchTrace={snap.syrix_search_trace || null}
        deepResearch={deepResearch}
        onToggleDeepResearch={() => setDeepResearch(v => !v)}
        model={model}
        onModelChange={setModel}
        zenithTurn={((pendingTurn && pendingTurn.model) || pendingModelRef.current) === 'zenith'}
        pendingRid={(pendingTurn && pendingTurn.rid) || ''}
        ourTurnAnswered={ourTurnAnswered}
        onUploadClick={onUploadClick}
        fileInputRef={fileInputRef}
        onUploadChosen={onUploadChosen}
        attachments={attachments}
        onRemoveAttachment={removeAttachment}
        onRetryAttachment={retryAttachment}
        onDropFiles={onDropFiles}
        onComposerPaste={onComposerPaste}
        onCancelTurn={cancelTurn}
        isSpeaking={isSpeaking}
        onCancelSpeech={cancelSpeech}
        chatLayout={chatLayout}
        onLayoutChange={setChatLayout}
        isHome={isHome}
      />

      {/* Stage mode — minimized-chat edge tab on the left rim; reopens the
          docked chat (the browser tweens over to make room). */}
      {stageActive && !chatOpen && (
        <button className="eg-stage-chat-tab" title="Open chat"
          onClick={() => {
            setChatOpen(true);
            setTimeout(() => { try { inputRef.current?.focus(); } catch (_) {} }, 60);
          }}>
          <span className="eg-stage-chat-tab-ico">💬</span>
          <span className="eg-stage-chat-tab-lbl">CHAT</span>
        </button>
      )}

      {/* ── Bottom bar — the one persistent composer for the canvas chat.
          2026-07-09 — no longer hidden when chat is "open": there is no
          separate panel composer anymore, this bar IS the input. ── */}
      {offlineSendTs > 0 && (
        <div className="eg-offline-note">
          SYRIX is offline — your message is queued and will be answered when it
          reconnects.
        </div>
      )}
      <div className={"eg-bottom-bar" + (bottomDragOver ? ' eg-drag-over' : '')}
        onDragOver={e => {
          try {
            if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) {
              e.preventDefault();
              if (!bottomDragOver) setBottomDragOver(true);
            }
          } catch (_) {}
        }}
        onDragLeave={e => { e.preventDefault(); setBottomDragOver(false); }}
        onDrop={e => {
          e.preventDefault(); setBottomDragOver(false);
          if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) onDropFiles(e.dataTransfer.files);
        }}>
        {/* Attachment tray — a full-width block ABOVE the composer pill
            (2026-07-16). Previously the cards rendered inline inside the pill
            and squeezed the text input; the input now always stays full
            width. The open panel's own composer keeps its copy of the tray
            in side/half/full layouts, where this bar is hidden behind it. */}
        {/* Launch surface — mark, greeting, composer, starting points, in that
            order, in one flex column. The whole stack rides the composer's
            travel down to the foot of the window when the first message
            lands. */}
        {isHome && !voiceDockShown && (
          <div className="eg-home-hero" aria-label="Start a conversation">
            <span className="eg-home-mark" aria-hidden="true">
              <img src="../assets/icons/syrix_app_logo.png" alt="" draggable="false" />
            </span>
            <h2 className="eg-home-title">{privateMode ? 'Private chat' : greeting}</h2>
            <p className="eg-home-sub">{privateMode
              ? 'Not saved to history or memory. Answers come from the local model, and nothing leaves this PC.'
              : 'Ask anything — live research, your machine, your memory.'}</p>
          </div>
        )}
        {privateMode && !isHome && (
          <div className="eg-private-pill" role="status">
            <span className="eg-private-pill-ico" aria-hidden="true">{EgI.shield}</span>
            <span><b>Private</b> — not saved · local model · offline</span>
            <button type="button" onClick={() => setPrivateMode(false)}>Turn off</button>
          </div>
        )}
        {voiceDockShown && <EgVoiceDock voice={voice} leaving={!voice.active} />}
        {chatLayout !== 'side' && chatLayout !== 'half' && chatLayout !== 'full' && (
          <EgAttachTray attachments={attachments} onRemove={removeAttachment}
            onRetry={retryAttachment} docked />
        )}
        {/* Black liquid-glass island pill — all controls live inside */}
        <div className={"eg-bottom-composer" + (composerTall ? ' is-tall' : '') + (attachments.length ? ' has-files' : '') + (model === 'zenith' ? ' zenith' : '') + (model === 'auto' ? ' auto' : '') + (dictating ? ' is-dictating' : '') + (dictationBusy ? ' is-transcribing' : '') + (voice.active ? ' is-voice' : '') + (privateMode ? ' is-private' : '')}>
          {/* + button → file upload (direct, no menu — menu was clipped by overflow:hidden) */}
          <div className="eg-plus-wrap">
            <button
              className="eg-plus-btn"
              onClick={onUploadClick}
              title="Attach / upload">
              {EgI.plus}
            </button>
          </div>
          {/* SPARK / ZENITH model switcher — same dropdown as the open panel. */}
          <EgModelSwitcher model={model} onModelChange={setModel} />
          {/* Inner pill — the text input lives inside its own pill shape */}
          <div className={"eg-bottom-input-pill" + (dictating ? ' is-dictating' : '') + (dictationBusy ? ' is-transcribing' : '')}>
            {dictating && <EgMicWave active />}
            {/* A textarea that grows with what is typed, as in ChatGPT: a long
                message wraps onto new lines and stays readable instead of
                sliding sideways out of a one-line box. Enter sends,
                Shift+Enter starts a new line. */}
            <textarea
              ref={composerRef}
              className="eg-bottom-input"
              rows={1}
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onPaste={onComposerPaste}
              onKeyDown={e => {
                if (egShouldSend(e)) { e.preventDefault(); submit(); }
                if (e.key === 'Escape') setPlusMenuOpen(false);
                if (e.key === 'ArrowUp' || e.key === 'ArrowDown') setChatOpen(true);
              }}
              onFocus={() => { setChatOpen(true); setPlusMenuOpen(false); }}
              placeholder={
                dictationNoMic ? 'Plug in a mic to use this'
                : dictating ? 'Speak to text — listening…'
                : dictationBusy ? 'Transcribing…'
                : heldSendName ? 'Sending as soon as ' + heldSendName + ' is ready…'
                : voice.active ? 'Talk, or type a message…'
                : privateMode ? 'Private chat — not saved'
                : 'Type to chat...'
              }
            />
          </div>
          {/* Dictation — speak and it types into the box (2026-07-11). Not a
              mute control; global mic/TTS mute lives in Settings now. */}
          <button
            className={"eg-bottom-mute-btn" + (dictating ? ' is-listening' : '') + (dictationBusy ? ' is-transcribing' : '')}
            onClick={toggleDictation}
            disabled={dictationBusy || voice.active}
            title={dictating ? 'Listening — click to stop dictating' : dictationBusy ? 'Transcribing…' : 'Dictate — speak to type'}>
            {dictationBusy ? EgI.spinner || EgI.mic : EgI.mic}
          </button>
          {/* Voice — the orb joins this chat; the same button ends it. */}
          <button
            type="button"
            className={"eg-bottom-voice-btn" + (voice.active ? ' is-live' : '')}
            onClick={() => (voice.active ? voice.stop() : voice.start())}
            disabled={dictating || dictationBusy}
            aria-pressed={voice.active}
            title={voice.active ? 'End voice' : 'Talk to SYRIX'}>
            {EgI.wave}
          </button>
          {/* Send — enabled with text OR ready attachments (parity with the
              panel composer: attachments-only sends default to "Analyze this
              file." inside submit()). While SYRIX is thinking/speaking on the
              turn we just sent, this same button becomes Stop (same shape,
              square glyph instead of the arrow) so there's no separate
              control to hunt for — click again to cancel. */}
          <button
            className={"eg-bottom-send-btn" + ((working || isSpeaking) ? ' is-stop' : '')}
            onClick={(working || isSpeaking) ? cancelTurn : submit}
            disabled={!(working || isSpeaking) && !draft.trim() && !attachments.some(c => c.status === 'ready' || c.status === 'partial')}
            title={(working || isSpeaking) ? 'Stop Syrix' : 'Send'}>
            {(working || isSpeaking) ? EgI.stop : EgI.send}
          </button>
        </div>
        {/* Starting points — a chip row under the centred composer, shown only
            on the launch surface. Sending anything (including one of these)
            fills the transcript, which drops the row and glides the composer
            down to the foot of the window. */}
        {isHome && !voiceDockShown && !privateMode && (
          <div className="eg-starters" aria-label="Starting points">
            {(() => {
              const cx = egConnectLogos(snap);
              return (
                <button type="button" className="eg-starter is-connect"
                  title="Connect your apps so SYRIX can work with them"
                  onClick={() => setConnectorsOpen(true)}>
                  <span className="eg-connect-stack" aria-hidden="true">
                    {cx.logos.map(l => <img key={l} src={'assets/connector-logos/' + l} alt="" draggable="false" />)}
                  </span>
                  <span className="eg-starter-lbl">{cx.count ? cx.count + (cx.count === 1 ? ' app' : ' apps') + ' connected' : 'Connect an app'}</span>
                </button>
              );
            })()}
            {quickActions.map(action => (
              <button
                key={action.prompt}
                type="button"
                className="eg-starter"
                title={action.prompt}
                aria-label={action.prompt}
                onClick={() => submit(action.prompt)}>
                <span className="eg-starter-ico" aria-hidden="true">{action.icon}</span>
                <span className="eg-starter-lbl">{action.label}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

const EG_PRESETS_IDS = ['clean', 'research'];
window.SyrixAuroraShell = SyrixAuroraShell;
})();
