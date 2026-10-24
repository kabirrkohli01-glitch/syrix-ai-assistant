/**
 * Pre-compile JSX → JS using esbuild (classic transform, no module bundling).
 * Each file is compiled individually so global load order in index.html is preserved.
 * React/ReactDOM remain as window globals from the CDN UMD builds.
 *
 * Usage:
 *   node build.js          — one-shot build (used by npm start)
 *   node build.js --watch  — incremental watch mode (npm run watch)
 */
const esbuild = require('esbuild');
const path    = require('path');

const WATCH = process.argv.includes('--watch');

// Same load order as index.html
const JSX_FILES = [
  'syrix-answer-renderer.jsx',
  'syrix-adaptive-cards.jsx',
  'syrix-primary-chat.jsx',
  'syrix-primary-widgets.jsx',
  'syrix-pass2-widgets.jsx',
  'syrix-hud-app-dock.jsx',
  'syrix-3d-sandbox.jsx',
  'syrix-activity-feed.jsx',
  'syrix-zenith-panel.jsx',
  'syrix-connectors-panel.jsx',
  'syrix-supercomputer-workspace.jsx',
  'syrix-island-hub.jsx',
  'syrix-aurora-shell.jsx',
  'syrix-settings.jsx',
  'syrix-boot-cinematic.jsx',
  'syrix-primary-app.jsx',
];

const OPTS = {
  entryPoints: JSX_FILES.map(f => path.join(__dirname, f)),
  bundle:      false,          // keep files separate; globals are shared via window.*
  jsx:         'transform',    // classic: JSX → React.createElement() calls
  jsxFactory:  'React.createElement',
  jsxFragment: 'React.Fragment',
  target:      ['chrome126'],  // Electron 31 ships Chromium 126
  outdir:      path.join(__dirname, 'dist'),
  logLevel:    'warning',
};

async function main() {
  if (WATCH) {
    const ctx = await esbuild.context(OPTS);
    await ctx.watch();
    console.log('esbuild watching ' + JSX_FILES.length + ' JSX files → dist/  (Ctrl-C to stop)');
  } else {
    await esbuild.build(OPTS);
    console.log('esbuild OK — ' + JSX_FILES.length + ' JSX files compiled → dist/');
  }
}

main().catch(err => {
  console.error('esbuild FAILED:', err.message || err);
  process.exit(1);
});
