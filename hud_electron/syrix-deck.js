/**
 * Deck — launches the apps of the SYRIX ecosystem from the HUD (main process).
 *
 * The app list lives in deck.json beside this file. The renderer can ask for
 * that list and ask for one entry to be launched by id; it can never pass a
 * path, a command or an address, so a compromised page cannot use the Deck to
 * run anything that is not already listed there.
 *
 * Launches are detached: an app started from the Deck keeps running when the
 * HUD closes, exactly as if its desktop shortcut had been used.
 */
"use strict";

const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

const CONFIG_FILE = path.join(__dirname, "deck.json");
const SYRIX_ELECTRON = path.join(__dirname, "node_modules", "electron", "dist", "electron.exe");

function expand(value) {
  return String(value == null ? "" : value).replace(/\$\{(\w+)\}/g, (_m, name) => {
    if (name === "SYRIX_ELECTRON") return SYRIX_ELECTRON;
    if (name === "USERPROFILE") return process.env.USERPROFILE || os.homedir();
    return process.env[name] || "";
  });
}

function loadApps() {
  try {
    const doc = JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
    return Array.isArray(doc.apps) ? doc.apps.filter((a) => a && a.id && a.launch) : [];
  } catch (_) {
    return [];
  }
}

// Only loopback addresses are probed; the Deck never reaches off this PC to
// decide whether something is running.
function probe(url, timeoutMs) {
  return new Promise((resolve) => {
    let u;
    try { u = new URL(url); } catch (_) { resolve(false); return; }
    if (u.protocol !== "http:" || !/^(127\.0\.0\.1|localhost)$/.test(u.hostname)) { resolve(false); return; }
    const req = http.get({ host: u.hostname, port: u.port || 80, path: u.pathname || "/", timeout: timeoutMs || 700 },
      (res) => { res.resume(); resolve(true); });
    req.on("timeout", () => { req.destroy(); resolve(false); });
    req.on("error", () => resolve(false));
  });
}

// A program counts as available when its executable and every absolute path
// in its arguments exist — a moved project folder shows as missing rather
// than failing silently on click.
function available(launch) {
  if (launch.url) return /^https:\/\//i.test(String(launch.url));
  if (!launch.run) return false;
  const paths = [expand(launch.run)].concat((launch.args || []).map(expand).filter((a) => /^[a-z]:\\/i.test(a)));
  if (launch.cwd) paths.push(expand(launch.cwd));
  return paths.every((p) => { try { return fs.existsSync(p); } catch (_) { return false; } });
}

async function list() {
  const apps = loadApps();
  return Promise.all(apps.map(async (a) => {
    const L = a.launch || {};
    const probeUrl = L.probe || (L.reuse && L.reuse.probe) || "";
    return {
      id: String(a.id),
      name: String(a.name || a.id),
      short: String(a.short || a.name || a.id),
      tagline: String(a.tagline || ""),
      icon: String(a.icon || ""),
      aura: /^#[0-9a-f]{3,8}$/i.test(String(a.aura || "")) ? String(a.aura) : "",
      kind: L.url ? "web" : "app",
      available: available(L),
      running: probeUrl ? await probe(probeUrl, 600) : false,
    };
  }));
}

// The launched program inherits a plain environment: Electron's own variables
// (ELECTRON_RUN_AS_NODE above all) would change how another Electron app
// starts.
function childEnv() {
  const env = {};
  for (const [k, v] of Object.entries(process.env)) {
    if (!/^ELECTRON_/i.test(k)) env[k] = v;
  }
  return env;
}

async function launch(id, shell) {
  const app = loadApps().find((a) => String(a.id) === String(id || ""));
  if (!app) return { ok: false, reason: "unknown" };
  const L = app.launch || {};
  try {
    if (L.reuse && L.reuse.open && await probe(L.reuse.probe, 700)) {
      await shell.openExternal(String(L.reuse.open));
      return { ok: true, how: "reopened" };
    }
    if (L.url) {
      if (!/^https:\/\//i.test(String(L.url))) return { ok: false, reason: "not_https" };
      await shell.openExternal(String(L.url));
      return { ok: true, how: "opened" };
    }
    if (!available(L)) return { ok: false, reason: "missing" };
    const child = spawn(expand(L.run), (L.args || []).map(expand), {
      cwd: L.cwd ? expand(L.cwd) : undefined,
      detached: true,
      stdio: "ignore",
      windowsHide: false,
      env: childEnv(),
    });
    child.on("error", () => {});
    child.unref();
    return { ok: true, how: "launched" };
  } catch (err) {
    return { ok: false, reason: String((err && err.message) || err).slice(0, 160) };
  }
}

/** Wire the two IPC channels. `trusted(event)` gates launching to the HUD window. */
function register(ipcMain, shell, trusted) {
  ipcMain.handle("hud:deck-list", () => list());
  ipcMain.handle("hud:deck-launch", (event, payload) => {
    if (typeof trusted === "function" && !trusted(event)) return { ok: false, reason: "forbidden" };
    return launch(payload && payload.id, shell);
  });
}

module.exports = { register, list, launch, _expand: expand, _available: available };
