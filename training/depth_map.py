"""Flachwasserkarte der Boddenkette aus Sentinel-2 (Satellite-Derived Bathymetry, mehrere Varianten).

Lädt Sentinel-2 L2A (AWS Earth Search, ohne Konto), nimmt nur windstille Wintertage (Wind aus dem
Open-Meteo-Archiv, ERA5 – bei Wind wird im Bodden Sediment aufgewirbelt, im Sommer trüben Algen)
und davon die Szenen mit dem klarsten Wasser,
rechnet vier Varianten einer "Flachheit" und baut eine Vergleichsseite je Spot.

Aufruf (aus dem Repo-Ordner):
    python training/depth_map.py                       # Standard: Winter (Nov–März), vorher 36 h ≤ 10 kn, 15 klarste Tage
    python training/depth_map.py --max-wind 8 --calm-hours 48 --scenes 6
    python training/depth_map.py --months alle --max-wind 0    # ohne Saison- und Windfilter
    python training/depth_map.py --skip-download       # nur neu rechnen/zeichnen aus data/depth/szenen/
    python training/depth_map.py --res 20 --threads 1  # wenig Speicher / langsames Netz

Speicher: jeder Tag wird einzeln geladen, ausgewertet und nach data/depth/szenen/*.npz geschrieben (Neustart
setzt dort fort). Spitze bei 10 m etwa 2–3 GB, bei --res 20 unter 1 GB.

Ergebnis in data/depth/ (nicht im Repo):
    vergleich.html          Spots x Varianten nebeneinander, im Browser öffnen
    flach_<variante>.tif    GeoTIFF je Variante (0 = tief ... 1 = sehr flach), für die App später
    tiefe_<variante>.tif    nur mit Tiefenpunkten: geschätzte Tiefe in m
    tiefenpunkte_vorlage.csv  Vorlage für eigene / SonarChart-Tiefen

Tiefenpunkte (optional, macht aus der relativen Karte Meter):
    data/depth/tiefenpunkte.csv mit Spalten  lat,lon,tiefe_m,quelle
    z. B. Werte, die man in der Navionics-App (SonarChart) abliest, oder eigene Messungen (Stab, Echolot).
    Tiefe bezogen auf Mittelwasser. Ab ca. 10 Punkten pro Bodden wird die Anpassung brauchbar;
    die Seite zeigt dann je Variante, wie gut sie die Punkte trifft (Fehler mit Punkt-Auslassung).

Varianten (alle als Median über die klarsten Szenen):
    A  blau/grün   Stumpf-Verhältnis ln(B2)/ln(B3)   klassisch, für klares Wasser
    B  grün/rot    Stumpf-Verhältnis ln(B3)/ln(B4)   für trübes Wasser (Caballero & Stumpf)
    C  Helligkeit  Lyzenga ln(B3 - tiefes Wasser)    heller Grund = flach, einfach
    D  Häufigkeit  Anteil der Szenen, in denen der Grund deutlich durchscheint (robust, erkennt Rinnen)
    E  D + A       D entscheidet flach/tief, A liefert im Flachen die Feinheiten (Pruchten: A und D passen)

Braucht zusätzlich zur kitewind-Umgebung:  odc-stac pystac-client rasterio matplotlib
    mamba install -c conda-forge odc-stac pystac-client rasterio matplotlib
"""
import argparse, base64, datetime as dt, io, json, os, sys

import numpy as np
import pandas as pd
import xarray as xr
import odc.geo.xr  # noqa: F401  (.odc-Zugriff: CRS, GeoTIFF schreiben)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
CRS = "EPSG:32633"
BBOX = (12.40, 54.29, 12.95, 54.46)        # lon0, lat0, lon1, lat1 – ganze Darß-Zingster Boddenkette
BANDS = ["blue", "green", "red", "nir", "scl"]
HALF_KM = 2.0                              # Ausschnitt je Spot: ±2 km

# Spots aus app/spots.js (nur Bodden – an der Ostsee ist das Wasser zu tief/brandig für diese Methode)
SPOTS = {"pruchten": ("Pruchten", 54.386068, 12.659851), "born": ("Born", 54.381079, 12.504912),
         "saal": ("Saal", 54.312080, 12.480294), "dabitz": ("Dabitz", 54.362277, 12.805830)}

# SCL-Klassen der L2A-Szenenklassifikation
SCL_WATER, SCL_CLOUD, SCL_ICE = 6, (3, 8, 9, 10), 11

VARIANTS = {
    "A": "A · blau/grün (Stumpf)",
    "B": "B · grün/rot (Stumpf, trüb)",
    "C": "C · Helligkeit (Lyzenga)",
    "D": "D · Häufigkeit Grund sichtbar",
    "E": "E · D + A im Flachen",
}


# ---------------------------------------------------------------- Laden

def search(years, max_cloud):
    from pystac_client import Client
    cat = Client.open(STAC)
    items = cat.search(collections=[COLLECTION], bbox=BBOX, datetime=f"{years[0]}-01-01/{years[1]}-12-31",
                       query={"eo:cloud_cover": {"lt": max_cloud}}).item_collection()
    print(f"{len(items)} Sentinel-2-Kacheln mit < {max_cloud} % Wolken gefunden")
    return items


def boa_offset(item):
    """Ab Prozessierung 04.00 (2022) haben L2A-Werte einen Versatz von -1000, sofern nicht schon abgezogen."""
    p = item.properties
    if p.get("earthsearch:boa_offset_applied"):
        return 0.0
    return -1000.0 if str(p.get("s2:processing_baseline", "00.00")) >= "04.00" else 0.0


def load(items, bands, res):
    import odc.stac
    from odc.stac import configure_rio
    configure_rio(cloud_defaults=True, aws={"aws_unsigned": True})
    ds = odc.stac.load(items, bands=bands, bbox=BBOX, crs=CRS, resolution=res, groupby="solar_day",
                       chunks={"x": 2048, "y": 2048}, resampling={"scl": "nearest", "*": "bilinear"},
                       fail_on_error=False)
    off = {}
    for it in items:
        off[pd.Timestamp(it.datetime).tz_localize(None).normalize()] = boa_offset(it)
    o = xr.DataArray([off.get(pd.Timestamp(t).normalize(), 0.0) for t in ds.time.values], dims="time",
                     coords={"time": ds.time})
    for b in bands:
        if b != "scl":
            v = ds[b].where(ds[b] > 0)
            ds[b] = ((v + o) / 10000.0).astype("float32")
    return ds


WIND_POINT = (54.37, 12.66)   # Mitte Barther Bodden / Pruchten


def wind_history(t0, t1):
    """Stündlicher Wind (ERA5 über Open-Meteo-Archiv, kostenlos) über dem Bodden in Knoten."""
    import urllib.parse
    from download_training_data import get
    q = dict(latitude=WIND_POINT[0], longitude=WIND_POINT[1], start_date=f"{t0:%Y-%m-%d}", end_date=f"{t1:%Y-%m-%d}",
             hourly="wind_speed_10m", wind_speed_unit="kn", timezone="UTC")
    h = get("https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(q))["hourly"]
    return pd.Series(h["wind_speed_10m"], index=pd.to_datetime(h["time"]), dtype=float)


def add_wind(rows, calm_hours):
    """Mittel- und Höchstwind der letzten calm_hours Stunden vor dem Überflug (aufgewirbelter Sand braucht Zeit)."""
    t = pd.to_datetime(rows.time).dt.tz_localize(None).dt.round("h")
    w = wind_history(t.min() - pd.Timedelta(days=3), min(t.max(), pd.Timestamp.today() - pd.Timedelta(days=6)))
    mean, mx = [], []
    for ti in t:
        win = w[ti - pd.Timedelta(hours=calm_hours): ti]
        ok = win.notna().sum() >= calm_hours * 0.8
        mean.append(win.mean() if ok else np.nan); mx.append(win.max() if ok else np.nan)
    return rows.assign(wind_mittel_kn=np.round(mean, 1), wind_max_kn=np.round(mx, 1))


def screen(items, n_scenes, months, calm_hours, max_wind):
    """Grob (60 m) alle Tage prüfen: Wintermonat, vorher windstill, Bodden wolkenfrei, kein Eis,
    Wasser möglichst klar (wenig Rot)."""
    if months:
        items = [it for it in items if it.datetime.month in months]
        print(f"{len(items)} Kacheln in den Monaten {sorted(months)}")
    days = sorted({pd.Timestamp(it.datetime).tz_localize(None).normalize() for it in items})
    print(f"Vorauswahl bei 60 m: {len(days)} Tage in Paketen zu 25 …")
    parts = []
    for i in range(0, len(days), 25):
        batch = set(days[i:i + 25])
        sub = [it for it in items if pd.Timestamp(it.datetime).tz_localize(None).normalize() in batch]
        ds = load(sub, ["red", "scl"], 60).compute()
        scl = ds.scl
        water = scl == SCL_WATER
        parts.append(pd.DataFrame({
            "time": pd.to_datetime(ds.time.values),
            "nwasser": water.sum(["x", "y"]).values,
            "wolke": scl.isin(SCL_CLOUD).mean(["x", "y"]).values,
            "eis": (scl == SCL_ICE).mean(["x", "y"]).values,
            "rot_wasser": ds.red.where(water).median(["x", "y"]).values,
        }))
        print(f"  {min(i + 25, len(days))}/{len(days)}", flush=True)
        del ds, scl, water
    rows = pd.concat(parts, ignore_index=True)
    nw = rows.pop("nwasser")
    rows.insert(1, "wasser_anteil", nw / nw.quantile(0.9))
    if max_wind is not None:
        rows = add_wind(rows, calm_hours)
    ok = rows[(rows.wasser_anteil > 0.85) & (rows.wolke < 0.02) & (rows.eis < 0.01)
              & (rows.rot_wasser > 0.002)].dropna(subset=["rot_wasser"])   # <= 0: Kalibrier-/Versatzfehler der Szene
    print(f"{len(ok)} wolken- und eisfreie Tage")
    if max_wind is not None:
        calm = ok[ok.wind_max_kn <= max_wind]
        print(f"{len(calm)} davon mit höchstens {max_wind:g} kn in den {calm_hours} h davor")
        if len(calm) < 3:
            print("Zu wenige windstille Tage – nehme die ruhigsten (nach Höchstwind).")
            calm = ok.sort_values("wind_max_kn").head(max(3, n_scenes // 2))
        ok = calm
    ok = ok.sort_values("rot_wasser")
    print(f"Verwendet: die {min(n_scenes, len(ok))} Tage mit dem klarsten Wasser:")
    pick = ok.head(n_scenes)
    print(pick.round(4).to_string(index=False))
    return rows, pick


def scene_layers(ds1):
    """Eine Szene -> Zwischenebenen (alles, was danach nur noch einen Median über die Tage braucht)."""
    clear = ~ds1.scl.isin(SCL_CLOUD + (SCL_ICE,))
    b, g, r = (ds1[k].where(clear).clip(min=1e-4) for k in ("blue", "green", "red"))
    ndwi = (ds1.green - ds1.nir) / (ds1.green + ds1.nir)
    sclw = (ds1.scl == SCL_WATER)
    # flaches helles Wasser landet in SCL manchmal nicht als "Wasser" -> NDWI zusätzlich
    wm = ((ndwi > 0.05) | sclw) & (ds1.nir < 0.06) & clear
    n = 1000.0
    deep = float(g.where(wm).quantile(0.05))        # dunkelste 5 % des Boddens = tiefes Wasser
    med = float(g.where(wm).median())
    out = xr.Dataset({
        # A/B: Verhältnis steigt mit der Tiefe -> Vorzeichen drehen (größer = flacher)
        "A": -(np.log(n * b) / np.log(n * g)),
        "B": -(np.log(n * g) / np.log(n * r)),
        # C: Lyzenga, heller als tiefes Wasser = flacher
        "C": np.log((g - deep).clip(min=1e-4)),
        # D: Grund "scheint durch", wenn Grün deutlich über dem Boddenmedian der Szene liegt
        "D": (g > med * 1.35).where(g.notnull()),
        "ndwi": ndwi, "nir": ds1.nir, "sclw": sclw.astype("float32"),
        "red": ds1.red, "green": ds1.green, "blue": ds1.blue,
    })
    return out.astype("float32").drop_vars("quantile", errors="ignore")


LAYERS = ["A", "B", "C", "D", "ndwi", "nir", "sclw", "red", "green", "blue"]


def scene_file(out, day):
    return f"{out}/szenen/{pd.Timestamp(day):%Y%m%d}.npz"


def load_scenes(items, days, out, res):
    """Tag für Tag laden und auswerten, Ergebnis je Tag auf die Platte – hält den Speicher klein.
    Schon vorhandene Tage werden übersprungen (Abbruch und Neustart sind kein Problem)."""
    import gc
    os.makedirs(f"{out}/szenen", exist_ok=True)
    for i, day in enumerate(days, 1):
        f = scene_file(out, day)
        if os.path.exists(f):
            print(f"[{i}/{len(days)}] {day:%d.%m.%Y} schon da"); continue
        keep = [it for it in items if pd.Timestamp(it.datetime).tz_localize(None).normalize() == day]
        print(f"[{i}/{len(days)}] {day:%d.%m.%Y}: lade {len(keep)} Kachel(n) bei {res} m …", flush=True)
        ds1 = load(keep, BANDS, res).isel(time=0).compute()
        lay = scene_layers(ds1)
        with open(f + ".tmp", "wb") as fh:   # NumPy-Format: braucht keine NetCDF-Bibliothek
            np.savez_compressed(fh, x=lay.x.values, y=lay.y.values, **{v: lay[v].values for v in LAYERS})
        os.replace(f + ".tmp", f)
        del ds1, lay; gc.collect()


def median_layer(files, name):
    """Median einer Ebene über alle Tage, eine Ebene nach der anderen (≈ Tage × 30 MB im Speicher)."""
    stack = []
    for f in files:
        with np.load(f) as z:
            if not stack:
                x, y = z["x"], z["y"]
            stack.append(z[name])
    stack = np.stack(stack)
    with np.errstate(all="ignore"):
        m = np.nanmean(stack, axis=0) if name == "D" else np.nanmedian(stack, axis=0)
    del stack
    return xr.DataArray(m.astype("float32"), dims=("y", "x"), coords={"y": y, "x": x}).odc.assign_crs(CRS)


def variants(files):
    """Gibt je Variante eine 'Flachheit' zurück: größer = flacher (noch nicht normiert)."""
    ndwi, sclw, nir = (median_layer(files, k) for k in ("ndwi", "sclw", "nir"))
    wm = ((ndwi > 0.05) | (sclw > 0.5)) & (nir < 0.06)
    del ndwi, sclw, nir
    out = {}
    for k in ("A", "B", "C", "D"):   # E wird unten aus A und D gebildet
        print(f"Variante {k} …", flush=True)
        out[k] = median_layer(files, k).where(wm)
    # Im trüben Bodden ist nicht sicher, in welche Richtung die Verhältnisse A/B mit der Tiefe laufen.
    # Richtung daher an C ausrichten (heller Grund = flach ist physikalisch eindeutig).
    ref = out["C"].values.ravel()
    for k in ("A", "B", "D"):
        a = out[k].values.ravel(); ok = np.isfinite(a) & np.isfinite(ref)
        if ok.sum() > 100 and np.corrcoef(a[ok], ref[ok])[0, 1] < 0:
            out[k] = -out[k]
            print(f"Variante {k}: Richtung umgedreht (passt so zu heller = flach)")
    out["E"] = combine_da(out, wm)
    rgb = xr.Dataset({k: median_layer(files, k) for k in ("red", "green", "blue")})
    return out, wm, rgb


def combine_da(out, wm):
    """E: D entscheidet flach/tief (erkennt auch Rinnen), A liefert nur im Flachen die Abstufung.
    A allein hält tiefe Rinnen fälschlich für flach, D allein ist grob (nur Anteil von ~10 Tagen)."""
    a, d = normalize(out["A"], wm), normalize(out["D"], wm)
    ds = d.rolling(x=3, y=3, center=True, min_periods=1).mean().where(wm)   # leicht glätten
    w = ((ds - 0.15) / 0.3).clip(0, 1)                                     # 0 = tief, 1 = sicher flach
    return (0.5 * ds + 0.5 * w * a).where(wm).astype("float32")


def normalize(v, wm):
    lo, hi = np.nanpercentile(v.where(wm).values, [2, 98])
    return ((v - lo) / (hi - lo)).clip(0, 1)


# ---------------------------------------------------------------- Tiefenpunkte

def to_xy(lat, lon):
    from pyproj import Transformer
    return Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(lon, lat)


def sample(v, x, y, r=15):
    return float(v.sel(x=slice(x - r, x + r), y=slice(y + r, y - r)).mean())


def calibrate(flat, path):
    if not os.path.exists(path):
        return None
    pts = pd.read_csv(path).dropna(subset=["lat", "lon", "tiefe_m"])
    if len(pts) < 5:
        print(f"Nur {len(pts)} Tiefenpunkte – mindestens 5 nötig, Kalibrierung übersprungen.")
        return None
    xy = [to_xy(a, o) for a, o in zip(pts.lat, pts.lon)]
    res = {}
    for k, v in flat.items():
        s = np.array([sample(v, x, y) for x, y in xy])
        ok = np.isfinite(s)
        sx, d = s[ok], pts.tiefe_m.values[ok]
        if len(sx) < 5:
            continue
        a, c = np.polyfit(sx, d, 1)
        loo = []
        for i in range(len(sx)):
            m = np.arange(len(sx)) != i
            ai, ci = np.polyfit(sx[m], d[m], 1)
            loo.append(ai * sx[i] + ci - d[i])
        loo = np.array(loo)
        res[k] = {"a": a, "c": c, "n": int(ok.sum()), "rmse_loo": float(np.sqrt(np.mean(loo ** 2))),
                  "r": float(np.corrcoef(sx, d)[0, 1]), "pts": pts[ok].assign(sat=sx)}
        print(f"Variante {k}: n={ok.sum()}  r={res[k]['r']:+.2f}  Fehler (Auslassung) {res[k]['rmse_loo']:.2f} m")
    return res


# ---------------------------------------------------------------- Bekannte Flächen (Labels) und EMODnet

# Becken grob nach Länge: das Wasser ist je Becken anders gefärbt, Schwellen gelten nur innerhalb eines Beckens
BASINS = {"Saaler/Bodstedter Bodden": (12.30, 12.62), "Barther Bodden/Zingster Strom": (12.62, 12.80),
          "Grabow": (12.80, 13.00)}
LABELS = f"{ROOT}/training/depth_labels.geojson"


def rasterize_labels(v, path=LABELS):
    """GeoJSON-Flächen (Eigenschaft 'klasse': 'stehtief' oder 'tief') auf das Raster legen."""
    if not os.path.exists(path):
        return []
    import shapely
    from shapely.geometry import shape
    from pyproj import Transformer
    from rasterio import features
    to = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    gb = v.odc.geobox
    out = []
    for f in json.load(open(path))["features"]:
        g = shapely.transform(shape(f["geometry"]), lambda c: np.c_[to.transform(c[:, 0], c[:, 1])])
        m = features.rasterize([g], out_shape=v.shape, transform=gb.affine, dtype="uint8").astype(bool)
        near = features.rasterize([g.buffer(300)], out_shape=v.shape, transform=gb.affine, dtype="uint8").astype(bool)
        out.append({**f["properties"], "mask": m, "near": near, "lon": shapely.centroid(shape(f["geometry"])).x})
    return out


def auc(pos, neg):
    pos, neg = pos[np.isfinite(pos)], neg[np.isfinite(neg)]
    if len(pos) < 20 or len(neg) < 20:
        return np.nan
    rk = np.r_[pos, neg].argsort().argsort() + 1
    return float((rk[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def check_labels(layers, wm):
    """Je Fläche und Variante: Trefferquote (AUC), dass die Fläche flacher (stehtief) bzw. tiefer (tief) ist
    als das übrige Wasser im selben Becken (≥ 300 m Abstand). 0,5 = Zufall, 1 = perfekt."""
    v0 = next(iter(layers.values()))
    labs = rasterize_labels(v0)
    if not labs:
        return None
    w = wm.values
    rows = []
    for L in labs:
        basin = next(n for n, (a, b) in BASINS.items() if a <= L["lon"] < b)
        lo, hi = BASINS[basin]
        from pyproj import Transformer
        lon, _ = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(v0.x.values, np.full(v0.x.size, float(v0.y.mean())))
        inb = np.broadcast_to((lon >= lo) & (lon < hi), w.shape)
        pos = L["mask"] & w
        neg = inb & w & ~L["near"]
        for other in labs:            # andere Flächen nicht als Gegenbeispiel nehmen
            neg &= ~other["near"]
        tief = L.get("klasse") == "tief"
        r = {"Fläche": L.get("name", "?"), "Klasse": L.get("klasse", "?"), "Becken": basin}
        for k, v in layers.items():
            a = v.values
            r[k] = auc(a[neg], a[pos]) if tief else auc(a[pos], a[neg])
        rows.append(r)
    t = pd.DataFrame(rows)
    print("Abgleich mit bekannten Flächen (AUC, 0,5 = Zufall):")
    print(t.round(2).to_string(index=False))
    return t


def emodnet(like):
    """EMODnet-Tiefenmodell (~115 m, kostenlos) auf das Raster legen; Tiefe in m (positiv)."""
    import urllib.parse, rasterio
    from rasterio.io import MemoryFile
    from rasterio.warp import reproject, Resampling
    from download_training_data import get
    lon0, lat0, lon1, lat1 = BBOX
    last = None
    for cov in ("emodnet__mean", "emodnet:mean"):
        q = [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"), ("coverageId", cov),
             ("format", "image/tiff"), ("subset", f"Lat({lat0},{lat1})"), ("subset", f"Long({lon0},{lon1})")]
        try:
            raw = get("https://ows.emodnet-bathymetry.eu/wcs?" + urllib.parse.urlencode(q), binary=True, tries=2)
            with MemoryFile(raw) as mf, mf.open() as src:
                dst = np.full(like.shape, np.nan, dtype="float32")
                reproject(src.read(1).astype("float32"), dst, src_transform=src.transform, src_crs=src.crs,
                          src_nodata=src.nodata, dst_transform=like.odc.geobox.affine, dst_crs=CRS,
                          dst_nodata=np.nan, resampling=Resampling.bilinear)
            depth = xr.DataArray(-dst, dims=("y", "x"), coords={"y": like.y, "x": like.x}).odc.assign_crs(CRS)
            print(f"EMODnet geladen ({cov}): Tiefe im Bodden Median {float(depth.where(depth > 0).median()):.1f} m")
            return depth
        except Exception as e:   # Netz, Dienst oder Format – EMODnet ist nur eine Zusatzspalte
            last = e
    print(f"EMODnet nicht geladen ({last}) – weiter ohne.")
    return None


BSH_URL = "https://gdi.bsh.de/de/data/Elevation-Bathymetry.zip"   # BSH-Geländemodell Nord-/Ostsee, 50 m, NHN


def bsh_depth(like, path):
    """BSH-Tiefenmodell (50 m) aus der ZIP (oder einer Rasterdatei) auf das Raster legen; Tiefe in m (positiv).
    Liest jedes Rasterformat, das GDAL kennt (GeoTIFF, ASCII-Grid, NetCDF …), auch mehrere Kacheln."""
    import zipfile
    import rasterio
    from rasterio.warp import reproject, Resampling
    if not os.path.exists(path):
        print(f"BSH-Modell nicht gefunden ({path}). Herunterladen:\n  curl -L -o {path} {BSH_URL}")
        return None
    if zipfile.is_zipfile(path):
        names = zipfile.ZipFile(path).namelist()
        rasters = [f"/vsizip/{path}/{n}" for n in names
                   if n.lower().endswith((".tif", ".tiff", ".asc", ".nc", ".grd", ".bag", ".vrt", ".img"))]
        if not rasters:
            print("BSH-ZIP enthält keine bekannte Rasterdatei. Inhalt:\n  " + "\n  ".join(names[:40]))
            return None
    else:
        rasters = [path]
    dst = np.full(like.shape, np.nan, dtype="float32")
    for r in rasters:
        with rasterio.open(r) as src:
            crs = src.crs or "EPSG:25832"          # BSH: ETRS89/UTM32, falls nicht angegeben
            tmp = np.full(like.shape, np.nan, dtype="float32")
            try:
                reproject(rasterio.band(src, 1), tmp, src_crs=crs, src_nodata=src.nodata,
                          dst_transform=like.odc.geobox.affine, dst_crs=CRS, dst_nodata=np.nan,
                          resampling=Resampling.bilinear)
            except Exception as e:
                print(f"  {r}: übersprungen ({e})"); continue
            dst = np.where(np.isnan(dst), tmp, dst)
    if np.isnan(dst).all():
        print("BSH-Modell deckt die Boddenkette nicht ab.")
        return None
    depth = xr.DataArray(-dst, dims=("y", "x"), coords={"y": like.y, "x": like.x}).odc.assign_crs(CRS)
    print(f"BSH geladen ({len(rasters)} Datei(en)): Tiefe im Bodden Median {float(depth.where(depth > 0).median()):.1f} m")
    return depth


# ---------------------------------------------------------------- Bilder und Seite

def png(arr, cmap, vmin=0, vmax=1, size=None, marker=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    h, w = arr.shape[:2]
    scale = (size or w) / w
    fig = plt.figure(figsize=(w * scale / 100, h * scale / 100), dpi=100)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off()
    if arr.ndim == 2:
        cm = plt.get_cmap(cmap).copy(); cm.set_bad("#d9d4c7")
        ax.imshow(np.ma.masked_invalid(arr), cmap=cm, vmin=vmin, vmax=vmax, interpolation="nearest")
    else:
        ax.imshow(arr, interpolation="nearest")
    if marker:
        ax.plot(*marker, marker="o", ms=9, mfc="none", mec="#e4002b", mew=2)
    buf = io.BytesIO(); fig.savefig(buf, format="png"); plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def truecolor(ds):
    a = np.stack([ds.red.values, ds.green.values, ds.blue.values], -1)
    a = np.clip(a / np.nanpercentile(a, 99), 0, 1) ** 0.7     # Wasser aufhellen
    return np.nan_to_num(a, nan=0.85)


def crop(v, lat, lon):
    x, y = to_xy(lat, lon)
    d = HALF_KM * 1000
    c = v.sel(x=slice(x - d, x + d), y=slice(y + d, y - d))
    mx = int(np.argmin(np.abs(c.x.values - x))); my = int(np.argmin(np.abs(c.y.values - y)))
    return c, (mx, my)


def depth_cmap():
    from matplotlib.colors import LinearSegmentedColormap
    # 0 = tief (dunkelblau) ... 1 = sehr flach (Sand)
    return LinearSegmentedColormap.from_list("flach", ["#0b2a4a", "#1f5f8b", "#4fa3c7", "#a8dbd9", "#f3e3b0", "#f7c873"])


def page(ds, flat, wm, cal, picked, out, emod=None, labtab=None, bsh=None):
    cmap = depth_cmap()
    tc = truecolor(ds)
    tcx = xr.DataArray(np.moveaxis(tc, -1, 0), dims=("band", "y", "x"), coords={"y": ds.y.values, "x": ds.x.values})
    norm = {k: normalize(v, wm) for k, v in flat.items()}
    cols = [("tc", "Satellitenbild (Median)")] + [(k, VARIANTS[k]) for k in flat]
    if bsh is not None:
        cols.append(("bsh", "BSH (m, 50 m Raster)"))
    if emod is not None:
        cols.append(("emod", "EMODnet (m, ~115 m Raster)"))
    if cal:
        cols += [(f"m{k}", f"{k} in Metern") for k in cal]
    rows = []
    for sid, (name, lat, lon) in SPOTS.items():
        cells = []
        for key, label in cols:
            if key == "tc":
                c, mk = crop(tcx, lat, lon)
                src = png(np.moveaxis(c.values, 0, -1), None, size=300, marker=mk)
            elif key in ("emod", "bsh"):
                c, mk = crop((emod if key == "emod" else bsh).where(wm), lat, lon)
                src = png(c.values, cmap.reversed(), vmin=0, vmax=4, size=300, marker=mk)
            elif key.startswith("m"):
                k = key[1:]
                c, mk = crop(flat[k] * cal[k]["a"] + cal[k]["c"], lat, lon)
                src = png(c.values, cmap.reversed(), vmin=0, vmax=3, size=300, marker=mk)
            else:
                # Farbskala je Ausschnitt strecken, sonst gehen die Unterschiede am Spot im ganzen Bodden unter
                c, mk = crop(flat[key], lat, lon)
                v = c.values
                if np.isfinite(v).sum() > 50:
                    lo, hi = np.nanpercentile(v, [2, 98]); v = np.clip((v - lo) / (hi - lo + 1e-9), 0, 1)
                src = png(v, cmap, size=300, marker=mk)
            cells.append(f'<figure><img src="{src}" alt="{name} {label}"><figcaption>{label}</figcaption></figure>')
        rows.append(f'<section><h2>{name}</h2><div class="row">{"".join(cells)}</div></section>')
    over = "".join(f'<figure><img src="{png(norm[k].values, cmap, size=900)}" alt="{VARIANTS[k]}">'
                   f'<figcaption>{VARIANTS[k]}</figcaption></figure>' for k in flat)
    caltab = ""
    if cal:
        caltab = "<h2>Abgleich mit Tiefenpunkten</h2><table><tr><th>Variante</th><th>Punkte</th><th>r</th><th>Fehler (Auslassung)</th></tr>" + "".join(
            f"<tr><td>{VARIANTS[k]}</td><td>{c['n']}</td><td>{c['r']:+.2f}</td><td>{c['rmse_loo']:.2f} m</td></tr>" for k, c in cal.items()) + "</table>"
    if labtab is not None and len(labtab):
        ks = [k for k in labtab.columns if k not in ("Fläche", "Klasse", "Becken")]
        caltab += ("<h2>Bekannte Flächen</h2><p class=note>Trefferquote (AUC), dass die Fläche richtig als flacher bzw. "
                   "tiefer erkannt wird als das übrige Wasser im selben Becken. 0,5 = Zufall, 1 = perfekt. "
                   "Flächen in training/depth_labels.geojson.</p><table><tr><th>Fläche</th><th>Klasse</th>"
                   + "".join(f"<th>{VARIANTS.get(k, k)}</th>" for k in ks) + "</tr>"
                   + "".join(f"<tr><td>{r['Fläche']}</td><td>{r['Klasse']}</td>"
                             + "".join(f"<td>{r[k]:.2f}</td>" for k in ks) + "</tr>" for _, r in labtab.iterrows())
                   + "</table>")
    pt = pd.to_datetime(picked.time).dt.strftime("%d.%m.%Y")
    if "wind_max_kn" in picked:
        days = ", ".join(f"{d} ({w:g} kn)" for d, w in zip(pt, picked.wind_max_kn))
    else:
        days = ", ".join(pt)
    html = f"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Flachwasser Bodden – Varianten</title><style>
body{{margin:0;font:15px/1.5 system-ui,sans-serif;background:#f6f4ee;color:#14212b}}
main{{max-width:1600px;margin:auto;padding:24px}} h1{{font-size:28px;margin:0 0 4px}} h2{{font-size:20px;margin:28px 0 8px}}
.row{{display:flex;gap:10px;overflow-x:auto}} figure{{margin:0;flex:none}} img{{display:block;width:300px;height:auto;border-radius:4px}}
figcaption{{font-size:13px;color:#4a5763;margin-top:4px}} .over img{{width:900px;max-width:90vw}}
.legend{{display:flex;align-items:center;gap:8px;font-size:13px}} .bar{{width:240px;height:12px;border-radius:6px;
background:linear-gradient(90deg,#0b2a4a,#1f5f8b,#4fa3c7,#a8dbd9,#f3e3b0,#f7c873)}}
table{{border-collapse:collapse}} td,th{{padding:4px 10px;border-bottom:1px solid #ccc;text-align:left}} .note{{color:#4a5763;font-size:13px}}
</style></head><body><main>
<h1>Flachwasser im Bodden – welche Variante passt?</h1>
<p class="note">Sentinel-2, Median der {len(picked)} Tage mit dem klarsten Wasser (in Klammern: Höchstwind davor): {days}. Ausschnitte ±{HALF_KM:g} km um den Spot (roter Kreis), Norden oben.
Farben sind relativ und je Ausschnitt gestreckt (2.–98. Perzentil, unten bei der ganzen Kette über den ganzen Bodden), keine Meter – außer in den Meter-Spalten mit Tiefenpunkten.</p>
<div class="legend"><span>tief</span><span class="bar"></span><span>sehr flach</span><span style="margin-left:16px">grau = Land/Schilf</span></div>
{caltab}{"".join(rows)}
<h2>Ganze Boddenkette</h2><div class="row over">{over}</div>
</main></body></html>"""
    with open(f"{out}/vergleich.html", "w") as f:
        f.write(html)
    print(f"Seite: {out}/vergleich.html")


def write_tifs(flat, wm, cal, out):
    for k, v in flat.items():
        normalize(v, wm).astype("float32").odc.write_cog(f"{out}/flach_{k}.tif", overwrite=True)
        if cal and k in cal:
            (v * cal[k]["a"] + cal[k]["c"]).astype("float32").odc.write_cog(f"{out}/tiefe_{k}.tif", overwrite=True)


def template(out):
    p = f"{out}/tiefenpunkte_vorlage.csv"
    rows = [f"{lat:.5f},{lon:.5f},,{name} (Beispiel – Tiefe eintragen)" for name, lat, lon in SPOTS.values()]
    with open(p, "w") as f:
        f.write("lat,lon,tiefe_m,quelle\n" + "\n".join(rows) + "\n")


# ---------------------------------------------------------------- Hauptablauf

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=f"{ROOT}/data/depth")
    ap.add_argument("--years", default=f"2018-{dt.date.today().year}")
    ap.add_argument("--scenes", type=int, default=15, help="Anzahl klarster Tage für den Median")
    ap.add_argument("--max-cloud", type=float, default=60,
                    help="max. Wolken je ganzer Kachel (110 km) in %%; ob der Bodden selbst frei ist, prüft die Vorauswahl")
    ap.add_argument("--months", default="11,12,1,2,3",
                    help="nur diese Monate (Winter: wenig Algen); 'alle' für das ganze Jahr")
    ap.add_argument("--max-wind", type=float, default=10,
                    help="max. Wind (kn) in den Stunden vor dem Überflug; 0 = kein Windfilter")
    ap.add_argument("--calm-hours", type=int, default=36, help="so viele Stunden vorher windstill")
    ap.add_argument("--res", type=int, default=10, help="Auflösung in m (20 halbiert Zeit und Speicher grob auf 1/4)")
    ap.add_argument("--threads", type=int, default=2, help="parallele Lese-Threads (wenig RAM: 1–2)")
    ap.add_argument("--no-emodnet", action="store_true", help="EMODnet-Tiefenmodell nicht laden")
    ap.add_argument("--bsh", default=f"{ROOT}/data/depth/Elevation-Bathymetry.zip",
                    help=f"BSH-Tiefenmodell (ZIP oder Raster), Download: {BSH_URL}; leer = nicht verwenden")
    ap.add_argument("--skip-download", action="store_true",
                    help="nur neu rechnen/zeichnen aus data/depth/szenen/ (z. B. nach neuen Tiefenpunkten)")
    a = ap.parse_args()
    import dask
    dask.config.set(scheduler="threads", num_workers=a.threads)
    os.makedirs(a.out, exist_ok=True)
    if a.skip_download:
        picked = pd.read_csv(f"{a.out}/szenen.csv", parse_dates=["time"])
    else:
        y0, y1 = (int(x) for x in a.years.split("-"))
        items = search((y0, y1), a.max_cloud)
        if not len(items):
            sys.exit("Keine Szenen gefunden.")
        months = None if a.months == "alle" else {int(m) for m in a.months.split(",")}
        allrows, picked = screen(items, a.scenes, months, a.calm_hours, a.max_wind or None)
        allrows.to_csv(f"{a.out}/alle_tage.csv", index=False)
        picked.to_csv(f"{a.out}/szenen.csv", index=False)
        load_scenes(items, list(pd.to_datetime(picked.time).dt.normalize()), a.out, a.res)
    files = [scene_file(a.out, d) for d in pd.to_datetime(picked.time).dt.normalize()]
    files = [f for f in files if os.path.exists(f)]
    if not files:
        sys.exit("Keine Szenen in data/depth/szenen/ – erst ohne --skip-download laufen lassen.")
    flat, wm, rgb = variants(files)
    emod = None if a.no_emodnet else emodnet(flat["A"])
    layers = dict(flat)
    if emod is not None:
        layers["EMODnet"] = -emod.where(wm)          # flacher = größer, wie die Varianten
        emod.where(wm).astype("float32").odc.write_cog(f"{a.out}/emodnet_tiefe.tif", overwrite=True)
    bsh = bsh_depth(flat["A"], a.bsh) if a.bsh else None
    if bsh is not None:
        layers["BSH"] = -bsh.where(wm)
        bsh.where(wm).astype("float32").odc.write_cog(f"{a.out}/bsh_tiefe.tif", overwrite=True)
    labtab = check_labels(layers, wm)
    if labtab is not None:
        labtab.to_csv(f"{a.out}/flaechen_check.csv", index=False)
    cal = calibrate(flat, f"{a.out}/tiefenpunkte.csv")
    template(a.out)
    write_tifs(flat, wm, cal, a.out)
    page(rgb, flat, wm, cal, picked, a.out, emod, labtab, bsh)


if __name__ == "__main__":
    main()
