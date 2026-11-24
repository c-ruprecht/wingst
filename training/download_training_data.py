"""Lädt alle Trainingsdaten für das lokale Kite-Windmodell (Zingst / Darß / Bodden).

Quellen
  1. Open-Meteo: archivierte Modellvorhersagen (ICON, ECMWF IFS, GFS) + Meerestemperatur
  2. DWD Open Data: stündliche Windmessungen (Darßer Ort, Barth, Arkona, Warnemünde, Hiddensee)
                    und Luftdruck (Arkona, Warnemünde) für das Druckgefälle
  3. DMI Open Data (Dänemark): Wind Vindebæk Kyst + Gedser, Wassertemperatur Gedser Havn
  4. Copernicus Marine: Sentinel-1-Radarwind, 0.01° (~1 km), Ostsee, 2018 bis heute
     Braucht ein kostenloses Konto und einmalig:  copernicusmarine login

Aufruf
  python3 download_training_data.py                 # alles
  python3 download_training_data.py --skip openmeteo
  python3 download_training_data.py --only dmi
  python3 download_training_data.py --only previous_runs   # für das 7-Tage-Modell
  python3 download_training_data.py --only satellite   # nach: copernicusmarine login
  python3 download_training_data.py --only blh         # GFS-Grenzschichthöhe aus früheren Läufen (7-Tage-Modell)
  python3 download_training_data.py --only sst_bodden  # Satelliten-Wassertemperatur Bodden + Ostsee (Copernicus)

Für 1–3 reicht die Python-Standardbibliothek, für 4 das Paket copernicusmarine. Bereits geladene Dateien werden übersprungen,
ein abgebrochener Lauf kann also einfach neu gestartet werden.
Ergebnis: Ordner ./data/{openmeteo,openmeteo_prev,dwd,dmi,sentinel1}/
"""
import argparse, csv, io, json, os, re, time, urllib.error, urllib.parse, urllib.request, zipfile

YEARS = range(2021, 2026)
OUT = "data"

# ---------- 1. Open-Meteo ----------
PLACES = {
    "darss_ort": (54.4738, 12.5014), "barth": (54.3405, 12.7109),
    "prerow": (54.452, 12.575), "zingst": (54.445, 12.690),
    "fuhlendorf": (54.385, 12.580), "pruchten": (54.383, 12.660),
    "darss_schwelle": (54.70, 12.70),
    "born": (54.380, 12.504), "saal": (54.318, 12.478),
}
MODELS = ["icon_seamless", "ecmwf_ifs025", "gfs_seamless"]
OM_HOURLY = ",".join([
    "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "wind_speed_80m", "wind_direction_80m",
    "temperature_2m", "pressure_msl", "cloud_cover", "shortwave_radiation", "boundary_layer_height",
    "cape", "precipitation", "temperature_850hPa", "wind_speed_850hPa", "wind_direction_850hPa",
])

# ---------- 1b. Open-Meteo frühere Läufe (Vorlaufzeit 1–7 Tage) ----------
PR_PLACES = {k: PLACES[k] for k in ("darss_ort", "barth", "darss_schwelle", "prerow", "zingst",
                                     "fuhlendorf", "pruchten", "born", "saal")}
PR_YEARS = [2024, 2025]
PR_VARS = ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "wind_speed_80m", "temperature_2m",
           "pressure_msl", "cloud_cover", "shortwave_radiation", "cape", "precipitation"]
PR_MODEL_VARS = {"icon_seamless": PR_VARS, "gfs_seamless": PR_VARS,
                 "ecmwf_ifs025": ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "temperature_2m",
                                  "pressure_msl", "cloud_cover"]}


def previous_runs():
    """Monatsweise und in kleinen Variablenpaketen abfragen; Fortschritt anzeigen."""
    import calendar
    for name, (lat, lon) in PR_PLACES.items():
        for y in PR_YEARS:
            for m, vars_ in PR_MODEL_VARS.items():
                path = f"{OUT}/openmeteo_prev/{name}_{m}_{y}.csv"
                if os.path.exists(path):
                    continue
                allvars = [f"{v}_previous_day{d}" for v in vars_ for d in range(1, 8)]
                packs = [allvars[i:i + 14] for i in range(0, len(allvars), 14)]   # 2 Variablen x 7 Tage
                frames = []
                for mon in range(1, 13):
                    last = calendar.monthrange(y, mon)[1]
                    print(f"  {name} {m} {y}-{mon:02d} ({len(packs)} Pakete) …", end=" ", flush=True)
                    t0 = time.time(); cols = {}
                    for pk in packs:
                        q = dict(latitude=lat, longitude=lon, start_date=f"{y}-{mon:02d}-01",
                                 end_date=f"{y}-{mon:02d}-{last}", hourly=",".join(pk), models=m,
                                 wind_speed_unit="kn", timezone="UTC")
                        h = get("https://previous-runs-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q),
                                timeout=120)["hourly"]
                        cols.update(h)
                        time.sleep(0.5)
                    frames.append(cols)
                    print(f"{time.time() - t0:.0f} s", flush=True)
                keys = ["time"] + allvars
                rows = []
                for c in frames:
                    n = len(c["time"])
                    rows += zip(*(c.get(k, [None] * n) for k in keys))
                write_rows(path, keys, rows); print(" ->", path, flush=True)


# ---------- 2. DWD ----------
DWD_BASE = "https://opendata.dwd.de/climate_environment/CDC/observations_germany/climate/hourly"
DWD_WIND = {"06097": "darss_ort", "00298": "barth", "00183": "arkona", "04271": "warnemuende"}  # Hiddensee-Vitte misst keinen Wind
DWD_PRESSURE = {"00183": "arkona", "04271": "warnemuende", "00298": "barth"}

# ---------- 3. DMI ----------
DMI = "https://opendataapi.dmi.dk/v2"
DMI_MET = {"06147": "vindebaek_kyst", "06149": "gedser"}
DMI_MET_PARAMS = ["wind_speed_past1h", "wind_dir_past1h", "wind_gust_always_past1h", "temp_dry", "pressure_at_sea"]
DMI_OCEAN = {"31623": "gedser_havn"}   # Wassertemperatur tw, 10-min


# ---------- 4. Copernicus Marine (Sentinel-1 SAR-Wind) ----------
SAT_BBOX = dict(minimum_longitude=12.30, maximum_longitude=13.20,
                minimum_latitude=54.28, maximum_latitude=54.75)
SAT_DATASETS = {
    # Multi-Year (geprüft), 2018 bis Mitte 2025
    "cmems_obs-wind_bal_phy_my_l3-s1a-sar-asc-0.01deg_P1D-i": ("2018-03-13", "2025-07-26"),
    "cmems_obs-wind_bal_phy_my_l3-s1a-sar-desc-0.01deg_P1D-i": ("2018-03-13", "2025-07-26"),
    "cmems_obs-wind_bal_phy_my_l3-s1b-sar-asc-0.01deg_P1D-i": ("2018-03-13", "2021-12-23"),
    "cmems_obs-wind_bal_phy_my_l3-s1b-sar-desc-0.01deg_P1D-i": ("2018-03-13", "2021-12-23"),
    # Near-Real-Time, ab 2025 (Sentinel-1C)
    "cmems_obs-wind_bal_phy_nrt_l3-s1c-sar-asc-0.01deg_P1D-i": ("2025-06-12", None),
    "cmems_obs-wind_bal_phy_nrt_l3-s1c-sar-desc-0.01deg_P1D-i": ("2025-06-12", None),
}


def satellite():
    try:
        import copernicusmarine
    except ImportError:
        raise SystemExit("Paket fehlt: conda env update -f environment.yml  (oder pip install copernicusmarine)")
    os.makedirs(f"{OUT}/sentinel1", exist_ok=True)
    for ds, (start, end) in SAT_DATASETS.items():
        fn = f"{ds}.nc"
        if os.path.exists(f"{OUT}/sentinel1/{fn}"):
            continue
        print(" ", ds)
        try:
            copernicusmarine.subset(dataset_id=ds, start_datetime=start, end_datetime=end,
                                    output_directory=f"{OUT}/sentinel1", output_filename=fn, **SAT_BBOX)
        except Exception as e:   # z. B. Datensatz umbenannt oder nicht mehr verfügbar
            print(f"   übersprungen: {e}")


# ---------- 1c. GFS-Grenzschichthöhe aus früheren Läufen (ICON/ECMWF liefern sie bei Open-Meteo nicht) ----------
def blh():
    import calendar
    vars_ = [f"boundary_layer_height_previous_day{d}" for d in range(1, 8)]
    for name in ("darss_ort", "barth"):
        lat, lon = PLACES[name]
        for y in PR_YEARS:
            path = f"{OUT}/openmeteo_prev/{name}_gfs_blh_{y}.csv"
            if os.path.exists(path):
                continue
            rows = []
            for mon in range(1, 13):
                last = calendar.monthrange(y, mon)[1]
                print(f"  {name} BLH {y}-{mon:02d} …", end=" ", flush=True)
                q = dict(latitude=lat, longitude=lon, start_date=f"{y}-{mon:02d}-01", end_date=f"{y}-{mon:02d}-{last}",
                         hourly=",".join(vars_), models="gfs_seamless", timezone="UTC")
                h = get("https://previous-runs-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q), timeout=120)["hourly"]
                n = len(h["time"])
                rows += zip(*([h["time"]] + [h.get(v, [None] * n) for v in vars_]))
                print("ok", flush=True); time.sleep(0.5)
            write_rows(path, ["time"] + vars_, rows); print(" ->", path, flush=True)


# ---------- 4b. Satelliten-Wassertemperatur (Copernicus, DMI L4, 0.02°, täglich) ----------
SST_DATASET = "DMI-BALTIC-SST-L4-NRT-OBS_FULL_TIME_SERIE"   # Produkt SST_BAL_SST_L4_NRT_OBSERVATIONS_010_007_b


def sst_bodden():
    try:
        import copernicusmarine
    except ImportError:
        raise SystemExit("Paket fehlt: conda env update -f environment.yml  (oder pip install copernicusmarine)")
    os.makedirs(f"{OUT}/sst_sat", exist_ok=True)
    fn = "bal_sst_l4_2023_2025.nc"
    if os.path.exists(f"{OUT}/sst_sat/{fn}"):
        return
    copernicusmarine.subset(dataset_id=SST_DATASET, variables=["analysed_sst"],
                            start_datetime="2023-01-01", end_datetime="2025-12-31",
                            output_directory=f"{OUT}/sst_sat", output_filename=fn, **SAT_BBOX)


def _ssl_context():
    """macOS-Python von python.org bringt keine Zertifikate mit -> certifi nutzen, falls vorhanden."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = _ssl_context()


def get(url, binary=False, tries=6, timeout=180):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kitewind-training/1.0"})
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                data = r.read()
            return data if binary else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                body = e.read().decode(errors="ignore")
                print(f"   429: {body[:120]}", flush=True)
                if "Daily" in body:
                    raise SystemExit("Open-Meteo: Tageslimit erreicht – morgen erneut starten.")
                print("   Rate-Limit, warte 60 s"); time.sleep(60); continue
            if e.code >= 500 and i < tries - 1:
                time.sleep(10); continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                raise SystemExit("SSL-Zertifikate fehlen. Im conda-Env starten (python statt python3) oder: pip install certifi")
            if i < tries - 1:
                print(f"   ({e.__class__.__name__}, neuer Versuch)", flush=True)
                time.sleep(10); continue
            raise


def write_rows(path, header, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)
    os.replace(tmp, path)


# ---------- Open-Meteo ----------
def openmeteo():
    for name, (lat, lon) in PLACES.items():
        for y in YEARS:
            for m in MODELS:
                path = f"{OUT}/openmeteo/{name}_{m}_{y}.csv"
                if os.path.exists(path):
                    continue
                q = dict(latitude=lat, longitude=lon, start_date=f"{y}-01-01", end_date=f"{y}-12-31",
                         hourly=OM_HOURLY, models=m, wind_speed_unit="kn", timezone="UTC")
                d = get("https://historical-forecast-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q))
                h = d["hourly"]; keys = list(h)
                write_rows(path, keys, zip(*(h[k] for k in keys))); print(" ", path); time.sleep(1)
            path = f"{OUT}/openmeteo/{name}_sst_{y}.csv"
            if not os.path.exists(path):
                q = dict(latitude=lat, longitude=lon, start_date=f"{y}-01-01", end_date=f"{y}-12-31",
                         hourly="sea_surface_temperature", timezone="UTC")
                d = get("https://marine-api.open-meteo.com/v1/marine?" + urllib.parse.urlencode(q))
                h = d["hourly"]; keys = list(h)
                write_rows(path, keys, zip(*(h[k] for k in keys))); print(" ", path); time.sleep(1)


# ---------- DWD ----------
def dwd_files(product, sid):
    out = []
    for period in ("historical", "recent"):
        html = get(f"{DWD_BASE}/{product}/{period}/", binary=True).decode("latin1")
        out += [f"{DWD_BASE}/{product}/{period}/{f}" for f in sorted(set(re.findall(rf'stundenwerte_\w+_{sid}_[\w]*\.zip', html)))]
    return out


def dwd(product, stations, tag):
    for sid, name in stations.items():
        path = f"{OUT}/dwd/{name}_{tag}.csv"
        if os.path.exists(path):
            continue
        rows, header = {}, None
        for url in dwd_files(product, sid):
            z = zipfile.ZipFile(io.BytesIO(get(url, binary=True)))
            fn = next(n for n in z.namelist() if n.startswith("produkt"))
            lines = z.read(fn).decode("latin1").splitlines()
            header = [c.strip() for c in lines[0].split(";")][:-1]
            for ln in lines[1:]:
                parts = [c.strip() for c in ln.split(";")][:-1]
                if parts and parts[1][:4] >= str(min(YEARS)):
                    rows[parts[1]] = parts          # recent überschreibt historical
        if not rows:
            print(f"  {name}: keine {tag}-Daten beim DWD, übersprungen"); continue
        write_rows(path, header, [rows[k] for k in sorted(rows)]); print(" ", path, len(rows))


# ---------- DMI ----------
def dmi_series(collection, sid, param, y):
    url = (f"{DMI}/{collection}/collections/observation/items?stationId={sid}&parameterId={param}"
           f"&datetime={y}-01-01T00:00:00Z/{y}-12-31T23:59:59Z&limit=300000")
    d = get(url)
    return sorted((f["properties"]["observed"], f["properties"]["value"]) for f in d["features"])


def dmi():
    for sid, name in DMI_MET.items():
        for y in YEARS:
            path = f"{OUT}/dmi/{name}_{y}.csv"
            if os.path.exists(path):
                continue
            table = {}
            for p in DMI_MET_PARAMS:
                for t, v in dmi_series("metObs", sid, p, y):
                    table.setdefault(t, {})[p] = v
            write_rows(path, ["time_utc"] + DMI_MET_PARAMS,
                       [[t] + [table[t].get(p) for p in DMI_MET_PARAMS] for t in sorted(table)])
            print(" ", path, len(table))
    for sid, name in DMI_OCEAN.items():
        for y in YEARS:
            path = f"{OUT}/dmi/{name}_tw_{y}.csv"
            if os.path.exists(path):
                continue
            rows = dmi_series("oceanObs", sid, "tw", y)
            write_rows(path, ["time_utc", "tw"], rows); print(" ", path, len(rows))


STEPS = {"dwd": lambda: (dwd("wind", DWD_WIND, "wind"), dwd("pressure", DWD_PRESSURE, "pressure")),
         "dmi": dmi, "openmeteo": openmeteo, "previous_runs": previous_runs, "satellite": satellite,
         "blh": blh, "sst_bodden": sst_bodden}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=STEPS)
    ap.add_argument("--skip", choices=STEPS, action="append", default=[])
    a = ap.parse_args()
    for name, fn in STEPS.items():
        if (a.only and name != a.only) or name in a.skip:
            continue
        print(f"== {name}"); fn()
    print("fertig")