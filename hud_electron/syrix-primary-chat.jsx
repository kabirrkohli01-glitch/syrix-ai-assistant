/* SYRIX CHAT — chat-app style panel */
const { useState: uCS, useEffect: uCE, useRef: uCR } = React;

const chatCSS = `
.p-chat {
  position: fixed; top: 0; right: 0; bottom: 0; width: min(420px, 100vw);
  background: rgba(3,8,16,0.97); backdrop-filter: blur(26px);
  border-left: 1px solid rgba(34,211,238,0.12);
  display: flex; flex-direction: column;
  clip-path: inset(calc(100% - 52px) 6px 6px calc(100% - 52px) round 52px);
  opacity: 0;
  pointer-events: none;
  transition: clip-path 0.44s cubic-bezier(0.22,1,0.36,1), opacity 0.18s cubic-bezier(0.22,1,0.36,1), height 0.38s cubic-bezier(0.4,0,0.2,1), top 0.38s cubic-bezier(0.4,0,0.2,1), border-radius 0.38s cubic-bezier(0.4,0,0.2,1);
  /* Must overlay ABOVE the screen-vision workspace surfaces (z 95–165),
     the screen-vision fullscreen surface (z 165), the model picker (z 1900),
     and stay below the settings overlay (z 5000) and pre-boot (z 100000). */
  z-index: 2600;
  isolation: isolate;
  box-shadow: -30px 0 80px rgba(0,0,0,0.65);
}
.p-chat.open { clip-path: inset(0 0 0 0 round 0px); opacity: 1; pointer-events: auto; }
/* Compact mode: docked to bottom-right, less than half screen height */
.p-chat.compact {
  top: auto;
  bottom: 0;
  height: min(42vh, 500px);
  border-radius: 16px 0 0 0;
  border-top: 1px solid rgba(34,211,238,0.16);
}
/* Side mode: full-height right rail */
.p-chat.side {
  top: 0;
  bottom: 0;
  height: auto;
  border-radius: 0;
}
.p-chat-head {
  padding: 14px 16px; flex-shrink: 0;
  border-bottom: 1px solid rgba(34,211,238,0.09);
  display: flex; align-items: center; gap: 10px;
}
.p-chat-orb {
  width: 34px; height: 34px; border-radius: 50%; flex-shrink: 0;
  background: radial-gradient(circle, rgba(34,211,238,0.9), rgba(59,130,246,0.65) 55%, transparent 80%);
  box-shadow: 0 0 20px rgba(34,211,238,0.45);
  animation: cOrb 2.6s ease-in-out infinite;
}
@keyframes cOrb { 0%,100%{transform:scale(1);opacity:.9} 50%{transform:scale(1.1);opacity:1} }
.p-chat-label { font: 600 12px/1 'JetBrains Mono'; letter-spacing: 0.32em; color: #e9f3ff; }
.p-chat-sub { font: 500 8px/1 'JetBrains Mono'; letter-spacing: 0.24em; color: rgba(34,211,238,0.6); margin-top: 4px; }
.p-chat-head-btns { margin-left: auto; display: flex; align-items: center; gap: 2px; }
.p-chat-layout-btn {
  background: none; border: none; cursor: pointer;
  color: rgba(110,140,170,0.5); padding: 6px; transition: color .15s, background .15s;
  line-height: 1; border-radius: 6px; display: flex; align-items: center; justify-content: center;
}
.p-chat-layout-btn:hover { color: rgba(34,211,238,0.85); background: rgba(34,211,238,0.07); }
.p-chat-layout-btn.active { color: rgba(34,211,238,0.7); background: rgba(34,211,238,0.05); }
.p-chat-x { background: none; border: none; cursor: pointer; color: rgba(110,140,170,0.5); font-size: 15px; padding: 6px; transition: color .15s, background .15s; line-height: 1; border-radius: 6px; }
.p-chat-x:hover { color: #ff4f6e; background: rgba(255,79,110,0.08); }
.p-chat-msgs { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; }
.p-chat-msgs::-webkit-scrollbar { width: 3px; }
.p-chat-msgs::-webkit-scrollbar-thumb { background: rgba(34,211,238,0.16); border-radius: 2px; }
.p-msg { display: flex; gap: 10px; align-items: flex-start; animation: msgIn .3s ease-out; }
.p-msg.user { flex-direction: row-reverse; }
@keyframes msgIn { from{opacity:0;transform:translateY(8px)} to{opacity:1;transform:none} }
.p-msg-av {
  width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font: 600 8px 'JetBrains Mono'; letter-spacing: 0.08em;
}
.p-msg-av.ai { background: rgba(34,211,238,0.09); border: 1px solid rgba(34,211,238,0.24); color: #22d3ee; }
.p-msg-av.user { background: rgba(59,130,246,0.09); border: 1px solid rgba(59,130,246,0.24); color: #3b82f6; }
.p-msg-av.system { background: rgba(var(--eg-accent-surface-rgb),0.08); border: 1px solid rgba(var(--eg-accent-rgb),0.22); color: rgb(var(--eg-accent-rgb)); }
.p-msg-body {
  max-width: 82%; padding: 10px 14px; border-radius: 14px;
  font: 400 13px/1.58 'Inter'; color: rgba(220,235,255,0.92);
  word-break: break-word; white-space: pre-wrap;
}
.p-md-table, .p-md table { border-collapse: collapse; margin: 10px 0; font: 400 12px 'Inter'; width: 100%; word-break: break-word; }
.p-md-table th, .p-md table th { background: rgba(var(--eg-accent-surface-rgb),0.13); border: 1px solid rgba(var(--eg-accent-rgb),0.30); padding: 7px 10px; text-align: left; font-weight: 600; color: rgba(var(--eg-accent-rgb),0.96); line-height: 1.5; }
.p-md-table td, .p-md table td { border: 1px solid rgba(var(--eg-accent-rgb),0.16); padding: 6px 10px; color: rgba(220,235,255,0.90); line-height: 1.55; vertical-align: top; }
.p-md-table tr:nth-child(even), .p-md table tr:nth-child(even) { background: rgba(var(--eg-accent-surface-rgb),0.045); }
/* 2026-06-21 — Markdown answer body. Block layout
   so newlines from marked don't add blank lines (parent is pre-wrap). */
.p-md { white-space: normal; }
.p-md > :first-child { margin-top: 0; }
.p-md > :last-child { margin-bottom: 0; }
.p-md p { margin: 0 0 13px; line-height: 1.68; }
.p-md p:last-child { margin-bottom: 0; }
.p-md h1, .p-md h2, .p-md h3, .p-md h4 { margin: 12px 0 6px; line-height: 1.3; color: rgba(232,244,255,0.97); font-weight: 700; }
.p-md h1 { font-size: 16px; } .p-md h2 { font-size: 15px; } .p-md h3 { font-size: 13.5px; } .p-md h4 { font-size: 12.5px; }
.p-md ul, .p-md ol { margin: 8px 0 14px; padding-left: 22px; }
.p-md li { margin: 9px 0; line-height: 1.65; }
.p-md li:first-child { margin-top: 0; }
.p-md li:last-child { margin-bottom: 0; }
.p-md li > ul, .p-md li > ol { margin: 5px 0; }
.p-md strong { color: rgba(238,247,255,0.98); font-weight: 700; }
.p-md em { color: rgba(220,235,255,0.95); }
.p-md a { color: #6cc6ff; text-decoration: none; border-bottom: 1px solid rgba(108,198,255,0.35); }
.p-md a:hover { border-bottom-color: rgba(108,198,255,0.9); }
.p-md code { font: 400 11.5px 'JetBrains Mono', monospace; background: rgba(34,211,238,0.10); border: 1px solid rgba(34,211,238,0.16); border-radius: 5px; padding: 1px 5px; color: #aee6ff; }
.p-md pre { margin: 8px 0; padding: 10px 12px; background: rgba(6,14,24,0.7); border: 1px solid rgba(34,211,238,0.16); border-radius: 9px; overflow-x: auto; }
.p-md pre code { background: none; border: none; padding: 0; color: rgba(220,235,255,0.92); font-size: 11.5px; line-height: 1.5; }
.p-md blockquote { margin: 8px 0; padding: 4px 12px; border-left: 3px solid rgba(34,211,238,0.4); background: rgba(34,211,238,0.05); color: rgba(210,228,248,0.9); border-radius: 0 6px 6px 0; }
.p-md hr { border: none; border-top: 1px solid rgba(34,211,238,0.16); margin: 12px 0; }
.p-msg.ai .p-msg-body { background: rgba(34,211,238,0.055); border: 1px solid rgba(34,211,238,0.1); border-top-left-radius: 4px; }
.p-msg.user .p-msg-body { background: rgba(59,130,246,0.1); border: 1px solid rgba(59,130,246,0.18); border-top-right-radius: 4px; }
.p-msg.system .p-msg-body { background: rgba(var(--eg-accent-surface-rgb),0.055); border: 1px solid rgba(var(--eg-accent-rgb),0.12); border-top-left-radius: 4px; color: rgba(var(--eg-accent-bright-rgb),0.92); }
.p-msg-meta { margin-top: 7px; font: 600 8px/1.3 'JetBrains Mono'; letter-spacing: 0.16em; color: rgba(110,140,170,0.7); text-transform: uppercase; }
.p-msg-img { max-width: 200px; border-radius: 8px; margin-top: 8px; display: block; border: 1px solid rgba(34,211,238,0.15); }
.p-typing { display: flex; gap: 5px; align-items: center; padding: 2px 0; }
.p-tdot { width: 6px; height: 6px; border-radius: 50%; background: #22d3ee; animation: td 1.2s ease-in-out infinite; }
.p-tdot:nth-child(2){animation-delay:.22s} .p-tdot:nth-child(3){animation-delay:.44s}
@keyframes td { 0%,80%,100%{transform:scale(.55);opacity:.3} 40%{transform:scale(1);opacity:1} }
.p-trace-step { margin-top: 6px; font: 500 9px/1.4 'JetBrains Mono'; color: rgba(34,211,238,0.65); letter-spacing: 0.08em; animation: ts-fade 0.4s ease; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 220px; }
@keyframes ts-fade { from{opacity:0;transform:translateY(3px)} to{opacity:1;transform:translateY(0)} }
/* 2026-06-09 — Grok-style step ladder inside thinking bubble. */
/* Thinking card — card-style container matching aurora's eg-think */
.p-thinking-card { margin-top: 6px; padding: 10px 12px; border-radius: 12px; background: rgba(34,211,238,0.04); border: 1px solid rgba(34,211,238,0.14); animation: think-card-in 0.38s cubic-bezier(0.2,0.9,0.3,1.1); }
@keyframes think-card-in { from{opacity:0;transform:translateY(7px) scale(0.97)} to{opacity:1;transform:none} }
.p-trace-ladder { margin-top: 8px; display: flex; flex-direction: column; gap: 5px; border-left: 2px solid rgba(34,211,238,0.22); padding-left: 12px; }
.p-trace-rung { display: flex; align-items: center; gap: 7px; font: 600 10.5px/1.4 'Inter'; color: rgba(230,242,255,0.88); animation: rung-in 0.3s cubic-bezier(0.2,0.9,0.3,1.1); position: relative; }
.p-trace-rung-bullet { position: absolute; left: -16px; top: 50%; transform: translateY(-50%); width: 7px; height: 7px; border-radius: 50%; background: rgba(116,231,217,0.72); box-shadow: 0 0 6px rgba(116,231,217,0.45); }
.p-trace-rung.kind-search .p-trace-rung-bullet { background: #74e7d9; box-shadow: 0 0 8px rgba(116,231,217,0.7); }
.p-trace-rung.kind-source .p-trace-rung-bullet { background: #a4d8ff; box-shadow: 0 0 7px rgba(164,216,255,0.6); }
.p-trace-rung.kind-synthesis .p-trace-rung-bullet { background: rgb(var(--eg-accent-surface-rgb)); box-shadow: 0 0 7px rgba(var(--eg-accent-rgb),0.6); }
.p-trace-rung.kind-brain_route .p-trace-rung-bullet { background: #16d37a; box-shadow: 0 0 7px rgba(22,211,122,0.65); }
.p-trace-rung.kind-tool_call .p-trace-rung-bullet { background: #c89bff; box-shadow: 0 0 6px rgba(200,155,255,0.55); }
.p-trace-rung-label { letter-spacing: 0.04em; color: rgba(230,242,255,0.94); flex-shrink: 0; }
.p-trace-rung-detail { color: rgba(160,185,210,0.6); font-weight: 400; font-size: 9.5px; letter-spacing: 0.01em; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 240px; }
.p-trace-rung.clickable { cursor: pointer; transition: color 0.15s; }
.p-trace-rung.clickable:hover .p-trace-rung-detail { color: rgba(164,216,255,0.95); text-decoration: underline; }
@keyframes rung-in { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
/* 2026-06-28 — clearer thinking ladder: completed steps dim, the live step
   stays bright with a breathing bullet, and its detail wraps so it's readable. */
.p-trace-rung.done-step { opacity: 0.45; }
.p-trace-rung.active { opacity: 1; }
.p-trace-rung.active .p-trace-rung-label { color: #d8f0ff; }
.p-trace-rung.active .p-trace-rung-bullet { animation: rung-pulse 1.4s ease-in-out infinite; }
.p-trace-rung.active .p-trace-rung-detail { white-space: normal; max-width: none; color: rgba(190,212,235,0.85); }
@keyframes rung-pulse { 0%,100% { transform: translateY(-50%) scale(1); opacity: 1; box-shadow: 0 0 6px rgba(116,231,217,0.5); } 50% { transform: translateY(-50%) scale(1.7); opacity: 0.55; box-shadow: 0 0 12px rgba(116,231,217,0.85); } }
.p-think-count { margin-left: auto; font: 600 8px 'JetBrains Mono'; letter-spacing: 0.16em; text-transform: uppercase;
  color: rgba(116,231,217,0.7); background: rgba(116,231,217,0.08); border: 1px solid rgba(116,231,217,0.2);
  border-radius: 999px; padding: 2px 8px; }
/* Source/search domain pills with favicon */
.p-trace-pills { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.p-trace-fav-pill { display: inline-flex; align-items: center; gap: 3px; background: rgba(164,216,255,0.07); border: 1px solid rgba(164,216,255,0.2); border-radius: 999px; padding: 2px 7px; font: 600 8.5px 'JetBrains Mono'; letter-spacing: 0.07em; color: rgba(164,216,255,0.82); cursor: pointer; transition: background .14s, border-color .14s; animation: rung-in 0.28s cubic-bezier(0.2,0.9,0.3,1.1); white-space: nowrap; }
.p-trace-fav-pill:hover { background: rgba(164,216,255,0.14); border-color: rgba(164,216,255,0.38); color: rgba(200,230,255,0.95); }
.p-trace-fav-pill img { width: 12px; height: 12px; border-radius: 2px; display: block; flex-shrink: 0; }
.p-trace-search-pill { display: inline-flex; align-items: center; gap: 3px; background: rgba(116,231,217,0.07); border: 1px solid rgba(116,231,217,0.22); border-radius: 999px; padding: 2px 7px; font: 600 8.5px 'JetBrains Mono'; letter-spacing: 0.07em; color: rgba(116,231,217,0.82); animation: rung-in 0.28s cubic-bezier(0.2,0.9,0.3,1.1); white-space: nowrap; }
.p-trace-brain-pill { display: inline-flex; align-items: center; gap: 3px; background: rgba(22,211,122,0.07); border: 1px solid rgba(22,211,122,0.22); border-radius: 999px; padding: 2px 7px; font: 600 8.5px 'JetBrains Mono'; letter-spacing: 0.07em; color: rgba(22,211,122,0.78); animation: rung-in 0.28s cubic-bezier(0.2,0.9,0.3,1.1); white-space: nowrap; }
/* Current-action headline in thinking header */
.p-think-action { font: 500 10.5px 'Inter'; letter-spacing: 0.01em; color: rgba(34,211,238,0.82); animation: ts-fade 0.35s ease; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 180px; }
.p-reassurance { margin-top: 10px; padding: 6px 10px; border-left: 2px solid rgba(var(--eg-accent-rgb),0.55); background: rgba(var(--eg-accent-surface-rgb),0.06); font: 500 11px/1.5 'Inter'; color: rgba(var(--eg-accent-rgb),0.92); letter-spacing: 0.02em; border-radius: 0 6px 6px 0; animation: reass-in 0.45s ease; }
@keyframes reass-in { from { opacity: 0; transform: translateX(-4px); } to { opacity: 1; transform: translateX(0); } }
.p-route-chip { display: inline-flex; align-items: center; margin-top: 6px; padding: 2px 7px; border-radius: 999px; background: rgba(34,211,238,0.07); border: 1px solid rgba(34,211,238,0.18); font: 600 8px/1.4 'JetBrains Mono'; letter-spacing: 0.22em; text-transform: uppercase; color: rgba(34,211,238,0.55); }
.p-sources { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; }
.p-source-chip { display: inline-flex; align-items: center; padding: 2px 8px; border-radius: 999px; background: rgba(99,102,241,0.08); border: 1px solid rgba(99,102,241,0.22); font: 600 8px/1.4 'JetBrains Mono'; letter-spacing: 0.16em; color: rgba(165,180,252,0.75); white-space: nowrap; text-decoration: none; cursor: default; transition: background .15s, border-color .15s, color .15s; }
a.p-source-chip { cursor: pointer; }
a.p-source-chip:hover { background: rgba(99,102,241,0.18); border-color: rgba(99,102,241,0.45); color: rgba(199,210,254,0.95); }
.p-followups { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(34,211,238,0.07); }
.p-followup-chip { display: inline-flex; align-items: center; padding: 4px 10px; border-radius: 999px; background: rgba(34,211,238,0.08); border: 1px solid rgba(34,211,238,0.22); font: 500 11px/1.3 'Inter'; color: rgba(220,235,255,0.92); cursor: pointer; transition: background .15s, border-color .15s, transform .15s; }
.p-followup-chip:hover { background: rgba(34,211,238,0.18); border-color: rgba(34,211,238,0.48); transform: translateY(-1px); }
.p-followup-chip:active { transform: translateY(0); }
.p-followup-chip .ico { margin-right: 6px; opacity: 0.7; font-size: 9px; }
.p-confidence { display: inline-flex; align-items: center; margin-top: 7px; margin-right: 6px; padding: 2px 8px; border-radius: 999px; font: 600 8px/1.4 'JetBrains Mono'; letter-spacing: 0.16em; text-transform: uppercase; }
.p-confidence.high { background: rgba(72,199,142,0.10); border: 1px solid rgba(72,199,142,0.30); color: rgba(72,199,142,0.95); }
.p-confidence.med { background: rgba(var(--eg-accent-surface-rgb),0.10); border: 1px solid rgba(var(--eg-accent-rgb),0.28); color: rgba(var(--eg-accent-rgb),0.92); }
.p-confidence.low { background: rgba(255,107,107,0.10); border: 1px solid rgba(255,107,107,0.30); color: rgba(255,107,107,0.92); }
.p-time { margin-top: 4px; font: 400 9px/1.2 'JetBrains Mono'; letter-spacing: 0.10em; color: rgba(110,140,170,0.5); }
.p-fb-reasons { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.p-fb-reason { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.10); color: rgba(220,235,255,0.85); border-radius: 6px; padding: 2px 7px; font: 500 10px 'Inter'; cursor: pointer; transition: background .12s, border-color .12s; }
.p-fb-reason:hover { background: rgba(34,211,238,0.10); border-color: rgba(34,211,238,0.28); }
.p-chat-foot { padding: 10px 14px 18px; border-top: 1px solid rgba(34,211,238,0.07); flex-shrink: 0; }
/* Attachment chips. Docked to the composer, so the row reads as part of the
   input rather than as content floating over the conversation. The chip
   carries the three things needed to tell one attachment from another at a
   glance — type, name, and whether it is ready — and nothing else. */
.p-attach-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.p-attach-chip {
  position: relative; display: inline-flex; align-items: center; gap: 9px;
  max-width: 260px; padding: 7px 30px 7px 8px; border-radius: 11px;
  background: rgba(8,18,34,0.82); border: 1px solid rgba(34,211,238,0.16);
  transition: border-color .18s, background .18s;
}
.p-attach-chip:hover { border-color: rgba(34,211,238,0.34); background: rgba(10,22,42,0.9); }
.p-attach-chip.busy { border-color: rgba(34,211,238,0.3); }
.p-attach-chip.bad { border-color: rgba(255,79,110,0.42); background: rgba(40,10,18,0.78); }
.p-attach-thumb img { width: 34px; height: 34px; object-fit: cover; border-radius: 7px; display: block; border: 1px solid rgba(34,211,238,0.22); }
/* The icon is a coloured tile with the extension on it: readable at this size
   where a pictogram is not, and unambiguous between DOC and PDF. */
.p-attach-icon {
  width: 34px; height: 34px; border-radius: 7px; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  font: 700 8px/1 'JetBrains Mono'; letter-spacing: 0.06em; color: #fff;
}
.p-attach-icon.pdf   { background: linear-gradient(160deg,#e8465f,#b3122c); }
.p-attach-icon.doc   { background: linear-gradient(160deg,#3b82f6,#1d4ed8); }
.p-attach-icon.sheet { background: linear-gradient(160deg,#22c55e,#15803d); }
.p-attach-icon.slide { background: linear-gradient(160deg,#fb923c,#c2410c); }
.p-attach-icon.text  { background: linear-gradient(160deg,#64748b,#334155); }
.p-attach-meta { display: flex; flex-direction: column; min-width: 0; gap: 2px; }
.p-attach-name { font: 500 11px/1.25 'Inter'; color: #dcebff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.p-attach-sub { font: 500 8px/1.2 'JetBrains Mono'; letter-spacing: 0.12em; text-transform: uppercase; color: rgba(140,175,220,0.62); }
.p-attach-chip.busy .p-attach-sub { color: rgba(34,211,238,0.8); }
.p-attach-chip.bad .p-attach-sub { color: rgba(255,138,160,0.9); text-transform: none; letter-spacing: 0.04em; }
.p-attach-rm {
  position: absolute; top: 50%; right: 7px; transform: translateY(-50%);
  width: 17px; height: 17px; border-radius: 50%; border: none; cursor: pointer;
  background: rgba(120,150,190,0.16); color: rgba(210,230,255,0.75);
  font-size: 11px; line-height: 1; display: flex; align-items: center;
  justify-content: center; transition: background .15s, color .15s;
}
.p-attach-rm:hover { background: #ff4f6e; color: #fff; }
.p-input-row {
  display: flex; gap: 8px; align-items: flex-end;
  background: rgba(6,14,28,0.72); border: 1px solid rgba(34,211,238,0.13); border-radius: 12px;
  padding: 8px 10px; transition: border-color .2s;
}
.p-input-row:focus-within { border-color: rgba(34,211,238,0.28); }
.p-chat-ta { flex: 1; min-width: 0; background: transparent; border: none; outline: none; resize: none; color: #e9f3ff; font: 400 13px/1.52 'Inter'; min-height: 20px; max-height: 120px; scrollbar-width: none; }
.p-chat-ta::placeholder { color: rgba(80,110,150,0.55); }
.p-up-btn { background: none; border: none; cursor: pointer; color: rgba(34,211,238,0.42); padding: 4px; display: flex; align-items: center; transition: color .15s, box-shadow .18s; flex-shrink: 0; border-radius: 7px; }
.p-up-btn:hover { color: #22d3ee; box-shadow: 0 0 0 1px rgba(34,211,238,0.16), 0 0 14px rgba(34,211,238,0.14); }
.p-input-actions { display: flex; gap: 8px; align-items: flex-end; flex-shrink: 0; }
.p-send-btn { background: rgba(34,211,238,0.11); border: 1px solid rgba(34,211,238,0.26); border-radius: 8px; color: #22d3ee; cursor: pointer; padding: 7px 13px; font: 600 9px 'JetBrains Mono'; letter-spacing: 0.2em; transition: all .15s; flex-shrink: 0; }
.p-send-btn:hover { background: rgba(34,211,238,0.22); border-color: rgba(34,211,238,0.54); box-shadow: 0 0 0 1px rgba(34,211,238,0.15), 0 0 16px rgba(34,211,238,0.16); }
.p-stop-btn { background: rgba(255,79,110,0.12); border: 1px solid rgba(255,79,110,0.38); border-radius: 8px; color: #ff4f6e; cursor: pointer; padding: 7px 11px; font: 600 9px 'JetBrains Mono'; letter-spacing: 0.2em; transition: all .15s; flex-shrink: 0; min-width: 52px; }
.p-stop-btn:hover { background: rgba(255,79,110,0.24); border-color: rgba(34,211,238,0.5); box-shadow: 0 0 0 1px rgba(34,211,238,0.14), 0 0 16px rgba(34,211,238,0.14); }
/* 2026-05-30 — Chat "+" action menu (Deep Research). */
.p-plus-btn { background: rgba(34,211,238,0.06); border: 1px solid rgba(34,211,238,0.18); cursor: pointer; color: rgba(34,211,238,0.72); padding: 0; display: flex; align-items: center; justify-content: center; transition: color .15s, background .15s, box-shadow .18s; flex-shrink: 0; border-radius: 8px; width: 28px; height: 28px; font: 700 16px 'JetBrains Mono'; line-height: 1; }
.p-plus-btn:hover { color: #22d3ee; background: rgba(34,211,238,0.14); box-shadow: 0 0 0 1px rgba(34,211,238,0.20), 0 0 14px rgba(34,211,238,0.14); }
.p-plus-btn.active { color: #fff; background: rgba(34,211,238,0.34); border-color: rgba(34,211,238,0.6); }
.p-plus-menu { position: absolute; bottom: 100%; left: 8px; transform: translateY(-6px); background: rgba(4,11,22,0.96); backdrop-filter: blur(16px); border: 1px solid rgba(34,211,238,0.32); border-radius: 10px; padding: 6px; display: flex; flex-direction: column; gap: 4px; min-width: 220px; box-shadow: 0 18px 42px rgba(0,0,0,0.55); z-index: 30; }
.p-plus-menu-item { background: transparent; border: 1px solid transparent; color: rgba(220,235,255,0.88); cursor: pointer; padding: 8px 10px; border-radius: 7px; font: 500 12px 'Inter'; text-align: left; display: flex; align-items: center; gap: 8px; transition: background .12s, border-color .12s; }
.p-plus-menu-item:hover { background: rgba(34,211,238,0.08); border-color: rgba(34,211,238,0.20); }
.p-plus-menu-item .ico { width: 16px; height: 16px; flex-shrink: 0; color: rgba(34,211,238,0.85); display: flex; align-items: center; justify-content: center; }
.p-plus-menu-item .meta { font: 500 9px/1.2 'JetBrains Mono'; letter-spacing: 0.14em; color: rgba(34,211,238,0.55); text-transform: uppercase; margin-left: 24px; margin-top: 2px; }
.p-mode-chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 9px; border-radius: 999px; font: 600 9px 'JetBrains Mono'; letter-spacing: 0.18em; text-transform: uppercase; margin-right: 8px; background: rgba(34,211,238,0.16); border: 1px solid rgba(34,211,238,0.34); color: #22d3ee; cursor: pointer; }
.p-mode-chip.dash { background: rgba(var(--eg-accent-surface-rgb),0.16); border-color: rgba(var(--eg-accent-rgb),0.36); color: rgb(var(--eg-accent-rgb)); }
.p-mode-chip:hover { filter: brightness(1.18); }
.p-mode-chip .x { font-size: 11px; line-height: 1; opacity: 0.7; }

/* ── SPARK / ZENITH model switcher + ZENITH frame glow (2026-07-02) ─────── */
.p-model-btn { background: rgba(34,211,238,0.06); border: 1px solid rgba(34,211,238,0.18); cursor: pointer; color: rgba(34,211,238,0.72); padding: 0 9px; display: flex; align-items: center; justify-content: center; gap: 5px; transition: color .15s, background .15s, box-shadow .18s, border-color .18s; flex-shrink: 0; border-radius: 8px; height: 28px; font: 700 8.5px 'JetBrains Mono'; letter-spacing: 0.14em; white-space: nowrap; }
.p-model-btn:hover { color: #22d3ee; background: rgba(34,211,238,0.14); box-shadow: 0 0 0 1px rgba(34,211,238,0.20), 0 0 14px rgba(34,211,238,0.14); }
.p-model-btn.active { color: #fff; background: rgba(34,211,238,0.30); border-color: rgba(34,211,238,0.55); }
.p-model-btn.zenith { color: #cbb8ff; background: rgba(168,130,255,0.11); border-color: rgba(168,130,255,0.40); box-shadow: 0 0 0 1px rgba(168,130,255,0.14), 0 0 16px rgba(139,92,246,0.22); }
.p-model-btn.zenith:hover { color: #d7c9ff; background: rgba(168,130,255,0.19); box-shadow: 0 0 0 1px rgba(168,130,255,0.24), 0 0 20px rgba(139,92,246,0.30); }
.p-model-menu { position: absolute; bottom: 100%; left: 0; transform: translateY(-6px); background: rgba(4,11,22,0.96); backdrop-filter: blur(16px); border: 1px solid rgba(34,211,238,0.32); border-radius: 10px; padding: 6px; display: flex; flex-direction: column; gap: 4px; min-width: 238px; box-shadow: 0 18px 42px rgba(0,0,0,0.55); z-index: 30; }
.p-model-menu-item { background: transparent; border: 1px solid transparent; color: rgba(220,235,255,0.88); cursor: pointer; padding: 8px 10px; border-radius: 7px; text-align: left; display: flex; flex-direction: column; gap: 3px; transition: background .12s, border-color .12s; }
.p-model-menu-item:hover { background: rgba(34,211,238,0.08); border-color: rgba(34,211,238,0.20); }
.p-model-menu-item.sel { border-color: rgba(34,211,238,0.34); background: rgba(34,211,238,0.07); }
.p-model-menu-item .mi-name { font: 700 11px 'JetBrains Mono'; letter-spacing: 0.12em; color: #7de3f4; }
.p-model-menu-item .mi-desc { font: 500 8.5px/1.3 'JetBrains Mono'; letter-spacing: 0.1em; color: rgba(160,190,220,0.62); text-transform: uppercase; }
.p-model-menu-item.zenith .mi-name { color: #cbb8ff; }
.p-model-menu-item.zenith .mi-desc { color: rgba(184,166,235,0.62); }
.p-model-menu-item.zenith:hover { background: rgba(168,130,255,0.09); border-color: rgba(168,130,255,0.28); }
.p-model-menu-item.zenith.sel { border-color: rgba(168,130,255,0.44); background: rgba(168,130,255,0.09); }
/* ZENITH selected: the whole chat frame carries a slow-breathing violet glow. */
.p-chat.zenith { border-left-color: rgba(168,130,255,0.36); animation: zenithBreathe 3.8s ease-in-out infinite; }
.p-chat.zenith.compact { border-top-color: rgba(168,130,255,0.32); }
.p-chat.zenith .p-input-row { border-color: rgba(168,130,255,0.24); }
.p-chat.zenith .p-input-row:focus-within { border-color: rgba(168,130,255,0.48); }
.p-chat.zenith .p-chat-ta::placeholder { color: rgba(190,175,255,0.55); }
.p-chat.zenith .p-send-btn { background: rgba(168,130,255,0.13); border-color: rgba(168,130,255,0.36); color: #cbb8ff; }
.p-chat.zenith .p-send-btn:hover { background: rgba(168,130,255,0.22); border-color: rgba(168,130,255,0.62); box-shadow: 0 0 0 1px rgba(168,130,255,0.18), 0 0 16px rgba(139,92,246,0.24); }
@keyframes zenithBreathe {
  0%, 100% { box-shadow: -30px 0 80px rgba(0,0,0,0.65), 0 0 40px -8px rgba(139,92,246,0.30), inset 0 0 64px -44px rgba(168,130,255,0.26); }
  50%      { box-shadow: -30px 0 80px rgba(0,0,0,0.65), 0 0 58px -6px rgba(139,92,246,0.48), inset 0 0 76px -40px rgba(168,130,255,0.40); }
}

/* ── Smart panels (inline rich cards: sports / stock / weather / answer) ─────
   Shown only when SYRIX judges a structured card adds clarity. Aurora-glass
   look to match the widgets: blur, amber/cyan accents, soft catch-light. */
.p-panel { margin-top: 10px; border-radius: 13px; overflow: hidden;
  background: linear-gradient(150deg, rgba(10,18,32,0.82), rgba(6,12,24,0.9));
  border: 1px solid rgba(34,211,238,0.16);
  box-shadow: 0 10px 30px rgba(0,0,0,0.42), inset 0 1px 0 rgba(255,255,255,0.05);
  backdrop-filter: blur(18px) saturate(1.3); animation: panelIn 0.42s cubic-bezier(0.23,1,0.32,1); }
@keyframes panelIn { from { opacity: 0; transform: translateY(8px) scale(0.97); } to { opacity: 1; transform: none; } }
.p-panel-head { display: flex; align-items: center; gap: 7px; padding: 8px 12px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
  font: 600 8.5px 'JetBrains Mono'; letter-spacing: 0.2em; text-transform: uppercase; }
.p-panel-head .p-panel-tag { margin-left: auto; opacity: 0.6; font-size: 7.5px; }
.p-panel-body { padding: 11px 13px; }
.p-panel.sports .p-panel-head { color: #74e7d9; background: rgba(116,231,217,0.06); }
.p-panel.stock .p-panel-head { color: rgb(var(--eg-accent-rgb)); background: rgba(var(--eg-accent-surface-rgb),0.06); }
.p-panel.weather .p-panel-head { color: #a4d8ff; background: rgba(164,216,255,0.06); }
.p-panel.answer .p-panel-head { color: #c89bff; background: rgba(200,155,255,0.06); }

/* sports rows */
.p-sport-game { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center;
  gap: 10px; padding: 8px 4px; }
.p-sport-game + .p-sport-game { border-top: 1px solid rgba(255,255,255,0.05); }
.p-sport-team { display: flex; align-items: center; gap: 8px; min-width: 0; }
.p-sport-team.away { flex-direction: row-reverse; text-align: right; }
.p-sport-team img { width: 22px; height: 22px; object-fit: contain; flex-shrink: 0; }
.p-sport-name { font: 600 12px 'Inter'; color: #e9f3ff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.p-sport-name.win { color: #fff; }
.p-sport-score { font: 700 20px 'JetBrains Mono'; letter-spacing: 0.04em; color: #fff;
  text-align: center; white-space: nowrap; }
.p-sport-status { display: block; margin-top: 2px; font: 600 8px 'JetBrains Mono';
  letter-spacing: 0.14em; text-transform: uppercase; text-align: center; }
.p-sport-status.live { color: #16d37a; }
.p-sport-status.live::before { content: ''; display: inline-block; width: 6px; height: 6px;
  border-radius: 50%; background: #16d37a; margin-right: 5px; box-shadow: 0 0 7px #16d37a;
  animation: sportPulse 1.3s ease-in-out infinite; vertical-align: middle; }
.p-sport-status.pre { color: rgba(160,185,210,0.7); }
.p-sport-status.post { color: rgba(160,185,210,0.55); }
@keyframes sportPulse { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }

/* stock card */
.p-stock-row { display: flex; align-items: baseline; gap: 10px; }
.p-stock-sym { font: 700 16px 'JetBrains Mono'; letter-spacing: 0.06em; color: #fff; }
.p-stock-price { font: 700 22px 'Inter'; color: #fff; }
.p-stock-chg { font: 600 13px 'JetBrains Mono'; padding: 2px 8px; border-radius: 7px; }
.p-stock-chg.up { color: #16d37a; background: rgba(22,211,122,0.12); }
.p-stock-chg.down { color: #ff6b6b; background: rgba(255,107,107,0.12); }
.p-stock-spark { margin-top: 9px; display: block; width: 100%; height: 34px; }

/* weather card */
.p-weather-row { display: flex; align-items: center; gap: 14px; }
.p-weather-temp { font: 700 30px 'Inter'; color: #fff; line-height: 1; }
.p-weather-meta { font: 500 11px/1.5 'Inter'; color: rgba(180,210,255,0.78); }
.p-weather-cond { font: 600 11px 'JetBrains Mono'; letter-spacing: 0.08em; color: #a4d8ff; text-transform: uppercase; }

/* answer card */
.p-answer-headline { font: 600 14px/1.45 'Inter'; color: #f3eaff; }
.p-answer-facts { margin: 8px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 5px; }
.p-answer-facts li { font: 400 12px/1.45 'Inter'; color: rgba(220,235,255,0.85); padding-left: 14px; position: relative; }
.p-answer-facts li::before { content: '▸'; position: absolute; left: 0; color: #c89bff; font-size: 10px; }
.p-panel-srcs { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 9px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.05); }
.p-panel-src { font: 600 8px 'JetBrains Mono'; letter-spacing: 0.1em; color: rgba(165,180,252,0.78);
  text-decoration: none; padding: 2px 7px; border-radius: 999px; background: rgba(99,102,241,0.08);
  border: 1px solid rgba(99,102,241,0.2); white-space: nowrap; transition: background .15s, color .15s; }
.p-panel-src:hover { background: rgba(99,102,241,0.2); color: #fff; }
@media (prefers-reduced-motion: reduce) { .p-panel { animation: none; } .p-sport-status.live::before { animation: none; } }
`;

// Monotonic counter for unique message IDs across renders.
let _chatMsgSeq = 1;

// ── Smart panels ────────────────────────────────────────────────────────────
// Inline rich cards rendered under an answer when the backend attaches a
// `panel` object to the chat line. The backend decides WHEN (only for crisp
// structured answers); the renderer decides HOW. Unknown/empty → nothing.
function _panelHost(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch (_) { return 'source'; }
}

function _PanelSources({ sources }) {
  const srcs = Array.isArray(sources) ? sources.slice(0, 4) : [];
  if (!srcs.length) return null;
  return (
    <div className="p-panel-srcs">
      {srcs.map((s, i) => {
        const url = (s && (s.url || s.href)) || '';
        const label = (s && (s.name || s.title)) || (url ? _panelHost(url) : 'source');
        return url
          ? <a key={i} className="p-panel-src" href={url} target="_blank" rel="noreferrer" title={url}>{label}</a>
          : <span key={i} className="p-panel-src">{label}</span>;
      })}
    </div>
  );
}

function _Sparkline({ points, up }) {
  const vals = (Array.isArray(points) ? points : []).filter(v => typeof v === 'number');
  if (vals.length < 2) return null;
  const w = 240, h = 34, min = Math.min(...vals), max = Math.max(...vals);
  const span = (max - min) || 1;
  const step = w / (vals.length - 1);
  const d = vals.map((v, i) => `${i === 0 ? 'M' : 'L'}${(i * step).toFixed(1)},${(h - ((v - min) / span) * (h - 4) - 2).toFixed(1)}`).join(' ');
  const col = up ? '#16d37a' : '#ff6b6b';
  return (
    <svg className="p-stock-spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none">
      <path d={d} fill="none" stroke={col} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function SmartPanel({ panel, timeMs }) {
  // 2026-07-02 — honesty: the card is a one-shot snapshot attached to the
  // message. The pulsing LIVE dot / "now" tag only show while the data is
  // actually fresh (<60s); after that the card declares its age instead of
  // faking liveness. (Hooks run before any early return — React rules.)
  const bornMs = (Number.isFinite(timeMs) && timeMs > 0) ? timeMs : Date.now();
  const [nowTick, setNowTick] = uCS(Date.now());
  uCE(() => {
    const id = setInterval(() => setNowTick(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);
  if (!panel || typeof panel !== 'object') return null;
  const type = String(panel.type || '').toLowerCase();
  const sources = <_PanelSources sources={panel.sources} />;
  const ageS = Math.max(0, (nowTick - bornMs) / 1000);
  const isFresh = ageS < 60;
  const ageTag = isFresh ? ''
    : ageS < 3600 ? Math.round(ageS / 60) + ' min ago'
    : ageS < 86400 ? Math.round(ageS / 3600) + ' h ago'
    : 'snapshot';

  if (type === 'sports') {
    const games = Array.isArray(panel.games) ? panel.games.slice(0, 5) : [];
    if (!games.length) return null;
    return (
      <div className="p-panel sports">
        <div className="p-panel-head">⚽ {panel.title || 'Live Scores'}<span className="p-panel-tag">{(panel.league || '') + (ageTag ? ' · ' + ageTag : '')}</span></div>
        <div className="p-panel-body">
          {games.map((g, gi) => {
            const sides = Array.isArray(g.sides) ? g.sides : [];
            const a = sides[0] || {}, b = sides[1] || {};
            const st = String(g.state || '').toLowerCase();
            // The pulsing green dot means "live RIGHT NOW" — a stale snapshot
            // of an in-progress game renders as a static status instead.
            const stCls = st === 'in' ? (isFresh ? 'live' : 'post') : st === 'pre' ? 'pre' : 'post';
            return (
              <div key={gi} className="p-sport-game">
                <div className="p-sport-team home">
                  {a.logo ? <img src={a.logo} alt="" /> : null}
                  <span className={"p-sport-name" + (a.winner ? ' win' : '')}>{a.name || '—'}</span>
                </div>
                <div>
                  <div className="p-sport-score">{(a.score != null ? a.score : '–')} : {(b.score != null ? b.score : '–')}</div>
                  <span className={"p-sport-status " + stCls}>{g.detail || st}</span>
                </div>
                <div className="p-sport-team away">
                  {b.logo ? <img src={b.logo} alt="" /> : null}
                  <span className={"p-sport-name" + (b.winner ? ' win' : '')}>{b.name || '—'}</span>
                </div>
              </div>
            );
          })}
          {sources}
        </div>
      </div>
    );
  }

  if (type === 'stock') {
    const chg = typeof panel.change_pct === 'number' ? panel.change_pct : null;
    const up = (chg == null) ? true : chg >= 0;
    return (
      <div className="p-panel stock">
        <div className="p-panel-head">📈 {panel.symbol || 'Quote'}<span className="p-panel-tag">{panel.exchange || 'market'}</span></div>
        <div className="p-panel-body">
          <div className="p-stock-row">
            <span className="p-stock-sym">{panel.symbol || ''}</span>
            <span className="p-stock-price">{panel.price != null ? (panel.currency || '') + panel.price : '—'}</span>
            {chg != null && (
              <span className={"p-stock-chg " + (up ? 'up' : 'down')}>
                {up ? '▲' : '▼'} {Math.abs(chg).toFixed(2)}%
              </span>
            )}
          </div>
          {panel.name && panel.name !== panel.symbol && <div className="p-stock-name">{panel.name}</div>}
          <_Sparkline points={panel.spark} up={up} />
          <div className="p-stock-facts">
            {panel.change != null && <span>Change <b>{(panel.change >= 0 ? '+' : '') + (panel.currency || '') + Math.abs(panel.change).toFixed(2)}</b></span>}
            {(panel.day_low != null && panel.day_high != null) && <span>Day <b>{(panel.currency || '') + Number(panel.day_low).toFixed(2) + ' – ' + (panel.currency || '') + Number(panel.day_high).toFixed(2)}</b></span>}
            {panel.month_change_pct != null && <span>1M <b>{(panel.month_change_pct >= 0 ? '+' : '') + Number(panel.month_change_pct).toFixed(2) + '%'}</b></span>}
            {panel.conversion && <span>{panel.conversion.amount + ' ' + panel.conversion.from + ' = '}<b>{panel.conversion.result + ' ' + panel.conversion.to}</b></span>}
          </div>
          {Array.isArray(panel.others) && panel.others.map((o, i) => {
            const oc = typeof o.change_pct === 'number' ? o.change_pct : null;
            return (
              <div key={i} className="p-stock-other">
                <span className="p-stock-sym">{o.symbol}</span>
                <span className="p-stock-price">{o.price != null ? (o.currency || '') + o.price : '—'}</span>
                {oc != null && <span className={"p-stock-chg " + (oc >= 0 ? 'up' : 'down')}>{oc >= 0 ? '▲' : '▼'} {Math.abs(oc).toFixed(2)}%</span>}
              </div>
            );
          })}
          {sources}
        </div>
      </div>
    );
  }

  if (type === 'weather') {
    return (
      <div className="p-panel weather">
        <div className="p-panel-head">{panel.icon || '🌤'} {panel.location || 'Weather'}<span className="p-panel-tag">{isFresh ? 'now' : ageTag}</span></div>
        <div className="p-panel-body">
          <div className="p-weather-row">
            <span className="p-weather-temp">{panel.temp_c != null ? Math.round(panel.temp_c) + '°' : '—'}</span>
            <div>
              <div className="p-weather-cond">{panel.condition || ''}</div>
              <div className="p-weather-meta">
                {panel.feels_c != null ? 'Feels ' + Math.round(panel.feels_c) + '° · ' : ''}
                {panel.humidity != null ? panel.humidity + '% humidity · ' : ''}
                {panel.wind_kmh != null ? Math.round(panel.wind_kmh) + ' km/h wind' + (panel.wind_from ? ' ' + panel.wind_from : '') : ''}
              </div>
              {(panel.high_c != null && panel.low_c != null) && (
                <div className="p-weather-meta">{'H ' + Math.round(panel.high_c) + '° · L ' + Math.round(panel.low_c) + '°'}</div>
              )}
            </div>
          </div>
          {Array.isArray(panel.daily) && panel.daily.length > 1 && (
            <div className="p-weather-days">
              {panel.daily.slice(0, 7).map((d, i) => (
                <div key={i} className="p-weather-day" title={d.condition || ''}>
                  <span>{i === 0 ? 'Today' : (d.day || '')}</span>
                  <span className="ico">{d.icon || ''}</span>
                  <span>{d.high_c != null ? Math.round(d.high_c) + '°' : ''}</span>
                  <span className="lo">{d.low_c != null ? Math.round(d.low_c) + '°' : ''}</span>
                </div>
              ))}
            </div>
          )}
          {sources}
        </div>
      </div>
    );
  }

  if (type === 'clock') {
    return (
      <div className="p-panel weather">
        <div className="p-panel-head">🕒 {panel.location || 'Local time'}<span className="p-panel-tag">{panel.zone || ''}</span></div>
        <div className="p-panel-body">
          <div className="p-clock-time">{panel.time || '—'}</div>
          <div className="p-clock-date">{panel.date || ''}</div>
        </div>
      </div>
    );
  }

  if (type === 'answer') {
    const facts = Array.isArray(panel.facts) ? panel.facts.slice(0, 5) : [];
    if (!panel.headline && !facts.length) return null;
    return (
      <div className="p-panel answer">
        <div className="p-panel-head">✦ {panel.title || 'Answer'}<span className="p-panel-tag">live</span></div>
        <div className="p-panel-body">
          {panel.headline && <div className="p-answer-headline">{panel.headline}</div>}
          {facts.length > 0 && (
            <ul className="p-answer-facts">{facts.map((f, i) => <li key={i}>{f}</li>)}</ul>
          )}
          {sources}
        </div>
      </div>
    );
  }

  return null;
}

// Module-level pure utilities — stable references so ChatMessage can be memoized.
function extractRouteLabel(text) {
  if (!text) return { clean: text || '', label: '' };
  const m = String(text).match(/\s+\[([A-Z][A-Z0-9_\s]*)\]\s*$/);
  if (!m) return { clean: text, label: '' };
  return { clean: text.slice(0, m.index).trimEnd(), label: m[1].trim() };
}

function cleanBrainLabel(value) {
  let s = String(value || '').replace(/\s+/g, ' ').trim();
  if (!s) return '';
  s = s.replace(/^brain\s*:\s*/i, '').trim();
  s = s.replace(/\s+brain\s*$/i, '').trim();
  const low = s.toLowerCase();
  if (low.startsWith('core_v2:')) {
    const tail = s.split(':').slice(1).join(':').trim();
    const t = tail.toLowerCase();
    if (t === 'deep_research') return 'Deep Research';
    if (t === 'live_research') return 'Live Research';
    if (t === 'brain') return 'Brain';
    if (t === 'website') return 'Website';
    if (t === 'pc_status') return 'PC Status';
    if (t === 'weather') return 'Weather';
    if (t === 'news') return 'News';
    if (t === 'map') return 'Map';
    if (t === 'chart') return 'Chart';
    if (t === 'memory') return 'Memory';
    if (t === 'identity') return 'Identity';
    if (t === 'thinking') return 'Thinking';
    return tail || 'Core V2';
  }
  const parts = s.split('·').map(x => x.trim()).filter(Boolean);
  if ((low.includes('nim') || low.includes('nvidia')) && parts.length > 1) return parts[parts.length - 1];
  if (low === 'nim' || low === 'nvidia nim' || low === 'nvidia_nim') return 'NIM';
  if (low === 'groq') return 'Groq';
  if (low === 'auto' || low.includes('automatic')) return 'AUTO';
  return s;
}

function modeLabel(m) {
  if (m === 'deep_research') return 'Deep Research';
  return '';
}

// Thinking panel helpers — translate internal intent/detail strings to friendly labels.
const _INTENT_FRIENDLY = {
  general_knowledge: 'Consulting Syrix Brain',
  wellbeing: 'Personality reply',
  chitchat: 'Casual chat',
  greeting: 'Greeting',
  farewell: 'Farewell',
  live_research: 'Web research',
  deep_research: 'Deep research',
  live_news: 'Pulling news',
  weather: 'Checking weather',
  trade_analysis: 'Trade analysis',
  stock_price: 'Stock quote',
  map: 'Map lookup',
  chart: 'Chart data',
  identity: 'Identity check',
  memory_recall: 'Memory recall',
  memory_remember: 'Saving to memory',
  memory_forget: 'Clearing memory',
  memory_review: 'Reviewing memory',
  system_action: 'Running system action',
  pc_status: 'System metrics',
  brain_status: 'Brain status',
  reminder: 'Setting reminder',
  continuation: 'Following up',
  clarify: 'Clarifying',
};
function friendlyIntentName(raw) {
  if (!raw) return '';
  const k = String(raw).toLowerCase().replace(/[\s:\-]/g, '_').trim();
  return _INTENT_FRIENDLY[k] || String(raw).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}
function faviconFor(url) {
  try { return 'https://icons.duckduckgo.com/ip3/' + new URL(url).hostname + '.ico'; }
  catch (_) { return ''; }
}
function domainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); }
  catch (_) { return url ? String(url).slice(0, 26) : ''; }
}
// 2026-07-02 — "2 min ago" / "3 h ago" / "Jul 1" for source freshness stamps.
// Accepts ISO strings (with or without tz) and RSS-style dates; '' on failure.
function relativeTimeFrom(iso) {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  const diffS = Math.max(0, (Date.now() - t) / 1000);
  if (diffS < 45) return 'just now';
  if (diffS < 3600) return Math.round(diffS / 60) + ' min ago';
  if (diffS < 86400) return Math.round(diffS / 3600) + ' h ago';
  if (diffS < 86400 * 7) return Math.round(diffS / 86400) + ' d ago';
  try {
    return new Date(t).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch (_) { return ''; }
}
// 2026-07-02 — Perplexity-style compact source pills under live answers:
// favicon + domain + freshness ("ESPN · 2 min ago"). Only rendered when the
// message carries envelope sources, so plain chat answers stay clean. Full
// detail (provider, confidence, published/retrieved) lives in ⓘ INSPECT.
function SourcePills({ sources }) {
  const list = (Array.isArray(sources) ? sources : []).filter(s => s && (s.url || s.name)).slice(0, 5);
  if (!list.length) return null;
  const openSrc = (url, name) => {
    if (!url) return;
    try {
      if (window.SyrixBridge && typeof window.SyrixBridge.open === 'function') window.SyrixBridge.open(url, { title: name });
      else window.open(url, '_blank', 'noopener');
    } catch (_) {}
  };
  return (
    <div className="p-sources">
      {list.map((s, i) => {
        const dom = domainOf(s.url);
        const fav = s.url ? faviconFor(s.url) : '';
        const stamp = relativeTimeFrom(s.published || s.retrieved_at);
        const label = (dom || String(s.name || 'source').slice(0, 24)) + (stamp ? ' · ' + stamp : '');
        const title = String(s.name || dom || 'source')
          + (s.published ? '\nPublished: ' + s.published : '')
          + (s.retrieved_at ? '\nRetrieved: ' + s.retrieved_at : '')
          + (s.provider ? '\nVia: ' + s.provider : '');
        return (
          <a key={i} className="p-source-chip p-src-pill" title={title}
             onClick={(ev) => { ev.preventDefault(); openSrc(s.url, s.name); }}>
            {fav && <img src={fav} alt="" style={{ width: 11, height: 11, borderRadius: 3, marginRight: 5 }}
                         onError={(e) => { try { e.target.style.display = 'none'; } catch (_) {} }} />}
            {label}
          </a>
        );
      })}
    </div>
  );
}
/* ── Deep analysis section (ATLAS 2026-07-03) ────────────────────────────────
   The Zenith deep pass always runs in the background for informational turns
   and attaches to the SAME message (entry.deep, patched live via the
   'syrix-chat-deep' event). The SPARK/ZENITH toggle is a VIEW preference:
   ZENITH position auto-expands this section; execution is identical. */
function DeepAnalysis({ deep, requestId }) {
  const status = String((deep && deep.status) || 'pending');
  const [open, setOpen] = uCS(() => {
    try {
      if (String((deep && deep.view_hint) || '') === 'expand') return true;
      return String(localStorage.getItem('syrix_chat_model_v1') || '').toLowerCase() === 'zenith';
    } catch (_) { return false; }
  });
  if (!deep || typeof deep !== 'object') return null;
  // Presentation V2 (2026-07-05): the chat header carries NO metadata — no
  // source counts, no confidence percentages. That evidence lives in the
  // message's inspect panel (AnswerInspector reads m.deep).
  const headline = status === 'pending' ? 'Zenith · deep analysis running…'
    : status === 'ready' ? 'Zenith · Deep Analysis'
    : status === 'insufficient_sources' ? 'Zenith · not enough verified sources'
    : 'Zenith · deep analysis unavailable';
  const isReady = status === 'ready';
  // ZENITH is the deep flagship (violet). When the deep answer is ready it is
  // the PRIMARY result — rendered as a prominent, elevated card, not a muted
  // secondary drawer.
  const cardStyle = isReady
    ? { marginTop: 9, borderRadius: 12, border: '1px solid rgba(168,130,255,0.42)',
        background: 'linear-gradient(150deg, rgba(168,130,255,0.10), rgba(139,92,246,0.04))',
        boxShadow: '0 8px 30px -12px rgba(139,92,246,0.5)', overflow: 'hidden' }
    : { marginTop: 8, borderRadius: 10, border: '1px solid rgba(168,130,255,0.24)',
        background: 'rgba(168,130,255,0.05)', overflow: 'hidden' };
  return (
    <div className={'p-deep-card status-' + status} style={cardStyle}>
      <div onClick={() => setOpen(v => !v)}
           style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 11px',
                    cursor: 'pointer', userSelect: 'none', fontSize: 12,
                    fontWeight: isReady ? 700 : 500,
                    color: isReady ? '#d7c9ff' : 'rgba(215,201,255,0.9)' }}
           title={open ? 'Collapse deep analysis' : 'Expand deep analysis'}>
        <span style={{ color: '#c4b5fd' }}>◈</span>
        <span key={headline}>{headline}</span>
        {status === 'pending' && (
          <span className="p-typing" style={{ transform: 'scale(0.8)' }}>
            <span className="p-tdot"></span><span className="p-tdot"></span><span className="p-tdot"></span>
          </span>
        )}
        <span style={{ marginLeft: 'auto', opacity: 0.7 }}>{open ? '▾' : '▸'}</span>
      </div>
      {open && (
        <div style={{ padding: '2px 12px 11px', fontSize: isReady ? 13.5 : 13 }}>
          {status === 'pending' && (
            typeof window !== 'undefined' && typeof window.SyrixZenithPanel === 'function'
              ? <window.SyrixZenithPanel working={true}
                  sinceTs={Number(deep.ts || (Date.now() / 1000) - 120)} />
              : <div style={{ opacity: 0.75 }}>Reading full articles and cross-checking claims…</div>
          )}
          {status === 'ready' && (
            /* Presentation V2: the deep answer renders as a premium body only.
               Sources, telemetry, and lane diagnostics moved to the inspect
               panel — the chat shows the answer, inspect shows the evidence. */
            <SmartBody text={String(deep.text || '')} />
          )}
          {(status === 'insufficient_sources' || status === 'failed') && (
            <div style={{ opacity: 0.8 }}>
              {String(deep.text || 'Deep analysis could not complete — the fast answer above stands.')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
function searchProviderFrom(label) {
  const m = String(label || '').match(/searching\s+(.+)/i);
  return m ? m[1].trim() : '';
}
function activeStepHeader(steps) {
  if (!steps.length) return 'SYRIX is thinking…';
  const last = steps[steps.length - 1];
  if (last.kind === 'synthesis' && last.detail) return friendlyIntentName(last.detail);
  if (last.kind === 'synthesis') return last.label || 'Composing answer…';
  if (last.kind === 'brain_route') return last.label || 'Consulting Syrix Brain';
  if (last.kind === 'search') {
    const p = searchProviderFrom(last.label);
    return p ? 'Searching ' + p + '…' : (last.label || 'Searching the web…');
  }
  if (last.kind === 'source') return 'Reading ' + (domainOf(last.url) || 'source') + '…';
  if (last.kind === 'tool_call') return last.label || 'Running tool…';
  if (last.kind === 'memory') return last.label || 'Recalling memory…';
  if (last.kind === 'done') return last.label || 'Done';
  if (last.kind === 'error') return last.label || 'Something failed';
  return last.label || 'SYRIX is thinking…';
}

// CAP-12 (2026-06-04): parse a pipe-delimited markdown table out of a body
// string. Returns { before, table: { header, rows }, after } when found,
// else { before: text }. Only matches a contiguous block of >=2 table lines
// separated by an optional ---|--- alignment row.
function extractFirstMarkdownTable(text) {
  if (!text || text.indexOf('|') === -1) return { before: text };
  const lines = String(text).split(/\r?\n/);
  let start = -1;
  let alignmentIdx = -1;
  let end = -1;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.indexOf('|') === -1) continue;
    if (/^\|?\s*[-:]+\s*(\|\s*[-:]+\s*)+\|?\s*$/.test(line)) {
      // Alignment row — header must be the line above.
      if (i > 0 && lines[i - 1].indexOf('|') !== -1) {
        start = i - 1;
        alignmentIdx = i;
        end = i + 1;
        while (end < lines.length && lines[end].indexOf('|') !== -1) end++;
        break;
      }
    }
  }
  if (start === -1 || alignmentIdx === -1 || end <= alignmentIdx) return { before: text };
  const parseRow = (raw) => raw.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(s => s.trim());
  const header = parseRow(lines[start]);
  const rows = [];
  for (let j = alignmentIdx + 1; j < end; j++) {
    const cells = parseRow(lines[j]);
    if (cells.some(c => c.length > 0)) rows.push(cells);
  }
  if (header.length === 0 || rows.length === 0) return { before: text };
  return {
    before: lines.slice(0, start).join('\n'),
    table: { header, rows },
    after: lines.slice(end).join('\n'),
  };
}

function MarkdownTable({ header, rows }) {
  return (
    <table className="p-md-table">
      <thead>
        <tr>{header.map((h, i) => <th key={i}>{h}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
        ))}
      </tbody>
    </table>
  );
}

// 2026-06-21 — Full markdown rendering for SYRIX answers (chat-app
// style). Uses the locally-vendored `marked` + `DOMPurify` (loaded in
// index.html, offline-safe). Configured once: GFM tables/lists, soft line
// breaks, links forced to open externally. Falls back to plain text when the
// libs aren't present so the bubble never breaks.
let _mdConfigured = false;
function _ensureMarkdownConfig() {
  if (_mdConfigured) return;
  _mdConfigured = true;
  try {
    if (window.marked && window.marked.setOptions) {
      window.marked.setOptions({ gfm: true, breaks: true, headerIds: false, mangle: false });
    }
    if (window.DOMPurify && window.DOMPurify.addHook) {
      window.DOMPurify.addHook('afterSanitizeAttributes', function (node) {
        if (node && node.tagName === 'A') {
          node.setAttribute('target', '_blank');
          node.setAttribute('rel', 'noopener noreferrer');
        }
      });
    }
  } catch (_) {}
}

function renderMarkdownHtml(text) {
  try {
    const md = (typeof window !== 'undefined') ? window.marked : null;
    const purify = (typeof window !== 'undefined') ? window.DOMPurify : null;
    if (!md || !purify) return null;
    _ensureMarkdownConfig();
    const src = String(text == null ? '' : text);
    const raw = md.parse ? md.parse(src) : md(src);
    return purify.sanitize(raw, { ADD_ATTR: ['target', 'rel'] });
  } catch (_) { return null; }
}

// Renders an answer body as markdown when the vendored libs are available,
// otherwise plain text (CSS `white-space: pre-wrap` keeps newlines readable).
function MarkdownBody({ text }) {
  const html = renderMarkdownHtml(text);
  if (html === null) return <span>{text}</span>;
  return <div className="p-md" dangerouslySetInnerHTML={{ __html: html }} />;
}

// Presentation V2 (2026-07-05): preferred answer body — the shared smart
// renderer (typography, glass tables, code cards, step flows, pros/cons).
// Falls back to the legacy markdown body, then plain text, so an answer is
// never lost even if the renderer script fails to load.
function SmartBody({ text, streaming, stable, sources }) {
  if (typeof window !== 'undefined' && typeof window.SyrixAnswerRenderer === 'function') {
    return React.createElement(window.SyrixAnswerRenderer,
      { text: String(text == null ? '' : text), streaming: !!streaming, stable: !!stable, sources });
  }
  return <MarkdownBody text={text} />;
}

// CAP-26 (2026-06-04): map route label to a coloured status dot.
// Attachment chips. The extension drives both the glyph and the colour band,
// so a PDF and a spreadsheet are distinguishable before the filename is read —
// which is the whole job of a file-type icon.
const _FILE_TYPES = [
  [/\.pdf$/i,                 'pdf',   'PDF'],
  [/\.docx?$/i,               'doc',   'DOC'],
  [/\.xlsx?$|\.csv$/i,        'sheet', 'XLS'],
  [/\.pptx?$/i,               'slide', 'PPT'],
  [/\.(txt|md|rtf|json)$/i,   'text',  'TXT'],
];

function fileClass(name) {
  const hit = _FILE_TYPES.find(([re]) => re.test(String(name || '')));
  return hit ? hit[1] : 'text';
}

function fileIcon(name) {
  const hit = _FILE_TYPES.find(([re]) => re.test(String(name || '')));
  return hit ? hit[2] : 'FILE';
}

function fileSize(bytes) {
  const n = Number(bytes || 0);
  if (!n) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

function routeChipColor(label) {
  const u = String(label || '').toUpperCase();
  if (!u) return '';
  if (u === 'AGENT_LOOP' || u === 'DEEP_RESEARCH') return '#a78bfa';   // purple
  if (u === 'CHART') return 'rgb(var(--eg-accent-rgb))';                                 // amber
  if (u.includes('FAILED') || u.includes('TIMEOUT')) return '#ff6b6b'; // red
  if (u === 'CLARIFY' || u === 'NEEDS_SETUP') return 'rgb(var(--eg-accent-rgb))';        // yellow
  if (u === 'LIVE_RESEARCH' || u === 'WEATHER' || u === 'NEWS') return '#22d3ee'; // cyan
  return '#48c78e'; // green / success default
}

function formatChatTime(timeMs) {
  if (!timeMs || !Number.isFinite(timeMs)) return '';
  try {
    const d = new Date(timeMs);
    if (isNaN(d.getTime())) return '';
    let h = d.getHours();
    const ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12;
    if (h === 0) h = 12;
    const mins = String(d.getMinutes()).padStart(2, '0');
    return h + ':' + mins + ' ' + ampm;
  } catch (_) { return ''; }
}

function confidenceBand(c) {
  const v = Number(c);
  if (!Number.isFinite(v)) return '';
  if (v >= 0.8) return 'high';
  if (v >= 0.5) return 'med';
  return 'low';
}

// CAP-15 (2026-06-04): when a follow-up chip mentions trading/options/chart
// terms, fire it via Deep Research mode for proper source-checked analysis.
function followUpModeFor(text) {
  const low = String(text || '').toLowerCase();
  if (/\b(option(?:s)?\s+flow|deep[- ]?dive|smc|order\s+block|liquidity|"4h"|earnings|short\s+interest|institutional\s+flow|macro)/i.test(low)) {
    return 'deep_research';
  }
  return '';
}

const FEEDBACK_REASONS = [
  { key: 'wrong_info', label: 'Wrong info' },
  { key: 'misunderstood', label: "Didn't understand me" },
  { key: 'too_short', label: 'Too short' },
  { key: 'too_long', label: 'Too long' },
  { key: 'other', label: 'Other' },
];

// 2026-06-12 (WP3) — Answer inspector. Clicking a SYRIX reply reveals exactly
// how it was produced: which brain/model, the route, where it looked
// (web search / memory / tools), and the sources it read. Reads the per-message
// reasoning trace (snapshotted from the activity feed at reply time).
const _INSPECT_KIND = {
  intent:        { dot: '#7aaeff', where: '' },
  plan:          { dot: '#8e7cff', where: '' },
  brain_route:   { dot: '#16d37a', where: 'brain' },
  brain_fallback:{ dot: 'rgb(var(--eg-accent-rgb))', where: 'brain' },
  brain_restored:{ dot: '#16d37a', where: 'brain' },
  tool_call:     { dot: '#74e7d9', where: 'tool' },
  search:        { dot: '#74e7d9', where: 'web' },
  source:        { dot: '#a4d8ff', where: 'web' },
  memory:        { dot: '#c89bff', where: 'memory' },
  synthesis:     { dot: 'rgb(var(--eg-accent-rgb))', where: 'brain' },
  done:          { dot: '#16d37a', where: '' },
  error:         { dot: '#ff6b8b', where: '' },
  info:          { dot: '#9bb1c9', where: '' },
};
function AnswerInspector({ m }) {
  const steps = Array.isArray(m.researchSteps) ? m.researchSteps : [];
  const brain = cleanBrainLabel(m.provider) || (m.researchSteps || []).reduce((acc, e) =>
    acc || (String(e.kind) === 'brain_route' ? String(e.provider || '') : ''), '') || 'auto';
  // Summarise "where it looked".
  const wheres = new Set();
  steps.forEach(e => { const w = (_INSPECT_KIND[String(e.kind || '').toLowerCase()] || {}).where; if (w) wheres.add(w); });
  const whereLabel = {
    web: 'searched the web', memory: 'used your memory', tool: 'ran a tool', brain: 'reasoned with the model',
  };
  const whereSummary = Array.from(wheres).map(w => whereLabel[w] || w).join(' · ') || 'answered from the model';
  // Collect sources (from trace source rows + the message's own source list).
  // 2026-07-02 — keeps the full source object so the inspector can show
  // published/retrieved stamps, provider, and per-source confidence.
  const srcMap = new Map();
  steps.forEach(e => { if (String(e.kind) === 'source' && e.source_url) srcMap.set(e.source_url, { name: e.source_title || e.detail || e.source_url }); });
  // Presentation V2 (2026-07-05): the inspect panel is the ONLY place evidence
  // renders — merge the Zenith deep-layer sources in with the message's own.
  const deepObj = (m.deep && typeof m.deep === 'object') ? m.deep : null;
  const srcLists = [
    Array.isArray(m.sources) ? m.sources : [],
    (deepObj && Array.isArray(deepObj.sources)) ? deepObj.sources : [],
  ];
  srcLists.forEach(list => list.forEach(s => {
    if (s && s.url) srcMap.set(s.url, {
      name: s.name || s.url, published: s.published || '',
      retrieved_at: s.retrieved_at || '', provider: s.provider || '',
      confidence: s.confidence || '',
    });
  }));
  const sources = Array.from(srcMap.entries()).slice(0, 12);
  const stepRows = steps.filter(e => String(e.kind) !== 'source');
  const confRaw = (typeof m.confidence === 'number') ? m.confidence
    : (deepObj && typeof deepObj.confidence === 'number') ? deepObj.confidence : null;
  const conf = (confRaw != null) ? Math.round(confRaw * 100) : null;
  const S = {
    wrap: { marginTop: 8, padding: '10px 12px', borderRadius: 12, background: 'rgba(255,255,255,0.045)', border: '1px solid rgba(255,255,255,0.10)', fontSize: 11, lineHeight: 1.5 },
    row: { display: 'flex', gap: 8, alignItems: 'baseline', marginBottom: 3 },
    key: { color: 'rgba(180,195,215,0.6)', font: "600 9px 'JetBrains Mono'", letterSpacing: '0.08em', textTransform: 'uppercase', minWidth: 56, flexShrink: 0 },
    val: { color: 'rgba(232,240,252,0.92)' },
    steps: { marginTop: 8, borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: 8 },
    step: { display: 'flex', alignItems: 'flex-start', gap: 8, padding: '2px 0' },
    dot: { width: 7, height: 7, borderRadius: '50%', marginTop: 5, flexShrink: 0 },
    slabel: { color: 'rgba(232,240,252,0.9)', font: "500 11px 'Inter',sans-serif" },
    sdetail: { color: 'rgba(180,195,215,0.6)', font: "400 10px 'JetBrains Mono'", marginLeft: 6 },
    chip: { display: 'inline-flex', alignItems: 'center', gap: 4, maxWidth: 240, padding: '2px 8px', margin: '3px 4px 0 0', borderRadius: 999, background: 'rgba(108,198,255,0.14)', border: '1px solid rgba(108,198,255,0.4)', color: '#9dd6ff', font: "500 10px 'JetBrains Mono'", cursor: 'pointer', textDecoration: 'none', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  };
  return React.createElement('div', { className: 'eg-inspect p-answer-inspect', style: S.wrap, onClick: (e) => e.stopPropagation() },
    React.createElement('div', { style: S.row },
      React.createElement('span', { style: S.key }, 'Brain'),
      React.createElement('span', { style: S.val }, brain + (m.model ? '  ·  ' + m.model : ''))),
    (m.route_label || m.intent) && React.createElement('div', { style: S.row },
      React.createElement('span', { style: S.key }, 'Route'),
      React.createElement('span', { style: S.val }, m.route_label || m.intent)),
    React.createElement('div', { style: S.row },
      React.createElement('span', { style: S.key }, 'Did'),
      React.createElement('span', { style: S.val }, whereSummary)),
    (conf != null) && React.createElement('div', { style: S.row },
      React.createElement('span', { style: S.key }, 'Conf'),
      React.createElement('span', { style: S.val }, conf + '%')),
    /* 2026-07-05 — Web Brain telemetry (inspect-only diagnostics). Falls back
       to the Zenith deep-layer telemetry now that the chat card hides it. */
    ((m.telemetry && typeof m.telemetry === 'object') || (deepObj && deepObj.telemetry && typeof deepObj.telemetry === 'object')) && (() => {
      const t = (m.telemetry && typeof m.telemetry === 'object') ? m.telemetry : deepObj.telemetry;
      const rows = [];
      if (t.sources_found != null) rows.push(['Retrieval',
        t.sources_found + ' found · ' + (t.sources_used || 0) + ' used'
        + (t.extraction_success_pct != null ? ' · ' + t.extraction_success_pct + '% extracted' : '')]);
      if (t.verdict) rows.push(['Verify',
        String(t.verdict)
        + (t.agree_domains ? ' · ' + t.agree_domains + ' domains agree' : '')
        + (typeof t.agreement === 'number' && t.agreement > 0 ? ' · agreement ' + Math.round(t.agreement * 100) + '%' : '')
        + (t.conflicts ? ' · ' + t.conflicts + ' conflict' + (t.conflicts === 1 ? '' : 's') : '')]);
      if (t.avg_domain_trust != null || t.freshness != null) rows.push(['Quality',
        (t.avg_domain_trust != null ? 'trust ' + Math.round(t.avg_domain_trust * 100) + '%' : '')
        + (t.avg_domain_trust != null && t.freshness != null ? ' · ' : '')
        + (t.freshness != null ? 'freshness ' + Math.round(t.freshness * 100) + '% (' + (t.freshness_profile || 'static') + ')' : '')]);
      if ((t.rewrites_used || t.retries_used || (t.category && t.category !== 'general')))
        rows.push(['Effort',
          (t.category && t.category !== 'general' ? t.category + ' retriever' : 'generic')
          + (t.rewrites_used ? ' · ' + t.rewrites_used + ' rewrites' : '')
          + (t.retries_used ? ' · ' + t.retries_used + ' retries' : '')]);
      const missingLanes = (deepObj && Array.isArray(deepObj.missing_lanes)) ? deepObj.missing_lanes.filter(Boolean) : [];
      if (missingLanes.length) rows.push(['Lanes', 'no result: ' + missingLanes.slice(0, 4).join(', ')]);
      return rows.map(([k, v], i) => React.createElement('div', { key: 'tel' + i, style: S.row },
        React.createElement('span', { style: S.key }, k),
        React.createElement('span', { style: S.val }, v)));
    })(),
    stepRows.length > 0 && React.createElement('div', { style: S.steps },
      stepRows.map((e, i) => {
        const meta = _INSPECT_KIND[String(e.kind || '').toLowerCase()] || _INSPECT_KIND.info;
        return React.createElement('div', { key: i, style: S.step },
          React.createElement('span', { style: { ...S.dot, background: meta.dot } }),
          React.createElement('span', null,
            React.createElement('span', { style: S.slabel }, e.label || e.kind),
            e.detail && React.createElement('span', { style: S.sdetail }, String(e.detail).slice(0, 80))));
      })),
    sources.length > 0 && React.createElement('div', { style: { marginTop: 8 } },
      React.createElement('div', { style: { ...S.key, marginBottom: 4 } }, 'Sources'),
      sources.map(([url, sm], i) => {
        const name = (sm && sm.name) || url;
        const stamp = relativeTimeFrom((sm && (sm.published || sm.retrieved_at)) || '');
        const tip = url
          + (sm && sm.published ? '\nPublished: ' + sm.published : '')
          + (sm && sm.retrieved_at ? '\nRetrieved: ' + sm.retrieved_at : '')
          + (sm && sm.provider ? '\nVia: ' + sm.provider : '')
          + (sm && sm.confidence ? '\nSource confidence: ' + sm.confidence : '');
        return React.createElement('a', {
          key: i, href: url, target: '_blank', rel: 'noopener noreferrer', style: S.chip, title: tip,
          onClick: (ev) => {
            ev.preventDefault(); ev.stopPropagation();
            try {
              if (window.SyrixBridge && typeof window.SyrixBridge.open === 'function') window.SyrixBridge.open(url, { title: name });
              else window.open(url, '_blank', 'noopener');
            } catch (_) {}
          },
        }, String(name).slice(0, 36) + (stamp ? ' · ' + stamp : ''));
      })),
    (!stepRows.length && !sources.length) && React.createElement('div', { style: { ...S.val, opacity: 0.6, marginTop: 4 } },
      'Trace not retained for this message.'),
  );
}

const ChatMessage = React.memo(function ChatMessage({ m, onFollowUpClick }) {
  const { clean: displayText, label: parsedLabel } = extractRouteLabel(m.text);
  const routeLabel = m.route_label || parsedLabel;
  // GAP-005: thumbs feedback. One vote per message; click locks the bubble.
  const [vote, setVote] = uCS('');
  // 2026-06-12 (WP3) — click-to-inspect: show which brain answered, the route,
  // where it looked (search / memory / codebase), and the sources it read.
  const [inspectOpen, setInspectOpen] = uCS(false);
  // CAP-16 (2026-06-04): after thumbs-down, ask for a reason once.
  const [askReason, setAskReason] = uCS(false);
  const [reasonChosen, setReasonChosen] = uCS('');
  const sendFeedback = (which, reasonKey) => {
    const rid = String(m.request_id || m.id || '').trim();
    if (!rid) return;
    if (which === 'down' && !reasonKey && !askReason) {
      // First click: lock the bubble, show the reasons.
      setVote('down');
      setAskReason(true);
      return;
    }
    if (!reasonKey && vote) return;
    if (which === 'up') setVote('up');
    try {
      const B = window.SyrixBridge;
      if (B && B.send) {
        const payload = { request_id: rid, vote: which };
        if (reasonKey) payload.reason = reasonKey;
        B.send('feedback', { data: payload });
      }
    } catch (_) {}
    if (reasonKey) {
      setReasonChosen(reasonKey);
      setAskReason(false);
    }
  };
  const conf = m.confidence;
  const band = confidenceBand(conf);
  const followUps = Array.isArray(m.follow_ups) ? m.follow_ups : [];
  const chipColor = routeChipColor(routeLabel);
  const timeText = formatChatTime(m.timeMs);

  // 2026-06-09 — Grok/ChatGPT-style streaming reveal. Char-by-char, ~35 cps,
  // with punctuation pauses for cadence. Hard-capped at 6s total so even very
  // long replies finish in a reasonable time. Skipped when:
  //   • the message is already marked streamed (history replay / re-render)
  //   • the user prefers reduced motion
  //   • the role is not AI
  const rawBody = displayText || m.text || '';
  const isAi = m.role === 'ai';
  const alreadyStreamed = !!m.streamed;
  const reducedMotion = (typeof window !== 'undefined' && window.matchMedia)
    ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
    : false;
  // 2026-07-02 — the fake typewriter is now a FALLBACK only: if this reply
  // already streamed token-by-token for real (chat_token path), replaying a
  // 35-cps animation on the final text would be fake latency.
  const liveStreamed = (() => {
    try {
      const prefix = String((typeof window !== 'undefined' && window.__syrixLastStreamText) || '');
      return isAi && prefix.length > 24 && rawBody.startsWith(prefix.slice(0, 48));
    } catch (_) { return false; }
  })();
  const skipReveal = !isAi || alreadyStreamed || reducedMotion || liveStreamed;
  const [revealLen, setRevealLen] = uCS(() => (skipReveal ? (rawBody || '').length : 0));
  const revealedRef = uCR(skipReveal);
  uCE(() => {
    if (skipReveal) {
      revealedRef.current = true;
      setRevealLen((rawBody || '').length);
      return;
    }
    const total = (rawBody || '').length;
    if (total <= 0) { setRevealLen(0); return; }
    // Pacing: target ~35 chars per second, bounded between 600ms (short reply)
    // and 6000ms (very long reply) so we never sit on the animation forever.
    const baseCps = 35;
    const totalMs = Math.max(600, Math.min(6000, Math.round((total / baseCps) * 1000)));
    const stepMs = Math.max(14, totalMs / total);
    // Punctuation pauses add cadence: . ! ? get +100ms, , ; : get +50ms.
    let i = 0;
    let timer = null;
    const tick = () => {
      // Advance one char per tick for fidelity (Grok-like).
      i = Math.min(total, i + 1);
      setRevealLen(i);
      if (i >= total) {
        revealedRef.current = true;
        // Mutate the parent's message object so subsequent re-renders / theme
        // switches don't replay the animation. Safe: the renderer keys on m.id.
        try { m.streamed = true; } catch (_) {}
        return;
      }
      const lastCh = rawBody.charCodeAt(i - 1);
      let extra = 0;
      // 46='.' 33='!' 63='?' → big pause
      if (lastCh === 46 || lastCh === 33 || lastCh === 63) extra = 100;
      // 44=',' 59=';' 58=':' → small pause
      else if (lastCh === 44 || lastCh === 59 || lastCh === 58) extra = 50;
      timer = setTimeout(tick, stepMs + extra);
    };
    timer = setTimeout(tick, stepMs);
    return () => { try { clearTimeout(timer); } catch (_) {} };
  }, [m.id, skipReveal]);

  const shownBody = isAi && !skipReveal ? rawBody.slice(0, revealLen) : rawBody;
  // Once fully revealed (or skipReveal), parse markdown tables. While the
  // animation is mid-reveal we render raw text so the table doesn't flicker.
  const fullyShown = skipReveal || revealedRef.current || revealLen >= rawBody.length;
  const tableParts = isAi && fullyShown
    ? extractFirstMarkdownTable(rawBody)
    : { before: shownBody };
  return (
    <div className={"p-msg " + m.role}>
      <div className={"p-msg-av " + m.role}>{m.role === 'user' ? 'YOU' : m.role === 'system' ? 'SYS' : 'AI'}</div>
      <div className="p-msg-body">
        {m.role === 'user' && m.mode && (
          <div style={{ marginBottom: 4 }}>
            <span className="p-mode-chip"
                  style={{ fontSize: 8, padding: '2px 7px', cursor: 'default', pointerEvents: 'none' }}>
              {modeLabel(m.mode)}
            </span>
          </div>
        )}
        {/* ZENITH progressive upgrade: once the deep answer is ready, this
            fast bubble is relabelled the "quick take" — the flagship answer
            is the prominent violet card below. */}
        {isAi && m.deep && String(m.deep.status || '') === 'ready' && (
          <div style={{ marginBottom: 5, fontSize: 8.5, letterSpacing: '0.14em',
                        textTransform: 'uppercase', color: 'rgba(199,180,255,0.72)',
                        fontFamily: "'JetBrains Mono', monospace" }}>
            ⚡ Quick take
          </div>
        )}
        {isAi && m.panel && m.panel.media && typeof window !== 'undefined' && window.SyrixAnswerMedia
          && React.createElement(window.SyrixAnswerMedia.Images, { images: m.panel.media.images })}
        {isAi && fullyShown ? (
          // Fully revealed AI answer → Presentation V2 smart renderer
          // (typography, glass tables, code cards, step flows). SmartBody
          // falls back to the legacy markdown body, then the manual table
          // parser, so the answer always renders.
          (typeof window !== 'undefined' && typeof window.SyrixAnswerRenderer === 'function')
            || renderMarkdownHtml(rawBody) !== null ? (
            <SmartBody text={rawBody} stable={isAi} sources={m.sources} />
          ) : tableParts.table ? (
            <>
              {tableParts.before && <span>{tableParts.before}</span>}
              <MarkdownTable header={tableParts.table.header} rows={tableParts.table.rows} />
              {tableParts.after && <span>{tableParts.after}</span>}
            </>
          ) : (
            <span>{tableParts.before}</span>
          )
        ) : isAi && typeof window !== 'undefined' && typeof window.SyrixAnswerRenderer === 'function' ? (
          // 2026-07-20 — AI mid-reveal renders through the SAME smart renderer
          // (streaming:true) so headings/tables/lists/code build up in their
          // final structure as characters stream in; the finished answer no
          // longer rebuilds into a different layout when the reveal ends.
          <SmartBody text={shownBody} streaming stable />
        ) : (
          // Non-AI partial (or renderer unavailable) → plain text; AI partials
          // still get a light syntax strip so raw **/##/``` never flash.
          <span>{(isAi && typeof window !== 'undefined' && typeof window.SyrixStripStreamMd === 'function')
            ? window.SyrixStripStreamMd(shownBody) : shownBody}</span>
        )}
        {m.role === 'ai' && m.panel && fullyShown && <SmartPanel panel={m.panel} timeMs={m.timeMs} />}
        {isAi && fullyShown && typeof window !== 'undefined' && window.SyrixAnswerMedia && (
          <>
            {m.panel && m.panel.media && React.createElement(window.SyrixAnswerMedia.Products, { products: m.panel.media.products })}
            {React.createElement(window.SyrixAnswerMedia.Sources, { sources: m.sources })}
          </>
        )}
        {m.role === 'ai' && (
          <div className="p-msg-meta p-msg-inspect-row"
               onClick={() => setInspectOpen(o => !o)}
               title="Click to see brain, route, confidence, and sources"
               style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 6, marginTop: 5 }}>
            {/* Presentation V2 (2026-07-05): brain/model names, route chips,
                and confidence badges no longer render in chat — that is
                retrieval metadata and it lives in the inspect panel. */}
            <span>SYRIX</span>
            <span className="p-inspect-toggle" style={{ opacity: 0.7 }}>{inspectOpen ? '▾ hide' : 'ⓘ inspect'}</span>
          </div>
        )}
        {m.role === 'ai' && inspectOpen && (
          <AnswerInspector m={m} />
        )}
        {/* 2026-07-04 — source pills are INSPECT-ONLY again (user request:
            nothing link-shaped may show in the visible answer). They render
            with the inspector, alongside the full source list. */}
        {m.role === 'ai' && inspectOpen && fullyShown && <SourcePills sources={m.sources} />}
        {/* ATLAS (2026-07-03) — expandable Zenith deep-analysis layer for the
            same message; fills in live via the 'syrix-chat-deep' event. */}
        {m.role === 'ai' && fullyShown && m.deep && (
          <DeepAnalysis deep={m.deep} requestId={m.requestId || m.request_id} />
        )}
        {m.imgs && m.imgs.map((img, i) => (
          <img key={i} src={img.url} alt="" className="p-msg-img" />
        ))}
        {m.role === 'ai' && followUps.length > 0 && (
          <div className="p-followups">
            {followUps.slice(0, 3).map((q, i) => (
              <button key={i} className="p-followup-chip"
                onClick={() => onFollowUpClick && onFollowUpClick(q, followUpModeFor(q))}
                title="Send as next message">
                <span className="ico">↗</span>{q}
              </button>
            ))}
          </div>
        )}
        {m.role === 'ai' && (
          <div className="p-msg-feedback" style={{ marginTop: 6, display: 'flex', gap: 6, alignItems: 'center' }}>
            <button
              type="button"
              onClick={() => sendFeedback('up')}
              disabled={!!vote}
              title="Helpful"
              aria-label="Mark helpful"
              style={{
                background: vote === 'up' ? 'rgba(72,199,142,0.22)' : 'rgba(255,255,255,0.06)',
                border: '1px solid rgba(255,255,255,0.12)',
                color: vote === 'up' ? '#48c78e' : 'rgba(255,255,255,0.75)',
                borderRadius: 6,
                padding: '2px 8px',
                fontSize: 11,
                lineHeight: 1,
                cursor: vote ? 'default' : 'pointer',
              }}
            >▲</button>
            <button
              type="button"
              onClick={() => sendFeedback('down')}
              disabled={vote === 'down' && !askReason && !!reasonChosen}
              title="Not helpful"
              aria-label="Mark not helpful"
              style={{
                background: vote === 'down' ? 'rgba(255,107,107,0.22)' : 'rgba(255,255,255,0.06)',
                border: '1px solid rgba(255,255,255,0.12)',
                color: vote === 'down' ? '#ff6b6b' : 'rgba(255,255,255,0.75)',
                borderRadius: 6,
                padding: '2px 8px',
                fontSize: 11,
                lineHeight: 1,
                cursor: 'pointer',
              }}
            >▼</button>
            {reasonChosen && (
              <span style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.14em',
                color: 'rgba(180,210,255,0.55)', textTransform: 'uppercase' }}>
                Thanks · {(FEEDBACK_REASONS.find(r => r.key === reasonChosen) || {}).label || 'noted'}
              </span>
            )}
            {timeText && (
              <span className="p-time" style={{ marginLeft: 'auto', marginTop: 0 }}>{timeText}</span>
            )}
          </div>
        )}
        {m.role === 'ai' && askReason && !reasonChosen && (
          <div className="p-fb-reasons">
            {FEEDBACK_REASONS.map(r => (
              <button key={r.key} className="p-fb-reason"
                onClick={() => sendFeedback('down', r.key)}>
                {r.label}
              </button>
            ))}
          </div>
        )}
        {m.role !== 'ai' && timeText && (
          <div className="p-time">{timeText}</div>
        )}
      </div>
    </div>
  );
});

function SyrixChat({ open, onClose }) {
  // 'compact' = bottom-docked less-than-half-screen (default), 'side' = full right rail
  const [chatMode, setChatMode] = uCS('compact');
  const [msgs, setMsgs] = uCS([]);
  const [input, setInput] = uCS('');
  const [imgs, setImgs] = uCS([]);
  const [docs, setDocs] = uCS([]);
  const [typing, setTyping] = uCS(false);
  const [isPending, setIsPending] = uCS(false);
  const [voiceSpeaking, setVoiceSpeaking] = uCS(false);
  const endRef = uCR(null);
  // Reset to compact mode and scroll to bottom whenever panel opens.
  uCE(() => {
    if (!open) return;
    setChatMode('compact');
    setTimeout(() => {
      const node = endRef.current;
      if (node && node.parentElement) node.parentElement.scrollTop = node.parentElement.scrollHeight;
    }, 80);
  }, [open]);
  const fileRef = uCR(null);
  const taRef = uCR(null);
  const pendingSinceRef = uCR(0);
  const bridgeMetaRef = uCR({ brain: '', model: '' });
  const thinkingStartedAtRef = uCR(0);  // stuck-thinking watchdog timestamp
  const sendingRef = uCR(false);
  const lastSendRef = uCR({ key: '', ts: 0 });
  // Tracks the most-recently subscription-added AI message text so that the
  // B.chat() promise resolution does not add a second identical bubble.
  const lastSubAiTextRef = uCR('');
  const renderedResponseIdsRef = uCR(new Set());
  const finalReplyRequestIdsRef = uCR(new Set());
  const lastTypingPingRef = uCR(0);
  const pendingUiRef = uCR(false);
  const activeRequestIdRef = uCR('');
  const activeUserSentAtRef = uCR(0);
  const pendingTimeoutRef = uCR(null);
  const terminalNoticeIdsRef = uCR(new Set());
  // Watermark: number of chat_lines already processed from the bridge.
  // Only lines at index >= watermark are new and need processing.
  // 2026-05-30 — On the FIRST subscription tick we set the watermark to the
  // bridge's current line count.  That way HUD reopen / page reload starts
  // with a fresh visible chat; only NEW lines from this point forward appear.
  // Persistent memory (Obsidian, hud_chat.json) is untouched.
  const chatWatermarkRef = uCR(-1); // -1 = uninitialized
  const chatHardResetRef = uCR(false); // true = RESET button was pressed, skip history restore
  // 2026-05-30 — chat "+" action menu state.
  const [plusOpen, setPlusOpen] = uCS(false);
  const [nextMode, setNextMode] = uCS(''); // '' | 'deep_research'
  const plusMenuRef = uCR(null);
  // 2026-07-02 — SPARK / ZENITH model switcher. STICKY (a model, not a
  // one-shot mode): persisted in localStorage, synced across both shells via
  // the 'syrix-model-changed' event. This state is only the REQUEST signal —
  // the backend-extracted marker is the source of truth for what actually ran.
  const MODEL_LS_KEY = 'syrix_chat_model_v1';
  const [model, setModelState] = uCS(() => {
    try {
      const v = String(localStorage.getItem(MODEL_LS_KEY) || '').toLowerCase();
      return (v === 'zenith' || v === 'auto') ? v : 'spark';
    } catch (_) { return 'spark'; }
  });
  const [modelMenuOpen, setModelMenuOpen] = uCS(false);
  const modelMenuRef = uCR(null);
  // Model snapshot for the IN-FLIGHT turn (mid-flight switching must not
  // repaint the pending turn's thinking panel).
  const pendingModelRef = uCR('spark');
  function setModel(next) {
    const v = (next === 'zenith' || next === 'auto') ? next : 'spark';
    setModelState(v);
    try { localStorage.setItem(MODEL_LS_KEY, v); } catch (_) {}
    try { window.dispatchEvent(new CustomEvent('syrix-model-changed', { detail: { model: v, source: 'primary' } })); } catch (_) {}
    try { console.info('[SYRIX_MODEL] selected=' + v); } catch (_) {}
  }
  uCE(() => {
    const onModelChanged = (e) => {
      if (e && e.detail && e.detail.source === 'primary') return;
      const v = e && e.detail && e.detail.model;
      setModelState((v === 'zenith' || v === 'auto') ? v : 'spark');
    };
    window.addEventListener('syrix-model-changed', onModelChanged);
    return () => window.removeEventListener('syrix-model-changed', onModelChanged);
  }, []);
  uCE(() => {
    if (!modelMenuOpen) return;
    const onDoc = (e) => {
      try { if (modelMenuRef.current && !modelMenuRef.current.contains(e.target)) setModelMenuOpen(false); } catch (_) {}
    };
    const onKey = (e) => { if (e.key === 'Escape') setModelMenuOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [modelMenuOpen]);
  // 2026-05-31 — latest trace step from Deep Research / live research.
  const [latestTraceStep, setLatestTraceStep] = uCS(null);
  const [streamingText, setStreamingText] = uCS('');
  // 2026-06-09 — Grok-style step ladder. Each entry: { kind, label, detail,
  // url, title, snippet, ts, query, result_count }. Capped at 20 entries so
  // the in-flight feed can show all sources read. Source rows are clickable
  // and snapshot onto m.researchSteps when the reply lands (see subscription).
  const [traceSteps, setTraceSteps] = uCS([]);
  const traceStepsRef = uCR([]);
  uCE(() => { traceStepsRef.current = traceSteps; }, [traceSteps]);
  // 2026-07-02 — truthful stall notice (>8s with no new trace event) + live
  // elapsed-seconds counter for the thinking card.
  const [showReassurance, setShowReassurance] = uCS(false);
  const [thinkElapsedS, setThinkElapsedS] = uCS(0);
  const thinkingFirstSeenRef = uCR(0);
  // Task 12: backend is_thinking flag drives the indicator for voice commands.
  const [isBackendThinking, setIsBackendThinking] = uCS(false);
  // 2026-07-02 — real bridge connection state for honest header chrome.
  const [bridgeOnline, setBridgeOnline] = uCS(false);
  // 2026-06-09 — Pull activity-feed-driven steps (search → sources → synthesis).
  // Reuses the universal Activity Feed hook when present.
  const activityHook = (typeof window !== 'undefined' && typeof window.SyrixActivityFeedHook === 'function')
    ? window.SyrixActivityFeedHook : null;
  const activity = activityHook ? activityHook() : { events: [] };

  function chatLifecycleLog(event, requestId, reason) {
    try {
      const rid = String(requestId || activeRequestIdRef.current || '');
      let line = '[CHAT_LIFECYCLE] ' + String(event || 'event') + ' request_id=' + (rid || 'none');
      if (reason) line += ' reason=' + String(reason || '').replace(/\s+/g, '_').slice(0, 80);
      console.info(line);
    } catch (_) {}
  }

  function clearPendingTimeout() {
    try {
      if (pendingTimeoutRef.current) clearTimeout(pendingTimeoutRef.current);
    } catch (_) {}
    pendingTimeoutRef.current = null;
  }

  function markFinalReplyForRequest(requestId, source) {
    const rid = String(requestId || '').trim();
    if (!rid) return;
    if (!finalReplyRequestIdsRef.current.has(rid)) {
      finalReplyRequestIdsRef.current.add(rid);
      chatLifecycleLog('final_reply_marked', rid, source || 'unknown');
    }
  }

  function hasFinalReplyForRequest(requestId) {
    const rid = String(requestId || '').trim();
    if (!rid) return false;
    return finalReplyRequestIdsRef.current.has(rid) || renderedResponseIdsRef.current.has(rid);
  }

  function lifecycleMetaDetail(meta) {
    const m = meta && typeof meta === 'object' ? meta : {};
    const source = String(m.source || m.status_source || '').replace(/\s+/g, '_').slice(0, 48);
    const route = String(m.route || m.intent || '').replace(/\s+/g, '_').slice(0, 48);
    const action = String(m.action || m.tool || '').replace(/\s+/g, '_').slice(0, 48);
    const provider = cleanMessageProvider(m.provider || m.brain || '');
    const bits = [];
    if (route) bits.push('route=' + route);
    if (action) bits.push('action=' + action);
    if (provider) bits.push('provider=' + provider);
    if (source) bits.push('source=' + source);
    return bits.length ? ' (' + bits.join(', ') + ')' : '';
  }

  function pushTerminalNotice(kind, requestId, meta) {
    const rid = String(requestId || activeRequestIdRef.current || '').trim();
    const key = (rid || 'none') + '|' + String(kind || 'failed');
    if (terminalNoticeIdsRef.current.has(key)) return;
    terminalNoticeIdsRef.current.add(key);
    // 2026-07-02 — timeouts and empty completions are no longer swallowed:
    // a dropped request used to look identical to "SYRIX just stopped
    // thinking", which is exactly the trust-killer the reliability pass is
    // targeting. Copy stays user-level (no tracebacks, no provider debug).
    let text = '';
    if (kind === 'timeout' || kind === 'request_timeout') {
      text = 'That took too long and I stopped waiting — the provider may be slow. Try again.';
    } else if (kind === 'failed' || kind === 'request_failed') {
      text = 'Connection issue — please try again.';
    } else if (kind === 'offline') {
      text = 'SYRIX is offline. Check the backend is running.';
    } else if (kind === 'completed_empty') {
      text = 'I finished but produced no reply — retry that one.';
    }
    if (!text) return;
    pushUnique({ id: Date.now(), role: 'system', text, timeMs: Date.now(), requestId: rid, source: 'terminal_notice' });
  }

  function schedulePendingTimeout(requestId) {
    clearPendingTimeout();
    pendingTimeoutRef.current = setTimeout(() => {
      const rid = String(requestId || activeRequestIdRef.current || '').trim();
      if (!pendingUiRef.current && !pendingSinceRef.current && !sendingRef.current) return;
      chatLifecycleLog('request_timeout', rid, 'react_local_timeout');
      pushTerminalNotice('timeout', rid, { source: 'react_local_timeout' });
      if (rid) clearPendingTurn('local_timeout', rid);
      else clearPendingTurn('local_timeout_before_request_id', '', { localUnowned: true });
    }, 360000);  // 2026-06-09 — was 125s. The user explicitly forbade chat timeouts.
  }

  function clearPendingTurn(reason, requestId, options) {
    const rid = String(requestId || '').trim();
    const activeRid = String(activeRequestIdRef.current || '').trim();
    const localUnowned = !!(options && options.localUnowned);
    // Backend/subscription completions must own the exact active request. The
    // only id-less clear is an explicit local failure before dispatch assigns
    // an id; it is rejected as soon as any request has become active.
    if (localUnowned ? !!activeRid : (!rid || !activeRid || rid !== activeRid)) {
      chatLifecycleLog('thinking_clear_ignored', rid, 'request_owner_mismatch');
      return false;
    }
    const wasPending = !!(pendingUiRef.current || pendingSinceRef.current || sendingRef.current);
    clearPendingTimeout();
    pendingUiRef.current = false;
    pendingSinceRef.current = 0;
    sendingRef.current = false;
    activeRequestIdRef.current = '';
    activeUserSentAtRef.current = 0;
    setTyping(false);
    setIsPending(false);
    setStreamingText('');
    setIsBackendThinking(false);
    setLatestTraceStep(null);
    setTraceSteps([]);
    setShowReassurance(false);
    thinkingFirstSeenRef.current = 0;
    if (wasPending) chatLifecycleLog('thinking_cleared', rid, reason || 'cleared');
    return true;
  }

  function assistantCompletesPending(requestId, timeMs) {
    if (!pendingUiRef.current && !pendingSinceRef.current) return false;
    const activeRid = String(activeRequestIdRef.current || '').trim();
    const lineRid = String(requestId || '').trim();
    return !!(activeRid && lineRid && activeRid === lineRid);
  }

  function clearCompletedTurnAfterContentGrace(requestId, reason, meta) {
    const rid = String(requestId || '').trim();
    if (rid && hasFinalReplyForRequest(rid)) {
      clearPendingTurn(reason || 'completed', rid);
      return;
    }
    chatLifecycleLog('completed_grace_started', rid, (meta && meta.source) || reason || 'completed');
    setTimeout(() => {
      const activeRid = String(activeRequestIdRef.current || '').trim();
      if (!rid || !activeRid || rid !== activeRid) return;
      if (!pendingUiRef.current && !pendingSinceRef.current && !sendingRef.current) return;
      if (rid && hasFinalReplyForRequest(rid)) {
        clearPendingTurn(reason || 'completed', rid);
        return;
      }
      if (rid) {
        chatLifecycleLog('no_final_reply_confirmed', rid, (meta && meta.source) || reason || 'unknown');
        pushTerminalNotice('completed_empty', rid, meta);
      }
      clearPendingTurn(reason || 'completed', rid);
    }, 1800);
  }

  function isProgressDelayLine(text, line) {
    const B = window.SyrixBridge;
    if (B && B.isProgressAssistantMessage) {
      try { return !!B.isProgressAssistantMessage(line || text, line); } catch (_) {}
    }
    const norm = String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
    if (!norm || norm.length > 220) return false;
    return /\b(?:working on it|getting things ready|still working|one sec|1 sec|bit heavier)\b/.test(norm);
  }

  function isLikelyEarlyAckLine(text) {
    const norm = String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
    if (!norm || norm.length > 220) return false;
    return /\b(?:accepted|queued|sent|processing|received|route accepted|command accepted|still processing|still working|working on it|getting things ready)\b/.test(norm)
      || /^(?:ok|okay|ack|received|queued|sent)\.?$/.test(norm);
  }

  function isTechnicalAssistantLine(text, line) {
    const B = window.SyrixBridge;
    if (B && B.isTechnicalChatSpam) {
      try { return !!B.isTechnicalChatSpam(line || text); } catch (_) {}
    }
    return /\b(?:BRAIN:\s*(?:PUBLIC SOURCES|LOCAL_ACTION_LAYER)|LIMITEDACCESS|freshness\s+unknown|confidence\s+zero|fetched[_\s-]?at|browserfallbackfailed|nosearchresults|Source errors|Traceback \(most recent call last\)|stack trace|provider debug|raw timeout|SYRIX timed out before a reply reached the HUD)\b/i.test(String(text || ''));
  }

  function msgRole(raw) {
    // Core Stability Pass: explicit roles. Unknown / empty → 'system' (NOT
    // 'ai'), so a stray backend status line cannot impersonate an assistant
    // reply. Only 'user' lines come from the human; only 'ai' lines reach
    // the rendered "AI" bubble; 'system' lines are diagnostic-only.
    const role = String(raw || '').toLowerCase();
    if (role === 'user') return 'user';
    if (role === 'syrix' || role === 'assistant' || role === 'ai') return 'ai';
    return 'system';
  }

  function msgText(line) {
    if (!line || typeof line !== 'object') return '';
    return String(line.text || line.body || line.message || '').trim();
  }

  function msgTimeMs(line) {
    if (!line || typeof line !== 'object') return 0;
    const direct = line.ts_epoch ?? line.epoch ?? line.time_epoch;
    if (typeof direct === 'number' && Number.isFinite(direct)) return direct > 1e12 ? direct : direct * 1000;
    const raw = line.ts ?? line.timestamp ?? line.time;
    if (typeof raw === 'number' && Number.isFinite(raw)) return raw > 1e12 ? raw : raw * 1000;
    if (typeof raw === 'string' && raw.trim()) {
      const n = Number(raw);
      if (Number.isFinite(n)) return n > 1e12 ? n : n * 1000;
      const ms = Date.parse(raw);
      if (Number.isFinite(ms)) return ms;
    }
    return 0;
  }

  function msgRequestId(line) {
    if (!line || typeof line !== 'object') return '';
    return String(line.request_id || line.hud_chat_request_id || line.chat_request_id || '').trim();
  }

  function isRouteOnlyBrainLabel(value) {
    const raw = String(value || '').replace(/\s+/g, ' ').trim();
    if (!raw) return false;
    const low = raw.toLowerCase();
    if (low.startsWith('core_v2:')) {
      const tail = low.split(':').slice(1).join(':').trim();
      return !!tail && tail !== 'brain' && tail !== 'thinking';
    }
    return /^(weather|news|map|chart|website|live research|deep research|pc status|memory|identity|system action|open-meteo|rss|windows api|powerprof|vkey|identity adapter)$/i.test(raw);
  }

  function cleanMessageProvider(value) {
    if (isRouteOnlyBrainLabel(value)) return '';
    const cleaned = cleanBrainLabel(value);
    const low = cleaned.toLowerCase();
    if (!cleaned) return '';
    if (/^(groq|ollama|nim|nvidia nim|openrouter|local|brain|auto)$/i.test(cleaned)) return cleaned;
    if (/(llama|mixtral|qwen|gpt|gemma|mistral|deepseek|claude)/i.test(cleaned)) return cleaned;
    if (low.startsWith('nim ')) return cleaned;
    return '';
  }

  function sameMessage(a, b) {
    if (!a || !b || a.role !== b.role) return false;
    const arid = String(a.requestId || '').trim();
    const brid = String(b.requestId || '').trim();
    if (a.role === 'ai' && arid && brid) return arid === brid;
    const at = String(a.text || '').replace(/\s+/g, ' ').trim().toLowerCase();
    const bt = String(b.text || '').replace(/\s+/g, ' ').trim().toLowerCase();
    if (!at || at !== bt) return false;
    const am = Number(a.timeMs || 0);
    const bm = Number(b.timeMs || 0);
    if (a.role !== 'user') return !am || !bm || Math.abs(am - bm) < 60000;
    return !am || !bm || Math.abs(am - bm) < 15000;
  }

  function pushUnique(next) {
    if (!next || !String(next.text || '').trim()) return;
    // If the bridge subscription already added this AI message (tracked via
    // lastSubAiTextRef), skip to prevent a duplicate bubble.
    if (next.role === 'ai') {
      const rid = String(next.requestId || '').trim();
      if (rid && renderedResponseIdsRef.current.has(rid)) {
        try { console.info('duplicate_response_suppressed request_id=' + rid + ' source=react'); } catch (_) {}
        return;
      }
      if (rid) markFinalReplyForRequest(rid, 'push_unique_ai');
      const norm = String(next.text || '').replace(/\s+/g, ' ').trim().toLowerCase();
      if (norm && norm === lastSubAiTextRef.current) {
        lastSubAiTextRef.current = '';
        return;
      }
    }
    setMsgs(prev => {
      if (prev.some(m => sameMessage(m, next))) return prev;
      if (next.role === 'ai' && next.requestId) {
        renderedResponseIdsRef.current.add(String(next.requestId));
        markFinalReplyForRequest(next.requestId, 'push_unique_rendered');
      }
      return [...prev, next];
    });
  }

  const turnActive = isPending || voiceSpeaking || isBackendThinking;

  uCE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    const unsub = window.SyrixBridge.subscribe((snap) => {
      bridgeMetaRef.current = { brain: snap.brain || '', brain_label: snap.brain_label || snap.brain || '', model: snap.model || '' };
      const speaking = String(snap.state || '').toUpperCase() === 'SPEAKING';
      setVoiceSpeaking(speaking);
      // 2026-06-08 — Stuck-thinking watchdog. The user reported SYRIX
      // sometimes spinning forever on "thinking" with no reply. If the
      // backend never clears is_thinking (e.g. WS message dropped, backend
      // crashed mid-turn, dispatch hung past timeout), force it locally
      // after 75s of continuous "true" with no chat advance. The next
      // backend update will re-enable it if work is genuinely ongoing.
      const _thinkingTrue = snap.is_thinking === true;
      if (_thinkingTrue) {
        if (!thinkingStartedAtRef.current) thinkingStartedAtRef.current = Date.now();
        // 2026-06-09 — Raised from 75s → 240s. The user explicitly demanded
        // the thinking bubble stay alive until the answer lands. Only clear
        // as a last-resort watchdog if the backend has been "thinking" for
        // 4 full minutes — almost certainly a wedged dispatch at that point.
        if ((Date.now() - thinkingStartedAtRef.current) > 240000) {
          try { console.warn('[CHAT_LIFECYCLE] stuck_thinking_watchdog_clear'); } catch (_) {}
          setIsBackendThinking(false);
          thinkingStartedAtRef.current = 0;
        } else {
          setIsBackendThinking(true);
        }
      } else {
        thinkingStartedAtRef.current = 0;
        setIsBackendThinking(false);
      }
      const allLines = Array.isArray(snap.chat_lines) ? snap.chat_lines : [];
      // Watermark: only process NEW lines since last subscription call.
      // This prevents O(n²) sameMessage scans and stops dedup from silencing
      // repeated-text replies (e.g. SYRIX says "OK" twice in different turns).
      // First tick after fresh HUD reopen: skip historical lines so the visible
      // chat starts empty.  Persistent memory (Obsidian, hud_chat.json) is not
      // touched — only the renderer's visible message list resets.
      if (chatWatermarkRef.current < 0 || chatHardResetRef.current) {
        const wasHardReset = chatHardResetRef.current;
        chatHardResetRef.current = false;
        chatWatermarkRef.current = allLines.length;
        if (wasHardReset) return; // skip history restore — user wants a clean slate
        try { console.info('[CHAT_LIFECYCLE] visible_chat_initialised baseline=' + allLines.length); } catch (_) {}
        // Restore the last 20 messages from the hello-payload history so the
        // chat panel is not blank after every restart or HUD reconnect.
        const history = Array.isArray(snap.chat_history) ? snap.chat_history : [];
        if (history.length) {
          const restored = [];
          history.forEach(line => {
            const text = msgText(line);
            if (!text) return;
            const role = msgRole(line.role);
            if (role === 'system') return;
            if (role === 'ai' && isTechnicalAssistantLine(text, line)) return;
            const tms = msgTimeMs(line);
            const requestId = msgRequestId(line);
            const { clean: subCleanText, label: subParsedLabel } = extractRouteLabel(text);
            restored.push({
              id: 'hist-' + (_chatMsgSeq++),
              role,
              text: subCleanText || text,
              timeMs: tms,
              requestId,
              request_id: requestId,
              provider: cleanMessageProvider(line.provider || line.brain || ''),
              model: line.model || line.model_used || '',
              route_label: String(line.route_label || line.label || subParsedLabel || '').trim(),
              sources: Array.isArray(line.sources) ? line.sources : [],
              follow_ups: Array.isArray(line.follow_ups) ? line.follow_ups : [],
              confidence: (typeof line.confidence === 'number') ? line.confidence : null,
              panel: (line.panel && typeof line.panel === 'object') ? line.panel : null,
              deep: (line.deep && typeof line.deep === 'object') ? line.deep : null,
              telemetry: (line.telemetry && typeof line.telemetry === 'object') ? line.telemetry : null,
            });
          });
          if (restored.length) {
            setMsgs(restored);
            try { console.info('[CHAT_LIFECYCLE] history_restored count=' + restored.length); } catch (_) {}
          }
        }
        return;
      }
      const wm = chatWatermarkRef.current;
      const newLines = allLines.slice(wm);
      if (!newLines.length) return;
      chatWatermarkRef.current = allLines.length;
      newLines.forEach(line => {
        const text = msgText(line);
        if (!text) return;
        const role = msgRole(line.role);
        if (role !== 'ai') return;
        if (isProgressDelayLine(text, line)) return;
        if (isTechnicalAssistantLine(text, line)) return;
        const requestId = msgRequestId(line);
        const tms = msgTimeMs(line);
        markFinalReplyForRequest(requestId, 'subscription_ai_line');
        if (assistantCompletesPending(requestId, tms)) {
          chatLifecycleLog('assistant_reply_received', requestId, 'subscription');
          clearPendingTurn('assistant_reply_received', requestId);
        }
      });
      setMsgs(prev => {
        let out = prev;
        newLines.forEach(line => {
          const text = msgText(line);
          if (!text) return;
          const role = msgRole(line.role);
          // Skip internal system events and user echoes — user bubbles are
          // rendered immediately at send-time (optimistic UI).
          if (role === 'system') return;
          if (role === 'user') return;
          if (role === 'ai' && isTechnicalAssistantLine(text, line)) return;
          const tms = msgTimeMs(line);
          const requestId = msgRequestId(line);
          if (role === 'ai' && requestId && renderedResponseIdsRef.current.has(requestId)) {
            try { console.info('duplicate_response_suppressed request_id=' + requestId + ' source=react_subscription'); } catch (_) {}
            return;
          }
          const { clean: subCleanText, label: subParsedLabel } = extractRouteLabel(text);
          // 2026-06-12 (WP3) — snapshot the per-request reasoning trace onto the
          // message so clicking it later shows exactly what SYRIX did: which
          // brain, what route, where it looked (search / memory / codebase),
          // and which sources it read. Pulled from the live activity feed.
          let _traceForMsg = [];
          try {
            const _af = (snap.raw && Array.isArray(snap.raw.activity_feed)) ? snap.raw.activity_feed : [];
            if (requestId) {
              _traceForMsg = _af.filter(e => e && String(e.request_id || '') === String(requestId));
            }
            if (!_traceForMsg.length && Array.isArray(traceStepsRef.current)) {
              _traceForMsg = traceStepsRef.current.slice();
            }
          } catch (_) { _traceForMsg = []; }
          const next = {
            id: 'sub-' + (_chatMsgSeq++),
            role,
            text: subCleanText || text,
            timeMs: tms,
            requestId,
            request_id: requestId,
            provider: cleanMessageProvider(line.provider || line.brain || ''),
            model: line.model || line.model_used || snap.model || '',
            route_label: String(line.route_label || line.label || subParsedLabel || '').trim(),
            sources: Array.isArray(line.sources) ? line.sources : [],
            follow_ups: Array.isArray(line.follow_ups) ? line.follow_ups : [],
            confidence: (typeof line.confidence === 'number') ? line.confidence : null,
            panel: (line.panel && typeof line.panel === 'object') ? line.panel : null,
            deep: (line.deep && typeof line.deep === 'object') ? line.deep : null,
            telemetry: (line.telemetry && typeof line.telemetry === 'object') ? line.telemetry : null,
            researchSteps: _traceForMsg,
          };
          if (out === prev) out = [...prev];
          out.push(next);
          if (role === 'ai' && requestId) {
            renderedResponseIdsRef.current.add(requestId);
            markFinalReplyForRequest(requestId, 'subscription_rendered');
          }
          // Track for pushUnique dedup: if the promise also resolves with this
          // same text, skip it there since the subscription already added it.
          if (role === 'ai') lastSubAiTextRef.current = String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
        });
        return out;
      });
    });
    return () => { try { unsub && unsub(); } catch (_) {} };
  }, []);

  // Pick up the doc_id the backend assigns once it has parsed an upload.
  // Until it arrives the chip shows as still reading, because sending the
  // turn before the parse lands grounds it on nothing.
  uCE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    const unsub = window.SyrixBridge.subscribe(snap => {
      const parsed = (snap && snap.raw && Array.isArray(snap.raw.syrix_docs))
        ? snap.raw.syrix_docs : null;
      if (!parsed || !parsed.length) return;
      const byUpload = {};
      parsed.forEach(d => { const k = String(d.upload_id || ''); if (k) byUpload[k] = d; });
      setDocs(prev => {
        let changed = false;
        const next = prev.map(a => {
          const b = byUpload[a.upload_id];
          if (!b) return a;
          const doc_id = b.doc_id || a.doc_id;
          const failed = String(b.status || '') === 'failed';
          if (doc_id === a.doc_id && !failed) return a;
          changed = true;
          return failed
            ? { ...a, kind: 'error', error: b.error || 'could not be read' }
            : { ...a, doc_id, ready: !!doc_id };
        });
        return changed ? next : prev;
      });
    });
    return () => { try { unsub && unsub(); } catch (_) {} };
  }, []);

  // 2026-07-02 — real connection status for the header chip (the old
  // "ONLINE" text was hardcoded regardless of the backend state).
  // (2026-07-03: the legacy `syrix_latest_trace_step` ladder builder that
  // lived here was deleted — no Python emitter has fed it since the
  // activity_feed became the single event source. The rid-scoped
  // activity-feed builder below is the only ladder writer now.)
  uCE(() => {
    if (!window.SyrixBridge || !window.SyrixBridge.subscribe) return;
    const unsub2 = window.SyrixBridge.subscribe((snap) => {
      try { setBridgeOnline(String(snap.status || '') === 'online'); } catch (_) {}
    });
    return () => { try { unsub2 && unsub2(); } catch (_) {} };
  }, []);

  // ATLAS (2026-07-03) — live deep-layer patch: when the background Zenith
  // pass finishes, the bridge dispatches 'syrix-chat-deep' and the matching
  // message gains its deep section in place.
  uCE(() => {
    const onDeep = (e) => {
      const rid = e && e.detail ? String(e.detail.request_id || '') : '';
      const deep = e && e.detail ? e.detail.deep : null;
      if (!rid || !deep || typeof deep !== 'object') return;
      setMsgs(prev => prev.map(m => (
        m && m.role === 'ai'
          && (String(m.requestId || '') === rid || String(m.request_id || '') === rid)
          ? { ...m, deep }
          : m
      )));
    };
    window.addEventListener('syrix-chat-deep', onDeep);
    return () => window.removeEventListener('syrix-chat-deep', onDeep);
  }, []);

  // 2026-06-09 — Grok-style sources ladder. Whenever the activity feed gains
  // a `source` event during an in-flight turn, append it as a clickable row
  // in the thinking bubble.
  const lastFeedTsRef = uCR(0);
  uCE(() => {
    if (!Array.isArray(activity.events)) return;
    if (!isBackendThinking && !isPending) {
      lastFeedTsRef.current = activity.events.length
        ? Number(activity.events[activity.events.length - 1].ts || 0)
        : 0;
      return;
    }
    const fresh = activity.events.filter(ev => Number(ev.ts || 0) > lastFeedTsRef.current);
    if (!fresh.length) return;
    lastFeedTsRef.current = Number(fresh[fresh.length - 1].ts || lastFeedTsRef.current);
    setTraceSteps((prev) => {
      let next = prev.slice();
      fresh.forEach((ev) => {
        const kind = String(ev.kind || '').toLowerCase();
        // 2026-07-03 (Atlas rebuild) — STRICT rid scoping: every backend
        // emitter now tags its rid, so untagged events are dropped too (they
        // were the cross-turn bleed vector — a concurrent voice/proactive
        // turn's rid-less events used to render into this turn's ladder).
        const evRid = String(ev.request_id || '').trim();
        const activeRid = String(activeRequestIdRef.current || '').trim();
        if (!evRid || !activeRid || evRid !== activeRid) return;
        // 'done'/'error' included so the ladder truthfully closes with
        // "Answer ready · N ms" / the real failure instead of just vanishing.
        // Atlas pipeline kinds render as first-class rungs.
        if (!['search', 'source', 'synthesis', 'tool_call', 'brain_route', 'memory', 'done', 'error',
              'context_init', 'retrieval', 'filter_rank', 'verify', 'pack_built', 'pack_enriched', 'spark_run', 'zenith_run', 'merge_done'].includes(kind)) return;
        const label = String(ev.label || '').trim();
        const detail = String(ev.detail || '').slice(0, 200);
        const url = String(ev.source_url || '').trim();
        const title = String(ev.source_title || '').trim();
        const snippet = String((ev.extra && ev.extra.snippet) || ev.snippet || '').slice(0, 240);
        const resultCount = Number((ev.extra && ev.extra.result_count) || ev.result_count || 0) || 0;
        const provider = String(ev.provider || '').trim();
        // Dedup: same kind+label+url already at tail? skip.
        const tail = next[next.length - 1];
        if (tail && tail.kind === kind && tail.label === label && tail.url === url) return;
        next.push({
          kind, label, detail, url, title, snippet,
          provider, result_count: resultCount,
          ts: Number(ev.ts || Date.now() / 1000) * 1000,
        });
      });
      return next.length > 20 ? next.slice(-20) : next;
    });
  }, [activity.events, isBackendThinking, isPending]);

  // 2026-07-02 — truthful stall notice + live elapsed timer. The old version
  // showed a fabricated "One sec, the user…" on a bare 10s wall-clock timer even
  // while real steps were flowing. Now: the notice appears only when NO new
  // trace event has arrived for >8s (a genuine stall signal) and names the
  // last real step; the elapsed counter ticks for the whole turn.
  uCE(() => {
    const active = isPending || isBackendThinking;
    if (!active) {
      thinkingFirstSeenRef.current = 0;
      if (showReassurance) setShowReassurance(false);
      if (thinkElapsedS) setThinkElapsedS(0);
      return;
    }
    if (!thinkingFirstSeenRef.current) thinkingFirstSeenRef.current = Date.now();
    const id = setInterval(() => {
      const now = Date.now();
      setThinkElapsedS(Math.floor((now - thinkingFirstSeenRef.current) / 1000));
      const lastStepTs = traceSteps.length
        ? Number(traceSteps[traceSteps.length - 1].ts || 0)
        : thinkingFirstSeenRef.current;
      setShowReassurance(now - lastStepTs >= 8000);
    }, 1000);
    return () => clearInterval(id);
  }, [isPending, isBackendThinking, showReassurance, thinkElapsedS, traceSteps.length]);

  uCE(() => {
    function terminalMatches(requestId) {
      const rid = String(requestId || '').trim();
      const activeRid = String(activeRequestIdRef.current || '').trim();
      return !!(rid && activeRid && rid === activeRid);
    }
    function onLifecycle(ev) {
      const d = (ev && ev.detail) || {};
      const event = String(d.event || '').toLowerCase();
      const rid = String(d.request_id || d.requestId || '').trim();
      if (!terminalMatches(rid)) return;
      if (event === 'assistant_reply_received') {
        markFinalReplyForRequest(rid, 'lifecycle_assistant_reply');
        clearPendingTurn('assistant_reply_received', rid);
      } else if (event === 'request_failed' || event === 'request_timeout') {
        pushTerminalNotice(event.replace(/^request_/, ''), rid, d);
        clearPendingTurn(event.replace(/^request_/, ''), rid);
      } else if (event === 'request_completed') {
        clearCompletedTurnAfterContentGrace(rid, 'completed', d);
      } else if (event === 'request_cancelled') {
        clearPendingTurn(event.replace(/^request_/, ''), rid);
      }
    }
    function onStatus(ev) {
      const d = (ev && ev.detail) || {};
      const state = String(d.state || '').toLowerCase();
      const rid = String(d.request_id || d.requestId || '').trim();
      const source = String(d.source || '').toLowerCase();
      if (!terminalMatches(rid)) return;
      if (state === 'failed' || state === 'timeout') {
        pushTerminalNotice(state, rid, d);
        clearPendingTurn(state, rid);
      } else if (state === 'completed' || state === 'complete') {
        if (source === 'hud_reply') markFinalReplyForRequest(rid, 'chat_status_hud_reply');
        clearCompletedTurnAfterContentGrace(rid, state === 'complete' ? 'completed' : state, d);
      } else if (state === 'cancelled' || state === 'canceled') {
        clearPendingTurn(state === 'complete' ? 'completed' : state, rid);
      }
    }
    window.addEventListener('syrix-chat-lifecycle', onLifecycle);
    window.addEventListener('syrix-chat-status', onStatus);
    function onChatToken(ev) {
      const d = ev && ev.detail;
      if (!d) return;
      const rid = String(d.rid || d.request_id || '').trim();
      const activeRid = String(activeRequestIdRef.current || '').trim();
      // Streaming is conversational content, so ownership is stricter than
      // degraded terminal-status handling: both ids must be present and must
      // match. A late token or done frame from a superseded/voice/legacy turn
      // must never replace or clear the active typed-chat bubble.
      if (!rid || !activeRid || rid !== activeRid) return;
      if (d.done) { setStreamingText(''); return; }
      setStreamingText(d.text || '');
      // 2026-07-02 — remember the live-streamed text so ChatMessage can skip
      // the fake post-hoc typewriter for replies the user already watched
      // stream in for real.
      try { window.__syrixLastStreamText = d.text || ''; } catch (_) {}
    }
    window.addEventListener('syrix-chat-token', onChatToken);
    return () => {
      window.removeEventListener('syrix-chat-lifecycle', onLifecycle);
      window.removeEventListener('syrix-chat-status', onStatus);
      window.removeEventListener('syrix-chat-token', onChatToken);
    };
  }, []);

  // Hard reset: RESET button in Dynamic Island clears all visible chat messages.
  // 2026-06-09 — Also wipe every dedup ref so a subscription tick firing
  // immediately after the reset cannot resurrect previously-rendered lines or
  // silently drop the next legitimate message. Same for trace-step state and
  // the long_report panel which previously kept growing across resets.
  uCE(() => {
    function onHardReset() {
      chatHardResetRef.current = true;
      setMsgs([]);
      setTraceSteps([]);
      setLatestTraceStep(null);
      setShowReassurance(false);
      try {
        chatWatermarkRef.current = -1;
        renderedResponseIdsRef.current.clear();
        finalReplyRequestIdsRef.current.clear();
        terminalNoticeIdsRef.current.clear();
        lastSubAiTextRef.current = '';
        lastFeedTsRef.current = 0;
      } catch (_) {}
      try { window.dispatchEvent(new CustomEvent('syrix-long-report-clear', { detail: { reason: 'hard_reset' } })); } catch (_) {}
      try { console.info('[CHAT_LIFECYCLE] hard_reset_clear'); } catch (_) {}
    }
    window.addEventListener('syrix-soft-reset', onHardReset);
    return () => window.removeEventListener('syrix-soft-reset', onHardReset);
  }, []);

  // Smart auto-scroll: only scrolls to bottom when the user is already near
  // the bottom (within 140px). This lets users scroll up to read history
  // without being yanked back when new messages arrive.
  uCE(() => {
    const node = endRef.current;
    if (!node || !node.parentElement) return;
    const parent = node.parentElement;
    const distFromBottom = parent.scrollHeight - parent.scrollTop - parent.clientHeight;
    if (distFromBottom > 140) return;
    try {
      const reduced = typeof window !== 'undefined' && window.matchMedia
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (reduced) {
        parent.scrollTop = parent.scrollHeight;
      } else {
        parent.scrollTo({ top: parent.scrollHeight, behavior: 'smooth' });
      }
    } catch (_) {
      parent.scrollTop = parent.scrollHeight;
    }
  }, [msgs, typing, isPending, turnActive, isBackendThinking, traceSteps.length]);

  async function send() {
    // Core Stability Pass: ONLY user-typed text (or an attached image) is
    // dispatched. The optimistic message is explicitly role=user with a
    // 'local' source tag so the dedup pass can match it against the
    // backend echo deterministically — no more "fake message" / self-loop.
    const text = input.trim();
    // A parsed document is a reason to send on its own, the same as an image.
    const readyDocIds = docs.filter(d => d.kind === 'file' && d.doc_id)
                            .map(d => d.doc_id);
    if (!text && imgs.length === 0 && readyDocIds.length === 0) return;
    if (sendingRef.current) return;
    const now = Date.now();
    const imgKey = imgs.map(im => [im && im.mime, String((im && im.b64) || '').slice(0, 48)].join(':')).join('|');
    const sendKey = (text.replace(/\s+/g, ' ').trim().toLowerCase() + '|' + imgKey).slice(0, 520);
    if (sendKey && lastSendRef.current.key === sendKey && (now - lastSendRef.current.ts) < 1500) return;
    sendingRef.current = true;
    lastSendRef.current = { key: sendKey, ts: now };
    const id = now;
    const snapshot = [...imgs];
    // 2026-05-31 — capture mode BEFORE it resets so the bubble can show the chip.
    const modeSnapshot = String(nextMode || '').trim();
    // 2026-06-09 — Clear the previous long_report panel so it does NOT linger
    // across turns ("bubble keeps growing"). The host app dock listens for
    // this event and resets its rendered report state.
    try { window.dispatchEvent(new CustomEvent('syrix-long-report-clear', { detail: { reason: 'new_user_message' } })); } catch (_) {}
    // Reset the in-flight trace ladder so research events from the previous
    // turn don't bleed into this one.
    setTraceSteps([]);
    setLatestTraceStep(null);
    setStreamingText('');
    setMsgs(m => [...m, { id, role: 'user', text, imgs: snapshot, timeMs: id, source: 'local', mode: modeSnapshot }]);
    setInput('');
    setImgs([]);
    // An attachment still being parsed is kept: dropping it here is what made
    // a file look sent when the turn had not been grounded on it.
    setDocs(a => a.filter(d => d.kind === 'file' && !d.doc_id));
    if (taRef.current) { taRef.current.style.height = 'auto'; }
    // Always scroll to bottom when user sends — they want to see the reply.
    setTimeout(() => {
      const node = endRef.current;
      if (node && node.parentElement) node.parentElement.scrollTop = node.parentElement.scrollHeight;
    }, 20);
    pendingSinceRef.current = id;
    activeUserSentAtRef.current = id;
    activeRequestIdRef.current = '';
    pendingUiRef.current = true;
    setTyping(true);
    setIsPending(true);
    chatLifecycleLog('request_started', '', 'hud_send');
    chatLifecycleLog('thinking_shown', '', 'hud_send');
    schedulePendingTimeout('');
    // 2026-05-30 — Forward the user-selected mode for this turn (if any) to
    // the bridge so the runtime spine can route to Deep Research. Modes are
    // one-shot: reset after dispatch.
    const modeForTurn = String(nextMode || '').trim();
    if (modeForTurn) {
      try { console.info('[CHAT_LIFECYCLE] mode_dispatched mode=' + modeForTurn); } catch (_) {}
    }
    // 2026-07-02 — snapshot the sticky model for THIS turn (mid-flight
    // switching must not confuse the pending turn's thinking panel).
    const modelForTurn = (model === 'zenith' || model === 'auto') ? model : 'spark';
    pendingModelRef.current = modelForTurn;
    if (modelForTurn === 'zenith') {
      try { console.info('[CHAT_LIFECYCLE] model_dispatched model=zenith'); } catch (_) {}
    }
    let bridgeRequestId = '';
    try {
      const chatPromise = (window.SyrixBridge && window.SyrixBridge.chat)
        ? window.SyrixBridge.chat({ text, images: snapshot, docIds: readyDocIds,
                                    mode: modeForTurn, model: modelForTurn })
        : Promise.resolve('Bridge unavailable.');
      // One-shot mode: reset immediately so the next message is normal chat.
      if (modeForTurn) setNextMode('');
      bridgeRequestId = chatPromise && chatPromise.requestId ? String(chatPromise.requestId) : '';
      if (bridgeRequestId) {
        activeRequestIdRef.current = bridgeRequestId;
        schedulePendingTimeout(bridgeRequestId);
      }
      const reply = await chatPromise;
      const activeRidAtCompletion = String(activeRequestIdRef.current || '').trim();
      const stillOwnsTurn = !!(bridgeRequestId && activeRidAtCompletion === bridgeRequestId);
      if (reply && isProgressDelayLine(reply)) {
        chatLifecycleLog('progress_reply_ignored', bridgeRequestId, 'promise_progress');
      } else if (reply && isLikelyEarlyAckLine(reply)) {
        chatLifecycleLog('promise_ack_ignored', bridgeRequestId, 'promise_ack');
      } else if (reply && !isTechnicalAssistantLine(reply) && stillOwnsTurn && !hasFinalReplyForRequest(bridgeRequestId)) {
        pushUnique({ id: Date.now(), role: 'ai', text: reply, timeMs: Date.now(), requestId: bridgeRequestId, provider: '', model: '', source: 'backend' });
        markFinalReplyForRequest(bridgeRequestId, 'promise_reply');
        chatLifecycleLog('assistant_reply_received', bridgeRequestId, 'promise_reply');
        clearPendingTurn('promise_reply', bridgeRequestId);
      } else if (reply && bridgeRequestId) {
        chatLifecycleLog('promise_reply_ignored', bridgeRequestId, stillOwnsTurn ? 'already_delivered' : 'stale_owner');
      } else {
        const _emptyRid = bridgeRequestId;
        chatLifecycleLog('empty_reply', _emptyRid, 'promise_empty');
        // Empty promise reply means one of:
        //   1. Subscription already delivered the real reply (clearPendingTurn already
        //      ran, setting activeRequestIdRef back to ''). — do nothing.
        //   2. Bridge deduped the request (Promise.resolve('')). — clear silently.
        //   3. Empty text/image submission rejected by bridge. — clear silently.
        // In all cases: do NOT show a terminal failure notice. Real failures arrive
        // via syrix-chat-lifecycle / syrix-chat-status events or the 125-second timeout.
        if (!pendingUiRef.current && !pendingSinceRef.current) {
          // Already fully cleared by subscription — just reset sending flag.
          sendingRef.current = false;
        } else if (!_emptyRid) {
          // Still pending but no request_id → dedup/empty-text case; clear silently.
          clearPendingTurn('empty_reply_no_request_id', '', { localUnowned: true });
        }
        // If we have a request_id and are still pending, subscription/timeout handles it.
      }
      // Empty replies are no longer allowed to leave the visible thinking
      // state stuck. Backend/runtime failures should arrive as assistant or
      // terminal lifecycle events; the local timeout is the final safety net.
    } catch (err) {
      const msg = (err && err.message) ? err.message : 'Connection hiccup — please try again.';
      const activeRid = String(activeRequestIdRef.current || '').trim();
      if (bridgeRequestId && activeRid === bridgeRequestId) {
        pushUnique({ id: Date.now(), role: 'system', text: msg, timeMs: Date.now(), requestId: bridgeRequestId, source: 'error' });
        clearPendingTurn('error', bridgeRequestId);
      } else if (!bridgeRequestId && !activeRid) {
        pushUnique({ id: Date.now(), role: 'system', text: msg, timeMs: Date.now(), source: 'error' });
        clearPendingTurn('error_before_request_id', '', { localUnowned: true });
      } else {
        chatLifecycleLog('promise_error_ignored', bridgeRequestId, 'stale_owner');
      }
    }
  }

  function cancelTurn() {
    const B = window.SyrixBridge;
    const rid = String(activeRequestIdRef.current || '').trim();
    // Primary: cascade through cancel methods (each internally calls cancelSpeech).
    if (B && B.cancelPendingChat && rid) {
      try { B.cancelPendingChat(rid); } catch (_) {}
    } else if (B && B.cancelActiveTurn) {
      try { B.cancelActiveTurn(); } catch (_) {}
    } else if (B && B.cancelPendingChat) {
      try { B.cancelPendingChat(); } catch (_) {}
    } else if (B && B.cancelChat) {
      try { B.cancelChat(); } catch (_) {}
    }
    // Belt-and-suspenders: always also send cancel_speech directly so TTS
    // stops even when the bridge phase-detection has already cleared _pendingChat.
    if (B && B.send) {
      try { B.send('cancel_speech'); } catch (_) {}
    }
    if (B && B.cancelSpeech) {
      try { B.cancelSpeech(); } catch (_) {}
    }
    chatLifecycleLog('request_cancelled', rid, 'stop_button');
    if (rid) clearPendingTurn('cancelled', rid);
    else clearPendingTurn('cancelled_before_request_id', '', { localUnowned: true });
    setVoiceSpeaking(false);
    setIsBackendThinking(false);
  }

  // Documents ride the Document Intelligence upload the rest of the HUD
  // already uses — `upload_file`, then the backend parses and reports a
  // doc_id on hud_state.syrix_docs — rather than a second parser wired only
  // into this composer. Images still ride the turn as base64, which is what
  // the vision path expects.
  const MAX_DOC_BYTES = 8 * 1024 * 1024;
  const DOC_EXT = /\.(pdf|docx?|xlsx?|pptx?|csv|txt|md|rtf|json)$/i;

  function fileKind(f) {
    const name = String((f && f.name) || '');
    const mime = String((f && f.type) || '');
    if (mime.startsWith('image/')) return 'image';
    if (DOC_EXT.test(name)) return 'file';
    // A file the parser has no reader for is refused here rather than
    // uploaded and silently ignored, which is what made an attachment look
    // accepted and then go unmentioned in the answer.
    return mime ? 'file' : '';
  }

  function addDocFromFile(f) {
    const id = 'a' + Math.random().toString(36).slice(2, 8);
    const name = String((f && f.name) || 'attachment');
    if (f.size > MAX_DOC_BYTES) {
      setDocs(a => a.concat([{ id, name, size: f.size, kind: 'error',
                               error: 'over the 8 MB limit' }]));
      return true;
    }
    const reader = new FileReader();
    reader.onerror = () => setDocs(a => a.map(d => d.id === id
      ? { ...d, kind: 'error', error: 'could not be read' } : d));
    reader.onload = () => {
      const raw = String(reader.result || '');
      try {
        window.SyrixBridge && window.SyrixBridge.send &&
          window.SyrixBridge.send('upload_file', { data: {
            name, size: f.size, type: f.type || '', mime: f.type || '',
            upload_id: id, payload_b64: raw,
          } });
      } catch (_) {
        setDocs(a => a.map(d => d.id === id
          ? { ...d, kind: 'error', error: 'upload failed' } : d));
      }
    };
    setDocs(a => a.concat([{ id, name, size: f.size, mime: f.type || '',
                             kind: 'file', upload_id: id, doc_id: '',
                             ready: false }]));
    reader.readAsDataURL(f);
    return true;
  }

  function addAnyFile(f) {
    const kind = fileKind(f);
    if (kind === 'image') return addImageFromFile(f);
    if (kind === 'file') return addDocFromFile(f);
    return false;
  }

  function addImageFromFile(f) {
    if (!f || !String(f.type || '').startsWith('image/')) return false;
    try {
      const reader = new FileReader();
      reader.onload = ev => {
        const data = String((ev && ev.target && ev.target.result) || '');
        const parts = data.split(',');
        const b64 = parts.length > 1 ? parts[1] : '';
        if (!b64) return;
        // 2026-06-12 (WP4) — a new upload REPLACES the previous image. The
        // backend analyzes one image per message, and users expect the image
        // they just added to be the one SYRIX looks at (not a stale earlier
        // one). Removes the "didn't replace previous attachment" confusion.
        setImgs([{ b64, mime: f.type || 'image/png', url: data,
                   name: f.name || '', size: f.size || 0 }]);
      };
      reader.readAsDataURL(f);
      return true;
    } catch (_) {
      return false;
    }
  }

  function onFile(e) {
    Array.from((e && e.target && e.target.files) || []).forEach(addAnyFile);
    if (e && e.target) e.target.value = '';
  }

  function onPaste(e) {
    const dt = e && e.clipboardData;
    if (!dt) return;
    let handled = false;
    Array.from(dt.items || []).forEach(item => {
      if (!item || item.kind !== 'file') return;
      const f = item.getAsFile && item.getAsFile();
      if (addAnyFile(f)) handled = true;
    });
    if (!handled) {
      Array.from(dt.files || []).forEach(f => {
        if (addAnyFile(f)) handled = true;
      });
    }
    if (handled) e.preventDefault();
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }

  // 2026-05-30 — close the "+" menu when the user clicks anywhere else.
  uCE(() => {
    if (!plusOpen) return;
    function onDoc(ev) {
      const root = plusMenuRef.current;
      if (root && root.contains(ev.target)) return;
      setPlusOpen(false);
    }
    function onEsc(ev) {
      if (ev.key === 'Escape') setPlusOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onEsc);
    };
  }, [plusOpen]);

  function selectNextMode(mode) {
    setNextMode(mode || '');
    setPlusOpen(false);
    if (taRef.current) {
      try { taRef.current.focus(); } catch (_) {}
    }
  }

  // CAP-1/15 (2026-06-04): clicking a follow-up chip pre-fills the input
  // (and, for trading-flavoured chips, pre-selects Deep Research mode) so
  // the user can just tap-tap-Enter to chain a related question.
  function onFollowUpClick(text, mode) {
    if (!text) return;
    setInput(String(text));
    if (mode) setNextMode(mode);
    if (taRef.current) {
      try {
        taRef.current.focus();
        taRef.current.style.height = 'auto';
        taRef.current.style.height = Math.min(taRef.current.scrollHeight, 120) + 'px';
      } catch (_) {}
    }
    try { console.info('[CHAT_LIFECYCLE] followup_chip_clicked mode=' + (mode || 'default')); } catch (_) {}
  }

  function autoHeight(e) {
    setInput(e.target.value);
    const now = Date.now();
    if (now - lastTypingPingRef.current > 5000) {
      lastTypingPingRef.current = now;
      try {
        const B = window.SyrixBridge;
        if (B && B.noteUserActivity) B.noteUserActivity('typing');
      } catch (_) {}
    }
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px';
  }

  return (
    <>
      <style>{chatCSS}</style>
      <div className={"p-chat" + (open ? ' open' : '') + ' ' + (chatMode === 'compact' ? 'compact' : 'side') + (model === 'zenith' ? ' zenith' : '')}>
        <div className="p-chat-head">
          <div className="p-chat-orb"></div>
          <div>
            <div className="p-chat-label">SYRIX</div>
            {/* 2026-07-02 — honest status: bound to the real WS connection
                instead of a hardcoded "ONLINE". */}
            <div className="p-chat-sub" style={bridgeOnline ? undefined : { color: 'rgba(255,160,120,0.9)' }}>
              {bridgeOnline ? 'NEURAL ASSISTANT · ONLINE' : 'NEURAL ASSISTANT · RECONNECTING…'}
            </div>
          </div>
          <div className="p-chat-head-btns">
            {/* Minimize — compact bottom-docked panel */}
            <button
              className={"p-chat-layout-btn" + (chatMode === 'compact' ? ' active' : '')}
              onClick={() => setChatMode('compact')}
              title="Compact view">
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">
                <line x1="3" y1="13" x2="13" y2="13"/>
                <polyline points="5.5,9.5 8,12.5 10.5,9.5"/>
                <line x1="8" y1="3" x2="8" y2="12.5"/>
              </svg>
            </button>
            {/* Side panel — full-height right rail */}
            <button
              className={"p-chat-layout-btn" + (chatMode === 'side' ? ' active' : '')}
              onClick={() => setChatMode('side')}
              title="Side panel view">
              <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round">
                <rect x="1.5" y="2" width="13" height="12" rx="1.5"/>
                <line x1="10.5" y1="2" x2="10.5" y2="14"/>
              </svg>
            </button>
            <button className="p-chat-x" onClick={onClose} title="Close chat">✕</button>
          </div>
        </div>

        <div className="p-chat-msgs">
          {msgs.map(m => <ChatMessage key={m.id} m={m} onFollowUpClick={onFollowUpClick} />)}
          {/* 2026-07-02 — REAL token streaming. The old `!isPending` gate
              suppressed the live bubble for exactly the turns that stream
              (panel sends set isPending=true), so users only ever saw the
              fake post-hoc typewriter. Streamed text now renders whenever
              tokens are arriving, and the thinking card yields to it. */}
          {streamingText && (
            <div className="p-msg ai" style={{ opacity: 0.92 }}>
              <div className="p-msg-av ai">AI</div>
              <div className="p-msg-body">
                {/* 2026-07-20 — live token stream renders through the SAME
                    smart renderer (streaming:true) so structure builds up in
                    its final form and the hand-off to the committed message
                    doesn't rebuild the layout. Plain pre-wrap only when the
                    renderer is unavailable. */}
                <div style={{ wordBreak: 'break-word' }}>
                  {typeof window !== 'undefined' && typeof window.SyrixAnswerRenderer === 'function'
                    ? <SmartBody text={streamingText} streaming stable />
                    : <span style={{ whiteSpace: 'pre-wrap' }}>{streamingText}</span>}
                  <span style={{ display: 'inline-block', width: 8, height: 12, background: 'rgba(120,180,255,0.7)', marginLeft: 2, borderRadius: 1, animation: 'p-blink 0.9s step-end infinite', verticalAlign: 'middle' }} />
                </div>
              </div>
            </div>
          )}
          {(isPending || isBackendThinking) && !streamingText
            && pendingModelRef.current === 'zenith'
            && typeof window !== 'undefined' && typeof window.SyrixZenithPanel === 'function' && (
            /* ZENITH turn — sub-agent deployment panel replaces the ladder. */
            <window.SyrixZenithPanel working={true} sinceTs={activeUserSentAtRef.current / 1000} />
          )}
          {(isPending || isBackendThinking) && !streamingText
            && !(pendingModelRef.current === 'zenith'
                 && typeof window !== 'undefined' && typeof window.SyrixZenithPanel === 'function') && (
            <div className="p-msg ai">
              <div className="p-msg-av ai">AI</div>
              <div className="p-msg-body">
                {/* Thinking card with live trace ladder */}
                <div className="p-thinking-card">
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: traceSteps.length ? 8 : 0 }}>
                    <div className="p-typing">
                      <span className="p-tdot"></span>
                      <span className="p-tdot"></span>
                      <span className="p-tdot"></span>
                    </div>
                    <span className="p-think-action" key={activeStepHeader(traceSteps)}>
                      {activeStepHeader(traceSteps)}
                    </span>
                    <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                      {thinkElapsedS >= 2 && (
                        <span className="p-think-count" title="Elapsed time">{thinkElapsedS}s</span>
                      )}
                      {traceSteps.length > 0 && (
                        <span className="p-think-count">{traceSteps.length}</span>
                      )}
                    </span>
                  </div>
                  {traceSteps.length > 0 && (
                    <div className="p-trace-ladder">
                      {traceSteps.map((step, i) => {
                        const isSource = step.kind === 'source' && step.url;
                        const isSearch = step.kind === 'search';
                        const isSynthesis = step.kind === 'synthesis';
                        const isBrainRoute = step.kind === 'brain_route';
                        const kindIcon = {
                          search: '⌕', source: '↗', synthesis: '◐', brain_route: '⊛',
                          tool_call: '⚙', memory: '✦', intent: '⊙', done: '●',
                          context_init: '⊕', retrieval: '⌕', filter_rank: '≡',
                          verify: '✓', pack_built: '▣', pack_enriched: '▣', spark_run: '⚡',
                          zenith_run: '◈', merge_done: '◈',
                        }[step.kind] || '◇';
                        const onClickSource = isSource
                          ? (e) => {
                              e.stopPropagation();
                              try {
                                if (window.SyrixBridge && typeof window.SyrixBridge.open === 'function') {
                                  window.SyrixBridge.open(step.url, { title: step.title || step.label });
                                } else {
                                  window.open(step.url, '_blank', 'noopener');
                                }
                              } catch (_) {}
                            }
                          : undefined;
                        const isActive = i === traceSteps.length - 1;
                        const provider = isSearch ? searchProviderFrom(step.label) : '';
                        const favUrl = isSource && step.url ? faviconFor(step.url) : '';
                        const domain = isSource && step.url ? domainOf(step.url) : '';
                        // For synthesis: translate the raw intent detail to friendly text.
                        const synthDetail = isSynthesis && step.detail ? friendlyIntentName(step.detail) : '';
                        // Label shown in the rung: for synthesis with known intent → use translated name.
                        const rungLabel = isSynthesis && synthDetail ? synthDetail : step.label;
                        return (
                          <div
                            key={`${step.ts}-${i}`}
                            className={"p-trace-rung kind-" + step.kind + (isSource ? ' clickable' : '') + (isActive ? ' active' : ' done-step')}
                            style={{ animationDelay: (i * 65) + 'ms' }}
                            onClick={onClickSource}
                            title={isSource ? step.url : (synthDetail || step.detail || step.label)}
                          >
                            <span className="p-trace-rung-bullet"></span>
                            <span className="p-trace-rung-label">{kindIcon} {rungLabel}</span>
                            {/* Source: favicon pill + domain */}
                            {isSource && domain && (
                              <span className="p-trace-fav-pill" onClick={onClickSource}>
                                {favUrl && <img src={favUrl} alt="" onError={e => { try { e.target.style.display='none'; } catch(_){} }} />}
                                {domain}
                              </span>
                            )}
                            {/* Search: provider pill */}
                            {isSearch && provider && !isActive && (
                              <span className="p-trace-search-pill">{provider}</span>
                            )}
                            {/* Non-source, non-synthesis, non-brain_route detail text */}
                            {!isSource && !isSynthesis && !isBrainRoute && step.detail && (
                              <span className="p-trace-rung-detail">{step.detail}</span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {traceSteps.length === 0 && latestTraceStep && (
                    <div className="p-trace-step" key={latestTraceStep}>{latestTraceStep}</div>
                  )}
                  {showReassurance && (
                    <div className="p-reassurance">
                      {(() => {
                        const last = traceSteps.length ? traceSteps[traceSteps.length - 1] : null;
                        return last && last.label
                          ? 'Still working — last step: ' + String(last.label).slice(0, 60) + '…'
                          : 'Still working — waiting on the backend…';
                      })()}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}
          <div ref={endRef}></div>
        </div>

        <div className="p-chat-foot">
          {nextMode && (
            <div style={{ marginBottom: 6, display: 'flex', alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="p-mode-chip"
                    onClick={() => setNextMode('')}
                    title="Click to cancel this mode for the next message">
                {modeLabel(nextMode)} <span className="x">×</span>
              </span>
              <span style={{ font: '500 9px JetBrains Mono', letterSpacing: '0.16em', color: 'rgba(180,210,255,0.55)' }}>
                NEXT MESSAGE
              </span>
            </div>
          )}
          {/* Attachments sit INSIDE the composer, above the input row and
              below the transcript. Previously a bare thumbnail strip was
              rendered outside `.p-chat-foot`, so it floated over the
              conversation with no filename, no size and no way to tell a
              parsed document from one still being read. */}
          {(imgs.length > 0 || docs.length > 0) && (
            <div className="p-attach-row">
              {imgs.map((img, i) => (
                <div key={'i' + i} className="p-attach-chip image" title={img.name || 'pasted image'}>
                  <span className="p-attach-thumb"><img src={img.url} alt="" /></span>
                  <span className="p-attach-meta">
                    <span className="p-attach-name">{img.name || 'Pasted image'}</span>
                    <span className="p-attach-sub">{img.size ? fileSize(img.size) : (img.mime || 'image')}</span>
                  </span>
                  <button className="p-attach-rm" title="Remove"
                          onClick={() => setImgs(a => a.filter((_, j) => j !== i))}>×</button>
                </div>
              ))}
              {docs.map(d => (
                <div key={d.id}
                     className={'p-attach-chip ' + (d.kind === 'error' ? 'bad' : d.ready ? 'ready' : 'busy')}
                     title={d.kind === 'error' ? (d.name + ' — ' + d.error) : d.name}>
                  <span className={'p-attach-icon ' + fileClass(d.name)}>{fileIcon(d.name)}</span>
                  <span className="p-attach-meta">
                    <span className="p-attach-name">{d.name}</span>
                    <span className="p-attach-sub">
                      {d.kind === 'error' ? d.error
                        : d.ready ? fileSize(d.size)
                        : 'reading…'}
                    </span>
                  </span>
                  <button className="p-attach-rm" title="Remove"
                          onClick={() => setDocs(a => a.filter(x => x.id !== d.id))}>×</button>
                </div>
              ))}
            </div>
          )}
          <div className="p-input-row" style={{ position: 'relative' }}>
            {/* ATLAS (2026-07-03) — the SPARK/ZENITH switcher is now a VIEW
                preference: both engines always run over the same fact pack;
                ZENITH position auto-expands the deep-analysis layer. */}
            <div ref={modelMenuRef} style={{ position: 'relative', alignSelf: 'flex-end' }}>
              <button className={"p-model-btn" + (model === 'zenith' ? ' zenith' : '') + (modelMenuOpen ? ' active' : '')}
                      onClick={() => setModelMenuOpen(v => !v)}
                      title={model === 'zenith'
                        ? 'View: ZENITH — deep analysis auto-expands under every answer. Click to switch.'
                        : model === 'auto'
                          ? 'View: AUTO — SYRIX picks SPARK or ZENITH per question. Click to switch.'
                          : 'View: SPARK — fast answer first, deep analysis collapsed. Click to switch.'}>
                {model === 'zenith' ? '◈ ZENITH' : model === 'auto' ? '✦ AUTO' : '⚡ SPARK'}
              </button>
              {modelMenuOpen && (
                <div className="p-model-menu" role="menu">
                  <button className={"p-model-menu-item" + (model === 'auto' ? ' sel' : '')}
                          onClick={() => { setModel('auto'); setModelMenuOpen(false); }}>
                    <span className="mi-name">✦ AUTO</span>
                    <span className="mi-desc">picks SPARK or ZENITH per question</span>
                  </button>
                  <button className={"p-model-menu-item" + (model === 'spark' ? ' sel' : '')}
                          onClick={() => { setModel('spark'); setModelMenuOpen(false); }}>
                    <span className="mi-name">⚡ SPARK</span>
                    <span className="mi-desc">fast answer first · deep analysis collapsed</span>
                  </button>
                  <button className={"p-model-menu-item zenith" + (model === 'zenith' ? ' sel' : '')}
                          onClick={() => { setModel('zenith'); setModelMenuOpen(false); }}>
                    <span className="mi-name">◈ ZENITH</span>
                    <span className="mi-desc">auto-expand deep analysis · same grounded data</span>
                  </button>
                </div>
              )}
            </div>
            <div ref={plusMenuRef} style={{ position: 'relative', alignSelf: 'flex-end' }}>
              <button className={"p-plus-btn" + (plusOpen ? ' active' : '')}
                      onClick={() => setPlusOpen(v => !v)}
                      title="More actions">+</button>
              {plusOpen && (
                <div className="p-plus-menu" role="menu">
                  <button className="p-plus-menu-item" onClick={() => selectNextMode('deep_research')}>
                    <span className="ico">
                      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                        <circle cx="7" cy="7" r="4"/>
                        <line x1="10" y1="10" x2="13" y2="13"/>
                      </svg>
                    </span>
                    <div style={{ display: 'flex', flexDirection: 'column' }}>
                      <span>Deep Research</span>
                      <span className="meta">live source-checked</span>
                    </div>
                  </button>
                </div>
              )}
            </div>
            <textarea ref={taRef} className="p-chat-ta" rows="1"
              placeholder={nextMode === 'deep_research' ? 'Ask anything to research deeply…'
                           : model === 'zenith' ? 'Ask ZENITH anything…'
                           : 'Message Syrix...'}
              value={input} onChange={autoHeight} onKeyDown={onKey} onPaste={onPaste} />
            <button className="p-up-btn" onClick={() => fileRef.current && fileRef.current.click()}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                <polyline points="17 8 12 3 7 8"/>
                <line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
            </button>
            <div className="p-input-actions">
              <button className="p-send-btn" onClick={send} title="Send message">
                SEND
              </button>
              <button className="p-stop-btn" onClick={cancelTurn} title="Stop thinking or speaking">
                STOP
              </button>
            </div>
          </div>
          <input ref={fileRef} type="file" multiple style={{ display: 'none' }}
                 accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.csv,.txt,.md,.rtf,.json"
                 onChange={onFile} />
        </div>
      </div>
    </>
  );
}

window.SyrixChat = SyrixChat;
