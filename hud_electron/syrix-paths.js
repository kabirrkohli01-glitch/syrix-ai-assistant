/* SYRIX runtime path resolver (Electron side).
 *
 * MUST stay rule-identical to ../syrix_paths.py:
 *   1. env SYRIX_STATE_DIR
 *   2. %LOCALAPPDATA%\SYRIX
 *   3. ~\AppData\Local\SYRIX
 *   4. os tmpdir \SYRIX
 *
 * Runtime mailboxes live in <root>\state, logs in <root>\logs, PID files in
 * <root>\run. Required by main.js AND preload.js (CommonJS, Node context only).
 */
"use strict";

const path = require("path");
const fs = require("fs");
const os = require("os");

const RUNTIME_STATE_FILES = new Set([
  "hud_state.json",
  "hud_control.json",
  "hud_chat.json",
  "hud_ready.json",
  "hud_brain_state.json",
  "hud_free_web_state.json",
  "hud_brain_control_result.json",
  "hud_hand_control_event.json",
  "hud_voice_sync.json",
]);

let _cachedRoot = null;
const _ensuredDirs = new Set();

function computeRoot() {
  const envDir = String(process.env.SYRIX_STATE_DIR || "").trim();
  if (envDir) return path.resolve(envDir);
  let local = String(process.env.LOCALAPPDATA || "").trim();
  if (!local) {
    const candidate = path.join(os.homedir(), "AppData", "Local");
    try { if (fs.statSync(candidate).isDirectory()) local = candidate; } catch (_) {}
  }
  if (!local) local = os.tmpdir();
  return path.join(local, "SYRIX");
}

function syrixRoot() {
  if (_cachedRoot === null) _cachedRoot = computeRoot();
  return _cachedRoot;
}

function _ensureDir(p) {
  if (!_ensuredDirs.has(p)) {
    try { fs.mkdirSync(p, { recursive: true }); } catch (_) {}
    _ensuredDirs.add(p);
  }
  return p;
}

function stateDir() { return _ensureDir(path.join(syrixRoot(), "state")); }
function logsDir()  { return _ensureDir(path.join(syrixRoot(), "logs")); }
function cacheDir() { return _ensureDir(path.join(syrixRoot(), "cache")); }
function runDir()   { return _ensureDir(path.join(syrixRoot(), "run")); }

function stateFile(name) { return path.join(stateDir(), path.basename(String(name || ""))); }
function runFile(name)   { return path.join(runDir(), path.basename(String(name || ""))); }
function logFile(name)   { return path.join(logsDir(), path.basename(String(name || ""))); }

/* Cap an append-only log at startup: when over maxBytes, shift to "<name>.1"
 * (replacing any previous .1). Cheap stand-in for rotation on files we only
 * ever open with the "a" flag (autospawn stdio, startup log). */
function capLogAtOpen(filePath, maxBytes) {
  try {
    const st = fs.statSync(filePath);
    if (st.size > maxBytes) {
      const rolled = filePath + ".1";
      try { fs.rmSync(rolled, { force: true }); } catch (_) {}
      fs.renameSync(filePath, rolled);
    }
  } catch (_) {}
  return filePath;
}

module.exports = {
  RUNTIME_STATE_FILES,
  syrixRoot,
  stateDir,
  logsDir,
  cacheDir,
  runDir,
  stateFile,
  runFile,
  logFile,
  capLogAtOpen,
};
