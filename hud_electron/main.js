/* SYRIX desktop interface — Electron main process. */

// Load config.json first, then ../.env overrides. Both set process.env only if key not already set.
(function loadConfig() {
  const fs = require("fs"), path = require("path");
  // 1. config.json (local to hud_electron/)
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(__dirname, "config.json"), "utf8"));
    for (const [k, v] of Object.entries(cfg)) {
      if (k && typeof v === "string" && !(k in process.env)) process.env[k] = v;
    }
  } catch (_) {}
  // 2. ../.env (parent dir) — wins over config.json if both define same key
  try {
    fs.readFileSync(path.join(__dirname, "..", ".env"), "utf8")
      .split(/\r?\n/)
      .forEach(line => {
        const t = line.trim();
        if (!t || t[0] === "#") return;
        const eq = t.indexOf("=");
        if (eq < 0) return;
        const k = t.slice(0, eq).trim();
        const v = t.slice(eq + 1).trim().replace(/^(["'])(.*)\1$/, "$2");
        if (k) process.env[k] = v; // .env always wins
      });
  } catch (_) {}
})();

// Keep Electron security warnings enabled. The active renderer uses prebuilt
// local bundles plus integrity-pinned CDN scripts, so its script CSP no longer
// needs unsafe-inline or unsafe-eval exceptions.

const { app, BrowserWindow, BrowserView, Menu, Tray, desktopCapturer, globalShortcut, ipcMain, nativeImage, screen, session, shell } = require("electron");

/* Keep rendering when the SYRIX window is behind something else.
 *
 * Chromium stops laying out and painting a window it considers OCCLUDED, and it
 * marks a window occluded as soon as another window fully covers it — the OS
 * still reports it visible and not minimised. Everything inside then reports
 * document.hidden = true, layout stops, and lazy renderers (YouTube's video
 * grid, Gmail's message list) never populate.
 *
 * That is fatal for Supercomputer specifically, because the whole point of
 * backgrounding a task is that the user is doing something else — which means the
 * SYRIX window is behind his other windows. Measured on a live YouTube tab in
 * exactly that state: the agent's page digest was 483 characters, it reported
 * "the page content is heavily truncated", could not find the account menu, and
 * the upload task failed with the page sitting right there. The HUD's own
 * document reported hidden = true at the same moment.
 *
 * These three switches are the standard Chromium opt-out and cost nothing but a
 * little GPU while a window is covered. Set before app ready, which is the only
 * time command-line switches are read. */
for (const sw of ["disable-backgrounding-occluded-windows",
                  "disable-renderer-backgrounding",
                  "disable-background-timer-throttling"]) {
  try { app.commandLine.appendSwitch(sw); } catch (_) {}
}
// Embedded pages must not open a direct UDP/WebRTC path around the guarded
// browser proxy. Camera/mic remain denied separately by the session policy.
try {
  app.commandLine.appendSwitch("force-webrtc-ip-handling-policy", "disable_non_proxied_udp");
} catch (_) {}

const fs = require("fs");
const crypto = require("crypto");
const path = require("path");
const net = require("net");
const http = require("http");
const os = require("os");
const { pathToFileURL } = require("url");
const { spawn, execFileSync } = require("child_process");

const _SYRIX_ROOT = path.join(__dirname, "..");
// Phase 0 (2026-06-10): runtime state lives under %LOCALAPPDATA%\SYRIX (OneDrive
// sync locks were stalling hot writes). syrix-paths.js mirrors ../syrix_paths.py.
const _syrixPaths = require("./syrix-paths.js");
const _SYRIX_STARTUP_LOG_PATH = _syrixPaths.capLogAtOpen(_syrixPaths.logFile("syrix_startup.log"), 5 * 1024 * 1024);
const _SYRIX_HUD_READY_PATH = _syrixPaths.stateFile("hud_ready.json");
const _SYRIX_HUD_STATE_PATH = _syrixPaths.stateFile("hud_state.json");

// One ephemeral nonce authenticates the trusted HUD renderer to the loopback
// WebSocket for this process tree. Backend-first launches set it before
// spawning Electron; Electron-first launches generate it here and pass it only
// through the inherited environment to the Python child. Never persist/log it.
const _SYRIX_HUD_WS_TOKEN_ENV = "SYRIX_HUD_WS_TOKEN";
const _inheritedHudWsToken = String(process.env[_SYRIX_HUD_WS_TOKEN_ENV] || "");
const _SYRIX_HUD_WS_BOOT_TOKEN = /^[0-9a-fA-F]{64}$/.test(_inheritedHudWsToken)
  ? _inheritedHudWsToken
  : crypto.randomBytes(32).toString("hex");
process.env[_SYRIX_HUD_WS_TOKEN_ENV] = _SYRIX_HUD_WS_BOOT_TOKEN;

const _SYRIX_MAIN_LOG_RING = [];
const _SYRIX_LOG_SECRET_RE = /(bearer\s+)[a-z0-9._~+/-]+|((?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|session[_-]?token|client[_-]?secret|authorization|cookie|set-cookie|password|passwd|pwd|secret|credential)\s*[=:]\s*)[^\s,&"']+/ig;
const _SYRIX_LOG_URL_RE = /\b(?:https?|wss?|file):\/\/[^\s"'<>]+/ig;
function _syrixRedactLogLine(line) {
  return String(line || "")
    .replace(_SYRIX_LOG_SECRET_RE, (_m, bearer, key) => (bearer || key || "") + "[REDACTED]")
    .replace(_SYRIX_LOG_URL_RE, "[URL_REDACTED]");
}
function _syrixStartupLog(line) {
  const msg = _syrixRedactLogLine(String(line || ""));
  try { console.log(msg); } catch (_) {}
  try {
    fs.mkdirSync(path.dirname(_SYRIX_STARTUP_LOG_PATH), { recursive: true });
    fs.appendFileSync(_SYRIX_STARTUP_LOG_PATH, `[${new Date().toISOString()}] ${msg}\n`, "utf8");
  } catch (_) {}
}

// Electron can be launched directly from a shortcut, which bypasses
// `npm start` and therefore its `node build.js` step.  The renderer loads
// dist/*.js, not the JSX source, so a newer source file would otherwise look
// like an update that "didn't apply".  Rebuild only when a compiled file is
// missing or older than its matching source file.
function _syrixEnsureHudDistCurrent() {
  if (app.isPackaged) return true;
  const sources = [
    "syrix-answer-renderer.jsx", "syrix-adaptive-cards.jsx", "syrix-primary-chat.jsx",
    "syrix-primary-widgets.jsx", "syrix-pass2-widgets.jsx", "syrix-hud-app-dock.jsx",
    "syrix-3d-sandbox.jsx", "syrix-activity-feed.jsx", "syrix-zenith-panel.jsx",
    "syrix-connectors-panel.jsx", "syrix-supercomputer-workspace.jsx",
    "syrix-island-hub.jsx", "syrix-aurora-shell.jsx",
    "syrix-boot-cinematic.jsx", "syrix-primary-app.jsx",
  ];
  try {
    const stale = sources.some((source) => {
      const input = path.join(__dirname, source);
      const output = path.join(__dirname, "dist", source.replace(/\.jsx$/i, ".js"));
      return !fs.existsSync(output) || fs.statSync(input).mtimeMs > fs.statSync(output).mtimeMs;
    });
    if (!stale) {
      _syrixStartupLog("[HUD_BUILD] status=current action=skip");
      return true;
    }
    const nodeCandidates = [
      process.env.SYRIX_NODE_EXE,
      process.env.NODE_EXE,
      process.platform === "win32" ? path.join(process.env.ProgramFiles || "C:\\Program Files", "nodejs", "node.exe") : "node",
      "node",
    ].filter(Boolean);
    const nodeExe = nodeCandidates.find((candidate) => {
      try {
        return String(candidate).toLowerCase() === "node" || fs.existsSync(candidate);
      } catch (_) {
        return false;
      }
    });
    if (!nodeExe) throw new Error("node_not_found");
    execFileSync(nodeExe, [path.join(__dirname, "build.js")], {
      cwd: __dirname,
      windowsHide: true,
      timeout: 20000,
      stdio: "ignore",
    });
    _syrixStartupLog("[HUD_BUILD] status=rebuilt reason=source_newer_than_dist");
    return true;
  } catch (err) {
    _syrixStartupLog(`[HUD_BUILD] status=failed action=continue err=${err && err.message ? err.message : err}`);
    return false;
  }
}
function _syrixRememberMainLog(level, args) {
  try {
    const msg = Array.from(args || []).map((v) => {
      if (typeof v === "string") return v;
      try { return JSON.stringify(v); } catch (_) { return String(v); }
    }).join(" ");
    _SYRIX_MAIN_LOG_RING.push({
      ts: new Date().toISOString(),
      level: String(level || "info").toUpperCase(),
      line: _syrixRedactLogLine(msg).slice(0, 1200),
      source: "electron",
    });
    if (_SYRIX_MAIN_LOG_RING.length > 300) _SYRIX_MAIN_LOG_RING.splice(0, _SYRIX_MAIN_LOG_RING.length - 300);
  } catch (_) {}
}
(function installSyrixConsoleBuffer() {
  try {
    ["log", "info", "warn", "error"].forEach((name) => {
      const original = console[name] || console.log;
      console[name] = function syrixBufferedConsole() {
        _syrixRememberMainLog(name === "log" ? "info" : name, arguments);
        return original.apply(console, arguments);
      };
    });
  } catch (_) {}
})();

function _syrixSleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

let _SYRIX_LAST_HUD_STATE_WRITE_WARN = 0;
function _syrixHudStateWriteWarn(message) {
  const now = Date.now();
  if (now - _SYRIX_LAST_HUD_STATE_WRITE_WARN < 60000) return;
  _SYRIX_LAST_HUD_STATE_WRITE_WARN = now;
  try { console.warn(message); } catch (_) {}
}

function _syrixWriteJsonAtomicSync(targetPath, payload) {
  const tmp = `${targetPath}.electron.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`;
  let fd = null;
  try {
    fs.mkdirSync(path.dirname(targetPath), { recursive: true });
    fd = fs.openSync(tmp, "w");
    fs.writeFileSync(fd, JSON.stringify(payload, null, 2), "utf8");
    try { fs.fsyncSync(fd); } catch (_) {}
    fs.closeSync(fd);
    fd = null;
    fs.renameSync(tmp, targetPath);
    return true;
  } catch (err) {
    try { if (fd !== null) fs.closeSync(fd); } catch (_) {}
    try { fs.rmSync(tmp, { force: true }); } catch (_) {}
    throw err;
  }
}

async function _syrixMergeHudState(fields) {
  if (!fields || typeof fields !== "object") return false;
  let lastErr = null;
  const waits = [20, 35, 50, 75, 110, 160, 220];
  for (let attempt = 0; attempt < waits.length; attempt += 1) {
    try {
      let state = {};
      try {
        state = JSON.parse(fs.readFileSync(_SYRIX_HUD_STATE_PATH, "utf8")) || {};
      } catch (_) {
        state = {};
      }
      Object.assign(state, fields);
      _syrixWriteJsonAtomicSync(_SYRIX_HUD_STATE_PATH, state);
      return true;
    } catch (err) {
      lastErr = err;
      await _syrixSleep(waits[attempt]);
    }
  }
  _syrixHudStateWriteWarn("[HUD_APP_ACK] hud_state write failed: " + (lastErr && lastErr.message ? lastErr.message : lastErr) + " suspected_onedrive_lock=true");
  return false;
}

function _syrixTailTextFile(file, maxBytes) {
  try {
    if (!file || !fs.existsSync(file)) return [];
    const st = fs.statSync(file);
    const size = Math.max(0, Number(st.size || 0));
    const len = Math.min(size, Math.max(4096, Number(maxBytes || 160000)));
    const fd = fs.openSync(file, "r");
    const buf = Buffer.alloc(len);
    fs.readSync(fd, buf, 0, len, Math.max(0, size - len));
    fs.closeSync(fd);
    return buf.toString("utf8").split(/\r?\n/).filter(Boolean).map((line) => ({
      ts: "",
      level: "",
      line: _syrixRedactLogLine(line).slice(0, 1200),
      source: path.basename(file),
    }));
  } catch (err) {
    return [{
      ts: new Date().toISOString(),
      level: "WARN",
      line: _syrixRedactLogLine("Could not read log file: " + (err && err.message ? err.message : err)),
      source: "electron",
    }];
  }
}

// Backend (Python) auto-spawn. If the HUD launches standalone (user double-
// clicked Electron) and ws://127.0.0.1:5111 is dead, fire up syrix_host.py
// as a tracked child so Electron can coordinate its graceful shutdown. Python checks
// SYRIX_HUD_SPAWNED_BY_ELECTRON=1 and skips its own HUD spawn to avoid a
// double window. Skipped entirely when SYRIX_NO_BACKEND_AUTOSPAWN=1.
const _SYRIX_BACKEND_PORT = 5111;
let _syrixPythonProc = null;
let _syrixBackendStartPromise = null;
const _SYRIX_SHUTDOWN_STATUS_PATH = _syrixPaths.runFile("shutdown_status.json");
let _SYRIX_CLOSE_REQUEST_ID = "";

function _syrixReadShutdownStatus() {
  try {
    const raw = JSON.parse(fs.readFileSync(_SYRIX_SHUTDOWN_STATUS_PATH, "utf8"));
    return raw && typeof raw === "object" ? raw : null;
  } catch (_) {
    return null;
  }
}

function _syrixBackendProcessExited() {
  return !_syrixPythonProc || _syrixPythonProc.exitCode !== null || _syrixPythonProc.killed;
}

async function _syrixWaitForGracefulBackend(requestId, timeoutMs) {
  const deadline = Date.now() + Math.max(1000, Number(timeoutMs || 7000));
  // A backend Electron spawned is watched through its process handle. Probing
  // port 5111 instead opened a raw TCP connection every ~100 ms, and the
  // backend logged each one as a failed WebSocket handshake with a traceback.
  const ownsBackend = !!_syrixPythonProc;
  while (Date.now() < deadline) {
    const status = _syrixReadShutdownStatus();
    if (status && status.stage === "done" && String(status.request_id || "") === String(requestId || "")) {
      // The backend writes the done marker immediately before os._exit(); give
      // the process handle one short turn to observe that exit before closing
      // Electron.
      const exitDeadline = Date.now() + 500;
      while (!_syrixBackendProcessExited() && Date.now() < exitDeadline) await _syrixSleep(50);
      return _syrixBackendProcessExited();
    }
    if (ownsBackend) {
      if (_syrixBackendProcessExited()) return true;
      await _syrixSleep(50);
      continue;
    }
    const backendPortOpen = await _syrixIsPortOpen(_SYRIX_BACKEND_PORT, 120);
    if (!backendPortOpen && status &&
        String(status.request_id || "") === String(requestId || "") &&
        (status.stage === "started" || status.stage === "requested")) {
      return true;
    }
    await _syrixSleep(250);
  }
  return false;
}

function _syrixTaskkillTreeSync(pid, reason) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0 || n === process.pid) return false;
  try {
    execFileSync("taskkill.exe", ["/PID", String(n), "/T", "/F"], {
      windowsHide: true,
      timeout: 4000,
      stdio: "ignore",
    });
    console.log(`[SYRIX_SHUTDOWN] process_tree_killed pid=${n} reason=${reason || "fallback"}`);
    return true;
  } catch (_) {
    return false;
  }
}

function _syrixOwnedProcessSnapshot() {
  if (process.platform !== "win32") return [];
  const ps = [
    "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|py|electron|node|piper|ffmpeg)(\\.exe)?$' } | Select-Object Name,ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress",
  ];
  try {
    const raw = execFileSync("powershell.exe", ["-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps[0]], {
      windowsHide: true,
      timeout: 2500,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch (_) {
    return [];
  }
}

function _syrixIsOwnedProcess(row) {
  const name = String(row && row.Name || "").toLowerCase();
  const cmd = String(row && row.CommandLine || "").toLowerCase();
  const root = _SYRIX_ROOT.toLowerCase();
  if (!cmd || !name) return false;
  if (name === "python.exe" || name === "pythonw.exe" || name === "py.exe") {
    return cmd.includes(root) || cmd.includes("syrix_host.py") || cmd.includes("syrix_host_piper_tts");
  }
  if (name === "electron.exe" || name === "node.exe") {
    // This instance and its own renderer, GPU and utility helpers are not
    // sweep targets. They exit with the main process, and killing them while
    // the main process is still tearing down can end it before it finishes.
    if (name === "electron.exe" &&
        (Number(row.ProcessId) === process.pid || Number(row.ParentProcessId) === process.pid)) {
      return false;
    }
    return cmd.includes(root) || cmd.includes("hud_electron");
  }
  if (name === "piper.exe" || name === "ffmpeg.exe") return cmd.includes(root) || cmd.includes("piper_env");
  // 2026-07-25 — Launch_SYRIX.bat runs under a cmd.exe console that outlived
  // every previous sweep, because only the interpreter names were matched. The
  // console holds the backend's stdio and keeps the tree alive.
  if (name === "cmd.exe" || name === "conhost.exe") {
    return cmd.includes("launch_syrix") || cmd.includes("syrix_debug") || cmd.includes(root);
  }
  return false;
}

// 2026-07-25 — Release the models SYRIX loaded into Ollama, whoever owns the
// server.
//
// The ownership PID file is only written when SYRIX *starts* Ollama. On this
// machine the Ollama tray app auto-starts at login, so SYRIX adopts a running
// server, never records ownership, and therefore never releases anything — the
// models it loaded sat in RAM and VRAM until the box was rebooted. That was the
// bulk of the "SYRIX is off but still using my memory" complaint.
//
// Unloading is not killing: `keep_alive: 0` tells Ollama to evict the model
// immediately. A user-managed daemon keeps running, just empty-handed. Killing
// the server is still reserved for the case where SYRIX genuinely owns it.
function _syrixOllamaRequest(method, url, body, timeoutMs) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (value) => { if (!done) { done = true; resolve(value); } };
    try {
      const target = new URL(url);
      const payload = body ? JSON.stringify(body) : "";
      const req = http.request({
        method,
        hostname: target.hostname,
        port: target.port || 80,
        path: target.pathname,
        headers: payload ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(payload) } : {},
        timeout: timeoutMs,
      }, (res) => {
        let text = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => { if (text.length < 262144) text += chunk; });
        res.on("end", () => { try { finish(JSON.parse(text)); } catch (_) { finish({}); } });
      });
      req.on("timeout", () => { try { req.destroy(); } catch (_) {} finish(null); });
      req.on("error", () => finish(null));
      if (payload) req.write(payload);
      req.end();
    } catch (_) {
      finish(null);
    }
  });
}

async function _syrixReleaseOllamaModels(reason) {
  const host = String(process.env.OLLAMA_HOST || "http://127.0.0.1:11434").replace(/\/+$/, "");
  const ps = await _syrixOllamaRequest("GET", `${host}/api/ps`, null, 1200);
  const loaded = (ps && Array.isArray(ps.models) ? ps.models : [])
    .map((m) => String((m && (m.name || m.model)) || "").trim())
    .filter(Boolean);
  if (!loaded.length) return 0;
  const results = await Promise.all(loaded.map((model) =>
    _syrixOllamaRequest("POST", `${host}/api/generate`, { model, keep_alive: 0 }, 2500)));
  const released = results.filter((r) => r !== null).length;
  _syrixStartupLog(`[SYRIX_SHUTDOWN] ollama_models_released count=${released} reason=${reason || "shutdown"}`);
  return released;
}

// Prove the sweep worked instead of assuming it. Re-scans, kills anything that
// survived, and returns what is still standing so the caller can log it.
function _syrixVerifyOwnedProcessesGone(reason) {
  if (process.platform !== "win32") return [];
  let survivors = [];
  for (let attempt = 0; attempt < 2; attempt += 1) {
    survivors = _syrixOwnedProcessSnapshot()
      .filter((row) => _syrixIsOwnedProcess(row))
      .filter((row) => Number(row.ProcessId) !== process.pid);
    if (!survivors.length) break;
    for (const row of survivors) _syrixTaskkillTreeSync(row.ProcessId, `verify_${reason || "shutdown"}`);
  }
  if (survivors.length) {
    const detail = survivors.map((r) => `${r.Name}:${r.ProcessId}`).join(",");
    _syrixStartupLog(`[SYRIX_SHUTDOWN] survivors_after_verify count=${survivors.length} procs=${detail}`);
  } else {
    _syrixStartupLog(`[SYRIX_SHUTDOWN] verified_clean reason=${reason || "shutdown"}`);
  }
  return survivors;
}
function _syrixIsPortOpen(port, timeoutMs) {
  return new Promise((resolve) => {
    const sock = new net.Socket();
    let done = false;
    const finish = (ok) => {
      if (done) return;
      done = true;
      try { sock.destroy(); } catch (_) {}
      resolve(ok);
    };
    sock.setTimeout(timeoutMs);
    sock.once("connect", () => finish(true));
    sock.once("timeout", () => finish(false));
    sock.once("error", () => finish(false));
    sock.connect(port, "127.0.0.1");
  });
}
function _syrixResolvePythonExe() {
  // Prefer pythonw.exe so the backend has NO console attached. python.exe
  // (even with windowsHide:true) still gets a hidden console; the Intel
  // Fortran runtime loaded by numpy/MKL via chromadb registers a console
  // ctrl-handler that aborts on CTRL_CLOSE_EVENT (the "forrtl: error 200:
  // program aborting due to window-CLOSE event" crash we hit at boot).
  // pythonw.exe = no console = no console event = no abort.
  const root = path.join(__dirname, "..");
  const venvW = path.join(root, ".venv311", "Scripts", "pythonw.exe");
  if (fs.existsSync(venvW)) return venvW;
  const venv = path.join(root, ".venv311", "Scripts", "python.exe");
  if (fs.existsSync(venv)) return venv;
  return process.platform === "win32" ? "pythonw.exe" : "python";
}
async function _syrixEnsureBackendRunning() {
  if (typeof gotLock !== "undefined" && !gotLock) return;
  if (String(process.env.SYRIX_NO_BACKEND_AUTOSPAWN || "").trim() === "1") {
    _syrixStartupLog("[BACKEND] backend_autospawn_skipped env=SYRIX_NO_BACKEND_AUTOSPAWN");
    return;
  }
  // Clear the chat log so every session starts with a blank slate.
  try {
    // Atomic (2026-07-19 audit): a plain writeFileSync here raced the Python
    // reader at boot — same torn-read class the state writers already fixed.
    _syrixWriteJsonAtomicSync(_syrixPaths.stateFile("hud_chat.json"), { lines: [] });
    _syrixStartupLog("[STARTUP] hud_chat_cleared fresh_session=true");
  } catch (_) {}
  _syrixStartupLog(`[BACKEND] backend_probe port=${_SYRIX_BACKEND_PORT}`);
  const alive = await _syrixIsPortOpen(_SYRIX_BACKEND_PORT, 600);
  if (alive) {
    _syrixStartupLog(`[PROCESS_GUARD] port=${_SYRIX_BACKEND_PORT} owner=existing_backend action=skip_spawn`);
    return;
  }
  const root = _SYRIX_ROOT;
  const script = path.join(root, "syrix_host.py");
  if (!fs.existsSync(script)) {
    _syrixStartupLog(`[SYRIX_BACKEND] syrix_host.py_not_found path=${script} action=skip_spawn`);
    return;
  }
  const pyExe = _syrixResolvePythonExe();
  _syrixStartupLog(`[SYRIX_BACKEND] backend_spawn_attempt exe=${pyExe} script=${script}`);
  try {
    const aliveNow = await _syrixIsPortOpen(_SYRIX_BACKEND_PORT, 100);
    if (aliveNow) {
      _syrixStartupLog(`[PROCESS_GUARD] port=${_SYRIX_BACKEND_PORT} owner=existing_backend action=skip_spawn_late`);
      return;
    }
    const env = Object.assign({}, process.env, {
      SYRIX_HUD_SPAWNED_BY_ELECTRON: "1",
      SYRIX_HUD_WS_TOKEN: _SYRIX_HUD_WS_BOOT_TOKEN,
      // Both sides must resolve the same runtime tree even under an override.
      SYRIX_STATE_DIR: _syrixPaths.syrixRoot(),
      PYTHONIOENCODING: "utf-8",
      PYTHONUTF8: "1",
      // Belt-and-suspenders against the Fortran/MKL CTRL_CLOSE_EVENT abort:
      // even with pythonw.exe (no console), these tell Intel's runtime and
      // OpenMP not to install their own console ctrl-handlers.
      FOR_DISABLE_CONSOLE_CTRL_HANDLER: "1",
      KMP_HANDLE_SIGNALS: "0",
    });
    const outLog = fs.openSync(_syrixPaths.capLogAtOpen(_syrixPaths.logFile("backend_autospawn.log"), 5 * 1024 * 1024), "a");
    _syrixPythonProc = spawn(pyExe, ["-X", "utf8", script], {
      cwd: root,
      env,
      stdio: ["ignore", outLog, outLog],
      windowsHide: true,
    });
    _syrixStartupLog(`[SYRIX_BACKEND] backend_started pid=${_syrixPythonProc.pid}`);
  } catch (err) {
    _syrixStartupLog(`[SYRIX_BACKEND] spawn_failed err=${err && err.message ? err.message : err}`);
  }
}

function _syrixStartBackendAfterHudVisible(reason) {
  if (_syrixBackendStartPromise) return _syrixBackendStartPromise;
  _syrixStartupLog(`[BACKEND] backend_start_deferred_until_hud_visible reason=${reason || "startup"}`);
  _syrixBackendStartPromise = _syrixEnsureBackendRunning().catch((err) => {
    _syrixStartupLog(`[BACKEND] backend_start_failed err=${err && err.message ? err.message : err}`);
  });
  return _syrixBackendStartPromise;
}

function _syrixEnvFlag(name, defaultValue) {
  const raw = process.env[name];
  if (raw === undefined || raw === null || String(raw).trim() === "") return !!defaultValue;
  return !/^(?:0|false|no|off|disabled)$/i.test(String(raw).trim());
}

function _syrixSafePartitionId(raw) {
  const base = String(raw || "").trim().replace(/^persist:/i, "");
  const cleaned = base
    .replace(/[^a-z0-9._-]/ig, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
  return cleaned || "syrix-hud-browser";
}

const HUD_BROWSER_DATA_ROOT = path.join(__dirname, ".electron_user_data");
const HUD_BROWSER_USER_DATA_PATH = path.join(HUD_BROWSER_DATA_ROOT, "userdata");
const HUD_BROWSER_CACHE_PATH = path.join(HUD_BROWSER_DATA_ROOT, "cache");

// Persistent partition for HUD browsing. This is an Electron profile stored
// under HUD_BROWSER_USER_DATA_PATH; it never reads Chrome/Edge/default browser
// cookies, cache, saved passwords, or login state.
// Toggle persistence via HUD_APP_PERSISTENT_SESSION env (default ON).
const HUD_APP_PERSISTENT_SESSION =
  _syrixEnvFlag("HUD_APP_PERSISTENT_SESSION", true);
const HUD_BROWSER_PARTITION_ID = _syrixSafePartitionId(
  process.env.HUD_BROWSER_PARTITION_ID || process.env.HUD_APP_PARTITION_ID || "syrix-hud-browser"
);
const HUD_APP_PARTITION = HUD_APP_PERSISTENT_SESSION
  ? "persist:" + HUD_BROWSER_PARTITION_ID
  : "";
const HUD_BROWSER_EPHEMERAL_PARTITION = _syrixSafePartitionId(HUD_BROWSER_PARTITION_ID + "-ephemeral");
const HUD_BROWSER_SESSION_PARTITION = HUD_APP_PARTITION || HUD_BROWSER_EPHEMERAL_PARTITION;
const HUD_BROWSER_LEGACY_PARTITIONS = ["persist:syrix-hud-app"].filter((p) => p !== HUD_APP_PARTITION);
const HUD_BROWSER_CLEAR_DATA_ON_STARTUP =
  _syrixEnvFlag("HUD_BROWSER_CLEAR_DATA_ON_STARTUP", false) ||
  _syrixEnvFlag("HUD_APP_CLEAR_SESSION_ON_STARTUP", false);
const HUD_BROWSER_ALLOW_EXTERNAL_POPOUT =
  _syrixEnvFlag("HUD_BROWSER_ALLOW_EXTERNAL_POPOUT", false);
const HUD_BROWSER_BLOCKED_PERMISSIONS = new Set([
  "background-sync",
  "bluetooth",
  "camera",
  "clipboard-read",
  "clipboard-sanitized-write",
  "clipboard-write",
  "display-capture",
  "file-system",
  "filesystem",
  "geolocation",
  "hid",
  "idle-detection",
  "local-fonts",
  "media",
  "mediaKeySystem",
  "midi",
  "midiSysex",
  "microphone",
  "notifications",
  "openExternal",
  "payment-handler",
  "pointerLock",
  "serial",
  "usb",
  "videoCapture",
  "window-management",
]);
let _hudBrowserPermissionsConfigured = false;
let _hudBrowserNetworkReady = false;
let _hudBrowserNetworkConfiguring = null;
let _hudSafeProxy = null;
const _hudBrowserGuardedPartitions = new Set();

(function configureElectronUserData() {
  try {
    fs.mkdirSync(HUD_BROWSER_USER_DATA_PATH, { recursive: true });
    fs.mkdirSync(HUD_BROWSER_CACHE_PATH, { recursive: true });
  } catch (_) {}
  try {
    app.setPath("userData", HUD_BROWSER_USER_DATA_PATH);
    app.setPath("cache", HUD_BROWSER_CACHE_PATH);
    console.log("[HUD_ELECTRON] userData=", HUD_BROWSER_USER_DATA_PATH, "cache=", HUD_BROWSER_CACHE_PATH);
  } catch (_) {}
})();

async function _hudBrowserClearSessionData(reason) {
  const partitions = [HUD_BROWSER_SESSION_PARTITION]
    .concat(HUD_BROWSER_LEGACY_PARTITIONS)
    .filter(Boolean);
  const clearedPartitions = [];
  try {
    for (const partition of partitions) {
      const s = session.fromPartition(partition);
      await s.clearStorageData();
      try { await s.clearCache(); } catch (_) {}
      try { await s.cookies.flushStore(); } catch (_) {}
      clearedPartitions.push(partition);
    }
    _syrixStartupLog(`[HUD_BROWSER] data_clear ok=true reason=${reason || "manual"} partitions=${clearedPartitions.length}`);
    return {
      ok: true,
      cleared: clearedPartitions.length > 0,
      partition: HUD_BROWSER_SESSION_PARTITION,
      clearedPartitions,
    };
  } catch (exc) {
    const msg = String((exc && exc.message) || exc).slice(0, 200);
    _syrixStartupLog(`[HUD_BROWSER] data_clear ok=false reason=${reason || "manual"} err=${msg}`);
    return { ok: false, reason: msg };
  }
}

function _hudBrowserPermissionName(permission) {
  return String(permission || "").trim();
}

function _hudBrowserDenyPermission(permission, details) {
  const name = _hudBrowserPermissionName(permission);
  const key = name.toLowerCase();
  const blocked = HUD_BROWSER_BLOCKED_PERMISSIONS.has(name) || HUD_BROWSER_BLOCKED_PERMISSIONS.has(key);
  // HUD app content runs without preload/Node. Keep website auth/storage
  // working, but deny the dangerous Chromium permission families by default.
  const deny = blocked;
  try {
    if (deny) {
      let domain = "";
      try {
        const requestingUrl = String((details && (details.requestingUrl || details.embeddingOrigin || details.securityOrigin)) || "");
        domain = requestingUrl ? new URL(requestingUrl).hostname : "";
      } catch (_) {}
      _hudAppDiag("permission_denied", { permission: name || "unknown", domain, listed: blocked ? "true" : "false" });
    }
  } catch (_) {}
  return deny;
}

function _configureHudBrowserPermissions(reason) {
  if (_hudBrowserPermissionsConfigured) return;
  _hudBrowserPermissionsConfigured = true;
  try {
    const ses = session.fromPartition(HUD_BROWSER_SESSION_PARTITION);
    try {
      ses.setPermissionRequestHandler((_webContents, permission, callback, details) => {
        const deny = _hudBrowserDenyPermission(permission, details);
        try { callback(!deny); } catch (_) {}
      });
    } catch (_) {}
    try {
      ses.setPermissionCheckHandler((_webContents, permission, requestingOrigin, details) => {
        return !_hudBrowserDenyPermission(permission, Object.assign({}, details || {}, {
          requestingUrl: requestingOrigin || (details && details.requestingUrl) || "",
        }));
      });
    } catch (_) {}
    try {
      ses.setDevicePermissionHandler(() => false);
    } catch (_) {}
    try {
      ses.setDisplayMediaRequestHandler((_request, callback) => {
        try { callback({}); } catch (_) {}
      });
    } catch (_) {}
    for (const eventName of ("select-usb-device,select-serial-port,select-bluetooth-device,select-hid-device").split(",")) {
      try {
        ses.on(eventName, (event, _details, callback) => {
          try { event.preventDefault(); } catch (_) {}
          try { callback(""); } catch (_) {}
          try { _hudAppDiag("device_permission_denied", { permission: eventName }); } catch (_) {}
        });
      } catch (_) {}
    }
    try {
      if (typeof ses.setUSBProtectedClassesHandler === "function") {
        ses.setUSBProtectedClassesHandler(() => []);
      }
    } catch (_) {}
    _syrixStartupLog(`[HUD_BROWSER] permissions_hardened partition=${HUD_BROWSER_SESSION_PARTITION} reason=${reason || "startup"}`);
  } catch (exc) {
    _syrixStartupLog(`[HUD_BROWSER] permissions_harden_failed err=${exc && exc.message ? exc.message : exc}`);
  }
}

async function _configureHudBrowserNetwork(reason) {
  if (_hudBrowserNetworkReady && _hudSafeProxy) return true;
  if (_hudBrowserNetworkConfiguring) return _hudBrowserNetworkConfiguring;
  _hudBrowserNetworkConfiguring = (async () => {
    let candidate = null;
    try {
      const { createSafeProxy } = require("./syrix-safe-proxy.js");
      candidate = await createSafeProxy();
      const configured = await _guardHudBrowserPartition(
        HUD_BROWSER_SESSION_PARTITION,
        candidate,
        reason || "startup",
      );
      if (!configured) throw new Error("primary_partition_guard_failed");
      _hudSafeProxy = candidate;
      _hudBrowserNetworkReady = true;
      _syrixStartupLog(`[HUD_BROWSER] network_guard_ready ok=true reason=${reason || "startup"}`);
      return true;
    } catch (exc) {
      _hudBrowserNetworkReady = false;
      if (candidate) {
        try { await candidate.close(); } catch (_) {}
      }
      const msg = String((exc && exc.message) || exc || "guard_setup_failed").slice(0, 160);
      _syrixStartupLog(`[HUD_BROWSER] network_guard_ready ok=false reason=${reason || "startup"} err=${msg}`);
      return false;
    } finally {
      _hudBrowserNetworkConfiguring = null;
    }
  })();
  return _hudBrowserNetworkConfiguring;
}

async function _guardHudBrowserPartition(partition, proxy, reason) {
  const partitionId = String(partition || "").trim();
  const activeProxy = proxy || _hudSafeProxy;
  if (!partitionId || !activeProxy || !activeProxy.proxyRules) return false;
  if (_hudBrowserGuardedPartitions.has(partitionId)) return true;
  try {
    const ses = session.fromPartition(partitionId);
    await ses.setProxy({
      proxyRules: activeProxy.proxyRules,
      // Chromium normally bypasses proxies for loopback. Removing that
      // implicit bypass ensures localhost and LAN subresources reach the
      // guard and are rejected rather than connected directly.
      proxyBypassRules: "<-loopback>",
    });
    try { await ses.forceReloadProxyConfig(); } catch (_) {}
    _hudBrowserGuardedPartitions.add(partitionId);
    _syrixStartupLog(`[HUD_BROWSER] partition_guarded ok=true reason=${reason || "runtime"}`);
    return true;
  } catch (exc) {
    const msg = String((exc && exc.message) || exc || "partition_guard_failed").slice(0, 160);
    _syrixStartupLog(`[HUD_BROWSER] partition_guarded ok=false reason=${reason || "runtime"} err=${msg}`);
    return false;
  }
}

// Shared fail-closed bootstrap for any embedded browser subsystem.  Modules
// stay lazy, their IPC registration remains owned by the module, and no view
// can be used until its actual Electron partition has the safe proxy.
const _guardedBrowserModules = new Map();
const _guardedBrowserPending = new Map();

async function _ensureGuardedBrowserModule(spec = {}) {
  const key = String(spec.key || "").trim();
  if (!key) return { ok: false, reason: "browser_module_key_missing" };
  if (_guardedBrowserPending.has(key)) return _guardedBrowserPending.get(key);
  const pending = (async () => {
    try {
      if (!_hudBrowserNetworkReady
          && !await _configureHudBrowserNetwork(spec.networkReason || key)) {
        return { ok: false, reason: "browser_network_guard_unavailable" };
      }
      let module = _guardedBrowserModules.get(key);
      if (!module) {
        module = spec.load();
        spec.install(module);
        _guardedBrowserModules.set(key, module);
      }
      const partition = String(spec.partition(module) || "").trim();
      if (!partition || !await _guardHudBrowserPartition(
        partition,
        _hudSafeProxy,
        spec.partitionReason || key,
      )) {
        return { ok: false, reason: "browser_network_guard_unavailable" };
      }
      return spec.snapshot(module);
    } catch (exc) {
      const kind = String((exc && exc.name) || "Error").slice(0, 80);
      _syrixStartupLog(`[HUD_BROWSER] module_guard_failed key=${key} kind=${kind}`);
      return { ok: false, reason: "browser_module_guard_failed" };
    }
  })();
  _guardedBrowserPending.set(key, pending);
  try {
    return await pending;
  } finally {
    if (_guardedBrowserPending.get(key) === pending) {
      _guardedBrowserPending.delete(key);
    }
  }
}

function _disposeGuardedBrowserModules() {
  for (const module of _guardedBrowserModules.values()) {
    try { if (module && typeof module.dispose === "function") module.dispose(); } catch (_) {}
  }
  _guardedBrowserModules.clear();
  _guardedBrowserPending.clear();
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  _syrixStartupLog("[PROCESS_GUARD] duplicate_detected=electron action=quit");
  app.quit();
} else {
  _syrixStartupLog("[PROCESS_GUARD] electron_instance=primary lock=acquired");
  _syrixStartupLog(`[STARTUP] electron_entrypoint=${__filename} cwd=${process.cwd()} pid=${process.pid} argv=${JSON.stringify(process.argv)}`);
  _syrixStartupLog("[HUD_GUARD] canonical_hud_entrypoint=hud_electron/main.js renderer=index.html legacy_renderer=blocked");
}

let mainWindow = null;
let _hudReadyHeartbeat = null;
let _hudShowRecoveryTimer = null;
let _hudStartupVisibilityMonitor = null;
let _hudStartupVisibilityMonitorUntil = 0;
// Core Stability Pass (2026-05-17):
//   When the user clicks the X / closes the HUD, we want a full backend
//   shutdown — not Electron walking away while Python / mic / camera /
//   STT / wake listeners keep running in the background.
//
//   The renderer has the live WebSocket (SyrixBridge) — main.js does not —
//   so we ask the renderer to send the `app_close` command via WS before
//   we actually destroy the window. `_APP_CLOSE_GRACE_MS` is the bounded wait
//   before the ownership-scoped final fallback so a stuck / hung renderer never
//   blocks close.
let _appQuitRequested = false;
let _appShutdownInProgress = false;
let _appShutdownFinalized = false;
// The backend's own teardown takes well under a second once app_close
// arrives (it starts on the WebSocket receive thread). Anything still alive
// after this window is ended by the process-tree kill, so a slow backend can
// never keep SYRIX open after its window has gone. Twelve seconds here kept a
// dying instance holding the single-instance lock long enough that reopening
// SYRIX revived the closed window instead of starting a new one.
const _APP_CLOSE_GRACE_MS = 2500;
// Set when SYRIX is launched again while this instance is still tearing down.
let _syrixRelaunchRequested = false;
// 2026-07-02 — was `path.join(__dirname, "..", "hud_control.json")` (the
// OneDrive repo root), but the backend polls the syrix-paths state dir; the
// app_close file fallback therefore never reached the backend when the WS
// was down, orphaning the Python process on close.
const _HUD_CONTROL_PATH = _syrixPaths.stateFile("hud_control.json");

function _syrixBoundsOnAnyDisplay(bounds) {
  try {
    const b = bounds || {};
    if (!Number.isFinite(b.x) || !Number.isFinite(b.y) || !Number.isFinite(b.width) || !Number.isFinite(b.height)) return false;
    if (b.width < 200 || b.height < 160) return false;
    return screen.getAllDisplays().some((display) => {
      const area = display.workArea || display.bounds;
      const overlapW = Math.min(b.x + b.width, area.x + area.width) - Math.max(b.x, area.x);
      const overlapH = Math.min(b.y + b.height, area.y + area.height) - Math.max(b.y, area.y);
      return overlapW >= 120 && overlapH >= 120;
    });
  } catch (_) {
    return false;
  }
}

function _syrixClampWindowToVisibleDisplay(win, reason) {
  try {
    if (!win || win.isDestroyed()) return;
    const current = win.getBounds();
    if (_syrixBoundsOnAnyDisplay(current)) return;
    const display = screen.getPrimaryDisplay();
    const area = display.workArea || display.bounds;
    const width = Math.max(900, Math.min(Number(current.width || 1400), Math.max(900, area.width - 40)));
    const height = Math.max(640, Math.min(Number(current.height || 860), Math.max(640, area.height - 40)));
    const next = {
      x: Math.round(area.x + Math.max(0, (area.width - width) / 2)),
      y: Math.round(area.y + Math.max(0, (area.height - height) / 2)),
      width: Math.round(width),
      height: Math.round(height),
    };
    win.setBounds(next);
    _syrixStartupLog(`[HUD_WINDOW] bounds_recovered reason=${reason || "startup"} previous=${JSON.stringify(current)} next=${JSON.stringify(next)}`);
  } catch (err) {
    _syrixStartupLog(`[HUD_WINDOW] bounds_recovery_failed reason=${reason || "startup"} err=${err && err.message ? err.message : err}`);
  }
}

function _syrixWriteHudReady(visible, reason) {
  try {
    const bounds = mainWindow && !mainWindow.isDestroyed() ? mainWindow.getBounds() : null;
    const payload = {
      visible: !!visible,
      status: visible ? "visible" : "not_visible",
      reason: String(reason || ""),
      pid: process.pid,
      ts: Date.now() / 1000,
      bounds,
      entrypoint: "hud_electron/main.js",
      renderer: "index.html",
    };
    // Atomic (2026-07-19 audit): this heartbeat rewrites hud_ready.json on an
    // interval — the last remaining REPEATING non-atomic mailbox writer.
    _syrixWriteJsonAtomicSync(_SYRIX_HUD_READY_PATH, payload);
  } catch (err) {
    _syrixStartupLog(`[HUD_WINDOW] ready_marker_write_failed err=${err && err.message ? err.message : err}`);
  }
}

function _syrixStartHudReadyHeartbeat(reason) {
  if (_hudReadyHeartbeat) return;
  _syrixWriteHudReady(true, reason || "startup_visible");
  _hudReadyHeartbeat = setInterval(() => {
    try {
      const visible = !!(
        mainWindow &&
        !mainWindow.isDestroyed() &&
        mainWindow.isVisible() &&
        !mainWindow.isMinimized() &&
        _syrixBoundsOnAnyDisplay(mainWindow.getBounds())
      );
      _syrixWriteHudReady(visible, visible ? "heartbeat_visible" : "heartbeat_not_visible");
    } catch (_) {}
  }, 15000);
}

function _syrixStopHudReadyHeartbeat(reason) {
  try {
    if (_hudReadyHeartbeat) clearInterval(_hudReadyHeartbeat);
  } catch (_) {}
  _hudReadyHeartbeat = null;
  _syrixWriteHudReady(false, reason || "window_closed");
}

function _syrixStopStartupVisibilityMonitor() {
  try {
    if (_hudStartupVisibilityMonitor) clearInterval(_hudStartupVisibilityMonitor);
  } catch (_) {}
  _hudStartupVisibilityMonitor = null;
  _hudStartupVisibilityMonitorUntil = 0;
}

function _syrixNeedsVisibleWindowRecovery() {
  try {
    return !!(
      mainWindow &&
      !mainWindow.isDestroyed() &&
      !mainWindow.isMinimized() &&
      (!mainWindow.isVisible() || !_syrixBoundsOnAnyDisplay(mainWindow.getBounds()))
    );
  } catch (_) {
    return false;
  }
}

function _syrixStartStartupVisibilityMonitor(reason) {
  const until = Date.now() + 125000;
  _hudStartupVisibilityMonitorUntil = Math.max(_hudStartupVisibilityMonitorUntil || 0, until);
  if (_hudStartupVisibilityMonitor) return;
  _syrixStartupLog(`[HUD_WINDOW] startup_visibility_monitor armed reason=${reason || "startup"}`);
  _hudStartupVisibilityMonitor = setInterval(() => {
    try {
      if (Date.now() > _hudStartupVisibilityMonitorUntil) {
        _syrixStopStartupVisibilityMonitor();
        return;
      }
      if (_syrixNeedsVisibleWindowRecovery()) {
        _syrixStartupLog("[HUD_WINDOW] startup_visibility_monitor recovering hidden_or_minimized_window");
        _syrixForceShowMainWindow("startup-visibility-monitor");
      }
    } catch (_) {}
  }, 4000);
}

function _syrixForceShowMainWindow(reason) {
  try {
    if (!mainWindow || mainWindow.isDestroyed()) {
      _syrixStartupLog(`[HUD_WINDOW] force_show_skipped reason=${reason || "startup"} no_window=true`);
      return false;
    }
    _syrixClampWindowToVisibleDisplay(mainWindow, reason);
    if (mainWindow.isMinimized()) mainWindow.restore();
    if (!mainWindow.isVisible()) mainWindow.show();
    mainWindow.focus();
    mainWindow.setAlwaysOnTop(true, "normal");
    setTimeout(() => {
      try {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setAlwaysOnTop(false);
      } catch (_) {}
    }, 700);
    _syrixStartHudReadyHeartbeat(reason || "force_show");
    _syrixStartupLog(`[HUD_WINDOW] shown reason=${reason || "startup"} visible=${mainWindow.isVisible()} minimized=${mainWindow.isMinimized()} bounds=${JSON.stringify(mainWindow.getBounds())}`);
    _syrixStartStartupVisibilityMonitor(reason || "force_show");
    _syrixStartBackendAfterHudVisible(reason || "startup_visible");
    return true;
  } catch (err) {
    _syrixStartupLog(`[HUD_WINDOW] force_show_failed reason=${reason || "startup"} err=${err && err.message ? err.message : err}`);
    return false;
  }
}

/** Reliable backend signal when the HUD window closes (WS alone can be lost mid-teardown). */
function _writeHudControlCommand(command, source) {
  try {
    const payload = {
      command: String(command || "").trim(),
      source: String(source || "hud_electron_main"),
      seq: Date.now() * 1000000 + Math.floor(Math.random() * 1000),
      ts: Date.now() / 1000,
    };
    if (payload.command === "app_close") {
      if (!_SYRIX_CLOSE_REQUEST_ID) {
        _SYRIX_CLOSE_REQUEST_ID = `${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      }
      payload.shutdown_id = _SYRIX_CLOSE_REQUEST_ID;
    }
    if (!payload.command) return false;
    // 2026-07-07 audit C1: this used to writeFileSync directly — a torn write
    // racing the backend's 0.22s poller, and it dropped any pending queue[]
    // entries. Carry the queue over and write atomically (tmp + rename), same
    // contract as the sendControl IPC handler.
    try {
      const current = JSON.parse(fs.readFileSync(_HUD_CONTROL_PATH, "utf8")) || {};
      if (current && typeof current === "object" && !Array.isArray(current) && Array.isArray(current.queue)) {
        payload.queue = current.queue.slice(-20);
      }
    } catch (_) {}
    const tmp = _HUD_CONTROL_PATH + ".mainctl.tmp";
    fs.writeFileSync(tmp, JSON.stringify(payload, null, 2), "utf8");
    fs.renameSync(tmp, _HUD_CONTROL_PATH);
    return true;
  } catch (err) {
    try {
      console.warn("[APP_CLOSE] hud_control write failed:", err && err.message ? err.message : err);
    } catch (_) {}
    return false;
  }
}

function _notifyBackendHudClosing(source) {
  if (!_SYRIX_CLOSE_REQUEST_ID) {
    _SYRIX_CLOSE_REQUEST_ID = `${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }
  try {
    _writeHudControlCommand("app_close", source || "hud_window_close");
  } catch (_) {}
  try {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("hud:app-closing", { shutdown_id: _SYRIX_CLOSE_REQUEST_ID });
      mainWindow.webContents.send("hud:release-media");
    }
  } catch (_) {}
}

// ── Notification-area icon ──────────────────────────────────────────────
// SYRIX sits in the notification area (and under "Show hidden icons") for as
// long as any part of it is running, as Windows apps with background work
// do. While the window is closing the icon says so, and "Open SYRIX" then
// starts a fresh instance once the teardown has finished. The icon is
// destroyed before the process exits; one left to the OS stays behind as a
// dead icon until the pointer passes over it.
let _syrixTray = null;

function _syrixTrayMenu() {
  const closing = _appShutdownInProgress;
  return Menu.buildFromTemplate([
    { label: closing ? "Open SYRIX again" : "Open SYRIX", click: _syrixTrayOpen },
    { type: "separator" },
    { label: closing ? "Closing SYRIX…" : "Quit SYRIX", enabled: !closing, click: _syrixTrayQuit },
  ]);
}

function _syrixTrayOpen() {
  if (_appShutdownInProgress) {
    _syrixRelaunchRequested = true;
    _syrixStartupLog("[PROCESS_GUARD] tray_open action=relaunch_after_teardown");
    return;
  }
  _syrixForceShowMainWindow("tray");
}

function _syrixTrayQuit() {
  _syrixStopStartupVisibilityMonitor();
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.close();
  else app.quit();
}

function _syrixCreateTray() {
  if (_syrixTray || process.platform !== "win32") return;
  try {
    const icon = nativeImage.createFromPath(path.join(__dirname, "..", "assets", "icons", "syrix_app_logo.ico"));
    _syrixTray = new Tray(icon);
    _syrixTray.setToolTip("SYRIX");
    _syrixTray.setContextMenu(_syrixTrayMenu());
    _syrixTray.on("click", _syrixTrayOpen);
  } catch (err) {
    _syrixTray = null;
    _syrixStartupLog(`[HUD_TRAY] create_failed err=${err && err.message ? err.message : err}`);
  }
}

function _syrixTrayShowClosing() {
  if (!_syrixTray) return;
  try {
    _syrixTray.setToolTip("SYRIX is closing…");
    _syrixTray.setContextMenu(_syrixTrayMenu());
  } catch (_) {}
}

function _syrixDestroyTray() {
  try { if (_syrixTray) _syrixTray.destroy(); } catch (_) {}
  _syrixTray = null;
}

function createWindow() {
  const display = screen.getPrimaryDisplay();
  const { width, height } = display.workAreaSize;
  const preloadPath = path.join(__dirname, "preload.js");
  const rendererPath = path.join(__dirname, "index.html");

  _syrixStartupLog(`[HUD_WINDOW] create_attempt display=${width}x${height} preload=${preloadPath} renderer=${rendererPath}`);

  mainWindow = new BrowserWindow({
    width: Math.min(1700, width - 60),
    height: Math.min(1000, height - 60),
    minWidth: 1100,
    minHeight: 720,
    frame: false,
    // Keep the custom SYRIX chrome, but retain Windows' normal window shell
    // styles underneath it. Without this, frameless windows lose the native
    // minimize/restore animation and can behave inconsistently in the
    // taskbar and Alt+Tab switcher.
    thickFrame: true,
    skipTaskbar: false,
    minimizable: true,
    maximizable: true,
    closable: true,
    transparent: false,
    // The window's own paint before the page loads: the start-up ground,
    // not a blue-grey, so the first frame matches what follows it.
    backgroundColor: "#080604",
    show: false,
    autoHideMenuBar: true,
    title: "SYRIX",
    icon: path.join(__dirname, "..", "assets", "icons", "syrix_app_logo.ico"),
    webPreferences: {
      preload: preloadPath,
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
    },
  });

  _syrixStartupLog(`[HUD_WINDOW] BrowserWindow created id=${mainWindow.id} show=false`);
  const wc = mainWindow.webContents;
  wc.on("dom-ready", () => _syrixStartupLog("[HUD_RENDERER] dom-ready"));
  wc.on("did-finish-load", () => _syrixStartupLog("[HUD_RENDERER] did-finish-load"));
  wc.on("did-fail-load", (_event, errCode, errDesc, validatedURL, isMainFrame) => {
    _syrixStartupLog(`[HUD_RENDERER] did-fail-load code=${errCode} desc=${errDesc} url=${validatedURL} main=${!!isMainFrame}`);
  });
  wc.on("render-process-gone", (_event, details) => {
    _syrixStartupLog(`[HUD_RENDERER] render-process-gone reason=${details && details.reason} exitCode=${details && details.exitCode}`);
  });
  wc.on("unresponsive", () => _syrixStartupLog("[HUD_RENDERER] unresponsive"));
  wc.on("preload-error", (_event, preloadPathForError, error) => {
    _syrixStartupLog(`[HUD_RENDERER] preload-error path=${preloadPathForError} err=${error && error.message ? error.message : error}`);
  });
  wc.on("console-message", (_event, level, message, line, sourceId) => {
    if (Number(level) >= 2) {
      _syrixStartupLog(`[HUD_RENDERER_CONSOLE] level=${level} source=${sourceId}:${line} message=${String(message || "").slice(0, 500)}`);
    }
  });
  // The trusted shell stays on its one local entrypoint. Generated answers and
  // source titles must not be able to navigate this privileged renderer or
  // create a second Electron window; validated HTTP(S) links are opened in the
  // already-sandboxed HUD app dock instead.
  const rendererUrl = pathToFileURL(rendererPath).href;
  wc.setWindowOpenHandler(({ url }) => {
    try {
      const safe = _hudAppUrlAllowed(url);
      if (safe.ok) {
        setImmediate(() => { _hudAppOpenStaged(safe.url, "trusted_shell_link"); });
      }
    } catch (_) {}
    return { action: "deny" };
  });
  wc.on("will-navigate", (event, navUrl) => {
    if (String(navUrl || "") === rendererUrl) return;
    try { event.preventDefault(); } catch (_) {}
    try {
      const safe = _hudAppUrlAllowed(navUrl);
      if (safe.ok) {
        setImmediate(() => { _hudAppOpenStaged(safe.url, "trusted_shell_navigation"); });
      }
    } catch (_) {}
  });

  _syrixStartupLog(`[HUD_RENDERER] loadFile_attempt path=${rendererPath}`);
  mainWindow.loadFile(rendererPath)
    .then(() => _syrixStartupLog("[HUD_RENDERER] loadFile_resolved"))
    .catch((err) => _syrixStartupLog(`[HUD_RENDERER] loadFile_failed err=${err && err.message ? err.message : err}`));
  mainWindow.once("ready-to-show", () => {
    _syrixForceShowMainWindow("ready-to-show");
  });
  _hudShowRecoveryTimer = setTimeout(() => {
    _syrixForceShowMainWindow("startup-visible-window-recovery");
  }, 1600);

  mainWindow.on("show", () => _syrixStartupLog("[HUD_WINDOW] event=show"));
  mainWindow.on("hide", () => {
    _syrixStartupLog("[HUD_WINDOW] event=hide");
    // A close request also hides the window before the bounded shutdown
    // handshake completes.  Do not let a recovery callback scheduled during
    // startup resurrect that window after shutdown has begun.
    if (!_appShutdownInProgress && _hudStartupVisibilityMonitor &&
        !mainWindow.isMinimized() && Date.now() <= _hudStartupVisibilityMonitorUntil) {
      setTimeout(() => _syrixForceShowMainWindow("startup-hide-recovery"), 250);
    }
  });
  mainWindow.on("minimize", () => {
    _syrixStartupLog("[HUD_WINDOW] event=minimize");
  });
  mainWindow.on("restore", () => _syrixStartupLog("[HUD_WINDOW] event=restore"));
  mainWindow.on("focus", () => _syrixStartupLog("[HUD_WINDOW] event=focus"));

  // Intercept the first close so we can ask the renderer to notify the
  // Python backend over WS, then proceed with the actual destroy after a
  // bounded grace window. Without this hand-off Python would keep running
  // (mic + camera + STT + wake listeners + bridges) after the HUD vanished.
  mainWindow.on("close", (event) => {
    if (_appShutdownFinalized) return;
    event.preventDefault();
    if (_appShutdownInProgress) return;
    _appShutdownInProgress = true;
    _appQuitRequested = true;
    _syrixStopStartupVisibilityMonitor();
    try {
      console.log("[APP_CLOSE] hud_window_closed");
      _syrixStopHudReadyHeartbeat("window_close_requested");
      _notifyBackendHudClosing("hud_window_close");
    } catch (_) {}
    try { if (mainWindow && !mainWindow.isDestroyed()) mainWindow.hide(); } catch (_) {}
    _syrixTrayShowClosing();
    (async () => {
      const closeStarted = Date.now();
      const graceful = await _syrixWaitForGracefulBackend(_SYRIX_CLOSE_REQUEST_ID, _APP_CLOSE_GRACE_MS);
      // 2026-07-25 — ALWAYS sweep, even when the handshake said "graceful".
      //
      // `_syrixBackendProcessExited()` is `!_syrixPythonProc || ...`, and when
      // Launch_SYRIX.bat starts the backend, Electron never spawned it — so
      // that handle is null and the check returns true unconditionally. A
      // "graceful" result therefore proved nothing about the real process, and
      // the sweep that would have found it (it scans by command line) was
      // skipped. That is why Python survived every close.
      //
      // The sweep is ownership-scoped and idempotent: killing an already-dead
      // pid is a no-op, so running it after a genuinely clean exit costs
      // nothing and closes the case where it was not.
      await _syrixTeardown("hud_close");
      _syrixStartupLog(`[SYRIX_SHUTDOWN] close_sequence_done graceful=${graceful} ms=${Date.now() - closeStarted}`);
      // Tear down embedded HUD apps (BrowserView renderers) BEFORE the
      // main window is destroyed. If we wait for the 'closed' event the
      // mainWindow is already gone and removeBrowserView throws, leaving
      // the BrowserView renderer processes orphaned (parent dead, child
      // alive). Doing it here guarantees every child renderer dies with us.
      try {
        for (const id of Array.from(_hudApps.keys())) {
          try { _hudAppDestroy(id); } catch (_) {}
        }
      } catch (_) {}
      try {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.destroy();
      } catch (_) {}
      _appShutdownFinalized = true;
      _syrixFinishExit();
    })().catch(() => {
      _syrixTeardown("hud_close_error").finally(() => {
        _appShutdownFinalized = true;
        _syrixFinishExit();
      });
    });
  });

  mainWindow.on("closed", () => {
    try {
      if (_hudShowRecoveryTimer) clearTimeout(_hudShowRecoveryTimer);
    } catch (_) {}
    _hudShowRecoveryTimer = null;
    _syrixStopStartupVisibilityMonitor();
    _syrixStopHudReadyHeartbeat("window_closed");
    // Tear down any embedded HUD apps so their WebContents don't leak.
    try {
      for (const id of Array.from(_hudApps.keys())) {
        try { _hudAppDestroy(id); } catch (_) {}
      }
    } catch (_) {}
    mainWindow = null;
  });

  // Re-clamp bounds for all open HUD apps on resize / move so they stay
  // inside the HUD layout zone regardless of window size.
  const _relayoutAll = () => {
    _hudStageCancelTween();
    _hudAppApplyAllBounds();
    _hudAppBroadcast();
  };
  mainWindow.on("resize", _relayoutAll);
  mainWindow.on("maximize", _relayoutAll);
  mainWindow.on("unmaximize", _relayoutAll);
  mainWindow.on("enter-full-screen", _relayoutAll);
  mainWindow.on("leave-full-screen", _relayoutAll);
}

function _syrixTrustedHudIpcSender(event) {
  try {
    return !!(
      event && event.sender && mainWindow && !mainWindow.isDestroyed()
      && event.sender === mainWindow.webContents
    );
  } catch (_) {
    return false;
  }
}

ipcMain.handle("hud:ws-auth-token", (event) => {
  if (!_syrixTrustedHudIpcSender(event)) throw new Error("Forbidden");
  return _SYRIX_HUD_WS_BOOT_TOKEN;
});

// Deck: the ecosystem launch pad. Launching is limited to the HUD window and
// to the ids listed in deck.json (see syrix-deck.js).
require("./syrix-deck").register(ipcMain, shell, _syrixTrustedHudIpcSender);
// Settings → SYRIX: SYRIX.md and the owner profile, opened by key only.
require("./syrix-settings-ipc").register(ipcMain, shell, _syrixTrustedHudIpcSender);

ipcMain.handle("hud:toggle-pin", () => {
  if (!mainWindow) return false;
  const next = !mainWindow.isAlwaysOnTop();
  mainWindow.setAlwaysOnTop(next, "screen-saver");
  return next;
});

ipcMain.handle("hud:minimize", () => {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.handle("hud:close", () => {
  _syrixStopStartupVisibilityMonitor(); // disarm 125s re-show guard so hide() sticks
  try {
    _notifyBackendHudClosing("hud_close_button");
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.hide(); // instant visual close
  } catch (_) {}
  if (mainWindow) mainWindow.close(); // grace-period cleanup runs in background
});

// The title-bar Maximize button. A true maximize keeps the window's caption
// styles, so Windows animates minimize, restore and close as it does for any
// other app; fullscreen drops those styles and every transition becomes a cut.
ipcMain.handle("hud:toggle-maximize", () => {
  if (!mainWindow) return false;
  if (mainWindow.isFullScreen()) {
    mainWindow.setFullScreen(false);
    return false;
  }
  if (mainWindow.isMaximized()) mainWindow.unmaximize();
  else mainWindow.maximize();
  return mainWindow.isMaximized();
});

ipcMain.handle("hud:toggle-fullscreen", () => {
  if (!mainWindow) return false;
  const next = !mainWindow.isFullScreen();
  mainWindow.setFullScreen(next);
  return next;
});

ipcMain.handle("hud:focus-main", () => {
  if (!mainWindow) return false;
  try {
    return _syrixForceShowMainWindow("ipc_focus_main");
  } catch (_) {
    return false;
  }
});

// Telemetry master switch — mirrors Python _system_telemetry_enabled (read from
// the real parent config.json). When false (default), the native CPU sampler and
// hud:sysinfo IPC stay inert so the HUD does NO always-on system polling; Task
// Manager owns CPU/RAM/GPU stats. Applied at HUD launch (restart to change).
let _SYRIX_TELEMETRY_ENABLED = false;
(function _loadTelemetryFlag() {
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "config.json"), "utf8"));
    if (typeof cfg.system_telemetry_enabled === "boolean") _SYRIX_TELEMETRY_ENABLED = cfg.system_telemetry_enabled;
  } catch (_) {}
})();

// Native system info — CPU, RAM, disk, uptime, hostname. Independent of the
// Python backend so the orb stats ring always shows real data.
let _cpuPrevSample = null;
let _cpuCurPct = null;
(function _startCpuSampler() {
  if (!_SYRIX_TELEMETRY_ENABLED) return;  // no always-on CPU polling when telemetry disabled
  function sample() {
    const cpus = os.cpus();
    let total = 0, idle = 0;
    for (const c of cpus) { for (const [t, v] of Object.entries(c.times)) { total += v; if (t === 'idle') idle += v; } }
    return { total, idle };
  }
  _cpuPrevSample = sample();
  setInterval(() => {
    const prev = _cpuPrevSample;
    const cur = sample();
    const td = cur.total - prev.total, id = cur.idle - prev.idle;
    _cpuCurPct = td > 0 ? Math.max(0, Math.min(100, Math.round((1 - id / td) * 100))) : _cpuCurPct;
    _cpuPrevSample = cur;
  }, 2000);
})();

ipcMain.handle("hud:sysinfo", async () => {
  if (!_SYRIX_TELEMETRY_ENABLED) return { ok: false, reason: "telemetry_disabled" };
  try {
    const totalMem = os.totalmem();
    const freeMem  = os.freemem();
    const ramPct   = Math.round((1 - freeMem / totalMem) * 100);
    const ramUsed  = (totalMem - freeMem) / 1073741824;
    const ramTotal = totalMem / 1073741824;
    const cpus     = os.cpus();
    let diskPct = null, diskUsedGb = null, diskTotalGb = null;
    try {
      const st = fs.statfsSync("C:\\");
      const dtotal = st.blocks * st.bsize, dfree = st.bfree * st.bsize;
      diskPct    = Math.round((1 - dfree / dtotal) * 100);
      diskUsedGb = (dtotal - dfree) / 1073741824;
      diskTotalGb = dtotal / 1073741824;
    } catch (_) {}
    const uptimeSecs = Math.floor(os.uptime());
    const uh = Math.floor(uptimeSecs / 3600), um = Math.floor((uptimeSecs % 3600) / 60);
    return {
      ok:           true,
      cpu_percent:  _cpuCurPct,
      cpu_model:    cpus[0] ? cpus[0].model.trim().replace(/\s+/g, ' ') : '',
      cpu_cores:    cpus.length,
      ram_percent:  ramPct,
      ram_used_gb:  Math.round(ramUsed * 10) / 10,
      ram_total_gb: Math.round(ramTotal * 10) / 10,
      disk_percent: diskPct,
      disk_used_gb: diskUsedGb != null ? Math.round(diskUsedGb) : null,
      disk_total_gb: diskTotalGb != null ? Math.round(diskTotalGb) : null,
      uptime_str:   `${uh}h ${um}m`,
      hostname:     os.hostname(),
      platform:     `${os.type()} ${os.release()}`,
    };
  } catch (e) {
    return { ok: false, error: String(e.message || e) };
  }
});

ipcMain.handle("hud:get-groq-model", () => {
  try { return String(process.env.GROQ_MODEL || "llama-3.3-70b-versatile").trim(); } catch (_) { return "llama-3.3-70b-versatile"; }
});

ipcMain.handle("hud:runtime-logs", (_e, payload) => {
  const limit = Math.max(50, Math.min(500, Number(payload && payload.limit) || 400));
  const root = path.join(__dirname, "..");
  const logFiles = [
    _syrixPaths.logFile("backend_autospawn.log"),
    path.join(root, "run_session_lifetime_debug.txt"),
    path.join(root, "run_after_chatfix.txt"),
  ];
  const fileLines = [];
  for (const file of logFiles) {
    const tail = _syrixTailTextFile(file, 180000);
    if (tail.length) fileLines.push(...tail.slice(-limit));
  }
  const lines = fileLines.concat(_SYRIX_MAIN_LOG_RING).slice(-limit);
  return { ok: true, lines, sources: logFiles.map((f) => path.basename(f)) };
});

// Phase 0: renderer JSON polling reads %LOCALAPPDATA%\SYRIX\state via IPC —
// relative file:// fetches can't reach outside the repo dir, and the packaged
// build won't have one. Allowlisted runtime mailbox names only.
ipcMain.handle("hud:runtime-json", (_e, payload) => {
  try {
    const name = path.basename(String((payload && payload.name) || "").trim());
    if (!_syrixPaths.RUNTIME_STATE_FILES.has(name)) return { ok: false, reason: "not_allowed" };
    const txt = fs.readFileSync(_syrixPaths.stateFile(name), "utf8");
    if (!txt) return { ok: false, reason: "empty" };
    return { ok: true, data: JSON.parse(txt) };
  } catch (err) {
    return { ok: false, reason: (err && err.code) === "ENOENT" ? "missing" : "read_failed" };
  }
});

// 2026-07-02 — file fallback for renderer commands when the WS is down.
// Every button used to be a SILENT no-op without the socket. Commands are
// appended to a `queue` array inside hud_control.json — the backend poller
// drains the queue before the legacy single-slot command, so a fallback
// write can never clobber an in-flight WS-written command (or vice versa).
ipcMain.handle("hud:send-control", (_e, payload) => {
  try {
    const p = payload && typeof payload === "object" ? payload : {};
    const command = String(p.command || "").trim();
    if (!command) return { ok: false, reason: "missing_command" };
    const entry = {
      command,
      source: String(p.source || "hud_renderer_file_fallback"),
      seq: Date.now() * 1000000 + Math.floor(Math.random() * 1000),
      ts: Date.now() / 1000,
    };
    if (typeof p.text === "string" && p.text.length) entry.text = p.text;
    if (p.data && typeof p.data === "object") entry.data = p.data;
    let current = {};
    try { current = JSON.parse(fs.readFileSync(_HUD_CONTROL_PATH, "utf8")) || {}; } catch (_) { current = {}; }
    if (typeof current !== "object" || Array.isArray(current)) current = {};
    const queue = Array.isArray(current.queue) ? current.queue : [];
    queue.push(entry);
    current.queue = queue.slice(-20);
    // Unique tmp name (2026-07-19 audit): the fixed ".sendctl.tmp" collided
    // when two sends raced — one rename would fail or ship the other's bytes.
    const tmp = `${_HUD_CONTROL_PATH}.sendctl.${process.pid}.${Date.now()}.${Math.random().toString(16).slice(2)}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(current, null, 2), "utf8");
    fs.renameSync(tmp, _HUD_CONTROL_PATH);
    return { ok: true, seq: entry.seq };
  } catch (err) {
    return { ok: false, reason: err && err.message ? String(err.message).slice(0, 120) : "write_failed" };
  }
});

/* Spotify Web API proxy.
 * Secrets stay in the main process. The renderer receives only sanitized
 * playback/search data and clean auth/device/Premium errors. */
let _spotifyAccessToken = "";
function _spotifyToken() {
  if (_spotifyAccessToken) return _spotifyAccessToken;
  try { _spotifyAccessToken = String(process.env.SPOTIFY_ACCESS_TOKEN || "").trim(); } catch (_) {}
  return _spotifyAccessToken;
}
function _spotifyStatus() {
  const access = _spotifyToken();
  const refresh = String(process.env.SPOTIFY_REFRESH_TOKEN || "").trim();
  const client = String(process.env.SPOTIFY_CLIENT_ID || "").trim();
  const secret = String(process.env.SPOTIFY_CLIENT_SECRET || "").trim();
  return {
    configured: !!(access || (refresh && client && secret)),
    hasAccessToken: !!access,
    hasRefreshToken: !!refresh,
    hasClientId: !!client,
    reason: (access || (refresh && client && secret)) ? "" : "missing_auth",
  };
}
async function _spotifyRefreshAccessToken() {
  const refresh = String(process.env.SPOTIFY_REFRESH_TOKEN || "").trim();
  const client = String(process.env.SPOTIFY_CLIENT_ID || "").trim();
  const secret = String(process.env.SPOTIFY_CLIENT_SECRET || "").trim();
  if (!refresh || !client || !secret) return "";
  if (typeof fetch !== "function") return "";
  const body = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: refresh,
  });
  const basic = Buffer.from(`${client}:${secret}`, "utf8").toString("base64");
  const resp = await fetch("https://accounts.spotify.com/api/token", {
    method: "POST",
    headers: {
      "Authorization": `Basic ${basic}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });
  if (!resp.ok) return "";
  const data = await resp.json().catch(() => null);
  const token = data && data.access_token ? String(data.access_token) : "";
  if (token) {
    _spotifyAccessToken = token;
    process.env.SPOTIFY_ACCESS_TOKEN = token;
  }
  return token;
}
function _spotifyReason(status, payload) {
  if (status === 401) return "missing_auth";
  if (status === 403) return "premium_required";
  if (status === 404) return "no_active_device";
  if (status === 204) return "no_content";
  const msg = String((payload && (payload.error && (payload.error.reason || payload.error.message))) || "");
  if (/premium/i.test(msg)) return "premium_required";
  if (/device/i.test(msg)) return "no_active_device";
  return "spotify_error";
}
async function _spotifyFetch(pathname, opts, retry) {
  if (typeof fetch !== "function") return { ok: false, reason: "fetch_unavailable" };
  let token = _spotifyToken();
  if (!token) token = await _spotifyRefreshAccessToken();
  if (!token) return { ok: false, reason: "missing_auth" };
  const resp = await fetch("https://api.spotify.com/v1" + pathname, Object.assign({}, opts || {}, {
    headers: Object.assign({
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
    }, (opts && opts.headers) || {}),
  }));
  if (resp.status === 401 && retry !== false) {
    _spotifyAccessToken = "";
    const fresh = await _spotifyRefreshAccessToken();
    if (fresh) return _spotifyFetch(pathname, opts, false);
  }
  let data = null;
  if (resp.status !== 204) {
    data = await resp.json().catch(() => null);
  }
  if (!resp.ok && resp.status !== 204) {
    return { ok: false, status: resp.status, reason: _spotifyReason(resp.status, data), error: data && data.error ? data.error.message : "" };
  }
  return { ok: true, status: resp.status, data };
}
// 2026-05-19 Phase 8 — device discovery + transfer. Spotify Connect requires
// an active device; without one, /me/player/play returns 404 NOT_FOUND. Most
// "Spotify not working" reports trace to this. We fetch /me/player/devices
// and optionally transfer playback to the first available device so the user
// does not have to manually launch a Spotify client first.
async function _spotifyDevices() {
  const res = await _spotifyFetch("/me/player/devices", {}, true);
  if (!res.ok) return { ok: false, reason: res.reason || "spotify_error", status: res.status, devices: [] };
  const devices = (res.data && Array.isArray(res.data.devices) ? res.data.devices : []).map((d) => ({
    id: String(d.id || ""),
    name: String(d.name || ""),
    type: String(d.type || ""),
    is_active: !!d.is_active,
    is_restricted: !!d.is_restricted,
    volume_percent: typeof d.volume_percent === "number" ? d.volume_percent : null,
  }));
  return { ok: true, devices };
}
async function _spotifyTransfer(deviceId, startPlay) {
  const id = String(deviceId || "").trim();
  if (!id) return { ok: false, reason: "missing_device" };
  return _spotifyFetch("/me/player", {
    method: "PUT",
    body: JSON.stringify({ device_ids: [id], play: !!startPlay }),
  }, true);
}
function _spotifyTrackItems(data) {
  const tracks = data && data.tracks && Array.isArray(data.tracks.items) ? data.tracks.items : [];
  const out = tracks.slice(0, 6).map((t) => {
    const album = t.album || {};
    const imgs = Array.isArray(album.images) ? album.images : [];
    return {
      id: t.id || "",
      uri: t.uri || "",
      type: "track",
      name: t.name || "",
      artist: Array.isArray(t.artists) ? t.artists.map((a) => a && a.name).filter(Boolean).join(", ") : "",
      album: album.name || "",
      image: imgs[0] && imgs[0].url || "",
      duration_ms: t.duration_ms || 0,
    };
  });
  const artists = data && data.artists && Array.isArray(data.artists.items) ? data.artists.items : [];
  for (const a of artists.slice(0, 3)) {
    const imgs = Array.isArray(a.images) ? a.images : [];
    out.push({
      id: a.id || "",
      uri: a.uri || "",
      type: "artist",
      name: a.name || "",
      artist: "Artist",
      album: "",
      image: imgs[0] && imgs[0].url || "",
      duration_ms: 0,
    });
  }
  const playlists = data && data.playlists && Array.isArray(data.playlists.items) ? data.playlists.items : [];
  for (const p of playlists.slice(0, 3)) {
    const imgs = Array.isArray(p.images) ? p.images : [];
    out.push({
      id: p.id || "",
      uri: p.uri || "",
      type: "playlist",
      name: p.name || "",
      artist: "Playlist",
      album: "",
      image: imgs[0] && imgs[0].url || "",
      duration_ms: 0,
    });
  }
  return out.slice(0, 8);
}
ipcMain.handle("hud:spotify-status", () => _spotifyStatus());
ipcMain.handle("hud:spotify-close", () => {
  const { spawn } = require("child_process");
  return new Promise((resolve) => {
    const args = process.platform === "win32"
      ? ["taskkill", ["/IM", "Spotify.exe", "/F"]]
      : ["pkill", ["-f", "Spotify"]];
    const child = spawn(args[0], args[1], { windowsHide: true, stdio: "ignore" });
    child.on("close", (code) => resolve({ ok: code === 0 || code === 128, reason: code === 0 ? "closed" : `exit_${code}` }));
    child.on("error", (err) => resolve({ ok: false, reason: String(err.message || err).slice(0, 120) }));
  });
});
ipcMain.handle("hud:spotify-api", async (_e, payload) => {
  const action = String((payload && payload.action) || "").trim().toLowerCase();
  const data = (payload && payload.data) || {};
  const st = _spotifyStatus();
  if (!st.configured) return { ok: false, reason: "missing_auth" };
  try {
    if (action === "current") {
      return _spotifyFetch("/me/player?additional_types=track", {}, true);
    }
    if (action === "search") {
      const q = String(data.query || "").trim();
      if (!q) return { ok: true, items: [] };
      const res = await _spotifyFetch("/search?" + new URLSearchParams({ q, type: "track,artist,playlist", limit: "6" }).toString(), {}, true);
      if (!res.ok) return res;
      return { ok: true, items: _spotifyTrackItems(res.data) };
    }
    if (action === "play") {
      let uri = String(data.uri || "").trim();
      const q = String(data.query || "").trim();
      if (!uri && q) {
        const res = await _spotifyFetch("/search?" + new URLSearchParams({ q, type: "track,artist,playlist", limit: "1" }).toString(), {}, true);
        if (!res.ok) return res;
        const items = _spotifyTrackItems(res.data);
        uri = items[0] && items[0].uri || "";
        if (!uri) return { ok: false, reason: "not_found" };
      }
      const body = uri
        ? JSON.stringify(uri.startsWith("spotify:track:") ? { uris: [uri] } : { context_uri: uri })
        : undefined;
      const explicitDevice = String(data.device_id || "").trim();
      const playPath = explicitDevice
        ? "/me/player/play?" + new URLSearchParams({ device_id: explicitDevice }).toString()
        : "/me/player/play";
      let res = await _spotifyFetch(playPath, { method: "PUT", body }, true);
      // Auto-transfer fallback: if no active device, pick the first available
      // device, transfer playback (starting it), then retry play to honour the
      // requested URI/context.
      if (!res.ok && res.reason === "no_active_device") {
        const devs = await _spotifyDevices();
        // FLAW-401 (2026-06-04): _spotifyDevices can return devs.devices = undefined
        // when Spotify returns an empty 204, leading to "Cannot read length of undefined".
        if (devs && devs.ok && Array.isArray(devs.devices) && devs.devices.length) {
          const target = devs.devices.find((d) => d.is_active) || devs.devices.find((d) => !d.is_restricted) || devs.devices[0];
          if (target && target.id) {
            await _spotifyTransfer(target.id, !body);
            res = await _spotifyFetch(
              "/me/player/play?" + new URLSearchParams({ device_id: target.id }).toString(),
              { method: "PUT", body },
              true,
            );
            if (res.ok) res.transferred_to = { id: target.id, name: target.name, type: target.type };
          }
        }
      }
      return res;
    }
    if (action === "devices") {
      return _spotifyDevices();
    }
    if (action === "transfer") {
      const id = String(data.device_id || "").trim();
      if (!id) return { ok: false, reason: "missing_device" };
      return _spotifyTransfer(id, !!data.play);
    }
    if (action === "pause") return _spotifyFetch("/me/player/pause", { method: "PUT" }, true);
    if (action === "next") return _spotifyFetch("/me/player/next", { method: "POST" }, true);
    if (action === "previous") return _spotifyFetch("/me/player/previous", { method: "POST" }, true);
    if (action === "seek") {
      const pos = Math.max(0, Math.round(Number(data.position_ms || 0)));
      return _spotifyFetch("/me/player/seek?" + new URLSearchParams({ position_ms: String(pos) }).toString(), { method: "PUT" }, true);
    }
    if (action === "volume") {
      const pct = Math.max(0, Math.min(100, Math.round(Number(data.volume_percent || 0))));
      return _spotifyFetch("/me/player/volume?" + new URLSearchParams({ volume_percent: String(pct) }).toString(), { method: "PUT" }, true);
    }
    return { ok: false, reason: "unknown_action" };
  } catch (err) {
    return { ok: false, reason: "spotify_error", error: String(err && err.message ? err.message : err).slice(0, 180) };
  }
});

/* ─────────────────────────────────────────────────────────────────────
 * SYRIX HUD App Dock — BrowserView-based embedded app manager.
 *
 * Each "HUD app" is a sandboxed BrowserView attached to the main HUD
 * window. The renderer (React) computes layout and sends bounds; the
 * main process clamps them to the HUD layout zone (between the top
 * status bar and bottom widget bar) so HUD chrome remains visible at
 * all times — including while an app is "fullscreen inside the HUD".
 *
 * Security baseline applied to every embedded app:
 *   - nodeIntegration: false
 *   - contextIsolation: true
 *   - sandbox: true
 *   - webSecurity: true
 *   - dedicated Electron partition/profile under .electron_user_data
 *   - preload: none (the embedded site has no Node access)
 *   - URL allowlist: http(s) only; file://, javascript:, data:, blob:,
 *     and private/admin IP ranges are rejected.
 *
 * Bounds layout (in renderer pixel coords relative to mainWindow):
 *   reserved top:    64px (status bar)
 *   reserved bottom: 64px (bottom bar)
 *   reserved left:   72px ("HUD rail" — orb when corner-docked + nav)
 *   reserved right:  16px margin + 420px SYRIX chat rail (.p-chat width)
 *
 * Modes:
 *   - "normal"     : centered glass panel ~70% of layout zone
 *   - "minimized"  : view hidden (size 0); shell shows minimized chip
 *   - "fullscreen" : fills the layout zone (still leaves HUD chrome visible)
 *   - "custom"     : renderer-supplied bounds (resize/move handles)
 * ─────────────────────────────────────────────────────────────────── */

const HUD_APP_MAX = 6;                         // protect memory: cap simultaneous apps
const HUD_APP_INITIAL_LOAD_TIMEOUT_MS = (() => {
  const raw = Number(process.env.SYRIX_HUD_APP_INITIAL_LOAD_TIMEOUT_S || process.env.syrix_hud_app_initial_load_timeout_s || 6);
  const sec = Number.isFinite(raw) && raw > 0 ? raw : 6;
  return Math.max(1500, Math.min(12000, Math.round(sec * 1000)));
})();
const HUD_APP_RESTORE_ON_STARTUP = /^(1|true|yes|on)$/i.test(
  String(process.env.SYRIX_HUD_APP_RESTORE_ON_STARTUP || process.env.syrix_hud_app_restore_on_startup || "")
);
/* Must match .p-chat width in syrix-primary-chat.jsx */
const HUD_CHAT_RAIL_W = 420;
const HUD_APP_MARGIN_RIGHT = 16;
const HUD_APP_LAYOUT = {
  top: 64,
  bottom: 64,
  left: 146,
  right: HUD_APP_MARGIN_RIGHT + HUD_CHAT_RAIL_W,
};
/* Titlebar height — must match .syrix-hud-app-chrome in syrix-hud-app-dock.jsx */
const HUD_APP_CHROME_H = 38;

/* ── Stage mode — browser fills the window as a backdrop ─────────────────────
 * When enabled, the active BrowserView covers the whole content area below a
 * top nav strip (the Dynamic Island morphs into a nav bar there). chatInset
 * reserves a left column while the stage chat panel is docked. Per-app
 * mode/bounds are NOT touched, so exiting stage restores the exact windowed
 * layout. The overlay refcount freezes the view (capturePage snapshot shown
 * by the dock) so HTML panels can render on top of the native view. */
const HUD_STAGE_NAV_H = 68;
let _hudStage = { enabled: false, chatInset: 0 };
let _hudStageTween = null;
let _hudOverlayCount = 0;
let _hudOverlayFrozen = false;

const _hudApps = new Map();                    // id -> { view, url, title, mode, bounds, history, navIdx, vision, createdAt }
let _hudAppActive = null;                      // active app id (for keyboard focus)

function _hudAppDomain(rawUrl) {
  try { return new URL(String(rawUrl || "")).hostname || ""; } catch (_) { return ""; }
}

function _hudAppOpenSource(raw) {
  const source = String(raw || "").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_");
  if (["user_click", "voice_command", "route", "restore", "startup"].includes(source)) return source;
  return "user_click";
}

function _hudAppDisplayUrl(rawUrl) {
  try {
    const u = new URL(String(rawUrl || ""));
    u.username = "";
    u.password = "";
    u.search = "";
    u.hash = "";
    return u.toString();
  } catch (_) {
    return "";
  }
}

function _hudAppDiag(event, fields) {
  try {
    const safe = [];
    for (const [k, v] of Object.entries(fields || {})) {
      if (/url|token|cookie|password|secret|credential/i.test(k)) continue;
      safe.push(`${k}=${String(v).replace(/\s+/g, "_").slice(0, 120)}`);
    }
    _syrixStartupLog(`[HUD_BROWSER] event=${event} ${safe.join(" ")}`.trim());
  } catch (_) {}
}

function _hudAppBroadcast() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  try {
    const list = [];
    for (const [id, app] of _hudApps.entries()) {
      const currentUrl = app.url || "";
      list.push({
        id,
        url: _hudAppDisplayUrl(currentUrl),
        domain: _hudAppDomain(currentUrl),
        title: app.title || "",
        pageTitle: app.title || "",
        mode: app.mode || "normal",
        bounds: app.boundsApplied || app.bounds || null,
        canGoBack: !!(app.view && !app.view.webContents.isDestroyed() && app.view.webContents.canGoBack()),
        canGoForward: !!(app.view && !app.view.webContents.isDestroyed() && app.view.webContents.canGoForward()),
        loading: !!(app.view && !app.view.webContents.isDestroyed() && app.view.webContents.isLoading()),
        lastError: app.lastError || "",
        controlsStatus: app.controlsStatus || "",
        controlsDetail: app.controlsDetail || "",
        controlsRetryCount: Number(app.controlsRetryCount || 0),
        updatedAt: Date.now() / 1000,
        vision: !!app.vision,
      });
    }
    mainWindow.webContents.send("hud-app:state", {
      apps: list,
      active: _hudAppActive,
      stage: { enabled: _hudStage.enabled, chatInset: _hudStage.chatInset, navH: HUD_STAGE_NAV_H, frozen: _hudOverlayFrozen },
    });
  } catch (_) {}
}

function _hudAppUrlAllowed(rawUrl) {
  const s = String(rawUrl || "").trim();
  if (!s) return { ok: false, reason: "empty_url" };
  let u;
  try { u = new URL(s); } catch (_) { return { ok: false, reason: "invalid_url" }; }
  const proto = (u.protocol || "").toLowerCase();
  if (proto !== "http:" && proto !== "https:") return { ok: false, reason: "scheme_blocked:" + proto };
  const host = (u.hostname || "").toLowerCase();
  if (!host) return { ok: false, reason: "no_host" };
  // Fast literal/suffix rejection for user feedback. The network proxy below
  // independently resolves and pins every main-frame and subresource request.
  if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal")) {
    return { ok: false, reason: "private_host" };
  }
  if (net.isIP(host)) {
    try {
      const { isPublicAddress } = require("./syrix-safe-proxy.js");
      if (!isPublicAddress(host)) return { ok: false, reason: "private_address" };
    } catch (_) {
      return { ok: false, reason: "address_guard_unavailable" };
    }
  }
  // Block well-known credential / admin path patterns at the URL surface.
  if (/(?:^|[^a-z0-9])(?:wp-admin|phpmyadmin|router|admin)\b/i.test(u.pathname || "")) {
    return { ok: false, reason: "admin_path" };
  }
  return { ok: true, url: u.toString() };
}

function _hudAppUrlKey(rawUrl) {
  try {
    const u = new URL(String(rawUrl || "").trim());
    u.hash = "";
    return u.toString().replace(/\/$/, "").toLowerCase();
  } catch (_) {
    return String(rawUrl || "").trim().replace(/\/$/, "").toLowerCase();
  }
}

function _hudAppSiteKey(rawUrl) {
  try {
    const u = new URL(String(rawUrl || "").trim());
    const host = String(u.hostname || "").toLowerCase().replace(/^www\./, "");
    const path = String(u.pathname || "").toLowerCase();
    if (!host) return "";
    if (host.endsWith("google.com") && path.startsWith("/maps")) return "google_maps";
    if (host === "mail.google.com") return "gmail";
    if (host === "calendar.google.com") return "google_calendar";
    if (host === "drive.google.com") return "google_drive";
    if (host.endsWith("tradingview.com")) return "tradingview";
    if (host.endsWith("flightradar24.com")) return "flightradar24";
    if (host.endsWith("worldmonitor.app")) return "worldmonitor";
    return host;
  } catch (_) {
    return "";
  }
}

function _hudAppLayoutZone() {
  if (!mainWindow || mainWindow.isDestroyed()) return { x: 0, y: 0, w: 0, h: 0 };
  const cb = mainWindow.getContentBounds();
  const x = HUD_APP_LAYOUT.left;
  const y = HUD_APP_LAYOUT.top;
  const w = Math.max(320, cb.width - HUD_APP_LAYOUT.left - HUD_APP_LAYOUT.right);
  const h = Math.max(240, cb.height - HUD_APP_LAYOUT.top - HUD_APP_LAYOUT.bottom);
  return { x, y, w, h };
}

function _hudAppComputeBounds(mode, custom) {
  const zone = _hudAppLayoutZone();
  if (_hudStage.enabled && mode !== "minimized") {
    // Stage: full-bleed backdrop below the nav strip, minus the docked-chat
    // column. The chat docks on the RIGHT (2026-07-04), so the inset comes
    // off the view's width while x stays pinned to the left edge.
    const cb = mainWindow && !mainWindow.isDestroyed() ? mainWindow.getContentBounds() : { width: zone.w + zone.x, height: zone.h + zone.y };
    const inset = Math.max(0, Math.round(Number(_hudStage.chatInset) || 0));
    return {
      x: 0,
      y: HUD_STAGE_NAV_H,
      width: Math.max(320, cb.width - inset),
      height: Math.max(240, cb.height - HUD_STAGE_NAV_H),
    };
  }
  if (mode === "fullscreen") {
    return { x: zone.x, y: zone.y, width: zone.w, height: zone.h };
  }
  if (mode === "minimized") {
    return { x: -2000, y: -2000, width: 1, height: 1 };
  }
  if (mode === "custom" && custom && typeof custom === "object") {
    const cb = mainWindow && !mainWindow.isDestroyed() ? mainWindow.getContentBounds() : { width: zone.w + zone.x, height: zone.h + zone.y };
    const w = Math.max(280, Math.min(cb.width, Number(custom.width) || zone.w * 0.7));
    const h = Math.max(220, Math.min(cb.height, Number(custom.height) || zone.h * 0.7));
    const rawX = Number.isFinite(Number(custom.x)) && custom.x !== null ? Number(custom.x) : zone.x + (zone.w - w) / 2;
    const rawY = Number.isFinite(Number(custom.y)) && custom.y !== null ? Number(custom.y) : zone.y + (zone.h - h) / 2;
    const x = Math.max(0, Math.min(cb.width - w, rawX));
    const y = Math.max(0, Math.min(cb.height - h, rawY));
    return { x: Math.round(x), y: Math.round(y), width: Math.round(w), height: Math.round(h) };
  }
  // "normal" — centered ~70% panel
  const w = Math.round(zone.w * 0.72);
  const h = Math.round(zone.h * 0.78);
  const x = Math.round(zone.x + (zone.w - w) / 2);
  const y = Math.round(zone.y + (zone.h - h) / 2);
  return { x, y, width: w, height: h };
}

function _hudAppApplyBounds(app) {
  if (!app || !app.view || app.view.webContents.isDestroyed()) return;
  try {
    if (app.id !== _hudAppActive || app.mode === "minimized" || _hudOverlayFrozen) {
      // While the stage overlay freeze is active, EVERY view is parked so HTML
      // panels can paint over the snapshot the dock renders in its place.
      app.view.setBounds({ x: -4000, y: -4000, width: 1, height: 1 });
      if (_hudOverlayFrozen && app.id === _hudAppActive) {
        app.boundsApplied = _hudAppComputeBounds(app.mode, app.bounds);
      }
      return;
    }
    const b = _hudAppComputeBounds(app.mode, app.bounds);
    const viewBounds = { ...b };
    // Stage mode has no 38px React titlebar — the island nav bar lives in the
    // reserved top strip instead.
    if (!_hudStage.enabled && app.mode !== "minimized" && viewBounds.height > HUD_APP_CHROME_H + 80) {
      viewBounds.y += HUD_APP_CHROME_H;
      viewBounds.height -= HUD_APP_CHROME_H;
    }
    app.view.setBounds(viewBounds);
    app.boundsApplied = b;
  } catch (_) {}
}

function _hudAppApplyAllBounds() {
  for (const app of _hudApps.values()) _hudAppApplyBounds(app);
}

function _hudStageCancelTween() {
  if (_hudStageTween) {
    clearInterval(_hudStageTween);
    _hudStageTween = null;
  }
}

// Smoothly animate the active view to its current target bounds (~250ms).
// Falls back to an instant snap whenever there is no meaningful start rect.
function _hudStageTweenBounds() {
  _hudStageCancelTween();
  const app = _hudAppActive ? _hudApps.get(_hudAppActive) : null;
  const snap = () => { _hudAppApplyAllBounds(); _hudAppBroadcast(); };
  if (!app || !app.view || app.view.webContents.isDestroyed() || app.mode === "minimized" || _hudOverlayFrozen) {
    snap();
    return;
  }
  let from = null;
  try { from = app.view.getBounds(); } catch (_) {}
  if (!from || from.x <= -1000 || from.width <= 1) { snap(); return; }
  const to = _hudAppComputeBounds(app.mode, app.bounds);
  const start = Date.now();
  const DUR = 250;
  _hudStageTween = setInterval(() => {
    const t = Math.min(1, (Date.now() - start) / DUR);
    const e = 1 - Math.pow(1 - t, 3); // easeOutCubic
    try {
      app.view.setBounds({
        x: Math.round(from.x + (to.x - from.x) * e),
        y: Math.round(from.y + (to.y - from.y) * e),
        width: Math.round(from.width + (to.width - from.width) * e),
        height: Math.round(from.height + (to.height - from.height) * e),
      });
    } catch (_) {}
    if (t >= 1) {
      _hudStageCancelTween();
      snap();
    }
  }, 16);
}

function _hudStageSet(payload) {
  const prev = { ..._hudStage };
  if (payload && typeof payload.enabled === "boolean") _hudStage.enabled = payload.enabled;
  if (payload && payload.chatInset !== undefined) {
    const inset = Number(payload.chatInset);
    _hudStage.chatInset = Number.isFinite(inset) ? Math.max(0, Math.min(800, Math.round(inset))) : 0;
  }
  if (!_hudStage.enabled && prev.enabled) {
    // Leaving stage: drop any overlay freeze so the windowed layout comes back live.
    _hudOverlayCount = 0;
    if (_hudOverlayFrozen) {
      _hudOverlayFrozen = false;
      try { mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents.send("hud-app:freeze", { frozen: false }); } catch (_) {}
    }
  }
  if (_hudStage.enabled && !prev.enabled) {
    // Entering stage: clear any island push-down state so it can't restore
    // stale pre-stage bounds later (the handler itself no-ops while staged).
    _islandOverlayActive = false;
    _islandOverlaySavedBounds = null;
  }
  const changed = prev.enabled !== _hudStage.enabled || prev.chatInset !== _hudStage.chatInset;
  _hudAppBroadcast(); // flip renderer UI promptly, even mid-tween
  if (changed && _hudStage.enabled && !_hudOverlayFrozen) {
    _hudStageTweenBounds();
  } else if (changed) {
    _hudStageCancelTween();
    _hudAppApplyAllBounds();
    _hudAppBroadcast();
  }
  if (changed) _hudAppDiag("stage_set", { enabled: _hudStage.enabled ? "true" : "false", chat_inset: _hudStage.chatInset });
  return { ok: true, stage: { enabled: _hudStage.enabled, chatInset: _hudStage.chatInset, navH: HUD_STAGE_NAV_H, frozen: _hudOverlayFrozen } };
}

function _hudAppAttachListeners(app) {
  if (!app || !app.view) return;
  const wc = app.view.webContents;
  // Refuse downloads from embedded apps — could DoS disk.
  try {
    wc.session.on("will-download", (event) => { try { event.preventDefault(); } catch (_) {} });
  } catch (_) {}
  // Reject window.open / target=_blank — surface as popout instead.
  try {
    wc.setWindowOpenHandler(({ url }) => {
      const safe = _hudAppUrlAllowed(url);
      if (safe.ok) { try { _hudAppOpenStaged(safe.url, "user_click"); } catch (_) {} }
      return { action: "deny" };
    });
  } catch (_) {}
  // Block navigations to unsafe schemes.
  try {
    wc.on("will-navigate", (event, navUrl) => {
      const safe = _hudAppUrlAllowed(navUrl);
      if (!safe.ok) {
        try { event.preventDefault(); } catch (_) {}
        return;
      }
      app.url = safe.url;
      _hudAppBroadcast();
    });
  } catch (_) {}
  wc.on("did-finish-load", () => {
    try { app.title = String(wc.getTitle() || app.title || "App"); } catch (_) {}
    try { app.url = String(wc.getURL() || app.url || ""); } catch (_) {}
    app.lastError = "";
    try {
      const latency = Date.now() - Number(app.loadStartedAt || app.createdAt || Date.now());
      _hudAppDiag("load_finish", { id: app.id || "", domain: _hudAppDomain(app.url || ""), latency_ms: latency, mode: app.mode || "normal", source: app.openSource || "" });
    } catch (_) {}
    _hudAppBroadcast();
  });
  wc.on("did-fail-load", (_e, errCode, errDesc, vUrl) => {
    if (errCode === -3) return; // aborted (e.g., user navigated away)
    app.lastError = `${errCode} ${errDesc}`.slice(0, 200);
    try {
      const latency = Date.now() - Number(app.loadStartedAt || app.createdAt || Date.now());
      _hudAppDiag("load_fail", { id: app.id || "", domain: _hudAppDomain(vUrl || app.url || ""), latency_ms: latency, error_code: errCode });
    } catch (_) {}
    _hudAppBroadcast();
  });
  wc.on("page-title-updated", (_e, title) => { app.title = String(title || ""); _hudAppBroadcast(); });
  wc.on("did-navigate", () => _hudAppBroadcast());
  wc.on("did-navigate-in-page", () => _hudAppBroadcast());
  wc.on("did-start-loading", () => {
    app.loadStartedAt = Date.now();
    app.lastError = "";
    try { _hudAppDiag("load_start", { id: app.id || "", domain: _hudAppDomain(app.url || ""), mode: app.mode || "normal", source: app.openSource || "" }); } catch (_) {}
    _hudAppBroadcast();
  });
  wc.on("dom-ready", () => {
    try {
      const latency = Date.now() - Number(app.loadStartedAt || app.createdAt || Date.now());
      _hudAppDiag("dom_ready", { id: app.id || "", domain: _hudAppDomain(app.url || ""), latency_ms: latency, source: app.openSource || "" });
    } catch (_) {}
  });
  wc.on("did-stop-loading", () => _hudAppBroadcast());
}

function _hudAppLoadURLWithTimeout(app, safeUrl) {
  return new Promise((resolve) => {
    if (!app || !app.view || app.view.webContents.isDestroyed()) {
      resolve({ ok: false, reason: "webcontents_destroyed" });
      return;
    }
    const wc = app.view.webContents;
    let settled = false;
    let timer = null;
    const cleanup = () => {
      try { wc.removeListener("did-finish-load", onFinish); } catch (_) {}
      try { wc.removeListener("did-fail-load", onFail); } catch (_) {}
      if (timer) {
        try { clearTimeout(timer); } catch (_) {}
        timer = null;
      }
    };
    const finish = (res) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(res || { ok: false, reason: "unknown_load_result" });
    };
    const onFinish = () => finish({ ok: true });
    const onFail = (_e, errCode, errDesc, vUrl, isMainFrame) => {
      if (errCode === -3) return; // aborted by a newer navigation
      if (isMainFrame === false) return;
      finish({
        ok: false,
        reason: `load_failed:${errCode}`,
        detail: String(errDesc || "load failed").slice(0, 160),
        validatedUrl: _hudAppDisplayUrl(vUrl || safeUrl),
      });
    };
    timer = setTimeout(() => {
      try {
        const latency = Date.now() - Number(app.loadStartedAt || app.createdAt || Date.now());
        _hudAppDiag("load_timeout", { id: app.id || "", domain: _hudAppDomain(safeUrl), latency_ms: latency, source: app.openSource || "" });
      } catch (_) {}
      finish({
        ok: false,
        reason: "initial_load_timeout",
        detail: `No load confirmation within ${Math.round(HUD_APP_INITIAL_LOAD_TIMEOUT_MS / 1000)}s`,
      });
    }, HUD_APP_INITIAL_LOAD_TIMEOUT_MS);
    try {
      wc.once("did-finish-load", onFinish);
      wc.once("did-fail-load", onFail);
      _hudAppDiag("open_start", { id: app.id || "", domain: _hudAppDomain(safeUrl), mode: app.mode || "normal", source: app.openSource || "" });
      const loadPromise = wc.loadURL(safeUrl);
      if (loadPromise && typeof loadPromise.catch === "function") {
        loadPromise.catch((exc) => {
          finish({
            ok: false,
            reason: "load_rejected",
            detail: String((exc && exc.message) || exc || "load rejected").slice(0, 160),
          });
        });
      }
    } catch (exc) {
      finish({
        ok: false,
        reason: "load_exception",
        detail: String((exc && exc.message) || exc || "load exception").slice(0, 160),
      });
    }
  });
}

// Every link SYRIX opens — in an answer, a source title, a page already open
// in the browser — lands in the in-HUD browser the way a source chip does:
// stage on, new tab. These paths used to create the view without switching
// stage on, which put it in the old windowed frame (no way to close or move
// it, a clock and sleep toggle from the first HUD in its bar).
function _hudAppOpenStaged(url, source) {
  try { if (!_hudStage.enabled) _hudStageSet({ enabled: true }); } catch (_) {}
  return _hudAppCreate({ url, mode: "normal", newTab: true, source }).catch(() => {});
}

async function _hudAppCreate({ url, mode, bounds, source, newTab }) {
  if (!mainWindow || mainWindow.isDestroyed()) return { ok: false, reason: "no_main_window" };
  if (!_hudBrowserNetworkReady && !await _configureHudBrowserNetwork("hud_app_create")) {
    return { ok: false, reason: "browser_network_guard_unavailable" };
  }
  _configureHudBrowserPermissions("hud_app_create");
  const safe = _hudAppUrlAllowed(url);
  if (!safe.ok) return safe;
  const nextMode = mode === "fullscreen" || mode === "minimized" || mode === "custom" ? mode : "normal";
  const openSource = _hudAppOpenSource(source);
  const safeKey = _hudAppUrlKey(safe.url);
  const safeSiteKey = _hudAppSiteKey(safe.url);
  const forceNewTab = !!newTab;
  for (const [existingId, existing] of _hudApps.entries()) {
    if (!existing || !existing.view || existing.view.webContents.isDestroyed()) continue;
    const sameExactUrl = _hudAppUrlKey(existing.url || "") === safeKey;
    const sameSite = safeSiteKey && _hudAppSiteKey(existing.url || "") === safeSiteKey;
    if (forceNewTab || (!sameExactUrl && !sameSite)) continue;
    existing.mode = nextMode;
    if (bounds && typeof bounds === "object") existing.bounds = bounds;
    existing.openSource = openSource;
    _hudAppActive = existingId;
    try { mainWindow.setTopBrowserView(existing.view); } catch (_) {}
    _hudAppApplyAllBounds();
    _hudAppBroadcast();
    _hudAppDiag("open_reuse", { id: existingId, domain: _hudAppDomain(existing.url || safe.url), mode: existing.mode, source: openSource, match: sameExactUrl ? "url" : "site" });
    if (existing.lastError) {
      return { ok: false, id: existingId, url: existing.url || safe.url, mode: existing.mode, reused: true, source: openSource, reason: "existing_app_load_error:" + String(existing.lastError).slice(0, 120) };
    }
    return { ok: true, id: existingId, url: existing.url || safe.url, mode: existing.mode, reused: true, focused: true, source: openSource };
  }
  if (_hudApps.size >= HUD_APP_MAX) return { ok: false, reason: "too_many_apps" };

  const view = new BrowserView({
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      backgroundThrottling: false,
      spellcheck: false,
      webviewTag: false,
      plugins: false,
      navigateOnDragDrop: false,
      // Persistent partition: cookies/localStorage survive HUD restarts only
      // inside this app-owned Electron profile. We DO NOT copy browser cookies,
      // scrape credentials, or manually store passwords.
      partition: HUD_BROWSER_SESSION_PARTITION,
      // No preload: embedded content has no Node access at all.
    },
  });
  try { view.setBackgroundColor("#03080f"); } catch (_) {}
  try { view.setAutoResize({ width: false, height: false, horizontal: false, vertical: false }); } catch (_) {}

  const id = "ha-" + Date.now() + "-" + Math.floor(Math.random() * 1e4);
  const app = {
    id,
    view,
    url: safe.url,
    title: "Loading…",
    mode: nextMode,
    bounds: bounds && typeof bounds === "object" ? bounds : null,
    vision: false,
    createdAt: Date.now(),
    loadStartedAt: Date.now(),
    openSource,
    controlsStatus: "pending",
    controlsDetail: "",
    controlsRetryCount: 0,
  };
  _hudApps.set(id, app);
  _hudAppActive = id;
  try { mainWindow.addBrowserView(view); } catch (_) {}
  _hudAppApplyAllBounds();
  _hudAppAttachListeners(app);
  _hudAppBroadcast();
  _hudAppDiag("frame_created", { id, domain: _hudAppDomain(safe.url), mode: app.mode || "normal", source: openSource });
  const loadWatch = _hudAppLoadURLWithTimeout(app, safe.url);
  _hudAppStartChromeWatchdog(id);
  loadWatch.then((loadResult) => {
    const live = _hudApps.get(id);
    if (!live) return;
    if (!loadResult || !loadResult.ok) {
      live.lastError = String((loadResult && (loadResult.detail || loadResult.reason)) || "load failed").slice(0, 200);
      try {
        _hudAppDiag("initial_load_incomplete", {
          id,
          domain: _hudAppDomain(safe.url),
          reason: (loadResult && loadResult.reason) || "load_failed",
          page_kept_open: "true",
        });
      } catch (_) {}
      _hudAppBroadcast();
    }
  }).catch((exc) => {
    const live = _hudApps.get(id);
    if (!live) return;
    live.lastError = String((exc && exc.message) || exc || "load failed").slice(0, 200);
    _hudAppBroadcast();
  });
  return { ok: true, id, url: safe.url, mode: app.mode, source: openSource, newTab: forceNewTab, detail: "loading" };
}

function _hudAppDestroy(id) {
  const app = _hudApps.get(id);
  if (!app) return { ok: false, reason: "not_found" };
  try {
    if (app.view) {
      // Move off-screen immediately so it vanishes visually even if async destroy is slow.
      try { app.view.setBounds({ x: -4000, y: -4000, width: 1, height: 1 }); } catch (_) {}
      try { mainWindow.removeBrowserView(app.view); } catch (_) {}
      if (!app.view.webContents.isDestroyed()) {
        try { app.view.webContents.session.cookies.flushStore(); } catch (_) {}
        try { app.view.webContents.stop(); } catch (_) {}
        // Destroy async so in-flight IPC responses don't crash.
        const wc = app.view.webContents;
        setTimeout(() => {
          try { if (!wc.isDestroyed()) { wc.close(); } } catch (_) {}
          setTimeout(() => { try { wc.destroy && wc.destroy(); } catch (_) {} }, 80);
        }, 30);
      }
    }
  } catch (_) {}
  _hudApps.delete(id);
  try { _hudAppChromeAckedAt.delete(id); } catch (_) {}
  if (_hudApps.size === 0) {
    // Last tab gone — stage/overlay state must self-heal (covers emergency
    // close and renderer crashes, not just the polite close button).
    _hudStageCancelTween();
    _hudStage = { enabled: false, chatInset: 0 };
    _hudOverlayCount = 0;
    if (_hudOverlayFrozen) {
      _hudOverlayFrozen = false;
      try { mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents.send("hud-app:freeze", { frozen: false }); } catch (_) {}
    }
  }
  if (_hudAppActive === id) {
    const next = _hudApps.size ? Array.from(_hudApps.keys()).pop() : null;
    _hudAppActive = next;
    if (next) {
      const nextApp = _hudApps.get(next);
      try { if (nextApp && nextApp.view) mainWindow.setTopBrowserView(nextApp.view); } catch (_) {}
    }
  }
  _hudAppApplyAllBounds();
  _hudAppBroadcast();
  return { ok: true };
}

function _hudAppSetMode(id, mode, bounds) {
  const app = _hudApps.get(id);
  if (!app) return { ok: false, reason: "not_found" };
  const next = ["normal", "fullscreen", "minimized", "custom"].includes(mode) ? mode : "normal";
  app.mode = next;
  if (bounds && typeof bounds === "object") app.bounds = bounds;
  // When un-minimizing, bring the BrowserView back to front of the stack so it
  // visually layers above sibling BrowserViews.
  if (next !== "minimized") {
    try { mainWindow.setTopBrowserView(app.view); } catch (_) {}
    _hudAppActive = id;
  }
  _hudAppApplyAllBounds();
  _hudAppBroadcast();
  return { ok: true, mode: next };
}

function _hudAppFocus(id) {
  const app = _hudApps.get(id);
  if (!app || !app.view || app.view.webContents.isDestroyed()) return { ok: false, reason: "not_found" };
  if (app.mode === "minimized") app.mode = "normal";
  _hudAppActive = id;
  try { mainWindow.setTopBrowserView(app.view); } catch (_) {}
  _hudAppApplyAllBounds();
  _hudAppBroadcast();
  _hudAppDiag("focus", { id, domain: _hudAppDomain(app.url || ""), mode: app.mode || "normal" });
  return { ok: true, id, focused: true };
}

function _hudAppNavigate(id, action, url) {
  const app = _hudApps.get(id);
  if (!app || !app.view || app.view.webContents.isDestroyed()) return { ok: false, reason: "not_found" };
  try {
    _hudAppActive = id;
    try { mainWindow.setTopBrowserView(app.view); } catch (_) {}
    _hudAppApplyAllBounds();
    const wc = app.view.webContents;
    if (action === "back") { if (wc.canGoBack()) wc.goBack(); else return { ok: false, reason: "no_back" }; }
    else if (action === "forward") { if (wc.canGoForward()) wc.goForward(); else return { ok: false, reason: "no_forward" }; }
    else if (action === "refresh") wc.reload();
    else if (action === "url" && url) {
      const safe = _hudAppUrlAllowed(String(url));
      if (!safe.ok) return safe;
      app.url = safe.url;
      app.loadStartedAt = Date.now();
      app.lastError = "";
      wc.loadURL(safe.url);
    }
    else return { ok: false, reason: "unknown_action" };
    _hudAppBroadcast();
    return { ok: true };
  } catch (exc) {
    return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
  }
}

function _hudAppPopout(id) {
  if (!HUD_BROWSER_ALLOW_EXTERNAL_POPOUT) {
    return { ok: false, reason: "external_popout_disabled_for_profile_isolation" };
  }
  const app = _hudApps.get(id);
  if (!app) return { ok: false, reason: "not_found" };
  const url = app.url || "";
  const safe = _hudAppUrlAllowed(url);
  if (!safe.ok) return safe;
  try { shell.openExternal(safe.url); } catch (exc) {
    return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
  }
  // Closing the HUD app after popout matches user expectation.
  return _hudAppDestroy(id);
}

function _hudAppSetVision(id, enabled) {
  const app = _hudApps.get(id);
  if (!app) return { ok: false, reason: "not_found" };
  app.vision = !!enabled;
  _hudAppBroadcast();
  return { ok: true, vision: app.vision };
}

// Chrome-mount watchdog: when a BrowserView is created, the React side is
// expected to render the per-app chrome (move/resize/nav/minimize/fullscreen/
// close). If the ack is late, we retry through HUD state and keep the tab open
// while the BrowserView is still alive; the emergency close accelerator remains
// available as a last-resort escape hatch.
const _hudAppChromeAckedAt = new Map();          // id -> ts of last React-side ack
const HUD_APP_CHROME_ACK_RETRY_DELAYS_MS = [900, 1800, 3200, 5200, 8000, 12000];
const HUD_APP_EMERGENCY_ACCELERATOR = "Control+Shift+W";
let _hudAppNoticeSeq = 0;

function _hudAppArmEmergencyCloseAccelerator() {
  // Idempotent. Always register so this works even if React is unresponsive.
  try {
    if (!globalShortcut.isRegistered(HUD_APP_EMERGENCY_ACCELERATOR)) {
      globalShortcut.register(HUD_APP_EMERGENCY_ACCELERATOR, () => {
        try {
          for (const id of Array.from(_hudApps.keys())) _hudAppDestroy(id);
          _syrixStartupLog("[HUD_BROWSER] event=emergency_close_all source=accelerator");
        } catch (_) {}
      });
    }
  } catch (_) {}
}

function _hudAppSetControlsStatus(app, status, detail, retryCount) {
  if (!app) return;
  app.controlsStatus = String(status || "");
  app.controlsDetail = String(detail || "").slice(0, 160);
  if (Number.isFinite(Number(retryCount))) app.controlsRetryCount = Number(retryCount);
  app.controlsLastStatusAt = Date.now();
  _hudAppBroadcast();
}

function _hudAppPublishControlsNotice(app, detail, tone, force) {
  try {
    if (!app) return;
    const now = Date.now();
    if (!force && app.controlsNoticeLastAt && (now - app.controlsNoticeLastAt) < 7000) return;
    app.controlsNoticeLastAt = now;
    const seq = ++_hudAppNoticeSeq;
    const text = String(detail || "controls retrying").slice(0, 180);
    const notice = {
      seq,
      ts: now / 1000,
      kind: "hud_browser_controls",
      tone: tone || "warn",
      text,
      id: app.id || "",
    };
    const latest = {
      seq,
      command: "hud_browser_controls",
      ok: tone !== "error",
      tone: tone || "warn",
      detail: text,
      ts: now / 1000,
      id: app.id || "",
      domain: _hudAppDomain(app.url || ""),
    };
    _syrixMergeHudState({
      syrix_island_notice: notice,
      syrix_latest_hud_notification: latest,
    }).catch(() => {});
  } catch (_) {}
}

function _hudAppStartChromeWatchdog(id) {
  const firstApp = _hudApps.get(id);
  if (firstApp) {
    firstApp.controlsAttachStartedAt = Date.now();
    firstApp.controlsRetryCount = 0;
    _hudAppChromeAckedAt.delete(id);
    _hudAppSetControlsStatus(firstApp, "attaching", "controls attaching", 0);
    _hudAppDiag("controls_attach_start", { id, domain: _hudAppDomain(firstApp.url || "") });
  }

  const scheduleCheck = (idx) => setTimeout(() => {
    const app = _hudApps.get(id);
    if (!app) return; // already gone
    const ackTs = _hudAppChromeAckedAt.get(id) || 0;
    if (ackTs > 0) return; // chrome confirmed
    const wc = app.view && app.view.webContents;
    const webContentsAlive = !!(wc && !wc.isDestroyed());
    let crashed = false;
    try { crashed = !!(webContentsAlive && typeof wc.isCrashed === "function" && wc.isCrashed()); } catch (_) {}
    const unrecoverable = !webContentsAlive || crashed;
    const attempt = idx + 1;
    const ageMs = Date.now() - Number(app.controlsAttachStartedAt || app.createdAt || Date.now());
    if (unrecoverable) {
      _hudAppSetControlsStatus(app, "failed", "controls unavailable", attempt);
      _hudAppDiag("controls_attach_unrecoverable", { id, attempt, age_ms: ageMs, crashed: crashed ? "true" : "false" });
      _hudAppPublishControlsNotice(app, "controls unavailable", "error", true);
      return;
    }

    // No React-side chrome confirmation yet. Keep the tab open while the page
    // remains usable, and keep nudging the HUD-side controls to attach.
    _hudAppSetControlsStatus(app, "retrying", "controls retrying", attempt);
    _hudAppDiag("controls_attach_retry", { id, attempt, age_ms: ageMs });
    _hudAppArmEmergencyCloseAccelerator();
    _hudAppPublishControlsNotice(app, "controls retrying", "warn", attempt === 1);
    if (idx + 1 < HUD_APP_CHROME_ACK_RETRY_DELAYS_MS.length) {
      scheduleCheck(idx + 1);
    }
  }, HUD_APP_CHROME_ACK_RETRY_DELAYS_MS[Math.min(idx, HUD_APP_CHROME_ACK_RETRY_DELAYS_MS.length - 1)]);

  scheduleCheck(0);
}

// Allow React's HudAppFrame to confirm it has mounted chrome for a given app.
ipcMain.handle("hud-app:chrome-ack", (_e, payload) => {
  try {
    const id = String((payload && payload.id) || "");
    if (!id || !_hudApps.has(id)) return { ok: false, reason: "unknown_id" };
    const now = Date.now();
    _hudAppChromeAckedAt.set(id, now);
    const app = _hudApps.get(id);
    if (app) {
      app.controlsAttachedAt = now;
      const latency = now - Number(app.controlsAttachStartedAt || app.createdAt || now);
      _hudAppSetControlsStatus(app, "attached", "controls attached", app.controlsRetryCount || 0);
      _hudAppDiag("controls_attached", {
        id,
        latency_ms: latency,
        retry_count: Number(app.controlsRetryCount || 0),
      });
    }
    return { ok: true };
  } catch (exc) {
    return { ok: false, reason: String(exc).slice(0, 120) };
  }
});

// Emergency close-all: zero-dependency path the bridge can call when React is
// confirmed unresponsive. Also exposed as a public IPC so a tray menu can fire it.
ipcMain.handle("hud-app:emergency-close-all", () => {
  try {
    const ids = Array.from(_hudApps.keys());
    for (const id of ids) _hudAppDestroy(id);
    _hudAppDiag("emergency_close_all", { count: ids.length, source: "ipc" });
    return { ok: true, closed: ids.length };
  } catch (exc) {
    return { ok: false, reason: String(exc).slice(0, 120) };
  }
});

ipcMain.handle("hud-app:open", async (_e, payload) => {
  const url = (payload && payload.url) || "";
  const mode = (payload && payload.mode) || "normal";
  const bounds = (payload && payload.bounds) || null;
  const source = _hudAppOpenSource(payload && (payload.source || payload.openSource || payload.reason));
  const newTab = !!(payload && (payload.newTab || payload.new_tab));
  const stageFlag = payload && typeof payload.stage === "boolean" ? payload.stage : null;
  _hudAppDiag("open_request", { source, domain: _hudAppDomain(url), mode, new_tab: newTab ? "true" : "false", stage: stageFlag === null ? "keep" : String(stageFlag) });
  // Arm the emergency accelerator EVERY time a tab is opened so it's available
  // before the first watchdog fires.
  _hudAppArmEmergencyCloseAccelerator();
  // Flip stage BEFORE creating the view so the first paint already uses stage
  // bounds — no flash of the floating panel.
  if (stageFlag !== null && stageFlag !== _hudStage.enabled) _hudStageSet({ enabled: stageFlag });
  return await _hudAppCreate({ url, mode, bounds, source, newTab });
});
ipcMain.handle("hud-app:stage", (_e, payload) => _hudStageSet(payload || {}));
// Refcounted overlay freeze: while any HTML panel wants to paint over the
// staged browser, swap the live view for a capturePage snapshot (rendered by
// the dock) and park every view. Restore live on the last release.
ipcMain.handle("hud-app:overlay", async (_e, payload) => {
  const acquire = !!(payload && payload.acquire);
  const prev = _hudOverlayCount;
  _hudOverlayCount = Math.max(0, _hudOverlayCount + (acquire ? 1 : -1));
  if (!_hudStage.enabled) return { ok: true, count: _hudOverlayCount, frozen: _hudOverlayFrozen };
  if (prev === 0 && _hudOverlayCount === 1) {
    _hudStageCancelTween();
    const app = _hudAppActive ? _hudApps.get(_hudAppActive) : null;
    let snapshot = null;
    let frozenBounds = null;
    if (app && app.view && !app.view.webContents.isDestroyed() && app.mode !== "minimized") {
      frozenBounds = _hudAppComputeBounds(app.mode, app.bounds);
      try {
        const img = await app.view.webContents.capturePage();
        const buf = img.toJPEG(70);
        if (buf && buf.length) snapshot = "data:image/jpeg;base64," + buf.toString("base64");
      } catch (_) {}
    }
    // A release may have raced in while capturePage was awaited.
    if (_hudOverlayCount <= 0 || !_hudStage.enabled) {
      return { ok: true, count: _hudOverlayCount, frozen: _hudOverlayFrozen };
    }
    _hudOverlayFrozen = true;
    _hudAppApplyAllBounds();
    try { mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents.send("hud-app:freeze", { frozen: true, snapshot, bounds: frozenBounds }); } catch (_) {}
    _hudAppBroadcast();
  } else if (prev > 0 && _hudOverlayCount === 0 && _hudOverlayFrozen) {
    _hudOverlayFrozen = false;
    _hudAppApplyAllBounds();
    try { mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents.send("hud-app:freeze", { frozen: false }); } catch (_) {}
    _hudAppBroadcast();
  }
  return { ok: true, count: _hudOverlayCount, frozen: _hudOverlayFrozen };
});
ipcMain.handle("hud-app:close", (_e, payload) => _hudAppDestroy((payload && payload.id) || ""));
ipcMain.handle("hud-app:focus", (_e, payload) => _hudAppFocus((payload && payload.id) || ""));
ipcMain.handle("hud-app:mode",  (_e, payload) => _hudAppSetMode(
  (payload && payload.id) || "", (payload && payload.mode) || "normal", payload && payload.bounds
));
ipcMain.handle("hud-app:nav",   (_e, payload) => _hudAppNavigate(
  (payload && payload.id) || "", (payload && payload.action) || "", (payload && payload.url) || null
));
ipcMain.handle("hud-app:popout", (_e, payload) => _hudAppPopout((payload && payload.id) || ""));
ipcMain.handle("hud-app:vision", (_e, payload) => _hudAppSetVision(
  (payload && payload.id) || "", !!(payload && payload.enabled)
));

// Drag support: hide BrowserView during drag so mousemove events reach the HTML layer,
// then restore at the dropped position. Without this, the BrowserView (native overlay)
// steals mousemove events the moment the cursor leaves the 38px chrome strip.
ipcMain.handle("hud-app:drag-start", async (_e, payload) => {
  const id = String((payload && payload.id) || "");
  const app = _hudApps.get(id);
  if (!app || !app.view) return { ok: false, reason: "not_found" };
  let snapshot = null;
  try {
    const img = await app.view.webContents.capturePage();
    const jpegBuf = img.toJPEG(72);
    snapshot = "data:image/jpeg;base64," + jpegBuf.toString("base64");
  } catch (_) {}
  try { app.view.setBounds({ x: -4000, y: -4000, width: 1, height: 1 }); } catch (_) {}
  return { ok: true, snapshot };
});
ipcMain.handle("hud-app:drag-end", (_e, payload) => {
  const id = String((payload && payload.id) || "");
  const bounds = payload && payload.bounds;
  if (!id) return { ok: false, reason: "no_id" };
  return _hudAppSetMode(id, "custom", bounds);
});

// ── Island overlay — push active BrowserView below island panel when open ────
// The Dynamic Island panel is ~420px tall from top: 18px. When open, we shift
// the active BrowserView down so the island dropdown renders fully visible above
// it (BrowserViews are native overlays that normally cover HTML elements).
let _islandOverlayActive = false;
let _islandOverlaySavedBounds = null; // { id, bounds }
ipcMain.handle("hud:island-overlay", (_e, payload) => {
  // In stage mode the island IS the nav bar and the view already starts below
  // it; the push-down behavior would fight the stage bounds.
  if (_hudStage.enabled) return;
  const open = !!(payload && payload.open);
  if (open === _islandOverlayActive) return;
  _islandOverlayActive = open;
  if (open) {
    const app = _hudAppActive ? _hudApps.get(_hudAppActive) : null;
    if (app && app.view && app.boundsApplied && app.mode !== "minimized") {
      try {
        const cur = app.boundsApplied;
        const ISLAND_BOTTOM = 440;
        if (cur.y < ISLAND_BOTTOM) {
          _islandOverlaySavedBounds = { id: app.id, bounds: { ...cur } };
          // Frame (chrome bar) starts at ISLAND_BOTTOM; BrowserView content starts CHROME_H below that.
          const newFrameH = Math.max(HUD_APP_CHROME_H + 80, cur.height - (ISLAND_BOTTOM - cur.y));
          const newViewH = Math.max(80, newFrameH - HUD_APP_CHROME_H);
          app.view.setBounds({ x: cur.x, y: ISLAND_BOTTOM + HUD_APP_CHROME_H, width: cur.width, height: newViewH });
          app.boundsApplied = { x: cur.x, y: ISLAND_BOTTOM, width: cur.width, height: newFrameH };
          _hudAppBroadcast();
        }
      } catch (_) {}
    }
  } else {
    if (_islandOverlaySavedBounds) {
      _islandOverlaySavedBounds = null;
      _hudAppApplyAllBounds();
      _hudAppBroadcast();
    }
  }
});

// ── Supercomputer browser (2026-07-24) ─────────────────────────────────────
// The whole subsystem lives in syrix-supercomputer-browser.js: its own view
// registry, its own sc-browser:* handlers, its own hardening. main.js only
// bootstraps it on first use (so a session that never enters the mode never
// even loads the file) and disposes it on quit.
//
// It runs in HUD_BROWSER_SESSION_PARTITION — the SAME Electron profile as the
// HUD's own browser — so Supercomputer is signed in to whatever the user is signed
// in to there. Without that, "read my email" and "open my docs" cannot work at
// all. Acting inside those accounts is still gated per-action on the Python
// side; reachability is not authority.
//
// Read from the real parent config.json, same pattern as the telemetry flag
// above. Default ON: signed out of everything, this mode cannot do most of what
// it exists for. Set supercomputer_persistent_profile_enabled false to run it
// in a throwaway partition instead (applied at HUD launch; restart to change).
let _SC_SHARED_PROFILE = true;
(function _loadScProfileFlag() {
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "config.json"), "utf8"));
    if (typeof cfg.supercomputer_persistent_profile_enabled === "boolean") {
      _SC_SHARED_PROFILE = cfg.supercomputer_persistent_profile_enabled;
    }
  } catch (_) {}
})();

// Open a file SYRIX produced with the OS default application.
//
// The user clicks the filename in the chat; this is a user action, not an agent
// one, and the agent cannot reach it — there is no tool that calls this. Scoped
// anyway, because "open a path the renderer sent" is exactly the shape of thing
// that becomes a way to launch anything: it must resolve inside the
// Supercomputer workspaces tree, a chosen working folder, or Downloads, and it
// must not be executable.
const _SC_UNSAFE_OPEN = new Set([
  ".exe", ".msi", ".bat", ".cmd", ".com", ".scr", ".cpl", ".ps1", ".psm1",
  ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".dll", ".sys",
  ".reg", ".inf", ".lnk", ".jar", ".apk", ".app", ".dmg", ".pkg", ".deb", ".rpm",
]);

function _scOpenRoots() {
  const roots = [];
  try {
    roots.push(path.join(os.homedir(), "AppData", "Local", "SYRIX", "supercomputer"));
    roots.push(path.join(os.homedir(), "Downloads"));
  } catch (_) {}
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "config.json"), "utf8"));
    const chosen = String(cfg.supercomputer_workspace_root || "").trim();
    if (chosen) roots.push(chosen);
  } catch (_) {}
  return roots.filter(Boolean).map((r) => { try { return fs.realpathSync(r); } catch (_) { return r; } });
}

ipcMain.handle("sc-browser:open-file", async (_e, payload) => {
  const raw = String((payload && payload.path) || "").trim();
  if (!raw) return { ok: false, reason: "no_path" };
  let target;
  try { target = fs.realpathSync(raw); } catch (_) { return { ok: false, reason: "not_found" }; }
  if (_SC_UNSAFE_OPEN.has(path.extname(target).toLowerCase())) {
    return { ok: false, reason: "refused_executable" };
  }
  const inside = _scOpenRoots().some((root) => {
    const rel = path.relative(root, target);
    return rel && !rel.startsWith("..") && !path.isAbsolute(rel);
  });
  if (!inside) return { ok: false, reason: "outside_workspace" };
  try {
    const { shell } = require("electron");
    const err = await shell.openPath(target);
    return err ? { ok: false, reason: String(err).slice(0, 160) } : { ok: true };
  } catch (exc) {
    return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
  }
});

// Folder picker for Supercomputer's working folder. Electron owns dialogs; the
// Python side validates whatever comes back before anything is written to it,
// so this returns a path and grants nothing by itself.
ipcMain.handle("sc-browser:choose-folder", async () => {
  try {
    const { dialog } = require("electron");
    const res = await dialog.showOpenDialog(mainWindow, {
      title: "Choose where SYRIX saves what it makes",
      properties: ["openDirectory", "createDirectory"],
    });
    if (!res || res.canceled || !res.filePaths || !res.filePaths.length) {
      return { ok: false, reason: "cancelled" };
    }
    return { ok: true, path: res.filePaths[0] };
  } catch (exc) {
    return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
  }
});

ipcMain.handle("sc-browser:ensure", () => _ensureGuardedBrowserModule({
  key: "protected_workspace",
  networkReason: "protected_workspace",
  load: () => require("./syrix-supercomputer-browser.js"),
  install: (module) => module.install({
    ipcMain, session, BrowserView,
    getMainWindow: () => mainWindow,
    sharedPartition: _SC_SHARED_PROFILE ? HUD_BROWSER_SESSION_PARTITION : "",
    log: (m) => _syrixStartupLog(`[SC_BROWSER] ${m}`),
  }),
  partition: (module) => module._internals.partitionId(),
  partitionReason: _SC_SHARED_PROFILE ? "supercomputer_shared" : "supercomputer_ephemeral",
  snapshot: (module) => module.snapshot(),
}));

// ── Phase 5: HUD-sandbox automation primitives ─────────────────────────────
// SYRIX drives ONLY the embedded BrowserViews (never the user's real desktop).
// read  = executeJavaScript digest (assigns stable data-syrix-id to actionable
//          elements); act = sendInputEvent (real trusted mouse/key events that
//          work with React-controlled inputs); shot = capturePage.
// Every call is gated by the Python policy layer (sandbox_browser.py) BEFORE it
// reaches here; this side just executes against the live webContents.

// Digest script: runs inside the page, tags actionable elements with a stable
// id, and returns a compact structural summary the agent can reason over.
const _SYRIX_DIGEST_JS = `(() => {
  const out = { title: document.title || "", url: location.href, elements: [] };
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return null;
    if (r.bottom < 0 || r.top > (window.innerHeight + 600)) return null;
    const st = window.getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || st.opacity === "0") return null;
    return r;
  };
  let n = 0;
  const sel = 'a,button,input,textarea,select,[role=button],[role=link],[role=tab],[role=searchbox],[onclick]';
  const seen = new Set();
  document.querySelectorAll(sel).forEach((el) => {
    if (n >= 120) return;
    const r = vis(el); if (!r) return;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    let label = (el.getAttribute("aria-label") || el.getAttribute("placeholder") || el.getAttribute("name") || el.value || el.innerText || el.textContent || "").trim().replace(/\\s+/g, " ").slice(0, 80);
    if (!label && tag === "input") label = type || "input";
    const key = tag + "|" + label + "|" + Math.round(r.top);
    if (seen.has(key)) return; seen.add(key);
    let id = el.getAttribute("data-syrix-id");
    if (!id) { id = "sx" + (++n); el.setAttribute("data-syrix-id", id); }
    out.elements.push({
      id, tag, type, label,
      role: el.getAttribute("role") || "",
      editable: (tag === "input" && !["button","submit","checkbox","radio","hidden"].includes(type)) || tag === "textarea" || el.isContentEditable === true,
      isPassword: type === "password",
      cx: Math.round(r.left + r.width / 2),
      cy: Math.round(r.top + r.height / 2),
    });
  });
  // Lightweight readable text (digest mode caps this).
  out.text = (document.body ? (document.body.innerText || "") : "").replace(/\\s+\\n/g, "\\n").trim().slice(0, 6000);
  return out;
})()`;

function _sandboxApp(id) {
  const app = _hudApps.get(String(id || "") || _hudAppActive || "");
  if (!app || !app.view || app.view.webContents.isDestroyed()) return null;
  return app;
}

ipcMain.handle("hud-app:dom-digest", async (_e, payload) => {
  const app = _sandboxApp(payload && payload.id);
  if (!app) return { ok: false, reason: "no_active_tab" };
  const mode = String((payload && payload.mode) || "digest");
  try {
    const data = await app.view.webContents.executeJavaScript(_SYRIX_DIGEST_JS, true);
    if (mode === "text") {
      return { ok: true, data: { title: data.title, url: data.url, text: data.text } };
    }
    return { ok: true, data };
  } catch (exc) {
    return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
  }
});

ipcMain.handle("hud-app:input", async (_e, payload) => {
  const app = _sandboxApp(payload && payload.id);
  if (!app) return { ok: false, reason: "no_active_tab" };
  const op = String((payload && payload.op) || "");
  const wc = app.view.webContents;
  try {
    // Resolve element centre coords from its stable id (live re-read so a
    // re-rendered DOM can't act on stale coordinates).
    const locate = async (elId) => {
      const js = `(() => { const el = document.querySelector('[data-syrix-id=' + JSON.stringify(${JSON.stringify(String(elId || ""))}) + ']'); if (!el) return null; const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return null; el.scrollIntoView({block:'center',inline:'center'}); const r2 = el.getBoundingClientRect(); const t = (el.getAttribute('type')||'').toLowerCase(); return { cx: Math.round(r2.left + r2.width/2), cy: Math.round(r2.top + r2.height/2), isPassword: t === 'password' }; })()`;
      return await wc.executeJavaScript(js, true);
    };
    if (op === "click") {
      const loc = await locate(payload.element_id);
      if (!loc) return { ok: false, reason: "element_not_found" };
      wc.sendInputEvent({ type: "mouseMove", x: loc.cx, y: loc.cy });
      wc.sendInputEvent({ type: "mouseDown", x: loc.cx, y: loc.cy, button: "left", clickCount: 1 });
      wc.sendInputEvent({ type: "mouseUp", x: loc.cx, y: loc.cy, button: "left", clickCount: 1 });
      return { ok: true, data: { x: loc.cx, y: loc.cy } };
    }
    if (op === "type") {
      const loc = await locate(payload.element_id);
      if (!loc) return { ok: false, reason: "element_not_found" };
      // Password fields are refused on the Python side; this is defence in depth.
      if (loc.isPassword) return { ok: false, reason: "refused_password_field" };
      wc.sendInputEvent({ type: "mouseDown", x: loc.cx, y: loc.cy, button: "left", clickCount: 1 });
      wc.sendInputEvent({ type: "mouseUp", x: loc.cx, y: loc.cy, button: "left", clickCount: 1 });
      const text = String((payload && payload.text) || "");
      for (const ch of text) {
        wc.sendInputEvent({ type: "char", keyCode: ch });
      }
      if (payload && payload.submit) {
        wc.sendInputEvent({ type: "keyDown", keyCode: "Return" });
        wc.sendInputEvent({ type: "char", keyCode: "\\r" });
        wc.sendInputEvent({ type: "keyUp", keyCode: "Return" });
      }
      return { ok: true, data: { typed: text.length } };
    }
    if (op === "scroll") {
      const dy = Number((payload && payload.dy) || 600);
      wc.sendInputEvent({ type: "mouseWheel", x: 10, y: 10, deltaX: 0, deltaY: -dy, canScroll: true });
      return { ok: true };
    }
    return { ok: false, reason: "unknown_op" };
  } catch (exc) {
    return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
  }
});

ipcMain.handle("hud-app:capture", async (_e, payload) => {
  const app = _sandboxApp(payload && payload.id);
  if (!app) return { ok: false, reason: "no_active_tab" };
  try {
    const img = await app.view.webContents.capturePage();
    return { ok: true, data: { png_base64: img.toPNG().toString("base64") } };
  } catch (exc) {
    return { ok: false, reason: String((exc && exc.message) || exc).slice(0, 160) };
  }
});
ipcMain.handle("hud-app:ack", async (_e, payload) => {
  const raw = payload && typeof payload === "object" ? payload : {};
  const ack = {
    seq: Number(raw.seq || 0),
    command: String(raw.command || "").slice(0, 80),
    ok: !!raw.ok,
    detail: String(raw.detail || "").slice(0, 240),
    ts: Number(raw.ts || Date.now() / 1000),
  };
  for (const k of ["id", "mode", "action", "latency_ms", "request_id", "target_type", "domain", "reused", "focused", "new_tab"]) {
    if (k in raw) ack[k] = raw[k];
  }
  const t0 = Date.now();
  const ok = await _syrixMergeHudState({
    syrix_hud_app_ack: ack,
    syrix_latest_hud_notification: ack,
  });
  try {
    const ms = Math.max(0, Date.now() - t0);
    if (ms > 250 || !ok) {
      console.info(
        "[HUD_LATENCY] subsystem=electron_main event=hud_app_ack_write status=" +
          (ok ? "ok" : "failed") +
          " latency_ms=" +
          ms +
          " command=" +
          String(ack.command || ""),
      );
    }
  } catch (_) {}
  return { ok };
});
ipcMain.handle("hud-app:list", () => {
  const list = [];
  for (const [id, app] of _hudApps.entries()) {
    list.push({
      id,
      url: _hudAppDisplayUrl(app.url || ""),
      domain: _hudAppDomain(app.url || ""),
      title: app.title || "",
      mode: app.mode || "normal",
      lastError: app.lastError || "",
      vision: !!app.vision,
    });
  }
  return {
    apps: list,
    active: _hudAppActive,
    stage: { enabled: _hudStage.enabled, chatInset: _hudStage.chatInset, navH: HUD_STAGE_NAV_H, frozen: _hudOverlayFrozen },
  };
});

// Persistent session metadata + clear-cache helper. Cleared cookies/storage
// scope is limited to the HUD app partition; the main HUD's own state never
// touches this partition.
ipcMain.handle("hud-app:session-info", () => {
  return {
    persistent: HUD_APP_PERSISTENT_SESSION,
    partition: HUD_BROWSER_SESSION_PARTITION,
    userDataPath: HUD_BROWSER_USER_DATA_PATH,
    cachePath: HUD_BROWSER_CACHE_PATH,
    clearOnStartup: HUD_BROWSER_CLEAR_DATA_ON_STARTUP,
    externalPopoutAllowed: HUD_BROWSER_ALLOW_EXTERNAL_POPOUT,
  };
});
ipcMain.handle("hud-app:clear-session", async () => {
  try {
    for (const id of Array.from(_hudApps.keys())) _hudAppDestroy(id);
  } catch (_) {}
  // clearStorageData wipes cookies, localStorage, IndexedDB, cache storage,
  // service workers, etc. for this partition only.
  return _hudBrowserClearSessionData("ipc_request");
});

app.on("second-instance", () => {
  if (_appShutdownInProgress) {
    // The window was closed and this instance is still tearing down. Showing
    // it again revived a window that then closed itself; start a fresh
    // instance once the teardown has finished instead.
    _syrixRelaunchRequested = true;
    _syrixStartupLog("[PROCESS_GUARD] electron_second_instance action=relaunch_after_teardown");
    return;
  }
  _syrixStartupLog("[PROCESS_GUARD] electron_second_instance action=show_existing_hud");
  if (mainWindow) {
    _syrixForceShowMainWindow("second-instance");
  } else {
    createWindow();
  }
});

app.whenReady().then(async () => {
  if (!gotLock) return;
  try {
    if (process.platform === "win32" && app.setAppUserModelId) {
      app.setAppUserModelId("com.syrix.neural.hud");
    }
  } catch (_) {}
  _syrixEnsureHudDistCurrent();
  if (HUD_BROWSER_CLEAR_DATA_ON_STARTUP) {
    await _hudBrowserClearSessionData("startup_config");
  }
  await _configureHudBrowserNetwork("app_ready");
  _configureHudBrowserPermissions("app_ready");
  _syrixStartupLog(`[STARTUP] app_ready pid=${process.pid} cwd=${process.cwd()} action=create_visible_hud_first`);
  // Wipe stale app-dock state persisted from the last session. BrowserViews do
  // not survive Electron restarts, and restore is intentionally opt-in so the
  // HUD boots clean in standby with no browser tab/frame.
  try {
    const _hs = JSON.parse(fs.readFileSync(_SYRIX_HUD_STATE_PATH, "utf8") || "{}");
    let _changed = false;
    const _staleApps = Array.isArray(_hs.syrix_hud_apps) ? _hs.syrix_hud_apps : [];
    if (_hs.syrix_hud_app_request != null) {
      _hs.syrix_hud_app_request = null;
      _changed = true;
    }
    if (!HUD_APP_RESTORE_ON_STARTUP && (_staleApps.length || _hs.syrix_active_hud_app || _hs.syrix_active_hud_context || _hs.syrix_hud_app_mode)) {
      const hadHeatmap = _staleApps.some((a) => /tradingview\.com\/heatmap\/stock/i.test(String((a && a.url) || "")));
      _hs.syrix_hud_apps = [];
      _hs.syrix_active_hud_app = "";
      _hs.syrix_active_hud_context = null;
      _hs.syrix_hud_app_mode = null;
      _hs.syrix_hud_app_vision_active = false;
      _changed = true;
      _syrixStartupLog(`[STARTUP] cleared stale hud_app session_state count=${_staleApps.length} heatmap=${hadHeatmap ? "true" : "false"} restore_enabled=false`);
    }
    if (_changed) {
      _syrixMergeHudState({
        syrix_hud_app_request: _hs.syrix_hud_app_request,
        syrix_hud_apps: _hs.syrix_hud_apps,
        syrix_active_hud_app: _hs.syrix_active_hud_app,
        syrix_active_hud_context: _hs.syrix_active_hud_context,
        syrix_hud_app_mode: _hs.syrix_hud_app_mode,
        syrix_hud_app_vision_active: _hs.syrix_hud_app_vision_active,
      });
    }
  } catch (_) {}
  _syrixStartBackendAfterHudVisible("app_ready_parallel");
  createWindow();
  _syrixCreateTray();

  // Privacy hotkey: Ctrl+Alt+P toggles screen-vision privacy.
  try {
    globalShortcut.register("Control+Alt+P", () => {
      if (mainWindow) {
        mainWindow.webContents.send("hud:hotkey", { name: "privacy" });
      }
    });
  } catch (_) {
    /* hotkey registration is best effort */
  }

  // Quick-actions palette: Ctrl+K (mirrors Cmd-K plan item).
  // CommandOrControl+K registers reliably on Windows; plain Control+K often fails silently if reserved.
  try {
    const paletteCb = () => {
      if (mainWindow) mainWindow.webContents.send("hud:hotkey", { name: "palette" });
    };
    if (!globalShortcut.register("CommandOrControl+K", paletteCb)) {
      globalShortcut.register("Ctrl+Shift+K", paletteCb);
    }
  } catch (_) {}

  // Screen Vision capture — returns the primary display so "what's on my
  // screen?" sees the user's actual content, not the SYRIX HUD overlay.
  // HUD App vision (the ◉ button in the browser chrome) captures the same
  // screen; since SYRIX sits on top, its window is visible within the frame.
  try {
    session.defaultSession.setDisplayMediaRequestHandler(
      async (_request, callback) => {
        try {
          const [screenSources, windowSources] = await Promise.all([
            desktopCapturer.getSources({ types: ["screen"] }),
            desktopCapturer.getSources({ types: ["window"] }),
          ]);
          console.log(
            `[SCREEN_CAPTURE] screen_sources=${screenSources.length} window_sources=${windowSources.length}`
          );
          // Prefer the primary screen (Entire Screen / Screen 1).
          const primaryScreen = screenSources.find((s) => {
            const n = String((s && s.name) || "").toLowerCase();
            return n.includes("entire screen") || n.includes("screen 1") || n.includes("display 1") || n === "screen 0";
          }) || screenSources[0];
          if (primaryScreen) {
            console.log(`[SCREEN_CAPTURE] using_screen source=${primaryScreen.name}`);
            callback({ video: primaryScreen });
            return;
          }
          // Fallback: use any available window source so vision still works.
          const anyWindow = windowSources[0];
          if (anyWindow) {
            console.log(`[SCREEN_CAPTURE] screen_unavailable fallback=window source=${anyWindow.name}`);
            callback({ video: anyWindow });
            return;
          }
          console.warn("[SCREEN_CAPTURE] no_capture_source_found");
          callback({});
        } catch (err) {
          console.warn(
            "[SCREEN_CAPTURE] handler_error:",
            err && err.message ? err.message : err
          );
          callback({});
        }
      },
      { useSystemPicker: false }
    );
    console.log("[SCREEN_CAPTURE] setDisplayMediaRequestHandler registered");
  } catch (err) {
    console.warn(
      "[SCREEN_CAPTURE] setDisplayMediaRequestHandler failed:",
      err && err.message ? err.message : err
    );
  }
});

app.on("window-all-closed", () => {
  _syrixStopStartupVisibilityMonitor();
  _syrixStopHudReadyHeartbeat("window_all_closed");
  if (process.platform !== "darwin") app.quit();
});

function _syrixKillBackend(opts) {
  // The ownership sweep is a PowerShell process scan (~1 s). The teardown
  // skips it here because _syrixVerifyOwnedProcessesGone runs the same scan
  // straight after and kills anything it finds.
  const sweep = !(opts && opts.sweep === false);
  const pids = new Set();
  if (_syrixPythonProc && _syrixPythonProc.pid) pids.add(Number(_syrixPythonProc.pid));
  if (process.platform === "win32") {
    if (sweep) {
      for (const row of _syrixOwnedProcessSnapshot()) {
        if (_syrixIsOwnedProcess(row)) pids.add(Number(row.ProcessId));
      }
    }
    for (const pid of pids) _syrixTaskkillTreeSync(pid, "grace_timeout");
  } else {
    for (const pid of pids) {
      try { process.kill(-pid, "SIGTERM"); } catch (_) {}
      try { process.kill(pid, "SIGTERM"); } catch (_) {}
    }
  }
  _syrixPythonProc = null;
}

// 2026-06-01 — Belt-and-suspenders ollama cleanup.
// The Python shutdown path tries to kill the ollama process SYRIX spawned, but
// if Electron force-kills Python via `taskkill /T /F` the Python handler does
// not get a chance to run.  Reading the SYRIX-owned PID file here closes that
// gap so ollama.exe never lingers after the HUD closes.  A user's pre-existing
// Ollama (started before SYRIX boot) is untouched because no PID file is
// written in that path.
function _syrixKillOllamaIfOwned() {
  try {
    const pidFile = _syrixPaths.runFile("ollama_started_by_syrix.pid");
    if (!fs.existsSync(pidFile)) return;
    const raw = fs.readFileSync(pidFile, "utf8").trim();
    let pid = parseInt(raw, 10);
    if (!Number.isFinite(pid)) {
      try {
        const record = JSON.parse(raw);
        pid = parseInt(record && record.pid, 10);
      } catch (_) {}
    }
    if (!Number.isFinite(pid) || pid <= 0) {
      try { fs.unlinkSync(pidFile); } catch (_) {}
      return;
    }
    if (process.platform === "win32") {
      _syrixTaskkillTreeSync(pid, "owned_ollama");
    } else {
      try { process.kill(-pid, "SIGTERM"); } catch (_) {}
      try { process.kill(pid, "SIGTERM"); } catch (_) {}
    }
    try { fs.unlinkSync(pidFile); } catch (_) {}
    console.log(`[SYRIX_OLLAMA] kill pid=${pid}`);
  } catch (err) {
    console.warn(`[SYRIX_OLLAMA] kill failed: ${err && err.message ? err.message : err}`);
  }
}

// The v3 core starts its own `ollama serve` when no cloud lane can answer
// (and always in Private mode) and records it in state/v3/runtime/ollama.pid.
// Python stops it on a graceful exit, but a force-killed backend never runs
// that hook, and the server then held RAM and VRAM until the next launch.
// Killed only when the recorded pid is still an ollama.exe, so a pid reused
// by another program after a reboot is never touched.
function _syrixKillV3LocalModel() {
  try {
    const pidFile = path.join(_syrixPaths.stateDir(), "v3", "runtime", "ollama.pid");
    if (!fs.existsSync(pidFile)) return;
    let pid = 0;
    try { pid = parseInt(JSON.parse(fs.readFileSync(pidFile, "utf8")).pid, 10); } catch (_) {}
    if (Number.isFinite(pid) && pid > 0 && process.platform === "win32") {
      let image = "";
      try {
        image = execFileSync("tasklist.exe", ["/FI", `PID eq ${pid}`, "/FO", "CSV", "/NH"], {
          windowsHide: true, timeout: 3000, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"],
        });
      } catch (_) {}
      if (/"ollama\.exe"/i.test(String(image || ""))) _syrixTaskkillTreeSync(pid, "v3_local_model");
    }
    try { fs.unlinkSync(pidFile); } catch (_) {}
  } catch (err) {
    console.warn(`[SYRIX_OLLAMA] v3 local model stop failed: ${err && err.message ? err.message : err}`);
  }
}

// Everything SYRIX started is stopped here, once, whichever way the app is
// leaving: the backend tree, the models it loaded into Ollama, an Ollama it
// started itself, v3's local model server, then a scan that kills anything
// still standing.
let _syrixTeardownPromise = null;
let _syrixTeardownDone = false;
function _syrixTeardown(reason) {
  if (_syrixTeardownPromise) return _syrixTeardownPromise;
  _syrixTeardownPromise = (async () => {
    const t0 = Date.now();
    try { _syrixKillBackend({ sweep: false }); } catch (_) {}
    try { await _syrixReleaseOllamaModels(reason); } catch (_) {}
    try { _syrixKillOllamaIfOwned(); } catch (_) {}
    try { _syrixKillV3LocalModel(); } catch (_) {}
    // Last word before the process goes: nothing SYRIX-owned may outlive it.
    try { _syrixVerifyOwnedProcessesGone(reason); } catch (_) {}
    _syrixTeardownDone = true;
    _syrixStartupLog(`[SYRIX_SHUTDOWN] teardown_done reason=${reason} ms=${Date.now() - t0}`);
  })();
  return _syrixTeardownPromise;
}

function _syrixReleaseAppResources() {
  _syrixDestroyTray();
  _syrixStopStartupVisibilityMonitor();
  _syrixStopHudReadyHeartbeat("app_exit");
  // Embedded browser modules own their views and partitions; tear all lazy
  // modules down even when the app exits without a clean mode exit.
  _disposeGuardedBrowserModules();
  try {
    if (_hudSafeProxy) {
      _hudSafeProxy.close();
      _hudSafeProxy = null;
      _hudBrowserNetworkReady = false;
      _hudBrowserGuardedPartitions.clear();
    }
  } catch (_) {}
  try {
    globalShortcut.unregisterAll();
  } catch (_) {}
}

// app.exit() rather than app.quit(): the teardown has already run, and quit()
// re-enters will-quit. A launch that arrived during the teardown is honoured
// here, by starting a fresh instance once this one has released everything.
function _syrixFinishExit() {
  _syrixReleaseAppResources();
  if (_syrixRelaunchRequested) {
    try {
      const args = process.argv.slice(1).map((a) => (a === "." ? app.getAppPath() : a));
      app.relaunch({ args });
      _syrixStartupLog("[PROCESS_GUARD] relaunch_after_teardown=true");
    } catch (_) {}
  }
  app.exit(0);
}

app.on("will-quit", (event) => {
  // Reached by a renderer crash, window-all-closed or another exit that did
  // not go through the window's close handler. Never leave SYRIX's backend
  // (or its owned Ollama child) running after the HUD process exits.
  if (_syrixTeardownDone) {
    _syrixReleaseAppResources();
    return;
  }
  event.preventDefault();
  _syrixTeardown("will_quit").finally(_syrixFinishExit);
});

app.on("quit", () => {
  _syrixStopHudReadyHeartbeat("quit");
});
