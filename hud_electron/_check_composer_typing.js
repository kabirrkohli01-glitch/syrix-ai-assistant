"use strict";
/*
 * Types into the real built HUD (dist/) in headless Chromium and checks the
 * composer keeps what was typed.
 *
 * A capture-phase listener that set React state on every keystroke made the
 * composer drop all typing: React re-rendered mid-event and reset the
 * controlled input to its previous value before its own change handler ran.
 * No static check sees that; typing into the page does.
 *
 * Run after `node build.js`:  node _check_composer_typing.js
 */
const path = require("path");
const { pathToFileURL } = require("url");
const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch();
  let failed = 0;
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
    await page.goto(pathToFileURL(path.join(__dirname, "index.html")).href);
    await page.waitForTimeout(5000);
    const box = page.locator('textarea[placeholder="Type to chat..."]').first();
    await box.focus();
    await page.keyboard.type("hello syrix", { delay: 15 });
    await page.waitForTimeout(250);
    const value = await box.inputValue();
    if (value !== "hello syrix") {
      failed += 1;
      console.log(`  FAIL: typed "hello syrix", the composer holds ${JSON.stringify(value)}`);
    }
  } finally {
    await browser.close();
  }
  console.log(failed ? `Composer typing check FAILED (${failed}).` : "Composer typing check OK.");
  process.exit(failed ? 1 : 0);
})();
