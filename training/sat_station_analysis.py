"""Satellitenwind an den Spots im Verhältnis zu den Live-Stationen Darßer Ort und Barth – für die Windanalyse der App.

Grundlage: results/sar300_spot_samples.csv (sar_spot_model.py): je Sentinel-1-Überflug der Wind auf dem Wasser
(300 m, Median im Umkreis von 1,5 km) an jedem Spot, dazu die DWD-Messung an Darßer Ort und Barth zur selben
Stunde und die Modelle ICON/GFS/ECMWF am Spot.

Ergebnis: app/satellit.json
  spots[spot]: Verhältnis zu Darßer Ort und Barth gesamt, je Windrichtung (Median, Quartile, Anzahl), je Jahreszeit,
               morgens/abends, Zusammenhang r, Verhältnis zu ICON
  areas:       Mittel Ostsee (Prerow, Zingst) und Bodden (Born, Saal, Pruchten, Dabitz) je Richtung
  meta:        Zeitraum, Zahl der Bilder, Uhrzeiten

Aufruf (Repo-Ordner): python training/sat_station_analysis.py
"""
import json, os
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "results", "sar300_spot_samples.csv")
OUT = os.path.join(ROOT, "app", "satellit.json")
SEC = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]
AREAS = {"Ostsee": ["prerow", "zingst"], "Bodden": ["born", "saal", "pruchten", "dabitz"]}
SEASON = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Frühling", 4: "Frühling", 5: "Frühling",
          6: "Sommer", 7: "Sommer", 8: "Sommer", 9: "Herbst", 10: "Herbst", 11: "Herbst"}
MIN_KN, MIN_N = 6.0, 6


def stats(r):
    r = r.dropna()
    if len(r) < MIN_N:
        return {"n": int(len(r))}
    return {"n": int(len(r)), "median": round(float(r.median()), 2),
            "q25": round(float(r.quantile(.25)), 2), "q75": round(float(r.quantile(.75)), 2)}


def main():
    s = pd.read_csv(SRC, parse_dates=["time"]).drop_duplicates(["time", "spot"])
    s["sec"] = (((s["dir"] + 22.5) % 360) // 45).astype(int).map(dict(enumerate(SEC)))
    loc = s.time.dt.tz_convert("Europe/Berlin")
    s["season"] = loc.dt.month.map(SEASON)
    s["tod"] = np.where(loc.dt.hour < 12, "morgens", "abends")
    out = {"meta": {"bilder": int(s.time.nunique()), "von": f"{s.time.min():%Y-%m}", "bis": f"{s.time.max():%Y-%m}",
                    "uhrzeiten": {str(k): int(v) for k, v in loc.drop_duplicates().dt.hour.value_counts().sort_index().items()},
                    "min_kn": MIN_KN, "min_n": MIN_N},
           "spots": {}, "areas": {}}
    for spot, g in s.groupby("spot"):
        o = {}
        for st in ("darss", "barth"):
            x = g[g[st] >= MIN_KN]
            ratio = x.sar / x[st]
            o[st] = {"gesamt": stats(ratio),
                     "r": round(float(x.sar.corr(x[st])), 2),
                     "richtung": {k: stats(ratio[x.sec == k]) for k in SEC},
                     "saison": {k: stats(ratio[x.season == k]) for k in ("Winter", "Frühling", "Sommer", "Herbst")},
                     "tageszeit": {k: stats(ratio[x.tod == k]) for k in ("morgens", "abends")}}
        x = g[g.icon >= MIN_KN]
        o["icon"] = {"gesamt": stats(x.sar / x.icon), "richtung": {k: stats((x.sar / x.icon)[x.sec == k]) for k in SEC}}
        o["n_bilder"] = int(len(g))
        out["spots"][spot] = o
    for area, spots in AREAS.items():
        a = {}
        for st in ("darss", "barth"):
            x = s[s.spot.isin(spots) & (s[st] >= MIN_KN)]
            ratio = x.sar / x[st]
            a[st] = {"gesamt": stats(ratio), "richtung": {k: stats(ratio[x.sec == k]) for k in SEC}}
        out["areas"][area] = a
    # Stationen untereinander zur Einordnung: Darßer Ort / Barth zu denselben Zeitpunkten (Überflüge)
    t = s.drop_duplicates("time")
    t = t[(t.barth >= MIN_KN)]
    out["stationen"] = {"darss_zu_barth": stats(t.darss / t.barth),
                        "richtung": {k: stats((t.darss / t.barth)[t.sec == k]) for k in SEC}}
    json.dump(out, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    print(f"-> {OUT}")
    for area in AREAS:
        a = out["areas"][area]
        print(f"{area}: zu Darßer Ort {a['darss']['gesamt']}, zu Barth {a['barth']['gesamt']}")
    print("Darßer Ort / Barth:", out["stationen"]["darss_zu_barth"])


if __name__ == "__main__":
    main()
