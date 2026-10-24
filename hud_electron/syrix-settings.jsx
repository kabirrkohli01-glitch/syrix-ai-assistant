/* ═══════════════════════════════════════════════════════════════════════════
   Settings — full-window page over the HUD.

   Every control here does something in the running app; a control with
   nothing behind it is not shown. Sections:

     General        default mode, Enter to send
     Appearance     palette, text size, brightness, reduce motion, top-bar stats
     Voice          microphone and speaker (used by the voice orb and dictation)
     Privacy        Private mode, delete all chats
     SYRIX          SYRIX.md (house rules), owner profile, what SYRIX runs on
     Supercomputer  capability switches, voice, temporary data
     Connectors     opens the Connectors page
     Advanced       connection status, runtime logs, alerts, diagnostic copy

   HUD preferences are kept in localStorage and applied by window.SyrixPrefs,
   which runs at script load so they hold from the first paint rather than
   only while this page is open.

   Exposes window.SyrixSettings and window.SyrixPrefs.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  if (typeof React === 'undefined') return;
  const { useState, useEffect, useRef, useCallback } = React;

  /* ── Preferences ─────────────────────────────────────────────────────── */
  const PREF_DEFAULTS = {
    'eg-enter-sends': '1',
    'eg-text-size': 'default',
    'eg-reduce-motion': '0',
    'eg-show-metrics': '1',
    'eg-brightness': '100',
  };
  function prefGet(key) {
    try {
      const v = localStorage.getItem(key);
      return v == null ? PREF_DEFAULTS[key] : v;
    } catch (_) { return PREF_DEFAULTS[key]; }
  }
  function applyPrefs() {
    const b = document.body;
    if (!b) return;
    const size = prefGet('eg-text-size');
    b.classList.toggle('eg-text-small', size === 'small');
    b.classList.toggle('eg-text-large', size === 'large');
    b.classList.toggle('eg-reduce-motion', prefGet('eg-reduce-motion') === '1');
    b.classList.toggle('eg-hide-metrics', prefGet('eg-show-metrics') === '0');
    const bright = Math.max(30, Math.min(100, Number(prefGet('eg-brightness')) || 100));
    // Only a dimmed HUD carries the filter: a filter on the root also makes it
    // the containing block for fixed children, so it is absent at 100%.
    b.classList.toggle('eg-dimmed', bright < 100);
    b.style.setProperty('--eg-brightness', String(bright / 100));
  }
  function prefSet(key, value) {
    try { localStorage.setItem(key, String(value)); } catch (_) {}
    applyPrefs();
    try { window.dispatchEvent(new CustomEvent('syrix-prefs-changed', { detail: { key, value: String(value) } })); } catch (_) {}
  }
  function usePref(key) {
    const [v, setV] = useState(() => prefGet(key));
    useEffect(() => {
      const on = (e) => { if (e && e.detail && e.detail.key === key) setV(e.detail.value); };
      window.addEventListener('syrix-prefs-changed', on);
      return () => window.removeEventListener('syrix-prefs-changed', on);
    }, [key]);
    return [v, (next) => { setV(String(next)); prefSet(key, next); }];
  }
  window.SyrixPrefs = {
    get: prefGet,
    set: prefSet,
    // Composer keys: Enter sends unless the user chose Ctrl+Enter, in which
    // case plain Enter falls through to the textarea and adds a line.
    shouldSend(e) {
      if (!e || e.key !== 'Enter' || (e.nativeEvent && e.nativeEvent.isComposing) || e.isComposing) return false;
      if (prefGet('eg-enter-sends') === '0') return !!(e.ctrlKey || e.metaKey);
      return !e.shiftKey;
    },
  };
  if (document.body) applyPrefs();
  else document.addEventListener('DOMContentLoaded', applyPrefs, { once: true });

  /* ── Bridge helpers ──────────────────────────────────────────────────── */
  function useSnap() {
    const [snap, setSnap] = useState(() => {
      try { return window.SyrixBridge && window.SyrixBridge.getSnapshot ? window.SyrixBridge.getSnapshot() : null; } catch (_) { return null; }
    });
    useEffect(() => {
      const B = window.SyrixBridge;
      if (!B || !B.subscribe) return undefined;
      return B.subscribe((s) => setSnap(s));
    }, []);
    return snap || {};
  }
  function send(cmd, data) {
    try { const B = window.SyrixBridge; return !!(B && B.send && B.send(cmd, { data: data || {} })); } catch (_) { return false; }
  }
  function toast(text) {
    try { window.dispatchEvent(new CustomEvent('syrix-shell-toast', { detail: { text } })); } catch (_) {}
  }

  /* ── Icons ───────────────────────────────────────────────────────────── */
  const sv = (d, extra) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" {...(extra || {})}>{d}</svg>
  );
  const ICON = {
    general:    sv(<><circle cx="12" cy="12" r="3"/><path d="M19.4 12a7.4 7.4 0 0 0-.1-1.2l2.1-1.6-2-3.5-2.5 1a7.5 7.5 0 0 0-2-1.2l-.4-2.6h-4l-.4 2.6a7.5 7.5 0 0 0-2 1.2l-2.5-1-2 3.5 2.1 1.6a7.4 7.4 0 0 0 0 2.4l-2.1 1.6 2 3.5 2.5-1c.6.5 1.3.9 2 1.2l.4 2.6h4l.4-2.6a7.5 7.5 0 0 0 2-1.2l2.5 1 2-3.5-2.1-1.6c.1-.4.1-.8.1-1.2z"/></>),
    appearance: sv(<><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor" stroke="none"/></>),
    voice:      sv(<><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/></>),
    privacy:    sv(<path d="M12 3l8 3v6c0 4.6-3.2 8.4-8 9.6C7.2 20.4 4 16.6 4 12V6l8-3z"/>),
    syrix:      sv(<><path d="M12 2.8l2.3 5.9 6.2.5-4.7 4 1.5 6.1L12 16l-5.3 3.3 1.5-6.1-4.7-4 6.2-.5z"/></>),
    super:      sv(<><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4M7 9l3 2-3 2M12 13h4"/></>),
    connectors: sv(<><path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0z"/><path d="M12 16v5"/></>),
    advanced:   sv(<><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></>),
    chevron:    sv(<path d="M9 6l6 6-6 6"/>),
    close:      sv(<path d="M6 6l12 12M18 6L6 18"/>),
  };

  // Each section has its own tint for its icon tile and page header, as in
  // iOS and macOS Settings, and words a search should find it by.
  const SECTIONS = [
    { id: 'general',    label: 'General',       icon: ICON.general,    tint: '#8e8e93', sub: 'How SYRIX answers and how you send.',
      keys: 'mode spark zenith auto default enter send ctrl sleep idle timeout composer' },
    { id: 'appearance', label: 'Appearance',    icon: ICON.appearance, tint: '#5e5ce6', sub: 'Palette, size and motion.',
      keys: 'theme aura dark palette text size font brightness motion animation stats cpu ram gpu' },
    { id: 'voice',      label: 'Voice',         icon: ICON.voice,      tint: '#ff375f', sub: 'The microphone and speaker SYRIX uses.',
      keys: 'microphone mic speaker audio output input test sound' },
    { id: 'privacy',    label: 'Privacy',       icon: ICON.privacy,    tint: '#0a84ff', sub: 'What is kept, and what leaves this PC.',
      keys: 'private mode delete chats history clear' },
    { id: 'syrix',      label: 'SYRIX',         icon: ICON.syrix,      tint: '#d4a849', sub: 'Who SYRIX is, and what it knows about you.',
      keys: 'syrix.md house rules system prompt identity owner profile about you models' },
    { id: 'super',      label: 'Supercomputer', icon: ICON.super,      tint: '#bf5af2', sub: 'What the agentic workspace may do.',
      keys: 'agent browser tools capabilities permissions voice data automation' },
    { id: 'connectors', label: 'Connectors',    icon: ICON.connectors, tint: '#30d158', sub: 'Accounts and services SYRIX can use.',
      keys: 'apps accounts gmail calendar drive github notion connect' },
    { id: 'advanced',   label: 'Advanced',      icon: ICON.advanced,   tint: '#ff9f0a', sub: 'Status and logs, for when something is not right.',
      keys: 'logs status diagnostic backend alerts debug' },
  ];

  /* ── Building blocks ─────────────────────────────────────────────────── */
  function Toggle({ on, onChange, disabled, label }) {
    return (
      <button type="button" role="switch" aria-checked={!!on} aria-label={label}
        className={'sx-switch' + (on ? ' is-on' : '')} disabled={!!disabled}
        onClick={() => !disabled && onChange && onChange(!on)}>
        <span className="sx-switch-knob" />
      </button>
    );
  }
  function Segmented({ value, options, onChange, label }) {
    const idx = Math.max(0, options.findIndex(o => o.value === value));
    return (
      <div className="sx-seg" role="radiogroup" aria-label={label}
        style={{ '--seg-n': options.length, '--seg-i': idx }}>
        <span className="sx-seg-thumb" aria-hidden="true" />
        {options.map(o => (
          <button key={o.value} type="button" role="radio" aria-checked={o.value === value}
            className={'sx-seg-opt' + (o.value === value ? ' is-on' : '')}
            onClick={() => onChange(o.value)}>{o.label}</button>
        ))}
      </div>
    );
  }
  function Group({ title, foot, children }) {
    return (
      <section className="sx-group">
        {title && <h3 className="sx-group-title">{title}</h3>}
        <div className="sx-card">{children}</div>
        {foot && <p className="sx-group-foot">{foot}</p>}
      </section>
    );
  }
  function Row({ label, sub, children, onClick, danger, stack }) {
    const body = (
      <>
        <div className="sx-row-text">
          <div className={'sx-row-label' + (danger ? ' is-danger' : '')}>{label}</div>
          {sub && <div className="sx-row-sub">{sub}</div>}
        </div>
        {children != null && <div className="sx-row-ctl">{children}</div>}
      </>
    );
    if (onClick) {
      return <button type="button" className={'sx-row is-action' + (stack ? ' is-stack' : '')} onClick={onClick}>{body}</button>;
    }
    return <div className={'sx-row' + (stack ? ' is-stack' : '')}>{body}</div>;
  }
  function Btn({ children, onClick, kind, disabled, title }) {
    return (
      <button type="button" title={title} disabled={!!disabled}
        className={'sx-btn' + (kind ? ' is-' + kind : '')} onClick={onClick}>{children}</button>
    );
  }
  function Select({ value, onChange, options, label }) {
    return (
      <span className="sx-select">
        <select value={value} aria-label={label} onChange={e => onChange(e.target.value, e.target.options[e.target.selectedIndex])}>
          {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <span className="sx-select-caret" aria-hidden="true">{ICON.chevron}</span>
      </span>
    );
  }
  // The page header: the section's icon tile, title and one line, over a
  // soft glow in the section's tint.
  function Head({ title, sub }) {
    const sec = SECTIONS.find(x => x.label === title);
    return (
      <header className="sx-head" style={sec ? { '--sx-tint': sec.tint } : undefined}>
        {sec && <span className="sx-head-glow" aria-hidden="true" />}
        {sec && <span className="sx-tile is-lg" aria-hidden="true">{sec.icon}</span>}
        <div className="sx-head-text">
          <h2 className="sx-title">{title}</h2>
          {sub && <p className="sx-sub">{sub}</p>}
        </div>
      </header>
    );
  }

  /* ── General ─────────────────────────────────────────────────────────── */
  function readModel() {
    try {
      const v = String(localStorage.getItem('syrix_chat_model_v1') || '').toLowerCase();
      return (v === 'zenith' || v === 'auto') ? v : 'spark';
    } catch (_) { return 'spark'; }
  }
  // After this long with no mouse or keyboard input the HUD goes to sleep
  // (PrimaryHUD's idle check). The value lives in the backend config and
  // comes back through the snapshot.
  const IDLE_CHOICES = [5, 10, 15, 30, 60, 120, 240];
  function idleLabel(m) { return m < 60 ? m + ' minutes' : (m === 60 ? '1 hour' : (m / 60) + ' hours'); }
  function idleFromSnap(snap) {
    const raw = snap.raw || {};
    const hs = raw.hud_settings || {};
    const v = Number(raw.syrix_user_idle_timeout_minutes != null ? raw.syrix_user_idle_timeout_minutes : hs.user_idle_timeout_minutes);
    return Number.isFinite(v) && v > 0 ? Math.round(v) : 15;
  }
  function General({ snap }) {
    const [model, setModel] = useState(readModel);
    const serverIdle = idleFromSnap(snap);
    const [idle, setIdle] = useState(serverIdle);
    useEffect(() => { setIdle(serverIdle); }, [serverIdle]);
    function pickIdle(v) {
      const n = Number(v) || 15;
      setIdle(n);
      try { const B = window.SyrixBridge; if (B && B.setUserIdleTimeoutMinutes) B.setUserIdleTimeoutMinutes(n); } catch (_) {}
    }
    const idleOpts = (IDLE_CHOICES.includes(idle) ? IDLE_CHOICES : IDLE_CHOICES.concat(idle).sort((a, b) => a - b))
      .map(m => ({ value: String(m), label: idleLabel(m) }));
    const [enterSends, setEnterSends] = usePref('eg-enter-sends');
    useEffect(() => {
      const on = (e) => { const v = e && e.detail && e.detail.model; setModel((v === 'zenith' || v === 'auto') ? v : 'spark'); };
      window.addEventListener('syrix-model-changed', on);
      return () => window.removeEventListener('syrix-model-changed', on);
    }, []);
    function pick(v) {
      setModel(v);
      try { localStorage.setItem('syrix_chat_model_v1', v); } catch (_) {}
      try { window.dispatchEvent(new CustomEvent('syrix-model-changed', { detail: { model: v, source: 'settings' } })); } catch (_) {}
    }
    return (
      <>
        <Head title="General" sub="How SYRIX answers and how you send." />
        <Group title="Mode" foot="The same choice as the mode menu in the composer. Supercomputer opens from that menu.">
          <Row label="Default mode" sub={model === 'auto' ? 'Picks Spark or Zenith for each question'
            : model === 'zenith' ? 'Zenith 1.1 — deeper thinking for complex questions'
            : 'Spark 1.1 — fast answers to everyday questions'}>
            <Segmented label="Default mode" value={model} onChange={pick}
              options={[{ value: 'auto', label: 'Auto' }, { value: 'spark', label: 'Spark' }, { value: 'zenith', label: 'Zenith' }]} />
          </Row>
        </Group>
        <Group title="Sleep" foot="Provider and tool timeouts are separate: this only decides when the HUD rests.">
          <Row label="Sleep when idle" sub="After this long with no mouse or keyboard input.">
            <Select label="Sleep when idle" value={String(idle)} onChange={pickIdle} options={idleOpts} />
          </Row>
        </Group>
        <Group title="Composer">
          <Row label="Enter to send"
            sub={enterSends === '0' ? 'Ctrl+Enter sends. Enter starts a new line.' : 'Enter sends. Shift+Enter starts a new line.'}>
            <Toggle label="Enter to send" on={enterSends !== '0'} onChange={on => setEnterSends(on ? '1' : '0')} />
          </Row>
        </Group>
      </>
    );
  }

  /* ── Appearance ──────────────────────────────────────────────────────── */
  function Appearance({ palette, onPaletteChange }) {
    const [size, setSize] = usePref('eg-text-size');
    const [motion, setMotion] = usePref('eg-reduce-motion');
    const [metrics, setMetrics] = usePref('eg-show-metrics');
    const [bright, setBright] = usePref('eg-brightness');
    const b = Number(bright) || 100;
    return (
      <>
        <Head title="Appearance" sub="Palette, size and motion." />
        <Group title="Theme">
          <div className="sx-themes">
            {[{ id: 'aurora', name: 'Aura', sub: 'Champagne glass' }, { id: 'dark', name: 'Dark', sub: 'True black, no accent' }].map(t => (
              <button key={t.id} type="button" className={'sx-theme' + (palette === t.id ? ' is-on' : '')}
                onClick={() => onPaletteChange && onPaletteChange(t.id)} aria-pressed={palette === t.id}>
                <span className={'sx-theme-art is-' + t.id} aria-hidden="true">
                  <span className="sx-theme-side" /><span className="sx-theme-line" /><span className="sx-theme-line is-short" /><span className="sx-theme-pill" />
                </span>
                <span className="sx-theme-name">{t.name}</span>
                <span className="sx-theme-sub">{t.sub}</span>
              </button>
            ))}
          </div>
        </Group>
        <Group title="Display">
          <Row label="Text size" sub="Messages in the conversation.">
            <Segmented label="Text size" value={size} onChange={setSize}
              options={[{ value: 'small', label: 'Small' }, { value: 'default', label: 'Default' }, { value: 'large', label: 'Large' }]} />
          </Row>
          <Row label="Brightness" sub="Dims the whole window." stack>
            <div className="sx-slider-row">
              <input type="range" min="30" max="100" step="1" value={b} className="sx-slider"
                style={{ '--fill': (((b - 30) / 70) * 100).toFixed(1) + '%' }}
                onChange={e => setBright(e.target.value)} aria-label="Brightness" />
              <span className="sx-slider-val">{b}%</span>
            </div>
          </Row>
          <Row label="Reduce motion" sub="Fades instead of slides, and no ambient animation.">
            <Toggle label="Reduce motion" on={motion === '1'} onChange={on => setMotion(on ? '1' : '0')} />
          </Row>
          <Row label="System stats in the top bar" sub="CPU, RAM and GPU load.">
            <Toggle label="System stats" on={metrics !== '0'} onChange={on => setMetrics(on ? '1' : '0')} />
          </Row>
        </Group>
      </>
    );
  }

  /* ── Voice ───────────────────────────────────────────────────────────── */
  // A short two-note chime as a WAV, so the speaker test needs no asset.
  function chimeUrl() {
    const rate = 22050, dur = 0.42, n = Math.floor(rate * dur);
    const buf = new ArrayBuffer(44 + n * 2), v = new DataView(buf);
    const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVEfmt '); v.setUint32(16, 16, true);
    v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, n * 2, true);
    for (let i = 0; i < n; i++) {
      const t = i / rate, f = t < dur / 2 ? 660 : 880;
      const env = Math.min(1, t * 40) * Math.exp(-((t % (dur / 2)) * 9));
      v.setInt16(44 + i * 2, Math.sin(2 * Math.PI * f * t) * env * 0.35 * 32767, true);
    }
    return URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
  }
  function Voice() {
    const [devices, setDevices] = useState({ inputs: [], outputs: [], loaded: false });
    const [micId, setMicId] = useState(() => { try { return localStorage.getItem('syrix_mic_device_id') || ''; } catch (_) { return ''; } });
    const [spkId, setSpkId] = useState(() => { try { return localStorage.getItem('syrix_speaker_device_id') || ''; } catch (_) { return ''; } });
    const [level, setLevel] = useState(-1);
    const testRef = useRef(null);
    const load = useCallback(async () => {
      try {
        const all = await navigator.mediaDevices.enumerateDevices();
        setDevices({
          inputs: all.filter(d => d.kind === 'audioinput' && d.deviceId !== 'default' && d.deviceId !== 'communications'),
          outputs: all.filter(d => d.kind === 'audiooutput' && d.deviceId !== 'default' && d.deviceId !== 'communications'),
          loaded: true,
        });
      } catch (_) { setDevices(d => ({ ...d, loaded: true })); }
    }, []);
    useEffect(() => {
      load();
      const md = navigator.mediaDevices;
      if (md && md.addEventListener) md.addEventListener('devicechange', load);
      return () => { if (md && md.removeEventListener) md.removeEventListener('devicechange', load); stopTest(); };
    }, []);
    function stopTest() {
      const t = testRef.current; testRef.current = null;
      if (!t) return;
      try { cancelAnimationFrame(t.raf); } catch (_) {}
      try { clearTimeout(t.timer); } catch (_) {}
      try { t.stream.getTracks().forEach(tr => tr.stop()); } catch (_) {}
      try { t.ctx.close(); } catch (_) {}
      setLevel(-1);
    }
    async function testMic() {
      if (testRef.current) { stopTest(); return; }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: micId ? { deviceId: { exact: micId } } : true });
        const ctx = new AudioContext();
        const an = ctx.createAnalyser(); an.fftSize = 512;
        ctx.createMediaStreamSource(stream).connect(an);
        const data = new Float32Array(an.fftSize);
        const t = { stream, ctx, raf: 0, timer: 0 };
        testRef.current = t;
        const tick = () => {
          an.getFloatTimeDomainData(data);
          let s = 0; for (let i = 0; i < data.length; i++) s += data[i] * data[i];
          setLevel(Math.min(1, Math.sqrt(s / data.length) * 6));
          t.raf = requestAnimationFrame(tick);
        };
        tick();
        t.timer = setTimeout(stopTest, 8000);
        load();
      } catch (_) {
        toast('The microphone could not be opened. Check it is plugged in and allowed.');
      }
    }
    async function testSpeaker() {
      const url = chimeUrl();
      const a = new Audio(url);
      try { if (spkId && a.setSinkId) await a.setSinkId(spkId); } catch (_) {}
      a.onended = () => URL.revokeObjectURL(url);
      a.play().catch(() => toast('That speaker could not play the test sound.'));
    }
    function pickMic(id, opt) {
      setMicId(id);
      try { id ? localStorage.setItem('syrix_mic_device_id', id) : localStorage.removeItem('syrix_mic_device_id'); } catch (_) {}
      send('set_mic_device', { label: opt ? opt.text : '', device_id: id });
      if (testRef.current) { stopTest(); }
    }
    function pickSpk(id, opt) {
      setSpkId(id);
      try { id ? localStorage.setItem('syrix_speaker_device_id', id) : localStorage.removeItem('syrix_speaker_device_id'); } catch (_) {}
      send('set_speaker_device', { label: opt ? opt.text : '', device_id: id });
    }
    const micOpts = [{ value: '', label: 'System default' }].concat(devices.inputs.map(d => ({ value: d.deviceId, label: d.label || 'Microphone' })));
    const spkOpts = [{ value: '', label: 'System default' }].concat(devices.outputs.map(d => ({ value: d.deviceId, label: d.label || 'Speaker' })));
    return (
      <>
        <Head title="Voice" sub="The microphone and speaker SYRIX uses." />
        <Group title="Microphone" foot="Used by the voice orb and by dictation. A change applies the next time either starts.">
          <Row label="Input" sub={devices.loaded && !devices.inputs.length ? 'No microphone found.' : null}>
            <Select label="Microphone" value={micId} onChange={pickMic} options={micOpts} />
          </Row>
          <Row label="Test microphone" sub={level >= 0 ? 'Speak — the bar follows your voice.' : 'Listens for eight seconds. Nothing is recorded.'}>
            {level >= 0 && <span className="sx-meter" aria-hidden="true"><span style={{ transform: 'scaleX(' + Math.max(0.02, level) + ')' }} /></span>}
            <Btn onClick={testMic}>{level >= 0 ? 'Stop' : 'Test'}</Btn>
          </Row>
        </Group>
        <Group title="Speaker" foot="Spoken replies in voice conversations play here.">
          <Row label="Output" sub={devices.loaded && !devices.outputs.length ? 'No separate outputs found; the system default is used.' : null}>
            <Select label="Speaker" value={spkId} onChange={pickSpk} options={spkOpts} />
          </Row>
          <Row label="Test speaker" sub="Plays a short chime.">
            <Btn onClick={testSpeaker}>Play</Btn>
          </Row>
        </Group>
      </>
    );
  }

  /* ── Privacy ─────────────────────────────────────────────────────────── */
  function Privacy({ snap }) {
    const [confirm, setConfirm] = useState(false);
    const priv = !!snap.private_mode;
    return (
      <>
        <Head title="Privacy" sub="What is kept, and what leaves this PC." />
        <Group foot="Off at every start. Chats in Private mode are not saved to history or memory, answers come from the local model, and nothing leaves this PC.">
          <Row label="Private mode" sub={priv ? 'On — this chat is not saved.' : 'Off'}>
            <Toggle label="Private mode" on={priv}
              onChange={on => { try { window.dispatchEvent(new CustomEvent('syrix-private-set', { detail: { on } })); } catch (_) {} }} />
          </Row>
        </Group>
        <Group title="History" foot="Deletes every saved chat from this PC. It cannot be undone.">
          {!confirm ? (
            <Row label="Delete all chats" danger onClick={() => setConfirm(true)}>
              <span className="sx-chev">{ICON.chevron}</span>
            </Row>
          ) : (
            <Row label="Delete every saved chat?" sub="This cannot be undone." danger>
              <Btn onClick={() => setConfirm(false)}>Cancel</Btn>
              <Btn kind="danger" onClick={() => {
                try { window.dispatchEvent(new CustomEvent('syrix-clear-all-chats')); } catch (_) {}
                setConfirm(false);
                toast('All chats deleted.');
              }}>Delete</Btn>
            </Row>
          )}
        </Group>
      </>
    );
  }

  /* ── SYRIX ───────────────────────────────────────────────────────────── */
  function useSyrixFiles() {
    const [info, setInfo] = useState(null);
    useEffect(() => {
      const api = window.syrixHud && window.syrixHud.syrixFiles;
      if (!api) return undefined;
      let alive = true;
      const load = () => api.info().then(r => { if (alive) setInfo(r); }).catch(() => {});
      load();
      // Editing happens in another app; refresh the count on return.
      window.addEventListener('focus', load);
      const t = setInterval(load, 5000);
      return () => { alive = false; window.removeEventListener('focus', load); clearInterval(t); };
    }, []);
    return info;
  }
  function openFile(key, reveal) {
    const api = window.syrixHud && window.syrixHud.syrixFiles;
    if (!api) return;
    api.open(key, reveal).then(r => {
      if (!r || !r.ok) toast(r && r.reason === 'missing' ? 'That file does not exist yet.' : 'The file could not be opened.');
    }).catch(() => {});
  }
  function Syrix() {
    const info = useSyrixFiles();
    const house = info && info['house-rules'];
    const owner = info && info['owner-profile'];
    const limit = info && info.limit;
    const pct = house && limit ? Math.min(1, house.chars / limit) : 0;
    return (
      <>
        <Head title="SYRIX" sub="Who SYRIX is, and what it knows about you." />
        <div className="sx-hero">
          <img className="sx-hero-mark" src="../assets/icons/syrix_app_logo.png" alt="" draggable="false" />
          <div className="sx-hero-text">
            <div className="sx-hero-name">SYRIX</div>
            <div className="sx-hero-sub">Built by Kabirr Kohli · Kohli Core · SYRIX v3</div>
          </div>
        </div>
        <Group title="House rules" foot="SYRIX.md is SYRIX's system prompt: identity, loyalty and tone. It is read at the top of every answer, so an edit applies to the next message. Notes inside <!-- --> are not sent.">
          <Row label="SYRIX.md"
            sub={house ? (house.exists
              ? house.chars.toLocaleString() + (limit ? ' of ' + limit.toLocaleString() + ' characters' : ' characters')
              : 'Missing — SYRIX is using its built-in rules.') : 'Reading…'}>
            <Btn onClick={() => openFile('house-rules', true)} disabled={!house || !house.exists}>Show</Btn>
            <Btn kind="primary" onClick={() => openFile('house-rules')} disabled={!house || !house.exists}>Edit</Btn>
          </Row>
          {house && house.exists && limit ? (
            <div className="sx-budget" aria-hidden="true">
              <span className={pct > 0.98 ? 'is-full' : ''} style={{ transform: 'scaleX(' + pct + ')' }} />
            </div>
          ) : null}
        </Group>
        <Group title="About you" foot="What SYRIX answers from when asked about you. It is never looked up on the web, and anything not written here is treated as unknown.">
          <Row label="Owner profile" sub={owner ? (owner.exists ? 'knowledge/' + owner.name : 'Not created yet.') : 'Reading…'}>
            <Btn kind="primary" onClick={() => openFile('owner-profile')} disabled={!owner || !owner.exists}>Edit</Btn>
          </Row>
        </Group>
        <Group title="Modes">
          <Row label="Spark 1.1" sub="Fast answers to everyday questions." />
          <Row label="Zenith 1.1" sub="Deeper thinking for complex questions." />
          <Row label="Supercomputer" sub="Agentic tasks and automation in its own workspace." />
        </Group>
      </>
    );
  }

  /* ── Supercomputer ───────────────────────────────────────────────────── */
  // Mirrors syrix_supercomputer/capabilities.py; kept literal so the page
  // renders without the mode ever being loaded. Turning one on makes it
  // possible — SYRIX still asks before every use.
  const SC_RISKY = [
    ['supercomputer_capability_file_read', 'Read Downloads, Documents, Desktop and Pictures'],
    ['supercomputer_capability_uploads', 'Attach your files to a web form'],
    ['supercomputer_capability_downloads', 'Download files'],
    ['supercomputer_capability_authenticated_sites', 'Use a signed-in account'],
    ['supercomputer_capability_read_private_documents', 'Read email or private documents'],
    ['supercomputer_capability_draft_messages', 'Write drafts (never sends them)'],
    ['supercomputer_capability_send_messages', 'Send emails and messages'],
    ['supercomputer_capability_submit_forms', 'Fill in and submit forms'],
    ['supercomputer_capability_publish', 'Publish content'],
    ['supercomputer_capability_delete_online_content', 'Delete online content'],
    ['supercomputer_capability_account_settings', 'Change account settings'],
    ['supercomputer_capability_payments', 'Make purchases or payments'],
    ['supercomputer_capability_agree_to_terms', 'Agree to terms on your behalf'],
    ['supercomputer_capability_export_outside_chat', 'Export outside this chat'],
  ];
  function Supercomputer({ snap }) {
    // State comes from the mirrored config (syrix_host writes every
    // supercomputer_* key there), so this works without entering the mode.
    const cfg = snap.config || (snap.raw && snap.raw.config) || {};
    const [pending, setPending] = useState({});
    const on = (key, dflt) => (key in pending ? pending[key] : (cfg[key] === undefined ? !!dflt : !!cfg[key]));
    useEffect(() => { setPending({}); }, [JSON.stringify(cfg)]);
    const set = (key, value) => { setPending(p => ({ ...p, [key]: value })); send('set_config', { key, value }); };
    const enabled = on('supercomputer_enabled', true);
    const gender = String(cfg.supercomputer_voice_gender || 'female');
    return (
      <>
        <Head title="Supercomputer" sub="What the agentic workspace may do." />
        <Group>
          <Row label="Supercomputer" sub="Off means the mode never loads and never runs anything.">
            <Toggle label="Supercomputer" on={enabled} onChange={v => set('supercomputer_enabled', v)} />
          </Row>
          <Row label="Browser automation" sub="Open and read pages in its own browser.">
            <Toggle label="Browser automation" disabled={!enabled} on={on('supercomputer_browser_enabled', true)} onChange={v => set('supercomputer_browser_enabled', v)} />
          </Row>
          <Row label="Tools" sub="Master switch for every action. Off means answers only.">
            <Toggle label="Tools" disabled={!enabled} on={on('supercomputer_tools_enabled', true)} onChange={v => set('supercomputer_tools_enabled', v)} />
          </Row>
        </Group>
        <Group title="Off by default" foot="Turning one on makes it possible, not automatic: SYRIX asks you before every single use. Off means it is not offered to the model and is refused if requested.">
          {SC_RISKY.map(([key, label]) => (
            <Row key={key} label={label}>
              <Toggle label={label} disabled={!enabled} on={on(key, false)} onChange={v => set(key, v)} />
            </Row>
          ))}
        </Group>
        <Group title="Voice">
          <Row label="Supercomputer voice" sub="Separate from the voice orb. The microphone is released when it is off or the mode is left.">
            <Toggle label="Supercomputer voice" disabled={!enabled} on={on('supercomputer_voice_enabled', true)} onChange={v => set('supercomputer_voice_enabled', v)} />
          </Row>
          <Row label="Voice" sub="Plays a preview when chosen.">
            <Segmented label="Voice" value={gender === 'male' ? 'male' : 'female'}
              onChange={g => { send('set_config', { key: 'supercomputer_voice_gender', value: g }); send('supercomputer_voice_preview', { gender: g }); }}
              options={[{ value: 'female', label: 'Female' }, { value: 'male', label: 'Male' }]} />
          </Row>
        </Group>
        <Group title="Data" foot="The browser profile is ephemeral: no cookie, cache or login survives leaving the mode. Files you asked SYRIX to save are kept.">
          <Row label="Clear temporary data" sub="Snapshots, extracted page text and caches.">
            <Btn onClick={() => { send('supercomputer_clear_data', {}); toast('Temporary Supercomputer data cleared.'); }}>Clear</Btn>
          </Row>
          <Row label="Reset permissions" sub="Revokes every grant now.">
            <Btn onClick={() => { send('supercomputer_reset_permissions', {}); toast('Supercomputer permissions reset.'); }}>Reset</Btn>
          </Row>
        </Group>
      </>
    );
  }

  /* ── Connectors ──────────────────────────────────────────────────────── */
  function Connectors({ snap, onClose }) {
    const list = ((snap.syrix_connectors || (snap.raw && snap.raw.syrix_connectors) || {}).connectors) || [];
    const live = list.filter(c => c && (c.health === 'connected' || c.health === 'needs_reauth'));
    return (
      <>
        <Head title="Connectors" sub="Accounts and services SYRIX can use." />
        <Group>
          <Row label="Manage connectors" sub={live.length ? live.length + ' connected' : 'None connected'}
            onClick={() => {
              onClose && onClose();
              setTimeout(() => { try { window.SyrixStage && window.SyrixStage.registry.invoke('connectors'); } catch (_) {} }, 60);
            }}>
            <span className="sx-chev">{ICON.chevron}</span>
          </Row>
        </Group>
      </>
    );
  }

  /* ── Advanced ────────────────────────────────────────────────────────── */
  function redact(text) {
    return String(text || '')
      .replace(/(bearer\s+)[a-z0-9._~+/-]+|((?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization)\s*[=:]\s*)[^\s,&"']+/ig,
        (_m, bearer, key) => (bearer || key || '') + '[REDACTED]')
      .replace(/https?:\/\/[^\s"']*(?:oauth|access_token|refresh_token|client_secret|client_id|code=)[^\s"']*/gi, '[OAUTH_URL]')
      .replace(/\bsk-[a-z0-9]{8,}\b/gi, '[REDACTED]');
  }
  function logLevel(row) {
    const ex = String((row && row.level) || '').toUpperCase();
    if (ex === 'ERROR' || ex === 'CRITICAL') return 'error';
    if (ex === 'WARN' || ex === 'WARNING') return 'warn';
    const m = String((row && row.line) || row || '').match(/\|\s*(ERROR|CRITICAL|WARN(?:ING)?)\s*\|/i);
    return m ? (/warn/i.test(m[1]) ? 'warn' : 'error') : 'info';
  }
  function Advanced({ snap, localNotifications }) {
    const [logs, setLogs] = useState(null);
    const [filter, setFilter] = useState('all');
    const [openDetails, setOpenDetails] = useState({});
    const [alerts, setAlerts] = useState(() => {
      try { return (window.SyrixBridge && window.SyrixBridge.getHudNotificationHistory && window.SyrixBridge.getHudNotificationHistory()) || []; } catch (_) { return []; }
    });
    const logRef = useRef(null);
    const loadLogs = useCallback(() => {
      const api = window.syrixHud && window.syrixHud.runtimeLogs;
      if (!api || !api.recent) { setLogs([]); return; }
      api.recent(200).then(res => {
        const rows = (res && Array.isArray(res.lines) ? res.lines : []).map(r => ({
          level: logLevel(r), source: String((r && r.source) || ''), line: redact((r && r.line) || r),
        }));
        setLogs(rows.slice(-200));
      }).catch(() => setLogs([]));
    }, []);
    useEffect(() => { loadLogs(); const t = setInterval(loadLogs, 4000); return () => clearInterval(t); }, []);
    useEffect(() => { const el = logRef.current; if (el) el.scrollTop = el.scrollHeight; }, [logs, filter]);
    useEffect(() => {
      const on = (ev) => {
        try {
          const h = ev && ev.detail && Array.isArray(ev.detail.history) ? ev.detail.history
            : (window.SyrixBridge && window.SyrixBridge.getHudNotificationHistory ? window.SyrixBridge.getHudNotificationHistory() : []);
          setAlerts(h || []);
        } catch (_) {}
      };
      window.addEventListener('syrix-hud-notification-history', on);
      return () => window.removeEventListener('syrix-hud-notification-history', on);
    }, []);
    const status = String(snap.status || 'offline').toLowerCase();
    const backend = status === 'online' ? 'Connected' : status === 'connecting' ? 'Connecting…' : 'Not connected';
    const shown = (logs || []).filter(r => filter === 'all' || (filter === 'problems' && r.level !== 'info'));
    const seen = new Set();
    const alertRows = [...(localNotifications || []), ...(alerts || []).slice().reverse()].filter(r => {
      const k = r && (r.id || r.seq || r.time); if (seen.has(k)) return false; seen.add(k); return true;
    }).slice(0, 30);
    function copy(text, done) {
      try { navigator.clipboard.writeText(text).then(() => toast(done)).catch(() => toast('Could not copy.')); } catch (_) { toast('Could not copy.'); }
    }
    function diagnostic() {
      const apps = Array.isArray(snap.syrix_hud_apps) ? snap.syrix_hud_apps : [];
      return redact([
        'SYRIX diagnostic',
        'generated: ' + new Date().toISOString(),
        'backend: ' + status,
        'private_mode: ' + !!snap.private_mode,
        'voice_state: ' + String(snap.state || '—'),
        'hud_apps_open: ' + apps.length,
        'palette: ' + (document.body.classList.contains('pal-dark') ? 'dark' : 'aura'),
        'user_agent: ' + navigator.userAgent,
      ].join('\n'));
    }
    return (
      <>
        <Head title="Advanced" sub="Status and logs, for when something is not right." />
        <Group title="Status">
          <Row label="Backend" sub="The SYRIX process this window talks to.">
            <span className={'sx-status is-' + (status === 'online' ? 'ok' : status === 'connecting' ? 'warn' : 'off')}>{backend}</span>
          </Row>
          <Row label="Copy diagnostic" sub="A short status summary for a bug report. Secrets are removed.">
            <Btn onClick={() => copy(diagnostic(), 'Diagnostic copied.')}>Copy</Btn>
          </Row>
        </Group>
        <Group title="Runtime log" foot="The last 200 lines from the backend and this window. Secrets are removed.">
          <div className="sx-log-bar">
            <Segmented label="Log filter" value={filter} onChange={setFilter}
              options={[{ value: 'all', label: 'All' }, { value: 'problems', label: 'Problems' }]} />
            <span className="sx-log-count">{shown.length} lines</span>
            <Btn onClick={() => copy(shown.map(r => r.level.toUpperCase().padEnd(5) + ' | ' + (r.source || 'runtime') + ' | ' + r.line).join('\n'), 'Log copied.')}>Copy</Btn>
          </div>
          <div className="sx-log" ref={logRef}>
            {logs === null ? <div className="sx-log-empty">Loading…</div>
              : !shown.length ? <div className="sx-log-empty">{logs.length ? 'No problems in the last 200 lines.' : 'No log lines yet.'}</div>
              : shown.map((r, i) => (
                <div key={i} className={'sx-log-line is-' + r.level}>
                  <span className="sx-log-src">{r.source || 'runtime'}</span>
                  <span className="sx-log-text">{r.line}</span>
                </div>
              ))}
          </div>
        </Group>
        <Group title="Alerts" foot={alertRows.length ? null : 'Nothing has raised an alert this session.'}>
          {alertRows.map((r, i) => {
            let when = '';
            try { when = new Date(r.time || (Number(r.ts || 0) * 1000)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); } catch (_) {}
            const sev = String(r.severity || 'info').toLowerCase();
            const key = r.id || r.seq || i;
            return (
              <React.Fragment key={key}>
                <Row label={r.message || 'Notification'} sub={when}>
                  {r.technical && (
                    <Btn onClick={() => setOpenDetails(o => ({ ...o, [key]: !o[key] }))}>{openDetails[key] ? 'Hide' : 'Details'}</Btn>
                  )}
                  <span className={'sx-status is-' + (sev === 'error' ? 'off' : sev === 'warn' ? 'warn' : 'ok')}>{sev}</span>
                </Row>
                {r.technical && openDetails[key] && (
                  <pre className="sx-alert-detail">{redact(JSON.stringify(r.technical, null, 2))}</pre>
                )}
              </React.Fragment>
            );
          })}
          {alertRows.length > 0 && (
            <Row label="Clear alerts" onClick={() => {
              try { window.SyrixBridge && window.SyrixBridge.clearHudNotificationHistory && window.SyrixBridge.clearHudNotificationHistory(); } catch (_) {}
              setAlerts([]);
            }}><span className="sx-chev">{ICON.chevron}</span></Row>
          )}
        </Group>
      </>
    );
  }

  /* ── Page ────────────────────────────────────────────────────────────── */
  function SyrixSettings({ onClose, palette, onPaletteChange, localNotifications, initialSection }) {
    const snap = useSnap();
    const [section, setSection] = useState(() => {
      try { return initialSection || sessionStorage.getItem('sx-settings-section') || 'general'; } catch (_) { return 'general'; }
    });
    const bodyRef = useRef(null);
    const [query, setQuery] = useState('');
    const itemRefs = useRef({});
    const [pill, setPill] = useState(null);
    const q = query.trim().toLowerCase();
    const shown = q ? SECTIONS.filter(x => (x.label + ' ' + x.keys).toLowerCase().includes(q)) : SECTIONS;
    // The highlight slides to the chosen section rather than jumping.
    React.useLayoutEffect(() => {
      const el = itemRefs.current[section];
      setPill(el && shown.some(x => x.id === section) ? { top: el.offsetTop, height: el.offsetHeight } : null);
    }, [section, q]);
    useEffect(() => {
      try { sessionStorage.setItem('sx-settings-section', section); } catch (_) {}
      if (bodyRef.current) bodyRef.current.scrollTop = 0;
    }, [section]);
    useEffect(() => {
      const onKey = (e) => {
        if (e.key !== 'Escape') return;
        if (e.target && e.target.closest && e.target.closest('.sx-search') && e.target.value) return;
        e.stopPropagation(); onClose && onClose();
      };
      document.addEventListener('keydown', onKey);
      return () => document.removeEventListener('keydown', onKey);
    }, [onClose]);

    let content = null;
    try {
      if (section === 'general') content = <General snap={snap} />;
      else if (section === 'appearance') content = <Appearance palette={palette} onPaletteChange={onPaletteChange} />;
      else if (section === 'voice') content = <Voice />;
      else if (section === 'privacy') content = <Privacy snap={snap} />;
      else if (section === 'syrix') content = <Syrix />;
      else if (section === 'super') content = <Supercomputer snap={snap} />;
      else if (section === 'connectors') content = <Connectors snap={snap} onClose={onClose} />;
      else content = <Advanced snap={snap} localNotifications={localNotifications} />;
    } catch (_) {
      content = <Head title="Unavailable" sub="This section could not be shown." />;
    }

    const owner = (() => { try { return (window.SyrixOwner && window.SyrixOwner.name()) || ''; } catch (_) { return ''; } })();
    return (
      <div className="sx-set" role="dialog" aria-modal="true" aria-label="Settings">
        <aside className="sx-nav">
          <div className="sx-nav-title">Settings</div>
          <button type="button" className="sx-account" onClick={() => setSection('syrix')} title="SYRIX and what it knows about you">
            <span className="sx-avatar" aria-hidden="true">{(owner.trim()[0] || 'S').toUpperCase()}</span>
            <span className="sx-account-text">
              <span className="sx-account-name">{owner || 'SYRIX'}</span>
              <span className="sx-account-sub">SYRIX v3 · Kohli Core</span>
            </span>
            <span className="sx-chev" aria-hidden="true">{ICON.chevron}</span>
          </button>
          <label className="sx-search">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
            <input value={query} placeholder="Search" aria-label="Search settings" spellCheck={false}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && shown[0]) setSection(shown[0].id); if (e.key === 'Escape' && query) { e.stopPropagation(); setQuery(''); } }} />
          </label>
          <nav className="sx-nav-list">
            {pill && <span className="sx-nav-pill" aria-hidden="true" style={{ transform: 'translateY(' + pill.top + 'px)', height: pill.height }} />}
            {shown.map(s => (
              <button key={s.id} type="button" ref={el => { itemRefs.current[s.id] = el; }}
                className={'sx-nav-item' + (section === s.id ? ' is-on' : '')}
                aria-current={section === s.id ? 'page' : undefined}
                onClick={() => setSection(s.id)} style={{ '--sx-tint': s.tint }}>
                <span className="sx-tile" aria-hidden="true">{s.icon}</span>
                <span className="sx-nav-lbl">{s.label}</span>
              </button>
            ))}
            {!shown.length && <div className="sx-nav-empty">No settings match.</div>}
          </nav>
          <div className="sx-nav-foot">SYRIX v3 · Kohli Core</div>
        </aside>
        <main className="sx-main" ref={bodyRef}>
          <button type="button" className="sx-done" onClick={onClose} title="Close (Esc)">Done</button>
          <div className="sx-page" key={section}>{content}</div>
        </main>
      </div>
    );
  }

  window.SyrixSettings = SyrixSettings;
})();
