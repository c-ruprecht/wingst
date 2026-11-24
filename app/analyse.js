/* Wingst – Seite „Windanalyse“: liest analyse.json (training/analysis_page.py) und zeichnet Tabellen/Diagramme. */
"use strict";
const $ = s => document.querySelector(s);
const SITE_NAME = { darss_ort: "Zingst & Prerow (Messung Darßer Ort)", barth: "Bodden (Messung Barth)" };
const SITE_SHORT = { darss_ort: "Ostsee", barth: "Bodden" };
const M = {
  icon: { label: "ICON", color: "#0b6e8a" }, gfs: { label: "GFS", color: "#b26b00" },
  ecmwf: { label: "ECMWF", color: "#7a5cc2" }, mean3: { label: "Mittel der drei", color: "#5a6e77" },
  local: { label: "Lokal", color: "#2f9d5c" }
};
const SECT = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"];
const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
const f2 = v => v == null ? "–" : v.toFixed(2).replace(".", ",");
const pc = v => v == null ? "–" : Math.round(v * 100) + " %";
let D, thr = 12;
/* Tabellen seitlich scrollbar machen, damit die Seite auf dem Handy nicht breiter wird */
const put = (sel, html) => { $(sel).innerHTML = html.replace(/<table/g, '<div class="tscroll"><table').replace(/<\/table>/g, "</table></div>"); };

const rows = (site, t) => D.skill.filter(r => r.site === site && r.thr === t).sort((a, b) => a.lead - b.lead);
const at = (site, t, lead) => D.skill.find(r => r.site === site && r.thr === t && r.lead === lead);
function bestRaw(r) {
  let best = null;
  for (const k of ["icon", "gfs", "ecmwf", "mean3"]) if (r[`auc_${k}`] != null && (!best || r[`auc_${k}`] > r[`auc_${best}`])) best = k;
  return best;
}

/* ---------- Empfehlung ---------- */
function drawTrust() {
  const groups = [["Morgen und übermorgen", [1, 2]], ["Tag 3–4", [3, 4]], ["Tag 5–6", [5, 6]], ["Tag 7", [7]]];
  const avg = (site, leads, k) => {
    const v = leads.map(l => at(site, 12, l)?.[`auc_${k}`]).filter(x => x != null);
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
  };
  const advise = (site, leads) => {
    const cand = ["local", "icon", "gfs", "ecmwf", "mean3"].map(k => [k, avg(site, leads, k)]).filter(x => x[1] != null).sort((a, b) => b[1] - a[1]);
    const [k1, v1] = cand[0];
    const raw = cand.find(([k]) => k !== "local");
    const loc = cand.find(([k]) => k === "local");
    const quality = v1 >= 0.88 ? "verlässlich" : v1 >= 0.8 ? "gute Tendenz" : v1 >= 0.72 ? "grobe Tendenz" : "kaum mehr als Zufall";
    let text = `<b>${M[k1].label}</b> <span class="auc">${f2(v1)}</span>`;
    if (k1 === "local" && raw) text += `<br><small>ohne Korrektur am besten: ${M[raw[0]].label} ${f2(raw[1])}</small>`;
    else if (loc) text += `<br><small>lokal: ${f2(loc[1])}${loc[1] < v1 - 0.01 ? " – bringt hier nichts" : ""}</small>`;
    return `<td class="q${v1 >= 0.88 ? 3 : v1 >= 0.8 ? 2 : v1 >= 0.72 ? 1 : 0}">${text}<br><small>${quality}</small></td>`;
  };
  let h = `<table class="atab"><thead><tr><th></th><th>${SITE_SHORT.darss_ort}<br><small>Zingst, Prerow</small></th><th>${SITE_SHORT.barth}<br><small>Born, Saal, Fuhlendorf, Pruchten</small></th></tr></thead><tbody>`;
  for (const [lab, leads] of groups) h += `<tr><th>${lab}</th>${advise("darss_ort", leads)}${advise("barth", leads)}</tr>`;
  h += "</tbody></table>";
  const d1 = at("darss_ort", 12, 1), d2 = at("darss_ort", 12, 2), b1 = at("barth", 12, 1);
  h += `<ul class="tips">
    <li><b>Ostsee (Zingst, Prerow):</b> Das lokale Modell ist bis Tag 6 klar besser als jedes Rohmodell. ICON allein kündigt ab Tag 2 nur etwa ${pc(d2.hit_icon)} der kitebaren Tage an – es zeigt an der Küste zu wenig Wind, besonders bei West- und Nordwestwind. Das lokale Modell erkennt ${pc(d2.hit_local)}.</li>
    <li><b>Bodden:</b> Hier ist das <b>Mittel aus ICON, GFS und ECMWF</b> an jedem Tag das Beste; das lokale Modell bringt keinen Gewinn (Trefferwert Tag 1: Mittel ${f2(b1.auc_mean3)}, lokal ${f2(b1.auc_local)}). Grund: Die Station Barth steht an Land, und dort treffen die Modelle schon ohne Korrektur gut. Auf dem Wasser weht es etwa 1,3- bis 1,45-mal so stark wie in Barth (Satellitenauswertung).</li>
    <li><b>Ab Tag 5</b> sind alle Modelle nur noch Tendenz. Dann zählt, ob sich die Modelle einig sind: In der Vorhersage zeigen die Ensemble-Zeilen („Ens“, „E %“), wie viele von rund 240 Läufen Wind bringen.</li>
    <li><b>Tag 7:</b> ICON rechnet nur ~6,5 Tage weit; die App nimmt dann GFS. Trefferwert um 0,7 – zum Planen nur als erster Hinweis.</li>
  </ul>`;
  put("#trust", h);
}

/* ---------- Liniendiagramm Trefferwert über Vorlauf ---------- */
function lineChart(site) {
  const R = rows(site, thr);
  const W = 340, H = 210, L = 34, B = 26, T = 10, Rr = 10;
  const x = l => L + (l - 1) * (W - L - Rr) / 6;
  const ymin = 0.6, ymax = 1.0;
  const y = v => T + (ymax - v) / (ymax - ymin) * (H - T - B);
  let s = `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="Trefferwert je Vorlauf, ${SITE_NAME[site]}">`;
  for (const v of [0.6, 0.7, 0.8, 0.9, 1.0]) s += `<line x1="${L}" x2="${W - Rr}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${L - 4}" y="${y(v) + 4}" class="ax" text-anchor="end">${f2(v).slice(0, 3)}</text>`;
  for (let l = 1; l <= 7; l++) s += `<text x="${x(l)}" y="${H - 8}" class="ax" text-anchor="middle">${l}</text>`;
  for (const k of ["gfs", "ecmwf", "icon", "mean3", "local"]) {
    const pts = R.filter(r => r[`auc_${k}`] != null).map(r => [x(r.lead), y(Math.max(ymin, r[`auc_${k}`]))]);
    if (!pts.length) continue;
    const w = k === "local" ? 3 : k === "mean3" ? 2 : 1.5;
    s += `<polyline points="${pts.map(p => p.join(",")).join(" ")}" fill="none" stroke="${M[k].color}" stroke-width="${w}" ${k === "mean3" ? 'stroke-dasharray="5 3"' : ""}/>`;
    pts.forEach(p => { s += `<circle cx="${p[0]}" cy="${p[1]}" r="${k === "local" ? 3 : 2}" fill="${M[k].color}"/>`; });
  }
  s += `<text x="${W - Rr}" y="${H - 8}" class="ax" text-anchor="end" dx="0" dy="0"></text></svg>`;
  return s;
}
function drawCharts() {
  const legend = `<div class="legend chartlegend">${["local", "icon", "gfs", "ecmwf", "mean3"].map(k => `<span><i class="ln" style="background:${M[k].color}"></i>${M[k].label}</span>`).join("")}<span>· waagrecht: Tage im Voraus</span></div>`;
  const sites = thr >= 20 ? ["darss_ort"] : ["darss_ort", "barth"];
  put("#charts", `<div class="charts">${sites.map(s => `<figure><figcaption>${SITE_NAME[s]} · kitebar an ${pc(at(s, thr, 1).rate)} der Tage</figcaption>${lineChart(s)}</figure>`).join("")}</div>`
    + (thr >= 20 ? `<p class="hint">Bodden bei 20+ kn: nur an 3 % der Tage – zu selten für eine Auswertung.</p>` : "") + legend);
  drawHits();
}

/* ---------- Treffer/Fehlalarme ---------- */
function drawHits() {
  const sites = thr >= 20 ? ["darss_ort"] : ["darss_ort", "barth"];
  let h = "";
  for (const s of sites) {
    h += `<h3>${SITE_NAME[s]}, ${thr}+ kn</h3><table class="atab num"><thead><tr><th>Tage voraus</th>${[1, 2, 3, 4, 5, 6, 7].map(l => `<th>${l}</th>`).join("")}</tr></thead><tbody>`;
    for (const [k, lab] of [["icon", "ICON"], ["local", "Lokal"]]) {
      h += `<tr><th>${lab} erkannt</th>${[1, 2, 3, 4, 5, 6, 7].map(l => { const r = at(s, thr, l); return `<td>${pc(r?.[`hit_${k}`])}</td>`; }).join("")}</tr>`;
      h += `<tr class="fa"><th>${lab} Fehlalarm</th>${[1, 2, 3, 4, 5, 6, 7].map(l => { const r = at(s, thr, l); return `<td>${pc(r?.[`fa_${k}`])}</td>`; }).join("")}</tr>`;
    }
    h += "</tbody></table>";
  }
  put("#hits", h);
}

/* ---------- Jahreszeiten ---------- */
function drawSeason() {
  const S = ["Winter", "Frühling", "Sommer", "Herbst"], G = ["1–2", "3–4", "5–7"];
  let h = "";
  for (const site of ["darss_ort", "barth"]) {
    h += `<h3>${SITE_NAME[site]}</h3><table class="atab"><thead><tr><th></th>${G.map(g => `<th>Tag ${g}</th>`).join("")}</tr></thead><tbody>`;
    for (const s of S) {
      h += `<tr><th>${s}</th>`;
      for (const g of G) {
        const r = D.season.find(x => x.site === site && x.thr === 12 && x.season === s && x.leads === g);
        const raw = bestRaw(r);
        const useLocal = r.auc_local != null && r.auc_local > r[`auc_${raw}`] + 0.01;
        const k = useLocal ? "local" : raw, v = r[`auc_${k}`];
        h += `<td class="q${v >= 0.88 ? 3 : v >= 0.8 ? 2 : v >= 0.72 ? 1 : 0}"><b>${M[k].label}</b> ${f2(v)}${useLocal ? `<br><small>roh: ${M[raw].label} ${f2(r[`auc_${raw}`])}</small>` : ""}</td>`;
      }
      h += "</tr>";
    }
    h += `</tbody></table><p class="hint">Kitebar (12+ kn) im ${S.map(s => { const r = D.season.find(x => x.site === site && x.thr === 12 && x.season === s && x.leads === "1–2"); return `${s} ${pc(r.rate)}`; }).join(", ")} der Tage (2024–25).</p>`;
  }
  h += `<p>Auffällig: Im <b>Frühling</b> liegen an der Ostsee alle Rohmodelle schwächer (kühles Wasser, stabile Luft – der Wind kommt unten schlechter an). Im <b>Herbst</b> gewinnt das lokale Modell am meisten, weil ICON dann den Westwind über dem noch warmen Wasser besonders stark unterschätzt.</p>`;
  put("#season", h);
}

/* ---------- Richtungsfehler ---------- */
function drawBias() {
  let h = "";
  for (const site of ["darss_ort", "barth"]) {
    h += `<h3>${SITE_NAME[site]}</h3><table class="atab num"><thead><tr><th></th>${SECT.map(s => `<th>${s}</th>`).join("")}</tr></thead><tbody>`;
    for (const k of ["icon", "gfs", "ecmwf"]) {
      h += `<tr><th>${M[k].label}</th>${D.bias[site][k].sector.map(v => `<td class="${v >= 2 ? "bp" : v <= -2 ? "bn" : ""}">${v > 0 ? "+" : ""}${v.toFixed(1).replace(".", ",")}</td>`).join("")}</tr>`;
    }
    h += "</tbody></table>";
  }
  h += `<p>An der Ostsee zeigen alle Modelle bei <b>Wind von See (SW bis N)</b> 2–6 kn zu wenig – ECMWF am stärksten. Die Modellzelle enthält Land, auf dem der Wind gebremst wird; über dem Wasser fehlt diese Reibung. Bei <b>Wind von Land (O bis S)</b> zeigen ICON und GFS dagegen 1–3 kn zu viel, ECMWF passt dort ungefähr. Am Bodden liegt GFS fast immer 2–3 kn zu hoch, ICON und ECMWF passen ungefähr.</p>`;
  put("#bias", h);
}

/* ---------- Klima ---------- */
function bars(vals, labels, max, color) {
  const W = 340, H = 130, L = 26, B = 18, T = 6, bw = (W - L) / vals.length;
  let s = `<svg viewBox="0 0 ${W} ${H}" class="chart">`;
  for (const v of [0, 0.25, 0.5, 0.75]) if (v <= max) { const y = T + (1 - v / max) * (H - T - B); s += `<line x1="${L}" x2="${W}" y1="${y}" y2="${y}" class="grid"/><text x="${L - 3}" y="${y + 4}" class="ax" text-anchor="end">${Math.round(v * 100)}</text>`; }
  vals.forEach((v, i) => {
    const h = v / max * (H - T - B);
    s += `<rect x="${L + i * bw + 2}" y="${H - B - h}" width="${bw - 4}" height="${h}" rx="2" fill="${color}"><title>${labels[i]}: ${pc(v)}</title></rect>`;
    s += `<text x="${L + i * bw + bw / 2}" y="${H - 5}" class="ax" text-anchor="middle">${labels[i]}</text>`;
  });
  return s + "</svg>";
}
function drawClimate() {
  let h = "";
  for (const site of ["darss_ort", "barth"]) {
    h += `<figure><figcaption>${SITE_NAME[site]}: kitebare Tage je Monat in %</figcaption><div class="clim">`;
    for (const [t, col] of [[12, "#8fd48a"], [15, "#f4d23f"], [20, "#e8644a"]]) {
      if (site === "barth" && t === 20) continue;
      h += `<div><small>${t}+ kn</small>${bars(D.climate[site][t], MONTHS, 0.75, col)}</div>`;
    }
    h += "</div></figure>";
  }
  const c = D.climate.darss_ort[12];
  h += `<p>An der Darßer Ostküste weht es am häufigsten von <b>Oktober bis Februar</b> (Januar: ${pc(c[0])} der Tage mit 12+ kn), im Sommer an etwa ${pc((c[5] + c[6] + c[7]) / 3)} der Tage. Der Juli ist dank Westwindlagen oft besser als Juni und August. Barth misst an Land und zeigt deshalb weniger – auf dem Bodden selbst weht es deutlich mehr.</p>`;
  put("#climate", h);
  put("#dirs", `<div class="clim">${["darss_ort", "barth"].map(s => `<div><small>${SITE_NAME[s]}</small>${bars(D.dirs[s], SECT, 0.6, "#0b6e8a")}</div>`).join("")}</div>
    <p>Kitewind kommt hier meist aus <b>Südwest bis West</b> (Darßer Ort 75 %, Barth 70 % der windigen Stunden). Für Zingst (Nordstrand) ist das schräg ablandig bis sideshore; Born und Saal am Bodden liegen dafür ideal. Nordost- und Ostlagen sind seltener, bringen an der Ostsee aber auflandigen Wind.</p>`);
}

/* ---------- Lokales Modell ---------- */
function drawLocal() {
  const d = l => at("darss_ort", 12, l);
  put("#local", `
    <p><b>Was es ist:</b> Ein Entscheidungsbaum-Modell (XGBoost), das für jede Stunde aus ICON, GFS und ECMWF, der Windrichtung, Luft- und Wassertemperatur, Sonne, Druckverteilung und Jahreszeit die Chance auf Wind über der Schwelle berechnet – angelernt an den echten Messungen. Es läuft komplett auf deinem Gerät.</p>
    <p><b>Wo es stark ist:</b> An der Ostsee (Zingst, Prerow) bis Tag 6. Es kennt die typischen Fehler: zu wenig Wind bei See-Wind, zu viel bei Land-Wind, kühle Luft über warmem Wasser im Herbst. Trefferwert morgen ${f2(d(1).auc_local)} statt ${f2(d(1).auc_icon)} (ICON), an Tag 4 ${f2(d(4).auc_local)} statt ${f2(d(4).auc_icon)}. Es erkennt rund ${pc(d(3).hit_local)} der kitebaren Tage drei Tage im Voraus, ICON nur ${pc(d(3).hit_icon)}.</p>
    <p><b>Wo nicht:</b> Am Bodden – die Messstation Barth steht an Land, und dort treffen die Modelle schon gut; das Mittel der drei Modelle ist dort besser. An Tag 7 ist es nicht besser als das Modellmittel. Bei 20+ kn am Bodden gibt es zu wenige Fälle.</p>
    <p><b>Grenzen:</b> Nur zwei Jahre Daten (2024–2025). Die Prozentangaben sind Rangfolgen, keine geprüften Wahrscheinlichkeiten. Richtung und Spot-Ausrichtung prüft die App getrennt. Böen und Wellen kennt es nicht.</p>`);
}

/* ---------- Trefferquote je Prozentbereich ---------- */
let relThr = 12;
function drawRel() {
  const G = ["1–2", "3–4", "5–7"];
  const sites = relThr >= 20 ? ["darss_ort"] : ["darss_ort", "barth"];
  let h = "";
  for (const site of sites) {
    const R = D.reliability.filter(r => r.site === site && r.thr === relThr);
    const bins = [...new Set(R.map(r => r.bin))];
    h += `<h3>${SITE_NAME[site]}, ${relThr}+ kn</h3><table class="atab num"><thead><tr><th>App zeigt</th>${G.map(g => `<th>Tag ${g}</th>`).join("")}</tr></thead><tbody>`;
    for (const b of bins) {
      h += `<tr><th>${b}</th>${G.map(g => { const r = R.find(x => x.bin === b && x.leads === g);
        return r && r.n >= 15 ? `<td class="${r.rate >= 0.88 ? "q3" : r.rate >= 0.7 ? "q2" : r.rate >= 0.4 ? "q1" : ""}">${pc(r.rate)} <small>(${r.n})</small></td>` : `<td><small>${r ? r.n : 0} Tage</small></td>`; }).join("")}</tr>`;
    }
    h += "</tbody></table>";
  }
  const r = (s, b, g) => D.reliability.find(x => x.site === s && x.thr === 12 && x.bin === b && x.leads === g);
  const a = r("darss_ort", "90–100 %", "1–2"), c = r("darss_ort", "90–100 %", "5–7");
  h += `<p>Lesebeispiel Ostsee, 12 kn: Zeigt die App für morgen oder übermorgen über 90 %, war es in ${pc(a.rate)} der Fälle kitebar. Für Tag 5–7 heißt dieselbe Anzeige nur noch ${pc(c.rate)}. Ab Tag 6 rechnet die App deshalb eine Eichung ein; davor sind die Werte schon so gut geeicht, dass eine Eichung sie in der Prüfung verschlechtert hat.</p>`;
  put("#rel", h);
}

function setThr(t) {
  thr = t;
  document.querySelectorAll("#thrSel .seg").forEach(b => { const on = Number(b.dataset.thr) === t; b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); });
  drawCharts();
}

fetch("analyse.json").then(r => r.json()).then(j => {
  D = j;
  drawTrust(); drawCharts(); drawRel(); drawSeason(); drawBias(); drawClimate(); drawLocal();
  document.querySelectorAll("#relThr .seg").forEach(b => b.addEventListener("click", () => {
    relThr = Number(b.dataset.thr);
    document.querySelectorAll("#relThr .seg").forEach(x => { const on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-pressed", on); });
    drawRel(); }));
  document.querySelectorAll("#thrSel .seg").forEach(b => b.addEventListener("click", () => setThr(Number(b.dataset.thr))));
}).catch(e => { put("#trust", `<p class="status err">Auswertung konnte nicht geladen werden (${e.message}).</p>`); });
