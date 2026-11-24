"""Tiefenebene für die App aus dem BSH-Geländemodell (50 m): Zonen, Tiefenlinien, Fahrrinne.

Aufruf (aus dem Repo-Ordner):
    curl -L -o data/depth/Elevation-Bathymetry.zip https://gdi.bsh.de/de/data/Elevation-Bathymetry.zip
    python training/depth_layer.py                 # schreibt app/depth.geojson

Inhalt von app/depth.geojson (WGS84, Tiefe in m unter NHN ≈ Mittelwasser):
    art=zone   klasse=stehtief (< 0,8 m) | flach (0,8–1,5 m)
    art=linie  tiefe=2 | 3                     Tiefenlinien, zeigen auch Sandbänke vor Zingst/Prerow
    art=rinne                                 Fahrrinne: schmal und deutlich tiefer als die Umgebung (ausgebaggert)

Nur Daten des BSH, keine Satellitenbilder. Schwellen siehe unten.
"""
import json, os, sys, zipfile

import numpy as np
import rasterio
from rasterio import features
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds
from scipy import ndimage
import shapely
from shapely.geometry import shape, mapping
from pyproj import Transformer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = f"{ROOT}/data/depth/Elevation-Bathymetry.zip"
OUT = f"{ROOT}/app/depth.geojson"
BBOX = (12.38, 54.28, 13.00, 54.50)      # Boddenkette plus Ostsee vor Darß und Zingst
STEHTIEF, FLACH = 0.8, 1.5               # m
LINIEN = (2.0, 3.0)                      # m
RINNE_TIEFER = 0.6                       # m tiefer als der Median im Umkreis von ~550 m
RINNE_MIN = 1.8                          # m Mindesttiefe einer Rinne
RINNE_MIN_LEN = 12                       # Pixel (600 m) – kürzere Stücke sind meist Löcher
SIMPLIFY = 25                            # m


def load(path):
    if zipfile.is_zipfile(path):
        name = next(n for n in zipfile.ZipFile(path).namelist() if "baltic" in n.lower())
        path = f"/vsizip/{path}/{name}"
    src = rasterio.open(path)
    win = from_bounds(*transform_bounds("EPSG:4326", src.crs, *BBOX), src.transform)
    e = src.read(1, window=win, boundless=True, fill_value=np.nan).astype("float32")
    return e, src.window_transform(win), src.crs


def polys(mask, T, crs, props, min_area=0):
    to = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    out = []
    for g, v in features.shapes(mask.astype("uint8"), mask=mask, transform=T):
        g = shape(g)
        if g.area < min_area:
            continue
        g = g.simplify(SIMPLIFY)
        g = shapely.transform(g, lambda c: np.c_[to.transform(c[:, 0], c[:, 1])])
        out.append({"type": "Feature", "properties": props, "geometry": mapping(g)})
    return out


def lines(depth, T, crs, level):
    """Tiefenlinie als Rand der Fläche 'tiefer als level' (geglättet, ohne Kleinkram)."""
    deep = ndimage.binary_opening(np.nan_to_num(depth) >= level, iterations=1)
    to = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    h, w = depth.shape
    x0, y0 = T * (0, 0); x1, y1 = T * (w, h)
    inner = shapely.box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)).buffer(-100)   # Ausschnittsrand weglassen
    out = []
    for g, v in features.shapes(deep.astype("uint8"), mask=deep, transform=T):
        g = shape(g)
        if g.area < 4 * 2500:
            continue
        for ring in [g.exterior, *g.interiors]:
            if ring.length < 400:
                continue
            ln = shapely.LineString(ring.coords).intersection(inner).simplify(SIMPLIFY)
            for part in getattr(ln, "geoms", [ln]):
                if part.is_empty or part.geom_type != "LineString" or part.length < 400:
                    continue
                part = shapely.transform(part, lambda c: np.c_[to.transform(c[:, 0], c[:, 1])])
                out.append({"type": "Feature", "properties": {"art": "linie", "tiefe": level}, "geometry": mapping(part)})
    return out


def ostsee(water, depth):
    """Ostsee = Wasser über 1 m Tiefe, das mit der Nordwestecke verbunden ist. Bodden und Ostsee hängen
    im Ausschnitt nur über flaches Windwatt (< 1 m) oder schmale Rinnen zusammen; 300 m Abtragen trennt Letztere."""
    core = ndimage.binary_erosion(water & (np.nan_to_num(depth) > 1.0), iterations=6, border_value=1)
    lab, _ = ndimage.label(core)
    sea = lab == lab[0, 0]          # Nordwestecke liegt in der Ostsee
    return ndimage.binary_dilation(sea, iterations=10) & water


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else ZIP
    if not os.path.exists(path):
        sys.exit(f"BSH-Modell fehlt: curl -L -o {ZIP} https://gdi.bsh.de/de/data/Elevation-Bathymetry.zip")
    e, T, crs = load(path)
    depth = np.where(e < 0, -e, np.nan)            # BSH: Höhe über NHN, Wasser negativ
    water = np.isfinite(depth)
    feats = []
    feats += polys(water & (depth < STEHTIEF), T, crs, {"art": "zone", "klasse": "stehtief"}, min_area=3 * 2500)
    feats += polys(water & (depth >= STEHTIEF) & (depth < FLACH), T, crs, {"art": "zone", "klasse": "flach"}, min_area=3 * 2500)
    for lv in LINIEN:
        feats += lines(depth, T, crs, lv)
    # Fahrrinne: deutlich tiefer als die Umgebung und selbst tief genug
    med = ndimage.median_filter(np.nan_to_num(depth), size=11)
    rinne = water & (depth - med > RINNE_TIEFER) & (depth > RINNE_MIN)
    rinne = ndimage.binary_closing(rinne, iterations=2) & water
    lab, n = ndimage.label(rinne, structure=np.ones((3, 3)))
    size = ndimage.sum(rinne, lab, range(1, n + 1))
    rinne = np.isin(lab, np.where(size >= RINNE_MIN_LEN)[0] + 1)
    # nur im Bodden: an der Außenküste sind es natürliche Senken vor dem Strand, keine Fahrrinnen
    rinne &= ~ostsee(water, depth)
    feats += polys(rinne, T, crs, {"art": "rinne"})
    fc = {"type": "FeatureCollection",
          "properties": {"quelle": "BSH, Digitales Geländemodell Ostsee 50 m (gdi.bsh.de, Elevation-Bathymetry)",
                         "stehtief_m": STEHTIEF, "flach_m": FLACH},
          "features": feats}
    s = json.dumps(fc, ensure_ascii=False, separators=(",", ":"))
    import re
    s = re.sub(r"(\d+\.\d{5})\d+", r"\1", s)
    open(OUT, "w").write(s)
    kinds = {}
    for f in feats:
        k = f["properties"]["art"] + "/" + str(f["properties"].get("klasse", f["properties"].get("tiefe", "")))
        kinds[k] = kinds.get(k, 0) + 1
    print(f"{OUT}: {len(s) / 1e3:.0f} kB", kinds)


if __name__ == "__main__":
    main()
