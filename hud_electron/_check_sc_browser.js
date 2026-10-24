/* Supercomputer browser module — security invariants (2026-07-24).
 *
 * The Python side gates every op before it is sent, but the Electron module is
 * the last line and must hold on its own. This checks the invariants that
 * cannot be verified from the Python tests: the URL policy as implemented in
 * JS, the BrowserView security flags, and the absence of anything that would
 * let a Supercomputer op reach a view it did not create.
 *
 * Run by `npm run check`.
 */
const fs = require('fs');
const path = require('path');

const modPath = path.join(__dirname, 'syrix-supercomputer-browser.js');
const source = fs.readFileSync(modPath, 'utf8');
const mod = require(modPath);

let checks = 0;
function check(condition, message) {
  checks += 1;
  if (!condition) throw new Error('FAIL: ' + message);
}

/* ── URL policy ─────────────────────────────────────────────────────────── */
const blocked = [
  ['javascript:alert(1)', 'javascript: scheme'],
  ['file:///C:/Windows/win.ini', 'file: scheme'],
  ['data:text/html,<h1>x', 'data: scheme'],
  ['ftp://example.com/', 'ftp: scheme'],
  ['http://localhost:8080/', 'localhost'],
  ['http://127.0.0.1/', 'loopback'],
  ['http://127.1.2.3/', 'loopback range'],
  ['http://[::1]/', 'ipv6 loopback'],
  ['http://0.0.0.0/', 'wildcard address'],
  ['http://10.1.2.3/', 'RFC1918 10/8'],
  ['http://192.168.0.1/', 'RFC1918 192.168/16'],
  ['http://172.20.0.1/', 'RFC1918 172.16/12'],
  ['http://169.254.169.254/latest/meta-data/', 'link-local / cloud metadata'],
  ['http://box.localhost/', '.localhost suffix'],
  ['', 'empty url'],
  ['not a url', 'unparseable url'],
];
for (const [url, why] of blocked) {
  const v = mod.urlAllowed(url);
  check(v.ok === false, `${why} must be refused (${url})`);
}

const allowed = [
  'https://en.wikipedia.org/wiki/Chromium',
  'http://example.com/path?q=1',
  'https://api.github.com/repos/x/y',
];
for (const url of allowed) {
  check(mod.urlAllowed(url).ok === true, `ordinary web URL must be allowed (${url})`);
}

/* Credentials in the authority: hostname must resolve to the REAL host, which
 * is how a host check gets pointed at the wrong authority. */
const spoof = mod.urlAllowed('https://github.com@127.0.0.1/x');
check(spoof.ok === false, 'userinfo must not disguise a loopback host');

/* ── BrowserView security flags ─────────────────────────────────────────── */
check(/nodeIntegration:\s*false/.test(source), 'nodeIntegration must be false');
check(/contextIsolation:\s*true/.test(source), 'contextIsolation must be true');
check(/sandbox:\s*true/.test(source), 'renderer must be sandboxed');
check(/webSecurity:\s*true/.test(source), 'webSecurity must stay on');
check(/webviewTag:\s*false/.test(source), 'webviewTag must be false');
check(/allowRunningInsecureContent:\s*false/.test(source), 'insecure content must be refused');
/* Match an actual property assignment, not the comment explaining its absence. */
check(!/^\s*preload\s*:/m.test(source), 'Supercomputer page content must have no preload');

/* ── Hardening present ──────────────────────────────────────────────────── */
check(/setPermissionRequestHandler/.test(source), 'permission request handler missing');
check(/setPermissionCheckHandler/.test(source), 'permission check handler missing');
check(/setDevicePermissionHandler/.test(source), 'device permission handler missing');
check(/will-download/.test(source), 'downloads must be blocked');
check(/setWindowOpenHandler/.test(source), 'window.open must be denied');
check(/will-navigate/.test(source), 'navigation policy hook missing');
for (const perm of ['microphone', 'camera', 'geolocation', 'clipboard-read', 'usb', 'serial']) {
  check(source.includes(`'${perm}'`), `${perm} must be in the blocked permission set`);
}

/* ── Ephemeral partition ────────────────────────────────────────────────── */
check(!/['"]persist:/.test(source),
  'the Supercomputer partition must be ephemeral — no persist: prefix');
const p1 = mod._internals.partitionId();
check(/^syrix-supercomputer-/.test(p1), 'partition id must be namespaced');
mod.dispose();
const p2 = mod._internals.partitionId();
check(p1 !== p2, 'dispose must force a fresh partition, so nothing carries over');

/* ── Isolation from the app dock ────────────────────────────────────────── */
check(!/hud-app:/.test(source),
  'the Supercomputer module must never touch hud-app:* IPC or its views');
check(!/_hudApps/.test(source), 'the Supercomputer module must not read the dock registry');
check(/sc-browser:/.test(source), 'handlers must live under the sc-browser namespace');

/* main.js must stay a thin bootstrap, not host the subsystem. */
const mainSource = fs.readFileSync(path.join(__dirname, 'main.js'), 'utf8');
const scLines = mainSource.split('\n').filter((l) => /sc-browser|_scBrowser|supercomputer-browser/i.test(l));
check(scLines.length <= 14,
  `main.js should only bootstrap the Supercomputer browser, found ${scLines.length} lines`);
check(/require\("\.\/syrix-supercomputer-browser\.js"\)/.test(mainSource),
  'main.js must lazily require the Supercomputer browser module');

console.log(`OK — syrix-supercomputer-browser.js passed ${checks} security checks.`);
