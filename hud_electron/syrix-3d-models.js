/*
 * SYRIX 3D MODEL LIBRARY — Procedural builders for educational/visual 3D
 * objects spawned in the HUD sandbox. Pure Three.js geometry; no GLTF
 * downloads, no heavy assets. Loaded lazily — `SyrixModels3D.build(kind, THREE)`
 * is only invoked when the user actually selects a model.
 *
 * Each entry exposes:
 *   build(THREE)  → THREE.Object3D
 *   describe()    → { name, summary, category }
 *   animate?(mesh, dt) → optional per-frame animation hook
 *
 * Public surface:
 *   window.SyrixModels3D = {
 *     list()        → [{ id, name, category, summary }]
 *     build(id, THREE) → THREE.Object3D | null
 *     describe(id)  → { name, summary, category } | null
 *     animate(id, mesh, dt) → void
 *     categories()  → [{ category, ids: [] }]
 *   }
 *
 * Safety rules: all models build entirely in-renderer with no network
 * fetches. If THREE isn't available, build() returns null. Each builder
 * caps its own poly count to stay under ~5k triangles for smooth interaction.
 */
(function () {
  'use strict';
  if (window.SyrixModels3D) return;

  function _mat(THREE, opts) {
    // Phase-3 (2026-05-19) realism upgrade: use MeshPhysicalMaterial so
    // procedural planets/atoms pick up the same clearcoat reading as the
    // primary orb. Falls back to MeshStandardMaterial on older Three.js builds.
    const Ctor = THREE.MeshPhysicalMaterial || THREE.MeshStandardMaterial;
    return new Ctor(Object.assign({
      roughness: 0.55, metalness: 0.15, color: 0xc8d0e8,
      clearcoat: 0.35, clearcoatRoughness: 0.4,
      envMapIntensity: 1.0,
    }, opts || {}));
  }
  function _emissive(THREE, color, intensity) {
    const Ctor = THREE.MeshPhysicalMaterial || THREE.MeshStandardMaterial;
    return new Ctor({
      color: color, emissive: color, emissiveIntensity: intensity || 1.5,
      roughness: 0.3, metalness: 0.4,
      clearcoat: 0.45, clearcoatRoughness: 0.25,
    });
  }
  function _bandedSphere(THREE, baseColor, bandColor, radius, bands) {
    // Procedurally bands a sphere by colouring vertical strips via
    // vertex colours — gives a planet-stripe vibe without textures.
    const geom = new THREE.SphereGeometry(radius, 48, 32);
    const pos = geom.attributes.position;
    const colors = [];
    const c1 = new THREE.Color(baseColor);
    const c2 = new THREE.Color(bandColor);
    for (let i = 0; i < pos.count; i++) {
      const y = pos.getY(i);
      const t = (Math.sin(y * (bands || 6)) + 1) * 0.5;
      const c = c1.clone().lerp(c2, t * 0.55);
      colors.push(c.r, c.g, c.b);
    }
    geom.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    const mat = new THREE.MeshStandardMaterial({
      vertexColors: true, roughness: 0.62, metalness: 0.1,
    });
    return new THREE.Mesh(geom, mat);
  }

  // ───────────────────────── PLANETS / SPACE ─────────────────────────
  const BUILDERS = {
    earth: {
      category: 'space',
      describe: () => ({
        name: 'Earth',
        category: 'space',
        summary: 'Third planet from the Sun. Diameter ~12,742 km, day length 24 h, one natural satellite (the Moon). The only planet known to support life.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const earth = _bandedSphere(THREE, 0x2c5fb6, 0x66c66a, 0.7, 4);
        group.add(earth);
        // Cloud / atmosphere — translucent outer shell.
        const atmos = new THREE.Mesh(
          new THREE.SphereGeometry(0.74, 32, 24),
          new THREE.MeshBasicMaterial({ color: 0x88c4ff, transparent: true, opacity: 0.15, side: THREE.BackSide }),
        );
        group.add(atmos);
        group.userData._spin = 0.004;
        return group;
      },
    },
    earth_layers: {
      category: 'space',
      describe: () => ({
        name: 'Earth Layers',
        category: 'space',
        summary: 'Cutaway-style Earth model with atmosphere, crust, mantle, outer core, and inner core. Explode separates the layers; reset reforms them.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const defs = [
          ['inner core', 0.22, 0xfff0a8, 0.96, 0.00],
          ['outer core', 0.36, 0xff9b39, 0.58, 0.42],
          ['mantle', 0.55, 0xe05c2f, 0.42, 0.86],
          ['crust', 0.68, 0x56a35d, 0.42, 1.28],
          ['atmosphere', 0.76, 0x8fd8ff, 0.18, 1.70],
        ];
        const parts = [];
        defs.forEach(([name, radius, color, opacity, spread], idx) => {
          const mat = new THREE.MeshStandardMaterial({
            color,
            transparent: true,
            opacity,
            roughness: 0.48,
            metalness: 0.08,
            side: idx === defs.length - 1 ? THREE.BackSide : THREE.DoubleSide,
          });
          const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 48, 32), mat);
          mesh.userData._layerName = name;
          mesh.userData._rest = { x: 0, y: 0, z: 0 };
          mesh.userData._expanded = { x: spread, y: 0, z: 0 };
          group.add(mesh);
          parts.push(mesh);
        });
        group.userData._spin = 0.002;
        group.userData._syrixLayerParts = parts;
        group.userData._layersExpanded = false;
        return group;
      },
      animate(mesh, dt) {
        if (!mesh) return;
        mesh.rotation.y += (mesh.userData && mesh.userData._spin) || 0.002;
        const expanded = !!(mesh.userData && mesh.userData._layersExpanded);
        const parts = (mesh.userData && mesh.userData._syrixLayerParts) || [];
        parts.forEach((part) => {
          const target = expanded ? part.userData._expanded : part.userData._rest;
          if (!target) return;
          part.position.x += (target.x - part.position.x) * 0.10;
          part.position.y += (target.y - part.position.y) * 0.10;
          part.position.z += (target.z - part.position.z) * 0.10;
        });
      },
    },
    moon: {
      category: 'space',
      describe: () => ({ name: 'Moon', category: 'space', summary: 'Earth\'s only natural satellite. Tidally locked, diameter 3,474 km, distance ~384,400 km. Drives ocean tides.' }),
      build(THREE) {
        const m = new THREE.Mesh(new THREE.SphereGeometry(0.45, 40, 28),
          _mat(THREE, { color: 0xb4b4be, roughness: 0.95, metalness: 0.02 }));
        m.userData._spin = 0.0015;
        return m;
      },
    },
    sun: {
      category: 'space',
      describe: () => ({ name: 'Sun', category: 'space', summary: 'G-type main-sequence star at the centre of the solar system. Diameter 1.39M km, fuses ~600M tonnes of hydrogen into helium per second.' }),
      build(THREE) {
        const group = new THREE.Group();
        const core = new THREE.Mesh(
          new THREE.SphereGeometry(0.85, 48, 32),
          _emissive(THREE, 0xffb142, 2.2),
        );
        group.add(core);
        const halo = new THREE.Mesh(
          new THREE.SphereGeometry(0.95, 32, 24),
          new THREE.MeshBasicMaterial({ color: 0xffd66c, transparent: true, opacity: 0.18, side: THREE.BackSide }),
        );
        group.add(halo);
        group.userData._spin = 0.002;
        return group;
      },
    },
    mercury: {
      category: 'space',
      describe: () => ({ name: 'Mercury', category: 'space', summary: 'Smallest, innermost planet. No atmosphere, surface temperatures swing from -180°C to 430°C. Year = 88 Earth days.' }),
      build(THREE) {
        const m = new THREE.Mesh(new THREE.SphereGeometry(0.32, 32, 24),
          _mat(THREE, { color: 0x9c8e7e, roughness: 0.9 }));
        m.userData._spin = 0.002;
        return m;
      },
    },
    venus: {
      category: 'space',
      describe: () => ({ name: 'Venus', category: 'space', summary: 'Second planet. Thick CO2 atmosphere, sulphuric clouds, surface ~462°C. Rotates backwards relative to most planets.' }),
      build(THREE) {
        const m = _bandedSphere(THREE, 0xe6a25a, 0xfff0c4, 0.5, 5);
        m.userData._spin = 0.0025;
        return m;
      },
    },
    mars: {
      category: 'space',
      describe: () => ({ name: 'Mars', category: 'space', summary: 'Fourth planet. Thin CO2 atmosphere, polar ice caps, hosts the solar system\'s tallest volcano (Olympus Mons, 22 km).' }),
      build(THREE) {
        const m = _bandedSphere(THREE, 0xc1440e, 0xe6815e, 0.45, 4);
        m.userData._spin = 0.0038;
        return m;
      },
    },
    jupiter: {
      category: 'space',
      describe: () => ({ name: 'Jupiter', category: 'space', summary: 'Largest planet, gas giant. Famous Great Red Spot is a storm wider than Earth. 95 known moons.' }),
      build(THREE) {
        const group = new THREE.Group();
        group.add(_bandedSphere(THREE, 0xc9a875, 0x8a5934, 0.95, 9));
        const spot = new THREE.Mesh(
          new THREE.SphereGeometry(0.13, 18, 14),
          _mat(THREE, { color: 0xb74a2c, roughness: 0.6 }),
        );
        spot.position.set(0.78, -0.18, 0.42);
        spot.scale.set(1, 0.55, 0.4);
        group.add(spot);
        group.userData._spin = 0.005;
        return group;
      },
    },
    saturn: {
      category: 'space',
      describe: () => ({ name: 'Saturn', category: 'space', summary: 'Sixth planet, second largest. Iconic ring system of ice and rock. Density less than water — would float.' }),
      build(THREE) {
        const group = new THREE.Group();
        group.add(_bandedSphere(THREE, 0xd6c08a, 0x8a7045, 0.78, 7));
        const ringGeom = new THREE.RingGeometry(1.05, 1.55, 64);
        const ringMat = new THREE.MeshBasicMaterial({
          color: 0xe8d6a8, side: THREE.DoubleSide, transparent: true, opacity: 0.75,
        });
        const ring = new THREE.Mesh(ringGeom, ringMat);
        ring.rotation.x = Math.PI / 2 - 0.35;
        group.add(ring);
        group.userData._spin = 0.0045;
        return group;
      },
    },
    solar_system: {
      category: 'space',
      describe: () => ({
        name: 'Solar System (simplified)',
        category: 'space',
        summary: 'Schematic view of the Sun and the inner planets to Saturn. Not to scale — distances compressed so all bodies fit in view.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        // Sun core
        const sun = new THREE.Mesh(
          new THREE.SphereGeometry(0.35, 32, 24),
          _emissive(THREE, 0xffb142, 2.0),
        );
        group.add(sun);
        // Orbit rings + planets.
        const planets = [
          { name: 'mercury', r: 0.7,  size: 0.05, color: 0x9c8e7e, speed: 0.011 },
          { name: 'venus',   r: 0.95, size: 0.09, color: 0xe6a25a, speed: 0.009 },
          { name: 'earth',   r: 1.25, size: 0.10, color: 0x3c7fc6, speed: 0.007 },
          { name: 'mars',    r: 1.55, size: 0.07, color: 0xc1440e, speed: 0.006 },
          { name: 'jupiter', r: 1.95, size: 0.18, color: 0xc9a875, speed: 0.004 },
          { name: 'saturn',  r: 2.35, size: 0.15, color: 0xd6c08a, speed: 0.003 },
        ];
        planets.forEach((p) => {
          const orbit = new THREE.Mesh(
            new THREE.RingGeometry(p.r - 0.005, p.r + 0.005, 64),
            new THREE.MeshBasicMaterial({ color: 0x335577, side: THREE.DoubleSide, transparent: true, opacity: 0.35 }),
          );
          orbit.rotation.x = Math.PI / 2;
          group.add(orbit);
          const body = new THREE.Mesh(
            new THREE.SphereGeometry(p.size, 22, 16),
            _mat(THREE, { color: p.color, roughness: 0.65 }),
          );
          body.position.set(p.r, 0, 0);
          body.userData._orbit = { r: p.r, angle: Math.random() * Math.PI * 2, speed: p.speed };
          group.add(body);
        });
        group.userData._isSolarSystem = true;
        return group;
      },
      animate(mesh, dt) {
        if (!mesh || !mesh.children) return;
        for (const c of mesh.children) {
          const o = c.userData && c.userData._orbit;
          if (!o) continue;
          o.angle += o.speed;
          c.position.x = Math.cos(o.angle) * o.r;
          c.position.z = Math.sin(o.angle) * o.r;
        }
      },
    },
    black_hole: {
      category: 'space',
      describe: () => ({
        name: 'Black hole',
        category: 'space',
        summary: 'Region of spacetime where gravity is so strong nothing — including light — escapes past the event horizon. Surrounded by an accretion disk of superheated matter.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        // Event horizon — pure black sphere.
        const horizon = new THREE.Mesh(
          new THREE.SphereGeometry(0.45, 48, 32),
          new THREE.MeshBasicMaterial({ color: 0x000000 }),
        );
        group.add(horizon);
        // Photon ring — thin emissive torus.
        const photonRing = new THREE.Mesh(
          new THREE.TorusGeometry(0.5, 0.018, 16, 96),
          _emissive(THREE, 0xffb95e, 2.4),
        );
        photonRing.rotation.x = Math.PI / 2;
        group.add(photonRing);
        // Accretion disk — flat ring with bright inner edge.
        const disk = new THREE.Mesh(
          new THREE.RingGeometry(0.55, 1.4, 96, 1),
          new THREE.MeshBasicMaterial({
            color: 0xff9b3d, side: THREE.DoubleSide, transparent: true, opacity: 0.78,
          }),
        );
        disk.rotation.x = Math.PI / 2 - 0.25;
        group.add(disk);
        const diskOuter = new THREE.Mesh(
          new THREE.RingGeometry(1.4, 1.85, 64, 1),
          new THREE.MeshBasicMaterial({
            color: 0x9a4eff, side: THREE.DoubleSide, transparent: true, opacity: 0.35,
          }),
        );
        diskOuter.rotation.x = Math.PI / 2 - 0.25;
        group.add(diskOuter);
        group.userData._spin = 0.008;
        return group;
      },
    },
    spacecraft: {
      category: 'space',
      describe: () => ({
        name: 'Spacecraft',
        category: 'space',
        summary: 'Simplified single-stage rocket: cone fairing, fuselage, and fins. Real vehicles add staging, gimbal engines, payload bay and life-support systems.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const body = new THREE.Mesh(
          new THREE.CylinderGeometry(0.18, 0.20, 1.0, 24),
          _mat(THREE, { color: 0xdce5ef, metalness: 0.55, roughness: 0.35 }),
        );
        group.add(body);
        const nose = new THREE.Mesh(
          new THREE.ConeGeometry(0.18, 0.45, 24),
          _mat(THREE, { color: 0xff6b6b, roughness: 0.4 }),
        );
        nose.position.y = 0.72;
        group.add(nose);
        // 4 fins
        for (let i = 0; i < 4; i++) {
          const fin = new THREE.Mesh(
            new THREE.BoxGeometry(0.04, 0.28, 0.22),
            _mat(THREE, { color: 0x224b8e }),
          );
          fin.position.set(0.18 * Math.cos(i * Math.PI / 2), -0.45, 0.18 * Math.sin(i * Math.PI / 2));
          fin.rotation.y = i * Math.PI / 2;
          group.add(fin);
        }
        // Engine flame
        const flame = new THREE.Mesh(
          new THREE.ConeGeometry(0.14, 0.3, 18),
          _emissive(THREE, 0xffa040, 2.6),
        );
        flame.position.y = -0.65;
        flame.rotation.x = Math.PI;
        group.add(flame);
        group.userData._spin = 0.002;
        return group;
      },
    },

    // ─────────────────────── ANATOMY / BIOLOGY ───────────────────────
    human_body: {
      category: 'anatomy',
      describe: () => ({
        name: 'Human body (simplified)',
        category: 'anatomy',
        summary: 'Schematic torso with head, arms and legs. Real anatomy involves 206 bones, 600+ muscles, and 11 organ systems — this is a placeholder for visual interaction.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const skin = _mat(THREE, { color: 0xe1b6a4, roughness: 0.7, metalness: 0.05 });
        const head  = new THREE.Mesh(new THREE.SphereGeometry(0.18, 28, 20), skin);
        head.position.y = 0.85;
        group.add(head);
        const torso = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.6, 0.22), skin);
        torso.position.y = 0.42;
        group.add(torso);
        // Arms
        for (const sx of [-1, 1]) {
          const arm = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.55, 14), skin);
          arm.position.set(sx * 0.27, 0.42, 0);
          group.add(arm);
        }
        // Legs
        for (const sx of [-0.11, 0.11]) {
          const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.65, 14), skin);
          leg.position.set(sx, -0.18, 0);
          group.add(leg);
        }
        return group;
      },
    },
    heart: {
      category: 'anatomy',
      describe: () => ({
        name: 'Heart',
        category: 'anatomy',
        summary: 'Four-chambered muscular organ. Beats ~100,000 times a day, pumping ~7,500 L of blood through ~100,000 km of vessels.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const mat = _emissive(THREE, 0xc11b3c, 0.6);
        // Two lobes
        const l1 = new THREE.Mesh(new THREE.SphereGeometry(0.32, 32, 24), mat);
        l1.position.set(-0.18, 0.12, 0);
        const l2 = new THREE.Mesh(new THREE.SphereGeometry(0.32, 32, 24), mat);
        l2.position.set(0.18, 0.12, 0);
        group.add(l1); group.add(l2);
        const tip = new THREE.Mesh(new THREE.ConeGeometry(0.42, 0.55, 24), mat);
        tip.position.y = -0.22;
        tip.rotation.x = Math.PI;
        group.add(tip);
        group.userData._beat = 0;
        return group;
      },
      animate(mesh, dt) {
        if (!mesh.userData) return;
        mesh.userData._beat = (mesh.userData._beat || 0) + (dt || 0.016);
        const s = 1 + 0.05 * Math.sin(mesh.userData._beat * 5.5);
        mesh.scale.set(s, s, s);
      },
    },
    brain: {
      category: 'anatomy',
      describe: () => ({
        name: 'Brain',
        category: 'anatomy',
        summary: 'Central organ of the nervous system. ~86 billion neurons forming ~100 trillion synapses. Consumes ~20% of the body\'s energy.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const mat = _mat(THREE, { color: 0xe89eb0, roughness: 0.55 });
        // Two hemispheres + cerebellum cluster.
        for (const sx of [-0.18, 0.18]) {
          const hemi = new THREE.Mesh(new THREE.SphereGeometry(0.35, 32, 24), mat);
          hemi.position.x = sx;
          hemi.scale.set(1, 0.95, 1.1);
          group.add(hemi);
        }
        // Add 30 small bump spheres for the cortex folds.
        for (let i = 0; i < 30; i++) {
          const r = 0.42;
          const u = Math.random() * Math.PI * 2;
          const v = Math.random() * Math.PI;
          const x = r * Math.cos(u) * Math.sin(v);
          const y = r * Math.cos(v);
          const z = r * Math.sin(u) * Math.sin(v);
          const bump = new THREE.Mesh(
            new THREE.SphereGeometry(0.06 + Math.random() * 0.05, 12, 10),
            mat,
          );
          bump.position.set(x, y, z);
          group.add(bump);
        }
        // Brain stem
        const stem = new THREE.Mesh(
          new THREE.CylinderGeometry(0.07, 0.09, 0.25, 14),
          _mat(THREE, { color: 0xc77c8e, roughness: 0.6 }),
        );
        stem.position.y = -0.32;
        group.add(stem);
        return group;
      },
    },
    dna: {
      category: 'biology',
      describe: () => ({
        name: 'DNA helix',
        category: 'biology',
        summary: 'Double helix of deoxyribonucleic acid. Encodes genetic instructions via 4 bases (A, T, C, G). ~3.2 billion base pairs in the human genome.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const turns = 4;
        const steps = 48;
        const radius = 0.32;
        const height = 1.6;
        const matA = _emissive(THREE, 0x22d3ee, 0.9);
        const matB = _emissive(THREE, 0xff6ec7, 0.9);
        const matRung = _mat(THREE, { color: 0xc8c8d6, metalness: 0.4 });
        for (let i = 0; i < steps; i++) {
          const t = i / steps;
          const a = t * Math.PI * 2 * turns;
          const y = (t - 0.5) * height;
          const x1 = Math.cos(a) * radius;
          const z1 = Math.sin(a) * radius;
          const x2 = Math.cos(a + Math.PI) * radius;
          const z2 = Math.sin(a + Math.PI) * radius;
          const bA = new THREE.Mesh(new THREE.SphereGeometry(0.05, 14, 10), matA);
          bA.position.set(x1, y, z1);
          group.add(bA);
          const bB = new THREE.Mesh(new THREE.SphereGeometry(0.05, 14, 10), matB);
          bB.position.set(x2, y, z2);
          group.add(bB);
          if (i % 2 === 0) {
            const rung = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, radius * 2, 8), matRung);
            rung.position.set((x1 + x2) / 2, y, (z1 + z2) / 2);
            rung.rotation.z = Math.PI / 2;
            rung.rotation.y = a + Math.PI / 2;
            group.add(rung);
          }
        }
        group.userData._spin = 0.005;
        return group;
      },
    },

    // ─────────────────────── ANIMALS ───────────────────────
    cat: {
      category: 'animals',
      describe: () => ({
        name: 'Cat (simplified)',
        category: 'animals',
        summary: 'Domestic cat (Felis catus). Obligate carnivore, retractable claws, ~70-80 cm long incl. tail, average life expectancy 12-18 years.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const fur = _mat(THREE, { color: 0xb88a3c, roughness: 0.85 });
        const body = new THREE.Mesh(new THREE.SphereGeometry(0.35, 24, 18), fur);
        body.scale.set(1.6, 0.7, 0.7);
        group.add(body);
        const head = new THREE.Mesh(new THREE.SphereGeometry(0.22, 22, 18), fur);
        head.position.set(0.5, 0.12, 0);
        group.add(head);
        // Ears
        for (const sz of [-1, 1]) {
          const ear = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.16, 14), fur);
          ear.position.set(0.5, 0.32, sz * 0.12);
          ear.rotation.z = -0.2 * sz;
          group.add(ear);
        }
        // Legs
        for (const xx of [-0.3, 0.3]) for (const zz of [-0.18, 0.18]) {
          const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.24, 10), fur);
          leg.position.set(xx, -0.3, zz);
          group.add(leg);
        }
        // Tail
        const tail = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.02, 0.55, 12), fur);
        tail.position.set(-0.55, 0.1, 0);
        tail.rotation.z = -Math.PI / 3;
        group.add(tail);
        return group;
      },
    },
    dog: {
      category: 'animals',
      describe: () => ({
        name: 'Dog (simplified)',
        category: 'animals',
        summary: 'Canis lupus familiaris. Domesticated wolf descendants, ~15,000 years co-evolved with humans. Wide breed variation in size and shape.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const fur = _mat(THREE, { color: 0x8b5a2b, roughness: 0.85 });
        const body = new THREE.Mesh(new THREE.BoxGeometry(0.72, 0.32, 0.34), fur);
        group.add(body);
        const head = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.28, 0.28), fur);
        head.position.set(0.45, 0.12, 0);
        group.add(head);
        const snout = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.12, 0.14), fur);
        snout.position.set(0.62, 0.05, 0);
        group.add(snout);
        for (const xx of [-0.25, 0.25]) for (const zz of [-0.13, 0.13]) {
          const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.3, 10), fur);
          leg.position.set(xx, -0.28, zz);
          group.add(leg);
        }
        const tail = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.02, 0.32, 10), fur);
        tail.position.set(-0.42, 0.12, 0);
        tail.rotation.z = Math.PI / 3.5;
        group.add(tail);
        return group;
      },
    },
    bird: {
      category: 'animals',
      describe: () => ({
        name: 'Bird (simplified)',
        category: 'animals',
        summary: 'Class Aves. ~10,000 species. Defined by feathers, beaks, hollow bones, and laying hard-shelled eggs. Many fly; some — penguins, ostriches — do not.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const body = new THREE.Mesh(
          new THREE.SphereGeometry(0.28, 24, 18),
          _mat(THREE, { color: 0x3aa6d1, roughness: 0.5 }),
        );
        body.scale.set(1.2, 0.9, 0.9);
        group.add(body);
        const head = new THREE.Mesh(
          new THREE.SphereGeometry(0.16, 22, 16),
          _mat(THREE, { color: 0x57c6e6, roughness: 0.5 }),
        );
        head.position.set(0.32, 0.12, 0);
        group.add(head);
        const beak = new THREE.Mesh(
          new THREE.ConeGeometry(0.05, 0.16, 14),
          _mat(THREE, { color: 0xf2b71a, roughness: 0.4 }),
        );
        beak.position.set(0.48, 0.1, 0);
        beak.rotation.z = -Math.PI / 2;
        group.add(beak);
        // Wings — two thin slabs.
        for (const sz of [-1, 1]) {
          const wing = new THREE.Mesh(
            new THREE.BoxGeometry(0.36, 0.04, 0.18),
            _mat(THREE, { color: 0x2a85ad, roughness: 0.55 }),
          );
          wing.position.set(-0.04, 0.05, sz * 0.18);
          wing.rotation.z = sz * 0.15;
          group.add(wing);
        }
        return group;
      },
    },

    // ─────────────────────── CHEMISTRY / PHYSICS ───────────────────────
    atom: {
      category: 'chemistry',
      describe: () => ({
        name: 'Atom (Bohr model)',
        category: 'chemistry',
        summary: 'Schematic Bohr model: nucleus of protons & neutrons surrounded by electrons in orbital shells. Real electron behaviour is quantum mechanical (probability clouds, not orbits).',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const nucleus = new THREE.Group();
        const proton  = _emissive(THREE, 0xff6b6b, 1.5);
        const neutron = _emissive(THREE, 0xcfd6e0, 0.7);
        // 6 protons + 6 neutrons (carbon-like) tightly clustered.
        for (let i = 0; i < 6; i++) {
          const p = new THREE.Mesh(new THREE.SphereGeometry(0.08, 16, 12), proton);
          p.position.set((Math.random() - 0.5) * 0.12, (Math.random() - 0.5) * 0.12, (Math.random() - 0.5) * 0.12);
          nucleus.add(p);
          const n = new THREE.Mesh(new THREE.SphereGeometry(0.08, 16, 12), neutron);
          n.position.set((Math.random() - 0.5) * 0.12, (Math.random() - 0.5) * 0.12, (Math.random() - 0.5) * 0.12);
          nucleus.add(n);
        }
        group.add(nucleus);
        // 3 orbital rings + an electron on each.
        const orbits = [];
        for (let i = 0; i < 3; i++) {
          const r = 0.55 + i * 0.22;
          const ring = new THREE.Mesh(
            new THREE.TorusGeometry(r, 0.005, 8, 64),
            new THREE.MeshBasicMaterial({ color: 0x66c6ff, transparent: true, opacity: 0.45 }),
          );
          ring.rotation.x = Math.PI / 2 + i * 0.3;
          ring.rotation.y = i * 0.5;
          group.add(ring);
          const electron = new THREE.Mesh(
            new THREE.SphereGeometry(0.05, 14, 10),
            _emissive(THREE, 0x22d3ee, 1.6),
          );
          electron.userData._orbit = { r: r, angle: Math.random() * Math.PI * 2, speed: 0.04 - i * 0.012, tiltX: i * 0.3, tiltY: i * 0.5 };
          group.add(electron);
          orbits.push(electron);
        }
        group.userData._atomElectrons = orbits;
        return group;
      },
      animate(mesh, dt) {
        const electrons = mesh.userData && mesh.userData._atomElectrons;
        if (!electrons) return;
        for (const e of electrons) {
          const o = e.userData._orbit;
          o.angle += o.speed;
          // Approximate the tilted orbit.
          const x = Math.cos(o.angle) * o.r;
          const y = Math.sin(o.angle) * o.r * Math.sin(o.tiltX) * 0.45;
          const z = Math.sin(o.angle) * o.r * Math.cos(o.tiltX);
          e.position.set(x, y, z);
        }
      },
    },
    molecule_water: {
      category: 'chemistry',
      describe: () => ({
        name: 'Water (H₂O)',
        category: 'chemistry',
        summary: 'Two hydrogens covalently bonded to one oxygen at ~104.5°. Polar molecule, universal solvent, basis of all known life.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const oMat = _emissive(THREE, 0xff6b6b, 0.8);
        const hMat = _emissive(THREE, 0xffffff, 0.6);
        const bondMat = _mat(THREE, { color: 0xc8c8d6 });
        const O = new THREE.Mesh(new THREE.SphereGeometry(0.28, 28, 20), oMat);
        group.add(O);
        for (const dir of [-1, 1]) {
          const a = dir * 52.25 * Math.PI / 180; // 104.5° apart
          const x = Math.sin(a) * 0.55;
          const y = Math.cos(a) * 0.55 - 0.2;
          const H = new THREE.Mesh(new THREE.SphereGeometry(0.16, 22, 16), hMat);
          H.position.set(x, y, 0);
          group.add(H);
          // Bond
          const bond = new THREE.Mesh(
            new THREE.CylinderGeometry(0.04, 0.04, Math.sqrt(x * x + (y + 0.2) * (y + 0.2)), 12),
            bondMat,
          );
          bond.position.set(x / 2, (y - 0.2) / 2, 0);
          bond.lookAt(new THREE.Vector3(x, y, 0));
          bond.rotateX(Math.PI / 2);
          group.add(bond);
        }
        group.userData._spin = 0.004;
        return group;
      },
    },
    molecule_methane: {
      category: 'chemistry',
      describe: () => ({
        name: 'Methane (CH₄)',
        category: 'chemistry',
        summary: 'One carbon, four hydrogens in tetrahedral geometry (~109.5°). Simplest hydrocarbon, main component of natural gas.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const cMat = _emissive(THREE, 0x2a2a2a, 0.4);
        const hMat = _emissive(THREE, 0xffffff, 0.6);
        const bondMat = _mat(THREE, { color: 0xc8c8d6 });
        const C = new THREE.Mesh(new THREE.SphereGeometry(0.28, 28, 20), cMat);
        group.add(C);
        // Four hydrogens at tetrahedron vertices.
        const verts = [
          new THREE.Vector3( 0.55,  0.55,  0.55),
          new THREE.Vector3( 0.55, -0.55, -0.55),
          new THREE.Vector3(-0.55,  0.55, -0.55),
          new THREE.Vector3(-0.55, -0.55,  0.55),
        ];
        for (const v of verts) {
          const H = new THREE.Mesh(new THREE.SphereGeometry(0.15, 22, 16), hMat);
          H.position.copy(v);
          group.add(H);
          const dir = v.clone();
          const len = dir.length();
          const bond = new THREE.Mesh(
            new THREE.CylinderGeometry(0.04, 0.04, len, 12),
            bondMat,
          );
          bond.position.copy(v.clone().multiplyScalar(0.5));
          bond.lookAt(v);
          bond.rotateX(Math.PI / 2);
          group.add(bond);
        }
        group.userData._spin = 0.003;
        return group;
      },
    },

    // ─────────────────────── ENGINEERING ───────────────────────
    car: {
      category: 'engineering',
      describe: () => ({
        name: 'Car (simplified)',
        category: 'engineering',
        summary: 'Schematic four-wheel passenger vehicle. Modern designs add chassis, drivetrain, ECU, safety cells, suspension geometry, etc.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const paint = _mat(THREE, { color: 0xd11b1b, metalness: 0.7, roughness: 0.3 });
        const glass = new THREE.MeshStandardMaterial({ color: 0x1a3a55, metalness: 0.8, roughness: 0.15, transparent: true, opacity: 0.85 });
        const tyre  = _mat(THREE, { color: 0x111111, roughness: 0.95 });
        // Body
        const body = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.28, 0.55), paint);
        body.position.y = 0.18;
        group.add(body);
        const cabin = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.22, 0.5), glass);
        cabin.position.set(-0.05, 0.42, 0);
        group.add(cabin);
        // Wheels
        for (const xx of [-0.4, 0.4]) for (const zz of [-0.3, 0.3]) {
          const w = new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.13, 0.08, 18), tyre);
          w.rotation.x = Math.PI / 2;
          w.position.set(xx, 0.0, zz);
          group.add(w);
        }
        // Headlights
        for (const zz of [-0.18, 0.18]) {
          const h = new THREE.Mesh(new THREE.SphereGeometry(0.04, 12, 10), _emissive(THREE, 0xffffe0, 2.2));
          h.position.set(0.62, 0.22, zz);
          group.add(h);
        }
        return group;
      },
    },
    engine: {
      category: 'engineering',
      describe: () => ({
        name: 'Engine (V-block)',
        category: 'engineering',
        summary: 'Schematic internal combustion engine V-block with two banks of cylinders. Real engines integrate camshafts, valves, injectors, ECU, cooling and exhaust.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const block = _mat(THREE, { color: 0x2c3a4a, metalness: 0.7, roughness: 0.35 });
        const head  = _mat(THREE, { color: 0x8a8e98, metalness: 0.8, roughness: 0.3 });
        const main = new THREE.Mesh(new THREE.BoxGeometry(0.7, 0.32, 0.5), block);
        group.add(main);
        // Two banks of 4 cylinders, splayed 30°.
        for (const sx of [-1, 1]) {
          const bank = new THREE.Group();
          for (let i = 0; i < 4; i++) {
            const cyl = new THREE.Mesh(
              new THREE.CylinderGeometry(0.08, 0.08, 0.32, 18),
              head,
            );
            cyl.position.x = (i - 1.5) * 0.16;
            bank.add(cyl);
          }
          bank.rotation.z = sx * 0.5;
          bank.position.y = 0.28;
          group.add(bank);
        }
        // Crank pulley (front)
        const pulley = new THREE.Mesh(
          new THREE.CylinderGeometry(0.11, 0.11, 0.08, 22),
          _mat(THREE, { color: 0xb0b6c0, metalness: 0.85 }),
        );
        pulley.rotation.z = Math.PI / 2;
        pulley.position.set(0.38, 0, 0);
        group.add(pulley);
        return group;
      },
    },

    // ─────────────────────── COMPUTING / AI ───────────────────────
    neural_net: {
      category: 'computing',
      describe: () => ({
        name: 'Neural network',
        category: 'computing',
        summary: 'Feed-forward neural network with input, hidden, and output layers. Each weighted edge passes signals forward; weights are learned by backprop.',
      }),
      build(THREE) {
        const group = new THREE.Group();
        const layers = [5, 7, 7, 4];
        const xStep = 0.6;
        const layerNodes = [];
        const nodeMat = _emissive(THREE, 0x22d3ee, 1.6);
        const edgeMat = new THREE.LineBasicMaterial({ color: 0x447ad6, transparent: true, opacity: 0.45 });
        for (let li = 0; li < layers.length; li++) {
          const n = layers[li];
          const nodes = [];
          const xPos = (li - (layers.length - 1) / 2) * xStep;
          for (let i = 0; i < n; i++) {
            const yPos = (i - (n - 1) / 2) * 0.22;
            const node = new THREE.Mesh(new THREE.SphereGeometry(0.07, 18, 14), nodeMat);
            node.position.set(xPos, yPos, 0);
            group.add(node);
            nodes.push(node);
          }
          layerNodes.push(nodes);
        }
        // Edges
        for (let li = 0; li < layerNodes.length - 1; li++) {
          for (const a of layerNodes[li]) {
            for (const b of layerNodes[li + 1]) {
              const geom = new THREE.BufferGeometry().setFromPoints([a.position, b.position]);
              const line = new THREE.Line(geom, edgeMat);
              group.add(line);
            }
          }
        }
        group.userData._neuralPulse = 0;
        group.userData._neuralNodes = layerNodes;
        return group;
      },
      animate(mesh, dt) {
        const layerNodes = mesh.userData && mesh.userData._neuralNodes;
        if (!layerNodes) return;
        mesh.userData._neuralPulse = ((mesh.userData._neuralPulse || 0) + (dt || 0.016)) % 4;
        const phase = mesh.userData._neuralPulse;
        for (let li = 0; li < layerNodes.length; li++) {
          const local = Math.max(0, 1 - Math.abs(phase - li * 0.6));
          for (const n of layerNodes[li]) {
            if (n.material && n.material.emissiveIntensity !== undefined) {
              n.material.emissiveIntensity = 1.0 + local * 2.0;
            }
          }
        }
      },
    },
  };

  function list() {
    const out = [];
    for (const id of Object.keys(BUILDERS)) {
      const b = BUILDERS[id];
      const meta = b.describe ? b.describe() : { name: id, category: b.category || 'other', summary: '' };
      out.push({ id, ...meta });
    }
    return out;
  }
  function categories() {
    const map = new Map();
    for (const id of Object.keys(BUILDERS)) {
      const cat = (BUILDERS[id].describe ? BUILDERS[id].describe().category : BUILDERS[id].category) || 'other';
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat).push(id);
    }
    return Array.from(map.entries()).map(([category, ids]) => ({ category, ids }));
  }
  function build(id, THREE) {
    if (!THREE) return null;
    const b = BUILDERS[id];
    if (!b || typeof b.build !== 'function') return null;
    try {
      const obj = b.build(THREE);
      if (obj) {
        obj.userData._modelId = id;
      }
      return obj;
    } catch (err) {
      try { console.warn('[SYRIX_MODELS] build failed for', id, err); } catch (_) {}
      return null;
    }
  }
  function describe(id) {
    const b = BUILDERS[id];
    if (!b || !b.describe) return null;
    return b.describe();
  }
  function animate(id, mesh, dt) {
    const b = BUILDERS[id];
    if (!b) return;
    if (typeof b.animate === 'function') {
      try { b.animate(mesh, dt); } catch (_) {}
    } else if (mesh && mesh.userData && mesh.userData._spin) {
      mesh.rotation.y += mesh.userData._spin;
    }
  }
  function has(id) {
    return Object.prototype.hasOwnProperty.call(BUILDERS, id);
  }

  window.SyrixModels3D = { list, categories, build, describe, animate, has };
})();
