/**
 * Static checks for the Deck (syrix-deck.js + deck.json). Launches nothing:
 * the shell is a stub and no "run" entry is ever started.
 *
 *   node _check_deck.js
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const deck = require("./syrix-deck");

(async () => {
  const doc = JSON.parse(fs.readFileSync(path.join(__dirname, "deck.json"), "utf8"));
  const ids = doc.apps.map((a) => a.id);
  assert.strictEqual(new Set(ids).size, ids.length, "deck ids must be unique");

  for (const a of doc.apps) {
    assert.ok(a.name && a.icon && a.launch, a.id + ": name, icon and launch are required");
    assert.ok(fs.existsSync(path.join(__dirname, a.icon)), a.id + ": icon file missing (" + a.icon + ")");
    if (a.launch.url) assert.ok(/^https:\/\//.test(a.launch.url), a.id + ": web apps open over https only");
    for (const u of [a.launch.probe, a.launch.reuse && a.launch.reuse.probe].filter(Boolean)) {
      assert.ok(/^http:\/\/(127\.0\.0\.1|localhost)[:/]/.test(u), a.id + ": probes stay on this PC");
    }
  }

  const rows = await deck.list();
  assert.deepStrictEqual(rows.map((r) => r.id), ids, "list() returns every app in order");
  for (const r of rows) assert.strictEqual(typeof r.available, "boolean");

  const opened = [];
  const stubShell = { openExternal: async (u) => { opened.push(u); } };

  // Only ids in deck.json can be launched; a path or command is not an id.
  for (const bad of ["", "C:\\Windows\\System32\\cmd.exe", "../deck", "calc"]) {
    const res = await deck.launch(bad, stubShell);
    assert.deepStrictEqual(res, { ok: false, reason: "unknown" }, "refused: " + bad);
  }

  const web = doc.apps.find((a) => a.launch.url);
  if (web) {
    const res = await deck.launch(web.id, stubShell);
    assert.ok(res.ok, "web app opens");
    assert.deepStrictEqual(opened, [web.launch.url]);
  }

  assert.strictEqual(deck._expand("${USERPROFILE}\\x").endsWith("\\x"), true);
  assert.ok(deck._expand("${SYRIX_ELECTRON}").endsWith(path.join("electron", "dist", "electron.exe")));

  console.log("Deck checks OK (" + rows.length + " apps; " + rows.filter((r) => r.available).length + " available here)");
})().catch((err) => { console.error(err && err.message ? err.message : err); process.exit(1); });
