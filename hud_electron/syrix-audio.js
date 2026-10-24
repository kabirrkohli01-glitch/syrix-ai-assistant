/* SYRIX HUD — Web Audio sound system */
(function () {
  let ctx;
  let muted = false;
  let masterGain;
  const MASTER_VOLUME = 0.24;
  function storedSfxMuted() {
    try {
      const v = localStorage.getItem('syrix_sfx_muted') || localStorage.getItem('syrix_audio_muted');
      if (v === '1' || v === 'true') return true;
      const legacy = JSON.parse(localStorage.getItem('syrix_hud_v5_cfg') || localStorage.getItem('syrix_hud_v4_cfg') || '{}');
      return !!legacy?.sfxMuted;
    } catch (_) {
      return false;
    }
  }
  function isMuted() { return muted || storedSfxMuted(); }
  function init() {
    if (ctx) return;
    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      masterGain = ctx.createGain();
      masterGain.gain.value = isMuted() ? 0 : MASTER_VOLUME;
      masterGain.connect(ctx.destination);
    } catch (e) {}
  }
  function setMuted(m) { muted = !!m; if (masterGain) masterGain.gain.value = isMuted() ? 0 : MASTER_VOLUME; }
  function tone(freq, dur, type, vol) {
    if (!ctx || isMuted()) return;
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = type || 'sine';
    o.frequency.value = freq;
    g.gain.setValueAtTime(0, ctx.currentTime);
    g.gain.linearRampToValueAtTime(vol || 0.18, ctx.currentTime + 0.005);
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + dur);
    o.connect(g).connect(masterGain);
    o.start();
    o.stop(ctx.currentTime + dur);
  }
  function click() { init(); tone(1300, 0.06, 'square', 0.06); tone(1900, 0.04, 'sine', 0.04); }
  function hover() { init(); tone(2400, 0.04, 'sine', 0.025); }
  function activate() { init(); tone(440, 0.12, 'sine', 0.12); tone(880, 0.18, 'sine', 0.08); }
  function warn() { init(); tone(220, 0.18, 'sawtooth', 0.1); setTimeout(() => tone(180, 0.18, 'sawtooth', 0.1), 120); }
  function notify() { init(); tone(880, 0.08, 'sine', 0.08); setTimeout(() => tone(1320, 0.12, 'sine', 0.08), 60); }
  /* Supercomputer task cues (2026-07-26).
   *
   * A long agentic task is exactly the case where you look away — that is the
   * point of it — so it has to be able to tell you it is finished without you
   * watching. Three distinct shapes so the meaning carries without looking:
   * a rising resolve for done, a soft two-note query for "I need you", and a
   * short fall for failed. Synthesised like every other cue here (there are no
   * sound files in this repo at all) and muted by the same global switch.
   */
  function taskComplete() {
    init();
    // A major-third rise landing on the octave: finished, and finished WELL.
    tone(587.33, 0.09, 'sine', 0.07);
    setTimeout(() => tone(739.99, 0.10, 'sine', 0.075), 85);
    setTimeout(() => tone(987.77, 0.30, 'sine', 0.065), 175);
  }
  function taskNeedsYou() {
    init();
    // Deliberately unresolved — it is a question, not an announcement.
    tone(659.25, 0.10, 'sine', 0.055);
    setTimeout(() => tone(830.61, 0.16, 'sine', 0.05), 105);
  }
  function taskFailed() {
    init();
    // Low and brief. Something did not work; nothing is on fire.
    tone(392.00, 0.12, 'triangle', 0.06);
    setTimeout(() => tone(293.66, 0.22, 'triangle', 0.05), 110);
  }
  function timerStart() { init(); tone(660, 0.055, 'sine', 0.07); setTimeout(() => tone(990, 0.075, 'sine', 0.055), 48); }
  function timerStop() { init(); tone(520, 0.06, 'triangle', 0.06); setTimeout(() => tone(330, 0.08, 'triangle', 0.05), 54); }
  function timerReset() { init(); tone(420, 0.045, 'square', 0.045); setTimeout(() => tone(420, 0.045, 'square', 0.035), 70); }
  function boot() {
    init();
    if (!ctx || isMuted()) return;
    const now = ctx.currentTime;

    try {
      const sr = ctx.sampleRate;
      const nBuf = ctx.createBuffer(1, Math.round(sr * 0.12), sr);
      for (let ch = 0; ch < 2; ch++) {
        if (ch >= nBuf.numberOfChannels) break;
        const d = nBuf.getChannelData(ch);
        for (let i = 0; i < d.length; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / d.length);
      }
      const nSrc = ctx.createBufferSource();
      nSrc.buffer = nBuf;
      const nF = ctx.createBiquadFilter();
      nF.type = 'highpass';
      nF.frequency.setValueAtTime(4800, now);
      nF.Q.value = 0.7;
      const nG = ctx.createGain();
      nG.gain.setValueAtTime(0, now);
      nG.gain.linearRampToValueAtTime(0.012, now + 0.012);
      nG.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
      nSrc.connect(nF).connect(nG).connect(masterGain);
      nSrc.start(now);
    } catch (_) {}

    function softTone(freq, delay, dur, vol, type) {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      const f = ctx.createBiquadFilter();
      const t0 = now + delay;
      o.type = type || 'sine';
      o.frequency.value = freq;
      f.type = 'lowpass';
      f.frequency.setValueAtTime(2600, t0);
      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(vol, t0 + 0.035);
      g.gain.exponentialRampToValueAtTime(0.001, t0 + dur);
      o.connect(f).connect(g).connect(masterGain);
      o.start(t0);
      o.stop(t0 + dur + 0.03);
    }

    softTone(329.63, 0.04, 0.42, 0.035, 'triangle');
    softTone(493.88, 0.12, 0.46, 0.027, 'sine');
    softTone(739.99, 0.24, 0.52, 0.018, 'sine');
    softTone(1479.98, 0.37, 0.22, 0.008, 'sine');
  }
  function startHum() {
    init();
    if (!ctx) return;
    const o1 = ctx.createOscillator();
    const o2 = ctx.createOscillator();
    const g = ctx.createGain();
    o1.frequency.value = 60;
    o2.frequency.value = 90.5;
    o1.type = 'sine'; o2.type = 'sine';
    g.gain.value = 0;
    g.gain.linearRampToValueAtTime(0.012, ctx.currentTime + 1);
    o1.connect(g); o2.connect(g);
    g.connect(masterGain);
    o1.start(); o2.start();
    return { stop: () => { try { o1.stop(); o2.stop(); } catch (e) {} } };
  }
  /* 2026-07-11 — Voice Mode search ambience: a very soft, relaxing pad
     (two low sine partials a fifth apart through a slowly-breathing lowpass)
     the voice overlay fades in only while a long deep-web-search phase is
     running, so multi-second silences still feel alive. Returns a handle
     whose stop() fades out over ~0.5s; returns null if audio is unavailable
     or muted. Nothing here loops samples — it is all synthesized, so there
     is no asset to load and nothing to glitch. */
  function searchAmbience() {
    init();
    if (!ctx || isMuted()) return null;
    try {
      const now = ctx.currentTime;
      const g = ctx.createGain();
      g.gain.setValueAtTime(0.0001, now);
      g.connect(masterGain);
      const f = ctx.createBiquadFilter();
      f.type = 'lowpass';
      f.frequency.setValueAtTime(820, now);
      f.Q.value = 0.4;
      f.connect(g);
      const o1 = ctx.createOscillator();
      o1.type = 'sine';
      o1.frequency.value = 196.0;      // G3
      const o2 = ctx.createOscillator();
      o2.type = 'sine';
      o2.frequency.value = 293.66;     // D4 — a calm open fifth
      const og1 = ctx.createGain(); og1.gain.value = 0.55;
      const og2 = ctx.createGain(); og2.gain.value = 0.32;
      o1.connect(og1); og1.connect(f);
      o2.connect(og2); og2.connect(f);
      const lfo = ctx.createOscillator();
      lfo.frequency.value = 0.12;      // slow filter breathing for movement
      const lfoG = ctx.createGain(); lfoG.gain.value = 210;
      lfo.connect(lfoG); lfoG.connect(f.frequency);
      g.gain.linearRampToValueAtTime(0.045, now + 1.4); // whisper-quiet under masterGain
      o1.start(now); o2.start(now); lfo.start(now);
      let stopped = false;
      return {
        stop: () => {
          if (stopped) return;
          stopped = true;
          try {
            const t = ctx.currentTime;
            g.gain.cancelScheduledValues(t);
            g.gain.setValueAtTime(Math.max(0.0001, g.gain.value), t);
            g.gain.exponentialRampToValueAtTime(0.0001, t + 0.5);
          } catch (_) {}
          setTimeout(() => {
            try { o1.stop(); o2.stop(); lfo.stop(); } catch (_) {}
            try { g.disconnect(); } catch (_) {}
          }, 650);
        },
      };
    } catch (_) { return null; }
  }
  /* 2026-07-17 — Voice Mode thinking pad: a warm, rounded low dyad (a calm
     low fifth through a slowly-breathing lowpass), even quieter and softer than
     searchAmbience. The overlay fades it in only when genuine processing runs
     past ~1.5s, and fades it out the instant the answer starts — it must never
     overlap speech, never restart while live, and never sound like a scanner.
     Returns a handle whose stop() fades out over ~0.5s; null if muted. */
  function thinkingPad() {
    init();
    if (!ctx || isMuted()) return null;
    try {
      const now = ctx.currentTime;
      const g = ctx.createGain();
      g.gain.setValueAtTime(0.0001, now);
      g.connect(masterGain);
      const f = ctx.createBiquadFilter();
      f.type = 'lowpass';
      f.frequency.setValueAtTime(560, now);
      f.Q.value = 0.3;
      f.connect(g);
      const o1 = ctx.createOscillator();
      o1.type = 'sine';
      o1.frequency.value = 130.81;     // C3 — low, warm root
      const o2 = ctx.createOscillator();
      o2.type = 'sine';
      o2.frequency.value = 196.0;      // G3 — a soft fifth above
      const og1 = ctx.createGain(); og1.gain.value = 0.6;
      const og2 = ctx.createGain(); og2.gain.value = 0.28;
      o1.connect(og1); og1.connect(f);
      o2.connect(og2); og2.connect(f);
      const lfo = ctx.createOscillator();
      lfo.frequency.value = 0.16;      // gentle breathing
      const lfoG = ctx.createGain(); lfoG.gain.value = 120;
      lfo.connect(lfoG); lfoG.connect(f.frequency);
      g.gain.linearRampToValueAtTime(0.03, now + 0.6);  // soft fade-in
      o1.start(now); o2.start(now); lfo.start(now);
      let stopped = false;
      return {
        stop: () => {
          if (stopped) return;
          stopped = true;
          try {
            const t = ctx.currentTime;
            g.gain.cancelScheduledValues(t);
            g.gain.setValueAtTime(Math.max(0.0001, g.gain.value), t);
            g.gain.exponentialRampToValueAtTime(0.0001, t + 0.5);
          } catch (_) {}
          setTimeout(() => {
            try { o1.stop(); o2.stop(); lfo.stop(); } catch (_) {}
            try { g.disconnect(); } catch (_) {}
          }, 620);
        },
      };
    } catch (_) { return null; }
  }
  window.SyrixSound = { click, hover, activate, warn, notify, timerStart, timerStop, timerReset, boot, setMuted, startHum, searchAmbience, thinkingPad, init, taskComplete, taskNeedsYou, taskFailed };
})();
