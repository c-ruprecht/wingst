"""Flachwasserkarte der Boddenkette aus Sentinel-2 (Satellite-Derived Bathymetry, mehrere Varianten).

Lädt Sentinel-2 L2A (AWS Earth Search, ohne Konto), nimmt nur windstille Wintertage (Wind aus dem
Open-Meteo-Archiv, ERA5 – bei Wind wird im Bodden Sediment aufgewirbelt, im Sommer trüben Algen)
und davon die Szenen mit dem klarsten Wasser,
rechnet vier Varianten einer "Flachheit" und baut eine Vergleichsseite je Spot.

Aufruf (aus dem Repo-Ordner):
    python training/depth_map.py                       # Standard: Winter (Nov–März), vorher 36 h ≤ 10 kn, 10 klarste Tage
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
    D  Häufigkeit  Anteil der Szenen, in denen der Grund deutlich durchscheint (robust gegen Einzelszenen)

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
    ok = rows[(rows.wasser_anteil > 0.85) & (rows.wolke < 0.02) & (rows.eis < 0.01)].dropna(subset=["rot_wasser"])
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
    for k in ("A", "B", "C", "D"):
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
    rgb = xr.Dataset({k: median_layer(files, k) for k in ("red", "green", "blue")})
    return out, wm, rgb


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


def page(ds, flat, wm, cal, picked, out):
    cmap = depth_cmap()
    tc = truecolor(ds)
    tcx = xr.DataArray(np.moveaxis(tc, -1, 0), dims=("band", "y", "x"), coords={"y": ds.y.values, "x": ds.x.values})
    norm = {k: normalize(v, wm) for k, v in flat.items()}
    cols = [("tc", "Satellitenbild (Median)")] + [(k, VARIANTS[k]) for k in flat]
    if cal:
        cols += [(f"m{k}", f"{k} in Metern") for k in cal]
    rows = []
    for sid, (name, lat, lon) in SPOTS.items():
        cells = []
        for key, label in cols:
            if key == "tc":
                c, mk = crop(tcx, lat, lon)
                src = png(np.moveaxis(c.values, 0, -1), None, size=300, marker=mk)
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
    ap.add_argument("--scenes", type=int, default=10, help="Anzahl klarster Tage für den Median")
    ap.add_argument("--max-cloud", type=float, default=20, help="max. Wolken je Kachel in %%")
    ap.add_argument("--months", default="11,12,1,2,3",
                    help="nur diese Monate (Winter: wenig Algen); 'alle' für das ganze Jahr")
    ap.add_argument("--max-wind", type=float, default=10,
                    help="max. Wind (kn) in den Stunden vor dem Überflug; 0 = kein Windfilter")
    ap.add_argument("--calm-hours", type=int, default=36, help="so viele Stunden vorher windstill")
    ap.add_argument("--res", type=int, default=10, help="Auflösung in m (20 halbiert Zeit und Speicher grob auf 1/4)")
    ap.add_argument("--threads", type=int, default=2, help="parallele Lese-Threads (wenig RAM: 1–2)")
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
    cal = calibrate(flat, f"{a.out}/tiefenpunkte.csv")
    template(a.out)
    write_tifs(flat, wm, cal, a.out)
    page(rgb, flat, wm, cal, picked, a.out)


if __name__ == "__main__":
    main()
