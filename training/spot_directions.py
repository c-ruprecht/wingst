"""Welche Windrichtung ist an welchem Spot am saubersten? Auswertung der 300-m-Radarwinde (sar_highres.py).

Je Spot und Windrichtung (8 Sektoren, Richtung aus der Messung zur Überflugzeit):
  faktor      Wind auf dem Wasser am Spot / Messung Barth (Bodden) bzw. Darßer Ort (Ostsee), Median
  unruhe      räumliche Streuung des Windes im Umkreis von 1,5 km (Standardabw./Mittel, %) – hoch bei
              Abschattung durch Land und Wald, Windlöchern und Böen
  fetch_km    freie Wasserstrecke in Luv (aus der Wasserkarte); offene Ostsee = 60 km
  ufer        auflandig / sideshore / ablandig aus der Lage des Ufers
  welle_cm    geschätzte Wellenhöhe bei 15 kn aus Fetch und Wassertiefe (Flachwasserformel des Shore
              Protection Manual, Bodden 2 m, Ostsee 10 m) – gerechnet, nicht gemessen
Aufruf: python spot_directions.py <data/sar300>   → results/spot_directions.csv, app/spot_directions.json
"""
import sys, glob, json, numpy as np, pandas as pd
SAR = sys.argv[1]
BBOX = (12.30, 54.28, 12.90, 54.52)
SPOTS = {  # id: (lat, lon, Ostsee?)
    "prerow": (54.455506, 12.555672, True), "zingst": (54.442567, 12.707110, True),
    "born": (54.381079, 12.504912, False), "saal": (54.312080, 12.480294, False),
    "fuhlendorf": (54.385, 12.580, False), "pruchten": (54.386068, 12.659851, False),
    "dabitz": (54.362277, 12.805830, False)}
OPEN_SEA_KM = 60      # Ostsee nach Norden/Westen offen; 60 km reichen für die Wellenformel (danach kaum Zuwachs)
SEC = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]
w = np.load(f"{SAR}/water_mask.npy"); H, W = w.shape
ys, xs = np.where(w)
lat_px = BBOX[3] - (ys + 0.5) / H * (BBOX[3] - BBOX[1]); lon_px = BBOX[0] + (xs + 0.5) / W * (BBOX[2] - BBOX[0])
from scipy.ndimage import binary_erosion
INNER = binary_erosion(w, structure=np.ones((3, 3)))[ys, xs]   # Wasserpixel ohne Landnachbarn (Startpunkte)
KM_LAT, KM_LON = 111.2, 111.2 * np.cos(np.radians(54.4))
PX_LAT, PX_LON = (BBOX[3] - BBOX[1]) / H, (BBOX[2] - BBOX[0]) / W


def dist(la, lo):
    return np.hypot((lat_px - la) * KM_LAT, (lon_px - lo) * KM_LON)


def is_water(la, lo):
    r, c = int((BBOX[3] - la) / PX_LAT), int((lo - BBOX[0]) / PX_LON)
    if not (4 <= r < H and 4 <= c < W):      # oberste Zeile/linke Spalte haben keine Daten -> Rand
        return None                       # außerhalb des Bildes: als offenes Wasser/unbekannt werten
    return bool(w[r, c])


def fetch(la, lo, frm, max_km=20, step=0.1):
    """Freie Wasserstrecke in Luv: vom nächsten Wasserpixel des Spots gegen den Wind laufen, bis 200 m Land am Stück.
    Nord- und Westrand des Bildausschnitts sind offene Ostsee."""
    # Startpunkt 500 m vom Ufer ins Wasser (dort wird gekitet), sonst das nächste Wasserpixel
    sd = np.radians(shore_dir(la, lo))
    tla, tlo = la + np.cos(sd) * 0.6 / KM_LAT, lo + np.sin(sd) * 0.6 / KM_LON
    i0 = np.argmin(np.where(INNER, np.hypot((lat_px - tla) * KM_LAT, (lon_px - tlo) * KM_LON), 1e9))
    la0, lo0 = lat_px[i0], lon_px[i0]
    a = np.radians(frm)
    k, run, land = 0.0, 0.0, 0
    while k < max_km:
        k += step
        p = (la0 + np.cos(a) * k / KM_LAT, lo0 + np.sin(a) * k / KM_LON)
        wv = is_water(*p)
        if wv is None:
            open_sea = p[0] > BBOX[3] or p[1] < BBOX[0]
            return OPEN_SEA_KM if open_sea else run
        if wv:
            run += step * (land + 1); land = 0
        else:
            land += 1
            if land >= 2:
                return run
    return run


def shore_dir(la, lo):
    """Richtung vom Spot zum offenen Wasser (Mittel der Wasserpixel im Umkreis 1,5 km)."""
    d = dist(la, lo); m = d < 1.5
    v = np.c_[(lat_px[m] - la) * KM_LAT, (lon_px[m] - lo) * KM_LON].mean(0)
    return (np.degrees(np.arctan2(v[1], v[0])) + 360) % 360


def hs_spm(U, F_km, depth):
    """Signifikante Wellenhöhe (m), Shore Protection Manual 1984, flaches Wasser."""
    g = 9.81; F = F_km * 1000
    a = 0.530 * (g * depth / U ** 2) ** 0.75
    return 0.283 * np.tanh(a) * np.tanh(0.00565 * (g * F / U ** 2) ** 0.5 / np.tanh(a)) * U ** 2 / g


rows = []
files = sorted(glob.glob(f"{SAR}/wind/*.npz"))
for spot, (la, lo, sea) in SPOTS.items():
    d = dist(la, lo)
    near = np.argsort(d)[:9]                       # Wind am Spot
    ring = (d < 1.5)                               # Umgebung für die Unruhe
    rec = []
    for f in files:
        z = np.load(f); v = z["v"] * 1.944
        ref = float(z["darss_kn"] if sea else z["barth_kn"])
        a = v[ys[near], xs[near]]; a = a[np.isfinite(a)]
        b = v[ys[ring], xs[ring]]; b = b[np.isfinite(b)]
        if a.size < 5 or b.size < 10 or not np.isfinite(ref):
            continue
        rec.append(dict(dir=float(z["dir"]), spot_kn=np.median(a), ref=ref, cv=np.std(b) / max(np.mean(b), 1)))
    R = pd.DataFrame(rec)
    R = R[R.spot_kn >= 8]
    R["sek"] = R.dir.map(lambda x: SEC[int(((x + 22.5) % 360) // 45)])
    sd = shore_dir(la, lo)
    for i, s in enumerate(SEC):
        frm = i * 45
        g = R[R.sek == s]
        rel = (frm - sd + 540) % 360 - 180          # 0 = Wind kommt vom Wasser (auflandig)
        ufer = "auflandig" if abs(rel) <= 35 else "schräg auflandig" if abs(rel) <= 70 else "sideshore" if abs(rel) <= 110 else "schräg ablandig" if abs(rel) <= 145 else "ablandig"
        F = fetch(la, lo, frm)
        rows.append(dict(spot=spot, sektor=s, n=len(g),
                         faktor=round(float(np.median(g.spot_kn / g.ref)), 2) if len(g) >= 6 else None,
                         unruhe=round(float(np.median(g.cv)) * 100) if len(g) >= 6 else None,
                         fetch_km=round(F, 1), ufer=ufer,
                         welle_cm=round(float(hs_spm(15 / 1.944, max(F, 0.1), 10 if sea else 2)) * 100)))
out = pd.DataFrame(rows)
out.to_csv("../results/spot_directions.csv", index=False)
open("../app/spot_directions.json", "w").write(out.to_json(orient="records", force_ascii=False))
print(out.to_string())
