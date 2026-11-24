"""Plausibilitätsprüfung: eigener Satellitenwind (300 m) gegen das Copernicus-Produkt (1 km, results/sat_samples.csv)
zur selben Überflugzeit, an denselben Punkten/Regionen. Ergebnis: results/sar300_validation.csv/.png"""
import sys, glob, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
SAR = sys.argv[1]
BBOX = (12.30, 54.28, 12.90, 54.52)
w = np.load(f"{SAR}/water_mask.npy"); H, W = w.shape
ys, xs = np.where(w)
lat = BBOX[3] - (ys + 0.5) / H * (BBOX[3] - BBOX[1]); lon = BBOX[0] + (xs + 0.5) / W * (BBOX[2] - BBOX[0])
s = pd.read_csv("../results/sat_samples.csv"); s["time"] = pd.to_datetime(s.time).dt.round("h")
# Punkte des 1-km-Produkts (gleiche Koordinaten wie in satellite.py) und Regionen
PTS = {"darss_ort_see": (54.485, 12.50), "prerow_see": (54.465, 12.575), "zingst_see": (54.457, 12.69), "pramort_see": (54.448, 12.85)}   # wie satellite.py
REG = {"saaler_bodstedter": (lon < 12.58) & (lat < 54.41), "barther_bodden": (lon >= 12.64) & (lon < 12.78) & (lat < 54.42),
       "grabow": (lon >= 12.78) & (lat < 54.42)}
def near(la, lo, n=9):
    d = np.hypot((lat - la) * 111, (lon - lo) * 111 * np.cos(np.radians(la))); return np.argsort(d)[:n]
rows = []
for f in sorted(glob.glob(f"{SAR}/wind/*.npz")):
    z = np.load(f); v = z["v"] * 1.944; t = pd.Timestamp(str(z["time"]))
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    rec = {"time": t}
    for k, (la, lo) in PTS.items():
        vals = v[ys[near(la, lo)], xs[near(la, lo)]]; vals = vals[np.isfinite(vals)]
        rec[k] = np.median(vals) if vals.size >= 5 else np.nan
    for k, m in REG.items():
        vals = v[ys[m], xs[m]]; vals = vals[np.isfinite(vals)]
        rec[k] = np.median(vals) if vals.size > 20 else np.nan
    rows.append(rec)
own = pd.DataFrame(rows)
out, pairs = [], {}
for k in list(PTS) + list(REG):
    l3 = s[s.name == k][["time", "kn"]].rename(columns={"kn": "l3"})
    m = own[["time", k]].merge(l3, on="time").dropna()
    if len(m) < 5: out.append(dict(ort=k, n=len(m))); continue
    pairs[k] = m
    hi = m[m.l3 >= 8]
    out.append(dict(ort=k, n=len(m), r=round(m[[k, "l3"]].corr().iloc[0, 1], 2),
                    abw_kn=round((m[k] - m.l3).mean(), 1), mae_kn=round((m[k] - m.l3).abs().mean(), 1),
                    verh_unter8=round(np.median((m[k] / m.l3)[m.l3 < 8]), 2), verh_ab8=round(np.median(hi[k] / hi.l3), 2) if len(hi) else None,
                    verh_ab12=round(np.median((m[k] / m.l3)[m.l3 >= 12]), 2) if (m.l3 >= 12).sum() else None))
res = pd.DataFrame(out); res.to_csv("../results/sar300_validation.csv", index=False); print(res.to_string())
fig, ax = plt.subplots(2, 4, figsize=(14, 7)); ax = ax.ravel()
for i, (k, m) in enumerate(pairs.items()):
    a = ax[i]; a.scatter(m.l3, m[k], s=8, alpha=.6); a.plot([0, 35], [0, 35], "k--", lw=1)
    a.set_title(f"{k} (n={len(m)})", fontsize=10); a.set_xlim(0, 35); a.set_ylim(0, 35)
    a.set_xlabel("Copernicus 1 km (kn)"); a.set_ylabel("eigen 300 m (kn)")
for j in range(i + 1, len(ax)): ax[j].axis("off")
plt.tight_layout(); plt.savefig("../results/sar300_validation.png", dpi=120)
