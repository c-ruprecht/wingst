/* Wingst – Kitewind-App. Holt alle Daten direkt im Browser, rechnet lokal, kein eigener Server. */
"use strict";

const MODELS = { icon: "icon_seamless", gfs: "gfs_seamless", ecmwf: "ecmwf_ifs025" };
const MODEL_LABEL = { icon: "ICON", gfs: "GFS", ecmwf: "ECMWF", mean: "Mittel" };
const KMH2KN = 1 / 1.852, MS2KN = 1 / 0.514444;
const CACHE_KEY = "wingst-cache-v1";

const state = {
  fc: null,          // { spots: { id: { time:[], sunrise:{}, sunset:{}, icon:{ws,wd,gust}, ... } }, fetched }
  obs: {},           // { stationId: { kn, gust, dir, time } }
  local: null,       // geladenes Korrekturmodell (später)
  model: load("model", "icon"),
  thr: Number(load("thr", 12)),
  spot: load("spot", "zingst"),
  map: null,
  markers: []
};

/* ---------- kleine Helfer ---------- */
function load(k, d) { try { return localStorage.getItem("wingst-" + k) ?? d; } catch (e) { return d; } }
function save(k, v) { try { localStorage.setItem("wingst-" + k, v); } catch (e) {} }
const $ = s => document.querySelector(s);
const compass = d => ["N","NNO","NO","ONO","O","OSO","SO","SSO","S","SSW","SW","WSW","W","WNW","NW","NNW"][Math.round((((d % 360) + 360) % 360) / 22.5) % 16];
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

/* ---------- Daten holen ---------- */
async function fetchJSON(url, timeoutMs = 20000) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ctl.signal });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    return await r.json();
  } finally { clearTimeout(t); }
}

async function fetchForecast() {
  const p = new URLSearchParams({
    latitude: SPOTS.map(s => s.lat).join(","), longitude: SPOTS.map(s => s.lon).join(","),
    hourly: "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
    daily: "sunrise,sunset", models: Object.values(MODELS).join(","),
    wind_speed_unit: "kn", timezone: "Europe/Berlin", forecast_days: "7"
  });
  let res = await fetchJSON("https://api.open-meteo.com/v1/forecast?" + p);
  if (res && res.error) throw new Error(res.reason || "Open-Meteo-Fehler");
  if (!Array.isArray(res)) res = [res];
  if (res.length < SPOTS.length) throw new Error(`Open-Meteo lieferte ${res.length} statt ${SPOTS.length} Orte`);
  // Bei mehreren Modellen hängt Open-Meteo den Modellnamen an jede Variable an (z. B. sunrise_icon_seamless).
  const pick = (obj, name, k) => {
    if (obj[name]) return obj[name][k];
    for (const key of Object.keys(obj)) if (key.startsWith(name + "_") && obj[key] && obj[key][k] != null) return obj[key][k];
    return null;
  };
  const spots = {};
  SPOTS.forEach((s, i) => {
    const h = res[i].hourly, d = res[i].daily;
    if (!h || !h.time) throw new Error("Antwort ohne Stundenwerte");
    const o = { time: h.time, sunrise: {}, sunset: {} };
    (d && d.time ? d.time : []).forEach((day, k) => { o.sunrise[day] = pick(d, "sunrise", k); o.sunset[day] = pick(d, "sunset", k); });
    for (const [key, m] of Object.entries(MODELS)) {
      o[key] = { ws: h[`wind_speed_10m_${m}`], wd: h[`wind_direction_10m_${m}`], gust: h[`wind_gusts_10m_${m}`] };
    }
    o.mean = meanModel(o);
    spots[s.id] = o;
  });
  return { spots, fetched: new Date().toISOString() };
}

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

/* ---------- Auswertung: Stunden am Stück ---------- */
function series(spotId, model) {
  const o = state.fc.spots[spotId];
  return { time: o.time, ws: o[model].ws, wd: o[model].wd, gust: o[model].gust, sunrise: o.sunrise, sunset: o.sunset };
}

function days(spotId, model) {
  const sp = SPOTS.find(s => s.id === spotId);
  const s = series(spotId, model);
  const byDay = new Map();
  s.time.forEach((t, i) => {
    const day = t.slice(0, 10), hour = Number(t.slice(11, 13));
    const rise = s.sunrise[day], set = s.sunset[day];
    const hr = rise ? Number(rise.slice(11, 13)) + Number(rise.slice(14, 16)) / 60 : 7;    // Ersatz, falls keine Sonnenzeiten
    const hs = set ? Number(set.slice(11, 13)) + Number(set.slice(14, 16)) / 60 : 20;
    if (hour < Math.ceil(hr) || hour > Math.floor(hs) - 1) return;
    if (!byDay.has(day)) byDay.set(day, []);
    byDay.get(day).push({ hour, kn: s.ws[i], dir: s.wd[i], gust: s.gust[i] });
  });
  return [...byDay.entries()].map(([date, hours]) => {
    const runs = {};
    for (const thr of THRESHOLDS) runs[thr] = bestRun(hours, sp, thr);
    return { date, hours, runs };
  });
}

function bestRun(hours, sp, thr) {
  let best = { len: 0, start: null, end: null }, cur = null, prev = null;
  for (const h of hours) {
    const ok = h.kn != null && h.dir != null && h.kn >= thr && inWindow(h.dir, sp.win);
    if (ok && cur && prev === h.hour - 1) { cur.len++; cur.end = h.hour; }
    else if (ok) cur = { len: 1, start: h.hour, end: h.hour };
    else cur = null;
    if (cur && cur.len > best.len) best = { ...cur };
    prev = h.hour;
  }
  return best;
}

const runClass = len => len >= 4 ? "r4" : len >= MIN_RUN ? "r2" : len >= 1 ? "r1" : "r0";
const runText = r => r.len ? `${r.len} h` : "–";
const spanText = r => r.len ? `${r.start}–${r.end + 1} Uhr` : "";

/* ---------- lokales Korrekturmodell (Platzhalter bis zum Training) ---------- */
async function loadLocalModel() {
  try {
    const r = await fetch("model/manifest.json", { cache: "no-cache" });
    if (!r.ok) return null;
    return await r.json();
  } catch (e) { return null; }
}
function localRun(spotId, date, thr) {
  return null;   // wird mit dem trainierten 7-Tage-Modell gefüllt
}

/* ---------- Karte ---------- */
function initMap() {
  const map = new maplibregl.Map({
    container: "map",
    style: "https://tiles.openfreemap.org/styles/liberty",
    center: [12.62, 54.43], zoom: 9.6, attributionControl: { compact: true },
    cooperativeGestures: true
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.on("load", () => {
    map.addSource("s2", {
      type: "raster", tileSize: 256, maxzoom: 15,
      tiles: ["https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg"],
      attribution: "Sentinel-2 cloudless – https://s2maps.eu by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020)"
    });
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
    const today = state.fc ? days(sp.id, state.model)[0] : null;
    const r = today ? today.runs[state.thr] : { len: 0 };
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
    el.innerHTML = `${o.dir != null ? arrow(o.dir, "arrow big") : ""}<span class="lab"><b>${Math.round(o.kn)} kn</b> ${o.dir != null ? compass(o.dir) : ""}<small>${st.name}</small></span>`;
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
  const ref = days(SPOTS[0].id, state.model);
  const head = (d, i) => {
    const x = new Date(d.date + "T12:00:00");
    const wd = i === 0 ? "Heute" : x.toLocaleDateString("de-DE", { weekday: "short" });
    return `<th scope="col" class="${isWeekend(d.date) ? "we" : ""}">${wd}<br><small>${x.getDate()}.${x.getMonth() + 1}.</small></th>`;
  };
  let html = `<thead><tr><th scope="col" class="spotcol">Spot</th>${ref.map(head).join("")}</tr></thead><tbody>`;
  for (const sp of SPOTS) {
    const ds = days(sp.id, state.model);
    html += `<tr class="${sp.id === state.spot ? "cur" : ""}"><th scope="row"><button type="button" class="linkbtn" data-spot="${sp.id}">${sp.short}</button></th>`;
    for (const d of ds) {
      const r = d.runs[state.thr];
      const loc = localRun(sp.id, d.date, state.thr);
      html += `<td class="cell ${runClass(r.len)}${isWeekend(d.date) ? " we" : ""}" data-spot="${sp.id}" title="${spanText(r)}"><span class="v">${r.len || "–"}</span>${loc != null ? `<span class="loc">lokal ${loc} h</span>` : ""}</td>`;
    }
    html += "</tr>";
  }
  t.innerHTML = html + "</tbody>";
  t.querySelectorAll("[data-spot]").forEach(e => e.addEventListener("click", () => selectSpot(e.dataset.spot)));
}

/* ---------- Detail eines Spots ---------- */
function drawDetail() {
  const box = $("#detail");
  if (!state.fc) { box.innerHTML = ""; return; }
  const sp = SPOTS.find(s => s.id === state.spot);
  const ds = days(sp.id, state.model);
  const others = Object.fromEntries(["icon", "gfs", "ecmwf"].map(m => [m, days(sp.id, m)]));
  const o = state.obs[sp.ref];
  const rows = ds.map((d, i) => {
    const strip = d.hours.map(h => {
      const cls = h.kn >= 20 ? "s20" : h.kn >= 15 ? "s15" : h.kn >= 12 ? "s12" : h.kn >= 9 ? "s9" : "s0";
      const off = h.dir != null && !inWindow(h.dir, sp.win) ? " off" : "";
      return `<span class="${cls}${off}" title="${h.hour} Uhr: ${Math.round(h.kn)} kn ${compass(h.dir)}, Böe ${Math.round(h.gust ?? 0)} kn"></span>`;
    }).join("");
    const cmp = ["icon", "gfs", "ecmwf"].map(m => `${MODEL_LABEL[m]} ${runText(others[m][i]?.runs[state.thr] ?? { len: 0 })}`).join(" · ");
    return `<div class="day${isWeekend(d.date) ? " we" : ""}">
      <div class="day-h"><b>${dayLabel(d.date, i)}</b><span class="cmp">${state.thr}+ kn: ${cmp}</span></div>
      <div class="strip">${strip}</div>
      <div class="strip-ax"><span>${d.hours[0]?.hour ?? ""} Uhr</span><span>${(d.hours.at(-1)?.hour ?? 0) + 1} Uhr</span></div>
      <div class="runs">${THRESHOLDS.map(thr => `<span class="run ${runClass(d.runs[thr].len)}"><b>${runText(d.runs[thr])}</b> ${thr}+ kn <small>${spanText(d.runs[thr])}</small></span>`).join("")}</div>
    </div>`;
  }).join("");
  box.innerHTML = `
    <div class="panel-head"><h2>${sp.name}</h2>
      <span class="meta">${sp.water} · Richtung ${compass(sp.win[0])} bis ${compass(sp.win[1])} · ${MODEL_LABEL[state.model]}</span></div>
    ${o ? `<p class="obsline">${arrow(o.dir ?? 0)} Gemessen ${STATIONS.find(s => s.id === sp.ref).name}, ${fmtTime(o.time)} Uhr: <b>${Math.round(o.kn)} kn</b> ${o.dir != null ? compass(o.dir) : ""}${o.gust ? `, Böe ${Math.round(o.gust)} kn` : ""}</p>` : ""}
    <div class="days">${rows}</div>
    <div class="legend"><span><i class="sw s9"></i>9–12</span><span><i class="sw s12"></i>12–15</span><span><i class="sw s15"></i>15–20</span><span><i class="sw s20"></i>20+ kn</span><span><i class="sw s12 off"></i>Richtung passt nicht</span></div>`;
}

/* ---------- Messstationen ---------- */
function drawStations() {
  const list = STATIONS.map(st => {
    const o = state.obs[st.id];
    return `<li>${o && o.dir != null ? arrow(o.dir) : '<span class="arrow"></span>'}<b>${st.name}</b>
      <span>${o ? `${Math.round(o.kn)} kn ${o.dir != null ? compass(o.dir) : ""}${o.gust ? ` · Böe ${Math.round(o.gust)}` : ""} <small>${fmtTime(o.time)}</small>` : "keine Daten"}</span></li>`;
  }).join("");
  $("#stations").innerHTML = `<div class="panel-head"><h2 id="stH">Messungen jetzt</h2></div><ul class="stnlist">${list}</ul>
    <h3>Darßer Schwelle (offene See, letzte 7 Tage, m/s)</h3>
    <div class="sill-img"><img src="${SILL.plot}" alt="Windgeschwindigkeit Darßer Schwelle, letzte 7 Tage" loading="lazy"><img src="${SILL.plotDir}" alt="Windrichtung Darßer Schwelle, letzte 7 Tage" loading="lazy"></div>`;
}

/* ---------- Ablauf ---------- */
function renderAll() { drawWeek(); drawDetail(); drawStations(); drawMarkers(); }

function selectSpot(id) {
  state.spot = id; save("spot", id); renderAll();
  $("#detail").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
}

async function refresh() {
  const st = $("#status");
  st.textContent = "Lade Vorhersage und Messwerte …"; st.className = "status";
  $("#refreshBtn").disabled = true;
  const [fc, obs] = await Promise.allSettled([fetchForecast(), fetchObs()]);
  if (fc.status === "fulfilled") state.fc = fc.value;
  if (obs.status === "fulfilled") state.obs = obs.value;
  if (fc.status === "fulfilled") {
    try { localStorage.setItem(CACHE_KEY, JSON.stringify({ fc: state.fc, obs: state.obs })); } catch (e) {}
    st.textContent = `Vorhersage von ${fmtTime(state.fc.fetched)} Uhr · ${Object.keys(state.obs).length} Messstationen`;
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
  $("#modelSel").value = state.model;
  $("#modelSel").addEventListener("change", e => { state.model = e.target.value; save("model", state.model); renderAll(); });
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
    if (c && c.fc) { state.fc = c.fc; state.obs = c.obs || {}; }
  } catch (e) {}
  if (window.maplibregl) initMap();
  else $("#map").innerHTML = '<p class="maperr">Karte nicht verfügbar (offline).</p>';
  renderAll();
  loadLocalModel().then(m => { state.local = m; });
  refresh();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}
document.addEventListener("DOMContentLoaded", init);