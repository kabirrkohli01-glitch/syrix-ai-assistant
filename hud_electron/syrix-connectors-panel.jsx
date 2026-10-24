/* SYRIX Connectors page — full-window view over the HUD (2026-07-12).
 *
 * Renders entirely from the `syrix_connectors` key of the bridge snapshot
 * (pushed by syrix_connectors.hud_api on boot and after every mutation) and
 * talks back through SyrixBridge.send('connector_*', { data: {...} }) — the
 * same owned-command → widget-ack path every other HUD control uses.
 *
 * This is a PAGE, not a widget: fixed full-window surface with its own
 * scroll, opened from the sidebar's "Connectors" item. "Back" and Esc
 * close it (Esc closes an open connector first). The HUD underneath is untouched.
 *
 * Exposed as window.SyrixConnectorsPanel({ onClose }).
 */

(function () {
  'use strict';
  if (typeof React === 'undefined') return;
  const { useState, useEffect, useMemo, useRef } = React;

  /* ── Bridge helpers ─────────────────────────────────────────────────── */
  const CX_SEND_DEDUPE_MS = 1800;
  const cxRecentSends = new Map();

  function cxSend(command, data) {
    const payload = data || {};
    if (command === 'connector_connect') {
      const connector = String(payload.connector || '').trim().toLowerCase();
      const now = Date.now();
      const previous = Number(cxRecentSends.get(connector) || 0);
      if (connector && now - previous < CX_SEND_DEDUPE_MS) return false;
      if (connector) cxRecentSends.set(connector, now);
    }
    try {
      const B = window.SyrixBridge;
      if (B && B.send) return B.send(command, { data: payload });
    } catch (_) {}
    return false;
  }

  function useCxSnapshot() {
    const [snap, setSnap] = useState(() => {
      try { return (window.SyrixBridge && window.SyrixBridge.getSnapshot()) || {}; }
      catch (_) { return {}; }
    });
    useEffect(() => {
      const B = window.SyrixBridge;
      if (!B || !B.subscribe) return undefined;
      return B.subscribe(setSnap);
    }, []);
    return snap;
  }

  function cxAgo(ts) {
    const t = Number(ts || 0);
    if (!t) return '—';
    const s = Math.max(0, Date.now() / 1000 - t);
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  /* ── Brand tiles — each connector's own logo, not a lettermark ────────── */
  /* Brand marks use their current, recognisable app-icon treatment.  Keep
     Google on white and do not turn a company's logo into a generic initial. */
  const CX_LOGO_BG = {
    gmail:           '#ffffff',
    google_calendar: '#ffffff',
    google_drive:    '#ffffff',
    notion:          '#ffffff',
    slack:           '#ffffff',
    github:          'linear-gradient(150deg,#2b3138,#0d1117)',
    discord:         'linear-gradient(150deg,#5865f2,#4752c4)',
    spotify:         '#1ed760',
    onedrive:        'linear-gradient(150deg,#1683d8,#0a4f9e)',
    outlook:         'linear-gradient(150deg,#1a8cff,#0b5394)',
    dropbox:         'linear-gradient(150deg,#3d9cff,#0061fe)',
    filesystem:      'linear-gradient(150deg,var(--eg-gold,rgb(var(--eg-accent-rgb))),var(--eg-gold-deep,rgb(var(--eg-accent-deep-rgb))))',
    hostinger:        'linear-gradient(150deg,#673de6,#4b22b7)',
    hostinger_mail:   'linear-gradient(150deg,#673de6,#4b22b7)',
    ibkr:             '#ffffff',
    stripe:           'linear-gradient(150deg,#635bff,#3c35c8)',
    coinbase:         'linear-gradient(150deg,#1652f0,#0b36ad)',
    shopify:          '#ffffff',
    teams:            'linear-gradient(150deg,#6264a7,#40427c)',
    todoist:          'linear-gradient(150deg,#e44332,#aa2418)',
    asana:            'linear-gradient(150deg,#f06a6a,#a73dd6)',
    trello:           'linear-gradient(150deg,#0c66e4,#074aab)',
    jira:             'linear-gradient(150deg,#2684ff,#0753b8)',
    confluence:       'linear-gradient(150deg,#1868db,#093f96)',
    linear:           'linear-gradient(150deg,#5e6ad2,#343a83)',
    gitlab:           'linear-gradient(150deg,#fc6d26,#e24329)',
    hubspot:          'linear-gradient(150deg,#ff7a59,#c74729)',
  };

  function LogoGmail() {
    return (
      <svg viewBox="0 0 48 48" width="62%" height="62%">
        <path fill="#4285f4" d="M6 13.2v25h6.1V19.1z"/>
        <path fill="#34a853" d="M35.9 19.1v19.1H42v-25z"/>
        <path fill="#ea4335" d="M6 13.2 24 27l18-13.8v6.4L24 33.1 6 19.6z"/>
        <path fill="#c5221f" d="M6 13.2 10.8 9.7 24 20.1V27z"/>
        <path fill="#fbbc04" d="M24 20.1 37.2 9.7 42 13.2 24 27z"/>
      </svg>
    );
  }

  /* Compact brand marks are local SVGs: no third-party image requests and no
     generic letter placeholders on the connectors people actually use. */
  function LogoWord({ text, fill = '#fff', size = 15 }) { return <svg viewBox="0 0 48 48" width="82%" height="82%"><text x="24" y="29" textAnchor="middle" fontFamily="Arial, sans-serif" fontSize={size} fontWeight="800" fill={fill}>{text}</text></svg>; }
  function LogoIBKR() { return <svg viewBox="0 0 48 48" width="70%" height="70%" aria-label="Interactive Brokers"><path fill="#df1022" d="M9 40V23L32 4 19.7 30.3 39 40z"/><path fill="#a40817" d="M9 40h20.4L19.7 30.3z"/><circle cx="32.5" cy="27.4" r="7.4" fill="#df1022"/></svg>; }
  function LogoHostinger() { return <svg viewBox="0 0 48 48" width="70%" height="70%" aria-label="Hostinger"><path fill="#fff" d="M7 9 16 14v9l8 4.6 8-4.6V14l9-5v11.7l-8.8 5.1v10.2L24 40.8l-8.2-4.7V25.8L7 20.7z"/></svg>; }
  function LogoStripe() { return <LogoWord text="stripe" size={12} />; }
  function LogoCoinbase() { return <svg viewBox="0 0 48 48" width="67%" height="67%"><circle cx="24" cy="24" r="18" fill="#fff"/><path fill="#1652f0" d="M29 17.7a9 9 0 1 0 0 12.6l-3.2-2.5a5 5 0 1 1 0-7.6z"/></svg>; }
  function LogoShopify() { return <svg viewBox="0 0 48 48" width="72%" height="72%" aria-label="Shopify"><path fill="#95bf47" d="m10 16 28-4 3 27-18 4-16-5z"/><path fill="#5e8e3e" d="m28 12 10 4 3 23-18 4z"/><path fill="#95bf47" d="M16 16c.5-7 3.2-11 7.3-11 4.3 0 6.6 4.3 6.8 10l-3 .3c-.2-4.3-1.5-7.1-3.8-7.1-2.1 0-3.6 2.7-4.1 8z"/><text x="23" y="34" textAnchor="middle" fontFamily="Arial, sans-serif" fontSize="17" fontWeight="800" fill="#fff">S</text></svg>; }
  function LogoTeams() { return <svg viewBox="0 0 48 48" width="72%" height="72%"><circle cx="34" cy="15" r="6" fill="#c7c9ff"/><circle cx="37" cy="30" r="8" fill="#a8a9ed"/><rect x="6" y="13" width="23" height="23" rx="3" fill="#fff"/><text x="17.5" y="30" textAnchor="middle" fontFamily="Arial" fontSize="15" fontWeight="800" fill="#6264a7">T</text></svg>; }
  function LogoTodoist() { return <svg viewBox="0 0 48 48" width="70%" height="70%"><path fill="#fff" d="m9 13 15 12 15-12v6L24 31 9 19zm0 11 15 12 15-12v6L24 42 9 30z"/></svg>; }
  function LogoAsana() { return <svg viewBox="0 0 48 48" width="70%" height="70%"><circle cx="16" cy="29" r="8" fill="#fff"/><circle cx="32" cy="29" r="8" fill="#fff"/><circle cx="24" cy="15" r="8" fill="#fff"/></svg>; }
  function LogoTrello() { return <svg viewBox="0 0 48 48" width="72%" height="72%"><rect x="7" y="7" width="34" height="34" rx="5" fill="#fff"/><rect x="13" y="13" width="8" height="17" rx="2" fill="#0c66e4"/><rect x="27" y="13" width="8" height="11" rx="2" fill="#0c66e4"/></svg>; }
  function LogoJira() { return <svg viewBox="0 0 48 48" width="70%" height="70%"><path fill="#fff" d="m24 6 15 15-15 15-15-15zm0 10-5 5 5 5 5-5z"/></svg>; }
  function LogoConfluence() { return <svg viewBox="0 0 48 48" width="70%" height="70%"><path fill="#fff" d="M8 16c7-7 17-7 27 0l-4 5c-5-4-10-4-15 0zM13 27c6-5 16-5 22 1l-4 5c-5-4-10-4-14 0z"/></svg>; }
  function LogoLinear() { return <svg viewBox="0 0 48 48" width="70%" height="70%"><circle cx="24" cy="24" r="16" fill="#fff"/><path fill="#5e6ad2" d="M12 24h24a12 12 0 0 1-24 0z"/></svg>; }
  function LogoGitLab() { return <svg viewBox="0 0 48 48" width="73%" height="73%"><path fill="#fff" d="m24 40-14-13 5-15h18l5 15zm-9-13 9 8 9-8-3-9H18z"/><path fill="#fc6d26" d="m24 35-5-8h10z"/></svg>; }
  function LogoHubSpot() { return <svg viewBox="0 0 48 48" width="70%" height="70%"><circle cx="24" cy="24" r="7" fill="#fff"/><circle cx="34" cy="13" r="4" fill="#fff"/><circle cx="37" cy="34" r="4" fill="#fff"/><path stroke="#fff" strokeWidth="4" d="M28 20 32 16m-2 13 4 3M17 20l-6-5v-7"/><circle cx="11" cy="8" r="4" fill="#fff"/></svg>; }

  function LogoGoogleCalendar() {
    return (
      <svg viewBox="0 0 48 48" width="66%" height="66%">
        <rect x="4" y="6" width="40" height="38" rx="6" fill="#ffffff"/>
        <path d="M4 14a6 6 0 0 1 6-6h28a6 6 0 0 1 6 6v4H4z" fill="#1a73e8"/>
        <rect x="13" y="1.5" width="4.4" height="10" rx="2.2" fill="#8ab4f8"/>
        <rect x="30.6" y="1.5" width="4.4" height="10" rx="2.2" fill="#8ab4f8"/>
        <text x="24" y="35" textAnchor="middle" fontFamily="Arial, sans-serif" fontSize="17" fontWeight="700" fill="#3c4043">31</text>
      </svg>
    );
  }

  function LogoGoogleDrive() {
    return (
      <svg viewBox="0 0 87.3 78" width="60%" height="60%">
        <path fill="#0066da" d="m6.6 66.85 3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8h-27.5c0 1.55.4 3.1 1.2 4.5z"/>
        <path fill="#00ac47" d="m43.65 25-13.75-23.8c-1.35.8-2.5 1.9-3.3 3.3l-25.4 44a9.06 9.06 0 0 0 -1.2 4.5h27.5z"/>
        <path fill="#ea4335" d="m73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5h-27.5l5.85 11.5z"/>
        <path fill="#00832d" d="m43.65 25 13.75-23.8c-1.35-.8-2.9-1.2-4.5-1.2h-18.5c-1.6 0-3.15.45-4.5 1.2z"/>
        <path fill="#2684fc" d="m59.8 53h-32.3l-13.75 23.8c1.35.8 2.9 1.2 4.5 1.2h50.8c1.6 0 3.15-.45 4.5-1.2z"/>
        <path fill="#ffba00" d="m73.4 26.5-12.7-22c-.8-1.4-1.95-2.5-3.3-3.3l-13.75 23.8 16.15 28h27.45c0-1.55-.4-3.1-1.2-4.5z"/>
      </svg>
    );
  }

  function LogoGitHub() {
    return (
      <svg viewBox="0 0 24 24" width="58%" height="58%">
        <path fill="#ffffff" d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/>
      </svg>
    );
  }

  function LogoNotion() {
    return (
      <svg viewBox="0 0 48 48" width="66%" height="66%">
        <rect x="4" y="4" width="40" height="40" rx="8" fill="#ffffff" stroke="#191919" strokeWidth="2.5"/>
        <text x="24" y="33" textAnchor="middle" fontFamily="Georgia, serif" fontSize="23" fontWeight="800" fill="#191919">N</text>
      </svg>
    );
  }

  function LogoSlack() {
    return (
      <svg viewBox="0 0 122.8 122.8" width="60%" height="60%">
        <path fill="#e01e5a" d="M25.8 77.6c0 7.1-5.8 12.9-12.9 12.9S0 84.7 0 77.6s5.8-12.9 12.9-12.9h12.9v12.9z"/>
        <path fill="#e01e5a" d="M32.3 77.6c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9v32.3c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V77.6z"/>
        <path fill="#36c5f0" d="M45.2 25.8c-7.1 0-12.9-5.8-12.9-12.9S38.1 0 45.2 0s12.9 5.8 12.9 12.9v12.9H45.2z"/>
        <path fill="#36c5f0" d="M45.2 32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H12.9C5.8 58.1 0 52.3 0 45.2s5.8-12.9 12.9-12.9h32.3z"/>
        <path fill="#2eb67d" d="M97 45.2c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9-5.8 12.9-12.9 12.9H97V45.2z"/>
        <path fill="#2eb67d" d="M90.5 45.2c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V12.9C64.7 5.8 70.5 0 77.6 0s12.9 5.8 12.9 12.9v32.3z"/>
        <path fill="#ecb22e" d="M77.6 97c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9-12.9-5.8-12.9-12.9V97h12.9z"/>
        <path fill="#ecb22e" d="M77.6 90.5c-7.1 0-12.9-5.8-12.9-12.9s5.8-12.9 12.9-12.9h32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H77.6z"/>
      </svg>
    );
  }

  function LogoDiscord() {
    return (
      <svg viewBox="0 0 24 24" width="64%" height="64%">
        <path fill="#ffffff" d="M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z"/>
      </svg>
    );
  }

  function LogoSpotify() {
    return (
      <svg viewBox="0 0 24 24" width="64%" height="64%">
        <path fill="#000000" d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.841.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.021.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.24 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.6.18-1.2.72-1.38 4.26-1.26 11.28-1.02 15.72 1.621.539.3.719 1.02.42 1.56-.299.421-1.02.599-1.559.3z"/>
      </svg>
    );
  }

  function LogoOneDrive() {
    return (
      <svg viewBox="0 0 48 48" width="66%" height="66%">
        <path fill="#ffffff" d="M35 20.6a8.6 8.6 0 0 0-16.4-2.7A7.2 7.2 0 0 0 8.6 25a7.2 7.2 0 0 0 0 14.4H35a7.7 7.7 0 0 0 0-15.4c-.02-.9-.15-1.99 0-2.4z"/>
      </svg>
    );
  }

  function LogoOutlook() {
    return (
      <svg viewBox="0 0 48 48" width="60%" height="60%">
        <circle cx="20.5" cy="24" r="12.6" fill="none" stroke="#ffffff" strokeWidth="6.6"/>
        <rect x="31" y="10" width="13" height="21" rx="2.4" fill="#ffffff" opacity="0.92"/>
        <rect x="33.4" y="12.4" width="8.2" height="16.2" rx="1.2" fill="#1a8cff"/>
      </svg>
    );
  }

  function LogoDropbox() {
    return (
      <svg viewBox="0 0 43 40" width="60%" height="60%">
        <path fill="#ffffff" d="M10.75 0L0 6.87l10.75 6.87L21.5 6.87 10.75 0z"/>
        <path fill="#ffffff" d="M32.25 0L21.5 6.87l10.75 6.87L43 6.87 32.25 0z"/>
        <path fill="#ffffff" d="M0 20.6l10.75 6.87L21.5 20.6l-10.75-6.87L0 20.6z"/>
        <path fill="#ffffff" d="M32.25 13.73L21.5 20.6l10.75 6.87L43 20.6l-10.75-6.87z"/>
        <path fill="#ffffff" d="M10.75 29.44l10.75 6.87 10.75-6.87-10.75-6.87-10.75 6.87z"/>
      </svg>
    );
  }

  function LogoFilesystem() {
    return (
      <svg viewBox="0 0 24 24" width="54%" height="54%">
        <path fill="#ffffff" d="M3 6.2C3 5.02 3.96 4.06 5.14 4.06h4.3c.62 0 1.2.28 1.59.76l1.02 1.25h6.81c1.18 0 2.14.96 2.14 2.14v9.3c0 1.18-.96 2.14-2.14 2.14H5.14C3.96 19.65 3 18.69 3 17.5V6.2z"/>
      </svg>
    );
  }

  const CX_LOGOS = {
    gmail: LogoGmail,
    google_calendar: LogoGoogleCalendar,
    google_drive: LogoGoogleDrive,
    github: LogoGitHub,
    notion: LogoNotion,
    slack: LogoSlack,
    discord: LogoDiscord,
    spotify: LogoSpotify,
    onedrive: LogoOneDrive,
    outlook: LogoOutlook,
    dropbox: LogoDropbox,
    filesystem: LogoFilesystem,
    ibkr: LogoIBKR, hostinger: LogoHostinger, hostinger_mail: LogoHostinger,
    stripe: LogoStripe, coinbase: LogoCoinbase, shopify: LogoShopify,
    teams: LogoTeams, todoist: LogoTodoist, asana: LogoAsana, trello: LogoTrello,
    jira: LogoJira, confluence: LogoConfluence, linear: LogoLinear,
    gitlab: LogoGitLab, hubspot: LogoHubSpot,
  };
  // A few detailed marks are bundled vectors.  Everything else uses the
  // accurate local app-icon artwork above; no remote image loading is needed.
  const CX_LOGO_ASSETS = {
    gmail: 'gmail-reference',
    gitlab: 'gitlab-reference',
    hostinger: 'hostinger-reference',
    hostinger_mail: 'hostinger-reference',
    shopify: 'shopify-reference',
    coinbase: 'coinbase-reference',
    ibkr: 'ibkr-reference',
  };
  const CX_REFERENCE_LOGO_ASSETS = new Set(Object.values(CX_LOGO_ASSETS));

  function CxTile({ id, size }) {
    const px = size || 38;
    const bg = CX_LOGO_BG[id] || 'linear-gradient(150deg,#3a4250,#242a34)';
    const Logo = CX_LOGOS[id];
    const asset = CX_LOGO_ASSETS[id];
    const isReferenceAsset = asset && CX_REFERENCE_LOGO_ASSETS.has(asset);
    return (
      <div className="cx-tile" style={{ width: px, height: px, minWidth: px, background: bg }}>
        {asset ? <img className={'cx-brand-logo' + (isReferenceAsset ? ' is-reference' : '')}
          src={'assets/connector-logos/' + asset + (isReferenceAsset ? '.png' : '.svg')} alt="" />
          : Logo ? <Logo /> : <span style={{ fontSize: Math.round(px * 0.42), color: '#fff', fontWeight: 800 }}>
          {String(id || '?').replace(/_.*/, '').slice(0, 1).toUpperCase()}
        </span>}
      </div>
    );
  }

  /* ── Status ─────────────────────────────────────────────────────────── */
  const CX_HEALTH = {
    connected:    { label: 'Connected',        cls: 'ok'   },
    available:    { label: 'Not connected',    cls: 'mut'  },
    coming_soon:  { label: 'Coming soon',      cls: 'mut'  },
    needs_reauth: { label: 'Reconnect needed', cls: 'warn' },
    needs_setup:  { label: 'Setup required',   cls: 'warn' },
    needs_update: { label: 'Update required',  cls: 'err'  },
    error:        { label: 'Error',            cls: 'err'  },
    disabled:     { label: 'Disabled',         cls: 'mut'  },
  };
  const CX_CATEGORY = {
    communication: 'Communication', work: 'Work & productivity', storage: 'Files & storage',
    development: 'Development', hosting: 'Hosting', finance: 'Finance',
    commerce: 'Commerce', media: 'Media', local: 'This PC',
  };

  function cxIsConnected(c) { return c.health === 'connected' || c.health === 'needs_reauth'; }
  function cxFlowFor(c, authFlow) { return authFlow && authFlow.connector === c.id ? authFlow : null; }
  function cxFlowBusy(flow) {
    return !!(flow && (flow.status === 'opening_browser'
      || flow.status === 'waiting_consent' || flow.status === 'verifying_credentials'));
  }

  function CxStatus({ health }) {
    const h = CX_HEALTH[health] || { label: health || '—', cls: 'mut' };
    return <span className={'cx-status is-' + h.cls}><span className="cx-status-dot" />{h.label}</span>;
  }

  function CxSwitch({ on, disabled, onFlip, label }) {
    return (
      <button type="button" role="switch" aria-checked={!!on} aria-label={label}
        className={'cx-switch' + (on ? ' is-on' : '')} disabled={!!disabled}
        onClick={disabled ? undefined : onFlip}>
        <span className="cx-switch-knob" />
      </button>
    );
  }

  /* The one thing a card offers, in words that say what will happen. */
  function cxPrimary(c, flow) {
    if (!c.implemented) return null;
    if (cxFlowBusy(flow)) return { label: 'Waiting for sign-in…', busy: true };
    if (c.health === 'needs_reauth') return { label: 'Reconnect', connect: true };
    if (c.health === 'connected') return { label: 'Manage' };
    if (c.health === 'needs_setup') return { label: 'Set up' };
    if (c.health === 'needs_update' || c.health === 'disabled') return { label: 'Details' };
    return { label: 'Connect', connect: true };
  }

  /* ── Card ───────────────────────────────────────────────────────────
     One row-shaped card: the app's mark, its name with its state beside it,
     two lines saying what it does, and one round action on the right —
     + to connect, a tick when connected, an arrow when it needs a look. */
  const CX_ICON = {
    plus:  <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M12 5v14M5 12h14"/></svg>,
    check: <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>,
    more:  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 6l6 6-6 6"/></svg>,
  };
  function CxCard({ c, authFlow, onOpen, index }) {
    const flow = cxFlowFor(c, authFlow);
    const soon = !c.implemented;
    const act = cxPrimary(c, flow);
    const connected = cxIsConnected(c);
    const h = CX_HEALTH[c.health] || null;
    const badge = soon ? { label: 'Coming soon', cls: 'mut' }
      : c.health === 'available' ? null
      : h;
    return (
      <div className={'cx-card' + (soon ? ' is-soon' : '') + (connected ? ' is-connected' : '')}
        style={{ animationDelay: Math.min(index || 0, 18) * 18 + 'ms' }}
        role={soon ? undefined : 'button'} tabIndex={soon ? -1 : 0}
        title={c.account && connected ? c.name + ' — ' + c.account : c.name}
        onClick={soon ? undefined : () => onOpen(c.id)}
        onKeyDown={e => { if (!soon && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); onOpen(c.id); } }}>
        <CxTile id={c.id} size={40} />
        <div className="cx-card-body">
          <div className="cx-card-line">
            <span className="cx-card-name">{c.name}</span>
            {badge && <span className={'cx-badge is-' + badge.cls}>{badge.label}</span>}
          </div>
          <div className="cx-card-desc">{connected && c.account ? c.account : c.description}</div>
        </div>
        {act && (
          <button type="button"
            className={'cx-act' + (connected ? ' is-done' : act.connect ? ' is-add' : '')}
            disabled={act.busy}
            aria-label={act.label + ' ' + c.name} title={act.label}
            onClick={e => {
              e.stopPropagation();
              onOpen(c.id);
              if (act.connect) cxSend('connector_connect', { connector: c.id });
            }}>
            {act.busy ? <span className="cx-spin" /> : connected && c.health === 'connected' ? CX_ICON.check : act.connect ? CX_ICON.plus : CX_ICON.more}
          </button>
        )}
      </div>
    );
  }

  /* Section marks for the Discover view. */
  const CX_SECTION_ICON = {
    connected:     <path d="M5 12.5l4.5 4.5L19 7.5"/>,
    popular:       <><path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/></>,
    communication: <path d="M4 5h16v11H8l-4 4z"/>,
    work:          <><path d="M9 6h11M9 12h11M9 18h11"/><path d="M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/></>,
    storage:       <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>,
    development:   <path d="M8 8l-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>,
    hosting:       <><rect x="4" y="4" width="16" height="7" rx="1.5"/><rect x="4" y="13" width="16" height="7" rx="1.5"/><path d="M8 7.5h.01M8 16.5h.01"/></>,
    finance:       <><path d="M4 19V5M4 19h16"/><path d="M8 15l4-4 3 3 5-6"/></>,
    commerce:      <><path d="M6 7h12l-1 13H7z"/><path d="M9 7a3 3 0 0 1 6 0"/></>,
    media:         <><circle cx="12" cy="12" r="9"/><path d="M10 8.5l5 3.5-5 3.5z"/></>,
    local:         <><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></>,
    other:         <><circle cx="6" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="18" cy="12" r="1.5"/></>,
  };
  function CxSectionHead({ id, label, onViewAll, count }) {
    return (
      <div className="cx-sec-head">
        <h2 className="cx-sec-title">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{CX_SECTION_ICON[id] || CX_SECTION_ICON.other}</svg>
          {label}
        </h2>
        {onViewAll && <button type="button" className="cx-viewall" onClick={onViewAll}>View all{count ? ' ' + count : ''}</button>}
      </div>
    );
  }
  // Shown first on Discover, in this order, when implemented.
  const CX_POPULAR = ['gmail', 'google_calendar', 'google_drive', 'github', 'notion', 'outlook', 'slack', 'spotify', 'dropbox'];

  /* ── Detail sheet ───────────────────────────────────────────────────── */
  function CxSheet({ c, activity, authFlow, secureStore, onClose }) {
    const [confirmDisc, setConfirmDisc] = useState(false);
    useEffect(() => { setConfirmDisc(false); }, [c && c.id]);
    useEffect(() => {
      if (!confirmDisc) return undefined;
      const t = setTimeout(() => setConfirmDisc(false), 5000);
      return () => clearTimeout(t);
    }, [confirmDisc]);
    if (!c) return null;
    const connected = cxIsConnected(c);
    const flow = cxFlowFor(c, authFlow);
    const busy = cxFlowBusy(flow);
    const perms = Array.isArray(c.permissions) ? c.permissions : [];
    const tasks = Array.isArray(c.tasks) ? c.tasks : [];
    const acts = (activity || []).filter(a => a.connector === c.id).slice(0, 8);
    const allowedCount = perms.filter(p => p.state === 'allowed').length;
    return (
      <React.Fragment>
        <div className="cx-scrim" onClick={onClose} aria-hidden="true" />
        <aside className="cx-sheet" role="dialog" aria-label={c.name + ' connector'} key={c.id}>
          <div className="cx-sheet-head">
            <CxTile id={c.id} size={52} />
            <div className="cx-sheet-title">
              <div className="cx-sheet-name">{c.name}</div>
              <CxStatus health={c.health} />
              {c.account ? <div className="cx-sheet-account">{c.account}</div> : null}
            </div>
            <button type="button" className="cx-icon-btn" onClick={onClose} title="Close (Esc)" aria-label="Close">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>
            </button>
          </div>

          <div className="cx-sheet-body">
            <p className="cx-sheet-desc">{c.description}</p>

            {flow && (
              <div className={'cx-banner ' + (flow.status === 'failed' ? 'is-err'
                              : flow.status === 'connected' ? 'is-ok' : 'is-info')}>
                {busy ? <span className="cx-spin" /> : null}
                <span>{flow.status === 'failed'
                  ? ('Sign-in failed: ' + (flow.detail || 'unknown error'))
                  : flow.status === 'connected'
                    ? 'Connected.'
                    : flow.status === 'setup_opened'
                      ? (flow.detail || 'Finish setup in your browser, then restart SYRIX and press Connect.')
                      : flow.status === 'verifying_credentials'
                        ? (flow.detail || 'Checking the credentials…')
                        : 'Finish signing in in your browser — SYRIX is waiting.'}</span>
              </div>
            )}

            {c.health === 'needs_setup' && (
              <div className="cx-note">
                <b>Setup required.</b> {c.setup_hint || "Add this connector's credential to the local .env file."}
                {' '}Then restart SYRIX and press Connect. The secret is moved into encrypted Windows storage and never shown here.
              </div>
            )}

            {c.implemented && (!connected || c.health === 'needs_reauth') && (
              <button type="button" className="cx-btn is-primary is-wide"
                disabled={busy || c.health === 'needs_update' || c.health === 'disabled'}
                onClick={() => cxSend('connector_connect', { connector: c.id })}>
                {busy ? <span className="cx-spin" /> : null}
                {busy ? 'Waiting for sign-in…' : (c.health === 'needs_reauth' ? 'Reconnect ' : 'Connect ') + c.name}
              </button>
            )}

            {perms.length > 0 && (
              <section className="cx-section">
                <div className="cx-section-head">
                  <span>What SYRIX may do</span>
                  <span className="cx-section-meta">{allowedCount} of {perms.length} allowed</span>
                </div>
                {!connected && <div className="cx-section-hint">Connect first to change these.</div>}
                <div className="cx-list">
                  {perms.map(p => {
                    const allowed = p.state === 'allowed';
                    return (
                      <div key={p.key} className={'cx-item' + (p.locked ? ' is-locked' : '')}>
                        <div className="cx-item-text">
                          <div className="cx-item-label">{p.label}{p.locked ? <span className="cx-lock">Not available yet</span> : null}</div>
                          <div className="cx-item-desc">{p.description}</div>
                        </div>
                        <CxSwitch on={allowed} label={p.label}
                          disabled={!!p.locked || !connected}
                          onFlip={() => cxSend('connector_set_permission',
                            { connector: c.id, action: p.key, allowed: !allowed })} />
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            {tasks.length > 0 && (
              <section className="cx-section">
                <div className="cx-section-head"><span>Runs in the background</span></div>
                <div className="cx-list">
                  {tasks.map(t => (
                    <div key={t.key} className="cx-item">
                      <div className="cx-item-text">
                        <div className="cx-item-label">{t.label}</div>
                        <div className="cx-item-desc">{t.description}</div>
                      </div>
                      <CxSwitch on={!!t.enabled} label={t.label} disabled={!connected}
                        onFlip={() => cxSend('connector_set_task',
                          { connector: c.id, task: t.key, enabled: !t.enabled })} />
                    </div>
                  ))}
                </div>
              </section>
            )}

            <section className="cx-section">
              <div className="cx-section-head"><span>Security</span></div>
              <div className="cx-kv"><span>Last synced</span><b>{cxAgo(c.last_sync)}</b></div>
              <div className="cx-kv"><span>Sign-in stored</span>
                <b>{secureStore && secureStore.available ? 'Encrypted on this PC (Windows DPAPI)' : 'Secure storage unavailable'}</b></div>
              <div className="cx-kv"><span>Your data</span>
                <b>Titles and metadata first; full content only when you ask, and never stored</b></div>
              {c.last_error ? <div className="cx-note is-err">Last error: {c.last_error}</div> : null}
            </section>

            {acts.length > 0 && (
              <section className="cx-section">
                <div className="cx-section-head"><span>Recent activity</span></div>
                <div className="cx-activity">
                  {acts.map((a, i) => (
                    <div key={i} className="cx-act-row">
                      <span className={'cx-act-dot ' + (a.result === 'ok' ? 'is-ok' : 'is-no')} />
                      <span className="cx-act-what">{a.action}</span>
                      <span className="cx-act-time">{cxAgo(a.ts)}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>

          {connected && (
            <div className="cx-sheet-foot">
              <button type="button" className={'cx-btn is-danger' + (confirmDisc ? ' is-armed' : '')}
                onClick={() => {
                  if (!confirmDisc) { setConfirmDisc(true); return; }
                  setConfirmDisc(false);
                  cxSend('connector_disconnect', { connector: c.id });
                }}>
                {confirmDisc ? 'Click again to disconnect' : 'Disconnect'}
              </button>
            </div>
          )}
        </aside>
      </React.Fragment>
    );
  }

  /* ── Page ───────────────────────────────────────────────────────────── */
  function SyrixConnectorsPanel({ onClose }) {
    const snap = useCxSnapshot();
    const conn = (snap && (snap.syrix_connectors
      || (snap.raw && snap.raw.syrix_connectors))) || null;

    const [query, setQuery] = useState('');
    // Discover shows sections; the other views are one filtered grid.
    const [view, setView] = useState('discover');
    const [category, setCategory] = useState('');
    const [selectedId, setSelectedId] = useState(null);
    const [showSoon, setShowSoon] = useState(false);
    const refreshedRef = useRef(false);
    const searchRef = useRef(null);

    // One refresh on open so the page is current even if the backend booted
    // before the HUD (the state key is also seeded at backend boot).
    useEffect(() => {
      if (refreshedRef.current) return;
      refreshedRef.current = true;
      cxSend('connectors_refresh');
    }, []);

    // Esc closes the open connector first, then the page. "/" focuses search.
    const selectedRef = useRef(null);
    selectedRef.current = selectedId;
    useEffect(() => {
      function onKey(e) {
        if (e.key === 'Escape') {
          if (selectedRef.current) { setSelectedId(null); return; }
          try { onClose && onClose(); } catch (_) {}
        } else if (e.key === '/' && document.activeElement !== searchRef.current
                   && !/input|textarea/i.test((document.activeElement && document.activeElement.tagName) || '')) {
          e.preventDefault();
          try { searchRef.current && searchRef.current.focus(); } catch (_) {}
        }
      }
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [onClose]);

    const list = (conn && Array.isArray(conn.connectors)) ? conn.connectors : [];
    const q = query.trim().toLowerCase();
    const matches = useMemo(() => list.filter(c => {
      if (q && !(c.name + ' ' + c.description).toLowerCase().includes(q)) return false;
      if (category && c.category !== category) return false;
      if (view === 'connected') return cxIsConnected(c);
      if (view === 'available') return !cxIsConnected(c) && c.implemented;
      return true;
    }), [list, q, view, category]);

    const ready = matches.filter(c => c.implemented);
    const soonRows = matches.filter(c => !c.implemented);
    const connectedRows = ready.filter(cxIsConnected);
    const popularRows = CX_POPULAR.map(id => ready.find(c => c.id === id)).filter(Boolean);
    const sections = Object.keys(CX_CATEGORY)
      .map(key => ({ key, label: CX_CATEGORY[key], rows: ready.filter(c => c.category === key) }))
      .filter(g => g.rows.length > 0);
    const uncategorised = ready.filter(c => !CX_CATEGORY[c.category]);
    if (uncategorised.length) sections.push({ key: 'other', label: 'Other', rows: uncategorised });
    const categories = Object.keys(CX_CATEGORY).filter(key => list.some(c => c.category === key));
    // Searching or picking a category turns Discover into one plain grid.
    const discover = view === 'discover' && !q && !category;

    const counts = {
      connected: list.filter(cxIsConnected).length,
      available: list.filter(c => c.implemented && !cxIsConnected(c)).length,
    };
    const selected = list.find(c => c.id === selectedId) || null;
    const pending = (conn && Array.isArray(conn.pending_confirm)) ? conn.pending_confirm : [];
    const authFlow = conn ? conn.auth_flow : null;
    const secureStore = conn ? conn.secure_store : null;
    const disabledAll = conn && conn.enabled === false;
    let cardIndex = 0;
    const card = c => <CxCard key={c.id} c={c} authFlow={authFlow} onOpen={setSelectedId} index={cardIndex++} />;
    const VIEWS = [['discover', 'Discover'], ['all', 'All'], ['connected', 'Connected'], ['available', 'Available']];

    const page = (
      <div className="cx-root" role="dialog" aria-label="Connectors">
        <style>{CX_CSS}</style>
        <div className="cx-scroll">
          <div className="cx-wrap">
            <button type="button" className="cx-back" onClick={onClose} title="Back to SYRIX (Esc)">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
              SYRIX
            </button>
            <h1 className="cx-h1">Connectors</h1>
            <p className="cx-sub">Connect services so SYRIX can access and act on your data. Sign-ins are encrypted on this PC, and SYRIX asks before anything that changes something.</p>

            <label className="cx-search">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
              <input ref={searchRef} value={query} onChange={e => setQuery(e.target.value)}
                placeholder="Search all connectors" spellCheck={false} aria-label="Search all connectors" />
              {query ? <button type="button" className="cx-search-clear" onClick={() => setQuery('')} aria-label="Clear search">×</button>
                : <kbd>/</kbd>}
            </label>

            <div className="cx-bar">
              <nav className="cx-pills" aria-label="Show">
                {VIEWS.map(([id, label]) => (
                  <button key={id} type="button" className={'cx-pill' + (view === id ? ' is-active' : '')}
                    aria-pressed={view === id} onClick={() => setView(id)}>
                    {label}
                    {id === 'connected' && counts.connected ? <span className="cx-pill-n">{counts.connected}</span> : null}
                  </button>
                ))}
              </nav>
              <label className="cx-cat">
                <select value={category} onChange={e => setCategory(e.target.value)} aria-label="Category">
                  <option value="">All categories</option>
                  {categories.map(k => <option key={k} value={k}>{CX_CATEGORY[k]}</option>)}
                </select>
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>
              </label>
            </div>

            {disabledAll && (
              <div className="cx-banner is-err">Connectors are switched off by the <code>connectors_enabled</code> setting in config.json.</div>
            )}

            {pending.map(p => (
              <div key={p.confirm_id} className="cx-banner is-warn cx-approval">
                <span>SYRIX wants to <b>{p.action_label}</b> on <b>{p.connector}</b>
                  {p.reason ? <span className="cx-dim"> — {p.reason}</span> : null}</span>
                <span className="cx-banner-actions">
                  <button type="button" className="cx-btn is-quiet"
                    onClick={() => cxSend('connector_deny_action', { confirm_id: p.confirm_id })}>Deny</button>
                  <button type="button" className="cx-btn is-primary"
                    onClick={() => cxSend('connector_confirm_action', { confirm_id: p.confirm_id })}>Allow</button>
                </span>
              </div>
            ))}

            {!conn && (
              <div className="cx-empty"><span className="cx-spin" /> Waiting for SYRIX…</div>
            )}

            {discover ? (
              <>
                {connectedRows.length > 0 && (
                  <section className="cx-group">
                    <CxSectionHead id="connected" label="Connected" onViewAll={() => setView('connected')} />
                    <div className="cx-grid">{connectedRows.slice(0, 6).map(card)}</div>
                  </section>
                )}
                {popularRows.length > 0 && (
                  <section className="cx-group">
                    <CxSectionHead id="popular" label="Popular" />
                    <div className="cx-grid">{popularRows.slice(0, 6).map(card)}</div>
                  </section>
                )}
                {sections.map(g => (
                  <section className="cx-group" key={g.key}>
                    <CxSectionHead id={g.key} label={g.label} count={g.rows.length > 6 ? g.rows.length : 0}
                      onViewAll={g.rows.length > 6 ? () => { setView('all'); setCategory(g.key); } : null} />
                    <div className="cx-grid">{g.rows.slice(0, 6).map(card)}</div>
                  </section>
                ))}
              </>
            ) : (
              <section className="cx-group">
                {category && <CxSectionHead id={category} label={CX_CATEGORY[category] || 'Other'} />}
                <div className="cx-grid">{ready.map(card)}</div>
              </section>
            )}

            {soonRows.length > 0 && (view === 'discover' || view === 'all') && (
              <section className="cx-group">
                <button type="button" className="cx-group-toggle" onClick={() => setShowSoon(v => !v)} aria-expanded={showSoon}>
                  <span className={'cx-caret' + (showSoon ? ' is-open' : '')} aria-hidden="true">›</span>
                  Coming soon <span className="cx-pill-n">{soonRows.length}</span>
                </button>
                {showSoon && (
                  <div className="cx-grid">
                    {soonRows.map(c => <CxCard key={c.id} c={c} authFlow={authFlow} onOpen={() => {}} index={0} />)}
                  </div>
                )}
              </section>
            )}

            {conn && matches.length === 0 && (
              <div className="cx-empty">No connectors match{query ? ' “' + query + '”' : ' this view'}.</div>
            )}
          </div>
        </div>

        <CxSheet c={selected} activity={(conn && conn.activity) || []}
          authFlow={authFlow} secureStore={secureStore}
          onClose={() => setSelectedId(null)} />
      </div>
    );

    // Portal straight onto document.body: rendered inside the aurora shell
    // tree, any transformed/filtered ancestor creates a stacking context that
    // traps the page UNDER the fixed HUD chrome (island 9000, chat 9100,
    // bars 8500, rail 8000) no matter what z-index it asks for.
    if (typeof ReactDOM !== 'undefined' && ReactDOM.createPortal) {
      return ReactDOM.createPortal(page, document.body);
    }
    return page;
  }

  /* ── Styles (scoped, injected with the page) ──────────────────────────
     Painted entirely from the --eg-* tokens aurora-glass.css sets on
     body.theme-aurora-glass, so the page follows the Aura and Dark palettes
     with the rest of the HUD. It is portaled onto document.body, so the
     tokens inherit straight through. */
  const CX_CSS = `
  .cx-root {
    /* Above all HUD chrome — island (9000), chat (9100), bars (8500),
       rail (8000), history (8600). */
    position: fixed; inset: 0; z-index: 9550;
    display: flex; flex-direction: column; overflow: hidden;
    background: var(--bg, #080604);
    color: var(--eg-text-1); font-family: Inter, system-ui, sans-serif;
    animation: cxIn .32s var(--eg-spring, cubic-bezier(.32,.72,0,1)) both;
  }
  @keyframes cxIn { from { opacity: 0; transform: scale(.99); } to { opacity: 1; transform: none; } }

  .cx-scroll { flex: 1; overflow-y: auto; overflow-x: hidden; }
  .cx-scroll::-webkit-scrollbar { width: 8px; }
  .cx-scroll::-webkit-scrollbar-thumb { background: var(--eg-stroke-2); border-radius: 4px; }
  .cx-wrap { max-width: 1140px; margin: 0 auto; padding: 22px 40px 56px; }

  .cx-back {
    display: inline-flex; align-items: center; gap: 4px; margin: 0 0 18px -8px;
    padding: 6px 12px 6px 6px; border-radius: 999px; cursor: pointer;
    font: 500 13px Inter, sans-serif; color: var(--eg-text-2);
    background: none; border: none;
    transition: background .15s, color .15s;
  }
  .cx-back:hover { background: var(--eg-hover); color: var(--eg-text-1); }
  .cx-h1 { margin: 0; font: 600 28px/1.2 Inter, sans-serif; letter-spacing: -.015em; color: var(--eg-text-1); }
  .cx-sub { margin: 6px 0 20px; font: 450 14.5px/1.5 Inter, sans-serif; color: var(--eg-text-2); max-width: 760px; }

  .cx-search {
    display: flex; align-items: center; gap: 10px; width: 100%; box-sizing: border-box;
    padding: 0 12px 0 14px; height: 44px; border-radius: 12px;
    background: var(--eg-glass-1); border: 1px solid var(--eg-stroke-2); color: var(--eg-text-3);
    transition: border-color .15s, background .15s, box-shadow .15s;
  }
  .cx-search:focus-within { border-color: rgba(var(--eg-accent-rgb), .45); background: var(--eg-glass-2); box-shadow: 0 0 0 4px rgba(var(--eg-accent-rgb), .07); }
  .cx-search input { flex: 1; min-width: 0; background: none; border: none; outline: none; color: var(--eg-text-1); font: 450 14px Inter, sans-serif; }
  .cx-search input::placeholder { color: var(--eg-text-3); }
  .cx-search kbd { font: 600 10.5px 'JetBrains Mono', monospace; color: var(--eg-text-3); border: 1px solid var(--eg-stroke-2); border-radius: 5px; padding: 1px 6px; }
  .cx-search-clear { background: none; border: none; color: var(--eg-text-2); font-size: 18px; line-height: 1; cursor: pointer; padding: 0 2px; }

  .cx-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin: 18px 0 8px; }
  .cx-pills { display: flex; flex-wrap: wrap; gap: 8px; }
  .cx-pill, .cx-cat {
    display: inline-flex; align-items: center; gap: 7px; height: 34px; padding: 0 15px; box-sizing: border-box;
    border-radius: 999px; border: 1px solid var(--eg-stroke-2); background: none; cursor: pointer;
    font: 500 13.5px Inter, sans-serif; color: var(--eg-text-1);
    transition: background .15s, border-color .15s, color .15s;
  }
  .cx-pill:hover, .cx-cat:hover { background: var(--eg-hover); }
  .cx-pill.is-active { background: var(--eg-glass-3); border-color: transparent; }
  .cx-pill-n { font: 600 11px Inter, sans-serif; padding: 1px 7px; border-radius: 999px; background: var(--eg-glass-2); color: var(--eg-text-2); }
  .cx-cat { position: relative; padding: 0 12px 0 15px; }
  .cx-cat select {
    appearance: none; -webkit-appearance: none; background: none; border: none; outline: none; cursor: pointer;
    font: inherit; color: inherit; padding-right: 4px;
  }
  .cx-cat select option { background: #1c1c1e; color: #fff; }
  .cx-cat svg { color: var(--eg-text-2); pointer-events: none; }

  .cx-group { margin-top: 26px; }
  .cx-sec-head { display: flex; align-items: center; justify-content: space-between; margin: 0 0 10px; }
  .cx-sec-title { display: inline-flex; align-items: center; gap: 8px; margin: 0; font: 500 14px Inter, sans-serif; color: var(--eg-text-2); }
  .cx-sec-title svg { color: var(--eg-text-3); }
  .cx-viewall { background: none; border: none; cursor: pointer; padding: 4px 6px; border-radius: 6px; font: 500 13px Inter, sans-serif; color: var(--eg-text-3); transition: color .15s, background .15s; }
  .cx-viewall:hover { color: var(--eg-text-1); background: var(--eg-hover); }
  .cx-group-toggle {
    display: inline-flex; align-items: center; gap: 8px; cursor: pointer; margin-bottom: 10px;
    background: none; border: none; padding: 0; font: 500 14px Inter, sans-serif; color: var(--eg-text-2);
  }
  .cx-group-toggle:hover { color: var(--eg-text-1); }
  .cx-caret { display: inline-block; font-size: 16px; transition: transform .2s var(--eg-spring); }
  .cx-caret.is-open { transform: rotate(90deg); }
  .cx-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }

  .cx-card {
    position: relative; display: flex; align-items: center; gap: 14px; min-width: 0; min-height: 72px; box-sizing: border-box;
    padding: 14px 14px 14px 16px; border-radius: 12px; cursor: pointer;
    background: var(--eg-glass-1); border: 1px solid var(--eg-stroke);
    transition: background .16s, border-color .16s;
    animation: cxCardIn .34s var(--eg-spring) both;
  }
  @keyframes cxCardIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  .cx-card:hover { background: var(--eg-glass-2); border-color: var(--eg-stroke-2); }
  .cx-card:focus-visible { outline: 2px solid rgba(var(--eg-accent-rgb), .6); outline-offset: 2px; }
  .cx-card.is-soon { cursor: default; opacity: .5; }
  .cx-card.is-soon:hover { background: var(--eg-glass-1); border-color: var(--eg-stroke); }
  .cx-card-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
  .cx-card-line { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .cx-card-name { font: 600 14.5px Inter, sans-serif; color: var(--eg-text-1); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cx-badge { flex-shrink: 0; font: 500 12px Inter, sans-serif; color: var(--eg-text-3); white-space: nowrap; }
  .cx-badge.is-ok { color: #4cd17f; }
  .cx-badge.is-warn { color: #f2c14e; }
  .cx-badge.is-err { color: #ee8484; }
  .cx-card-desc {
    font: 450 12.5px/1.4 Inter, sans-serif; color: var(--eg-text-2);
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .cx-act {
    flex-shrink: 0; width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center; cursor: pointer;
    background: none; border: none; color: var(--eg-text-2);
    transition: background .15s, color .15s, transform .15s var(--eg-spring);
  }
  .cx-act:hover:not(:disabled) { background: var(--eg-glass-3); color: var(--eg-text-1); }
  .cx-act:active:not(:disabled) { transform: scale(.9); }
  .cx-act.is-done { color: #4cd17f; }
  .cx-act:disabled { cursor: default; }

  .cx-status { display: inline-flex; align-items: center; gap: 6px; font: 600 11px Inter, sans-serif; color: var(--eg-text-3); white-space: nowrap; }
  .cx-status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--eg-text-3); flex-shrink: 0; }
  .cx-status.is-ok { color: #6fd99a; }
  .cx-status.is-ok .cx-status-dot { background: #6fd99a; box-shadow: 0 0 0 3px rgba(111, 217, 154, .16); }
  .cx-status.is-warn { color: #f2c14e; }
  .cx-status.is-warn .cx-status-dot { background: #f2c14e; }
  .cx-status.is-err { color: #ee8484; }
  .cx-status.is-err .cx-status-dot { background: #ee8484; }

  .cx-btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 7px; cursor: pointer;
    font: 600 12.5px Inter, sans-serif; padding: 8px 16px; border-radius: 10px;
    border: 1px solid var(--eg-stroke-2); background: var(--eg-glass-2); color: var(--eg-text-1);
    transition: background .15s, border-color .15s, transform .12s var(--eg-spring), filter .15s;
  }
  .cx-btn:hover:not(:disabled) { background: var(--eg-glass-3); }
  .cx-btn:active:not(:disabled) { transform: scale(.96); }
  .cx-btn:disabled { opacity: .5; cursor: default; }
  .cx-btn.is-primary { background: rgb(var(--eg-accent-rgb)); border-color: rgb(var(--eg-accent-rgb)); color: var(--bg); }
  .cx-btn.is-primary:hover:not(:disabled) { background: rgb(var(--eg-accent-bright-rgb)); filter: none; }
  .cx-btn.is-quiet { background: transparent; }
  .cx-btn.is-wide { width: 100%; padding: 11px 16px; font-size: 13px; }
  .cx-btn.is-danger { color: #ee8484; border-color: rgba(238, 132, 132, .35); background: transparent; }
  .cx-btn.is-danger:hover { background: rgba(238, 132, 132, .10); }
  .cx-btn.is-danger.is-armed { background: rgba(238, 132, 132, .18); border-color: rgba(238, 132, 132, .7); }

  .cx-banner {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    padding: 12px 14px; border-radius: 12px; margin-bottom: 12px;
    font: 500 12.5px/1.45 Inter, sans-serif; color: var(--eg-text-1);
    background: var(--eg-glass-2); border: 1px solid var(--eg-stroke-2);
  }
  .cx-banner.is-warn { border-color: rgba(242, 193, 78, .45); background: rgba(242, 193, 78, .08); }
  .cx-banner.is-err { border-color: rgba(238, 132, 132, .45); background: rgba(238, 132, 132, .08); }
  .cx-banner.is-ok { border-color: rgba(111, 217, 154, .45); background: rgba(111, 217, 154, .08); }
  .cx-banner code { font-family: 'JetBrains Mono', monospace; font-size: 11.5px; }
  .cx-approval > span:first-child { flex: 1; min-width: 200px; }
  .cx-banner-actions { display: inline-flex; gap: 8px; margin-left: auto; }
  .cx-dim { color: var(--eg-text-3); }
  .cx-empty { display: flex; align-items: center; justify-content: center; gap: 10px; padding: 48px 0; color: var(--eg-text-3); font: 500 13px Inter, sans-serif; }

  .cx-tile {
    position: relative; overflow: hidden; flex-shrink: 0;
    border-radius: 11px; display: flex; align-items: center; justify-content: center;
    color: #fff; font-weight: 800; font-family: Inter, sans-serif; user-select: none;
    box-shadow: inset 0 0 0 1px rgba(255,255,255,.14), 0 4px 12px rgba(0,0,0,.30);
  }
  .cx-tile svg { display: block; }
  .cx-brand-logo { display: block; width: 66%; height: 66%; object-fit: contain; }
  .cx-brand-logo.is-reference { width: 100%; height: 100%; object-fit: cover; }

  /* Detail sheet — slides in over the page from the right. */
  .cx-scrim { position: fixed; inset: 0; z-index: 1; background: rgba(0,0,0,.45); animation: cxFade .2s ease both; }
  @keyframes cxFade { from { opacity: 0; } to { opacity: 1; } }
  .cx-sheet {
    position: fixed; top: 0; right: 0; bottom: 0; z-index: 2;
    width: min(460px, 100vw); display: flex; flex-direction: column;
    background: var(--eg-surface-2); border-left: 1px solid var(--eg-stroke-2);
    box-shadow: -30px 0 80px -30px rgba(0,0,0,.8);
    animation: cxSheetIn .36s var(--eg-spring) both;
  }
  @keyframes cxSheetIn { from { transform: translateX(40px); opacity: 0; } to { transform: none; opacity: 1; } }
  .cx-sheet-head { display: flex; align-items: flex-start; gap: 14px; padding: 22px 20px 16px; border-bottom: 1px solid var(--eg-stroke); }
  .cx-sheet-title { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
  .cx-sheet-name { font: 700 18px Inter, sans-serif; }
  .cx-sheet-account { font: 500 12px Inter, sans-serif; color: var(--eg-text-3); }
  .cx-sheet-body { flex: 1; overflow-y: auto; padding: 16px 20px 24px; display: flex; flex-direction: column; gap: 14px; }
  .cx-sheet-desc { margin: 0; font: 500 13px/1.55 Inter, sans-serif; color: var(--eg-text-2); }
  .cx-sheet-foot { padding: 14px 20px; border-top: 1px solid var(--eg-stroke); display: flex; justify-content: flex-end; }
  .cx-icon-btn {
    display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px;
    border-radius: 9px; border: none; background: none; color: var(--eg-text-3); cursor: pointer;
    transition: background .15s, color .15s;
  }
  .cx-icon-btn:hover { background: var(--eg-glass-2); color: var(--eg-text-1); }

  .cx-section { display: flex; flex-direction: column; gap: 8px; }
  .cx-section-head { display: flex; justify-content: space-between; align-items: baseline; font: 650 12px Inter, sans-serif; letter-spacing: .03em; text-transform: uppercase; color: var(--eg-text-3); }
  .cx-section-meta { font: 500 11px Inter, sans-serif; letter-spacing: 0; text-transform: none; }
  .cx-section-hint { font: 500 12px Inter, sans-serif; color: var(--eg-text-3); }
  .cx-list { display: flex; flex-direction: column; border: 1px solid var(--eg-stroke); border-radius: 12px; overflow: hidden; }
  .cx-item { display: flex; align-items: center; gap: 14px; padding: 12px 14px; background: var(--eg-glass-1); }
  .cx-item + .cx-item { border-top: 1px solid var(--eg-stroke); }
  .cx-item.is-locked { opacity: .6; }
  .cx-item-text { flex: 1; min-width: 0; }
  .cx-item-label { font: 600 13px Inter, sans-serif; color: var(--eg-text-1); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .cx-item-desc { font: 500 11.5px/1.45 Inter, sans-serif; color: var(--eg-text-3); margin-top: 2px; }
  .cx-lock { font: 600 10px Inter, sans-serif; color: var(--eg-text-3); border: 1px solid var(--eg-stroke-2); border-radius: 999px; padding: 1px 7px; }

  .cx-switch {
    position: relative; flex-shrink: 0; width: 38px; height: 22px; border-radius: 999px; cursor: pointer;
    background: var(--eg-glass-3); border: 1px solid var(--eg-stroke-2);
    transition: background .2s, border-color .2s;
  }
  .cx-switch-knob {
    position: absolute; top: 2px; left: 2px; width: 16px; height: 16px; border-radius: 50%;
    background: var(--eg-text-2); box-shadow: 0 1px 3px rgba(0,0,0,.35);
    transition: transform .22s var(--eg-spring), background .2s;
  }
  .cx-switch.is-on { background: rgb(var(--eg-accent-rgb)); border-color: rgb(var(--eg-accent-rgb)); }
  .cx-switch.is-on .cx-switch-knob { transform: translateX(16px); background: var(--bg); }
  .cx-switch:disabled { opacity: .45; cursor: default; }

  .cx-kv { display: flex; justify-content: space-between; gap: 16px; font: 500 12px/1.45 Inter, sans-serif; color: var(--eg-text-3); }
  .cx-kv b { font-weight: 550; color: var(--eg-text-2); text-align: right; max-width: 62%; }
  .cx-note { font: 500 12px/1.5 Inter, sans-serif; color: var(--eg-text-2); padding: 11px 13px; border-radius: 11px; background: var(--eg-glass-1); border: 1px solid var(--eg-stroke); }
  .cx-note.is-err { color: #ee8484; border-color: rgba(238, 132, 132, .35); }
  .cx-activity { display: flex; flex-direction: column; gap: 2px; }
  .cx-act-row { display: flex; align-items: center; gap: 10px; padding: 6px 2px; font: 500 12px Inter, sans-serif; }
  .cx-act-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  .cx-act-dot.is-ok { background: #6fd99a; }
  .cx-act-dot.is-no { background: #ee8484; }
  .cx-act-what { flex: 1; min-width: 0; color: var(--eg-text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cx-act-time { color: var(--eg-text-3); flex-shrink: 0; }

  .cx-spin {
    width: 13px; height: 13px; min-width: 13px; border-radius: 50%; display: inline-block;
    border: 2px solid rgba(var(--eg-accent-rgb), .25); border-top-color: rgb(var(--eg-accent-rgb));
    animation: cxSpin .8s linear infinite;
  }
  .cx-btn.is-primary .cx-spin { border-color: rgba(0,0,0,.2); border-top-color: var(--bg); }
  @keyframes cxSpin { to { transform: rotate(360deg); } }

  @media (max-width: 1100px) { .cx-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  @media (max-width: 720px) {
    .cx-wrap { padding: 16px 16px 40px; }
    .cx-grid { grid-template-columns: 1fr; }
  }
  @media (prefers-reduced-motion: reduce) {
    .cx-root, .cx-card, .cx-sheet, .cx-scrim { animation-duration: .001ms !important; }
  }
  `;

  window.SyrixConnectorsPanel = SyrixConnectorsPanel;
})();
