/* SYRIX ISLAND HUB — shared stores + renderers that make the Dynamic Island
 * a live activity surface instead of a status pill.
 *
 * Exposes on window:
 *   SyrixStopwatch          — global stopwatch store (survives widget close + HUD reload)
 *   SyrixMediaSessionHook   — app-level hook: Spotify/media state that keeps
 *                             polling even when the Music widget is closed
 *   SyrixIslandCard         — renderer for backend rich cards (sports / wiki)
 *   SyrixIslandActivities   — expanded-island activity rows w/ controls
 *   SyrixIslandEq           — tiny equalizer for the compact pill
 *
 * Loaded BEFORE syrix-primary-app.jsx (see index.html / build.js order).
 * Everything is wrapped in an IIFE so no top-level const leaks into the
 * shared script scope. */
(() => {
  const { useState, useEffect, useRef } = React;

  /* ── Global stopwatch store ─────────────────────────────────────────────
   * The Timer widget's stopwatch used to be widget-local state, so closing
   * the widget killed a running stopwatch. This store is the single source
   * of truth: the widget and the Dynamic Island both subscribe to it. */
  const SW_KEY = 'syrix_stopwatch_v1';
  function swLoad() {
    try {
      const raw = JSON.parse(localStorage.getItem(SW_KEY) || 'null');
      if (raw && typeof raw === 'object') {
        return {
          running: !!raw.running,
          baseMs: Math.max(0, Number(raw.baseMs) || 0),
          startedAt: Math.max(0, Number(raw.startedAt) || 0),
        };
      }
    } catch (_) {}
    return { running: false, baseMs: 0, startedAt: 0 };
  }
  const swState = swLoad();
  // A stopwatch "running" from a previous session keeps counting (wall clock),
  // but guard against a corrupt startedAt in the future.
  if (swState.running && (!swState.startedAt || swState.startedAt > Date.now())) {
    swState.startedAt = Date.now();
  }
  const swSubs = new Set();
  function swSave() {
    try { localStorage.setItem(SW_KEY, JSON.stringify(swState)); } catch (_) {}
  }
  function swElapsedMs() {
    return swState.baseMs + (swState.running ? Math.max(0, Date.now() - swState.startedAt) : 0);
  }
  function swGet() {
    return { running: swState.running, elapsedMs: swElapsedMs() };
  }
  function swEmit() {
    swSave();
    const snap = swGet();
    swSubs.forEach((fn) => { try { fn(snap); } catch (_) {} });
  }
  window.SyrixStopwatch = {
    get: swGet,
    subscribe(fn) { swSubs.add(fn); try { fn(swGet()); } catch (_) {} return () => swSubs.delete(fn); },
    start() {
      if (swState.running) return;
      swState.startedAt = Date.now();
      swState.running = true;
      swEmit();
    },
    pause() {
      if (!swState.running) return;
      swState.baseMs = swElapsedMs();
      swState.running = false;
      swState.startedAt = 0;
      swEmit();
    },
    toggle() { swState.running ? window.SyrixStopwatch.pause() : window.SyrixStopwatch.start(); },
    reset() {
      swState.running = false;
      swState.baseMs = 0;
      swState.startedAt = 0;
      swEmit();
    },
  };

  function fmtStopwatch(ms) {
    const s = Math.max(0, Math.floor(ms / 1000));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const ss = s % 60;
    return (h > 0 ? String(h).padStart(2, '0') + ':' : '')
      + String(m).padStart(2, '0') + ':' + String(ss).padStart(2, '0');
  }
  window.SyrixStopwatch.format = fmtStopwatch;

  /* ── Media session hook ─────────────────────────────────────────────────
   * App-level (mounted once by primary-app). Polls Spotify through the
   * main-process IPC when configured — 3s while something is loaded, 10s when
   * idle — and falls back to the backend `snap.music` feed otherwise. The key
   * property: it keeps running when the Music widget is closed, which is what
   * lets the island take over the session. */
  function SyrixMediaSessionHook() {
    const [spConfig, setSpConfig] = useState(null);
    const [sp, setSp] = useState(null);
    const [spFetchedAt, setSpFetchedAt] = useState(0);
    const [music, setMusic] = useState({ provider: '', state: 'idle', track: '', artist: '' });
    const pollTimerRef = useRef(null);
    const spRef = useRef(null);
    spRef.current = sp;

    useEffect(() => {
      try {
        const api = window.syrixHud && window.syrixHud.spotify;
        if (api && api.status) {
          api.status().then((cfg) => setSpConfig(cfg || { configured: false }))
            .catch(() => setSpConfig({ configured: false }));
        } else {
          setSpConfig({ configured: false });
        }
      } catch (_) { setSpConfig({ configured: false }); }
    }, []);

    useEffect(() => {
      if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
      return window.SyrixBridge.subscribe((s) => {
        if (s && s.music && typeof s.music === 'object') setMusic(s.music);
      });
    }, []);

    useEffect(() => {
      if (!spConfig || !spConfig.configured) return;
      let cancelled = false;
      async function poll() {
        try {
          const api = window.syrixHud && window.syrixHud.spotify;
          if (!api || !api.call) return;
          const res = await api.call('current', {});
          if (cancelled) return;
          if (res && res.ok) {
            setSp(res.data || null);
            setSpFetchedAt(Date.now());
          } else if (res && res.status === 204) {
            setSp(null);
            setSpFetchedAt(Date.now());
          }
        } catch (_) {}
        if (!cancelled) {
          const hasTrack = !!(spRef.current && spRef.current.item);
          pollTimerRef.current = setTimeout(poll, hasTrack ? 3000 : 10000);
        }
      }
      poll();
      return () => {
        cancelled = true;
        if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      };
    }, [spConfig]);

    async function spCall(action, payload) {
      try {
        const api = window.syrixHud && window.syrixHud.spotify;
        if (!api || !api.call) return null;
        const res = await api.call(action, payload || {});
        // Refresh after a control action so the island stays truthful. Spotify's
        // own player state can lag a play/pause command by up to ~1s, so a
        // single 450ms check was sometimes catching it mid-transition and
        // showing the pre-click state (pause "not sticking"). Check twice.
        const refresh = async () => {
          try {
            const cur = await api.call('current', {});
            if (cur && cur.ok) { setSp(cur.data || null); setSpFetchedAt(Date.now()); }
            else if (cur && cur.status === 204) { setSp(null); setSpFetchedAt(Date.now()); }
          } catch (_) {}
        };
        setTimeout(refresh, 450);
        setTimeout(refresh, 1300);
        return res;
      } catch (_) { return null; }
    }

    const hasSpotify = !!(spConfig && spConfig.configured);
    const spTrack = sp && sp.item;
    const B = window.SyrixBridge;

    function play()  { hasSpotify ? spCall('play', {})     : (B && (B.musicResume ? B.musicResume() : (B.musicPlay && B.musicPlay('')))); }
    function pause() { hasSpotify ? spCall('pause')        : (B && B.musicPause && B.musicPause()); }
    function next()  { hasSpotify ? spCall('next')         : (B && B.musicNext && B.musicNext()); }
    function prev()  { hasSpotify ? spCall('previous')     : (B && B.musicPrev && B.musicPrev()); }

    if (hasSpotify) {
      const playing = !!(sp && sp.is_playing);
      return {
        source: 'spotify',
        active: !!spTrack,
        playing,
        paused: !playing && !!spTrack,
        track: spTrack ? String(spTrack.name || '') : '',
        artist: spTrack ? (spTrack.artists || []).map((a) => a.name).join(', ') : '',
        artUrl: spTrack ? (((spTrack.album || {}).images || []).map((i) => i.url)[0] || '') : '',
        progressMs: sp ? Number(sp.progress_ms || 0) : 0,
        durationMs: spTrack ? Number(spTrack.duration_ms || 0) : 0,
        progressTs: spFetchedAt,
        play, pause, next, prev,
      };
    }
    const mState = String(music.state || 'idle');
    const mActive = mState === 'playing' || mState === 'paused';
    return {
      source: music.provider ? String(music.provider) : 'media',
      active: mActive,
      playing: mState === 'playing',
      paused: mState === 'paused',
      track: String(music.track || ''),
      artist: String(music.artist || (music.provider ? music.provider.toUpperCase() : '')),
      artUrl: '',
      progressMs: 0, durationMs: 0, progressTs: 0,
      play, pause, next, prev,
    };
  }
  window.SyrixMediaSessionHook = SyrixMediaSessionHook;

  /* ── Compact-pill equalizer ── */
  function SyrixIslandEq({ playing }) {
    return (
      <div className={'sy-isl-eq' + (playing ? ' on' : '')} aria-hidden="true">
        <span/><span/><span/><span/>
      </div>
    );
  }
  window.SyrixIslandEq = SyrixIslandEq;

  /* ── Icons (inline, stroke = currentColor) ── */
  const IcPrev = (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6 8.5 6V6z"/></svg>
  );
  const IcNext = (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6h2v12h-2z"/></svg>
  );
  const IcPlay = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
  );
  const IcPause = (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
  );

  /* ── Activity rows for the expanded island ──────────────────────────────
   * One unified section: every task that is active-but-not-stopped and whose
   * widget is closed shows here with real controls. */
  function SyrixIslandActivities({
    media, showMedia, onEndMedia,
    stopwatch, showStopwatch,
    timers, timerDisplay, showTimers, onStopTimers,
  }) {
    const rows = [];

    if (showMedia && media && media.active) {
      const pct = media.durationMs > 0
        ? Math.min(100, ((media.progressMs + (media.playing ? Date.now() - media.progressTs : 0)) / media.durationMs) * 100)
        : 0;
      rows.push(
        <div className="sy-isl-act sy-isl-act-media" key="media">
          {media.artUrl
            ? <img className="sy-isl-art" src={media.artUrl} alt="" onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }} />
            : <div className="sy-isl-art sy-isl-art-disc"><span className={media.playing ? 'spin' : ''}/></div>}
          <div className="sy-isl-act-meta">
            <div className="sy-isl-act-title" title={media.track}>{(media.track || 'NOTHING PLAYING').slice(0, 34)}</div>
            <div className="sy-isl-act-sub">{(media.artist || media.source || '').slice(0, 36)}</div>
            {media.durationMs > 0 && (
              <div className="sy-isl-seek"><div className="sy-isl-seek-fill" style={{ width: pct + '%' }}/></div>
            )}
          </div>
          <div className="sy-isl-act-ctrls">
            <button className="sy-isl-cbtn" title="Previous" onClick={(e) => { e.stopPropagation(); media.prev(); }}>{IcPrev}</button>
            <button className="sy-isl-cbtn main" title={media.playing ? 'Pause' : 'Play'}
              onClick={(e) => { e.stopPropagation(); media.playing ? media.pause() : media.play(); }}>
              {media.playing ? IcPause : IcPlay}
            </button>
            <button className="sy-isl-cbtn" title="Next" onClick={(e) => { e.stopPropagation(); media.next(); }}>{IcNext}</button>
            <button className="sy-isl-cbtn danger" title="End — pause and remove from island"
              onClick={(e) => { e.stopPropagation(); onEndMedia && onEndMedia(); }}>
              ■
            </button>
          </div>
        </div>
      );
    }

    if (showTimers && timers && timers.length > 0) {
      rows.push(
        <div className="sy-isl-act sy-isl-act-timer" key="timer">
          <div className="sy-isl-act-badge">⏱</div>
          <div className="sy-isl-act-meta">
            <div className="sy-isl-act-title mono gold">{timerDisplay || '--:--'}</div>
            <div className="sy-isl-act-sub">
              {timers.length > 1
                ? `${timers.length} TIMERS RUNNING`
                : String((timers[0] && (timers[0].label || timers[0].text)) || 'TIMER').slice(0, 34).toUpperCase()}
            </div>
          </div>
          <div className="sy-isl-act-ctrls">
            <button className="sy-isl-cbtn danger wide" title="Stop all timers"
              onClick={(e) => { e.stopPropagation(); onStopTimers && onStopTimers(); }}>
              ■ STOP
            </button>
          </div>
        </div>
      );
    }

    if (showStopwatch && stopwatch && (stopwatch.running || stopwatch.elapsedMs > 0)) {
      rows.push(
        <div className="sy-isl-act sy-isl-act-sw" key="stopwatch">
          <div className="sy-isl-act-badge">⏲</div>
          <div className="sy-isl-act-meta">
            <div className={'sy-isl-act-title mono' + (stopwatch.running ? ' green' : '')}>
              {fmtStopwatch(stopwatch.elapsedMs)}
            </div>
            <div className="sy-isl-act-sub">STOPWATCH · {stopwatch.running ? 'RUNNING' : 'PAUSED'}</div>
          </div>
          <div className="sy-isl-act-ctrls">
            <button className="sy-isl-cbtn main" title={stopwatch.running ? 'Pause' : 'Resume'}
              onClick={(e) => { e.stopPropagation(); window.SyrixStopwatch.toggle(); }}>
              {stopwatch.running ? IcPause : IcPlay}
            </button>
            <button className="sy-isl-cbtn danger" title="Reset stopwatch"
              onClick={(e) => { e.stopPropagation(); window.SyrixStopwatch.reset(); }}>
              ↺
            </button>
          </div>
        </div>
      );
    }

    if (!rows.length) return null;
    return (
      <div className="sy-isl-activities">
        <div className="sy-isl-sec-head">
          <span>NOW</span>
          <b>{rows.length} ACTIVE</b>
        </div>
        {rows}
      </div>
    );
  }
  window.SyrixIslandActivities = SyrixIslandActivities;

  /* ── Persistent watches ──────────────────────────────────────────────── */
  function SyrixIslandWatches({ tasks, onCancel }) {
    const rows = (Array.isArray(tasks) ? tasks : []).filter((task) => {
      return task && (task.archetype === 'watch' || ['quote', 'file', 'email', 'manual'].includes(task.kind));
    });
    if (!rows.length) return null;
    const active = rows.filter((task) => ['queued', 'running'].includes(String(task.status || '').toLowerCase())).length;
    return (
      <div className="sy-isl-activities sy-isl-watches">
        <div className="sy-isl-sec-head">
          <span>WATCHES</span><b>{active} ACTIVE</b>
        </div>
        {rows.map((task) => {
          const status = String(task.status || 'running').toLowerCase();
          const resolved = status === 'resolved';
          const failed = status === 'failed' || status === 'cancelled';
          const title = String(task.title || task.kind || 'Live watch').slice(0, 42);
          const detail = String(task.detail || status).slice(0, 88);
          return (
            <div className="sy-isl-act sy-isl-act-watch" key={task.id}>
              <div className="sy-isl-act-badge">{resolved ? '✓' : failed ? '!' : '◐'}</div>
              <div className="sy-isl-act-meta">
                <div className={'sy-isl-act-title' + (resolved ? ' green' : '')} title={title}>{title}</div>
                <div className="sy-isl-act-sub" title={detail}>{detail}</div>
              </div>
              <div className="sy-isl-act-ctrls">
                {Array.isArray(task.actions) && task.actions[0] && task.actions[0].url && (
                  <button className="sy-isl-cbtn" title="Open result"
                    onClick={(e) => { e.stopPropagation(); openInHud(task.actions[0].url, task.actions[0].label || 'Watch result'); }}>
                    ↗
                  </button>
                )}
                {!resolved && !failed && (
                  <button className="sy-isl-cbtn danger" title="Cancel watch"
                    onClick={(e) => { e.stopPropagation(); onCancel && onCancel(task.id); }}>×</button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    );
  }
  window.SyrixIslandWatches = SyrixIslandWatches;

  /* ── Supercomputer, running in the background ───────────────────────────
   * Closing the workspace with a task still going does NOT stop it: the
   * session detaches and the agent keeps working. Until now that was invisible
   * — the user left the mode and had no idea whether anything was still happening,
   * which makes "close it and carry on in normal chat" feel like abandoning
   * the task rather than backgrounding it.
   *
   * Rendered straight from the `syrix_supercomputer` state key, which the loop
   * already republishes on every step, so there is nothing new to keep in sync.
   * Only shown while DETACHED — inside the workspace the Steps panel is right
   * there and a second copy in the island would just be noise.                */
  function SyrixIslandSupercomputer({ state, onOpen, onStop }) {
    const sc = state || {};
    if (!sc.active || !sc.detached) return null;
    const task = sc.task || {};
    const status = String(task.state || '').toLowerCase();
    const terminal = ['done', 'stopped', 'failed'].includes(status);
    const steps = Array.isArray(task.steps) ? task.steps : [];
    const idx = steps.findIndex((s) => s && s.status === 'running');
    const step = idx >= 0 ? steps[idx] : null;
    const waiting = Array.isArray(sc.pending_permission) && sc.pending_permission.length > 0;

    const title = String(task.goal || 'Supercomputer task').slice(0, 44);
    let detail;
    if (waiting) detail = 'Waiting for your permission — open it to answer';
    else if (terminal) detail = status === 'done' ? 'Finished' : 'Stopped';
    else if (step) {
      detail = (idx >= 0 ? `Step ${idx + 1} of ${steps.length} — ` : '')
        + String(step.detail || step.label || 'working').slice(0, 70);
    } else detail = String(sc.status && sc.status.label ? sc.status.label : 'Working…').slice(0, 70);

    return (
      <div className="sy-isl-activities sy-isl-super">
        <div className="sy-isl-sec-head">
          <span>SUPERCOMPUTER</span><b>{terminal ? 'DONE' : waiting ? 'NEEDS YOU' : 'RUNNING'}</b>
        </div>
        <div className="sy-isl-act sy-isl-act-super">
          <div className="sy-isl-act-badge">{terminal ? '✓' : waiting ? '!' : '◐'}</div>
          <div className="sy-isl-act-meta">
            <div className={'sy-isl-act-title' + (terminal && status === 'done' ? ' green' : '')}
                 title={title}>{title}</div>
            <div className="sy-isl-act-sub" title={detail}>{detail}</div>
          </div>
          <div className="sy-isl-act-ctrls">
            <button className="sy-isl-cbtn" title="Open Supercomputer"
              onClick={(e) => { e.stopPropagation(); onOpen && onOpen(); }}>↗</button>
            {!terminal && (
              <button className="sy-isl-cbtn danger" title="Stop this task"
                onClick={(e) => { e.stopPropagation(); onStop && onStop(); }}>×</button>
            )}
          </div>
        </div>
      </div>
    );
  }
  window.SyrixIslandSupercomputer = SyrixIslandSupercomputer;

  /* ── Rich cards (backend-pushed) ────────────────────────────────────────
   * sports: mini scoreboard, google-style — team | score | status
   * wiki:   photo on one side, summary on the other                      */
  function openInHud(url, title) {
    if (!url) return;
    try {
      const B = window.SyrixBridge;
      if (B && B.openHudApp) { B.openHudApp({ url, title: title || url }); return; }
      if (B && B.openAppInHud) { B.openAppInHud(url, { source: 'island_card' }); return; }
    } catch (_) {}
    try { window.open(url, '_blank', 'noopener'); } catch (_) {}
  }

  // Ranked field (F1 / golf) — one event, many competitors, no "vs".
  function LeaderboardRows({ g }) {
    const standings = Array.isArray(g.standings) ? g.standings : [];
    return (
      <div className="sy-isl-lb">
        {standings.map((s, i) => (
          <div className={'sy-isl-lb-row' + (s.winner ? ' win' : '')} key={i}>
            <span className="sy-isl-lb-pos">{s.pos || i + 1}</span>
            <span className="sy-isl-lb-name">{s.name}</span>
            {s.score ? <span className="sy-isl-lb-score">{s.score}</span> : null}
          </div>
        ))}
      </div>
    );
  }

  function SportsCard({ card }) {
    const games = Array.isArray(card.games) ? card.games : [];
    return (
      <div className="sy-isl-card sy-isl-card-sports">
        <div className="sy-isl-card-head">
          <span className="sy-isl-card-kind">
            {card.live && <span className="sy-isl-live-dot"/>}
            {String(card.league_label || card.league || 'SCOREBOARD').toUpperCase()}
          </span>
          <span className="sy-isl-card-src">ESPN</span>
        </div>
        {games.map((g, gi) => {
          const isLive = g.state === 'in';
          if (g.format === 'leaderboard') {
            return (
              <div className={'sy-isl-score-row sy-isl-lb-wrap' + (isLive ? ' live' : '')} key={gi}
                onClick={(e) => { e.stopPropagation(); openInHud(g.url || card.url, g.event); }}
                title="Open on ESPN">
                <div className="sy-isl-lb-head">{String(g.event || '').slice(0, 60)}</div>
                <LeaderboardRows g={g}/>
                <div className="sy-isl-score-detail">{String(g.detail || g.state || '').slice(0, 40)}</div>
              </div>
            );
          }
          const sides = Array.isArray(g.sides) ? g.sides : [];
          const a = sides[0] || {}, b = sides[1] || {};
          return (
            <div className={'sy-isl-score-row' + (isLive ? ' live' : '')} key={gi}
              onClick={(e) => { e.stopPropagation(); openInHud(g.url || card.url, g.event); }}
              title="Open on ESPN">
              <div className="sy-isl-score-side">
                {a.logo ? <img src={a.logo} alt="" onError={(e) => { e.currentTarget.style.display = 'none'; }}/> : null}
                <span className={'sy-isl-team' + (a.winner ? ' win' : '')}>{a.abbr || String(a.name || '').slice(0, 12)}</span>
              </div>
              <div className={'sy-isl-score-mid' + ((String(a.score || '').length > 5 || String(b.score || '').length > 5) ? ' long' : '')}>
                <b className={a.winner ? 'win' : ''}>{a.score || '–'}</b>
                <em>{isLive ? '·' : 'vs'}</em>
                <b className={b.winner ? 'win' : ''}>{b.score || '–'}</b>
              </div>
              <div className="sy-isl-score-side right">
                <span className={'sy-isl-team' + (b.winner ? ' win' : '')}>{b.abbr || String(b.name || '').slice(0, 12)}</span>
                {b.logo ? <img src={b.logo} alt="" onError={(e) => { e.currentTarget.style.display = 'none'; }}/> : null}
              </div>
              <div className="sy-isl-score-detail">{String(g.detail || g.state || '').slice(0, 40)}</div>
            </div>
          );
        })}
      </div>
    );
  }

  // Entity glyphs shown when a wiki card has no image — keeps every card
  // visually consistent (a "slot" is always there) instead of collapsing.
  const _ENTITY_GLYPH = { person: '◉', company: '⌂', place: '⛰', topic: '◈' };

  // Source-aware confidence rendering:
  //   high   → clean rich card (image + summary), no source noise.
  //   medium → card + a short "sources used" line (traceable, not loud).
  //   low    → "Unverified" card: badge, NO confident summary, sources or an
  //            explicit "No reliable structured sources found" line. Never a
  //            fabricated identity — transparency over guessing.
  function SourcesLine({ sources }) {
    const list = Array.isArray(sources) ? sources.filter((s) => s && s.label) : [];
    if (!list.length) return null;
    return (
      <div className="sy-isl-wiki-sources">
        <span className="sy-isl-src-lead">Sources</span>
        {list.slice(0, 4).map((s, i) => (
          s.url
            ? <a key={i} className="sy-isl-src-pill" href={s.url} title={s.url}
                onClick={(e) => { e.stopPropagation(); e.preventDefault(); openInHud(s.url, s.label); }}>{s.label}</a>
            : <span key={i} className="sy-isl-src-pill">{s.label}</span>
        ))}
      </div>
    );
  }

  function WikiCard({ card }) {
    const etype = String(card.entity_type || 'topic');
    const isLogo = !!card.is_logo;
    const level = String(card.confidence_level || 'high');
    const unverified = !!card.unverified || level === 'low';
    const hasSources = Array.isArray(card.sources) && card.sources.length > 0;
    const srcLabel = unverified ? 'UNVERIFIED'
      : etype === 'company' ? 'COMPANY' : etype === 'person' ? 'PERSON'
      : etype === 'place' ? 'PLACE' : 'WIKIPEDIA';
    return (
      <div className={'sy-isl-card sy-isl-card-wiki kind-' + etype
        + (unverified ? ' is-unverified' : '') + ' conf-' + level}>
        {card.image ? (
          <div className={'sy-isl-wiki-photo' + (isLogo ? ' is-logo' : '') + (etype === 'place' ? ' is-place' : '')}>
            <img src={card.image} alt={card.title}
              onError={(e) => { try { e.currentTarget.parentElement.style.display = 'none'; } catch (_) {} }}/>
          </div>
        ) : (
          <div className={'sy-isl-wiki-photo-empty kind-' + etype} aria-hidden="true">
            <span>{_ENTITY_GLYPH[etype] || _ENTITY_GLYPH.topic}</span>
          </div>
        )}
        <div className="sy-isl-wiki-body">
          <div className="sy-isl-wiki-title">
            {String(card.title || '').slice(0, 60)}
            {unverified && <span className="sy-isl-unverified-tag" title="Low confidence — identity not verified">unverified</span>}
          </div>
          {card.description ? <div className="sy-isl-wiki-desc">{String(card.description).slice(0, 90)}</div> : null}
          {unverified ? (
            // No confident summary for a low-confidence match.
            hasSources
              ? <div className="sy-isl-wiki-unverified-note">Possible match — not verified. Check the sources below.</div>
              : <div className="sy-isl-wiki-unverified-note">No reliable structured sources found.</div>
          ) : (
            <div className="sy-isl-wiki-extract">{String(card.extract || '')}</div>
          )}
          {(unverified || level === 'medium') && <SourcesLine sources={card.sources}/>}
          <div className="sy-isl-card-foot">
            <span className={'sy-isl-card-src' + (unverified ? ' warn' : '')}>{srcLabel}</span>
            {card.url ? (
              <button className="sy-isl-cbtn wide" title="Open the full article inside SYRIX"
                onClick={(e) => { e.stopPropagation(); openInHud(card.url, card.title); }}>
                OPEN ↗
              </button>
            ) : null}
          </div>
        </div>
      </div>
    );
  }

  function SyrixIslandCard({ card, onDismiss }) {
    if (!card || !card.kind) return null;
    return (
      <div className={'sy-isl-card-shell kind-' + card.kind} onClick={(e) => e.stopPropagation()}>
        <button className="sy-isl-card-x" title="Dismiss"
          onClick={(e) => { e.stopPropagation(); onDismiss && onDismiss(); }}>×</button>
        {card.kind === 'sports' ? <SportsCard card={card}/> : null}
        {card.kind === 'wiki' ? <WikiCard card={card}/> : null}
      </div>
    );
  }
  window.SyrixIslandCard = SyrixIslandCard;

  /* ── Card history — past sports/wiki cards, reopenable from the island's
   * own expanded panel only (no separate window). The backend keeps the last
   * dozen full card payloads under syrix_island_card_history; each row here
   * IS a complete card, so reopening just re-feeds it through SyrixIslandCard. */
  function fmtCardTimeAgo(ts) {
    const t = Number(ts || 0);
    if (!t) return '';
    const secs = Date.now() / 1000 - t;
    if (secs < 5) return 'now';
    if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
    if (secs < 86400) return Math.floor(secs / 3600) + 'h ago';
    return Math.floor(secs / 86400) + 'd ago';
  }

  function SyrixIslandCardHistory({ history, onReopen }) {
    const items = Array.isArray(history) ? history.slice().reverse() : [];
    if (!items.length) return null;
    return (
      <div className="sy-isl-history">
        <div className="sy-isl-sec-head">
          <span>CARD HISTORY</span>
          <b>{items.length} SAVED</b>
        </div>
        {items.map((h) => (
          <button className="sy-isl-hist-row" key={h.seq || h.ts}
            onClick={(e) => { e.stopPropagation(); onReopen && onReopen(h); }}
            title={'Reopen — ' + (h.title || h.league_label || h.kind)}>
            {h.image
              ? <img className={'sy-isl-hist-thumb' + (h.is_logo ? ' is-logo' : '')} src={h.image} alt=""
                  onError={(e) => { e.currentTarget.style.visibility = 'hidden'; }}/>
              : <span className="sy-isl-hist-ico">{h.kind === 'sports' ? '⚑' : (_ENTITY_GLYPH[h.entity_type] || '◈')}</span>}
            <span className="sy-isl-hist-meta">
              <span className="sy-isl-hist-title">
                {String(h.title || h.league_label || 'Card').slice(0, 30)}
              </span>
              <span className="sy-isl-hist-sub">{String(h.kind || '').toUpperCase()} · {fmtCardTimeAgo(h.ts)}</span>
            </span>
          </button>
        ))}
      </div>
    );
  }
  window.SyrixIslandCardHistory = SyrixIslandCardHistory;

  /* ── Styles — co-located so the hub stays drop-in ── */
  const css = `
  /* compact equalizer */
  .sy-isl-eq { display:flex; align-items:flex-end; gap:2px; height:11px; }
  .sy-isl-eq span { width:2.5px; border-radius:1px; background:#16d37a; height:3px;
    box-shadow:0 0 5px rgba(22,211,122,0.5); transition:height .3s ease; }
  .sy-isl-eq.on span { animation: syIslEq .7s ease-in-out infinite alternate; }
  .sy-isl-eq.on span:nth-child(1){ animation-duration:.62s; }
  .sy-isl-eq.on span:nth-child(2){ animation-duration:.44s; animation-delay:-.15s; }
  .sy-isl-eq.on span:nth-child(3){ animation-duration:.53s; animation-delay:-.3s; }
  .sy-isl-eq.on span:nth-child(4){ animation-duration:.41s; animation-delay:-.07s; }
  @keyframes syIslEq { from{height:3px} to{height:11px} }

  /* activities */
  .sy-isl-activities { display:flex; flex-direction:column; gap:8px; padding:2px 0 6px; }
  .sy-isl-sec-head { display:flex; align-items:center; justify-content:space-between; }
  .sy-isl-sec-head span { font:600 8px/1 'JetBrains Mono'; letter-spacing:.22em; color:rgba(140,180,220,.62); }
  .sy-isl-sec-head b { font:600 8px/1 'JetBrains Mono'; letter-spacing:.14em; color:rgba(var(--eg-accent-rgb),.75); }
  .sy-isl-act { display:flex; align-items:center; gap:10px; padding:7px 8px;
    background:rgba(255,255,255,0.035); border:1px solid rgba(140,200,255,0.09);
    border-radius:12px; min-width:0;
    animation: syIslRowIn .34s cubic-bezier(.23,1,.32,1) both; }
  .sy-isl-act + .sy-isl-act { margin-top:0; }
  @keyframes syIslRowIn { from{opacity:0; transform:translateY(-4px) scale(.985);} to{opacity:1; transform:none;} }
  .sy-isl-art { width:34px; height:34px; border-radius:8px; object-fit:cover; flex-shrink:0;
    box-shadow:0 4px 12px rgba(0,0,0,.45); }
  .sy-isl-art-disc { display:flex; align-items:center; justify-content:center;
    background:radial-gradient(circle at 50% 50%, rgba(var(--eg-accent-surface-rgb),.28), rgba(10,16,30,.9) 70%); }
  .sy-isl-art-disc span { width:10px; height:10px; border-radius:50%;
    border:2px solid rgba(var(--eg-accent-rgb),.8); border-top-color:transparent; }
  .sy-isl-art-disc span.spin { animation: syIslSpin 1.6s linear infinite; }
  @keyframes syIslSpin { to { transform: rotate(360deg); } }
  .sy-isl-act-badge { width:34px; height:34px; flex-shrink:0; display:flex; align-items:center;
    justify-content:center; font-size:15px; border-radius:8px;
    background:rgba(var(--eg-accent-surface-rgb),.10); color:rgba(var(--eg-accent-rgb),.9); }
  .sy-isl-act-watch .sy-isl-act-badge { background:rgba(34,211,238,.10); color:rgba(110,225,255,.95); }
  .sy-isl-act-watch .sy-isl-act-sub { letter-spacing:.04em; text-transform:none; }
  /* Supercomputer running in the background — the mode's own chrome palette,
     so a glance says which subsystem is working. */
  .sy-isl-super .sy-isl-sec-head span { color:rgba(207,216,230,.78); }
  .sy-isl-act-super .sy-isl-act-badge { background:rgba(207,216,230,.12); color:rgba(242,247,255,.95); }
  .sy-isl-act-super .sy-isl-act-sub { letter-spacing:.04em; text-transform:none; }
  .sy-isl-act-meta { flex:1; min-width:0; }
  .sy-isl-act-title { font:600 11px/1.25 Inter; color:#e9f3ff; white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; }
  .sy-isl-act-title.mono { font:700 13px/1.2 'JetBrains Mono'; letter-spacing:.05em; }
  .sy-isl-act-title.gold { color:rgb(var(--eg-accent-rgb)); text-shadow:0 0 8px rgba(var(--eg-accent-rgb),.4); }
  .sy-isl-act-title.green { color:#16d37a; text-shadow:0 0 8px rgba(22,211,122,.35); }
  .sy-isl-act-sub { font:500 8px/1.4 'JetBrains Mono'; letter-spacing:.14em;
    color:rgba(150,180,215,.6); text-transform:uppercase; white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; margin-top:2px; }
  .sy-isl-seek { height:2.5px; border-radius:2px; background:rgba(120,150,190,.18);
    margin-top:5px; overflow:hidden; }
  .sy-isl-seek-fill { height:100%; border-radius:2px;
    background:linear-gradient(90deg, rgba(var(--eg-accent-surface-rgb),.75), rgba(var(--eg-accent-surface-rgb),.9));
    transition:width .9s linear; }
  .sy-isl-act-ctrls { display:flex; align-items:center; gap:5px; flex-shrink:0; }
  .sy-isl-cbtn { display:inline-flex; align-items:center; justify-content:center;
    width:26px; height:26px; border-radius:8px; cursor:pointer;
    background:rgba(255,255,255,.05); border:1px solid rgba(140,200,255,.12);
    color:rgba(218,236,255,.85); font:700 9px 'JetBrains Mono'; letter-spacing:.1em;
    transition:background .15s, border-color .15s, transform .12s, color .15s; }
  .sy-isl-cbtn:hover { background:rgba(var(--eg-accent-surface-rgb),.14); border-color:rgba(var(--eg-accent-rgb),.4); color:rgb(var(--eg-accent-bright-rgb)); }
  .sy-isl-cbtn:active { transform:scale(.92); }
  .sy-isl-cbtn.main { width:30px; height:30px; background:rgba(var(--eg-accent-surface-rgb),.14);
    border-color:rgba(var(--eg-accent-rgb),.34); color:rgb(var(--eg-accent-bright-rgb)); }
  .sy-isl-cbtn.danger:hover { background:rgba(255,79,110,.14); border-color:rgba(255,79,110,.4); color:#ffaebd; }
  .sy-isl-cbtn.wide { width:auto; padding:0 9px; }

  /* card shell */
  .sy-isl-card-shell { position:relative; padding:2px 0 8px;
    animation: syIslCardIn .46s cubic-bezier(.23,1.12,.32,1) both; }
  @keyframes syIslCardIn {
    from { opacity:0; transform:translateY(-7px) scale(.97); filter:blur(5px); }
    to   { opacity:1; transform:none; filter:blur(0); } }
  .sy-isl-card-x { position:absolute; top:0; right:-4px; z-index:3;
    width:20px; height:20px; border-radius:7px; cursor:pointer;
    display:flex; align-items:center; justify-content:center;
    background:rgba(255,255,255,.05); border:1px solid rgba(140,200,255,.14);
    color:rgba(200,220,245,.75); font:600 13px/1 Inter;
    transition:background .15s, color .15s; }
  .sy-isl-card-x:hover { background:rgba(255,79,110,.16); color:#ffb9c6; border-color:rgba(255,79,110,.4); }
  .sy-isl-card-head { display:flex; align-items:center; justify-content:space-between;
    margin-bottom:7px; padding-right:22px; }
  .sy-isl-card-kind { display:inline-flex; align-items:center; gap:6px;
    font:700 8px/1 'JetBrains Mono'; letter-spacing:.2em; color:rgba(var(--eg-accent-rgb),.85); }
  .sy-isl-card-src { font:600 7px/1 'JetBrains Mono'; letter-spacing:.2em; color:rgba(140,180,220,.42); }
  .sy-isl-live-dot { width:6px; height:6px; border-radius:50%; background:#ff4f6e;
    box-shadow:0 0 7px rgba(255,79,110,.85); animation: syIslLive 1.1s ease-in-out infinite; }
  @keyframes syIslLive { 0%,100%{opacity:1} 50%{opacity:.35} }

  /* scoreboard rows */
  .sy-isl-card-sports { width:322px; max-width:74vw; }
  .sy-isl-score-row { display:grid; cursor:pointer;
    grid-template-columns:1fr auto 1fr; grid-template-areas: 'l m r' 'd d d';
    align-items:center; column-gap:8px; padding:7px 9px; border-radius:11px;
    background:rgba(255,255,255,.03); border:1px solid rgba(140,200,255,.08);
    transition:background .16s, border-color .16s; }
  .sy-isl-score-row:hover { background:rgba(var(--eg-accent-surface-rgb),.07); border-color:rgba(var(--eg-accent-rgb),.26); }
  .sy-isl-score-row + .sy-isl-score-row { margin-top:6px; }
  .sy-isl-score-row.live { border-color:rgba(255,79,110,.22); }
  .sy-isl-score-side { grid-area:l; display:flex; align-items:center; gap:6px; min-width:0; }
  .sy-isl-score-side.right { grid-area:r; justify-content:flex-end; }
  .sy-isl-score-side img { width:18px; height:18px; object-fit:contain; flex-shrink:0; }
  .sy-isl-team { font:600 10px/1.2 'JetBrains Mono'; letter-spacing:.06em;
    color:rgba(220,238,255,.82); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .sy-isl-team.win { color:rgb(var(--eg-accent-bright-rgb)); }
  .sy-isl-score-mid { grid-area:m; display:flex; align-items:baseline; gap:7px; }
  .sy-isl-score-mid b { font:700 15px/1 'JetBrains Mono'; color:#e9f3ff; white-space:nowrap; }
  .sy-isl-score-mid.long b { font-size:10.5px; letter-spacing:-0.02em; }
  .sy-isl-score-mid b.win { color:rgb(var(--eg-accent-rgb)); text-shadow:0 0 9px rgba(var(--eg-accent-rgb),.4); }
  .sy-isl-score-mid em { font:600 8px/1 'JetBrains Mono'; color:rgba(140,180,220,.5); font-style:normal; }
  .sy-isl-score-detail { grid-area:d; margin-top:4px; text-align:center;
    font:500 8px/1.3 'JetBrains Mono'; letter-spacing:.12em;
    color:rgba(150,185,220,.55); text-transform:uppercase; }
  .sy-isl-score-row.live .sy-isl-score-detail { color:rgba(255,130,152,.8); }

  /* leaderboard rows (F1 / golf) — nested inside a .sy-isl-score-row shell so
     it inherits the same hover/live border treatment as a match row. */
  .sy-isl-lb-wrap { display:block; grid-template-columns:none; padding:8px 10px; }
  .sy-isl-lb-head { font:700 9px/1.3 'JetBrains Mono'; letter-spacing:.1em;
    color:rgba(var(--eg-accent-rgb),.85); text-transform:uppercase; margin-bottom:6px;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .sy-isl-lb { display:flex; flex-direction:column; gap:3px; }
  .sy-isl-lb-row { display:flex; align-items:center; gap:8px; padding:3px 4px; border-radius:7px;
    animation: syIslRowIn .3s cubic-bezier(.23,1,.32,1) both; }
  .sy-isl-lb-row.win { background:rgba(var(--eg-accent-surface-rgb),.09); }
  .sy-isl-lb-pos { width:16px; flex-shrink:0; text-align:center;
    font:700 10px/1 'JetBrains Mono'; color:rgba(150,185,220,.6); }
  .sy-isl-lb-row.win .sy-isl-lb-pos { color:rgb(var(--eg-accent-rgb)); }
  .sy-isl-lb-name { flex:1; min-width:0; font:600 10.5px/1.2 'JetBrains Mono';
    color:rgba(220,238,255,.85); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .sy-isl-lb-row.win .sy-isl-lb-name { color:rgb(var(--eg-accent-bright-rgb)); }
  .sy-isl-lb-score { flex-shrink:0; font:600 9px/1 'JetBrains Mono'; color:rgba(150,185,220,.55); }

  /* wiki card — entity-aware layout (person / company / place / topic) */
  .sy-isl-card-wiki { display:flex; gap:11px; width:352px; max-width:76vw; padding-right:16px; }
  .sy-isl-wiki-photo { flex-shrink:0; width:84px; }
  .sy-isl-wiki-photo img { width:84px; height:104px; object-fit:cover; border-radius:11px;
    box-shadow:0 8px 22px rgba(0,0,0,.5), inset 0 0 0 1px rgba(255,255,255,.08); }
  /* Company logos are never cropped like a portrait — contain on a light
     tile so transparent-background / dark-text marks stay legible. */
  .sy-isl-wiki-photo.is-logo { width:96px; }
  .sy-isl-wiki-photo.is-logo img { width:96px; height:96px; object-fit:contain; border-radius:14px;
    background:rgba(255,255,255,.94); padding:10px;
    box-shadow:0 8px 20px rgba(0,0,0,.4), inset 0 0 0 1px rgba(0,0,0,.06); }
  .sy-isl-wiki-photo.is-place { width:104px; }
  .sy-isl-wiki-photo.is-place img { width:104px; height:84px; border-radius:11px; object-fit:cover; }
  .sy-isl-wiki-photo-empty { display:flex; align-items:center; justify-content:center; flex-shrink:0;
    width:84px; height:104px; border-radius:11px; font-size:26px; color:rgba(var(--eg-accent-rgb),.55);
    background:radial-gradient(circle at 40% 30%, rgba(var(--eg-accent-surface-rgb),.22), rgba(10,16,30,.9) 75%);
    box-shadow:inset 0 0 0 1px rgba(255,255,255,.08); }
  .sy-isl-wiki-photo-empty.kind-company { color:rgba(140,200,255,.6);
    background:radial-gradient(circle at 40% 30%, rgba(140,200,255,.18), rgba(10,16,30,.9) 75%); }
  .sy-isl-wiki-photo-empty.kind-place { color:rgba(140,220,180,.6);
    background:radial-gradient(circle at 40% 30%, rgba(120,220,170,.18), rgba(10,16,30,.9) 75%); }
  .sy-isl-wiki-body { flex:1; min-width:0; }
  .sy-isl-wiki-title { font:700 13px/1.25 Inter; color:#f2f7ff; }
  .sy-isl-wiki-desc { font:600 8px/1.4 'JetBrains Mono'; letter-spacing:.13em;
    color:rgba(var(--eg-accent-rgb),.8); text-transform:uppercase; margin-top:3px; }
  .sy-isl-wiki-extract { font:400 10.5px/1.5 Inter; color:rgba(205,222,244,.86);
    margin-top:6px; max-height:118px; overflow-y:auto; padding-right:4px; }
  .sy-isl-wiki-extract::-webkit-scrollbar { width:3px; }
  .sy-isl-wiki-extract::-webkit-scrollbar-thumb { background:rgba(var(--eg-accent-surface-rgb),.3); border-radius:2px; }
  .sy-isl-card-foot { display:flex; align-items:center; justify-content:space-between; margin-top:8px; }
  .sy-isl-card-src.warn { color:rgba(var(--eg-accent-rgb),.95); }

  /* source-aware confidence: sources line (medium/unverified) */
  .sy-isl-wiki-sources { display:flex; flex-wrap:wrap; align-items:center; gap:5px; margin-top:7px; }
  .sy-isl-src-lead { font:600 7.5px/1 'JetBrains Mono'; letter-spacing:.16em; text-transform:uppercase;
    color:rgba(170,190,214,.6); margin-right:1px; }
  .sy-isl-src-pill { font:600 9px/1 Inter; color:rgba(205,222,244,.9); text-decoration:none;
    padding:3px 8px; border-radius:999px; background:rgba(140,200,255,.1);
    border:1px solid rgba(140,200,255,.2); transition:background .15s, border-color .15s; cursor:pointer; }
  .sy-isl-src-pill:hover { background:rgba(140,200,255,.2); border-color:rgba(140,200,255,.42); }

  /* unverified (low-confidence) entity card — muted, transparent, no guess */
  .sy-isl-card-wiki.is-unverified { opacity:.97; }
  .sy-isl-card-wiki.is-unverified .sy-isl-wiki-photo,
  .sy-isl-card-wiki.is-unverified .sy-isl-wiki-photo-empty { filter:grayscale(.35) brightness(.92); }
  .sy-isl-unverified-tag { display:inline-block; margin-left:7px; vertical-align:middle;
    font:700 7.5px/1 'JetBrains Mono'; letter-spacing:.1em; text-transform:uppercase;
    color:#0a1020; background:rgba(var(--eg-accent-surface-rgb),.92); padding:2px 6px; border-radius:5px; }
  .sy-isl-wiki-unverified-note { font:400 10.5px/1.5 Inter; color:rgba(var(--eg-accent-rgb),.9);
    margin-top:6px; font-style:italic; }

  /* card history — reopenable rows inside the island's own expanded panel */
  .sy-isl-history { display:flex; flex-direction:column; gap:6px; padding:2px 0 6px; }
  .sy-isl-hist-row { display:flex; align-items:center; gap:9px; padding:6px 8px; width:100%;
    background:rgba(255,255,255,.03); border:1px solid rgba(140,200,255,.08); border-radius:11px;
    cursor:pointer; text-align:left; font:inherit; color:inherit;
    transition:background .15s, border-color .15s;
    animation: syIslRowIn .3s cubic-bezier(.23,1,.32,1) both; }
  .sy-isl-hist-row:hover { background:rgba(var(--eg-accent-surface-rgb),.08); border-color:rgba(var(--eg-accent-rgb),.28); }
  .sy-isl-hist-thumb { width:26px; height:26px; border-radius:7px; object-fit:cover; flex-shrink:0; }
  .sy-isl-hist-thumb.is-logo { object-fit:contain; background:rgba(255,255,255,.92); padding:3px; }
  .sy-isl-hist-ico { width:26px; height:26px; flex-shrink:0; display:flex; align-items:center;
    justify-content:center; border-radius:7px; background:rgba(var(--eg-accent-surface-rgb),.1);
    color:rgba(var(--eg-accent-rgb),.85); font-size:12px; }
  .sy-isl-hist-meta { flex:1; min-width:0; display:flex; flex-direction:column; gap:1px; }
  .sy-isl-hist-title { font:600 10.5px/1.2 Inter; color:#e9f3ff; white-space:nowrap;
    overflow:hidden; text-overflow:ellipsis; }
  .sy-isl-hist-sub { font:500 7.5px/1.3 'JetBrains Mono'; letter-spacing:.1em;
    color:rgba(150,180,215,.55); text-transform:uppercase; }

  @media (prefers-reduced-motion: reduce) {
    .sy-isl-eq.on span, .sy-isl-art-disc span.spin, .sy-isl-live-dot { animation:none; }
    .sy-isl-act, .sy-isl-card-shell, .sy-isl-lb-row, .sy-isl-hist-row { animation:none; }
  }
  `;
  try {
    const tag = document.createElement('style');
    tag.setAttribute('data-syrix', 'island-hub');
    tag.textContent = css;
    document.head.appendChild(tag);
  } catch (_) {}
})();
