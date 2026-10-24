/* Progress-panel regression harness (2026-09-19)
 *
 * The thinking panel blinked: it appeared, vanished for a few seconds, and
 * came back, replaying steps it had already shown — sometimes a stage from the
 * previous question. Two independent causes, both fixed, both pinned here:
 *
 *   1. The activity feed is a ROLLING WINDOW. The panel derived its whole
 *      contents from it on every render, so a step present in one frame and
 *      gone from the next made the ladder empty and rebuild itself.
 *      → SyrixSparkPulse now accumulates: a step is appended the first time it
 *        is seen and stays for the rest of the turn.
 *
 *   2. The turn's identity was inferred from the feed. Between rounds the feed
 *      can hold no event tagged with this request, so the id read as empty,
 *      compared unequal to the real one, and wiped the ladder mid-turn.
 *      → The shell passes the request id it sent the turn with (`turnId`), and
 *        an absent id means "no new information", never "new turn".
 *
 * What this guarantees, offline (no Electron, no live backend):
 *   1. Across a turn whose feed window is replaced each round — including
 *      rounds where nothing carries this request id — the ladder only GROWS.
 *   2. A row, once placed, never changes text or position.
 *   3. No step is ever listed twice.
 *   4. A genuinely new request id DOES clear it, so one question's steps can
 *      never appear under the next.
 *   5. The panel keeps rendering its heading throughout, including the frames
 *      where the feed has nothing for it — it is the shell that decides when
 *      the panel lives, not the feed.
 *
 * Same shape as _check_streaming_render.js: compile the JSX, run it in a VM
 * with a mini-React whose hooks persist across renders, and assert on the
 * resulting tree.
 */
'use strict';
const Babel = require('@babel/standalone');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

let failures = 0;
function check(condition, message) {
  if (!condition) { failures++; console.error('  FAIL: ' + message); }
}

/* ── Mini-React with REAL hook state ──────────────────────────────────────
   The accumulator lives in module scope, but the component still needs
   useState/useRef/useEffect to survive across renders or nothing behaves like
   a mount. Hook slots are keyed by call order within a render pass, which is
   exactly React's own rule. */
let hookSlots = [];
let hookIndex = 0;
const React = {
  Fragment: '__FRAGMENT__',
  createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
  useState(init) {
    const i = hookIndex++;
    if (!(i in hookSlots)) hookSlots[i] = typeof init === 'function' ? init() : init;
    return [hookSlots[i], (v) => { hookSlots[i] = typeof v === 'function' ? v(hookSlots[i]) : v; }];
  },
  useRef(init) {
    const i = hookIndex++;
    if (!(i in hookSlots)) hookSlots[i] = { current: init };
    return hookSlots[i];
  },
  /* Effects must actually RUN: the feed hook subscribes to the bridge inside
     one, so a no-op useEffect meant the panel never received a single event
     and every assertion passed against an empty ladder. Runs on first call
     per slot and again when its deps change, which is what a mount plus an
     update does. */
  useEffect(fn, deps) {
    const i = hookIndex++;
    const prev = hookSlots[i];
    const changed = !prev || !deps || !prev.deps
      || deps.length !== prev.deps.length
      || deps.some((d, k) => d !== prev.deps[k]);
    if (changed) {
      hookSlots[i] = { deps: deps ? deps.slice() : null };
      try { fn(); } catch (_) {}
    }
  },
  useCallback: (fn) => fn,
  useMemo: (fn) => fn(),
};

const listeners = [];
const sandboxWindow = {
  React,
  SyrixBridge: {
    subscribe(fn) { listeners.push(fn); return () => {}; },
  },
  addEventListener() {},
  removeEventListener() {},
  localStorage: { getItem: () => null, setItem: () => {} },
};
sandboxWindow.window = sandboxWindow;

const sandbox = {
  window: sandboxWindow,
  React,
  document: {
    createElement: () => ({ id: '', textContent: '', style: {} }),
    getElementById: () => null,
    head: { appendChild() {} },
  },
  console,
  setTimeout: () => 0,
  clearTimeout: () => {},
  Date,
  Math,
  Set,
  Map,
  Number,
  String,
  Array,
  Object,
  JSON,
  URL,
  isFinite,
};
vm.createContext(sandbox);

const src = fs.readFileSync(path.join(__dirname, 'syrix-activity-feed.jsx'), 'utf8');
const compiled = Babel.transform(src, {
  presets: [['react', { runtime: 'classic' }], ['env', { modules: 'commonjs' }]],
  filename: 'syrix-activity-feed.jsx',
}).code;
vm.runInContext(compiled, sandbox, { filename: 'syrix-activity-feed.jsx' });

const SparkPulse = sandboxWindow.SyrixSparkPulse;
check(typeof SparkPulse === 'function', 'SyrixSparkPulse is exported on window');
if (typeof SparkPulse !== 'function') { process.exit(1); }

/* ── Render helpers ────────────────────────────────────────────────────── */
function renderPanel(props) {
  hookIndex = 0;
  return SparkPulse(props);
}
function walk(node, fn) {
  if (node == null || node === false || node === true) return;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, fn)); return; }
  if (typeof node === 'string' || typeof node === 'number') { fn({ text: String(node) }); return; }
  const { type, props, children } = node;
  const merged = Object.assign({}, props);
  if (children && children.length) merged.children = children.length === 1 ? children[0] : children;
  if (typeof type === 'function') { walk(type(merged), fn); return; }
  fn({ className: (props && props.className) || '' , node });
  const kids = (props && props.children != null) ? props.children
    : (children && children.length ? children : null);
  if (kids != null) walk(kids, fn);
}
/* The step labels, in order. Reads the pill text straight off the elements
   rather than the DOM, since there is no DOM here. */
function stepsOf(tree) {
  const out = [];
  walk(tree, (n) => {
    if (n.className === 'syx-think-txt' && n.node) {
      const kids = n.node.children;
      const text = Array.isArray(kids) ? kids.join('') : String(kids == null ? '' : kids);
      if (text) out.push(text);
    }
  });
  return out;
}
function hasHeading(tree) {
  let found = false;
  walk(tree, (n) => { if (n.className === 'syx-think-title') found = true; });
  return found;
}
function emit(events) { listeners.forEach((fn) => fn({ raw: { activity_feed: events } })); }

/* ── A research turn: the feed window is REPLACED each round, and two of the
      rounds carry nothing tagged with this request. ─────────────────────── */
const RID = 'rid-one';
const t = Date.now() / 1000;
const rounds = [
  [{ kind: 'intent', label: 'Intent: live_research', request_id: RID, ts: t },
   { kind: 'search', label: 'Searching: uk interest rates', request_id: RID, ts: t, provider: 'free_web' }],
  [{ kind: 'info', label: 'heartbeat', ts: t + 1 }],
  [{ kind: 'source', label: 'Read source', source_url: 'https://www.bbc.co.uk/a', source_title: 'BBC', request_id: RID, ts: t + 2, provider: 'ddgs' },
   { kind: 'filter_rank', label: 'Ranking 9 results', request_id: RID, ts: t + 2 }],
  [{ kind: 'info', label: 'heartbeat', ts: t + 3 }],
  [{ kind: 'verify', label: 'Cross-check', detail: '2 claims', request_id: RID, ts: t + 4 },
   { kind: 'synthesis', label: 'Synthesis', request_id: RID, ts: t + 4 }],
];

console.log('progress panel: a turn whose feed window rolls, with gaps');
let prev = [];
rounds.forEach((events, i) => {
  emit(events);
  const tree = renderPanel({ working: true, turnId: RID });
  check(tree != null, 'round ' + (i + 1) + ': panel rendered');
  check(hasHeading(tree), 'round ' + (i + 1) + ': the "Thinking" heading stayed up');
  const cur = stepsOf(tree);
  check(cur.length >= prev.length,
    'round ' + (i + 1) + ': the ladder did not shrink (' + prev.length + ' -> ' + cur.length + ')');
  prev.forEach((label, j) => {
    check(cur[j] === label,
      'round ' + (i + 1) + ': row ' + j + ' held its place ("' + label + '" vs "' + cur[j] + '")');
  });
  check(new Set(cur).size === cur.length, 'round ' + (i + 1) + ': no repeated step');
  prev = cur;
});
check(prev.length >= 4, 'the finished ladder kept every stage it was told about (' + prev.join(' | ') + ')');

/* ── No cap: a long turn keeps every stage on screen ───────────────────── */
console.log('progress panel: a long turn is not truncated');
const RIDL = 'rid-long';
// Connector steps carry their own name, so twenty of them are twenty
// genuinely distinct stages — the case a cap would silently truncate.
const LONG_N = 20;
for (let i = 0; i < LONG_N; i++) {
  emit([{ kind: 'connector', label: 'Service ' + i + ' connector', detail: 'Read',
          request_id: RIDL, ts: t + 20 + i }]);
  renderPanel({ working: true, turnId: RIDL });
}
const longSteps = stepsOf(renderPanel({ working: true, turnId: RIDL }));
check(longSteps.length === LONG_N,
  'all ' + LONG_N + ' stages stayed on the ladder, uncapped (' + longSteps.length + ')');
check(new Set(longSteps).size === longSteps.length,
  'a long ladder still has no repeats');

/* ── Stages that share a label are ONE line, not a repeat ──────────────── */
console.log('progress panel: stages with the same label do not stack up');
const RIDD = 'rid-dupe';
// `search` and `retrieval` both read "Searching the web", and the feed hands
// them over in separate frames.
emit([{ kind: 'search', label: 'Searching: a', request_id: RIDD, ts: t + 60, provider: 'free_web' }]);
renderPanel({ working: true, turnId: RIDD });
emit([{ kind: 'retrieval', label: 'Retrieving: b', request_id: RIDD, ts: t + 61, provider: 'ddgs' }]);
const dupeSteps = stepsOf(renderPanel({ working: true, turnId: RIDD }));
check(dupeSteps.length === 1,
  'the two stages merged into one line (' + dupeSteps.join(' | ') + ')');
// ...but a genuine second round after other work IS its own line.
emit([{ kind: 'verify', label: 'Cross-check', request_id: RIDD, ts: t + 62 }]);
renderPanel({ working: true, turnId: RIDD });
emit([{ kind: 'search', label: 'Searching: c', request_id: RIDD, ts: t + 63, provider: 'free_web' }]);
const roundTwo = stepsOf(renderPanel({ working: true, turnId: RIDD }));
check(roundTwo.length === 3,
  'a second search after other work earned its own line (' + roundTwo.join(' | ') + ')');

/* ── A new question clears it ──────────────────────────────────────────── */
console.log('progress panel: a new request id starts a new ladder');
const RID2 = 'rid-two';
emit([{ kind: 'search', label: 'Searching: something else', request_id: RID2, ts: t + 10, provider: 'free_web' }]);
const fresh = stepsOf(renderPanel({ working: true, turnId: RID2 }));
check(fresh.length > 0, 'the new turn has its own steps');
check(!fresh.includes('Cross-checking facts'),
  'the previous question\'s steps did not carry over (' + fresh.join(' | ') + ')');

/* ── An absent id is not a new turn ────────────────────────────────────── */
console.log('progress panel: an untagged frame is not a turn boundary');
const before = stepsOf(renderPanel({ working: true, turnId: RID2 }));
emit([{ kind: 'info', label: 'heartbeat', ts: t + 11 }]);
const after = stepsOf(renderPanel({ working: true, turnId: RID2 }));
check(after.length === before.length && after.every((v, i) => v === before[i]),
  'the ladder was untouched by a frame with nothing for it');

if (failures) {
  console.error('\nprogress panel: ' + failures + ' FAILURE(S)');
  process.exit(1);
}
console.log('\nprogress panel: OK — the ladder only ever grows, and only a new question resets it');
