// homepage: the flow-field hero animation plus live stat counts.
// particles drift along a pseudo-wind field, capped and pre-warmed for 60fps.

(() => {
  const canvas = document.getElementById("hero-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  let W, H, particles;
  const COUNT = innerWidth < 700 ? 350 : 900; // capped, phones get fewer

  const resize = () => {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  // cheap value-noise flow field, angle varies smoothly over space and time
  const angle = (x, y, t) =>
    Math.sin(x * 0.0016 + t * 0.00016) * 1.6 +
    Math.cos(y * 0.0021 - t * 0.00011) * 1.4 +
    Math.sin((x + y) * 0.0007) * 0.8;

  const spawn = () => ({
    x: Math.random() * W, y: Math.random() * H,
    life: 60 + Math.random() * 160,
    speed: 0.35 + Math.random() * 0.75,
  });

  const init = () => {
    resize();
    particles = Array.from({ length: COUNT }, spawn);
    // pre-warm so the first visible frame already has trails
    ctx.fillStyle = "#0b0f15";
    ctx.fillRect(0, 0, W, H);
  };

  let last = 0;
  const frame = (t) => {
    // fade instead of clear, that is what draws the streamlines
    ctx.fillStyle = "rgba(11, 15, 21, 0.075)";
    ctx.fillRect(0, 0, W, H);
    ctx.lineWidth = 1;

    for (const p of particles) {
      const a = angle(p.x, p.y, t);
      const nx = p.x + Math.cos(a) * p.speed * 2.2;
      const ny = p.y + Math.sin(a) * p.speed * 2.2;
      // teal streams with a few blue ones mixed in
      ctx.strokeStyle = (p.speed > 0.85)
        ? "rgba(53, 179, 156, 0.30)" : "rgba(91, 147, 217, 0.14)";
      ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(nx, ny); ctx.stroke();
      p.x = nx; p.y = ny; p.life--;
      if (p.life <= 0 || p.x < -10 || p.x > W + 10 || p.y < -10 || p.y > H + 10)
        Object.assign(p, spawn());
    }
    if (!reduced) requestAnimationFrame(frame);
  };

  init();
  addEventListener("resize", () => { resize(); }, { passive: true });
  if (reduced) {
    // static single pass for reduced motion users
    for (let i = 0; i < 40; i++) frame(i * 16);
  } else {
    requestAnimationFrame(frame);
  }
})();

// live stats strip fed by the overview endpoint
(async () => {
  const data = await TS.fetchJSON("/api/live/overview");
  if (data.error || !data.counts) return;
  const pairs = [["stat-events", data.counts.events], ["stat-quakes", data.counts.quakes],
                 ["stat-storms", data.counts.storms]];
  const seen = new Set();
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting || seen.has(e.target.id)) continue;
      seen.add(e.target.id);
      const v = pairs.find(p => p[0] === e.target.id);
      if (v) TS.countUp(e.target, v[1]);
    }
  }, { threshold: 0.4 });
  pairs.forEach(([id]) => { const el = document.getElementById(id); if (el) io.observe(el); });
})();
