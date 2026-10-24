/* SYRIX PRIMARY BALL — Three.js 3D energy orb, voice-reactive */
(function () {
  'use strict';
  const T = window.THREE;
  if (!T) { console.warn('[SYRIX] Three.js not loaded'); return; }

  function createSyrixBall(canvas) {
    const renderer = new T.WebGLRenderer({ canvas, alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    // Phase-3 (2026-05-18) realism upgrade: enable ACES tone mapping and
    // physically-correct light falloff so MeshPhysicalMaterial reads as
    // genuine glass rather than the cartoony MeshStandard look it had before.
    try {
      renderer.outputColorSpace = T.SRGBColorSpace || renderer.outputColorSpace;
      renderer.toneMapping = T.ACESFilmicToneMapping || renderer.toneMapping;
      renderer.toneMappingExposure = 1.05;
      // useLegacyLights / physicallyCorrectLights removed in r155+; physical
      // lighting is the default in r160, so no assignment is needed.
    } catch (_) { /* older Three.js — ignore */ }

    const scene = new T.Scene();
    const camera = new T.PerspectiveCamera(42, 1, 0.01, 200);
    camera.position.z = 5.5;

    const root = new T.Group();
    scene.add(root);

    const COL = { p: 0xdce8ff, s: 0xb8ccf8, a: 0xd4c8ff, w: 0xffffff };

    // Body sphere — MeshPhysicalMaterial with clearcoat gives a wet/glassy
    // highlight reading. Falls back gracefully to MeshStandardMaterial if the
    // host Three.js build is older than r137.
    //
    // 2026-07-11 (obsidian-glass redesign) — spec: "transparent dark glass
    // body... smoked black glass... you can see depth inside". Dropped from
    // near-opaque (opacity implicit 1, metalness 0.88) to a true translucent
    // shell (opacity 0.60, metalness low, depthWrite off) so the inner wave
    // structure added below actually reads THROUGH the glass instead of
    // being hidden behind an opaque sphere. depthWrite:false + explicit
    // renderOrder keep the transparency sane: the glow shell draws first,
    // the glass shell next, the inner wave and rim last (drawn "in front"
    // regardless of triangle-level z, which is the standard trick for a
    // single translucent shell containing visible internal detail).
    const PhysCtor = T.MeshPhysicalMaterial || T.MeshStandardMaterial;
    const bodyMat = new PhysCtor({
      color: 0x050301, metalness: 0.10, roughness: 0.16,
      transparent: true, opacity: 0.46, depthWrite: false,
      emissive: new T.Color(0x0c0602), emissiveIntensity: 0.35,
      clearcoat: 1.0, clearcoatRoughness: 0.06,
      sheen: 0.4, sheenColor: new T.Color(COL.w), sheenRoughness: 0.35,
      envMapIntensity: 0.85,
    });
    const body = new T.Mesh(new T.SphereGeometry(1.0, 96, 96), bodyMat);
    body.renderOrder = 0;
    root.add(body);

    // 2026-07-09 (Voice Mode UI pass) — target look is a plain matte-dark
    // sphere with ONE thin rim ring hugging its edge and a subtle geometric
    // mesh texture, not a busy multi-ring/particle "energy ball" (reference:
    // Syrix voice mode ui.png). Rings/dots/particles/stars removed rather
    // than just dimmed, so there is nothing left to accidentally re-brighten
    // via the state formulas below; the wireframe shell is kept and made
    // more visible since it's the one element that matches the reference's
    // mesh texture.
    // 2026-07-11 — fully invisible now (opacity 0, not deleted, so a future
    // look can bring it back). The new obsidian-glass spec is explicit and
    // repeated: "smooth and reflective... clean circular silhouette...
    // minimal... the main focus is the thin gold boundary and the delicate
    // internal particle wave" — nothing else. The 11x11 latitude/longitude
    // grid (added 2026-07-09 for an earlier, different reference image) still
    // read as a visible wireframe-globe pattern even at opacity 0.015 (faint
    // additive geometry against a near-black background stays perceptible
    // through the renderer's ACES tone mapping), which contradicts "smooth"
    // and competed with the wave for focus — confirmed via screenshot.
    const wireMat = new T.MeshBasicMaterial({ color: COL.p, wireframe: true, transparent: true, opacity: 0.0, depthWrite: false });
    const wire = new T.Mesh(new T.SphereGeometry(1.03, 11, 11), wireMat);
    wire.renderOrder = 1;
    root.add(wire);

    // Outer glow — drawn first/behind everything else in the shell.
    const outerMat = new T.MeshBasicMaterial({ color: COL.p, transparent: true, opacity: 0.055, side: T.BackSide, depthWrite: false });
    const outer = new T.Mesh(new T.SphereGeometry(1.20, 32, 32), outerMat);
    outer.renderOrder = -1;
    root.add(outer);

    // Inner core — 2026-07-11 obsidian-glass redesign: the spec's "inner
    // core" IS the wave structure below, not a separate bright dot (a
    // visible eyeball-like white sphere here fights the wave for focus).
    // Shrunk and dimmed to a faint ember at the wave's crest — barely there,
    // reads as "the wave's own glow gathering," not a second light source.
    const coreMat = new T.MeshBasicMaterial({ color: COL.w, transparent: true, opacity: 0.45, depthWrite: false });
    const core = new T.Mesh(new T.SphereGeometry(0.055, 32, 32), coreMat);
    core.renderOrder = 3;
    root.add(core);

    // Core halo — single, clean
    const haloMat = new T.MeshBasicMaterial({ color: COL.p, transparent: true, opacity: 0.14, depthWrite: false });
    const halo = new T.Mesh(new T.SphereGeometry(0.11, 32, 32), haloMat);
    halo.renderOrder = 3;
    root.add(halo);

    // Thin luminous rim hugging the body's edge — "very thin... warm
    // champagne gold... soft and diffused, not a harsh neon line" (2026-07-11
    // spec). A crisp core ring plus a softer, wider glow ring behind it gives
    // the diffused-edge read the spec asks for without a custom fresnel
    // shader; their own surface shading under the point lights below
    // (kl/fl/rl) is what makes it "brighter in some sections, softer in
    // others."
    const ringDefs = [
      { r: 1.048, t: 0.055, col: COL.p, op: 0.30, rx: 0, ry: 0, spd: 0.006 },
      { r: 1.045, t: 0.018, col: COL.p, op: 0.95, rx: 0, ry: 0, spd: 0.009 },
    ];
    const rings = ringDefs.map(d => {
      const mat = new T.MeshBasicMaterial({ color: d.col, transparent: true, opacity: d.op });
      const m = new T.Mesh(new T.TorusGeometry(d.r, d.t, 16, 140), mat);
      m.rotation.set(d.rx, d.ry, 0);
      m.userData = { spd: d.spd, baseOp: d.op };
      m.renderOrder = 3;
      root.add(m);
      return m;
    });

    // ── Inner wave structure (2026-07-11 obsidian-glass redesign) ──────────
    // Spec: "a subtle flowing wave structure... 3D particle mesh / wireframe
    // surface... tiny golden-brown points and thin connections... curves
    // smoothly through the center like a flowing energy wave... one side
    // rises higher while the middle dips down... elegant curved S-like
    // flow... fine particles and lines... fades softly into the dark
    // interior." Built once as a static point cloud + matching line grid on
    // an S-curved height field, sized to sit inside the glass body; the only
    // per-frame cost is a slow rotation + gentle opacity breathe in tick().
    const WAVE_NX = 30, WAVE_NZ = 15;
    const waveGroup = new T.Group();
    root.add(waveGroup);

    function waveHeight(xn, zn) {
      // xn, zn normalized to [-1, 1]. sin term = the S-curve across X; the
      // squared dip term pulls the middle down; skew makes one side ride
      // higher than the other, per the written spec.
      const s = Math.sin(xn * Math.PI * 0.95);
      const dip = -Math.pow(1 - Math.abs(xn), 2) * 0.16;
      const skew = xn * 0.09;
      const zTilt = zn * 0.045;
      return s * 0.30 + dip + skew + zTilt;
    }

    const WAVE_DEEP = new T.Color(0x3a2308);   // fades into this near the edges (not pure black — must stay visible)
    const WAVE_MID = new T.Color(0xd9922f);
    const WAVE_HOT = new T.Color(0xffe6b0);    // brightest, near the crest/center

    const waveRows = [];               // [row][col] -> {pos:Vector3, color:Color} | null (thinned)
    for (let iz = 0; iz < WAVE_NZ; iz++) {
      const zn = (iz / (WAVE_NZ - 1)) * 2 - 1;
      const row = [];
      for (let ix = 0; ix < WAVE_NX; ix++) {
        const xn = (ix / (WAVE_NX - 1)) * 2 - 1;
        // Organic density variation — "small variations in density" — skip
        // a minority of points, weighted slightly toward the outer edges so
        // the wave visibly thins out rather than ending on a hard rectangle.
        const edgeBias = Math.max(Math.abs(xn), Math.abs(zn));
        if (Math.random() < 0.06 + edgeBias * 0.22) { row.push(null); continue; }
        const jitter = () => (Math.random() - 0.5) * 0.018;
        const x = xn * 0.80 + jitter();
        const y = waveHeight(xn, zn) + jitter();
        const z = zn * 0.42 + jitter();
        const centerFade = 1 - Math.pow(edgeBias, 1.3); // 1 at center, 0 at rim
        const col = WAVE_DEEP.clone().lerp(
          Math.random() > 0.5 ? WAVE_MID : WAVE_HOT,
          Math.max(0.08, centerFade) * (0.55 + Math.random() * 0.45),
        );
        row.push({ pos: new T.Vector3(x, y, z), color: col });
      }
      waveRows.push(row);
    }

    const wavePositions = [];
    const waveColors = [];
    for (const row of waveRows) {
      for (const pt of row) {
        if (!pt) continue;
        wavePositions.push(pt.pos.x, pt.pos.y, pt.pos.z);
        waveColors.push(pt.color.r, pt.color.g, pt.color.b);
      }
    }
    const waveGeo = new T.BufferGeometry();
    waveGeo.setAttribute('position', new T.Float32BufferAttribute(wavePositions, 3));
    waveGeo.setAttribute('color', new T.Float32BufferAttribute(waveColors, 3));
    const waveMat = new T.PointsMaterial({
      size: 0.052, sizeAttenuation: true, vertexColors: true,
      transparent: true, opacity: 0.95, depthWrite: false,
    });
    const wavePoints = new T.Points(waveGeo, waveMat);
    wavePoints.renderOrder = 2; // renderOrder must be set on the renderable mesh, not the parent Group
    waveGroup.add(wavePoints);

    // Thin connecting lines — "made from many tiny golden-brown points and
    // thin connections... fabric-like appearance". A sparse neighbor grid
    // (not every link — "variations in density"), dimmer than the points.
    const waveLinePos = [];
    const waveLineCol = [];
    function pushLine(a, b) {
      if (!a || !b) return;
      if (Math.random() < 0.4) return; // sparse, not a solid mesh sheet
      waveLinePos.push(a.pos.x, a.pos.y, a.pos.z, b.pos.x, b.pos.y, b.pos.z);
      const c1 = a.color.clone().multiplyScalar(0.6);
      const c2 = b.color.clone().multiplyScalar(0.6);
      waveLineCol.push(c1.r, c1.g, c1.b, c2.r, c2.g, c2.b);
    }
    for (let iz = 0; iz < WAVE_NZ; iz++) {
      for (let ix = 0; ix < WAVE_NX; ix++) {
        const cur = waveRows[iz][ix];
        if (ix + 1 < WAVE_NX) pushLine(cur, waveRows[iz][ix + 1]);
        if (iz + 1 < WAVE_NZ) pushLine(cur, waveRows[iz + 1][ix]);
      }
    }
    const waveLineGeo = new T.BufferGeometry();
    waveLineGeo.setAttribute('position', new T.Float32BufferAttribute(waveLinePos, 3));
    waveLineGeo.setAttribute('color', new T.Float32BufferAttribute(waveLineCol, 3));
    const waveLineMat = new T.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0.30, depthWrite: false,
    });
    const waveLines = new T.LineSegments(waveLineGeo, waveLineMat);
    waveLines.renderOrder = 2;
    waveGroup.add(waveLines);

    // No orbiting particles or starfield in this look — kept as an empty
    // array (not deleted) so the tick()/dispose() code below needs no
    // further changes; forEach on an empty array is a no-op.
    const dotGroup = new T.Group();
    root.add(dotGroup);
    const particles = [];
    const starGeo = new T.BufferGeometry();
    starGeo.setAttribute('position', new T.BufferAttribute(new Float32Array(0), 3));
    const starMat = new T.PointsMaterial({ color: COL.p, size: 0.020, transparent: true, opacity: 0, sizeAttenuation: true });
    const stars = new T.Points(starGeo, starMat);
    root.add(stars);

    // Lights — Phase-3 (2026-05-18) rebalanced for physically-correct mode.
    // HemisphereLight gives the orb a subtle sky/ground gradient so it never
    // looks flat-shaded from a single side. KeyLight reads as cyan rim, fill
    // adds the cool blue secondary, and the top-back light gives a small
    // specular hotspot that catches the new clearcoat layer.
    const hemi = new T.HemisphereLight(0xf0f4ff, 0x04040a, 0.55);
    scene.add(hemi);
    scene.add(new T.AmbientLight(0xffffff, 0.18));
    const kl = new T.PointLight(COL.p, 8.0, 18); kl.position.set(3, 2, 4); scene.add(kl);
    const fl = new T.PointLight(COL.s, 5.0, 14); fl.position.set(-3, -1, 2); scene.add(fl);
    const rl = new T.PointLight(0xffffff, 2.0, 8); rl.position.set(0, 4, -3); scene.add(rl);

    // State
    let state = 'idle', micLvl = 0, micTarget = 0, voiceLvl = 0, voiceTarget = 0;
    // 2026-07-11 (Voice Mode black/amber redesign) — calm profile. When set,
    // every state maps to one steady heartbeat-slow swell with only a whisper
    // of extra glow for mic/voice energy: no strobing, no dramatic scale or
    // light jumps. Opt-in per host (the Voice Mode overlay turns it on); the
    // default 'false' keeps every other orb surface byte-identical.
    let calm = false;
    let curScale = 1, tgtScale = 1;
    let mx = 0, my = 0, dragData = null;
    let userRotX = 0, userRotY = 0;

    const onMouseMove = e => {
      if (!dragData) { mx = (e.clientX / innerWidth - 0.5) * 2; my = (e.clientY / innerHeight - 0.5) * 2; }
      else { userRotY += (e.clientX - dragData.lx) * 0.007; userRotX += (e.clientY - dragData.ly) * 0.007; dragData.lx = e.clientX; dragData.ly = e.clientY; }
    };
    const onMouseDown = e => { dragData = { lx: e.clientX, ly: e.clientY }; };
    const onMouseUp = () => { dragData = null; };
    window.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mousedown', onMouseDown);
    window.addEventListener('mouseup', onMouseUp);

    let stopped = false;
    let active = true;
    let raf = 0;
    function tick() {
      if (stopped) return;
      raf = requestAnimationFrame(tick);
      // 2026-05-19 optimization: skip GPU work entirely when the HUD window
      // is hidden (minimized, focused elsewhere, alt-tabbed). RAF still fires
      // at ~1Hz when hidden so the loop resumes immediately on re-show.
      // 2026-07-09 — `active` mirrors the same idea for a persistent-but-
      // currently-not-shown host (e.g. Voice Mode closed): skip render work,
      // keep the RAF alive so it resumes instantly on reopen.
      if (typeof document !== 'undefined' && document.hidden) return;
      if (!active) return;
      const t = performance.now() * 0.001;
      // Suppress orb breathing in idle/sleep to keep Aurora Glass calm.
      let pulse = 0.5 + 0.5 * Math.sin(t * 2.1);
      if (state === 'idle' || state === 'sleep' || state === 'listening') {
        pulse = 0.5; // fixed midpoint — no breathing
      }
      try {
        if (document.body && document.body.classList && document.body.classList.contains('theme-aurora-glass')) {
          pulse = 0.5; // extra safety for Aurora Glass theme
        }
      } catch (_) {}
      const fastP = 0.5 + 0.5 * Math.sin(t * 7.0);
      // Voice-like oscillators for the 'speaking' state. 2026-07-09 — the
      // previous 14.0/9.3 rad/s pair (~2.2Hz) read as a strobe/blink rather
      // than "alive": every dot and light swung through its full range every
      // ~450ms. Slowed to a slow-breathing cadence (~0.5Hz) with a much
      // smaller amplitude window (0.72-1.0 instead of 0-1) so the glow rises
      // and falls smoothly instead of flashing on/off.
      const speakP  = 0.72 + 0.28 * Math.sin(t * 3.1);
      const speakP2 = 0.72 + 0.28 * Math.sin(t * 2.15 + 0.9);
      micLvl += (micTarget - micLvl) * 0.16;
      voiceLvl += (voiceTarget - voiceLvl) * 0.16;
      const lvl = state === 'listening' ? micLvl : state === 'speaking' ? voiceLvl : 0;

      // Inner wave — perpetual, very slow drift regardless of state/profile
      // ("curves smoothly... suspended inside glass", spec). No per-vertex
      // work (cheap): rotate the whole group and let its own points/lines
      // catch the moving key light naturally, with only a whisper of extra
      // shimmer tied to live energy.
      waveGroup.rotation.y = Math.sin(t * 0.05) * 0.10;
      waveGroup.rotation.x = Math.cos(t * 0.037) * 0.035;
      waveMat.opacity = 0.85 + 0.10 * Math.sin(t * 0.6) + lvl * 0.10;
      waveLineMat.opacity = 0.32 + 0.08 * Math.sin(t * 0.6 + 0.6);

      // ── CALM PROFILE (2026-07-11) — steady-heartbeat rendering, used by
      // the Voice Mode overlay's black/amber orb. One slow swell drives
      // everything; mic/voice energy adds only a whisper of extra glow.
      // Returns early so the dramatic per-state formulas below never run.
      if (calm) {
        const hb = 0.5 + 0.5 * Math.sin(t * 1.9);     // ~0.3 Hz heartbeat swell
        const gl = Math.min(0.5, lvl * 0.5);          // heavily damped live energy
        tgtScale = state === 'sleep' ? 0.90
          : state === 'interrupted' ? 0.96
          : 1.0 + hb * 0.018 + gl * 0.030;
        curScale += (tgtScale - curScale) * 0.03;
        root.scale.setScalar(curScale);
        const ctX = dragData ? userRotX : -my * 0.10;
        const ctY = dragData ? userRotY : mx * 0.12 + t * 0.0016;
        root.rotation.x += (ctX - root.rotation.x) * 0.04;
        root.rotation.y += (ctY - root.rotation.y) * 0.03;
        const cws = 0.0026 + gl * 0.0020;
        wire.rotation.y += cws; wire.rotation.x += cws * 0.38;
        // 2026-07-11 — wireMat left at 0 (was a per-frame override that kept
        // relighting the outer wireframe-globe grid regardless of the
        // constructor's opacity; confirmed via screenshot the grid stayed
        // visible even at 0.015 there). outer/core/halo dimmed to match the
        // "no separate bright center, smooth clean silhouette" spec — the
        // wave below is the one internal focal point now.
        outerMat.opacity = 0.045 + hb * 0.018 + gl * 0.022;
        const ccs = 1.0 + hb * 0.10 + gl * 0.16;
        core.scale.setScalar(ccs);
        coreMat.opacity = 0.22 + hb * 0.08 + gl * 0.08;
        halo.scale.setScalar(ccs * 1.45);
        haloMat.opacity = 0.07 + hb * 0.05 + gl * 0.06;
        rings.forEach(ring => {
          ring.rotation.z += ring.userData.spd * 0.45;
          ring.material.opacity = ring.userData.baseOp * (0.55 + hb * 0.18 + gl * 0.15);
        });
        kl.intensity = 4.6 + hb * 1.1 + gl * 1.6;
        fl.intensity = 3.0 + hb * 0.8 + gl * 1.2;
        renderer.render(scene, camera);
        return;
      }

      // Scale
      if (state === 'sleep') tgtScale = 0.80;
      else if (state === 'listening') tgtScale = 1.0 + lvl * 0.30;
      else if (state === 'speaking') tgtScale = 1.0 + voiceLvl * 0.22;
      else if (state === 'processing') tgtScale = 1.0 + fastP * 0.055;
      // 2026-07-09 (Voice Mode V2 foundation pass) — a quick contraction
      // below resting scale reads as "cut off" (barge-in just aborted TTS),
      // distinct from speaking's glow and listening's energetic swell.
      else if (state === 'interrupted') tgtScale = 0.92;
      else tgtScale = 1.0 + pulse * 0.022;
      curScale += (tgtScale - curScale) * 0.045;
      root.scale.setScalar(curScale);

      // Rotation
      const tX = dragData ? userRotX : -my * 0.18;
      const tY = dragData ? userRotY : mx * 0.22 + t * 0.003;
      root.rotation.x += (tX - root.rotation.x) * 0.06;
      root.rotation.y += (tY - root.rotation.y) * 0.04;

      // Wire
      const ws = state === 'sleep' ? 0.0015 : state === 'speaking' ? 0.022 + voiceLvl * 0.025 : state === 'processing' ? 0.012 : 0.005 + lvl * 0.01;
      wire.rotation.y += ws; wire.rotation.x += ws * 0.38;
      // 2026-07-09 — boosted across the board so the mesh texture reads
      // clearly against the dark body, matching the reference's visible
      // geometric surface pattern instead of a barely-there faint shell.
      wireMat.opacity = state === 'sleep' ? 0.10 : state === 'speaking' ? 0.34 + speakP * 0.18 + voiceLvl * 0.10 : state === 'processing' ? 0.30 + fastP * 0.08 : 0.24 + lvl * 0.12 + pulse * 0.025;
      outerMat.opacity = state === 'sleep' ? 0.025 : state === 'speaking' ? 0.10 + speakP * 0.08 + voiceLvl * 0.06 : state === 'processing' ? 0.09 + fastP * 0.04 : 0.06 + lvl * 0.09 + pulse * 0.018;

      // Core + halo — clean breathing
      const cs = state === 'sleep' ? 0.75 + pulse * 0.07 : state === 'speaking' ? 1.0 + speakP * 0.18 + voiceLvl * 0.22 : state === 'processing' ? 1.0 + fastP * 0.22 : 1.0 + pulse * 0.18 + lvl * 0.55;
      core.scale.setScalar(cs);
      coreMat.opacity = state === 'sleep' ? 0.40 + pulse * 0.08 : state === 'speaking' ? 0.88 + speakP * 0.12 + voiceLvl * 0.10 : state === 'processing' ? 0.88 + fastP * 0.12 : state === 'interrupted' ? 0.55 : 0.90 + lvl * 0.10;
      const haloS = cs * (state === 'speaking' ? 1.5 + speakP * 0.35 : state === 'processing' ? 1.4 + fastP * 0.18 : 1.4);
      halo.scale.setScalar(haloS);
      haloMat.opacity = state === 'sleep' ? 0.06 : state === 'speaking' ? 0.22 + speakP2 * 0.22 + voiceLvl * 0.30 : state === 'processing' ? 0.20 + fastP * 0.20 : state === 'interrupted' ? 0.10 : 0.18 + lvl * 0.35 + pulse * 0.05;

      // Rings
      const rs = state === 'sleep' ? 0.15 : state === 'speaking' ? 2.5 + speakP * 1.5 + voiceLvl * 3.0 : state === 'processing' ? 3.0 : 1.0 + lvl * 5.0;
      rings.forEach(ring => {
        ring.rotation.z += ring.userData.spd * rs;
        ring.material.opacity = ring.userData.baseOp * (state === 'sleep' ? 0.25 : state === 'speaking' ? 0.70 + speakP * 0.30 + voiceLvl * 0.40 : state === 'processing' ? 0.60 + fastP * 0.40 : 0.65 + pulse * 0.35 + lvl * 0.5);
      });

      // Dots — speaking uses the same slow-breathing cadence as speakP (was a
      // raw 14 rad/s sine, i.e. the strobe/blink look) with a gentler per-dot
      // phase offset so they shimmer in sequence instead of flashing in unison.
      dotGroup.children.forEach((dot, i) => {
        const phase = (i / 24) * Math.PI * 2;
        dot.material.opacity = state === 'sleep' ? 0.2 : state === 'speaking' ? (0.55 + 0.35 * Math.sin(t * 3.1 + phase)) * (0.75 + voiceLvl * 0.25) : state === 'processing' ? (0.4 + 0.6 * Math.sin(t * 11 + phase)) * 0.85 : (0.5 + 0.5 * Math.sin(t * 3 + phase)) * (0.6 + lvl * 0.4);
      });

      // Particles
      const ps = state === 'sleep' ? 0.10 : state === 'speaking' ? 2.2 + voiceLvl * 3.5 : state === 'processing' ? 1.8 : 0.55 + lvl * 2.8;
      particles.forEach(p => {
        p.userData.ang += 0.009 * p.userData.spd * ps;
        const { r, ang, elev } = p.userData;
        p.position.set(Math.cos(ang)*r*Math.cos(elev), Math.sin(elev)*r, Math.sin(ang)*r*Math.cos(elev));
        p.material.opacity = p.userData.baseOp * (state === 'sleep' ? 0.12 : state === 'speaking' ? 0.70 + speakP2 * 0.30 + voiceLvl * 0.40 : state === 'processing' ? 0.65 + fastP * 0.35 : 0.65 + pulse * 0.35 + lvl * 0.55);
      });

      // Stars
      stars.rotation.y += 0.0003 * (state === 'sleep' ? 0.2 : state === 'speaking' ? 2.5 : state === 'processing' ? 2.0 : 1.0);
      starMat.opacity = state === 'sleep' ? 0.15 : state === 'speaking' ? 0.38 + speakP * 0.14 : state === 'processing' ? 0.42 + fastP * 0.10 : 0.40 + pulse * 0.08;

      // Lights — Phase-3 scaled for physically-correct mode (intensities are
      // ~2.5× the legacy values to match perceived brightness under ACES).
      // 2026-07-09 — speaking's voiceLvl weight trimmed (5.0→3.0): loud TTS
      // syllables were kicking intensity up hard enough, on top of the old
      // fast oscillator, to blow the warm gold light out to white. The slower
      // speakP cadence above plus this lower ceiling keeps it a rich amber
      // glow that swells with the voice instead of flashing.
      // 2026-07-09 (Voice Mode V2 foundation pass) — the LISTENING branch had
      // the same clip-to-white exposure the comment above already diagnosed
      // and fixed for SPEAKING, just never applied here: at full mic level
      // (lvl→1.0, common right as the orb wakes and hears the user's own
      // voice) this hit kl≈21.5 / fl≈11.5, well past SPEAKING's tuned ceiling
      // of kl≈12.5 / fl≈10.0 — almost certainly the reported "orb goes white"
      // flash. Trimmed the lvl coefficients so LISTENING's peak lands in the
      // same range instead of nearly double it. INTERRUPTED gets its own low,
      // non-pulsing value so the barge-in "cut off" dip reads as a dip, not
      // an idle glow.
      kl.intensity = state === 'sleep' ? 2.0 : state === 'speaking' ? 6.0 + speakP * 3.5 + voiceLvl * 3.0 : state === 'processing' ? 5.5 + fastP * 5.0 : state === 'interrupted' ? 3.0 : 6.5 + lvl * 5.0 + pulse * 2.0;
      fl.intensity = state === 'sleep' ? 1.0 : state === 'speaking' ? 4.0 + speakP2 * 3.0 + voiceLvl * 3.0 : state === 'processing' ? 3.5 + fastP * 2.5 : state === 'interrupted' ? 2.0 : 4.5 + lvl * 4.0;

      renderer.render(scene, camera);
    }
    tick();

    function resize() {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    const ro = new ResizeObserver(resize);
    ro.observe(canvas.parentElement || canvas);
    resize();

    function setTheme(primary, secondary) {
      const theme = (primary && typeof primary === 'object')
        ? primary
        : { primary, secondary };
      const p = Number.isFinite(theme.primary) ? theme.primary : COL.p;
      const s = Number.isFinite(theme.secondary) ? theme.secondary : COL.s;
      const body = Number.isFinite(theme.body) ? theme.body : 0x040e1e;
      const emissive = Number.isFinite(theme.emissive) ? theme.emissive : 0x061a30;
      const sky = Number.isFinite(theme.hemiSky) ? theme.hemiSky : 0x9bd2ff;
      const ground = Number.isFinite(theme.hemiGround) ? theme.hemiGround : 0x0a0e1e;
      const rim = Number.isFinite(theme.rim) ? theme.rim : COL.w;
      // The bright inner core defaulted to pure white (COL.w), which made the
      // whole orb read as "white" even under a warm gold corona. Let the theme
      // retint it to a warm hot-white so the centre matches the champagne look.
      const coreCol = Number.isFinite(theme.core) ? theme.core : COL.w;
      bodyMat.color.setHex(body);
      if (bodyMat.emissive) bodyMat.emissive.setHex(emissive);
      if (bodyMat.sheenColor) bodyMat.sheenColor.setHex(p);
      wireMat.color.setHex(p); outerMat.color.setHex(p);
      haloMat.color.setHex(p);
      coreMat.color.setHex(coreCol);
      rings.forEach((ring, i) => ring.material.color.setHex(i === 1 ? s : p));
      particles.forEach((particle, i) => particle.material.color.setHex(i % 2 ? p : s));
      starMat.color.setHex(p); hemi.color.setHex(sky); hemi.groundColor.setHex(ground);
      kl.color.setHex(p); fl.color.setHex(s); rl.color.setHex(rim);
      dotGroup.children.forEach(d => d.material.color.setHex(p));
    }

    // Phase-3 Runtime-Repair (2026-05-16): register the arc reactor with the
    // generic 3D control registry so hand-control / contactless / voice
    // gestures can rotate / zoom it just like any other 3D model.
    let _registryId = '';
    try {
      const reg = window.SyrixModel3DRegistry;
      if (reg && reg.register) {
        _registryId = 'arc_reactor:main';
        let _particlesOn = true;
        reg.register(_registryId, {
          name: 'Arc Reactor',
          type: 'arc_reactor',
          enabled_controls: ['rotate', 'zoom', 'reset', 'switch', 'toggle_particles', 'select'],
          apply: (action, value) => {
            const v = value || {};
            if (action === 'rotate') {
              const deg = (v.degrees != null ? v.degrees : 15);
              const axis = (v.axis || 'Y');
              const rad = deg * Math.PI / 180;
              if (axis === 'X') root.rotation.x += rad;
              else if (axis === 'Z') root.rotation.z += rad;
              else root.rotation.y += rad;
              return true;
            }
            if (action === 'zoom') {
              const delta = (v.delta != null ? v.delta : 0);
              const f = 1.0 + delta;
              const next = root.scale.x * f;
              const clamped = Math.max(0.5, Math.min(2.5, next));
              root.scale.set(clamped, clamped, clamped);
              return true;
            }
            if (action === 'toggle_particles') {
              _particlesOn = !_particlesOn;
              outerMat.opacity = _particlesOn ? 0.055 : 0;
              haloMat.opacity = _particlesOn ? 0.22 : 0;
              return true;
            }
            if (action === 'select') {
              // Brief halo pulse.
              const orig = haloMat.opacity;
              haloMat.opacity = Math.min(1.0, orig + 0.6);
              setTimeout(() => { try { haloMat.opacity = orig; } catch (_) {} }, 220);
              return true;
            }
            return false;
          },
          getTransform: () => ({
            rotate: { x: root.rotation.x, y: root.rotation.y, z: root.rotation.z },
            scale: root.scale.x,
            position: { x: root.position.x, y: root.position.y, z: root.position.z },
          }),
          resetTransform: () => {
            try {
              root.rotation.set(0, 0, 0);
              root.scale.set(1, 1, 1);
              return true;
            } catch (_) { return false; }
          },
        });
        try { console.log('[3D_MODEL] created type=arc_reactor id=' + _registryId); } catch (_) {}
      }
    } catch (_) { /* swallow */ }

    return {
      stop: () => {
        stopped = true;
        try { cancelAnimationFrame(raf); } catch (_) {}
        try { window.removeEventListener('mousemove', onMouseMove); } catch (_) {}
        try { canvas.removeEventListener('mousedown', onMouseDown); } catch (_) {}
        try { window.removeEventListener('mouseup', onMouseUp); } catch (_) {}
        ro.disconnect();
        try {
          if (_registryId && window.SyrixModel3DRegistry && window.SyrixModel3DRegistry.unregister) {
            window.SyrixModel3DRegistry.unregister(_registryId);
          }
        } catch (_) {}
        // 2026-07-09 — createSyrixBall() runs fresh on every Voice Mode overlay
        // open (mount effect) and this stop() on every close, but the renderer
        // and its GPU resources were never released. Chromium caps live WebGL
        // contexts (~16); a few open/close cycles exhausted the pool and new
        // canvases (this orb next time, and unrelated HUD 3D surfaces) started
        // rendering solid white/blank instead of failing loudly. Dispose
        // everything and force-lose the context so the slot is actually freed.
        try {
          scene.traverse(obj => {
            try { if (obj.geometry) obj.geometry.dispose(); } catch (_) {}
            try {
              const mats = Array.isArray(obj.material) ? obj.material : (obj.material ? [obj.material] : []);
              mats.forEach(m => { try { m.dispose(); } catch (_) {} });
            } catch (_) {}
          });
        } catch (_) {}
        try { renderer.dispose(); } catch (_) {}
        try {
          const ext = renderer.getContext && renderer.getContext().getExtension('WEBGL_lose_context');
          if (ext) ext.loseContext();
        } catch (_) {}
      },
      setState: s => {
        const next = String(s || 'idle');
        if (next !== state) state = next;
      },
      setMicLevel: l => { micTarget = Math.max(0, Math.min(1, Number(l) || 0)); },
      setVoiceLevel: l => { voiceTarget = Math.max(0, Math.min(1, Number(l) || 0)); },
      setActive: v => { active = !!v; },
      setCalm: v => { calm = !!v; },
      setTheme, resize,
    };
  }

  window.SyrixPrimaryBall = { createSyrixBall };
})();
