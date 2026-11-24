/* Wingst – Kitewind-App. Holt alle Daten direkt im Browser, rechnet lokal, kein eigener Server. */
"use strict";

const MODELS = { icon: "icon_seamless", gfs: "gfs_seamless", ecmwf: "ecmwf_ifs025" };
const MODEL_LABEL = { icon: "ICON", gfs: "GFS", ecmwf: "ECMWF", mean: "Mittel" };
const KMH2KN = 1 / 1.852, MS2KN = 1 / 0.514444;
const CACHE_KEY = "wingst-cache-v2";
/* Punkte, an denen das Korrekturmodell trainiert wurde (plus Darßer Schwelle für Druck/Wassertemperatur) */
const MODEL_SITES = [
  { id: "darss_ort", lat: 54.4738, lon: 12.5014 },
  { id: "barth", lat: 54.3405, lon: 12.7109 },
  { id: "darss_schwelle", lat: 54.70, lon: 12.70 }
];
const SPOT_SITE = { darss: "darss_ort", barth: "barth" };          // Spot.ref -> Modellstandort
const VAR_MAP = { ws: "wind_speed_10m", wd: "wind_direction_10m", gust: "wind_gusts_10m", ws80: "wind_speed_80m",
  t2m: "temperature_2m", pmsl: "pressure_msl", cloud: "cloud_cover", swrad: "shortwave_radiation", cape: "cape", precip: "precipitation",
  blh: "boundary_layer_height" };
/* Abklingzeit (h) des aktuellen Messfehlers von ICON – aus 2023–2025 geschätzt (training/nowcast) */
const NOWCAST_TAU = { darss_ort: 20, barth: 3 };
const HEIGHT_FACTOR = { darss: 0.983, barth: 1.0 };   // Anemometer 12 m -> 10 m wie im Training
const P_ON = 0.5;   // ab dieser Wahrscheinlichkeit zählt eine Stunde im lokalen Modell als „Wind über Schwelle“

const state = {
  fc: null,          // { spots:{id:{time,sunrise,sunset,icon,gfs,ecmwf,mean}}, sites:{id:{…alle Variablen}}, sst:{id:Zahl}, fetched }
  obs: {},
  model: null,       // { manifest, hourly:{site:{thr:model}}, daily:{…} }
  local: null,       // { site: { thr: { p:[…], day:{date:p} } } }
  ens: null,         // Ensemble-Läufe je Standort (WingstEns.fetchAll)
  ensBias: null,     // richtungsabhängige Abweichung je Station/Modell
  ensCache: {},
  modelSel: load("model", "icon"),
  thr: THRESHOLDS.includes(Number(load("thr", 14))) ? Number(load("thr", 14)) : 14,
  spot: load("spot", "zingst"),
  map: null,
  markers: []
};

/* ---------- kleine Helfer ---------- */
function load(k, d) { try { return localStorage.getItem("wingst-" + k) ?? d; } catch (e) { return d; } }
function save(k, v) { try { localStorage.setItem("wingst-" + k, v); } catch (e) {} }
const $ = s => document.querySelector(s);
const compass = d => d == null ? "" : ["N","NNO","NO","ONO","O","OSO","SO","SSO","S","SSW","SW","WSW","W","WNW","NW","NNW"][Math.round((((d % 360) + 360) % 360) / 22.5) % 16];
function inWindow(dir, [a, b]) { return a <= b ? dir >= a && dir <= b : dir >= a || dir <= b; }
const arrow = (dir, cls = "arrow") => `<svg class="${cls}" viewBox="0 0 20 20" aria-hidden="true"><g transform="rotate(${(dir + 180) % 360} 10 10)"><path d="M10 1 L15 11 L11 9.5 L11 19 L9 19 L9 9.5 L5 11 Z"/></g></svg>`;
const fmtTime = iso => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
function dayLabel(dateStr, i) {
  const d = new Date(dateStr + "T12:00:00");
  if (i === 0) return "Heute";
  if (i === 1) return "Morgen";
  return d.toLocaleDateString("de-DE", { weekday: "short" }) + " " + d.toLocaleDateString("de-DE", { day: "numeric", month: "numeric" });
}
const isWeekend = dateStr => [0, 6].includes(new Date(dateStr + "T12:00:00").getDay());
const siteOf = sp => SPOT_SITE[sp.ref];
const tooRare = (sp, thr) => false;   // seit 14/18 kn (Wasser-Äquivalent) genug Fälle an beiden Stationen

/* ---------- Daten holen ---------- */
async function fetchJSON(url, timeoutMs = 25000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ctl.signal });
    const body = await r.json().catch(() => null);
    if (!r.ok) throw new Error((body && body.reason) || `${r.status} ${r.statusText}`);
    return body;
  } finally { clearTimeout(t); }
}

function pickDaily(obj, name, k) {   // Open-Meteo hängt bei mehreren Modellen den Modellnamen an
  if (obj[name]) return obj[name][k];
  for (const key of Object.keys(obj)) if (key.startsWith(name + "_") && obj[key] && obj[key][k] != null) return obj[key][k];
  return null;
}

async function fetchForecast() {
  const places = [...SPOTS, ...MODEL_SITES];
  const p = new URLSearchParams({
    latitude: places.map(s => s.lat).join(","), longitude: places.map(s => s.lon).join(","),
    hourly: Object.values(VAR_MAP).join(","), daily: "sunrise,sunset",
    models: Object.values(MODELS).join(","), wind_speed_unit: "kn", timezone: "Europe/Berlin", forecast_days: "7"
  });
  let res = await fetchJSON("https://api.open-meteo.com/v1/forecast?" + p);
  if (res && res.error) throw new Error(res.reason || "Open-Meteo-Fehler");
  if (!Array.isArray(res)) res = [res];
  if (res.length < places.length) throw new Error(`Open-Meteo lieferte ${res.length} statt ${places.length} Orte`);
  const parse = r => {
    const h = r.hourly, d = r.daily;
    if (!h || !h.time) throw new Error("Antwort ohne Stundenwerte");
    const o = { time: h.time, sunrise: {}, sunset: {} };
    (d && d.time ? d.time : []).forEach((day, k) => { o.sunrise[day] = pickDaily(d, "sunrise", k); o.sunset[day] = pickDaily(d, "sunset", k); });
    for (const [key, m] of Object.entries(MODELS)) {
      o[key] = {};
      for (const [short, v] of Object.entries(VAR_MAP)) o[key][short] = h[`${v}_${m}`] || [];
    }
    o.mean = meanModel(o);
    return o;
  };
  const spots = {}, sites = {};
  SPOTS.forEach((s, i) => { spots[s.id] = parse(res[i]); });
  MODEL_SITES.forEach((s, i) => { sites[s.id] = parse(res[SPOTS.length + i]); });
  const [sst, bodden] = await Promise.all([
    fetchSST().catch(e => { console.warn("Wassertemperatur", e); return {}; }),
    fetchBodden().catch(e => { console.warn("Bodden-Temperatur", e); return null; })
  ]);
  return { spots, sites, sst, bodden, fetched: new Date().toISOString() };
}

async function fetchSST() {
  const p = new URLSearchParams({ latitude: MODEL_SITES.map(s => s.lat).join(","), longitude: MODEL_SITES.map(s => s.lon).join(","),
    hourly: "sea_surface_temperature", forecast_days: "1", timezone: "Europe/Berlin" });
  let res = await fetchJSON("https://marine-api.open-meteo.com/v1/marine?" + p);
  if (!Array.isArray(res)) res = [res];
  const out = {};
  MODEL_SITES.forEach((s, i) => {
    const v = (res[i]?.hourly?.sea_surface_temperature || []).filter(Number.isFinite);
    out[s.id] = v.length ? v[0] : null;
  });
  return out;
}

/** Bodden-Wassertemperatur (Ersatz): geglättete Lufttemperatur Barth der letzten 21 Tage + Vorhersage. */
async function fetchBodden() {
  const b = MODEL_SITES.find(s => s.id === "barth");
  const p = new URLSearchParams({ latitude: b.lat, longitude: b.lon, hourly: "temperature_2m", models: "icon_seamless",
    past_days: "21", forecast_days: "7", timezone: "Europe/Berlin" });
  const r = await fetchJSON("https://api.open-meteo.com/v1/forecast?" + p);
  const t = r.hourly.temperature_2m || r.hourly.temperature_2m_icon_seamless;
  const proxy = WingstModel.boddenProxy(t);
  const map = {};
  r.hourly.time.forEach((k, i) => { map[k] = proxy[i]; });
  return map;
}
const minusDays = (t, d) => new Date(new Date(t + ":00Z") - d * 864e5).toISOString().slice(0, 16);

function meanModel(o) {
  const n = o.time.length, ws = [], wd = [], gust = [];
  for (let i = 0; i < n; i++) {
    let u = 0, v = 0, s = 0, g = 0, c = 0, cg = 0;
    for (const k of Object.keys(MODELS)) {
      const a = o[k].ws[i], dir = o[k].wd[i];
      if (a == null || dir == null) continue;
      u += -a * Math.sin(dir * Math.PI / 180); v += -a * Math.cos(dir * Math.PI / 180); s += a; c++;
      if (o[k].gust[i] != null) { g += o[k].gust[i]; cg++; }
    }
    ws.push(c ? s / c : null);
    wd.push(c ? (Math.atan2(-u, -v) * 180 / Math.PI + 360) % 360 : null);
    gust.push(cg ? g / cg : null);
  }
  return { ws, wd, gust };
}

async function fetchObs() {
  const out = {};
  await Promise.all(STATIONS.map(async st => {
    try {
      if (st.src === "dwd") {
        const r = await fetchJSON(`https://api.brightsky.dev/current_weather?dwd_station_id=${st.sid}`);
        const w = r.weather;
        if (w.wind_speed_10 == null) return;
        out[st.id] = { kn: w.wind_speed_10 * KMH2KN, gust: (w.wind_gust_speed_10 ?? 0) * KMH2KN, dir: w.wind_direction_10, time: w.timestamp };
      } else {
        const base = "https://opendataapi.dmi.dk/v2/metObs/collections/observation/items?limit=1&sortorder=observed,DESC&stationId=" + st.sid;
        const [s, d, g] = await Promise.all(["wind_speed", "wind_dir", "wind_gust_always_past1h"].map(p =>
          fetchJSON(`${base}&parameterId=${p}`).catch(() => null)));
        const f = s && s.features && s.features[0];
        if (!f) return;
        out[st.id] = { kn: f.properties.value * MS2KN, dir: d?.features?.[0]?.properties.value ?? null,
                       gust: g?.features?.[0] ? g.features[0].properties.value * MS2KN : null, time: f.properties.observed };
      }
    } catch (e) { console.warn("Messung", st.id, e); }
  }));
  return out;
}

/* ---------- lokales Korrekturmodell ---------- */
/* Eichung (training/calibrate.py): nur ab Tag 6 – davor sind die rohen Werte in der Kreuzvalidierung besser geeicht */
const CALIB_FROM_LEAD = 6;
function calibrate(cal, p, lead) {
  if (!cal || lead < CALIB_FROM_LEAD || p == null) return p;
  const c = cal[String(lead)]; if (!c) return p;
  const [xs, ys] = c;
  if (p <= xs[0]) return ys[0];
  if (p >= xs[xs.length - 1]) return ys[ys.length - 1];
  let i = 1; while (xs[i] < p) i++;
  const f = (p - xs[i - 1]) / Math.max(xs[i] - xs[i - 1], 1e-9);
  return ys[i - 1] + f * (ys[i] - ys[i - 1]);
}

async function loadLocalModel() {
  const man = await fetchJSON("model/manifest.json");
  const out = { manifest: man, hourly: {}, daily: {} };
  out.calib = await fetchJSON("model/calib.json").catch(() => null);
  const jobs = [];
  for (const [site, levels] of Object.entries(man.sites)) {
    for (const level of ["hourly", "daily"]) {
      out[level][site] = {};
      for (const [thr, file] of Object.entries(levels[level])) {
        jobs.push(fetchJSON("model/" + file, 60000).then(m => { out[level][site][thr] = m; }));
      }
    }
  }
  await Promise.all(jobs);
  return out;
}

function computeLocal() {
  const fc = state.fc, M = state.model;
  if (!fc || !fc.sites || !M) return null;
  const today = fc.sites.darss_ort.time[0].slice(0, 10);
  const dayIdx = t => Math.round((new Date(t.slice(0, 10) + "T12:00:00") - new Date(today + "T12:00:00")) / 864e5);
  const out = {};
  for (const site of Object.keys(M.manifest.sites)) {
    const S = fc.sites[site];
    const lead = S.time.map(t => Math.min(7, Math.max(1, dayIdx(t))));
    const bod = fc.bodden ? S.time.map((t, i) => fc.bodden[minusDays(t, lead[i])] ?? null) : null;
    const rows = WingstModel.hourlyFeatures(S, fc.sites.barth, fc.sites.darss_schwelle,
      fc.sst?.[site] ?? null, fc.sst?.darss_schwelle ?? null, S.time, lead, bod);
    const dfeats = Array.isArray(M.manifest.daily_features) ? M.manifest.daily_features : M.manifest.daily_features[site];
    out[site] = {};
    for (const thr of M.manifest.thresholds) {
      const hm = M.hourly[site][thr], dm = M.daily[site][thr];
      const p = rows.map(r => WingstModel.predictProba(hm, WingstModel.vec(r, M.manifest.hourly_features)));
      const day = {};
      for (const d of WingstModel.dailyFeatures(rows, p, thr)) {
        const ld = Math.min(7, Math.max(1, dayIdx(d.date + "T12:00")));
        day[d.date] = calibrate(M.calib?.[site]?.[String(thr)], WingstModel.predictProba(dm, WingstModel.vec(d.x, dfeats)), ld);
      }
      out[site][thr] = { p, day, time: S.time };
    }
  }
  return out;
}

/* ---------- Nowcast: aktueller Messfehler von ICON, klingt über einige Stunden ab ---------- */
function nowcast(sp) {
  const fc = state.fc, o = state.obs[sp.ref];
  if (!fc || !fc.sites || !o || o.kn == null) return null;
  const site = siteOf(sp), S = fc.sites[site];
  const obsTime = new Date(o.time);
  if (Date.now() - obsTime > 3 * 3600e3) return null;          // Messung zu alt
  const key = obsTime.toLocaleString("sv-SE", { timeZone: "Europe/Berlin" }).slice(0, 13).replace(" ", "T") + ":00";
  const k = S.time.indexOf(key);
  if (k < 0 || S.icon.ws[k] == null) return null;
  const err = o.kn * HEIGHT_FACTOR[sp.ref] - S.icon.ws[k];
  const tau = NOWCAST_TAU[site];
  const map = {};
  const spotIcon = fc.spots[sp.id].icon.ws;
  fc.spots[sp.id].time.forEach((t, i) => {
    const L = i - k;
    if (L < 0 || t.slice(0, 10) !== key.slice(0, 10) || spotIcon[i] == null) return;   // nur heute
    map[t] = Math.max(0, spotIcon[i] + err * Math.exp(-L / tau));
  });
  return { err, map, at: key };
}

/* ---------- Auswertung: Stunden am Stück ---------- */
function daylightHours(o, day) {
  const rise = o.sunrise[day], set = o.sunset[day];
  const hr = rise ? Number(rise.slice(11, 13)) + Number(rise.slice(14, 16)) / 60 : 7;
  const hs = set ? Number(set.slice(11, 13)) + Number(set.slice(14, 16)) / 60 : 20;
  return [Math.ceil(hr), Math.floor(hs) - 1];
}

function ensFor(sp, byDay) {
  const site = siteOf(sp);
  if (!state.ens || !state.ens[site]) return null;
  if (state.ensCache[sp.id]) return state.ensCache[sp.id];
  try {
    const hb = new Map([...byDay].map(([d, hs]) => [d, hs.map(h => h.hour)]));
    return (state.ensCache[sp.id] = WingstEns.probabilities(state.ens[site], state.ensBias?.[site], sp.win, hb, THRESHOLDS, MIN_RUN, inWindow));
  } catch (e) { console.warn("Ensemble", e); return null; }
}

/** Tage eines Spots: je Stunde Werte aller Modelle + lokale Wahrscheinlichkeiten; je Schwelle Läufe. */
function days(spotId) {
  const sp = SPOTS.find(s => s.id === spotId);
  const o = state.fc.spots[spotId];
  const loc = state.local && state.local[siteOf(sp)];
  const nc = nowcast(sp);
  const byDay = new Map();
  o.time.forEach((t, i) => {
    const day = t.slice(0, 10), hour = Number(t.slice(11, 13));
    const [h0, h1] = daylightHours(o, day);
    if (hour < h0 || hour > h1) return;
    if (!byDay.has(day)) byDay.set(day, []);
    const h = { hour, t, models: {} };
    for (const m of ["icon", "gfs", "ecmwf", "mean"]) h.models[m] = { kn: o[m].ws[i], dir: o[m].wd[i], gust: o[m].gust[i] };
    h.dirMean = o.mean.wd[i];
    h.now = nc ? (nc.map[t] ?? null) : null;
    h.t2m = o.icon.t2m?.[i] ?? o.gfs.t2m?.[i] ?? null;
    h.cloud = o.icon.cloud?.[i] ?? o.gfs.cloud?.[i] ?? null;
    h.precip = o.icon.precip?.[i] ?? o.gfs.precip?.[i] ?? null;
    h.p = {};
    if (loc) for (const thr of THRESHOLDS) {
      const k = loc[thr].time.indexOf(t);
      h.p[thr] = k >= 0 ? loc[thr].p[k] : null;
    }
    byDay.get(day).push(h);
  });
  const E = ensFor(sp, byDay);
  if (E) for (const hs of byDay.values()) for (const h of hs) {
    h.ens = E.hourly[h.t] || null; h.aifs = E.median.aifs?.[h.t] ?? null; h.wn2 = E.median.wn2?.[h.t] ?? null;
  }
  return [...byDay.entries()].map(([date, hours]) => {
    const runs = {}, local = {}, pday = {};
    for (const m of ["icon", "gfs", "ecmwf", "mean"]) {
      runs[m] = {};
      for (const thr of THRESHOLDS) runs[m][thr] = bestRun(hours, h => h.models[m].kn != null && h.models[m].dir != null && h.models[m].kn >= thr && inWindow(h.models[m].dir, sp.win));
    }
    for (const thr of THRESHOLDS) {
      local[thr] = loc && !tooRare(sp, thr) ? bestRun(hours, h => h.p[thr] != null && h.p[thr] >= P_ON && h.dirMean != null && inWindow(h.dirMean, sp.win)) : null;
      pday[thr] = loc && !tooRare(sp, thr) ? (loc[thr].day[date] ?? null) : null;
    }
    const hasNow = hours.some(h => h.now != null);
    const ens = E ? E.daily[date] : null;
    return { date, hours, runs, local, pday, hasNow, nowErr: nc ? nc.err : null, ens, hasEns: !!E };
  });
}

function bestRun(hours, ok) {
  let best = { len: 0, start: null, end: null }, cur = null, prev = null;
  for (const h of hours) {
    const good = ok(h);
    if (good && cur && prev === h.hour - 1) { cur.len++; cur.end = h.hour; }
    else if (good) cur = { len: 1, start: h.hour, end: h.hour };
    else cur = null;
    if (cur && cur.len > best.len) best = { ...cur };
    prev = h.hour;
  }
  return best;
}

const runClass = len => len >= 4 ? "r4" : len >= MIN_RUN ? "r2" : len >= 1 ? "r1" : "r0";
const runText = r => r && r.len ? `${r.len} h` : "–";
const spanText = r => r && r.len ? `${r.start}–${r.end + 1} Uhr` : "";
const pct = p => p == null ? "" : `${Math.round(p * 100)}\u00a0%`;
/** Hauptwert einer Zelle: lokal korrigiert, sonst gewähltes Modell */
const mainRun = (d, thr) => d.local[thr] ?? d.runs[state.modelSel][thr];

/* ---------- Karte ---------- */
function initMap() {
  const map = new maplibregl.Map({
    container: "map", style: "https://tiles.openfreemap.org/styles/liberty",
    center: [12.62, 54.43], zoom: 9.6, attributionControl: { compact: true }, cooperativeGestures: true
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.on("load", () => {
    map.addSource("s2", { type: "raster", tileSize: 256, maxzoom: 15,
      tiles: ["https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg"],
      attribution: "Sentinel-2 cloudless – https://s2maps.eu by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020)" });
    const firstSymbol = map.getStyle().layers.find(l => l.type === "symbol");
    map.addLayer({ id: "s2", type: "raster", source: "s2", layout: { visibility: "none" } }, firstSymbol && firstSymbol.id);
    if (load("layer", "map") === "sat") setLayer("sat");
  });
  state.map = map;
}

function setLayer(which) {
  save("layer", which);
  $("#btnMap").classList.toggle("on", which === "map"); $("#btnMap").setAttribute("aria-pressed", which === "map");
  $("#btnSat").classList.toggle("on", which === "sat"); $("#btnSat").setAttribute("aria-pressed", which === "sat");
  const m = state.map;
  if (m && m.getLayer("s2")) m.setLayoutProperty("s2", "visibility", which === "sat" ? "visible" : "none");
}

function drawMarkers() {
  state.markers.forEach(m => m.remove()); state.markers = [];
  if (!state.map) return;
  for (const sp of SPOTS) {
    const today = state.fc ? days(sp.id)[0] : null;
    const r = today ? mainRun(today, state.thr) : { len: 0 };
    const el = document.createElement("button");
    el.type = "button";
    el.className = `spot-mk mk-${runClass(r.len)}${sp.id === state.spot ? " cur" : ""}`;
    el.innerHTML = `<span class="dot"></span><span class="lab"><b>${sp.name}</b><span>${today ? runText(r) + " " + state.thr + "+" : ""}</span></span>`;
    el.setAttribute("aria-label", `${sp.name}: heute ${runText(r)} ab ${state.thr} kn`);
    el.addEventListener("click", ev => { ev.stopPropagation(); selectSpot(sp.id); });
    state.markers.push(new maplibregl.Marker({ element: el, anchor: "left", offset: [-8, 0] }).setLngLat([sp.lon, sp.lat]).addTo(state.map));
  }
  for (const st of STATIONS) {
    const o = state.obs[st.id];
    if (!o) continue;
    const el = document.createElement("div");
    el.className = "stn-mk";
    el.innerHTML = `${o.dir != null ? arrow(o.dir, "arrow big") : ""}<span class="lab"><b>${Math.round(o.kn)} kn</b> ${compass(o.dir)}<small>${st.name}</small></span>`;
    state.markers.push(new maplibregl.Marker({ element: el, anchor: "center" }).setLngLat([st.lon, st.lat]).addTo(state.map));
  }
  const el = document.createElement("a");
  el.className = "stn-mk sill"; el.href = SILL.page; el.target = "_blank"; el.rel = "noopener";
  el.innerHTML = `<span class="sq"></span><span class="lab"><b>Darßer Schwelle</b><small>Messmast, Diagramm unten</small></span>`;
  state.markers.push(new maplibregl.Marker({ element: el, anchor: "left" }).setLngLat([SILL.lon, SILL.lat]).addTo(state.map));
}

/* ---------- Wochenübersicht ---------- */
function drawWeek() {
  const t = $("#week");
  if (!state.fc) { t.innerHTML = ""; return; }
  const ref = days(SPOTS[0].id);
  const head = (d, i) => {
    const x = new Date(d.date + "T12:00:00");
    const wd = i === 0 ? "Heute" : x.toLocaleDateString("de-DE", { weekday: "short" });
    return `<th scope="col" class="${isWeekend(d.date) ? "we" : ""}">${wd}<br><small>${x.getDate()}.${x.getMonth() + 1}.</small></th>`;
  };
  let html = `<thead><tr><th scope="col" class="spotcol">Spot</th>${ref.map(head).join("")}</tr></thead><tbody>`;
  for (const sp of SPOTS) {
    const ds = days(sp.id);
    html += `<tr class="${sp.id === state.spot ? "cur" : ""}"><th scope="row"><button type="button" class="linkbtn" data-spot="${sp.id}">${sp.short}</button></th>`;
    for (const d of ds) {
      const rare = tooRare(sp, state.thr);
      const r = rare ? { len: 0 } : mainRun(d, state.thr);
      const raw = d.runs[state.modelSel][state.thr];
      const ep = d.ens?.[state.thr]?.p;
      const sub = ep != null ? `<span class="sub">E ${Math.round(ep * 100)}%</span>` : d.local[state.thr] ? `<span class="sub">${MODEL_LABEL[state.modelSel]} ${raw.len || "–"}</span>` : "";
      const p = d.pday[state.thr];
      html += `<td class="cell ${rare ? "r0 rare" : runClass(r.len)}${isWeekend(d.date) ? " we" : ""}" data-spot="${sp.id}" title="${rare ? "zu selten für eine Prognose" : spanText(r)}">
        <span class="v">${rare ? "·" : (r.len || "–")}</span>${p != null ? `<span class="pd">${Math.round(p * 100)}%</span>` : ""}${sub}</td>`;
    }
    html += "</tr>";
  }
  t.innerHTML = html + "</tbody>";
  t.querySelectorAll("[data-spot]").forEach(e => e.addEventListener("click", () => selectSpot(e.dataset.spot)));
  $("#weekHint").innerHTML = state.local
    ? `Zahl: Stunden am Stück ab ${state.thr} kn aus passender Richtung (lokales Modell). Darunter: Chance auf mind. ${MIN_RUN} h, „E“ = Anteil der Ensemble-Läufe.`
    : `Zahl: längste Zeit am Stück (h) bei Tageslicht ab ${state.thr} kn aus passender Richtung (${MODEL_LABEL[state.modelSel]}, unkorrigiert – Korrekturmodell lädt noch).`;
}

/* ---------- Detail eines Spots: stündliche Tabelle im Windfinder-Stil (3 Modelle + lokal) ---------- */
const KN_SCALE = [[0, "k0"], [6, "k1"], [9, "k2"], [12, "k3"], [15, "k4"], [18, "k5"], [21, "k6"], [25, "k7"], [30, "k8"]];
const knCls = kn => { if (kn == null) return "kx"; let c = "k0"; for (const [v, k] of KN_SCALE) if (kn >= v) c = k; return c; };
const pCls = p => p == null ? "kx" : p >= 0.75 ? "p4" : p >= P_ON ? "p3" : p >= 0.3 ? "p2" : p >= 0.15 ? "p1" : "p0";
const cloudIcon = c => c == null ? "" : c < 20 ? "☀" : c < 60 ? "⛅" : "☁";

const sunIcon = up => `<svg class="sico" viewBox="0 0 20 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M5 12a5 5 0 0 1 10 0"/><path d="M2 12h16"/><path d="${up ? "M10 1v4M8 3l2-2 2 2" : "M10 1v4M8 3l2 2 2-2"}"/></svg>`;
/* Bestes Rohmodell je Gebiet (Windanalyse 2024–25): Ostsee ICON, Bodden Mittel der drei */
const BEST_RAW = { darss_ort: ["icon", "ICON"], barth: ["mean", "Mittel"] };
function drawDetail() {
  const box = $("#detail");
  if (!state.fc) { box.innerHTML = ""; return; }
  const sp = SPOTS.find(s => s.id === state.spot);
  const ds = days(sp.id);
  const o = state.obs[sp.ref];
  const [bm, bmLabel] = BEST_RAW[siteOf(sp)];
  const [t1, t2] = THRESHOLDS;
  const blocks = ds.map((d, i) => {
    const o2 = state.fc.spots[sp.id];
    const rise = o2.sunrise[d.date], set = o2.sunset[d.date];
    const hm = s => s ? s.slice(11, 16) : "";
    const sunL = (first) => first ? `<td class="sun" rowspan="99" title="Sonnenaufgang">${sunIcon(true)}<span>${hm(rise)}</span></td>` : "";
    const sunR = (first) => first ? `<td class="sun" rowspan="99" title="Sonnenuntergang">${sunIcon(false)}<span>${hm(set)}</span></td>` : "";
    let firstRow = true;
    const cells = (fn) => { const f = firstRow; firstRow = false; return sunL(f) + d.hours.map(fn).join("") + sunR(f); };
    const offCls = h => h.dirMean != null && !inWindow(h.dirMean, sp.win) ? " off" : "";
    const ed = d.ens?.[t1];
    const ensTip = ed && ed.p != null ? WingstEns.MODELS.filter(m => ed.byModel[m.key] != null).map(m => `${m.label.replace(/ \(KI\)/, "")} ${pct(ed.byModel[m.key])}`).join(", ") : "";
    const run = t => { const r = d.local[t]; return r && r.len ? `${t}+: <b>${r.len} h</b> ${spanText(r)}` : `${t}+: –`; };
    const pRow = t => `<tr class="lrow"><th scope="row">Lokal ${t}+</th>${cells(h => {
      const p = h.p[t]; return `<td class="${pCls(p)}${offCls(h)}" title="Chance auf ${t}+ kn">${p == null ? "·" : Math.round(p * 100)}</td>`; })}</tr>`;
    return `<section class="fday${isWeekend(d.date) ? " we" : ""}">
      <div class="day-h"><b>${dayLabel(d.date, i)}</b>
        <span class="cmp">${state.local ? `${run(t1)} · ${run(t2)}${d.pday[t1] != null ? ` · kitebar ${pct(d.pday[t1])}` : ""}` : ""}${ed && ed.p != null ? ` · <span title="${ensTip}">Ensemble ${pct(ed.p)}</span>` : ""}</span></div>
      <div class="ftable-wrap"><table class="ftable">
        <tbody>
          <tr class="hrow"><th scope="row">Uhr</th>${cells(h => `<td>${String(h.hour).padStart(2, "0")}</td>`)}</tr>
          <tr class="drow"><th scope="row">Richtung</th>${cells(h => `<td class="${offCls(h).trim()}" title="${compass(h.dirMean)}">${h.dirMean != null ? arrow(h.dirMean) : ""}</td>`)}</tr>
          <tr class="mrow"><th scope="row" title="bestes Rohmodell für dieses Gebiet">${bmLabel} kn</th>${cells(h => {
            const x = h.models[bm]; return `<td class="${knCls(x.kn)}${offCls(h)}">${x.kn == null ? "–" : Math.round(x.kn)}</td>`; })}</tr>
          ${d.hasNow ? `<tr class="mrow nrow"><th scope="row" title="ICON plus aktueller Messfehler (${d.nowErr > 0 ? "+" : ""}${d.nowErr.toFixed(1)} kn), klingt ab">Jetzt-korr.</th>${cells(h => `<td class="${h.now == null ? "kx" : knCls(h.now)}${offCls(h)}">${h.now == null ? "" : Math.round(h.now)}</td>`)}</tr>` : ""}
          <tr class="grow2"><th scope="row">Böen</th>${cells(h => { const g = h.models.icon.gust ?? h.models.gfs.gust; return `<td>${g == null ? "" : Math.round(g)}</td>`; })}</tr>
          ${state.local ? pRow(t1) + pRow(t2) : ""}
          <tr class="wrow"><th scope="row">Wetter</th>${cells(h => `<td title="${h.cloud == null ? "" : Math.round(h.cloud) + " % Wolken"}">${h.precip > 0.2 ? "🌧" : cloudIcon(h.cloud)}</td>`)}</tr>
          <tr class="trow"><th scope="row">°C</th>${cells(h => `<td>${h.t2m == null ? "" : Math.round(h.t2m)}</td>`)}</tr>
        </tbody></table></div>
    </section>`;
  }).join("");
  box.innerHTML = `
    <div class="panel-head"><h2>${sp.name}</h2>
      <span class="meta">${sp.water} · passende Richtung ${compass(sp.win[0])} bis ${compass(sp.win[1])} · <a href="spotguide.html" id="toGuide">Spotguide</a></span></div>
    ${o ? `<p class="obsline">${arrow(o.dir ?? 0)} Gemessen ${STATIONS.find(s => s.id === sp.ref).name}, ${fmtTime(o.time)} Uhr: <b>${Math.round(o.kn)} kn</b> ${compass(o.dir)}${o.gust ? `, Böe ${Math.round(o.gust)} kn` : ""}</p>` : ""}
    <div class="days">${blocks}</div>
    <div class="legend"><span>${bmLabel} (kn):</span>${KN_SCALE.map(([v, k], j) => `<span><i class="sw ${k}"></i>${v}${KN_SCALE[j + 1] ? "–" + KN_SCALE[j + 1][0] : "+"}</span>`).join("")}</div>
    <div class="legend"><span>Lokal = Chance in % auf Wind über der Schwelle (auf dem Wasser):</span><span><i class="sw p1"></i>15–30</span><span><i class="sw p2"></i>30–50</span><span><i class="sw p3"></i>50–75</span><span><i class="sw p4"></i>75+</span><span>blasse Spalte: Richtung passt nicht</span></div>`;
  const g = $("#toGuide"); if (g) g.addEventListener("click", () => save("guide", sp.id));
}

/* ---------- Messstationen ---------- */
/* Private Stationen bei Windfinder: keine offene Schnittstelle, daher nur verlinkt */
const WF_LINKS = [
  ["Born am Darß", "https://www.windfinder.com/report/born_am_dar_", "am Bodden"],
  ["Zingst", "https://www.windfinder.com/report/zingst", ""],
  ["Ahrenshoop Deich", "https://www.windfinder.com/windstatistics/ahrenshoop_deich", "Ostseeküste Fischland"]
];
function drawStations() {
  const list = STATIONS.map(st => {
    const o = state.obs[st.id];
    return `<li>${o && o.dir != null ? arrow(o.dir) : '<span class="arrow"></span>'}<b>${st.name}</b>
      <span>${o ? `${Math.round(o.kn)} kn ${compass(o.dir)}${o.gust ? ` · Böe ${Math.round(o.gust)}` : ""} <small>${fmtTime(o.time)}</small>` : "keine Daten"}</span></li>`;
  }).join("");
  $("#stations").innerHTML = `<div class="panel-head"><h2 id="stH">Messungen jetzt</h2></div><ul class="stnlist">${list}</ul>
    <h3>Weitere Live-Stationen (Windfinder)</h3>
    <p class="wflinks">${WF_LINKS.map(([n, u, note]) => `<a href="${u}" target="_blank" rel="noopener">${n}</a> <small>${note}</small>`).join("<br>")}</p>
    <h3>Darßer Schwelle (offene See, letzte 7 Tage, m/s)</h3>
    <div class="sill-img"><img src="${SILL.plot}" alt="Windgeschwindigkeit Darßer Schwelle, letzte 7 Tage" loading="lazy"><img src="${SILL.plotDir}" alt="Windrichtung Darßer Schwelle, letzte 7 Tage" loading="lazy"></div>`;
}

/* ---------- Ablauf ---------- */
/* ---------- Woche 2: Tendenz Tag 8–14 nur aus Ensembles (IFS, AIFS, WeatherNext 2 reichen 15 Tage, GEFS 10) ---------- */
const ALL_DIRS = [0, 360];
let CLIMATE = null;   // kitebare Tage je Monat, gemessen 2016–2025 (analyse.json)
function longDays() {
  const ens = state.ens?.darss_ort;
  if (!ens || !state.fc) return [];
  const last = state.fc.spots[SPOTS[0].id];
  const knownDays = Object.keys(last.sunrise).sort();
  const lastDay = knownDays[knownDays.length - 1];
  const [h0, h1] = daylightHours(last, lastDay);          // Tageslicht des letzten bekannten Tages weiterverwenden
  const days = [...new Set(ens.time.map(t => t.slice(0, 10)))].filter(d => d > lastDay).slice(0, 7);
  return days.map(d => [d, Array.from({ length: h1 - h0 + 1 }, (_, i) => h0 + i)]);
}
function longProb(site, win, hb) {
  try { return WingstEns.probabilities(state.ens[site], state.ensBias?.[site], win, new Map(hb), THRESHOLDS, MIN_RUN, inWindow); }
  catch (e) { console.warn("Woche 2", e); return null; }
}
function drawLong() {
  const box = $("#long");
  const hb = longDays();
  if (!hb.length) { box.innerHTML = ""; return; }
  const thr = state.thr;
  const head = hb.map(([d]) => { const x = new Date(d + "T12:00:00");
    return `<th scope="col" class="${isWeekend(d) ? "we" : ""}">${x.toLocaleDateString("de-DE", { weekday: "short" })}<br><small>${x.getDate()}.${x.getMonth() + 1}.</small></th>`; }).join("");
  const cell = (p, d) => `<td class="cell ${p == null ? "r0" : p >= 0.6 ? "r4" : p >= 0.4 ? "r2" : p >= 0.2 ? "r1" : "r0"}${isWeekend(d) ? " we" : ""}"><span class="v">${p == null ? "·" : Math.round(p * 100)}</span></td>`;
  // je Station ohne Richtungsfilter – vergleichbar mit dem Klima der Messstation
  const summary = [];
  let rows = "";
  for (const [site, lab] of [["darss_ort", "Ostsee"], ["barth", "Bodden"]]) {
    const P = longProb(site, ALL_DIRS, hb); if (!P) continue;
    const ps = hb.map(([d]) => P.daily[d]?.[thr]?.p ?? null);
    const valid = ps.filter(p => p != null);
    const exp = valid.reduce((a, b) => a + b, 0);
    const clim = CLIMATE?.[site]?.[thr] ? hb.reduce((s, [d]) => s + CLIMATE[site][thr][Number(d.slice(5, 7)) - 1], 0) : null;
    if (valid.length) summary.push({ lab, exp, clim, n: valid.length });
    rows += `<tr><th scope="row">${lab}<br><small>${site === "barth" ? "Messung Barth" : "Messung Darßer Ort"}</small></th>${ps.map((p, i) => cell(p, hb[i][0])).join("")}</tr>`;
  }
  const verdict = s => {
    if (s.clim == null) return `${s.lab}: erwartet ${s.exp.toFixed(1).replace(".", ",")} kitebare Tage`;
    const r = s.exp / Math.max(s.clim, 0.1);
    const word = r >= 1.25 ? "<b>windiger als üblich</b>" : r <= 0.75 ? "<b>ruhiger als üblich</b>" : "etwa wie üblich";
    return `${s.lab}: ${word} – erwartet ${s.exp.toFixed(1).replace(".", ",")} kitebare Tage, üblich ${s.clim.toFixed(1).replace(".", ",")}`;
  };
  box.innerHTML = `<div class="panel-head"><h2 id="longH">Woche 2: Tendenz</h2><span class="meta">${thr}+ kn · nur Ensembles</span></div>
    ${summary.length ? `<p class="longsum">${summary.map(verdict).join("<br>")}</p>` : ""}
    <div class="weekwrap"><table class="week long"><thead><tr><th scope="col" class="spotcol">Gebiet</th>${head}</tr></thead><tbody>${rows}</tbody></table></div>
    <p class="hint">Anteil der Ensemble-Läufe mit mind. ${MIN_RUN} h ab ${thr} kn, alle Richtungen. Einzelne Tage sind so weit voraus nicht vorhersagbar, die Zeile zeigt die Wetterlage. „Üblich“: gemessen 2016–2025 im selben Monat.</p>`;
}

function renderAll() { drawWeek(); drawLong(); drawDetail(); drawStations(); drawMarkers(); }

function selectSpot(id) {
  state.spot = id; save("spot", id); renderAll();
  $("#detail").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

function recomputeLocal() {
  state.ensCache = {};
  try { state.local = computeLocal(); }
  catch (e) { console.warn("Lokales Modell", e); state.local = null; }
}

async function refresh() {
  const st = $("#status");
  st.textContent = "Lade Vorhersage und Messwerte …"; st.className = "status";
  $("#refreshBtn").disabled = true;
  const [fc, obs, ens] = await Promise.allSettled([fetchForecast(), fetchObs(), WingstEns.fetchAll(fetchJSON)]);
  if (fc.status === "fulfilled") state.fc = fc.value;
  if (obs.status === "fulfilled") state.obs = obs.value;
  if (ens.status === "fulfilled") { state.ens = ens.value; state.ensCache = {}; }
  else console.warn("Ensemble nicht geladen", ens.reason);
  recomputeLocal();
  if (fc.status === "fulfilled") {
    try { localStorage.setItem(CACHE_KEY, JSON.stringify({ fc: state.fc, obs: state.obs })); } catch (e) {}
    st.textContent = `Vorhersage von ${fmtTime(state.fc.fetched)} Uhr · ${Object.keys(state.obs).length} Messstationen · ${state.local ? "lokale Korrektur aktiv" : "lokale Korrektur lädt …"}${state.ens ? " · Ensemble aktiv" : ""}`;
  } else if (state.fc) {
    st.textContent = `Keine Verbindung zu Open-Meteo (${fc.reason.message}). Zeige gespeicherte Vorhersage von ${new Date(state.fc.fetched).toLocaleString("de-DE")}.`;
    st.className = "status warn";
  } else {
    st.textContent = `Vorhersage konnte nicht geladen werden (${fc.reason.message}). Bitte später erneut „Aktualisieren“.`;
    st.className = "status err";
  }
  $("#refreshBtn").disabled = false;
  renderAll();
}

function init() {
  $("#modelSel").value = state.modelSel;
  $("#modelSel").addEventListener("change", e => { state.modelSel = e.target.value; save("model", state.modelSel); renderAll(); });
  document.querySelectorAll(".thr-toggle .seg").forEach(b => {
    const on = Number(b.dataset.thr) === state.thr;
    b.classList.toggle("on", on); b.setAttribute("aria-pressed", on);
    b.addEventListener("click", () => {
      state.thr = Number(b.dataset.thr); save("thr", state.thr);
      document.querySelectorAll(".thr-toggle .seg").forEach(x => { const o = x === b; x.classList.toggle("on", o); x.setAttribute("aria-pressed", o); });
      renderAll();
    });
  });
  $("#refreshBtn").addEventListener("click", refresh);
  $("#btnMap").addEventListener("click", () => setLayer("map"));
  $("#btnSat").addEventListener("click", () => setLayer("sat"));
  try {
    const c = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
    if (c && c.fc && c.fc.sites) { state.fc = c.fc; state.obs = c.obs || {}; }
  } catch (e) {}
  if (window.maplibregl) initMap();
  else $("#map").innerHTML = '<p class="maperr">Karte nicht verfügbar (offline).</p>';
  renderAll();
  fetchJSON("analyse.json").then(j => { CLIMATE = j.climate; drawLong(); }).catch(() => {});
  fetchJSON("model/ens_bias.json").then(b => { state.ensBias = b; state.ensCache = {}; renderAll(); }).catch(e => console.warn("Ensemble-Korrektur", e));
  loadLocalModel()
    .then(m => { state.model = m; recomputeLocal(); renderAll();
      if (state.fc && state.local) $("#status").textContent = $("#status").textContent.replace("lokale Korrektur lädt …", "lokale Korrektur aktiv"); })
    .catch(e => console.warn("Korrekturmodell nicht geladen", e));
  refresh();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}
document.addEventListener("DOMContentLoaded", init);
