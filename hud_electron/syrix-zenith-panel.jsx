/* SYRIX ZENITH PANEL — live sub-agent deployment view (rebuilt 2026-07-04).
 *
 * Renders the flagship model's mission in real time: which sub-agents are
 * deployed, what each is doing right now, the sources being read, how many
 * tools are active, and an honest elapsed clock.
 *
 * TRUTH RULE: every pixel of motion here is driven by a REAL backend event —
 * no fake timers, no synthetic progress. Events arrive on three real streams,
 * all rid-scoped to the active mission and merged/deduped by `zid`:
 *   1. WS `zenith_agent_event`      → snap.zenith_events   (low latency)
 *   2. activity_feed kind "zenith"  → hud_state.json       (survives WS-down)
 *   3. activity_feed ATLAS kinds    → source / retrieval / pack_built /
 *      pack_enriched / zenith_run / merge_done  (the real retrieval + the
 *      real pages being read, so "sources" are the actual URLs, not empty)
 *
 * Purple identity: ZENITH is the deep flagship (violet); SPARK is the fast
 * engine (amber). SPARK turns never mount this component.
 */

/* Frameless liquid-glass redesign (2026-07-11) — same visual language as the
 * SPARK thinking pulse (SyrixSparkPulse in syrix-activity-feed.jsx): no card,
 * no border-boxed rectangle, small translucent blurred pill capsules sitting
 * directly on the canvas. Violet stays ZENITH's identity color (SPARK is
 * amber) so the two are still visually distinguishable turn-to-turn. The one
 * thing ZENITH's version must do that SPARK's doesn't: say plainly how many
 * sub-agents are deployed/working right now — that's the zp-pill-main text
 * below, always driven by the real agent roster, never a fake count. */
const ZP_CSS = `
.zp-frameless { display: flex; flex-direction: column; gap: 7px; margin: 6px 0 2px; font-family: 'Inter', system-ui, sans-serif; max-width: 100%; animation: zpFade .3s ease; }
@keyframes zpFade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
.zp-row { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; }
.zp-pill {
  display: inline-flex; align-items: center; gap: 7px; padding: 4px 12px 4px 4px; border-radius: 999px;
  background: linear-gradient(155deg, rgba(199,180,255,.09), rgba(199,180,255,.025));
  border: 1px solid rgba(168,130,255,0.26);
  -webkit-backdrop-filter: blur(14px) saturate(1.5); backdrop-filter: blur(14px) saturate(1.5);
  box-shadow: inset 0 1px 0 rgba(224,214,255,.12), 0 6px 16px -9px rgba(0,0,0,.55);
  animation: zpPillIn .32s cubic-bezier(0.32,0.72,0,1) both;
}
@keyframes zpPillIn { from { opacity: 0; transform: translateY(6px) scale(.97); } to { opacity: 1; transform: none; } }
.zp-pill-ico {
  width: 20px; height: 20px; flex-shrink: 0; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: rgba(168,130,255,.16); color: #cbb8ff;
}
.zp-pill.is-active .zp-pill-ico { animation: zpIcoBreath 2.2s ease-in-out infinite; }
@keyframes zpIcoBreath { 0%,100% { opacity: .68; } 50% { opacity: 1; } }
.zp-pill-txt { font: 500 11.5px 'Inter', sans-serif; letter-spacing: .01em; color: rgba(238,232,255,.92); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.zp-pill-sub .zp-pill-txt { color: rgba(210,200,240,.72); font-size: 11px; }
.zp-pill.status-error .zp-pill-ico { background: rgba(255,110,110,.16); color: #ff9d9d; }
.zp-pill.status-error .zp-pill-txt { color: rgba(255,180,180,.9); }
.zp-pill.status-done .zp-pill-ico { background: rgba(143,227,168,.16); color: #8fe3a8; }
.zp-pill.status-done .zp-pill-txt { color: rgba(200,236,210,.82); }
.zp-agent-verb { font: 500 10px 'Inter', sans-serif; color: rgba(203,184,255,.62); margin-left: 1px; }
.zp-note { font: 500 11px/1.5 'Inter', sans-serif; color: rgba(255,190,150,.92); padding: 1px 2px; }
.zp-stage-line { font: 500 11px 'Inter', sans-serif; color: rgba(210,200,240,.68); padding: 0 2px; }
.zp-chips { display: inline-flex; align-items: center; flex-wrap: wrap; gap: 4px; }
.zp-chip {
  display: inline-flex; align-items: center; gap: 3px; padding: 2px 9px; border-radius: 999px;
  background: rgba(168,130,255,.055); border: 1px solid rgba(168,130,255,.22);
  color: rgba(220,210,245,.78); font: 500 10px 'Inter', sans-serif; cursor: pointer;
  transition: filter .14s ease, color .14s ease;
}
.zp-chip:hover { filter: brightness(1.25); color: #cbb8ff; }
@media (prefers-reduced-motion: reduce) {
  .zp-frameless, .zp-pill, .zp-pill.is-active .zp-pill-ico { animation: none !important; }
}
`;

function _zpEnsureStyles() {
  try {
    if (document.getElementById('zp-styles')) return;
    const el = document.createElement('style');
    el.id = 'zp-styles';
    el.textContent = ZP_CSS;
    document.head.appendChild(el);
  } catch (_) {}
}

/* ATLAS activity-feed kinds the panel folds in alongside zenith agent events,
 * so the retrieval phase and the real sources being read are visible live. */
const _ZP_ATLAS_KINDS = new Set([
  'source', 'retrieval', 'filter_rank', 'verify', 'pack_built', 'pack_enriched',
  'zenith_run', 'merge_done',
]);

/* Normalize one raw event (WS or activity-feed shape) into the panel shape.
 * Accepts zenith agent events AND the ATLAS pipeline kinds above. */
function _zpNorm(ev) {
  if (!ev || typeof ev !== 'object') return null;
  const kind = String(ev.kind || '');
  const type = String(ev.type || '');
  const isZenith = kind === 'zenith' || type === 'zenith_agent_event';
  const isAtlas = _ZP_ATLAS_KINDS.has(kind);
  if (!isZenith && !isAtlas) return null;
  return {
    kind: kind || (isZenith ? 'zenith' : ''),
    zid: String(ev.zid || ''),
    id: String(ev.id || ''),
    ts: Number(ev.ts || 0),
    rid: String(ev.request_id || ev.rid || ''),
    agentId: String(ev.agent_id || ''),
    agentName: String(ev.agent_name || ''),
    agentRole: String(ev.agent_role || ''),
    agentStatus: String(ev.agent_status || ''),
    label: String(ev.label || ''),
    query: String(ev.query || ''),
    tool: String(ev.tool || ''),
    toolsInUse: Number(ev.tools_in_use || 0),
    agentsActive: Number(ev.agents_active || 0),
    progress: Number(ev.progress || 0),
    stage: String(ev.stage || ''),
    detail: String(ev.detail || ''),
    sourceUrl: String(ev.source_url || ''),
    sourceTitle: String(ev.source_title || ''),
  };
}

const _ZP_ACTIVE = new Set(['spawned', 'searching', 'reading', 'reporting']);
const _ZP_VERBS = {
  spawned: 'deployed', searching: 'searching', reading: 'reading',
  reporting: 'writing up', done: 'done', error: 'failed',
};
// Friendly fallback names for the full sub-agent roster (agent_name usually
// already carries these; this is the safety net).
const _ZP_ROLE_NAMES = {
  planner: 'Query Analyst', contradiction: 'Contradiction Scan',
  risk: 'Risk Analysis', comparison: 'Comparison',
  quant: 'Quantitative Estimate', tradeoff: 'Tradeoff Analysis',
  verify: 'Verification Agent', synthesis: 'Writing Agent', critic: 'Review Agent',
  research: 'Research Agent',
};

/* Clean-mode friendly maps (2026-07-10) — internal tool/stage tokens never
 * reach the user. Gated on window.SyrixCleanMode so the dev trace
 * (localStorage syrix_dev_trace='1') restores the raw tokens instantly. */
const _ZP_TOOL_FRIENDLY = {
  free_web_search: 'Web search', web_search: 'Web search',
  self_critique: 'Self review', claim_matrix: 'Claim cross-check',
  query_analysis: 'Question analysis', synthesis: 'Writing',
  contradiction: 'Contradiction scan', risk: 'Risk analysis',
  comparison: 'Comparison', quant: 'Quantitative estimate',
  tradeoff: 'Tradeoff analysis',
  arxiv: 'arXiv', stackexchange: 'Stack Exchange', dictionary: 'Dictionary',
  github: 'GitHub', reddit: 'Reddit',
};
function _zpToolName(t) {
  const k = String(t || '').toLowerCase();
  if (_ZP_TOOL_FRIENDLY[k]) return _ZP_TOOL_FRIENDLY[k];
  const s = k.replace(/_/g, ' ').trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}
const _ZP_STAGE_FRIENDLY = {
  understand: 'Understanding the question',
  deploy: 'Deploying research agents',
  verify: 'Verifying findings',
  synthesize: 'Composing the answer',
  done: 'Research complete',
};
// ATLAS pipeline rows carry raw labels ("ddgs: 8 results · 412ms") — in clean
// mode the stage line + ticker show these stage names instead.
const _ZP_KIND_FRIENDLY = {
  retrieval: 'Searching the web', filter_rank: 'Selecting the best information',
  verify: 'Cross-checking facts', pack_built: 'Information found',
  pack_enriched: 'Reading key pages', zenith_run: 'Deep analysis engaged',
  merge_done: 'Research complete', source: 'Reading sources',
};
function _zpCleanOn() {
  try { return !window.SyrixCleanMode || window.SyrixCleanMode(); }
  catch (_) { return true; }
}
// Friendly one-liner for a normalized event (stage token wins, then atlas
// kind, then the backend label — zenith agent labels are already human).
function _zpFriendlyLine(e) {
  if (!e) return '';
  if (e.stage && _ZP_STAGE_FRIENDLY[e.stage]) return _ZP_STAGE_FRIENDLY[e.stage];
  if (e.kind && _ZP_KIND_FRIENDLY[e.kind]) return _ZP_KIND_FRIENDLY[e.kind];
  return e.label;
}

function _zpOpen(url) {
  if (!url) return;
  try {
    if (window.SyrixBridge && typeof window.SyrixBridge.openExternal === 'function') {
      window.SyrixBridge.openExternal(url);
      return;
    }
  } catch (_) {}
  try { window.open(url, '_blank', 'noopener'); } catch (_) {}
}

function _zpDomain(url) {
  try { return String(url).replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0]; }
  catch (_) { return ''; }
}

// Liquid-glass icon glyph, shared with SPARK via window.SyrixLiquidIcon
// (defined in syrix-activity-feed.jsx, loaded earlier — see index.html).
function _zpIcon(kind) {
  if (typeof window === 'undefined' || typeof window.SyrixLiquidIcon !== 'function') return null;
  return React.createElement(window.SyrixLiquidIcon, { kind: kind });
}
const _ZP_STATUS_ICON = {
  spawned: 'bolt', searching: 'search', reading: 'page', reporting: 'write', done: 'done', error: 'warn',
};

function SyrixZenithPanel(props) {
  // 2026-07-06 — freeze the mission start at MOUNT time (the caller's
  // timestamp when it has one, otherwise "now"). This is what lets a
  // brand-new mission instantly discard the previous one's cards instead of
  // showing them until its own first event arrives — see the sinceTs-first
  // filter below. A plain `Number(props.sinceTs || 0)` recomputed every
  // render would recompute to 0 whenever a caller mounts this panel without
  // a sinceTs prop, disabling the filter entirely for that mount.
  const sinceTsRef = React.useRef(0);
  if (!sinceTsRef.current) {
    sinceTsRef.current = Number((props && props.sinceTs) || 0) || (Date.now() / 1000);
  }
  const sinceTs = sinceTsRef.current;
  _zpEnsureStyles();

  // Transport 1 — WS events pushed by the bridge (already rid-scoped there).
  const [wsEvents, setWsEvents] = React.useState(() => {
    try {
      const s = window.SyrixBridge && window.SyrixBridge.getSnapshot && window.SyrixBridge.getSnapshot();
      return (s && Array.isArray(s.zenith_events)) ? s.zenith_events : [];
    } catch (_) { return []; }
  });
  // 2026-07-10 (product review C3) — connection truth. With the backend dead
  // this panel used to animate "Deploying sub-agents…" forever over a void.
  // Track the bridge's own status so the mission visibly pauses when the
  // backend is gone, instead of performing work that is not happening.
  const [connStatus, setConnStatus] = React.useState(() => {
    try {
      const s = window.SyrixBridge && window.SyrixBridge.getSnapshot && window.SyrixBridge.getSnapshot();
      return String((s && s.status) || 'online');
    } catch (_) { return 'online'; }
  });
  React.useEffect(() => {
    if (!window.SyrixBridge || typeof window.SyrixBridge.subscribe !== 'function') return undefined;
    const unsub = window.SyrixBridge.subscribe((s) => {
      try {
        setWsEvents(Array.isArray(s.zenith_events) ? s.zenith_events : []);
        setConnStatus(String((s && s.status) || 'online'));
      } catch (_) {}
    });
    return () => { try { typeof unsub === 'function' && unsub(); } catch (_) {} };
  }, []);
  const offline = (props && props.offline === true)
    || connStatus === 'offline' || connStatus === 'reconnecting';

  // Transport 2 + 3 — activity feed (hud_state.json poll; survives WS-down).
  const feedHook = (typeof window !== 'undefined' && typeof window.SyrixActivityFeedHook === 'function')
    ? window.SyrixActivityFeedHook : null;
  const feed = feedHook ? feedHook() : { events: [] };
  const feedEvents = Array.isArray(feed.events) ? feed.events : [];

  // Merge → normalize → rid-scope (newest tagged rid is the active mission) →
  // dedupe by zid. rid is a tighter scope than a time window, so once we know
  // the mission rid we keep ALL of its events (early retrieval + late analysis).
  const events = React.useMemo(() => {
    const all = [];
    for (const ev of feedEvents) { const n = _zpNorm(ev); if (n) all.push(n); }
    for (const ev of wsEvents) { const n = _zpNorm(ev); if (n) all.push(n); }
    if (!all.length) return [];
    all.sort((a, b) => a.ts - b.ts);
    // 2026-07-06 — a fresh mission must never inherit the previous one's
    // cards while its own first event is still in flight. Restrict to events
    // at/after this mount's sinceTs BEFORE picking the active rid, so a
    // stale rid from an already-finished mission can never win the scope
    // below (that was the "old thinking panel remains visible" bug: the
    // rid of the last-finished mission kept winning until the new mission's
    // first event finally arrived and overwrote it).
    const pool = all.filter((e) => e.ts >= sinceTs - 0.5);
    if (!pool.length) return [];
    let rid = '';
    for (let i = pool.length - 1; i >= 0; i--) { if (pool[i].rid) { rid = pool[i].rid; break; } }
    const scoped = rid ? pool.filter((e) => e.rid === rid) : pool;
    const seen = new Set();
    const out = [];
    for (const e of scoped) {
      const key = e.zid || (e.rid + '|' + e.agentId + '|' + e.agentStatus + '|' + e.kind + '|' + e.label + '|' + e.ts.toFixed(3));
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(e);
    }
    return out;
  }, [feedEvents, wsEvents, sinceTs]);

  // Agent cards — one per agent_id (mission/stage + non-agent atlas rows
  // excluded), latest status wins.
  const agents = React.useMemo(() => {
    const byId = new Map();
    for (const e of events) {
      if (!e.agentId || e.agentId === 'mission') continue;
      if (e.agentRole === 'planner' && !e.agentId.startsWith('analyst')) continue;
      let card = byId.get(e.agentId);
      if (!card) {
        card = { id: e.agentId, name: e.agentName || _ZP_ROLE_NAMES[e.agentRole] || e.agentId,
                 role: e.agentRole, firstTs: e.ts, tools: new Set() };
        byId.set(e.agentId, card);
      }
      card.status = e.agentStatus || card.status || 'spawned';
      card.label = e.label || card.label || '';
      card.query = e.query || card.query || '';
      if (e.agentName) card.name = e.agentName;
      if (e.tool) card.tools.add(e.tool);
      card.lastTs = e.ts;
    }
    return Array.from(byId.values()).sort((a, b) => a.firstTs - b.firstTs);
  }, [events]);

  // Real sources being read — deduped URLs from source events (ATLAS reader
  // emits one KIND_SOURCE per page, plus any zenith source events).
  const sources = React.useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const e of events) {
      if (!e.sourceUrl) continue;
      const key = e.sourceUrl.split('#')[0].replace(/\/$/, '').toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ url: e.sourceUrl, title: e.sourceTitle || _zpDomain(e.sourceUrl), ts: e.ts });
    }
    return out;
  }, [events]);

  const latest = events.length ? events[events.length - 1] : null;
  // Stage line — prefer an explicit mission stage; otherwise the latest labelled
  // event (keeps the top line live during the retrieval/enrich phase).
  const latestStage = React.useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) { if (events[i].stage) return events[i]; }
    for (let i = events.length - 1; i >= 0; i--) { if (events[i].label) return events[i]; }
    return null;
  }, [events]);

  // Terminal marker → freeze the clock and stop the shimmer.
  const doneEv = React.useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.stage === 'done' || e.kind === 'merge_done') return e;
    }
    return null;
  }, [events]);
  const working = !offline && !(props && props.working === false) && !doneEv;

  const activeCount = agents.filter((a) => _ZP_ACTIVE.has(a.status)).length;
  const toolsInUse = latest ? latest.toolsInUse : 0;

  // Real elapsed clock: from the mission start (user-send / deep.ts) to now, or
  // frozen at the terminal event's timestamp.
  const [nowS, setNowS] = React.useState(() => Date.now() / 1000);
  React.useEffect(() => {
    if (!working) return undefined;
    const id = setInterval(() => setNowS(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [working]);
  const missionStart = sinceTs || (events.length ? events[0].ts : 0);
  const endS = doneEv ? doneEv.ts : nowS;
  const elapsedS = missionStart ? Math.max(0, Math.floor(endS - missionStart)) : 0;

  // 2026-07-10 (product review C3) — stall watchdog. A mission that has gone
  // quiet for minutes while "working" is indistinguishable from a hang; say
  // so instead of shimmering forever.
  const lastEventTs = events.length ? events[events.length - 1].ts : 0;
  const stalled = working && (
    (lastEventTs > 0 && (nowS - lastEventTs) > 120)
    || (!lastEventTs && missionStart > 0 && (nowS - missionStart) > 75)
  );

  // Ticker — the last few real events with a human label (agent steps, mission
  // stage transitions, and pages read).
  const ticker = React.useMemo(() => {
    const rows = events.filter((e) => (e.agentId !== 'mission' && e.label) || e.stage);
    return rows.slice(-6);
  }, [events]);

  const clean = _zpCleanOn();
  const agentChip = (working && activeCount > 0)
    ? `${activeCount} agent${activeCount === 1 ? '' : 's'} working`
    : `${agents.length || (working ? '…' : '0')} agent${agents.length === 1 ? '' : 's'}`;
  const stageText = latestStage ? (clean ? _zpFriendlyLine(latestStage) : latestStage.label) : '';
  return (
    <div className="zp-frameless" aria-live="polite">
      {/* Main status pill — always states the real sub-agent count, never a
          fake/synthetic number (TRUTH RULE, see file header). */}
      <div className="zp-row">
        <div className={'zp-pill zp-pill-main'
          + (working && !stalled && !offline ? ' is-active' : '')
          + (offline || stalled ? ' status-error' : '')}>
          <span className="zp-pill-ico">{_zpIcon('layers')}</span>
          <span className="zp-pill-txt">
            {offline ? 'Zenith · offline' : stalled ? 'Zenith · stalled' : `Zenith · ${agentChip}`}
          </span>
        </div>
        {!offline && elapsedS >= 2 && (
          <div className="zp-pill zp-pill-sub"><span className="zp-pill-txt">{elapsedS}s</span></div>
        )}
        {sources.length > 0 && (
          <div className="zp-pill zp-pill-sub"><span className="zp-pill-txt">{sources.length} source{sources.length === 1 ? '' : 's'}</span></div>
        )}
        {toolsInUse > 0 && (
          <div className="zp-pill zp-pill-sub"><span className="zp-pill-txt">{toolsInUse} tool{toolsInUse === 1 ? '' : 's'}</span></div>
        )}
      </div>

      {offline && (
        <div className="zp-note">
          SYRIX is offline — this research is paused and will resume when the backend reconnects.
        </div>
      )}
      {!offline && stalled && (
        <div className="zp-note">
          No progress for a while — the backend may be stuck. Try sending the question again.
        </div>
      )}
      {!offline && !stalled && latestStage && stageText && (
        <div className="zp-stage-line" key={latestStage.zid || latestStage.label}>{stageText}</div>
      )}
      {agents.length === 0 && !offline && !stalled && (
        <div className="zp-stage-line">
          {sources.length > 0 ? `Reading ${sources.length} source${sources.length === 1 ? '' : 's'}…` : 'Deploying sub-agents…'}
        </div>
      )}

      {/* One pill per real sub-agent — name + live verb, no boxed card. */}
      {agents.length > 0 && (
        <div className="zp-row">
          {agents.map((a) => {
            const active = _ZP_ACTIVE.has(a.status);
            const cls = 'zp-pill'
              + (active ? ' is-active' : '')
              + (a.status === 'done' ? ' status-done' : '')
              + (a.status === 'error' ? ' status-error' : '');
            const verb = _ZP_VERBS[a.status] || a.status || '…';
            const tools = Array.from(a.tools).map((t) => (clean ? _zpToolName(t) : t)).filter(Boolean);
            const tip = (a.query && active ? `${verb}: ${a.query}` : (a.label || a.name))
              + (tools.length ? ` · ${tools.join(', ')}` : '');
            return (
              <div className={cls} key={a.id} title={tip}>
                <span className="zp-pill-ico">{_zpIcon(_ZP_STATUS_ICON[a.status] || 'bolt')}</span>
                <span className="zp-pill-txt">{a.name}</span>
                <span className="zp-agent-verb">{verb}</span>
              </div>
            );
          })}
        </div>
      )}

      {/* Sources read, as clickable chips — same idiom as SPARK's source chips. */}
      {ticker.length > 0 && (
        <div className="zp-row zp-chips">
          {ticker.map((e) => {
            const link = !!e.sourceUrl;
            const line = clean ? _zpFriendlyLine(e) : e.label;
            const text = (link && e.sourceTitle) ? e.sourceTitle : line;
            return (
              <span className="zp-chip" key={e.zid || (e.ts + e.label)}
                    onClick={link ? () => _zpOpen(e.sourceUrl) : undefined}
                    title={link ? e.sourceUrl : (e.detail || e.label)}>
                {text}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

window.SyrixZenithPanel = SyrixZenithPanel;
