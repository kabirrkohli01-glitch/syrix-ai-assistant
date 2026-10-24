/* Streaming-render regression harness (2026-07-20)
 *
 * Proves the incremental rich-text streaming contract for
 * syrix-answer-renderer.jsx — the fix for "types plain text first, then
 * reformats into headings/tables/bullets/code after the answer finishes".
 *
 * What it guarantees, offline (no Electron, no live backend):
 *   1. streamGuard is a NO-OP on complete markdown, so the last streamed frame
 *      (streaming:true, full text) normalizes byte-identically to the finished
 *      message (streaming:false) → the answer never rebuilds when generation
 *      ends.
 *   2. Streaming and finished renders of the same text produce the SAME set of
 *      rich block types (heading/table/list/code) — one renderer, not two.
 *   3. Rich structure appears INCREMENTALLY across growing prefixes (a heading
 *      renders as a heading, a table as a table) instead of plain text.
 *   4. Incomplete markdown at the streaming caret is safe: no raw "](http…",
 *      no open ``` fence, no dangling inline-code backtick, no empty heading
 *      flashes on screen.
 *   5. The message is never rendered twice / with disappearing content: every
 *      prefix renders a single sxr-root tree and never throws.
 *   6. Source-level: both chat shells route the live stream through the SAME
 *      SyrixAnswerRenderer (streaming), not the plain syntax stripper.
 *
 * Mirrors the _check_preview_security.js pattern: Babel-compile the JSX, run in
 * a VM with a mini-React that recursively renders function components, and
 * assert on the resulting block tree.
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

/* ── Load the vendored markdown lexer (marked v12 UMD → exports object) ──── */
function loadMarked() {
  const src = fs.readFileSync(path.join(__dirname, 'vendor', 'marked.min.js'), 'utf8');
  const mod = { exports: {} };
  const sandbox = { module: mod, exports: mod.exports };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'marked.min.js' });
  return sandbox.exports;
}
const marked = loadMarked();

/* ── Minimal DOM + React so the renderer's function components actually run ─ */
global.window = {
  marked,
  // The renderer only needs sanitize to exist and return a string; XSS
  // sanitization correctness is covered by _check_preview_security.js.
  DOMPurify: { sanitize: (s) => String(s == null ? '' : s) },
};
global.document = {
  createElement: () => ({ id: '', textContent: '' }),
  head: { appendChild: () => {} },
};
// navigator is a getter-only global on newer Node; the renderer only touches
// navigator.clipboard inside a click handler we never fire, so a best-effort
// define is enough.
try { Object.defineProperty(global, 'navigator', { value: {}, configurable: true }); } catch (_) {}

const React = {
  memo: (fn) => fn,
  createElement: (type, props, ...children) => ({ type, props: props || {}, children }),
  useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
  Fragment: '__FRAGMENT__',
};
global.React = React;

/* Recursively render an element tree to a plain {tag,className,html,children}
   tree, invoking every function component (that is what a real React render
   does — without it the "smart" blocks would never execute). */
function renderToTree(el) {
  if (el == null || el === false || el === true) return null;
  if (Array.isArray(el)) return el.map(renderToTree).filter((x) => x != null);
  if (typeof el === 'string' || typeof el === 'number') return { text: String(el) };
  const { type, props, children } = el;
  const merged = Object.assign({}, props);
  if (children && children.length) merged.children = children.length === 1 ? children[0] : children;
  if (typeof type === 'function') return renderToTree(type(merged));
  if (type === React.Fragment) return renderToTree(merged.children);
  const html = props && props.dangerouslySetInnerHTML ? String(props.dangerouslySetInnerHTML.__html || '') : '';
  const kids = props && props.children != null ? props.children
    : (children && children.length ? (children.length === 1 ? children[0] : children) : null);
  return {
    tag: typeof type === 'string' ? type : '?',
    className: (props && props.className) || '',
    html,
    children: kids != null ? renderToTree(kids) : null,
  };
}

function walk(node, fn) {
  if (node == null) return;
  if (Array.isArray(node)) { node.forEach((n) => walk(n, fn)); return; }
  fn(node);
  if (node.children != null) walk(node.children, fn);
}
function allClasses(node) {
  const set = new Set();
  walk(node, (n) => { if (n.className) String(n.className).split(/\s+/).forEach((c) => c && set.add(c)); });
  return set;
}
function allText(node) {
  let out = '';
  walk(node, (n) => { if (n.text) out += n.text + ' '; if (n.html) out += n.html + ' '; });
  return out;
}
// The rich block classes the renderer emits for each structural shape.
function blockShapes(node) {
  const cls = allClasses(node);
  return {
    heading: cls.has('sxr-h'),
    table: cls.has('sxr-table-wrap'),
    list: cls.has('sxr-ul') || cls.has('sxr-ol') || cls.has('sxr-flow')
      || cls.has('sxr-facts') || cls.has('sxr-rank') || cls.has('sxr-stats') || cls.has('sxr-timeline'),
    code: cls.has('sxr-code'),
    callout: cls.has('sxr-callout'),
    root: cls.has('sxr-root'),
    stable: cls.has('sxr-stable'),
  };
}

/* ── Compile + evaluate the renderer ──────────────────────────────────────── */
const sourcePath = path.join(__dirname, 'syrix-answer-renderer.jsx');
const compiled = Babel.transform(fs.readFileSync(sourcePath, 'utf8'), {
  presets: [['react', { runtime: 'classic' }], ['env', { modules: 'commonjs' }]],
  plugins: ['transform-class-properties', 'transform-object-rest-spread', 'transform-flow-strip-types'],
  filename: 'syrix-answer-renderer.jsx',
}).code;
vm.runInThisContext(compiled, { filename: 'syrix-answer-renderer.compiled.js' });

const Renderer = global.window.SyrixAnswerRenderer;
const streamGuard = global.window.SyrixStreamGuard;
const normalize = global.window.SyrixNormalizeMarkdown;
const selectedBrandIds = global.window.SyrixSelectedBrandIds;
check(typeof Renderer === 'function', 'SyrixAnswerRenderer must be exported as a function');
check(typeof streamGuard === 'function', 'SyrixStreamGuard must be exported for the harness');
check(typeof normalize === 'function', 'SyrixNormalizeMarkdown must be exported');
check(typeof selectedBrandIds === 'function', 'brand-link selector must be exported for the harness');

/* Brand links stay scarce: unique trusted mentions only, capped at four;
   code and already-linked markdown are left to their native renderers. */
{
  const picked = selectedBrandIds('Use GitHub, Slack, GitLab, HubSpot, and Discord.');
  check(picked.size === 4, 'plain brand links must be capped at four per answer');
  check(picked.has('github') && picked.has('slack') && picked.has('gitlab') && picked.has('hubspot'),
    'the first four trusted one-off mentions must be selected');
  check(!selectedBrandIds('GitHub works with GitHub Actions.').has('github'),
    'a repeated brand must remain normal text to avoid over-linking');
  check(selectedBrandIds('`GitHub` and [Slack](https://slack.com)').size === 0,
    'inline code and existing markdown links must not trigger plain-name links');
  check(selectedBrandIds('Leave some slack in the schedule and use linear interpolation.').size === 0,
    'ordinary lowercase words that share brand names must remain plain text');
}

function render(text, opts) {
  return renderToTree(Renderer(Object.assign({ text }, opts || {})));
}

/* ══════════════════════════════════════════════════════════════════════════
   1. streamGuard is a no-op on COMPLETE markdown → final == last stream frame
   ══════════════════════════════════════════════════════════════════════════ */
const COMPLETE_ANSWERS = [
  '# Overview\n\nThe **fast** path wins.\n\n| Model | Score |\n|---|---|\n| A | 9 |\n| B | 7 |\n',
  'Here are the steps:\n\n1. First do this\n2. Then that\n3. Finally ship it\n',
  'Reasons:\n\n- Point one\n- Point two with `inline code`\n- Point three\n',
  'Try this:\n\n```python\nprint("hello")\n```\n\nThat runs it.',
  'See [the docs](https://example.com/guide) for more.',
  '## Result\n\nParagraph one.\n\n> A note worth calling out.\n\nParagraph two.',
];
for (const full of COMPLETE_ANSWERS) {
  check(streamGuard(full) === full,
    'streamGuard must be a no-op on complete markdown (else finished answer rebuilds): ' + JSON.stringify(full.slice(0, 40)));
  check(normalize(streamGuard(full)) === normalize(full),
    'last streamed frame must normalize identically to the finished message');
}

/* ══════════════════════════════════════════════════════════════════════════
   2. Streaming and finished renders of the same text → SAME rich block shapes
   ══════════════════════════════════════════════════════════════════════════ */
const MIXED = '# Report\n\nLead paragraph.\n\n## Data\n\n| K | V |\n|---|---|\n| a | 1 |\n| b | 2 |\n\n'
  + 'Steps:\n\n1. one step here\n2. two step here\n3. three step here\n\n'
  + 'Notes:\n\n- alpha\n- beta\n\n```js\nconst x = 1;\nconsole.log(x);\n```\n';
{
  const fin = blockShapes(render(MIXED, { streaming: false, stable: true }));
  const str = blockShapes(render(MIXED, { streaming: true }));
  check(fin.heading && fin.table && fin.list && fin.code, 'finished render must produce heading+table+list+code');
  check(str.heading && str.table && str.list && str.code, 'streaming render of full text must produce the SAME heading+table+list+code');
  check(fin.heading === str.heading && fin.table === str.table && fin.list === str.list && fin.code === str.code,
    'streaming and finished block shapes must match exactly (one renderer, no second layout)');
  check(str.stable === true, 'streaming render must carry the sxr-stable class (no re-pop on hand-off)');
  check(fin.stable === true, 'a streamed finished message (stable:true) must suppress the entrance animation');
}

/* ══════════════════════════════════════════════════════════════════════════
   3. Rich structure appears INCREMENTALLY across growing prefixes
   ══════════════════════════════════════════════════════════════════════════ */
{
  // Once the heading line is complete, it renders as a heading (not plain text).
  const afterHeading = blockShapes(render('# Report\n\nLead para', { streaming: true }));
  check(afterHeading.heading, 'a completed heading line must render as a heading mid-stream');
  // Once two body rows exist, the pipe block renders as a real table.
  const midTable = render('## Data\n\n| K | V |\n|---|---|\n| a | 1 |\n| b |', { streaming: true });
  check(blockShapes(midTable).table, 'a table with >=2 rows must render as a table mid-stream (not a wall of pipes)');
  check(!/\|\s*---/.test(allText(midTable)), 'the table separator row must not leak into visible text mid-stream');
  // A list under construction renders as a list.
  check(blockShapes(render('Notes:\n\n- alpha\n- be', { streaming: true })).list,
    'a bulleted list must render as a list mid-stream');
}

/* ══════════════════════════════════════════════════════════════════════════
   4. Incomplete markdown at the caret is safe (no raw broken syntax)
   ══════════════════════════════════════════════════════════════════════════ */
{
  // Unfinished link → show the label, never a raw "](http…".
  check(streamGuard('See [OpenAI](https://ope') === 'See OpenAI',
    'unfinished inline link must degrade to its label');
  check(streamGuard('Read [the guide') === 'Read the guide',
    'a link with no closing bracket yet must degrade to its label');
  const linkTree = render('See [OpenAI](https://ope', { streaming: true });
  check(!/\]\(http/.test(allText(linkTree)), 'no raw "](http" may reach the screen while a link is being typed');
  // Citations: a half-typed one is held back until it closes, and the
  // 【1†L1-L3】 dialect streams as the same [1] the finished answer carries.
  check(streamGuard('It was founded in 1993 [1') === 'It was founded in 1993 ',
    'a half-typed [n] must be held back, never shown as a bare digit');
  check(streamGuard('It was founded in 1993【1†L4') === 'It was founded in 1993',
    'a half-typed 【n† marker must be held back');
  check(streamGuard('Founded in 1993【1†L4-L6】 by three engineers.') === 'Founded in 1993 [1] by three engineers.',
    'the 【n†…】 dialect must stream as [n]');
  check(streamGuard('It rose 3%【turn0search3】 today.') === 'It rose 3% today.',
    'a marker naming no numbered source must be dropped');
  // Open inline code span → drop the dangling backtick.
  check(streamGuard('Run `cod') === 'Run cod', 'open inline-code span must drop its dangling backtick');
  // Bare heading marker with no text yet → held back (no empty heading flash).
  check(streamGuard('intro\n## ') === 'intro\n', 'a bare heading marker at the caret must be held back');
  check(!blockShapes(render('intro\n## ', { streaming: true })).heading, 'no empty heading block may flash before its text arrives');
  // Unclosed code fence → normalizeMarkdown balances it → renders as a code
  // card, never a raw ``` line, and the body is preserved.
  const openFence = render('```python\nprint("hi")', { streaming: true });
  check(blockShapes(openFence).code, 'an unclosed code fence must render as a code block mid-stream');
  check(/print/.test(allText(openFence)), 'code-fence body must be preserved while the fence is still open');
  check(!/```/.test(allText(openFence)), 'raw ``` fence markers must never reach the screen');
}

/* ══════════════════════════════════════════════════════════════════════════
   5. Every growing prefix renders exactly one sxr-root and never throws
      (no double render, no disappearing content, no retype)
   ══════════════════════════════════════════════════════════════════════════ */
{
  let prevLen = -1, everShrank = false, threw = false;
  for (let i = 1; i <= MIXED.length; i += 3) {
    const prefix = MIXED.slice(0, i);
    let tree;
    try { tree = render(prefix, { streaming: true }); } catch (e) { threw = true; break; }
    let roots = 0;
    walk(tree, (n) => { if (n.className && String(n.className).split(/\s+/).includes('sxr-root')) roots++; });
    check(roots === 1, 'each streaming frame must render exactly one sxr-root (got ' + roots + ' at len ' + i + ')');
    // Visible text length should grow monotonically (content never disappears
    // wholesale as tokens arrive — edge trims are bounded, not resets).
    const len = allText(tree).replace(/\s+/g, ' ').trim().length;
    if (len + 24 < prevLen) everShrank = true;   // allow small caret-edge trims
    prevLen = Math.max(prevLen, len);
  }
  check(!threw, 'no streaming prefix may throw');
  check(!everShrank, 'visible content must not disappear wholesale as tokens arrive');
}

/* ══════════════════════════════════════════════════════════════════════════
   6. Source-level — both shells route the live stream through the SAME
      SyrixAnswerRenderer (streaming), not the plain syntax stripper
   ══════════════════════════════════════════════════════════════════════════ */
{
  const aurora = fs.readFileSync(path.join(__dirname, 'syrix-aurora-shell.jsx'), 'utf8');
  const primary = fs.readFileSync(path.join(__dirname, 'syrix-primary-chat.jsx'), 'utf8');
  // The bubble renders the stream through useEgSmoothText (steady reveal of
  // bursty tokens); either name is the live stream text.
  check(/(?:streamText|smoothStream),\s*streaming:\s*true/.test(aurora.replace(/\s+/g, ' ')),
    'aurora live streaming bubble must render streamText through SyrixAnswerRenderer with streaming:true');
  check(/streaming:\s*animating/.test(aurora),
    'aurora message body must pass streaming:animating so mid-reveal uses the rich renderer');
  check(/<SmartBody text=\{shownBody\} streaming stable \/>/.test(primary)
      || /SmartBody[^>]*shownBody[^>]*streaming/.test(primary),
    'primary-chat AI mid-reveal must render through SmartBody with streaming');
  check(/text=\{streamingText\} streaming stable/.test(primary),
    'primary-chat live streaming bubble must render through SmartBody with streaming');
}

if (failures) {
  console.error('\nStreaming-render checks FAILED (' + failures + ').');
  process.exit(1);
}
console.log('Streaming-render checks OK.');
