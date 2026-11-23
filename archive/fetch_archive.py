"""Tägliches Vorhersage-Archiv (läuft als GitHub Action, siehe .github/workflows/archive.yml).

Open-Meteo hebt einzelne Ensemble-Läufe nur ~3 Tage auf. Damit wir später auf genau den Daten trainieren können,
die die App sieht, speichert dieses Skript zweimal täglich:
  ens_<site>.csv.gz   Ensemble ECMWF IFS, ECMWF AIFS, Google WeatherNext 2, ICON-EPS, GEFS: Wind 10 m (Stärke kn, Richtung)
  det.json.gz         deterministische Läufe ICON/GFS/ECMWF/AIFS an allen Spots und Stationen (alle App-Variablen)
  sst.json.gz         Wassertemperatur Open-Meteo Marine
Messungen werden nicht archiviert, die gibt es dauerhaft beim DWD/DMI.
Nur Python-Standardbibliothek. Aufruf: python3 archive/fetch_archive.py <Ausgabeordner>
"""
import csv, datetime as dt, gzip, io, json, os, sys, time, urllib.parse, urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else "out"
SITES = {"darss_ort": (54.4738, 12.5014), "barth": (54.3405, 12.7109)}
PLACES = {"darss_ort": (54.4738, 12.5014), "barth": (54.3405, 12.7109), "darss_schwelle": (54.70, 12.70),
          "prerow": (54.452, 12.575), "zingst": (54.445, 12.690), "born": (54.380, 12.504), "saal": (54.318, 12.478),
          "fuhlendorf": (54.385, 12.580), "pruchten": (54.383, 12.660)}
ENS = ["ecmwf_ifs025_ensemble", "ecmwf_aifs025_ensemble", "google_weathernext2_ensemble", "icon_seamless_eps", "ncep_gefs025"]
DET = ["icon_seamless", "gfs_seamless", "ecmwf_ifs025", "ecmwf_aifs025_single"]
HOURLY = ("wind_speed_10m,wind_direction_10m,wind_gusts_10m,wind_speed_80m,temperature_2m,pressure_msl,cloud_cover,"
          "shortwave_radiation,cape,precipitation,boundary_layer_height")


def get(url, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "wingst-archive/1.0"}), timeout=180) as r:
                return json.loads(r.read())
        except Exception as e:
            print("  Fehler:", e, flush=True)
            time.sleep(20 * (i + 1))
    raise SystemExit("Abbruch: " + url[:120])


def q(base, **kw):
    return base + "?" + urllib.parse.urlencode(kw)


def main():
    os.makedirs(OUT, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    # 1. Ensembles: eine Anfrage je Standort und Modell (hält die Antworten klein)
    for site, (lat, lon) in SITES.items():
        buf = io.StringIO(); w = csv.writer(buf)
        w.writerow(["model", "member", "var"] + ["t%d" % h for h in range(168)])
        times = None
        for m in ENS:
            r = get(q("https://ensemble-api.open-meteo.com/v1/ensemble", latitude=lat, longitude=lon,
                      hourly="wind_speed_10m,wind_direction_10m", models=m, wind_speed_unit="kn",
                      timezone="UTC", forecast_days=7))
            h = r["hourly"]; times = times or h["time"]
            for k, arr in h.items():
                if k == "time":
                    continue
                var = "ws" if k.startswith("wind_speed") else "wd"
                mem = int(k.split("_member")[1][:2]) if "_member" in k else 0
                w.writerow([m, mem, var] + ["" if x is None else (round(x, 1) if var == "ws" else int(round(x))) for x in arr])
            time.sleep(2)
        buf.write("# time0=" + times[0] + "\n")
        with gzip.open(f"{OUT}/ens_{site}.csv.gz", "wt") as f:
            f.write(buf.getvalue())
        print("ok ens", site, flush=True)
    # 2. deterministische Läufe
    names = list(PLACES)
    r = get(q("https://api.open-meteo.com/v1/forecast", latitude=",".join(str(PLACES[n][0]) for n in names),
              longitude=",".join(str(PLACES[n][1]) for n in names), hourly=HOURLY, models=",".join(DET),
              wind_speed_unit="kn", timezone="UTC", forecast_days=7))
    with gzip.open(f"{OUT}/det.json.gz", "wt") as f:
        json.dump({"places": names, "data": r if isinstance(r, list) else [r]}, f)
    print("ok det", flush=True)
    # 3. Wassertemperatur
    r = get(q("https://marine-api.open-meteo.com/v1/marine", latitude=",".join(str(PLACES[n][0]) for n in SITES),
              longitude=",".join(str(PLACES[n][1]) for n in SITES), hourly="sea_surface_temperature",
              forecast_days=2, timezone="UTC"))
    with gzip.open(f"{OUT}/sst.json.gz", "wt") as f:
        json.dump(r, f)
    with open(f"{OUT}/STAMP", "w") as f:
        f.write(stamp + "\n")
    print("fertig", stamp)


if __name__ == "__main__":
    main()
