const Babel = require('@babel/standalone');
const fs = require('fs');
const vm = require('vm');

const sourcePath = require('path').join(__dirname, 'syrix-answer-renderer.jsx');
const source = fs.readFileSync(sourcePath, 'utf8');

function check(condition, message) {
  if (!condition) throw new Error(message);
}

check(/sandbox="allow-scripts"/.test(source), 'preview iframe must allow scripts only');
check(!/sandbox="[^"]*allow-same-origin/.test(source), 'preview must never get same-origin');
check(!/sandbox="[^"]*(?:allow-forms|allow-popups|allow-downloads|allow-top-navigation)/.test(source),
  'preview gained an unsafe sandbox permission');

global.window = {};
global.document = {
  createElement: () => ({ id: '', textContent: '' }),
  head: { appendChild: () => {} },
};
global.navigator = {};
global.React = {
  memo: (fn) => fn,
  createElement: () => null,
  useState: (initial) => [initial, () => {}],
};

const compiled = Babel.transform(source, {
  presets: [['react', { runtime: 'classic' }], ['env', { modules: 'commonjs' }]],
  plugins: ['transform-class-properties', 'transform-object-rest-spread', 'transform-flow-strip-types'],
  filename: 'syrix-answer-renderer.jsx',
}).code;
vm.runInThisContext(compiled, { filename: 'syrix-answer-renderer.compiled.js' });

const build = global.window.SyrixBuildPreviewSrcDoc;
check(typeof build === 'function', 'preview srcdoc builder was not exported');

const doc = build({
  html: '<main><h1>Demo</h1><button id="go">Go</button></main>',
  css: 'button { color: rebeccapurple; }',
  js: 'document.querySelector("#go").onclick = () => document.body.dataset.ok = "1";',
});
for (const policy of [
  "default-src 'none'", "connect-src 'none'", "frame-src 'none'",
  "worker-src 'none'", "form-action 'none'", "base-uri 'none'",
]) {
  check(doc.includes(policy), `missing CSP policy: ${policy}`);
}
check(doc.includes('no-referrer'), 'preview must suppress referrer data');
check(doc.includes('Preview error'), 'preview must surface runtime errors');
check(doc.includes('button { color: rebeccapurple; }'), 'CSS bundle was not composed');
check(doc.includes('document.querySelector'), 'JavaScript bundle was not composed');

const escaped = build({ js: 'document.body.textContent = "ok";</script><script>bad()</script>' });
check(!escaped.includes('</script><script>bad()'), 'user JavaScript can break out of its script tag');
check(build({ html: 'x'.repeat(250001) }) === '', 'oversized preview was not rejected');

console.log('Preview security checks OK.');
