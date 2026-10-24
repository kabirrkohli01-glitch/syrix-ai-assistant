/* SYRIX 3D SANDBOX (Phase-2D, 2026-05-15) ───────────────────────────────
 *
 * Lightweight Three.js HUD overlay that lets the user create / manipulate
 * primitive 3D objects without launching Blender. Sits ABOVE the cyber grid
 * and the vision workspace; receives sandbox requests from Python through
 * `snap.syrix_sandbox_request` and from voice / hand-control gestures on the
 * renderer side.
 *
 * Primitives supported (mapped from the Python parse_sandbox_subject keys):
 *
 *   particles    → animated cyan particle field (1,500 points)
 *   arc_reactor  → low-poly Iron-Man-style arc reactor preview (rings + glow)
 *   glow_sphere  → emissive sphere
 *   wire_cube    → cube rendered as wireframe
 *   neon_ring    → torus with bloom-ish emission
 *   torus        → standard torus
 *   torus_knot   → torus-knot
 *   pyramid      → 4-sided pyramid
 *
 * Actions:
 *   rotate / bigger / smaller / move(direction) / explode / clear /
 *   export_blender (delegated to bridge.sandboxExportBlender)
 *
 * Public:
 *   window.SyrixSandbox3D = React component (mount once, near app root)
 *   window.SyrixSandbox3DApi = { add, clear, rotate, scale, move, explode,
 *                                count, exportLast }
 */
(function () {
  'use strict';
  if (window.SyrixSandbox3D) return;
  const { useEffect, useRef, useState, useCallback } = React;

  function _bridge() { return window.SyrixBridge || null; }
  function _three()  { return window.THREE || null; }

  // Internal mutable scene we keep on the singleton so multiple mounts share state.
  const _singleton = {
    renderer: null,
    scene:    null,
    camera:   null,
    container: null,
    objects:   [],  // {kind, mesh, exportTag}
    lastSeqApplied: 0,
    raf: 0,
    enabled: false,
  };

  function _ensureMaterial(THREE, kind) {
    switch (kind) {
      case 'glow_sphere':
        return new THREE.MeshStandardMaterial({
          color: 0x66d6ff, emissive: 0x33b5ff, emissiveIntensity: 1.5,
          roughness: 0.22, metalness: 0.15,
        });
      case 'wire_cube':
        return new THREE.MeshBasicMaterial({
          color: 0x22d3ee, wireframe: true,
        });
      case 'neon_ring':
        return new THREE.MeshStandardMaterial({
          color: 0xff6ec7, emissive: 0xff3aa0, emissiveIntensity: 2.0,
          roughness: 0.25, metalness: 0.4,
        });
      case 'arc_reactor':
        return new THREE.MeshStandardMaterial({
          color: 0xa5e7ff, emissive: 0x55cfff, emissiveIntensity: 2.2,
          roughness: 0.2, metalness: 0.5,
        });
      default:
        return new THREE.MeshStandardMaterial({
          color: 0xc8d0e8, roughness: 0.4, metalness: 0.3,
        });
    }
  }

  function _buildPrimitive(THREE, kind) {
    let mesh = null;
    if (kind === 'particles') {
      // Spherical living core. Particles are distributed evenly on a sphere
      // shell via the Fibonacci / golden-spiral sequence (no clumping at the
      // poles like uniform-random spherical sampling), then jittered slightly
      // inward so the field has depth instead of being a hollow shell. Each
      // particle carries its rest position and a per-particle phase used by
      // the animation loop for organic breathing + reactive expansion.
      const geom = new THREE.BufferGeometry();
      // Performance cap — keeps the field at <2k vertices on integrated GPUs.
      const N = 1200;
      const positions = new Float32Array(N * 3);
      const restPos   = new Float32Array(N * 3);
      const phases    = new Float32Array(N);
      const radii     = new Float32Array(N);
      const golden = Math.PI * (3 - Math.sqrt(5));
      const baseRadius = 1.25;
      for (let i = 0; i < N; i++) {
        const y = 1 - (i / (N - 1)) * 2;            // -1 .. 1
        const r = Math.sqrt(Math.max(0, 1 - y * y));
        const theta = golden * i;
        const jitter = 0.78 + Math.random() * 0.32; // 0.78..1.10 inward shell
        const rad = baseRadius * jitter;
        const x = Math.cos(theta) * r * rad;
        const z = Math.sin(theta) * r * rad;
        const yy = y * rad;
        positions[i * 3 + 0] = x;
        positions[i * 3 + 1] = yy;
        positions[i * 3 + 2] = z;
        restPos[i * 3 + 0]   = x;
        restPos[i * 3 + 1]   = yy;
        restPos[i * 3 + 2]   = z;
        phases[i] = Math.random() * Math.PI * 2;
        radii[i]  = rad;
      }
      geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      const mat = new THREE.PointsMaterial({
        color: 0x88e3ff, size: 0.05, transparent: true, opacity: 0.88, sizeAttenuation: true,
        depthWrite: false, blending: THREE.AdditiveBlending,
      });
      mesh = new THREE.Points(geom, mat);
      mesh.userData = {
        rest: restPos,
        phases: phases,
        radii: radii,
        count: N,
        breathAmp: 0.06,
        breathSpeed: 1.4,
        expand: 1.0,            // user-driven uniform scale (1.0 = rest)
        targetExpand: 1.0,      // smoothed target for ease-in/out
        offset: [0, 0, 0],      // translation from hand drag / mouse drag
        targetOffset: [0, 0, 0],
        grabStrength: 0.0,      // 1.0 = fully grabbed; suppresses breathing
      };
    } else if (kind === 'arc_reactor') {
      const group = new THREE.Group();
      const core = new THREE.Mesh(new THREE.SphereGeometry(0.45, 32, 24), _ensureMaterial(THREE, 'arc_reactor'));
      group.add(core);
      const outer = new THREE.Mesh(
        new THREE.TorusGeometry(1.1, 0.12, 16, 64),
        new THREE.MeshStandardMaterial({ color: 0xcdd6e8, metalness: 1.0, roughness: 0.25 }),
      );
      outer.rotation.x = Math.PI / 2;
      group.add(outer);
      const inner = new THREE.Mesh(
        new THREE.TorusGeometry(0.8, 0.08, 16, 48),
        new THREE.MeshStandardMaterial({ color: 0xcdd6e8, metalness: 1.0, roughness: 0.3 }),
      );
      inner.rotation.x = Math.PI / 2;
      group.add(inner);
      // 10 small glow nodes around the rim.
      for (let i = 0; i < 10; i++) {
        const a = (i / 10) * Math.PI * 2;
        const node = new THREE.Mesh(
          new THREE.SphereGeometry(0.08, 16, 12),
          new THREE.MeshStandardMaterial({ color: 0x88e6ff, emissive: 0x55ddff, emissiveIntensity: 3.0 }),
        );
        node.position.set(Math.cos(a) * 0.72, 0.02, Math.sin(a) * 0.72);
        group.add(node);
      }
      mesh = group;
    } else if (kind === 'glow_sphere') {
      mesh = new THREE.Mesh(new THREE.SphereGeometry(0.55, 48, 32), _ensureMaterial(THREE, 'glow_sphere'));
    } else if (kind === 'wire_cube') {
      mesh = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.9, 0.9), _ensureMaterial(THREE, 'wire_cube'));
    } else if (kind === 'neon_ring') {
      mesh = new THREE.Mesh(new THREE.TorusGeometry(0.7, 0.14, 24, 96), _ensureMaterial(THREE, 'neon_ring'));
    } else if (kind === 'torus') {
      mesh = new THREE.Mesh(new THREE.TorusGeometry(0.65, 0.18, 24, 64), _ensureMaterial(THREE, 'torus'));
    } else if (kind === 'torus_knot') {
      mesh = new THREE.Mesh(new THREE.TorusKnotGeometry(0.55, 0.15, 96, 12), _ensureMaterial(THREE, 'torus_knot'));
    } else if (kind === 'pyramid') {
      mesh = new THREE.Mesh(new THREE.ConeGeometry(0.7, 1.0, 4), _ensureMaterial(THREE, 'pyramid'));
    } else if (window.SyrixModels3D && window.SyrixModels3D.has && window.SyrixModels3D.has(kind)) {
      // Phase-4 extended library — Earth, planets, atoms, molecules,
      // organs, animals, cars, engines, neural nets, etc. The model
      // library returns its own animation hook in userData when needed.
      mesh = window.SyrixModels3D.build(kind, THREE);
      if (!mesh) {
        // Library said yes but build threw — fall back gracefully.
        mesh = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.7, 0.7), _ensureMaterial(THREE, 'unknown'));
      }
    } else {
      // Unknown subject — fall back to a small cube so something appears.
      mesh = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.7, 0.7), _ensureMaterial(THREE, 'unknown'));
    }
    if (!mesh) return null;
    // Stagger positions so multiple objects don't overlap.
    mesh.position.set(
      (Math.random() - 0.5) * 1.6,
      (Math.random() - 0.5) * 0.6,
      (Math.random() - 0.5) * 1.4,
    );
    return mesh;
  }

  /* ── Scene init ───────────────────────────────────────────────────── */

  function _initScene(container) {
    const THREE = _three();
    if (!THREE) return false;
    if (_singleton.renderer && _singleton.container === container) return true;
    // Tear down a previous renderer if we're re-mounting.
    if (_singleton.renderer) {
      try { _singleton.renderer.dispose(); } catch (_) {}
      if (_singleton.container && _singleton.renderer.domElement) {
        try { _singleton.container.removeChild(_singleton.renderer.domElement); } catch (_) {}
      }
    }
    const scene = new THREE.Scene();
    scene.background = null;  // transparent over HUD grid

    const w = container.clientWidth  || 600;
    const h = container.clientHeight || 360;
    const camera = new THREE.PerspectiveCamera(48, w / h, 0.1, 100);
    camera.position.set(0, 1.1, 3.4);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(window.devicePixelRatio || 1);
    renderer.setSize(w, h);
    renderer.domElement.style.position = 'absolute';
    renderer.domElement.style.inset = '0';
    renderer.domElement.style.pointerEvents = 'none';   // overlay; HUD owns input
    container.appendChild(renderer.domElement);

    // Lights — cool key + magenta rim for a HUD vibe.
    const key = new THREE.DirectionalLight(0xb8e0ff, 1.6);
    key.position.set(2.0, 2.4, 2.0);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0xff7dd1, 0.7);
    rim.position.set(-2.5, 0.5, -2.0);
    scene.add(rim);
    scene.add(new THREE.AmbientLight(0x66798f, 0.4));

    _singleton.renderer = renderer;
    _singleton.scene = scene;
    _singleton.camera = camera;
    _singleton.container = container;
    return true;
  }

  function _animateOnce() {
    if (!_singleton.renderer) return;
    // Idle rotation for built-in primitives + extended library hooks.
    const now = performance.now();
    const dt = (_singleton._lastFrame ? Math.min(0.1, (now - _singleton._lastFrame) / 1000) : 0.016);
    _singleton._lastFrame = now;
    for (const obj of _singleton.objects) {
      if (!obj.mesh) continue;
      // Extended-library models drive their own animation hook.
      if (window.SyrixModels3D && window.SyrixModels3D.has && window.SyrixModels3D.has(obj.kind)) {
        try { window.SyrixModels3D.animate(obj.kind, obj.mesh, dt); } catch (_) {}
        continue;
      }
      if (obj.kind === 'particles') {
        obj.mesh.rotation.y += 0.0015;
        // Breathing + expand/contract + drag offset for the living orb.
        const ud = obj.mesh.userData || null;
        const geom = obj.mesh.geometry;
        const attr = geom && geom.attributes && geom.attributes.position;
        if (ud && ud.rest && attr) {
          // Ease the targets so user controls feel organic.
          ud.expand += (ud.targetExpand - ud.expand) * 0.12;
          ud.offset[0] += (ud.targetOffset[0] - ud.offset[0]) * 0.18;
          ud.offset[1] += (ud.targetOffset[1] - ud.offset[1]) * 0.18;
          ud.offset[2] += (ud.targetOffset[2] - ud.offset[2]) * 0.18;
          const t = now * 0.001 * (ud.breathSpeed || 1.4);
          const amp = (ud.breathAmp || 0.06) * (1.0 - Math.min(1.0, ud.grabStrength || 0));
          const ex = ud.expand;
          const ox = ud.offset[0], oy = ud.offset[1], oz = ud.offset[2];
          const rest = ud.rest;
          const phases = ud.phases;
          const arr = attr.array;
          const cnt = ud.count;
          for (let i = 0; i < cnt; i++) {
            const i3 = i * 3;
            const ph = phases[i];
            const wave = Math.sin(t + ph);
            // Each particle moves slightly along its rest-direction
            // (= outward from origin) for the breathing motion, then we
            // apply the user-driven expand and drag offset.
            const rx = rest[i3 + 0], ry = rest[i3 + 1], rz = rest[i3 + 2];
            const k = ex + amp * wave;
            arr[i3 + 0] = rx * k + ox;
            arr[i3 + 1] = ry * k + oy;
            arr[i3 + 2] = rz * k + oz;
          }
          attr.needsUpdate = true;
        }
      } else if (obj.kind === 'arc_reactor' || obj.kind === 'neon_ring' || obj.kind === 'torus_knot') {
        obj.mesh.rotation.y += 0.004;
      }
    }
    _singleton.renderer.render(_singleton.scene, _singleton.camera);
  }

  function _startLoop() {
    if (!_singleton.renderer) return;
    cancelAnimationFrame(_singleton.raf);
    function tick() {
      _singleton.raf = requestAnimationFrame(tick);
      // 2026-05-19 optimization: skip three.js render entirely when the HUD
      // window is hidden — saves significant GPU when the sandbox has
      // particles or model meshes spinning in the background.
      if (typeof document !== 'undefined' && document.hidden) return;
      _animateOnce();
    }
    tick();
  }

  function _stopLoop() { cancelAnimationFrame(_singleton.raf); _singleton.raf = 0; }

  /* ── API ──────────────────────────────────────────────────────────── */

  function add(kind) {
    const THREE = _three();
    if (!THREE || !_singleton.scene) return null;
    const mesh = _buildPrimitive(THREE, kind);
    if (!mesh) return null;
    _singleton.scene.add(mesh);
    const entry = { kind: kind, mesh, exportTag: kind };
    _singleton.objects.push(entry);
    // Phase-3 Runtime-Repair (2026-05-16): register every sandbox object with
    // the generic 3D registry so hand-control / voice can rotate / zoom / pan
    // any object, not only the most-recently-added one. Each object becomes
    // independently selectable via voice or HUD UI.
    try {
      const reg = window.SyrixModel3DRegistry;
      if (reg && reg.register) {
        const id = `sandbox:${kind}:${_singleton.objects.length}`;
        entry.registryId = id;
        const isParticles = (kind === 'particles');
        // Phase-4: prefer the extended library's human-readable name if it
        // owns this kind, so the registry surface shows "Earth" rather than
        // "earth" and the educational summary can be inspected via the
        // model picker UI.
        let displayName = kind.replace(/_/g, ' ');
        try {
          if (window.SyrixModels3D && window.SyrixModels3D.has && window.SyrixModels3D.has(kind)) {
            const meta = window.SyrixModels3D.describe(kind);
            if (meta && meta.name) displayName = meta.name;
            entry.meta = meta || null;
          }
        } catch (_) {}
        reg.register(id, {
          name: displayName,
          type: kind,
          enabled_controls: isParticles
            ? ['rotate', 'reset', 'switch', 'toggle_particles']
            : ['rotate', 'zoom', 'pan', 'select', 'reset', 'switch'],
          apply: (action, value) => {
            const v = value || {};
            if (action === 'rotate') {
              const deg = (v.degrees != null ? v.degrees : 25);
              const axis = (v.axis || 'Y');
              const rad = deg * Math.PI / 180;
              if (axis === 'X') entry.mesh.rotation.x += rad;
              else if (axis === 'Z') entry.mesh.rotation.z += rad;
              else entry.mesh.rotation.y += rad;
              return true;
            }
            if (action === 'zoom') {
              const delta = (v.delta != null ? v.delta : 0);
              const factor = 1.0 + delta;
              entry.mesh.scale.set(
                Math.max(0.1, entry.mesh.scale.x * factor),
                Math.max(0.1, entry.mesh.scale.y * factor),
                Math.max(0.1, entry.mesh.scale.z * factor));
              return true;
            }
            if (action === 'pan') {
              entry.mesh.position.x += (v.dx || 0) * 1.4;
              entry.mesh.position.y -= (v.dy || 0) * 1.4;
              return true;
            }
            if (action === 'select') {
              // Visual select pulse — slight emissive boost if material supports it.
              try {
                if (entry.mesh.material && entry.mesh.material.emissiveIntensity != null) {
                  const orig = entry.mesh.material.emissiveIntensity;
                  entry.mesh.material.emissiveIntensity = orig * 1.6;
                  setTimeout(() => {
                    try { entry.mesh.material.emissiveIntensity = orig; } catch (_) {}
                  }, 220);
                }
              } catch (_) {}
              return true;
            }
            return false;
          },
          getTransform: () => ({
            rotate: {
              x: entry.mesh.rotation.x,
              y: entry.mesh.rotation.y,
              z: entry.mesh.rotation.z,
            },
            scale: entry.mesh.scale.x,
            position: {
              x: entry.mesh.position.x,
              y: entry.mesh.position.y,
              z: entry.mesh.position.z,
            },
          }),
          resetTransform: () => {
            try {
              entry.mesh.rotation.set(0, 0, 0);
              entry.mesh.scale.set(1, 1, 1);
              entry.mesh.position.set(
                (Math.random() - 0.5) * 1.6,
                (Math.random() - 0.5) * 0.6,
                (Math.random() - 0.5) * 1.4);
              return true;
            } catch (_) { return false; }
          },
        });
        try {
          console.log(`[3D_MODEL] created type=${kind} id=${id}`);
        } catch (_) {}
      }
    } catch (_) { /* swallow */ }
    return entry;
  }

  function clearAll() {
    if (!_singleton.scene) return;
    for (const obj of _singleton.objects) {
      try { _singleton.scene.remove(obj.mesh); } catch (_) {}
      try {
        obj.mesh.traverse && obj.mesh.traverse((m) => {
          if (m && m.geometry && m.geometry.dispose) m.geometry.dispose();
          if (m && m.material && m.material.dispose) m.material.dispose();
        });
      } catch (_) {}
      // Unregister from the 3D control registry too, so contactless
      // gestures don't end up dispatched to a deleted mesh.
      try {
        if (obj.registryId && window.SyrixModel3DRegistry && window.SyrixModel3DRegistry.unregister) {
          window.SyrixModel3DRegistry.unregister(obj.registryId);
        }
      } catch (_) {}
    }
    _singleton.objects = [];
  }

  function _last() { return _singleton.objects[_singleton.objects.length - 1] || null; }

  function _disposeEntry(obj) {
    if (!obj || !_singleton.scene) return false;
    try { _singleton.scene.remove(obj.mesh); } catch (_) {}
    try {
      obj.mesh && obj.mesh.traverse && obj.mesh.traverse((m) => {
        if (m && m.geometry && m.geometry.dispose) m.geometry.dispose();
        if (m && m.material && m.material.dispose) m.material.dispose();
      });
    } catch (_) {}
    try {
      if (obj.registryId && window.SyrixModel3DRegistry && window.SyrixModel3DRegistry.unregister) {
        window.SyrixModel3DRegistry.unregister(obj.registryId);
      }
    } catch (_) {}
    return true;
  }

  function rotate(axis, degrees) {
    const ent = _last();
    if (!ent) return false;
    const rad = (Number(degrees) || 25) * Math.PI / 180;
    const a = String(axis || 'Y').toUpperCase();
    if (a === 'X') ent.mesh.rotation.x += rad;
    else if (a === 'Z') ent.mesh.rotation.z += rad;
    else ent.mesh.rotation.y += rad;
    return true;
  }

  function scale(factor) {
    const ent = _last();
    if (!ent) return false;
    const f = Number(factor) || 1.0;
    const ud = ent.mesh && ent.mesh.userData;
    if (ent.kind === 'particles' && ud && ud.targetExpand != null) {
      ud.targetExpand = Math.max(0.42, Math.min(3.2, Number(ud.targetExpand || 1) * f));
      return true;
    }
    ent.mesh.scale.set(ent.mesh.scale.x * f, ent.mesh.scale.y * f, ent.mesh.scale.z * f);
    return true;
  }

  function move(direction, step) {
    const ent = _last();
    if (!ent) return false;
    const d = String(direction || '').toLowerCase();
    const s = Number(step) || 0.35;
    if (d === 'left')     ent.mesh.position.x -= s;
    else if (d === 'right')    ent.mesh.position.x += s;
    else if (d === 'up')       ent.mesh.position.y += s;
    else if (d === 'down')     ent.mesh.position.y -= s;
    else if (d === 'forward')  ent.mesh.position.z += s;
    else if (d === 'back')     ent.mesh.position.z -= s;
    else return false;
    return true;
  }

  function explode(distance) {
    if (!_singleton.objects.length) return false;
    const d = Number(distance) || 0.7;
    for (const obj of _singleton.objects) {
      const ud = obj.mesh && obj.mesh.userData;
      if (obj.kind === 'particles' && ud && ud.targetExpand != null) {
        ud.targetExpand = Math.min(3.2, Math.max(Number(ud.targetExpand || 1), 1.9 + d));
        continue;
      }
      if (ud && ud._syrixLayerParts) {
        ud._layersExpanded = true;
        continue;
      }
      const v = new (_three()).Vector3(
        obj.mesh.position.x || (Math.random() - 0.5),
        obj.mesh.position.y || (Math.random() - 0.5),
        obj.mesh.position.z || (Math.random() - 0.5),
      ).normalize().multiplyScalar(d);
      obj.mesh.position.add(v);
    }
    return true;
  }

  function reform() {
    const ent = _last();
    if (!ent) return false;
    const ud = ent.mesh && ent.mesh.userData;
    if (ent.kind === 'particles' && ud && ud.targetExpand != null) {
      ud.targetExpand = 1.0;
      ud.targetOffset = [0, 0, 0];
      return true;
    }
    if (ud && ud._syrixLayerParts) {
      ud._layersExpanded = false;
      ud._syrixLayerParts.forEach((part) => { part.visible = true; });
      return true;
    }
    ent.mesh.position.set(0, 0, 0);
    ent.mesh.rotation.set(0, 0, 0);
    ent.mesh.scale.set(1, 1, 1);
    return true;
  }

  function deleteLast() {
    const ent = _last();
    if (!ent) return false;
    _disposeEntry(ent);
    _singleton.objects.pop();
    return true;
  }

  function duplicateLast() {
    const ent = _last();
    if (!ent) return null;
    const next = add(ent.kind);
    if (next && next.mesh && ent.mesh) {
      next.mesh.position.copy(ent.mesh.position).add(new (_three()).Vector3(0.28, 0.10, 0));
      next.mesh.rotation.copy(ent.mesh.rotation);
      next.mesh.scale.copy(ent.mesh.scale);
    }
    return next;
  }

  function count() { return _singleton.objects.length; }

  function exportLast() {
    const ent = _last();
    if (!ent) return false;
    const B = _bridge();
    if (B && B.sandboxExportBlender) B.sandboxExportBlender(ent.exportTag || ent.kind);
    return true;
  }

  /* ── Voice & Python request consumer ───────────────────────────────── */

  function _applyRequest(req) {
    if (!req || typeof req !== 'object') return;
    const seq = Number(req.seq || 0);
    if (!seq || seq <= _singleton.lastSeqApplied) return;
    _singleton.lastSeqApplied = seq;
    const a = String(req.action || '').toLowerCase();
    if (a === 'create') {
      const subj = String(req.subject || '').toLowerCase();
      if (subj) add(subj);
    } else if (a === 'rotate') {
      rotate('Y', 30);
    } else if (a === 'bigger') {
      scale(1.25);
    } else if (a === 'smaller') {
      scale(0.8);
    } else if (a === 'move') {
      move(String(req.direction || 'right'));
    } else if (a === 'explode') {
      explode(0.8);
    } else if (a === 'clear') {
      clearAll();
    } else if (a === 'export_blender') {
      exportLast();
    }
  }

  /* ── React component ──────────────────────────────────────────────── */

  function SyrixSandbox3D(props) {
    const containerRef = useRef(null);
    const [ready, setReady] = useState(false);
    const [objCount, setObjCount] = useState(0);
    const [panelOpen, setPanelOpen] = useState(false);

    useEffect(() => {
      if (!containerRef.current) return;
      const ok = _initScene(containerRef.current);
      if (!ok) return;
      setReady(true);
      _startLoop();
      _singleton.enabled = true;
      return () => {
        _stopLoop();
        _singleton.enabled = false;
      };
    }, []);

    /* Re-handle bridge requests via snapshot stream. */
    useEffect(() => {
      const B = _bridge();
      if (!B || !B.subscribe) return;
      const unsub = B.subscribe((snap) => {
        const req = snap && snap.syrix_sandbox_request;
        if (req) _applyRequest(req);
        setObjCount(count());
      });
      return () => { try { unsub && unsub(); } catch (_) {} };
    }, []);

    /* Resize. */
    useEffect(() => {
      function onResize() {
        if (!_singleton.renderer || !_singleton.container) return;
        const w = _singleton.container.clientWidth  || 600;
        const h = _singleton.container.clientHeight || 360;
        _singleton.renderer.setSize(w, h);
        _singleton.camera.aspect = w / h;
        _singleton.camera.updateProjectionMatrix();
      }
      window.addEventListener('resize', onResize);
      return () => window.removeEventListener('resize', onResize);
    }, []);

    /* Side settings panel — hidden by default. Set `showSidePanel` on the
     * parent if you want the verbose layout back. */
    const visible = !!props.workspaceOpen && !!props.showSidePanel;

    function addAnd(kind) { add(kind); setObjCount(count()); }
    function clearAnd()  { clearAll(); setObjCount(count()); }

    return (
      <>
        <div
          ref={containerRef}
          className="syrix-sb3d-stage"
          style={{
            position: 'fixed',
            inset: '92px 96px 138px 96px',
            zIndex: 96,
            pointerEvents: 'none',
            opacity: props.workspaceOpen ? 1 : 0,
            transition: 'opacity 0.18s ease',
          }}
        ></div>
        {visible && (
          <div className="syrix-sb3d-panel" style={{
            position: 'fixed', right: 32, top: 124, width: 220,
            padding: 12, zIndex: 110,
            background: 'rgba(3,8,16,0.86)',
            border: '1px solid rgba(34,211,238,0.22)',
            borderRadius: 12, color: '#e7f5ff',
            font: '500 11px Inter',
            boxShadow: '0 28px 70px rgba(0,0,0,0.55)',
          }}>
            <div style={{
              font: '600 10px JetBrains Mono', letterSpacing: '0.22em',
              color: '#22d3ee', textTransform: 'uppercase', marginBottom: 10,
            }}>3D SANDBOX · {objCount} OBJ</div>
            <div style={{ display: 'grid', gap: 5 }}>
              {[
                ['particles', '· particles'],
                ['earth_layers', '· earth layers'],
                ['arc_reactor', '· arc reactor'],
                ['glow_sphere', '· glow sphere'],
                ['wire_cube', '· wire cube'],
                ['neon_ring', '· neon ring'],
                ['torus_knot', '· torus knot'],
              ].map(([k, label]) => (
                <button key={k} onClick={() => addAnd(k)}
                  style={_btnStyle()}>{label}</button>
              ))}
            </div>
            <div style={{ display: 'grid', gap: 5, marginTop: 8, borderTop: '1px dashed rgba(34,211,238,0.16)', paddingTop: 8 }}>
              <button onClick={() => { rotate('Y', 25); }} style={_btnStyle()}>rotate</button>
              <button onClick={() => { scale(1.2); }} style={_btnStyle()}>bigger</button>
              <button onClick={() => { scale(0.83); }} style={_btnStyle()}>smaller</button>
              <button onClick={() => { explode(0.7); }} style={_btnStyle()}>explode</button>
              <button onClick={reform} style={_btnStyle()}>reform</button>
              <button onClick={() => { duplicateLast(); setObjCount(count()); }} style={_btnStyle()}>duplicate</button>
              <button onClick={() => { deleteLast(); setObjCount(count()); }} style={_btnStyle({ danger: true })}>delete</button>
              <button onClick={clearAnd} style={_btnStyle({ danger: true })}>clear</button>
              <button onClick={exportLast} style={_btnStyle({ accent: true })}>send → Blender</button>
            </div>
          </div>
        )}
      </>
    );
  }

  function _btnStyle(opts) {
    const o = opts || {};
    return {
      background: o.accent ? 'rgba(34,211,238,0.16)' : o.danger ? 'rgba(255,79,110,0.12)' : 'rgba(34,211,238,0.06)',
      border: '1px solid ' + (o.accent ? 'rgba(34,211,238,0.5)' : o.danger ? 'rgba(255,79,110,0.4)' : 'rgba(34,211,238,0.22)'),
      borderRadius: 6,
      color: o.danger ? '#ff7d92' : '#22d3ee',
      padding: '6px 8px',
      font: "600 9px 'JetBrains Mono', monospace",
      letterSpacing: '0.2em',
      cursor: 'pointer',
      textTransform: 'uppercase',
    };
  }

  window.SyrixSandbox3D = SyrixSandbox3D;
  window.SyrixSandbox3DApi = { add, clear: clearAll, rotate, scale, move, explode, reform, reset: reform, deleteLast, duplicateLast, count, exportLast };
})();
