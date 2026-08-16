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

// ---------- homepage motion polish ----------
// Two tiers. Everything bails out for users who prefer reduced motion, and the
// cursor-driven effects additionally need a pointer that can hover -- but the
// scroll-linked ones must not, or a touch reader loses the hero fade and the
// progress rule for no reason.
const TS_MOTION = !matchMedia("(prefers-reduced-motion: reduce)").matches;

if (TS_MOTION && matchMedia("(hover: hover)").matches) {

  // soft glow that trails the cursor, lerped for a weighty feel
  const glow = document.createElement("div");
  glow.className = "cursor-glow";
  document.body.appendChild(glow);
  let gx = innerWidth / 2, gy = innerHeight / 2, tx = gx, ty = gy;
  addEventListener("pointermove", (e) => { tx = e.clientX; ty = e.clientY; glow.classList.add("on"); }, { passive: true });
  (function follow() {
    gx += (tx - gx) * 0.12; gy += (ty - gy) * 0.12;
    glow.style.transform = `translate(${gx}px, ${gy}px)`;
    requestAnimationFrame(follow);
  })();

  // magnetic hero buttons: nudge toward the cursor when hovered
  document.querySelectorAll(".hero-ctas .btn").forEach((btn) => {
    btn.classList.add("magnetic");
    btn.addEventListener("pointermove", (e) => {
      const r = btn.getBoundingClientRect();
      const mx = e.clientX - r.left - r.width / 2;
      const my = e.clientY - r.top - r.height / 2;
      btn.style.transform = `translate(${mx * 0.25}px, ${my * 0.35}px)`;
    });
    btn.addEventListener("pointerleave", () => { btn.style.transform = ""; });
  });

  // module cards: cursor spotlight (feeds the CSS --mx/--my) plus a subtle 3D tilt
  document.querySelectorAll(".mod-card").forEach((card) => {
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width;
      const py = (e.clientY - r.top) / r.height;
      card.style.setProperty("--mx", `${px * 100}%`);
      card.style.setProperty("--my", `${py * 100}%`);
      card.style.transform = `translateY(-4px) rotateX(${(0.5 - py) * 6}deg) rotateY(${(px - 0.5) * 6}deg)`;
    });
    card.addEventListener("pointerleave", () => { card.style.transform = ""; });
  });

  // stagger the bento cards in on scroll instead of all at once
  document.querySelectorAll(".bento .mod-card").forEach((card, i) => {
    card.style.transitionDelay = `${(i % 4) * 0.06}s`;
  });
}

// scroll-linked motion: no pointer required
if (TS_MOTION) {

  // Gentle parallax + fade on the hero as it scrolls away.
  //
  // Measured against the viewport, not a fixed pixel count. A hard 620px meant
  // the hero was fully transparent barely half a screen down, so on a tall
  // display the headline vanished while it still filled most of the window. The
  // fade now holds for the first third of a screen and finishes as the hero
  // leaves, and it eases rather than running linearly, so nothing snaps.
  const heroInner = document.querySelector(".hero-inner");
  if (heroInner) {
    let queued = false;
    const onScroll = () => {
      const h = innerHeight || 800;
      const t = Math.min(Math.max((scrollY - h * 0.34) / (h * 0.62), 0), 1);
      heroInner.style.transform = `translateY(${Math.min(scrollY, h) * 0.16}px)`;
      heroInner.style.opacity = `${1 - t * t}`;
      queued = false;
    };
    addEventListener("scroll", () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(onScroll);
    }, { passive: true });
  }

  // the hero writes itself in on load, one element at a time
  document.querySelectorAll(".hero-inner .wrap > *").forEach((el, i) => {
    el.classList.add("hero-rise");
    el.style.animationDelay = `${0.08 + i * 0.09}s`;
  });
}

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

// ---------- national model grid preview ----------
// Draws the real field, same interpolation the command center uses, over a
// CONUS bounding box in web mercator so it matches the live map. Each lead day
// is rendered once into its own offscreen canvas and then just blitted, so
// playing the week through is free after the first pass.
(() => {
  const canvas = document.getElementById("field-preview");
  if (!canvas || !window.TSField) return;

  // CONUS only. The full grid includes Alaska, Hawaii and Puerto Rico, but a box
  // wide enough for Alaska shrinks the lower 48 to a smudge; the caption says so.
  const W_LON = -125.2, E_LON = -66.4, S_LAT = 24.2, N_LAT = 49.6;
  const DAYS = 7;
  const HOLD = 1100;           // ms a frame stays up while playing

  const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 180) / 2));
  const Y0 = merc(N_LAT), Y1 = merc(S_LAT);

  const ctx = canvas.getContext("2d");
  const caption = document.getElementById("field-caption");
  const dayLabel = document.getElementById("field-day");
  const ticks = document.getElementById("field-ticks");
  const frames = new Array(DAYS).fill(null);   // day -> offscreen canvas
  const fields = new Array(DAYS).fill(null);   // day -> queryable TSField
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let day = 0, timer = null, spacing = 1, started = false, nearby = false;

  const dayName = (d) => {
    if (d === 0) return "Today";
    if (d === 1) return "Tomorrow";
    const t = new Date(); t.setDate(t.getDate() + d);
    return t.toLocaleDateString(undefined, { weekday: "long" });
  };

  const sizeCanvas = () => {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    // cap the backing store: this is a decorative preview, and every pixel is an
    // interpolation, so a retina 1200px card would be a million field lookups
    const w = Math.min(Math.round(canvas.clientWidth * dpr), 900);
    canvas.width = w;
    canvas.height = Math.round(w * (Y0 - Y1) / ((E_LON - W_LON) * Math.PI / 180));
  };

  // render one day's grid into an offscreen canvas at the current size.
  // the built field is kept, not just the pixels, so hovering can ask it for
  // the value at an arbitrary coordinate rather than sampling the image back.
  function renderFrame(data, d) {
    const f = TSField.create();
    f.build(data);
    fields[d] = f;
    spacing = data.spacing_deg || 1;
    const w = canvas.width, h = canvas.height;
    const off = document.createElement("canvas");
    off.width = w; off.height = h;
    const octx = off.getContext("2d");
    const img = octx.createImageData(w, h);
    const px = img.data;

    // separable like the map tiles: longitude depends only on x, latitude only on y
    const lons = new Float64Array(w), lats = new Float64Array(h);
    for (let i = 0; i < w; i++) lons[i] = W_LON + ((i + 0.5) / w) * (E_LON - W_LON);
    for (let j = 0; j < h; j++) {
      const y = Y0 + ((j + 0.5) / h) * (Y1 - Y0);
      lats[j] = (2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180 / Math.PI;
    }

    for (let j = 0; j < h; j++) {
      const lat = lats[j];
      for (let i = 0; i < w; i++) {
        const v = f.value(lat, lons[i]);
        if (v === null) continue;                 // offshore stays transparent
        const [r, g, b] = TSField.rampRGB(v);
        const o = (j * w + i) * 4;
        px[o] = r; px[o + 1] = g; px[o + 2] = b; px[o + 3] = 235;
      }
    }
    octx.putImageData(img, 0, 0);
    return off;
  }

  const paint = () => {
    const f = frames[day];
    if (!f) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(f, 0, 0);
    dayLabel.textContent = dayName(day);
    [...ticks.children].forEach((b, i) => b.classList.toggle("on", i === day));
  };

  const show = (d) => { day = d; paint(); };

  const stop = () => { if (timer) { clearInterval(timer); timer = null; } };
  const play = () => {
    if (reduced || timer) return;
    timer = setInterval(() => {
      // only advance onto days that are actually loaded, so a slow fetch shows a
      // held frame rather than a blank one
      let next = (day + 1) % DAYS;
      if (!frames[next]) next = 0;
      show(next);
    }, HOLD);
  };

  async function load() {
    sizeCanvas();
    const first = await TS.fetchJSON("/api/national/grid?layer=composite&day=0");
    if (first.error || !first.cells || !first.cells.length) {
      caption.textContent = "The national grid is rebuilding; the live map has the current field.";
      return;
    }
    frames[0] = renderFrame(first, 0);
    show(0);
    caption.textContent =
      `${first.cells.length.toLocaleString()} scored points at ${spacing}° spacing, ` +
      `interpolated to every pixel. Click a day to scrub the outlook.`;

    if (nearby) loadRest();
  }

  // the remaining days trickle in; each is a separate small response
  let restStarted = false;
  async function loadRest() {
    if (restStarted || !frames[0]) return;
    restStarted = true;
    for (let d = 1; d < DAYS; d++) {
      const res = await TS.fetchJSON(`/api/national/grid?layer=composite&day=${d}`);
      if (res.error || !res.cells || !res.cells.length) break;
      frames[d] = renderFrame(res, d);
      ticks.children[d].disabled = false;
    }
    play();
  }

  // build the day buttons up front so the control does not pop in later
  for (let d = 0; d < DAYS; d++) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = d === 0 ? "Today" : `+${d}d`;
    b.disabled = d !== 0;
    b.addEventListener("click", () => { stop(); if (frames[d]) show(d); });
    ticks.appendChild(b);
  }

  // The first frame is not gated on visibility. Day 0 is one small cached
  // request and a few hundred milliseconds of interpolation, and gating it on an
  // IntersectionObserver meant the field could still be empty when someone
  // scrolled straight to it. The observer below only governs the animation, so a
  // reader who never reaches this section still costs six fewer requests.
  const start = () => { if (!started) { started = true; load(); } };
  if (document.readyState === "complete") setTimeout(start, 0);
  else addEventListener("load", start, { once: true });

  const setNearby = (v) => {
    if (v === nearby) return;
    nearby = v;
    if (v) { start(); loadRest(); play(); } else { stop(); }
  };

  new IntersectionObserver((entries) => {
    for (const e of entries) setNearby(e.isIntersecting);
  }, { threshold: 0, rootMargin: "400px 0px" }).observe(canvas);

  // A plain scroll check backs the observer up. Inside an iframe, and in a few
  // headless setups, the observer has been seen to never deliver an intersecting
  // entry, which leaves the six outlook days unloaded and their ticks disabled.
  addEventListener("scroll", () => {
    const r = canvas.getBoundingClientRect();
    setNearby(r.bottom > -400 && r.top < innerHeight + 400);
  }, { passive: true });

  // Hover readout. The claim this section makes is that every coordinate in the
  // country resolves, and the cheapest way to prove it is to let someone point
  // at an arbitrary pixel and get the number back. This asks the field object
  // for that exact latitude and longitude rather than sampling the rendered
  // image, so what appears is the model's value, not a colour read backwards.
  if (matchMedia("(hover: hover)").matches) {
    const tip = document.createElement("div");
    tip.className = "field-tip";
    tip.hidden = true;
    canvas.parentElement.appendChild(tip);

    const bandOf = (v) => v < 25 ? "Low" : v < 50 ? "Moderate" : v < 75 ? "High" : "Extreme";

    canvas.addEventListener("pointermove", (e) => {
      const f = fields[day];
      if (!f) return;
      const cw = canvas.clientWidth, ch = canvas.clientHeight;
      const lon = W_LON + (e.offsetX / cw) * (E_LON - W_LON);
      const lat = (2 * Math.atan(Math.exp(Y0 + (e.offsetY / ch) * (Y1 - Y0))) - Math.PI / 2) * 180 / Math.PI;
      const v = f.value(lat, lon);
      if (v === null) { tip.hidden = true; return; }   // offshore
      const label = bandOf(v);
      tip.innerHTML = `<i style="background:${TSField.rampColor(v)}"></i>`
        + `<b>${v.toFixed(0)}</b> <span>${label}</span>`
        + `<em>${lat.toFixed(1)}&deg;N ${Math.abs(lon).toFixed(1)}&deg;W</em>`;
      tip.hidden = false;
      // keep the card from clipping it near the edges
      tip.style.left = `${Math.min(Math.max(e.offsetX, 70), cw - 70)}px`;
      tip.style.top = `${Math.max(e.offsetY - 14, 44)}px`;
    }, { passive: true });
    canvas.addEventListener("pointerleave", () => { tip.hidden = true; }, { passive: true });
  }

  // re-render at the new size on a real width change, debounced
  let rw = canvas.clientWidth, rt = null;
  addEventListener("resize", () => {
    if (Math.abs(canvas.clientWidth - rw) < 40) return;
    rw = canvas.clientWidth;
    clearTimeout(rt);
    rt = setTimeout(() => { started = false; restStarted = false; frames.fill(null); fields.fill(null); stop(); load(); }, 300);
  }, { passive: true });
})();

// ---------- grid coverage stat + validation numbers ----------
(async () => {
  const el = document.getElementById("stat-grid");
  if (el) {
    const s = await TS.fetchJSON("/api/national/status");
    if (!s.error && s.scored_points) {
      const io = new IntersectionObserver((entries, obs) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          TS.countUp(e.target, s.scored_points);
          obs.disconnect();
        }
      }, { threshold: 0.4 });
      io.observe(el);
    }
  }

  const rows = document.getElementById("valid-rows");
  if (!rows) return;
  const v = await TS.fetchJSON("/api/validation");
  const models = (v && v.models || []).filter(m => m.available !== false && m.roc_auc != null);
  if (!models.length) {
    rows.innerHTML = '<p class="small muted">Validation runs with training; the report page ' +
      'has the current numbers.</p>';
    return;
  }
  rows.innerHTML = models.map(m => `
    <div class="valid-row">
      <span class="nm">${m.name}</span>
      <span class="mt">AUC <b>${m.roc_auc.toFixed(3)}</b></span>
      <span class="mt">Brier skill <b>${m.brier_skill != null ? m.brier_skill.toFixed(3) : "n/a"}</b></span>
    </div>`).join("");
})();
