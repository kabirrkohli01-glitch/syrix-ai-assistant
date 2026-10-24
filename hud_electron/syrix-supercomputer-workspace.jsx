/* SYRIX Supercomputer workspace — full-window agentic environment (2026-07-24).
 *
 * Layout matches the approved design: top bar with the mode pill, a narrow left
 * rail, the chat column, and the work surface (task bar → browser → drawer).
 *
 * Everything here is scoped to Supercomputer. It reads the `syrix_supercomputer`
 * key of the bridge snapshot and writes through SyrixBridge.send('supercomputer_*')
 * plus the `window.syrixHud.superComputer` IPC namespace for browser chrome.
 * Nothing in this file touches normal SYRIX state.
 *
 * Two state sources, deliberately:
 *   · browser chrome (tabs, URL, loading) comes straight from Electron over
 *     `sc-browser:state`, because a tab click must feel instant and is a local
 *     concern;
 *   · task, steps, permissions, results and status come from the Python
 *     snapshot, because those are the backend's truth.
 *
 * Exposed as window.SyrixSupercomputerWorkspace({ onClose }).
 */

(function () {
  'use strict';
  if (typeof React === 'undefined') return;
  const { useState, useEffect, useRef, useCallback, useMemo } = React;

  const LS_SPLIT = 'syrix_sc_split_v1';
  const LS_DRAWER = 'syrix_sc_drawer_v1';
  const LS_DRAWER_TAB = 'syrix_sc_drawer_tab_v1';
  const CHAT_MIN = 300, CHAT_MAX = 900, CHAT_DEFAULT = 440;
  const DRAWER_MIN = 120, DRAWER_MAX = 520, DRAWER_DEFAULT = 200;

  /* ── Bridge helpers ─────────────────────────────────────────────────── */
  function scSend(command, data) {
    try {
      const B = window.SyrixBridge;
      if (B && B.send) return B.send(command, { data: data || {} });
    } catch (_) {}
    return false;
  }

  function scNative() {
    try { return (window.syrixHud && window.syrixHud.superComputer) || null; }
    catch (_) { return null; }
  }

  function useScSnapshot() {
    const [snap, setSnap] = useState(() => {
      try { return (window.SyrixBridge && window.SyrixBridge.getSnapshot()) || {}; }
      catch (_) { return {}; }
    });
    useEffect(() => {
      const B = window.SyrixBridge;
      if (!B || !B.subscribe) return undefined;
      return B.subscribe(setSnap);
    }, []);
    return snap;
  }

  /* The `syrix_supercomputer` key is written by a SEPARATE writer from the HUD's
     main state builder, and is re-injected into every filtered write. Between
     those two writes a snapshot can arrive without the key at all — and a naive
     `|| {}` fallback then blanks the task, the steps and the status for one
     frame, which is exactly the flicker the user saw during a running task.

     So: hold the last state that actually had content, and only replace it when
     a snapshot genuinely carries one. A missing key means "no news", never
     "everything was cleared". Leaving the mode is what resets it, not a dropped
     frame. */
  function useScState(snap) {
    const lastRef = useRef(null);
    const incoming = (snap && (snap.syrix_supercomputer
      || (snap.raw && snap.raw.syrix_supercomputer))) || null;
    if (incoming && typeof incoming === 'object' && Object.keys(incoming).length) {
      lastRef.current = incoming;
    }
    return lastRef.current || {};
  }

  function clockOf(ts) {
    const t = Number(ts || 0);
    if (!t) return '';
    const d = new Date(t < 1e12 ? t * 1000 : t);
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }

  function readNum(key, dflt, min, max) {
    try {
      const v = Number(localStorage.getItem(key));
      if (Number.isFinite(v) && v >= min && v <= max) return v;
    } catch (_) {}
    return dflt;
  }

  /* ── Icons (inline SVG, 1.6 stroke to match the HUD) ────────────────── */
  const I = {
    back: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M11 18l-6-6 6-6"/></svg>,
    gear: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 12a7.4 7.4 0 0 0-.1-1.2l2.1-1.6-2-3.5-2.5 1a7.5 7.5 0 0 0-2-1.2l-.4-2.6h-4l-.4 2.6a7.5 7.5 0 0 0-2 1.2l-2.5-1-2 3.5 2.1 1.6a7.4 7.4 0 0 0 0 2.4l-2.1 1.6 2 3.5 2.5-1c.6.5 1.3.9 2 1.2l.4 2.6h4l.4-2.6a7.5 7.5 0 0 0 2-1.2l2.5 1 2-3.5-2.1-1.6c.1-.4.1-.8.1-1.2z"/></svg>,
    stop: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/></svg>,
    pause: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round"><path d="M9 6v12M15 6v12"/></svg>,
    play: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round"><path d="M8 5l11 7-11 7z" fill="currentColor"/></svg>,
    hand: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3.2"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/></svg>,
    ret: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/></svg>,
    pen: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>,
    chev: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6"/></svg>,
    dots: <svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>,
    arrowL: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>,
    arrowR: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>,
    reload: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M20 11a8 8 0 1 0-.6 4"/><path d="M20 5v6h-6"/></svg>,
    lock: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><rect x="4.5" y="10.5" width="15" height="10" rx="2"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/></svg>,
    globe: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18z"/></svg>,
    plus: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>,
    x: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>,
    clock: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>,
    check: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.6 2.6L16 9.5"/></svg>,
    circle: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="12" cy="12" r="9"/></svg>,
    ban: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"><circle cx="12" cy="12" r="9"/><path d="M6 18L18 6"/></svg>,
    mic: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11A6.5 6.5 0 0 0 12 17.5 6.5 6.5 0 0 0 18.5 11M12 17.5V21"/></svg>,
    micOff: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><line x1="3" y1="3" x2="21" y2="21"/><path d="M9 9v3a3 3 0 0 0 5.1 2.1M15 9.3V6a3 3 0 0 0-5.9-.6"/><path d="M17 16.9A6.5 6.5 0 0 1 5.5 11M18.5 11v.4M12 17.5V21"/></svg>,
    clip: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12.5l-8.5 8.5a5.5 5.5 0 0 1-7.8-7.8l9-9a3.7 3.7 0 0 1 5.2 5.2l-9 9a1.8 1.8 0 0 1-2.6-2.6l8.1-8.1"/></svg>,
    send: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12h13M12 5l7 7-7 7"/></svg>,
    file: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>,
    img: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="10" r="1.5"/><path d="M21 16l-5-5-9 8"/></svg>,
    code: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M9 18l-6-6 6-6M15 6l6 6-6 6"/></svg>,
    beaker: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M9 3v6.5L4 19a2 2 0 0 0 1.8 3h12.4A2 2 0 0 0 20 19l-5-9.5V3"/><path d="M8 3h8"/></svg>,
    search: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>,
    home: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d="M3 11l9-7 9 7"/><path d="M5.5 9.6V20h13V9.6"/><path d="M10 20v-5.5h4V20"/></svg>,
    plug: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M9 3v6M15 3v6"/><path d="M6 9h12v3a6 6 0 0 1-12 0z"/><path d="M12 18v3"/></svg>,
    shield: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l7 3v6c0 4.2-2.9 7.7-7 9-4.1-1.3-7-4.8-7-9V6z"/></svg>,
    folder: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>,
    pdf: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M8.5 17v-3.2h1.1a1 1 0 0 1 0 2H8.5M13 17v-3.2h.9a1.6 1.6 0 0 1 0 3.2zM17 13.8h1.6M17 15.4h1.2M17 13.8V17"/></svg>,
    panel: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="M9.5 4.5v15"/></svg>,
    sheet: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M8 12h8M8 15h8M8 18h8M11.5 12v6"/></svg>,
  };

  // The SYRIX mark itself for the brand slot and the assistant avatar — this is
  // SYRIX, in a mode. The mode's own glyph belongs on the mode pill and nowhere
  // else.
  const LOGO = '../assets/icons/syrix_app_logo.png';
  const MODE_ICON = 'assets/mode-icons/supercomputer.png';
  // Opened automatically when the workspace mounts, so the browser is there and
  // usable from the first second. Google, in the user's own signed-in profile — the
  // same browser the HUD already has, not a stripped-down stand-in.
  const START_PAGE = 'https://www.google.com/';

  /* What the user typed in the address bar: a URL to visit, or a search.
     Anything with a scheme or a dotted host is an address; everything else is
     a query, which is what every real browser does with that field. */
  function addressToUrl(raw) {
    const s = String(raw || '').trim();
    if (!s) return '';
    if (/^https?:\/\//i.test(s)) return s;
    if (/^[a-z][a-z0-9+.-]*:/i.test(s)) return '';           // mailto:, file:, javascript: — refused
    if (/^localhost(:\d+)?(\/|$)/i.test(s)) return '';       // loopback is blocked anyway
    if (/^[\w-]+(\.[\w-]+)+(:\d+)?(\/|\?|#|$)/.test(s) && !/\s/.test(s)) return 'https://' + s;
    return 'https://www.google.com/search?q=' + encodeURIComponent(s);
  }

  /* ── Top bar ────────────────────────────────────────────────────────── */
  function ScTopBar({ onClose, status }) {
    const tone = (status && status.tone) || 'idle';
    return (
      <header className="sc-topbar">
        <img className="sc-brand-mark" src={LOGO} alt="" draggable="false" />
        <span className="sc-brand-word">SYRIX</span>
        <span className="sc-mode-badge">
          <img className="sc-mode-glyph" src={MODE_ICON} alt="" draggable="false" />
          Supercomputer
        </span>
        <div className="sc-topbar-spacer" />
        <span className={'sc-top-status is-' + tone} aria-live="polite">
          <span className="sc-status-dot" />
          {(status && status.label) || 'Ready'}
        </span>
        <button type="button" className="sc-exit" onClick={onClose}
                title="Leave Supercomputer and return to the HUD">
          <span className="sc-exit-ico">{I.x}</span>
          Exit
        </button>
      </header>
    );
  }

  /* ── Left rail ──────────────────────────────────────────────────────── */
  /* The rail carries the same places the normal HUD rail does, so entering this
     mode does not cost the user access to Connectors, History or Privacy. They open
     the SAME panels the HUD owns — nothing here is a Supercomputer-only
     re-implementation that could drift from the real one. */
  function ScRail({ onClose, onSettings, onPalette, onConnectors }) {
    return (
      <nav className="sc-rail">
        <button type="button" className="sc-rail-btn sc-rail-top" onClick={onClose} title="Back to HUD (Esc)">
          <span className="sc-rail-ico">{I.back}</span>
          <span className="sc-rail-lbl">Back<br />to HUD</span>
        </button>
        <div className="sc-rail-spacer" />
        <button type="button" className="sc-rail-btn" onClick={onConnectors}
                title="Connectors — Gmail and other services">
          <span className="sc-rail-ico">{I.plug}</span>
          <span className="sc-rail-lbl">Connectors</span>
        </button>
        <button type="button" className="sc-rail-btn" onClick={onSettings} title="Supercomputer settings">
          <span className="sc-rail-ico">{I.gear}</span>
          <span className="sc-rail-lbl">Settings</span>
        </button>
        <button type="button" className="sc-rail-btn" onClick={onPalette} title="Command palette (Ctrl+K)">
          <span className="sc-rail-kbd">Ctrl+K</span>
          <span className="sc-rail-lbl">Command<br />Palette</span>
        </button>
      </nav>
    );
  }

  /* ── Permission card ──────────────────────────────────────────────────
     The card IS the message. Answering it does not delete it and post a
     paragraph underneath — it resolves in place, keeping the question, the
     answer and what followed from it in one block, where it was asked.

     Before 2026-07-28 pressing No removed the card and appended a separate
     assistant message; because cards render after every chat line, the reply
     also landed out of order. One decision, three fragments, none of them
     next to the thing they were about. */
  const PERM_OUTCOME = {
    allow: { cls: 'is-allowed', label: 'You said yes', head: 'Allowed' },
    decline: { cls: 'is-declined', label: 'You said no', head: 'Declined' },
    discuss: { cls: 'is-discuss', label: 'You asked to talk it through', head: 'Paused' },
    expired: { cls: 'is-expired', label: 'No answer in time', head: 'Expired' },
  };

  function ScPermissionCard({ req }) {
    const decision = String((req && req.decision) || '');
    const done = !!decision;
    const out = PERM_OUTCOME[decision] || null;
    const detail = (req && req.outcome) || {};
    const decide = (d) =>
      scSend('supercomputer_permission_decision', { request_id: req.request_id, decision: d });
    return (
      <div className={'sc-perm' + (done ? ' is-done ' + (out ? out.cls : '') : '')}
           role="group"
           aria-label={done ? 'Permission request, answered' : 'Permission request'}>
        <div className="sc-perm-head">
          <span className="sc-perm-ico">{done ? (out && out.cls === 'is-allowed' ? I.check : I.shield) : I.shield}</span>
          <span className="sc-perm-title">
            {done ? (out ? out.head : 'Answered') : 'SYRIX wants to do this'}
          </span>
          {done ? <span className="sc-perm-when">{clockOf(req.decided_ts || req.created_ts)}</span> : null}
        </div>
        {/* What it will do, in its own line and in plain words — the thing to
            read before pressing anything. Kept after answering: a decision you
            cannot see the subject of is not a record of anything. */}
        <div className="sc-perm-what">{req.description || req.action}</div>
        {req.why && !done ? <div className="sc-perm-body">{req.why}</div> : null}
        {!done ? (
          <div className="sc-perm-paused">
            Nothing is running. SYRIX is paused here until you answer.
          </div>
        ) : null}
        {req.scope ? (
          <div className="sc-perm-scope"><span className="sc-perm-scope-k">Scope</span> {req.scope}</div>
        ) : null}
        {req.url ? (
          <div className="sc-perm-scope"><span className="sc-perm-scope-k">Address</span> {req.url}</div>
        ) : null}

        {!done ? (
          <div className="sc-perm-actions">
            <button type="button" className="sc-b sc-b-amber" onClick={() => decide('allow')}
                    title="Go ahead">Yes</button>
            <button type="button" className="sc-b" onClick={() => decide('decline')}
                    title="Stop here — I will tell you how to do it yourself">No</button>
            <button type="button" className="sc-b" onClick={() => decide('discuss')}
                    title="Stop and tell me what to do differently">Chat about this</button>
          </div>
        ) : (
          <div className="sc-perm-answer">
            <div className="sc-perm-verdict">
              <span className="sc-perm-verdict-dot" />
              {out ? out.label : 'Answered'}
              {detail.headline ? <span className="sc-perm-verdict-sub">{detail.headline}</span> : null}
            </div>
            {detail.reached ? (
              <div className="sc-perm-line">
                <span className="sc-perm-line-k">Got as far as</span> {detail.reached}
              </div>
            ) : null}
            {Array.isArray(detail.sources) && detail.sources.length ? (
              <div className="sc-perm-line">
                <span className="sc-perm-line-k">Pages read</span> {detail.sources.join(', ')}
              </div>
            ) : null}
            {Array.isArray(detail.files) && detail.files.length ? (
              <div className="sc-perm-line">
                <span className="sc-perm-line-k">Files made</span> {detail.files.join(', ')}
              </div>
            ) : null}
            {detail.guidance ? (
              <div className="sc-perm-guidance">{renderInlineBold(detail.guidance)}</div>
            ) : null}
          </div>
        )}
      </div>
    );
  }

  /* **bold** inside card guidance, without pulling the whole markdown
     renderer into a card that must stay compact. */
  function renderInlineBold(text) {
    const parts = String(text || '').split(/(\*\*[^*]+\*\*)/g);
    return parts.map((p, i) =>
      /^\*\*[^*]+\*\*$/.test(p)
        ? <strong key={'b' + i}>{p.slice(2, -2)}</strong>
        : <React.Fragment key={'t' + i}>{p}</React.Fragment>);
  }

  /* ── File links in the chat ──────────────────────────────────────────────
     A bare "C:\Users\...\workspaces\sct178...\hn_brief.pdf" is not something
     anyone can use. What the user wants is the thing itself: its icon, its real
     name, and a click that opens it. So assistant text is scanned for absolute
     paths to files SYRIX made, and each becomes a chip.

     Only the rendering changes — the backend still reports the full path, which
     is what makes the claim checkable. */
  const FILE_PATH_RE =
    /(?:[A-Za-z]:\\|\\\\)[^\s"'<>|?*\n]+?\.(pdf|docx?|xlsx?|csv|md|txt|html?|pptx?|png|jpe?g|gif|webp|svg|json|zip|py|js|ts|jsx|tsx|css)\b/gi;

  const FILE_KIND = {
    pdf: 'pdf', doc: 'doc', docx: 'doc', md: 'doc', txt: 'doc', rtf: 'doc',
    xls: 'sheet', xlsx: 'sheet', csv: 'sheet',
    png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', svg: 'image',
    html: 'code', htm: 'code', json: 'code', py: 'code', js: 'code', ts: 'code',
    jsx: 'code', tsx: 'code', css: 'code',
  };

  function fileChipIcon(ext) {
    const kind = FILE_KIND[String(ext || '').toLowerCase()] || 'doc';
    if (kind === 'image') return I.img;
    if (kind === 'sheet') return I.sheet;
    if (kind === 'code') return I.code;
    if (kind === 'pdf') return I.pdf;
    return I.file;
  }

  function openMadeFile(path) {
    const n = scNative();
    if (!n || !n.openFile) return;
    n.openFile(path).catch(() => {});
  }

  function ScFileChip({ path }) {
    const name = String(path).split(/[\\/]/).pop() || path;
    const ext = (name.split('.').pop() || '').toLowerCase();
    return (
      <button type="button" className="sc-filechip" title={`Open ${name}\n${path}`}
              onClick={() => openMadeFile(path)}>
        <span className={'sc-filechip-ico is-' + (FILE_KIND[ext] || 'doc')}>
          {fileChipIcon(ext)}
        </span>
        <span className="sc-filechip-name">{name}</span>
      </button>
    );
  }

  /* Split rendered text around file paths, so the prose still goes through the
     shared answer renderer and only the paths become chips. */
  function renderWithFileChips(text) {
    const body = String(text || '');
    FILE_PATH_RE.lastIndex = 0;
    const parts = [];
    let last = 0;
    let m;
    while ((m = FILE_PATH_RE.exec(body)) !== null) {
      if (m.index > last) parts.push({ text: body.slice(last, m.index) });
      parts.push({ file: m[0] });
      last = m.index + m[0].length;
    }
    if (!parts.length) return null;          // nothing to do — normal render
    if (last < body.length) parts.push({ text: body.slice(last) });

    const Renderer = (typeof window !== 'undefined'
      && typeof window.SyrixAnswerRenderer === 'function') ? window.SyrixAnswerRenderer : null;
    return parts.map((p, i) => p.file
      ? <ScFileChip key={'f' + i} path={p.file} />
      : (Renderer
          ? React.createElement(Renderer, { key: 't' + i, text: p.text })
          : <span key={'t' + i}>{p.text}</span>));
  }

  /* ── Chat column ────────────────────────────────────────────────────── */
  /* Voice orb — appears inside the composer while the toggle is on. Its own
     component and its own canvas-free CSS animation; it never touches
     syrix-voice-presence.js or anything normal Voice Mode owns. */
  function ScVoiceOrb({ vstate }) {
    const cls = 'sc-orb is-' + (vstate || 'listening');
    const label = vstate === 'speaking' ? 'Speaking'
      : vstate === 'thinking' ? 'Thinking'
      : 'Listening';
    return (
      <div className="sc-orb-wrap" title={label}>
        <div className={cls}><i /><i /><i /></div>
        <span className="sc-orb-lbl">{label}</span>
      </div>
    );
  }

  /* Supercomputer's own mode switcher. It picks how hard the agent thinks per
     step, and toggles voice — which stays INSIDE this chat column as an orb in
     the composer. It never opens a separate overlay or tab: leaving the
     workspace to talk would defeat the point of a mode built around watching
     the browser. Shares nothing with the normal HUD's model switcher. */
  const DEPTHS = [
    { key: 'spark',  label: 'Spark',  hint: 'Fastest — keeps long browsing loops snappy' },
    { key: 'auto',   label: 'Auto',   hint: 'Balanced. The default' },
    { key: 'zenith', label: 'Zenith', hint: 'Deepest — for analysis, comparison and code' },
  ];

  function ScModeSwitch({ depth, onDepth, voiceOn, onVoiceToggle }) {
    const [open, setOpen] = useState(false);
    const current = DEPTHS.find((d) => d.key === depth) || DEPTHS[1];
    useEffect(() => {
      if (!open) return undefined;
      const off = () => setOpen(false);
      document.addEventListener('mousedown', off);
      return () => document.removeEventListener('mousedown', off);
    }, [open]);
    return (
      <div className="sc-modeswitch" onMouseDown={(e) => e.stopPropagation()}>
        <button type="button" className="sc-modeswitch-btn" onClick={() => setOpen((v) => !v)}
                title="How hard SYRIX thinks on each step">
          <span className="sc-modeswitch-lbl">{current.label}</span>
          <span className="sc-modeswitch-caret">{I.chev}</span>
        </button>
        <button type="button"
                className={'sc-modeswitch-voice' + (voiceOn ? ' is-on' : '')}
                onClick={onVoiceToggle}
                title={voiceOn ? 'Turn voice off' : 'Talk to SYRIX — stays in this chat'}>
          {voiceOn ? I.mic : I.micOff}
          <span>Voice</span>
        </button>
        {open ? (
          <div className="sc-modeswitch-menu" role="menu">
            {DEPTHS.map((d) => (
              <button type="button" key={d.key} role="menuitem"
                      className={'sc-modeswitch-item' + (d.key === depth ? ' is-sel' : '')}
                      onClick={() => { setOpen(false); onDepth(d.key); }}>
                <span className="sc-modeswitch-item-lbl">{d.label}</span>
                <span className="sc-modeswitch-item-hint">{d.hint}</span>
              </button>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  /* How much SYRIX asks before it acts. Three modes, and the label says what
     the current one MEANS rather than what it is called, because that is the
     bit that changes what pressing Send does. */
  const SAFETY_ICON = { ask_always: 'shield', balanced: 'shield', auto: 'play' };

  function ScSafetyPicker({ safety, onSafety }) {
    const [open, setOpen] = useState(false);
    const modes = (safety && safety.modes) || [];
    const current = modes.find((m) => m.key === (safety && safety.mode)) || modes[1] || null;
    useEffect(() => {
      if (!open) return undefined;
      const off = () => setOpen(false);
      document.addEventListener('mousedown', off);
      return () => document.removeEventListener('mousedown', off);
    }, [open]);
    if (!current) return null;
    const key = safety.mode || 'balanced';
    return (
      <div className="sc-safety" onMouseDown={(e) => e.stopPropagation()}>
        <button type="button" className={'sc-safety-btn is-' + key}
                onClick={() => setOpen((v) => !v)}
                title={current.hint}>
          <span className="sc-safety-ico">{I[SAFETY_ICON[key] || 'shield']}</span>
          <span className="sc-safety-lbl">{current.label}</span>
          <span className="sc-modeswitch-caret">{I.chev}</span>
        </button>
        {open ? (
          <div className="sc-safety-menu" role="menu">
            <div className="sc-safety-head">Before SYRIX acts</div>
            {modes.map((m) => (
              <button type="button" key={m.key} role="menuitem"
                      className={'sc-modeswitch-item' + (m.key === key ? ' is-sel' : '')}
                      onClick={() => { setOpen(false); onSafety(m.key); }}>
                <span className="sc-modeswitch-item-lbl">{m.label}</span>
                <span className="sc-modeswitch-item-hint">{m.hint}</span>
              </button>
            ))}
            <div className="sc-safety-foot">
              Passwords, payment details, unsafe files and your local network are
              refused in every mode.
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  function ScChat({ lines, pending, resolved, status, draft, setDraft, onSend, busy, collapsed,
                   onToggle, voice, onVoiceToggle, depth, onDepth, attachments,
                   onAttach, onDropAttachment, task, safety, onSafety,
                   workspaceRoot, onChooseWorkspace, onNewChat }) {
    const feedRef = useRef(null);
    const fileRef = useRef(null);

    /* One ordered conversation: messages and permission cards interleaved by
       when they happened. An unanswered card always sorts last — it is the
       thing being waited on, so it belongs at the bottom whatever its clock
       says. */
    const feed = useMemo(() => {
      const tsOf = (v) => {
        const n = Number(v || 0);
        return n > 1e12 ? n / 1000 : n;
      };
      const items = [];
      (lines || []).forEach((line, i) => {
        items.push({ line, ts: tsOf(line && line.ts), seq: i, open: false });
      });
      (resolved || []).forEach((card, i) => {
        items.push({
          card,
          ts: tsOf(card.decided_ts || card.created_ts),
          seq: 1e6 + i,
          open: false,
        });
      });
      (pending || []).forEach((card, i) => {
        items.push({ card, ts: tsOf(card.created_ts), seq: 2e6 + i, open: true });
      });
      items.sort((a, b) => {
        if (a.open !== b.open) return a.open ? 1 : -1;
        if (a.ts && b.ts && a.ts !== b.ts) return a.ts - b.ts;
        return a.seq - b.seq;
      });
      return items;
    }, [lines, pending, resolved]);

    /* Working state, derived from the task rather than a second progress
       channel that could disagree with the Steps panel. */
    const live = useMemo(() => {
      const steps = (task && task.steps) || [];
      const idx = steps.findIndex((s) => s.status === 'running');
      const state = (task && task.state) || '';
      const running = state === 'running' || state === 'planning';
      return {
        working: running || (busy && !steps.length),
        step: idx >= 0 ? steps[idx] : null,
        index: idx >= 0 ? idx + 1 : 0,
        count: steps.length,
      };
    }, [task, busy]);

    useEffect(() => {
      const el = feedRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }, [feed.length, live.step && live.step.label]);

    if (collapsed) {
      return (
        <div className="sc-chat is-collapsed">
          <button type="button" className="sc-chat-expand" onClick={onToggle} title="Show chat">
            <span className="sc-chat-expand-ico">{I.panel}</span>
            <span className="sc-chat-expand-lbl">Chat</span>
          </button>
        </div>
      );
    }

    return (
      <section className="sc-chat">
        <div className="sc-chat-head">
          <span className="sc-chat-title">
            {lines.length ? 'Conversation' : 'New Conversation'}
          </span>
          <div className="sc-topbar-spacer" />
          {/* Start a fresh conversation without leaving the mode. Deliberately
              KEEPS the browser and its tabs — "new chat" means a different
              subject, not "close the twelve pages I am researching". */}
          <button type="button" className="sc-icon-btn sc-newchat" onClick={onNewChat}
                  title="Start a new chat (keeps your browser tabs)"
                  aria-label="Start a new chat">
            {I.plus}
          </button>
          <ScModeSwitch depth={depth} onDepth={onDepth}
                        voiceOn={!!voice.active} onVoiceToggle={onVoiceToggle} />
          <button type="button" className="sc-icon-btn" onClick={onToggle} title="Collapse chat"
                  aria-label="Collapse chat">
            {I.panel}
          </button>
        </div>

        <div className="sc-feed" ref={feedRef}>
          {feed.map((entry, i) => {
            /* A permission card sits at the point in the conversation where it
               was raised, not permanently at the bottom. Rendering every card
               after every message is what made an answered card appear to
               "move" below replies that came before it. */
            if (entry.card) {
              return <ScPermissionCard key={'p' + entry.card.request_id} req={entry.card} />;
            }
            const ln = entry.line;
            const role = (ln && ln.role) || 'assistant';
            const text = (ln && (ln.text || ln.content)) || '';
            if (!String(text).trim()) return null;
            if (role === 'user') {
              return (
                <div className="sc-msg is-user" key={'m' + i}>
                  <div className="sc-msg-meta"><span className="sc-msg-who">You</span>
                    <span className="sc-msg-time">{clockOf(ln.ts)}</span></div>
                  <div className="sc-msg-bubble">{text}</div>
                </div>
              );
            }
            return (
              <div className="sc-msg is-syrix" key={'m' + i}>
                <div className="sc-msg-meta">
                  <img className="sc-msg-avatar" src={LOGO} alt="" draggable="false" />
                  <span className="sc-msg-who">SYRIX</span>
                  <span className="sc-msg-time">{clockOf(ln.ts)}</span>
                </div>
                {/* The SAME renderer normal chat uses, so an answer is formatted
                    identically in both places — headings, lists, tables, code.
                    Rendering it as plain pre-wrap text here made Supercomputer
                    look like a different, worse assistant. Falls back to plain
                    text only if the shared component is somehow absent. */}
                <div className="sc-msg-text">
                  {renderWithFileChips(text)
                    || (typeof window !== 'undefined'
                        && typeof window.SyrixAnswerRenderer === 'function'
                      ? React.createElement(window.SyrixAnswerRenderer, { text: String(text) })
                      : text)}
                </div>
              </div>
            );
          })}

          {status && status.key === 'waiting' ? (
            <div className="sc-chip is-amber"><span className="sc-chip-ico">{I.clock}</span>Waiting for permission</div>
          ) : null}
          {/* Live working state, in the chat rather than only in the drawer:
              which step it is on and what it is doing right now. A silent
              spinner for two minutes reads as a hang. */}
          {live.working ? (
            <div className="sc-working">
              <span className="sc-dots"><i /><i /><i /></span>
              <div className="sc-working-body">
                <div className="sc-working-step">
                  {live.step ? live.step.label : (status.label || 'Working')}
                  {live.count ? <span className="sc-working-count">
                    step {live.index} of {live.count}</span> : null}
                </div>
                {live.step && live.step.detail
                  ? <div className="sc-working-note">{live.step.detail}</div> : null}
              </div>
            </div>
          ) : null}
        </div>

        <div className={'sc-composer' + (voice.active ? ' is-voice' : '')}
             onDragOver={(e) => { e.preventDefault(); }}
             onDrop={(e) => {
               e.preventDefault();
               const files = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
               if (files.length) onAttach(files);
             }}>
          {voice.active ? <ScVoiceOrb vstate={voice.state} /> : null}

          {attachments.length ? (
            <div className="sc-attachments">
              {attachments.map((a) => (
                <div className={'sc-attach' + (a.kind === 'error' ? ' is-error' : '')}
                     key={a.id} title={a.error ? `${a.name} — ${a.error}` : a.name}>
                  <span className="sc-attach-ico">{a.kind === 'image' ? I.img : I.file}</span>
                  <span className="sc-attach-name">{a.name}</span>
                  {/* Say plainly when a file is not ready yet: sending it while
                      still parsing would look like SYRIX ignored it. */}
                  {a.kind === 'error'
                    ? <span className="sc-attach-state">{a.error}</span>
                    : (!a.ready ? <span className="sc-attach-state">reading…</span> : null)}
                  <button type="button" className="sc-attach-x"
                          onClick={() => onDropAttachment(a.id)} title="Remove">{I.x}</button>
                </div>
              ))}
            </div>
          ) : null}

          <textarea
            className="sc-input"
            value={draft}
            placeholder={voice.active
              ? 'Listening — or type instead…'
              : 'Ask SYRIX to do something. Drop a file, or paste a link.'}
            rows={3}
            onChange={(e) => setDraft(e.target.value)}
            onPaste={(e) => {
              const files = Array.from((e.clipboardData && e.clipboardData.files) || []);
              if (files.length) { e.preventDefault(); onAttach(files); }
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); }
            }}
          />
          <div className="sc-composer-row">
            <input ref={fileRef} type="file" multiple hidden
                   accept="image/*,.pdf,.txt,.md,.csv,.json,.docx,.xlsx"
                   onChange={(e) => {
                     const files = Array.from(e.target.files || []);
                     if (files.length) onAttach(files);
                     e.target.value = '';
                   }} />
            <button type="button" className="sc-icon-btn sc-clip"
                    onClick={() => fileRef.current && fileRef.current.click()}
                    title="Attach an image or document">{I.clip}</button>
            {/* How much SYRIX asks before it acts. Right next to Send, because
                it changes what pressing Send will do. */}
            <ScSafetyPicker safety={safety} onSafety={onSafety} />
            <button type="button" className="sc-icon-btn"
                    onClick={onChooseWorkspace}
                    title={workspaceRoot
                      ? `Working folder: ${workspaceRoot}`
                      : 'Choose where SYRIX saves what it makes'}>
              {I.folder}
            </button>
            <span className="sc-composer-hint">
              {busy ? 'Working…' : 'Enter to send · Shift+Enter for a new line'}
            </span>
            <div className="sc-topbar-spacer" />
            <button type="button" className="sc-send" onClick={onSend}
                    disabled={busy || (!draft.trim() && !attachments.length)} title="Send">
              {I.send}
            </button>
          </div>
        </div>
      </section>
    );
  }

  /* ── Task bar ───────────────────────────────────────────────────────── */
  const TASK_TERMINAL = { done: 1, stopped: 1, failed: 1 };
  /* What the task line should say. A finished task must stop calling itself
     "Current task" — leaving it there is how a completed run looks like a stuck
     one. The goal stays visible (it is useful context for a follow-up), but the
     label tells the truth about its state. */
  function taskLead(state) {
    if (state === 'done') return 'Last task (done):';
    if (state === 'stopped') return 'Last task (stopped):';
    if (state === 'failed') return 'Last task (failed):';
    if (state === 'awaiting_permission') return 'Waiting on you:';
    if (state === 'paused_user' || state === 'paused_discussion') return 'Paused:';
    return 'Current task:';
  }

  function ScTaskBar({ task, status, control, onAnnotate, canAnnotate, onShowSteps }) {
    const state = (task && task.state) || '';
    const steps = (task && task.steps) || [];
    const doneCount = steps.filter((st) => st.status === 'done' || st.status === 'skipped').length;
    const pct = steps.length ? Math.round((doneCount / steps.length) * 100) : 0;
    const running = state === 'running' || state === 'planning';
    const terminal = !!TASK_TERMINAL[state];
    const userDriving = control === 'user';
    const goal = (task && task.goal) || '';
    return (
      <div className="sc-taskbar">
        <div className={'sc-task-line' + (terminal ? ' is-terminal' : '')}>
          <span className="sc-task-k">{taskLead(state)}</span>
          <span className="sc-task-goal" title={goal}>{goal || 'Nothing running yet — ask for something.'}</span>
        </div>
        <div className="sc-task-actions">
          <button type="button" className="sc-b sc-b-danger" disabled={!running && !userDriving}
                  onClick={() => scSend('supercomputer_stop', { reason: 'user_stop' })}
                  title="Cancel browser actions, model output and anything queued">
            <span className="sc-b-ico">{I.stop}</span>Stop
          </button>
          {state === 'paused_user' ? (
            <button type="button" className="sc-b" onClick={() => scSend('supercomputer_resume', {})} title="Resume">
              <span className="sc-b-ico">{I.play}</span>Resume
            </button>
          ) : (
            <button type="button" className="sc-b" disabled={!running}
                    onClick={() => scSend('supercomputer_pause', {})} title="Pause after the current step">
              <span className="sc-b-ico">{I.pause}</span>Pause
            </button>
          )}
          <button type="button" className={'sc-b' + (userDriving ? '' : ' sc-b-amber-text')}
                  disabled={userDriving}
                  onClick={() => scSend('supercomputer_take_control', {})}
                  title="Take the browser yourself — SYRIX stops immediately">
            <span className="sc-b-ico">{I.hand}</span>Take Control
          </button>
          <button type="button" className={'sc-b' + (userDriving ? ' sc-b-amber-text' : '')}
                  disabled={!userDriving}
                  onClick={() => scSend('supercomputer_return_control', {})}
                  title="Hand the browser back — SYRIX resumes from the current page">
            <span className="sc-b-ico">{I.ret}</span>Return Control
          </button>
          <button type="button" className="sc-b" onClick={onAnnotate} disabled={!canAnnotate}
                  title={canAnnotate ? 'Click something on the page and tell SYRIX what to do with it'
                    : 'Open a page first'}>
            <span className="sc-b-ico">{I.pen}</span>Annotate
          </button>
          <div className="sc-topbar-spacer" />
          <button type="button" className={'sc-status is-' + ((status && status.tone) || 'idle')}
                  onClick={onShowSteps} title="Show the steps">
            <span className="sc-status-dot" />
            <span>{steps.length ? doneCount + ' of ' + steps.length + ' steps' : ((status && status.label) || 'Ready')}</span>
            <span className="sc-status-caret">{I.chev}</span>
          </button>
        </div>
        {steps.length ? (
          <div className={'sc-progress' + (running ? ' is-live' : '') + (terminal ? ' is-' + state : '')}
               role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: Math.max(pct, running ? 4 : 0) + '%' }} />
          </div>
        ) : null}
      </div>
    );
  }

  /* ── Browser panel ──────────────────────────────────────────────────── */
  /* The browser chrome, laid out like the browser everyone already knows: a tab
     strip with a new-tab button on the end, then a toolbar of back / forward /
     reload / home and one address bar that also searches. Deliberately familiar
     — this is the pane the user drives by hand when he takes control, so anything
     inventive here is just something to relearn. */
  function ScBrowser({ chrome, viewportRef, onNav, onFocusTab, onCloseTab, onNewTab,
                       onHome, annotating }) {
    const tabs = (chrome && chrome.tabs) || [];
    const [urlDraft, setUrlDraft] = useState('');
    const [editing, setEditing] = useState(false);
    const urlRef = useRef(null);
    useEffect(() => { if (!editing) setUrlDraft((chrome && chrome.url) || ''); },
      [chrome && chrome.url, editing]);

    const loading = !!(chrome && chrome.loading);
    const secure = /^https:/i.test(String((chrome && chrome.url) || ''));

    return (
      <div className="sc-browser">
        <div className="sc-tabs">
          {tabs.map((t) => (
            <div key={t.id} className={'sc-tab' + (t.id === chrome.active ? ' is-active' : '')}
                 onClick={() => onFocusTab(t.id)}
                 onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); onCloseTab(t.id); } }}
                 title={t.url || t.title}>
              {t.favicon
                ? <img className="sc-tab-ico" src={t.favicon} alt="" draggable="false"
                       onError={(e) => { e.target.style.visibility = 'hidden'; }} />
                : <span className="sc-tab-ico sc-tab-ico-fallback">{I.globe}</span>}
              <span className="sc-tab-title">{t.loading ? 'Loading…' : (t.title || 'New tab')}</span>
              <button type="button" className="sc-tab-x"
                      onClick={(e) => { e.stopPropagation(); onCloseTab(t.id); }} title="Close tab">{I.x}</button>
            </div>
          ))}
          <button type="button" className="sc-tab-new" onClick={onNewTab}
                  title="New tab (Ctrl+T)" aria-label="New tab">{I.plus}</button>
        </div>

        <div className="sc-urlbar">
          <button type="button" className="sc-nav-btn" disabled={!chrome.canGoBack}
                  onClick={() => onNav('back')} title="Back (Alt+Left)">{I.arrowL}</button>
          <button type="button" className="sc-nav-btn" disabled={!chrome.canGoForward}
                  onClick={() => onNav('forward')} title="Forward (Alt+Right)">{I.arrowR}</button>
          <button type="button" className="sc-nav-btn"
                  onClick={() => onNav(loading ? 'stop' : 'reload')}
                  title={loading ? 'Stop loading' : 'Reload (Ctrl+R)'}>
            {loading ? I.x : I.reload}
          </button>
          <button type="button" className="sc-nav-btn" onClick={onHome}
                  title="Home">{I.home}</button>
          <div className={'sc-url' + (editing ? ' is-editing' : '')}>
            <span className={'sc-url-lock' + (secure ? ' is-secure' : '')}
                  title={secure ? 'Connection is encrypted' : 'Not encrypted'}>
              {secure ? I.lock : I.globe}
            </span>
            <input
              ref={urlRef}
              className="sc-url-input"
              value={urlDraft}
              placeholder="Search Google or type a URL"
              spellCheck={false}
              onFocus={(e) => { setEditing(true); e.target.select(); }}
              onBlur={() => setEditing(false)}
              onChange={(e) => setUrlDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); onNav('goto', urlDraft); }
                if (e.key === 'Escape') { setUrlDraft((chrome && chrome.url) || ''); e.target.blur(); }
              }}
            />
          </div>
        </div>

        <div className="sc-viewport" ref={viewportRef}>
          {annotating ? (
            <div className="sc-annotate-hint">
              <span className="sc-b-ico">{I.pen}</span>
              Click an element on the page — Esc to cancel
            </div>
          ) : null}
          {!tabs.length ? (
            <div className="sc-viewport-empty">
              <img className="sc-empty-logo" src={LOGO} alt="" draggable="false" />
              <div className="sc-empty-title">No page open</div>
              <div className="sc-empty-body">
                Ask SYRIX to open a site, or press <b>+</b> and type an address.
              </div>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  /* ── Bottom drawer ──────────────────────────────────────────────────── */
  function stepIcon(status) {
    if (status === 'done') return <span className="sc-step-ico is-done">{I.check}</span>;
    if (status === 'running') return <span className="sc-step-ico is-run">{I.circle}</span>;
    if (status === 'blocked') return <span className="sc-step-ico is-blocked">{I.ban}</span>;
    return <span className="sc-step-ico is-pending">{I.circle}</span>;
  }

  function resultIcon(kind) {
    if (kind === 'file') return I.file;
    if (kind === 'screenshot') return I.img;
    if (kind === 'code') return I.code;
    if (kind === 'test') return I.beaker;
    if (kind === 'source') return I.globe;
    return I.search;
  }

  function ScDrawer({ tab, setTab, open, setOpen, height, onResizeStart, task, activity, results, statusKey }) {
    const steps = (task && task.steps) || [];
    const body = () => {
      if (tab === 'steps') {
        if (!steps.length) return <div className="sc-empty-row">No steps yet. Ask SYRIX to do something.</div>;
        return steps.map((s, i) => (
          <div className={'sc-step is-' + (s.status || 'pending')} key={'s' + i}>
            {stepIcon(s.status)}
            <span className="sc-step-lbl">{s.label}</span>
            {/* The running step carries the agent's current reasoning, so this
                panel shows what SYRIX is doing right now rather than only which
                box it is in. */}
            {s.status === 'running' && s.detail
              ? <span className="sc-step-note" title={s.detail}>{s.detail}</span> : null}
            {s.status === 'running' && !s.detail && statusKey === 'reading'
              ? <span className="sc-chip is-amber is-inline">Reading page</span> : null}
            {s.status === 'blocked' && s.detail
              ? <span className="sc-chip is-red is-inline">{s.detail}</span> : null}
            {s.status === 'skipped'
              ? <span className="sc-chip is-inline">Not needed</span> : null}
            <span className="sc-step-time">
              {s.status === 'pending' ? 'Pending' : clockOf(s.ts)}
            </span>
          </div>
        ));
      }
      if (tab === 'activity') {
        if (!activity.length) return <div className="sc-empty-row">Nothing has run yet.</div>;
        return activity.slice().reverse().map((a, i) => (
          <div className={'sc-act is-' + (a.result || '')} key={'a' + i}>
            <span className={'sc-act-dot is-' + (a.result || '')} />
            <span className="sc-act-action">{a.action}</span>
            <span className="sc-act-scope">{a.scope || ''}</span>
            <span className="sc-act-result">{a.result}{a.reason ? ' · ' + a.reason : ''}</span>
            <span className="sc-step-time">{clockOf(a.ts)}</span>
          </div>
        ));
      }
      if (!results.length) return <div className="sc-empty-row">Nothing produced yet.</div>;
      return results.slice().reverse().map((r, i) => {
        // A file SYRIX made can be opened in the browser before the user decides
        // what to do with it. Nothing leaves the task workspace by clicking
        // this — it is a look, not an export.
        const rel = (r.meta && r.meta.relative) || '';
        const openable = r.kind === 'file' && !!rel;
        return (
        <div className={'sc-res' + (openable ? ' is-openable' : '')} key={'r' + i}
             onClick={openable ? () => scSend('supercomputer_open_result', { relative: rel }) : undefined}
             title={openable ? 'Open it in the browser' : (r.location || '')}>
          <span className="sc-res-ico">{resultIcon(r.kind)}</span>
          <span className="sc-res-title">{r.title}</span>
          {r.kind === 'code' && (r.added || r.removed)
            ? <span className="sc-res-diff"><b className="add">+{r.added}</b> <b className="rem">-{r.removed}</b></span> : null}
          {r.kind === 'test'
            ? <span className={'sc-chip is-inline ' + (r.passed ? 'is-green' : 'is-red')}>
                {r.passed ? 'Passed' : 'Failed'}</span> : null}
          <span className="sc-res-loc" title={r.location}>{r.location || r.detail}</span>
          {openable ? <span className="sc-res-open">Open</span> : null}
          <span className="sc-step-time">{clockOf(r.ts)}</span>
        </div>
        );
      });
    };

    return (
      <div className={'sc-drawer' + (open ? '' : ' is-closed')}
           style={{ height: (open ? height : 42) + 'px' }}>
        <div className="sc-drawer-grip" onMouseDown={onResizeStart} title="Drag to resize" />
        <div className="sc-drawer-head">
          {['steps', 'activity', 'results'].map((k) => (
            <button type="button" key={k}
                    className={'sc-drawer-tab' + (tab === k && open ? ' is-active' : '')}
                    onClick={() => { setTab(k); setOpen(true); }}>
              {k[0].toUpperCase() + k.slice(1)}
            </button>
          ))}
          <div className="sc-topbar-spacer" />
          <button type="button" className="sc-drawer-toggle" onClick={() => setOpen(!open)}>
            {open ? 'Hide' : 'Show'}
            <span className={'sc-drawer-chev' + (open ? '' : ' is-up')}>{I.chev}</span>
          </button>
        </div>
        {open ? <div className="sc-drawer-body" key={tab}>{body()}</div> : null}
      </div>
    );
  }

  /* ── Command palette ────────────────────────────────────────────────── */
  function ScPalette({ open, onClose, commands }) {
    const [q, setQ] = useState('');
    const [sel, setSel] = useState(0);
    const inputRef = useRef(null);
    const filtered = useMemo(() => {
      const s = q.trim().toLowerCase();
      return commands.filter((c) => !s || c.label.toLowerCase().includes(s)
        || (c.hint || '').toLowerCase().includes(s));
    }, [q, commands]);

    useEffect(() => { if (open) { setQ(''); setSel(0); setTimeout(() => inputRef.current && inputRef.current.focus(), 10); } }, [open]);
    useEffect(() => { setSel(0); }, [q]);
    if (!open) return null;

    const run = (c) => { onClose(); if (c && c.run) c.run(); };
    return (
      <div className="sc-palette-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
        <div className="sc-palette" role="dialog" aria-label="Command palette">
          <div className="sc-palette-search">
            <span className="sc-palette-ico">{I.search}</span>
            <input ref={inputRef} className="sc-palette-input" value={q} placeholder="Type a command..."
                   onChange={(e) => setQ(e.target.value)}
                   onKeyDown={(e) => {
                     if (e.key === 'Escape') { e.preventDefault(); onClose(); }
                     if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, filtered.length - 1)); }
                     if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
                     if (e.key === 'Enter') { e.preventDefault(); run(filtered[sel]); }
                   }} />
          </div>
          <div className="sc-palette-list">
            {filtered.length ? filtered.map((c, i) => (
              <button type="button" key={c.id}
                      className={'sc-palette-item' + (i === sel ? ' is-sel' : '')}
                      onMouseEnter={() => setSel(i)} onClick={() => run(c)}>
                <span className="sc-palette-lbl">{c.label}</span>
                {c.hint ? <span className="sc-palette-hint">{c.hint}</span> : null}
              </button>
            )) : <div className="sc-empty-row">No matching command.</div>}
          </div>
        </div>
      </div>
    );
  }

  /* ── Annotate prompt ────────────────────────────────────────────────── */
  function ScAnnotatePrompt({ picked, onCancel, onSubmit }) {
    const [note, setNote] = useState('');
    const ref = useRef(null);
    useEffect(() => { setTimeout(() => ref.current && ref.current.focus(), 10); }, []);
    if (!picked) return null;
    return (
      <div className="sc-palette-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
        <div className="sc-annotate" role="dialog" aria-label="Annotate element">
          <div className="sc-annotate-head">Tell SYRIX what to do with this</div>
          <div className="sc-annotate-target">
            <span className="sc-annotate-tag">{picked.tag}</span>
            <span className="sc-annotate-text">{picked.label || picked.text || '(no text)'}</span>
          </div>
          <textarea ref={ref} className="sc-annotate-input" rows={3} value={note}
                    placeholder="e.g. extract the figures in this table"
                    onChange={(e) => setNote(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSubmit(note); }
                      if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
                    }} />
          <div className="sc-annotate-actions">
            <button type="button" className="sc-b sc-b-amber" onClick={() => onSubmit(note)}
                    disabled={!note.trim()}>Send to SYRIX</button>
            <button type="button" className="sc-b" onClick={onCancel}>Cancel</button>
          </div>
        </div>
      </div>
    );
  }

  /* ── Page ───────────────────────────────────────────────────────────── */
  function SyrixSupercomputerWorkspace({ onClose, onOpenSettings, resumeSessionId }) {
    const openSettings = useCallback(() => {
      try { sessionStorage.setItem('sx-settings-section', 'super'); } catch (_) {}
      try { onOpenSettings && onOpenSettings(); } catch (_) {}
    }, [onOpenSettings]);
    const snap = useScSnapshot();
    const state = useScState(snap);
    const task = state.task || {};
    const status = state.status || {};
    const control = (state.browser && state.browser.control) || 'closed';
    const pending = Array.isArray(state.pending_permission) ? state.pending_permission : [];
    const resolved = Array.isArray(state.resolved_permission) ? state.resolved_permission : [];
    const activity = Array.isArray(state.activity) ? state.activity : [];
    const results = Array.isArray(state.results) ? state.results : [];

    const [chatW, setChatW] = useState(() => readNum(LS_SPLIT, CHAT_DEFAULT, CHAT_MIN, CHAT_MAX));
    const [chatCollapsed, setChatCollapsed] = useState(false);
    const [drawerH, setDrawerH] = useState(() => readNum(LS_DRAWER, DRAWER_DEFAULT, DRAWER_MIN, DRAWER_MAX));
    const [drawerOpen, setDrawerOpen] = useState(true);
    const [drawerTab, setDrawerTab] = useState(() => {
      try { return localStorage.getItem(LS_DRAWER_TAB) || 'steps'; } catch (_) { return 'steps'; }
    });
    const [draft, setDraft] = useState('');
    const [busy, setBusy] = useState(false);
    const [attachments, setAttachments] = useState([]);
    const [paletteOpen, setPaletteOpen] = useState(false);
    const [picked, setPicked] = useState(null);
    const [chrome, setChrome] = useState({ tabs: [], active: '', url: '', canGoBack: false, canGoForward: false, annotating: false });

    const viewportRef = useRef(null);
    const rootRef = useRef(null);
    const watermarkRef = useRef(null);

    /* Session lifecycle. Exiting the mode is what tears the browser, models and
       permissions down, so it must happen even on an abrupt unmount. */
    useEffect(() => {
      // Full page, not an overlay: suppress the HUD chrome that is pinned
      // above this layer, and restore it on the way out.
      try { document.body.classList.add('body--supercomputer'); } catch (_) {}
      // The ONE enter. Carries the chat to reopen when the user came from history,
      // so the backend restores that conversation, its agent memory and its
      // tabs instead of starting a blank session.
      const resuming = String(resumeSessionId || '');
      scSend('supercomputer_enter', resuming ? { resume_session_id: resuming } : {});

      const native = scNative();
      if (native) {
        // Bring the browser up immediately. An agentic workspace with an empty
        // pane looks broken, and the user should be able to just start browsing.
        // On a resume the backend reopens the saved tabs, so DON'T also drop a
        // blank Google in front of them.
        native.ensure()
          .then(() => native.tabs())
          .then((r) => {
            const existing = (r && r.ok && r.data && r.data.tabs) || [];
            if (!existing.length && !resuming) {
              return native.open({ url: START_PAGE, new_tab: true });
            }
            return null;
          })
          .catch(() => {});
      }

      return () => {
        try { document.body.classList.remove('body--supercomputer'); } catch (_) {}
        try { const n = scNative(); if (n) { n.setBounds(null, true); n.dispose(); } } catch (_) {}
        scSend('supercomputer_exit', { reason: 'workspace_unmounted' });
      };
    }, []);

    /* Reopen the pages a restored conversation was last on.
       Done HERE, not in the backend: every Python-side navigation goes through
       the task-scope policy, which refuses when there is no TaskContract — and
       a resume happens outside any task, so restoring from there was refused
       every time. It is also the wrong actor. The user picked this conversation, so
       these are his tabs, opened exactly like the start page above. */
    const restoredTabsRef = useRef('');
    useEffect(() => {
      const urls = (state && state.restore_tabs) || [];
      if (!Array.isArray(urls) || !urls.length) return;
      const key = urls.join('|');
      if (restoredTabsRef.current === key) return;
      restoredTabsRef.current = key;
      const native = scNative();
      if (!native) return;
      native.ensure()
        .then(() => urls.reduce(
          (chain, url) => chain.then(() => native.open({ url, new_tab: true }).catch(() => {})),
          Promise.resolve()))
        .catch(() => {});
    }, [state.restore_tabs]);

    /* Reopening a chat while the workspace is ALREADY open.
       The mount effect above only runs once, so picking a second conversation
       out of history without leaving the mode would otherwise do nothing at
       all. Remounting is not the answer — the unmount tears down the
       BrowserViews — so the resume is sent on the prop change instead. */
    const mountedResume = useRef(String(resumeSessionId || ''));
    useEffect(() => {
      const next = String(resumeSessionId || '');
      if (!next || next === mountedResume.current) return;
      mountedResume.current = next;
      try {
        if (typeof window !== 'undefined' && window.__syrixScWatermark) {
          window.__syrixScWatermark.id = null;
        }
      } catch (_) {}
      setDraft('');
      setAttachments([]);
      scSend('supercomputer_enter', { resume_session_id: next });
    }, [resumeSessionId]);

    /* Browser chrome comes straight from Electron so tab clicks feel instant. */
    useEffect(() => {
      const native = scNative();
      if (!native || !native.onState) return undefined;
      const off = native.onState(setChrome);
      native.tabs().then((r) => { if (r && r.ok) setChrome(r.data); }).catch(() => {});
      return off;
    }, []);

    /* Supercomputer's conversation is its own thread. The backend tags every
       line written while this mode is live with channel:'supercomputer' (the
       same chokepoint Voice Mode uses), so filtering on that tag is what keeps
       the two apart — in BOTH directions, and across a restart.

       THE WATERMARK IS KEYED ON THE BACKEND SESSION, NOT ON MOUNT (2026-07-26).
       It used to be a useRef initialised to the line count at mount, and since
       leaving the mode UNMOUNTS this component, coming back always stamped a
       fresh watermark and sliced the whole conversation away. That was intended
       once — "a fresh conversation rather than replaying the last one" — but it
       is the reset the user kept hitting: step out for ten seconds, come back, and
       everything you had just been discussing is gone.

       Now the watermark lives on `window` and is re-stamped only when the
       backend hands us a genuinely different session_id — which happens on a
       real new session and when the New chat button asks for one. Same session,
       same conversation. */
    const allLines = useMemo(() => {
      const raw = (snap && (snap.chat_lines || (snap.raw && snap.raw.chat_lines))) || [];
      return (Array.isArray(raw) ? raw : [])
        .filter((m) => String((m && m.channel) || '') === 'supercomputer');
    }, [snap]);

    const sessionId = String((state && state.session_id) || '');
    if (typeof window !== 'undefined') {
      const mark = window.__syrixScWatermark || (window.__syrixScWatermark = { id: null, at: 0 });
      if (mark.id !== sessionId) {
        // A new session starts where the transcript currently ends, so lines
        // from a previous one never bleed into it.
        mark.id = sessionId;
        mark.at = allLines.length;
      }
      watermarkRef.current = mark.at;
    }

    /* A conversation reopened from history.
       It CANNOT arrive through chat_lines: `chat_load_session` publishes the
       loaded transcript to `syrix_loaded_session` for the normal chat panel and
       never rewrites the live chat buffer. So a reopened Supercomputer chat had
       literally nothing to render, which is what "it opens a new chat instead
       of taking me back" actually was. The backend hands it over in the mode's
       own snapshot instead, and it sits above the live lines. */
    const restored = useMemo(() => {
      const raw = (state && state.chat_restored) || [];
      return Array.isArray(raw) ? raw : [];
    }, [state.chat_restored]);

    const lines = useMemo(
      () => restored.concat(allLines.slice(watermarkRef.current || 0)),
      [restored, allLines, watermarkRef.current]);

    /* A BrowserView is a NATIVE layer: it always paints above the HTML, whatever
       the z-index says. So any HTML overlay — the command palette, the annotate
       prompt, settings — has to park the view or it renders underneath and the
       page looks like it is "glitching over everything". This is the single
       source of truth for "is something covering the browser right now". */
    const [foreignOverlay, setForeignOverlay] = useState(false);
    const [connectorsOpen, setConnectorsOpen] = useState(false);
    const overlayOpen = paletteOpen || !!picked || foreignOverlay || connectorsOpen;

    /* Position the native BrowserView over the viewport box. Runs on every
       geometry change: mount, resize, divider drag, drawer resize, collapse,
       and every time an overlay opens or closes. */
    const syncBounds = useCallback(() => {
      const native = scNative();
      const el = viewportRef.current;
      if (!native) return;
      if (!el) { native.setBounds(null, true); return; }
      const r = el.getBoundingClientRect();
      const hidden = overlayOpen || r.width < 40 || r.height < 40;
      native.setBounds({ x: r.left, y: r.top, width: r.width, height: r.height }, hidden);
    }, [overlayOpen]);

    useEffect(() => { syncBounds(); },
      [syncBounds, chatW, chatCollapsed, drawerH, drawerOpen, chrome.tabs.length, overlayOpen]);
    useEffect(() => {
      let raf = 0;
      const until = performance.now() + 420;
      const step = () => { syncBounds(); if (performance.now() < until) raf = requestAnimationFrame(step); };
      raf = requestAnimationFrame(step);
      return () => cancelAnimationFrame(raf);
    }, [chatCollapsed, drawerOpen]);
    useEffect(() => {
      const on = () => syncBounds();
      window.addEventListener('resize', on);
      const id = setInterval(syncBounds, 1000);  // catches HUD chrome shifts
      return () => { window.removeEventListener('resize', on); clearInterval(id); };
    }, [syncBounds]);

    /* Overlays owned by OTHER components (HUD settings, widget menus) mount
       outside this tree, so there is no prop to read. Watching the DOM for them
       is immediate and needs no plumbing through two components — and it is
       what "the browser overlaps everything" actually was. */
    useEffect(() => {
      const SELECTOR = '.sx-set, .p-widget-menu, .p-modal-scrim, .eg-modal-scrim';
      const check = () => setForeignOverlay(!!document.querySelector(SELECTOR));
      check();
      const mo = new MutationObserver(check);
      mo.observe(document.body, { childList: true, subtree: true });
      return () => mo.disconnect();
    }, []);

    /* Divider drag — persisted so the split survives leaving the mode. */
    const startSplit = useCallback((e) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = chatW;
      const move = (ev) => {
        const next = Math.max(CHAT_MIN, Math.min(CHAT_MAX, startW + (ev.clientX - startX)));
        setChatW(next);
      };
      const up = () => {
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        document.body.classList.remove('sc-dragging');
        try { localStorage.setItem(LS_SPLIT, String(Math.round(chatWRef.current))); } catch (_) {}
      };
      document.body.classList.add('sc-dragging');
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    }, [chatW]);
    const chatWRef = useRef(chatW);
    useEffect(() => { chatWRef.current = chatW; }, [chatW]);

    /* Drawer resize — also persisted. */
    const startDrawer = useCallback((e) => {
      e.preventDefault();
      const startY = e.clientY;
      const startH = drawerH;
      const move = (ev) => {
        const next = Math.max(DRAWER_MIN, Math.min(DRAWER_MAX, startH - (ev.clientY - startY)));
        setDrawerH(next);
      };
      const up = () => {
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        document.body.classList.remove('sc-dragging-v');
        try { localStorage.setItem(LS_DRAWER, String(Math.round(drawerHRef.current))); } catch (_) {}
      };
      document.body.classList.add('sc-dragging-v');
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    }, [drawerH]);
    const drawerHRef = useRef(drawerH);
    useEffect(() => { drawerHRef.current = drawerH; }, [drawerH]);

    useEffect(() => { try { localStorage.setItem(LS_DRAWER_TAB, drawerTab); } catch (_) {} }, [drawerTab]);

    /* Attachments.
       Images ride the turn directly as base64, which is what the vision path
       expects. Everything else goes through the SAME Document Intelligence
       upload the normal HUD uses — `upload_file`, then the backend parses it
       and reports a doc_id in hud_state.syrix_docs, and the turn is grounded on
       that id. Reusing that pipeline rather than inventing a parallel one is
       the point: a PDF read in Supercomputer is parsed by the same code that
       reads it anywhere else, so there is no second implementation to drift.

       Bytes come only from a file the user explicitly picked — the backend is never
       handed a path it would then open on its own. */
    const MAX_ATTACH_BYTES = 8 * 1024 * 1024;
    const onAttach = useCallback((files) => {
      files.slice(0, 4).forEach((file) => {
        const id = 'a' + Math.random().toString(36).slice(2, 8);
        const isImage = /^image\//.test(file.type || '');
        if (file.size > MAX_ATTACH_BYTES) {
          setAttachments((prev) => prev.concat([{
            id, name: file.name, kind: 'error', error: 'over the 8 MB limit',
          }]));
          return;
        }
        const reader = new FileReader();
        reader.onerror = () => setAttachments((prev) => prev.map((a) =>
          a.id === id ? { ...a, kind: 'error', error: 'could not be read' } : a));
        reader.onload = () => {
          const raw = String(reader.result || '');
          if (isImage) {
            setAttachments((prev) => prev.concat([{
              id, name: file.name, mime: file.type, kind: 'image',
              b64: raw.slice(raw.indexOf(',') + 1), ready: true,
            }]));
            return;
          }
          setAttachments((prev) => prev.concat([{
            id, name: file.name, mime: file.type || '', kind: 'file',
            upload_id: id, doc_id: '', ready: false,
          }]));
          try {
            window.SyrixBridge?.send?.('upload_file', { data: {
              name: file.name, size: file.size, type: file.type || '',
              mime: file.type || '', upload_id: id, payload_b64: raw,
            } });
          } catch (_) {
            setAttachments((prev) => prev.map((a) =>
              a.id === id ? { ...a, kind: 'error', error: 'upload failed' } : a));
          }
        };
        reader.readAsDataURL(file);
      });
    }, []);
    const onDropAttachment = useCallback((id) => {
      setAttachments((prev) => prev.filter((a) => a.id !== id));
    }, []);

    /* Pick up the doc_id the backend assigns once it has parsed an upload. */
    useEffect(() => {
      const docs = (snap && snap.raw && Array.isArray(snap.raw.syrix_docs))
        ? snap.raw.syrix_docs : null;
      if (!docs || !docs.length) return;
      const byUpload = {};
      docs.forEach((d) => { const k = String(d.upload_id || ''); if (k) byUpload[k] = d; });
      setAttachments((prev) => {
        let changed = false;
        const next = prev.map((a) => {
          const b = byUpload[a.upload_id];
          if (!b) return a;
          const doc_id = b.doc_id || a.doc_id;
          const failed = String(b.status || '') === 'failed';
          if (doc_id === a.doc_id && !failed) return a;
          changed = true;
          return failed
            ? { ...a, kind: 'error', error: b.error || 'could not be read' }
            : { ...a, doc_id, ready: !!doc_id };
        });
        return changed ? next : prev;
      });
    }, [snap && snap.raw && snap.raw.syrix_docs]);

    /* Send a turn. model:'supercomputer' is produced only here. */
    const send = useCallback((textOverride) => {
      const text = String(textOverride != null ? textOverride : draft).trim();
      const files = attachments.filter((a) => a.kind !== 'error');
      if ((!text && !files.length) || busy) return;
      setBusy(true);
      if (textOverride == null) { setDraft(''); setAttachments([]); }
      try {
        const images = files.filter((a) => a.kind === 'image')
          .map((a) => ({ b64: a.b64, mime: a.mime, name: a.name }));
        // Only ids the backend has actually finished parsing. Sending one it is
        // still working on would ground the turn on nothing and read as SYRIX
        // ignoring the file.
        const docIds = files.filter((a) => a.kind === 'file' && a.doc_id)
          .map((a) => a.doc_id);
        const p = window.SyrixBridge && window.SyrixBridge.chat
          ? window.SyrixBridge.chat({
              text: text || 'Have a look at this.',
              mode: 'chat', model: 'supercomputer',
              images, docIds,
            })
          : null;
        if (p && typeof p.then === 'function') p.then(() => setBusy(false), () => setBusy(false));
        else setBusy(false);
      } catch (_) { setBusy(false); }
    }, [draft, busy, attachments]);

    /* Voice toggle. Entirely Supercomputer's own session — it never touches
       normal Voice Mode, which stays exactly as it was. */
    const voiceState = state.voice || { active: false, state: 'off' };
    const toggleVoice = useCallback(() => {
      scSend(voiceState.active ? 'supercomputer_voice_off' : 'supercomputer_voice_on', {});
    }, [voiceState.active]);

    /* Reasoning depth — Supercomputer's own switcher, not the HUD's. */
    const depth = (state.models && state.models.depth) || 'auto';
    const setDepth = useCallback((next) => {
      scSend('supercomputer_set_depth', { depth: next });
    }, []);

    /* How much it asks before acting, and where it saves what it makes. */
    const safety = state.safety || { mode: 'balanced', modes: [] };
    const onSafety = useCallback((next) => {
      scSend('supercomputer_set_safety_mode', { mode: next });
    }, []);
    const workspaceRoot = state.workspace_root || '';
    /* Sound on a state change, not on a poll.
       A task runs for minutes and the whole point is that the user can look away
       while it does — so it has to be able to say "done" without him watching.
       Fired on the TRANSITION into a terminal state (and on a card appearing),
       never on every snapshot, or a finished task would chime ~6 times a second
       for as long as it sat there. */
    const prevTaskState = useRef('');
    const prevPendingCount = useRef(0);
    useEffect(() => {
      const sfx = (typeof window !== 'undefined' && window.SyrixSound) || null;
      const enabled = state.sounds_enabled !== false;
      const now = String(task.state || '');
      const was = prevTaskState.current;
      prevTaskState.current = now;
      if (!sfx || !enabled || !was || was === now) return;
      try {
        if (now === 'done') sfx.taskComplete && sfx.taskComplete();
        else if (now === 'failed' || now === 'stopped') sfx.taskFailed && sfx.taskFailed();
      } catch (_) {}
    }, [task.state, state.sounds_enabled]);

    useEffect(() => {
      const sfx = (typeof window !== 'undefined' && window.SyrixSound) || null;
      const enabled = state.sounds_enabled !== false;
      const count = pending.length;
      const was = prevPendingCount.current;
      prevPendingCount.current = count;
      if (!sfx || !enabled) return;
      // Only when a NEW card appears, not when one is answered.
      if (count > was) { try { sfx.taskNeedsYou && sfx.taskNeedsYou(); } catch (_) {} }
    }, [pending.length, state.sounds_enabled]);

    /* New chat: a fresh conversation, same browser.
       The backend rotates the saved chat session (so this one is filed on its
       own in history), clears the agent's memory and the task panels, and
       deliberately leaves every tab open. The local watermark is reset here so
       the feed clears immediately rather than after the next state push. */
    const onNewChat = useCallback(() => {
      try {
        if (typeof window !== 'undefined' && window.__syrixScWatermark) {
          window.__syrixScWatermark.id = null;
        }
      } catch (_) {}
      setDraft('');
      setAttachments([]);
      scSend('supercomputer_new_chat', {});
    }, []);

    const onChooseWorkspace = useCallback(() => {
      // Electron owns the folder picker; the backend validates whatever comes
      // back before anything is written into it.
      const n = scNative();
      if (!n || !n.chooseFolder) return;
      n.chooseFolder().then((r) => {
        if (r && r.ok && r.path) scSend('supercomputer_choose_workspace', { path: r.path });
      }).catch(() => {});
    }, []);

    /* Browser chrome actions go straight to Electron — they are the user's own
       navigation, not agent actions, so they do not pass the agent gate. */
    const onNav = useCallback((action, url) => {
      const n = scNative();
      if (!n) return;
      if (action === 'goto') {
        const target = addressToUrl(url);
        if (!target) return;
        // No tab yet (fresh session, or the last one was closed) — nav has
        // nothing to act on, so open one rather than silently doing nothing.
        if (!chrome.active) { n.open({ url: target, new_tab: true }).catch(() => {}); return; }
        n.nav(chrome.active, 'goto', target).catch(() => {});
        return;
      }
      n.nav(chrome.active, action, url || '').catch(() => {});
    }, [chrome.active]);
    const onFocusTab = useCallback((id) => { const n = scNative(); if (n) n.focus(id).catch(() => {}); }, []);
    const onCloseTab = useCallback((id) => { const n = scNative(); if (n) n.close(id).catch(() => {}); }, []);
    // A blank new tab is a dead end in an agentic browser; land on the start
    // page so the tab is immediately usable by the user and readable by SYRIX.
    const onNewTab = useCallback(() => {
      const n = scNative();
      if (n) n.open({ url: START_PAGE, new_tab: true }).catch(() => {});
    }, []);
    const onHome = useCallback(() => {
      const n = scNative();
      if (!n) return;
      if (!chrome.active) { n.open({ url: START_PAGE, new_tab: true }).catch(() => {}); return; }
      n.nav(chrome.active, 'goto', START_PAGE).catch(() => {});
    }, [chrome.active]);

    /* Annotate: pick an element, then attach an instruction and send it. */
    const onAnnotate = useCallback(() => {
      const n = scNative();
      if (!n) return;
      n.annotate(chrome.active).then((r) => {
        if (r && r.ok) setPicked(r.data);
      }).catch(() => {});
    }, [chrome.active]);

    const submitAnnotation = useCallback((note) => {
      const p = picked;
      setPicked(null);
      if (!p || !String(note || '').trim()) return;
      const target = p.label || p.text || p.tag || 'the element';
      send(`Regarding the ${p.tag} "${String(target).slice(0, 80)}" on ${p.url}: ${note}`);
    }, [picked, send]);

    /* Esc dismisses whatever is on top — and NOTHING else.
     *
     * It used to close the whole workspace, which is wrong for a mode you work
     * inside: Escape is the key you hit to get out of a text field or a
     * dialog, and here it threw away the window, the tabs and the view of a
     * running task. Nobody expects that, and there is already a ✕ and the mode
     * switcher for leaving deliberately. */
    useEffect(() => {
      const onKey = (e) => {
        if (e.key === 'Escape') {
          if (paletteOpen) { setPaletteOpen(false); return; }
          if (picked) { setPicked(null); return; }
          return;
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
          e.preventDefault();
          setPaletteOpen((v) => !v);
        }
        // The browser shortcuts everyone already has muscle memory for. The
        // native BrowserView never sees these — it is a separate webContents —
        // so they have to be handled here or they simply do nothing.
        if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 't') {
          e.preventDefault(); onNewTab();
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'r') {
          e.preventDefault(); onNav('reload');
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'w') {
          e.preventDefault(); if (chrome.active) onCloseTab(chrome.active);
        }
        if (e.altKey && e.key === 'ArrowLeft') { e.preventDefault(); onNav('back'); }
        if (e.altKey && e.key === 'ArrowRight') { e.preventDefault(); onNav('forward'); }
      };
      document.addEventListener('keydown', onKey);
      return () => document.removeEventListener('keydown', onKey);
    }, [onClose, paletteOpen, picked, onNewTab, onNav, onCloseTab, chrome.active]);

    const commands = useMemo(() => [
      { id: 'stop', label: 'Stop the current task', hint: 'cancels browser, model and queued work',
        run: () => scSend('supercomputer_stop', { reason: 'palette' }) },
      { id: 'pause', label: 'Pause after this step', run: () => scSend('supercomputer_pause', {}) },
      { id: 'resume', label: 'Resume the task', run: () => scSend('supercomputer_resume', {}) },
      { id: 'take', label: 'Take control of the browser', run: () => scSend('supercomputer_take_control', {}) },
      { id: 'return', label: 'Return control to SYRIX', run: () => scSend('supercomputer_return_control', {}) },
      { id: 'annotate', label: 'Annotate an element', hint: 'click something on the page', run: onAnnotate },
      { id: 'newtab', label: 'New browser tab', run: onNewTab },
      { id: 'reload', label: 'Reload the page', run: () => onNav('reload') },
      { id: 'steps', label: 'Show Steps', run: () => { setDrawerTab('steps'); setDrawerOpen(true); } },
      { id: 'activity', label: 'Show Activity', run: () => { setDrawerTab('activity'); setDrawerOpen(true); } },
      { id: 'results', label: 'Show Results', run: () => { setDrawerTab('results'); setDrawerOpen(true); } },
      { id: 'drawer', label: 'Toggle the bottom drawer', run: () => setDrawerOpen((v) => !v) },
      { id: 'chat', label: 'Collapse / expand chat', run: () => setChatCollapsed((v) => !v) },
      { id: 'clear', label: 'Clear Supercomputer temporary data',
        hint: 'browser snapshots, page text and caches', run: () => scSend('supercomputer_clear_data', {}) },
      { id: 'reset', label: 'Reset Supercomputer permissions',
        hint: 'revokes every grant in this session', run: () => scSend('supercomputer_reset_permissions', {}) },
      { id: 'settings', label: 'Open Supercomputer settings', run: () => openSettings() },
      { id: 'leave', label: 'Leave Supercomputer', run: () => onClose && onClose() },
    ], [onAnnotate, onNewTab, onNav, onClose, openSettings]);

    return (
      <div className="sc-root" ref={rootRef} role="dialog" aria-label="SYRIX Supercomputer">
        <ScTopBar onClose={onClose} status={status} />
        <div className="sc-body">
          <ScRail onClose={onClose} onSettings={openSettings}
                  onPalette={() => setPaletteOpen(true)}
                  onConnectors={() => setConnectorsOpen(true)} />

          <div className="sc-chat-col" style={{ width: chatCollapsed ? 46 : chatW + 'px' }}>
            <ScChat lines={lines} pending={pending} resolved={resolved}
                    status={status} draft={draft} setDraft={setDraft}
                    onSend={send} busy={busy} collapsed={chatCollapsed}
                    onToggle={() => setChatCollapsed((v) => !v)}
                    voice={voiceState} onVoiceToggle={toggleVoice}
                    depth={depth} onDepth={setDepth} task={task}
                    safety={safety} onSafety={onSafety}
                    workspaceRoot={workspaceRoot} onChooseWorkspace={onChooseWorkspace}
                    attachments={attachments} onAttach={onAttach}
                    onNewChat={onNewChat}
                    onDropAttachment={onDropAttachment} />
          </div>

          <div className="sc-split" onMouseDown={startSplit} title="Drag to resize" />

          <div className="sc-work">
            <ScTaskBar task={task} status={status} control={control} onAnnotate={onAnnotate}
                       canAnnotate={!!chrome.active}
                       onShowSteps={() => { setDrawerTab('steps'); setDrawerOpen(true); }} />
            <ScBrowser chrome={chrome} viewportRef={viewportRef} onNav={onNav}
                       onFocusTab={onFocusTab} onCloseTab={onCloseTab} onNewTab={onNewTab}
                       onHome={onHome} annotating={!!chrome.annotating} />
            <ScDrawer tab={drawerTab} setTab={setDrawerTab} open={drawerOpen} setOpen={setDrawerOpen}
                      height={drawerH} onResizeStart={startDrawer} task={task}
                      activity={activity} results={results} statusKey={status.key} />
          </div>
        </div>

        {/* The HUD's own Connectors page, rendered inside this mode rather than
            re-implemented — so what the user connects here is what SYRIX uses
            everywhere, and there is no second copy to drift. */}
        {connectorsOpen && typeof window !== 'undefined'
          && typeof window.SyrixConnectorsPanel === 'function' ? (
          <div className="sc-panel-host">
            {React.createElement(window.SyrixConnectorsPanel,
              { onClose: () => setConnectorsOpen(false) })}
          </div>
        ) : null}

        <ScPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />
        <ScAnnotatePrompt picked={picked} onCancel={() => setPicked(null)} onSubmit={submitAnnotation} />
      </div>
    );
  }

  /* ── Styles ─────────────────────────────────────────────────────────── */
  const CSS = `
/* z-index must clear EVERY piece of HUD chrome or the shell underneath stays
   clickable and paints through: rail 8000, bars 8500, rail-flyout 9500,
   connectors 9550, voice overlay 9600, model menu 9800, island 9900/9901,
   toast stack 10000. 10050 clears all of them and stays below the boot
   cinematic and emergency overlays (99999), which must always win.
   _check_sc_workspace.js verifies this against the real stylesheets, because
   this shipped once at 900 and the entire page was unclickable.
   The body--supercomputer rules below remove the pieces pinned with
   !important, which a higher z-index alone cannot beat.

   Every colour is a palette token (aurora-glass.css), so the workspace is
   champagne glass in Aura and true black and grey in Dark, like the rest of
   SYRIX. The one hex is the opaque fallback the check above requires. */
.sc-root{position:fixed;inset:0;z-index:10050;display:flex;flex-direction:column;
  background:#080604;background:var(--bg,#080604);color:var(--eg-text-1);overflow:hidden;isolation:isolate;
  font-family:'Inter','Segoe UI',system-ui,sans-serif;font-size:13px;-webkit-font-smoothing:antialiased;
  animation:scIn .38s var(--eg-spring) both}
.sc-root::before{content:'';position:absolute;inset:0;pointer-events:none;z-index:-1;
  background:radial-gradient(ellipse 70% 40% at 60% -10%,rgba(var(--eg-accent-surface-rgb),.08),transparent 70%)}
body.pal-dark .sc-root::before{display:none}
@keyframes scIn{from{opacity:0;transform:scale(.985)}to{opacity:1;transform:none}}
@keyframes scRise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes scPop{from{opacity:0;transform:translateY(-4px) scale(.97)}to{opacity:1;transform:none}}
@keyframes scFade{from{opacity:0}to{opacity:1}}
body.sc-dragging{cursor:col-resize !important;user-select:none !important}
body.sc-dragging-v{cursor:row-resize !important;user-select:none !important}

/* shared */
.sc-topbar-spacer{flex:1 1 auto}
.sc-icon-btn{width:30px;height:30px;display:flex;align-items:center;justify-content:center;flex:0 0 auto;
  background:none;border:none;color:var(--eg-text-3);border-radius:8px;cursor:pointer;padding:6px;
  transition:color .15s var(--eg-ease),background .15s var(--eg-ease),transform .15s var(--eg-spring)}
.sc-icon-btn:hover:not(:disabled){color:var(--eg-text-1);background:var(--eg-hover)}
.sc-icon-btn:active:not(:disabled){transform:scale(.92)}
.sc-icon-btn:disabled{opacity:.3;cursor:default}
.sc-icon-btn svg{width:100%;height:100%}
.sc-b{display:inline-flex;align-items:center;gap:8px;height:36px;padding:0 14px;border-radius:10px;
  background:var(--eg-glass-1);border:1px solid var(--eg-stroke);color:var(--eg-text-1);font-size:12.5px;
  font-weight:500;font-family:inherit;cursor:pointer;
  transition:background .15s var(--eg-ease),border-color .15s var(--eg-ease),opacity .15s,transform .15s var(--eg-spring)}
.sc-b:hover:not(:disabled){background:var(--eg-glass-2);border-color:var(--eg-stroke-2)}
.sc-b:active:not(:disabled){transform:scale(.96)}
.sc-b:disabled{opacity:.35;cursor:default}
.sc-b-ico{width:15px;height:15px;display:flex}
.sc-b-ico svg{width:100%;height:100%}
.sc-b-amber{background:rgb(var(--eg-accent-rgb));border-color:transparent;color:var(--bg,#000);font-weight:600}
.sc-b-amber:hover:not(:disabled){background:rgb(var(--eg-accent-bright-rgb));border-color:transparent}
.sc-b-amber-text{color:rgb(var(--eg-accent-bright-rgb))}
.sc-b-danger .sc-b-ico{color:#ff5a48}
.sc-b-danger:hover:not(:disabled){background:rgba(255,69,58,.12);border-color:rgba(255,69,58,.35)}

/* top bar */
.sc-topbar{height:56px;flex:0 0 56px;display:flex;align-items:center;gap:12px;padding:0 18px;
  border-bottom:1px solid var(--eg-stroke);background:var(--eg-side-surface)}
.sc-brand-mark{width:28px;height:28px;object-fit:contain}
.sc-brand-word{font-size:15px;letter-spacing:.22em;font-weight:600;color:var(--eg-text-1)}
.sc-mode-badge{display:inline-flex;align-items:center;gap:8px;height:30px;padding:0 12px 0 9px;margin-left:4px;
  border-radius:999px;background:var(--eg-glass-1);border:1px solid var(--eg-stroke);
  color:var(--eg-text-1);font-size:12.5px;font-weight:500}
.sc-mode-glyph{width:16px;height:16px;object-fit:contain;flex:0 0 auto}
body.pal-dark .sc-mode-glyph{filter:grayscale(1) brightness(1.4)}
.sc-top-status{display:inline-flex;align-items:center;gap:8px;font-size:12.5px;color:var(--eg-text-2);
  padding:0 6px;animation:scFade .3s ease both}
.sc-exit{display:inline-flex;align-items:center;gap:7px;height:32px;padding:0 13px 0 10px;border-radius:999px;
  background:var(--eg-glass-1);border:1px solid var(--eg-stroke);color:var(--eg-text-1);font:500 12.5px 'Inter',sans-serif;
  cursor:pointer;transition:background .15s var(--eg-ease),border-color .15s,transform .15s var(--eg-spring)}
.sc-exit:hover{background:rgba(255,69,58,.12);border-color:rgba(255,69,58,.35)}
.sc-exit:active{transform:scale(.95)}
.sc-exit-ico{width:13px;height:13px;display:flex}
.sc-exit-ico svg{width:100%;height:100%}

.sc-body{flex:1;display:flex;min-height:0}

/* rail */
.sc-rail{width:76px;flex:0 0 76px;display:flex;flex-direction:column;align-items:center;
  padding:14px 6px 16px;border-right:1px solid var(--eg-stroke);background:var(--eg-side-surface);gap:4px}
.sc-rail-spacer{flex:1 1 auto}
.sc-rail-btn{display:flex;flex-direction:column;align-items:center;gap:6px;width:64px;padding:10px 2px;
  background:none;border:none;color:var(--eg-text-3);cursor:pointer;border-radius:12px;
  font-size:10.5px;line-height:1.3;text-align:center;
  transition:color .15s var(--eg-ease),background .15s var(--eg-ease),transform .15s var(--eg-spring)}
.sc-rail-btn:hover{color:var(--eg-text-1);background:var(--eg-hover)}
.sc-rail-btn:active{transform:scale(.94)}
.sc-rail-ico{width:20px;height:20px;display:flex}
.sc-rail-ico svg{width:100%;height:100%}
.sc-rail-kbd{font-size:10px;padding:2px 6px;border:1px solid var(--eg-stroke-2);border-radius:6px;color:var(--eg-text-2)}
.sc-rail-lbl{letter-spacing:.01em}

/* chat column */
.sc-chat-col{flex:0 0 auto;min-width:46px;display:flex;background:var(--eg-side-surface);overflow:hidden;
  transition:width .36s var(--eg-spring)}
body.sc-dragging .sc-chat-col{transition:none}
.sc-chat{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;animation:scFade .3s ease both}
.sc-chat.is-collapsed{align-items:center;justify-content:flex-start;padding-top:14px}
.sc-chat-expand{display:flex;flex-direction:column;align-items:center;gap:10px;background:none;border:none;
  color:var(--eg-text-3);cursor:pointer;font-size:10.5px;padding:8px 4px;border-radius:10px;transition:color .15s,background .15s}
.sc-chat-expand:hover{color:var(--eg-text-1);background:var(--eg-hover)}
.sc-chat-expand-ico{width:18px;height:18px;display:flex}
.sc-chat-expand-ico svg{width:100%;height:100%}
.sc-chat-expand-lbl{writing-mode:vertical-rl;letter-spacing:.14em}
.sc-chat-head{display:flex;align-items:center;gap:6px;padding:14px 14px 10px 18px}
.sc-chat-title{font-size:14.5px;color:var(--eg-text-1);font-weight:600}

.sc-feed{flex:1;overflow-y:auto;padding:6px 18px 14px;display:flex;flex-direction:column;
  gap:20px;min-height:0;scrollbar-width:thin;scrollbar-color:var(--eg-glass-3) transparent}
.sc-msg{animation:scRise .34s var(--eg-spring) both}
.sc-msg.is-user{align-self:flex-end;max-width:88%}
.sc-msg.is-user .sc-msg-meta{justify-content:flex-end}
.sc-msg-meta{display:flex;align-items:center;gap:7px;margin-bottom:6px}
.sc-msg-avatar{width:20px;height:20px;object-fit:contain}
.sc-msg-who{font-size:12.5px;font-weight:600;color:var(--eg-text-1)}
.sc-msg-time{font-size:11px;color:var(--eg-text-3)}
.sc-msg-bubble{background:var(--eg-active);border:1px solid var(--eg-stroke);border-radius:16px 16px 5px 16px;
  padding:10px 14px;font-size:14px;line-height:1.6;color:var(--eg-text-1);white-space:pre-wrap;word-break:break-word}
.sc-msg-text{font-size:14px;line-height:1.68;color:var(--eg-text-1);white-space:pre-wrap;padding-left:1px;word-break:break-word}

/* permission card */
.sc-perm{border:1px solid rgba(var(--eg-accent-rgb),.45);background:var(--eg-glass-1);border-radius:14px;padding:15px 16px;
  animation:scRise .36s var(--eg-spring) both,scPermGlow 2.4s ease-in-out .4s infinite}
@keyframes scPermGlow{0%,100%{box-shadow:0 0 0 0 rgba(var(--eg-accent-rgb),0)}50%{box-shadow:0 0 0 4px rgba(var(--eg-accent-rgb),.10)}}
.sc-perm-head{display:flex;align-items:center;gap:9px;margin-bottom:9px}
.sc-perm-ico{width:18px;height:18px;color:rgb(var(--eg-accent-bright-rgb));display:flex}
.sc-perm-ico svg{width:100%;height:100%}
.sc-perm-title{font-size:13.5px;font-weight:600;color:var(--eg-text-1)}
.sc-perm-what{color:var(--eg-text-1);font-size:13.5px;line-height:1.5;margin:0 0 7px;font-weight:500}
.sc-perm-body{font-size:12.5px;line-height:1.6;color:var(--eg-text-2);margin-bottom:12px}
.sc-perm-paused{color:var(--eg-text-3);font-size:11.5px;margin:9px 0 0}
.sc-perm-scope{font-size:11.5px;color:var(--eg-text-2);margin-bottom:12px;font-family:ui-monospace,'Cascadia Code',monospace;word-break:break-all}
.sc-perm-scope-k{color:var(--eg-text-3);margin-right:6px;font-family:'Inter',sans-serif}
.sc-perm-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.sc-perm.is-done{animation:scFade .3s ease both;border-color:var(--eg-stroke);padding:13px 14px}
.sc-perm.is-done .sc-perm-what{color:var(--eg-text-2);font-weight:400;font-size:12.5px}
.sc-perm.is-done .sc-perm-ico{color:var(--eg-text-2)}
.sc-perm.is-allowed{border-color:rgba(52,199,89,.35)}
.sc-perm.is-allowed .sc-perm-ico{color:#4cd17f}
.sc-perm.is-declined{border-color:rgba(255,69,58,.35)}
.sc-perm-when{margin-left:auto;font-size:11px;color:var(--eg-text-3);font-variant-numeric:tabular-nums}
.sc-perm-answer{margin-top:10px;padding-top:10px;border-top:1px solid var(--eg-stroke)}
.sc-perm-verdict{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12.5px;font-weight:600;color:var(--eg-text-1)}
.sc-perm-verdict-sub{font-weight:400;color:var(--eg-text-3);font-size:11.5px}
.sc-perm-verdict-dot{width:7px;height:7px;border-radius:50%;background:var(--eg-text-3);flex:none}
.sc-perm.is-allowed .sc-perm-verdict-dot{background:#4cd17f}
.sc-perm.is-declined .sc-perm-verdict-dot{background:#ff6b61}
.sc-perm.is-discuss .sc-perm-verdict-dot{background:rgb(var(--eg-accent-rgb))}
.sc-perm-line{font-size:11.5px;color:var(--eg-text-2);margin-top:7px;line-height:1.5;overflow-wrap:anywhere}
.sc-perm-line-k{color:var(--eg-text-3);margin-right:6px}
.sc-perm-guidance{font-size:11.5px;color:var(--eg-text-2);line-height:1.6;margin-top:9px}
.sc-perm-guidance strong{color:var(--eg-text-1);font-weight:600}

/* chips and the live working line */
.sc-chip{display:inline-flex;align-items:center;gap:7px;padding:6px 12px;border-radius:999px;font-size:12px;
  background:var(--eg-glass-1);border:1px solid var(--eg-stroke);color:var(--eg-text-2);width:fit-content;
  animation:scRise .3s var(--eg-spring) both}
.sc-chip.is-amber{border-color:rgba(var(--eg-accent-rgb),.45);background:rgba(var(--eg-accent-rgb),.08);color:rgb(var(--eg-accent-bright-rgb))}
.sc-chip.is-red{border-color:rgba(255,69,58,.35);background:rgba(255,69,58,.08);color:#ff8b7e}
.sc-chip.is-green{border-color:rgba(52,199,89,.35);background:rgba(52,199,89,.08);color:#4cd17f}
.sc-chip.is-inline{padding:2px 9px;font-size:11px;animation:none}
.sc-chip-ico{width:14px;height:14px;display:flex}
.sc-chip-ico svg{width:100%;height:100%}
.sc-dots{display:inline-flex;gap:4px}
.sc-dots i{width:5px;height:5px;border-radius:50%;background:rgb(var(--eg-accent-rgb));animation:scb 1.2s ease-in-out infinite}
.sc-dots i:nth-child(2){animation-delay:.15s}.sc-dots i:nth-child(3){animation-delay:.3s}
@keyframes scb{0%,60%,100%{opacity:.25;transform:translateY(0)}30%{opacity:1;transform:translateY(-2px)}}
.sc-working{display:flex;align-items:flex-start;gap:12px;padding:12px 14px;border-radius:14px;
  background:var(--eg-glass-1);border:1px solid var(--eg-stroke);animation:scRise .3s var(--eg-spring) both;
  position:relative;overflow:hidden}
.sc-working::after{content:'';position:absolute;inset:0;pointer-events:none;
  background:linear-gradient(100deg,transparent 30%,rgba(var(--eg-accent-rgb),.07) 50%,transparent 70%);
  background-size:220% 100%;animation:scSheen 2.4s linear infinite}
@keyframes scSheen{from{background-position:120% 0}to{background-position:-120% 0}}
.sc-working .sc-dots{margin-top:6px;flex:0 0 auto}
.sc-working-body{min-width:0;flex:1}
.sc-working-step{display:flex;align-items:baseline;gap:8px;color:var(--eg-text-1);font-size:13px;font-weight:500}
.sc-working-count{color:var(--eg-text-3);font-size:11px;font-weight:400;flex:0 0 auto}
.sc-working-note{color:var(--eg-text-2);font-size:12px;line-height:1.5;margin-top:3px}

/* composer */
.sc-composer{margin:0 14px 14px;border:1px solid var(--eg-stroke-2);background:var(--eg-glass-1);border-radius:18px;
  padding:12px 14px 10px;box-shadow:var(--eg-inner-hi);
  transition:border-color .2s var(--eg-ease),box-shadow .2s var(--eg-ease),background .2s}
.sc-composer:focus-within{border-color:rgba(var(--eg-accent-rgb),.45);background:var(--eg-glass-2);
  box-shadow:var(--eg-inner-hi),0 0 0 4px rgba(var(--eg-accent-rgb),.07)}
.sc-composer.is-voice{border-color:rgba(var(--eg-accent-rgb),.5)}
.sc-input{width:100%;background:none;border:none;outline:none;resize:none;color:var(--eg-text-1);
  font-size:14px;font-family:inherit;line-height:1.55;min-height:56px;max-height:220px}
.sc-input::placeholder{color:var(--eg-text-3)}
.sc-composer-row{display:flex;align-items:center;gap:6px;margin-top:8px}
.sc-composer-hint{color:var(--eg-text-3);font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-left:4px}
.sc-send{width:34px;height:34px;border-radius:50%;border:none;background:rgb(var(--eg-accent-rgb));color:var(--bg,#000);
  display:flex;align-items:center;justify-content:center;cursor:pointer;padding:8px;flex:0 0 auto;
  transition:background .15s var(--eg-ease),transform .18s var(--eg-spring),opacity .15s}
.sc-send:hover:not(:disabled){background:rgb(var(--eg-accent-bright-rgb));transform:scale(1.06)}
.sc-send:active:not(:disabled){transform:scale(.92)}
.sc-send:disabled{opacity:.3;cursor:default}
.sc-send svg{width:100%;height:100%}

/* attachments in the composer */
.sc-attachments{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 9px}
.sc-attach{display:flex;align-items:center;gap:7px;max-width:200px;height:30px;padding:0 6px 0 9px;
  border-radius:9px;background:var(--eg-glass-2);border:1px solid var(--eg-stroke);color:var(--eg-text-1);font-size:11.5px;
  animation:scPop .24s var(--eg-spring) both}
.sc-attach-ico{width:13px;height:13px;flex:0 0 auto;color:var(--eg-text-2);display:flex}
.sc-attach-ico svg{width:100%;height:100%}
.sc-attach-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-attach-state{flex:0 0 auto;color:var(--eg-text-3);font-size:10.5px}
.sc-attach.is-error{border-color:rgba(255,69,58,.4);color:#ff8b7e}
.sc-attach.is-error .sc-attach-state{color:#ff8b7e}
.sc-attach-x{width:18px;height:18px;flex:0 0 auto;border:none;background:none;color:var(--eg-text-3);
  cursor:pointer;padding:3px;display:flex;border-radius:50%;transition:background .15s,color .15s}
.sc-attach-x:hover{color:var(--eg-text-1);background:var(--eg-glass-3)}
.sc-attach-x svg{width:100%;height:100%}

/* mode switcher, voice toggle and safety picker */
.sc-modeswitch{position:relative;display:flex;align-items:center;gap:6px}
.sc-modeswitch-btn,.sc-modeswitch-voice,.sc-safety-btn{display:flex;align-items:center;gap:5px;height:28px;padding:0 10px;
  border-radius:999px;border:1px solid var(--eg-stroke);background:var(--eg-glass-1);color:var(--eg-text-2);
  font-size:11.5px;font-weight:500;font-family:inherit;cursor:pointer;
  transition:background .15s var(--eg-ease),color .15s,border-color .15s,transform .15s var(--eg-spring)}
.sc-modeswitch-btn{color:var(--eg-text-1)}
.sc-modeswitch-btn:hover,.sc-modeswitch-voice:hover,.sc-safety-btn:hover{background:var(--eg-glass-2);color:var(--eg-text-1)}
.sc-modeswitch-btn:active,.sc-modeswitch-voice:active,.sc-safety-btn:active{transform:scale(.95)}
.sc-modeswitch-caret{width:12px;height:12px;display:flex;color:var(--eg-text-3)}
.sc-modeswitch-caret svg{width:100%;height:100%}
.sc-modeswitch-voice svg{width:13px;height:13px}
.sc-modeswitch-voice.is-on{color:rgb(var(--eg-accent-bright-rgb));border-color:rgba(var(--eg-accent-rgb),.45);background:rgba(var(--eg-accent-rgb),.10)}
.sc-modeswitch-menu,.sc-safety-menu{position:absolute;z-index:14;padding:5px;border-radius:14px;
  background:var(--eg-surface-2);border:1px solid var(--eg-stroke-2);box-shadow:var(--eg-shadow-lg),var(--eg-inner-hi);
  backdrop-filter:blur(30px) saturate(1.6);-webkit-backdrop-filter:blur(30px) saturate(1.6);
  animation:scPop .22s var(--eg-spring) both}
.sc-modeswitch-menu{top:34px;right:0;width:256px;transform-origin:top right}
.sc-safety-menu{bottom:34px;left:0;width:296px;transform-origin:bottom left}
.sc-modeswitch-item{display:flex;flex-direction:column;gap:2px;width:100%;padding:9px 11px;
  border:none;border-radius:10px;background:none;color:var(--eg-text-1);text-align:left;cursor:pointer;font-family:inherit;
  transition:background .12s var(--eg-ease)}
.sc-modeswitch-item:hover{background:var(--eg-hover)}
.sc-modeswitch-item.is-sel{background:var(--eg-active)}
.sc-modeswitch-item-lbl{font-size:12.5px;font-weight:600}
.sc-modeswitch-item-hint{font-size:11px;color:var(--eg-text-3)}
.sc-safety{position:relative;flex:0 0 auto}
.sc-safety-btn{max-width:170px}
.sc-safety-ico{width:13px;height:13px;flex:0 0 auto;display:flex}
.sc-safety-ico svg{width:100%;height:100%}
.sc-safety-lbl{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-safety-btn.is-ask_always{color:#64b5ff;border-color:rgba(100,181,255,.4);background:rgba(100,181,255,.09)}
.sc-safety-btn.is-auto{color:rgb(var(--eg-accent-bright-rgb));border-color:rgba(var(--eg-accent-rgb),.45);background:rgba(var(--eg-accent-rgb),.10)}
.sc-safety-head{padding:7px 11px 5px;color:var(--eg-text-3);font-size:10.5px;text-transform:uppercase;letter-spacing:.06em}
.sc-safety-foot{padding:8px 11px 6px;margin-top:3px;border-top:1px solid var(--eg-stroke);color:var(--eg-text-3);font-size:10.5px;line-height:1.45}

/* a file SYRIX made, in the chat */
.sc-filechip{display:inline-flex;align-items:center;gap:7px;vertical-align:middle;max-width:100%;margin:2px 3px;
  padding:5px 11px 5px 8px;border-radius:10px;border:1px solid var(--eg-stroke);background:var(--eg-glass-1);
  color:var(--eg-text-1);font-family:inherit;font-size:13px;cursor:pointer;
  transition:background .15s var(--eg-ease),border-color .15s,transform .15s var(--eg-spring)}
.sc-filechip:hover{background:var(--eg-glass-2);border-color:var(--eg-stroke-2)}
.sc-filechip:hover .sc-filechip-name{text-decoration:underline}
.sc-filechip:active{transform:scale(.97)}
.sc-filechip-ico{width:15px;height:15px;flex:0 0 auto;display:flex;color:#64b5ff}
.sc-filechip-ico svg{width:100%;height:100%}
.sc-filechip-ico.is-pdf{color:#ff6b5e}
.sc-filechip-ico.is-sheet{color:#4cd17f}
.sc-filechip-ico.is-image{color:#c08cff}
.sc-filechip-ico.is-code{color:rgb(var(--eg-accent-bright-rgb))}
.sc-filechip-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-underline-offset:2px}

/* voice in the composer: a calm pulsing dot, in the palette accent */
.sc-orb-wrap{display:flex;align-items:center;gap:10px;padding:2px 2px 10px;animation:scFade .3s ease both}
.sc-orb{position:relative;width:24px;height:24px;flex:0 0 auto}
.sc-orb i{position:absolute;inset:0;border-radius:50%;border:1.5px solid rgba(var(--eg-accent-rgb),.5)}
.sc-orb i:nth-child(1){animation:scOrb 2s ease-out infinite}
.sc-orb i:nth-child(2){animation:scOrb 2s ease-out infinite .66s}
.sc-orb i:nth-child(3){inset:7px;border:none;background:rgb(var(--eg-accent-rgb));animation:scOrbCore 1.6s ease-in-out infinite}
.sc-orb.is-speaking i:nth-child(3){animation-duration:.6s}
.sc-orb.is-thinking i:nth-child(1),.sc-orb.is-thinking i:nth-child(2){animation-play-state:paused;opacity:.25}
.sc-orb.is-thinking i:nth-child(3){animation:scOrbCore .9s ease-in-out infinite}
@keyframes scOrb{0%{transform:scale(.55);opacity:.8}100%{transform:scale(1.4);opacity:0}}
@keyframes scOrbCore{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(.72);opacity:.6}}
.sc-orb-lbl{font-size:12px;font-weight:500;color:var(--eg-text-1)}

/* split handle */
.sc-split{width:5px;flex:0 0 5px;cursor:col-resize;background:transparent;position:relative;
  border-left:1px solid var(--eg-stroke);transition:background .15s}
.sc-split:hover{background:rgba(var(--eg-accent-rgb),.35)}
.sc-split::after{content:'';position:absolute;inset:0 -4px}

/* work surface: task bar, browser, drawer */
.sc-work{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0;padding:14px 16px 0;gap:12px}
.sc-taskbar{flex:0 0 auto;display:flex;flex-direction:column;gap:10px}
.sc-task-line{display:flex;align-items:baseline;gap:9px;min-width:0;animation:scFade .3s ease both}
.sc-task-k{color:rgb(var(--eg-accent-bright-rgb));font-weight:600;font-size:13px;flex:0 0 auto}
.sc-task-goal{color:var(--eg-text-1);font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-task-line.is-terminal .sc-task-k{color:var(--eg-text-3)}
.sc-task-line.is-terminal .sc-task-goal{color:var(--eg-text-2)}
.sc-task-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.sc-progress{height:3px;border-radius:999px;background:var(--eg-glass-2);overflow:hidden;position:relative}
.sc-progress span{display:block;height:100%;border-radius:inherit;background:rgb(var(--eg-accent-rgb));
  transition:width .6s var(--eg-spring)}
.sc-progress.is-live span{background:linear-gradient(90deg,rgb(var(--eg-accent-deep-rgb)),rgb(var(--eg-accent-bright-rgb)),rgb(var(--eg-accent-deep-rgb)));
  background-size:200% 100%;animation:scFlow 1.6s linear infinite}
@keyframes scFlow{from{background-position:200% 0}to{background-position:0 0}}
.sc-progress.is-done span{background:#4cd17f}
.sc-progress.is-failed span,.sc-progress.is-stopped span{background:#ff6b61}

.sc-status{display:flex;align-items:center;gap:9px;height:36px;padding:0 12px 0 14px;border-radius:10px;
  background:var(--eg-glass-1);border:1px solid var(--eg-stroke);font:500 12.5px 'Inter',sans-serif;color:var(--eg-text-1);
  cursor:pointer;transition:background .15s var(--eg-ease),border-color .15s,transform .15s var(--eg-spring)}
.sc-status:hover{background:var(--eg-glass-2);border-color:var(--eg-stroke-2)}
.sc-status:active{transform:scale(.97)}
.sc-status-dot{width:8px;height:8px;border-radius:50%;background:var(--eg-text-3);flex:0 0 auto;transition:background .3s}
.is-busy .sc-status-dot{background:rgb(var(--eg-accent-rgb));animation:scp 1.4s ease-in-out infinite}
.is-attention .sc-status-dot{background:rgb(var(--eg-accent-rgb));box-shadow:0 0 0 3px rgba(var(--eg-accent-rgb),.2)}
.is-user .sc-status-dot{background:#64b5ff}
.is-error .sc-status-dot{background:#ff5a48}
.is-done .sc-status-dot{background:#4cd17f}
.sc-status-caret{width:14px;height:14px;color:var(--eg-text-3);display:flex;transform:rotate(-90deg)}
.sc-status-caret svg{width:100%;height:100%}
@keyframes scp{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.45;transform:scale(.8)}}

/* browser */
.sc-browser{flex:1;display:flex;flex-direction:column;min-height:0;
  border:1px solid var(--eg-stroke);border-radius:14px;overflow:hidden;background:var(--eg-glass-1);
  box-shadow:0 20px 50px -30px rgba(0,0,0,.9)}
.sc-tabs{display:flex;align-items:center;gap:3px;padding:7px 8px 0;background:var(--eg-side-surface);overflow-x:auto;scrollbar-width:none}
.sc-tabs::-webkit-scrollbar{display:none}
.sc-tab{display:flex;align-items:center;gap:8px;height:34px;padding:0 8px 0 11px;border-radius:10px 10px 0 0;
  background:transparent;color:var(--eg-text-3);font-size:12.5px;cursor:pointer;max-width:240px;min-width:120px;
  border:1px solid transparent;border-bottom:none;
  transition:background .15s var(--eg-ease),color .15s var(--eg-ease);animation:scTabIn .28s var(--eg-spring) both}
@keyframes scTabIn{from{opacity:0;max-width:0;min-width:0}to{opacity:1;max-width:240px;min-width:120px}}
.sc-tab:hover{background:var(--eg-hover);color:var(--eg-text-1)}
.sc-tab.is-active{background:var(--eg-glass-2);color:var(--eg-text-1);border-color:var(--eg-stroke)}
.sc-tab-ico{width:15px;height:15px;flex:0 0 auto;object-fit:contain;border-radius:3px}
.sc-tab-ico-fallback{display:flex;color:var(--eg-text-3)}
.sc-tab-ico-fallback svg{width:100%;height:100%}
.sc-tab-title{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-tab-x{width:18px;height:18px;flex:0 0 auto;border:none;background:none;color:var(--eg-text-3);
  border-radius:50%;cursor:pointer;padding:4px;display:flex;opacity:.6;transition:background .15s,color .15s,opacity .15s}
.sc-tab:hover .sc-tab-x,.sc-tab.is-active .sc-tab-x{opacity:1}
.sc-tab-x:hover{background:var(--eg-glass-3);color:var(--eg-text-1)}
.sc-tab-x svg{width:100%;height:100%}
.sc-tab-new{width:28px;height:28px;flex:0 0 auto;border:none;background:none;color:var(--eg-text-2);
  border-radius:50%;cursor:pointer;padding:6px;margin:0 0 4px 4px;display:flex;
  transition:background .15s,color .15s,transform .15s var(--eg-spring)}
.sc-tab-new:hover{background:var(--eg-hover);color:var(--eg-text-1)}
.sc-tab-new:active{transform:scale(.88)}
.sc-tab-new svg{width:100%;height:100%}
.sc-urlbar{display:flex;align-items:center;gap:4px;padding:7px 10px;background:var(--eg-glass-2);border-bottom:1px solid var(--eg-stroke)}
.sc-nav-btn{width:30px;height:30px;flex:0 0 auto;border:none;background:none;color:var(--eg-text-1);
  border-radius:50%;cursor:pointer;padding:7px;display:flex;transition:background .15s,color .15s,transform .15s var(--eg-spring)}
.sc-nav-btn:hover:not(:disabled){background:var(--eg-hover)}
.sc-nav-btn:active:not(:disabled){transform:scale(.88)}
.sc-nav-btn:disabled{color:var(--eg-text-3);opacity:.5;cursor:default}
.sc-nav-btn svg{width:100%;height:100%}
.sc-url{flex:1;display:flex;align-items:center;gap:9px;height:32px;padding:0 13px;border-radius:999px;margin-left:4px;
  background:var(--eg-glass-1);border:1px solid var(--eg-stroke);min-width:0;
  transition:background .15s var(--eg-ease),border-color .15s,box-shadow .15s}
.sc-url:hover{background:var(--eg-glass-2)}
.sc-url.is-editing,.sc-url:focus-within{border-color:rgba(var(--eg-accent-rgb),.45);box-shadow:0 0 0 3px rgba(var(--eg-accent-rgb),.08)}
.sc-url-lock{width:13px;height:13px;color:var(--eg-text-3);flex:0 0 auto;display:flex}
.sc-url-lock.is-secure{color:#4cd17f}
.sc-url-lock svg{width:100%;height:100%}
.sc-url-input{flex:1;background:none;border:none;outline:none;color:var(--eg-text-1);font-size:12.5px;font-family:inherit;min-width:0}
.sc-url-input::placeholder{color:var(--eg-text-3)}
.sc-viewport{flex:1;position:relative;min-height:0;background:var(--bg,#000)}
.sc-viewport-empty{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:10px;text-align:center;padding:24px;animation:scFade .4s ease both}
.sc-empty-logo{width:52px;height:52px;object-fit:contain;opacity:.6;margin-bottom:4px}
.sc-empty-title{font-size:14px;font-weight:500;color:var(--eg-text-1)}
.sc-empty-body{font-size:12.5px;color:var(--eg-text-3);max-width:300px;line-height:1.6}
.sc-annotate-hint{position:absolute;top:12px;left:50%;transform:translateX(-50%);z-index:5;
  display:flex;align-items:center;gap:9px;padding:9px 15px;border-radius:999px;
  background:var(--eg-surface-2);border:1px solid rgba(var(--eg-accent-rgb),.5);color:var(--eg-text-1);font-size:12.5px;
  box-shadow:var(--eg-shadow-lg);animation:scPop .24s var(--eg-spring) both}

/* drawer */
.sc-drawer{flex:0 0 auto;display:flex;flex-direction:column;position:relative;
  border-top:1px solid var(--eg-stroke);min-height:0;overflow:hidden;
  transition:height .34s var(--eg-spring)}
body.sc-dragging-v .sc-drawer{transition:none}
.sc-drawer-grip{position:absolute;top:-3px;left:0;right:0;height:7px;cursor:row-resize;z-index:2;transition:background .15s}
.sc-drawer-grip:hover{background:rgba(var(--eg-accent-rgb),.35)}
.sc-drawer-head{display:flex;align-items:center;gap:6px;padding:0 6px;height:42px;flex:0 0 42px}
.sc-drawer-tab{background:none;border:none;color:var(--eg-text-3);font-size:12.5px;font-weight:500;font-family:inherit;
  cursor:pointer;height:28px;padding:0 12px;border-radius:999px;transition:color .15s var(--eg-ease),background .15s var(--eg-ease)}
.sc-drawer-tab:hover{color:var(--eg-text-1);background:var(--eg-hover)}
.sc-drawer-tab.is-active{color:var(--eg-text-1);background:var(--eg-active)}
.sc-drawer-toggle{display:flex;align-items:center;gap:6px;background:none;border:none;color:var(--eg-text-3);
  font-size:12px;font-family:inherit;cursor:pointer;padding:5px 8px;border-radius:8px;transition:color .15s,background .15s}
.sc-drawer-toggle:hover{color:var(--eg-text-1);background:var(--eg-hover)}
.sc-drawer-chev{width:14px;height:14px;display:flex;transform:rotate(180deg);transition:transform .3s var(--eg-spring)}
.sc-drawer-chev.is-up{transform:rotate(0deg)}
.sc-drawer-chev svg{width:100%;height:100%}
.sc-drawer-body{flex:1;overflow-y:auto;padding:2px 16px 14px;min-height:0;animation:scFade .25s ease both}

.sc-step,.sc-act,.sc-res{display:flex;align-items:center;gap:11px;padding:8px 6px;font-size:12.5px;
  border-bottom:1px solid var(--eg-stroke);animation:scRise .3s var(--eg-spring) both}
.sc-step:last-child,.sc-act:last-child,.sc-res:last-child{border-bottom:none}
.sc-step-ico{width:17px;height:17px;flex:0 0 auto;display:flex}
.sc-step-ico svg{width:100%;height:100%}
.sc-step-ico.is-done{color:#4cd17f}
.sc-step-ico.is-run{color:rgb(var(--eg-accent-rgb));animation:scp 1.4s ease-in-out infinite}
.sc-step-ico.is-blocked{color:#ff5a48}
.sc-step-ico.is-pending{color:var(--eg-text-3);opacity:.6}
.sc-step.is-running{background:rgba(var(--eg-accent-rgb),.06);border-radius:8px}
.sc-step-lbl{flex:1;color:var(--eg-text-1);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-step.is-pending .sc-step-lbl{color:var(--eg-text-3)}
.sc-step.is-done .sc-step-lbl{color:var(--eg-text-2)}
.sc-step-note{flex:0 1 auto;max-width:44%;color:var(--eg-text-3);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-step-time{flex:0 0 auto;color:var(--eg-text-3);font-size:11.5px;min-width:62px;text-align:right;font-variant-numeric:tabular-nums}
.sc-res.is-openable{cursor:pointer;border-radius:8px;transition:background .15s}
.sc-res.is-openable:hover{background:var(--eg-hover)}
.sc-res-open{flex:0 0 auto;color:rgb(var(--eg-accent-bright-rgb));font-size:11px;font-weight:600;opacity:0;transition:opacity .15s}
.sc-res.is-openable:hover .sc-res-open{opacity:1}
.sc-act-dot{width:7px;height:7px;border-radius:50%;flex:0 0 auto;background:#4cd17f}
.sc-act-dot.is-denied,.sc-act-dot.is-blocked,.sc-act-dot.is-error,.sc-act-dot.is-declined{background:#ff5a48}
.sc-act-dot.is-confirm_pending,.sc-act-dot.is-discuss{background:rgb(var(--eg-accent-rgb))}
.sc-act-dot.is-timeout{background:#ffa04d}
.sc-act-action{flex:0 0 auto;color:var(--eg-text-1);font-family:ui-monospace,'Cascadia Code',monospace;font-size:11.5px}
.sc-act-scope{flex:1;color:var(--eg-text-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-act-result{flex:0 0 auto;color:var(--eg-text-2);font-size:11.5px;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-res-ico{width:16px;height:16px;flex:0 0 auto;color:var(--eg-text-2);display:flex}
.sc-res-ico svg{width:100%;height:100%}
.sc-res-title{flex:0 0 auto;color:var(--eg-text-1);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-res-diff{flex:0 0 auto;font-family:ui-monospace,monospace;font-size:11px}
.sc-res-diff .add{color:#4cd17f}.sc-res-diff .rem{color:#ff5a48;margin-left:5px}
.sc-res-loc{flex:1;color:var(--eg-text-3);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-empty-row{padding:16px 6px;color:var(--eg-text-3);font-size:12.5px}

/* command palette and annotate */
.sc-palette-scrim{position:fixed;inset:0;z-index:950;background:rgba(0,0,0,.5);
  display:flex;align-items:flex-start;justify-content:center;padding-top:14vh;
  backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);animation:scFade .2s ease both}
.sc-palette,.sc-annotate{width:min(560px,90vw);background:var(--eg-surface-2);border:1px solid var(--eg-stroke-2);
  border-radius:16px;box-shadow:var(--eg-shadow-lg),var(--eg-inner-hi);overflow:hidden;
  animation:scPalIn .3s var(--eg-spring) both}
@keyframes scPalIn{from{opacity:0;transform:translateY(-10px) scale(.97)}to{opacity:1;transform:none}}
.sc-palette-search{display:flex;align-items:center;gap:11px;padding:14px 16px;border-bottom:1px solid var(--eg-stroke)}
.sc-palette-ico{width:16px;height:16px;color:var(--eg-text-3);display:flex}
.sc-palette-ico svg{width:100%;height:100%}
.sc-palette-input{flex:1;background:none;border:none;outline:none;color:var(--eg-text-1);font-size:14.5px;font-family:inherit}
.sc-palette-input::placeholder{color:var(--eg-text-3)}
.sc-palette-list{max-height:min(380px,50vh);overflow-y:auto;padding:6px}
.sc-palette-item{display:flex;align-items:baseline;gap:11px;width:100%;padding:10px 12px;border:none;
  background:none;color:var(--eg-text-1);font-size:13px;font-family:inherit;text-align:left;cursor:pointer;border-radius:10px;
  transition:background .1s}
.sc-palette-item.is-sel{background:var(--eg-active)}
.sc-palette-lbl{flex:0 0 auto}
.sc-palette-hint{flex:1;color:var(--eg-text-3);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-annotate{padding:18px}
.sc-annotate-head{font-size:14.5px;color:var(--eg-text-1);font-weight:600;margin-bottom:12px}
.sc-annotate-target{display:flex;align-items:center;gap:9px;padding:10px 12px;border-radius:10px;
  background:var(--eg-glass-1);border:1px solid var(--eg-stroke);margin-bottom:12px}
.sc-annotate-tag{flex:0 0 auto;font-family:ui-monospace,monospace;font-size:11px;color:rgb(var(--eg-accent-bright-rgb));
  padding:2px 7px;border-radius:6px;background:rgba(var(--eg-accent-rgb),.10)}
.sc-annotate-text{flex:1;color:var(--eg-text-2);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sc-annotate-input{width:100%;box-sizing:border-box;background:var(--eg-glass-1);border:1px solid var(--eg-stroke);border-radius:10px;
  color:var(--eg-text-1);font-size:13px;font-family:inherit;padding:10px 12px;outline:none;resize:none;line-height:1.5;
  transition:border-color .15s}
.sc-annotate-input:focus{border-color:rgba(var(--eg-accent-rgb),.45)}
.sc-annotate-actions{display:flex;gap:8px;margin-top:13px}

/* Host for a full-window HUD panel (Connectors) opened from the rail. */
.sc-panel-host{position:absolute;inset:0;z-index:20}

/* scrollbars */
.sc-feed::-webkit-scrollbar,.sc-drawer-body::-webkit-scrollbar,.sc-palette-list::-webkit-scrollbar{width:9px}
.sc-feed::-webkit-scrollbar-thumb,.sc-drawer-body::-webkit-scrollbar-thumb,
.sc-palette-list::-webkit-scrollbar-thumb{background:var(--eg-glass-3);border-radius:5px;border:2px solid transparent;background-clip:content-box}
.sc-feed::-webkit-scrollbar-track,.sc-drawer-body::-webkit-scrollbar-track{background:transparent}

/* Supercomputer is a full page, not an overlay. These few HUD elements are
   pinned above 9950 with !important, so covering them is not enough — they are
   removed for as long as the workspace is mounted and restored on exit.
   Scoped entirely to this body class; normal SYRIX is untouched. */
body.body--supercomputer .p-island,
body.body--supercomputer .p-island-panel,
body.body--supercomputer .p-widget-menu,
body.body--supercomputer .p-stage-folder-panel,
body.body--supercomputer .p-conn-badge,
body.body--supercomputer .eg-bottom-composer,
body.body--supercomputer .eg-rail,
body.body--supercomputer .eg-model-menu{display:none !important}
body.body--supercomputer .cx-root{z-index:10120}

@media (max-width:1100px){.sc-rail{width:60px;flex-basis:60px}.sc-rail-lbl{display:none}.sc-top-status{display:none}}
@media (prefers-reduced-motion:reduce){
  .sc-root,.sc-root *{animation-duration:.001ms !important;animation-iteration-count:1 !important;transition-duration:.001ms !important}
}
`;

  if (typeof document !== 'undefined' && !document.getElementById('sc-workspace-css')) {
    const st = document.createElement('style');
    st.id = 'sc-workspace-css';
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  window.SyrixSupercomputerWorkspace = SyrixSupercomputerWorkspace;
})();
