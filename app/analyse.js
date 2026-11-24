/* Wingst – Seite „Windanalyse“: liest analyse.json (training/analysis_page.py u. a.) und zeichnet Tabellen/Diagramme. */
"use strict";
const $ = s => document.querySelector(s);
const SITE_NAME = { darss_ort: "Ostsee (Zingst, Prerow)", barth: "Bodden" };
const M = {
  icon: { label: "ICON", color: "#0b6e8a" }, gfs: { label: "GFS", color: "#b26b00" },
  ecmwf: { label: "ECMWF", color: "#7a5cc2" }, mean3: { label: "Mittel der drei", color: "#5a6e77" },
  local: { label: "Lokal", color: "#2f9d5c" }
};
const SECT = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"];
const MONTHS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
const SPOT_NAME = { zingst: "Zingst", prerow: "Prerow", born: "Born", fuhlendorf: "Fuhlendorf", saal: "Saal", pruchten: "Pruchten" };
const f2 = v => v == null ? "–" : v.toFixed(2).replace(".", ",");
const pc = v => v == null ? "–" : Math.round(v * 100) + " %";
const qcls = v => v >= 0.88 ? "q3" : v >= 0.8 ? "q2" : v >= 0.72 ? "q1" : "q0";
let D, thr = 12, relThr = 12;

/* Tabellen seitlich scrollbar machen, damit die Seite auf dem Handy nicht breiter wird */
const put = (sel, html) => { $(sel).innerHTML = html.replace(/<table/g, '<div class="tscroll"><table').replace(/<\/table>/g, "</table></div>"); };
const at = (site, t, lead) => D.skill.find(r => r.site === site && r.thr === t && r.lead === lead);
const avg = (site, leads, k) => {
  const v = leads.map(l => at(site, 12, l)?.[`auc_${k}`]).filter(x => x != null);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
};

/* ---------- Kurz gesagt ---------- */
function drawSummary() {
  const d2 = at("darss_ort", 12, 2), c = D.climate.darss_ort[12];
  const sf = D.spotfactors.map(s => s.sar_vs_mean3).filter(Boolean);
  const lo = Math.round((Math.min(...sf) - 1) * 100), hi = Math.round((Math.max(...sf) - 1) * 100);
  const dirs = D.dirs.darss_ort, sw_w = dirs[5] + dirs[6];
  $("#summary").innerHTML = [
    `Zingst und Prerow: Bis Tag 4 ist die lokale Zahl der beste Wert. ICON allein kündigt zwei Tage vorher nur ${pc(d2.hit_icon)} der kitebaren Tage an, das lokale Modell ${pc(d2.hit_local)}.`,
    `Bodden: Dort ist das Mittel aus ICON, GFS und ECMWF am besten. Die lokale Korrektur bringt nichts, weil die Station Barth an Land steht.`,
    `Ab Tag 5 gibt es nur noch eine Tendenz. Dann hilft das Ensemble-Prozent mehr als jede einzelne Zahl.`,
    `Auf dem Wasser weht es ${lo}–${hi} % mehr, als die Modelle zeigen. Nur bei Südwind ist es weniger.`,
    `Am windigsten ist es von Oktober bis Februar (Januar: ${pc(c[0])} der Tage kitebar), im Sommer etwa jeder zweite Tag. ${pc(sw_w)} des Windes ab 12 kn kommt aus Südwest bis West.`
  ].map(t => `<li>${t}</li>`).join("");
}

/* ---------- Welchem Wert trauen ---------- */
function drawTrust() {
  const groups = [["Tag 1–2", [1, 2]], ["Tag 3–4", [3, 4]], ["Tag 5–6", [5, 6]], ["Tag 7", [7]]];
  const cell = (site, leads) => {
    const c = ["local", "icon", "gfs", "ecmwf", "mean3"].map(k => [k, avg(site, leads, k)]).filter(x => x[1] != null).sort((a, b) => b[1] - a[1]);
    const [k, v] = c[0];
    const alt = c.find(([x]) => x !== k && (k === "local" ? x !== "local" : x === "local"));
    return `<td class="${qcls(v)}"><b>${M[k].label}</b> ${f2(v)}${alt ? `<br><small>${M[alt[0]].label} ${f2(alt[1])}</small>` : ""}</td>`;
  };
  let h = `<table class="atab"><thead><tr><th></th><th>Ostsee</th><th>Bodden</th></tr></thead><tbody>`;
  for (const [lab, leads] of groups) h += `<tr><th>${lab}</th>${cell("darss_ort", leads)}${cell("barth", leads)}</tr>`;
  put("#trust", h + `</tbody></table><p class="hint">Grün: verlässlich (ab 0,88). Gelb: Tendenz. Rot: kaum besser als Zufall. Tag 7 rechnet ICON nicht mehr, die App nimmt dann GFS.</p>`);
}

/* ---------- Trefferquote je Prozentbereich ---------- */
function drawRel() {
  const G = ["1–2", "3–4", "5–7"];
  const sites = relThr >= 20 ? ["darss_ort"] : ["darss_ort", "barth"];
  let h = "";
  for (const site of sites) {
    const R = D.reliability.filter(r => r.site === site && r.thr === relThr);
    const bins = [...new Set(R.map(r => r.bin))];
    h += `<h3>${SITE_NAME[site]}</h3><table class="atab num"><thead><tr><th>App zeigt</th>${G.map(g => `<th>Tag ${g}</th>`).join("")}</tr></thead><tbody>`;
    for (const b of bins) {
      h += `<tr><th>${b}</th>${G.map(g => { const r = R.find(x => x.bin === b && x.leads === g);
        return r && r.n >= 15 ? `<td class="${r.rate >= 0.88 ? "q3" : r.rate >= 0.7 ? "q2" : r.rate >= 0.4 ? "q1" : ""}">${pc(r.rate)} <small>(${r.n})</small></td>` : `<td><small>${r ? r.n : 0} Tage</small></td>`; }).join("")}</tr>`;
    }
    h += "</tbody></table>";
  }
  if (relThr >= 20) h += `<p class="hint">Bodden bei 20+ kn: an 3 % der Tage, zu selten für eine Auswertung.</p>`;
  h += `<p class="hint">Bis Tag 4 stimmt die Anzeige gut. Ab Tag 5 war sie zu sicher, deshalb rechnet die App ab Tag 6 eine Eichung ein.</p>`;
  put("#rel", h);
}

/* ---------- Liniendiagramm ---------- */
function lineChart(site) {
  const R = D.skill.filter(r => r.site === site && r.thr === thr).sort((a, b) => a.lead - b.lead);
  const W = 340, H = 200, L = 34, B = 24, T = 8, Rr = 10;
  const x = l => L + (l - 1) * (W - L - Rr) / 6;
  const y = v => T + (1.0 - v) / 0.4 * (H - T - B);
  let s = `<svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="AUC je Vorlauf, ${SITE_NAME[site]}">`;
  for (const v of [0.6, 0.7, 0.8, 0.9, 1.0]) s += `<line x1="${L}" x2="${W - Rr}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${L - 4}" y="${y(v) + 4}" class="ax" text-anchor="end">${f2(v).slice(0, 3)}</text>`;
  for (let l = 1; l <= 7; l++) s += `<text x="${x(l)}" y="${H - 6}" class="ax" text-anchor="middle">${l}</text>`;
  for (const k of ["gfs", "ecmwf", "icon", "mean3", "local"]) {
    const pts = R.filter(r => r[`auc_${k}`] != null).map(r => [x(r.lead), y(Math.max(0.6, r[`auc_${k}`]))]);
    if (!pts.length) continue;
    s += `<polyline points="${pts.map(p => p.join(",")).join(" ")}" fill="none" stroke="${M[k].color}" stroke-width="${k === "local" ? 3 : k === "mean3" ? 2 : 1.5}" ${k === "mean3" ? 'stroke-dasharray="5 3"' : ""}/>`;
  }
  return s + "</svg>";
}
function drawCharts() {
  const sites = thr >= 20 ? ["darss_ort"] : ["darss_ort", "barth"];
  const legend = `<div class="legend chartlegend">${["local", "icon", "gfs", "ecmwf", "mean3"].map(k => `<span><i class="ln" style="background:${M[k].color}"></i>${M[k].label}</span>`).join("")}</div>`;
  put("#charts", `<div class="charts">${sites.map(s => `<figure><figcaption>${SITE_NAME[s]}</figcaption>${lineChart(s)}</figure>`).join("")}</div>${legend}`);
}

/* ---------- Wind auf dem Wasser je Spot ---------- */
function drawSpots() {
  const cols = ["W", "SW", "O", "S"];
  const order = ["zingst", "prerow", "born", "fuhlendorf", "saal", "pruchten"];
  const R = order.map(k => D.spotfactors.find(s => s.spot === k)).filter(Boolean);
  const fx = v => v == null ? "–" : `${v > 1 ? "+" : ""}${Math.round((v - 1) * 100)} %`;
  let h = `<table class="atab num"><thead><tr><th>Spot</th><th>gesamt</th>${cols.map(l => `<th>${l}</th>`).join("")}</tr></thead><tbody>`;
  for (const r of R) h += `<tr><th>${SPOT_NAME[r.spot]}</th><td><b>${fx(r.sar_vs_mean3)}</b></td>${cols.map(k => `<td>${fx(r["m3_" + k])}</td>`).join("")}</tr>`;
  put("#spots", h + `</tbody></table><p class="hint">Die Modelle rechnen die Spots halb als Land und bremsen den Wind zu stark. Bei Südwind kommt der Wind über Land und ist auch auf dem Wasser schwächer. ICON liegt bis zu 30 % zu tief, GFS fast richtig. Die Bilder entstehen gegen 6 und 18 Uhr, Mittagsthermik ist nicht erfasst.</p>`);
}

/* ---------- Klima ---------- */
function bars(vals, labels, max, color) {
  const W = 340, H = 120, L = 26, B = 18, T = 6, bw = (W - L) / vals.length;
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
  const c = D.climate.darss_ort;
  put("#climate", `<div class="clim">
      <figure><figcaption>Kitebare Tage, 12+ kn (%)</figcaption>${bars(c[12], MONTHS, 0.75, "#8fd48a")}</figure>
      <figure><figcaption>Kitebare Tage, 15+ kn (%)</figcaption>${bars(c[15], MONTHS, 0.75, "#f4d23f")}</figure>
      <figure><figcaption>Windrichtung ab 12 kn (%)</figcaption>${bars(D.dirs.darss_ort, SECT, 0.6, "#0b6e8a")}</figure>
    </div>
    <p class="hint">Südwest bis West ist am Zingster Nordstrand schräg ablandig, am Bodden in Born und Saal ideal. Nordost bringt am Strand auflandigen Wind, ist aber selten.</p>`);
}

function toggle(id, cb) {
  document.querySelectorAll(`#${id} .seg`).forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(`#${id} .seg`).forEach(x => { const on = x === b; x.classList.toggle("on", on); x.setAttribute("aria-pressed", on); });
    cb(Number(b.dataset.thr));
  }));
}

fetch("analyse.json").then(r => r.json()).then(j => {
  D = j;
  drawSummary(); drawTrust(); drawRel(); drawCharts(); drawSpots(); drawClimate();
  toggle("relThr", t => { relThr = t; drawRel(); });
  toggle("thrSel", t => { thr = t; drawCharts(); });
}).catch(e => { $("#summary").innerHTML = `<li>Auswertung konnte nicht geladen werden (${e.message}).</li>`; });
