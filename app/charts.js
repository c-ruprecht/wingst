/* Wingst – Boxplot je Monat mit einem Punkt pro Jahr (Windklima). */
"use strict";
const MONTHS_SHORT = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

function quantile(sorted, q) {
  const pos = (sorted.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

/** byYear: {"2021": [12 Werte 0–1 oder null], …}; zeichnet Box (25–75 %), Median, Spanne und Jahrespunkte.
    Das jüngste Jahr wird hervorgehoben. */
function boxplot(byYear, color, max = 0.8) {
  const years = Object.keys(byYear || {}).sort();
  if (!years.length) return "";
  const last = years[years.length - 1];
  const W = 340, H = 132, L = 26, B = 30, T = 6, bw = (W - L) / 12;
  const y = v => T + (1 - Math.min(v, max) / max) * (H - T - B);
  let s = `<svg viewBox="0 0 ${W} ${H}" class="chart box">`;
  for (let v = 0; v <= max + 1e-9; v += 0.25) s += `<line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${L - 3}" y="${y(v) + 4}" class="ax" text-anchor="end">${Math.round(v * 100)}</text>`;
  for (let m = 0; m < 12; m++) {
    const cx = L + m * bw + bw / 2;
    const vals = years.map(yr => byYear[yr][m]).filter(v => v != null).sort((a, b) => a - b);
    if (vals.length >= 3) {
      const q1 = quantile(vals, 0.25), q2 = quantile(vals, 0.5), q3 = quantile(vals, 0.75);
      s += `<line x1="${cx}" x2="${cx}" y1="${y(vals[0])}" y2="${y(vals[vals.length - 1])}" class="whisk"/>`;
      s += `<rect x="${cx - bw * 0.32}" y="${y(q3)}" width="${bw * 0.64}" height="${Math.max(1, y(q1) - y(q3))}" rx="2" fill="${color}" fill-opacity=".55"><title>${MONTHS_SHORT[m]}: Median ${Math.round(q2 * 100)} %, mittlere Hälfte ${Math.round(q1 * 100)}–${Math.round(q3 * 100)} %</title></rect>`;
      s += `<line x1="${cx - bw * 0.32}" x2="${cx + bw * 0.32}" y1="${y(q2)}" y2="${y(q2)}" class="med"/>`;
    }
    years.forEach((yr, k) => {
      const v = byYear[yr][m];
      if (v == null) return;
      const dx = years.length > 1 ? (k / (years.length - 1) - 0.5) * bw * 0.5 : 0;
      s += `<circle cx="${cx + dx}" cy="${y(v)}" r="${yr === last ? 2.6 : 1.8}" class="${yr === last ? "pt last" : "pt"}"><title>${yr}: ${Math.round(v * 100)} %</title></circle>`;
    });
    s += `<text x="${cx}" y="${H - B + 12}" class="ax" text-anchor="middle">${MONTHS_SHORT[m]}</text>`;
  }
  s += `<circle cx="${L + 4}" cy="${H - 7}" r="1.8" class="pt"/><text x="${L + 10}" y="${H - 4}" class="ax">je Jahr ${years[0]}–${years[years.length - 2] || years[0]}</text>`;
  s += `<circle cx="${L + 150}" cy="${H - 7}" r="2.6" class="pt last"/><text x="${L + 156}" y="${H - 4}" class="ax">${last}</text>`;
  s += `<rect x="${L + 200}" y="${H - 11}" width="10" height="8" rx="1" fill="${color}" fill-opacity=".55"/><text x="${L + 214}" y="${H - 4}" class="ax">mittlere Hälfte</text>`;
  return s + "</svg>";
}

/* ---- Windrichtung je Monat, gleiche Spaltenbreite wie boxplot() ---- */
const DIR8 = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"];
const BX = { W: 340, L: 26 }, colX = m => BX.L + m * (BX.W - BX.L) / 12 + (BX.W - BX.L) / 24;

/** Mini-Windrose je Monat unter dem Boxplot: Blätter zeigen, woher der Wind kommt (Länge und Deckkraft = Anteil) */
function dirRoses(rows) {
  let s = `<svg viewBox="0 0 ${BX.W} 46" class="chart dirs">`;
  rows.forEach((r, m) => {
    const cx = colX(m), cy = 15, R = 13, mx = Math.max(...r.sec, 0.01);
    s += `<circle cx="${cx}" cy="${cy}" r="${R}" class="rose-bg"/>`;
    r.sec.forEach((p, k) => {
      if (p < 0.02) return;
      const a1 = (k * 45 - 20) * Math.PI / 180, a2 = (k * 45 + 20) * Math.PI / 180, rr = R * Math.sqrt(p / mx);
      s += `<path d="M${cx},${cy} L${cx + rr * Math.sin(a1)},${cy - rr * Math.cos(a1)} A${rr},${rr} 0 0 1 ${cx + rr * Math.sin(a2)},${cy - rr * Math.cos(a2)} Z" class="petal" fill-opacity="${0.35 + 0.65 * p / mx}"><title>${MONTHS_SHORT[m]} ${DIR8[k]}: ${Math.round(p * 100)} %</title></path>`;
    });
  });
  return s + `<text x="${BX.L}" y="42" class="ax">Rosen: woher der Wind kommt (oben = Nord)</text></svg>`;
}
