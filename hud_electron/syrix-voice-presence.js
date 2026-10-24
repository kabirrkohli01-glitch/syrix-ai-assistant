/* SYRIX Voice Presence (2026-07-17, visual overhaul 2026-07-18) — the Voice
 * Mode orb. A contained amber-and-black glass sphere in the spirit of modern
 * ChatGPT/Siri voice UIs: dark glass body, fluid internal amber lobes that
 * swirl and react to REAL mic/TTS amplitude, an undulating energy rim while
 * speech flows, ember particles for life, and a soft specular highlight.
 * Performant Canvas 2D (no WebGL context to leak), DPR-capped, RAF gated on
 * active + document visibility, with a prefers-reduced-motion path that only
 * breathes opacity.
 *
 * API (unchanged — drop-in for the shell mount):
 *   const p = window.SyrixVoicePresence.create(canvas);
 *   p.setState('listening'|'user-speaking'|'holding'|'thinking'|'searching'|
 *              'speaking'|'interrupted'|'muted'|'error'|'idle');
 *   p.setLevel(mic0to1, tts0to1);
 *   p.setActive(bool);  p.resize();  p.stop();
 */
(function () {
  'use strict';

  var AMBER_HOT = [255, 214, 160];    // near-white amber for the core peak
  var AMBER_CORE = [247, 186, 105];   // warm inner glow
  var AMBER_MID = [233, 148, 56];     // body
  var AMBER_DEEP = [146, 78, 22];     // outer falloff
  var AMBER_EMBER = [255, 176, 84];   // particles
  var ERROR_TINT = [214, 92, 66];     // soft red-amber for error

  function lerp(a, b, t) { return a + (b - a) * t; }
  function clamp(x, lo, hi) { return x < lo ? lo : (x > hi ? hi : x); }
  function rgba(c, a) { return 'rgba(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + a + ')'; }
  function mix(c1, c2, t) {
    return [lerp(c1[0], c2[0], t), lerp(c1[1], c2[1], t), lerp(c1[2], c2[2], t)];
  }

  // Per-state visual targets:
  //   glow  — overall brightness            churn — internal lobe swirl speed
  //   react — amplitude → swell coupling    rim   — energy-rim visibility
  //   tint  — blend toward error red
  var STATES = {
    'idle':          { glow: 0.40, churn: 0.28, react: 0.10, rim: 0.10, tint: 0 },
    'listening':     { glow: 0.62, churn: 0.45, react: 0.60, rim: 0.30, tint: 0 },
    'user-speaking': { glow: 0.80, churn: 0.70, react: 1.00, rim: 0.55, tint: 0 },
    'holding':       { glow: 0.52, churn: 0.34, react: 0.25, rim: 0.18, tint: 0 },
    'thinking':      { glow: 0.58, churn: 0.95, react: 0.12, rim: 0.14, tint: 0 },
    'searching':     { glow: 0.66, churn: 1.20, react: 0.12, rim: 0.20, tint: 0 },
    'speaking':      { glow: 0.95, churn: 0.62, react: 0.95, rim: 0.85, tint: 0 },
    'interrupted':   { glow: 0.34, churn: 0.22, react: 0.05, rim: 0.08, tint: 0 },
    'muted':         { glow: 0.28, churn: 0.14, react: 0.02, rim: 0.05, tint: 0 },
    'error':         { glow: 0.42, churn: 0.24, react: 0.05, rim: 0.10, tint: 0.7 },
  };

  function create(canvas) {
    if (!canvas) return null;
    var ctx = canvas.getContext('2d');
    if (!ctx) return null;

    var reduceMotion = false;
    try { reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}

    var active = false, raf = 0, t0 = performance.now();
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var W = 0, H = 0, cx = 0, cy = 0, R = 0;

    // Smoothed (critically damped) drivers so nothing jitters.
    var micS = 0, ttsS = 0;
    var glowS = STATES.idle.glow, churnS = STATES.idle.churn, reactS = STATES.idle.react;
    var rimS = STATES.idle.rim, tintS = 0;
    var state = 'idle';
    var pulses = [];   // expanding rings emitted on speech-energy spikes
    var lastSpike = 0;

    // Internal fluid lobes — off-center radial gradients swirling INSIDE the
    // clipped sphere. Independent speeds/orbits so the motion never loops
    // visibly; wobble gives each lobe a slow shape-change.
    var lobes = [
      { r: 0.78, ang: 0.0, spd: 0.22, orbit: 0.34, phase: 0.0, wob: 0.9, alpha: 0.34 },
      { r: 0.60, ang: 2.1, spd: -0.31, orbit: 0.44, phase: 1.3, wob: 1.4, alpha: 0.30 },
      { r: 0.50, ang: 4.2, spd: 0.17, orbit: 0.52, phase: 2.7, wob: 0.7, alpha: 0.26 },
      { r: 0.38, ang: 5.4, spd: -0.42, orbit: 0.30, phase: 4.1, wob: 1.9, alpha: 0.24 },
    ];

    // Ember particles drifting slowly upward inside the sphere.
    var embers = [];
    (function seedEmbers() {
      for (var i = 0; i < 14; i++) {
        embers.push({
          a: Math.random() * Math.PI * 2,       // angle in the disc
          d: Math.pow(Math.random(), 0.6),      // 0..1 distance from center
          s: 0.02 + Math.random() * 0.05,       // rise speed
          tw: Math.random() * Math.PI * 2,      // twinkle phase
          sz: 0.8 + Math.random() * 1.6,
        });
      }
    })();

    function resize() {
      var rect = canvas.getBoundingClientRect();
      W = Math.max(1, Math.round(rect.width));
      H = Math.max(1, Math.round(rect.height));
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      cx = W / 2; cy = H / 2;
      R = Math.min(W, H) * 0.42;   // orb radius; leaves room for the rim glow
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function setState(s) {
      if (STATES[s]) state = s;
    }

    function setLevel(mic, tts) {
      micS = micS + (clamp(Number(mic) || 0, 0, 1) - micS) * 0.25;
      var tt = clamp(Number(tts) || 0, 0, 1);
      ttsS = ttsS + (tt - ttsS) * 0.25;
      // Emit a pulse ring on a speech-energy spike (sentence onsets).
      var now = performance.now();
      if (state === 'speaking' && tt > 0.55 && (now - lastSpike) > 460 && !reduceMotion) {
        pulses.push({ t: now, life: 1200 });
        lastSpike = now;
        if (pulses.length > 4) pulses.shift();
      }
    }

    function drawBlob(x, y, radius, col, alpha) {
      if (radius <= 0.5 || alpha <= 0.004) return;
      var g = ctx.createRadialGradient(x, y, radius * 0.04, x, y, radius);
      g.addColorStop(0, rgba(col, alpha));
      g.addColorStop(0.5, rgba(col, alpha * 0.5));
      g.addColorStop(1, rgba(col, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(x, y, radius, 0, Math.PI * 2);
      ctx.fill();
    }

    // Undulating energy rim — radius modulated by layered sines so it reads as
    // a living waveform hugging the sphere edge (Siri-style), amplitude-keyed.
    function drawRim(t, amp, col, alpha) {
      if (alpha <= 0.01) return;
      var steps = 72;
      var wobble = reduceMotion ? 0 : (0.015 + 0.05 * amp);
      ctx.beginPath();
      for (var i = 0; i <= steps; i++) {
        var a = (i / steps) * Math.PI * 2;
        var n = Math.sin(a * 3 + t * 2.1) * 0.45 +
                Math.sin(a * 5 - t * 1.6) * 0.35 +
                Math.sin(a * 8 + t * 3.3) * 0.20;
        var rr = R * (1.015 + wobble * n);
        var x = cx + Math.cos(a) * rr;
        var y = cy + Math.sin(a) * rr;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.strokeStyle = rgba(col, alpha);
      ctx.lineWidth = 1.4 + 2.2 * amp;
      ctx.stroke();
    }

    function frame() {
      if (!active) { raf = 0; return; }
      raf = requestAnimationFrame(frame);
      if (document.hidden) return;

      var tgt = STATES[state] || STATES.idle;
      glowS += (tgt.glow - glowS) * 0.06;
      churnS += (tgt.churn - churnS) * 0.05;
      reactS += (tgt.react - reactS) * 0.06;
      rimS += (tgt.rim - rimS) * 0.07;
      tintS += (tgt.tint - tintS) * 0.06;

      var now = performance.now();
      var t = (now - t0) / 1000;
      var amp = Math.max(micS, ttsS);
      var breathe = reduceMotion ? 0 : (Math.sin(t * 0.85) * 0.5 + 0.5);
      var swell = 1 + (0.035 * breathe) + (0.10 * amp * reactS);

      var hotCol = mix(AMBER_HOT, ERROR_TINT, tintS);
      var coreCol = mix(AMBER_CORE, ERROR_TINT, tintS);
      var midCol = mix(AMBER_MID, ERROR_TINT, tintS);
      var deepCol = mix(AMBER_DEEP, ERROR_TINT, tintS);

      ctx.clearRect(0, 0, W, H);

      // ── Outer ambience (outside the sphere) ──
      ctx.globalCompositeOperation = 'lighter';
      drawBlob(cx, cy, R * 1.55 * swell, deepCol, 0.16 * glowS);

      // Speech pulse rings radiating past the rim.
      for (var p = pulses.length - 1; p >= 0; p--) {
        var pu = pulses[p];
        var age = (now - pu.t) / pu.life;
        if (age >= 1) { pulses.splice(p, 1); continue; }
        var rad = R * (1.02 + age * 0.55);
        ctx.strokeStyle = rgba(coreCol, (1 - age) * (1 - age) * 0.30 * glowS);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(cx, cy, rad, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Energy rim (waveform edge).
      drawRim(t, amp, coreCol, rimS * (0.35 + 0.65 * amp) * glowS + 0.06);

      // ── Sphere body ──
      ctx.globalCompositeOperation = 'source-over';
      // Dark glass base — near-black with the faintest deep-amber depth.
      var base = ctx.createRadialGradient(cx - R * 0.25, cy - R * 0.3, R * 0.1, cx, cy, R);
      base.addColorStop(0, 'rgba(26,17,9,0.96)');
      base.addColorStop(0.65, 'rgba(12,8,4,0.97)');
      base.addColorStop(1, 'rgba(5,3,2,0.98)');
      ctx.fillStyle = base;
      ctx.beginPath();
      ctx.arc(cx, cy, R * swell, 0, Math.PI * 2);
      ctx.fill();

      // ── Internal fluid (clipped to the sphere) ──
      ctx.save();
      ctx.beginPath();
      ctx.arc(cx, cy, R * swell * 0.985, 0, Math.PI * 2);
      ctx.clip();
      ctx.globalCompositeOperation = 'lighter';

      var dt = reduceMotion ? 0 : 0.016;
      for (var i = 0; i < lobes.length; i++) {
        var b = lobes[i];
        b.ang += b.spd * dt * (0.5 + churnS);
        var wob = Math.sin(t * b.wob + b.phase);
        var ox = Math.cos(b.ang + b.phase) * R * b.orbit * (0.85 + 0.15 * wob);
        var oy = Math.sin(b.ang * 0.92 + b.phase) * R * b.orbit * (0.85 - 0.15 * wob);
        var rr = R * b.r * (1 + 0.10 * wob) * swell;
        var col = (i % 2 === 0) ? midCol : coreCol;
        drawBlob(cx + ox, cy + oy, rr, col, b.alpha * glowS * (0.8 + 0.5 * amp * reactS));
      }

      // Bright center core — the "voice" itself, keyed to amplitude.
      drawBlob(cx, cy, R * (0.30 + 0.14 * amp * reactS) * swell, coreCol, (0.42 + 0.34 * amp) * glowS);
      drawBlob(cx, cy, R * (0.13 + 0.08 * amp * reactS) * swell, hotCol, (0.30 + 0.45 * amp) * glowS);

      // Ember particles drifting up, twinkling.
      if (!reduceMotion) {
        for (var e2 = 0; e2 < embers.length; e2++) {
          var em = embers[e2];
          em.d -= em.s * 0.016 * (0.4 + churnS * 0.8);
          em.a += 0.0015 * (e2 % 2 ? 1 : -1);
          if (em.d <= 0.05) { em.d = 0.95; em.a = Math.random() * Math.PI * 2; }
          var er = em.d * R * 0.9;
          var exx = cx + Math.cos(em.a) * er;
          var eyy = cy + Math.sin(em.a) * er * 0.9 + (em.d - 0.5) * R * 0.15;
          var twk = 0.5 + 0.5 * Math.sin(t * 2.2 + em.tw);
          ctx.fillStyle = rgba(AMBER_EMBER, 0.10 + 0.22 * twk * glowS);
          ctx.beginPath();
          ctx.arc(exx, eyy, em.sz, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.restore();

      // ── Glass finish ──
      ctx.globalCompositeOperation = 'source-over';
      // Rim light: thin amber edge, brighter toward the top-left key light.
      var rim = ctx.createLinearGradient(cx - R, cy - R, cx + R, cy + R);
      rim.addColorStop(0, rgba(coreCol, 0.55 * glowS + 0.08));
      rim.addColorStop(0.5, rgba(deepCol, 0.20 * glowS + 0.04));
      rim.addColorStop(1, rgba(deepCol, 0.30 * glowS + 0.05));
      ctx.strokeStyle = rim;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(cx, cy, R * swell, 0, Math.PI * 2);
      ctx.stroke();

      // Specular highlight — soft arc top-left, like light on glass.
      var hl = ctx.createRadialGradient(cx - R * 0.38, cy - R * 0.46, R * 0.02, cx - R * 0.38, cy - R * 0.46, R * 0.55);
      hl.addColorStop(0, 'rgba(255,236,210,' + (0.10 + 0.06 * glowS) + ')');
      hl.addColorStop(1, 'rgba(255,236,210,0)');
      ctx.fillStyle = hl;
      ctx.beginPath();
      ctx.arc(cx, cy, R * swell, 0, Math.PI * 2);
      ctx.fill();
    }

    function setActive(on) {
      on = !!on;
      if (on === active) return;
      active = on;
      if (on) {
        resize();
        t0 = performance.now();
        if (!raf) raf = requestAnimationFrame(frame);
      }
    }

    function stop() {
      active = false;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
    }

    resize();
    return { setState: setState, setLevel: setLevel, setActive: setActive, resize: resize, stop: stop };
  }

  window.SyrixVoicePresence = { create: create };
})();
