/* SYRIX BOOT — the mark assembles
   Light blooms → the four quarters of the mark glide in out of focus and
   lock together → a soft flash, a ring of light and one sheen mark the
   lock → the wordmark settles letter by letter → a hairline fills until the
   backend is online → the scene eases forward and dissolves into the HUD.
   Exports: SyrixBootCinematic({ onReveal, onDone }) */

let _v6Played = false;

/* ── Boot sound v7 (2026-07-04) — calm and physical, not sci-fi. A soft
   low "power settle" swell under a gentle air sweep, then a warm two-note
   confirmation on logo lock with a quiet tactile tap. Shorter room, fewer
   partials, lower level: it should feel like a device waking up in the
   same room, not a movie trailer. ───────────────────────────────────── */
function _v6PlaySound() {
  try {
    const a = new (window.AudioContext || window.webkitAudioContext)();
    const master = a.createGain();
    master.gain.value = 0.14;
    master.connect(a.destination);

    /* Small warm room — short tail so nothing rings on. */
    const irLen = Math.floor(a.sampleRate * 1.1);
    const ir = a.createBuffer(2, irLen, a.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = ir.getChannelData(ch);
      for (let i = 0; i < irLen; i++)
        d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / irLen, 2.4);
    }
    const rev = a.createConvolver(); rev.buffer = ir;
    const rg = a.createGain(); rg.gain.value = 0.14;
    rev.connect(rg); rg.connect(master);

    /* Low power-settle swell — a sub sine easing in and away (0 → 0.9s). */
    const hum = a.createOscillator(); hum.type = 'sine';
    hum.frequency.setValueAtTime(52, a.currentTime);
    hum.frequency.linearRampToValueAtTime(64, a.currentTime + 0.9);
    const hg = a.createGain();
    hg.gain.setValueAtTime(0, a.currentTime);
    hg.gain.linearRampToValueAtTime(0.16, a.currentTime + 0.35);
    hg.gain.exponentialRampToValueAtTime(1e-4, a.currentTime + 1.0);
    hum.connect(hg); hg.connect(master);
    hum.start(a.currentTime); hum.stop(a.currentTime + 1.05);

    /* Gentle air sweep — quieter, narrower, more like a breath than a whoosh. */
    const nLen = Math.floor(a.sampleRate * 0.6);
    const nBuf = a.createBuffer(1, nLen, a.sampleRate);
    const nData = nBuf.getChannelData(0);
    for (let i = 0; i < nLen; i++) nData[i] = Math.random() * 2 - 1;
    const ns = a.createBufferSource(); ns.buffer = nBuf;
    const bp = a.createBiquadFilter();
    bp.type = 'bandpass';
    bp.frequency.setValueAtTime(220, a.currentTime);
    bp.frequency.exponentialRampToValueAtTime(2400, a.currentTime + 0.6);
    bp.Q.value = 1.1;
    const ng = a.createGain();
    ng.gain.setValueAtTime(0, a.currentTime);
    ng.gain.linearRampToValueAtTime(0.07, a.currentTime + 0.12);
    ng.gain.linearRampToValueAtTime(0, a.currentTime + 0.6);
    ns.connect(bp); bp.connect(ng); ng.connect(master); ng.connect(rev);
    ns.start(a.currentTime);

    /* Warm two-note confirmation on lock — C#5 then G#5, slightly detuned
       pairs for body, short decay. Reads "ready", not "spaceship". */
    function tone(freq, amp, delay, decay) {
      [0, 3].forEach((cents, k) => {
        const o = a.createOscillator(); o.type = 'sine';
        o.frequency.value = freq * Math.pow(2, cents / 1200);
        const g = a.createGain();
        g.gain.setValueAtTime(0, a.currentTime + delay);
        g.gain.linearRampToValueAtTime(amp * (k ? 0.4 : 1), a.currentTime + delay + 0.012);
        g.gain.exponentialRampToValueAtTime(1e-4, a.currentTime + delay + decay);
        o.connect(g); g.connect(master); g.connect(rev);
        o.start(a.currentTime + delay);
        o.stop(a.currentTime + delay + decay + 0.05);
      });
    }
    tone(554, 0.10, 0.62, 1.1);   /* C#5 — arrival   */
    tone(831, 0.06, 0.74, 1.3);   /* G#5 — settled   */

    /* Quiet tactile tap on lock — felt more than heard. */
    const click = a.createOscillator(); click.type = 'sine'; click.frequency.value = 84;
    const cg = a.createGain();
    cg.gain.setValueAtTime(0.12, a.currentTime + 0.61);
    cg.gain.exponentialRampToValueAtTime(1e-4, a.currentTime + 0.72);
    click.connect(cg); cg.connect(master);
    click.start(a.currentTime + 0.61); click.stop(a.currentTime + 0.73);
  } catch (_) {}
}

/* The hand-off: the assembled mark travels, in one move, into the SYRIX mark
   above the composer on the launch surface, while everything else in the
   start-up scene fades away around it. The real mark is held invisible until
   the flying one lands on it, so the swap cannot be seen. Returns false when
   there is no launch mark to land on (a chat is already open) — the caller
   then dissolves instead. */
function morphIntoHome(wrap) {
  try {
    if (!wrap) return false;
    const target = document.querySelector('.eg-home-mark img') || document.querySelector('.eg-home-mark');
    const mark = wrap.querySelector('.pbc6-mark');
    if (!target || !mark) return false;
    const a = mark.getBoundingClientRect();
    const b = target.getBoundingClientRect();
    if (!(a.width > 0 && b.width > 0)) return false;
    const dx = (b.left + b.width / 2) - (a.left + a.width / 2);
    const dy = (b.top + b.height / 2) - (a.top + a.height / 2);
    const scale = b.width / a.width;
    document.body.classList.add('syrix-boot-morph');
    // The settle animation holds a transform with fill-mode both; clear it so
    // the transition starts from where the mark actually is.
    mark.style.animation = 'none';
    mark.style.transform = 'none';
    void mark.offsetWidth;
    mark.style.transition = 'transform 1s cubic-bezier(0.65, 0, 0.2, 1)';
    mark.style.transform = 'translate(' + dx + 'px, ' + dy + 'px) scale(' + scale + ')';
    return true;
  } catch (_) {
    return false;
  }
}

function SyrixBootCinematic({ onReveal, onDone }) {
  const wrapRef = React.useRef(null);
  const fillRef = React.useRef(null);

  /* phases: 0=dark, 1=bloom+assembly, 2=lock+wordmark, 3=tagline+reveal, 4=progress */
  const [phase,   setPhase]   = React.useState(0);
  const [leaving, setLeaving] = React.useState('');
  const [wsReady, setWsReady] = React.useState(false);
  const [stage,   setStage]   = React.useState('Starting');

  const doneFiredRef   = React.useRef(false);
  const revealFiredRef = React.useRef(false);
  const wsReadyRef     = React.useRef(false);

  React.useEffect(() => { wsReadyRef.current = wsReady; }, [wsReady]);

  /* ── Authenticated backend readiness ────────────────────────────── */
  React.useEffect(() => {
    let dead = false;
    let detach = null;
    let retryTimer = null;
    function attach() {
      if (dead) return;
      try {
        const bridge = window.SyrixBridge;
        if (bridge && typeof bridge.subscribe === 'function') {
          detach = bridge.subscribe((snapshot) => {
            if (dead || !snapshot || snapshot.status !== 'online') return;
            wsReadyRef.current = true;
            setWsReady(true);
          });
          return;
        }
      } catch (_) {}
      retryTimer = setTimeout(attach, 250);
    }
    attach();
    return () => {
      dead = true;
      if (retryTimer) clearTimeout(retryTimer);
      try { if (typeof detach === 'function') detach(); } catch (_) {}
    };
  }, []);

  /* ── Safety fallback: 20s max wait then unblock anyway ─────────── */
  React.useEffect(() => {
    const t = setTimeout(() => {
      wsReadyRef.current = true;
      setWsReady(true);
    }, 20000);
    return () => clearTimeout(t);
  }, []);

  /* ── Reduced-motion fast path ────────────────────────────────────── */
  React.useEffect(() => {
    if (!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    setPhase(4);
    setTimeout(() => { try { if (onReveal) onReveal(); } catch (_) {} }, 40);
    setTimeout(() => { try { if (onDone) onDone(); } catch (_) {} }, 150);
  }, []);

  /* ── Main sequence ───────────────────────────────────────────────── */
  React.useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    const ts = [];
    const t = (ms, fn) => ts.push(setTimeout(fn, ms));
    t(60, () => {
      setPhase(1);
      if (!_v6Played) { _v6Played = true; _v6PlaySound(); }
    });
    t(1080, () => setPhase(2));
    t(1700, () => {
      setPhase(3);
      if (!revealFiredRef.current) {
        revealFiredRef.current = true;
        try { if (onReveal) onReveal(); } catch (_) {}
      }
    });
    t(2100, () => setPhase(4));
    return () => ts.forEach(clearTimeout);
  }, []);

  /* ── Progress: eased toward a ceiling until the backend is online,
     then a smooth run to the end. Written straight to the fill's
     transform so a frame never waits on a React render. ─────────────── */
  React.useEffect(() => {
    if (phase < 4) return undefined;
    const CEILING = 0.84, CEIL_MS = 3600, FINISH_MS = 650;
    const start = performance.now();
    let finishFrom = 0, finishAt = 0, shown = 0, raf = 0, lastStage = '';
    const ease = (x) => 1 - Math.pow(1 - x, 3);
    function tick(now) {
      let target = CEILING * ease(Math.min(1, (now - start) / CEIL_MS));
      if (wsReadyRef.current) {
        if (!finishAt) { finishAt = now; finishFrom = shown; }
        const k = Math.min(1, (now - finishAt) / FINISH_MS);
        target = finishFrom + (1 - finishFrom) * ease(k);
      }
      shown += (target - shown) * 0.18;
      if (target >= 1 && 1 - shown < 0.002) shown = 1;
      if (fillRef.current) fillRef.current.style.transform = 'scaleX(' + shown.toFixed(4) + ')';
      const next = wsReadyRef.current ? 'Ready' : shown < 0.3 ? 'Starting' : shown < 0.62 ? 'Loading' : 'Connecting';
      if (next !== lastStage) { lastStage = next; setStage(next); }
      if (shown >= 1) {
        setTimeout(() => {
          if (doneFiredRef.current) return;
          doneFiredRef.current = true;
          const finish = () => { try { if (onDone) onDone(); } catch (_) {} };
          if (morphIntoHome(wrapRef.current)) {
            setLeaving('morph');
            setTimeout(() => {
              try { document.body.classList.remove('syrix-boot-morph'); } catch (_) {}
              finish();
            }, 1080);
          } else {
            setLeaving('dissolve');
            setTimeout(finish, 820);
          }
        }, 260);
        return;
      }
      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [phase]); /* wsReady intentionally omitted — read live via wsReadyRef */

  const cls = 'pbc6-wrap' + (phase >= 1 ? ' p1' : '') + (phase >= 2 ? ' p2' : '')
    + (phase >= 3 ? ' p3' : '') + (phase >= 4 ? ' p4' : '')
    + (leaving === 'dissolve' ? ' is-leaving' : '') + (leaving === 'morph' ? ' is-morphing' : '');

  return (
    <div ref={wrapRef} className={cls}>
      <div className="pbc6-bg" aria-hidden="true">
        <div className="pbc6-bloom" />
        <div className="pbc6-drift" />
      </div>

      <div className="pbc6-stage">
        <div className="pbc6-logo">
          <div className="pbc6-logo-glow" aria-hidden="true" />
          <div className="pbc6-ring" aria-hidden="true" />
          <div className="pbc6-mark">
            {['tl', 'tr', 'bl', 'br'].map(q => (
              <div key={q} className={'pbc6-quad pbc6-quad--' + q}>
                <img
                  src="../assets/icons/syrix_app_logo.png"
                  className="pbc6-quad-img"
                  alt=""
                  onError={e => { e.currentTarget.style.opacity = '0'; }}
                />
              </div>
            ))}
          </div>
          <div className="pbc6-lock-flash" aria-hidden="true" />
          <div className="pbc6-logo-sheen" aria-hidden="true" />
        </div>

        <div className="pbc6-wordmark" aria-label="SYRIX">
          {'SYRIX'.split('').map((ch, i) => (
            <span key={i} className="pbc6-letter" style={{ transitionDelay: (i * 70) + 'ms' }} aria-hidden="true">{ch}</span>
          ))}
        </div>

        <p className="pbc6-sub">Built by Kabirr Kohli</p>
      </div>

      <div className="pbc6-foot">
        <div className="pbc6-bar-track">
          <div className="pbc6-bar-fill" ref={fillRef} style={{ transform: 'scaleX(0)' }} />
        </div>
        <div className={'pbc6-stat' + (wsReady ? ' is-online' : '')} aria-live="polite">
          <span className="pbc6-stat-word" key={stage}>{stage}</span>
        </div>
      </div>
    </div>
  );
}
