"use strict";

// Static fail-closed gates for the cross-process HUD WebSocket nonce. This
// checker intentionally never reads the nonce or launches Electron/backend.
const fs = require("fs");
const path = require("path");

function read(name) {
  return fs.readFileSync(path.join(__dirname, name), "utf8");
}

function requireText(source, needle, label) {
  if (!source.includes(needle)) {
    throw new Error("HUD WS auth check failed: " + label);
  }
}

const main = read("main.js");
const preload = read("preload.js");
const bridge = read("syrix-bridge.js");
const boot = read("syrix-boot-cinematic.jsx");

requireText(main, 'crypto.randomBytes(32).toString("hex")', "256-bit boot nonce generation missing");
requireText(main, "SYRIX_HUD_WS_TOKEN: _SYRIX_HUD_WS_BOOT_TOKEN", "backend child does not inherit nonce");
requireText(main, 'ipcMain.handle("hud:ws-auth-token", (event) => {', "trusted-shell nonce IPC missing");
requireText(main, "_syrixTrustedHudIpcSender(event)", "nonce IPC sender gate missing");
requireText(preload, 'ipcRenderer.invoke("hud:ws-auth-token")', "preload nonce bridge missing");
if (preload.includes("process.env.SYRIX_HUD_WS_TOKEN")) {
  throw new Error("HUD WS auth check failed: preload reads nonce directly from environment");
}

requireText(bridge, "_authenticateHudSocket(ws);", "socket open does not start authentication");
requireText(bridge, "ws.send(JSON.stringify({ type: 'auth', token }));", "auth frame missing");
requireText(
  bridge,
  "if (!_wsAuthenticated || !socket || socket.readyState !== WebSocket.OPEN) return false;",
  "normal WS traffic is not blocked before authentication"
);
if (/new\s+WebSocket\s*\(\s*['"]ws:\/\/127\.0\.0\.1:5111/i.test(boot)) {
  throw new Error("HUD WS auth check failed: boot screen opens an unauthenticated parallel socket");
}
requireText(boot, "snapshot.status !== 'online'", "boot readiness does not wait for authenticated bridge state");

const openStart = bridge.indexOf("ws.addEventListener('open'");
const messageStart = bridge.indexOf("ws.addEventListener('message'", openStart);
const helloStart = bridge.indexOf("if (msg.type === 'hello')", messageStart);
if (openStart < 0 || messageStart < 0 || helloStart < 0) {
  throw new Error("HUD WS auth check failed: connection lifecycle markers missing");
}
const preMessage = bridge.slice(openStart, messageStart);
if (!preMessage.includes("_authenticateHudSocket(ws)")) {
  throw new Error("HUD WS auth check failed: auth is not initiated on socket open");
}
if (preMessage.includes("setStatus('online')")) {
  throw new Error("HUD WS auth check failed: socket is marked online before server hello");
}
const helloWindow = bridge.slice(helloStart, helloStart + 900);
if (!helloWindow.includes("msg.authenticated !== true") ||
    !helloWindow.includes("_wsAuthenticated = true") ||
    !helloWindow.includes("setStatus('online')")) {
  throw new Error("HUD WS auth check failed: post-auth hello does not unlock the bridge");
}

console.log("HUD WebSocket auth static checks passed.");
