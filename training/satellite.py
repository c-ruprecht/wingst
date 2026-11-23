"""Sentinel-1-Radarwind (Copernicus Marine, ~1 km) an Spots und über der Boddenkette.

Aufruf: python satellite.py data/sentinel1 data/dwd results/
Ergebnis: Verhältnis Satellitenwind zu Stationsmessung, je Spot und Windsektor bzw. je Boddenabschnitt.
"""
import glob, sys
import numpy as np
import pandas as pd
import xarray as xr
from features import load_obs

SAT, DWD, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
MS2KN = 1 / 0.514444

# Punkte knapp vor dem Strand (Wasserpixel)
POINTS = {"darss_ort_see": (54.485, 12.50), "prerow_see": (54.465, 12.575),
          "zingst_see": (54.457, 12.69), "pramort_see": (54.448, 12.85)}
# Boddenabschnitte (lon0, lon1, lat0, lat1)
REGIONS = {"saaler_bodstedter": (12.45, 12.62, 54.33, 54.40), "barther_bodden": (12.62, 12.80, 54.36, 54.42),
           "grabow": (12.80, 12.98, 54.38, 54.42), "boddenkette": (12.45, 12.98, 54.33, 54.42)}
SECTORS = [0, 45, 90, 135, 180, 225, 270, 315, 360]
LABELS = ["N-NO", "NO-O", "O-SO", "SO-S", "S-SW", "SW-W", "W-NW", "NW-N"]


def overpass_times(ds):
    mt = ds.measurement_time.sel(latitude=54.485, longitude=12.50, method="nearest").values
    mt = mt[~np.isnat(mt)]
    tod = pd.Series(mt - mt.astype("datetime64[D]")).median()
    return (pd.to_datetime(ds.time.values) + tod).tz_localize("UTC").round("h")


rows = []
for f in sorted(glob.glob(f"{SAT}/*.nc")):
    ds = xr.open_dataset(f)
    t = overpass_times(ds)
    for name, (la, lo) in POINTS.items():
        sub = ds.sel(latitude=slice(la - 0.012, la + 0.012), longitude=slice(lo - 0.02, lo + 0.02))
        ok = sub.quality_level >= 3
        rows.append(pd.DataFrame({"time": t, "kind": "point", "name": name,
                                  "kn": sub.wind_speed.where(ok).mean(["latitude", "longitude"]).values * MS2KN,
                                  "dir_to": sub.wind_to_dir.where(ok).mean(["latitude", "longitude"]).values,
                                  "npix": ok.sum(["latitude", "longitude"]).values}))
    for name, (lo0, lo1, la0, la1) in REGIONS.items():
        sub = ds.sel(latitude=slice(la0, la1), longitude=slice(lo0, lo1))
        ws = sub.wind_speed.where(sub.quality_level >= 2)
        rows.append(pd.DataFrame({"time": t, "kind": "region", "name": name,
                                  "kn": ws.mean(["latitude", "longitude"]).values * MS2KN, "dir_to": np.nan,
                                  "npix": ws.count(["latitude", "longitude"]).values}))
S = pd.concat(rows).dropna(subset=["kn"])
S = S[S.npix >= 2].reset_index(drop=True)
obs = {"darss_ort": load_obs(DWD, "darss_ort"), "barth": load_obs(DWD, "barth")}
S["obs_darss_ort"] = obs["darss_ort"].reindex(S.time).values
S["obs_barth"] = obs["barth"].reindex(S.time).values
S.to_csv(f"{OUT}/sat_samples.csv", index=False)

# Strände relativ zum Wasser vor Darßer Ort, nach Windsektor
W = S[S.kind == "point"].pivot_table(index="time", columns="name", values="kn")
ref = S[(S.kind == "point") & (S.name == "darss_ort_see")].set_index("time").dir_to
W["sector"] = pd.cut((ref.reindex(W.index) + 180) % 360, SECTORS, labels=LABELS)
out = []
for p in ("prerow_see", "zingst_see", "pramort_see"):
    r = (W[p] / W["darss_ort_see"]).where(W["darss_ort_see"] > 6)
    g = W.assign(r=r).groupby("sector", observed=True).r.agg(["median", "count"])
    g["spot"] = p
    out.append(g.reset_index())
pd.concat(out).to_csv(f"{OUT}/sat_beach_factors.csv", index=False)

# Bodden relativ zur Station Barth
R = S[(S.kind == "region") & (S.obs_barth > 6)].reset_index(drop=True)
R.assign(ratio=R.kn / R.obs_barth).groupby("name").apply(lambda g: pd.Series({
    "n": len(g), "ratio_median": g.ratio.median(), "r": np.corrcoef(g.kn, g.obs_barth)[0, 1]})
).to_csv(f"{OUT}/sat_bodden_factors.csv")
print(open(f"{OUT}/sat_bodden_factors.csv").read())
print(pd.read_csv(f"{OUT}/sat_beach_factors.csv").round(2).to_string(index=False))
