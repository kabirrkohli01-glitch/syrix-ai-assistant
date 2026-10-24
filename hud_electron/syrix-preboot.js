/* Theme and palette on the very first frame.
 *
 * React applies these classes on mount, but only after every bundle has
 * loaded; until then the palette tokens fell back to the stylesheet's navy
 * default and the start-up screen flashed blue before settling. Loaded
 * synchronously at the top of <body> (a file, because the page's CSP allows
 * no inline script). */
(function () {
  var dark = false;
  try { dark = localStorage.getItem('syrix_palette') === 'dark'; } catch (_) {}
  document.body.classList.add('theme-aurora-glass');
  if (dark) {
    document.body.classList.add('pal-dark');
    document.documentElement.classList.add('pal-dark');
  }
})();
