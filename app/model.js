/* Lokales Korrekturmodell: XGBoost-Bäume in JavaScript auswerten und Merkmale wie im Training berechnen.
   Gegenstück zu training/train_7day.py (features) und training/export_app_models.py (Format). */
"use strict";

const WingstModel = (() => {
  const f32 = Math.fround;

  function predictMargin(model, x) {
    let s = model.base;
    for (const [feat, thr, left, right, defl] of model.trees) {
      let n = 0;
      while (left[n] !== -1) {
        const v = x[feat[n]];
        if (v === null || v === undefined || Number.isNaN(v)) n = defl[n] ? left[n] : right[n];
        else n = f32(v) < thr[n] ? left[n] : right[n];
      }
      s += thr[n];
    }
    return s;
  }
  const predictProba = (model, x) => 1 / (1 + Math.exp(-predictMargin(model, x)));

  /* ---------- Merkmale ---------- */
  const nanmean = a => { const v = a.filter(Number.isFinite); return v.length ? v.reduce((p, c) => p + c, 0) / v.length : null; };
  const nanmax = a => { const v = a.filter(Number.isFinite); return v.length ? Math.max(...v) : null; };
  const nanstd = a => {   // wie pandas: Stichproben-Standardabweichung (ddof = 1)
    const v = a.filter(Number.isFinite); if (v.length < 2) return null;
    const m = v.reduce((p, c) => p + c, 0) / v.length;
    return Math.sqrt(v.reduce((p, c) => p + (c - m) ** 2, 0) / (v.length - 1));
  };
  const num = v => (v === null || v === undefined || Number.isNaN(v)) ? null : v;
  const fb = (...vals) => { for (const v of vals) if (num(v) !== null) return v; return null; };

  /**
   * site: { icon:{ws,wd,gust,ws80,t2m,pmsl,cloud,swrad,cape,precip}, gfs:{…}, ecmwf:{…} } – Arrays je Stunde
   * barth, sea: gleiche Struktur für Barth und Darßer Schwelle (für Land-See-Kontrast und Druckgefälle)
   * sst: Wassertemperatur am Ort (Zahl), seaSst: Wassertemperatur Darßer Schwelle (Zahl)
   * time: lokale Zeitstempel "YYYY-MM-DDTHH:MM", lead: Vorlauftage je Stunde
   */
  const at = (v, i) => Array.isArray(v) ? num(v[i]) : num(v);   // Zahl oder Array je Stunde

  /** Bodden-Wassertemperatur (Ersatz): exponentiell geglättete Lufttemperatur Barth, wie pandas ewm(alpha, adjust=True). */
  function boddenProxy(t2m, tauDays = 5) {
    const a = 1 / (24 * tauDays), q = 1 - a, out = [];
    let num_ = 0, den = 0;
    for (const v of t2m) {
      num_ *= q; den *= q;
      if (num(v) !== null) { num_ += v; den += 1; }
      out.push(den > 0 ? num_ / den : null);
    }
    return out;
  }

  /* sst, seaSst, bod: Zahl oder Array je Stunde (Wert zum Ausgabezeitpunkt) */
  function hourlyFeatures(site, barth, sea, sst, seaSst, time, lead, bod = null) {
    const rows = [];
    for (let i = 0; i < time.length; i++) {
      const r = {};
      for (const m of ["icon", "gfs", "ecmwf"]) {
        const ws = num(site[m].ws[i]), wd = num(site[m].wd[i]);
        r[`${m}_ws10`] = ws;
        r[`${m}_gust`] = num(site[m].gust[i]);
        r[`${m}_u`] = ws !== null && wd !== null ? -ws * Math.sin(wd * Math.PI / 180) : null;
        r[`${m}_v`] = ws !== null && wd !== null ? -ws * Math.cos(wd * Math.PI / 180) : null;
      }
      r.icon_ws80 = num(site.icon.ws80?.[i]);
      r.gfs_ws80 = num(site.gfs.ws80?.[i]);
      const wss = [r.icon_ws10, r.gfs_ws10, r.ecmwf_ws10];
      r.models_mean_ws10 = nanmean(wss);
      r.models_max_ws10 = nanmax(wss);
      r.models_spread_ws10 = nanstd(wss);
      const u = nanmean([r.icon_u, r.gfs_u, r.ecmwf_u]), v = nanmean([r.icon_v, r.gfs_v, r.ecmwf_v]);
      if (u !== null && v !== null) { const a = Math.atan2(-u, -v); r.dir_sin = Math.sin(a); r.dir_cos = Math.cos(a); }
      else { r.dir_sin = null; r.dir_cos = null; }
      const t2m = fb(site.icon.t2m?.[i], site.gfs.t2m?.[i], site.ecmwf.t2m?.[i]);
      r.sst = at(sst, i);
      r.t2m_minus_sst = t2m !== null && r.sst !== null ? t2m - r.sst : null;
      const bt = fb(barth.icon.t2m?.[i], barth.gfs.t2m?.[i]);
      const ss = at(seaSst, i);
      r.land_sea_dT = bt !== null && ss !== null ? bt - ss : null;
      const sp = fb(sea.icon.pmsl?.[i], sea.gfs.pmsl?.[i]), bp = fb(barth.icon.pmsl?.[i], barth.gfs.pmsl?.[i]);
      r.dp_north_south = sp !== null && bp !== null ? sp - bp : null;
      const b = at(bod, i);
      r.bodden_wt = b;
      r.t2m_minus_bodden = t2m !== null && b !== null ? t2m - b : null;
      r.bodden_minus_sea = b !== null && ss !== null ? b - ss : null;
      r.swrad = fb(site.icon.swrad?.[i], site.gfs.swrad?.[i]);
      r.thermal = r.land_sea_dT === null ? null : Math.max(r.land_sea_dT, 0) * (r.swrad ?? 0) / 1000;
      if (u !== null && v !== null) {
        const wsm = Math.max(Math.sqrt(u * u + v * v), 1);
        r.onshore_n = -v / wsm;
        r.stab_onshore = r.t2m_minus_sst === null ? null : r.t2m_minus_sst * r.onshore_n;
      } else { r.onshore_n = null; r.stab_onshore = null; }
      r.cloud = fb(site.icon.cloud?.[i], site.gfs.cloud?.[i], site.ecmwf.cloud?.[i]);
      r.cape = fb(site.icon.cape?.[i], site.gfs.cape?.[i]);
      r.precip = fb(site.icon.precip?.[i], site.gfs.precip?.[i]);
      const hour = Number(time[i].slice(11, 13));
      r.hour_sin = Math.sin(2 * Math.PI * hour / 24); r.hour_cos = Math.cos(2 * Math.PI * hour / 24);
      const d = new Date(time[i].slice(0, 10) + "T12:00:00Z");
      const doy = Math.floor((d - Date.UTC(d.getUTCFullYear(), 0, 0)) / 864e5);
      r.doy_sin = Math.sin(2 * Math.PI * doy / 365.25); r.doy_cos = Math.cos(2 * Math.PI * doy / 365.25);
      r.blh = fb(site.gfs.blh?.[i]);
      r.lead_day = lead[i];
      r.dwd_ws10 = fb(r.icon_ws10, r.gfs_ws10);
      r._hour = hour; r._date = time[i].slice(0, 10);
      rows.push(r);
    }
    return rows;
  }

  const longestRun = flags => { let best = 0, cur = 0; for (const f of flags) { cur = f ? cur + 1 : 0; best = Math.max(best, cur); } return best; };

  /** Tagesmerkmale wie daily_table() im Training (Tageslicht = 7–19 Uhr). */
  function dailyFeatures(rows, p, thr) {
    const days = new Map();
    rows.forEach((r, i) => {
      if (r._hour < 7 || r._hour > 19) return;
      if (!days.has(r._date)) days.set(r._date, []);
      days.get(r._date).push({ r, p: p[i] });
    });
    const out = [];
    for (const [date, hs] of days) {
      if (hs.length < 8) continue;
      const g = k => hs.map(h => h.r[k]);
      out.push({ date, x: {
        icon_max: nanmax(g("dwd_ws10")), icon_mean: nanmean(g("dwd_ws10")),
        mean3_max: nanmax(g("models_mean_ws10")), mean3_mean: nanmean(g("models_mean_ws10")),
        max3_max: nanmax(g("models_max_ws10")), spread_mean: nanmean(g("models_spread_ws10")),
        gust_max: nanmax(g("gfs_gust")), u_mean: nanmean(g("gfs_u")), v_mean: nanmean(g("gfs_v")),
        t2m_sst: nanmean(g("t2m_minus_sst")), land_sea: nanmax(g("land_sea_dT")),
        swrad: nanmean(g("swrad")), cloud: nanmean(g("cloud")), dp: nanmean(g("dp_north_south")),
        bodden_dT: nanmean(g("t2m_minus_bodden")), bodden_sea: g("bodden_minus_sea").find(v => num(v) !== null) ?? null,
        thermal_max: nanmax(g("thermal")),
        doy_sin: hs[0].r.doy_sin, doy_cos: hs[0].r.doy_cos,
        p_hours: hs.filter(h => h.p >= 0.5).length, p_mean: nanmean(hs.map(h => h.p)),
        p_run: longestRun(hs.map(h => h.p >= 0.5)),
        icon_run: longestRun(hs.map(h => (h.r.dwd_ws10 ?? -1) >= thr)),
        lead_day: hs[0].r.lead_day
      }});
    }
    return out;
  }

  const vec = (row, feats) => feats.map(f => (row[f] === undefined ? null : row[f]));

  return { predictMargin, predictProba, hourlyFeatures, dailyFeatures, vec, boddenProxy };
})();

if (typeof module !== "undefined") module.exports = WingstModel;
