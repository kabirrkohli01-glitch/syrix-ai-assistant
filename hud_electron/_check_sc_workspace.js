/* Supercomputer workspace — full-page invariants (2026-07-25).
 *
 * The workspace shipped once at z-index 900, underneath every piece of HUD
 * chrome (rail 8000, bars 8500, island 9900). The page painted but the HUD
 * underneath stayed on top and clickable, so nothing in Supercomputer worked.
 * This check exists so that cannot happen again: it reads the real z-index out
 * of the component's own CSS and compares it against every high layer declared
 * in the HUD stylesheets.
 *
 * Run by `npm run check`.
 */
const fs = require('fs');
const path = require('path');

const here = __dirname;
const workspace = fs.readFileSync(path.join(here, 'syrix-supercomputer-workspace.jsx'), 'utf8');
const aurora = fs.readFileSync(path.join(here, 'aurora-glass.css'), 'utf8');
const primary = fs.readFileSync(path.join(here, 'syrix-primary-styles.css'), 'utf8');

let checks = 0;
function check(condition, message) {
  checks += 1;
  if (!condition) throw new Error('FAIL: ' + message);
}

/* ── The workspace must out-rank every HUD layer ────────────────────────── */
const rootMatch = workspace.match(/\.sc-root\s*\{[^}]*?z-index\s*:\s*(\d+)/);
check(rootMatch, '.sc-root must declare a z-index');
const rootZ = Number(rootMatch[1]);

/* Every z-index in the HUD stylesheets, ignoring the transient boot cinematic
   and emergency overlays at 99999 which are meant to win. */
const layers = [];
for (const css of [aurora, primary]) {
  const re = /z-index\s*:\s*(\d{3,6})/g;
  let m;
  while ((m = re.exec(css))) {
    const z = Number(m[1]);
    if (z < 99999) layers.push(z);
  }
}
const highest = Math.max(...layers);
check(rootZ > highest,
  `.sc-root z-index ${rootZ} must beat every HUD layer (highest is ${highest}) — ` +
  `otherwise the HUD renders on top and Supercomputer is unclickable`);
check(rootZ < 99999, '.sc-root must stay below the boot cinematic / emergency overlays');

/* Opaque, or the HUD shows through even when it is behind. */
check(/\.sc-root\s*\{[^}]*background\s*:\s*#[0-9a-f]{3,8}/i.test(workspace),
  '.sc-root needs an opaque background');

/* ── Chrome pinned above with !important must be removed, not covered ───── */
const pinned = ['.p-island', '.p-widget-menu', '.p-stage-folder-panel'];
for (const sel of pinned) {
  check(workspace.includes('body--supercomputer ' + sel),
    `${sel} is pinned above the workspace with !important and must be hidden ` +
    `by the body--supercomputer class`);
}
check(/body--supercomputer[^{]*\{[^}]*display\s*:\s*none\s*!important/.test(workspace),
  'the chrome suppression rules must use display:none !important');
check(workspace.includes("classList.add('body--supercomputer')")
  && workspace.includes("classList.remove('body--supercomputer')"),
  'the body class must be added on mount AND removed on unmount, or normal ' +
  'SYRIX stays broken after leaving the mode');

/* ── The way out ────────────────────────────────────────────────────────── */
check(/Back<br \/>to HUD|Back to HUD/.test(workspace),
  'there must be a visible way back to the HUD');
check(workspace.includes("onClose && onClose()"),
  'the exit control must actually call onClose');
check(/e\.key === 'Escape'/.test(workspace), 'Escape must leave the workspace');

/* ── The browser must be there on arrival ──────────────────────────────── */
check(/START_PAGE\s*=\s*'https:/.test(workspace), 'a start page must be defined');
check(workspace.includes('native.open({ url: START_PAGE'),
  'the browser must open automatically when the workspace mounts');

/* ── Voice lives in the composer and is Supercomputer-only ─────────────── */
check(workspace.includes('supercomputer_voice_on') && workspace.includes('supercomputer_voice_off'),
  'the composer voice toggle must drive the Supercomputer voice session');
check(workspace.includes('ScVoiceOrb'), 'the voice orb must exist');
check(/\.sc-orb\b/.test(workspace), 'the voice orb must have its own styles');
check(!/voice_mode_on|voice_mode_off|SyrixVoicePresence/.test(workspace),
  'the workspace must never touch normal Voice Mode');

/* ── Layout controls ──────────────────────────────────────────────────── */
check(workspace.includes('syrix_sc_split_v1'), 'the split must persist');
check(workspace.includes('syrix_sc_drawer_v1'), 'the drawer size must persist');
check(/cursor\s*:\s*col-resize/.test(workspace), 'the divider must be draggable');
check(/cursor\s*:\s*row-resize/.test(workspace), 'the drawer must be resizable');

/* ── Smoke render ───────────────────────────────────────────────────────────
 * Actually execute the compiled component with a minimal React stub. A
 * ReferenceError or a bad prop read means the page renders as nothing at all,
 * which is indistinguishable from the z-index bug when you are looking at a
 * broken screen. Static checks cannot catch that; running it can.
 */
function smokeRender() {
  const dist = path.join(here, 'dist', 'syrix-supercomputer-workspace.js');
  if (!fs.existsSync(dist)) {
    throw new Error('FAIL: dist/syrix-supercomputer-workspace.js missing — run node build.js');
  }

  const nodes = [];
  const React = {
    createElement(type, props, ...children) {
      const flat = children.flat(8).filter((c) => c != null && c !== false);
      if (typeof type === 'function') {
        // Render function components for real so their bodies execute.
        return React.createElement.call(null, 'rendered',
          { of: type.name }, type(Object.assign({}, props, { children: flat })));
      }
      const node = { type, props: props || {}, children: flat };
      nodes.push(node);
      return node;
    },
    Fragment: 'fragment',
    useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
    useEffect: () => {},
    useRef: (v) => ({ current: v === undefined ? null : v }),
    useCallback: (fn) => fn,
    useMemo: (fn) => fn(),
  };

  const store = {};
  const g = global;
  g.React = React;
  g.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
  g.document = {
    getElementById: () => null,
    createElement: () => ({ id: '', textContent: '', style: {} }),
    head: { appendChild: () => {} },
    body: { classList: { add: () => {}, remove: () => {} } },
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  g.window = {
    React,
    localStorage: g.localStorage,
    addEventListener: () => {},
    removeEventListener: () => {},
    SyrixBridge: { getSnapshot: () => ({}), subscribe: () => () => {}, send: () => true,
                   chat: () => Promise.resolve('') },
    syrixHud: {
      superComputer: {
        ensure: () => Promise.resolve({}), tabs: () => Promise.resolve({ ok: true, data: {} }),
        open: () => Promise.resolve({}), newTab: () => Promise.resolve({}),
        focus: () => Promise.resolve({}), nav: () => Promise.resolve({}),
        annotate: () => Promise.resolve({}), close: () => Promise.resolve({}),
        setBounds: () => {}, dispose: () => {}, onState: () => () => {},
      },
    },
  };
  g.setTimeout = g.setTimeout || ((fn) => fn());

  require(dist);
  const Comp = g.window.SyrixSupercomputerWorkspace;
  check(typeof Comp === 'function', 'window.SyrixSupercomputerWorkspace must be exported');

  // Render it. Any ReferenceError in the component body throws here.
  React.createElement(Comp, { onClose: () => {}, onOpenSettings: () => {} });

  const classes = nodes
    .map((n) => String((n.props && n.props.className) || ''))
    .join(' ');
  for (const need of ['sc-root', 'sc-topbar', 'sc-rail', 'sc-chat', 'sc-taskbar',
                      'sc-browser', 'sc-drawer', 'sc-composer']) {
    check(classes.includes(need), `smoke render produced no .${need}`);
  }
  check(classes.includes('sc-send'), 'the composer must render a send button');
  // Voice must be reachable from inside the chat column and nowhere else: it is
  // a toggle beside the mode switcher, never a separate overlay or tab, so the
  // browser stays visible while the user is talking.
  check(classes.includes('sc-modeswitch-voice'),
    'the chat column must render the inline voice toggle');
  check(classes.includes('sc-modeswitch'),
    'the chat column must render the Supercomputer mode switcher');
  check(classes.includes('sc-clip'), 'the composer must render the attach button');
  return nodes.length;
}

const rendered = smokeRender();

console.log(`OK — supercomputer workspace passed ${checks} layout/isolation checks ` +
  `(z-index ${rootZ} clears ${highest}); smoke render produced ${rendered} nodes.`);
