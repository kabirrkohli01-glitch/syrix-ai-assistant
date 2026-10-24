/* SYRIX ANSWER RENDERER — Presentation Engine V2 (2026-07-05)
 *
 * One shared component (window.SyrixAnswerRenderer) that turns a markdown
 * answer into premium UI blocks: typographic headings, glass tables, code
 * cards with copy + highlighting, step flows, pros/cons cards, fact rows,
 * callouts. The user never sees raw markdown syntax.
 *
 * CHAT = ANSWER, INSPECT = EVIDENCE: this component renders the answer only.
 * It never adds sources, confidence, or telemetry — those live exclusively
 * in the inspect panel owned by the shells.
 *
 * Dependencies: window.marked (lexer + parseInline) and window.DOMPurify,
 * both vendored locally. When either is missing the renderer degrades to
 * plain pre-wrap text so an answer is never lost.
 */
(function () {
  const { useState: srUS } = React;
  const srUC = typeof React.useContext === 'function'
    ? React.useContext
    : () => ({ ids: new Set() });
  const BrandLinkContext = typeof React.createContext === 'function'
    ? React.createContext({ ids: new Set() })
    : { Provider: ({ children }) => children };
  // Citation number → source, for turning "[2]" in the answer into a link to
  // the second source listed under it.
  const CitationContext = typeof React.createContext === 'function'
    ? React.createContext(null)
    : { Provider: ({ children }) => children };

  /* ── Restrained inline brand links ──────────────────────────────────────
     Normal chat and Supercomputer share this renderer, so this one allowlist
     gives both surfaces identical behaviour.  Plain names are linked only
     when mentioned once in the whole answer, with a maximum of four brands;
     explicit compact markdown links always keep their own destination. */
  const BRAND_LINKS = [
    { id: 'google-calendar', names: ['Google Calendar'], url: 'https://calendar.google.com/' },
    { id: 'google-drive', names: ['Google Drive'], url: 'https://drive.google.com/' },
    { id: 'interactive-brokers', names: ['Interactive Brokers', 'IBKR'], url: 'https://www.interactivebrokers.com/' },
    { id: 'microsoft-teams', names: ['Microsoft Teams'], url: 'https://teams.microsoft.com/' },
    { id: 'github', names: ['GitHub'], url: 'https://github.com/' },
    { id: 'gitlab', names: ['GitLab'], url: 'https://gitlab.com/' },
    { id: 'gmail', names: ['Gmail'], url: 'https://mail.google.com/' },
    { id: 'slack', names: ['Slack'], url: 'https://slack.com/' },
    { id: 'discord', names: ['Discord'], url: 'https://discord.com/' },
    { id: 'spotify', names: ['Spotify'], url: 'https://open.spotify.com/' },
    { id: 'notion', names: ['Notion'], url: 'https://www.notion.so/' },
    { id: 'dropbox', names: ['Dropbox'], url: 'https://www.dropbox.com/' },
    { id: 'outlook', names: ['Outlook'], url: 'https://outlook.live.com/' },
    { id: 'onedrive', names: ['OneDrive'], url: 'https://onedrive.live.com/' },
    { id: 'shopify', names: ['Shopify'], url: 'https://www.shopify.com/' },
    { id: 'coinbase', names: ['Coinbase'], url: 'https://www.coinbase.com/' },
    { id: 'stripe', names: ['Stripe'], url: 'https://stripe.com/' },
    { id: 'hostinger', names: ['Hostinger'], url: 'https://www.hostinger.com/' },
    { id: 'todoist', names: ['Todoist'], url: 'https://todoist.com/' },
    { id: 'asana', names: ['Asana'], url: 'https://asana.com/' },
    { id: 'trello', names: ['Trello'], url: 'https://trello.com/' },
    { id: 'jira', names: ['Jira'], url: 'https://www.atlassian.com/software/jira' },
    { id: 'confluence', names: ['Confluence'], url: 'https://www.atlassian.com/software/confluence' },
    { id: 'linear', names: ['Linear'], url: 'https://linear.app/' },
    { id: 'hubspot', names: ['HubSpot'], url: 'https://www.hubspot.com/' },
    { id: 'youtube', names: ['YouTube'], url: 'https://www.youtube.com/' },
    { id: 'reddit', names: ['Reddit'], url: 'https://www.reddit.com/' },
    { id: 'openai', names: ['OpenAI'], url: 'https://openai.com/' },
  ];
  const BRAND_BY_NAME = new Map();
  BRAND_LINKS.forEach((brand) => brand.names.forEach((name) => BRAND_BY_NAME.set(name.toLowerCase(), brand)));
  const BRAND_NAME_PATTERN = BRAND_LINKS.flatMap((brand) => brand.names)
    .sort((a, b) => b.length - a.length)
    .map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('|');

  function selectedBrandIds(raw) {
    // Code and already-linked labels should never trigger plain-name linking.
    const clean = String(raw || '')
      .replace(/```[\s\S]*?```/g, ' ')
      .replace(/`[^`\n]*`/g, ' ')
      .replace(/\[[^\]\n]+\]\([^)\n]+\)/g, ' ');
    // Case-sensitive on purpose: ordinary words such as "slack", "discord",
    // "notion" and "linear" must never become accidental company links.
    const re = new RegExp('\\b(' + BRAND_NAME_PATTERN + ')\\b', 'g');
    const counts = new Map(), order = [];
    let match;
    while ((match = re.exec(clean)) !== null) {
      const brand = BRAND_BY_NAME.get(match[1].toLowerCase());
      if (!brand) continue;
      if (!counts.has(brand.id)) order.push(brand.id);
      counts.set(brand.id, (counts.get(brand.id) || 0) + 1);
    }
    return new Set(order.filter((id) => counts.get(id) === 1).slice(0, 4));
  }

  /* ── Styles — injected once, `sxr-` prefixed, theme-neutral dark glass ── */
  const SXR_CSS = `
.sxr-root { position: relative; white-space: normal; word-break: break-word; font: 400 14px/1.72 'Inter', system-ui, sans-serif; color: rgba(236,243,252,0.93); }
/* 2026-07-11 — the answer-level corner Copy button moved into the message
   action row (next to ⓘ inspect, hover-only) in the aurora shell. Code
   blocks keep their own per-block copy. */
/* Legacy streaming placeholder — kept as the marked/DOMPurify-missing fallback
   only. The live streaming path now renders through the SAME rich pipeline as
   the finished answer (streamGuard + renderTokens), so there is no plain→rich
   swap to visually rebuild the answer when generation ends. */
.sxr-stream { display: block; white-space: pre-wrap; word-break: break-word; font: 400 14px/1.72 'Inter', system-ui, sans-serif; color: rgba(236,243,252,0.93); }
.sxr-root > :first-child { margin-top: 0 !important; }
.sxr-root > :last-child { margin-bottom: 0 !important; }
.sxr-p { margin: 0 0 12px; }
.sxr-lead { font-size: 15px; line-height: 1.7; color: rgba(244,248,255,0.97); }
.sxr-h { margin: 20px 0 10px; font-family: 'Inter', system-ui, sans-serif; color: rgba(248,250,255,0.98); line-height: 1.32; }
.sxr-h.d1 { font-size: 19px; font-weight: 700; letter-spacing: -0.01em; }
.sxr-h.d2 { font-size: 16.5px; font-weight: 700; letter-spacing: -0.005em; padding-bottom: 7px; border-bottom: 1px solid rgba(255,255,255,0.08); }
.sxr-h.d3 { font-size: 14.5px; font-weight: 650; color: rgba(var(--eg-accent-rgb),0.95); letter-spacing: 0.01em; }
.sxr-h.d4 { font-size: 13px; font-weight: 650; letter-spacing: 0.06em; text-transform: uppercase; color: rgba(200,214,232,0.75); }
.sxr-root strong { color: rgba(250,252,255,0.99); font-weight: 650; }
.sxr-root em { color: rgba(228,238,250,0.95); }
.sxr-root code.sxr-ic { font: 500 12px 'JetBrains Mono', monospace; background: rgba(var(--sxr-accent-rgb, 255,183,43),0.09); border: 1px solid rgba(var(--sxr-accent-rgb, 255,183,43),0.16); border-radius: 5px; padding: 1px 6px; color: rgb(var(--eg-accent-rgb)); }
.sxr-root a { color: #86c9ff; text-decoration: none; border-bottom: 1px solid rgba(134,201,255,0.3); }
.sxr-root a:hover { border-bottom-color: rgba(134,201,255,0.85); }
.sxr-root a.sxr-brand-link { display: inline-flex; align-items: center; gap: 4px; margin: 0 1px; padding: 0 2px; border-radius: 5px; vertical-align: -0.12em; white-space: nowrap; color: #9bd4ff; border-bottom-color: rgba(155,212,255,0.32); transition: color 140ms ease, background 140ms ease, border-color 140ms ease, transform 120ms cubic-bezier(0.23,1,0.32,1); }
.sxr-brand-link img { width: 13px; height: 13px; flex: 0 0 13px; border-radius: 3px; object-fit: contain; background: rgba(255,255,255,0.92); }
.sxr-root a.sxr-cite { display: inline-flex; align-items: center; justify-content: center; min-width: 15px; height: 15px; margin: 0 1px 0 2px; padding: 0 4px; border-radius: 999px; vertical-align: 0.28em; font: 600 9px/1 'JetBrains Mono', monospace; color: #bae6fd; background: rgba(125,211,252,0.14); border: 1px solid rgba(125,211,252,0.3); text-decoration: none; transition: background 140ms ease, color 140ms ease; }
.sxr-root a.sxr-cite:hover { background: rgba(125,211,252,0.32); color: #fff; }
.sxr-root span.sxr-cite-pending { display: inline-flex; align-items: center; justify-content: center; min-width: 15px; height: 15px; margin: 0 1px 0 2px; padding: 0 4px; border-radius: 999px; vertical-align: 0.28em; font: 600 9px/1 'JetBrains Mono', monospace; color: rgba(186,230,253,0.55); background: rgba(125,211,252,0.07); border: 1px solid rgba(125,211,252,0.16); }
.sxr-root a.sxr-brand-link:active { transform: scale(0.97); }
.sxr-root a.sxr-brand-link:focus-visible { outline: 2px solid rgba(134,201,255,0.82); outline-offset: 2px; }
@media (hover: hover) and (pointer: fine) { .sxr-root a.sxr-brand-link:hover { color: #c4e7ff; background: rgba(134,201,255,0.075); border-bottom-color: rgba(155,212,255,0.75); } }

/* Tables — rounded glass card, hover rows, generous padding */
.sxr-table-wrap { margin: 12px 0 14px; border-radius: 13px; border: 1px solid rgba(255,255,255,0.1); overflow: hidden; background: rgba(255,255,255,0.022); box-shadow: 0 6px 22px -12px rgba(0,0,0,0.5), inset 0 1px 0 rgba(255,255,255,0.045); }
.sxr-table-scroll { overflow-x: auto; }
.sxr-table-scroll::-webkit-scrollbar { height: 4px; }
.sxr-table-scroll::-webkit-scrollbar-thumb { background: rgba(var(--sxr-accent-rgb, 255,183,43),0.2); border-radius: 2px; }
.sxr-table { border-collapse: collapse; width: 100%; font: 400 12.5px/1.55 'Inter', sans-serif; }
.sxr-table th { background: rgba(var(--sxr-accent-rgb, 255,183,43),0.075); padding: 10px 14px; text-align: left; font-weight: 650; font-size: 11px; letter-spacing: 0.07em; text-transform: uppercase; color: rgba(var(--eg-accent-rgb),0.92); border-bottom: 1px solid rgba(var(--sxr-accent-rgb, 255,183,43),0.18); white-space: nowrap; }
.sxr-table td { padding: 9px 14px; color: rgba(230,240,252,0.9); border-bottom: 1px solid rgba(255,255,255,0.05); vertical-align: top; }
.sxr-table tbody tr:last-child td { border-bottom: none; }
.sxr-table tbody tr { transition: background 0.13s ease; }
.sxr-table tbody tr:hover { background: rgba(var(--sxr-accent-rgb, 255,183,43),0.05); }
.sxr-table td:first-child { font-weight: 550; color: rgba(244,248,255,0.95); }

/* Code — card with header (language badge + copy), highlight palette */
.sxr-code { margin: 12px 0 14px; max-width: 100%; border-radius: 12px; border: 1px solid rgba(255,255,255,0.1); overflow: hidden; background: rgba(4,8,16,0.72); box-shadow: 0 6px 22px -12px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.04); }
.sxr-code-head { display: flex; align-items: center; gap: 8px; padding: 7px 12px; background: rgba(255,255,255,0.035); border-bottom: 1px solid rgba(255,255,255,0.07); user-select: none; }
.sxr-code-lang { font: 650 9.5px 'JetBrains Mono', monospace; letter-spacing: 0.14em; text-transform: uppercase; color: rgba(var(--eg-accent-rgb),0.85); background: rgba(var(--sxr-accent-rgb, 255,183,43),0.1); border: 1px solid rgba(var(--sxr-accent-rgb, 255,183,43),0.2); border-radius: 999px; padding: 2px 9px; }
.sxr-code-copy { margin-left: auto; display: inline-flex; align-items: center; gap: 5px; background: none; border: 1px solid rgba(255,255,255,0.12); border-radius: 7px; color: rgba(205,218,235,0.75); font: 550 10px 'Inter', sans-serif; padding: 3px 9px; cursor: pointer; transition: all 0.14s ease; }
.sxr-code-copy:hover { color: rgb(var(--eg-accent-rgb)); border-color: rgba(var(--sxr-accent-rgb, 255,183,43),0.4); background: rgba(var(--sxr-accent-rgb, 255,183,43),0.08); }
.sxr-code-copy.done { color: #6fe3a5; border-color: rgba(111,227,165,0.4); }
.sxr-code-actions { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; }
.sxr-code-actions .sxr-code-copy { margin-left: 0; }
.sxr-code-run { display: inline-flex; align-items: center; gap: 6px; border: 1px solid rgba(var(--sxr-accent-rgb, 255,183,43),0.34); border-radius: 7px; color: rgba(var(--eg-accent-bright-rgb),0.94); background: rgba(var(--sxr-accent-rgb, 255,183,43),0.09); font: 620 10px 'Inter', sans-serif; padding: 3px 9px; cursor: pointer; transition: background 0.14s ease, border-color 0.14s ease, color 0.14s ease; }
.sxr-code-run:hover { color: #fff1cf; border-color: rgba(var(--sxr-accent-rgb, 255,183,43),0.58); background: rgba(var(--sxr-accent-rgb, 255,183,43),0.15); }
.sxr-code-run:focus-visible, .sxr-code-copy:focus-visible { outline: 2px solid rgba(134,201,255,0.8); outline-offset: 2px; }
.sxr-code-body { display: flex; overflow-x: auto; max-height: 560px; overflow-y: auto; padding: 11px 0; }
.sxr-code-body::-webkit-scrollbar { height: 4px; }
.sxr-code-body::-webkit-scrollbar-thumb { background: rgba(var(--sxr-accent-rgb, 255,183,43),0.2); border-radius: 2px; }
.sxr-code-lines { flex-shrink: 0; padding: 0 10px 0 13px; text-align: right; user-select: none; font: 400 11.5px/1.62 'JetBrains Mono', monospace; color: rgba(140,160,185,0.38); border-right: 1px solid rgba(255,255,255,0.06); }
.sxr-code-pre { margin: 0; min-width: 0; padding: 0 14px; font: 400 11.5px/1.62 'JetBrains Mono', monospace; color: rgba(226,236,250,0.92); background: none; border: none; white-space: pre; }
.sxr-preview { border-top: 1px solid rgba(255,255,255,0.08); background: rgba(2,5,10,0.86); }
.sxr-preview-bar { min-height: 34px; padding: 6px 10px 6px 12px; display: flex; align-items: center; gap: 8px; border-bottom: 1px solid rgba(255,255,255,0.07); background: rgba(255,255,255,0.025); }
.sxr-preview-status { display: inline-flex; align-items: center; gap: 7px; min-width: 0; color: rgba(196,211,229,0.72); font: 550 9.5px 'Inter', sans-serif; letter-spacing: 0.025em; }
.sxr-preview-status::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: #6fe3a5; box-shadow: 0 0 8px rgba(111,227,165,0.46); }
.sxr-preview-note { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sxr-preview-tools { margin-left: auto; display: inline-flex; align-items: center; gap: 5px; }
.sxr-preview-tool { border: 0; border-radius: 6px; padding: 3px 7px; background: transparent; color: rgba(204,218,235,0.72); font: 550 9.5px 'Inter', sans-serif; cursor: pointer; }
.sxr-preview-tool:hover { color: #eef5ff; background: rgba(255,255,255,0.07); }
.sxr-preview-tool:focus-visible { outline: 2px solid rgba(134,201,255,0.8); outline-offset: 1px; }
.sxr-preview-frame { display: block; width: 100%; height: 360px; border: 0; background: #fff; color-scheme: light; }
@media (max-width: 680px) { .sxr-preview-frame { height: 300px; } .sxr-preview-note { display: none; } }
.sxr-tok-kw { color: #c9a3ff; }
.sxr-tok-str { color: #9fe3a8; }
.sxr-tok-num { color: rgb(var(--eg-accent-rgb)); }
.sxr-tok-com { color: rgba(150,170,195,0.55); font-style: italic; }
.sxr-tok-fn { color: #8fd0ff; }

/* Step / timeline flow — numbered chips on a soft rail */
.sxr-flow { margin: 12px 0 14px; display: flex; flex-direction: column; }
.sxr-step { display: flex; gap: 12px; position: relative; padding-bottom: 14px; }
.sxr-step:last-child { padding-bottom: 2px; }
.sxr-step::before { content: ''; position: absolute; left: 12px; top: 27px; bottom: 1px; width: 2px; background: linear-gradient(180deg, rgba(var(--sxr-accent-rgb, 255,183,43),0.32), rgba(var(--sxr-accent-rgb, 255,183,43),0.05)); border-radius: 2px; }
.sxr-step:last-child::before { display: none; }
.sxr-step-n { flex-shrink: 0; width: 25px; height: 25px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font: 700 11px 'JetBrains Mono', monospace; color: rgb(var(--eg-accent-rgb)); background: rgba(var(--sxr-accent-rgb, 255,183,43),0.1); border: 1px solid rgba(var(--sxr-accent-rgb, 255,183,43),0.32); box-shadow: 0 0 12px -4px rgba(var(--sxr-accent-rgb, 255,183,43),0.4); }
.sxr-step-b { flex: 1; min-width: 0; padding-top: 2.5px; }
.sxr-step-b .sxr-p:last-child { margin-bottom: 0; }

/* Lists — elegant custom markers, no raw bullet spam */
.sxr-ul, .sxr-ol { margin: 8px 0 13px; padding: 0; list-style: none; }
.sxr-li { position: relative; padding-left: 21px; margin: 7px 0; }
.sxr-ul .sxr-li::before { content: ''; position: absolute; left: 5px; top: 0.62em; width: 6px; height: 6px; border-radius: 50%; background: rgba(var(--sxr-accent-rgb, 255,183,43),0.55); box-shadow: 0 0 6px rgba(var(--sxr-accent-rgb, 255,183,43),0.28); }
.sxr-li.good::before, .sxr-li.bad::before { display: none; }
.sxr-li .sxr-li-mark { position: absolute; left: 0; top: 0.08em; width: 16px; text-align: center; font-weight: 700; font-size: 12px; }
.sxr-li.good .sxr-li-mark { color: #6fe3a5; }
.sxr-li.bad .sxr-li-mark { color: #ff8f9d; }
.sxr-ol { counter-reset: sxr-oli; }
.sxr-ol .sxr-li { padding-left: 27px; counter-increment: sxr-oli; }
.sxr-ol .sxr-li::before { content: counter(sxr-oli); position: absolute; left: 0; top: 0.14em; width: 18px; height: 18px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font: 700 10px 'JetBrains Mono', monospace; color: rgba(var(--eg-accent-rgb),0.9); background: rgba(var(--sxr-accent-rgb, 255,183,43),0.09); border: 1px solid rgba(var(--sxr-accent-rgb, 255,183,43),0.22); }
.sxr-li > .sxr-ul, .sxr-li > .sxr-ol { margin: 5px 0 2px; }

/* Fact rows — "**Label:** value" lists become a definition grid */
.sxr-facts { margin: 10px 0 14px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.09); background: rgba(255,255,255,0.022); overflow: hidden; }
.sxr-fact { display: flex; gap: 12px; padding: 8px 13px; border-bottom: 1px solid rgba(255,255,255,0.05); }
.sxr-fact:last-child { border-bottom: none; }
.sxr-fact-k { flex-shrink: 0; width: 34%; max-width: 190px; font: 600 11px/1.6 'Inter', sans-serif; letter-spacing: 0.04em; color: rgba(var(--eg-accent-rgb),0.85); padding-top: 1px; }
.sxr-fact-v { flex: 1; min-width: 0; font-size: 13px; color: rgba(234,242,252,0.92); }

/* Pros / cons cards */
.sxr-pc { margin: 12px 0 14px; display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; }
.sxr-pc-card { border-radius: 13px; padding: 11px 14px 12px; border: 1px solid; box-shadow: inset 0 1px 0 rgba(255,255,255,0.04); }
.sxr-pc-card.pros { background: rgba(111,227,165,0.05); border-color: rgba(111,227,165,0.2); }
.sxr-pc-card.cons { background: rgba(255,124,140,0.05); border-color: rgba(255,124,140,0.2); }
.sxr-pc-title { font: 700 10.5px 'Inter', sans-serif; letter-spacing: 0.12em; text-transform: uppercase; margin-bottom: 8px; }
.sxr-pc-card.pros .sxr-pc-title { color: #6fe3a5; }
.sxr-pc-card.cons .sxr-pc-title { color: #ff8f9d; }
.sxr-pc-card .sxr-ul { margin: 0; }
.sxr-pc-card .sxr-li { margin: 5px 0; padding-left: 19px; font-size: 12.5px; }
.sxr-pc-card .sxr-li::before { display: none; }
.sxr-pc-card.pros .sxr-li .sxr-li-mark { color: #6fe3a5; }
.sxr-pc-card.cons .sxr-li .sxr-li-mark { color: #ff8f9d; }

/* Callouts (blockquotes) */
.sxr-callout { margin: 11px 0 13px; display: flex; gap: 10px; padding: 10px 13px; border-radius: 11px; background: rgba(var(--sxr-accent-rgb, 255,183,43),0.055); border: 1px solid rgba(var(--sxr-accent-rgb, 255,183,43),0.16); border-left: 3px solid rgba(var(--sxr-accent-rgb, 255,183,43),0.55); }
.sxr-callout.warn { background: rgba(255,159,90,0.06); border-color: rgba(255,159,90,0.2); border-left-color: rgba(255,159,90,0.6); }
.sxr-callout-ico { flex-shrink: 0; font-size: 13px; padding-top: 1px; }
.sxr-callout-b { flex: 1; min-width: 0; font-size: 13px; color: rgba(238,244,252,0.92); }
.sxr-callout-b .sxr-p { margin-bottom: 6px; }
.sxr-callout-b .sxr-p:last-child { margin-bottom: 0; }

/* Stat / progress bars — a list of "Label 65%" becomes labelled meters */
.sxr-stats { margin: 12px 0 14px; display: flex; flex-direction: column; gap: 9px; }
.sxr-stat { display: grid; grid-template-columns: minmax(64px, 34%) 1fr auto; align-items: center; gap: 11px; }
.sxr-stat-k { font-size: 12.5px; color: rgba(234,242,252,0.9); min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.sxr-stat-bar { position: relative; height: 7px; border-radius: 999px; background: rgba(255,255,255,0.07); overflow: hidden; }
.sxr-stat-fill { position: absolute; inset: 0 auto 0 0; border-radius: 999px; transform-origin: left; background: linear-gradient(90deg, rgba(var(--sxr-accent-rgb, 255,183,43),0.55), rgba(var(--sxr-accent-rgb, 255,183,43),0.95)); box-shadow: 0 0 10px -2px rgba(var(--sxr-accent-rgb, 255,183,43),0.5); animation: sxr-grow 0.5s cubic-bezier(0.22,1,0.36,1); }
.sxr-stat-v { font: 650 12px 'JetBrains Mono', monospace; color: rgba(var(--eg-accent-rgb),0.92); min-width: 36px; text-align: right; }
@keyframes sxr-grow { from { transform: scaleX(0); } to { transform: scaleX(1); } }

/* Ranking — medal / number chips on soft rows */
.sxr-rank { margin: 10px 0 14px; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.sxr-rank-item { display: flex; align-items: center; gap: 11px; padding: 8px 12px; border-radius: 11px; background: rgba(255,255,255,0.022); border: 1px solid rgba(255,255,255,0.06); transition: background 0.13s ease; }
.sxr-rank-item:hover { background: rgba(var(--sxr-accent-rgb, 255,183,43),0.05); }
.sxr-rank-medal { flex-shrink: 0; width: 24px; text-align: center; font-size: 15px; font-weight: 700; color: rgba(var(--eg-accent-rgb),0.85); font-variant-numeric: tabular-nums; }
.sxr-rank-body { flex: 1; min-width: 0; font-size: 13px; color: rgba(234,242,252,0.92); }

/* Timeline — dotted rail with an era / date lead */
.sxr-timeline { margin: 12px 0 14px; display: flex; flex-direction: column; }
.sxr-tl-item { display: flex; gap: 13px; position: relative; padding: 0 0 13px 0; }
.sxr-tl-item:last-child { padding-bottom: 2px; }
.sxr-tl-item::before { content: ''; position: absolute; left: 4px; top: 13px; bottom: -1px; width: 2px; background: linear-gradient(180deg, rgba(var(--sxr-accent-rgb, 255,183,43),0.3), rgba(var(--sxr-accent-rgb, 255,183,43),0.05)); }
.sxr-tl-item:last-child::before { display: none; }
.sxr-tl-dot { flex-shrink: 0; width: 10px; height: 10px; margin-top: 4px; border-radius: 50%; background: rgba(var(--sxr-accent-rgb, 255,183,43),0.9); box-shadow: 0 0 0 3px rgba(var(--sxr-accent-rgb, 255,183,43),0.14); z-index: 1; }
.sxr-tl-body { flex: 1; min-width: 0; font-size: 13px; color: rgba(234,242,252,0.9); }
.sxr-tl-when { display: inline-block; font: 650 11px 'JetBrains Mono', monospace; color: rgba(var(--eg-accent-rgb),0.9); margin-right: 8px; }

/* Inline badge / chip — a trailing [Label] on a list item */
.sxr-badge { display: inline-block; margin-left: 7px; padding: 1px 8px; border-radius: 999px; font: 650 10px 'Inter', sans-serif; letter-spacing: 0.04em; color: rgba(var(--eg-accent-rgb),0.92); background: rgba(var(--sxr-accent-rgb, 255,183,43),0.1); border: 1px solid rgba(var(--sxr-accent-rgb, 255,183,43),0.24); vertical-align: middle; white-space: nowrap; }

/* Gentle entrance so structured blocks fade in instead of popping into place
   when the finished answer swaps in. Plays once per block mount; stable message
   keys keep rows from remounting, so it never re-fires on re-render. */
@keyframes sxr-block-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
.sxr-table-wrap, .sxr-code, .sxr-facts, .sxr-pc, .sxr-callout, .sxr-flow, .sxr-timeline, .sxr-rank { animation: sxr-block-in 0.34s cubic-bezier(0.22,1,0.36,1) both; }
@media (prefers-reduced-motion: reduce) { .sxr-table-wrap, .sxr-code, .sxr-facts, .sxr-pc, .sxr-callout, .sxr-flow, .sxr-timeline, .sxr-rank { animation: none; } }
/* Stable render (a message that streamed in, or the live streaming frame):
   suppress the entrance animation so blocks do NOT re-pop when the streaming
   bubble hands off to the committed message — the two renders are already
   layout-identical, so any re-animation would read as a rebuild. Non-streamed
   messages (history restore, non-streaming turns) keep the gentle fade-in. */
.sxr-root.sxr-stable .sxr-table-wrap, .sxr-root.sxr-stable .sxr-code, .sxr-root.sxr-stable .sxr-facts, .sxr-root.sxr-stable .sxr-pc, .sxr-root.sxr-stable .sxr-callout, .sxr-root.sxr-stable .sxr-flow, .sxr-root.sxr-stable .sxr-timeline, .sxr-root.sxr-stable .sxr-rank { animation: none; }
`;

  let _cssInjected = false;
  function ensureCss() {
    if (_cssInjected) return;
    _cssInjected = true;
    try {
      const el = document.createElement('style');
      el.id = 'sxr-styles';
      el.textContent = SXR_CSS;
      document.head.appendChild(el);
    } catch (_) {}
  }

  /* ── Inline markdown → sanitized HTML ──────────────────────────────────── */
  function safeWebUrl(raw) {
    try {
      const url = new URL(String(raw || ''));
      return (url.protocol === 'https:' || url.protocol === 'http:') ? url : null;
    } catch (_) { return null; }
  }

  function faviconUrl(url) {
    const parsed = safeWebUrl(url);
    return parsed ? 'https://icons.duckduckgo.com/ip3/' + encodeURIComponent(parsed.hostname) + '.ico' : '';
  }

  function decorateAnchor(anchor) {
    const parsed = safeWebUrl(anchor.getAttribute('href'));
    if (!parsed) return;
    anchor.setAttribute('target', '_blank');
    anchor.setAttribute('rel', 'noopener noreferrer');
    if (anchor.classList.contains('sxr-cite')) return;
    const label = String(anchor.textContent || '').trim();
    const compact = label.length > 0 && label.length <= 36 && label.split(/\s+/).length <= 4;
    if (!compact || anchor.querySelector('img')) return;
    anchor.classList.add('sxr-brand-link');
    const img = document.createElement('img');
    img.setAttribute('src', faviconUrl(parsed.href));
    img.setAttribute('alt', '');
    img.setAttribute('aria-hidden', 'true');
    anchor.insertBefore(img, anchor.firstChild);
  }

  function makeBrandAnchor(brand, label) {
    const anchor = document.createElement('a');
    anchor.setAttribute('href', brand.url);
    anchor.setAttribute('title', 'Open ' + label);
    anchor.appendChild(document.createTextNode(label));
    decorateAnchor(anchor);
    return anchor;
  }

  function decorateBrandHtml(html, selectedIds) {
    try {
      const shell = document.createElement('span');
      shell.innerHTML = String(html || '');
      Array.from(shell.querySelectorAll('a')).forEach(decorateAnchor);
      if (!selectedIds || !selectedIds.size) return shell.innerHTML;
      const nameRe = new RegExp('\\b(' + BRAND_NAME_PATTERN + ')\\b', 'g');
      const visit = (node) => {
        Array.from(node.childNodes || []).forEach((child) => {
          if (child.nodeType === 1) {
            if (!/^(A|CODE|PRE|SCRIPT|STYLE)$/i.test(child.tagName || '')) visit(child);
            return;
          }
          if (child.nodeType !== 3 || !String(child.nodeValue || '').trim()) return;
          const text = String(child.nodeValue || '');
          nameRe.lastIndex = 0;
          let match, last = 0, changed = false;
          const frag = document.createDocumentFragment();
          while ((match = nameRe.exec(text)) !== null) {
            const brand = BRAND_BY_NAME.get(match[1].toLowerCase());
            if (!brand || !selectedIds.has(brand.id)) continue;
            if (match.index > last) frag.appendChild(document.createTextNode(text.slice(last, match.index)));
            frag.appendChild(makeBrandAnchor(brand, match[0]));
            last = match.index + match[0].length;
            changed = true;
          }
          if (!changed) return;
          if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
          child.parentNode.replaceChild(frag, child);
        });
      };
      visit(shell);
      return shell.innerHTML;
    } catch (_) { return String(html || ''); }
  }

  // "[2]" → a small numbered link to source 2. Only numbers with a source
  // behind them become links; text inside code and existing links is left
  // alone. The URL comes from the message's own source list and must be
  // http(s), so an answer cannot mint a link of its own this way.
  // While an answer streams its sources have not arrived, so a "[2]" has
  // nothing to link to. It is drawn as the same-sized chip, unlinked and
  // muted, rather than as bracket text that turns into a chip when the answer
  // finishes — that swap reflowed every line holding a citation at the last
  // moment.
  const PENDING_CITES = { pending: true };

  function decorateCitations(html, cites) {
    const pending = cites === PENDING_CITES;
    if (!pending && (!cites || !cites.size)) return html;
    if (String(html).indexOf('[') === -1) return html;
    try {
      const shell = document.createElement('span');
      shell.innerHTML = String(html || '');
      const re = /\[(\d{1,2})\]/g;
      const visit = (node) => {
        Array.from(node.childNodes || []).forEach((child) => {
          if (child.nodeType === 1) {
            if (!/^(A|CODE|PRE|SCRIPT|STYLE)$/i.test(child.tagName || '')) visit(child);
            return;
          }
          if (child.nodeType !== 3) return;
          const text = String(child.nodeValue || '');
          re.lastIndex = 0;
          let match, last = 0, changed = false;
          const frag = document.createDocumentFragment();
          while ((match = re.exec(text)) !== null) {
            if (pending) {
              if (match.index > last) frag.appendChild(document.createTextNode(text.slice(last, match.index)));
              const chip = document.createElement('span');
              chip.className = 'sxr-cite sxr-cite-pending';
              chip.textContent = match[1];
              frag.appendChild(chip);
              last = match.index + match[0].length;
              changed = true;
              continue;
            }
            const src = cites.get(Number(match[1]));
            if (!src) {
              // A number with no source behind it is dropped rather than
              // shown as a bare "[7]", along with the space before it.
              if (match.index > last) frag.appendChild(document.createTextNode(text.slice(last, match.index).replace(/[ \t]+$/, '')));
              last = match.index + match[0].length;
              changed = true;
              continue;
            }
            if (match.index > last) frag.appendChild(document.createTextNode(text.slice(last, match.index)));
            const a = document.createElement('a');
            a.className = 'sxr-cite';
            a.setAttribute('href', src.url);
            a.setAttribute('target', '_blank');
            a.setAttribute('rel', 'noopener noreferrer');
            a.setAttribute('title', src.name ? src.name + ' — ' + src.host : src.host);
            a.textContent = match[1];
            frag.appendChild(a);
            last = match.index + match[0].length;
            changed = true;
          }
          if (!changed) return;
          if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
          child.parentNode.replaceChild(frag, child);
        });
      };
      visit(shell);
      return shell.innerHTML;
    } catch (_) { return html; }
  }

  function citationMap(sources) {
    const out = new Map();
    (Array.isArray(sources) ? sources : []).forEach((s) => {
      if (!s || typeof s.n !== 'number') return;
      const parsed = safeWebUrl(s.url);
      if (!parsed) return;
      out.set(s.n, { url: parsed.href, name: String(s.name || ''), host: parsed.hostname.replace(/^www\./, '') });
    });
    return out;
  }

  function inlineHtml(src, selectedIds, cites) {
    try {
      const md = window.marked, purify = window.DOMPurify;
      if (!md || !purify) return null;
      const raw = md.parseInline ? md.parseInline(String(src || '')) : String(src || '');
      // Inline code needs the sxr class for the amber chip look.
      const html = String(raw).replace(/<code>/g, '<code class="sxr-ic">');
      const clean = purify.sanitize(html, { ADD_ATTR: ['target', 'rel'] });
      return decorateCitations(decorateBrandHtml(clean, selectedIds), cites);
    } catch (_) { return null; }
  }

  function Inline({ text, className }) {
    const brandLinks = srUC(BrandLinkContext);
    const cites = srUC(CitationContext);
    const html = inlineHtml(text, brandLinks && brandLinks.ids, cites);
    if (html === null) return <span className={className}>{text}</span>;
    return <span className={className} dangerouslySetInnerHTML={{ __html: html }} />;
  }

  /* ── Tiny syntax highlighter — comments / strings / numbers / keywords ── */
  const KEYWORDS = new Set(('function,return,if,else,elif,for,while,switch,case,break,continue,const,let,var,'
    + 'class,def,import,from,export,default,async,await,try,catch,except,finally,new,delete,typeof,'
    + 'instanceof,in,of,not,and,or,is,None,True,False,null,undefined,true,false,this,self,super,'
    + 'lambda,yield,raise,throw,pass,with,as,static,void,int,float,double,bool,string,str,struct,'
    + 'enum,interface,type,public,private,protected,fn,let,mut,impl,use,pub,match,SELECT,FROM,WHERE,'
    + 'INSERT,UPDATE,DELETE,JOIN,GROUP,ORDER,BY,LIMIT,select,from,where,insert,update,join,order,limit')
    .split(','));
  const TOK_RE = /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/|<!--[\s\S]*?-->)|("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`)|(\b\d+(?:\.\d+)?\b)/g;

  function highlightSegment(text, out, keyPrefix) {
    // keyword + function-call pass on a plain segment
    const wordRe = /([A-Za-z_][A-Za-z0-9_]*)(\s*\()?/g;
    let last = 0, m, k = 0;
    while ((m = wordRe.exec(text)) !== null) {
      if (m.index > last) out.push(text.slice(last, m.index));
      const w = m[1];
      if (KEYWORDS.has(w)) out.push(<span key={keyPrefix + 'k' + (k++)} className="sxr-tok-kw">{w}</span>);
      else if (m[2]) out.push(<span key={keyPrefix + 'f' + (k++)} className="sxr-tok-fn">{w}</span>);
      else out.push(w);
      last = m.index + w.length;
    }
    if (last < text.length) out.push(text.slice(last));
  }

  function highlightCode(code) {
    const out = [];
    let last = 0, m, i = 0;
    TOK_RE.lastIndex = 0;
    while ((m = TOK_RE.exec(code)) !== null) {
      if (m.index > last) highlightSegment(code.slice(last, m.index), out, 's' + i);
      if (m[1]) out.push(<span key={'c' + (i++)} className="sxr-tok-com">{m[1]}</span>);
      else if (m[2]) out.push(<span key={'q' + (i++)} className="sxr-tok-str">{m[2]}</span>);
      else out.push(<span key={'n' + (i++)} className="sxr-tok-num">{m[3]}</span>);
      last = m.index + m[0].length;
      i++;
    }
    if (last < code.length) highlightSegment(code.slice(last), out, 'e');
    return out;
  }

  /* ── Sandboxed interactive preview ──────────────────────────────────────
     Preview is intentionally limited to browser-native HTML/CSS/JS. The
     iframe gets scripts but NOT same-origin, forms, popups, downloads,
     navigation, pointer-lock, or any Electron/Node bridge. A CSP inside the
     opaque-origin document blocks all network, frames, workers, and forms. */
  const PREVIEW_LANGS = new Set([
    'html', 'htm', 'css', 'javascript', 'js', 'svg', 'web', 'webapp',
  ]);
  const PREVIEW_MAX_CHARS = 250000;

  function previewLang(lang, code) {
    const low = String(lang || '').trim().toLowerCase().split(/\s+/)[0];
    if (PREVIEW_LANGS.has(low)) return low;
    const sample = String(code || '').trim().slice(0, 200).toLowerCase();
    if (!low && /^(?:<!doctype\s+html|<html\b|<svg\b|<(?:main|div|canvas)\b)/.test(sample)) {
      return sample.startsWith('<svg') ? 'svg' : 'html';
    }
    return '';
  }

  function safeScriptText(src) {
    return String(src || '').replace(/<\/script/gi, '<\\/script');
  }

  function buildPreviewSrcDoc(bundle) {
    const b = bundle || {};
    const html = String(b.html || '');
    const css = String(b.css || '');
    const js = String(b.js || '');
    if ((html.length + css.length + js.length) > PREVIEW_MAX_CHARS) return '';
    const csp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
      + "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; "
      + "frame-src 'none'; child-src 'none'; worker-src 'none'; object-src 'none'; "
      + "form-action 'none'; base-uri 'none'; navigate-to 'none'";
    const guard = `<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}">`
      + `<meta name="referrer" content="no-referrer"><meta name="viewport" content="width=device-width,initial-scale=1">`
      + `<style>html,body{min-height:100%;margin:0}#__syrix_preview_error{display:none;position:fixed;z-index:2147483647;left:10px;right:10px;bottom:10px;max-height:42%;overflow:auto;white-space:pre-wrap;padding:9px 11px;border:1px solid #f09aa6;border-radius:8px;background:#2a0e14;color:#ffdce1;font:12px/1.45 ui-monospace,monospace;box-shadow:0 8px 28px #0008}</style>`
      + `<script>(function(){function show(v){var e=document.getElementById('__syrix_preview_error');if(!e){e=document.createElement('pre');e.id='__syrix_preview_error';document.documentElement.appendChild(e);}e.style.display='block';e.textContent='Preview error\\n'+String(v&&v.stack||v&&v.message||v||'Unknown error');}window.addEventListener('error',function(e){show(e.error||e.message);});window.addEventListener('unhandledrejection',function(e){show(e.reason);});})();<\/script>`;
    const extraStyle = css ? `<style>${String(css).replace(/<\/style/gi, '<\\/style')}</style>` : '';
    const extraScript = js ? `<script>${safeScriptText(js)}<\/script>` : '';

    if (html) {
      let doc = html;
      if (/<head(?:\s[^>]*)?>/i.test(doc)) {
        doc = doc.replace(/<head(?:\s[^>]*)?>/i, (m) => m + guard + extraStyle);
      } else if (/<html(?:\s[^>]*)?>/i.test(doc)) {
        doc = doc.replace(/<html(?:\s[^>]*)?>/i, (m) => m + '<head>' + guard + extraStyle + '</head>');
      } else {
        doc = '<!doctype html><html><head>' + guard + extraStyle
          + '</head><body>' + doc + '</body></html>';
      }
      if (extraScript) {
        doc = /<\/body\s*>/i.test(doc)
          ? doc.replace(/<\/body\s*>/i, extraScript + '</body>')
          : doc + extraScript;
      }
      return doc;
    }
    const sample = '<main><h1>Style preview</h1><p>Browser-native output runs here.</p><button type="button">Interactive control</button><div id="app"></div><canvas id="canvas"></canvas></main>';
    return '<!doctype html><html><head>' + guard + extraStyle
      + '</head><body>' + sample + extraScript + '</body></html>';
  }

  function PreviewPane({ bundle, runId, onReload, onReset }) {
    const srcDoc = buildPreviewSrcDoc(bundle);
    if (!srcDoc) {
      return (
        <div className="sxr-preview">
          <div className="sxr-preview-bar">
            <span className="sxr-preview-status">Preview unavailable: output exceeds 250 KB.</span>
            <span className="sxr-preview-tools"><button type="button" className="sxr-preview-tool" onClick={onReset}>Reset</button></span>
          </div>
        </div>
      );
    }
    return (
      <div className="sxr-preview">
        <div className="sxr-preview-bar">
          <span className="sxr-preview-status"><span className="sxr-preview-note">Sandboxed · network and local access blocked</span></span>
          <span className="sxr-preview-tools">
            <button type="button" className="sxr-preview-tool" onClick={onReload}>Reload</button>
            <button type="button" className="sxr-preview-tool" onClick={onReset}>Reset</button>
          </span>
        </div>
        <iframe
          key={runId}
          className="sxr-preview-frame"
          title="Sandboxed code preview"
          sandbox="allow-scripts"
          referrerPolicy="no-referrer"
          srcDoc={srcDoc}
        />
      </div>
    );
  }

  /* ── Code card ─────────────────────────────────────────────────────────── */
  function CodeCard({ code, lang, previewBundle }) {
    const [copied, setCopied] = srUS(false);
    const [previewOpen, setPreviewOpen] = srUS(false);
    const [previewRun, setPreviewRun] = srUS(0);
    const text = String(code || '').replace(/\n$/, '');
    if (!text.trim()) return null;   // never render an empty code card
    const lines = text.split('\n');
    const showLines = lines.length > 4;
    let body;
    try { body = highlightCode(text); } catch (_) { body = text; }  // never blank on a highlighter throw
    const doCopy = () => {
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text);
        } else {
          const ta = document.createElement('textarea');
          ta.value = text; document.body.appendChild(ta);
          ta.select(); document.execCommand('copy'); document.body.removeChild(ta);
        }
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      } catch (_) {}
    };
    return (
      <div className="sxr-code">
        <div className="sxr-code-head">
          <span className="sxr-code-lang">{String(lang || 'code').slice(0, 14)}</span>
          <span className="sxr-code-actions">
            {previewBundle && (
              <button type="button" className="sxr-code-run" onClick={() => {
                setPreviewOpen(true); setPreviewRun((n) => n + 1);
              }}>
                {previewOpen ? 'Run again' : 'Preview / Run'}
              </button>
            )}
            <button type="button" className={'sxr-code-copy' + (copied ? ' done' : '')} onClick={doCopy}>
              {copied ? '✓ Copied' : 'Copy'}
            </button>
          </span>
        </div>
        <div className="sxr-code-body">
          {showLines && (
            <div className="sxr-code-lines">
              {lines.map((_, i) => <div key={i}>{i + 1}</div>)}
            </div>
          )}
          <pre className="sxr-code-pre">{body}</pre>
        </div>
        {previewOpen && previewBundle && (
          <PreviewPane
            bundle={previewBundle}
            runId={previewRun}
            onReload={() => setPreviewRun((n) => n + 1)}
            onReset={() => { setPreviewOpen(false); setPreviewRun(0); }}
          />
        )}
      </div>
    );
  }

  /* ── Table ─────────────────────────────────────────────────────────────── */
  function cellText(cell) {
    if (cell == null) return '';
    if (typeof cell === 'string') return cell;
    return String(cell.text != null ? cell.text : cell);
  }
  function TableCard({ token }) {
    const header = Array.isArray(token.header) ? token.header : [];
    const rows = Array.isArray(token.rows) ? token.rows : [];
    return (
      <div className="sxr-table-wrap">
        <div className="sxr-table-scroll">
          <table className="sxr-table">
            <thead>
              <tr>{header.map((c, i) => <th key={i}><Inline text={cellText(c)} /></th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>{(Array.isArray(r) ? r : []).map((c, j) => <td key={j}><Inline text={cellText(c)} /></td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  /* ── Lists ─────────────────────────────────────────────────────────────── */
  const CHECK_RE = /^\s*(?:✓|✔|✅)\s*/;
  const CROSS_RE = /^\s*(?:✕|✗|✖|❌|✘)\s*/;
  // "**Label:** value" or "**Label** — value": colon may sit inside or
  // outside the bold; a separator must exist in one of the two spots so a
  // plain bold-lead sentence never turns into a fact row.
  const FACT_RE = /^\*\*([^*]{1,48}?)\s*(:|：)?\*\*\s*(:|：|—|–|-)?\s*(\S.*)$/s;
  function factParts(text) {
    const m = String(text || '').match(FACT_RE);
    if (!m || (!m[2] && !m[3])) return null;
    return { k: m[1].trim(), v: m[4] };
  }

  function itemFirstText(item) {
    // The first 'text' token of a list item = its own line (nested lists excluded).
    const toks = Array.isArray(item.tokens) ? item.tokens : [];
    for (const t of toks) if (t.type === 'text' || t.type === 'paragraph') return String(t.text || '');
    return String(item.text || '');
  }

  function itemBlocks(item) {
    // Every token of a list item EXCEPT its leading text line (rendered inline).
    // Fixes the "longer code goes blank" bug: a code block / table / blockquote
    // nested inside a step used to be dropped (only nested lists survived).
    const toks = Array.isArray(item.tokens) ? item.tokens : [];
    let leadIdx = -1;
    for (let i = 0; i < toks.length; i++) {
      if (toks[i].type === 'text' || toks[i].type === 'paragraph') { leadIdx = i; break; }
    }
    return toks.filter((_, i) => i !== leadIdx);
  }

  function ListItem({ item, forceMark }) {
    const toks = Array.isArray(item.tokens) ? item.tokens : [];
    const own = itemFirstText(item);
    let mark = forceMark || '';
    let text = own;
    if (!mark && CHECK_RE.test(own)) { mark = 'good'; text = own.replace(CHECK_RE, ''); }
    else if (!mark && CROSS_RE.test(own)) { mark = 'bad'; text = own.replace(CROSS_RE, ''); }
    else if (mark) { text = own.replace(CHECK_RE, '').replace(CROSS_RE, ''); }
    // A short trailing [Label] becomes a chip (e.g. "AI Engineer [Fast Growth]").
    // A real markdown link ends with ")", so it never matches this.
    let badge = null;
    const bm = text.match(/\s*\[([^[\]\n]{1,24})\]\s*$/);
    if (bm) { badge = bm[1].trim(); text = text.slice(0, text.length - bm[0].length); }
    const blocks = itemBlocks(item);
    return (
      <li className={'sxr-li' + (mark ? ' ' + mark : '')}>
        {mark && <span className="sxr-li-mark">{mark === 'good' ? '✓' : '✕'}</span>}
        <Inline text={text} />
        {badge && <span className="sxr-badge">{badge}</span>}
        {blocks.map((t, i) => <Block key={i} token={t} idx={i} />)}
      </li>
    );
  }

  function FactGrid({ items }) {
    return (
      <div className="sxr-facts">
        {items.map((it, i) => {
          const fp = factParts(itemFirstText(it));
          return (
            <div key={i} className="sxr-fact">
              <span className="sxr-fact-k">{fp ? fp.k : ''}</span>
              <span className="sxr-fact-v"><Inline text={fp ? fp.v : itemFirstText(it)} /></span>
            </div>
          );
        })}
      </div>
    );
  }

  function StepFlow({ items }) {
    return (
      <div className="sxr-flow">
        {items.map((it, i) => (
          <div key={i} className="sxr-step">
            <div className="sxr-step-n">{i + 1}</div>
            <div className="sxr-step-b">
              <Inline text={itemFirstText(it)} />
              {itemBlocks(it).map((t, j) => <Block key={j} token={t} idx={j} />)}
            </div>
          </div>
        ))}
      </div>
    );
  }

  /* ── Stat bars ─────────────────────────────────────────────────────────── */
  // "Label 65%" / "Label: 65%" / "Label — 65%" → labelled meter. The list-level
  // gate (>=60% of items match) keeps a lone "grew 30%" bullet from triggering.
  const STAT_RE = /^(.+?)[\s:：—–-]+(\d{1,3}(?:\.\d+)?)\s*%\s*$/;
  function statParts(text) {
    const m = String(text || '').trim().match(STAT_RE);
    if (!m) return null;
    const val = parseFloat(m[2]);
    if (!(val >= 0 && val <= 100)) return null;
    const label = m[1].trim();
    if (!label) return null;
    return { label, val };
  }
  function StatBars({ items }) {
    return (
      <div className="sxr-stats">
        {items.map((it, i) => {
          const p = statParts(itemFirstText(it));
          if (!p) return (
            <div key={i} className="sxr-stat"><span className="sxr-stat-k"><Inline text={itemFirstText(it)} /></span></div>
          );
          const shown = Number.isInteger(p.val) ? p.val : p.val.toFixed(1);
          return (
            <div key={i} className="sxr-stat">
              <span className="sxr-stat-k"><Inline text={p.label} /></span>
              <span className="sxr-stat-bar"><span className="sxr-stat-fill" style={{ width: p.val + '%' }} /></span>
              <span className="sxr-stat-v">{shown}%</span>
            </div>
          );
        })}
      </div>
    );
  }

  /* ── Timeline ──────────────────────────────────────────────────────────── */
  // A date/era-led item ("1969 — Moon landing", "18th century …", "March 2020…").
  const WHEN_RE = /^(?:\*\*)?\s*(\d{3,4}s?(?:\s?(?:BCE?|BC|AD|CE))?|\d{1,2}(?:st|nd|rd|th)\s+century(?:\s+(?:BCE?|BC|AD|CE))?|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b\**[)\s:：.—–-]*\s*(.*)$/i;
  function whenPart(text) {
    const m = String(text || '').trim().match(WHEN_RE);
    if (!m) return null;
    return { when: m[1].trim(), body: m[2].trim() };
  }
  function Timeline({ items }) {
    return (
      <div className="sxr-timeline">
        {items.map((it, i) => {
          const wp = whenPart(itemFirstText(it));
          return (
            <div key={i} className="sxr-tl-item">
              <span className="sxr-tl-dot" />
              <div className="sxr-tl-body">
                {wp && wp.when && <span className="sxr-tl-when">{wp.when}</span>}
                <Inline text={wp ? wp.body : itemFirstText(it)} />
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  /* ── Ranking (gated by a ranking heading in renderTokens) ──────────────── */
  const RANK_MEDALS = ['🏆', '🥈', '🥉'];
  function RankingList({ items }) {
    return (
      <ol className="sxr-rank">
        {items.map((it, i) => (
          <li key={i} className="sxr-rank-item">
            <span className="sxr-rank-medal">{i < 3 ? RANK_MEDALS[i] : (i + 1)}</span>
            <span className="sxr-rank-body"><Inline text={itemFirstText(it)} /></span>
          </li>
        ))}
      </ol>
    );
  }

  function ListBlock({ token, forceMark }) {
    const items = Array.isArray(token.items) ? token.items : [];
    if (!items.length) return null;
    // Percentage list → progress bars (works for ordered or unordered).
    if (!forceMark && items.length >= 2) {
      const statish = items.filter(it => statParts(itemFirstText(it)) !== null);
      if (statish.length >= Math.ceil(items.length * 0.6)) return <StatBars items={items} />;
      // Date/era-led list → timeline.
      const whenish = items.filter(it => whenPart(itemFirstText(it)) !== null);
      if (whenish.length >= Math.ceil(items.length * 0.7)) return <Timeline items={items} />;
    }
    if (token.ordered) {
      // Numbered flow for real multi-step sequences; plain numbered list otherwise.
      const flowFits = items.length >= 3 && items.length <= 14
        && items.every(it => itemFirstText(it).length <= 300)
        && items.some(it => itemFirstText(it).length > 24);
      if (flowFits) return <StepFlow items={items} />;
      return <ol className="sxr-ol">{items.map((it, i) => <ListItem key={i} item={it} />)}</ol>;
    }
    // Fact grid when most items are "**Label:** value".
    const factish = items.filter(it => factParts(itemFirstText(it)) !== null);
    if (items.length >= 2 && factish.length >= Math.ceil(items.length * 0.8)) {
      return <FactGrid items={items} />;
    }
    return <ul className="sxr-ul">{items.map((it, i) => <ListItem key={i} item={it} forceMark={forceMark} />)}</ul>;
  }

  /* ── Callout ───────────────────────────────────────────────────────────── */
  function Callout({ token }) {
    const inner = String(token.text || '');
    const warn = /^\s*(?:⚠|warning|caution|important)/i.test(inner);
    return (
      <div className={'sxr-callout' + (warn ? ' warn' : '')}>
        <span className="sxr-callout-ico">{warn ? '⚠' : 'ℹ'}</span>
        <div className="sxr-callout-b">
          {(Array.isArray(token.tokens) ? token.tokens : []).map((t, i) => <Block key={i} token={t} idx={i} />)}
        </div>
      </div>
    );
  }

  /* ── Block dispatcher ──────────────────────────────────────────────────── */
  function Block({ token, idx, lead, previewBundle }) {
    switch (token.type) {
      case 'heading': {
        const d = Math.min(4, Math.max(1, token.depth || 2));
        return <div className={'sxr-h d' + d}><Inline text={token.text} /></div>;
      }
      case 'paragraph':
        return <p className={'sxr-p' + (lead ? ' sxr-lead' : '')}><Inline text={token.text} /></p>;
      case 'text':
        return <p className="sxr-p"><Inline text={token.text} /></p>;
      case 'code':
        return <CodeCard code={token.text} lang={token.lang} previewBundle={previewBundle} />;
      case 'table':
        return <TableCard token={token} />;
      case 'list':
        return <ListBlock token={token} />;
      case 'blockquote':
        return <Callout token={token} />;
      case 'hr':
      case 'space':
        return null;
      case 'html': {
        const purify = window.DOMPurify;
        if (!purify) return null;
        return <div className="sxr-p" dangerouslySetInnerHTML={{ __html: purify.sanitize(String(token.raw || '')) }} />;
      }
      default:
        return token.raw ? <p className="sxr-p"><Inline text={token.raw} /></p> : null;
    }
  }

  /* ── Sequence transforms — pros/cons heading+list pairing ──────────────── */
  const PROS_RE = /^\s*[\W_]*\s*(pros|advantages|benefits|strengths|upsides?)\b/i;
  const CONS_RE = /^\s*[\W_]*\s*(cons|risks|disadvantages|drawbacks|weaknesses|downsides?|limitations)\b/i;

  function isHeadingLike(tok, re) {
    if (!tok) return false;
    if (tok.type === 'heading') return re.test(String(tok.text || ''));
    if (tok.type === 'paragraph') {
      const m = String(tok.text || '').match(/^\*\*([^*]{1,40})\*\*:?\s*$/);
      return !!(m && re.test(m[1]));
    }
    return false;
  }
  function headingLabel(tok) {
    const t = String(tok.text || '').replace(/^\*\*|\*\*:?\s*$/g, '').replace(/:$/, '').trim();
    return t;
  }
  function nextSolid(tokens, i) {
    let j = i + 1;
    while (j < tokens.length && tokens[j].type === 'space') j++;
    return j;
  }

  // "Top 5 …", "Top programming languages", "… ranking", "leaderboard",
  // "10 best …", "greatest …". Deliberately NOT bare "best" (so "Best
  // practices" step lists don't sprout medals).
  const RANK_HEADING_RE = /\b(rank(?:ing|ed|ings)?|leaderboard|top\s+(?:\d+|ten|\w+)|greatest|\d+\s+best)\b/i;

  function previewBundleFromTokens(tokens) {
    const bundle = { html: '', css: '', js: '' };
    let owner = -1, total = 0;
    for (let i = 0; i < tokens.length; i++) {
      const tok = tokens[i];
      if (!tok || tok.type !== 'code') continue;
      const code = String(tok.text || '');
      const lang = previewLang(tok.lang, code);
      if (!lang) continue;
      if (owner < 0) owner = i;
      total += code.length;
      if (total > PREVIEW_MAX_CHARS) break;
      if (lang === 'css') bundle.css += (bundle.css ? '\n\n' : '') + code;
      else if (lang === 'javascript' || lang === 'js') bundle.js += (bundle.js ? '\n\n' : '') + code;
      else if (!bundle.html) bundle.html = code;
    }
    if (owner < 0) return null;
    return { owner, bundle };
  }

  function renderTokens(tokens) {
    const out = [];
    const preview = previewBundleFromTokens(tokens);
    let leadDone = false;
    let sectionHeading = '';
    for (let i = 0; i < tokens.length; i++) {
      const tok = tokens[i];
      if (tok.type === 'heading') sectionHeading = String(tok.text || '');
      // Ranking: an ordered list under a "top/best/ranking" heading → medal rows.
      // Yields to stat/timeline lists so those specialised shapes still win.
      if (tok.type === 'list' && tok.ordered && RANK_HEADING_RE.test(sectionHeading)) {
        const its = Array.isArray(tok.items) ? tok.items : [];
        const statish = its.filter(it => statParts(itemFirstText(it)) !== null).length;
        const whenish = its.filter(it => whenPart(itemFirstText(it)) !== null).length;
        if (its.length >= 2 && statish < Math.ceil(its.length * 0.6) && whenish < Math.ceil(its.length * 0.7)) {
          out.push(<RankingList key={i} items={its} />);
          leadDone = true;
          continue;
        }
      }
      // Pros/Cons pairing: [Pros-heading, list, Cons-heading, list] → cards.
      if (isHeadingLike(tok, PROS_RE)) {
        const li1 = nextSolid(tokens, i);
        if (tokens[li1] && tokens[li1].type === 'list' && !tokens[li1].ordered) {
          const h2 = nextSolid(tokens, li1);
          if (isHeadingLike(tokens[h2], CONS_RE)) {
            const li2 = nextSolid(tokens, h2);
            if (tokens[li2] && tokens[li2].type === 'list' && !tokens[li2].ordered) {
              out.push(
                <div key={'pc' + i} className="sxr-pc">
                  <div className="sxr-pc-card pros">
                    <div className="sxr-pc-title">{headingLabel(tok) || 'Pros'}</div>
                    <ListBlock token={tokens[li1]} forceMark="good" />
                  </div>
                  <div className="sxr-pc-card cons">
                    <div className="sxr-pc-title">{headingLabel(tokens[h2]) || 'Cons'}</div>
                    <ListBlock token={tokens[li2]} forceMark="bad" />
                  </div>
                </div>
              );
              i = li2;
              continue;
            }
          }
        }
      }
      const isLead = !leadDone && tok.type === 'paragraph';
      if (tok.type === 'paragraph' || tok.type === 'heading' || tok.type === 'list'
          || tok.type === 'table' || tok.type === 'code') leadDone = true;
      out.push(
        <Block
          key={i}
          token={tok}
          idx={i}
          lead={isLead}
          previewBundle={preview && preview.owner === i ? preview.bundle : null}
        />
      );
    }
    return out;
  }

  /* ── Markdown normalizer ─────────────────────────────────────────────────
     Repairs the malformed markdown models most often emit BEFORE marked's
     lexer sees it, so the smart components actually engage instead of the
     text degrading to a raw-pipe paragraph. This is the client-side sibling
     of the backend polish() pass — it runs unconditionally so the renderer is
     self-sufficient even on paths where the backend cleanup didn't fire.

     Rules (all fence-aware — nothing inside a ``` block is ever touched, and
     idempotent — already-valid markdown passes straight through):
       • pipe-tables missing a |---| separator row  → synthesize one so marked
         parses a real table instead of a paragraph of literal pipes;
       • a whole table flattened onto ONE line (embedded |---|---| run) →
         split back into header/body rows before any other processing;
       • spaced `*** X ***` / stray `a *** b` / `****` spam → real bold or gone;
       • `**1. text**` pseudo-lists  → real `1. **text**` numbered items;
       • `# H1` → `## H2`, duplicate adjacent headings collapsed, HR spam
         (`***` / `___` / `---` on their own line) dropped;
       • conservative trailing/parenthetical source-domain scrub (belt-and-
         suspenders; the answer body stays clean, evidence lives in Inspect).
     Never alters meaning — only syntax. Any error → original text returned. */
  function nmSplitCells(line) {
    let s = String(line).trim().replace(/^\|/, '').replace(/\|$/, '');
    return s.split(/(?<!\\)\|/).map(c => c.trim());
  }
  function nmCountPipes(line) {
    return (String(line).match(/(?<!\\)\|/g) || []).length;
  }
  function nmIsSepRow(line) {
    const cells = nmSplitCells(line).filter(c => c !== '');
    return cells.length > 0 && cells.every(c => /^:?-{2,}:?$/.test(c.replace(/\s+/g, '')));
  }
  function nmFmtRow(row, cols) {
    let cells = nmSplitCells(row);
    if (cells.length < cols) cells = cells.concat(Array(cols - cells.length).fill(''));
    else if (cells.length > cols) cells = cells.slice(0, cols);
    return '| ' + cells.join(' | ') + ' |';
  }
  /* A run of 2+ separator cells (`|---|---|`) embedded inside one physical
     line — the signature of a table the model emitted with no newlines at all
     ("| Feature | A | B | |---|---|---| | Row | x | y | …"). The line-based
     table logic below can never see it, so nmSplitFlatTable rebuilds the rows
     first (mirrors backend _split_flat_table). Bails untouched on ambiguity. */
  const NM_SEP_RUN = /\|(?:\s*:?-{2,}:?\s*\|){2,}/;
  function nmSplitFlatTable(line) {
    const m = NM_SEP_RUN.exec(line);
    if (!m) return [line];
    const prefix = line.slice(0, m.index);
    const suffix = line.slice(m.index + m[0].length);
    const sepCells = m[0].replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
    const ncols = sepCells.length;
    if (prefix.indexOf('|') === -1) return [line];
    let ptoks = prefix.split('|');
    if (ptoks.length && !ptoks[ptoks.length - 1].trim()) ptoks = ptoks.slice(0, -1);
    if (ptoks.length < ncols) return [line];
    const header = ptoks.slice(-ncols).map(t => t.trim());
    const lead = ptoks.slice(0, -ncols).join('|').trim();
    let stoks = suffix.split('|');
    if (stoks.length && !stoks[0].trim()) stoks = stoks.slice(1);
    const rows = [];
    let i = 0;
    while (i + ncols <= stoks.length) {
      rows.push(stoks.slice(i, i + ncols).map(c => c.trim()));
      i += ncols;
      if (i < stoks.length && !stoks[i].trim()) i += 1;  // `| |` row boundary
    }
    const tail = stoks.slice(i).join('|').trim();
    if (!rows.length) return [line];
    const out = [];
    if (lead) out.push(lead, '');
    out.push('| ' + header.join(' | ') + ' |');
    out.push('|' + sepCells.map(c => ' ' + c + ' ').join('|') + '|');
    for (const r of rows) out.push('| ' + r.join(' | ') + ' |');
    if (tail) { out.push(''); for (const t of nmSplitFlatTable(tail)) out.push(t); }
    return out;
  }
  function nmUnflatten(text) {
    if ((text.match(/\|/g) || []).length < 7) return text;
    const out = [];
    let fenced = false;
    for (const line of text.split('\n')) {
      const t = line.trim();
      if (t.startsWith('```') || t.startsWith('~~~')) { fenced = !fenced; out.push(line); continue; }
      if (fenced || (line.match(/\|/g) || []).length < 7) { out.push(line); continue; }
      for (const s of nmSplitFlatTable(line)) out.push(s);
    }
    return out.join('\n');
  }
  const NM_TLD = '(?:com|org|net|gov|edu|news|info|co\\.uk|uk|in|ca|au|us)';
  const NM_DASH_DOMAIN = new RegExp('\\s+[\\u2014\\u2013-]\\s+(?:www\\.)?[a-z0-9][\\w-]*(?:\\.[\\w-]+)*\\.' + NM_TLD + '\\s*$', 'i');
  const NM_PAREN_DOMAIN = new RegExp('\\(\\s*(?:source:\\s*)?(?:www\\.)?[a-z0-9][\\w-]*(?:\\.[\\w-]+)*\\.' + NM_TLD + '\\s*\\)', 'ig');
  const NM_PAREN_SOURCE = /\(\s*source:\s*[^)\n]{1,60}\)/ig;
  // A fenced block that is really a leaked list of source domains (the model
  // sometimes dumps its citations as ```markdown / ```text / ``` with a domain
  // per line). Sources belong in Inspect, never the chat body — drop the whole
  // block. A genuine code snippet has a real language tag AND real code, so it
  // never matches (every non-blank line must be a bare domain). Mirrors the
  // backend polish `_is_source_dump_fence`.
  const NM_DUMP_LANGS = new Set(['', 'markdown', 'md', 'text', 'txt', 'plaintext',
    'sources', 'source', 'links', 'urls', 'refs', 'references']);
  const NM_DOMAIN_ONLY = new RegExp('^\\s*(?:[-*]\\s+)?(?:https?://)?(?:www\\.)?[a-z0-9][\\w-]*(?:\\.[\\w-]+)*\\.' + NM_TLD + '\\s*$', 'i');
  function nmIsSourceDump(lang, body) {
    if (!NM_DUMP_LANGS.has(lang)) return false;
    const nb = body.filter(l => l.trim());
    if (nb.length < 2) return false;
    return nb.every(l => NM_DOMAIN_ONLY.test(l));
  }

  function normalizeMarkdown(src) {
    let text = String(src == null ? '' : src);
    if (!text.trim()) return text;
    try {
      // Balance an unclosed fence first so the rest isn't swallowed.
      if (((text.match(/^\s*(?:```|~~~)/gm) || []).length) % 2 === 1) {
        text = text.replace(/\s+$/, '') + '\n```';
      }
      text = nmUnflatten(text);
      const lines = text.split('\n');
      const out = [];
      let inFence = false;
      let fenceOpen = '';
      let fenceBuf = [];
      let table = [];
      let lastHeadingKey = '';
      let lastEmitted = '';

      const flushTable = () => {
        if (!table.length) return;
        const rows = table; table = [];
        const counts = rows.map(nmCountPipes);
        const bordered = rows.some(r => { const t = r.trim(); return t.startsWith('|') || t.endsWith('|'); });
        const consistent = counts.every(c => c === counts[0]);
        // A real table: 2+ pipe rows that are either bordered or column-consistent.
        if (rows.length < 2 || !(bordered || consistent)) { rows.forEach(r => out.push(r)); return; }
        if (nmIsSepRow(rows[1])) { rows.forEach(r => out.push(r)); return; }  // already valid
        // Widen to the MAX column count across all rows (parity with backend
        // _rebuild_table) so a body row with more cells than the header isn't
        // truncated — nmFmtRow pads shorter rows to match.
        const cols = Math.max(2, ...rows.map(r => nmSplitCells(r).length));
        out.push(nmFmtRow(rows[0], cols));
        out.push('| ' + Array(cols).fill('---').join(' | ') + ' |');
        for (let i = 1; i < rows.length; i++) out.push(nmFmtRow(rows[i], cols));
      };

      for (const raw of lines) {
        const trimmed = raw.trim();
        if (trimmed.startsWith('```') || trimmed.startsWith('~~~')) {
          if (!inFence) {
            flushTable();
            inFence = true; fenceOpen = raw; fenceBuf = [];
          } else {
            inFence = false;
            const lang = fenceOpen.replace(/^\s*[`~]+/, '').trim().toLowerCase();
            if (!nmIsSourceDump(lang, fenceBuf)) {   // real code → keep verbatim
              out.push(fenceOpen);
              for (const f of fenceBuf) out.push(f);
              out.push(raw);
            }                                          // else: drop leaked sources
            fenceOpen = ''; fenceBuf = [];
          }
          continue;
        }
        if (inFence) { fenceBuf.push(raw); continue; }
        if (trimmed && nmCountPipes(trimmed) >= 2) { table.push(raw); continue; }
        flushTable();
        // Drop a horizontal-rule / asterisk-spam line (3+ of the same marker).
        if (/^\s*([-*_])\1{2,}\s*$/.test(raw)) continue;
        let line = raw;
        line = line.replace(/^(\s{0,3})#\s+/, '$1## ');           // H1 → H2
        const hm = line.match(/^\s{0,3}#{1,6}\s+(.*\S)\s*$/);      // dup-heading collapse
        if (hm) {
          const key = hm[1].toLowerCase();
          if (key && key === lastHeadingKey) continue;
          lastHeadingKey = key;
        } else if (trimmed) {
          lastHeadingKey = '';
        }
        line = line.replace(/^(\s*)\*\*(\d{1,2})[.)]\s+(.+?)\*\*(:?)\s*$/, '$1$2. **$3**$4'); // pseudo-list
        line = line.replace(/(^|\s)\*\*\*\s+([^*]+?)\s+\*\*\*/g, '$1**$2**');  // spaced "*** X ***" → bold (never a tight ***word***)
        line = line.replace(/\*{4,}/g, '');                        // asterisk spam
        line = line.replace(/(^|\s)\*{3}(?=\s|$)/g, '$1');         // orphan *** token
        line = line.replace(NM_DASH_DOMAIN, '');                   // "… — domain.com" tail
        line = line.replace(NM_PAREN_DOMAIN, '');                  // "(domain.com)"
        line = line.replace(NM_PAREN_SOURCE, '');                  // "(source: …)"
        line = line.replace(/(\S)  +(\S)/g, '$1 $2');              // tidy doubled spaces
        // A lone unbalanced ** (odd count) would bold the rest of the block —
        // drop the dangling marker (parity with backend _scrub_line).
        if (((line.match(/\*\*/g) || []).length % 2) === 1) {
          const bi = line.lastIndexOf('**');
          if (bi !== -1) line = line.slice(0, bi) + line.slice(bi + 2);
        }
        // Drop an exact-duplicate consecutive list item (model stutter).
        const lt = line.trim();
        if (lt && lt === lastEmitted && /^(?:[-*+]\s|\d{1,2}[.)]\s)/.test(lt)) continue;
        out.push(line);
        lastEmitted = lt;
      }
      // Fences are pre-balanced above, but never lose a buffered fence if one
      // somehow stayed open (and it isn't a leaked source dump).
      if (inFence && !nmIsSourceDump(fenceOpen.replace(/^\s*[`~]+/, '').trim().toLowerCase(), fenceBuf)) {
        out.push(fenceOpen);
        for (const f of fenceBuf) out.push(f);
      }
      flushTable();
      let result = out.join('\n').replace(/\n{3,}/g, '\n\n').replace(/[ \t]+$/gm, '');
      return result.trim() ? result : text;
    } catch (_) {
      return String(src == null ? '' : src);
    }
  }

  /* ── Streaming edge-guard ────────────────────────────────────────────────
     Makes an in-progress markdown string safe to render through the SAME rich
     pipeline as the finished answer, so structure appears incrementally
     instead of the old plain→rich swap that visually rebuilt the message when
     generation ended. It trims ONLY the trailing incomplete token at the
     streaming caret so no broken syntax flashes; a complete markdown string
     passes through byte-identical (see tests), which is what guarantees the
     final render is layout-identical to the last streamed frame.

     Fence balancing, flat/broken tables and dangling `**` lines are already
     repaired by normalizeMarkdown (run AFTER this), so this only covers the
     inline / caret-edge cases the line-based normalizer cannot: an unfinished
     link/image, an open inline-code span, and a bare heading marker with no
     text yet. Never throws. */
  function streamGuard(src) {
    let s = String(src == null ? '' : src);
    if (!s) return s;
    try {
      // Everything up to the last hard line break is settled; only the tail is
      // still being typed, so edge-repairs are scoped to it (prose on earlier
      // lines that legitimately contains '[' or '`' is never touched).
      const nl = s.lastIndexOf('\n');
      const head = nl >= 0 ? s.slice(0, nl + 1) : '';
      let tail = nl >= 0 ? s.slice(nl + 1) : s;

      // A citation half-typed at the caret ("【1†L", "[1") is held back until
      // it closes. Checked before the link rule below, which would otherwise
      // turn "[1" into a bare "1" for a frame.
      tail = tail.replace(/【[^】\n]{0,80}$/, '').replace(/\[\d{0,2}$/, '');

      // Unfinished inline link/image: "[label](partial" (no closing ')') or a
      // lone "[label" (no ']') → show just the label so a raw "](http…" never
      // flashes. A completed "[label](url)" has no dangling '(' and is skipped.
      tail = tail.replace(/!?\[([^\]\n]*)\]\([^)\n]*$/, '$1');
      tail = tail.replace(/!?\[([^\]\n]*)$/, '$1');

      // Open inline-code span: an odd count of single backticks on the tail
      // means the last `code` is still open → drop the trailing opener so a
      // stray ` doesn't render.
      if (((tail.match(/`/g) || []).length % 2) === 1) {
        const bi = tail.lastIndexOf('`');
        if (bi !== -1) tail = tail.slice(0, bi) + tail.slice(bi + 1);
      }

      // A heading marker with no text yet ("##" / "### ") at the caret → hold
      // it back so an empty heading block doesn't flash for a frame.
      if (/^\s{0,3}#{1,6}\s*$/.test(tail)) tail = '';

      return citeDialect(head + tail);
    } catch (_) {
      return String(src == null ? '' : src);
    }
  }

  // The numbered-citation dialect several lanes write, 【1†L1-L3】, as the
  // [1] the finished answer carries — the backend makes the same rewrite, so
  // the streamed frame and the final text cite identically. A marker naming
  // no numbered source (【turn0search3】) is dropped, as the backend drops it.
  function citeDialect(s) {
    if (s.indexOf('【') === -1) return s;
    return s.replace(/[ \t]*【(\d{1,2})(?:†[^】\n]{0,80})?】/g, ' [$1]')
      .replace(/【[^】\n]{0,80}】/g, '');
  }

  /* ── Root component ────────────────────────────────────────────────────── */
  // `streaming` (tokens still arriving) runs the incremental edge-guard first;
  // `stable` (a finished message that streamed in) suppresses the block
  // entrance animation so the hand-off from the streaming bubble to the
  // committed message doesn't re-pop layout-identical blocks. Both render
  // through the identical normalizeMarkdown → marked.lexer → renderTokens
  // pipeline, so a streamed answer never transforms into a different layout
  // once generation ends.
  const AnswerRendererMemo = React.memo(function SyrixAnswerRendererImpl({ text, streaming, stable, sources }) {
    ensureCss();
    const raw = String(text == null ? '' : text);
    if (!raw.trim()) return null;
    const guarded = streaming ? streamGuard(raw) : raw;
    const src = normalizeMarkdown(guarded);
    let tokens = null;
    try {
      const md = window.marked;
      if (md && md.lexer && window.DOMPurify) tokens = md.lexer(src, { gfm: true, breaks: true });
    } catch (_) { tokens = null; }
    // marked/DOMPurify missing → degrade to the plain syntax-stripped partial
    // while streaming (no raw markdown flash), or plain pre-wrap when finished.
    if (!tokens) {
      const body = streaming ? stripSyntaxForStream(src) : src;
      return <div className="sxr-root" style={{ whiteSpace: 'pre-wrap' }}>{body}</div>;
    }
    const rootClass = 'sxr-root' + (streaming || stable ? ' sxr-stable' : '') + (streaming ? ' sxr-live' : '');
    const brandValue = { ids: streaming ? new Set() : selectedBrandIds(src) };
    const cites = streaming ? PENDING_CITES : citationMap(sources);
    return <BrandLinkContext.Provider value={brandValue}>
      <CitationContext.Provider value={cites}>
        <div className={rootClass}>{renderTokens(tokens)}</div>
      </CitationContext.Provider>
    </BrandLinkContext.Provider>;
  });

  // Strip markdown syntax from a mid-typewriter partial so raw bold/heading/
  // fence tokens never flash on screen while text streams in. Cheap and lossy
  // by design — the finished message re-renders through the full component.
  function stripSyntaxForStream(text) {
    let s = String(text || '');
    s = s.replace(/^```[^\n]*$/gm, '');
    s = s.replace(/^#{1,6}\s+/gm, '');
    // Drop table separator rows, then flatten pipe rows to spaced cells so a
    // mid-stream table reads as text instead of a wall of pipes.
    s = s.replace(/^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$/gm, '');
    s = s.replace(/^\s*\|(.+)\|\s*$/gm, (m, g) => g.split('|').map(c => c.trim()).filter(Boolean).join('   '));
    s = s.replace(/\*\*\*([^*]+)\*\*\*/g, '$1');   // bold-italic → text
    s = s.replace(/\*\*([^*]*)\*\*/g, '$1');
    s = s.replace(/__([^_]*)__/g, '$1');
    // Stray/unclosed emphasis markers at the streaming edge.
    s = s.replace(/\*{3,}/g, '').replace(/\*\*/g, '').replace(/__/g, '');
    s = s.replace(/^\s*[-*]\s+/gm, '• ');
    s = s.replace(/^(?:-{3,}|\*{3,}|_{3,})\s*$/gm, '');
    return s;
  }

  // Export a PLAIN function wrapper, not the React.memo object directly.
  // Every consumer guards with `typeof window.SyrixAnswerRenderer === 'function'`,
  // but React.memo(...) is an object (typeof 'object') — that guard silently
  // failed and disabled the smart renderer app-wide, so answers fell back to
  // raw markdown. The wrapper satisfies the function check while the inner memo
  // still skips re-render when `text` is unchanged.
  function SyrixAnswerRenderer(props) {
    return React.createElement(AnswerRendererMemo, props);
  }
  window.SyrixAnswerRenderer = SyrixAnswerRenderer;
  window.SyrixStripStreamMd = stripSyntaxForStream;
  window.SyrixNormalizeMarkdown = normalizeMarkdown;
  // Streaming edge-guard — exposed for the streaming-render regression harness
  // (_check_streaming_render.js). Pure string→string; safe to call anywhere.
  window.SyrixStreamGuard = streamGuard;
  window.SyrixRenderTokens = renderTokens;
  // Exposed for the renderer smoke harness and security regression checks.
  // It returns inert srcdoc text only; execution still requires the sandboxed
  // iframe owned by PreviewPane.
  window.SyrixBuildPreviewSrcDoc = buildPreviewSrcDoc;
  window.SyrixSelectedBrandIds = selectedBrandIds;
  window.SyrixDecorateBrandHtml = decorateBrandHtml;
})();
