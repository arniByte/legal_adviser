/* Живые элементы интерфейса: тепловая аура и маскот из точек.
 *
 * Аура — несколько плывущих пятен на крошечном холсте 48×48; браузер растягивает его, и
 * размытие получается само. Маскот — круг из точек: ореол с бегущими волнами, глаза из тех
 * же точек. Глаза следят за курсором и полем ввода, моргают, бегают, пока идёт ответ,
 * улыбаются при наведении; курсор расталкивает точки, клик их разбрасывает.
 *
 * Все анимации считаются от прошедшего времени, а не от кадров, засыпают вне экрана и на
 * скрытой вкладке, а при prefers-reduced-motion рисуют один статичный кадр.
 */
(() => {
  "use strict";

  // ------------------------------------------------------------------ aura
  // Палитры в цветах ПСБ: от прозрачного через персик и оранжевый к фиолетовому и индиго.
  const PALETTES = {
    psb: [
      [0, [255, 255, 255, 0]],
      [0.22, [255, 228, 204, 0.85]],
      [0.4, [255, 172, 122, 1]],
      [0.56, [242, 107, 33, 1]],
      [0.7, [196, 96, 200, 1]],
      [0.84, [96, 86, 226, 1]],
      [1, [34, 30, 118, 1]],
    ],
    soft: [
      [0, [255, 255, 255, 0]],
      [0.25, [236, 232, 255, 0.9]],
      [0.5, [186, 178, 255, 1]],
      [0.75, [255, 186, 146, 1]],
      [1, [242, 112, 44, 1]],
    ],
    night: [
      [0, [255, 255, 255, 0]],
      [0.3, [214, 210, 255, 0.9]],
      [0.6, [124, 114, 240, 1]],
      [1, [40, 34, 130, 1]],
    ],
  };
  const tables = {};
  function table(name) {
    if (tables[name]) return tables[name];
    const stops = PALETTES[name] || PALETTES.psb;
    const out = new Uint8ClampedArray(256 * 4);
    for (let i = 0; i < 256; i++) {
      const v = i / 255;
      let k = 0;
      while (k < stops.length - 2 && v > stops[k + 1][0]) k++;
      const [p0, c0] = stops[k];
      const [p1, c1] = stops[k + 1];
      const f = Math.min(1, Math.max(0, (v - p0) / (p1 - p0)));
      for (let ch = 0; ch < 3; ch++) out[i * 4 + ch] = c0[ch] + (c1[ch] - c0[ch]) * f;
      out[i * 4 + 3] = (c0[3] + (c1[3] - c0[3]) * f) * 255;
    }
    return (tables[name] = out);
  }

  const AURA_SIZE = 48;
  const BLOBS = 6;
  function paintAura(image, t, o) {
    const lut = table(o.palette);
    const size = image.width, data = image.data;
    const energy = o.energy || 0, round = !!o.round, bands = o.bands === undefined ? 7 : o.bands;
    const speed = 0.18 + energy * 0.5;
    const orbit = round ? 0.2 : 0.3;
    const seed = o.seed || 0;
    const bx = [], by = [], br = [], bw = [];
    for (let b = 0; b < BLOBS; b++) {
      const phase = seed * 1.7 + b * 2.39;
      bx[b] = 0.5 + orbit * Math.sin(t * speed * (0.7 + b * 0.13) + phase);
      by[b] = 0.5 + orbit * Math.cos(t * speed * (0.6 + b * 0.11) + phase * 1.3);
      br[b] = 0.16 + 0.07 * Math.sin(t * 0.3 + b);
      bw[b] = b % 3 === 0 ? 1 : 0.62;
    }
    for (let y = 0; y < size; y++) {
      const fy = (y + 0.5) / size;
      for (let x = 0; x < size; x++) {
        const fx = (x + 0.5) / size;
        const r = Math.hypot(fx - 0.5, fy - 0.5) * 2;
        let v = round ? 0.55 * (1 - r * r * r) : 0;
        for (let b = 0; b < BLOBS; b++) {
          const dx = fx - bx[b], dy = fy - by[b];
          v += bw[b] * Math.exp(-(dx * dx + dy * dy) / (br[b] * br[b]));
        }
        v = Math.min(1, v * (round ? 0.62 : 0.8) * (1 + energy * 0.3));
        if (bands > 0) v = Math.round(v * bands) / bands;
        const alpha = round ? (r > 1 ? 0 : Math.min(1, (1 - r) * 6)) : 1;
        const i = Math.round(v * 255) * 4, p = (y * size + x) * 4;
        data[p] = lut[i]; data[p + 1] = lut[i + 1]; data[p + 2] = lut[i + 2]; data[p + 3] = lut[i + 3] * alpha;
      }
    }
  }

  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");

  /** Runs `draw(now)` at `fps` while the canvas is on screen and the tab is visible. */
  function animate(canvas, fps, draw, still) {
    let frame = 0, last = 0, visible = true;
    const loop = (now) => {
      frame = requestAnimationFrame(loop);
      if (now - last < 1000 / fps) return;
      last = now;
      draw(now);
    };
    const run = () => {
      cancelAnimationFrame(frame);
      if (motion.matches) return still();
      if (visible && !document.hidden) frame = requestAnimationFrame(loop);
    };
    const observer = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; run(); });
    observer.observe(canvas);
    document.addEventListener("visibilitychange", run);
    motion.addEventListener("change", run);
    still();
    run();
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("visibilitychange", run);
      motion.removeEventListener("change", run);
    };
  }

  function aura(canvas, options) {
    const o = Object.assign({ palette: "psb", seed: 0, bands: 7, round: false, energy: 0, fps: 24 }, options);
    canvas.width = AURA_SIZE;
    canvas.height = AURA_SIZE;
    const context = canvas.getContext("2d");
    const image = context.createImageData(AURA_SIZE, AURA_SIZE);
    const start = performance.now() - o.seed * 7000;
    const draw = (now) => { paintAura(image, (now - start) / 1000, o); context.putImageData(image, 0, 0); };
    const stop = animate(canvas, o.fps, draw, () => draw(start + 4000));
    return { set: (patch) => Object.assign(o, patch), stop };
  }

  /** One still frame as a data URL: a cover that costs nothing per frame. */
  function auraImage(palette, seed, round) {
    const c = document.createElement("canvas");
    c.width = c.height = AURA_SIZE;
    const ctx = c.getContext("2d");
    const image = ctx.createImageData(AURA_SIZE, AURA_SIZE);
    paintAura(image, 3 + seed * 2.1, { palette, seed, bands: 0, round });
    ctx.putImageData(image, 0, 0);
    return c.toDataURL();
  }

  // ------------------------------------------------------------------ mark
  const SMALL = 72;     // below this the mark is a logo: fewer dots, no eyes
  const LEVELS = 12;    // dots are drawn in 12 opacity levels: one path per level
  const PAD = 0.3;      // room around the live mark for scattered dots

  const smoothstep = (a, b, x) => { const k = Math.min(1, Math.max(0, (x - a) / (b - a))); return k * k * (3 - 2 * k); };
  const angleGap = (a, b) => Math.atan2(Math.sin(a - b), Math.cos(a - b));

  function halo(r, angle, t, energy, small) {
    const breath = Math.sin(t * 1.05) * (small ? 0.065 : 0.045);
    const contour = 0.76 + breath + Math.sin(angle * 3 - t * 0.75) * 0.04 + Math.cos(angle * 5 + t) * 0.02;
    const ring = Math.exp(-(((r - contour) / (small ? 0.18 : 0.12)) ** 2));
    const orbit = Math.exp(-((angleGap(angle, t * 0.9) / 0.65) ** 2));
    const back = Math.exp(-((angleGap(angle, -t * 0.6 + Math.PI) / 0.9) ** 2));
    const ripple = (0.5 + 0.5 * Math.cos(r * 14 - t * 2.2 + Math.sin(angle * 2 - t * 0.4))) ** 4;
    const rim = smoothstep(0.35, 0.6, r) * (1 - smoothstep(0.9, 1, r));
    const sweep = energy * Math.exp(-((angleGap(angle, t * 2.4) / 0.7) ** 2)) * Math.exp(-(((r - 0.8) / 0.2) ** 2));
    const ground = 0.05 * (1 - smoothstep(0.6, 0.95, r));
    return Math.min(1, Math.max(ground, ring * (0.24 + orbit * 0.68 + back * 0.35) + ripple * rim * 0.24 + sweep * 0.65));
  }

  // where to look: at the text field while someone types, otherwise at the pointer
  const gaze = { x: 0, y: 0, typedAt: 0, pointer: false };
  window.addEventListener("pointermove", (e) => { gaze.x = e.clientX; gaze.y = e.clientY; gaze.pointer = true; }, { passive: true });
  document.addEventListener("input", () => { gaze.typedAt = performance.now(); }, true);
  function lookTarget() {
    const a = document.activeElement;
    if (a instanceof HTMLTextAreaElement && performance.now() - gaze.typedAt < 1600) {
      const box = a.getBoundingClientRect();
      return { x: box.left + Math.min(box.width - 24, a.value.length * 7 + 24), y: box.top + 20 };
    }
    return gaze.pointer ? gaze : null;
  }

  function eyes(x, y, face, cell) {
    let value = 0;
    for (const side of [-1, 1]) {
      const cx = side * 0.29 + face.lookX * 0.12;
      const cy = -0.04 + face.lookY * 0.09;
      const dx = x - cx, dy = y - cy;
      if (face.happy) {
        const arc = Math.abs(Math.hypot(dx, dy - 0.1) - 0.14);
        const upper = dy - 0.1 < 0.01 ? 1 : 0;
        value = Math.max(value, upper * (1 - smoothstep(0.03, 0.07, arc)));
        continue;
      }
      const a = 0.14, b = Math.max(0.035, 0.25 * face.open);
      const d = (dx / a) ** 2 + (dy / b) ** 2;
      let eye = 1 - smoothstep(0.7, 1.15, d);
      const glint = Math.hypot(dx + 0.035 - face.lookX * 0.02, dy + b * 0.42);
      if (face.open > 0.7 && glint < cell * 0.75) eye *= 0.05;
      value = Math.max(value, eye);
    }
    return value;
  }

  function mark(canvas, options) {
    const o = Object.assign({ size: 160, eyes: true, mood: "idle" }, options);
    const size = o.size;
    const context = canvas.getContext("2d");
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const small = size < SMALL;
    const withEyes = o.eyes && !small;
    const pad = withEyes ? PAD : 0;
    const inner = Math.round(size * ratio);
    const margin = Math.round(inner * pad);
    canvas.width = canvas.height = inner + margin * 2;
    const full = size * (1 + 2 * pad);
    canvas.style.width = canvas.style.height = full + "px";
    if (pad) canvas.style.margin = -size * pad + "px";
    const ink = getComputedStyle(canvas).color;
    const cols = small ? 9 : Math.max(21, Math.round(size / 6)) | 1;
    const cell = inner / cols, cellNorm = 2 / cols;
    const fps = small ? 30 : 120;

    const n = cols * cols;
    const current = new Float32Array(n), ox = new Float32Array(n), oy = new Float32Array(n);
    const vx = new Float32Array(n), vy = new Float32Array(n);
    const points = [];
    for (let row = 0; row < cols; row++) {
      for (let col = 0; col < cols; col++) {
        const x = ((col + 0.5) / cols) * 2 - 1, y = ((row + 0.5) / cols) * 2 - 1;
        if (x * x + y * y <= 1) points.push({ x, y, r: Math.hypot(x, y), angle: Math.atan2(y, x), i: row * cols + col });
      }
    }
    const poke = { x: 0, y: 0, inside: false, burstAt: -1 };
    let lastBurst = -1, hovering = false;
    const face = { lookX: 0, lookY: 0, open: 1, happy: false };
    const start = performance.now();
    let nextBlink = start + 1800, blinkAt = -1, energy = 0, previous = 0;
    let ease = (k) => k;

    function updateFace(now) {
      const mood = hovering ? "happy" : o.mood;
      face.happy = mood === "happy";
      let tx = 0, ty = 0;
      if (mood === "thinking") { tx = Math.sin(now / 420) * 0.9; ty = -0.4; }
      else {
        const target = lookTarget();
        if (target) {
          const box = canvas.getBoundingClientRect();
          const dx = target.x - (box.left + box.width / 2), dy = target.y - (box.top + box.height / 2);
          const dist = Math.hypot(dx, dy) || 1, reach = Math.min(1, dist / 260);
          tx = (dx / dist) * reach; ty = (dy / dist) * reach;
        }
      }
      face.lookX += (tx - face.lookX) * ease(0.12);
      face.lookY += (ty - face.lookY) * ease(0.12);
      if (now > nextBlink && blinkAt < 0) blinkAt = now;
      face.open = 1;
      if (blinkAt >= 0) {
        const k = (now - blinkAt) / 220;
        face.open = k < 1 ? Math.abs(1 - 2 * k) : 1;
        if (k >= 1) { blinkAt = -1; nextBlink = now + 2600 + Math.random() * 3400; }
      }
    }

    const buckets = Array.from({ length: LEVELS }, () => []);
    function draw(now, settle) {
      const step = previous ? Math.min(0.05, Math.max(0, (now - previous) / 1000)) : 1 / 60;
      previous = Math.max(previous, now);
      ease = (k) => 1 - (1 - k) ** (step * 30);
      const t = (now - start) / 1000;
      energy += ((o.mood === "thinking" ? 1 : 0) - energy) * ease(0.06);
      if (withEyes && !motion.matches) updateFace(now);
      context.clearRect(0, 0, canvas.width, canvas.height);
      context.fillStyle = ink;
      const burst = withEyes && poke.burstAt !== lastBurst;
      lastBurst = poke.burstAt;
      if (withEyes && !settle) {
        for (const p of points) {
          const i = p.i;
          let fx = -140 * ox[i] - 13 * vx[i], fy = -140 * oy[i] - 13 * vy[i];
          const dx = p.x + ox[i] - poke.x, dy = p.y + oy[i] - poke.y;
          const dist = Math.hypot(dx, dy) || 0.001;
          if (poke.inside && dist < 0.5) { const push = 340 * (1 - dist / 0.5) ** 2; fx += (dx / dist) * push; fy += (dy / dist) * push; }
          if (burst) {
            const kick = 8 * Math.max(0.3, 1.3 - dist) * (0.7 + Math.random() * 0.6);
            vx[i] += (dx / dist) * kick + (Math.random() - 0.5) * 1.2;
            vy[i] += (dy / dist) * kick + (Math.random() - 0.5) * 1.2;
          }
          vx[i] += fx * step; vy[i] += fy * step;
          ox[i] += vx[i] * step; oy[i] += vy[i] * step;
        }
      }
      for (const b of buckets) b.length = 0;
      const follow = ease(0.38);
      for (const p of points) {
        let value = halo(p.r, p.angle, t, energy, small);
        if (withEyes) value = Math.max(value, eyes(p.x, p.y, face, cellNorm));
        const prev = current[p.i];
        const raw = settle ? value : prev + (value - prev) * follow;
        const next = Number.isFinite(raw) ? Math.min(1, Math.max(0, raw)) : 0;
        current[p.i] = next;
        const radius = cell * 0.47 * (0.08 + 0.92 * Math.sqrt(next));
        if (radius < 0.35) continue;
        buckets[Math.min(LEVELS - 1, Math.round(next * (LEVELS - 1)))].push(
          margin + (p.x + ox[p.i] + 1) * 0.5 * inner, margin + (p.y + oy[p.i] + 1) * 0.5 * inner, radius);
      }
      for (let level = 0; level < LEVELS; level++) {
        const dots = buckets[level];
        if (!dots.length) continue;
        context.globalAlpha = 0.3 + (0.7 * level) / (LEVELS - 1);
        context.beginPath();
        for (let k = 0; k < dots.length; k += 3) {
          context.moveTo(dots[k] + dots[k + 2], dots[k + 1]);
          context.arc(dots[k], dots[k + 1], dots[k + 2], 0, Math.PI * 2);
        }
        context.fill();
      }
      context.globalAlpha = 1;
    }

    const locate = (e) => {
      const box = canvas.getBoundingClientRect();
      poke.x = ((e.clientX - box.left) / box.width - 0.5) * 2 * (1 + 2 * pad);
      poke.y = ((e.clientY - box.top) / box.height - 0.5) * 2 * (1 + 2 * pad);
    };
    canvas.addEventListener("pointerenter", () => { hovering = true; poke.inside = true; });
    canvas.addEventListener("pointerleave", () => { hovering = false; poke.inside = false; });
    if (withEyes) {
      canvas.addEventListener("pointermove", locate);
      canvas.addEventListener("pointerdown", (e) => { locate(e); poke.burstAt = performance.now(); });
    }
    const stop = animate(canvas, fps, (now) => draw(now, false), () => draw(start + 1200, true));
    return { mood: (m) => { o.mood = m; }, stop };
  }

  /** A mark on a warm patch of aura: how every main screen begins. */
  function auraMark(host, size, opts) {
    const o = Object.assign({ palette: "soft", eyes: true }, opts);
    host.classList.add("aura-mark");
    host.style.width = host.style.height = size + "px";
    const glow = document.createElement("canvas");
    glow.className = "aura-mark__glow";
    glow.setAttribute("aria-hidden", "true");
    const dots = document.createElement("canvas");
    dots.className = "mark";
    dots.setAttribute("aria-hidden", "true");
    host.append(glow, dots);
    const a = aura(glow, { palette: o.palette, round: true, bands: 0, fps: 12, seed: o.seed || 0 });
    const m = mark(dots, { size, eyes: o.eyes });
    return {
      mood: (mood) => { m.mood(mood); a.set({ energy: mood === "thinking" ? 0.9 : 0 }); },
      stop: () => { a.stop(); m.stop(); },
    };
  }

  window.Paragraf = { aura, auraImage, mark, auraMark };
})();
