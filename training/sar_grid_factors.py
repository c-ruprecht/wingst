"""Korrekturfaktoren für die Windanimation der App: Satellitenwind (300 m) / ICON je Gitterpunkt und Windrichtung.

Die Animation zeigt ICON auf einem Gitter von ~3 km (app/windanim.js). ICON rechnet die schmalen Bodden halb als Land
und bremst den Wind dort zu stark. Aus den Sentinel-1-Bildern (sar_highres.py) lässt sich je Gitterpunkt messen, wie
viel Wind auf dem Wasser wirklich ankam, verglichen mit dem, was ICON zur selben Stunde gerechnet hat.

  Faktor[Richtung][Punkt] = Median(Satellitenwind im Umkreis von 1,5 km / ICON am Punkt), nur Wasser, ICON ≥ 6 kn,
  mindestens 8 Bilder je Richtung; dazu ein Faktor über alle Richtungen als Rückfall.

Ergebnis: app/model/wind_grid.json – die App multipliziert damit das ICON-Feld über Wasser („lokal korrigiert“).
ICON-Werte: historische Vorhersagen von Open-Meteo (icon_seamless), zwischengespeichert in data/sar300/icon_grid/.

Grenzen: Bilder nur gegen 6 und 18 Uhr Ortszeit, Mittagsthermik fehlt; der Faktor gilt für den Wind über Wasser,
nicht für die Böen.

Aufruf (Repo-Ordner): python training/sar_grid_factors.py
"""
import glob, json, os, sys, time, urllib.parse, urllib.request
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAR = os.path.join(ROOT, "data", "sar300")
CACHE = os.path.join(SAR, "icon_grid")
OUT = os.path.join(ROOT, "app", "model", "wind_grid.json")
BBOX = (12.30, 54.28, 12.90, 54.52)            # wie sar_highres.py
LATS = [round(54.26 + 0.03 * i, 3) for i in range(11)]      # wie app/windanim.js
LONS = [round(12.30 + 0.05 * i, 3) for i in range(14)]
SEC = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]
RADIUS_KM, MIN_WATER_PX, MIN_N, MIN_KN = 1.5, 15, 8, 6.0


def _ctx():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


CTX = _ctx()


def get(url, tries=8):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=180, context=CTX) as r:
                return json.loads(r.read())
        except Exception as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                sys.exit("SSL-Zertifikate fehlen: im conda-Env mit python starten oder pip install certifi")
            wait = 60 * (i + 1) if "429" in str(e) else 10 * (i + 1)     # 429: Minuten-/Stundenkontingent abwarten
            print(f"    {e} – neuer Versuch in {wait} s", flush=True); time.sleep(wait)
    sys.exit("Open-Meteo-Kontingent erschöpft. Später einfach neu starten – fertige Monate sind gespeichert.")


def icon_grid(times, pts):
    """ICON (historische Vorhersage) an den Gitterpunkten mit Wasser für die Überflugstunden. Monatsweise gespeichert."""
    os.makedirs(CACHE, exist_ok=True)
    months = sorted({t.strftime("%Y-%m") for t in times})
    frames = []
    for mo in months:
        f = os.path.join(CACHE, f"{mo}.csv.gz")
        if not os.path.exists(f):
            start = pd.Timestamp(mo + "-01"); end = (start + pd.offsets.MonthEnd(0))
            parts = []
            for b in range(0, len(pts), 40):                    # 40 Punkte je Anfrage
                chunk = pts[b:b + 40]
                q = dict(latitude=",".join(str(p[0]) for p in chunk), longitude=",".join(str(p[1]) for p in chunk),
                         models="icon_seamless", hourly="wind_speed_10m,wind_direction_10m", wind_speed_unit="kn",
                         start_date=start.strftime("%Y-%m-%d"), end_date=end.strftime("%Y-%m-%d"), timezone="UTC")
                js = get("https://historical-forecast-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q))
                js = js if isinstance(js, list) else [js]
                for (la, lo), o in zip(chunk, js):
                    h = o["hourly"]
                    parts.append(pd.DataFrame({"time": h["time"], "lat": la, "lon": lo,
                                               "ws": h["wind_speed_10m"], "wd": h["wind_direction_10m"]}))
                time.sleep(1)
            d = pd.concat(parts); d["time"] = pd.to_datetime(d.time, utc=True)
            d.to_csv(f, index=False)
            print(f"  ICON {mo}: {len(d)} Werte")
        d = pd.read_csv(f, parse_dates=["time"])
        frames.append(d[d.time.isin(times)])
    return pd.concat(frames).set_index(["time", "lat", "lon"]).sort_index()


def main():
    water = np.load(os.path.join(SAR, "water_mask.npy")); H, W = water.shape
    ys, xs = np.where(water)
    lat_px = BBOX[3] - (ys + 0.5) / H * (BBOX[3] - BBOX[1]); lon_px = BBOX[0] + (xs + 0.5) / W * (BBOX[2] - BBOX[0])
    near, wfrac = {}, np.zeros((len(LATS), len(LONS)))
    for i, la in enumerate(LATS):
        for j, lo in enumerate(LONS):
            d = np.hypot((lat_px - la) * 111.2, (lon_px - lo) * 111.2 * np.cos(np.radians(la)))
            idx = np.where(d <= RADIUS_KM)[0]
            area = np.pi * RADIUS_KM ** 2 / 0.09            # Pixel à 300 m im Kreis
            inside = BBOX[1] <= la <= BBOX[3] and BBOX[0] <= lo <= BBOX[2]
            wfrac[i, j] = min(1, len(idx) / area) if inside else np.nan
            if len(idx) >= MIN_WATER_PX:
                near[(i, j)] = idx

    files = sorted(glob.glob(os.path.join(SAR, "wind", "*.npz")))
    if not files:
        sys.exit(f"Keine Satellitenbilder in {SAR}/wind – erst python training/sar_highres.py")
    imgs = []
    for f in files:
        z = np.load(f); t = pd.Timestamp(str(z["time"]))
        t = (t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")).round("h")
        imgs.append((t, z["v"] * 1.943844))
    print(f"{len(imgs)} Satellitenbilder, {len(near)} Gitterpunkte mit Wasser im Umkreis von {RADIUS_KM} km")
    icon = icon_grid(sorted({t for t, _ in imgs}), [(LATS[i], LONS[j]) for (i, j) in sorted(near)])

    rows = []
    for t, v in imgs:
        for (i, j), idx in near.items():
            vals = v[ys[idx], xs[idx]]; vals = vals[np.isfinite(vals)]
            if vals.size < MIN_WATER_PX:
                continue
            try:
                ws, wd = icon.loc[(t, LATS[i], LONS[j])][["ws", "wd"]]
            except KeyError:
                continue
            if not np.isfinite(ws) or ws < MIN_KN:
                continue
            rows.append((i, j, int(((wd + 22.5) % 360) // 45), np.median(vals) / ws))
    R = pd.DataFrame(rows, columns=["i", "j", "sec", "ratio"])
    print(f"{len(R)} Vergleiche Satellit/ICON")

    f_all = np.full((len(LATS), len(LONS)), np.nan)
    f_sec = np.full((8, len(LATS), len(LONS)), np.nan)
    n_sec = np.zeros((8, len(LATS), len(LONS)), int)
    for (i, j), g in R.groupby(["i", "j"]):
        if len(g) >= MIN_N:
            f_all[i, j] = g.ratio.median()
        for s, gs in g.groupby("sec"):
            n_sec[s, i, j] = len(gs)
            if len(gs) >= MIN_N:
                f_sec[s, i, j] = gs.ratio.median()
    f_all = np.clip(f_all, 0.6, 2.2); f_sec = np.clip(f_sec, 0.6, 2.2)
    rnd = lambda a: [[None if not np.isfinite(x) else round(float(x), 2) for x in row] for row in a]
    out = {"quelle": "Sentinel-1 (300 m, eigene Auswertung) / ICON (Open-Meteo historische Vorhersage), "
                     f"{imgs[0][0]:%Y-%m}–{imgs[-1][0]:%Y-%m}, {len(imgs)} Bilder; Überflüge ~6 und ~18 Uhr",
           "lats": LATS, "lons": LONS, "sectors": SEC, "radius_km": RADIUS_KM, "min_n": MIN_N,
           "water": rnd(wfrac), "factor_all": rnd(f_all), "factor": [rnd(f_sec[s]) for s in range(8)],
           "n": n_sec.tolist()}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, "w"), separators=(",", ":"))
    ok = np.isfinite(f_all)
    print(f"-> {OUT}: Faktor über alle Richtungen an {ok.sum()} Punkten, Median {np.nanmedian(f_all):.2f} "
          f"(Spanne {np.nanmin(f_all):.2f}–{np.nanmax(f_all):.2f})")
    for s in range(8):
        print(f"   {SEC[s]:>2}: {np.isfinite(f_sec[s]).sum():3d} Punkte, Median {np.nanmedian(f_sec[s]) if np.isfinite(f_sec[s]).any() else float('nan'):.2f}")


if __name__ == "__main__":
    main()
