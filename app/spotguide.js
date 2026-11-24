/* Wingst – Spotguide: Karte, Klick auf Spot zeigt Richtungen, Welle, Windklima und Ausstattung (OpenStreetMap). */
"use strict";
const $ = s => document.querySelector(s);
const SECT = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"];
const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
const pc = v => v == null ? "–" : Math.round(v * 100) + " %";
const inWindow = (dir, [a, b]) => a <= b ? dir >= a && dir <= b : dir >= a || dir <= b;
let DIRS = [], ANA = null, map = null, markers = [], current = null;
const osmCache = {};

function load(k, d) { try { return localStorage.getItem("wingst-" + k) ?? d; } catch (e) { return d; } }
function save(k, v) { try { localStorage.setItem("wingst-" + k, v); } catch (e) {} }

/* ---------- Karte ---------- */
function initMap() {
  map = new maplibregl.Map({ container: "map", style: "https://tiles.openfreemap.org/styles/liberty",
    center: [12.64, 54.39], zoom: 9.7, attributionControl: { compact: true }, cooperativeGestures: true });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.on("load", () => {
    map.addSource("s2", { type: "raster", tileSize: 256, maxzoom: 15,
      tiles: ["https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2020_3857/default/g/{z}/{y}/{x}.jpg"],
      attribution: "Sentinel-2 cloudless – https://s2maps.eu by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2020)" });
    const firstSymbol = map.getStyle().layers.find(l => l.type === "symbol");
    map.addLayer({ id: "s2", type: "raster", source: "s2", layout: { visibility: "none" } }, firstSymbol && firstSymbol.id);
  });
  for (const sp of SPOTS) {
    const el = document.createElement("button");
    el.type = "button"; el.className = "spot-mk mk-r2"; el.dataset.id = sp.id;
    el.innerHTML = `<span class="dot"></span><span class="lab"><b>${sp.short}</b></span>`;
    el.setAttribute("aria-label", sp.name);
    el.addEventListener("click", ev => { ev.stopPropagation(); select(sp.id, true); });
    markers.push(new maplibregl.Marker({ element: el, anchor: "left", offset: [-8, 0] }).setLngLat([sp.lon, sp.lat]).addTo(map));
  }
}
function setLayer(which) {
  $("#btnMap").classList.toggle("on", which === "map"); $("#btnSat").classList.toggle("on", which === "sat");
  if (map && map.getLayer("s2")) map.setLayoutProperty("s2", "visibility", which === "sat" ? "visible" : "none");
}

/* ---------- Windrose: Eignung je Richtung ---------- */
function verdict(sp, r) {
  if (!r) return { cls: "na", word: "–" };
  if (r.ufer.includes("ablandig")) return { cls: "off", word: "ablandig" };
  if (!inWindow(SECT.indexOf(r.sektor) * 45, sp.win)) return { cls: "na", word: "nicht im Fenster" };
  if (r.unruhe == null || r.n < 10) return { cls: "ok", word: "geht" };
  return r.unruhe <= 5 ? { cls: "top", word: "sauber" } : r.unruhe <= 8 ? { cls: "ok", word: "geht" } : { cls: "gust", word: "böig" };
}
function rose(sp, rows) {
  const C = 90, R0 = 22, R1 = 82, col = { top: "#2f9d5c", ok: "#8fd48a", gust: "#f4d23f", off: "#e8a39a", na: "#d8dee2" };
  let s = `<svg viewBox="0 0 180 180" class="rose" role="img" aria-label="Eignung je Windrichtung">`;
  SECT.forEach((name, i) => {
    const r = rows.find(x => x.sektor === name), v = verdict(sp, r);
    const a0 = (i * 45 - 22.5 - 90) * Math.PI / 180, a1 = (i * 45 + 22.5 - 90) * Math.PI / 180;
    const p = (rad, a) => `${C + rad * Math.cos(a)},${C + rad * Math.sin(a)}`;
    s += `<path d="M${p(R0, a0)} L${p(R1, a0)} A${R1},${R1} 0 0 1 ${p(R1, a1)} L${p(R0, a1)} A${R0},${R0} 0 0 0 ${p(R0, a0)} Z" fill="${col[v.cls]}" stroke="var(--card)" stroke-width="2"><title>${name}: ${v.word}</title></path>`;
    const am = (i * 45 - 90) * Math.PI / 180;
    s += `<text x="${C + 52 * Math.cos(am)}" y="${C + 52 * Math.sin(am) + 4}" text-anchor="middle" class="rlab">${name}</text>`;
  });
  return s + `<circle cx="${C}" cy="${C}" r="${R0 - 3}" fill="var(--card)"/></svg>`;
}

/* ---------- Windklima ---------- */
function bars(vals, color) {
  const W = 340, H = 110, L = 24, B = 16, T = 6, bw = (W - L) / 12, max = 0.75;
  let s = `<svg viewBox="0 0 ${W} ${H}" class="chart">`;
  for (const v of [0, 0.25, 0.5, 0.75]) { const y = T + (1 - v / max) * (H - T - B); s += `<line x1="${L}" x2="${W}" y1="${y}" y2="${y}" class="grid"/><text x="${L - 3}" y="${y + 4}" class="ax" text-anchor="end">${v * 100}</text>`; }
  vals.forEach((v, i) => { const h = v / max * (H - T - B);
    s += `<rect x="${L + i * bw + 2}" y="${H - B - h}" width="${bw - 4}" height="${h}" rx="2" fill="${color}"><title>${MONTHS[i]}: ${pc(v)}</title></rect><text x="${L + i * bw + bw / 2}" y="${H - 4}" class="ax" text-anchor="middle">${MONTHS[i]}</text>`; });
  return s + "</svg>";
}

/* ---------- Ausstattung aus OpenStreetMap ---------- */
const OSM_GROUPS = [
  ["Parken", t => t.amenity === "parking"],
  ["Essen & Trinken", t => ["cafe", "restaurant", "bar", "pub", "fast_food", "ice_cream", "biergarten"].includes(t.amenity)],
  ["Wassersport", t => /kite|surf|water_sports/.test([t.shop, t.sport, t.leisure, t.club, t.name].join(" ").toLowerCase())],
  ["WC", t => t.amenity === "toilets"],
  ["Camping", t => t.tourism === "camp_site" || t.tourism === "caravan_site"]
];
const TYPE_DE = { parking: "Parkplatz", cafe: "Café", restaurant: "Restaurant", bar: "Bar", pub: "Kneipe", fast_food: "Imbiss", ice_cream: "Eis", biergarten: "Biergarten", toilets: "WC", camp_site: "Campingplatz", caravan_site: "Wohnmobilplatz" };
async function amenities(sp) {
  if (osmCache[sp.id]) return osmCache[sp.id];
  const q = `[out:json][timeout:20];(
    nwr(around:1200,${sp.lat},${sp.lon})[amenity~"^(parking|cafe|restaurant|bar|pub|fast_food|ice_cream|biergarten|toilets)$"];
    nwr(around:1500,${sp.lat},${sp.lon})[tourism~"^(camp_site|caravan_site)$"];
    nwr(around:2000,${sp.lat},${sp.lon})[shop=water_sports];
    nwr(around:2000,${sp.lat},${sp.lon})[sport~"kitesurfing|windsurfing|surfing"];
  );out center tags 80;`;
  const r = await fetch("https://overpass-api.de/api/interpreter", { method: "POST", body: "data=" + encodeURIComponent(q),
    headers: { "Content-Type": "application/x-www-form-urlencoded" } });
  if (!r.ok) throw new Error(`OpenStreetMap ${r.status}`);
  const j = await r.json();
  const items = j.elements.map(e => {
    const la = e.lat ?? e.center?.lat, lo = e.lon ?? e.center?.lon;
    const d = Math.hypot((la - sp.lat) * 111.2, (lo - sp.lon) * 111.2 * Math.cos(sp.lat * Math.PI / 180));
    return { t: e.tags || {}, d, url: `https://www.openstreetmap.org/${e.type}/${e.id}` };
  }).sort((a, b) => a.d - b.d);
  return (osmCache[sp.id] = items);
}
function amenityHtml(items) {
  let h = "";
  for (const [lab, test] of OSM_GROUPS) {
    const g = items.filter(x => test(x.t)).slice(0, lab === "Parken" ? 3 : 5);
    if (!g.length) continue;
    h += `<h3>${lab}</h3><ul class="osm">${g.map(x => {
      const type = TYPE_DE[x.t.amenity] || TYPE_DE[x.t.tourism] || "";
      const name = x.t.name || type || "ohne Namen";
      const extra = x.t.amenity === "parking" ? (x.t.fee === "yes" ? "kostenpflichtig" : x.t.fee === "no" ? "kostenlos" : "") : (x.t.opening_hours ? "geöffnet " + x.t.opening_hours : "");
      return `<li><a href="${x.url}" target="_blank" rel="noopener">${name}</a> <small>${type && x.t.name ? type + " · " : ""}${x.d < 1 ? Math.round(x.d * 1000 / 50) * 50 + " m" : x.d.toFixed(1).replace(".", ",") + " km"}${extra ? " · " + extra : ""}</small></li>`;
    }).join("")}</ul>`;
  }
  return h || `<p class="hint">In OpenStreetMap ist im Umkreis nichts eingetragen.</p>`;
}

/* ---------- Detail ---------- */
function select(id, scroll) {
  const sp = SPOTS.find(s => s.id === id); if (!sp) return;
  current = id; save("guide", id);
  markers.forEach(m => m.getElement().classList.toggle("cur", m.getElement().dataset.id === id));
  document.querySelectorAll(".spotpick button").forEach(b => b.classList.toggle("on", b.dataset.id === id));
  const info = SPOT_INFO[id] || { text: "", src: [] };
  const rows = DIRS.filter(r => r.spot === id);
  const site = sp.ref === "darss" ? "darss_ort" : "barth";
  const sf = ANA?.spotfactors?.find(s => s.spot === id);
  const good = SECT.filter(s => ["top"].includes(verdict(sp, rows.find(r => r.sektor === s)).cls));
  const ok = SECT.filter(s => ["ok"].includes(verdict(sp, rows.find(r => r.sektor === s)).cls));
  const flat = rows.filter(r => !r.ufer.includes("ab") && r.welle_cm < 15).map(r => r.sektor);
  const chop = rows.filter(r => !r.ufer.includes("ab") && r.welle_cm >= 30).map(r => r.sektor);
  const tbl = SECT.map(s => {
    const r = rows.find(x => x.sektor === s); if (!r) return "";
    const v = verdict(sp, r);
    const wave = r.ufer.includes("ab") ? "–" : r.welle_cm >= 30 ? `Welle (~${r.welle_cm} cm)` : r.welle_cm >= 15 ? `Kabbel (~${r.welle_cm} cm)` : "flach";
    return `<tr><th>${s}</th><td>${r.ufer}</td><td class="v-${v.cls}">${v.word}</td><td>${wave}</td></tr>`;
  }).join("");
  const c = ANA?.climate?.[site];
  $("#spot").innerHTML = `
    <div class="panel-head"><h2>${sp.name}</h2><span class="meta">${sp.water}</span></div>
    <p>${info.text}</p>
    <div class="guide-grid">
      <figure class="rosebox">${rose(sp, rows)}
        <figcaption><span class="lg lg-top"></span>sauber <span class="lg lg-ok"></span>geht <span class="lg lg-gust"></span>böig <span class="lg lg-off"></span>ablandig <span class="lg lg-na"></span>außerhalb</figcaption></figure>
      <div>
        <ul class="tips">
          ${good.length ? `<li>Am saubersten: <b>${good.join(", ")}</b>${ok.length ? `, auch gut: ${ok.join(", ")}` : ""}.</li>` : ok.length ? `<li>Gut: <b>${ok.join(", ")}</b>.</li>` : ""}
          ${flat.length ? `<li>Flach bei ${flat.join(", ")}${chop.length ? `, Welle bei ${chop.join(", ")}` : ""}.</li>` : ""}
          ${sf ? `<li>Auf dem Wasser im Mittel ${Math.round((sf.sar_vs_mean3 - 1) * 100)} % mehr Wind, als die Modelle zeigen.</li>` : ""}
        </ul>
        <a class="btn" href="./" id="toFc">Vorhersage für ${sp.short}</a>
      </div>
    </div>
    <div class="tscroll"><table class="atab"><thead><tr><th>Wind aus</th><th>Ufer</th><th>Wind</th><th>Wasser bei 15 kn</th></tr></thead><tbody>${tbl}</tbody></table></div>
    <p class="hint">„Sauber“ heißt: Auf den Radarbildern war der Wind im Umkreis von 1,5 km sehr gleichmäßig. Welle aus der freien Wasserstrecke gerechnet, nicht gemessen.</p>
    ${c ? `<h3>Wie oft weht es? (Tage mit 14+ kn, ${site === "barth" ? "Bodden, aus Messung Barth" : "Messung Darßer Ort"})</h3>${ANA?.climate_years?.[site] ? boxplot(ANA.climate_years[site][14], "#8fd48a") : bars(c[14], "#8fd48a")}` : ""}
    <h3>Vor Ort</h3><div id="osm"><p class="hint">Lade Daten aus OpenStreetMap …</p></div>
    <p class="hint">Quellen: ${info.src.map(([n, u]) => u ? `<a href="${u}" target="_blank" rel="noopener">${n}</a>` : n).join(" · ")}</p>`;
  $("#toFc").addEventListener("click", () => save("spot", id));
  $("#pick").hidden = true;
  if (scroll) $("#spot").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  amenities(sp).then(items => { if (current === id) $("#osm").innerHTML = amenityHtml(items); })
    .catch(e => { if (current === id) $("#osm").innerHTML = `<p class="hint">OpenStreetMap gerade nicht erreichbar (${e.message}).</p>`; });
}

function init() {
  $("#btnMap").addEventListener("click", () => setLayer("map"));
  $("#btnSat").addEventListener("click", () => setLayer("sat"));
  const pick = document.createElement("div");
  pick.className = "spotpick";
  pick.innerHTML = SPOTS.map(s => `<button type="button" class="seg" data-id="${s.id}">${s.short}</button>`).join("");
  $("#pick").after(pick);
  pick.querySelectorAll("button").forEach(b => b.addEventListener("click", () => select(b.dataset.id, true)));
  if (window.maplibregl) initMap();
  Promise.all([fetch("spot_directions.json").then(r => r.json()), fetch("analyse.json").then(r => r.json())])
    .then(([d, a]) => { DIRS = d; ANA = a; select(load("guide", "zingst"), false); })
    .catch(e => { $("#spot").innerHTML = `<p class="status err">Spotdaten konnten nicht geladen werden (${e.message}).</p>`; });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
}
document.addEventListener("DOMContentLoaded", init);
