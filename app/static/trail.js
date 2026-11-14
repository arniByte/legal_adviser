/* A trail behind the mouse on the empty background: small figures in PSB colours bloom out of a
 * blur and fade. They are drawn once on a canvas, then shown as plain images — no repainting.
 * Mouse only, empty background only, nothing with prefers-reduced-motion. */
(() => {
  "use strict";
  const STEP = 90;          // pixels between neighbouring figures
  const MIN_GAP_MS = 55;
  const LIFE_MS = 950;
  const MAX_ALIVE = 14;

  const fine = window.matchMedia("(hover: hover) and (pointer: fine)");
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (!fine.matches) return;

  const VIOLET = [80, 70, 216], INDIGO = [34, 30, 118], ORANGE = [242, 107, 33], PEACH = [255, 190, 150], LILAC = [198, 193, 255];
  const rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  function random(seed) { let s = seed * 9301 + 49297; return () => (s = (s * 9301 + 49297) % 233280) / 233280; }

  // Each figure draws into a size×size square centred at (size/2, size/2).
  const FIGURES = [
    // halftone disc: the mascot's dots, denser towards the rim
    (c, n, rnd) => {
      const R = n / 2, cols = 15, cell = n / cols;
      for (let i = 0; i < cols; i++) for (let j = 0; j < cols; j++) {
        const x = (i + 0.5) * cell, y = (j + 0.5) * cell, r = Math.hypot(x - R, y - R) / R;
        if (r > 1) continue;
        const v = Math.exp(-(((r - 0.72) / 0.22) ** 2)) * (0.6 + rnd() * 0.4);
        c.fillStyle = rgba(r > 0.6 ? VIOLET : LILAC, 0.25 + v * 0.75);
        c.beginPath(); c.arc(x, y, cell * 0.46 * (0.15 + 0.85 * Math.sqrt(v)), 0, Math.PI * 2); c.fill();
      }
    },
    // two leaning parallelograms: violet and orange, glass-like gradients
    (c, n) => {
      const g1 = c.createLinearGradient(n * 0.2, n * 0.2, n * 0.55, n * 0.8);
      g1.addColorStop(0, rgba(LILAC, 0.95)); g1.addColorStop(1, rgba(VIOLET, 0.95));
      c.fillStyle = g1;
      c.beginPath(); c.moveTo(n * 0.36, n * 0.18); c.lineTo(n * 0.56, n * 0.18); c.lineTo(n * 0.4, n * 0.82); c.lineTo(n * 0.2, n * 0.82); c.closePath(); c.fill();
      const g2 = c.createLinearGradient(n * 0.5, n * 0.2, n * 0.8, n * 0.8);
      g2.addColorStop(0, rgba(PEACH, 0.95)); g2.addColorStop(1, rgba(ORANGE, 0.95));
      c.fillStyle = g2;
      c.beginPath(); c.moveTo(n * 0.6, n * 0.18); c.lineTo(n * 0.8, n * 0.18); c.lineTo(n * 0.64, n * 0.82); c.lineTo(n * 0.44, n * 0.82); c.closePath(); c.fill();
    },
    // orbits: thin rings with a bead on each
    (c, n, rnd) => {
      const R = n / 2; c.translate(R, R);
      for (let k = 0; k < 4; k++) {
        const r = R * (0.32 + k * 0.17);
        c.strokeStyle = rgba(k % 2 ? ORANGE : VIOLET, 0.55 - k * 0.08); c.lineWidth = n * 0.012;
        c.beginPath(); c.ellipse(0, 0, r, r * 0.42, -0.5 + k * 0.35, 0, Math.PI * 2); c.stroke();
        const a = rnd() * Math.PI * 2;
        c.save(); c.rotate(-0.5 + k * 0.35);
        c.fillStyle = rgba(k % 2 ? ORANGE : INDIGO, 0.95);
        c.beginPath(); c.arc(Math.cos(a) * r, Math.sin(a) * r * 0.42, n * 0.03, 0, Math.PI * 2); c.fill();
        c.restore();
      }
    },
    // sun: rays from an orange core into lilac
    (c, n, rnd) => {
      const R = n / 2; c.translate(R, R); c.lineCap = "round";
      const count = 48;
      for (let i = 0; i < count; i++) {
        const a = (i / count) * Math.PI * 2, len = R * (0.5 + rnd() * 0.45);
        const g = c.createLinearGradient(0, 0, Math.cos(a) * len, Math.sin(a) * len);
        g.addColorStop(0, rgba(ORANGE, 0.95)); g.addColorStop(0.6, rgba(PEACH, 0.7)); g.addColorStop(1, rgba(LILAC, 0));
        c.strokeStyle = g; c.lineWidth = n * 0.018;
        c.beginPath(); c.moveTo(Math.cos(a) * R * 0.12, Math.sin(a) * R * 0.12); c.lineTo(Math.cos(a) * len, Math.sin(a) * len); c.stroke();
      }
      const core = c.createRadialGradient(0, 0, 0, 0, 0, R * 0.2);
      core.addColorStop(0, rgba([255, 240, 225], 1)); core.addColorStop(1, rgba(ORANGE, 0.9));
      c.fillStyle = core; c.beginPath(); c.arc(0, 0, R * 0.18, 0, Math.PI * 2); c.fill();
    },
    // halftone wave: a band of dots, violet into orange
    (c, n) => {
      const cols = 14, cell = n / cols;
      for (let i = 0; i < cols; i++) for (let j = 0; j < cols; j++) {
        const x = (i + 0.5) * cell, y = (j + 0.5) * cell;
        const wave = n / 2 + Math.sin((x / n) * Math.PI * 2) * n * 0.16;
        const v = Math.exp(-(((y - wave) / (n * 0.16)) ** 2)) * (1 - Math.abs(x / n - 0.5) * 1.4);
        if (v < 0.05) continue;
        const k = x / n;
        c.fillStyle = rgba([VIOLET[0] + (ORANGE[0] - VIOLET[0]) * k, VIOLET[1] + (ORANGE[1] - VIOLET[1]) * k, VIOLET[2] + (ORANGE[2] - VIOLET[2]) * k], 0.9);
        c.beginPath(); c.arc(x, y, cell * 0.45 * Math.sqrt(v), 0, Math.PI * 2); c.fill();
      }
    },
    // a soft aura bead with a glass highlight
    (c, n) => {
      const R = n / 2;
      const g = c.createRadialGradient(R * 0.7, R * 0.65, R * 0.05, R, R, R * 0.62);
      g.addColorStop(0, "rgba(255,255,255,0.95)"); g.addColorStop(0.35, rgba(LILAC, 0.9)); g.addColorStop(0.75, rgba(VIOLET, 0.9)); g.addColorStop(1, rgba(ORANGE, 0));
      c.fillStyle = g; c.beginPath(); c.arc(R, R, R * 0.62, 0, Math.PI * 2); c.fill();
    },
  ];

  let images = null;
  function makeImages() {
    const out = [];
    const size = 160;
    FIGURES.forEach((draw, k) => {
      for (let variant = 0; variant < 2; variant++) {
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = size;
        const c = canvas.getContext("2d");
        draw(c, size, random(k * 7 + variant + 1));
        out.push(canvas.toDataURL());
      }
    });
    return out;
  }

  const layer = document.createElement("div");
  layer.className = "trail";
  layer.setAttribute("aria-hidden", "true");
  document.body.prepend(layer);

  const isBackground = (t) => t instanceof Element && (t === document.body || t === document.documentElement
    || t.classList.contains("page") || t.classList.contains("home") || t.classList.contains("thread"));

  let next = 0, alive = 0, lastX = 0, lastY = 0, lastAt = 0, travelled = 0;
  function spawn(x, y) {
    images = images || makeImages();
    if (alive >= MAX_ALIVE) return;
    const img = document.createElement("img");
    img.src = images[next++ % images.length];
    img.alt = "";
    const size = 70 + Math.random() * 50;
    const turn = (Math.random() - 0.5) * 40;
    img.style.cssText = `left:${x - size / 2}px;top:${y - size / 2}px;width:${size}px;height:${size}px`;
    layer.append(img);
    alive++;
    const a = img.animate([
      { opacity: 0, transform: `scale(.35) rotate(${turn - 12}deg)`, filter: "blur(6px)" },
      { opacity: 0.9, transform: `scale(1) rotate(${turn}deg)`, filter: "blur(0)", offset: 0.18 },
      { opacity: 0.9, transform: `scale(1.02) rotate(${turn + 3}deg)`, filter: "blur(0)", offset: 0.6 },
      { opacity: 0, transform: `scale(.9) rotate(${turn + 8}deg)`, filter: "blur(8px)" },
    ], { duration: LIFE_MS, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" });
    a.onfinish = () => { img.remove(); alive--; };
  }

  window.addEventListener("pointermove", (e) => {
    if (still.matches || e.pointerType !== "mouse") return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    if (!isBackground(e.target)) { travelled = 0; return; }
    travelled += Math.hypot(dx, dy);
    const now = performance.now();
    if (travelled < STEP || now - lastAt < MIN_GAP_MS) return;
    travelled = 0; lastAt = now;
    spawn(e.clientX, e.clientY);
  }, { passive: true });
})();
