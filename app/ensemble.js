/* Wingst – Ensemble-Vorhersagen (Open-Meteo Ensemble API): ~240 Läufe aus 5 Modellen.
   Jeder Lauf wird mit der richtungsabhängigen Abweichung an der Station korrigiert (model/ens_bias.json),
   dann wird gezählt, wie viele Läufe die Schwelle erreichen. Jedes Modell zählt gleich viel. */
"use strict";
const WingstEns = (() => {
  const MODELS = [
    { id: "ecmwf_ifs025_ensemble", key: "ifs", label: "ECMWF IFS", bias: "ecmwf" },
    { id: "ecmwf_aifs025_ensemble", key: "aifs", label: "ECMWF AIFS (KI)", bias: "ecmwf" },
    { id: "google_weathernext2_ensemble", key: "wn2", label: "Google WeatherNext 2 (KI)", bias: null },
    { id: "icon_seamless_eps", key: "icon", label: "DWD ICON-EPS", bias: "icon" },
    { id: "ncep_gefs025", key: "gefs", label: "NOAA GEFS", bias: "gfs" }
  ];
  const SITES = [{ id: "darss_ort", lat: 54.4738, lon: 12.5014 }, { id: "barth", lat: 54.3405, lon: 12.7109 }];
  // Barth misst an Land. Faktor zum Wind auf dem Bodden je Jahreszeit und Ortszeit (model/water_factor.json),
  // gleich wie im lokalen Modell. Bis die Tabelle geladen ist: 1,1 (Tagesmittel).
  let WATER_TABLE = null;
  const setWater = t => { WATER_TABLE = t && t.barth; };
  const season = m => (m >= 3 && m <= 5) ? 1 : (m >= 6 && m <= 8) ? 2 : (m >= 9 && m <= 11) ? 3 : 0;
  function waterAt(site, t) {
    if (site !== "barth") return 1;
    if (!WATER_TABLE) return 1.1;
    return WATER_TABLE[season(Number(t.slice(5, 7)))][Number(t.slice(11, 13))];
  }

  /** Antwort in {time, models:{key:{ws:[Lauf][Stunde], wd:[…]}}} zerlegen. Schlüssel z. B.
      wind_speed_10m_member07_ecmwf_ifs025_ensemble; Kontrolllauf ohne _memberNN. */
  function parse(r) {
    const h = r.hourly, out = { time: h.time, models: {} };
    for (const m of MODELS) out.models[m.key] = { ws: [], wd: [] };
    for (const [k, arr] of Object.entries(h)) {
      const v = k.startsWith("wind_speed_10m") ? "ws" : k.startsWith("wind_direction_10m") ? "wd" : null;
      if (!v || !Array.isArray(arr)) continue;
      const m = MODELS.find(x => k.endsWith("_" + x.id)) || (MODELS.length === 1 ? MODELS[0] : null);
      if (!m) continue;
      const mm = k.match(/_member(\d+)/);
      const idx = mm ? Number(mm[1]) : 0;
      out.models[m.key][v][idx] = arr;
    }
    for (const m of MODELS) {                    // Lücken (fehlende Mitglieder) entfernen
      const o = out.models[m.key];
      const keep = o.ws.map((a, i) => a && o.wd[i] && a.some(x => x != null) ? i : -1).filter(i => i >= 0);
      o.ws = keep.map(i => o.ws[i]); o.wd = keep.map(i => o.wd[i]);
    }
    return out;
  }

  async function fetchAll(fetchJSON) {
    const p = new URLSearchParams({
      latitude: SITES.map(s => s.lat).join(","), longitude: SITES.map(s => s.lon).join(","),
      hourly: "wind_speed_10m,wind_direction_10m", models: MODELS.map(m => m.id).join(","),
      wind_speed_unit: "kn", timezone: "Europe/Berlin", forecast_days: "15"
    });
    let res = await fetchJSON("https://ensemble-api.open-meteo.com/v1/ensemble?" + p, 60000);
    if (res && res.error) throw new Error(res.reason || "Ensemble-Fehler");
    if (!Array.isArray(res)) res = [res];
    const out = {};
    SITES.forEach((s, i) => { out[s.id] = parse(res[i]); out[s.id].site = s.id; });
    return out;
  }

  const sector = d => Math.floor((((d + 22.5) % 360) + 360) % 360 / 45);
  function corrected(ws, wd, bias, siteBias, biasKey) {
    if (ws == null) return null;
    if (!siteBias) return ws;
    let b;
    if (biasKey && siteBias[biasKey]) b = siteBias[biasKey].sector[sector(wd ?? 0)];
    else {   // Modell ohne eigene Statistik: Mittel der drei bekannten
      const ks = Object.keys(siteBias);
      b = ks.reduce((s, k) => s + siteBias[k].sector[sector(wd ?? 0)], 0) / ks.length;
    }
    return Math.max(0, ws + b);
  }
  const median = a => { const v = a.filter(x => x != null).sort((x, y) => x - y); return v.length ? v[Math.floor(v.length / 2)] : null; };

  /**
   * Wahrscheinlichkeiten für einen Spot.
   * ens: Daten des Standorts; win: Richtungsfenster; hoursByDay: Map(date -> [Stunden 0–23 bei Tageslicht]).
   * Rückgabe: { hourly: {time: {thr: p}}, daily: {date: {thr: {p, byModel:{key:p}}}}, median: {key: {time: kn}} }
   */
  function probabilities(ens, siteBias, win, hoursByDay, thresholds, minRun, inWindow) {
    const idx = {}; ens.time.forEach((t, i) => { idx[t] = i; });
    const wf = ens.time.map(t => waterAt(ens.site, t));
    const corr = (ws, wd, b, sb, key, i) => { const v = corrected(ws, wd, b, sb, key); return v == null ? null : v * wf[i]; };
    const hourly = {}, daily = {}, med = {};
    for (const m of MODELS) med[m.key] = {};
    for (const [date, hours] of hoursByDay) {
      daily[date] = {};
      const ii = hours.map(h => idx[`${date}T${String(h).padStart(2, "0")}:00`]);
      for (const thr of thresholds) {
        const byModel = {};
        for (const m of MODELS) {
          const o = ens.models[m.key];
          if (!o.ws.length) continue;
          let hit = 0, n = 0;
          o.ws.forEach((wsArr, k) => {
            let run = 0, best = 0, valid = 0;
            ii.forEach((i, j) => {
              if (i == null) { run = 0; return; }
              const ws = corr(wsArr[i], o.wd[k][i], null, siteBias, m.bias, i), wd = o.wd[k][i];
              if (ws == null || wd == null) { run = 0; return; }
              valid++;
              const ok = ws >= thr && inWindow(wd, win);
              run = ok && j > 0 && hours[j] === hours[j - 1] + 1 ? run + 1 : ok ? 1 : 0;
              best = Math.max(best, run);
            });
            if (valid >= Math.min(4, ii.length)) { n++; if (best >= minRun) hit++; }
          });
          if (n) byModel[m.key] = hit / n;
        }
        const ks = Object.keys(byModel);
        daily[date][thr] = { p: ks.length ? ks.reduce((s, k) => s + byModel[k], 0) / ks.length : null, byModel };
      }
      // stündlich: Anteil der Läufe über Schwelle und aus passender Richtung, Modelle gleich gewichtet
      hours.forEach((h, j) => {
        const i = ii[j]; if (i == null) return;
        const t = `${date}T${String(h).padStart(2, "0")}:00`;
        hourly[t] = {};
        for (const thr of thresholds) {
          const fr = [];
          for (const m of MODELS) {
            const o = ens.models[m.key]; if (!o.ws.length) continue;
            let hit = 0, n = 0;
            o.ws.forEach((wsArr, k) => {
              const ws = corr(wsArr[i], o.wd[k][i], null, siteBias, m.bias, i), wd = o.wd[k][i];
              if (ws == null || wd == null) return;
              n++; if (ws >= thr && inWindow(wd, win)) hit++;
            });
            if (n) fr.push(hit / n);
          }
          hourly[t][thr] = fr.length ? fr.reduce((a, b) => a + b, 0) / fr.length : null;
        }
        for (const m of MODELS) {
          const o = ens.models[m.key];
          med[m.key][t] = median(o.ws.map(a => a[i]));
        }
      });
    }
    return { hourly, daily, median: med };
  }

  return { MODELS, SITES, fetchAll, probabilities, parse, setWater };
})();
if (typeof module !== "undefined") module.exports = WingstEns;
