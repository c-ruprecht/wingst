"""Satellitenwind im 300-m-Raster für Bodden und Küste – selbst gerechnet aus Sentinel-1-Radarbildern.

Warum: Das fertige Copernicus-Windprodukt hat ~1 km Raster und spart Küstenstreifen aus; die Bodden sind
nur 2–5 km breit. Aus den Radar-Rohbildern (Sentinel-1 IW GRD, 10 m) lässt sich Wind selbst rechnen:
Rückstreuung VV auf 300 m mitteln, dann mit dem Modell CMOD5.N (cmod5n.py) in Windstärke umrechnen.
Die Windrichtung kommt aus der Messung (Barth bzw. Darßer Ort) zur Überflugzeit.

Schritte (lokal ausführen, im conda-Env mit `python`):
  pip install sentinelhub rasterio
  # 1. Konto: https://dataspace.copernicus.eu  (dasselbe wie für Copernicus Browser, kostenlos)
  # 2. OAuth-Client anlegen: https://shapps.dataspace.copernicus.eu/dashboard/  → User settings → OAuth clients
  export CDSE_CLIENT_ID=...  CDSE_CLIENT_SECRET=...
  python sar_highres.py download --start 2024-01-01 --end 2025-12-31   # lädt VV + Einfallswinkel je Überflug (kleine GeoTIFFs)
  python sar_highres.py wind                                           # rechnet Wind (braucht data/dwd mit Barth/Darßer Ort)
  python sar_highres.py spots                                          # Faktoren je Spot gegen die Stationen
Ergebnis: data/sar300/raw/*.tif, data/sar300/wind/*.npz, results/sar300_spot_factors.csv, results/sar300_mean_ratio.png
Kosten: Sentinel Hub auf CDSE hat ein freies Monatskontingent; ein Bild dieser Größe kostet nur Bruchteile davon.
"""
import argparse, glob, json, os, sys
import numpy as np, pandas as pd

OUT = "data/sar300"
BBOX = (12.30, 54.28, 12.90, 54.52)     # Saaler, Bodstedter, Barther, Grabow-Bodden + Küste Prerow/Zingst
RES = 300                               # m
SPOTS = {"born": (54.380, 12.504), "saal": (54.318, 12.478), "fuhlendorf": (54.385, 12.580),
         "pruchten": (54.383, 12.660), "prerow": (54.452, 12.575), "zingst": (54.445, 12.690)}
STATIONS = {"barth": (54.3405, 12.7109), "darss_ort": (54.4738, 12.5014)}
EVALSCRIPT = """
//VERSION=3
function setup() {
  return { input: [{ bands: ["VV", "localIncidenceAngle", "dataMask"] }],
           output: { bands: 3, sampleType: "FLOAT32" } };
}
function evaluatePixel(s) { return [s.VV, s.localIncidenceAngle, s.dataMask]; }
"""


def sh_config():
    from sentinelhub import SHConfig
    cfg = SHConfig()
    cfg.sh_client_id = os.environ.get("CDSE_CLIENT_ID", "")
    cfg.sh_client_secret = os.environ.get("CDSE_CLIENT_SECRET", "")
    if not cfg.sh_client_id:
        raise SystemExit("CDSE_CLIENT_ID / CDSE_CLIENT_SECRET fehlen (siehe Kopf der Datei).")
    cfg.sh_base_url = "https://sh.dataspace.copernicus.eu"
    cfg.sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    return cfg


def download(start, end):
    from sentinelhub import (BBox, CRS, DataCollection, MimeType, SentinelHubCatalog, SentinelHubRequest,
                             bbox_to_dimensions)
    cfg = sh_config()
    s1 = DataCollection.SENTINEL1_IW.define_from("s1iw_cdse", service_url=cfg.sh_base_url)
    bbox = BBox(BBOX, crs=CRS.WGS84)
    size = bbox_to_dimensions(bbox, resolution=RES)
    cat = SentinelHubCatalog(config=cfg)
    items = list(cat.search(s1, bbox=bbox, time=(start, end)))
    print(f"{len(items)} Überflüge gefunden, Bildgröße {size} Pixel à {RES} m", flush=True)
    os.makedirs(f"{OUT}/raw", exist_ok=True)
    meta = []
    for it in items:
        t = pd.Timestamp(it["properties"]["datetime"])
        orbit = it["properties"].get("sat:orbit_state", "")
        fn = f"{OUT}/raw/{t:%Y%m%dT%H%M%S}_{orbit[:3]}.tif"
        meta.append(dict(file=os.path.basename(fn), time=t.isoformat(), orbit=orbit, id=it["id"]))
        if os.path.exists(fn):
            continue
        req = SentinelHubRequest(
            evalscript=EVALSCRIPT,
            input_data=[SentinelHubRequest.input_data(
                data_collection=s1, time_interval=(t - pd.Timedelta(minutes=2), t + pd.Timedelta(minutes=2)),
                other_args={"processing": {"backCoeff": "SIGMA0_ELLIPSOID", "orthorectify": True,
                                           "demInstance": "COPERNICUS_30", "speckleFilter": {"type": "NONE"}}})],
            responses=[SentinelHubRequest.output_response("default", MimeType.TIFF)],
            bbox=bbox, size=size, config=cfg)
        try:
            arr = req.get_data()[0]
        except Exception as e:
            print("  Fehler", fn, e); continue
        import rasterio
        from rasterio.transform import from_bounds
        with rasterio.open(fn, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=3, dtype="float32",
                           crs="EPSG:4326", transform=from_bounds(*BBOX, arr.shape[1], arr.shape[0]), compress="deflate") as dst:
            for b in range(3):
                dst.write(arr[:, :, b].astype("float32"), b + 1)
        print("  ok", fn, flush=True)
    pd.DataFrame(meta).drop_duplicates("file").to_csv(f"{OUT}/scenes.csv", index=False)


def read_obs(dwd_root, site):
    """Stündliche Messung Richtung (Grad) und Stärke (kn) von DWD (wie features.load_obs)."""
    sys.path.insert(0, os.path.dirname(__file__))
    from features import load_obs
    kn = load_obs(dwd_root, site)
    d = pd.read_csv(f"{dwd_root}/{site}_wind.csv", dtype={"MESS_DATUM": str})
    d["time"] = pd.to_datetime(d["MESS_DATUM"], format="%Y%m%d%H", utc=True)
    wd = pd.to_numeric(d.set_index("time")["D"], errors="coerce").where(lambda x: x >= 0)
    return pd.DataFrame({"kn": kn, "dir": wd.reindex(kn.index)})


def wind(dwd_root):
    import rasterio
    from cmod5n import cmod5n_inverse
    sc = pd.read_csv(f"{OUT}/scenes.csv")
    obs = {s: read_obs(dwd_root, s) for s in STATIONS}
    os.makedirs(f"{OUT}/wind", exist_ok=True)
    # Wassermaske aus dem Median aller Bilder: Wasser streut im Mittel deutlich schwächer zurück als Land
    vv_all = []
    for f in sc.file:
        p = f"{OUT}/raw/{f}"
        if os.path.exists(p):
            with rasterio.open(p) as r:
                vv = r.read(1); m = r.read(3)
            vv_all.append(np.where(m > 0, vv, np.nan))
    med = 10 * np.log10(np.clip(np.nanmedian(np.stack(vv_all), axis=0), 1e-6, None))
    # Schwelle zwischen Wasser (dunkel) und Land (hell) automatisch bestimmen (Otsu)
    vals = med[np.isfinite(med)]
    hist, edges = np.histogram(vals, bins=200)
    mids = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist); w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mids) / np.maximum(w0, 1); m1 = (np.sum(hist * mids) - np.cumsum(hist * mids)) / np.maximum(w1, 1)
    thr = mids[np.argmax(w0 * w1 * (m0 - m1) ** 2)]
    print(f"Wasser/Land-Schwelle: {thr:.1f} dB")
    water = med < thr
    # Küstenpixel (Nachbar ist Land) entfernen: Mischsignal
    from scipy.ndimage import binary_erosion
    water = binary_erosion(water, iterations=1)
    np.save(f"{OUT}/water_mask.npy", water)
    print(f"Wassermaske: {water.sum()} Pixel à {RES} m")
    for _, row in sc.iterrows():
        p = f"{OUT}/raw/{row.file}"
        if not os.path.exists(p):
            continue
        t = pd.Timestamp(row.time).tz_convert("UTC").round("h")
        o = obs["barth"].reindex([t]).iloc[0]
        if pd.isna(o.dir):
            o = obs["darss_ort"].reindex([t]).iloc[0]
        if pd.isna(o.dir):
            continue
        with rasterio.open(p) as r:
            vv, inc, m = r.read(1), r.read(2), r.read(3)
        # Blickrichtung des Radars: rechts zur Flugrichtung. Aufsteigend fliegt S-1 nach ~348°, absteigend nach ~192°.
        heading = 348.0 if str(row.orbit).lower().startswith("asc") else 192.0
        look = (heading + 90.0) % 360                      # Richtung, in die der Radar blickt
        # phi: Winkel zwischen Windrichtung (woher) und Blickrichtung; 0 = Wind weht auf den Radar zu
        phi = (o.dir - look) % 360                         # Wind kommt aus Blickrichtung -> weht auf den Radar zu
        ok = water & (m > 0) & np.isfinite(vv) & (vv > 0)
        v = np.full(vv.shape, np.nan)
        v[ok] = cmod5n_inverse(vv[ok], phi, inc[ok])
        np.savez_compressed(f"{OUT}/wind/{row.file[:-4]}.npz", v=v.astype("float32"), dir=o.dir, barth_kn=obs["barth"].kn.get(t, np.nan),
                            darss_kn=obs["darss_ort"].kn.get(t, np.nan), time=str(t))
    print("Wind gerechnet:", len(glob.glob(f"{OUT}/wind/*.npz")), "Bilder")


def pix(lat, lon, shape):
    h, w = shape
    r = int((BBOX[3] - lat) / (BBOX[3] - BBOX[1]) * h)
    c = int((lon - BBOX[0]) / (BBOX[2] - BBOX[0]) * w)
    return r, c


def spots():
    """Je Spot: Satellitenwind (Median im 900-m-Fenster, nur Wasser) geteilt durch Messung Barth bzw. Darßer Ort."""
    water = np.load(f"{OUT}/water_mask.npy")
    rows, ratios = [], []
    for f in sorted(glob.glob(f"{OUT}/wind/*.npz")):
        z = np.load(f)
        v = z["v"] * 1.944                                  # m/s -> kn
        rec = dict(time=str(z["time"]), dir=float(z["dir"]), barth_kn=float(z["barth_kn"]), darss_kn=float(z["darss_kn"]))
        for s, (lat, lon) in SPOTS.items():
            r, c = pix(lat, lon, v.shape)
            win = v[max(r - 2, 0):r + 3, max(c - 2, 0):c + 3]
            wm = water[max(r - 2, 0):r + 3, max(c - 2, 0):c + 3]
            vals = win[wm & np.isfinite(win)]
            rec[s] = float(np.median(vals)) if vals.size >= 3 else np.nan
        rows.append(rec)
        if rec["barth_kn"] >= 8:
            ratios.append(v / rec["barth_kn"])
    df = pd.DataFrame(rows)
    df.to_csv("../results/sar300_samples.csv", index=False)
    sec = lambda d: ["N", "NO", "O", "SO", "S", "SW", "W", "NW"][int(((d + 22.5) % 360) // 45)]
    df["sektor"] = df.dir.map(sec)
    out = []
    for s in SPOTS:
        ref = "darss_kn" if s in ("prerow", "zingst") else "barth_kn"
        x = df[(df[ref] >= 8) & df[s].notna()]
        out.append(dict(spot=s, ref=ref.replace("_kn", ""), n=len(x), faktor=round(float((x[s] / x[ref]).median()), 2) if len(x) else None,
                        **{f"faktor_{k}": round(float((g[s] / g[ref]).median()), 2) for k, g in x.groupby("sektor") if len(g) >= 8}))
    res = pd.DataFrame(out)
    res.to_csv("../results/sar300_spot_factors.csv", index=False)
    print(res.to_string())
    if ratios:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        mr = np.nanmedian(np.stack(ratios), axis=0)
        mr[~water] = np.nan
        plt.figure(figsize=(9, 4.5))
        plt.imshow(mr, extent=(BBOX[0], BBOX[2], BBOX[1], BBOX[3]), vmin=0.8, vmax=1.8, cmap="viridis", aspect="auto")
        plt.colorbar(label="Wind auf dem Wasser / Messung Barth (Median, Barth ≥ 8 kn)")
        for s, (lat, lon) in SPOTS.items():
            plt.plot(lon, lat, "w^"); plt.text(lon + 0.005, lat + 0.004, s, color="w", fontsize=8)
        plt.title(f"Sentinel-1, {RES} m, {len(ratios)} Überflüge")
        plt.savefig("../results/sar300_mean_ratio.png", dpi=150, bbox_inches="tight")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["download", "wind", "spots"])
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--dwd", default="data/dwd")
    a = ap.parse_args()
    if a.step == "download":
        download(a.start, a.end)
    elif a.step == "wind":
        wind(a.dwd)
    else:
        spots()
