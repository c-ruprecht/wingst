"""Welche Faktoren erklären den Fehler von ICON an Darßer Ort und Barth – getrennt nach Jahreszeit?
Ziel: Residuum obs - ICON (kn), nur Stunden mit ICON >= 6 kn und Tageslicht.
"""
import sys, numpy as np, pandas as pd, xgboost as xgb, shap
from features import build, read_om
OM, DWD, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
df = build(OM, DWD)
# zusätzlich: Grenzschichthöhe (ICON) und Scherung 850 hPa / 10 m
for site in ("darss_ort", "barth"):
    blh = read_om(OM, site, "icon_seamless")["boundary_layer_height"]
    m = df.site == site
    df.loc[m, "blh"] = blh.reindex(df.index[m]).values
df["ratio_850_10"] = df["ws850"] / df["icon_ws10"].clip(lower=1)
df["onshore"] = np.nan
df["resid"] = df["obs_kn"] - df["icon_ws10"]
loc = df.index.tz_convert("Europe/Berlin")
df["season"] = pd.Series(loc.month, index=df.index).map({12:"Winter",1:"Winter",2:"Winter",3:"Frühling",4:"Frühling",5:"Frühling",6:"Sommer",7:"Sommer",8:"Sommer",9:"Herbst",10:"Herbst",11:"Herbst"}).values
d = df[df.daylight & (df.icon_ws10 >= 6)].copy()
SEAS = ["Winter", "Frühling", "Sommer", "Herbst"]
FEATS = {"Stabilität Luft−Wasser (t2m−SST)": "t2m_minus_sst", "Wassertemperatur": "sst",
         "Land-See-Kontrast": "land_sea_dT", "Sonneneinstrahlung": "swrad", "Bewölkung": "cloud",
         "Grenzschichthöhe": "blh", "Höhenwind 850 hPa / 10 m": "ratio_850_10", "Scherung 80/10 m": "icon_shear_80_10",
         "Böenfaktor": "icon_gust_factor", "Druckgefälle Nord-Süd": "dp_north_south", "Schauer (CAPE)": "cape",
         "Luftdichte": "air_density", "Richtung sin": "dir_sin", "Richtung cos": "dir_cos",
         "Modellstreuung": "models_spread_ws10", "GFS−ICON": "gfs_minus_icon", "Tageszeit": "hour_cos", "ICON-Stärke": "icon_ws10"}
d["gfs_minus_icon"] = d["gfs_ws10"] - d["icon_ws10"]
rows, bias, stab, dirt = [], [], [], []
for site in ("darss_ort", "barth"):
    for s in SEAS:
        x = d[(d.site == site) & (d.season == s)].dropna(subset=["resid"])
        bias.append(dict(site=site, season=s, n=len(x), bias_kn=x.resid.mean(), mae_icon=x.resid.abs().mean(),
                         hit12=((x.obs_kn >= 12) & (x.icon_ws10 >= 12)).sum() / max(1, (x.obs_kn >= 12).sum())))
        for lo, hi, lab in [(-99, -3, "Luft ≥3° kälter (labil)"), (-3, -1, "1–3° kälter"), (-1, 1, "neutral"), (1, 3, "1–3° wärmer"), (3, 99, "Luft ≥3° wärmer (stabil)")]:
            y = x[(x.t2m_minus_sst >= lo) & (x.t2m_minus_sst < hi)]
            stab.append(dict(site=site, season=s, klasse=lab, n=len(y), bias_kn=y.resid.mean() if len(y) > 30 else np.nan))
        ang = (np.degrees(np.arctan2(x.dir_sin, x.dir_cos)) + 360) % 360
        sec = ((ang + 22.5) // 45 % 8).map(dict(enumerate(["N", "NO", "O", "SO", "S", "SW", "W", "NW"])))
        for k, g in x.groupby(sec):
            dirt.append(dict(site=site, season=s, sektor=k, n=len(g), bias_kn=g.resid.mean() if len(g) > 30 else np.nan))
        X = x[list(FEATS.values())]
        mdl = xgb.XGBRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, min_child_weight=20)
        mdl.fit(X, x.resid)
        sv = np.abs(shap.TreeExplainer(mdl).shap_values(X)).mean(0)
        for (lab, col), v in zip(FEATS.items(), sv):
            rows.append(dict(site=site, season=s, faktor=lab, shap_kn=v))
pd.DataFrame(bias).to_csv(f"{OUT}/season_bias.csv", index=False)
pd.DataFrame(stab).to_csv(f"{OUT}/season_stability.csv", index=False)
pd.DataFrame(dirt).to_csv(f"{OUT}/season_direction.csv", index=False)
pd.DataFrame(rows).to_csv(f"{OUT}/season_shap.csv", index=False)
print(pd.DataFrame(bias).round(2).to_string())
print(pd.DataFrame(stab).pivot_table(index=["site", "klasse"], columns="season", values="bias_kn")[SEAS].round(1).to_string())
print(pd.DataFrame(dirt).pivot_table(index=["site", "sektor"], columns="season", values="bias_kn")[SEAS].round(1).to_string())
sh = pd.DataFrame(rows).pivot_table(index=["site", "faktor"], columns="season", values="shap_kn")[SEAS]
for site in ("darss_ort", "barth"):
    t = sh.loc[site]; print(site); print(t.loc[t.mean(1).sort_values(ascending=False).index].round(2).to_string())
