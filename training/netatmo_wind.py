"""Private Netatmo-Windmesser rund um den Bodden finden, ihre Stundenwerte laden und gegen Barth (DWD) prüfen.

Netatmo-Stationen hängen meist auf Dächern oder im Garten. Brauchbar für uns sind nur die wenigen, die frei am
Wasser stehen. Dieses Skript sucht sie heraus:

  1. --find     alle öffentlichen Netatmo-Stationen mit Windmesser im Ausschnitt Bodden/Darß/Zingst
                -> data/netatmo/stations.csv (Lage, Entfernung zum nächsten Spot, aktueller Wert)
  2. --history  Stundenwerte je Station laden (Standard: 730 Tage)        -> data/netatmo/<station>.csv
  3. --check    jede Station gegen Barth (DWD) prüfen: Zusammenhang, Verhältnis je Windrichtung und Tageszeit,
                Abschirmung nach Westen                                     -> results/netatmo_check.csv

Zugang (einmalig, kostenlos):
  - Auf https://dev.netatmo.com mit einem Netatmo-Konto anmelden, unter „My apps“ eine App anlegen.
  - In der App mit dem „Token generator“ ein Token mit Scope read_station erzeugen.
  - Client-ID, Client-Secret und Refresh-Token als Umgebungsvariablen setzen:
        export NETATMO_CLIENT_ID=...  NETATMO_CLIENT_SECRET=...  NETATMO_REFRESH_TOKEN=...
    Das Skript erneuert das Token selbst und merkt sich das neue in data/netatmo/token.json (nicht im Repo).

Aufruf (aus dem Repo-Ordner oder training/):
  python training/netatmo_wind.py --find
  python training/netatmo_wind.py --history --days 730
  python training/netatmo_wind.py --check
"""
import argparse, json, math, os, sys, time, urllib.error, urllib.parse, urllib.request
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "netatmo")
TOKEN_FILE = os.path.join(DIR, "token.json")
API = "https://api.netatmo.com"
BBOX = dict(lat_sw=54.28, lon_sw=12.30, lat_ne=54.52, lon_ne=12.90)   # wie die Satellitenauswertung
KMH2KN = 1 / 1.852
SPOTS = {  # wie app/spots.js
    "prerow": (54.4520, 12.5600), "zingst": (54.4440, 12.6880), "born": (54.381079, 12.504912),
    "saal": (54.312080, 12.480294), "pruchten": (54.386068, 12.659851), "dabitz": (54.362277, 12.805830),
}
SECTORS = "N NO O SO S SW W NW".split()


# ---------------------------------------------------------------- Zugang

def _ssl():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CTX = _ssl()


def _post(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
        return json.loads(r.read())


def token():
    """Gültiges Access-Token; erneuert es über das Refresh-Token und speichert das neue Refresh-Token."""
    cid, sec = os.environ.get("NETATMO_CLIENT_ID"), os.environ.get("NETATMO_CLIENT_SECRET")
    if not cid or not sec:
        sys.exit("NETATMO_CLIENT_ID und NETATMO_CLIENT_SECRET fehlen (siehe Kopf dieses Skripts).")
    saved = json.load(open(TOKEN_FILE)) if os.path.exists(TOKEN_FILE) else {}
    if saved.get("access_token") and saved.get("expires_at", 0) > time.time() + 60:
        return saved["access_token"]
    refresh = saved.get("refresh_token") or os.environ.get("NETATMO_REFRESH_TOKEN")
    if not refresh:
        sys.exit("NETATMO_REFRESH_TOKEN fehlt (Token generator auf dev.netatmo.com, Scope read_station).")
    try:
        t = _post(f"{API}/oauth2/token", dict(grant_type="refresh_token", refresh_token=refresh,
                                               client_id=cid, client_secret=sec))
    except urllib.error.HTTPError as e:
        sys.exit(f"Token-Erneuerung abgelehnt ({e.code}): {e.read().decode()[:200]}\n"
                 f"Neues Token im Token generator erzeugen, NETATMO_REFRESH_TOKEN setzen und {TOKEN_FILE} löschen.")
    os.makedirs(DIR, exist_ok=True)
    t["expires_at"] = time.time() + t.get("expires_in", 10800)
    json.dump(t, open(TOKEN_FILE, "w"))
    return t["access_token"]


class Unavailable(Exception):
    pass


def api(path, params, tries=6):
    last = None
    for i in range(tries):
        url = f"{API}/api/{path}?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token()}"})
        try:
            with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            if e.code == 429 or "limit" in body.lower():          # 50 Anfragen / 10 s, 500 / h
                wait = 60 * (i + 1)
                print(f"    Netatmo-Limit erreicht, warte {wait} s …"); time.sleep(wait); continue
            if e.code >= 500:                                     # Netatmo überlastet (z. B. 503, Code 27)
                last = f"{e.code}"; wait = 10 * (i + 1)
                print(f"    Netatmo {e.code}, neuer Versuch in {wait} s …"); time.sleep(wait); continue
            if e.code in (401, 403) and i == 0:
                if os.path.exists(TOKEN_FILE):
                    s = json.load(open(TOKEN_FILE)); s["expires_at"] = 0; json.dump(s, open(TOKEN_FILE, "w"))
                continue
            raise Unavailable(f"{e.code}: {body}")   # z. B. Station nicht freigegeben -> überspringen
        except (urllib.error.URLError, TimeoutError) as e:
            time.sleep(5 * (i + 1)); last = e
    raise Unavailable(f"Netatmo {path}: keine Antwort ({last})")


def km(a, b):
    return math.hypot((a[0] - b[0]) * 111.2, (a[1] - b[1]) * 111.2 * math.cos(math.radians(a[0])))


# ---------------------------------------------------------------- 1. Stationen finden

def parse_public(body):
    """Antwort von getpublicdata -> eine Zeile je Station mit Windmesser."""
    rows = []
    for st in body:
        lon, lat = st["place"]["location"]
        for mod, m in (st.get("measures") or {}).items():
            if "wind_strength" not in m:
                continue
            near = min(SPOTS, key=lambda s: km((lat, lon), SPOTS[s]))
            rows.append({
                "station": st["_id"], "wind_module": mod, "lat": lat, "lon": lon,
                "altitude": st["place"].get("altitude"), "city": st["place"].get("city"),
                "street": st["place"].get("street"), "naechster_spot": near,
                "km_zum_spot": round(km((lat, lon), SPOTS[near]), 1),
                "jetzt_kn": round(m["wind_strength"] * KMH2KN, 1), "jetzt_richtung": m.get("wind_angle"),
                "boe_kn": round(m.get("gust_strength", np.nan) * KMH2KN, 1),
                "zeit": pd.to_datetime(m.get("wind_timeutc"), unit="s", utc=True),
            })
    return pd.DataFrame(rows)


def find():
    # Ausschnitt in Kacheln teilen: getpublicdata liefert pro Anfrage nur eine begrenzte Zahl von Stationen
    lats = np.linspace(BBOX["lat_sw"], BBOX["lat_ne"], 4); lons = np.linspace(BBOX["lon_sw"], BBOX["lon_ne"], 5)
    parts, missed = [], 0
    for a, b in zip(lats[:-1], lats[1:]):
        for c, d in zip(lons[:-1], lons[1:]):
            try:
                r = api("getpublicdata", dict(lat_sw=a, lon_sw=c, lat_ne=b, lon_ne=d, required_data="wind", filter="false"))
            except Unavailable as e:
                print(f"  Kachel {a:.2f}–{b:.2f} N, {c:.2f}–{d:.2f} O übersprungen: {e}"); missed += 1; continue
            parts.append(parse_public(r.get("body", [])))
            time.sleep(1)
    if missed:
        print(f"  {missed} von 12 Kacheln fehlen – später einfach --find noch einmal starten.")
    df = pd.concat(parts).drop_duplicates("station") if parts else pd.DataFrame()
    if df.empty:
        print("Keine öffentlichen Netatmo-Windmesser im Ausschnitt gefunden."); return
    df = df.sort_values("km_zum_spot")
    os.makedirs(DIR, exist_ok=True)
    df.to_csv(os.path.join(DIR, "stations.csv"), index=False)
    print(f"{len(df)} Windmesser gefunden -> {DIR}/stations.csv")
    print(df[["station", "city", "street", "naechster_spot", "km_zum_spot", "jetzt_kn", "jetzt_richtung", "zeit"]]
          .to_string(index=False))


# ---------------------------------------------------------------- 2. Verlauf laden

def history(days):
    st = pd.read_csv(os.path.join(DIR, "stations.csv"))
    end = int(time.time()) // 3600 * 3600; start = end - days * 86400      # volle Stunden, sonst verschobene Stundenfenster
    for _, s in st.iterrows():
        out = os.path.join(DIR, f"{s.station.replace(':', '')}.csv")
        have = pd.read_csv(out, parse_dates=["time"]) if os.path.exists(out) else pd.DataFrame()
        t0 = int(have.time.max().timestamp()) + 3600 if len(have) else start
        rows = []
        while t0 < end:
            try:
                r = api("getmeasure", dict(device_id=s.station, module_id=s.wind_module, scale="1hour",
                                       type="WindStrength,WindAngle,GustStrength", date_begin=t0, date_end=end,
                                       optimize="false", real_time="true"))
            except Unavailable as e:
                print(f"  {s.station}: abgebrochen ({e}), beim nächsten Lauf geht es hier weiter"); break
            body = r.get("body") or {}
            if not body:
                break
            for ts, v in body.items():
                rows.append({"time": pd.to_datetime(int(ts), unit="s", utc=True),
                             "kn": None if v[0] is None else v[0] * KMH2KN, "dir": v[1],
                             "gust_kn": None if v[2] is None else v[2] * KMH2KN})
            t0 = max(int(k) for k in body) + 3600          # je Anfrage höchstens 1024 Werte
            time.sleep(0.3)
        if rows:
            df = pd.concat([have, pd.DataFrame(rows)]).drop_duplicates("time").sort_values("time")
            df.to_csv(out, index=False, float_format="%.2f")
            print(f"  {s.station} ({s.city}, {s.naechster_spot} {s.km_zum_spot} km): {len(df)} Stunden "
                  f"ab {df.time.min():%Y-%m-%d}")
        else:
            print(f"  {s.station}: keine neuen Werte")


# ---------------------------------------------------------------- 3. Prüfen gegen Barth

def barth():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from features import load_obs
    dwd = os.path.join(ROOT, "data", "dwd")
    raw = pd.read_csv(f"{dwd}/barth_wind.csv", dtype={"MESS_DATUM": str})
    raw["time"] = pd.to_datetime(raw["MESS_DATUM"], format="%Y%m%d%H", utc=True)
    return load_obs(dwd, "barth"), raw.set_index("time")["D"].where(lambda s: s >= 0)


def assess(df, b_kn, b_dir):
    """Kennzahlen einer Station gegen Barth. Stundenmittel Netatmo (Ende der Stunde) gegen DWD-Stundenwert."""
    s = df.set_index("time")
    s.index = s.index.ceil("h")
    J = pd.DataFrame({"n_kn": s.kn, "n_dir": s["dir"], "b": b_kn, "b_dir": b_dir}).dropna(subset=["n_kn", "b"])
    if len(J) < 200:
        return None
    x = J[J.b >= 8].copy()
    sec = ((((x.b_dir + 22.5) % 360) // 45).astype("Int64")).map(dict(enumerate(SECTORS)))
    ratio = (x.n_kn / x.b).groupby(sec).median()
    hour = x.index.tz_convert("Europe/Berlin").hour
    day = (x.n_kn / x.b)[(hour >= 11) & (hour <= 16)].median()
    night = (x.n_kn / x.b)[(hour <= 5) | (hour >= 22)].median()
    dird = ((x.n_dir - x.b_dir + 180) % 360 - 180).abs().median()
    west = ratio.reindex(["SW", "W", "NW"]).mean()
    rest = ratio.reindex(["NO", "O", "SO", "S"]).mean()
    r = J.n_kn.corr(J.b)
    ok = (r >= 0.75) and (west >= 0.8) and (dird <= 30)
    out = {"stunden": len(J), "r_barth": round(float(r), 2), "verh_alle": round(float((x.n_kn / x.b).median()), 2),
           "verh_mittag": round(float(day), 2), "verh_nacht": round(float(night), 2),
           "verh_west": round(float(west), 2), "verh_ost_sued": round(float(rest), 2), "richtung_abw_grad": round(dird),
           "urteil": "brauchbar" if ok else ("abgeschirmt West" if west < 0.7 else "schwach")}
    out.update({f"verh_{k}": round(v, 2) for k, v in ratio.items()})
    return out


def check():
    st = pd.read_csv(os.path.join(DIR, "stations.csv"))
    b_kn, b_dir = barth()
    rows = []
    for _, s in st.iterrows():
        f = os.path.join(DIR, f"{s.station.replace(':', '')}.csv")
        if not os.path.exists(f):
            continue
        res = assess(pd.read_csv(f, parse_dates=["time"]), b_kn, b_dir)
        if res:
            rows.append({"station": s.station, "city": s.city, "street": s.street, "lat": s.lat, "lon": s.lon,
                         "naechster_spot": s.naechster_spot, "km_zum_spot": s.km_zum_spot, **res})
    if not rows:
        print("Keine Station mit genug gemeinsamen Stunden (erst --history)."); return
    R = pd.DataFrame(rows).sort_values(["urteil", "r_barth"], ascending=[True, False])
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    R.to_csv(os.path.join(ROOT, "results", "netatmo_check.csv"), index=False)
    print(R[["station", "city", "naechster_spot", "km_zum_spot", "stunden", "r_barth", "verh_alle", "verh_mittag",
             "verh_nacht", "verh_west", "verh_ost_sued", "richtung_abw_grad", "urteil"]].to_string(index=False))
    print("\nbrauchbar = r ≥ 0,75 zu Barth, bei SW–NW mindestens 80 % von Barth, Richtung im Mittel ≤ 30° daneben.\n"
          "verh_mittag > verh_nacht deutet auf Thermik bzw. Durchmischung am Tag hin (Wasser: eher umgekehrt).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--find", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--days", type=int, default=730)
    a = ap.parse_args()
    if not (a.find or a.history or a.check):
        ap.print_help(); sys.exit()
    if a.find: find()
    if a.history: history(a.days)
    if a.check: check()
