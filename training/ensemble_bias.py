"""Richtungsabhängige Abweichung (Messung − Modell, kn) je Station und Modell, für die Ensemble-Korrektur in der App.
Grundlage: Open-Meteo Historical Forecast (kurze Vorlaufzeit) 2023–2025 gegen DWD-Messungen.
Ergebnis: app/model/ens_bias.json  {site: {model: {"sector": [8 Werte N,NO,…,NW], "n": [...]}}}
"""
import sys, json, numpy as np, pandas as pd
from features import read_om, load_obs
OM, DWD, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
MOD = {"icon": "icon_seamless", "gfs": "gfs_seamless", "ecmwf": "ecmwf_ifs025"}
res = {}
for site in ("darss_ort", "barth"):
    obs = load_obs(DWD, site)
    res[site] = {}
    for k, m in MOD.items():
        d = read_om(OM, site, m)
        d = d[d.index >= "2023-01-01"]
        x = pd.DataFrame({"ws": d["wind_speed_10m"], "wd": d["wind_direction_10m"]}).join(obs, how="inner").dropna()
        x = x[x.ws >= 6]
        sec = (((x.wd + 22.5) % 360) // 45).astype(int)
        r = (x.obs_kn - x.ws).groupby(sec).agg(["mean", "size"]).reindex(range(8))
        # Sektoren mit wenig Daten zum Gesamtmittel schrumpfen
        w = r["size"].fillna(0) / (r["size"].fillna(0) + 200)
        b = w * r["mean"].fillna(0) + (1 - w) * (x.obs_kn - x.ws).mean()
        res[site][k] = {"sector": [round(v, 2) for v in b], "n": [int(v) for v in r["size"].fillna(0)]}
        print(site, k, [round(v, 1) for v in b])
json.dump(res, open(OUT, "w"), indent=1)
