"""Tägliches Vorhersage-Archiv (läuft als GitHub Action, siehe .github/workflows/archive.yml).

Open-Meteo hebt einzelne Ensemble-Läufe nur ~3 Tage auf. Damit wir später auf genau den Daten trainieren können,
die die App sieht, speichert dieses Skript zweimal täglich:
  ens_<site>.csv.gz   Ensemble ECMWF IFS, ECMWF AIFS, Google WeatherNext 2, ICON-EPS, GEFS: Wind 10 m (Stärke kn, Richtung)
  det.json.gz         deterministische Läufe ICON/GFS/ECMWF/AIFS an allen Spots und Stationen (alle App-Variablen)
  sst.json.gz         Wassertemperatur Open-Meteo Marine
  pegel.json.gz       Pegelonline (WSV): alle Messreihen der Pegel um Zingst/Barth/Darß, letzte 2 Tage
                      (Wasserstand, wo vorhanden auch Wasser-/Lufttemperatur und Wind). Pegelonline hält nur ~30 Tage vor.
Wind- und Druckmessungen des DWD/DMI werden nicht archiviert, die gibt es dauerhaft beim DWD/DMI.
Nur Python-Standardbibliothek. Aufruf: python3 archive/fetch_archive.py <Ausgabeordner>
"""
import csv, datetime as dt, gzip, io, json, os, sys, time, urllib.parse, urllib.request

OUT = sys.argv[1] if len(sys.argv) > 1 else "out"
SITES = {"darss_ort": (54.4738, 12.5014), "barth": (54.3405, 12.7109)}
PLACES = {"darss_ort": (54.4738, 12.5014), "barth": (54.3405, 12.7109), "darss_schwelle": (54.70, 12.70),
          "prerow": (54.452, 12.575), "zingst": (54.445, 12.690), "born": (54.380, 12.504), "saal": (54.318, 12.478),
          "pruchten": (54.386, 12.660), "dabitz": (54.362, 12.806)}
ENS = ["ecmwf_ifs025_ensemble", "ecmwf_aifs025_ensemble", "google_weathernext2_ensemble", "icon_seamless_eps", "ncep_gefs025"]
DET = ["icon_seamless", "gfs_seamless", "ecmwf_ifs025", "ecmwf_aifs025_single"]
HOURLY = ("wind_speed_10m,wind_direction_10m,wind_gusts_10m,wind_speed_80m,temperature_2m,pressure_msl,cloud_cover,"
          "shortwave_radiation,cape,precipitation,boundary_layer_height")


def get(url, tries=5, soft=False):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "wingst-archive/1.0"}), timeout=180) as r:
                return json.loads(r.read())
        except Exception as e:
            print("  Fehler:", e, flush=True)
            time.sleep(20 * (i + 1))
    if soft:
        return None
    raise SystemExit("Abbruch: " + url[:120])


PEGEL = "https://www.pegelonline.wsv.de/webservices/rest-api/v2"


def pegel():
    """Pegel im Umkreis von 40 km um den Zingst; jede Messreihe der letzten 2 Tage (Läufe überlappen absichtlich)."""
    st = get(q(f"{PEGEL}/stations.json", latitude=54.40, longitude=12.62, radius=40, includeTimeseries="true"), tries=3, soft=True)
    if not st:
        return None
    out = {}
    for s in st:
        series = {}
        for ts in s.get("timeseries", []):
            m = get(q(f"{PEGEL}/stations/{s['uuid']}/{ts['shortname']}/measurements.json", start="P2D"), tries=2, soft=True)
            if m:
                series[ts["shortname"]] = {"unit": ts.get("unit"), "name": ts.get("longname"),
                                           "values": [[x["timestamp"], x["value"]] for x in m]}
            time.sleep(0.5)
        out[s["shortname"]] = {"uuid": s["uuid"], "lat": s.get("latitude"), "lon": s.get("longitude"),
                               "water": (s.get("water") or {}).get("longname"), "series": series}
    return out


def q(base, **kw):
    return base + "?" + urllib.parse.urlencode(kw)


def main():
    os.makedirs(OUT, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    # 1. Ensembles: eine Anfrage je Standort und Modell (hält die Antworten klein)
    for site, (lat, lon) in SITES.items():
        buf = io.StringIO(); w = csv.writer(buf)
        w.writerow(["model", "member", "var", "werte stündlich ab time0 (UTC), 15 Tage"])
        times = None
        for m in ENS:
            r = get(q("https://ensemble-api.open-meteo.com/v1/ensemble", latitude=lat, longitude=lon,
                      hourly="wind_speed_10m,wind_direction_10m", models=m, wind_speed_unit="kn",
                      timezone="UTC", forecast_days=15))
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
    # 4. Pegel (darf fehlen, dann läuft der Rest trotzdem durch)
    try:
        p = pegel()
        if p:
            with gzip.open(f"{OUT}/pegel.json.gz", "wt") as f:
                json.dump(p, f)
            print("ok pegel", ", ".join(f"{k}:{'/'.join(v['series'])}" for k, v in p.items()), flush=True)
        else:
            print("pegel: keine Antwort, übersprungen", flush=True)
    except Exception as e:
        print("pegel: Fehler, übersprungen:", e, flush=True)
    with open(f"{OUT}/STAMP", "w") as f:
        f.write(stamp + "\n")
    print("fertig", stamp)


if __name__ == "__main__":
    main()
