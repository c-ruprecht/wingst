"""Historische Vorhersagen der KI-Modelle an Darßer Ort und Barth herunterladen (für die Prüfung der 14-Tage-Tendenz).

Quellen
  aifs  ECMWF AIFS (Einzellauf ab 02/2024, Ensemble ab 07/2025) – frei über dynamical.org (AWS, kein Konto nötig)
  wn2   Google WeatherNext 2, 64 Läufe, 15 Tage, ab 2022 – gs://weathernext/weathernext_2_0_0/zarr,
        Zugang vorher über das WeatherNext-Datenanfrageformular beantragen; danach einmal
        `gcloud auth application-default login`. Lizenz der historischen Daten: CC BY 4.0.

Aufruf (im conda-Env, mit `python`, nicht `python3`):
  pip install xarray zarr icechunk dynamical-catalog gcsfs
  python download_ai_history.py --inspect           # zeigt Aufbau der Datensätze (Ausgabe an Claude schicken, falls etwas fehlt)
  python download_ai_history.py --only aifs
  python download_ai_history.py --only wn2
Ergebnis: data/ai_history/<quelle>_<station>.csv.gz  (Spalten: init_time, lead_h, member, u10, v10, [t2m, msl])
Es werden nur die zwei Gitterpunkte gelesen; die Datensätze von dynamical.org sind dafür ausgelegt. Bei WeatherNext
hängt die Datenmenge von der Kachelung ab – --inspect zeigt sie, bevor etwas Großes geladen wird.
"""
import argparse, os, sys, time
import numpy as np, pandas as pd

OUT = "data/ai_history"
SITES = {"darss_ort": (54.4738, 12.5014), "barth": (54.3405, 12.7109)}
START = "2022-01-01"

# Namen, unter denen die Größen in den Datensätzen vorkommen können
NAMES = {"u10": ["wind_u_10m", "10m_u_component_of_wind", "u10", "u_component_of_wind_10m"],
         "v10": ["wind_v_10m", "10m_v_component_of_wind", "v10", "v_component_of_wind_10m"],
         "t2m": ["temperature_2m", "2m_temperature", "t2m"],
         "msl": ["pressure_reduced_to_mean_sea_level", "mean_sea_level_pressure", "msl", "pressure_msl"]}
DIMS = {"init": ["init_time", "time", "forecast_reference_time", "init"],
        "lead": ["lead_time", "prediction_timedelta", "step", "lead"],
        "member": ["ensemble_member", "sample", "member", "number", "realization"],
        "lat": ["latitude", "lat"], "lon": ["longitude", "lon"]}


def pick(names, available):
    for n in names:
        if n in available:
            return n
    return None


def describe(ds, label):
    print(f"\n===== {label} =====")
    print(ds)
    for v in list(ds.data_vars)[:40]:
        print(f"  {v}: dims={ds[v].dims} chunks={getattr(ds[v].data, 'chunksize', None)}")


def extract(ds, label, step_days=1):
    """Je Station den nächsten Gitterpunkt ziehen, Monat für Monat speichern (Abbruch und Neustart möglich)."""
    d = {k: pick(v, ds.dims) or pick(v, ds.coords) for k, v in DIMS.items()}
    vars_ = {k: pick(v, ds.data_vars) for k, v in NAMES.items()}
    print(label, "Dimensionen:", d, "Variablen:", vars_)
    if not (d["init"] and d["lat"] and d["lon"] and vars_["u10"] and vars_["v10"]):
        raise SystemExit(f"{label}: Aufbau unbekannt – bitte die Ausgabe von --inspect an Claude schicken.")
    keep = [v for v in vars_.values() if v]
    lon_vals = ds[d["lon"]].values
    os.makedirs(OUT, exist_ok=True)
    for site, (lat, lon) in SITES.items():
        lon_q = lon if lon_vals.min() < 0 or lon < 180 else lon % 360
        pt = ds[keep].sel({d["lat"]: lat, d["lon"]: lon_q}, method="nearest")
        inits = pd.to_datetime(pt[d["init"]].values)
        inits = inits[(inits >= START) & (inits.hour == 0)]           # nur 00-UTC-Läufe
        months = sorted(set(inits.to_period("M")))
        parts = []
        for m in months:
            fn = f"{OUT}/.part_{label}_{site}_{m}.csv.gz"
            if os.path.exists(fn):
                parts.append(fn); continue
            sel = inits[inits.to_period("M") == m][::step_days]
            t0 = time.time()
            sub = pt.sel({d["init"]: sel}).load()
            df = sub.to_dataframe().reset_index()
            ren = {d["init"]: "init_time", d["lat"]: "lat", d["lon"]: "lon"}
            if d["lead"]: ren[d["lead"]] = "lead"
            if d["member"]: ren[d["member"]] = "member"
            ren.update({v: k for k, v in vars_.items() if v})
            df = df.rename(columns=ren)
            if "lead" in df:
                df["lead_h"] = pd.to_timedelta(df["lead"]).dt.total_seconds() / 3600 if not np.issubdtype(df["lead"].dtype, np.number) else df["lead"]
                df = df.drop(columns="lead")
            cols = [c for c in ["init_time", "lead_h", "member", "u10", "v10", "t2m", "msl"] if c in df]
            df[cols].to_csv(fn, index=False, compression="gzip")
            parts.append(fn)
            print(f"  {label} {site} {m}: {len(df)} Zeilen, {time.time() - t0:.0f} s", flush=True)
        out = f"{OUT}/{label}_{site}.csv.gz"
        pd.concat([pd.read_csv(p) for p in parts]).to_csv(out, index=False, compression="gzip")
        print("  ->", out)


def open_dynamical(name):
    import dynamical_catalog
    return dynamical_catalog.open(name)


def aifs(inspect=False):
    found = False
    for name in ("ecmwf-aifs-single-forecast", "ecmwf-aifs-ens-forecast"):
        try:
            ds = open_dynamical(name)
        except Exception as e:
            print(f"{name}: nicht geöffnet ({e.__class__.__name__}: {e})")
            continue
        found = True
        if inspect:
            describe(ds, name)
        else:
            extract(ds, name.replace("-forecast", "").replace("ecmwf-", ""))
    if not found:
        try:
            import dynamical_catalog
            print("Verfügbar in dynamical_catalog:", [x for x in dir(dynamical_catalog) if not x.startswith("_")])
        except ImportError:
            print("Paket fehlt: pip install dynamical-catalog")


def wn2(inspect=False):
    import xarray as xr
    path = "gs://weathernext/weathernext_2_0_0/zarr"
    try:
        import gcsfs
        fs = gcsfs.GCSFileSystem(token="google_default")
        entries = fs.ls(path)
        print("Inhalt", path, ":", entries[:20], "…" if len(entries) > 20 else "")
    except Exception as e:
        raise SystemExit(f"Kein Zugriff auf {path} ({e}). Zugang beantragt und `gcloud auth application-default login` ausgeführt?")
    store = path if any(x.endswith((".zmetadata", "zarr.json", ".zgroup")) for x in entries) else "gs://" + entries[0]
    ds = xr.open_zarr(store, storage_options={"token": "google_default"}, consolidated=None)
    if inspect:
        describe(ds, "weathernext_2")
        return
    extract(ds, "wn2")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["aifs", "wn2"])
    ap.add_argument("--inspect", action="store_true")
    a = ap.parse_args()
    for name, fn in (("aifs", aifs), ("wn2", wn2)):
        if a.only in (None, name):
            print("==", name)
            try:
                fn(inspect=a.inspect)
            except SystemExit as e:
                print(e)
