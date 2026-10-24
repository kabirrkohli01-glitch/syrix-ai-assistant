/* SYRIX PASS-2 WIDGETS — Telemetry, MemoryGraph.
   These widgets consume Pass-2 state from hud_state.syrix_pass2 and
   hud_state.syrix_upgrades, and use UMD libraries loaded from index.html:
     window.Recharts             — Recharts
   (MemoryGraph no longer uses React Flow / zustand — those UMD scripts were
    removed from index.html on 2026-06-17.)
   Heavy libraries are accessed defensively: if the global is missing the
   widget renders a clean fallback instead of throwing. */

const { useState: p2WS, useEffect: p2WE, useRef: p2WR, useMemo: p2WM } = React;

function _p2Draggable(props) {
  // Use the existing Draggable already defined in syrix-primary-widgets.jsx.
  const D = window.SyrixDraggable;
  if (D) return React.createElement(D, props, props.children);
  // Fallback minimal frame.
  return React.createElement('div', {
    className: 'p-widget' + (props.focused ? ' focused' : ''),
    style: { left: props.x || 80, top: props.y || 80, ...(props.style || {}) },
    onMouseDown: () => props.onFocus && props.onFocus(props.id),
  }, [
    React.createElement('div', { key: 'h', className: 'p-w-bar' }, [
      React.createElement('span', { key: 't', className: 'p-w-title' }, props.title),
      React.createElement('button', {
        key: 'x', className: 'p-w-x', onClick: () => props.onClose && props.onClose(props.id),
      }, '×'),
    ]),
    React.createElement('div', { key: 'b', className: 'p-w-body' }, props.children),
  ]);
}

/* ─── shared subscription helper ──────────────────────────────────────── */
function useP2Snap() {
  const [snap, setSnap] = p2WS(null);
  p2WE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((s) => {
      if (!s) return;
      setSnap(prev => ({
        upgrades: s.syrix_upgrades || (prev && prev.upgrades) || null,
        pass2: s.syrix_pass2 || (prev && prev.pass2) || null,
        raw: s,
      }));
    });
  }, []);
  return snap || { upgrades: null, pass2: null, raw: null };
}

/* ─── 1. TelemetryChartWidget — Recharts rolling buffer ────────────────── */
function TelemetryChartWidget({ id, onClose, focused, onFocus, onBlur }) {
  const [history, setHistory] = p2WS([]);

  p2WE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    return window.SyrixBridge.subscribe((s) => {
      const r = s && s.resource_governor;
      const point = {
        t: new Date().toLocaleTimeString().slice(0, 8),
        cpu: r && typeof r.cpu_percent === 'number' ? Math.round(r.cpu_percent) : null,
        gpu: r && typeof r.gpu_percent === 'number' ? Math.round(r.gpu_percent) : null,
        mem: r && typeof r.memory_percent === 'number' ? Math.round(r.memory_percent) : null,
      };
      setHistory(prev => {
        const arr = [...prev, point];
        return arr.length > 40 ? arr.slice(-40) : arr;
      });
    });
  }, []);

  const RC = window.Recharts;
  return React.createElement(_p2Draggable, {
    id, title: 'Telemetry', x: 80, y: 470, onClose, focused, onFocus, onBlur,
    style: { width: 460, height: 280 },
  }, React.createElement('div', { className: 'p-w-body' },
    !RC ? React.createElement('div', { style: { font: '500 11px Inter', color: 'rgb(var(--eg-accent-rgb))' } }, 'Recharts UMD not loaded.')
    : React.createElement('div', { style: { width: '100%', height: 220 } },
        React.createElement(RC.ResponsiveContainer, { width: '100%', height: '100%' },
          React.createElement(RC.AreaChart, { data: history }, [
            React.createElement(RC.defs, { key: 'd' }, [
              React.createElement('linearGradient', { key: 'gc', id: 'gradCpu', x1: 0, y1: 0, x2: 0, y2: 1 }, [
                React.createElement('stop', { key: 'a', offset: '0%', stopColor: '#e9c98c', stopOpacity: 0.5 }),
                React.createElement('stop', { key: 'b', offset: '100%', stopColor: '#e9c98c', stopOpacity: 0 }),
              ]),
              React.createElement('linearGradient', { key: 'gg', id: 'gradGpu', x1: 0, y1: 0, x2: 0, y2: 1 }, [
                React.createElement('stop', { key: 'a', offset: '0%', stopColor: '#16d37a', stopOpacity: 0.45 }),
                React.createElement('stop', { key: 'b', offset: '100%', stopColor: '#16d37a', stopOpacity: 0 }),
              ]),
              React.createElement('linearGradient', { key: 'gm', id: 'gradMem', x1: 0, y1: 0, x2: 0, y2: 1 }, [
                React.createElement('stop', { key: 'a', offset: '0%', stopColor: '#f5c243', stopOpacity: 0.4 }),
                React.createElement('stop', { key: 'b', offset: '100%', stopColor: '#f5c243', stopOpacity: 0 }),
              ]),
            ]),
            React.createElement(RC.XAxis, { key: 'x', dataKey: 't', tick: { fontSize: 9, fill: '#6b8aa3' }, axisLine: false, tickLine: false }),
            React.createElement(RC.YAxis, { key: 'y', tick: { fontSize: 9, fill: '#6b8aa3' }, axisLine: false, tickLine: false, domain: [0, 100] }),
            React.createElement(RC.CartesianGrid, { key: 'g', stroke: 'rgba(var(--eg-accent-rgb),0.06)', vertical: false }),
            React.createElement(RC.Tooltip, { key: 't', contentStyle: { background: '#03070f', border: '1px solid rgba(var(--eg-accent-surface-rgb),0.22)', font: '11px JetBrains Mono' } }),
            React.createElement(RC.Area, { key: 'c', type: 'monotone', dataKey: 'cpu', stroke: 'rgb(var(--eg-accent-rgb))', strokeWidth: 1.4, fill: 'url(#gradCpu)' }),
            React.createElement(RC.Area, { key: 'gp', type: 'monotone', dataKey: 'gpu', stroke: '#16d37a', strokeWidth: 1.4, fill: 'url(#gradGpu)' }),
            React.createElement(RC.Area, { key: 'm', type: 'monotone', dataKey: 'mem', stroke: 'rgb(var(--eg-accent-rgb))', strokeWidth: 1.2, fill: 'url(#gradMem)' }),
          ])
        )
      )
  ));
}

/* ─── 2. MemoryWidget — SYRIX's mind: what he remembers about you ───────────
   2026-06-16 rebuild (the user): replaced the empty ReactFlow graph with a clean,
   gold-themed, readable list of remembered facts. Each has a Forget button
   (confirm on 2nd click) that deletes the note from the Obsidian vault, plus an
   "Open in Obsidian" action. Data: syrix_recent_memories + syrix_obsidian_status. */
function MemoryGraphWidget({ id, onClose, focused, onFocus, onBlur }) {
  const snap = useP2Snap();
  const raw = snap.raw || {};
  const recent = (raw.recent_memories && Array.isArray(raw.recent_memories.recent)) ? raw.recent_memories.recent : [];
  const obs = raw.obsidian_status || {};
  const total = Number(obs.total || 0);
  const connected = obs.enabled !== false && (total > 0 || recent.length > 0);
  const [confirmFn, setConfirmFn] = p2WS('');
  const [forgotten, setForgotten] = p2WS({});

  // Pull the latest memory list + vault status when the widget opens, then refresh.
  p2WE(() => {
    const B = window.SyrixBridge;
    try { B && B.getRecentMemories && B.getRecentMemories(); } catch (_) {}
    try { B && B.obsidianRefresh && B.obsidianRefresh(); } catch (_) {}
    const t = setInterval(() => { try { B && B.getRecentMemories && B.getRecentMemories(); } catch (_) {} }, 15000);
    return () => clearInterval(t);
  }, []);

  function relTime(ts) {
    let ms = Number(ts) || 0; if (ms && ms < 1e12) ms *= 1000;
    if (!ms) return '';
    const m = Math.floor(Math.max(0, Date.now() - ms) / 60000);
    if (m < 1) return 'just now';
    if (m < 60) return m + 'm ago';
    const h = Math.floor(m / 60); if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  }

  function forget(item) {
    const fn = item && item.filename; if (!fn) return;
    if (confirmFn !== fn) {
      setConfirmFn(fn);
      setTimeout(() => setConfirmFn(c => (c === fn ? '' : c)), 3500);
      return;
    }
    setForgotten(f => ({ ...f, [fn]: true }));
    setConfirmFn('');
    try { window.SyrixBridge && window.SyrixBridge.forgetMemory && window.SyrixBridge.forgetMemory(fn, item.kind || 'memory'); } catch (_) {}
  }

  function openObsidian() {
    try { window.SyrixBridge && window.SyrixBridge.openObsidian && window.SyrixBridge.openObsidian(); } catch (_) {}
  }

  const visible = recent.filter(it => it && it.filename && !forgotten[it.filename]);

  return React.createElement(_p2Draggable, {
    id, title: 'Memory', x: 460, y: 130, onClose, focused, onFocus, onBlur,
    style: { width: 460, height: 540 },
  }, (
    <div className="eg-mem">
      <div className="eg-mem-head">
        <span className={"eg-mem-dot" + (connected ? ' on' : '')} />
        <span className="eg-mem-status">
          {connected ? 'Connected to Obsidian' : 'Obsidian not connected'}
        </span>
        {total > 0 && <span className="eg-mem-count">{total} notes</span>}
        <button className="eg-mem-open" onClick={openObsidian} title="Open your vault in the Obsidian app">
          Open Obsidian
        </button>
      </div>
      <div className="eg-mem-sub">What SYRIX remembers about you</div>
      <div className="eg-mem-list">
        {visible.length === 0 ? (
          <div className="eg-mem-empty">
            Nothing yet — as you chat and tell SYRIX things, they’re saved here and to your Obsidian vault.
          </div>
        ) : visible.map((it, i) => (
          <div key={it.filename || i} className="eg-mem-row" style={{ animationDelay: (Math.min(i, 12) * 22) + 'ms' }}>
            <div className="eg-mem-row-main">
              <div className="eg-mem-row-title" title={it.title}>{it.title || it.filename}</div>
              <div className="eg-mem-row-meta">
                <span className="eg-mem-kind">{it.kind || 'memory'}</span>
                {relTime(it.ts) && <span className="eg-mem-time">· {relTime(it.ts)}</span>}
              </div>
            </div>
            <button
              className={"eg-mem-forget" + (confirmFn === it.filename ? ' confirm' : '')}
              onClick={() => forget(it)}
              title={confirmFn === it.filename ? 'Click again to forget' : 'Forget this'}>
              {confirmFn === it.filename ? 'Sure?' : 'Forget'}
            </button>
          </div>
        ))}
      </div>
    </div>
  ));
}

/* ─── registry ─────────────────────────────────────────────────────────── */
window.SyrixPass2Widgets = {
  telemetry_chart:  TelemetryChartWidget,
  memory_graph:     MemoryGraphWidget,
};
