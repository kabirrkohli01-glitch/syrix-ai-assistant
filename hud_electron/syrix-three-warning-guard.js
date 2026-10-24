"use strict";

(() => {
  const originalWarn = console.warn;
  console.warn = function (...args) {
    const first = args[0];
    if (
      typeof first === "string"
      && (
        first.includes("build/three")
        || first.includes("Multiple instances of Three")
      )
    ) {
      return;
    }
    originalWarn.apply(console, args);
  };
  window.__restoreConsoleWarn = () => {
    console.warn = originalWarn;
  };
})();
