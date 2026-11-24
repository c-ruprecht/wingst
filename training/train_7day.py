"""7-Tage-Modell: lernt aus früheren Modellläufen (Vorlaufzeit 1–7 Tage), wann es kitebar wird.

Zwei Ebenen:
  stündlich  P(Wind >= 12 / 15 kn) je Stunde, mit Vorlaufzeit als Merkmal
  täglich    P(Tag ist kitebar) = mind. 2 h am Stück >= 12 (bzw. 15) kn bei Tageslicht

Bewertet wird ehrlich: auf 2024 trainieren, 2025 testen, und umgekehrt – getrennt je Vorlaufzeit.

Aufruf: python train_7day.py ../data ../data/dwd ../results ../models
"""
import glob, json, sys
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import roc_auc_score, brier_score_loss
from features import load_obs

DATA, DWD, RES, MODELS = sys.argv[1:5]
SITES = ("darss_ort", "barth")
LEADS = range(1, 8)
YEARS = (2024, 2025)
THRS = (12, 15, 20)
MIN_RUN = 2           # Stunden am Stück für „kitebar“
MOD = {"icon_seamless": "icon", "gfs_seamless": "gfs", "ecmwf_ifs025": "ecmwf"}
HOURLY_PARAMS = dict(n_estimators=400, max_depth=5, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                     min_child_weight=10, tree_method="hist", n_jobs=4)
DAILY_PARAMS = dict(n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8,
                    min_child_weight=5, tree_method="hist", n_jobs=4)


def read_prev(place, model):
    fs = sorted(glob.glob(f"{DATA}/openmeteo_prev/{place}_{model}_*.csv"))
    d = pd.concat([pd.read_csv(f) for f in fs])
    d["time"] = pd.to_datetime(d["time"], utc=True)
    return d.drop_duplicates("time").set_index("time").sort_index()


def read_sst(place):
    fs = sorted(glob.glob(f"{DATA}/openmeteo/{place}_sst_*.csv"))
    d = pd.concat([pd.read_csv(f) for f in fs])
    d["time"] = pd.to_datetime(d["time"], utc=True)
    return d.drop_duplicates("time").set_index("time").sort_index()["sea_surface_temperature"]


BLH = {}
BODDEN_TAU_DAYS = 5   # Satellit Saaler Bodden 2024–25: tau 10 d passt besser (MAE 1,1 statt 1,3 °C), verschlechterte aber die AUC – daher 5 d


def read_bodden_proxy():
    """Ersatz für die Bodden-Wassertemperatur: exponentiell geglättete Lufttemperatur in Barth (ICON-Analyse).
    Die App rechnet dasselbe aus den letzten 10 Tagen (Open-Meteo past_days)."""
    fs = sorted(glob.glob(f"{DATA}/openmeteo/barth_icon_seamless_*.csv"))
    d = pd.concat([pd.read_csv(f) for f in fs])
    d["time"] = pd.to_datetime(d["time"], utc=True)
    t = d.drop_duplicates("time").set_index("time").sort_index()["temperature_2m"].asfreq("h").interpolate(limit=6)
    return t.ewm(alpha=1 / (24 * BODDEN_TAU_DAYS), ignore_na=True).mean()


def read_blh(place):
    """GFS-Grenzschichthöhe der früheren Läufe, falls heruntergeladen (--only blh)."""
    fs = sorted(glob.glob(f"{DATA}/openmeteo_prev/{place}_gfs_blh_*.csv"))
    if not fs:
        return None
    d = pd.concat([pd.read_csv(f) for f in fs])
    d["time"] = pd.to_datetime(d["time"], utc=True)
    return d.drop_duplicates("time").set_index("time").sort_index()


def lead_frame(raw, lead):
    """Spalten *_previous_day{lead} -> {modell}_{variable}."""
    out = {}
    for m, df in raw.items():
        suf = f"_previous_day{lead}"
        for c in df.columns:
            if c.endswith(suf):
                out[f"{MOD[m]}_{c[:-len(suf)]}"] = df[c]
    return pd.DataFrame(out)


def features(site, lead, raw_site, raw_barth, raw_sea, sst_site, sst_sea, bodden):
    f = lead_frame(raw_site, lead)
    b = lead_frame(raw_barth, lead)
    s = lead_frame(raw_sea, lead)
    X = pd.DataFrame(index=f.index)
    for m in ("icon", "gfs", "ecmwf"):
        ws, wd = f.get(f"{m}_wind_speed_10m"), f.get(f"{m}_wind_direction_10m")
        if ws is None:
            continue
        X[f"{m}_ws10"] = ws
        X[f"{m}_gust"] = f.get(f"{m}_wind_gusts_10m")
        X[f"{m}_u"] = -ws * np.sin(np.deg2rad(wd))
        X[f"{m}_v"] = -ws * np.cos(np.deg2rad(wd))
    def fb(var):   # ICON, ersatzweise GFS/ECMWF (ICON reicht nur ~6,5 Tage)
        out = f.get(f"icon_{var}")
        for m in ("gfs", "ecmwf"):
            alt = f.get(f"{m}_{var}")
            if alt is not None:
                out = alt if out is None else out.fillna(alt)
        return out
    X["icon_ws80"] = f.get("icon_wind_speed_80m")
    X["gfs_ws80"] = f.get("gfs_wind_speed_80m")
    ws = X[[c for c in ("icon_ws10", "gfs_ws10", "ecmwf_ws10") if c in X]]
    X["models_mean_ws10"] = ws.mean(axis=1)
    X["models_max_ws10"] = ws.max(axis=1)
    X["models_spread_ws10"] = ws.std(axis=1)
    u = X[[c for c in ("icon_u", "gfs_u", "ecmwf_u") if c in X]].mean(axis=1)
    v = X[[c for c in ("icon_v", "gfs_v", "ecmwf_v") if c in X]].mean(axis=1)
    ang = np.arctan2(-u, -v)          # Herkunftsrichtung der mittleren Strömung
    X["dir_sin"], X["dir_cos"] = np.sin(ang), np.cos(ang)
    X["dwd_ws10"] = X["icon_ws10"].fillna(X.get("gfs_ws10"))   # Referenz „DWD-Modell“ (Tag 7: GFS)
    # Wassertemperatur: Wert zum Ausgabezeitpunkt (Persistenz), kein Blick in die Zukunft
    sst_issue = sst_site.shift(freq=pd.Timedelta(days=lead)).reindex(X.index)
    sea_issue = sst_sea.shift(freq=pd.Timedelta(days=lead)).reindex(X.index)
    X["sst"] = sst_issue
    X["t2m_minus_sst"] = fb("temperature_2m") - sst_issue
    X["land_sea_dT"] = b["icon_temperature_2m"].fillna(b["gfs_temperature_2m"]) - sea_issue
    X["dp_north_south"] = s["icon_pressure_msl"].fillna(s["gfs_pressure_msl"]) - b["icon_pressure_msl"].fillna(b["gfs_pressure_msl"])
    # Bodden-Wassertemperatur (Ersatz) zum Ausgabezeitpunkt
    bod = bodden.shift(freq=pd.Timedelta(days=lead)).reindex(X.index)
    t2m = fb("temperature_2m")
    X["bodden_wt"] = bod
    X["t2m_minus_bodden"] = t2m - bod
    X["bodden_minus_sea"] = bod - sea_issue
    X["swrad"] = fb("shortwave_radiation")
    # Thermik: Land wärmer als See UND Sonne
    X["thermal"] = X["land_sea_dT"].clip(lower=0) * X["swrad"].fillna(0) / 1000
    # Stabilität bei auflandigem Wind (Nordkomponente) – Seewind/Kaltluft über warmem Wasser
    wsm = np.sqrt(u ** 2 + v ** 2).clip(lower=1)
    X["onshore_n"] = -v / wsm
    X["stab_onshore"] = X["t2m_minus_sst"] * X["onshore_n"]
    X["cloud"] = fb("cloud_cover")
    X["cape"] = fb("cape")
    X["precip"] = fb("precipitation")
    loc = X.index.tz_convert("Europe/Berlin")
    hour = loc.hour
    X["hour_sin"], X["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    X["doy_sin"], X["doy_cos"] = np.sin(2 * np.pi * loc.dayofyear / 365.25), np.cos(2 * np.pi * loc.dayofyear / 365.25)
    blh = BLH.get(site)
    if blh is not None:
        X["blh"] = blh[f"boundary_layer_height_previous_day{lead}"].reindex(X.index)
    X["lead_day"] = lead
    X["local_date"] = loc.date
    X["year"] = loc.year
    X["daylight"] = (hour >= 7) & (hour <= 19)
    return X


def longest_run(flags):
    best = cur = 0
    for v in flags:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    return best


def daily_table(H, thr, p_col):
    """Tageswerte aus Stundenwerten (nur Tageslicht)."""
    D = H[H.daylight].groupby(["local_date", "lead_day"])
    agg = D.agg(year=("year", "first"),
                icon_max=("dwd_ws10", "max"), icon_mean=("dwd_ws10", "mean"),
                mean3_max=("models_mean_ws10", "max"), mean3_mean=("models_mean_ws10", "mean"),
                max3_max=("models_max_ws10", "max"), spread_mean=("models_spread_ws10", "mean"),
                gust_max=("gfs_gust", "max"), u_mean=("gfs_u", "mean"), v_mean=("gfs_v", "mean"),
                t2m_sst=("t2m_minus_sst", "mean"), land_sea=("land_sea_dT", "max"),
                swrad=("swrad", "mean"), cloud=("cloud", "mean"), dp=("dp_north_south", "mean"),
                bodden_dT=("t2m_minus_bodden", "mean"), bodden_sea=("bodden_minus_sea", "first"),
                thermal_max=("thermal", "max"),
                doy_sin=("doy_sin", "first"), doy_cos=("doy_cos", "first"), n=("dwd_ws10", "size"),
                p_hours=(p_col, lambda s: float((s >= 0.5).sum())), p_mean=(p_col, "mean"))
    agg["icon_run"] = D.apply(lambda g: longest_run(g.sort_index().dwd_ws10 >= thr))
    # je Modell (für die Auswertung „welches Modell wann“; nicht als Merkmal benutzt)
    for m in ("icon", "gfs", "ecmwf"):
        agg[f"{m}_only_run"] = D.apply(lambda g, m=m: longest_run(g.sort_index()[f"{m}_ws10"] >= thr) if g[f"{m}_ws10"].notna().any() else np.nan)
        agg[f"{m}_only_max"] = D[f"{m}_ws10"].max()
    agg["mean3_run"] = D.apply(lambda g: longest_run(g.sort_index().models_mean_ws10 >= thr))
    agg["p_run"] = D.apply(lambda g: longest_run(g.sort_index()[p_col] >= 0.5))
    agg["obs_run"] = D.apply(lambda g: longest_run(g.sort_index().obs_kn >= thr))
    agg["y"] = (agg.obs_run >= MIN_RUN).astype(int)
    return agg[agg.n >= 8].reset_index()


NEW_DAILY = ["bodden_dT", "bodden_sea", "thermal_max"]
DAILY_FEATS = ["icon_max", "icon_mean", "mean3_max", "mean3_mean", "max3_max", "spread_mean", "gust_max",
               "u_mean", "v_mean", "t2m_sst", "land_sea", "swrad", "cloud", "dp", "bodden_dT", "bodden_sea", "thermal_max", "doy_sin", "doy_cos",
               "p_hours", "p_mean", "p_run", "icon_run", "lead_day"]
# Barth: Tagesmodell ohne die neuen Merkmale (2 Jahre reichen dort nicht, verschlechterte die Kreuzvalidierung)
DAILY_BY_SITE = {"darss_ort": DAILY_FEATS, "barth": [f for f in DAILY_FEATS if f not in NEW_DAILY]}


def main():
    sst = {p: read_sst(p) for p in ("darss_ort", "barth", "darss_schwelle")}
    bodden = read_bodden_proxy()
    for p in SITES:
        BLH[p] = read_blh(p)
    raw = {p: {m: read_prev(p, m) for m in MOD} for p in ("darss_ort", "barth", "darss_schwelle")}
    hourly_rows, daily_rows, imp_rows = [], [], []
    for site in SITES:
        obs = load_obs(DWD, site)
        H = pd.concat([features(site, d, raw[site], raw["barth"], raw["darss_schwelle"], sst[site], sst["darss_schwelle"], bodden)
                       for d in LEADS])
        H["obs_kn"] = obs.reindex(H.index).values
        H = H.dropna(subset=["obs_kn", "models_mean_ws10"])
        H = H[H.year.isin(YEARS)]
        feats = [c for c in H.columns if c not in ("obs_kn", "local_date", "year", "daylight", "dwd_ws10")]
        DF = DAILY_BY_SITE[site]
        for thr in THRS:
            # --- stündlich, Kreuzvalidierung über Jahre ---
            H[f"p{thr}"] = np.nan
            for test_year in YEARS:
                tr, te = H[H.year != test_year], H[H.year == test_year]
                m = xgb.XGBClassifier(**HOURLY_PARAMS).fit(tr[feats], (tr.obs_kn >= thr).astype(int))
                H.loc[H.year == test_year, f"p{thr}"] = m.predict_proba(te[feats])[:, 1]
            for lead in LEADS:
                s = H[(H.lead_day == lead) & H.daylight]
                y = (s.obs_kn >= thr).astype(int)
                hourly_rows.append(dict(site=site, thr=thr, lead_day=lead, n=len(s),
                                        auc_icon=roc_auc_score(y, s.dwd_ws10),
                                        auc_mean3=roc_auc_score(y, s.models_mean_ws10),
                                        auc_local=roc_auc_score(y, s[f"p{thr}"])))
            # --- täglich ---
            Dd = daily_table(H, thr, f"p{thr}")
            Dd["p_day"] = np.nan
            for test_year in YEARS:
                tr, te = Dd[Dd.year != test_year], Dd[Dd.year == test_year]
                m = xgb.XGBClassifier(**DAILY_PARAMS).fit(tr[DF], tr.y)
                Dd.loc[te.index, "p_day"] = m.predict_proba(te[DF])[:, 1]
            Dd.assign(site=site, thr=thr).to_csv(f"{RES}/7day_daily_oof_{site}_{thr}.csv", index=False)
            for lead in LEADS:
                s = Dd[Dd.lead_day == lead]
                pred_local = s.p_day >= 0.5
                pred_icon = s.icon_run >= MIN_RUN
                def scores(pred):
                    tp = (pred & (s.y == 1)).sum(); fp = (pred & (s.y == 0)).sum(); fn = (~pred & (s.y == 1)).sum()
                    return tp / max(tp + fn, 1), fp / max(tp + fp, 1), (pred == s.y.astype(bool)).mean()
                hi, fa, acc = scores(pred_local); hi0, fa0, acc0 = scores(pred_icon)
                daily_rows.append(dict(site=site, thr=thr, lead_day=lead, days=len(s), rate=s.y.mean(),
                                       auc_icon=roc_auc_score(s.y, s.icon_run + s.icon_max / 100),
                                       auc_local=roc_auc_score(s.y, s.p_day),
                                       brier_local=brier_score_loss(s.y, s.p_day),
                                       hit_icon=hi0, false_alarm_icon=fa0, acc_icon=acc0,
                                       hit_local=hi, false_alarm_local=fa, acc_local=acc,
                                       mae_run_icon=(s.icon_run - s.obs_run).abs().mean(),
                                       mae_run_local=(s.p_run - s.obs_run).abs().mean()))
            # --- Endmodelle auf allen Daten ---
            mh = xgb.XGBClassifier(**HOURLY_PARAMS).fit(H[feats], (H.obs_kn >= thr).astype(int))
            mh.save_model(f"{MODELS}/hourly7_{site}_{thr}kn.json")
            md = xgb.XGBClassifier(**DAILY_PARAMS).fit(Dd[DF], Dd.y)
            md.save_model(f"{MODELS}/daily7_{site}_{thr}kn.json")
            for fname, v in zip(feats, mh.feature_importances_):
                imp_rows.append(dict(site=site, thr=thr, level="hourly", feature=fname, gain=float(v)))
            for fname, v in zip(DF, md.feature_importances_):
                imp_rows.append(dict(site=site, thr=thr, level="daily", feature=fname, gain=float(v)))
            print("fertig", site, thr, flush=True)
        json.dump(feats, open(f"{MODELS}/hourly7_features.json", "w"))
    json.dump(DAILY_BY_SITE, open(f"{MODELS}/daily7_features.json", "w"))
    HR, DR, IR = pd.DataFrame(hourly_rows), pd.DataFrame(daily_rows), pd.DataFrame(imp_rows)
    HR.to_csv(f"{RES}/7day_hourly_auc.csv", index=False)
    DR.to_csv(f"{RES}/7day_daily_skill.csv", index=False)
    IR.to_csv(f"{RES}/7day_importance.csv", index=False)
    pd.set_option("display.width", 220)
    print(HR.round(3).to_string(index=False))
    print(DR.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
