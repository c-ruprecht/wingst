/* Windanalyse: Unterseiten (Übersicht, Satellit, Welle Ostsee), Satellit im Verhältnis zu den Stationen,
   Welle an der Ostsee. Daten: satellit.json (training/sat_station_analysis.py), welle.json (training/ostsee_waves.py).
   Nutzt $, put, SECT, SPOT_NAME aus analyse.js. */
"use strict";

/* ---------- Unterseiten ---------- */
const TABS = ["uebersicht", "satellit", "welle"];
function showTab(t) {
  if (!TABS.includes(t)) t = "uebersicht";
  document.querySelectorAll("[data-tab]").forEach(el => {
    if (el.tagName === "A") { el.classList.toggle("on", el.dataset.tab === t); el.setAttribute("aria-current", el.dataset.tab === t ? "true" : "false"); }
    else el.hidden = el.dataset.tab !== t;
  });
}
window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
showTab(location.hash.slice(1));

const OST = ["prerow", "zingst"], BOD = ["born", "saal", "pruchten", "dabitz"];
const REFNAME = { darss: "Darßer Ort", barth: "Barth" };
const ownRef = sp => OST.includes(sp) ? "darss" : "barth";
const k2 = v => v == null ? "–" : v.toFixed(2).replace(".", ",");
const pct = v => v == null ? "–" : `${v >= 1 ? "+" : "−"}${Math.abs(Math.round((v - 1) * 100))} %`;
const rcls = v => v == null ? "dna" : v >= 1.3 ? "q3" : v >= 1.1 ? "q2" : v >= 0.9 ? "" : v >= 0.75 ? "q1" : "q0";
const med = o => o && o.median != null ? o.median : null;
const LONG = { N: "Nord", NO: "Nordost", O: "Ost", SO: "Südost", S: "Süd", SW: "Südwest", W: "West", NW: "Nordwest" };

let SAT = null, WAVE = null, satRef = "own", waveSpot = "prerow";

/* ---------- Satellit ---------- */
function drawSatIntro() {
  const m = SAT.meta, A = SAT.areas, st = SAT.stationen;
  const hrs = Object.entries(m.uhrzeiten).map(([h, n]) => [+h, n]);
  const morning = hrs.filter(([h]) => h < 12).map(([h]) => h), evening = hrs.filter(([h]) => h >= 12).map(([h]) => h);
  const od = A.Ostsee.darss.richtung, bb = A.Bodden.barth.richtung, sd = st.richtung;
  const span = (r, ks) => ks.map(k => med(r[k])).filter(v => v != null);
  const rng = a => a.length ? `${k2(Math.min(...a))}–${k2(Math.max(...a))}` : "–";
  put("#satIntro", `
    <p>Sentinel-1 ist ein Radarsatellit. Er misst, wie rau die Wasseroberfläche ist, und daraus lässt sich der Wind in 10 m Höhe berechnen,
    auch durch Wolken. Wir werten jedes Bild in 300 m Auflösung selbst aus. Für die Spots nehmen wir den Median im Umkreis von 1,5 km.
    Ausgewertet sind <b>${m.bilder} Überflüge</b> von ${m.von.replace("-", "/")} bis ${m.bis.replace("-", "/")}, immer gegen
    ${Math.min(...morning)}–${Math.max(...morning) + 1} Uhr und ${Math.min(...evening)}–${Math.max(...evening) + 1} Uhr. Mittags gibt es keine Bilder.</p>
    <ul class="tips">
      <li><b>Ostsee:</b> Am Strand von Prerow und Zingst weht es im Mittel ${pct(med(A.Ostsee.darss.gesamt))} gegenüber dem Darßer Ort.
        Bei Wind aus West bis Nordwest etwas weniger (${rng(span(od, ["W", "NW"]))}), bei Nordost bis Südost dagegen deutlich mehr (${rng(span(od, ["NO", "O", "SO"]))}):
        Bei Ostwind steht der Mast am Darßer Ort im Windschatten des Darßwaldes und misst zu wenig.</li>
      <li><b>Bodden:</b> Auf dem Wasser weht es im Mittel ${pct(med(A.Bodden.barth.gesamt))} gegenüber der Messung in Barth, je nach Richtung ${rng(span(bb, SECT))}.
        Barth steht an Land am Flugplatz, das Wasser bremst den Wind weniger.</li>
      <li><b>Die beiden Stationen untereinander:</b> Darßer Ort misst bei West und Nordwest ${rng(span(sd, ["SW", "W", "NW"]))}-mal so viel wie Barth, bei Ost ${rng(span(sd, ["NO", "O", "SO"]))}-mal.
        Welche Station für einen Spot „stimmt“, hängt also stark von der Windrichtung ab.</li>
      ${m.eis_ueberfluege ? `<li><b>Eis:</b> Im Winter 2026 war der Bodden zeitweise zugefroren. Auf Eis liest das Radar fast Windstille.
        Die ${m.eis_ueberfluege} Überflüge, bei denen der Bodden im Winter unter 75 % von Barth lag, zählen deshalb für den Bodden nicht mit, die Ostsee bleibt drin.</li>` : ""}
      <li><b>Wie eng es zusammenhängt:</b> Die Werte am Spot und an der Station hängen mit r = ${[...OST, ...BOD].map(s => SAT.spots[s]?.[ownRef(s)]?.r).filter(Boolean).reduce((a, b) => Math.min(a, b), 9).toFixed(2).replace(".", ",")}–${[...OST, ...BOD].map(s => SAT.spots[s]?.[ownRef(s)]?.r).filter(Boolean).reduce((a, b) => Math.max(a, b), 0).toFixed(2).replace(".", ",")} zusammen.
        Ein einzelnes Bild ist auf etwa ±2 kn genau, im Mittel über viele Bilder ist das Verhältnis aber belastbar.</li>
    </ul>`);
}

function drawSatStations() {
  const row = sp => {
    const o = SAT.spots[sp]; if (!o) return "";
    const ref = ownRef(sp), x = o[ref], g = x.gesamt;
    const s = k => { const v = med(x.saison[k]); return `<td class="${rcls(v)}">${k2(v)}</td>`; };
    const t = k => { const v = med(x.tageszeit[k]); return `<td class="${rcls(v)}">${k2(v)}</td>`; };
    return `<tr><th>${SPOT_NAME[sp]}</th><td>${REFNAME[ref]}</td>
      <td class="${rcls(g.median)}"><b>${k2(g.median)}</b> <small>(${k2(g.q25)}–${k2(g.q75)})</small></td><td>${x.r.toFixed(2).replace(".", ",")}</td>
      ${t("morgens")}${t("abends")}${s("Winter")}${s("Frühling")}${s("Sommer")}${s("Herbst")}
      <td class="${rcls(med(o.icon.gesamt))}">${k2(med(o.icon.gesamt))}</td><td>${g.n}</td></tr>`;
  };
  const st = SAT.stationen.darss_zu_barth;
  put("#satStations", `<table class="atab num-c"><thead><tr><th>Spot</th><th>Station</th><th>Verhältnis</th><th>r</th>
    <th>morgens</th><th>abends</th><th>Winter</th><th>Frühling</th><th>Sommer</th><th>Herbst</th><th title="Satellit geteilt durch ICON am Spot">zu ICON</th><th>Bilder</th></tr></thead><tbody>
    <tr class="grp"><th colspan="12">Ostsee</th></tr>${OST.map(row).join("")}
    <tr class="grp"><th colspan="12">Bodden</th></tr>${BOD.map(row).join("")}
    <tr class="fa"><th>Darßer Ort</th><td>Barth</td><td>${k2(st.median)} <small>(${k2(st.q25)}–${k2(st.q75)})</small></td><td colspan="9">die beiden Messstationen zu den Überflugzeiten</td></tr>
    </tbody></table>
    <p class="hint">Morgens und abends liegen fast gleich, das Verhältnis hängt also kaum von der Tageszeit ab, soweit der Satellit sie sieht. Im Frühling ist der Bodden gegenüber Barth am schwächsten:
    Das Wasser ist kalt, die Luft darüber stabil und gebremst. „Zu ICON“: Das Wettermodell rechnet an den Spots im Mittel zu wenig Wind, an Pruchten und Saal fast richtig.</p>`);
}

function drawSatDirs() {
  const cell = (o, refKey) => {
    const r = o?.[refKey]?.richtung; if (!r) return SECT.map(() => `<td class="dna">–</td>`).join("");
    return SECT.map(k => { const c = r[k], v = med(c); return v == null ? `<td class="dna"><small>${c ? c.n : 0}</small></td>`
      : `<td class="${rcls(v)}" title="${c.n} Bilder, mittlere Hälfte ${k2(c.q25)}–${k2(c.q75)}">${k2(v)}</td>`; }).join("");
  };
  const row = sp => { const ref = satRef === "own" ? ownRef(sp) : satRef; return `<tr><th>${SPOT_NAME[sp]} <small>/ ${REFNAME[ref]}</small></th>${cell(SAT.spots[sp], ref)}</tr>`; };
  const area = (name, spots) => { const ref = satRef === "own" ? ownRef(spots[0]) : satRef;
    return `<tr class="sum"><th>${name} <small>/ ${REFNAME[ref]}</small></th>${cell(SAT.areas[name], ref)}</tr>`; };
  const st = SAT.stationen.richtung;
  put("#satDirs", `<table class="atab dirtab"><thead><tr><th>Spot / Station</th>${SECT.map(s => `<th>${s}</th>`).join("")}</tr></thead><tbody>
    ${OST.map(row).join("")}${area("Ostsee", OST)}${BOD.map(row).join("")}${area("Bodden", BOD)}
    <tr class="fa"><th>Darßer Ort <small>/ Barth</small></th>${SECT.map(k => `<td>${k2(med(st[k]))}</td>`).join("")}</tr>
    </tbody></table>
    <p class="hint">Grün: am Spot mehr Wind als an der Station, orange/rot: weniger. Kleine graue Zahl: zu wenig Bilder (Anzahl). Bei Nordwind gibt es am Bodden kaum Bilder mit genug Wind.
    Mit der Maus über einer Zelle stehen Bildzahl und Streuung.</p>`);
}

function drawSatUse() {
  put("#satUse", `
    <p>Die Satellitenbilder fließen an vier Stellen ein:</p>
    <ul class="tips">
      <li><b>Lokales Modell (Tageschancen ab 14/18 kn):</b> Gelernt wird an den Messungen Darßer Ort und Barth. Für den Bodden wird Barth vorher in Wind auf dem Wasser umgerechnet:
        Barth × Wasserfaktor je Jahreszeit und Uhrzeit. Den Grundwert dieses Faktors liefern die Satellitenbilder (Bodden im Verhältnis zur Ostsee), den Tagesgang die beiden Stationen.
        „14 kn am Bodden“ heißt deshalb 14 kn auf dem Wasser, nicht in Barth. Unterschiede zwischen den Bodden-Spots, etwa Saal gegenüber Pruchten, stecken in den Prozentzahlen nur über das Richtungsfenster des Spots, nicht über eigene Faktoren.</li>
      <li><b>Ensemble (ab Tag 3 am Bodden, ab Tag 5 an der Ostsee, Woche 2):</b> Jeder Ensemble-Lauf wird am Bodden mit demselben Wasserfaktor umgerechnet, bevor gezählt wird, wie viele Läufe über der Schwelle liegen.</li>
      <li><b>Windanimation auf der Karte:</b> Das ICON-Feld wird über dem Wasser je Gitterpunkt und Windrichtung mit dem Verhältnis Satellit zu ICON korrigiert, danach mit dem lokalen Windstärke-Modell verschoben.</li>
      <li><b>Jetzt-korr.:</b> Wind am Spot jetzt = aktuelle Messung an der Station × Satellitenfaktor des Spots für die gemessene Windrichtung (Tabelle „Je Windrichtung“).
        Am Bodden kommt der Tagesgang dazu: Die Bilder entstehen morgens und abends, mittags gleichen sich Land und Wasser an, deshalb wird der Faktor mittags kleiner.
        Die Abweichung zu ICON am Spot klingt danach ab, an der Ostsee über etwa 20 Stunden, am Bodden über etwa 3 Stunden.
        Bei Ostwind an der Ostsee macht das den größten Unterschied: Der Darßer Ort misst dann 40–50 % zu wenig, Jetzt-korr. rechnet das jetzt heraus.
        Mit der Maus über „Jetzt-korr.“ in der Tagesansicht steht die Rechnung, zum Beispiel „12 kn × 1,44 = 17 kn“.</li>
    </ul>`);
}

/* ---------- Welle Ostsee ---------- */
const NOTE = ["nein", "möglich", "gut", "sehr gut"], NCLS = ["dna", "q1", "q2", "q3"];
function waveRose(sp) {
  const W = WAVE.spots[sp], R = 120, cx = 140, cy = 140;
  const col = ["#cfd6d9", "#f2d33b", "#8fd17a", "#2e9e55"];
  const pol = (b, r) => [cx + r * Math.sin(b * Math.PI / 180), cy - r * Math.cos(b * Math.PI / 180)];
  let s = `<svg viewBox="0 0 280 280" class="rose" role="img" aria-label="Eignung für Downwind auf der Welle je Windrichtung, ${SPOT_NAME[sp]}">`;
  for (const d of W.richtungen) {
    const r = 28 + (R - 28) * Math.log10(1 + d.fetch_km) / Math.log10(401);
    const [x1, y1] = pol(d.grad - 10.5, r), [x2, y2] = pol(d.grad + 10.5, r), [a1, b1] = pol(d.grad - 10.5, 26), [a2, b2] = pol(d.grad + 10.5, 26);
    s += `<path d="M${a1},${b1} L${x1},${y1} A${r},${r} 0 0 1 ${x2},${y2} L${a2},${b2} A26,26 0 0 0 ${a1},${b1} Z" fill="${col[d.note]}" stroke="var(--card)" stroke-width="1">
      <title>Wind aus ${d.richtung}: ${d.ufer}, Anlauf ${d.fetch_km} km, ${NOTE[d.note]}</title></path>`;
  }
  // Strand: Linie quer zur Normalen, Pfeil aufs Meer
  const n = W.normal, [l1x, l1y] = pol(n - 90, 22), [l2x, l2y] = pol(n + 90, 22), [px, py] = pol(n, 24);
  s += `<line x1="${l1x}" y1="${l1y}" x2="${l2x}" y2="${l2y}" stroke="#c9a46a" stroke-width="5" stroke-linecap="round"/>
        <circle cx="${px}" cy="${py}" r="3" fill="#2b6fa8"/>`;
  for (const [t, b] of [["N", 0], ["O", 90], ["S", 180], ["W", 270]]) { const [x, y] = pol(b, R + 12); s += `<text x="${x}" y="${y + 4}" text-anchor="middle" class="rose-lab">${t}</text>`; }
  return s + `</svg><p class="hint">Fläche zeigt, woher der Wind kommt. Länge zeigt die Anlauflänge über dem Wasser (logarithmisch), Farbe die Eignung. Der braune Strich ist der Strand, der blaue Punkt liegt auf der Seeseite.</p>`;
}

function waveBest(sp) {
  const W = WAVE.spots[sp].richtungen;
  const list = n => W.filter(d => d.note === n);
  const say = ds => ds.map(d => `${d.richtung} (${d.welle["18"][0].toFixed(1).replace(".", ",")} m, ${d.welle["18"][1].toFixed(0)} s, Downwind nach ${d.downwind_nach})`).join(", ");
  const top = list(3), good = list(2);
  const days = ds => ds.reduce((a, d) => a + (d.tage_pro_jahr || 0), 0);
  const most = [...W].sort((a, b) => (b.tage_pro_jahr || 0) - (a.tage_pro_jahr || 0))[0];
  return `<li><b>Am besten:</b> ${top.length ? say(top) : "keine Richtung"}. Wellenhöhe und Periode gelten bei 18 kn.</li>
    ${good.length ? `<li><b>Gut:</b> ${say(good)}.</li>` : ""}
    <li><b>Wie oft:</b> An etwa ${days(top) + days(good)} Tagen im Jahr weht es aus einer dieser Richtungen mindestens 2 h am Stück ab 14 kn.
      Am häufigsten kommt der Wind aber aus ${most.richtung} (${most.tage_pro_jahr} Tage), dort ${most.ufer}: ${most.text.split(" – ")[1] || most.text}.</li>`;
}

function waveTable(sp) {
  const W = WAVE.spots[sp].richtungen;
  const w = (d, k) => `<span class="nw">${d.welle[k][0].toFixed(1).replace(".", ",")} m <small>${d.welle[k][1].toFixed(0)} s</small></span>`;
  return `<table class="atab wavetab"><thead><tr><th>Wind aus</th><th>Lage zum Strand</th><th>Anlauf</th><th>Welle bei 14 kn</th><th>18 kn</th><th>22 kn</th>
    <th>Downwind</th><th>Eignung</th><th title="Tage pro Jahr mit mind. 2 h am Stück ab 14 kn bei Tageslicht, Darßer Ort mit Satellitenfaktor auf den Strand umgerechnet">Tage/Jahr</th></tr></thead><tbody>
    ${W.map(d => `<tr><th>${d.richtung}</th><td>${d.ufer}</td><td>${d.fetch_km} km</td><td>${w(d, "14")}</td><td>${w(d, "18")}</td><td>${w(d, "22")}</td>
      <td>${d.note && d.ufer !== "auflandig" ? `nach ${d.downwind_nach}` : "–"}</td><td class="${NCLS[d.note]}"><b>${NOTE[d.note]}</b><br><small>${d.text.split(" – ")[1] || ""}</small></td>
      <td>${d.tage_pro_jahr ?? "–"}</td></tr>`).join("")}</tbody></table>`;
}

function drawWave() {
  $("#waveRose").innerHTML = waveRose(waveSpot);
  $("#waveBest").innerHTML = waveBest(waveSpot);
  put("#waveTable", waveTable(waveSpot));
}

function drawWaveIntro() {
  const P = WAVE.spots.prerow, Z = WAVE.spots.zingst;
  put("#waveIntro", `
    <p>Für Downwind mit dem Wing auf der Welle braucht es drei Dinge: Wind <b>schräg auflandig</b>, also 30–70° zur Strandlinie, damit Welle und Fahrtrichtung parallel zum Ufer laufen.
    Dazu einen <b>langen Anlauf</b> über offenem Wasser, damit sich saubere Wellenzüge mit längerer Periode bilden. Und <b>gleichmäßigen Wind</b>.
    Der Strand von Prerow schaut nach ${P.normal}° (NNO), der von Zingst nach ${Z.normal}° (N). Dieselbe Windrichtung trifft die beiden Strände also etwas verschieden.</p>
    <ul class="tips">
      <li><b>Anlauf:</b> von 500 m vor dem Strand bis zur nächsten Küste, gemessen auf einer Land-Wasser-Karte mit etwa 1 km Auflösung, gemittelt über einen Fächer von ±42° um die Windrichtung.
        Nach Norden reicht das offene Wasser bis Schweden und Bornholm, mehr als 100 km. Nach Nordwesten liegen nach 30–70 km die dänischen Inseln Falster und Møn. Bei Ost bis Südost begrenzen Zingst, Hiddensee und Rügen den Anlauf.</li>
      <li><b>Wellenhöhe und Periode:</b> Windsee nach dem Shore Protection Manual (US Army Corps of Engineers) für tiefes Wasser, durch den Anlauf begrenzt. Sie gilt draußen vor der Sandbank, nachdem der Wind einige Stunden konstant geweht hat.
        Am Strand bricht die Welle an der Sandbank früher und ist dort niedriger. Dünung aus anderen Richtungen ist nicht enthalten. Das Verfahren liegt eher etwas zu hoch, die Werte taugen vor allem zum Vergleich der Richtungen untereinander.</li>
      <li><b>Häufigkeit:</b> gezählt an den Messungen Darßer Ort seit 2021 und mit dem Satellitenfaktor je Richtung auf den Strand umgerechnet. Bei Ostwind wären es am Darßer Ort gemessen sonst viel zu wenige Tage.</li>
    </ul>`);
}

/* ---------- Beispielbilder (training/sat_example.py) ---------- */
function drawSatExamples(list) {
  const n = v => v.toFixed(1).replace(".", ",").replace(",0", ""), f = v => v.toFixed(2).replace(".", ",");
  put("#satExamples", list.map(e => {
    const sp = Object.entries(e.spots);
    const ost = sp.filter(([k]) => OST.includes(k)), bod = sp.filter(([k]) => BOD.includes(k));
    const line = (arr, st, meas) => arr.map(([k, v]) => `${v.name} ${n(v.kn)} kn = ${n(meas)} × ${f(v.faktor)}`).join(", ");
    return `<figure class="satex"><img src="${e.bild}" alt="Satellitenbild ${e.zeit}, Wind aus ${e.richtung}: Messung Darßer Ort ${n(e.darss)} kn, Barth ${n(e.barth)} kn, Satellitenwind an den Spots" loading="lazy">
      <figcaption><b>${e.id === "ost" ? "Ostwind: Ostsee und Bodden etwa gleich stark" : "Westwind: typischer Fall"}, ${e.richtung}, ${e.zeit.slice(8, 10)}.${e.zeit.slice(5, 7)}.${e.zeit.slice(0, 4)} ${e.zeit.slice(11)} Uhr.</b>
      Quadrate: Messung an der Station. Kreise: Satellitenwind auf dem Wasser am Spot, dahinter der Faktor zur Station.
      Ostsee zum Darßer Ort (${n(e.darss)} kn): ${line(ost, "darss", e.darss)}. Bodden zu Barth (${n(e.barth)} kn): ${line(bod, "barth", e.barth)}.
      ${e.windfeld ? "Farbe der Wasserfläche: Windfeld dieses Überflugs." : "Ohne Windfeld im Hintergrund, die Einzelbilder liegen nur lokal vor."}</figcaption></figure>`;
  }).join("") + `<p class="hint">Jetzt-korr. rechnet genauso, nur mit dem mittleren Faktor aus allen Bildern dieser Windrichtung statt mit dem Faktor eines einzelnen Bildes.
    Beispiel Ostwind: Darßer Ort misst 10 kn, Faktor für Prerow bei O im Mittel ${SAT ? f(SAT.spots.prerow.darss.richtung.O.median) : "1,44"} – Jetzt-korr. zeigt für Prerow dann
    ${SAT ? n(10 * SAT.spots.prerow.darss.richtung.O.median) : "14"} kn statt der 10 kn, die ICON plus Messfehler am Darßer Ort ergäbe.</p>`);
}
fetch("sat_beispiele.json").then(r => r.json()).then(drawSatExamples).catch(() => { $("#satExamples").innerHTML = "<p class=\"hint\">Noch keine Beispielbilder (python training/sat_example.py).</p>"; });

/* ---------- Laden ---------- */
function segToggle(id, cb) {
  const g = document.getElementById(id); if (!g) return;
  g.addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return;
    g.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); cb(b.dataset.v); });
}
fetch("satellit.json").then(r => r.json()).then(x => {
  SAT = x; drawSatIntro(); drawSatStations(); drawSatDirs(); drawSatUse();
  segToggle("satRef", v => { satRef = v; drawSatDirs(); });
}).catch(e => { $("#satIntro").innerHTML = `<p>Satellitenauswertung nicht geladen (${e.message}).</p>`; });
fetch("welle.json").then(r => r.json()).then(x => {
  WAVE = x; drawWaveIntro(); drawWave();
  segToggle("waveSpot", v => { waveSpot = v; drawWave(); });
}).catch(e => { $("#waveIntro").innerHTML = `<p>Wellenauswertung nicht geladen (${e.message}).</p>`; });
