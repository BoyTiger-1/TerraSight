// The national model grid as a continuous surface.
//
// The grid is ~800 scored points, not a picture, and the two obvious ways to
// draw it are both wrong. Discs (which is what a heatmap library gives you) are
// sized in screen pixels, so they shrink against the ground as you zoom in and
// overlap into colours no point actually holds as you zoom out. Bilinear
// interpolation would be the textbook answer but is not available here: despite
// `spacing_deg`, the grid is not a lattice. CONUS is two interleaved 1-degree
// sub-lattices, Alaska sits on 2.5, and Hawaii and Puerto Rico are lone points,
// so nearest-neighbour distance ranges from 0.6 to 2.5 degrees.
//
// So: local Shepard interpolation with a per-node support radius. Every pixel
// asks the field what the model says at that pixel's own centre, which is a
// function of geography alone and therefore identical at every zoom.
//
// Shared by the command center map and the homepage preview.

(() => {
  // widest a node's influence can reach, in degrees. sized so Alaska's
  // 2.5-degree lattice still joins up; it also sets the spatial hash cell.
  const MAX_SUPPORT = 2.7;

  // 0-100 to the map ramp. Same stops as dashboard.js's scoreColor(), so the
  // smooth field and the crisp cells are one palette and agree at the stops.
  const RAMP = [[0, 29, 79, 92], [25, 47, 125, 140], [40, 53, 179, 156],
                [55, 217, 161, 59], [70, 224, 112, 58], [85, 224, 82, 82]];

  // rounded here rather than at the call sites: writing a float into a canvas
  // Uint8ClampedArray rounds half-to-even while Math.round does not, which left
  // the field and the cells one unit apart on some values for no good reason.
  function rampRGB(v) {
    if (v <= RAMP[0][0]) return RAMP[0].slice(1);
    for (let i = 1; i < RAMP.length; i++) {
      if (v <= RAMP[i][0]) {
        const [t0, r0, g0, b0] = RAMP[i - 1], [t1, r1, g1, b1] = RAMP[i];
        const f = (v - t0) / (t1 - t0);
        return [Math.round(r0 + (r1 - r0) * f),
                Math.round(g0 + (g1 - g0) * f),
                Math.round(b0 + (b1 - b0) * f)];
      }
    }
    return RAMP[RAMP.length - 1].slice(1);
  }

  function rampColor(v) {
    const [r, g, b] = rampRGB(v);
    return `rgb(${r},${g},${b})`;
  }

  // a field is a closure over its own nodes so a page can hold more than one
  // (the homepage keeps all seven forecast days indexed at once)
  function create() {
    let nodes = [], hash = null, candKey = null, candList = null;

    function build(data) {
      const spacing = data.spacing_deg || 1;
      nodes = (data.cells || []).map(c => ({ lat: c.lat, lon: c.lon, v: c.v, r: 0 }));
      candKey = null; candList = null;

      // each node's support is set by how far away its nearest neighbour
      // actually is, so a 1-degree CONUS node blends with its neighbours while a
      // 2.5-degree Alaska node reaches far enough to find its own. A node whose
      // nearest neighbour is beyond MAX_SUPPORT is an island with nothing to
      // blend with, so it covers its own cell rather than a spurious disc of ocean.
      for (let i = 0; i < nodes.length; i++) {
        const a = nodes[i];
        let nn = Infinity;
        for (let j = 0; j < nodes.length; j++) {
          if (i === j) continue;
          const b = nodes[j];
          const d = (a.lat - b.lat) ** 2 + (a.lon - b.lon) ** 2;
          if (d < nn) nn = d;
        }
        nn = Math.sqrt(nn);
        a.r = nn > MAX_SUPPORT ? spacing * 0.5
            : Math.max(Math.min(nn * 1.35, MAX_SUPPORT), spacing * 0.8);
      }

      // spatial hash on MAX_SUPPORT-sized buckets: a lookup only has to scan the
      // 3x3 block around the query, which keeps the per-pixel cost flat whatever
      // the grid size.
      hash = new Map();
      nodes.forEach((n) => {
        const key = `${Math.floor(n.lat / MAX_SUPPORT)},${Math.floor(n.lon / MAX_SUPPORT)}`;
        let bucket = hash.get(key);
        if (!bucket) { bucket = []; hash.set(key, bucket); }
        bucket.push(n);
      });
      return nodes.length;
    }

    // a bucket is MAX_SUPPORT degrees wide, which is hundreds of pixels at the
    // zooms people actually use, so consecutive pixels along a scanline nearly
    // always want the same nine buckets. caching the merged candidate list turns
    // the common case into a single string compare.
    function candidatesFor(lat, lon) {
      const bj = Math.floor(lat / MAX_SUPPORT), bi = Math.floor(lon / MAX_SUPPORT);
      const key = `${bj},${bi}`;
      if (key === candKey) return candList;
      const list = [];
      for (let dj = -1; dj <= 1; dj++) {
        for (let di = -1; di <= 1; di++) {
          const bucket = hash.get(`${bj + dj},${bi + di}`);
          if (bucket) for (let k = 0; k < bucket.length; k++) list.push(bucket[k]);
        }
      }
      candKey = key; candList = list;
      return list;
    }

    // the model's value at an arbitrary point, or null where nothing is in range
    function value(lat, lon) {
      if (!hash) return null;
      const cands = candidatesFor(lat, lon);
      let sum = 0, wsum = 0;
      for (let k = 0; k < cands.length; k++) {
        const n = cands[k];
        const dlat = lat - n.lat, dlon = lon - n.lon;
        const d = Math.sqrt(dlat * dlat + dlon * dlon);
        if (d >= n.r) continue;
        if (d <= 1e-9) return n.v;        // standing on a node: report it exactly
        // Shepard weight, first power on purpose. Squaring it -- the textbook
        // default -- makes the nearest node dominate so hard that the value sits
        // flat at that node's score for the first fifth of the gap and then
        // lurches, which renders as a square plateau around every point: the
        // country breaks into blocks. First power still goes infinite at the node
        // (so the field is exact there) but the profile between two is near-linear.
        const w = (n.r - d) / (n.r * d);
        sum += w * n.v; wsum += w;
      }
      return wsum > 0 ? sum / wsum : null;
    }

    return { build, value, get size() { return nodes.length; } };
  }

  window.TSField = { create, rampRGB, rampColor, MAX_SUPPORT };
})();
