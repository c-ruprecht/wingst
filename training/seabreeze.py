"""Seewind/Thermik: Gibt es an Sonnentagen nachmittags mehr Wind und Drehung auf auflandig, als ICON zeigt?"""
import sys, numpy as np, pandas as pd
from features import build
df = build(sys.argv[1], sys.argv[2])
dwd = sys.argv[2]
def obsdir(site):
    d = pd.read_csv(f"{dwd}/{site}_wind.csv", dtype={"MESS_DATUM": str})
    d["time"] = pd.to_datetime(d["MESS_DATUM"], format="%Y%m%d%H", utc=True)
    return pd.to_numeric(d.set_index("time")["D"], errors="coerce").where(lambda x: x >= 0)
loc = df.index.tz_convert("Europe/Berlin")
df["h"] = loc.hour; df["m"] = loc.month
df["resid"] = df.obs_kn - df.icon_ws10
for site in ("darss_ort", "barth"):
    m = df.site == site
    df.loc[m, "obs_dir"] = obsdir(site).reindex(df.index[m]).values
df["icon_dir"] = (np.degrees(np.arctan2(df.dir_sin, df.dir_cos)) + 360) % 360
# Sonnentag mit schwachem Grundwind im Sommerhalbjahr (Apr–Aug)
day = df.groupby([df.site, loc.date]).agg(sw=("swrad", "max"), cl=("cloud", "mean"), w=("icon_ws10", "mean"), lsd=("land_sea_dT", "max"))
df = df.join(day, on=["site", pd.Index(loc.date)]) if False else df
key = pd.MultiIndex.from_arrays([df.site, loc.date])
for c in day.columns: df["d_" + c] = day[c].reindex(key).values
warm = df.m.between(4, 8)
sunny = (df.d_cl < 30) & (df.d_lsd > 5)
weak = df.d_w < 10
out = []
for site in ("darss_ort", "barth"):
    for lab, sel in [("sonnig+schwach", warm & sunny & weak), ("bewölkt+schwach", warm & (df.d_cl > 70) & weak)]:
        x = df[(df.site == site) & sel]
        g = x.groupby("h").agg(n=("resid", "size"), obs=("obs_kn", "mean"), icon=("icon_ws10", "mean"), resid=("resid", "mean"))
        # mittlere gemessene Richtung (Vektor)
        r = np.deg2rad(x.obs_dir); g["obs_dir"] = (np.degrees(np.arctan2(np.sin(r).groupby(x.h).mean(), np.cos(r).groupby(x.h).mean())) + 360) % 360
        r = np.deg2rad(x.icon_dir); g["icon_dir"] = (np.degrees(np.arctan2(np.sin(r).groupby(x.h).mean(), np.cos(r).groupby(x.h).mean())) + 360) % 360
        g = g.loc[[6, 9, 11, 13, 15, 17, 19]]
        print(f"\n{site} – {lab} (Apr–Aug, Tage: {x.index.normalize().nunique()})"); print(g.round(1).to_string())
