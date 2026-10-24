/* SYRIX ADAPTIVE CARDS — inline response components for the aurora shell.
 *
 * Renders the `panel` object the backend attaches to a chat line when the
 * semantic presentation decider (syrix_core_v2/adaptive_components.py) judged
 * the user's intent earns a compact data card under the answer: a stock
 * quote+spark chart, a live score, a weather snapshot, or product results for
 * a real shopping request.
 *
 * Rules (mirror the backend's):
 *   · The card is PART of the answer, never a replacement for it — it renders
 *     under the normal text bubble, compact, in the aurora champagne design
 *     language (tokens from aurora-glass.css with hard fallbacks).
 *   · Unknown/empty types render nothing. Type "answer" deliberately renders
 *     nothing here: Presentation V2 keeps citation evidence in the inspect
 *     panel, not the chat body.
 *   · Links open through the bridge (open_url) like every other aurora link.
 *
 * Exposes window.SyrixAdaptiveCard. Self-injects its own CSS (the old
 * SmartPanel styles live inside SyrixChat's mount and never load in aurora).
 */

const adaptiveCardsCSS = `
/* answer media — images above the answer; products, sources, next questions below */
.sxm-imgs { display: grid; gap: 6px; margin: 0 0 12px; max-width: 640px; }
.sxm-imgs.n1 { grid-template-columns: 1fr; }
.sxm-imgs.n1 .sxm-tile { aspect-ratio: 16 / 9; }
.sxm-imgs.n2 { grid-template-columns: 1fr 1fr; }
.sxm-imgs.n2 .sxm-tile { aspect-ratio: 4 / 3; }
.sxm-imgs.n3, .sxm-imgs.n4 { grid-template-columns: 2fr 1fr 1fr; grid-template-rows: 1fr 1fr; height: 216px; }
.sxm-imgs.n3 .sxm-tile:first-child, .sxm-imgs.n4 .sxm-tile:first-child { grid-row: 1 / span 2; }
.sxm-imgs.n3 .sxm-tile:nth-child(3), .sxm-imgs.n4 .sxm-tile:nth-child(4) { grid-column: 2 / span 2; }
.sxm-imgs.strip { display: flex; overflow-x: auto; padding-bottom: 4px; scroll-snap-type: x mandatory; }
.sxm-imgs.strip .sxm-tile { flex: 0 0 auto; width: 172px; height: 124px; scroll-snap-align: start; }
.sxm-tile { position: relative; min-height: 0; padding: 0; margin: 0; border: 1px solid var(--eg-stroke, rgba(255,245,230,0.10)); border-radius: 12px; overflow: hidden; background: rgba(255,245,230,0.04); cursor: zoom-in; }
.sxm-tile { background: linear-gradient(100deg, rgba(255,245,230,0.035) 30%, rgba(255,245,230,0.10) 50%, rgba(255,245,230,0.035) 70%); background-size: 220% 100%; animation: sxmShimmer 1.3s linear infinite; }
.sxm-tile.is-loaded { animation: none; background: rgba(255,245,230,0.04); }
.sxm-tile img { width: 100%; height: 100%; object-fit: cover; display: block; opacity: 0; transform: scale(1.035); filter: blur(6px);
  transition: opacity .55s ease, transform .75s cubic-bezier(0.2,0.8,0.2,1), filter .55s ease; }
.sxm-tile img.is-in { opacity: 1; transform: none; filter: none; }
.sxm-tile:hover img.is-in { transform: scale(1.045); filter: brightness(1.07); transition-delay: 0s !important; }
@keyframes sxmShimmer { from { background-position: 120% 0; } to { background-position: -120% 0; } }
.sxm-box img { animation: sxmZoomIn .28s cubic-bezier(0.2,0.8,0.2,1); }
@keyframes sxmZoomIn { from { opacity: 0; transform: scale(.96); } to { opacity: 1; transform: none; } }
.sxm-box { position: fixed; inset: 0; z-index: 99999; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; background: rgba(6,5,4,0.88); backdrop-filter: blur(8px); animation: sxcIn .2s ease; }
.sxm-box img { max-width: 86vw; max-height: 78vh; border-radius: 14px; object-fit: contain; box-shadow: 0 30px 90px rgba(0,0,0,0.65); }
.sxm-bar { display: flex; align-items: center; gap: 10px; max-width: 86vw; padding: 7px 8px 7px 14px; border-radius: 999px; background: rgba(30,26,20,0.94); border: 1px solid var(--eg-stroke, rgba(255,245,230,0.13)); font: 500 12px 'Inter', sans-serif; color: var(--eg-text-1, rgba(255,252,245,0.94)); }
.sxm-bar span { max-width: 46vw; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sxm-bar .sxc-link { white-space: nowrap; }
.sxm-bar button { width: 26px; height: 26px; border-radius: 999px; cursor: pointer; font: 600 13px 'Inter', sans-serif; color: var(--eg-text-1, #f5efe6); background: rgba(255,245,230,0.07); border: 1px solid rgba(255,245,230,0.12); }
.sxm-bar button:hover { background: rgba(255,245,230,0.16); }
.sxm-srcs { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 12px; }
.sxm-srcs-k { margin-right: 2px; font: 600 8.5px 'JetBrains Mono', monospace; letter-spacing: 0.2em; text-transform: uppercase; color: var(--eg-text-3, rgba(226,215,198,0.5)); }
.sxm-src { display: inline-flex; align-items: center; gap: 6px; max-width: 230px; padding: 4px 9px 4px 6px; border-radius: 999px; cursor: pointer; font: 500 11px 'Inter', sans-serif; color: var(--eg-text-2, rgba(244,237,226,0.82)); background: rgba(255,245,230,0.045); border: 1px solid var(--eg-stroke, rgba(255,245,230,0.10)); transition: background .15s, border-color .15s, transform .15s; }
.sxm-src:hover { background: var(--eg-gold-soft, rgba(var(--eg-accent-surface-rgb),0.10)); border-color: rgba(var(--eg-accent-rgb),0.40); transform: translateY(-1px); }
.sxm-src img { width: 14px; height: 14px; flex: 0 0 14px; border-radius: 3px; background: rgba(255,255,255,0.92); }
.sxm-src-host { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sxm-src-n { padding: 1px 5px; border-radius: 999px; font: 600 9px 'JetBrains Mono', monospace; color: var(--eg-text-3, rgba(226,215,198,0.6)); background: rgba(255,245,230,0.07); }
.sxm-prods { display: flex; gap: 10px; margin-top: 12px; padding-bottom: 6px; overflow-x: auto; scroll-snap-type: x mandatory; max-width: 720px; }
.sxm-prod { flex: 0 0 178px; display: flex; flex-direction: column; overflow: hidden; border-radius: 14px; cursor: pointer; scroll-snap-align: start; background: linear-gradient(160deg, rgba(30,26,20,0.92), rgba(18,15,11,0.94)); border: 1px solid var(--eg-stroke, rgba(255,245,230,0.13)); transition: transform .15s, border-color .15s; }
.sxm-prod:hover { transform: translateY(-2px); border-color: rgba(var(--eg-accent-rgb),0.45); }
.sxm-prod-img { height: 120px; display: flex; align-items: center; justify-content: center; background: #fff; }
.sxm-prod-img img { max-width: 100%; max-height: 100%; object-fit: contain; }
.sxm-prod-img span { font: 700 30px 'Inter', sans-serif; color: rgba(40,32,20,0.3); }
.sxm-prod-name { min-height: 32px; padding: 9px 10px 4px; font: 600 12px/1.3 'Inter', sans-serif; color: var(--eg-text-1, rgba(255,252,245,0.96)); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.sxm-prod-meta { display: flex; align-items: center; justify-content: space-between; gap: 6px; padding: 0 10px 10px; }
.sxm-prod-price { font: 700 13px 'Inter', sans-serif; color: #6fd99a; }
.sxm-prod-shop { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font: 500 10px 'Inter', sans-serif; color: var(--eg-text-3, rgba(226,215,198,0.6)); }
.sxm-note { margin-top: 3px; font: 400 9.5px 'Inter', sans-serif; color: var(--eg-text-3, rgba(226,215,198,0.5)); }
.sxm-next { display: flex; flex-direction: column; gap: 2px; margin-top: 14px; max-width: 640px; border-top: 1px solid rgba(255,245,230,0.07); padding-top: 8px; }
.sxm-next-k { margin-bottom: 4px; font: 600 8.5px 'JetBrains Mono', monospace; letter-spacing: 0.2em; text-transform: uppercase; color: var(--eg-text-3, rgba(226,215,198,0.5)); }
.sxm-next-q { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 7px 4px; border: 0; border-bottom: 1px solid rgba(255,245,230,0.05); background: transparent; cursor: pointer; text-align: left; font: 500 12.5px 'Inter', sans-serif; color: var(--eg-text-2, rgba(244,237,226,0.86)); transition: color .15s, padding .15s; }
.sxm-next-q:hover { color: var(--eg-gold-bright, rgb(var(--eg-accent-bright-rgb))); padding-left: 8px; }
.sxm-next-q .plus { opacity: 0.55; font-size: 14px; }
.sxc-wx-days { display: grid; grid-template-columns: repeat(auto-fill, minmax(56px, 1fr)); gap: 6px; margin-top: 10px; }
.sxc-wx-day { display: flex; flex-direction: column; align-items: center; gap: 2px; padding: 6px 4px; border-radius: 10px; background: rgba(255,245,230,0.04); font: 500 10.5px 'Inter', sans-serif; color: var(--eg-text-2, rgba(244,237,226,0.85)); }
.sxc-wx-day .ico { font-size: 16px; }
.sxc-wx-day .lo { color: var(--eg-text-3, rgba(226,215,198,0.55)); }
.sxc-clock-time { font: 700 34px/1 'Inter', sans-serif; letter-spacing: -0.02em; color: var(--eg-text-1, rgba(255,252,245,0.96)); }
.sxc-clock-date { margin-top: 5px; font: 500 11.5px 'Inter', sans-serif; color: var(--eg-text-2, rgba(244,237,226,0.8)); }
/* Card surfaces follow the active palette: warm glass in aurora, the iOS
   greys on a black ground in the dark palette. */
body.theme-aurora-glass { --sxc-card-bg: linear-gradient(160deg, rgba(30,26,20,0.92), rgba(18,15,11,0.94)); --sxc-ring: #15120E; }
body.theme-aurora-glass.pal-dark { --sxc-card-bg: linear-gradient(160deg, #1c1c1e, #141416); --sxc-ring: #1c1c1e; }
.sxc-card { margin-top: 9px; max-width: 520px; border-radius: 14px; overflow: hidden;
  background: var(--sxc-card-bg, linear-gradient(160deg, rgba(30,26,20,0.92), rgba(18,15,11,0.94)));
  border: 1px solid var(--eg-stroke, rgba(255,245,230,0.13));
  box-shadow: var(--eg-shadow, 0 18px 50px -22px rgba(0,0,0,0.75)), inset 0 1px 0 rgba(255,250,240,0.10);
  backdrop-filter: blur(16px) saturate(1.25);
  animation: sxcIn 0.42s cubic-bezier(0.23,1,0.32,1); }
@keyframes sxcIn { from { opacity: 0; transform: translateY(7px) scale(0.98); } to { opacity: 1; transform: none; } }
.sxc-head { display: flex; align-items: center; gap: 7px; padding: 8px 13px;
  border-bottom: 1px solid var(--eg-stroke, rgba(255,245,230,0.13));
  background: var(--eg-gold-soft, rgba(var(--eg-accent-surface-rgb),0.08));
  color: var(--eg-gold, rgb(var(--eg-accent-rgb)));
  font: 600 8.5px 'JetBrains Mono', monospace; letter-spacing: 0.2em; text-transform: uppercase; }
.sxc-head .sxc-tag { margin-left: auto; opacity: 0.6; font-size: 7.5px; letter-spacing: 0.14em; }
.sxc-body { padding: 11px 14px; }
.sxc-attr { margin-top: 8px; padding-top: 7px; border-top: 1px solid rgba(255,245,230,0.07);
  font: 600 7.5px 'JetBrains Mono', monospace; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--eg-text-3, rgba(var(--eg-accent-bright-rgb),0.5)); }
.sxc-attr a, .sxc-link { color: var(--eg-gold, rgb(var(--eg-accent-rgb))); cursor: pointer; text-decoration: none; }
.sxc-attr a:hover, .sxc-link:hover { color: var(--eg-gold-bright, rgb(var(--eg-accent-bright-rgb))); }

/* stock — rich Perplexity-style chart card */
.sxc-sx-iden { display: flex; align-items: baseline; gap: 8px; padding: 12px 14px 0; }
.sxc-sx-sym { font: 700 15px 'JetBrains Mono', monospace; letter-spacing: 0.05em; color: var(--eg-text-1, rgba(255,252,245,0.96)); }
.sxc-sx-exch { font: 600 8.5px 'JetBrains Mono', monospace; letter-spacing: 0.16em; text-transform: uppercase; color: var(--eg-text-3, rgba(var(--eg-accent-bright-rgb),0.5)); }
.sxc-sx-price-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; padding: 4px 14px 0; }
.sxc-sx-price { font: 700 24px 'Inter', sans-serif; color: var(--eg-text-1, rgba(255,252,245,0.96)); letter-spacing: -0.01em; }
.sxc-sx-delta { font: 600 12.5px 'JetBrains Mono', monospace; }
.sxc-sx-delta.up { color: #6fd99a; }
.sxc-sx-delta.down { color: #ee8484; }
.sxc-sx-asof { padding: 1px 14px 6px; font: 500 9.5px 'Inter', sans-serif; color: var(--eg-text-3, rgba(var(--eg-accent-bright-rgb),0.5)); }
.sxc-sx-chart { display: block; width: 100%; height: auto; }
.sxc-sx-stats { display: grid; grid-template-columns: repeat(3, 1fr); border-top: 1px solid rgba(255,245,230,0.07); }
.sxc-sx-stat { padding: 8px 12px; border-top: 1px solid rgba(255,245,230,0.05); }
.sxc-sx-stat:nth-child(-n+3) { border-top: none; }
.sxc-sx-stat + .sxc-sx-stat { border-left: 1px solid rgba(255,245,230,0.05); }
.sxc-sx-stat:nth-child(3n+1) { border-left: none; }
.sxc-sx-stat-k { font: 600 8px 'JetBrains Mono', monospace; letter-spacing: 0.14em; text-transform: uppercase; color: var(--eg-text-3, rgba(var(--eg-accent-bright-rgb),0.5)); }
.sxc-sx-stat-v { margin-top: 3px; font: 600 11.5px 'Inter', sans-serif; color: var(--eg-text-1, rgba(255,252,245,0.96)); white-space: nowrap; }
.sxc-sx-attr { padding: 7px 14px 9px; border-top: 1px solid rgba(255,245,230,0.07);
  font: 600 7.5px 'JetBrains Mono', monospace; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--eg-text-3, rgba(var(--eg-accent-bright-rgb),0.5)); }

/* stock — simple quote fallback */
.sxc-stock-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.sxc-stock-sym { font: 700 15px 'JetBrains Mono', monospace; letter-spacing: 0.06em; color: var(--eg-text-1, rgba(255,252,245,0.96)); }
.sxc-stock-price { font: 700 22px 'Inter', sans-serif; color: var(--eg-text-1, rgba(255,252,245,0.96)); }
.sxc-stock-chg { font: 600 12.5px 'JetBrains Mono', monospace; padding: 2px 8px; border-radius: 7px; }
.sxc-stock-chg.up { color: #6fd99a; background: rgba(111,217,154,0.12); }
.sxc-stock-chg.down { color: #ee8484; background: rgba(238,132,132,0.12); }
.sxc-spark { margin-top: 9px; display: block; width: 100%; height: 36px; }

/* sports */
.sxc-game { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 10px; padding: 7px 2px; }
.sxc-game + .sxc-game { border-top: 1px solid rgba(255,245,230,0.07); }
.sxc-team { display: flex; align-items: center; gap: 8px; min-width: 0; }
.sxc-team.away { flex-direction: row-reverse; text-align: right; }
.sxc-team img { width: 21px; height: 21px; object-fit: contain; flex-shrink: 0; }
.sxc-team-name { font: 600 12px 'Inter', sans-serif; color: var(--eg-text-1, rgba(255,252,245,0.96));
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sxc-team-name.win { color: var(--eg-gold-bright, rgb(var(--eg-accent-bright-rgb))); }
.sxc-score { font: 700 19px 'JetBrains Mono', monospace; letter-spacing: 0.04em; text-align: center;
  color: var(--eg-text-1, rgba(255,252,245,0.96)); white-space: nowrap; }
.sxc-status { display: block; margin-top: 2px; font: 600 8px 'JetBrains Mono', monospace;
  letter-spacing: 0.14em; text-transform: uppercase; text-align: center; }
.sxc-status.live { color: #6fd99a; }
.sxc-status.live::before { content: ''; display: inline-block; width: 6px; height: 6px; border-radius: 50%;
  background: #6fd99a; margin-right: 5px; box-shadow: 0 0 7px #6fd99a;
  animation: sxcPulse 1.3s ease-in-out infinite; vertical-align: middle; }
.sxc-status.pre { color: var(--eg-text-3, rgba(var(--eg-accent-bright-rgb),0.5)); }
.sxc-status.post { color: var(--eg-text-3, rgba(var(--eg-accent-bright-rgb),0.5)); }
@keyframes sxcPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }

/* weather */
.sxc-wx-row { display: flex; align-items: center; gap: 14px; }
.sxc-wx-temp { font: 700 30px 'Inter', sans-serif; color: var(--eg-text-1, rgba(255,252,245,0.96)); line-height: 1; }
.sxc-wx-cond { font: 600 10.5px 'JetBrains Mono', monospace; letter-spacing: 0.08em;
  color: var(--eg-gold, rgb(var(--eg-accent-rgb))); text-transform: uppercase; }
.sxc-wx-meta { font: 500 11px/1.5 'Inter', sans-serif; color: var(--eg-text-2, rgba(244,237,226,0.74)); }

/* products */
.sxc-prod { display: flex; align-items: baseline; gap: 9px; padding: 7px 2px; cursor: pointer; }
.sxc-prod + .sxc-prod { border-top: 1px solid rgba(255,245,230,0.07); }
.sxc-prod-title { font: 600 12px/1.45 'Inter', sans-serif; color: var(--eg-text-1, rgba(255,252,245,0.96)); flex: 1; min-width: 0; }
.sxc-prod:hover .sxc-prod-title { color: var(--eg-gold-bright, rgb(var(--eg-accent-bright-rgb))); }
.sxc-prod-price { font: 700 11.5px 'JetBrains Mono', monospace; color: var(--eg-gold, rgb(var(--eg-accent-rgb)));
  padding: 1px 7px; border-radius: 6px; background: var(--eg-gold-dim, rgba(var(--eg-accent-surface-rgb),0.14)); white-space: nowrap; }
.sxc-prod-dom { font: 600 8px 'JetBrains Mono', monospace; letter-spacing: 0.1em;
  color: var(--eg-text-3, rgba(var(--eg-accent-bright-rgb),0.5)); white-space: nowrap; }
@media (prefers-reduced-motion: reduce) { .sxc-card { animation: none; } .sxc-status.live::before { animation: none; } }

/* stock widget — compact quote with range tabs */
.sxw-card { margin: 0 0 12px; max-width: 420px; border-radius: 14px; overflow: hidden; padding: 12px 14px 10px;
  background: var(--sxc-card-bg, linear-gradient(160deg, rgba(30,26,20,0.92), rgba(18,15,11,0.94)));
  border: 1px solid var(--eg-stroke, rgba(255,245,230,0.13));
  box-shadow: 0 14px 40px -24px rgba(0,0,0,0.8), var(--eg-inner-hi, inset 0 1px 0 rgba(255,250,240,0.08));
  animation: sxcIn 0.36s cubic-bezier(0.23,1,0.32,1); }
.sxw-card.is-settled { animation: none; }
.sxw-id { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.sxw-name { font: 600 13px 'Inter', sans-serif; color: var(--eg-text-1, rgba(255,252,245,0.96)); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sxw-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font: 600 9px 'JetBrains Mono', monospace; letter-spacing: 0.08em; text-transform: uppercase; color: var(--eg-text-3, rgba(226,215,198,0.55)); }
.sxw-state { display: inline-flex; align-items: center; gap: 5px; letter-spacing: 0.04em; text-transform: none; font: 500 10px 'Inter', sans-serif; }
.sxw-state::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: var(--eg-text-3, rgba(226,215,198,0.45)); }
.sxw-state.open { color: #6fd99a; }
.sxw-state.open::before { background: #6fd99a; box-shadow: 0 0 6px #6fd99a; }
.sxw-age { text-transform: none; letter-spacing: 0; font: 500 10px 'Inter', sans-serif; }
.sxw-price-row { display: flex; align-items: baseline; gap: 9px; flex-wrap: wrap; margin-top: 8px; }
.sxw-price { font: 700 24px/1 'Inter', sans-serif; letter-spacing: -0.015em; color: var(--eg-text-1, rgba(255,252,245,0.96)); font-variant-numeric: tabular-nums; }
.sxw-delta { font: 600 12px 'Inter', sans-serif; font-variant-numeric: tabular-nums; }
.sxw-delta.up { color: #6fd99a; }
.sxw-delta.down { color: #ee8484; }
.sxw-when { font: 500 11px 'Inter', sans-serif; color: var(--eg-text-3, rgba(226,215,198,0.55)); }
.sxw-tabs { display: flex; gap: 4px; margin: 10px 0 4px; }
.sxw-tab { border: 0; cursor: pointer; padding: 3px 9px; border-radius: 7px; background: transparent; font: 600 10.5px 'Inter', sans-serif; color: var(--eg-text-3, rgba(226,215,198,0.6)); transition: background .15s, color .15s; }
.sxw-tab:hover { color: var(--eg-text-1, rgba(255,252,245,0.96)); background: var(--eg-hover, rgba(255,245,230,0.05)); }
.sxw-tab.is-on { color: var(--eg-text-1, rgba(255,252,245,0.96)); background: var(--eg-active, rgba(255,245,230,0.09)); }
.sxw-chart { display: block; width: 100%; height: auto; cursor: crosshair; }
.sxw-ticks { display: flex; justify-content: space-between; margin-top: 2px; font: 500 9.5px 'Inter', sans-serif; color: var(--eg-text-3, rgba(226,215,198,0.5)); font-variant-numeric: tabular-nums; }
.sxw-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px 12px; margin-top: 11px; padding-top: 10px; border-top: 1px solid var(--eg-stroke, rgba(255,245,230,0.07)); }
.sxw-stat { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.sxw-stat .k { font: 500 9.5px 'Inter', sans-serif; color: var(--eg-text-3, rgba(226,215,198,0.55)); }
.sxw-stat .v { font: 600 11.5px 'Inter', sans-serif; color: var(--eg-text-1, rgba(255,252,245,0.94)); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
.sxw-52 { display: grid; grid-template-columns: auto auto 1fr auto; align-items: center; gap: 8px; margin-top: 10px; font: 600 10.5px 'Inter', sans-serif; color: var(--eg-text-2, rgba(244,237,226,0.8)); font-variant-numeric: tabular-nums; }
.sxw-52 .k { font-weight: 500; font-size: 9.5px; color: var(--eg-text-3, rgba(226,215,198,0.55)); }
.sxw-52 .bar { position: relative; height: 4px; border-radius: 3px; background: var(--eg-glass-2, rgba(255,245,230,0.08)); }
.sxw-52 .fill { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; background: linear-gradient(90deg, rgba(238,132,132,0.55), rgba(111,217,154,0.65)); }
.sxw-52 .dot { position: absolute; top: 50%; width: 9px; height: 9px; margin: -4.5px 0 0 -4.5px; border-radius: 50%; background: var(--eg-text-1, #F3EADB); box-shadow: 0 0 0 2px var(--sxc-ring, #15120E); }
.sxw-hover-dot { stroke: var(--sxc-ring, #15120E); }
.sxw-cross { stroke: var(--eg-stroke-2, rgba(255,245,230,0.35)); }
.sxw-base { stroke: var(--eg-text-3, rgba(255,245,230,0.28)); }
.sxw-foot { display: flex; justify-content: space-between; gap: 10px; margin-top: 10px; font: 500 9.5px 'Inter', sans-serif; color: var(--eg-text-3, rgba(226,215,198,0.5)); }
@media (prefers-reduced-motion: reduce) { .sxw-card { animation: none; } }
`;

(function _sxcInjectCSS() {
  try {
    if (document.getElementById('syrix-adaptive-cards-css')) return;
    const st = document.createElement('style');
    st.id = 'syrix-adaptive-cards-css';
    st.textContent = adaptiveCardsCSS;
    document.head.appendChild(st);
  } catch (_) {}
})();

function _sxcOpen(url) {
  if (!url) return;
  try { window.SyrixBridge?.send?.('open_url', { data: { url: String(url) } }); } catch (_) {}
}

function _sxcHost(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch (_) { return ''; }
}

/* One attribution line (never citation pills — evidence lives in inspect). */
function _SxcAttribution({ sources }) {
  const s = Array.isArray(sources) && sources.length ? sources[0] : null;
  if (!s) return null;
  const url = (s.url || s.href || '');
  const label = s.title || s.name || _sxcHost(url) || 'source';
  return (
    <div className="sxc-attr">
      via {url
        ? <span className="sxc-link" onClick={ev => { ev.stopPropagation(); _sxcOpen(url); }}>{label}</span>
        : label}
    </div>
  );
}

function _SxcSpark({ points, up }) {
  const vals = (Array.isArray(points) ? points : []).filter(v => typeof v === 'number');
  if (vals.length < 2) return null;
  const w = 240, h = 36, min = Math.min(...vals), max = Math.max(...vals);
  const span = (max - min) || 1;
  const step = w / (vals.length - 1);
  const pts = vals.map((v, i) =>
    `${(i * step).toFixed(1)},${(h - ((v - min) / span) * (h - 6) - 3).toFixed(1)}`);
  const d = pts.map((p, i) => (i === 0 ? 'M' : 'L') + p).join(' ');
  const col = up ? '#6fd99a' : '#ee8484';
  const gid = 'sxcg' + (up ? 'u' : 'd');
  return (
    <svg className="sxc-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={col} stopOpacity="0.28" />
          <stop offset="100%" stopColor={col} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${d} L${w},${h} L0,${h} Z`} fill={`url(#${gid})`} stroke="none" />
      <path d={d} fill="none" stroke={col} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/* Perplexity-style intraday chart: the price line renders green above the
   previous close and red below it (two clip regions over one path), with a
   soft gradient fill, a dashed prev-close line + label, y/x axis ticks and
   volume bars. Pure SVG — no chart library. */
function _SxcStockChart({ panel }) {
  const vals = (Array.isArray(panel.series) ? panel.series : []).filter(v => typeof v === 'number');
  if (vals.length < 2) return null;
  const prev = typeof panel.prev_close === 'number' ? panel.prev_close : vals[0];
  const vols = (Array.isArray(panel.volumes) ? panel.volumes : []).map(v => (typeof v === 'number' && v > 0 ? v : 0));
  const hasVol = vols.length === vals.length && vols.some(v => v > 0);
  const cur = panel.currency || '';

  const W = 520, H = hasVol ? 208 : 176;
  const x0 = 46, x1 = 512, yTop = 12, yBot = hasVol ? 138 : 150;
  const volTop = yBot + 8, volBot = volTop + 30;
  const lblY = (hasVol ? volBot : yBot) + 16;

  let lo = Math.min(...vals, prev), hi = Math.max(...vals, prev);
  const pad = (hi - lo) * 0.06 || Math.abs(hi) * 0.005 || 1;
  lo -= pad; hi += pad;
  const X = i => x0 + (i / (vals.length - 1)) * (x1 - x0);
  const Y = v => yBot - ((v - lo) / (hi - lo)) * (yBot - yTop);
  const yPrev = Y(prev);

  const line = vals.map((v, i) => `${i === 0 ? 'M' : 'L'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
  const area = `${line} L${x1},${yBot} L${x0},${yBot} Z`;

  // 3 y-axis ticks (low / mid / high), compact labels.
  const fmtY = v => (hi - lo) < 4 ? v.toFixed(2) : v >= 10000 ? Math.round(v).toLocaleString() : v.toFixed(hi < 100 ? 1 : 0);
  const yTicks = [lo + (hi - lo) * 0.12, (lo + hi) / 2, lo + (hi - lo) * 0.88];

  // Prev-close label pill: right side, flipped under the line when it would
  // clip the top edge.
  const pillW = 118, pillH = 15;
  const pillAbove = yPrev > yTop + pillH + 4;
  const pillY = pillAbove ? yPrev - pillH - 3 : yPrev + 3;
  const prevLabel = `Prev close: ${cur}${fmtY(prev)}`;

  const uid = 'sx' + String(panel.symbol || '').replace(/[^A-Za-z0-9]/g, '') || 'sx0';
  const ticks = Array.isArray(panel.ticks) ? panel.ticks : [];

  return (
    <svg className="sxc-sx-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
      <defs>
        <clipPath id={uid + '-up'}><rect x={x0 - 2} y={0} width={x1 - x0 + 4} height={Math.max(0, yPrev)} /></clipPath>
        <clipPath id={uid + '-dn'}><rect x={x0 - 2} y={yPrev} width={x1 - x0 + 4} height={Math.max(0, yBot - yPrev + 2)} /></clipPath>
        <linearGradient id={uid + '-gu'} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#6fd99a" stopOpacity="0.30" />
          <stop offset="100%" stopColor="#6fd99a" stopOpacity="0.02" />
        </linearGradient>
        <linearGradient id={uid + '-gd'} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ee8484" stopOpacity="0.04" />
          <stop offset="100%" stopColor="#ee8484" stopOpacity="0.22" />
        </linearGradient>
      </defs>
      {/* y grid + labels */}
      {yTicks.map((v, i) => (
        <g key={i}>
          <line x1={x0} y1={Y(v)} x2={x1} y2={Y(v)} stroke="rgba(255,245,230,0.05)" strokeWidth="1" />
          <text x={x0 - 6} y={Y(v) + 3} textAnchor="end" fill="rgba(226,215,198,0.5)"
                fontSize="8.5" fontFamily="JetBrains Mono, monospace">{fmtY(v)}</text>
        </g>
      ))}
      {/* gradient fills + price line, split at prev close */}
      <path d={area} fill={`url(#${uid}-gu)`} clipPath={`url(#${uid}-up)`} />
      <path d={area} fill={`url(#${uid}-gd)`} clipPath={`url(#${uid}-dn)`} />
      <path d={line} fill="none" stroke="#6fd99a" strokeWidth="1.8" strokeLinejoin="round"
            strokeLinecap="round" clipPath={`url(#${uid}-up)`} />
      <path d={line} fill="none" stroke="#ee8484" strokeWidth="1.8" strokeLinejoin="round"
            strokeLinecap="round" clipPath={`url(#${uid}-dn)`} />
      {/* prev-close dashed line + pill */}
      <line x1={x0} y1={yPrev} x2={x1} y2={yPrev} stroke="rgba(255,245,230,0.35)"
            strokeWidth="1" strokeDasharray="4 4" />
      <g>
        <rect x={x1 - pillW} y={pillY} width={pillW} height={pillH} rx="7.5"
              fill="rgba(30,26,20,0.92)" stroke="rgba(255,245,230,0.16)" strokeWidth="1" />
        <text x={x1 - pillW / 2} y={pillY + 10.5} textAnchor="middle" fill="rgba(244,237,226,0.8)"
              fontSize="8.5" fontFamily="JetBrains Mono, monospace">{prevLabel}</text>
      </g>
      {/* volume bars */}
      {hasVol && (() => {
        const vmax = Math.max(...vols) || 1;
        const bw = Math.max(0.8, (x1 - x0) / vols.length - 0.6);
        return vols.map((v, i) => {
          if (!v) return null;
          const h = Math.max(1, (v / vmax) * (volBot - volTop));
          const up = i === 0 || vals[i] >= vals[i - 1];
          return <rect key={i} x={X(i) - bw / 2} y={volBot - h} width={bw} height={h}
                       fill={up ? 'rgba(111,217,154,0.45)' : 'rgba(238,132,132,0.45)'} />;
        });
      })()}
      {/* x time labels */}
      {ticks.map((t, i) => {
        if (!Array.isArray(t) || t.length < 2) return null;
        const frac = Math.max(0, Math.min(1, Number(t[0]) || 0));
        const anchor = frac < 0.06 ? 'start' : frac > 0.94 ? 'end' : 'middle';
        return <text key={i} x={x0 + frac * (x1 - x0)} y={lblY} textAnchor={anchor}
                     fill="rgba(226,215,198,0.5)" fontSize="8.5"
                     fontFamily="JetBrains Mono, monospace">{String(t[1])}</text>;
      })}
    </svg>
  );
}

/* ─── Stock widget ────────────────────────────────────────────────────────────
   A compact quote card: price and change, range tabs (1D, 5D, 1M, 6M, 1Y)
   drawn from series the backend fetched together, a chart with a hover
   crosshair, and the numbers people check first. Switching ranges needs no
   request; every range arrives with the card. */
const _SXW_RANGES = ['1D', '5D', '1M', '6M', '1Y'];
const _SXW_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const _SXW_DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function _sxwTime(epochS, fmt) {
  // Times arrive already shifted to the exchange's local time, so they are
  // read back with the UTC getters.
  const d = new Date(Number(epochS) * 1000);
  const hm = String(d.getUTCHours()).padStart(2, '0') + ':' + String(d.getUTCMinutes()).padStart(2, '0');
  if (fmt === 'time') return hm;
  if (fmt === 'daytime') return _SXW_DAYS[d.getUTCDay()] + ' ' + hm;
  if (fmt === 'month') return _SXW_MONTHS[d.getUTCMonth()] + ' ' + d.getUTCFullYear();
  return d.getUTCDate() + ' ' + _SXW_MONTHS[d.getUTCMonth()];
}

function _sxwNum(v, cur, scaleOf) {
  if (typeof v !== 'number' || !isFinite(v)) return '—';
  // Decimals follow the price, not the figure: a 21p move on a £15 share is
  // £0.21, not £0.2140. Sub-unit prices (a coin, a penny stock) keep four.
  const ref = typeof scaleOf === 'number' && isFinite(scaleOf) ? scaleOf : v;
  const digits = Math.abs(ref) >= 1 ? 2 : 4;
  return (cur || '') + v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function _sxwCompact(v) {
  if (typeof v !== 'number' || !isFinite(v) || v <= 0) return '—';
  if (v >= 1e12) return (v / 1e12).toFixed(2) + 'T';
  if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M';
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
  return String(Math.round(v));
}

function _sxwPct(v) {
  if (typeof v !== 'number' || !isFinite(v)) return null;
  return (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(2) + '%';
}

function SxcStockWidget({ panel, ageTag }) {
  const ranges = panel.ranges || {};
  const available = _SXW_RANGES.filter(k => ranges[k] && Array.isArray(ranges[k].series) && ranges[k].series.length >= 2);
  const [range, setRange] = React.useState(available[0] || '1D');
  const [hover, setHover] = React.useState(-1);
  const cur = panel.currency || '';
  const r = ranges[range] || ranges[available[0]] || { series: [], times: [], fmt: 'time' };
  const vals = (r.series || []).filter(v => typeof v === 'number');
  const times = Array.isArray(r.times) ? r.times : [];
  const intraday = range === '1D';
  const base = intraday && typeof panel.prev_close === 'number' ? panel.prev_close : vals[0];
  const last = vals.length ? vals[vals.length - 1] : panel.price;

  // Change for the range on show. Today's figures are the exchange's own.
  const chg = intraday && typeof panel.change_abs === 'number' ? panel.change_abs
    : (typeof last === 'number' && typeof base === 'number' ? last - base : null);
  const pct = intraday && typeof panel.change_pct === 'number' ? panel.change_pct
    : (chg != null && base ? (chg / base) * 100 : null);
  const up = (chg == null ? 0 : chg) >= 0;
  const rangeWord = { '1D': 'today', '5D': 'past 5 days', '1M': 'past month', '6M': 'past 6 months', '1Y': 'past year' }[range];

  // Chart geometry.
  const W = 400, H = 106, x0 = 4, x1 = 396, yTop = 12, yBot = 102;
  let lo = Math.min(...vals, base), hi = Math.max(...vals, base);
  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.004 || 1;
  lo -= pad; hi += pad;
  const n = vals.length;
  const X = i => x0 + (n > 1 ? (i / (n - 1)) : 0) * (x1 - x0);
  const Y = v => yBot - ((v - lo) / (hi - lo)) * (yBot - yTop);
  const line = vals.map((v, i) => `${i === 0 ? 'M' : 'L'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
  const area = `${line} L${x1},${yBot} L${x0},${yBot} Z`;
  const color = up ? '#6fd99a' : '#ee8484';
  const uid = 'sxw' + String(panel.symbol || '').replace(/[^A-Za-z0-9]/g, '') + range;
  const tickIdx = n >= 4 ? [0, Math.round((n - 1) / 3), Math.round(2 * (n - 1) / 3), n - 1] : [];

  function onMove(ev) {
    if (n < 2) return;
    const rect = ev.currentTarget.getBoundingClientRect();
    const fx = ((ev.clientX - rect.left) / rect.width) * W;
    const idx = Math.round(((fx - x0) / (x1 - x0)) * (n - 1));
    setHover(Math.max(0, Math.min(n - 1, idx)));
  }

  const hv = hover >= 0 && hover < n ? vals[hover] : null;
  const shownPrice = hv != null ? hv : panel.price;
  const shownChg = hv != null && typeof base === 'number' ? hv - base : chg;
  const shownPct = hv != null && base ? ((hv - base) / base) * 100 : pct;
  const shownUp = (shownChg == null ? 0 : shownChg) >= 0;

  // 52-week position.
  const yl = panel.year_low, yh = panel.year_high;
  const yPos = (typeof yl === 'number' && typeof yh === 'number' && yh > yl && typeof panel.price === 'number')
    ? Math.max(0, Math.min(1, (panel.price - yl) / (yh - yl))) : null;

  const seen = (window.__sxcSeen = window.__sxcSeen || new Set());
  const seenKey = (panel.symbol || '') + '|' + (panel.as_of || '');
  const settled = seen.has(seenKey);
  React.useEffect(() => { seen.add(seenKey); }, [seenKey]);

  const stats = [
    ['Open', _sxwNum(panel.open, cur)],
    ['Prev close', _sxwNum(panel.prev_close, cur)],
    ['Day range', (typeof panel.day_low === 'number' && typeof panel.day_high === 'number')
      ? _sxwNum(panel.day_low, cur) + ' – ' + _sxwNum(panel.day_high, cur) : '—'],
    ['Volume', _sxwCompact(panel.volume)],
    ['1 month', _sxwPct(panel.month_change_pct) || '—'],
    ['1 year', _sxwPct(panel.year_change_pct) || '—'],
  ];

  return (
    <div className={'sxw-card' + (settled ? ' is-settled' : '')}>
      <div className="sxw-top">
        <div className="sxw-id">
          <span className="sxw-name" title={panel.name || ''}>{panel.name || panel.symbol}</span>
          <span className="sxw-meta">
            {panel.symbol}{panel.exchange ? ' · ' + panel.exchange : ''}
            <span className={'sxw-state ' + (panel.market_open ? 'open' : 'closed')}>
              {panel.market_open ? 'Market open' : 'Market closed'}
            </span>
            {ageTag ? <span className="sxw-age">{ageTag}</span> : null}
          </span>
        </div>
      </div>
      <div className="sxw-price-row">
        <span className="sxw-price">{_sxwNum(shownPrice, cur)}</span>
        {shownChg != null && (
          <span className={'sxw-delta ' + (shownUp ? 'up' : 'down')}>
            {(shownUp ? '+' : '−') + _sxwNum(Math.abs(shownChg), cur, panel.price)}
            {shownPct != null ? ' (' + _sxwPct(shownPct) + ')' : ''}
          </span>
        )}
        <span className="sxw-when">
          {hv != null && times[hover] ? _sxwTime(times[hover], r.fmt) : rangeWord}
        </span>
      </div>
      {available.length > 1 && (
        <div className="sxw-tabs" role="tablist">
          {available.map(k => (
            <button key={k} type="button" role="tab" aria-selected={k === range}
                    className={'sxw-tab' + (k === range ? ' is-on' : '')}
                    onClick={ev => { ev.stopPropagation(); setRange(k); setHover(-1); }}>{k}</button>
          ))}
        </div>
      )}
      {n >= 2 && (
        <svg className="sxw-chart" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet"
             onMouseMove={onMove} onMouseLeave={() => setHover(-1)}>
          <defs>
            <linearGradient id={uid + '-g'} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.28" />
              <stop offset="100%" stopColor={color} stopOpacity="0.01" />
            </linearGradient>
          </defs>
          <path d={area} fill={`url(#${uid}-g)`} />
          {typeof base === 'number' && (
            <line className="sxw-base" x1={x0} y1={Y(base)} x2={x1} y2={Y(base)}
                  strokeWidth="1" strokeDasharray="3 4" vectorEffect="non-scaling-stroke" />
          )}
          <path d={line} fill="none" stroke={color} strokeWidth="1.7" strokeLinejoin="round"
                strokeLinecap="round" vectorEffect="non-scaling-stroke" />
          {hv != null && (
            <g>
              <line className="sxw-cross" x1={X(hover)} y1={yTop - 8} x2={X(hover)} y2={yBot}
                    strokeWidth="1" vectorEffect="non-scaling-stroke" />
              <circle className="sxw-hover-dot" cx={X(hover)} cy={Y(hv)} r="3.2" fill={color} strokeWidth="1.5"
                      vectorEffect="non-scaling-stroke" />
            </g>
          )}
        </svg>
      )}
      {n >= 2 && tickIdx.length > 0 && (
        <div className="sxw-ticks">
          {tickIdx.map((ti, k) => (
            <span key={k}>{times[ti] ? _sxwTime(times[ti], r.fmt === 'daytime' ? 'date' : r.fmt) : ''}</span>
          ))}
        </div>
      )}
      <div className="sxw-stats">
        {stats.map(([k, v]) => (
          <div key={k} className="sxw-stat"><span className="k">{k}</span><span className="v">{v}</span></div>
        ))}
      </div>
      {yPos != null && (
        <div className="sxw-52">
          <span className="k">52-wk</span>
          <span className="lo">{_sxwNum(yl, cur)}</span>
          <span className="bar"><span className="fill" style={{ width: (yPos * 100).toFixed(1) + '%' }} />
            <span className="dot" style={{ left: (yPos * 100).toFixed(1) + '%' }} /></span>
          <span className="hi">{_sxwNum(yh, cur)}</span>
        </div>
      )}
      <div className="sxw-foot">
        {panel.as_of ? <span>As of {panel.as_of}</span> : <span />}
        <span className="sxc-link" onClick={ev => { ev.stopPropagation(); _sxcOpen((panel.sources && panel.sources[0] && panel.sources[0].url) || ''); }}>
          {(panel.sources && panel.sources[0] && panel.sources[0].title) || 'Yahoo Finance'}</span>
      </div>
    </div>
  );
}

function SyrixAdaptiveCard({ panel, timeMs }) {
  // Honesty: the card is a one-shot snapshot. The LIVE pulse / "now" tag only
  // show while it is actually fresh (<60s); after that it declares its age.
  const bornMs = (Number.isFinite(timeMs) && timeMs > 0) ? timeMs : Date.now();
  const [nowTick, setNowTick] = React.useState(Date.now());
  React.useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);
  if (!panel || typeof panel !== 'object') return null;
  const type = String(panel.type || '').toLowerCase();
  const ageS = Math.max(0, (nowTick - bornMs) / 1000);
  const isFresh = ageS < 60;
  const ageTag = isFresh ? ''
    : ageS < 3600 ? Math.round(ageS / 60) + ' min ago'
    : ageS < 86400 ? Math.round(ageS / 3600) + ' h ago'
    : 'snapshot';

  if (type === 'stock' && panel.ranges && typeof panel.ranges === 'object'
      && Object.keys(panel.ranges).length) {
    return <SxcStockWidget panel={panel} ageTag={ageTag} />;
  }

  if (type === 'stock' && Array.isArray(panel.series) && panel.series.length >= 2) {
    // Rich Perplexity-style chart card (intraday series + stats grid).
    const chg = typeof panel.change_pct === 'number' ? panel.change_pct : 0;
    const chgAbs = typeof panel.change_abs === 'number' ? panel.change_abs : null;
    const up = chg >= 0;
    const cur = panel.currency || '';
    const stats = (Array.isArray(panel.stats) ? panel.stats : [])
      .filter(s => Array.isArray(s) && s.length >= 2).slice(0, 6);
    return (
      <div className="sxc-card">
        <div className="sxc-sx-iden">
          <span className="sxc-sx-sym">{panel.symbol || ''}</span>
          <span className="sxc-sx-exch">{panel.exchange || 'market'}{ageTag ? ' · ' + ageTag : ''}</span>
        </div>
        <div className="sxc-sx-price-row">
          <span className="sxc-sx-price">{panel.price != null ? cur + Number(panel.price).toLocaleString() : '—'}</span>
          <span className={'sxc-sx-delta ' + (up ? 'up' : 'down')}>
            {chgAbs != null ? (up ? '+' : '−') + cur + Math.abs(chgAbs).toLocaleString() + ' ' : ''}
            {up ? '↗' : '↘'} {Math.abs(chg).toFixed(2)}%
          </span>
        </div>
        {panel.as_of ? <div className="sxc-sx-asof">As of {panel.as_of}</div> : <div style={{ height: 6 }} />}
        <_SxcStockChart panel={panel} />
        {stats.length > 0 && (
          <div className="sxc-sx-stats">
            {stats.map((s, i) => (
              <div key={i} className="sxc-sx-stat">
                <div className="sxc-sx-stat-k">{String(s[0])}</div>
                <div className="sxc-sx-stat-v">{String(s[1])}</div>
              </div>
            ))}
          </div>
        )}
        <div className="sxc-sx-attr">via <span className="sxc-link"
          onClick={ev => { ev.stopPropagation(); _sxcOpen((panel.sources && panel.sources[0] && panel.sources[0].url) || ''); }}>
          {(panel.sources && panel.sources[0] && panel.sources[0].title) || 'Yahoo Finance'}</span></div>
      </div>
    );
  }

  if (type === 'stock') {
    const chg = typeof panel.change_pct === 'number' ? panel.change_pct : null;
    const up = (chg == null) ? true : chg >= 0;
    if (panel.price == null && !(Array.isArray(panel.spark) && panel.spark.length)) return null;
    return (
      <div className="sxc-card">
        <div className="sxc-head">📈 {panel.symbol || 'Quote'}
          <span className="sxc-tag">{(panel.exchange || 'market') + (ageTag ? ' · ' + ageTag : '')}</span></div>
        <div className="sxc-body">
          <div className="sxc-stock-row">
            <span className="sxc-stock-sym">{panel.symbol || ''}</span>
            <span className="sxc-stock-price">{panel.price != null ? (panel.currency || '') + panel.price : '—'}</span>
            {chg != null && (
              <span className={'sxc-stock-chg ' + (up ? 'up' : 'down')}>
                {up ? '▲' : '▼'} {Math.abs(chg).toFixed(2)}%
              </span>
            )}
          </div>
          <_SxcSpark points={panel.spark} up={up} />
          <_SxcAttribution sources={panel.sources} />
        </div>
      </div>
    );
  }

  if (type === 'sports') {
    const games = Array.isArray(panel.games) ? panel.games.slice(0, 5) : [];
    if (!games.length) return null;
    return (
      <div className="sxc-card">
        <div className="sxc-head">🏆 {panel.title || 'Scores'}
          <span className="sxc-tag">{(panel.league || '') + (ageTag ? ' · ' + ageTag : '')}</span></div>
        <div className="sxc-body">
          {games.map((g, gi) => {
            const sides = Array.isArray(g.sides) ? g.sides : [];
            const a = sides[0] || {}, b = sides[1] || {};
            const st = String(g.state || '').toLowerCase();
            // Pulsing dot means "live RIGHT NOW" — a stale snapshot of an
            // in-progress game renders as a static status instead.
            const stCls = st === 'in' ? (isFresh ? 'live' : 'post') : st === 'pre' ? 'pre' : 'post';
            return (
              <div key={gi} className="sxc-game">
                <div className="sxc-team home">
                  {a.logo ? <img src={a.logo} alt="" /> : null}
                  <span className={'sxc-team-name' + (a.winner ? ' win' : '')}>{a.name || '—'}</span>
                </div>
                <div>
                  <div className="sxc-score">{(a.score != null ? a.score : '–')} : {(b.score != null ? b.score : '–')}</div>
                  <span className={'sxc-status ' + stCls}>{g.detail || st}</span>
                </div>
                <div className="sxc-team away">
                  {b.logo ? <img src={b.logo} alt="" /> : null}
                  <span className={'sxc-team-name' + (b.winner ? ' win' : '')}>{b.name || '—'}</span>
                </div>
              </div>
            );
          })}
          <_SxcAttribution sources={panel.sources} />
        </div>
      </div>
    );
  }

  if (type === 'weather') {
    if (panel.temp_c == null && !panel.condition) return null;
    return (
      <div className="sxc-card">
        <div className="sxc-head">{panel.icon || '🌤'} {panel.location || 'Weather'}
          <span className="sxc-tag">{isFresh ? 'now' : ageTag}</span></div>
        <div className="sxc-body">
          <div className="sxc-wx-row">
            <span className="sxc-wx-temp">{panel.temp_c != null ? Math.round(panel.temp_c) + '°' : '—'}</span>
            <div>
              <div className="sxc-wx-cond">{panel.condition || ''}</div>
              <div className="sxc-wx-meta">
                {panel.feels_c != null ? 'Feels ' + Math.round(panel.feels_c) + '° · ' : ''}
                {panel.humidity != null ? panel.humidity + '% humidity · ' : ''}
                {panel.wind_kmh != null ? Math.round(panel.wind_kmh) + ' km/h wind' + (panel.wind_from ? ' ' + panel.wind_from : '') : ''}
              </div>
              {(panel.high_c != null && panel.low_c != null) && (
                <div className="sxc-wx-meta">{'H ' + Math.round(panel.high_c) + '° · L ' + Math.round(panel.low_c) + '°'}</div>
              )}
            </div>
          </div>
          {Array.isArray(panel.daily) && panel.daily.length > 1 && (
            <div className="sxc-wx-days">
              {panel.daily.slice(0, 7).map((d, i) => (
                <div key={i} className="sxc-wx-day" title={d.condition || ''}>
                  <span>{i === 0 ? 'Today' : (d.day || '')}</span>
                  <span className="ico">{d.icon || ''}</span>
                  <span>{d.high_c != null ? Math.round(d.high_c) + '°' : ''}</span>
                  <span className="lo">{d.low_c != null ? Math.round(d.low_c) + '°' : ''}</span>
                </div>
              ))}
            </div>
          )}
          <_SxcAttribution sources={panel.sources} />
        </div>
      </div>
    );
  }

  if (type === 'clock') {
    return (
      <div className="sxc-card">
        <div className="sxc-head">🕒 {panel.location || 'Local time'}<span className="sxc-tag">{panel.zone || ''}</span></div>
        <div className="sxc-body">
          <div className="sxc-clock-time">{panel.time || '—'}</div>
          <div className="sxc-clock-date">{panel.date || ''}</div>
        </div>
      </div>
    );
  }

  if (type === 'products') {
    const items = Array.isArray(panel.items) ? panel.items.slice(0, 4) : [];
    if (items.length < 2) return null;
    return (
      <div className="sxc-card">
        <div className="sxc-head">🛒 {panel.title || 'Options found'}
          <span className="sxc-tag">{ageTag || 'live'}</span></div>
        <div className="sxc-body">
          {items.map((it, i) => (
            <div key={i} className="sxc-prod" title={it.url || ''}
                 onClick={ev => { ev.stopPropagation(); _sxcOpen(it.url); }}>
              <span className="sxc-prod-title">{it.title || '—'}</span>
              {it.price ? <span className="sxc-prod-price">{it.price}</span> : null}
              <span className="sxc-prod-dom">{it.domain || _sxcHost(it.url)}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // "answer" (legacy citation card) and unknown types: nothing. Presentation
  // V2 keeps citation evidence in the inspect panel, never the chat body.
  return null;
}

window.SyrixAdaptiveCard = SyrixAdaptiveCard;

/* ── Answer media ─────────────────────────────────────────────────────────
   Shared by both chat views. Images arrive as local files the backend
   fetched under the web gateway's URL policy and verified by their bytes, so
   only file: URLs render and the renderer never contacts an image's host.
   Links go through the bridge's open_url, like every other link here. */
function _sxmLocal(src) { return typeof src === 'string' && src.startsWith('file:///'); }
function _sxmIcon(host) { return host ? 'https://icons.duckduckgo.com/ip3/' + host + '.ico' : ''; }

function SxmImages({ images, pending }) {
  const list = (Array.isArray(images) ? images : []).filter(im => im && _sxmLocal(im.src)).slice(0, 8);
  const [open, setOpen] = React.useState(-1);
  React.useEffect(() => {
    if (open < 0) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') setOpen(-1);
      else if (e.key === 'ArrowRight') setOpen(i => (i + 1) % list.length);
      else if (e.key === 'ArrowLeft') setOpen(i => (i + list.length - 1) % list.length);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, list.length]);
  if (!list.length) {
    // Room held for pictures still on their way, so nothing below moves when
    // they land.
    const n = Math.min(Number(pending) || 0, 5);
    if (!n) return null;
    return (
      <div className={'sxm-imgs ' + (n > 4 ? 'strip' : 'n' + n)} aria-hidden="true">
        {Array.from({ length: n }).map((_, i) => <span key={i} className="sxm-tile" />)}
      </div>
    );
  }
  // Pictures already shown once (in the live answer) do not fade in again
  // when the finished line takes over.
  const seen = (window.__sxmSeen = window.__sxmSeen || new Set());
  const cur = open >= 0 ? list[open] : null;
  return (
    <>
      <div className={'sxm-imgs ' + (list.length > 4 ? 'strip' : 'n' + list.length)}>
        {list.map((im, i) => (
          <button key={i} type="button" className={'sxm-tile' + (seen.has(im.src) ? ' is-loaded' : '')} title={im.title || im.domain || ''}
                  onClick={ev => { ev.stopPropagation(); setOpen(i); }}>
            <img src={im.src} alt={im.title || ''} loading="lazy" className={seen.has(im.src) ? 'is-in' : ''}
                 style={{ transitionDelay: seen.has(im.src) ? '0ms' : (i * 70) + 'ms' }}
                 onLoad={e => { try { seen.add(im.src); e.currentTarget.classList.add('is-in'); e.currentTarget.parentNode.classList.add('is-loaded'); } catch (_) {} }}
                 onError={e => { try { e.currentTarget.parentNode.style.display = 'none'; } catch (_) {} }} />
          </button>
        ))}
      </div>
      {cur && (
        <div className="sxm-box" onClick={() => setOpen(-1)}>
          <img src={cur.src} alt={cur.title || ''} onClick={ev => ev.stopPropagation()} />
          <div className="sxm-bar" onClick={ev => ev.stopPropagation()}>
            <span>{cur.title || cur.domain || ''}</span>
            {cur.page && /^https?:\/\//.test(cur.page) && (
              <span className="sxc-link" onClick={() => _sxcOpen(cur.page)}>{(cur.domain || 'Source') + ' ↗'}</span>
            )}
            {list.length > 1 && <button type="button" onClick={() => setOpen((open + list.length - 1) % list.length)}>‹</button>}
            {list.length > 1 && <button type="button" onClick={() => setOpen((open + 1) % list.length)}>›</button>}
            <button type="button" onClick={() => setOpen(-1)}>✕</button>
          </div>
        </div>
      )}
    </>
  );
}

function SxmSources({ sources }) {
  const list = (Array.isArray(sources) ? sources : []).filter(s => s && /^https?:\/\//.test(String(s.url || '')));
  const [all, setAll] = React.useState(false);
  if (!list.length) return null;
  const shown = all ? list : list.slice(0, 5);
  return (
    <div className="sxm-srcs">
      <span className="sxm-srcs-k">Sources</span>
      {shown.map((s, i) => {
        const host = _sxcHost(s.url);
        return (
          <span key={i} className="sxm-src" role="link" title={(s.name ? s.name + '\n' : '') + s.url}
                onClick={ev => { ev.stopPropagation(); _sxcOpen(s.url); }}>
            <img src={_sxmIcon(host)} alt="" onError={e => { e.currentTarget.style.display = 'none'; }} />
            <span className="sxm-src-host">{host}</span>
            {typeof s.n === 'number' && <span className="sxm-src-n">{s.n}</span>}
          </span>
        );
      })}
      {list.length > 5 && (
        <span className="sxm-src" role="button" onClick={ev => { ev.stopPropagation(); setAll(v => !v); }}>
          {all ? 'Show less' : '+' + (list.length - 5) + ' more'}
        </span>
      )}
    </div>
  );
}

function SxmProducts({ products }) {
  const list = (Array.isArray(products) ? products : [])
    .filter(p => p && p.name && /^https?:\/\//.test(String(p.url || ''))).slice(0, 6);
  if (!list.length) return null;
  return (
    <div>
      <div className="sxm-prods">
        {list.map((p, i) => (
          <div key={i} className="sxm-prod" role="link" title={p.name}
               onClick={ev => { ev.stopPropagation(); _sxcOpen(p.url); }}>
            <div className="sxm-prod-img">
              {_sxmLocal(p.image) ? <img src={p.image} alt="" /> : <span>{String(p.name || '?').slice(0, 1)}</span>}
            </div>
            <div className="sxm-prod-name">{p.name}</div>
            <div className="sxm-prod-meta">
              {p.price ? <span className="sxm-prod-price">{p.price}</span> : <span />}
              <span className="sxm-prod-shop">{p.merchant || ''}</span>
            </div>
          </div>
        ))}
      </div>
      {list.some(p => p.price) && <div className="sxm-note">Prices as listed by the retailer when searched.</div>}
    </div>
  );
}

function SxmFollowUps({ questions }) {
  const list = (Array.isArray(questions) ? questions : []).filter(q => typeof q === 'string' && q.trim()).slice(0, 3);
  if (!list.length) return null;
  return (
    <div className="sxm-next">
      <span className="sxm-next-k">Related</span>
      {list.map((q, i) => (
        <button key={i} type="button" className="sxm-next-q"
                onClick={ev => {
                  ev.stopPropagation();
                  try { window.dispatchEvent(new CustomEvent('syrix-ask', { detail: { text: q } })); } catch (_) {}
                }}>
          <span>{q}</span><span className="plus">+</span>
        </button>
      ))}
    </div>
  );
}

window.SyrixAnswerMedia = { Images: SxmImages, Sources: SxmSources, Products: SxmProducts, FollowUps: SxmFollowUps };

