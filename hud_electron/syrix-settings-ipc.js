/**
 * Settings → SYRIX: the two text files that shape SYRIX, read and opened from
 * the main process.
 *
 *   SYRIX.md                    the house rules at the top of every prompt
 *   knowledge/owner_profile.md  what SYRIX knows about its owner
 *
 * The renderer names a file by key; it can never pass a path. Files open in
 * the system's default editor, and edits take effect on SYRIX's next turn.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const FILES = {
  "house-rules": path.join(ROOT, "SYRIX.md"),
  "owner-profile": path.join(ROOT, "knowledge", "owner_profile.md"),
};
const PERSONA = path.join(ROOT, "syrix_v3", "persona.py");

// The limit is read from persona.py so the count shown here and the cut the
// backend applies can never disagree.
function promptLimit() {
  try {
    const m = /^PROMPT_LIMIT\s*=\s*(\d+)/m.exec(fs.readFileSync(PERSONA, "utf8"));
    return m ? Number(m[1]) : null;
  } catch (_) {
    return null;
  }
}

// Mirrors persona._clean_house_rules: comments are never sent, blank runs fold.
function cleaned(text) {
  return String(text || "")
    .replace(/\r\n/g, "\n")
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function info() {
  const out = { limit: promptLimit() };
  for (const [key, file] of Object.entries(FILES)) {
    try {
      const st = fs.statSync(file);
      const entry = { exists: true, modified: st.mtimeMs, name: path.basename(file) };
      if (key === "house-rules") entry.chars = cleaned(fs.readFileSync(file, "utf8")).length;
      out[key] = entry;
    } catch (_) {
      out[key] = { exists: false, name: path.basename(file) };
    }
  }
  return out;
}

function register(ipcMain, shell, trusted) {
  ipcMain.handle("hud:syrix-files", () => info());
  ipcMain.handle("hud:syrix-file-open", async (event, payload) => {
    if (typeof trusted === "function" && !trusted(event)) return { ok: false, reason: "forbidden" };
    const file = FILES[String((payload && payload.key) || "")];
    if (!file) return { ok: false, reason: "unknown" };
    if (!fs.existsSync(file)) return { ok: false, reason: "missing" };
    if (payload && payload.reveal) { shell.showItemInFolder(file); return { ok: true }; }
    const err = await shell.openPath(file);
    return err ? { ok: false, reason: err } : { ok: true };
  });
}

module.exports = { register, info, _cleaned: cleaned, _promptLimit: promptLimit };
