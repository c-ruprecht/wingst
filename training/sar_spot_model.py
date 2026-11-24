"""Satellitenwind (300 m, sar_highres.py) an den Spots gegen die Modelle ICON/GFS/ECMWF (frühere Läufe, 1 Tag Vorlauf).
Ergebnis: results/sar300_spot_samples.csv, results/sar300_spot_factors.csv
  Faktor = Median(Satellitenwind auf dem Wasser / Modellwind am Spot), je Spot und Windrichtung (Modellmittel >= 6 kn).
Aufruf: python sar_spot_model.py data/sar300 <ordner openmeteo_prev>
Hinweis: Überflüge um ~05 und ~17 UTC – Mittagsthermik ist nicht erfasst. Unter 8 kn liegt der eigene Satellitenwind
~10 % über dem Copernicus-Produkt; ab 8 kn stimmen beide überein (r = 0,98 vor Darßer Ort).
"""
import sys, glob, numpy as np, pandas as pd
SAR, PREV = sys.argv[1], sys.argv[2]
BBOX = (12.30, 54.28, 12.90, 54.52)
SP = {"born": (54.381079, 12.504912), "saal": (54.312080, 12.480294), "fuhlendorf": (54.385, 12.580),
      "pruchten": (54.386068, 12.659851), "prerow": (54.455506, 12.555672), "zingst": (54.442567, 12.707110),
      "dabitz": (54.362277, 12.805830)}
MODEL_AT = {"dabitz": "barth"}   # für Dabitz gibt es keine früheren Modellläufe -> Barth (6 km entfernt)
SEC = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]
w = np.load(f"{SAR}/water_mask.npy"); H, W = w.shape
ys, xs = np.where(w)
lat_px = BBOX[3] - (ys + 0.5) / H * (BBOX[3] - BBOX[1]); lon_px = BBOX[0] + (xs + 0.5) / W * (BBOX[2] - BBOX[0])
sel = {}
for k, (la, lo) in SP.items():   # die 9 nächsten Wasserpixel (~0,8 km²) – der Ortspunkt liegt teils an Land
    d = np.hypot((lat_px - la) * 111, (lon_px - lo) * 111 * np.cos(np.radians(la)))
    sel[k] = np.argsort(d)[:9]


def model(site):
    out = {}
    for m, k in (("icon_seamless", "icon"), ("gfs_seamless", "gfs"), ("ecmwf_ifs025", "ecmwf")):
        d = pd.concat([pd.read_csv(f) for f in sorted(glob.glob(f"{PREV}/{site}_{m}_*.csv"))])
        d["time"] = pd.to_datetime(d.time, utc=True); d = d.drop_duplicates("time").set_index("time")
        out[k + "_ws"] = d["wind_speed_10m_previous_day1"]; out[k + "_wd"] = d["wind_direction_10m_previous_day1"]
    return pd.DataFrame(out)


M = {k: model(MODEL_AT.get(k, k)) for k in SP}
rows = []
for f in sorted(glob.glob(f"{SAR}/wind/*.npz")):
    z = np.load(f); v = z["v"] * 1.944; t = pd.Timestamp(str(z["time"]))
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    for k, idx in sel.items():
        vals = v[ys[idx], xs[idx]]; vals = vals[np.isfinite(vals)]
        if vals.size < 5: continue
        mm = M[k].reindex([t]).iloc[0]
        u = np.nanmean([-mm[f"{m}_ws"] * np.sin(np.radians(mm[f"{m}_wd"])) for m in ("icon", "gfs", "ecmwf")])
        vv = np.nanmean([-mm[f"{m}_ws"] * np.cos(np.radians(mm[f"{m}_wd"])) for m in ("icon", "gfs", "ecmwf")])
        rows.append(dict(time=t, spot=k, sar=np.median(vals), icon=mm.icon_ws, gfs=mm.gfs_ws, ecmwf=mm.ecmwf_ws,
                         mean3=np.nanmean([mm.icon_ws, mm.gfs_ws, mm.ecmwf_ws]), dir=(np.degrees(np.arctan2(-u, -vv)) + 360) % 360,
                         barth=float(z["barth_kn"]), darss=float(z["darss_kn"])))
d = pd.DataFrame(rows).dropna(subset=["mean3"])
d.to_csv("../results/sar300_spot_samples.csv", index=False)
d["sek"] = d.dir.map(lambda x: SEC[int(((x + 22.5) % 360) // 45)])
out = []
for k, g in d.groupby("spot"):
    g = g[g.mean3 >= 6]
    r = dict(spot=k, n=len(g), sar_vs_mean3=round(np.median(g.sar / g.mean3), 2), sar_vs_icon=round(np.median(g.sar / g.icon), 2),
             sar_vs_gfs=round(np.median(g.sar / g.gfs), 2), sar_vs_barth=round(np.median((g.sar / g.barth)[g.barth >= 6]), 2))
    for s_, h in g.groupby("sek"):
        if len(h) >= 8: r["m3_" + s_] = round(np.median(h.sar / h.mean3), 2)
    out.append(r)
pd.DataFrame(out).to_csv("../results/sar300_spot_factors.csv", index=False)
print(pd.DataFrame(out).to_string())
