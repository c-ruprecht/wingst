/* Windanimation auf der Karte: stündliches Windfeld (ICON über Open-Meteo, ~3 km Gitter) als wandernde Partikel
   wie bei Windfinder, dazu eine leichte Farbfläche nach Windstärke und ein Zeitregler für die nächsten 7 Tage.
   Das Feld ist das unkorrigierte Modell; die Spot-Chips unter dem Regler zeigen dazu die lokale Chance je Spot.

   WingstWind.init(map, { spotInfo(tIso) -> [{name, kn, p}], thresholds: [14, 18] })  – einmal nach dem Laden der Karte
   Bedienung über #btnWind (an/aus), #windBar (Regler, Play) – siehe index.html */
const WingstWind = (() => {
  const LATS = [], LONS = [];
  for (let la = 54.26; la <= 54.561; la += 0.03) LATS.push(+la.toFixed(3));
  for (let lo = 12.30; lo <= 12.981; lo += 0.05) LONS.push(+lo.toFixed(3));
  const NY = LATS.length, NX = LONS.length;
  const COLORS = [[0, "#9ecae1"], [6, "#4fb3d9"], [9, "#3cbf8a"], [12, "#9bd34a"], [15, "#f2d33b"],
                  [18, "#f59a2c"], [21, "#e8503a"], [25, "#b23a8c"], [30, "#6b2a8f"]];
  const colorOf = kn => { let c = COLORS[0][1]; for (const [v, k] of COLORS) if (kn >= v) c = k; return c; };
  const reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  let map, opts, field = null, fetchedAt = 0, on = false, playing = false;
  let grid = null, corr = true, mode = "thr";            // Satellitenkorrektur, Farbe: "thr" = Schwellen, "kn" = Knoten
  const THR = () => (opts && opts.thresholds) || [14, 18];
  let hour = 0, frac = 0, raf = null, lastStep = 0;
  let bg, fg, bctx, fctx, parts = [], moving = false;

  /* ---------- Daten ---------- */
  async function load() {
    if (field && Date.now() - fetchedAt < 3600e3) return field;
    const lat = [], lon = [];
    for (const la of LATS) for (const lo of LONS) { lat.push(la); lon.push(lo); }
    const p = new URLSearchParams({ latitude: lat.join(","), longitude: lon.join(","), models: "icon_seamless",
      hourly: "wind_speed_10m,wind_direction_10m", wind_speed_unit: "kn", forecast_days: "7", timezone: "Europe/Berlin" });
    const r = await fetch("https://api.open-meteo.com/v1/forecast?" + p);
    if (!r.ok) throw new Error("Open-Meteo " + r.status);
    const js = await r.json();
    const arr = Array.isArray(js) ? js : [js];
    const time = arr[0].hourly.time, T = time.length;
    const u = new Float32Array(T * NY * NX), v = new Float32Array(T * NY * NX), s = new Float32Array(T * NY * NX);
    arr.forEach((o, k) => {
      const ws = o.hourly.wind_speed_10m, wd = o.hourly.wind_direction_10m;
      for (let t = 0; t < T; t++) {
        const i = t * NY * NX + k, a = (wd[t] ?? 0) * Math.PI / 180, sp = ws[t] ?? 0;
        u[i] = -sp * Math.sin(a); v[i] = -sp * Math.cos(a); s[i] = sp;     // Richtung = woher der Wind kommt
      }
    });
    field = { time, T, raw: { u, v, s } }; fetchedAt = Date.now();
    if (grid === null) grid = await fetch("model/wind_grid.json").then(r => r.ok ? r.json() : false).catch(() => false);
    field.cor = grid ? correct(field.raw, T) : null;
    pick();
    const now = new Date(), key = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}T${String(now.getHours()).padStart(2, "0")}:00`;
    hour = Math.max(0, time.indexOf(key)); frac = 0;
    return field;
  }

  /* Satellitenfaktor je Gitterpunkt und Windrichtung (training/sar_grid_factors.py): nur über Wasser, sonst 1 */
  function correct(raw, T) {
    const u = new Float32Array(raw.u), v = new Float32Array(raw.v), s = new Float32Array(raw.s);
    for (let y = 0; y < NY; y++) for (let x = 0; x < NX; x++) {
      const gy = grid.lats.indexOf(LATS[y]), gx = grid.lons.indexOf(LONS[x]);
      if (gy < 0 || gx < 0) continue;
      const all = grid.factor_all[gy][gx];
      for (let t = 0; t < T; t++) {
        const i = t * NY * NX + y * NX + x;
        const dir = (Math.atan2(-raw.u[i], -raw.v[i]) * 180 / Math.PI + 360) % 360;
        const f = grid.factor[Math.floor(((dir + 22.5) % 360) / 45)][gy][gx] ?? all;
        if (f == null) continue;
        u[i] *= f; v[i] *= f; s[i] *= f;
      }
    }
    return { u, v, s };
  }
  const isWater = (y, x) => {
    if (!grid) return true;
    const gy = grid.lats.indexOf(LATS[y]), gx = grid.lons.indexOf(LONS[x]);
    const w = gy < 0 || gx < 0 ? null : grid.water[gy][gx];
    return w == null ? true : w >= 0.25;                  // außerhalb der Satellitenkarte: offene See, zählt als Wasser
  };
  function pick() { const src = corr && field.cor ? field.cor : field.raw; field.u = src.u; field.v = src.v; field.s = src.s; }
  function thrColor(kn) { const [a, b] = THR(); return kn >= b ? "#0f7a3c" : kn >= a ? "#39b86b" : null; }

  /* bilinear im Gitter, linear zwischen zwei Stunden */
  function sample(lon, lat) {
    const fx = (lon - LONS[0]) / (LONS[1] - LONS[0]), fy = (lat - LATS[0]) / (LATS[1] - LATS[0]);
    if (fx < 0 || fy < 0 || fx > NX - 1 || fy > NY - 1) return null;
    const x0 = Math.min(NX - 2, Math.floor(fx)), y0 = Math.min(NY - 2, Math.floor(fy)), dx = fx - x0, dy = fy - y0;
    const t1 = Math.min(field.T - 1, hour + 1);
    const at = (arr, t) => {
      const b = t * NY * NX;
      const a00 = arr[b + y0 * NX + x0], a10 = arr[b + y0 * NX + x0 + 1], a01 = arr[b + (y0 + 1) * NX + x0], a11 = arr[b + (y0 + 1) * NX + x0 + 1];
      return (a00 * (1 - dx) + a10 * dx) * (1 - dy) + (a01 * (1 - dx) + a11 * dx) * dy;
    };
    const mix = arr => at(arr, hour) * (1 - frac) + at(arr, t1) * frac;
    return [mix(field.u), mix(field.v), mix(field.s)];
  }

  /* ---------- Zeichnen ---------- */
  function size() {
    const c = map.getCanvas(), dpr = window.devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
    for (const cv of [bg, fg]) { cv.width = w * dpr; cv.height = h * dpr; cv.style.width = w + "px"; cv.style.height = h + "px"; }
    bctx.setTransform(dpr, 0, 0, dpr, 0, 0); fctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function drawBg() {
    const w = bg.clientWidth, h = bg.clientHeight;
    bctx.clearRect(0, 0, w, h);
    if (!field) return;
    const t1 = Math.min(field.T - 1, hour + 1);
    bctx.globalAlpha = mode === "thr" ? 0.42 : 0.28;
    const hx = (LONS[1] - LONS[0]) / 2, hy = (LATS[1] - LATS[0]) / 2;
    for (let y = 0; y < NY; y++) for (let x = 0; x < NX; x++) {
      if (mode === "thr" && !isWater(y, x)) continue;      // Schwellen nur auf dem Wasser zeigen
      const k = y * NX + x, s = field.s[hour * NY * NX + k] * (1 - frac) + field.s[t1 * NY * NX + k] * frac;
      const c = mode === "thr" ? thrColor(s) : colorOf(s);
      if (!c) continue;
      const a = map.project([LONS[x] - hx, LATS[y] + hy]), b = map.project([LONS[x] + hx, LATS[y] - hy]);
      bctx.fillStyle = c;
      bctx.fillRect(a.x, a.y, b.x - a.x + 1, b.y - a.y + 1);
    }
    bctx.globalAlpha = 1;
  }

  function seed(p) {
    const w = fg.clientWidth, h = fg.clientHeight;
    p.x = Math.random() * w; p.y = Math.random() * h; p.age = Math.floor(Math.random() * 80);
    return p;
  }
  function reseed() {
    const w = fg.clientWidth, h = fg.clientHeight;
    const n = Math.round(Math.min(2500, w * h / 260));
    parts = Array.from({ length: n }, () => seed({}));
    fctx.clearRect(0, 0, w, h);
  }

  function frame(ts) {
    raf = on ? requestAnimationFrame(frame) : null;
    if (!field || moving) return;
    if (playing && ts - lastStep > 40) {                 // ~1 Modellstunde pro Sekunde, weich überblendet
      frac += 0.04; lastStep = ts;
      if (frac >= 1) { frac = 0; hour = hour + 1 >= field.T ? 0 : hour + 1; }
      syncBar(); drawBg();
    }
    const w = fg.clientWidth, h = fg.clientHeight;
    fctx.globalCompositeOperation = "destination-in";
    fctx.fillStyle = "rgba(0,0,0,0.9)"; fctx.fillRect(0, 0, w, h);
    fctx.globalCompositeOperation = "source-over";
    fctx.lineWidth = 1.6; fctx.lineCap = "round";
    const z = map.getZoom(), k = 0.1 * Math.pow(2, (z - 9.6) * 0.5);        // Pixel je Knoten und Bild: 15 kn ≈ 1,5 px
    for (const p of parts) {
      if (--p.age < 0 || p.x < 0 || p.y < 0 || p.x > w || p.y > h) { seed(p); p.age = 60 + Math.random() * 60; continue; }
      const ll = map.unproject([p.x, p.y]), f = sample(ll.lng, ll.lat);
      if (!f) { seed(p); continue; }
      const nx = p.x + f[0] * k, ny = p.y - f[1] * k;
      fctx.strokeStyle = mode === "thr" ? (thrColor(f[2]) || "rgba(40,60,70,.55)") : colorOf(f[2]);
      fctx.beginPath(); fctx.moveTo(p.x, p.y); fctx.lineTo(nx, ny); fctx.stroke();
      p.x = nx; p.y = ny;
    }
  }

  /* ---------- Bedienung ---------- */
  const $ = s => document.querySelector(s);
  function label(t) {
    const d = new Date(t);
    return d.toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "numeric" }) + " " + t.slice(11, 16) + " Uhr";
  }
  function syncBar() {
    if (!field) return;
    $("#windTime").value = hour;
    $("#windLabel").textContent = label(field.time[hour]);
    const info = opts.spotInfo ? opts.spotInfo(field.time[hour]) : [];
    $("#windSpots").innerHTML = info.map(i => `<span class="wchip" style="--c:${i.kn != null ? colorOf(i.kn) : "transparent"}"><b>${i.name}</b> ${i.kn != null ? Math.round(i.kn) + " kn" : "–"}${i.p != null ? ` · ${Math.round(i.p * 100)} %` : ""}</span>`).join("");
  }
  function syncModes() {
    const [a, b] = THR();
    $("#windMode").textContent = mode === "thr" ? `Farbe: ab ${a}/${b} kn` : "Farbe: Knoten";
    $("#windCorr").textContent = corr ? "Satellitenkorrektur an" : "ICON roh";
    $("#windCorr").hidden = grid === false;
    $("#windCorr").classList.toggle("on", corr);
    $("#windLegend").innerHTML = mode === "thr"
      ? `<span style="--c:#39b86b">ab ${a}</span><span style="--c:#0f7a3c;color:#fff">ab ${b} kn</span> auf dem Wasser`
      : `<span style="--c:#4fb3d9">6</span><span style="--c:#3cbf8a">9</span><span style="--c:#9bd34a">12</span><span style="--c:#f2d33b">15</span><span style="--c:#f59a2c">18</span><span style="--c:#e8503a">21</span><span style="--c:#b23a8c">25+</span> kn`;
    $("#windNote").textContent = corr && grid ? `Windfeld: ICON, über Wasser mit Satellitenfaktoren je Windrichtung korrigiert (${grid.quelle}).`
      : grid === false ? "Windfeld: ICON, unkorrigiert (Satellitenfaktoren noch nicht berechnet)." : "Windfeld: ICON, unkorrigiert.";
  }

  function setPlay(v) {
    playing = v; const b = $("#windPlay");
    b.textContent = v ? "❚❚" : "▶"; b.setAttribute("aria-label", v ? "Anhalten" : "Abspielen");
  }

  async function toggle(v) {
    on = v; save("wind", v ? "1" : "0");
    const b = $("#btnWind"); b.classList.toggle("on", v); b.setAttribute("aria-pressed", v);
    $("#windBar").hidden = !v; bg.style.display = fg.style.display = v ? "" : "none";
    $("#windLegend").hidden = !v;
    if (!v) { setPlay(false); if (raf) cancelAnimationFrame(raf); raf = null; return; }
    $("#windLabel").textContent = "Lade Windfeld …";
    try { await load(); } catch (e) { $("#windLabel").textContent = "Windfeld nicht verfügbar (" + e.message + ")"; return; }
    $("#windTime").max = field.T - 1;
    size(); reseed(); drawBg(); syncBar(); syncModes();
    if (!raf) raf = requestAnimationFrame(frame);
  }

  function save(k, v) { try { localStorage.setItem("wingst-" + k, v); } catch (e) {} }
  function loadPref(k) { try { return localStorage.getItem("wingst-" + k); } catch (e) { return null; } }

  function init(m, o = {}) {
    map = m; opts = o;
    const cont = map.getCanvasContainer();
    bg = document.createElement("canvas"); fg = document.createElement("canvas");
    for (const cv of [bg, fg]) { cv.className = "wind-canvas"; cv.style.display = "none"; cont.insertBefore(cv, map.getCanvas().nextSibling); }
    cont.insertBefore(bg, fg);
    bctx = bg.getContext("2d"); fctx = fg.getContext("2d");
    map.on("resize", () => { if (on) { size(); reseed(); drawBg(); } });
    map.on("movestart", () => { moving = true; if (on) fctx.clearRect(0, 0, fg.clientWidth, fg.clientHeight); });
    map.on("move", () => { if (on) drawBg(); });
    map.on("moveend", () => { moving = false; if (on) { reseed(); drawBg(); } });
    $("#btnWind").addEventListener("click", () => toggle(!on));
    $("#windPlay").addEventListener("click", () => setPlay(!playing));
    $("#windTime").addEventListener("input", e => { hour = +e.target.value; frac = 0; drawBg(); syncBar(); });
    mode = loadPref("windmode") || "thr"; corr = loadPref("windcorr") !== "0";
    $("#windMode").addEventListener("click", () => { mode = mode === "thr" ? "kn" : "thr"; save("windmode", mode); syncModes(); drawBg(); });
    $("#windCorr").addEventListener("click", () => { corr = !corr; save("windcorr", corr ? "1" : "0"); if (field) { pick(); drawBg(); } syncModes(); });
    syncModes();
    document.addEventListener("visibilitychange", () => { if (document.hidden) setPlay(false); });
    if (loadPref("wind") === "1") toggle(true).then(() => { if (!reduce) setPlay(false); });
  }

  return { init, refresh: () => on && syncBar() };
})();
